# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The unbilled dry run of the parallel-agents benchmark: both arms in the real Angelo, a scripted model.

    python3 parallel_dry_run.py --binary .../Angelo --output DIR [--arms single parallel]

Each arm is one trial of the unchanged live-eval launcher and trial driver (``eval_live_agent.run_trial``) on the
chess test, with ``parallel_dry_run_session.py`` as the in-app script: a fake signed-in record, no account, no
network, no provider process. The parallel arm runs with ``--parallel-agents`` exactly as a billed one. The run
passes when each arm's turn completed and every end-of-session check of the test passed; it prints and writes
``parallel-dry-run.json`` with each arm's wall clock and main-loop lateness (the mechanics' own cost, no model).
A GUI process: Mac 1 or a Windows VM, never Mac 2.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import types

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import eval_live_agent  # noqa: E402
import eval_send_record as send_record  # noqa: E402  (the trial's record, written as the launcher's)
import scenario_specs as specs  # noqa: E402

TASK_SET = "oneshot-chess"


def run_arm(binary, output, arm, timeout):
    from angelo_agent.connections import ConnectionStore
    folder = output / arm
    folder.mkdir(parents=True)
    source = folder / "source-accounts"
    record = ConnectionStore(source).add("OPENAI", "SUBSCRIPTION", name="Parallel dry-run scripted runtime")
    connection = eval_live_agent.isolated_connection(source, record["id"], folder / "accounts")
    scratch = folder / "isolation"
    for name in ("home", "tmp"):
        (scratch / name).mkdir(parents=True)
    args = types.SimpleNamespace(
        task_set=TASK_SET, long_tasks=40, observation="legacy", context_rebase_tokens=0, after_failure="skip",
        cache_ttl="", approve_delegation="", python_approvals="approve", connection_id=record["id"], team_mode="",
        task_timeout=timeout, effort="low", model="", input_isolation="on", stable_state_dir="", demo_record=False,
        ask_why_on_miss=False, debug_thinking=False, warm_up_only=False, parallel_agents=arm == "parallel")
    saved_script, saved_environment = eval_live_agent.SESSION_SCRIPT, dict(os.environ)
    eval_live_agent.SESSION_SCRIPT = HERE / "parallel_dry_run_session.py"
    os.environ.update(ANGELO_PARALLEL_DRY_RUN="1", HOME=str(scratch / "home"), CFFIXED_USER_HOME=str(scratch / "home"),
                      TMPDIR=str(scratch / "tmp"), **{specs.REFERENCES_ENV: str(specs.reference_root())})
    try:
        trial = eval_live_agent.run_trial(Path(binary), folder, folder / "accounts", args, 1, 1)
    finally:
        eval_live_agent.SESSION_SCRIPT = saved_script
        os.environ.clear()
        os.environ.update(saved_environment)
    send_record.write(folder / send_record.NAME, eval_live_agent.aggregate(
        {"dry_run": True, "task_set": TASK_SET, "arm": arm}, connection, [trial]))
    task = (trial.get("tasks") or [{}])[0]
    checks = (task.get("host_check") or {})
    results = checks.get("results") or []
    return {"arm": arm, "status": task.get("status"), "send_to_idle_s": round((task.get("send_to_idle_ms") or 0) / 1000, 2),
            "checks_passed": sum(1 for entry in results if entry.get("passed")), "checks_total": len(results),
            "failed": [entry.get("label") for entry in results if not entry.get("passed")][:20],
            "main_loop": {k: v for k, v in task.items() if k.startswith("main_loop_")},
            "parallel": task.get("parallel"), "error": trial.get("error")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--arms", nargs="+", choices=("single", "parallel"), default=["single", "parallel"])
    parser.add_argument("--task-timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    arms = [run_arm(args.binary, output, arm, args.task_timeout) for arm in args.arms]
    passed = all(a["status"] == "completed" and a["checks_total"] and a["checks_passed"] == a["checks_total"]
                 for a in arms)
    report = {"passed": passed, "arms": arms}
    (output / "parallel-dry-run.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
