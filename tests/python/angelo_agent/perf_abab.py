# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Interleaved ABAB for noisy ratchet operations, against the last stamped build.

An operation whose own samples spread by more than a third of its tolerance
(``perf_ratchet.NOISY_FRACTION``) cannot be judged by one run against an
absolute ceiling: the noise alone can cross it. Such an operation is decided
by running its benchmark four times in fresh processes, alternating the last
stamped build (A) and the candidate (B): A, B, A, B. Both sides get the same
harness, arguments, quiet-machine wait and isolated folders, so the machine's
state at each moment affects both.

The decision rule, set on 2026-10-21 before any run used it:

* ``regressed`` when both B values exceed both A values AND the mean of B
  exceeds the mean of A by more than the operation's tolerance;
* ``pass`` otherwise;
* ``inconclusive`` when any of the four runs has no valid value for the
  operation (failed, rejected or unmeasured), which the gate treats as a
  failure, never as a pass.

The order of both B runs above both A runs alone happens by chance one time
in six with no change at all; the magnitude condition is what keeps noise
from failing the gate, and the order condition keeps a single outlier run
from doing so.

A is the build recorded in ``perf_ceilings.json`` (``last_measured``): its
binary's SHA-256 or its Mach-O linker UUID must match (signing a release app
changes the SHA-256, not the UUID), or the comparison is refused. Keep a copy
of the stamped app. Only an explicit ``--baseline-approval TEXT`` (who
approved which build, and why) admits another build; the text is recorded with
the result.

Usage::

    python3 tests/python/angelo_agent/perf_abab.py --baseline <stamped Angelo> \\
        --candidate <Angelo> --receipt <fixture/perf-ratchet.json> --out <folder>
"""

import argparse
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perf_ratchet  # noqa: E402

ORDER = ("A", "B", "A", "B")


def verdict(a_values, b_values, tolerance):
    """The pre-registered ABAB rule for one operation."""
    if len(a_values) != 2 or len(b_values) != 2 or None in a_values + b_values:
        return "inconclusive"
    ordered = min(b_values) > max(a_values)
    larger = statistics.fmean(b_values) > statistics.fmean(a_values) * (1 + tolerance)
    return "regressed" if ordered and larger else "pass"


def decide(runs, operations, tolerances):
    """``{operation: {"A": [..], "B": [..], "verdict": ...}}`` from the four runs' ratchet sections.

    ``runs`` is a list of ``(side, section or None)`` in run order.
    """
    result = {}
    for name in operations:
        values = {"A": [], "B": []}
        for side, section in runs:
            value = ((section or {}).get("operations") or {}).get(name)
            values[side].append(value if isinstance(value, int) and value > 0 else None)
        result[name] = dict(values, verdict=verdict(values["A"], values["B"], tolerances[name]),
                            tolerance=tolerances[name])
    return result


def baseline_problem(ceilings, platform, baseline_sha256, baseline_uuid=None, approval=""):
    """Why the baseline binary is not the last stamped build (None when it is, or when an approval admits it)."""
    if approval.strip():
        return None
    last = ceilings["platforms"].get(platform, {}).get("last_measured") or {}
    if not last.get("binary_sha256"):
        return "perf_ceilings.json records no stamped build for %s" % platform
    if last["binary_sha256"] == baseline_sha256 or (baseline_uuid and last.get("binary_uuid") == baseline_uuid):
        return None
    return "the baseline binary is not the last stamped build (%s, %s)" % (last.get("commit"), last.get("date"))


def run_abab(run_benchmark, baseline, candidate, scripts, operations, tolerances):
    """Run each needed benchmark A, B, A, B; ``run_benchmark(binary, script, run_label)`` returns a section or None."""
    by_operation = {}
    for script in scripts:
        runs = [(side, run_benchmark(baseline if side == "A" else candidate, script, "%s%d" % (side, index)))
                for index, side in enumerate(ORDER)]
        wanted = [name for name in operations if operations[name] == script]
        by_operation.update(decide(runs, wanted, tolerances))
    return by_operation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", required=True, help="the last stamped Angelo binary (A)")
    parser.add_argument("--candidate", required=True, help="the Angelo binary under test (B)")
    parser.add_argument("--receipt", required=True, help="the candidate's fixture/perf-ratchet.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--baseline-approval", default="", help="who approved a baseline that is not the stamped build, and why")
    parser.add_argument("--max-load", type=float, default=None, help="as test_blender_perf_ratchet --max-load (recorded)")
    args = parser.parse_args(argv)
    import test_blender_perf_ratchet as gate
    if args.max_load is not None:
        gate.QUIET["limit"] = args.max_load  # The gate's isolated benchmark runner.
    receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    ceilings = perf_ratchet.load_ceilings(json.loads(perf_ratchet.CEILINGS_PATH.read_text(encoding="utf-8")))
    platform = receipt["ratchet"]["platform"]
    problem = baseline_problem(ceilings, platform, gate.sha256(args.baseline), perf_ratchet.binary_uuid(args.baseline),
                               args.baseline_approval)
    if problem:
        print("perf_abab:", problem, file=sys.stderr)
        return 2
    noisy = {row["operation"]: row["tolerance"] for row in receipt.get("rows", []) if row["status"] == "noisy"}
    if not noisy:
        print("perf_abab: the receipt has no noisy operations")
        return 0
    result = gate.abab_for(Path(args.out), Path(args.baseline), Path(args.candidate), receipt, noisy)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if all(entry["verdict"] == "pass" for entry in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
