# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ranked latency-return report of a request-survey run. Host-only: it reads files and sends nothing.

Usage, from the repository root, after ``eval_live_agent.py --task-set survey``::

    python3 tests/python/angelo_agent/survey_report.py RUN_DIR [RUN_DIR ...] [--output DIR] \\
        [--approval-wait-s 20] [--round-seconds 2.5]

It reads each run directory's ``eval-live-agent.json`` (or, for an interrupted
run, its ``trial-*/qa/trial.json``), skips rate-limited trials, and writes
``survey-report.json`` and ``survey-report.md`` (default: into the first run
directory). Per run of a task it derives:

* ``outcome``: ``declined-python`` (the harness declined reviewed Python: the
  gap signal), ``approval`` (it declined another approval-gated tool),
  ``failed`` (the request did not complete or a closed check failed),
  ``native`` (completed with nothing declined) or ``skipped``.
* rounds (Claude typed-plan rounds; Codex rounds are not observable) and
  provider turns; capability lookups and lookup rounds (``angelo_agent_capabilities``
  calls, consecutive ones counted as one round); invalid calls; rejections
  (admission rejections plus rejected plans, ``turn_activity``).
* phases: first native action, host time (checkpoint + precondition +
  execution + staging + commit; verification runs inside staging), time after
  the first verified result (total minus ``first_verified_result_ms``) and total.

