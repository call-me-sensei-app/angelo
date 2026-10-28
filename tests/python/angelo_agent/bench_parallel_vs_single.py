# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Single-chat Angelo against Angelo with parallel agents on, one one-shot test, interleaved (BILLED).

    python3 bench_parallel_vs_single.py --authorize-billed-runs --task-set oneshot-chess --trials 5 \\
        --output DIR [--first parallel] [--preflight CMD] -- <bench_mcp_vs_angelo.py Angelo-side arguments>

Both arms are ``bench_mcp_vs_angelo.py --sides angelo`` with the arguments after ``--``, byte for byte; the
parallel arm adds only ``--angelo-parallel-agents`` (the trial's Angelo then has ``ANGELO_ENABLE_PARALLEL_AGENTS=1``;
the single arm has it explicitly off) and, with ``--worker-cap N``, ``--angelo-worker-cap N`` (at most N workers at
once besides the lead; BENCH-PREREG Amendment B1, draft). Each arm has its own folder under ``DIR`` (``single/``, ``parallel/``) and
its own untimed warm-up (``--warm-up-only``), run right before that arm's first trial so each arm's first trial
starts equally warm. Then trial ``n`` of each arm runs in turn, the first arm alternating with ``n`` (``--first``
names trial 1's), each as one ``--resume --max-new-trials 1`` step, so the arms share the machine's conditions as
closely as an interleaved run can. ``--preflight`` (Mac 1: ``mac1_preflight.py
--min-free-gb 30 --record ...``) runs before every warm-up and trial; a preflight that fails stops the run before
the step, never during it, and the run says so. Nothing here grades: ``bench_parallel_grade.py`` grades both
folders, blind to the arm, and ``bench_parallel_report.py`` applies the pre-registered rules.

The run stops at the first step that stops, with its code, and launches nothing after it (break-it E2E-10 M-3): the
bench's own codes (1 a side that broke at its start or sign-in, 2 a refusal, 3 a provider limit) or ``PREFLIGHT``
(4: the machine is unfit; wait, then run again). Run again with the same arguments, it continues the same schedule
(break-it E2E-11 M-4): a trial the bench recorded as finished (its own ``trial_done``) is never run again, every other
step runs in the order trial ``n`` puts the arms, and each arm warms up again right before its first step of the new
run (``--skip-warm-up`` to not). Each step is checked to have finished the trial it was scheduled for. A trial the
harness ended before its message (``sent_nothing``) is pre-registered as invalid: it is set aside and run again
(break-it E2E-12 M-3). Every step and every stop is appended to ``interleave.json`` (replaced whole, atomically); a run
whose arguments differ from it is refused.
"""

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.

HERE = Path(__file__).resolve().parent
ARMS = ("single", "parallel")
RECORDS = ("early", "of-record")   # The maintainer, 2026-10-25: C3's run is of record; C1's and C2's are early.
REFUSED, PREFLIGHT = 2, 4   # The bench's own refusal code; a failed preflight's, apart from every bench code.


def parse(argv):
    argv = list(argv)
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--authorize-billed-runs", action="store_true", required=True)
    parser.add_argument("--task-set", required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--output", required=True)
    parser.add_argument("--first", choices=ARMS, default="single")
    parser.add_argument("--preflight", default="", help="a command that exits non-zero when the machine is unfit")
    parser.add_argument("--skip-warm-up", action="store_true", help="a resumed run whose warm-ups already ran")
    parser.add_argument("--worker-cap", type=int, default=0, help="the parallel arm's most workers at once (0: none)")
    parser.add_argument("--record", choices=RECORDS, help="early: C1's and C2's early verdicts (grade held); of-record: C3's run (BENCH-PREREG,"
                        " Benchmark of record). Unmarked, its report reads as not of record")
    args = parser.parse_args(argv)
    for forbidden in ("--sides", "--output", "--trials", "--task-set", "--angelo-parallel-agents", "--resume",
                      "--max-new-trials", "--warm-up-only", "--angelo-worker-cap"):
        if forbidden in rest:
            raise SystemExit("%s is this runner's to set, not the arms'" % forbidden)
    if not 1 <= args.trials <= 20:
        raise SystemExit("--trials is 1-20")
    if args.worker_cap < 0:
        raise SystemExit("--worker-cap is 0 (none) or more")
    return args, rest


def order(trial, first):
    """The arms of trial ``trial`` (1-based) in the order they run: ``first`` leads the odd trials."""
    other = ARMS[1] if first == ARMS[0] else ARMS[0]
    return (first, other) if trial % 2 == 1 else (other, first)


def command(args, rest, arm, warm_up=False):
    base = [sys.executable, str(HERE / "bench_mcp_vs_angelo.py"), "--authorize-billed-runs", "--sides", "angelo",
            "--task-set", args.task_set, "--trials", str(args.trials), "--output", str(Path(args.output) / arm)]
    base += ["--angelo-parallel-agents"] if arm == "parallel" else []
    base += ["--angelo-worker-cap", str(args.worker_cap)] if arm == "parallel" and args.worker_cap else []
    base += ["--warm-up-only"] if warm_up else ["--resume", "--max-new-trials", "1"]
    return base + list(rest)


def sent_nothing(folder, trial):
    """Whether ``folder``'s trial ``trial`` ended before its message: the harness aborted it (a connection or sign-in
    not ready, an add-on mismatch) with no task and nothing in flight, and no provider limit (the bench's own rule).
    A crash after the message leaves no abort, so it stays a failed trial (break-it E2E-12 M-3, H-2)."""
    try:
        report = (send_record.read(Path(folder) / ("angelo-trial-%02d" % trial) / send_record.NAME)
                  .get("trials") or [{}])[0]
    except (OSError, ValueError, AttributeError, IndexError, KeyError, TypeError):   # Any shape: not this rule's.
        return False
    return bool(isinstance(report, dict) and report.get("aborted") and not report.get("tasks")
                and not report.get("unfinished_task") and not report.get("invalid"))


# What the verdict reads of the attempts a resume set aside (bench_parallel_report): kept beside the rule above.
def attempts(folder):
    """``(trial, kept folder)`` for each attempt a resume set aside under ``interrupted/`` (the bench's ``set_aside``),
    in name order. A folder that holds only a stable workspace moved aside before a trial is no attempt of it (break-it
    E2E-14 L-4); a warm-up is never one (it lives under ``warm-up/``, never as a trial)."""
    for kept in sorted((folder / "interrupted").glob("angelo-trial-*")) if (folder / "interrupted").is_dir() else ():
        number = re.match(r"angelo-trial-(\d+)-", kept.name)
        if number is not None and any(kept.glob("angelo-trial-%02d*" % int(number.group(1)))):
            yield int(number.group(1)), kept


def set_aside(folder):
    """{trial: [why, ...]} for each attempt a resume set aside under ``interrupted/`` (``attempts``): ``"cap"`` (the
    bench's outer cap cut it), ``"provider limit"``, ``"exit <code>"``, or ``"no record"``."""
    found = {}
    for trial, kept in attempts(folder):
        why = "no record"
        if sent_nothing(kept, trial):
            found.setdefault(trial, []).append("sent nothing (invalid, run again)")
            continue
        try:
            why = "exit %s" % json.loads((kept / ("angelo-trial-%02d-launch.json" % trial)).read_text())["exit_code"]
            receipt = send_record.read(kept / ("angelo-trial-%02d" % trial) / send_record.NAME)
            why = "provider limit" if (receipt.get("trials") or [{}])[0].get("invalid") else why
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            pass
        found.setdefault(trial, []).append("cap" if why == "exit timeout" else why)
    return found


EXCLUDED = ("provider limit", "sent nothing")   # The pre-registered exclusions (rules 1 and 2): run again, not counted.


def lost(folder, rows, kept):
    """{trial: why} for each trial of this arm that failed and left no record a rule can read (E2E-12 H-2): every
    attempt a resume set aside for any reason but the pre-registered exclusions (the bench's cap, a crash whose
    receipt never came, E2E-13 H-3), and every attempt in the arm's folder with a launch record and no task record."""
    found = {}
    for trial, whys in kept.items():
        failures = [why for why in whys if not why.startswith(EXCLUDED)]
        if failures:
            found[trial] = "cut by the bench's cap" if "cap" in failures else "ended with no record (%s)" % failures[0]
    recorded = {row["trial"] for row in rows}
    for launch in sorted(folder.glob("angelo-trial-*-launch.json")):
        number = re.match(r"angelo-trial-(\d+)-launch", launch.name)
        trial = int(number.group(1)) if number else None
        if trial is None or trial in recorded or trial in found or sent_nothing(folder, trial):
            continue
        try:
            code = json.loads(launch.read_text()).get("exit_code")
        except (OSError, ValueError, AttributeError):
            code = None
        found[trial] = "cut by the bench's cap" if code == "timeout" else "no task recorded (exit %s)" % code
    return found


def preflight(args, record):
    if not args.preflight:
        return True
    result = subprocess.run(shlex.split(args.preflight), capture_output=True, text=True)
    record({"preflight": result.returncode, "output": result.stdout.strip()[-2000:], "at": time.time()})
    return result.returncode == 0


def main(argv=None):
    from bench_mcp_vs_angelo import set_aside, trial_done   # The bench's own, never copies of them.
    args, rest = parse(sys.argv[1:] if argv is None else argv)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    journal = out / "interleave.json"
    plan = {"task_set": args.task_set, "trials": args.trials, "first": args.first, "arm_arguments": rest,
            "worker_cap": args.worker_cap, "record": args.record}
    earlier = json.loads(journal.read_text()) if journal.is_file() else {}
    # A journal from before a key existed holds its old default (break-it E2E-16 L-2: no worker_cap was no cap).
    if earlier and any(earlier.get(key, {"worker_cap": 0}.get(key)) != value for key, value in plan.items()):
        print("Refused: this run's arguments differ from %s's; its schedule would mix" % journal, file=sys.stderr)
        return REFUSED   # The earlier journal stays as it was.
    log = list(earlier.get("steps") or ())

    def record(entry):
        log.append(entry)
        fresh = journal.with_name(journal.name + ".new")
        fresh.write_text(json.dumps({**plan, "steps": log}, indent=2))
        fresh.replace(journal)   # Whole or not at all.

    def step(arm, warm_up=False, trial=None):
        """0 when the step ran and finished what it was scheduled for; else the code the run stops with."""
        what = "%s %s" % (arm, "warm-up" if warm_up else "trial %d" % trial)
        if not preflight(args, record):
            record({"stopped": "preflight failed before " + what, "code": PREFLIGHT})
            return PREFLIGHT
        started = time.time()
        code = subprocess.run(command(args, rest, arm, warm_up)).returncode
        record({"arm": arm, "warm_up": warm_up, "trial": trial, "exit_code": code,
                "started": started, "elapsed_s": round(time.time() - started, 1)})
        if not code and trial is not None and not trial_done(out / arm, "angelo", trial):
            record({"stopped": "%s exited 0 without finishing it: the bench ran another trial or none" % what,
                    "code": REFUSED})
            return REFUSED
        if code:
            record({"stopped": "%s exited %s" % (what, code), "code": code})
        return code
    record({"run_started": time.time()})
    warmed = set() if not args.skip_warm_up else set(ARMS)
    for trial in range(1, args.trials + 1):
        for arm in order(trial, args.first):
            if trial_done(out / arm, "angelo", trial):
                if not sent_nothing(out / arm, trial):
                    continue   # Finished in an earlier run of this schedule: kept, never run again (E2E-11 M-4).
                record({"set_aside": "%s trial %d: the harness ended it before its message; invalid, run again"
                        % (arm, trial), "kept": set_aside(out / arm, "angelo", trial)})
            # Each arm's warm-up right before its own first trial: both arms start as warm, never one after the
            # other arm's whole trial (break-it E2E-3 M-6).
            if arm not in warmed:
                code = step(arm, warm_up=True)
                if code:
                    return code
                warmed.add(arm)
            code = step(arm, trial=trial)
            if code:
                return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
