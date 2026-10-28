# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How the questions of finished runs were answered: the line each model wrote and how it was read. Sends nothing.

Reads ``eval_live_agent.py`` output directories (``eval-live-agent.json``) and
``bench_mcp_vs_angelo.py`` ones (``bench-mcp-vs-angelo.json``) and prints, per
question run, its outcome (``eval_answers``), whether the answer line was read
exactly as the form asks or leniently (and with which liberty), and the line
as written; then one total per answer form. It is how a short billed run shows
whether a model follows the answer forms before they become a bar::

    python3 tests/python/angelo_agent/answer_forms_report.py RUN_DIR [RUN_DIR ...]

A question whose reply carried no answer line at all shows ``no_answer`` and
no line: read that run's chat to see what the model wrote instead.
"""

import json
import argparse
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.
import sys

import eval_answers as answers
import eval_grading as grading


def question_runs(directory):
    """``[{"run", "side", "trial", "task", ...answer}]`` for every question a run directory recorded."""
    directory, runs = Path(directory), []
    bench = directory / "bench-mcp-vs-angelo.json"
    if bench.is_file():
        records = [(record.get("side"), record.get("trial"), record, grading.record_contract(record))
                   for record in json.loads(bench.read_text(encoding="utf-8")).get("records", [])]
    elif send_record.finished(directory / send_record.NAME):
        records = [("angelo", number, record, (trial.get("grading") or {}).get("contract") or 0)
                   for number, trial in enumerate(send_record.read(directory / send_record.NAME, "utf-8").get(
                       "trials", []), 1)
                   for record in trial.get("tasks", [])]
    else:
        raise ValueError("%s holds no eval-live-agent.json or bench-mcp-vs-angelo.json" % directory)
    for side, trial, record, contract in records:
        answer = answers.answer_of(record)
        if record.get("kind") == "read_only" and answer:
            runs.append(dict({key: answer.get(key) for key in ("form", "outcome", "parsed", "lenient", "stated")},
                             run=directory.name, side=side, trial=trial, task=record.get("task"),
                             grading_contract=contract))
    return runs


def totals(runs, mixed_versions=False):
    """Per answer form: runs, right answers, answers read at all and those read leniently."""
    notice = grading.contract_notice({grading.record_contract(run) for run in runs})
    if notice and not mixed_versions:
        raise ValueError(notice)
    output = {}
    for run in runs:
        entry = output.setdefault(run["form"] or "unknown", {"runs": 0, "right": 0, "read": 0, "parsed_leniently": 0})
        entry["runs"] += 1
        entry["right"] += run["outcome"] == "right"
        entry["read"] += bool(run["parsed"])
        entry["parsed_leniently"] += run["parsed"] == answers.LENIENTLY
    return output


def text(runs, mixed_versions=False):
    counted = totals(runs, mixed_versions)
    contracts = {grading.record_contract(run) for run in runs}
    lines = [grading.contract_notice(contracts) or "Grading contract: %s." % grading.contract_label(contracts)] if runs else []
    for run in runs:
        how = run["parsed"] or "-"
        if run.get("lenient"):
            how += " (%s)" % "; ".join(run["lenient"])
        lines.append("%s %s trial %s %s [%s]: %s, read %s: %s" % (
            run["run"], run["side"], run["trial"], run["task"], run["form"], run["outcome"], how,
            json.dumps(run["stated"]) if run["stated"] is not None else "no answer line"))
    for form, entry in sorted(counted.items()):
        lines.append("form %s: right %d of %d runs; answer line read in %d, leniently in %d" % (
            form, entry["right"], entry["runs"], entry["read"], entry["parsed_leniently"]))
    return "\n".join(lines)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        print("Usage: answer_forms_report.py RUN_DIR [RUN_DIR ...] [--mixed-versions]", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--mixed-versions", action="store_true", help="report different grading contracts with a warning")
    args = parser.parse_args(arguments)
    try:
        print(text([run for folder in args.directories for run in question_runs(folder)], args.mixed_versions))
    except ValueError as error:
        print("%s Pass --mixed-versions to report them anyway." % error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
