# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The repeat-capture ratchet statistic: the three-write sum plus exact bytes.

Standard library only, so host tests pin it without Blender.

The contract (maintainer, 2026-10-27, prospectively for every build, both
sides of every comparison and every document size):

* Each repeat capture is bracketed by two reference writes of its own byte
  count (``mirror_io``), each the kernel I/O a plain capture does: the bytes
  written in 1 MiB chunks to a new file in the snapshot folder, then one
  ``fsync`` (``checkpoint_publication`` fsyncs each output; the read-back
  hash runs only for sculpt runtimes, which these documents do not have).
* A pair is valid only when the capture and both references are valid
  samples (``perf_counters.rejection``: no page-ins, no swap-ins or
  swap-outs during the sample).
* A pair's value is its **three-write sum**: reference before + capture +
  reference after. The ratcheted operation
  ``snapshot/<doc>_repeat_capture_three_write_sum`` is the median sum of at
  least ``MIN_PAIRS`` valid pairs (``statistic``); a run takes up to
  ``MAX_ATTEMPTS_FACTOR`` times the wanted repeats to collect them and
  otherwise reports no value (the gate then fails closed).
* The paired net (capture minus the mean of its two references,
  ``paired_net``) and the gross capture stay in every report as data and are
  never compared.
* The bytes written are ratcheted separately and exactly (``exact_bytes``).

Why the sum: kernel work (page-cache copies, reclaim) moves between adjacent
writes, so inside a pair the capture's cost and its references' are
anti-correlated (correlation -0.41 to -0.95 at 500 MB in the 2026-10-27 gate
ABAB). Subtracting the references amplifies that movement instead of cancelling
it: one binary's per-pair nets spanned 9-124 million instructions and gave a
false regression, while the three writes' total, which keeps every instruction
wherever the kernel put it, agreed within 0.6% across all four runs.
A change to the capture shows in the sum one for one; the references only add
the same-size write cost on both sides of every comparison.

History: the first statistic (the fewest instructions of any repeat minus the
fewest of any reference write) subtracted two minima taken at different
moments: on 2026-10-21 the reference write alone ranged 125-152 million
instructions on identical code while gross capture stayed at 200.1-200.9
million, and it reported a regression that did not exist. It was replaced on
2026-10-21 by the paired net, which gated until 2026-10-27 (reason above).
A literal null sink, excluding the kernel copy by construction, would need a
writer hook inside the native capture, which is safety code.
"""

import os
from pathlib import Path
import statistics
import uuid

MIN_PAIRS = 7
MAX_ATTEMPTS_FACTOR = 3
WRITE_CHUNK = 1 << 20  # Bytes per write call, as the first reference used.


def mirror_io(directory, size, measure):
    """``measure(function)`` around a capture's kernel I/O for ``size`` bytes; the file is removed afterwards.

    ``measure`` is ``perf_counters.measure``; its metrics are returned.
    """
    block = os.urandom(4096) * (WRITE_CHUNK // 4096)
    path = Path(directory) / ("write-reference-%s.bin" % uuid.uuid4().hex)

    def write():
        with open(path, "wb", buffering=0) as handle:
            remaining = size
            while remaining > 0:
                remaining -= handle.write(block[:min(remaining, len(block))])
            os.fsync(handle.fileno())
    try:
        return measure(write)[1]
    finally:
        path.unlink(missing_ok=True)


def pair(capture, before, after, rejection):
    """One repeat: its capture metrics and both bracketing reference metrics."""
    reasons = sorted(set(("capture " + reason) for reason in rejection(capture))
                     | set(("reference " + reason) for reason in rejection(before) + rejection(after)))
    counts = (capture.get("instructions"), before.get("instructions"), after.get("instructions"))
    if None in counts:
        reasons = sorted(set(reasons) | {"instructions unavailable"})
    entry = {"capture": counts[0], "reference_before": counts[1], "reference_after": counts[2], "reasons": reasons,
             "valid": not reasons}
    if None not in counts:
        entry["net"] = counts[0] - (counts[1] + counts[2]) / 2
        entry["three_write_sum"] = sum(counts)
    return entry


def three_write_sum(entry):
    """A pair's reference before + capture + reference after (None when a count is missing)."""
    counts = (entry.get("capture"), entry.get("reference_before"), entry.get("reference_after"))
    return None if None in counts else sum(counts)


def _net(entry):
    counts = (entry.get("capture"), entry.get("reference_before"), entry.get("reference_after"))
    return None if None in counts else counts[0] - (counts[1] + counts[2]) / 2


def _median_over_valid(pairs, value, label, minimum):
    values = [value(entry) for entry in pairs if entry["valid"]]
    values = [item for item in values if item is not None]
    reasons = {}
    for entry in pairs:
        for reason in entry["reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    result = {"statistic": label, "pairs": len(pairs), "valid": len(values), "rejected": len(pairs) - len(values),
              "reasons": reasons, "minimum": minimum, "median": None, "spread": None}
    if len(values) >= minimum:
        median = statistics.median(values)
        result["median"] = int(round(median))
        result["min"], result["max"] = int(round(min(values))), int(round(max(values)))
        result["spread"] = round((max(values) - min(values)) / median, 5) if median > 0 else None
    return result


def statistic(pairs, minimum=MIN_PAIRS):
    """The gated statistic over ``pair`` entries: median three-write sum; ``median`` is None below ``minimum`` valid pairs.

    Reads only each pair's capture and reference counts and its validity, so
    reports written before 2026-10-27 are scored the same way.
    """
    return _median_over_valid(pairs, three_write_sum, "median of (reference before + capture + reference after) "
                              "over valid pairs", minimum)


def paired_net(pairs, minimum=MIN_PAIRS):
    """The 2026-10-21 statistic (median of capture - mean of its references), reported only since 2026-10-27."""
    return _median_over_valid(pairs, _net, "median of (capture - mean of its two bracketing references) over valid "
                              "pairs (reported, not gated, since 2026-10-27)", minimum)


def enough(pairs, wanted):
    """Whether a run has collected ``wanted`` valid pairs."""
    return sum(1 for entry in pairs if entry["valid"]) >= wanted


def attempts_allowed(wanted):
    return wanted * MAX_ATTEMPTS_FACTOR


def exact_bytes(sizes):
    """The repeat captures' size: exact when every repeat wrote the same number of bytes."""
    if not sizes:
        return {"bytes": None, "exact": False}
    return {"bytes": max(sizes), "exact": len(set(sizes)) == 1, "distinct": sorted(set(sizes))}


def legacy_net(captures, references):
    """The first statistic (min capture - min reference), kept in reports for comparison only."""
    captured = [value for value in captures if value is not None]
    written = [value for value in references if value is not None]
    if not captured or not written or len(captured) != len(captures) or len(written) != len(references):
        return None
    return {"median": min(captured) - min(written), "statistic": "min capture - min reference (retired 2026-10-21)"}
