# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turn finished billed runs into ``model_profiles.MEASURED`` entries. Sends nothing.

Reads the Angelo side of ``bench_mcp_vs_angelo.py`` output directories
(``bench-mcp-vs-angelo.json``) and ``eval_live_agent.py`` output directories
(``eval-live-agent.json``), groups tasks by served model, effort and task kind
(edits and staged previews are ``scene_edit``, read-only questions
``scene_read``) and prints the entries to review and paste into
``scripts/addons_core/angelo_agent/model_profiles.py``. Pasting them is what
admits a model to Auto routing (doc/angelo/model_routing.rst), so review the
pass rates and sample sizes first.

Counting rules: a task skipped because an earlier task failed is not a sample
(its cause already counts once); a task passes only when it completed and its
host checks passed; latency is wall time per task (for a live-eval run, send to
idle, so an Auto escalation's re-run is included); cost is Claude Code's
estimate per task over all its attempts, unknown when one of them did not
record it (``eval_receipts.attempts_since``; a subscription spends plan
usage, not this amount).

Ungraded runs (``eval_grading``: a question whose answer no check read, an
edit no check could read) are evidence of nothing, so they are neither passed
nor failed here: ``tasks`` and ``pass_rate`` count graded runs only and
``ungraded`` states the rest. A kind measured only by ungraded runs has
``tasks`` 0 and so cannot admit a model. Their latency and cost still count.

Gate runs of different gate-set versions (``eval_gate``) are not one sample:
version 1 counted a question as passed without reading its answer. Nor are
runs of different approval modes (``eval_gate.approval_mode``: Auto approves
typed edits itself, Ask waits for the tester; a run that states no mode was
recorded in Ask). Both are refused unless ``--mixed-versions`` is given, and
then named on stderr.

A run that reused MCP trials of another model or effort (``--cross-model-reuse``,
a model-tier measurement in ``model_routing.rst``) is accepted: only Angelo's
own records are read (``_bench_samples``), and nothing here emits or forwards
a comparison with the MCP side, whose model differs. A diagnostic run
(``eval_diagnostics``) is refused, as by the gate tool, which also refuses a
cross-model run.

Usage, from the repository root::

    python3 tests/python/angelo_agent/measured_results.py <runs>/haiku-5task <runs>/haiku-long \\
        [--model claude-haiku-5-5] [--effort low] [--mixed-versions]

where ``<runs>`` is the folder the billed runs wrote to (their ``--output``).
"""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_diagnostics  # noqa: E402
import eval_gate  # noqa: E402
import eval_grading  # noqa: E402
from angelo_agent import model_profiles  # noqa: E402

KINDS = {"edit": "scene_edit", "preview": "scene_edit", "read_only": "scene_read"}


def _percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bench_samples(data, model, effort):
    plan = data.get("meta", {}).get("plan", {})
    for record in data.get("records", []):
        if record.get("side") != "angelo":
            continue
        served = record.get("models") or []
        yield {"model": model or (served[0] if len(served) == 1 else plan.get("model")),
               "effort": effort or plan.get("effort"), "kind": record.get("kind"), "status": record.get("status"),
               "grade": eval_grading.state(record),
               "passed": eval_grading.state(record) == "passed" and record.get("status") == "completed",
               "wall_ms": record.get("wall_ms"), "cost_usd": (record.get("provider_usage") or {}).get("cost_usd")}


def _eval_samples(data, model, effort):
    for trial in data.get("trials", []):
        for record in trial.get("tasks", []):
            receipt = record.get("receipt") or {}
            route = receipt.get("route") or {}
            served = route.get("served_models") or []
            yield {"model": model or (served[0] if len(served) == 1 else route.get("requested_model")),
                   "effort": effort or route.get("effort"), "kind": record.get("kind"), "status": record.get("status"),
                   "grade": eval_grading.state(record),
                   "passed": eval_grading.state(record) == "passed" and record.get("status") == "completed",
                   "wall_ms": record.get("send_to_idle_ms") or record.get("total_ms"),
                   # Every attempt of the request, including an Auto escalation's re-run.
                   "cost_usd": (record.get("usage_all_attempts") or receipt.get("usage") or {}).get("cost_usd")}


def _load(directory):
    directory = Path(directory)
    bench, live = directory / "bench-mcp-vs-angelo.json", directory / "eval-live-agent.json"
    if bench.is_file():
        return json.loads(bench.read_text()), True
    if live.is_file():
        return json.loads(live.read_text()), False
    raise ValueError("%s holds no bench-mcp-vs-angelo.json or eval-live-agent.json" % directory)


def samples(directory, model="", effort=""):
    """Per-task samples of one run directory."""
    data, bench = _load(directory)
    return list((_bench_samples if bench else _eval_samples)(data, model, effort))


def gate_version(directory):
    """The gate-set version a run directory's Angelo records state: 1 for a gate run recorded before versions,
    None for a run that asked no gate question."""
    data, bench = _load(directory)
    if bench:
        records = [record for record in data.get("records", []) if record.get("side") == "angelo"]
        stated = {record.get("gate_version") for record in records}
    else:
        stamps = [trial.get("grading") or {} for trial in data.get("trials", [])]
        if any(stamp.get("task_set") not in eval_gate.SETS + (None,) for stamp in stamps):
            return None   # A stamped run of another set (coverage opens with a gate question; it is no gate run).
        records = [record for trial in data.get("trials", []) for record in trial.get("tasks", [])]
        stated = {stamp.get("gate_version") for stamp in stamps}
    asked = any(str(record.get("task")) == "read_only_question" or str(record.get("task")).startswith("question_")
                for record in records)
    return max(stated - {None}, default=1 if asked else None)


def approval_modes(directory):
    """The approval modes a run directory's Angelo trials state (``eval_gate.approval_mode``)."""
    data, bench = _load(directory)
    if bench:
        return {eval_gate.approval_mode(record) for record in data.get("records", []) if record.get("side") == "angelo"}
    return {eval_gate.approval_mode(trial) for trial in data.get("trials", [])}


def mode_notice(directories):
    modes = set().union(*(approval_modes(directory) for directory in directories))
    return None if eval_gate.modes_comparable(modes) else eval_gate.NOT_COMPARABLE_APPROVAL % ", ".join(sorted(modes))


def measured(directories, model="", effort="", date="", mixed_versions=False):
    """``{canonical model: {effort: {kind: entry}}}`` over all directories' samples; never a diagnostic run's."""
    # A diagnostic run raises DiagnosticRun, which --mixed-versions does not override. A cross-model reuse does not:
    # only the Angelo side is read here, and the tier measurements in model_routing.rst are such runs.
    eval_diagnostics.refuse_claims(directories, angelo_only=True)
    versions = {gate_version(directory) for directory in directories} - {None}
    if not eval_gate.comparable(versions) and not mixed_versions:
        raise ValueError(eval_gate.NOT_COMPARABLE % eval_gate.describe(versions))
    if mode_notice(directories) and not mixed_versions:
        raise ValueError(mode_notice(directories))
    notice = eval_grading.contract_notice({value for directory in directories for value in grading_contracts(directory)})
    if notice and not mixed_versions:
        raise ValueError(notice)
    groups = {}
    for directory in directories:
        for sample in samples(directory, model, effort):
            kind = KINDS.get(sample["kind"])
            canonical = model_profiles.resolve(sample["model"] or "")
            if kind is None or canonical is None or not sample["effort"] or sample["status"] == "skipped":
                continue
            groups.setdefault((canonical, sample["effort"], kind), []).append((sample, Path(directory).name))
    output = {}
    for (canonical, run_effort, kind), entries in sorted(groups.items()):
        walls = [s["wall_ms"] for s, _ in entries if type(s["wall_ms"]) in {int, float}]
        costs = [s["cost_usd"] for s, _ in entries if type(s["cost_usd"]) in {int, float}]
        graded = [s for s, _ in entries if s["grade"] != "ungraded"]
        output.setdefault(canonical, {}).setdefault(run_effort, {})[kind] = {
            "tasks": len(graded), "ungraded": len(entries) - len(graded),
            "pass_rate": round(sum(s["passed"] for s in graded) / len(graded), 4) if graded else 0.0,
            "p75_ms": round(_percentile(walls, .75), 1) if walls else None,
            "cost_usd": round(_percentile(costs, .5), 6) if costs else None,
            "source": ", ".join(sorted({name for _, name in entries})), "date": date}
    return output


def grading_contracts(directory):
    """Original grading contracts, including unrecorded ones; do not stamp old results with today's parser."""
    data, bench = _load(directory)
    if bench:
        return {eval_grading.record_contract(record) for record in data.get("records", [])
                if record.get("side") == "angelo"}
    return {(trial.get("grading") or {}).get("contract") or 0 for trial in data.get("trials", [])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="MEASURED entries from finished runs (sends nothing)")
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--model", default="", help="override the served model (canonical ID or alias)")
    parser.add_argument("--effort", default="", help="override the effort the runs used")
    parser.add_argument("--date", default="", help="the date the runs were made, YYYY-MM-DD")
    parser.add_argument("--mixed-versions", action="store_true",
                        help="also combine runs of different gate-set versions or approval modes (named on stderr)")
    args = parser.parse_args(argv)
    try:
        output = measured(args.directories, args.model, args.effort, args.date, args.mixed_versions)
    except eval_diagnostics.DiagnosticRun as refusal:
        print(refusal, file=sys.stderr)
        return 2
    except ValueError as error:
        print("%s Pass --mixed-versions to combine them anyway." % error, file=sys.stderr)
        return 2
    versions = {gate_version(directory) for directory in args.directories} - {None}
    if not eval_gate.comparable(versions):
        print(eval_gate.NOT_COMPARABLE % eval_gate.describe(versions), file=sys.stderr)
    notice = eval_grading.contract_notice({value for directory in args.directories for value in grading_contracts(directory)})
    for note in (notice, mode_notice(args.directories)):
        if note:
            print(note, file=sys.stderr)
    crossed = [Path(directory).name for directory in args.directories
               if eval_diagnostics.cross_model(_load(directory)[0])]
    if crossed:   # Angelo's own samples only: no comparison with the MCP side, whose model differs.
        print("Cross-model reuse (%s): only Angelo's samples are read; nothing here compares them with the MCP side."
              % ", ".join(crossed), file=sys.stderr)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
