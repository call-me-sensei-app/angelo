# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Failed worker park saves: retain the live part, report the block, retry with bounded backoff.

Only failure episodes use these clocks/notices. ``worker_queue._held`` owns the records;
its ordinary off-main-thread save path performs retries too, forcing a fresh save after a
failure even if Blender now calls the document clean. A current save receipt, not an old
file or clean no-op, releases the hold. Unbinding discards the record and any pending save.
No model request is started here. Main thread, except the save itself in worker_queue.
"""

import math
import os
import time

from .ui_text import n_, notice

FIRST_RETRY = 5.0
MAX_RETRY = 60.0
FAILED = n_("Worker {worker} ({name}) could not save its part: {error}. Angelo is keeping it open and will retry saving automatically. Queued work may wait; nothing was merged.")
RECOVERED = n_("Worker {worker} ({name}) saved its part after a failed save. Queued work can continue.")


def _records():
    from . import worker_queue
    return worker_queue._held


def _notice(session, ui, template, error=""):
    from . import worker_sessions
    part = worker_sessions._parts.get(session)
    if part is None:
        return
    row = {"role": "system", **notice(template, worker=part[0], name=part[1], error=error)}
    for identity in dict.fromkeys((session, worker_sessions._lead_of(session))):
        chat = ui._controllers.get(identity)
        emit = getattr(chat, "emit", None)
        if callable(emit):
            try:
                emit("message", dict(row))
            except Exception as problem:   # A closing chat must not end the scheduler or lose the held worker.
                print("Angelo: worker save notice:", type(problem).__name__)


def failed(session, executor, error, ui):
    """Keep one failure episode, updating its cause/backoff without repeated notices."""
    from .provider_errors import sanitize
    records = _records()
    previous = records.get(session)
    first = previous is None or previous["executor"] is not executor
    attempts = 1 if first else previous["attempts"] + 1
    detail = error.get("message", error) if isinstance(error, dict) else error
    detail = sanitize(str(detail), limit=300)
    records[session] = {"executor": executor, "attempts": attempts, "error": detail,
                        "retry_at": time.monotonic() + min(MAX_RETRY, FIRST_RETRY * 2 ** min(attempts - 1, 4))}
    if first:
        _notice(session, ui, FAILED, detail)


def due(session):
    """No clock read on the successful path; an invalidated lead never starts a retry."""
    record = _records().get(session)
    if record is None:
        return True
    from . import worker_queue, worker_sessions
    return worker_queue.current(worker_sessions._lead_of(session)) and time.monotonic() >= record["retry_at"]


def validate_retry(record, answer):
    """A forced save must name this part/document and a newer save revision than at dispatch."""
    expected = record.get("retry")
    if expected is None:
        return None
    value, revision = answer.get("value"), answer.get("revision")
    document = answer.get("document")
    executor = record["executor"]
    current = executor.document_key[-1]
    latest = (executor.part_layout or {}).get("revision", 0)
    same_path = isinstance(value, str) and os.path.normcase(os.path.abspath(value)) == expected["path"]
    if (not same_path or type(revision) is not int or revision <= expected["revision"] or revision < latest
            or not isinstance(document, (tuple, list)) or tuple(document) != expected["document"]
            or not isinstance(current, (tuple, list)) or tuple(current) != expected["document"]):
        return "The retry did not confirm a fresh save of this worker's current part"
    return None


def turn_binding(chat):
    """A save belongs to one request, even if a newer request already ended before its reply is handled."""
    row = next((row for row in reversed(chat.state.get("messages") or ()) if row.get("role") == "user"), None)
    return getattr(chat, "_request_generation", None), id(row)


def current_turn(record, chat):
    return record["turn"] == turn_binding(chat)


def retry_binding(executor):
    """Bind recovery to the same live document and last known save before dispatch."""
    document = executor.document_key[-1]
    return {"path": os.path.normcase(os.path.abspath(executor.worker.part)),
            "document": tuple(document) if isinstance(document, (tuple, list)) else (),
            "revision": (executor.part_layout or {}).get("revision", 0)}


def recovered(session, ui):
    if _records().pop(session, None) is not None:
        _notice(session, ui, RECOVERED)


def blocker(session):
    record = _records().get(session)
    if record is None:
        return None
    return "its current part has not been saved after a save failure (%s); Angelo is retrying the save" % record["error"]


def facts(session):
    record = _records().get(session)
    if record is None:
        return {}
    return {"save_blocked": {"error": record["error"], "attempts": record["attempts"],
                              "retry_in_s": max(0, math.ceil(record["retry_at"] - time.monotonic())),
                              "worker_kept_open": True}}


def waiting(lead):
    if not _records():
        return False
    from . import worker_sessions
    return any(session in _records() for session in worker_sessions._started.get(lead.state["id"], ()))
