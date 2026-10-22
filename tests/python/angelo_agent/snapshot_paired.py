# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The repeat-capture ratchet statistic: paired net instructions plus exact bytes.

Standard library only, so host tests pin it without Blender.

Why paired: a dense capture retires most of its instructions in the kernel,
copying its bytes into the page cache, and that cost per byte moves with the
machine's free memory and swap. The first statistic (the fewest instructions
of any repeat minus the fewest of any reference write) subtracted two minima
taken at different moments: on 2026-10-21 the reference write alone ranged
125-152 million instructions on identical code while gross capture stayed at
200.1-200.9 million, and the net reported a regression that did not exist.

The statistic, set before any measurement with it (2026-10-21):

* Each repeat capture is bracketed by two reference writes of its own byte
  count (``mirror_io``), each the kernel I/O a plain capture does: the bytes
  written in 1 MiB chunks to a new file in the snapshot folder, then one
  ``fsync`` (``checkpoint_publication`` fsyncs each output; the read-back
  hash runs only for sculpt runtimes, which these documents do not have).
* A repeat's net is its capture minus the mean of its two references, so
  both sides of the subtraction see the same moment's kernel cost per byte.
* A pair is valid only when the capture and both references are valid
  samples (``perf_counters.rejection``: no page-ins, no swap-ins or
  swap-outs during the sample).
* The operation's value is the median net of at least ``MIN_PAIRS`` valid
  pairs; a run takes up to ``MAX_ATTEMPTS_FACTOR`` times the wanted repeats
  to collect them and otherwise reports no value (the gate then fails closed).
* The bytes written are ratcheted separately and exactly (``exact_bytes``).

The net is the work Angelo and Blender do themselves, with the kernel's copy
cancelled by measurement rather than excluded by construction: a literal null
sink would need a writer hook inside the native capture, which is safety
code. Gross counts stay in every report as a cross-check.
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
    return entry


def statistic(pairs, minimum=MIN_PAIRS):
    """The pre-registered statistic over ``pair`` entries; ``median`` is None below ``minimum`` valid pairs."""
    nets = [entry["net"] for entry in pairs if entry["valid"]]
    reasons = {}
    for entry in pairs:
        for reason in entry["reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    result = {"statistic": "median of (capture - mean of its two bracketing references) over valid pairs",
              "pairs": len(pairs), "valid": len(nets), "rejected": len(pairs) - len(nets), "reasons": reasons,
              "minimum": minimum, "median": None, "spread": None}
    if len(nets) >= minimum:
        median = statistics.median(nets)
        result["median"] = int(round(median))
        result["min"], result["max"] = int(round(min(nets))), int(round(max(nets)))
        result["spread"] = round((max(nets) - min(nets)) / median, 5) if median > 0 else None
    return result


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
