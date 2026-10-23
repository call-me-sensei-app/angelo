# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Do Codex users find an MCP server's tools from a plain request? Angelo against Blender MCP (LIVE, billed).

Opt-in only: it runs real models on the account the given Codex profile is signed in to (nothing signs in). Codex
0.163 defers MCP tools on current models (doc/angelo/external_mcp.rst, "Codex loads Angelo's tools on demand"), so
this counts how often a request that names no tool leads the model to call the server at all. Both arms get the same
client, flags, prompts and logging; only the server differs:

* ``angelo``: Angelo's window (factory scene, behind, access on) with the app ``codex`` connected at read, edit and
  python (no tasks); a stand-in person approves each script card after two seconds.
* ``blender_mcp``: stock Blender with Blender Lab's MCP add-on and server (``bench_mcp_trial.launch_blender``).

Each server's stdio passes through a logger, so every ``tools/call`` is counted on the server side. The apps restart
for every prompt (a fresh factory scene). The pre-registered design and readout are in the run notes::

    python3 check_mcp_discovery.py --binary <Angelo> --stock-blender <Blender> --mcp-addon-dir <frozen mcp_addon> \\
        --mcp-server-dir <frozen mcp_server> --codex <codex 0.163> --codex-home <signed-in Codex profile> \\
        --out <folder ending in .noindex> [--models gpt-6.1-sol,gpt-6-astra]
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPTS = ("Make a stone bridge in my Blender scene.",
           "Add three red cubes in a row to my Blender scene.",
           "What objects are in my open Blender scene?",
           "Light my Blender scene for a sunset.",
           "Move the cube in my Blender scene up by 2 metres.")
RUN_TIMEOUT = 900


def tee(log_path, command):
    """The stdio logger between Codex and an MCP server: every JSON-RPC line, timestamped, both directions."""
    log = open(log_path, "a", buffering=1)
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def out():
        for line in child.stdout:
            log.write("%.3f S> %s" % (time.time(), line))
            sys.stdout.write(line)
            sys.stdout.flush()
    threading.Thread(target=out, daemon=True).start()
    for line in sys.stdin:
        log.write("%.3f C> %s" % (time.time(), line))
        child.stdin.write(line)
        child.stdin.flush()
    child.stdin.close()
    child.wait(10)


def server_calls(log_path):
    """``(calls, errors)``: the tools/call requests the server received and how many answered with an error."""
    calls, pending, errors = [], {}, 0
    if not Path(log_path).is_file():
        return calls, errors
    for line in Path(log_path).read_text(errors="replace").splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        direction, payload = parts[1], parts[2]
        try:
            message = json.loads(payload)
        except ValueError:
            continue
        if direction == "C>" and message.get("method") == "tools/call":
            pending[json.dumps(message.get("id"))] = (message.get("params") or {}).get("name")
            calls.append((message.get("params") or {}).get("name"))
        elif direction == "S>" and json.dumps(message.get("id")) in pending:
            result = message.get("result") or {}
            errors += bool(message.get("error") or result.get("isError"))
    return calls, errors


def codex_events(path):
    """Shell commands, tokens and the final message from Codex's ``--json`` events."""
    shell, usage, final = 0, {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}, ""
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "command_execution":
            shell += 1
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            final = item.get("text", "")
        if event.get("type") == "turn.completed":
            for key in usage:
                usage[key] += int((event.get("usage") or {}).get(key) or 0)
    return shell, usage, final


def stand_in_model():
    """A local OpenAI Responses endpoint that answers every turn "OK" (``--dry-run``); returns its URL."""
    import http.server
    import socketserver
    from team_gateway_probe import openai_stream
    count = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _reply(self, body, content_type="application/json"):
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._reply(b'{"object": "list", "data": []}')

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            count.append(1)
            self._reply(openai_stream(len(count)), "text/event-stream")

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


