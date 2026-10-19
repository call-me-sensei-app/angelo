# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``bench_mcp_vs_angelo.py --warm-up-only``: one warm-up message per selected side, never a trial (``eval_warm_up``).

Each side's warm-up goes through that side's trial launch, in the run's own output folder, so it writes the very
prefix the trials will read:

* MCP on Claude: the trial's Claude Code command (``bench_mcp_trial.claude_command`` with the task set's client
  allowances), the run's MCP server, account profile and the run's working directory (``bench_workdirs``), whose
  path the client's system prompt names. Stock Blender is not started: the client lists the server's tools without
  it, and the message asks for no tool.
* MCP on Codex: the trial's ``codex app-server`` command and environment (``bench_mcp_codex``), the run's Codex
  profile and working directory, and an ephemeral thread as a trial's. Stock Codex keys its prompt cache by thread,
  so this warms only what threads share; it runs for symmetry with Angelo's side.
* Angelo: the trial's live-eval command (``bench_mcp_vs_angelo.angelo_command``) with ``--warm-up-only``.

Everything is written under ``warm-up/`` (``<side>-NN/`` and ``WARM-UP-NN.json``), never as ``*-trial-NN`` and
never into the run's plan, so trial counting, ``--resume``, ``gather`` and every summary and claim tool ignore it.
"""

import json
import time
from pathlib import Path

import bench_mcp_scenario as scenario
import bench_mcp_trial as mcp
import bench_workdirs as workdirs
import eval_diagnostics as diagnostics
import eval_provider_limits as limits
import eval_warm_up
import mcp_request_parameters
import oneshot_metrics

WAIT_S = 300.


def number(output):
    """The next warm-up number of this run folder (each call is its own record)."""
    folder = Path(output) / eval_warm_up.FOLDER
    taken = [int(path.stem.rsplit("-", 1)[-1]) for path in folder.glob("WARM-UP-*.json") if path.stem[-2:].isdigit()]
    return max(taken, default=0) + 1


def mcp_side(config, folder, stamp=None):
    """One warm-up message through the MCP side's trial launch; its record (``config``: the bench's MCP config)."""
    from angelo_agent.provider_usage import ClaudeProcessUsage
    folder.mkdir(parents=True)
    (folder / "tmp").mkdir()
    record = {"side": "mcp", "warm_up": True, "prompt": eval_warm_up.PROMPT}
    cwd = workdirs.fresh(config["claude_cwd"], config.get("claude_config_dir"), "claude")
    config_path = folder / "mcp-config.json"
    config_path.write_text(json.dumps(mcp.mcp_config(config, mcp.free_port()), indent=2))
    server = json.loads(config_path.read_text())["mcpServers"][mcp.SERVER_NAME]
    probed = mcp.probe_mcp_server(server, mcp.clean_environment(TMPDIR=folder / "tmp"), folder / "tmp", list_only=True)
    command = scenario.client_command(mcp.claude_command(config, config_path, probed["tools"]), config, folder)
    record["claude_command"] = command
    stream = mcp.ClaudeStream(command, mcp.clean_environment(
        CLAUDE_CONFIG_DIR=config["claude_config_dir"], TMPDIR=folder / "tmp",
        **config.get("fake_model_environment", {})), cwd, folder)
    try:
        stream.start()
        sent = time.monotonic()
        stream.send(eval_warm_up.PROMPT)
        events, result, problem = stream.collect(sent + WAIT_S, "warm-up")
        if problem == "deadline":
            stream.interrupt()
            more, result, problem = stream.collect(time.monotonic() + mcp.STOP_GRACE_S, "warm-up")
            events += more
        usage = ClaudeProcessUsage().turn(result) if result else None
        record.update(wall_ms=round((time.monotonic() - sent) * 1000, 3),
                      status="completed" if result and not result.get("is_error") else (problem or "failed"),
                      provider_usage=oneshot_metrics.with_write_split(usage, events) if result else None,
                      cost_usd=(result or {}).get("total_cost_usd"),
                      request_parameters=mcp_request_parameters.mcp(command, events))
        limit = limits.mcp_limit(events, result, record["provider_usage"])
        if limit:
            record.update(status="invalid", invalid=limits.INVALID, limit=limit)
    finally:
        stream.close()
    diagnostics.mark_transcript(folder / "claude-stream.jsonl", stamp or {}, stream=True)
    (folder / "warm-up.json").write_text(json.dumps(record, indent=2, default=str))
    return record


def mcp_codex_side(config, folder, stamp=None, fake_base_url=None):
    """One warm-up message through the Codex MCP side's trial launch (``fake_base_url``: the dry run's endpoint)."""
    import bench_mcp_codex as codex_mcp
    from angelo_agent.provider_usage import CodexThreadUsage
    folder.mkdir(parents=True)
    for name in ("tmp", "codex-sqlite"):
        (folder / name).mkdir()
    record = {"side": "mcp", "runtime": "codex", "warm_up": True, "prompt": eval_warm_up.PROMPT}
    cwd = workdirs.fresh(config["claude_cwd"], config.get("codex_home"), "codex")
    command = record["codex_command"] = codex_mcp.codex_command(config, mcp.free_port(), fake_base_url)
    stream = codex_mcp.CodexStream(command, codex_mcp.environment(config, folder, fake_base_url), cwd, folder, config)
    usage = CodexThreadUsage()
    try:
        sent = time.monotonic()
        stream.start()
        usage.thread_started(stream.thread_id)
        usage.begin_turn(stream.thread_id)
        stream.send(eval_warm_up.PROMPT)
        events, completed, problem = stream.collect(sent + WAIT_S, "warm-up")
        if problem == "deadline":
            stream.interrupt()
            more, completed, problem = stream.collect(time.monotonic() + mcp.STOP_GRACE_S, "warm-up")
            events += more
        for _, message in events:
            if message.get("method") == "thread/tokenUsage/updated":
                usage.observe(stream.thread_id, (message.get("params") or {}).get("tokenUsage"))
        turn = (completed or {}).get("turn") or {}
        record.update(wall_ms=round((time.monotonic() - sent) * 1000, 3), thread_id=stream.thread_id,
                      status="completed" if turn.get("status") == "completed" else (problem or turn.get("status")
                                                                                   or "failed"),
                      provider_usage=usage.finish_turn(round((time.monotonic() - sent) * 1000)),
                      request_parameters=mcp_request_parameters.codex(config, events), account=stream.account)
        limit = limits.codex_limit([message for _, message in events], completed)
        if limit:
            record.update(status="invalid", invalid=limits.INVALID, limit=limit)
    finally:
        stream.close()
    diagnostics.mark_transcript(folder / "codex-stream.jsonl", stamp or {}, stream=True)
    (folder / "warm-up.json").write_text(json.dumps(record, indent=2, default=str))
    return record


def angelo_side(args, output, folder, angelo_command):
    """One warm-up message through the Angelo side's trial launch (``angelo_command``: the bench's builder)."""
    import subprocess
    tmp = folder.parent / (folder.name + "-tmp")
    tmp.mkdir(parents=True)
    command, _workspace = angelo_command(args, output, folder)
    command = command + ["--warm-up-only"]
    record = {"side": "angelo", "warm_up": True, "prompt": eval_warm_up.PROMPT, "harness_command": command}
    started, since = time.monotonic(), time.time()
    with (folder.parent / (folder.name + ".log")).open("w") as log:
        try:
            record["exit_code"] = subprocess.run(command, cwd=Path(__file__).resolve().parents[3],
                                                 env=mcp.clean_environment(TMPDIR=tmp), stdout=log,
                                                 stderr=subprocess.STDOUT, timeout=2 * WAIT_S + 300).returncode
        except subprocess.TimeoutExpired:
            record["exit_code"] = "timeout"
    record["elapsed_s"] = round(time.monotonic() - started, 3)
    if getattr(args, "runtime", "claude") == "codex" and getattr(args, "codex_home", ""):
        from bench_mcp_vs_angelo import copy_codex_rollouts
        record["rollouts"] = copy_codex_rollouts(args.codex_home, since, folder.parent / (folder.name + "-rollouts"))
    try:
        trial = (json.loads((folder / "eval-live-agent.json").read_text()).get("trials") or [{}])[0]
    except (OSError, ValueError):
        trial = {}
    warm = trial.get("warm_up") or {}
    record.update(status=warm.get("status") or "failed", wall_ms=warm.get("send_to_idle_ms"),
                  provider_usage=warm.get("provider_usage"), cost_usd=warm.get("cost_usd"),
                  declined=warm.get("declined"), error=trial.get("error") or trial.get("aborted"))
    (folder.parent / (folder.name + ".json")).write_text(json.dumps(record, indent=2, default=str))
    return record


def run(args, output, config, angelo_command, price):
    """``--warm-up-only``: the selected sides' warm-ups, then exit (0 when each completed)."""
    index = number(output)
    folder = Path(output) / eval_warm_up.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    stamp = diagnostics.stamp(args)
    sides = {}
    if args.sides in ("both", "mcp"):
        side = mcp_codex_side if config.get("runtime") == "codex" else mcp_side
        sides["mcp"] = side(config, folder / ("mcp-%02d" % index), stamp)
    if args.sides in ("both", "angelo"):
        sides["angelo"] = angelo_side(args, output, folder / ("angelo-%02d" % index), angelo_command)
    price(list(sides.values()))   # Its list price, as a trial's (bench_mcp_vs_angelo.price_records).
    summary = {"warm_up": True, "note": eval_warm_up.NOTE, "prompt": eval_warm_up.PROMPT, "number": index,
               "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "conditions": {"model": args.model, "effort": args.effort, "task_set": getattr(args, "task_set", ""),
                              **({"diagnostics": stamp} if stamp else {})},
               "sides": sides}
    (folder / ("WARM-UP-%02d.json" % index)).write_text(json.dumps(summary, indent=2, default=str))
    print("WARM_UP", json.dumps({side: {key: record.get(key) for key in ("status", "wall_ms", "list_price_usd")}
                                 for side, record in sides.items()}), flush=True)
    return 0 if sides and all(record.get("status") == "completed" for record in sides.values()) else 1
