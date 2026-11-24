# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Runs the first-hour cases (``eval_firsthour_tasks``) for one arm and tabulates them. BILLED.

One arm is one build and one model on one connection. For every case of the group it runs ``eval_live_agent.py
--task-set firsthour-<case> --trials N`` (each trial a fresh process and chat), with the same flags for every arm
(effort, observation, cost cap, timeout), after one untimed, ungraded warm-up run labelled as such
(tests-warm-cache rule). Every trial counts: a trial the harness marks invalid (rate-limited) is reported and
excluded by the harness's own rule, for every arm alike. The table lists each case's passes out of its valid trials
and the failed checks, so misses are reported, not only the rate.

    python3 tests/python/angelo_agent/firsthour_run.py --authorize-billed-runs --label before-opus \\
        --group firsthour --trials 2 --blender <Angelo> --connection-id <uuid> \\
        --source-connections-dir <test profile connections> [--codex-home <profile>] --out <new folder>

``--plan`` prints the commands and runs nothing. ``tabulate <arm folders...>`` prints the before/after table.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import eval_firsthour_tasks as cases  # noqa: E402
import eval_send_record as send_record  # noqa: E402

COMMON = ["--observation", "turn-context", "--task-timeout", "300", "--cost-cap", "3"]
WARM_UP_CASE = "firsthour_object_vanished"   # A short dev case; its warm-up run is never counted.


def command(args, task_set, trials, out):
    cmd = [sys.executable, str(HERE / "eval_live_agent.py"), "--authorize-billed-runs", "--connection-id",
           args.connection_id, "--source-connections-dir", args.source_connections_dir, "--blender", args.blender,
           "--task-set", task_set, "--trials", str(trials), "--output", str(out)] + COMMON
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    if args.codex_home:
        cmd += ["--codex-home", args.codex_home]
    return cmd


def outcomes(folder):
    """``[(trial, state, failed checks, invalid)]`` of one case's run folder (its finished record only)."""
    report = send_record.read(Path(folder) / send_record.NAME)
    rows = []
    invalid = {t.get("trial") for t in report.get("invalid_trials") or []} if isinstance(
        report.get("invalid_trials"), list) else set()
    for trial in report.get("trials", []):
        for record in trial.get("tasks", []):
            checks = (record.get("host_check") or {}).get("checks") or {}
            rows.append((record.get("trial"), record.get("grade") or record.get("status"),
                         sorted(k for k, v in checks.items() if not v), trial.get("invalid") is not None
                         or record.get("trial") in invalid))
    return rows


def run(args):
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit("Use a new, empty --out folder")
    out.mkdir(parents=True, exist_ok=True)
    plan = [("warm-up", cases.task_set(cases.case(WARM_UP_CASE)), 1, out / "warm-up")]
    plan += [(c["id"], cases.task_set(c), args.trials, out / c["id"]) for c in cases.cases(args.group)]
    (out / "arm.json").write_text(json.dumps({"label": args.label, "group": args.group, "trials": args.trials,
                                              "blender": args.blender, "model": args.model, "effort": args.effort,
                                              "common": COMMON, "warm_up": WARM_UP_CASE}, indent=2))
    for name, task_set, trials, folder in plan:
        cmd = command(args, task_set, trials, folder)
        print("RUN", name, " ".join(cmd) if args.plan else "", flush=True)
        if not args.plan:
            subprocess.run(cmd, check=False)


def tabulate(folders):
    """Each arm's passes per case (valid trials only), and the failed checks of every miss. A case whose folder has
    no finished record (written at a send, between trials, or not yet) is "unfinished": shown, never counted."""
    arms = {}
    for folder in map(Path, folders):
        arm = json.loads((folder / "arm.json").read_text())
        rows = {}
        for c in cases.CASES:
            if send_record.finished(folder / c["id"] / send_record.NAME):
                rows[c["id"]] = outcomes(folder / c["id"])
            elif (folder / c["id"]).is_dir():
                rows[c["id"]] = "unfinished"
        arms[arm["label"]] = rows
    lines = ["| case | split | " + " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
    totals = {label: [0, 0, 0, 0] for label in arms}
    for c in cases.CASES:
        cells = []
        for label, rows in arms.items():
            got = rows.get(c["id"])
            if got is None:
                cells.append("-")
                continue
            if got == "unfinished":
                totals[label][3] += 1
                cells.append(got)
                continue
            valid = [r for r in got if not r[3]]
            passed = sum(1 for r in valid if r[1] == "passed")
            totals[label][0] += passed
            totals[label][1] += len(valid)
            totals[label][2] += len(got) - len(valid)
            misses = sorted({check for r in valid if r[1] != "passed" for check in (r[2] or [r[1]])})
            cells.append("%d/%d%s" % (passed, len(valid), (" (" + ", ".join(misses) + ")") if misses else ""))
        lines.append("| %s | %s | %s |" % (c["id"][len("firsthour_"):], c["split"], " | ".join(cells)))
    lines.append("| **total** | | " + " | ".join("%d/%d (+%d invalid, %d unfinished)" % tuple(t)
                                                  for t in totals.values()) + " |")
    return "\n".join(lines)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["tabulate"]:
        print(tabulate(argv[1:]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--authorize-billed-runs", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--label", required=True)
    parser.add_argument("--group", choices=cases.GROUPS, default="firsthour")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--blender", required=True)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--source-connections-dir", required=True)
    parser.add_argument("--codex-home", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not (args.authorize_billed_runs or args.plan):
        raise SystemExit("Every case is a billed turn: pass --authorize-billed-runs (or --plan)")
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