def start_angelo(args, root, env):
    import gui_launch
    for name in ("ready", "stop"):
        (root / name).unlink(missing_ok=True)
    process = subprocess.Popen(
        [args.binary, "--factory-startup", "--disable-autoexec", *gui_launch.window_flags("behind"),
         "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()), "--",
         "--native-phase", "gui", "--root", str(root)],
        env=env, stdout=(root / "angelo.log").open("a"), stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 200
    while not (root / "ready").is_file():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Angelo did not start; see angelo.log")
        time.sleep(0.3)
    return process


def stop_angelo(root, process):
    (root / "stop").write_text("1")
    try:
        process.wait(60)
    except subprocess.TimeoutExpired:
        process.kill()


def run_codex(args, model, prompt, folder, server):
    """One ``codex exec`` with one MCP server; ``server`` is (name, command, args, env)."""
    name, command, arguments, server_env = server
    work = folder / "cwd"
    work.mkdir(parents=True, exist_ok=True)
    log = folder / "mcp.log"
    wrapped = ["-I", str(Path(__file__).resolve()), "--tee", str(log), "--", command, *arguments]
    config = ["-c", "mcp_servers.%s.command=%s" % (name, json.dumps(sys.executable)),
              "-c", "mcp_servers.%s.args=%s" % (name, json.dumps(wrapped)),
              "-c", "mcp_servers.%s.tool_timeout_sec=900" % name, "-c", "mcp_servers.%s.startup_timeout_sec=60" % name]
    for key, value in server_env.items():
        config += ["-c", "mcp_servers.%s.env.%s=%s" % (name, key, json.dumps(str(value)))]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("OPENAI", "CODEX", "ANGELO", "BLENDER", "PYTHON", "ANTHROPIC", "CLAUDE"))}
    env["CODEX_HOME"] = args.codex_home
    if args.provider:
        env["PROBE_KEY"] = "sk-dry-run"
    started = time.monotonic()
    with (folder / "codex.jsonl").open("w") as handle:
        done = subprocess.run([args.codex, "exec", "--skip-git-repo-check", "--json", "--model", model,
                               "--dangerously-bypass-approvals-and-sandbox", *args.provider, *config, prompt],
                              env=env, cwd=work, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.PIPE,
                              text=True, timeout=RUN_TIMEOUT)
    calls, errors = server_calls(log)
    shell, usage, final = codex_events(folder / "codex.jsonl")
    started_server = log.is_file() and "initialize" in log.read_text(errors="replace")
    return {"exit": done.returncode, "seconds": round(time.monotonic() - started, 1), "server_started": started_server,
            "calls": calls, "call_errors": errors, "found": bool(calls), "used": len(calls) > errors, "shell": shell,
            "usage": usage, "final": final[-500:], "stderr": done.stderr[-600:]}


