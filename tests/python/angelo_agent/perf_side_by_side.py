# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Side-by-side judgement of instruction counts that drift with the machine (maintainer ruling 2026-11-12).

Asked how the sculpt-mesh perf limit should be settled, the maintainer answered "Side-by-side from now on":
e1d956ec1ef's ``native_reads/sculpt_mesh`` read 69,956 against a 66,073 ceiling while the stamped 1.0.10 binary read
69,959 the same hour. Such an operation is judged against the stamped baseline binary (A) run in the same slot, in
perf_abab's interleaved A, B, A, B, not by its absolute ceiling alone. The release checks apply the same rule over a
gate's output.

* Judged: an instruction-count operation that ``perf_ratchet.compare`` found ``regressed`` against its ceiling (its
  spread small, so not ``noisy``) and that has ABAB values. It is in the side-by-side class when it is a native read
  (``CLASS_PREFIXES``), or when the stamped baseline itself no longer reproduces its ceiling: the mean of A exceeds
  the ceiling by more than the operation's tolerance.
* It passes (``side_by_side``) when perf_abab's pre-registered rule passes and mean(B) / mean(A) is within the
  tolerance. Outside the class, with a missing value, or with more work side by side, it stays ``regressed``.
* ``perf_ratchet.stamp`` sets a passing operation's ceiling to the measured value (the same-session number) and
  records the ABAB. A B/A regression fails the gate, so its ceiling is never raised.
"""

import statistics

PASSED = "side_by_side"
# Operations that drift with the machine by nature (maintainer, 2026-11-12): the native reads.
CLASS_PREFIXES = ("native_reads/",)


def candidates(rows):
    """``{operation: tolerance}``: instruction-count operations that failed on their ceiling alone."""
    return {row["operation"]: row["tolerance"] for row in rows
            if row["status"] == "regressed" and row.get("unit", "instructions") == "instructions"}


def judge(row, values):
    """The side-by-side verdict of one ``regressed`` row from its ABAB values (``{"A": [..], "B": [..]}``)."""
    import perf_abab   # Here: perf_abab imports perf_ratchet, which imports this module.
    a, b = list((values or {}).get("A") or []), list((values or {}).get("B") or [])
    tolerance, ceiling = row["tolerance"], row["ceiling"]
    if len(a) != 2 or len(b) != 2 or any(not isinstance(value, int) or value <= 0 for value in a + b):
        return {"verdict": "regressed", "why": "the ABAB has no value on every run", "A": a, "B": b}
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    ratio = round(mean_b / mean_a, 5)
    detail = {"A": a, "B": b, "ratio": ratio}
    native = row["operation"].startswith(CLASS_PREFIXES)
    if not native and mean_a <= ceiling * (1 + tolerance):
        return dict(detail, verdict="regressed", why="the stamped baseline reproduces its ceiling (A mean %.0f), so "
                    "the candidate's extra work is real" % mean_a)
    reason = ("a native read" if native else "the stamped baseline no longer reproduces its ceiling (A mean %.0f)"
              % mean_a)
    if perf_abab.verdict(a, b, tolerance) != "pass" or ratio > 1 + tolerance:
        return dict(detail, verdict="regressed", why="%s, and side by side the candidate does more work (B/A %s, "
                    "tolerance %s)" % (reason, ratio, tolerance))
    return dict(detail, verdict=PASSED, why="%s; side by side B/A %s is within %s" % (reason, ratio, tolerance))


def apply(row, values):
    """Judge a ``regressed`` row in place when it has ABAB values."""
    if row.get("status") != "regressed" or row.get("unit", "instructions") != "instructions" or values is None:
        return row
    decided = judge(row, values)
    row["side_by_side"] = {key: decided[key] for key in ("A", "B", "ratio", "why") if key in decided}
    if decided["verdict"] == PASSED:
        row["ceiling_status"], row["status"] = row["status"], PASSED
    return row