Rankings follow the selection rule (``doc/angelo/native_coverage.rst``, "How to
continue"): return = how often a request hits a gap x the seconds a native path
would save.

* Gaps: tasks whose ``expect`` is not ``never-native`` and whose run declined
  Python or failed. Observed time = the run's total plus ``--approval-wait-s``
  per declined Python step (a conservative human review the harness skipped);
  saving = observed median - median total of native runs of the same category
  and kind (else of the same kind), never below zero; score = weight x gap rate
  x saving.
* Extra-round sinks: (lookup rounds + rejections + invalid calls) x seconds per
  round (the run's total / model rounds when known, else ``--round-seconds``);
  score = weight x mean extra seconds per attempted run.

Weights are the survey's estimates (``eval_survey_tasks``), not telemetry, and a
few trials are small samples; the report says how many runs each figure uses.

Grading (``eval_grading``): ``outcome`` above is how a request was routed, not
whether its result was right. Every run also has a grade, and the report
states ``passed``, ``failed``, ``ungraded`` and ``skipped`` over every run: a
run is passed only when a check read its result, and an ungraded run (an edit
the survey declares ungraded, a check that already held, a question without
ground truth) is listed with its reason, never counted as passed. Questions
get their own table: the answer outcomes (``eval_answers``) and how many runs
captured an image before answering. Trials record the survey's version and
task digest; runs of another version are refused unless ``--mixed-versions``
is given, and are then named in the report, because their tasks differ.
"""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import sys

import eval_grading as grading, eval_send_record as send_record  # noqa: E401  (send_record: the trial's record)
import eval_provider_limits as limits
import eval_survey_tasks as survey

LOOKUP_TOOL = "angelo_agent_capabilities"
PYTHON_TOOL = "angelo_execute_python"
# System rows the Claude runtime adds when a plan ran nothing and another round is asked for (providers.py).
PLAN_REJECTION_PREFIXES = ("Host rejected the plan", "Nothing of the last plan ran",
                           "The plan stopped before changing the scene")
# The row of a plan that ran with operations the host refused alone (plan_validation): those plans were rejected
# plans before, so a comparison with an earlier build adds this count to ``plan_rejections``.
PLAN_REFUSED_IN_PART_PREFIX = "Host skipped these steps of the plan"
OUTCOMES = ("native", "approval", "declined-python", "failed", "skipped")
GAP_OUTCOMES = frozenset({"declined-python", "failed"})
# Worst first: a tie between outcome counts reports the worse one.
SEVERITY = ("failed", "declined-python", "approval", "native", "skipped")
HOST_PHASES = ("checkpoint_ms", "precondition_ms", "execution_ms", "staging_ms", "commit_ms")
DEFAULT_APPROVAL_WAIT_S = 20.
DEFAULT_ROUND_S = 2.5
REPORT_JSON, REPORT_MD = "survey-report.json", "survey-report.md"


def turn_activity(rows):
    """Counts from one turn's chat rows: tool names, capability lookups, rejected plans and plans refused in part.

    Content-free: only closed ``angelo_*`` tool names and counts are kept, never
    arguments, results or text. Consecutive lookup rows count as one lookup
    round (a heuristic: two lookup plans with no row between them count once).
    """
    tools, lookup_rounds, plan_rejections, refused_in_part, previous = {}, 0, 0, 0, None
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("tool")
        if row.get("role") == "activity" and isinstance(name, str) and name.startswith("angelo_") and len(name) <= 64:
            tools[name] = tools.get(name, 0) + 1
            if name == LOOKUP_TOOL and previous != LOOKUP_TOOL:
                lookup_rounds += 1
            previous = name
            continue
        if row.get("role") == "system" and str(row.get("text", "")).startswith(PLAN_REJECTION_PREFIXES):
            plan_rejections += 1
        if row.get("role") == "system" and str(row.get("text", "")).startswith(PLAN_REFUSED_IN_PART_PREFIX):
            refused_in_part += 1
        previous = None
    return {"tools": tools, "capability_lookups": tools.get(LOOKUP_TOOL, 0), "lookup_rounds": lookup_rounds,
            "plan_rejections": plan_rejections, "plans_refused_in_part": refused_in_part}


def _number(value):
    return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def _median(values):
    values = [value for value in values if _number(value) is not None]
    return round(statistics.median(values), 3) if values else None


def _mean(values):
    values = [value for value in values if _number(value) is not None]
    return round(sum(values) / len(values), 3) if values else None


def outcome(record):
    """One run's routing outcome; a declined script outranks everything else."""
    if record.get("status") == "skipped":
        return "skipped"
    declined = record.get("declined") or []
    if PYTHON_TOOL in declined or (_number(record.get("python_fallbacks")) or 0) > 0:
        return "declined-python"
    if declined:
        return "approval"
    completed = record.get("status") == "completed" and (record.get("receipt") or {}).get("status") == "completed"
    return "native" if completed and record.get("host_check_passed") is not False else "failed"


def run_metrics(record, approval_wait_s=DEFAULT_APPROVAL_WAIT_S, round_s=DEFAULT_ROUND_S, task=None):
    """The per-run figures the rankings use, from the record's receipt and activity, and the run's grade."""
    receipt = record.get("receipt") or {}
    phases, counts = receipt.get("phases") or {}, receipt.get("counts") or {}
    activity = record.get("activity") if isinstance(record.get("activity"), dict) else None
    total, verified = _number(phases.get("total_ms")), _number(phases.get("first_verified_result_ms"))
    host = [phases[name] for name in HOST_PHASES if _number(phases.get(name)) is not None]
    rounds = record.get("model_rounds") if type(record.get("model_rounds")) is int else None
    python_steps = list(record.get("declined") or []).count(PYTHON_TOOL)
    lookup_rounds = activity.get("lookup_rounds", 0) if activity else 0
    rejections = (_number(counts.get("rejections")) or 0) + (activity.get("plan_rejections", 0) if activity else 0)
    invalid = _number(counts.get("invalid_calls")) or 0
    per_round = total / 1000 / rounds if total and rounds else round_s
    categories = record.get("fallback_categories") or {
        name[len("python_fallback_"):]: value for name, value in counts.items()
        if name.startswith("python_fallback_") and value}
    host_check = record.get("host_check") if isinstance(record.get("host_check"), dict) else {}
    answer = host_check.get("answer") if isinstance(host_check.get("answer"), dict) else {}
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    return {"outcome": outcome(record), "grade": grading.state(record, task), "answer": answer.get("outcome"),
            "parsed": answer.get("parsed"),
            "image_captured": bool(evidence.get("images")) or bool(answer.get("image_captured")),
            "ungraded_reason": host_check.get("reason") or host_check.get("vacuous") or answer.get("reason"),
            "rounds": rounds, "turns": record.get("provider_turn_requests"),
            "lookups": activity.get("capability_lookups") if activity else None,
            "lookup_rounds": lookup_rounds if activity else None, "invalid_calls": invalid, "rejections": rejections,
            "first_native_action_ms": _number(phases.get("first_native_action_ms")),
            "host_ms": round(sum(host), 3) if host else None,
            "after_verified_ms": round(total - verified, 3) if total is not None and verified is not None else None,
            "total_ms": total, "python_steps": python_steps,
            "observed_s": round(total / 1000 + approval_wait_s * python_steps, 3) if total is not None else None,
            "lookup_s": round(lookup_rounds * per_round, 3), "rejection_s": round((rejections + invalid) * per_round, 3),
            "fallback_categories": dict(categories), "activity_measured": activity is not None}


def load_records(directories):
    """Task records of every valid trial, how many trials were read and skipped as invalid, and which survey
    versions recorded them: ``{(version, tasks_digest): trials}`` (a trial without a stamp is version 1)."""
    records, trials, invalid, versions = [], 0, 0, Counter()
    for directory in directories:
        directory = Path(directory)
        summary = directory / send_record.NAME   # One written at a send is no summary: the trials' own reports are.
        if send_record.finished(summary):
            reports = send_record.read(summary, "utf-8").get("trials", [])
        else:
            reports = [json.loads(path.read_text(encoding="utf-8"))
                       for path in sorted(directory.glob("trial-*/qa/trial.json"))]
        for report in reports:
            if report.get("invalid") == limits.INVALID:
                invalid += 1
                continue
            trials += 1
            stamp = report.get("grading") if isinstance(report.get("grading"), dict) else {}
            if stamp or report.get("tasks"):   # A trial that died before its first record names no version.
                versions[(stamp.get("survey_version", 1), stamp.get("tasks_digest"))] += 1
            records.extend(dict(record, grading_contract=stamp.get("contract") or 0)
                           for record in report.get("tasks", []))
    return records, trials, invalid, versions


def _dominant(outcomes):
    if not outcomes:
        return None
    top = max(outcomes.values())
    return next(name for name in SEVERITY if outcomes.get(name) == top)


def _merge(dicts):
    total = Counter()
    for item in dicts:
        total.update({key: value for key, value in item.items() if _number(value)})
    return dict(sorted(total.items()))


def task_row(task, runs):
    """Per-task outcome counts and medians (means for counts) over its attempted runs."""
    attempted = [run for run in runs if run["outcome"] != "skipped"]
    outcomes = Counter(run["outcome"] for run in runs)
    row = {key: task.get(key) for key in ("id", "category", "weight", "expect", "kind")}
    row.update(runs=len(runs), attempted=len(attempted), outcomes=dict(outcomes), outcome=_dominant(outcomes),
               grades=dict(Counter(run["grade"] for run in runs)), basis=grading.basis(task),
               activity_measured=sum(run["activity_measured"] for run in attempted),
               fallback_categories=_merge(run["fallback_categories"] for run in attempted))
    for key in ("rounds", "turns", "first_native_action_ms", "host_ms", "after_verified_ms", "total_ms"):
        row[key] = _median(run[key] for run in attempted)
    for key in ("lookups", "lookup_rounds", "invalid_calls", "rejections"):
        row[key] = _mean(run[key] for run in attempted)
    return row


def _baselines(tasks, runs):
    """Median native total (s) per (category, kind) and per kind."""
    pooled = {}
    for task in tasks:
        for run in runs.get(task["id"], []):
            if run["outcome"] == "native" and run["total_ms"] is not None:
                pooled.setdefault((task["category"], task["kind"]), []).append(run["total_ms"] / 1000)
                pooled.setdefault(task["kind"], []).append(run["total_ms"] / 1000)
    return {key: statistics.median(values) for key, values in pooled.items()}


def rank_gaps(tasks, runs, approval_wait_s):
    """Gap tasks by weight x gap rate x estimated saving (unknown savings last)."""
    baselines, gaps = _baselines(tasks, runs), []
    for task in tasks:
        attempted = [run for run in runs.get(task["id"], []) if run["outcome"] != "skipped"]
        hits = [run for run in attempted if run["outcome"] in GAP_OUTCOMES]
        if task["expect"] == "never-native" or not hits:
            continue
        observed = _median(run["observed_s"] for run in hits)
        baseline = baselines.get((task["category"], task["kind"]), baselines.get(task["kind"]))
        saving = round(max(0., observed - baseline), 3) if observed is not None and baseline is not None else None
        rate = len(hits) / len(attempted)
        gaps.append({"id": task["id"], "category": task["category"], "expect": task["expect"],
                     "weight": task["weight"], "gap_runs": len(hits), "attempted": len(attempted),
                     "gap_rate": round(rate, 3), "outcomes": dict(Counter(run["outcome"] for run in hits)),
                     "python_steps": sum(run["python_steps"] for run in hits), "observed_s": observed,
                     "native_baseline_s": round(baseline, 3) if baseline is not None else None,
                     "saving_s": saving, "score": round(task["weight"] * rate * saving, 3) if saving is not None else None,
                     "fallback_categories": _merge(run["fallback_categories"] for run in hits)})
    return sorted(gaps, key=lambda gap: (gap["score"] is None, -(gap["score"] or 0), -gap["weight"], gap["id"]))


def rank_extra_rounds(tasks, runs):
    """Tasks whose runs spent extra model rounds, by weight x mean extra seconds per attempted run."""
    sinks = []
    for task in tasks:
        attempted = [run for run in runs.get(task["id"], []) if run["outcome"] != "skipped"]
        if not attempted:
            continue
        lookup_s = sum(run["lookup_s"] for run in attempted) / len(attempted)
        rejection_s = sum(run["rejection_s"] for run in attempted) / len(attempted)
        if lookup_s + rejection_s <= 0:
            continue
        sinks.append({"id": task["id"], "category": task["category"], "weight": task["weight"],
                      "attempted": len(attempted), "lookups": _mean(run["lookups"] for run in attempted),
                      "lookup_rounds": _mean(run["lookup_rounds"] for run in attempted),
                      "rejections": _mean(run["rejections"] for run in attempted),
                      "invalid_calls": _mean(run["invalid_calls"] for run in attempted),
                      "lookup_s": round(lookup_s, 3), "rejection_s": round(rejection_s, 3),
                      "extra_s": round(lookup_s + rejection_s, 3),
                      "score": round(task["weight"] * (lookup_s + rejection_s), 3)})
    return sorted(sinks, key=lambda sink: (-sink["score"], sink["id"]))


def category_rows(tasks, rows, gaps):
    gap_ids = {gap["id"] for gap in gaps}
    output = []
    for name in survey.CATEGORIES:
        members = [row for row in rows if row["category"] == name]
        if not members:
            continue
        outcomes = Counter()
        for row in members:
            outcomes.update(row["outcomes"])
        output.append({"category": name, "tasks": len(members), "weight": sum(row["weight"] for row in members),
                       "gap_weight": sum(row["weight"] for row in members if row["id"] in gap_ids),
                       "outcomes": dict(outcomes), "total_ms": _median(row["total_ms"] for row in members),
                       "lookups": round(sum(row["lookups"] or 0 for row in members), 3),
                       "fallback_categories": _merge(row["fallback_categories"] for row in members)})
    return output


def build_report(directories, approval_wait_s=DEFAULT_APPROVAL_WAIT_S, round_s=DEFAULT_ROUND_S, tasks=None):
    """The complete report dict over one or more run directories."""
    tasks = survey.build_survey_tasks() if tasks is None else tasks
    records, trials, invalid, versions = load_records(directories)
    known, runs, unknown = {task["id"]: task for task in tasks}, {}, Counter()
    for record in records:
        if record.get("task") in known:
            runs.setdefault(record["task"], []).append(
                run_metrics(record, approval_wait_s, round_s, known[record["task"]]))
        else:
            unknown[str(record.get("task"))] += 1
    rows = [task_row(task, runs.get(task["id"], [])) for task in tasks]
    gaps = rank_gaps(tasks, runs, approval_wait_s)
    every = [run for values in runs.values() for run in values]
    attempted = [run for run in every if run["outcome"] != "skipped"]
    current = (survey.VERSION, grading.tasks_digest(tasks))
    contracts = {grading.record_contract(record) for record in records}
    contract_stamp = {"contract": next(iter(contracts)) if len(contracts) == 1 else None}
    if len(contracts) > 1:
        contract_stamp["contracts"] = sorted(contracts)
    return {"inputs": [str(Path(d).resolve()) for d in directories], "trials": trials, "invalid_trials": invalid,
            "records": len(records), "unknown_tasks": dict(unknown),
            "survey": {"version": current[0], "tasks_digest": current[1], "comparable": set(versions) <= {current},
                       "recorded_with": [{"version": version, "tasks_digest": digest, "trials": count}
                                         for (version, digest), count in sorted(versions.items(), key=str)]},
            "grading": dict({state: sum(run["grade"] == state for run in every) for state in grading.STATES},
                            total=len(every), not_run=max(0, trials * len(tasks) - len(every)),
                            **contract_stamp),
            "ungraded": ungraded_rows(tasks, runs), "questions": question_rows(tasks, runs),
            "parameters": {"approval_wait_s": approval_wait_s, "round_seconds": round_s},
            "activity_measured_runs": sum(run["activity_measured"] for run in attempted),
            "attempted_runs": len(attempted), "categories": category_rows(tasks, rows, gaps), "tasks": rows,
            "gaps": gaps, "extra_round_sinks": rank_extra_rounds(tasks, runs),
            "caveats": ["Weights are estimates in eval_survey_tasks, not telemetry.",
                        "Approval waits for declined Python are the --approval-wait-s assumption; the harness "
                        "declines at once.",
                        "Runs recorded before the activity hook have no lookup or rejected-plan counts.",
                        "An outcome is a routing class; only the Graded column says whether a check read the result. "
                        "An image is counted as captured for the model's turn: the chat rows do not record that a "
                        "provider request carried it.",
                        "Codex model rounds are not observable; its extra rounds use --round-seconds."]}


def ungraded_rows(tasks, runs):
    """Tasks with ungraded runs and why: the task's own reason, or what the run recorded."""
    rows = []
    for task in tasks:
        ungraded = [run for run in runs.get(task["id"], []) if run["grade"] == "ungraded"]
        if ungraded:
            reasons = sorted({run["ungraded_reason"] for run in ungraded if run["ungraded_reason"]})
            rows.append({"id": task["id"], "runs": len(ungraded), "declared": bool(grading.ungraded_reason(task)),
                         "reasons": [grading.ungraded_reason(task)] if grading.ungraded_reason(task) else reasons
                         or ["no check read this run"]})
    return rows


def question_rows(tasks, runs):
    """Per question: its answer outcomes, how many answer lines had to be read leniently, and how many attempted
    runs captured an image at all."""
    rows = []
    for task in tasks:
        if task["kind"] != "read_only":
            continue
        attempted = [run for run in runs.get(task["id"], []) if run["outcome"] != "skipped"]
        rows.append({"id": task["id"], "form": (task.get("answer") or {}).get("form"),
                     "needs_image": (task.get("answer") or {}).get("evidence"), "attempted": len(attempted),
                     "answers": dict(Counter(run["answer"] or "not recorded" for run in attempted)),
                     "parsed_leniently": sum(run["parsed"] == "leniently" for run in attempted),
                     "image_captured": sum(bool(run["image_captured"]) for run in attempted)})
    return rows


def _s(milliseconds):
    return "–" if milliseconds is None else "%.1f" % (milliseconds / 1000)


def _v(value):
    return "–" if value is None else ("%g" % value if isinstance(value, (int, float)) else str(value))


def _outcomes(outcomes):
    return ", ".join("%s %d" % (name, outcomes[name]) for name in OUTCOMES if outcomes.get(name)) or "–"


def _grades(grades):
    return ", ".join("%s %d" % (name, grades[name]) for name in grading.STATES if grades.get(name)) or "–"


def _cell(text):
    """One table cell: a pipe or a line break in a recorded reason must not break the row."""
    return " ".join(str(text).replace("|", "/").split())


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows]


def markdown(report):
    lines = ["# Request survey report", "",
             "%d valid trials (%d rate-limited skipped), %d records, %d attempted runs; activity counts on %d. "
             "Approval wait %g s per declined Python step; %g s per extra round when rounds are unknown." % (
                 report["trials"], report["invalid_trials"], report["records"], report["attempted_runs"],
                 report["activity_measured_runs"], report["parameters"]["approval_wait_s"],
                 report["parameters"]["round_seconds"]), "",
             "Grading, in runs: %s. A run is passed only when a check read its result; ungraded runs are listed "
             "below and are never counted as passed." % grading.describe(report["grading"]), "",
             "Survey version %s, task digest %s." % (report["survey"]["version"], report["survey"]["tasks_digest"])]
    contracts = report["grading"].get("contracts", [report["grading"].get("contract") or 0])
    lines += ["", grading.contract_notice(contracts) or "Grading contract: %s." % grading.contract_label(contracts)]
    if not report["survey"]["comparable"]:
        lines += ["", "**Not comparable: these runs were recorded with another version of the survey (%s). Its "
                  "tasks, checks or order differ, so the figures below mix different requests.**" % ", ".join(
                      "version %s, digest %s, %d trial(s)" % (entry["version"], entry["tasks_digest"], entry["trials"])
                      for entry in report["survey"]["recorded_with"])]
    lines += ["", "## Gaps by weight x gap rate x saving", ""]
    lines += _table(["Rank", "Task", "Category", "Expect", "Weight", "Gap runs", "Python steps", "Observed s",
                     "Native s", "Saving s", "Score", "Fallback categories"],
                    [[str(i + 1), g["id"], g["category"], g["expect"], _v(g["weight"]),
                      "%d/%d" % (g["gap_runs"], g["attempted"]), str(g["python_steps"]), _v(g["observed_s"]),
                      _v(g["native_baseline_s"]), _v(g["saving_s"]), _v(g["score"]),
                      ", ".join(g["fallback_categories"]) or "–"] for i, g in enumerate(report["gaps"])])
    lines += ["", "## Extra-round sinks by weight x seconds", ""]
    lines += _table(["Rank", "Task", "Category", "Weight", "Lookups", "Rejections", "Invalid", "Lookup s",
                     "Rejection s", "Score"],
                    [[str(i + 1), s["id"], s["category"], _v(s["weight"]), _v(s["lookups"]), _v(s["rejections"]),
                      _v(s["invalid_calls"]), _v(s["lookup_s"]), _v(s["rejection_s"]), _v(s["score"])]
                     for i, s in enumerate(report["extra_round_sinks"])])
    lines += ["", "## Categories", ""]
    lines += _table(["Category", "Tasks", "Weight", "Gap weight", "Outcomes", "Median total s", "Lookups",
                     "Fallback categories"],
                    [[c["category"], str(c["tasks"]), _v(c["weight"]), _v(c["gap_weight"]), _outcomes(c["outcomes"]),
                      _s(c["total_ms"]), _v(c["lookups"]), ", ".join(c["fallback_categories"]) or "–"]
                     for c in report["categories"]])
    lines += ["", "## Tasks (medians; counts are means per attempted run)", ""]
    lines += _table(["Task", "Category", "Weight", "Expect", "Outcomes", "Graded", "Rounds", "Lookups", "Invalid",
                     "Rejections", "First action s", "Host s", "After verified s", "Total s"],
                    [[r["id"], r["category"], _v(r["weight"]), r["expect"], _outcomes(r["outcomes"]),
                      _grades(r["grades"]), _v(r["rounds"]),
                      _v(r["lookups"]), _v(r["invalid_calls"]), _v(r["rejections"]), _s(r["first_native_action_ms"]),
                      _s(r["host_ms"]), _s(r["after_verified_ms"]), _s(r["total_ms"])] for r in report["tasks"]])
    lines += ["", "## Questions (answer checked against the harness's ground truth)", ""]
    lines += _table(["Task", "Answer form", "Needs an image", "Attempted", "Answers", "Read leniently",
                     "Runs with an image captured"],
                    [[q["id"], _v(q["form"]), _v(q["needs_image"]), str(q["attempted"]),
                      ", ".join("%s %d" % item for item in sorted(q["answers"].items())) or "–",
                      str(q["parsed_leniently"]), str(q["image_captured"])] for q in report["questions"]])
    lines += ["", "## Ungraded runs (never counted as passed)", ""]
    lines += _table(["Task", "Runs", "Declared by the task", "Reason"],
                    [[u["id"], str(u["runs"]), "yes" if u["declared"] else "no", "; ".join(u["reasons"])]
                     for u in report["ungraded"]])
    lines += ["", "## Caveats", ""] + ["- " + caveat for caveat in report["caveats"]]
    if report["unknown_tasks"]:
        lines.append("- Records of tasks outside the survey were ignored: " + ", ".join(sorted(report["unknown_tasks"])))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rank a request-survey run's gaps by latency return (sends nothing)")
    parser.add_argument("directories", nargs="+", help="eval_live_agent.py output directories")
    parser.add_argument("--output", default="", help="where to write the report (default: the first directory)")
    parser.add_argument("--approval-wait-s", type=float, default=DEFAULT_APPROVAL_WAIT_S,
                        help="assumed human review per declined Python step, in seconds")
    parser.add_argument("--round-seconds", type=float, default=DEFAULT_ROUND_S,
                        help="seconds per extra model round when the run's rounds are unknown (Codex)")
    parser.add_argument("--mixed-versions", action="store_true",
                        help="also report runs recorded with another survey version (their tasks differ; the report "
                             "says so)")
    args = parser.parse_args(argv)
    if not (0 <= args.approval_wait_s <= 3600 and 0 < args.round_seconds <= 600):
        parser.error("--approval-wait-s must be 0-3600 and --round-seconds 0-600")
    report = build_report(args.directories, args.approval_wait_s, args.round_seconds)
    if not report["records"]:
        print("No task records in " + ", ".join(args.directories), file=sys.stderr)
        return 2
    notice = grading.contract_notice(report["grading"].get("contracts", []))
    if notice and not args.mixed_versions:
        print(notice + " Pass --mixed-versions to report them anyway.", file=sys.stderr)
        return 2
    if not report["survey"]["comparable"] and not args.mixed_versions:
        print("These runs were recorded with another survey version (%s); this is version %s, digest %s. Their "
              "tasks differ, so they are not compared. Pass --mixed-versions to report them anyway." % (
                  ", ".join("version %s, digest %s" % (entry["version"], entry["tasks_digest"])
                            for entry in report["survey"]["recorded_with"]),
                  report["survey"]["version"], report["survey"]["tasks_digest"]), file=sys.stderr)
        return 2
    output = Path(args.output or args.directories[0])
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_JSON).write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / REPORT_MD).write_text(markdown(report), encoding="utf-8")
    print("SURVEY_REPORT", json.dumps({"json": str(output / REPORT_JSON), "markdown": str(output / REPORT_MD),
                                       "gaps": len(report["gaps"]), "sinks": len(report["extra_round_sinks"]),
                                       "grading": report["grading"], "survey_version": report["survey"]["version"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
