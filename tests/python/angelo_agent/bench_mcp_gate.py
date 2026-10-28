# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Gate figures of ``bench_mcp_vs_angelo.py`` runs: what a run's totals mean at its gate-set version.

The gate sets are versioned (``eval_gate``). A run of version 1 keeps the
figure it was recorded with (host checks, which for a question meant the
unchanged scene alone) and states beside it how many of those passes no check
read. A run of version 2 or later has one figure, the grading contract's
(``eval_grading``): a clean five-task run reads passed 30 of 30 and a clean
forty-task run passed 120 of 120, every answer read. Runs of different
versions are never shown together without saying so.

Totals of finished runs, one line each (reads only; sends nothing)::

    python3 tests/python/angelo_agent/bench_mcp_gate.py RUN_DIR [RUN_DIR ...] [--mixed-versions]

Runs of more than one gate-set version, or of more than one approval mode
(``eval_gate.approval_mode``; a run that states none was recorded in Ask), are
refused unless ``--mixed-versions`` is given, and then named.
"""

import argparse
import json
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.
import sys

import bench_integrity
import eval_answers as answers
import eval_diagnostics
import eval_gate
import eval_grading as grading
import eval_live_tasks as live

UNCHECKED_NOTE = ("\\* A read-only question of gate set version 1, whose answer no check reads. Its runs left the "
                  "scene unchanged, which is all the pass columns and the pass rate mean for it; they keep that "
                  "meaning so recorded gate runs stay comparable. The grading contract (`eval_grading`) counts "
                  "these runs as ungraded, not passed: see the Graded row.")


def is_gate_list(tasks):
    """Whether a task list is a gate set's (it holds a gate question)."""
    return any(task.get("source") in grading.LEGACY_QUESTIONS and task["kind"] == "read_only" for task in tasks)


def side_versions(records):
    """``{side: gate-set version}`` of a gate run's records (a side recorded before versions is 1)."""
    versions = {}
    for record in records:
        versions[record["side"]] = max(versions.get(record["side"], 1), eval_gate.record_version(record))
    return versions


def unchecked(task, version):
    """Whether a task's passes, at that gate-set version, say nothing about an answer (a version-1 question)."""
    return bool(task) and version < 2 and task["kind"] == "read_only" and task.get("source") in grading.LEGACY_QUESTIONS


def version_lines(versions):
    """The Markdown lines that state a gate run's version, and that its sides differ when they do."""
    if not versions:
        return []
    if eval_gate.comparable(versions.values()):
        return ["Gate set version %d." % next(iter(versions.values())), ""]
    sides = "; ".join("%s: version %d" % item for item in sorted(versions.items()))
    return ["**%s** Here: %s." % (eval_gate.NOT_COMPARABLE % eval_gate.describe(set(versions.values())), sides), ""]


def recorded_version(output):
    """The gate-set version a run directory was recorded with: what its Angelo trials, else its MCP trials,
    state. 1 when it holds trials and none states a version (recorded before versions); None when it holds none.
    """
    seen = False
    for pattern, stamps in (
            ("angelo-trial-*/" + send_record.NAME, lambda data: [(data.get("plan") or {}).get("grading")] + [
                trial.get("grading") for trial in data.get("trials") or []]),   # A trial that never started has none.
            ("mcp-trial-*/trial.json", lambda data: [data])):
        for path in send_record.finished_only(sorted(Path(output).glob(pattern))):   # MCP's: no in_progress key.
            try:
                data = send_record.read(path, "utf-8")
            except (OSError, ValueError):
                continue
            seen = True
            stated = [stamp["gate_version"] for stamp in stamps(data) if isinstance(stamp, dict)
                      and stamp.get("gate_version")]
            if stated:
                return stated[0]
    return 1 if seen else None


