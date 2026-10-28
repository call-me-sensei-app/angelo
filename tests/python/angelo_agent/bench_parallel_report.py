# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pre-registered verdict of a ``bench_parallel_vs_single.py`` run (host side; sends nothing).

    python3 bench_parallel_report.py --run DIR

Reads each arm's results (``bench_mcp_vs_angelo.RESULTS``) and one-shot scorecard (``oneshot_report.SCORECARD``,
its ``saved_scene_checks``) under the names and keys those producers write, and the run's ``grades.json``
(``bench_parallel_grade``), and writes ``PARALLEL-REPORT.md`` and ``parallel-report.json``:

* every trial of both arms, valid or not, with why an invalid one is invalid (the harness's own rule: a provider's
  usage limit; nothing else is excluded here);
* wall clock from the message to the finished, merged scene (the trial's ``send_to_idle_ms``: the lead's turn and
  every worker's), median, p75 (nearest rank) and range over the valid trials; list-price cost; the end-of-session
  checks passed;
* each grade class's median score per arm (a class score is the mean of its criteria, 0-4);
* the ship rule fixed before any trial: parallel ships only if its median wall clock is lower, its median grade is
  at least the single arm's on every class, and it has no more trials with a broken piece. The report names every
  class that misses;
* the treatment was applied (BENCH-PREREG Amendment A, maintainer-approved 2026-10-25): a parallel trial whose lead
  started no worker that sent the provider a request (``parallel.working``, from ``eval_parallel``; a record that does
  not say counts as none) is an invalid treatment. It is named, never counted as a parallel result (left out of every
  parallel figure) and not re-run; the verdict reads "treatment not applied" and the parallel arm does not ship;
* a trial stopped at the cap counts as the cap (``wall_capped``), never as an unmeasured wall (break-it E2E-3 M-3);
* the smoothness budget: parallel's median p95 and its longest stall no worse than the single arm's; worse, or
  unmeasured, is a release blocker and the verdict says so (M-4);
* each trial's approvals, a worker card's included (``worker:<tool>``), which in Auto is a defect (L-18);
* the evidence is complete before any verdict (fixed before any timed trial, both arms, audit P1): every trial the plan
  registered (``interleave.json``'s ``trials``) is held by each arm, valid or lost; and every compared trial has its
  wall clock, the lead window's p95 and longest stall, and a score for every grade class the scenario defines
  (``GRADE_CLASSES``, never the classes the rows happen to hold). Anything missing is named, and the verdict is
  "incomplete": it never ships, and nothing missing shrinks a denominator. An input the verdict needs that is there
  but cannot be read (an arm's results, a record or a value of the wrong kind, the grades) is missing too, named;
  a reported-only input that cannot be read never stops or changes the verdict (break-it E2E-19B L-4);
* trials are compared in pairs, in one direction fixed before any timed trial (break-it E2E-11 M-4, E2E-12 H-2):
  wall clock, cost, grades and the lead window's p95 read only the trial numbers valid on both arms. A parallel
  failure outside a pair always counts against parallel (a broken piece, no worker, its longest stall); the single
  arm's evidence is its paired trials only, so nothing outside a pair ever relaxes parallel's bar. Unpaired trials
  and the runner's stops are listed;
* a trial that failed and left no record a rule can read is a failed trial of its arm (``lost``): one the bench's own
  outer cap cut, one that ended after its message with no task recorded (a crash), and any attempt a resume set aside
  under ``interrupted/`` but for the pre-registered exclusions, whatever its rerun did (E2E-11 L-4, E2E-12 H-2, E2E-13
  H-3). A lost trial, in either arm, is scored and timed as a crash after its message (every class 0, its wall clock
  the cap) and compared with the other arm's trial of the same number; its rerun is never that trial, and it never
  makes the run INCOMPLETE (BENCH-PREREG Amendment C6, the maintainer's "same as C1-C4"). Outside a pair a lost
  parallel trial still counts against parallel and a lost single one never relaxes parallel's bar. A trial the harness
  ended before its message, or that a provider limit voided, is invalid and run again. Every set-aside attempt is
  listed;
* a trial with a task record and no end-of-session capture (a crash after its message) is a failed trial of its arm,
  the same for both arms (BENCH-PREREG Amendment C, the maintainer's "Failed trial", break-it E2E-15 L-4): every class
  scores 0 (a broken piece), its wall clock is the cap unless its record measured one, its missing main-loop figures
  are no gap, and it is named "crashed, not captured", never INCOMPLETE;
* every trial with a task record, and every attempt a resume set aside that left one (the Clarification of F), shows it
  was sent its arm's registered message (BENCH-PREREG Amendment F, ``bench_messages``, the same check for both arms,
  every test and every cap; warm-ups unchecked); one that does not is named with what it recorded (an attempt with its
  folder) and the verdict is INCOMPLETE, never a failed trial of its arm. A lost trial keeps C6's treatment.

Reported beside the verdict and never read by it (BENCH-PREREG Amendments B and E): each trial's worker cap, most
workers at once and the memory of the lead and each worker, each one's peak over its send/ready baseline
(``bench_memory``), and how the lead and workers communicated (``bench_parallel_comms``).
"""
import re

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_mcp_vs_angelo import RESULTS  # noqa: E402 - the producers' own names, never a copy of them.
from bench_parallel_grade import capture_of  # noqa: E402
import bench_memory  # noqa: E402  (Amendments B2 and E: reported only)
import bench_messages  # noqa: E402  (Amendment F: each trial was sent its arm's registered message)
import bench_parallel_comms as comms  # noqa: E402  (Amendment B3: reported only)
import bench_parallel_notes  # noqa: E402  (the report's reported-only lines)
from bench_parallel_vs_single import lost, set_aside  # noqa: E402  (the runner's own set-asides)
from oneshot_report import SCORECARD  # noqa: E402

ARMS = ("single", "parallel")


def percentile(values, share):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))]


def median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


thread_cache = comms.thread_cache   # Reported only: each thread's first-call cache read and calls-2+ share.


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _read(path, problems, name):
    """A verdict input, the JSON object at ``path``, or None with why named in ``problems``: a malformed input is
    missing evidence, never a stopped verdict (audit P1, break-it E2E-19B L-4)."""
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        problems.append("%s: cannot be read (%s)" % (name, type(error).__name__))
        return None
    if not isinstance(value, dict):
        problems.append("%s: not a JSON object" % name)
    return value if isinstance(value, dict) else None


CAPPED = re.compile(r"exceeded ([0-9.]+) s")


def cap_of(run):
    """The pre-registered cap of the run's test: its scenario's ``LIMITS["wall_s"]``, or None."""
    try:
        import scenario_specs as specs
        task_set = json.loads((Path(run) / "interleave.json").read_text())["task_set"]
        return float(specs.module(specs.scenario_id(task_set)).LIMITS["wall_s"])
    except (OSError, ValueError, KeyError, AttributeError, TypeError, ImportError):
        return None


def wall_s(record, cap=None):
    """The trial's wall clock: from the message to the settled scene, or the cap when it was stopped there (the
    stop's own figure when its reason gives one, else the test's cap)."""
    if _number(record.get("send_to_idle_ms")):
        return round(record["send_to_idle_ms"] / 1000, 1), False
    if record.get("status") != "timeout":
        return None, False
    found = CAPPED.search(str(record.get("reason") or ""))
    wall = float(found.group(1)) if found else cap
    return (wall, True) if wall is not None else (None, False)


def reported(measure, *args):
    """A reported-only measurement (memory, communication): never in the verdict, so one that cannot be read never
    stops it (break-it E2E-16 M-1: a malformed tool call in a rollout raised through the whole verdict)."""
    try:
        return measure(*args)
    except Exception as error:   # noqa: BLE001
        return {"measured": False, "why": "%s: %s" % (type(error).__name__, error)}


def _checks(folder):
    """{trial: the saved scene's closed checks} from the one-shot scorecard (reported only)."""
    card = json.loads((folder / SCORECARD).read_text()) if (folder / SCORECARD).is_file() else {}
    return {side["trial"]: _dict(side.get("saved_scene_checks")) for side in _dict(card).get("sides") or ()
            if isinstance(side, dict) and side.get("side") == "angelo" and side.get("trial") is not None}


def arm_trials(folder, cap=None, problems=None):
    """One row per trial record of the arm. What the verdict reads is checked for its shape: a record that cannot be
    read is named in ``problems`` and kept as an unreadable trial (never compared), a value of the wrong kind is read as
    not recorded (so the evidence it was for is named missing); a reported-only read never stops the verdict."""
    problems = [] if problems is None else problems
    records = _dict(_read(folder / RESULTS, problems, "%s results" % folder.name)).get("records") or []
    if not isinstance(records, list):
        problems.append("%s results: its records are not a list" % folder.name)
        records = []
    checks, rows = reported(_checks, folder), []
    for record in records:
        if not isinstance(record, dict) or record.get("side") != "angelo":
            continue
        if not isinstance(record.get("trial"), int) or isinstance(record.get("trial"), bool):
            problems.append("%s results: a record whose trial number is %r" % (folder.name, record.get("trial")))
            continue
        try:
            rows.append(_row(folder, record, cap, checks))
        except Exception as error:   # noqa: BLE001
            problems.append("%s trial %d: its record cannot be read (%s: %s)" % (folder.name, record["trial"],
                                                                                 type(error).__name__, error))
            rows.append(dict(lost_row(record["trial"], None, None), valid=False, status="unreadable record",
                             wall_capped=False))
    return rows


def _row(folder, record, cap, checks):
    valid = record.get("status") != "invalid" and record.get("invalid") is None
    wall, capped = wall_s(record, cap)
    trial = folder / ("angelo-trial-%02d" % record["trial"])
    captured, task = capture_of(trial) is not None, _dict(reported(comms.recorded_task, trial))
    if wall is None and not captured:   # Amendment C3: a crash never wins rule 1.
        wall, capped = cap, cap is not None
    listed = lambda value: [str(item) for item in value] if isinstance(value, list) else []   # noqa: E731
    loop, cache = _dict(record.get("main_loop")), reported(thread_cache, folder / ("angelo-trial-%02d-rollouts"
                                                                                   % record["trial"]))
    return {"trial": record["trial"], "valid": valid, "status": record.get("status"),
            "why_invalid": None if valid else (record.get("invalid") or record.get("reason")),
            "accounting_partial": listed(record.get("accounting_partial")) or (
                [str(record["accounting_partial"])] if record.get("accounting_partial") else None),
            # None: the record does not say (an older harness, or parallel edit off), never taken as zero.
            "workers_started": _number(_dict(record.get("parallel")).get("working")),   # That sent a request.
            "worker_limit": _dict(record.get("parallel")).get("worker_limit"),   # What applied (B1).
            "worker_done_events": _number(record.get("worker_done_events")),   # Reported only (Amendment G),
            "events_after_stop": _number(record.get("worker_done_events_after_stop")),   # and its timeout stop.
            "wall_s": wall, "wall_capped": capped, "captured": captured,
            "approved": listed(record.get("approved")), "declined": listed(record.get("declined")),
            "list_price_usd": _number(record.get("list_price_usd")),
            "main_loop_p95_ms": _number(loop.get("main_loop_late_ms_p95")),
            "main_loop_max_ms": _number(loop.get("main_loop_late_ms_max")),
            "cache_by_thread": cache if isinstance(cache, list) else [],
            "checks_passed": _dict(checks.get(record["trial"])).get("passed"),
            "checks_total": _dict(checks.get(record["trial"])).get("total"),
            "checks_failed": listed(_dict(checks.get(record["trial"])).get("failed")),   # Reported only.
            "communication": reported(comms.trial, folder / ("angelo-trial-%02d-rollouts" % record["trial"]),
                                      comms.recorded_workers(trial), task.get("lead_threads")),
            **{key: value for key, value in _dict(reported(bench_memory.trial, folder, record["trial"], task)).items()
               if key not in ("measured", "why")}}


def lost_row(trial, why, cap):
    """A lost trial as the failed trial it is (Amendment C6, both arms): no record, so its wall clock is the cap (C3),
    every class scores 0 (C2) and its missing main-loop figures are no gap (C4)."""
    return {"trial": trial, "valid": True, "status": "lost", "why_invalid": None, "lost": why,
            "accounting_partial": None, "workers_started": None, "worker_limit": None, "worker_done_events": None,
            "events_after_stop": None,
            "wall_s": cap,
            "wall_capped": cap is not None, "captured": False, "approved": [], "declined": [], "list_price_usd": None,
            "main_loop_p95_ms": None, "main_loop_max_ms": None, "cache_by_thread": [], "checks_passed": None,
            "checks_total": None, "communication": {"measured": False, "why": "lost: %s" % why}, "rerun_of_lost": False}


def expected(run):
    """``(trials, classes)`` the run must hold: its plan's trial count and its scenario's grade classes, or Nones."""
    try:
        import scenario_specs as specs
        plan = json.loads((Path(run) / "interleave.json").read_text())
        return int(plan["trials"]), sorted(specs.module(specs.scenario_id(plan["task_set"])).GRADE_CLASSES)
    except (OSError, ValueError, KeyError, AttributeError, TypeError, ImportError):
        return None, None


def gaps(trials, classes, arms, held, checked, grades, crashed=None):
    """Every piece of evidence the verdict needs and lacks, named (audit P1): a planned trial an arm holds neither
    valid nor lost; and, for each trial ``checked`` per arm, a wall clock, the main loop's p95 and longest stall (but
    for one ``crashed`` or lost, Amendment C4 and C6), and a score for every grade class, every item of it scored."""
    if trials is None:
        return ["the plan's trial count or the scenario's grade classes (interleave.json, GRADE_CLASSES)"]
    found = []
    for arm in ARMS:
        found += ["%s trial %d: never finished" % (arm, trial) for trial in range(1, trials + 1)
                  if trial not in held[arm]]
        for trial in sorted(checked[arm]):
            row = next(row for row in arms[arm] if row["trial"] == trial and not row.get("rerun_of_lost"))
            found += ["%s trial %d: %s" % (arm, trial, name) for key, name in (
                ("wall_s", "wall clock"), ("main_loop_p95_ms", "main-loop p95"), ("main_loop_max_ms", "longest stall"))
                if row[key] is None and not (key != "wall_s" and trial in (crashed or {}).get(arm, ()))]
            for part in classes:
                scores = [g["score"] for g in grades if g["arm"] == arm and g["trial"] == trial and g["part"] == part]
                if not scores or any(score is None for score in scores):
                    found.append("%s trial %d: grade of %s" % (arm, trial, part))
    return found


def stops(run):
    try:
        return [entry for entry in json.loads((Path(run) / "interleave.json").read_text()).get("steps") or ()
                if isinstance(entry, dict) and entry.get("stopped")]
    except (OSError, ValueError, AttributeError):
        return []


def verdict(run):
    run = Path(run)
    cap = cap_of(run)
    problems = []   # Evidence the verdict needs that is there but cannot be read (break-it E2E-19B L-4).
    arms = {arm: arm_trials(run / arm, cap, problems) for arm in ARMS}
    kept = {arm: set_aside(run / arm) for arm in ARMS}
    failed = {arm: lost(run / arm, arms[arm], kept[arm]) for arm in ARMS}
    unshown = bench_messages.unshown(run, {arm: [row["trial"] for row in arms[arm]] for arm in ARMS})   # F1-F3.
    builds = {arm: reported(lambda arm: (json.loads((run / arm / RESULTS).read_text()).get("meta") or {}).get(
        "versions"), arm) for arm in ARMS}   # Every number is labelled with the build it measured.
    graded = _dict(_read(run / "grades.json", problems, "grades.json")).get("grades") if (
        run / "grades.json").is_file() else []
    grades = {"grades": [g for g in graded if isinstance(g, dict) and g.get("arm") in ARMS and isinstance(
        g.get("trial"), int) and isinstance(g.get("part"), str) and (g.get("score") is None or _number(
            g.get("score")) is not None)] if isinstance(graded, list) else []}
    if len(grades["grades"]) != (len(graded) if isinstance(graded, list) else -1):
        problems.append("grades.json: %s not in the grader's shape" % ("its grades" if not isinstance(graded, list)
                                                                      else "%d item(s)" % (len(graded) - len(
                                                                          grades["grades"]))))
    valid = {arm: {row["trial"] for row in rows if row["valid"] and row["trial"] not in failed[arm]}
             for arm, rows in arms.items()}
    # Amendment A rule 4: no worker that worked is an invalid treatment, never a parallel result, never re-run.
    untreated = sorted(row["trial"] for row in arms["parallel"] if row["trial"] in valid["parallel"]
                       and not row["workers_started"])
    held = {arm: valid[arm] | set(failed[arm]) | (set(untreated) if arm == "parallel" else set()) for arm in ARMS}
    valid["parallel"] -= set(untreated)
    for row in arms["parallel"]:
        row["valid"] = row["valid"] and row["trial"] not in untreated
    for arm, rows in arms.items():   # A lost trial's rerun is never that trial, whatever it did (E2E-13 H-3).
        for row in rows:
            row["rerun_of_lost"] = row["trial"] in failed[arm]
        rows += [lost_row(trial, why, cap) for trial, why in sorted(failed[arm].items())]
    # Amendment C6: a lost trial, in either arm, is a failed trial of that arm compared with the other arm's trial of
    # the same number. Only trials held by both arms are compared (E2E-11 M-4).
    compared = {arm: valid[arm] | set(failed[arm]) for arm in ARMS}
    paired = compared["single"] & compared["parallel"]
    unpaired = {arm: sorted(compared[arm] - paired) for arm in ARMS}
    # Whose failures count: every parallel trial (a lost one's rerun too: its stall counts), the single arm's paired
    # ones only (E2E-12 H-2, E2E-14 L-4): nothing outside a pair relaxes parallel's bar.
    failures = {"parallel": {row["trial"] for row in arms["parallel"] if row["valid"]}, "single": paired}
    for arm, rows in arms.items():
        for row in rows:
            row["counted"] = (row["valid"] and row["trial"] in failures[arm]
                              and (arm == "parallel" or not row["rerun_of_lost"]))
            row["valid"] = row["valid"] and row["trial"] in paired and not row["rerun_of_lost"]   # Compared: paired.
    valid = {arm: paired for arm in ARMS}
    trials, classes = expected(run)
    try:   # BENCH-PREREG, Benchmark of record: reported, never a rule; an unreadable journal reads as unmarked.
        record = json.loads((run / "interleave.json").read_text()).get("record")
    except (OSError, ValueError, AttributeError):
        record = None
    held_grade = record == "early" and not (run / "grades.json").is_file()   # Early runs are not graded (maintainer).
    # Amendment C: a counted trial that crashed after its message (no capture) scores 0 on every class; C6: a lost
    # one too, its rerun's grades set aside.
    crashed = {arm: sorted(row["trial"] for row in rows if row["counted"] and not row["captured"]
                           and row["status"] != "lost" and not row["rerun_of_lost"]) for arm, rows in arms.items()}
    zeroed = {(arm, trial): "crashed" for arm in ARMS for trial in crashed[arm]}
    zeroed.update({(arm, trial): "lost" for arm in ARMS for trial in failed[arm]})
    grades["grades"] = [g for g in grades["grades"] if zeroed.get((g["arm"], g["trial"])) != "lost"]
    graded = {(g["arm"], g["trial"], g["part"]) for g in grades["grades"]}
    grades["grades"] = grades["grades"] + [{"arm": arm, "trial": trial, "part": part, "score": 0, why: True}
                                           for (arm, trial), why in sorted(zeroed.items()) for part in classes or ()
                                           if (arm, trial, part) not in graded]
    summary = {}
    for arm, rows in arms.items():
        walls = [row["wall_s"] for row in rows if row["valid"] and row["wall_s"] is not None]
        # A trial whose accounting misses a chat still ending (only the parallel arm can be partial) is a lower
        # bound: listed apart, never in the median (break-it E2E-6 L-4).
        costs = [row["list_price_usd"] for row in rows if row["valid"] and row["list_price_usd"] is not None
                 and not row["accounting_partial"]]
        summary[arm] = {"valid_trials": len(walls), "trials": len({row["trial"] for row in rows}),
                        "wall_median_s": median(walls),
                        "wall_p75_s": percentile(walls, 0.75), "wall_min_s": min(walls, default=None),
                        "wall_max_s": max(walls, default=None), "cost_median_usd": median(costs),
                        "cost_lower_bounds_usd": [row["list_price_usd"] for row in rows if row["valid"]
                                                  and row["accounting_partial"]],
                        # The lead window's smoothness budget: parallel never worse than one chat.
                        "main_loop_p95_median_ms": median([r["main_loop_p95_ms"] for r in rows
                                                           if r["valid"] and r["main_loop_p95_ms"] is not None]),
                        # Its longest stall over every trial whose failures count: parallel's unpaired ones too.
                        "main_loop_max_ms": max((r["main_loop_max_ms"] for r in rows
                                                 if r["counted"] and r["main_loop_max_ms"] is not None), default=None)}
    missing = problems + gaps(trials, [] if held_grade else classes or [], arms, held, failures, grades["grades"],
                              {arm: set(crashed[arm]) | set(failed[arm]) for arm in ARMS})
    missing += bench_messages.named(unshown)   # Amendment F2: a trial not shown its registered message.
    classes = classes or sorted({g["part"] for g in grades["grades"]})
    per_class = {}
    for part in classes:
        per_class[part] = {arm: median([g["score"] for g in grades["grades"] if g["arm"] == arm
                                        and g["part"] == part and g["score"] is not None
                                        and g["trial"] in valid[arm]]) for arm in ARMS}
    misses = [part for part, scores in per_class.items()
              if scores["parallel"] is None or scores["single"] is None or scores["parallel"] < scores["single"]]
    # The floor (coordinator, 2026-10-24): no more trials than the single arm with a broken piece, any class at 1 or
    # below. "Top notch across all pieces" is no broken piece, not only good medians.
    broken = {arm: sorted({g["trial"] for g in grades["grades"] if g["arm"] == arm and g["trial"] in failures[arm]
                           and g["score"] is not None and g["score"] <= 1}
                          | (set(failed[arm]) & failures[arm])) for arm in ARMS}
    faster = (summary["parallel"]["wall_median_s"] is not None and summary["single"]["wall_median_s"] is not None
              and summary["parallel"]["wall_median_s"] < summary["single"]["wall_median_s"])
    speedup = (summary["single"]["wall_median_s"] / summary["parallel"]["wall_median_s"]
               if faster else None)
    floor_holds = len(broken["parallel"]) <= len(broken["single"])
    smooth = {key: (summary["single"][key], summary["parallel"][key])
              for key in ("main_loop_p95_median_ms", "main_loop_max_ms")}
    blocker = [key for key, (single, parallel) in smooth.items()
               if single is None or parallel is None or parallel > single]   # Worse or unmeasured: a blocker.
    # Rule 4 (amended before the freeze): a parallel trial whose lead started no worker, or that does not say,
    # measured one chat against one chat.
    # The treatment was applied in every parallel trial, and some compared one has a record to show it.
    exercised = bool(paired - set(failed["parallel"])) and not untreated
    rule = bool(not missing and faster and not misses and classes and floor_holds and exercised)
    return {"builds": builds, "record": record, "arms": arms, "complete": not missing, "missing": missing,
            "paired": sorted(paired), "unpaired": unpaired, "crashed": crashed,
            "lost": {arm: {str(trial): why for trial, why in sorted(failed[arm].items())} for arm in ARMS},
            "set_aside": {arm: {str(t): whys for t, whys in kept[arm].items()} for arm in ARMS}, "stops": stops(run),
            "summary": summary, "grades_by_class": per_class, "quality_misses": misses,
            "trials_with_a_broken_piece": broken, "floor_holds": floor_holds,
            "faster": faster, "speedup_median": round(speedup, 2) if speedup else None,
            "exercised": exercised, "treatment_applied": not untreated, "parallel_trials_without_a_worker": untreated,
            "ship_rule_holds": rule, "smoothness_blocker": blocker, "ships": rule and not blocker,
            "messages_not_shown": unshown,   # Amendment F: named, and the verdict is INCOMPLETE.
            "unscored": [g for g in grades["grades"] if g.get("score") is None],
            "graded": grades["grades"], "grade_held": held_grade}   # Scores read: crashed or lost trials' zeros too.


def markdown(result):
    lines = ["# With parallel edit in use (the person asks to split the work) vs one chat", "",
             "Ship rule (fixed before any trial): parallel median wall clock lower, its median grade at least the "
             "single arm's on every class, no more trials than the single arm with a broken piece (a class "
             "scored 1 or below), and a worker that worked in every valid parallel trial.", "",
             "**Record: %s.**" % {"of-record": "the run of record (the shipping build)", "early": "EARLY VERDICT, "
                                  "not of record: the 1.0.6 claim and the ship rule read the shipping build's run"}.get(result.get(
                 "record"), "NOT MARKED (no --record), so not of record"),
             "**Verdict: %s**" % ("grade held: early verdict, not graded" if result.get("grade_held") else "ships" if result["ships"] else "INCOMPLETE: does not ship" if result.get(
                 "messages_not_shown") else "TREATMENT NOT APPLIED: does not ship" if not result["treatment_applied"]
                 else "does not ship" if result["complete"] else "INCOMPLETE: does not ship"),
             "Evidence: %s" % ("complete" if result["complete"] else "missing %d: %s" % (
                 len(result["missing"]), "; ".join(result["missing"][:40]))),
             "Ship rule: %s. Smoothness (release blocker when parallel is worse or unmeasured): %s." % (
                 "not read (grade held)" if result.get("grade_held") else "holds" if result["ship_rule_holds"] else "misses",
                 "BLOCKER on " + ", ".join(result["smoothness_blocker"]) if result["smoothness_blocker"] else "ok"),
             "Treatment applied (a worker worked in every parallel trial): %s." % (
                 "yes" if result["exercised"] else
                 "NO: invalid treatment in parallel trial %s (no worker sent a request, or the record does not say); "
                 "never counted as a parallel result, not re-run" % ", ".join(
                     map(str, result["parallel_trials_without_a_worker"]))
                 if result["parallel_trials_without_a_worker"] else "NO: no valid parallel trial"),
             "Each trial and set-aside attempt sent its arm's registered message (Amendment F): %s." % (
                 "; ".join(bench_messages.named(result.get("messages_not_shown") or ())) or "yes"), "",
             "Builds: " + "; ".join("%s %s" % (arm, json.dumps(versions, sort_keys=True)[:300])
                                    for arm, versions in result["builds"].items()), "",
             "Trials compared in pairs (fixed before any trial): paired %s; unpaired (a parallel one's failures still "
             "count, a single one never relaxes parallel's bar): %s. Lost (no task record: a failed trial of its arm, "
             "every class 0, its wall clock the cap, compared with the other arm's trial of the same number; "
             "Amendment C6): %s. Set aside by a resume: %s. Runner stops: %s." % (
                 ", ".join(map(str, result["paired"])) or "none",
                 "; ".join("%s %s" % (arm, trials or "none") for arm, trials in result["unpaired"].items()),
                 "; ".join("%s %s" % (arm, found or "none") for arm, found in result["lost"].items()),
                 "; ".join("%s %s" % (arm, found or "none") for arm, found in result["set_aside"].items()),
                 "; ".join("%s (code %s)" % (stop["stopped"], stop.get("code")) for stop in result["stops"]) or "none"),
             "Crashed, not captured (a failed trial: every class 0, its wall clock the cap unless measured; Amendment "
             "C): %s." % "; ".join("%s %s" % (arm, trials or "none") for arm, trials in result["crashed"].items()), ""]
    lines += ["| arm | valid / trials | wall median s | p75 | range | cost median $ |", "|---|---|---|---|---|---|"]
    for arm, row in result["summary"].items():
        lines.append("| %s | %d / %d | %s | %s | %s-%s | %s |" % (arm, row["valid_trials"], row["trials"],
                     row["wall_median_s"], row["wall_p75_s"], row["wall_min_s"], row["wall_max_s"],
                     row["cost_median_usd"]))
    lines += bench_parallel_notes.closed_checks(result)   # Per arm, side by side (report format note, 2026-10-25).
    lines += ["", "Main loop lateness (the lead window's smoothness): " + "; ".join(
        "%s p95 median %s ms, longest stall %s ms" % (arm, row["main_loop_p95_median_ms"], row["main_loop_max_ms"])
        for arm, row in result["summary"].items())]
    lines += bench_parallel_notes.reported(result)   # Memory, communication, worker-done events.
    lines += ["", "Speed-up (single median / parallel median): %s" % result["speedup_median"], "",
              "| class | single | parallel |", "|---|---|---|"]
    for part, scores in result["grades_by_class"].items():
        lines.append("| %s | %s | %s |" % (part, scores["single"], scores["parallel"]))
    lines += ["", "Trials with a broken piece: " + "; ".join("%s %s" % (arm, trials or "none")
                                                         for arm, trials in result["trials_with_a_broken_piece"].items())]
    lines += bench_parallel_notes.cache(result)
    worker_cards = ["%s %d" % (arm, row["trial"]) for arm, rows in result["arms"].items() for row in rows
                    if any(name.startswith("worker:") for name in row["approved"] + row["declined"])]
    lines += ["", "Worker cards the tester answered (in Auto every worker edit is bounded, so any is a DEFECT): %s"
              % (", ".join(worker_cards) or "none")]
    unscored = ["%s %s trial %s: %s" % (g["arm"], g["part"], g["trial"], g.get("error") or "no answer in the asked form")
                for g in result.get("unscored") or ()]
    lines += ["", "Grade items with no score (a class none of whose items scored is unmeasured, so parallel does not "
              "ship): %s" % ("; ".join(unscored) or "none")]
    partial = ["%s %d (%s)" % (arm, row["trial"], ", ".join(row["accounting_partial"]))
               for arm, rows in result["arms"].items() for row in rows if row.get("accounting_partial")]
    lines += ["", "Trials whose cost and limit check miss a chat still ending at the cap (their cost a lower bound, "
              "left out of the cost median): %s" % (", ".join(partial) or "none")]
    lines += ["", "Classes where parallel is worse or unmeasured: %s" % (", ".join(result["quality_misses"]) or "none"),
              "", "## Every trial", "",
              "| arm | trial | valid | status | workers (cap asked, limit applied, most at once) | wall s | $ | checks "
              "| memory MB (sum peak; peak over send/ready) | approvals given |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for arm, rows in result["arms"].items():
        for row in rows:
            lines.append("| %s | %d | %s | %s | %s (%s, %s, %s) | %s%s | %s | %s/%s | %s | %s |" % (
                arm, row["trial"], row["valid"], str(row["status"]) + (" (a lost trial's rerun, not compared)"
                                                                  if row["rerun_of_lost"] else ""),
                row["workers_started"], row.get("worker_cap"),
                row.get("worker_limit"), row.get("most_workers_at_once"), row["wall_s"], " (cap)" if row["wall_capped"] else "",
                row["list_price_usd"], row["checks_passed"], row["checks_total"],
                bench_memory.cell(row),
                ", ".join(row["approved"]) or "none"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    result = verdict(args.run)
    (Path(args.run) / "parallel-report.json").write_text(json.dumps(result, indent=2))
    (Path(args.run) / "PARALLEL-REPORT.md").write_text(markdown(result))
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
