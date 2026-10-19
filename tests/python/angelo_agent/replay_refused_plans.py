# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replay recorded Claude plans through today's plan screen (unbilled, offline).

For every trial of a ``bench_mcp_vs_angelo.py`` or scenario run (the Angelo
chat record ``angelo-trial-NN/trial-01/state/sessions/<chat>.json`` beside
``angelo-trial-NN-transcript.jsonl``), every plan the model submitted (each
``StructuredOutput`` call of the transcript) goes through ``plan_validation.screen_plan``
and the plan it replaced, ``plan_validation.plan_problems``:

* a plan the host rejected (the prompt after it lists its problem lines)
  is reported as rejected again, or as running now with the operations it
  would dispatch, the lookups it refuses, the views it normalises and the job
  start it stops at; one today's validators accept whole is counted apart;
* every plan, rejected or accepted, is checked for the invariant
  (``invariant``): an invalid operation other than a lookup or a job start
  rejects it exactly as ``plan_problems`` does, a valid plan comes out
  unchanged, and otherwise exactly the mutating operations before the first
  operation refused alone are dispatched, with the arguments ``plan_problems``
  resolved.

Mutating operations other than job starts keep their recorded verdict: their
validity depended on the host's inferred bindings and script routing at the
time, which the recording does not hold. Lookups and job starts are validated
again with today's validators (short identifiers restored to long forms,
``replay_turn_context``); one whose verdict differs from the recorded one is
counted as drift. The view
budget is not replayed (the request's earlier views are not in the record).
Usage, from the repository root::

    python3 tests/python/angelo_agent/replay_refused_plans.py RUN_DIR [RUN_DIR ...] [--json OUT] [--as-recorded]
"""

import argparse
import itertools
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import replay_turn_context as replay  # noqa: E402
from angelo_agent import plan_validation, session_journal  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

PROMPT = "The host rejected your last plan before running any of its operations"   # plan_schema.rejection_prompt
FIX = "Fix these problems and submit a corrected plan:\n"
LINE = re.compile(r"operations\[(\d+)\] \(([^)]*)\): (.*)", re.S)
SCRIPT = "angelo_execute_python"


# ``--as-recorded``: job starts keep their recorded verdict too, so a refusal today's validators no longer give is
# replayed as it was (what the screen does with that class), instead of counted as valid today.
AS_RECORDED = {"jobs": False}


def _replayed(tool):
    """Whether today's validators judge ``tool`` (else its recorded verdict stands)."""
    return plan_validation._lookup(tool) if AS_RECORDED["jobs"] else plan_validation._alone(tool)


def _screens(operations, recorded):
    """``(screened, problems, resolved)`` of one plan, mutating verdicts taken from ``recorded`` ({index: message})."""
    def checker():
        indexes = itertools.count()

        def resolve(tool, arguments):
            index = next(indexes)
            if not _replayed(tool) and index in recorded:
                raise ValueError(recorded[index])
            return arguments
        return resolve

    def validate(tool, arguments):
        if _replayed(tool):
            return validate_call(tool, arguments)   # Lookups (and job starts): today's validators.
        return arguments                            # Other mutations: the recorded verdict (raised by resolve).

    with patch.object(plan_validation, "validate_call", validate):
        screened = plan_validation.screen_plan(operations, None, checker(), None)
        problems, resolved = plan_validation.plan_problems(operations, None, checker())
    return screened, problems, resolved


def invariant(operations, screened, problems, resolved):
    """Why the screen changed a mutating operation's handling beyond the contract, or None.

    An invalid operation other than a lookup or a job start must reject the plan exactly as ``plan_problems``
    does; a valid plan must come out unchanged; otherwise exactly the mutating operations before the first
    operation refused alone are dispatched, with the arguments ``plan_problems`` resolved.
    """
    blocking = [line for line in problems
                if not plan_validation._alone(operations[int(LINE.match(line).group(1))]["tool"])]
    if blocking:
        return None if screened.problems == problems else "an invalid edit no longer rejects the plan exactly"
    if not problems and (screened.problems or screened.operations != resolved or screened.refused or screened.notes
                         or screened.stops):
        return "a valid plan was changed"
    if screened.problems:
        return None if screened.problems == problems or screened.problems[-1].startswith("View budget") else (
            "a plan with nothing valid left is not rejected exactly")
    mutating = [op for op in screened.dispatched() if not plan_validation._lookup(op["tool"])]
    if mutating != [op for op in resolved[:screened.stop()] if not plan_validation._lookup(op["tool"])]:
        return "a mutating operation was dispatched differently"
    return None


def _scripts(plan):
    return [len(op["arguments"].get("code", "")) for op in (plan or {}).get("operations") or ()
            if isinstance(op, dict) and op.get("tool") == SCRIPT and isinstance(op.get("arguments"), dict)]


def rejection_lines(text):
    """The problem lines of a host prompt that rejected the last plan, else None.

    ``rejection_prompt``'s text, or the ``{"errors": [...]}`` message of the
    diagnostic lean protocol some diagnostic arms ran.
    """
    if text.startswith(PROMPT):
        body = text.split(FIX, 1)[1] if FIX in text else ""
        return [line for line in body.split("\nExact schemas", 1)[0].split("\n") if line.strip()]
    if text.startswith('{"errors": ['):
        try:
            errors = json.loads(text)["errors"]
        except (ValueError, KeyError, TypeError):
            return None
        return [str(line) for line in errors] if isinstance(errors, list) else None
    return None


def transcript_plans(path):
    """Every plan the runtime accepted, in order, and the host's rejection lines when the next prompt rejected it.

    Claude Code's transcript holds each ``StructuredOutput`` call and every
    prompt the host sent next; a call its schema check refused (an ``is_error``
    tool result) was never a plan. A rejection names the last plan before it.
    """
    plans, pending = [], {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        message = record.get("message") or {}
        content = message.get("content")
        if record.get("type") == "assistant" and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == "StructuredOutput":
                    pending[item.get("id")] = len(plans)
                    plans.append({"plan": item.get("input"), "rejection": None})
        elif record.get("type") == "user":
            items = content if isinstance(content, list) else [{"type": "text", "text": content}]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_result" and item.get("is_error") and item.get("tool_use_id") in pending:
                    plans[pending.pop(item["tool_use_id"])]["plan"] = None
                elif item.get("type") == "text" and plans:
                    lines = rejection_lines(str(item.get("text", "")))
                    last = next((entry for entry in reversed(plans) if entry["plan"] is not None), None)
                    if lines is not None and last is not None and last["rejection"] is None:
                        last["rejection"] = lines
    return [entry for entry in plans if entry["plan"] is not None]


def replay_trial(transcript, session, label):
    """Every recorded plan of one trial, screened (module docstring)."""
    maps = replay.long_form_maps(session)
    plans = transcript_plans(transcript)
    report = {"plans": 0, "accepted": 0, "accepted_changed": 0, "drift": [], "violations": [], "refused": []}
    for number, entry in enumerate(plans):
        plan = entry["plan"]
        operations = replay.restore_long_forms(plan.get("operations") if isinstance(plan, dict) else None, maps)
        if (not isinstance(operations, list) or not operations
                or any(not isinstance(op, dict) or set(op) != {"tool", "arguments"} for op in operations)):
            continue
        lines = entry["rejection"] or []
        recorded = {int(m.group(1)): m.group(3) for m in map(LINE.match, lines) if m}
        screened, problems, resolved = _screens(operations, recorded)
        report["plans"] += 1
        violation = invariant(operations, screened, problems, resolved)
        if violation:
            report["violations"].append({"where": label, "plan": number, "why": violation})
        today = {int(LINE.match(line).group(1)) for line in problems}
        for position, operation in enumerate(operations):
            if _replayed(operation["tool"]) and (position in today) != (position in recorded):
                report["drift"].append({"where": label, "plan": number, "tool": operation["tool"],
                                        "arguments": operation["arguments"], "recorded": recorded.get(position),
                                        "today": problems})
        if entry["rejection"] is None:
            report["accepted"] += 1
            report["accepted_changed"] += bool(screened.problems or screened.refused or screened.notes)
            continue
        dispatched = [op["tool"] for op in screened.dispatched()] if not screened.problems else []
        corrected = plans[number + 1]["plan"] if number + 1 < len(plans) else None
        report["refused"].append({
            "where": label, "plan": number, "recorded": lines,
            "recorded_tools": sorted({operations[i]["tool"] for i in recorded if i < len(operations)}),
            "plan_level": [line for line in lines if not LINE.match(line)],
            "outcome": "rejected" if screened.problems else "runs",
            "rejected_for": sorted({operations[int(LINE.match(line).group(1))]["tool"] for line in screened.problems
                                    if LINE.match(line) and not plan_validation._alone(
                                        operations[int(LINE.match(line).group(1))]["tool"])}),
            "accepted_today": not problems,
            "job_start_refused": {str(i): line for i, line in screened.stops.items()},
            "classes": sorted(({"normalised view"} if screened.notes else set())
                              | ({"refused lookup"} if screened.refused else set())
                              | ({"refused job start"} if screened.stops else set())),
            "dispatched": dispatched, "script_runs": SCRIPT in dispatched,
            "refused_lookups": {str(i): line for i, line in screened.refused.items()},
            "normalized": {str(i): note for i, note in screened.notes.items()},
            "script_bytes": _scripts({"operations": operations}), "resent_script_bytes": _scripts(corrected)})
    return report


def replay_run(directory):
    reports = []
    for transcript, session_path in replay.find_trials(Path(directory)):
        session = session_journal.read(session_path)[0]
        label = "%s/%s" % (Path(directory).name, transcript.name.split("-transcript")[0])
        reports.append(replay_trial(transcript, session, label))
    return reports


def summary(reports):
    refused = [row for report in reports for row in report["refused"]]
    lookups_only = [row for row in refused if not any(
        not plan_validation._alone(tool) for tool in row["recorded_tools"]) and not row["plan_level"]]
    runs = [row for row in refused if row["outcome"] == "runs" and not row["accepted_today"]]
    return {
        "trials": len(reports), "plans": sum(r["plans"] for r in reports),
        "accepted": sum(r["accepted"] for r in reports),
        "accepted_changed": sum(r["accepted_changed"] for r in reports),
        "violations": [v for r in reports for v in r["violations"]],
        "lookup_drift": [d for r in reports for d in r["drift"]],
        "refused_plans": len(refused),
        "refused_for_lookups_or_job_starts_only": len(lookups_only),
        "valid_by_todays_validators": sum(row["accepted_today"] for row in refused),
        "now_run": len(runs),
        "now_run_a_script": sum(row["script_runs"] for row in runs),
        "now_run_by_classes": _count(" + ".join(row["classes"]) for row in runs),
        "now_run_a_script_by_classes": _count(" + ".join(row["classes"]) for row in runs if row["script_runs"]),
        "still_rejected": sum(row["outcome"] == "rejected" for row in refused),
        "still_rejected_for": dict(sorted(_count(t for row in refused if row["outcome"] == "rejected"
                                                 for t in row["rejected_for"] or ["nothing valid left"]).items())),
        "script_bytes_now_kept": sum(sum(row["script_bytes"]) for row in runs if row["script_runs"]),
        "resent_script_bytes_avoided": sum(sum(row["resent_script_bytes"]) for row in runs if row["script_runs"]),
    }


def _count(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay recorded Claude plans through today's plan screen")
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--json", default="")
    parser.add_argument("--as-recorded", action="store_true",
                        help="keep job starts' recorded verdicts (refusals today's validators no longer give)")
    args = parser.parse_args(argv)
    AS_RECORDED["jobs"] = args.as_recorded
    reports = [report for directory in args.directories for report in replay_run(directory)]
    totals = summary(reports)
    print(json.dumps({key: value for key, value in totals.items() if key not in ("violations", "lookup_drift")},
                     indent=1))
    print("invariant violations: %d; lookup verdict drift: %d" % (len(totals["violations"]), len(totals["lookup_drift"])))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": totals, "trials": reports}, indent=1))
    return 1 if totals["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
