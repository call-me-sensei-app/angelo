# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_job_start``'s result as the host knows the job when the model reads it, not as it was when it started.

1.0.8 dogfood (B-009): a still render ended about a second after ``angelo_job_start`` returned ``running``; the chat
showed the host's "Job j1 (RENDER_STILL) done" row, and then the model's reply said the render "is running … when it
finishes". The start result was the only thing the model had heard, and nothing told it more before it wrote: a job's
end reaches the model in the next request's jobs block (``context_ledger.TurnBlocks.jobs``), and the model makes no
further call to a job it has just started (scene changes are refused until it ends).

So the start result is held, as Studio generations and flow runs are held until they end (``StudioPending``): while the
job runs, at most ``hold_seconds`` and never past its end, then it is the job's current entry
(``job_store.context_entry``): its end, outputs and seconds when it ended in time, its progress otherwise. A job its own
estimate puts past ``MAX_HOLD`` returns at once, as before, so a long render waits no longer than it did; a job with
no estimate is held ``DEFAULT_HOLD``. Holding reads only job records (the job runs on as before, on its own timer), so
Stop or the turn's end ends only this wait, never the job: ``angelo_job_cancel`` stops a job.
"""

import time

from .job_store import ACTIVE, context_entry
from .studio_agent import StudioPending

DEFAULT_HOLD = 3.0   # Seconds for a job with no estimate: most quick renders and exports end within it.
MAX_HOLD = 10.0      # Never longer; a job estimated past it returns at once.
ESTIMATE_FACTOR = 1.5   # Over its estimate (render_device's), so a job a little slower than estimated still ends in it.
EXPIRY_GRACE = 30.0     # The pending call outlives the hold by this much (Controller: call.expires).
KEPT = ("job", "kind", "cancel", "output", "requested", "if_taken", "device", "device_why", "gpu_available",
        "estimate_s", "estimate_from")


def hold_seconds(record):
    """How long ``record``'s start result may wait for the job to end: 0 for a job estimated past ``MAX_HOLD``."""
    estimate = (record.get("detail") or {}).get("estimate_s")
    if type(estimate) in (int, float) and estimate >= 0:
        wanted = estimate * ESTIMATE_FACTOR + 1.0
        return 0.0 if wanted > MAX_HOLD else wanted
    return DEFAULT_HOLD


def scene_now(record, active):
    """What an ended job's result says about scene changes, read from the manager now (``active``: its
    ``JobManager.active``): the next queued job starts the moment this one ends, and scene changes wait for it too
    (``job_host.guard``). Nothing is said when there is no manager to read."""
    if not callable(active):
        return ""
    other = active()
    if other is not None and other.get("id") != record.get("id"):
        return " Job %s (%s) is running now, so scene changes still wait until it ends." % (other["id"], other["kind"])
    return " Scene changes are allowed again."


def current(result, record, active=None):
    """The start result with the job's state now: its entry replaces the status fields of the moment it started."""
    entry = context_entry(record)
    merged = {key: value for key, value in result.items() if key in KEPT}
    merged.update(entry)
    if record["status"] in ACTIVE:
        merged["note"] = result.get("note", "")
        return merged
    merged.pop("cancel", None)
    merged.pop("estimate_s", None)
    merged["note"] = ("It ended before this result returned: this is its outcome." if record["status"] == "done" else
                      "It ended before this result returned, without finishing: this is its outcome.")
    merged["note"] += scene_now(record, active)
    return merged


def held(store, record, result, *, clock=time.monotonic, active=None):
    """``result`` itself for a job that has ended or is held no time; otherwise a pending result (``StudioPending``)
    that answers once the job ends or its hold is over, whichever comes first, with ``current`` (``active``: the
    manager's, for what the answer says about scene changes)."""
    seconds = hold_seconds(record)
    if record["status"] not in ACTIVE or seconds <= 0 or not callable(getattr(store, "get", None)):
        return result   # Ended, held no time, or no store to read it from (a stand-in manager).
    identity, deadline = record["id"], clock() + seconds

    def poll():
        now = store.get(identity) or record
        if now["status"] in ACTIVE and clock() < deadline:
            return None
        return current(result, now, active)

    pending = StudioPending(poll, lambda: None)   # Ends only this wait: the job runs on (angelo_job_cancel stops it).
    pending.timeout = seconds + EXPIRY_GRACE
    pending.started = result   # The job as it started (its ID, output, cancel): ``started`` reads it.
    # The chat's live row, with labels it already has (chat_titles.LIVE): no new interface text.
    pending.status_text = ("Waiting for a render" if record.get("kind", "").startswith("RENDER")
                           else "Staging native program")
    pending.activity_note = "Waits a few seconds for a quick job to end, so its result says how it ended."
    return pending


def started(result):
    """``angelo_job_start``'s result as the job started, held or not: its ID and where its output lands, for a caller
    that follows the job itself instead of waiting for the held outcome (the GUI suites that drive jobs step by step)."""
    return getattr(result, "started", result)
