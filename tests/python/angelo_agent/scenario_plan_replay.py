# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled plan replay: scripted model output through the real host, graded by a scenario's closed checks.

Usage, from the repository root (it starts a GUI process; no account, no network)::

    export GIT_LFS_SKIP_SMUDGE=1
    nice -n 10 python3 tests/python/angelo_agent/scenario_plan_replay.py \\
      --binary <Angelo.app>/Contents/MacOS/Angelo --task-set scenario-bridge \\
      --replay <plans.json> --runtime codex --output <new folder>

``--replay`` is the file ``scenario_plan_replay_session.py`` describes: per
scenario turn, the calls, scripts and plans a model wrote (or could have
written). The trial driver sends the scenario's own messages, approves as a
scenario run does and grades each turn with the scenario's closed checks; the
session then saves the scene and probes Undo, exactly as after a billed run.
``replay.json`` in the output folder holds, per turn, the bytes of the model
output as written, the checks' result and the receipt's host phases.

``--harness`` names another checkout's ``tests/python/angelo_agent`` when the
scenario set lives there (its trial driver and checks run against this
checkout's add-on). The exit code is 0 only when every turn passed its checks.
"""

import argparse
import json
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def run(binary, task_set, replay, output, *, runtime="codex", harness=None, task_timeout=300.):
    """Run one replay trial; returns ``{"success", "turns": {...}, ...}`` and writes ``replay.json``."""
    harness = Path(harness).resolve() if harness else HERE
    for path in (str(ROOT / "scripts/addons_core"), str(harness)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import eval_live_agent
    import scenario_specs as specs
    from angelo_agent.connections import ConnectionStore
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source-accounts"
    record = ConnectionStore(source).add("OPENAI" if runtime == "codex" else "ANTHROPIC", "SUBSCRIPTION",
                                         name="Plan replay scripted runtime")
    eval_live_agent.isolated_connection(source, record["id"], output / "accounts")
    scratch = output / "isolation"
    for name in ("home", "tmp"):
        (scratch / name).mkdir(parents=True)
    tasks = specs.tasks(specs.scenario_id(task_set))
    args = types.SimpleNamespace(
        task_set=task_set, long_tasks=40, observation="turn-context", context_rebase_tokens=0, after_failure="skip",
        cache_ttl="", approve_delegation="", python_approvals="approve", connection_id=record["id"], team_mode="",
        task_timeout=task_timeout, effort="low", model="", input_isolation="on", stable_state_dir="",
        demo_record=False)
    saved_script, saved_environment = eval_live_agent.SESSION_SCRIPT, dict(os.environ)
    eval_live_agent.SESSION_SCRIPT = HERE / "scenario_plan_replay_session.py"
    # No sign-in is needed, so the trial is isolated completely, HOME and the macOS user folder included.
    os.environ.update(ANGELO_PLAN_REPLAY=str(Path(replay).resolve()), ANGELO_PLAN_REPLAY_RUNTIME=runtime,
                      ANGELO_PLAN_REPLAY_HARNESS=str(harness), HOME=str(scratch / "home"),
                      CFFIXED_USER_HOME=str(scratch / "home"), TMPDIR=str(scratch / "tmp"),
                      **{specs.REFERENCES_ENV: str(specs.reference_root())})
    try:
        trial = eval_live_agent.run_trial(Path(binary), output, output / "accounts", args, 1, len(tasks))
    finally:
        eval_live_agent.SESSION_SCRIPT = saved_script
        os.environ.clear()
        os.environ.update(saved_environment)
    written_file = output / "trial-01" / "qa" / "replay-bytes.json"
    written = json.loads(written_file.read_text()) if written_file.is_file() else {}
    turns = {}
    for task in trial.get("tasks", []):
        check = task.get("host_check") or {}
        receipt = task.get("receipt") or {}
        entry = written.get(task["task"], {})
        turns[task["task"]] = {
            "status": task.get("status"), "checks_passed": check.get("passed"), "checks_total": check.get("total"),
            "passed": bool(task.get("host_check_passed")), "state_digest": task.get("state_digest"),
            "failed_checks": [row.get("label") for row in check.get("results", ()) if not row.get("passed")],
            "plan_bytes": sum(item["bytes"] for item in entry.get("plans", ())),
            "plans": len(entry.get("plans", ())),
            "program_operations": sum(item.get("program_operations", 0) for item in entry.get("plans", ())),
            "call_bytes": sum(item["bytes"] for item in entry.get("calls", ())), "calls": len(entry.get("calls", ())),
            "script_bytes": sum(item["bytes"] for item in entry.get("scripts", ())),
            "corrections": entry.get("corrections", []), "call_errors": entry.get("call_errors", []),
            "approved": task.get("approved"),
            "host_ms": {key: (receipt.get("phases") or {}).get(key) for key in
                        ("staging_ms", "verification_ms", "commit_ms", "total_ms")},
            "counts": receipt.get("counts")}
    result = {"task_set": task_set, "runtime": runtime, "binary": str(binary), "paid_requests": 0,
              "success": bool(turns) and len(turns) == len(tasks) and all(
                  turn["passed"] and not turn["corrections"] and not turn["call_errors"] for turn in turns.values()),
              "turns": turns, "scenario": trial.get("scenario"), "exit_code": trial.get("exit_code"),
              "elapsed_s": trial.get("elapsed_s"), "error": trial.get("error"), "aborted": trial.get("aborted")}
    (output / "replay.json").write_text(json.dumps(result, indent=1, default=str))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay scripted plans through the real host, unbilled.")
    parser.add_argument("--binary", required=True)
    parser.add_argument("--task-set", required=True)
    parser.add_argument("--replay", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime", choices=("codex", "claude"), default="codex")
    parser.add_argument("--harness", default="", help="another checkout's tests/python/angelo_agent")
    parser.add_argument("--task-timeout", type=float, default=300.)
    args = parser.parse_args(argv)
    result = run(args.binary, args.task_set, args.replay, args.output, runtime=args.runtime, harness=args.harness,
                 task_timeout=args.task_timeout)
    for name, turn in result["turns"].items():
        print("%-14s %-9s checks %s/%s  plan %5d B in %d  calls %4d B  scripts %5d B" % (
            name, turn["status"], turn["checks_passed"], turn["checks_total"], turn["plan_bytes"], turn["plans"],
            turn["call_bytes"], turn["script_bytes"]))
        for refused in turn["call_errors"]:
            print("  refused %s: %s" % (refused["tool"], refused["error"]))
    print("PLAN_REPLAY", json.dumps({"success": result["success"], "paid_requests": 0}))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
