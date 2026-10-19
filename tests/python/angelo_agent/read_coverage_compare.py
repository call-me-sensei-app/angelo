# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare two read_coverage_sweep.py records: what a baseline answered and a candidate does not.

  python3 tests/python/angelo_agent/read_coverage_compare.py BASELINE.json CANDIDATE.json [--json OUT]

Every read or row the baseline answered and the candidate does not is listed with
the candidate's reason. Reasons that only catalogued built-in getters produce
(doc/angelo/scene_intelligence.rst, "Catalogued built-in getters") are reported as
``catalogued``; anything else (a different code, an exception, a missing path) is a
``regression`` and makes the exit status 1. A path the candidate never visited
because an ancestor was withheld carries that ancestor (``under``) and its reason.
Values that both answered but differ are listed as ``changed`` for review (a native
replacement must give the built-in getter's answer), and the candidate's new answers
as ``gained``. A record that stopped at its read limit is not conclusive: the exit
status is 2 until both records are complete. No Blender is needed.
"""

import argparse
import json
import re
import sys

# Codes only the catalogue produces: a replacement that would load a file, evaluate, resync a
# view layer or run add-on code, or a value larger than a read may copy.
CATALOGUED = frozenset({"LOADS_FILE", "NOT_EVALUATED", "VIEW_LAYER_OUT_OF_SYNC", "EXTENSION_CODE",
                        "RESOURCE_LIMIT"})
_LAST_STEP = re.compile(r"(\.[^.\[]+|\[\d+\])$")


def reason(state):
    """The refusal code of a recorded state (``W:CODE``, ``M:CODE``, ``U:Type``), else None."""
    if state is None:
        return "NOT_VISITED"
    kind, _, detail = state.partition(":")
    return detail if kind in ("W", "M") else ("UNREADABLE:" + detail if kind == "U" else None)


def _path(key):
    """Data path of a recorded key: ``scene.objects@0|[2].name`` is ``scene.objects[2].name``."""
    address, _, row = key.partition("|")
    path = address.rsplit("@", 1)[0]
    for part in row.split(".") if row else ():
        path += part if part.startswith("[") else "." + part
    return path


def _withheld_ancestor(key, paths):
    """The nearest ancestor of a key that the record refused or withheld, with its code."""
    path = _path(key)
    while True:
        parent = _LAST_STEP.sub("", path)
        if parent == path:
            return None, None
        path = parent
        code = reason(paths[path]) if path in paths else None
        if code is not None:
            return path, code


def _lost(report, key, other, candidate_paths):
    if other is None:
        ancestor, code = _withheld_ancestor(key, candidate_paths)
        if ancestor is not None:
            entry = {"read": key, "reason": code, "under": ancestor}
            report["catalogued" if code in CATALOGUED else "regression"].append(entry)
            return
    code = reason(other)
    report["catalogued" if code in CATALOGUED else "regression"].append({"read": key, "reason": code})


def compare(baseline, candidate):
    base, cand = baseline["entries"], candidate["entries"]
    candidate_paths = {}
    for key, state in cand.items():
        candidate_paths.setdefault(_path(key), state)
    report = {"catalogued": [], "regression": [], "changed": [], "gained": []}
    for key, state in sorted(base.items()):
        other = cand.get(key)
        if state.startswith("V:"):
            if other is not None and other.startswith("V:"):
                if other != state:
                    report["changed"].append({"read": key})
            elif other is None or reason(other) is not None:
                _lost(report, key, other, candidate_paths)
        elif state.startswith("K:") and (other is None or not other.startswith("K:")):
            _lost(report, key, other, candidate_paths)
    for key, state in sorted(cand.items()):
        if state.startswith("V:") and not base.get(key, "").startswith("V:"):
            report["gained"].append({"read": key, "baseline": base.get(key, "NOT_VISITED")})
    report["summary"] = {name: len(report[name]) for name in ("catalogued", "regression", "changed", "gained")}
    report["summary"].update(
        complete=baseline.get("complete") is not False and candidate.get("complete") is not False,
        baseline_reads=baseline.get("reads"), candidate_reads=candidate.get("reads"),
        baseline_seconds=baseline.get("seconds"), candidate_seconds=candidate.get("seconds"),
        baseline_cpu_seconds=baseline.get("cpu_seconds"), candidate_cpu_seconds=candidate.get("cpu_seconds"),
        baseline_instructions=baseline.get("instructions"), candidate_instructions=candidate.get("instructions"),
        baseline_cycles=baseline.get("cycles"), candidate_cycles=candidate.get("cycles"),
        baseline_skipped=baseline.get("skipped"), candidate_skipped=candidate.get("skipped"))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--json", help="write the full comparison here")
    args = parser.parse_args(argv)
    with open(args.baseline, encoding="utf-8") as stream:
        baseline = json.load(stream)
    with open(args.candidate, encoding="utf-8") as stream:
        candidate = json.load(stream)
    report = compare(baseline, candidate)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=1)
    print(json.dumps(report["summary"]))
    for name in ("regression", "catalogued", "changed"):
        for entry in report[name]:
            under = " under " + entry["under"] if "under" in entry else ""
            print(name.upper(), entry["read"], (entry.get("reason") or "") + under)
    if not report["summary"]["complete"]:
        print("INCOMPLETE: a sweep stopped at its read limit; raise ANGELO_READ_COVERAGE_MAX and rerun both.",
              file=sys.stderr)
        return 2
    return 1 if report["regression"] else 0


if __name__ == "__main__":
    sys.exit(main())