def main():
    sys.path.insert(0, str(HERE))
    import bench_mcp_trial
    from external_app_env import isolated_environment
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("binary", "stock-blender", "mcp-addon-dir", "mcp-server-dir", "codex", "codex-home", "out"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--models", default="gpt-6.1-sol,gpt-6-astra")
    parser.add_argument("--prompts", default="1,2,3,4,5")
    parser.add_argument("--port", type=int, default=39417)
    parser.add_argument("--dry-run", action="store_true",
                        help="unbilled harness check: a local stand-in model that answers OK (no account is used)")
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    binary = Path(args.binary).resolve(strict=True)
    resources = binary.parents[1] / "Resources" / "5.2"
    angelo_root = root / "angelo-app"
    env = isolated_environment(angelo_root)
    sys.path.insert(0, str(resources / "scripts/addons_core"))
    from angelo_agent.external_clients import EDIT, PYTHON, READ, AppRegistry
    AppRegistry(env["ANGELO_STATE_DIR"]).connect("codex", levels=(READ, EDIT, PYTHON))
    angelo_server = ("angelo", str(resources / "python/bin/python3.13"),
                     ["-I", str(resources / "scripts/angelo/angelo_mcp.py"), "--client", "codex", "--state-dir",
                      env["ANGELO_STATE_DIR"]], {})
    config = {"stock_blender": args.stock_blender, "mcp_addon_dir": args.mcp_addon_dir,
              "mcp_server_dir": args.mcp_server_dir, "window_state": "behind"}
    entry = bench_mcp_trial.mcp_config(config, args.port)["mcpServers"][bench_mcp_trial.SERVER_NAME]
    blender_server = ("blender", entry["command"], entry["args"], entry["env"])
    if args.dry_run:
        url = stand_in_model()
        args.provider = ["-c", "model_provider=probe", "-c", "model_providers.probe.name=probe",
                         "-c", "model_providers.probe.base_url=" + json.dumps(url + "/v1"),
                         "-c", "model_providers.probe.env_key=PROBE_KEY", "-c", "model_providers.probe.wire_api=responses"]
        args.codex_home = str(root / "dry-codex-home")
        Path(args.codex_home).mkdir(exist_ok=True)
    else:
        args.provider = []
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "binary": str(binary), "dry_run": args.dry_run,
              "codex": subprocess.run([args.codex, "--version"], capture_output=True, text=True).stdout.strip(),
              "stock_blender": args.stock_blender, "runs": []}
    for model in args.models.split(","):
        for number in [int(value) for value in args.prompts.split(",")]:
            prompt = PROMPTS[number - 1]
            arms = ("angelo", "blender_mcp") if number % 2 else ("blender_mcp", "angelo")
            for arm in arms:
                folder = root / model / ("p%d" % number) / arm
                folder.mkdir(parents=True, exist_ok=True)
                process = None
                try:
                    if arm == "angelo":
                        process = start_angelo(args, angelo_root, env)
                        result = run_codex(args, model, prompt, folder, angelo_server)
                    else:
                        process, _info = bench_mcp_trial.launch_blender(config, folder, args.port)
                        result = run_codex(args, model, prompt, folder, blender_server)
                except Exception as error:  # noqa: BLE001 - an infrastructure failure is recorded, never a model miss.
                    result = {"infrastructure_error": "%s: %s" % (type(error).__name__, error)}
                finally:
                    if process is not None:
                        if arm == "angelo":
                            stop_angelo(angelo_root, process)
                        else:
                            bench_mcp_trial.stop_process(process)
                report["runs"].append(dict(result, model=model, prompt=number, arm=arm))
                (root / "report.json").write_text(json.dumps(report, indent=2))
                print(model, number, arm, {key: result.get(key) for key in
                                           ("found", "used", "calls", "shell", "usage", "infrastructure_error")},
                      flush=True)
    summary = {}
    for run in report["runs"]:
        key = "%s/%s" % (run["model"], run["arm"])
        row = summary.setdefault(key, {"runs": 0, "found": 0, "used": 0, "shell": 0, "invalid": 0,
                                       "input_tokens": [], "output_tokens": []})
        if "infrastructure_error" in run or not run.get("server_started"):
            row["invalid"] += 1
            continue
        row["runs"] += 1
        row["found"] += run["found"]
        row["used"] += run["used"]
        row["shell"] += bool(run["shell"])
        row["input_tokens"].append(run["usage"]["input_tokens"])
        row["output_tokens"].append(run["usage"]["output_tokens"])
    report["summary"] = summary
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(summary, indent=1))
    return 0


if "--tee" in sys.argv:
    _arguments = sys.argv[sys.argv.index("--tee") + 1:]
    tee(_arguments[0], _arguments[_arguments.index("--") + 1:])
    raise SystemExit(0)

if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        sys.exit(main())
    raise SystemExit

import addon_utils  # noqa: E402
import bpy  # noqa: E402

addon_utils.enable("angelo_agent", default_set=False, persistent=False)   # As the fixtures do under --factory-startup.
bpy.context.preferences.use_preferences_save = False
_root = Path(sys.argv[sys.argv.index("--root") + 1])
_seen = {}


def _start():
    addon = bpy.context.preferences.addons.get("angelo_agent")
    if addon is None:
        return 0.3
    addon.preferences.allow_local_cli = True
    if not (Path(os.environ["ANGELO_STATE_DIR"]) / "cli/endpoint.json").is_file():
        return 0.3
    (_root / "ready").write_text("1")
    return None


def _person():
    """A stand-in for the person: approve each script card two seconds after it appears."""
    from angelo_agent import ui
    now = time.monotonic()
    for controller in list(ui._controllers.values()):
        for call in list(controller.pending.values()):
            if call.approval and not call.executing and not call.automatic:
                first = _seen.setdefault(call.id, now)
                if now - first >= 2.0:
                    if call.name == "angelo_execute_python":
                        controller.review_script(call)
                    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                        bpy.ops.angelo.approve(call_id=call.id, session_id=controller.state["id"])
    if (_root / "stop").is_file():
        bpy.ops.wm.quit_blender()
        return None
    return 0.5


bpy.app.timers.register(_start, first_interval=1.0)
bpy.app.timers.register(_person, first_interval=2.0, persistent=True)
