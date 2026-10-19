# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replay a recorded Angelo session's typed messages on stock Blender + Blender MCP + Claude Code.

    python3 bench_mcp_replay.py --authorize-billed-runs --run <new folder> --script messages.json \\
        --connection-id UUID --source-connections-dir DIR --mcp-addon-dir DIR \\
        [--model claude-opus-5-5] [--effort medium] [--cap-usd 40]

BILLED: every message goes to the connection's own Claude Code profile. This is the MCP side of
``bench_mcp_vs_angelo.py``, unchanged, driven with a session's own messages instead of a task set: the same
launch (a fresh stock-Blender GUI with a private copy of Blender Lab's MCP add-on on a private port), the
same headless Claude Code command line (default system prompt, only the Blender MCP server's tools
pre-allowed: built-in file reading and web tools are denied, as nothing can answer a prompt), the same
isolation and the same usage accounting (``bench_mcp_trial``, ``bench_mcp_report``, ``provider_usage``).

``messages.json`` is a list of ``{"id", "stage", "text", "attachments": [paths], "stage_end": bool}``: the
messages a person typed into Angelo, word for word and in order, without the ones that only relayed an
Angelo product failure. Nothing is reviewed or approved: MCP runs the model's Python as it arrives. The
scene is copied after every message: ``stages/<stage>.blend`` after a stage's last message, else a rolling
``stages/latest.blend``. ``replay.json`` records per message: wall time, model requests, tool calls, tool
errors, tokens and Claude Code's cost estimate. The run stops when the cost passes ``--cap-usd`` or the
account reports a limit, and waits between messages while another billed run is on the machine. A message
without a recorded cost is counted in ``cost_unknown_messages``, never added as zero, so ``cost_usd`` (and the
cap check on it) covers the messages that recorded one.

It is a comparison of "the same messages on the same model", not of equal direction: notes written after
looking at Angelo's renders describe Angelo's scene. See doc/angelo/scenario_benchmarks.rst, "The island".
"""

import argparse
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

BILLED = r"^[^ ]*[Pp]ython[^ ]* [^ ]*(eval_live_agent|bench_mcp_vs_angelo)\.py"
HOME = Path.home()
SAVE = """import bpy
def _save():
    prefs = bpy.context.preferences.filepaths
    kept = prefs.file_preview_type
    prefs.file_preview_type = 'NONE'
    try:
        bpy.ops.wm.save_as_mainfile(filepath=%r, copy=True, check_existing=False)
    finally:
        prefs.file_preview_type = kept
    return None
bpy.app.timers.register(_save, first_interval=0.2)
result = {'scheduled': True, 'objects': len(bpy.context.scene.objects)}
"""
FACTS = """import bpy
s = bpy.context.scene
result = {'objects': len(s.objects), 'names': sorted(o.name for o in s.objects)[:80], 'engine': s.render.engine,
          'camera': s.camera.name if s.camera else None,
          'resolution': [s.render.resolution_x, s.render.resolution_y]}
"""


def other_billed_runs():
    import re
    output = subprocess.run(["ps", "-Ao", "command"], capture_output=True, text=True).stdout
    return [line[:160] for line in output.splitlines() if re.search(BILLED, line)]


REQUIRED = ("id", "stage", "text")


def add_cost(output, record):
    """Add a message's cost (Claude Code's own estimate) to ``output["cost_usd"]``; return it, or None.

    A message without a recorded cost is counted in ``cost_unknown_messages``, never added as zero: then
    ``cost_usd`` is the cost of the messages that recorded one, so the cap check on it is a lower bound.
    """
    cost = (record.get("provider_usage") or {}).get("cost_usd")
    if type(cost) not in (int, float):
        output["cost_unknown_messages"] = output.get("cost_unknown_messages", 0) + 1
        return None
    output["cost_usd"] = round(output["cost_usd"] + cost, 4)
    return cost


def load_script(path):
    """The validated message list of a replay; raises ``ValueError`` naming the first bad entry."""
    messages = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(messages, list) or not messages:
        raise ValueError("The script must be a non-empty list of messages")
    seen = set()
    for index, message in enumerate(messages, 1):
        missing = [key for key in REQUIRED if not isinstance(message, dict) or not str(message.get(key) or "").strip()]
        if missing:
            raise ValueError("Message %d lacks %s" % (index, ", ".join(missing)))
        if message["id"] in seen:
            raise ValueError("Message id %r is used twice" % message["id"])
        seen.add(message["id"])
        for attachment in message.get("attachments") or ():
            if not Path(attachment).is_file():
                raise ValueError("Message %d attaches a missing file: %s" % (index, attachment))
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--authorize-billed-runs", action="store_true",
                        help="required: every message is a paid request on the connection's subscription")
    parser.add_argument("--run", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--source-connections-dir", required=True)
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--cap-usd", type=float, default=40.0)
    parser.add_argument("--message-timeout", type=float, default=2700.0)
    parser.add_argument("--stock-blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
    parser.add_argument("--mcp-server-dir",
                        default=str(HOME / "Library/Application Support/Claude/Claude Extensions/ant.dir.gh.blender.blender-mcp"))
    parser.add_argument("--mcp-addon-dir", required=True, help="a copy of the Blender MCP add-on (never the person's own)")
    parser.add_argument("--claude", default=str(HOME / ".local/bin/claude"))
    args = parser.parse_args()
    if not args.authorize_billed_runs:
        print("REFUSED: pass --authorize-billed-runs; every message is billed", file=sys.stderr)
        return 2
    if other_billed_runs():
        print("REFUSED: another billed run is on this machine", file=sys.stderr)
        return 2
    harness = Path(__file__).resolve().parent
    sys.path[:0] = [str(harness.parents[2] / "scripts/addons_core"), str(harness)]
    import bench_mcp_report as report
    import bench_mcp_trial as mcp
    import bench_workdirs as workdirs
    import eval_provider_limits as limits
    from angelo_agent.provider_usage import ClaudeProcessUsage

    run = Path(args.run).resolve()
    run.mkdir(parents=True, exist_ok=True)
    if any(run.iterdir()):
        print("REFUSED: use a new, empty --run folder", file=sys.stderr)
        return 2
    try:
        messages = load_script(args.script)
    except (OSError, ValueError) as error:
        print("REFUSED: %s" % error, file=sys.stderr)
        return 2
    folder = run / "mcp"
    folder.mkdir()
    (folder / "tmp").mkdir()
    (run / "stages").mkdir()
    config = {"stock_blender": args.stock_blender, "mcp_server_dir": args.mcp_server_dir,
              "mcp_addon_dir": args.mcp_addon_dir, "claude": args.claude,
              "claude_config_dir": str(Path(args.source_connections_dir) / "claude-profile" / args.connection_id),
              "claude_cwd": str(workdirs.root(run) / "mcp-cwd"), "model": args.model, "effort": args.effort,
              "task_timeout": args.message_timeout, "input_isolation": "on"}
    output = {"side": "mcp", "model": args.model, "effort": args.effort, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "messages": [], "cost_usd": 0.0, "cap_usd": args.cap_usd}

    def write():
        (run / "replay.json").write_text(json.dumps(output, indent=1, default=str))

    blender = stream = client = None
    try:
        port = mcp.free_port()
        blender, output["blender"] = mcp.launch_blender(config, folder, port)
        client = mcp.AddonClient(port)
        cwd = workdirs.fresh(config["claude_cwd"], config["claude_config_dir"], "claude")   # No project context.
        config_path = folder / "mcp-config.json"
        config_path.write_text(json.dumps(mcp.mcp_config(config, port), indent=2))
        server = mcp.probe_mcp_server(mcp.mcp_config(config, port)["mcpServers"][mcp.SERVER_NAME],
                                      mcp.clean_environment(TMPDIR=folder / "tmp"), folder / "tmp", list_only=True)
        output["mcp_server"] = server
        command = mcp.claude_command(config, config_path, server["tools"])
        output["claude_command"] = command
        stream = mcp.ClaudeStream(command, mcp.clean_environment(CLAUDE_CONFIG_DIR=config["claude_config_dir"],
                                                                 TMPDIR=folder / "tmp"), cwd, folder)
        usage = ClaudeProcessUsage()
        for index, message in enumerate(messages, 1):
            record = {"index": index, "id": message["id"], "stage": message.get("stage"),
                      "attachments": [Path(path).name for path in message.get("attachments") or ()]}
            waited = time.monotonic()
            while other_billed_runs():           # the machine and the account are shared: never overlap a gate
                time.sleep(30.0)
            record["waited_for_other_runs_s"] = round(time.monotonic() - waited, 1)
            client.settle()
            cold = stream.process is None
            sent = time.monotonic()
            if cold:
                stream.start()
            stream.send(message["text"], message.get("attachments") or ())
            events, result, problem = stream.collect(sent + args.message_timeout, message["id"])
            if problem == "deadline":
                stream.interrupt()
                more, result, problem = stream.collect(time.monotonic() + mcp.STOP_GRACE_S, message["id"])
                events += more
                record["timed_out"] = True
            finished = next((stamp for stamp, entry in reversed(events) if entry.get("type") == "result"),
                            time.monotonic())
            analysis = report.analyze_mcp_events(events, sent)
            record.update(runtime_start="cold" if cold else "warm", wall_ms=round((finished - sent) * 1000, 1),
                          api_calls=analysis["api_calls"], tool_call_count=analysis["tool_call_count"],
                          tool_errors=analysis["tool_errors"], tool_names=analysis["tool_names"],
                          api_retries=len(analysis["api_retries"]),
                          tool_round_trip_ms_total=analysis["tool_round_trip_ms_total"])
            record["result"] = report.result_summary(result)
            record["provider_usage"] = usage.turn(result) if result else None
            succeeded = bool(result) and result.get("subtype") == "success" and not result.get("is_error")
            record["status"] = "timeout" if record.get("timed_out") else "completed" if succeeded else "failed"
            try:
                record["scene"] = client.execute(FACTS)
            except (OSError, RuntimeError, ValueError) as error:
                record["scene"] = {"error": str(error)[:400]}
            # A copy after every message: stages/<stage>.blend after a stage's last message, else a rolling safety copy.
            path = run / "stages" / ("%s.blend" % (message["stage"] if message.get("stage_end") else "latest"))
            if path.name == "latest.blend" and path.is_file():
                path.unlink()
            try:
                client.execute(SAVE % str(path))
                deadline = time.monotonic() + 300
                while not path.is_file() and time.monotonic() < deadline:
                    time.sleep(0.5)
                time.sleep(1.0)
                record["stage_copy"] = str(path) if path.is_file() else "not written"
            except (OSError, RuntimeError, ValueError) as error:
                record["stage_copy"] = "failed: %s" % str(error)[:300]
            output["messages"].append(record)
            cost = add_cost(output, record)
            write()
            print("MCP_MESSAGE", json.dumps({key: record.get(key) for key in
                  ("index", "id", "status", "wall_ms", "api_calls", "tool_call_count", "tool_errors")}),
                  "cost", "unknown" if cost is None else round(cost, 3), "total", output["cost_usd"], flush=True)
            try:
                limit = limits.mcp_limit(events, result, record["provider_usage"])
            except Exception as error:               # the detector must never end a paid run
                limit, record["limit_check_error"] = None, str(error)[:300]
            if limit:
                output["stopped"] = "account limit: %s" % limit
                break
            if problem == "exited":
                output["stopped"] = "Claude Code exited"
                break
            if output["cost_usd"] > args.cap_usd:
                output["stopped"] = "cost cap of %g USD passed" % args.cap_usd
                break
    except Exception:
        output["error"] = traceback.format_exc()
    finally:
        if stream is not None:
            stream.close()
        if blender is not None:
            mcp.quit_blender(blender, client) if client else mcp.stop_process(blender, grace=1.)
    output["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write()
    print("MCP_REPLAY_DONE", json.dumps({"messages": len(output["messages"]), "cost_usd": output["cost_usd"],
                                         "cost_unknown_messages": output.get("cost_unknown_messages", 0),
                                         "stopped": output.get("stopped"), "error": bool(output.get("error"))}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
