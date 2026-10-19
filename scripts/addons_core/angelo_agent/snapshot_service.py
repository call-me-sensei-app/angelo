# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender-host glue for deduplicated snapshots and automatic retention.

Main thread only. ``start`` runs one SnapshotWorker per profile (from the add-on's
register), ``after_capture`` hands each completed capture to it, and ``pump``
(from the UI timer) turns worker events into maintenance requests and short
chat notices. Capture itself is unchanged and never waits for the worker: when
the service is not running (scripts that import ui without registering it),
snapshots simply stay full copies.

Automatic retention needs protection references that only the main thread
knows: every chat's current request snapshot. They are collected here, together
with the timeline position at that moment, each time maintenance is requested.
"""

import queue
from pathlib import Path

from .checkpoint_filesystem import _private_directory, _read_json
from .checkpoint_policy import CheckpointPolicyStore
from .checkpoint_settings_ui import protected_snapshot_refs
from .checkpoint_timeline import NAME as TIMELINE_NAME
from .display_text import Text
from .snapshot_worker import SnapshotWorker
from .ui_text import n_, notice

EVENTS_PER_PUMP = 16
_workers = {}
_shown = set()


def _key(root):
    # Same normalization as CheckpointStore.root, so captures find their worker.
    return str(Path(root).absolute().resolve())


def start(root):
    key = _key(root)
    if key not in _workers:
        worker = SnapshotWorker(key)
        _workers[key] = worker
        worker.start()
        worker.submit_recovery()
    return _workers[key]


def stop():
    for worker in tuple(_workers.values()):
        worker.stop()
    _workers.clear()
    _shown.clear()


def running(root):
    return _key(root) in _workers


def after_capture(controller, record):
    """Tell the chat about a replaced seal key, then queue ingestion; never raises into the capture path."""
    from .checkpoint_seal import take_notices
    for template in take_notices(controller.checkpoints.root):
        controller.emit("message", {"role": "system", "notice": True, **notice(template)})
    worker = _workers.get(_key(controller.checkpoints.root))
    if worker is None:
        return False
    try:
        worker.submit_ingest(controller.checkpoints.session_id, record["id"], notify=controller.state["id"])
    except Exception as error:  # The full copy remains a valid snapshot.
        print("Angelo snapshot ingestion was not queued:", error)
        return False
    return True


def _watermark(root):
    """Latest timeline event when protection was collected; 0 protects all ordered points."""
    try:
        with _private_directory(root) as folder:
            clock = _read_json(folder, TIMELINE_NAME)
        return clock["sequence"] if type(clock.get("sequence")) is int else 0
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return 0


def request_maintenance(ui, notify=None):
    """Collect fresh protection on the main thread, then maintain off-thread."""
    refs = protected_snapshot_refs(ui._controllers.values())
    for root, worker in _workers.items():
        policies = CheckpointPolicyStore(root)
        policy = policies.load()
        worker.submit_maintenance(policy, refs, _watermark(root), notify, paused=bool(policies.error))


def _size(value):
    for unit, scale in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if value >= scale:
            return "%.1f %s" % (value / scale, unit)
    return "%d bytes" % value


def _limits(summary):
    policy, parts = summary["policy"], []
    if policy["max_bytes"]:
        parts.append(_size(summary["remaining_bytes"]) + " of " + _size(policy["max_bytes"]))
    if policy["max_count"]:
        parts.append("%d of %d snapshots" % (summary.get("remaining_count", 0), policy["max_count"]))
    if policy["max_age_days"]:
        parts.append("older than %d days" % policy["max_age_days"])
    return ", ".join(parts)


_FAILED = {"ingest": n_("Snapshot storage could not finish deduplicating a snapshot: {error}. Existing restore points stay available."),
           "maintain": n_("Snapshot storage could not finish its cleanup: {error}. Existing restore points stay available.")}
# Whole sentences keyed by (exactly one restore point, those captured before the latest git commit went first).
_REMOVED = {
    (True, False): n_("Snapshot limit applied: removed {count} oldest restore point ({size}). Their Restore links now show "
                      "unavailable; limits are in Snapshot Settings."),
    (True, True): n_("Snapshot limit applied: removed {count} oldest restore point ({size}), first those captured before the "
                     "project's latest git commit. Their Restore links now show unavailable; limits are in Snapshot Settings."),
    (False, False): n_("Snapshot limit applied: removed {count} oldest restore points ({size}). Their Restore links now show "
                       "unavailable; limits are in Snapshot Settings."),
    (False, True): n_("Snapshot limit applied: removed {count} oldest restore points ({size}), first those captured before the "
                      "project's latest git commit. Their Restore links now show unavailable; limits are in Snapshot Settings."),
}
_PROTECTED = {True: n_("Snapshots remain over the limit ({limits}) because {count} restore point is protected: active "
                       "requests, safety snapshots or required file-restore chains."),
              False: n_("Snapshots remain over the limit ({limits}) because {count} restore points are protected: active "
                        "requests, safety snapshots or required file-restore chains.")}


def notice_text(event):
    """Concise chat notice for a worker event, or None when nothing is worth saying.

    One notice is a ``Text`` (its English sentence plus the template ``_post`` stores for display);
    two notices for one event are joined into a plain English string.
    """
    if event["kind"] == "failed":
        return Text(_FAILED.get(event["task"], n_("Snapshot storage could not finish a task: {error}. Existing restore "
                                                  "points stay available.")), error=event["error"])
    if event["kind"] != "maintained":
        return None
    summary = event["summary"]
    if summary.get("paused"):
        return Text(n_("Snapshot settings could not be read, so automatic snapshot removal is paused. "
                       "Save Snapshot Settings again to resume it."))
    if summary.get("blocked"):
        return Text(n_("Automatic snapshot removal was skipped because snapshot history needs review. "
                       "Review Snapshot Retention shows the problem; nothing was deleted."))
    lines = []
    if summary["removed"]:
        count = len(summary["removed"])
        lines.append(Text(_REMOVED[count == 1, bool(summary.get("preferred"))], count=count,
                          size=_size(summary.get("released_bytes", 0))))
    if not summary["budget_met"]:
        protected = len(summary["protected"])
        lines.append(Text(_PROTECTED[protected == 1], limits=_limits(summary), count=protected))
    return lines[0] if len(lines) == 1 else " ".join(lines) or None


def _signature(event):
    if event["kind"] == "failed":
        return ("failed", event["task"], event["error"])
    summary = event["summary"]
    if summary.get("paused") or summary.get("blocked"):
        return ("paused" if summary.get("paused") else "blocked",)
    if summary["removed"]:
        return None  # Every removal is reported.
    return ("unmet", tuple(sorted(item["ref"] for item in summary["protected"])))


def _post(ui, event, text):
    signature = _signature(event)
    if signature is not None:
        if signature in _shown:
            return
        _shown.add(signature)
    controller = ui._controllers.get(event.get("notify")) or getattr(ui, "_controller", None)
    if controller is not None:
        controller.emit("message", {"role": "system", **(notice(text.template, **text.fields) if isinstance(text, Text)
                                                       else {"text": text})})


def pump(ui):
    """Main-thread UI tick: apply at most a few worker events, and say when the worker replaced a seal key."""
    from .checkpoint_seal import take_notices
    controller = getattr(ui, "_controller", None)
    for root in tuple(_workers) if controller is not None else ():  # Keyed like CheckpointStore.root.
        for template in take_notices(root):
            controller.emit("message", {"role": "system", "notice": True, **notice(template)})
    for worker in tuple(_workers.values()):
        for _ in range(EVENTS_PER_PUMP):
            try:
                event = worker.events.get_nowait()
            except queue.Empty:
                break
            if event["kind"] in {"ingested", "recovered"}:
                if event["kind"] == "recovered" or not event["stats"].get("skipped"):
                    request_maintenance(ui, event.get("notify"))
                continue
            if event["kind"] == "maintained":
                _forget_resolved(event["summary"])
            text = notice_text(event)
            if text:
                _post(ui, event, text)


def _forget_resolved(summary):
    """A resolved condition is reported again if it ever recurs."""
    active = {"paused": summary.get("paused"), "blocked": summary.get("blocked"),
              "unmet": not summary.get("budget_met", True)}
    for signature in tuple(_shown):
        if signature[0] in active and not active[signature[0]]:
            _shown.discard(signature)
