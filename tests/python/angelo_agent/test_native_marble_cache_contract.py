# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Direct cache-probe oracles, and the aggregate reuse ratio they replaced as a gate.

The 5:1 reused-per-full-draw ratio is reported, not asserted (maintainer, 2026-10-26, for every candidate from then
on): it also counted unrequested and cold draws, so a lower decorative cadence failed it with no waste. The direct
checks gate reuse and refresh per frame instead; the absolute CPU, pixel, orbit, clock and shutdown limits remain.
"""

import math

FIELDS = ("full", "reused", "clock", "full_seconds", "reused_seconds")


def reuse_report(stats, clock):
    """The reused frames per non-clock full draw of the helper-adjusted counters, for the report only."""
    full = stats["full"] - clock
    return {"reused": stats["reused"], "full_excluding_clock": full,
            "reused_per_full": round(stats["reused"] / max(1, full), 3),
            "meets_former_5_to_1": stats["reused"] >= 5 * max(1, full), "gate": False}


def delta(before, after):
    values = {key: after[key] - before[key] for key in FIELDS}
    assert all(math.isfinite(value) and value >= 0 for value in values.values()), "Counters went backwards"
    return values


def adjusted(raw, helper):
    """Remove only the explicitly accounted helper interval; never reset product counters."""
    result = delta(helper, raw)
    assert all(math.isclose(result[key] + helper[key], raw[key], rel_tol=0, abs_tol=1e-12)
               for key in FIELDS), "Helper accounting does not reconcile"
    return result


def frame(record):
    assert not record.get("errors"), record.get("errors")
    assert record["reuse_calls"] == record["post_calls"] == 1, "Missing, nested or unpaired draw"
    assert record["reason"] != "unobserved", "Unobserved decision"
    counts = record["delta"]
    assert record["eligible"] == record["reused"], "Eligible miss or invalid reuse"
    assert counts["reused"] == int(record["reused"]), "Reuse counter drift"
    expected_full = int(not record["reused"] and record["live_wait"] and not record["bypass"])
    assert counts["full"] == expected_full, "Full counter drift"
    assert record["chat_draws"] == int(not record["reused"]), "Unpaired full draw"
    assert counts["clock"] == int(record["reason"] == "clock"), "Clock counter drift"
    if record["live_wait"] and not record["bypass"]:
        assert record["entry_valid"], "No valid retained entry"


def refreshed(record, followups, reason, changed=None):
    frame(record)
    assert record["requested"] and not record["reused"] and record["reason"] == reason, "Wrong invalidation cause"
    assert record["delta"]["full"] == 1 and record["delta"]["reused"] == 0
    if changed:
        assert changed in record["changed"], "The intended key field did not invalidate"
    assert len(followups) == 2, "Follow-up requests are fixed"
    for item in followups:
        frame(item)
        assert item["requested"]
    assert any(item["eligible"] and item["reused"] for item in followups), "No stable reuse after this refresh"


def pixel_difference(first, direct):
    """Native RGB bytes, same key/dimensions and existing moving-mark mask + four pixels only."""
    import numpy as np
    assert first["reused"] and first["delta"]["reused"] == 1 and first["delta"]["full"] == 0
    assert not direct["reused"] and direct["bypass"] and direct["chat_draws"] == 1
    for item in (first, direct):
        assert type(item["sequence"]) is int and item["sequence"] > 0, "Missing capture sequence"
        assert item["capture_sequence"] == item["sequence"], "Capture belongs to another draw"
        assert isinstance(item["content_key"], tuple) and item["content_key"], "Missing static key"
    assert first["content_key"] == direct["content_key"], "Static content/clock changed between captures"
    assert first["size"] == direct["size"], "Capture dimensions changed"
    width, height = first["size"]
    a, b = (np.asarray(item["pixels"]) for item in (first, direct))
    assert width > 0 and height > 0 and a.shape == b.shape == (height, width, 3)
    assert np.isfinite(a).all() and np.isfinite(b).all()
    assert (a >= 0).all() and (a <= 255).all() and (b >= 0).all() and (b <= 255).all()
    mask = np.ones((height, width), dtype=bool)
    for x, y, w, h in first["holes"]:
        left, bottom = int(x) - 4, int(y) - 4
        mask[max(0, bottom):max(0, bottom + int(h) + 8), max(0, left):max(0, left + int(w) + 8)] = False
    count = int(mask.sum())
    assert count > 0 and np.ptp(a[mask], axis=0).max() > 0 and np.ptp(b[mask], axis=0).max() > 0, "Blank or fully masked capture"
    changed = (np.abs(a.astype(np.float32) - b.astype(np.float32)).max(axis=2) > 2.5) & mask
    share = float(changed.sum()) / count
    assert share <= .002, "Actual reused pixels differ from direct drawing"
    return {"changed_share": share, "unmasked_pixels": count}
