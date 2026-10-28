# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Record ratchet ceilings and tolerances from several runs of one build.

A restamp records an operation whose tolerance depends on its run-to-run
range from several runs of the build being stamped (gate receipts
``fixture/perf-ratchet.json`` or benchmark reports): the ceiling is the
highest run median and the tolerance follows ``perf_ratchet.restamp_tolerance``
(the standing rule, or twice the range with no floor for the snapshot
three-write sums, maintainer 2026-10-27). Runs of more than one binary or
platform are refused, so an ABAB's mixed A and B runs can never become a
ceiling. The restamps of record used three or four runs.

``perf_ratchet.py --update`` records other new operations from one run; it
leaves ``unstamped`` (range-only) operations for this tool, and the gate fails
on them until then.

Usage::

    python3 tests/python/angelo_agent/perf_rebaseline.py RUN1.json RUN2.json RUN3.json RUN4.json \\
        --operations 'snapshot/*_three_write_sum'

``--writes-files PATTERN`` marks operations that write files (the standing
10% floor; it never applies to the three-write sums). ``--allow-raise``
permits a higher ceiling than the recorded one. Exit codes: 0 recorded,
1 a refused raise, 2 unusable input.
"""

import argparse
import copy
import datetime
import fnmatch
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perf_ratchet  # noqa: E402
from perf_ratchet import RatchetError  # noqa: E402


def _build_identity(run):
    return (run.get("binary_sha256") or run.get("binary")) if isinstance(run, dict) else None


def rebaseline(ceilings, runs, names, *, writes_files=(), allow_raise=False, source="", today=None):
    """A new ceilings document recording ``names`` from ``runs``: ceiling = highest run median.

    Every run must measure every name in instructions, on one platform and one
    binary, or nothing is recorded; a ceiling rises only with ``allow_raise``.
    """
    measured, platforms, units = [], set(), {}
    for run in runs:
        platform, operations = perf_ratchet.load_results(run)
        platforms.add(platform)
        measured.append(operations)
        units.update(perf_ratchet.load_details(run)["units"])
    builds = {_build_identity(run) for run in runs} - {None}
    if len(platforms) != 1 or len(builds) > 1:
        raise RatchetError("A rebaseline needs runs of one build on one platform (got %d builds, platforms %s)"
                           % (len(builds), ", ".join(sorted(platforms))))
    if not names:
        raise RatchetError("No operation to rebaseline")
    platform = platforms.pop()
    result = copy.deepcopy(ceilings)
    entry = result["platforms"].setdefault(platform, {"operations": {}})
    operations, record = entry["operations"], {}
    for name in sorted(names):
        values = [run.get(name) for run in measured]
        if None in values:
            raise RatchetError("%s is missing from run %d" % (name, values.index(None) + 1))
        if units.get(name, "instructions") != "instructions":
            raise RatchetError("%s is an exact %s operation; perf_ratchet.py --update records it" % (name, units[name]))
        ceiling = max(values)
        tolerance = perf_ratchet.restamp_tolerance(name, values, default=ceilings["default_tolerance"],
                                                   writes_files=name in writes_files)
        previous = operations.get(name)
        if previous and ceiling > previous["ceiling"] and not allow_raise:
            raise RatchetError("Refusing to raise ceilings without --allow-raise: " + name)
        operations[name] = dict(previous or {}, ceiling=ceiling, tolerance=tolerance)
        record[name] = {"runs": values, "tolerance": tolerance}
    entry["operations"] = dict(sorted(operations.items()))
    entry.setdefault("rebaselined", []).append({"date": today or datetime.date.today().isoformat(),
                                                "source": source, "runs": len(runs), "operations": record})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", help="gate receipts or benchmark reports of one build")
    parser.add_argument("--operations", required=True, metavar="PATTERN", help="operations to record (fnmatch)")
    parser.add_argument("--writes-files", default="", metavar="PATTERN",
                        help="operations that write files (the standing 10%% floor)")
    parser.add_argument("--allow-raise", action="store_true", help="permit a higher ceiling than the recorded one")
    parser.add_argument("--ceilings", default=str(perf_ratchet.CEILINGS_PATH))
    args = parser.parse_args(argv)
    try:
        runs = [perf_ratchet._read_json(path, "results") for path in args.runs]
        path = Path(args.ceilings)
        ceilings = perf_ratchet.load_ceilings(perf_ratchet._read_json(path, "ceilings"))
        platform, first = perf_ratchet.load_results(runs[0])
        names = [name for name in first if fnmatch.fnmatchcase(name, args.operations)]
        writes = {name for name in names if args.writes_files and fnmatch.fnmatchcase(name, args.writes_files)}
        updated = rebaseline(ceilings, runs, names, writes_files=writes, allow_raise=args.allow_raise,
                             source=", ".join(Path(item).name for item in args.runs))
    except RatchetError as error:
        print("perf_rebaseline:", error, file=sys.stderr)
        return 1 if "Refusing to raise" in str(error) else 2
    path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    for name in sorted(names):
        print(json.dumps(dict(updated["platforms"][platform]["operations"][name], operation=name), sort_keys=True))
    print("Rebaselined", len(names), "operations in", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