def run_totals(results, side="angelo"):
    """One side's totals of a finished run (``bench-mcp-vs-angelo.json``), rate-limited trials left out.

    ``graded`` is every task the side ran (Angelo's staged preview included)
    under the grading contract. For gate set version 1, ``passed`` of ``runs``
    is the figure those runs were compared by (host checks) and
    ``unread_answers`` how many of the passed ones are questions no check
    read; from version 2 both figures are the contract's and none is unread.
    """
    plan = results["meta"]["plan"]
    task_set, count = plan.get("task_set", "default"), len(plan.get("angelo_tasks") or ()) or 2
    every = [record for record in results["records"] if record["side"] == side]
    records = [record for record in every if not record.get("invalid")]
    # What the side's own records state (rate-limited ones too), else the run's plan, else recorded before versions.
    version = (side_versions(every).get(side) or plan.get("gate_version") or 1) if task_set in eval_gate.SETS else None
    tasks = live.tasks_for(task_set, count, version)
    ran = plan.get(side + "_tasks") or [task["id"] for task in tasks]   # The MCP side runs no staged preview.
    graded = grading.totals(records, tasks, planned=len({record["trial"] for record in records}) * len(ran))
    passed = sum(record.get("host_check_passed") is True for record in records)
    return {"runs": len(records), "passed": passed, "unread_answers": passed - graded["passed"], "graded": graded,
            "gate_version": version, "answers": answers.lenient_counts(records),
            "grading_contracts": sorted({grading.record_contract(record) for record in every}),
            "approval_modes": sorted({eval_gate.approval_mode(record) for record in every}),
            # Scripts as the run's report states them (bench_integrity): "scripts allowed" or reviewed.
            "scripts": ((results["meta"].get("conditions") or {}).get("script_mode")
                        or bench_integrity.script_mode(plan.get("angelo")))}


def line(name, totals):
    """One run's totals as text: the contract's figure, and for version 1 the recorded one beside it."""
    version = totals["gate_version"]
    label = "%s (gate set version %d)" % (name, version) if version else name
    contracts = totals.get("grading_contracts", [])
    if any(contracts):
        label += " (grading contract %s)" % grading.contract_label(contracts)
    lenient = totals["answers"]
    read = ("; answers parsed leniently: %d of %d" % (lenient["parsed_leniently"], lenient["read"])
            if lenient["read"] else "")
    label += " (scripts %s)" % totals["scripts"] if totals.get("scripts") else ""
    if version == 1:
        return "%s: host checks %d/%d (%d of them questions whose answer no check read); graded: %s" % (
            label, totals["passed"], totals["runs"], totals["unread_answers"], grading.describe(totals["graded"]))
    return "%s: %s%s" % (label, grading.describe(totals["graded"]), read)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gate totals of finished benchmark runs (sends nothing)")
    parser.add_argument("directories", nargs="+", metavar="RUN_DIR")
    parser.add_argument("--mixed-versions", action="store_true",
                        help="print runs of different gate-set versions together, named as not comparable")
    args = parser.parse_args(argv)
    try:
        eval_diagnostics.refuse_claims(args.directories)   # Never a gate figure, whatever else is passed.
    except eval_diagnostics.DiagnosticRun as refusal:
        print(refusal, file=sys.stderr)
        return 2
    runs = [(Path(folder).name, run_totals(json.loads((Path(folder) / "bench-mcp-vs-angelo.json").read_text(
        encoding="utf-8")))) for folder in args.directories]
    versions = {totals["gate_version"] for _, totals in runs if totals["gate_version"]}
    if not eval_gate.comparable(versions):
        notice = eval_gate.NOT_COMPARABLE % eval_gate.describe(versions)
        if not args.mixed_versions:
            print(notice + " Pass --mixed-versions to print them together anyway.", file=sys.stderr)
            return 2
        print(notice)
    contracts = {value for _, totals in runs for value in totals["grading_contracts"]}
    modes = {mode for _, totals in runs for mode in totals.get("approval_modes", ())}
    for notice in (grading.contract_notice(contracts),
                   None if eval_gate.modes_comparable(modes) else eval_gate.NOT_COMPARABLE_APPROVAL % ", ".join(
                       sorted(modes))):
        if notice:
            if not args.mixed_versions:
                print(notice + " Pass --mixed-versions to print them together anyway.", file=sys.stderr)
                return 2
            print(notice)
    for name, totals in runs:
        print(line(name, totals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
