# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live-agent journey statistics: per-journey latency samples and their p50/p75.

Dependency-free; ``eval_live_tasks`` re-exports these for the launcher and reports.
"""

import math


def percentile(values, fraction):
    """Linear-interpolated percentile, the same definition as perf_counters/telemetry."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


JOURNEYS = {
    "cold_first_message": "total_ms of a completed request whose runtime started cold (first message of a trial)",
    "warm_follow_up": "total_ms of a completed request on an already running runtime",
    "prompt_to_first_verified_change": "first_verified_result_ms of a completed edit whose host checks passed",
    "staged_preview_accept": "harness time from the human accept call to the committed result, host checks passed",
}


def _phase(record, name):
    value = (record.get("receipt") or {}).get("phases", {}).get(name)
    return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def journey_samples(records):
    """Per-journey latency samples (ms) from per-task records; failures are excluded."""
    samples = {name: [] for name in JOURNEYS}
    for record in records:
        receipt = record.get("receipt") or {}
        completed = receipt.get("status") == "completed"
        total = _phase(record, "total_ms")
        if completed and total is not None:
            samples["cold_first_message" if receipt.get("runtime_start") == "cold" else "warm_follow_up"].append(total)
        verified = _phase(record, "first_verified_result_ms")
        if record.get("kind") == "edit" and completed and record.get("host_check_passed") is True and verified is not None:
            samples["prompt_to_first_verified_change"].append(verified)
        accept = record.get("accept") or {}
        if (record.get("kind") == "preview" and record.get("host_check_passed") is True
                and type(accept.get("accept_to_commit_ms")) in {int, float}):
            samples["staged_preview_accept"].append(accept["accept_to_commit_ms"])
    return samples


def summarize_journeys(records):
    """p50/p75 per journey. Small samples are reported as such, never padded."""
    output = {}
    for name, values in journey_samples(records).items():
        entry = {"definition": JOURNEYS[name], "samples": len(values), "values_ms": [round(v, 3) for v in values]}
        if values:
            entry.update(p50_ms=round(percentile(values, .5), 3), p75_ms=round(percentile(values, .75), 3))
        output[name] = entry
    return output
