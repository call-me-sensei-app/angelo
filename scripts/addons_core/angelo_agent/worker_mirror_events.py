# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker-hosted chat's mirror (``worker_mirror``) does with the frames its worker sends (design A′, C3, C9).

Three sources, applied on the lead's main thread in the order the worker sent them (the chat's own event queue,
``event_queue``, is the marshal: the channel's reader thread only puts):

* **journal** (``{"seq", "path"}``): the chat's file was written. The mirror reads it with the store's own reader
  (``SessionStore._read``: ``session_journal.read`` and ``apply``, then the same validation a chat load runs), so its
  state is the file's by construction. Several journal frames in one pump read the file once, at the last, and only
  the records written since the last read (``worker_mirror_journal``, from the frame's ``records``).
* **pending** (``{"calls": [{id, name, summary, digest, needs_person, expires}]}``): the calls waiting in the worker.
  Each becomes a ``ToolCall`` built from its summary alone, so the card draws what the worker will check, and
  Approve sends back the digest that was drawn (``MirrorController.approve_call``).
* **display events** (``Controller.emit`` kinds): the streamed text, thinking rows, activity rows, status, models and
  the turn's end, applied as ``Controller._pump_events`` applies them, but display only: nothing is saved, no tool is
  cancelled or finished, no provider or ledger is touched. The next journal replaces what they built; while a turn
  streams, a live row that the file has not caught up with keeps its longer text (a fragment forwarded before the
  worker's save would otherwise blink out until the next one).

Each frame carries the ``open_id`` of the ``chat.open`` it belongs to (C9): only frames of the chat's current open
are applied, and their requests are ordered within it (``worker_mirror_opens``).

Standard library only and no ``bpy``.
"""

import hashlib
import json
import time

from . import (activity_rows, event_queue, plan_progress, reply_overlay, script_overlay, ui_text, usage_meter,
               worker_mirror_cards)
from .pending_delivery import ToolCall

JOURNAL = "worker_journal"   # Internal kinds: never a ``Controller.emit`` kind, so a forwarded event cannot pose as one.
PENDING = "worker_pending"
RECOVERED = "worker_recovered"   # The lead's own: the chat's file reopened after its worker ended (C7).
REOPEN = "worker_reopen"         # The lead's own: a new ``chat.open`` of this chat starts (``worker_mirror_opens``).
CLOSED = "worker_closed"         # The lead's own: its worker answered ``chat.close`` (C2).
ANSWER = "worker_answer"         # The lead's own: a worker's answer to the person's Approve or Decline.
NOTICE = "worker_notice"         # The lead's own: a system notice its worker did not take.
OPEN_ID = "open_id"              # C9: the ``chat.open`` a frame's events belong to (the frame's, else its data's).
RECORDS = frozenset({"usage", "telemetry"})   # The lead writes them (C8): handed to the mirror's ``records`` seam.
NEWS = "news"
SIGNS = "signs"
RUNNING = "running_usage"
MODEL_KINDS = frozenset({"delta", "message_complete", "thinking", "thinking_started", "thinking_completed",
                         "activity", "script_preview"})
TURN_KINDS = MODEL_KINDS | {"status", "message"}
DEFAULT_EXPIRES = 180.0   # Seconds a card shows when the worker sent no expiry; the worker enforces its own.


def invocation_digest(name, arguments):
    """The digest ``TrustedScripts.script_authorized`` recomputes from a call's name and arguments."""
    try:
        return hashlib.sha256(json.dumps({"tool": name, "arguments": arguments}, sort_keys=True, ensure_ascii=False,
                                         allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        return ""


def frame_parts(event):
    """``(session, kind, data)`` of one ``{"event": {...}}`` body, or None when it is not one (dropped, logged).

    Fields are under ``data`` (C3); a body that carries them beside ``kind`` instead is read the same way.
    """
    if type(event) is not dict or type(event.get("session")) is not str or type(event.get("kind")) is not str:
        return None
    data = event.get("data")
    if data is None:
        data = {key: value for key, value in event.items() if key not in ("session", "kind", "data", "t", OPEN_ID)}
    if type(data) is not dict:
        return None
    if event.get(OPEN_ID) is not None:
        data = dict(data, **{OPEN_ID: event[OPEN_ID]})
    kind = event["kind"]
    if kind.startswith("worker_"):
        return None   # The internal kinds are the lead's own: a worker's frame never poses as one.
    return event["session"], {"journal": JOURNAL, "pending": PENDING}.get(kind, kind), data


def put(mirror, kind, data):
    """Any thread (the channel's reader): into the chat's own ordered queue, never blocking or dropped."""
    event_queue.put(mirror, kind, dict(data))


# Pending calls ------------------------------------------------------------------------------------------------------

def pending_calls(mirror, calls):
    """``mirror.pending`` from a worker's summaries. A call whose digest did not change keeps its object (a person's
    review of it stays); a changed digest is a new call that needs a new review. Malformed entries are left out."""
    if type(calls) is not list:
        return {}
    found, kept = {}, set()
    for entry in calls:
        if type(entry) is not dict or type(entry.get("id")) is not str or type(entry.get("name")) is not str:
            continue
        if entry.get("needs_person") is True and (type(entry.get("digest")) is not str or not entry["digest"]):
            continue   # A card with no digest could never be approved: never drawn.
        if type(entry.get("digest")) is not str:
            entry = dict(entry, digest="")   # Running or admitted without the person: shown, never approvable.
        summary = entry.get("summary") if type(entry.get("summary")) is dict else {}
        old = mirror.pending.get(entry["id"])
        if old is not None and getattr(old, "approval_digest", None) == entry["digest"] and old.name == entry["name"]:
            old.executing = summary.get("executing") is True
            old.needs_person = entry.get("needs_person") is True
            old.approval = summary.get("approval", True) is True and not old.approved   # An Approve sent is shown.
            found[old.id] = old
            kept.add(old.id)
            continue
        arguments = summary.get("arguments") if type(summary.get("arguments")) is dict else {}
        expires = entry.get("expires")
        seconds = float(expires) if type(expires) in (int, float) and expires == expires else DEFAULT_EXPIRES
        # The chat's bound document (the worker's part, ``set_document``), as a window's call carries the document it
        # was admitted in: every check that compares a call's document with its chat's (``approve_current_review``)
        # holds for a mirror's call too. A new hosting (a part switch) drops its cards (``worker_mirror_opens``).
        call = ToolCall(id=entry["id"], name=entry["name"], arguments=arguments,
                        document=getattr(mirror, "document", None),
                        expires=time.monotonic() + max(0.0, seconds),
                        approval=summary.get("approval", True) is True, executing=summary.get("executing") is True,
                        usage_label=str(summary.get("usage_label") or ""), chat_id=mirror.state.get("id", ""),
                        program_arguments=summary.get("program_arguments") if type(
                            summary.get("program_arguments")) is dict else None)
        call.invocation_digest = invocation_digest(call.name, call.arguments)
        call.approval_digest = entry["digest"]   # What Approve sends back (C6): the worker refuses any other.
        call.needs_person = entry.get("needs_person") is True
        unshown = worker_mirror_cards.prepare(call, summary)   # Its typed edit's review, prepared as on admission.
        if unshown is not None:
            from . import worker_mirror_ops
            worker_mirror_ops.add_row(mirror, "error", unshown)   # The lead's own row: kept across reloads.
        found[call.id] = call
    mirror.reviewed_calls &= kept   # A review is of one (id, digest): a changed digest needs a new one (S3).
    return found


def apply_pending(mirror, data):
    """A ``pending`` event: the cards the chat draws now and Approve's digests (``note_pending``), unless the mirror
    already shows a newer account of them (``seq``: the worker's version of its pending calls, also carried by the
    ``chat.calls`` answers ``worker_mirror_settle`` applies). The version is the current open's (``worker_mirror_opens``
    forgets it with the cards)."""
    seq, last = data.get("seq"), mirror.__dict__.get("pending_seq")
    if type(seq) is int:
        if type(last) is int and seq < last:
            return False
        mirror.pending_seq = seq
    mirror.pending = pending_calls(mirror, data.get("calls"))
    from . import worker_consent_lead
    worker_consent_lead.note_pending(mirror.state["id"], data.get("calls") or [])
    return True


# Journal ------------------------------------------------------------------------------------------------------------

RETRY_SECONDS = 0.25   # A read that failed (an I/O error, a file being replaced) is tried again this often.


def reload(mirror, records=None):
    """Replace the mirror's state with the file's, read as a chat load reads it (``worker_mirror_journal``: only what
    is new since the last read, given the journal frame's ``records``). Rows equal to the ones shown keep their
    objects; a live row the file has not caught up with keeps its longer text while the turn streams. A read that
    failed (an I/O error) changes nothing shown: the mirror reads again ``RETRY_SECONDS`` later (``retry_due``).
    Content the reader does not fully understand shows as a load shows it (unverified history)."""
    store = mirror.store._store
    store.hint = records
    state, _partial = store._read()   # The load's reader without its writes (a kept copy, stale removal).
    if getattr(store, "read_error", None) is not None:
        if mirror.reload_retry is None:
            print("Angelo: a worker chat's file could not be read; reading it again:", type(store.read_error).__name__)
        mirror.reload_retry = time.monotonic() + RETRY_SECONDS
        return False
    mirror.reload_retry = None
    old_rows = mirror.state.get("messages") or []
    rows = state.get("messages") or []
    live = set(mirror._items.values()) | set(mirror._thinking_messages.values()) if mirror.busy else set()
    for index, row in enumerate(rows):
        if index >= len(old_rows):
            break
        previous = old_rows[index]
        if previous is row or previous == row:   # The same copy (``MirrorStore._rows``): no deep compare.
            rows[index] = previous
        elif (index in live and previous.get("role") == row.get("role")
              and isinstance(previous.get("text"), str) and isinstance(row.get("text"), str)
              and len(previous["text"]) > len(row["text"]) and previous["text"].startswith(row["text"])):
            rows[index] = dict(row, text=previous["text"])
    with mirror.lock:
        mirror.state.clear()
        mirror.state.update(state)
        mirror._activities = {m.get("call_id"): i for i, m in enumerate(rows) if m.get("role") == "activity"}
        for table in (mirror._items, mirror._thinking_messages):
            for key, index in list(table.items()):
                if index >= len(rows):
                    table.pop(key, None)
    mirror.store.seal(len(rows))
    return True


def retry_due(mirror):
    """A failed read is due again (``reload``)."""
    return mirror.reload_retry is not None and time.monotonic() >= mirror.reload_retry


# Display events -----------------------------------------------------------------------------------------------------

def _pair(data):
    epoch, generation = data.get("_epoch"), data.get("_generation")
    return (epoch, generation) if type(epoch) is int and type(generation) is int else None


def admit(mirror, kind, data):
    """Whether a forwarded event is shown: never one of an older request than the newest seen. A turn event of a new
    request starts the mirror's turn (its live row, clock and overlays), as the worker's own ``_start_turn`` did."""
    from . import worker_mirror_opens
    if not worker_mirror_opens.admits(mirror, data):
        return False   # A frame of an earlier (or unannounced) open of this chat (C9).
    pair = _pair(data)
    if pair is not None:
        if mirror._worker_request is not None and pair < mirror._worker_request:
            return False
        mirror._worker_request = max(pair, mirror._worker_request or pair)
        if kind in TURN_KINDS and pair != mirror._ended_request and not mirror.busy:
            mirror.begin_turn()   # A turn the person's Send did not start here (a worker's own re-run).
    return True


def apply(mirror, kind, data):
    """One display event (module docstring). Returns True when anything shown changed."""
    now = time.monotonic()
    if kind in MODEL_KINDS:
        mirror._signs["accepted"], mirror._signs["last"] = True, now
    if kind in {"delta", "thinking"} and type(data.get("text")) is str and mirror.busy:
        usage_meter.streamed(getattr(mirror, "_request_metrics", None), len(data["text"]))   # The live count.
    if reply_overlay.update(mirror, kind, data) or script_overlay.update(mirror, kind, data):
        return True
    if kind == "message" and type(data.get("role")) is str and type(data.get("text")) is str:
        message = ui_text.attach_recipe({"role": data["role"], "text": mirror.redact(data["text"])}, data,
                                        mirror.redact)
        for flag in ("host_summary", "notice"):
            if data.get(flag) is True and data["role"] == "system":
                message[flag] = True
        with mirror.lock:
            index = len(mirror.state["messages"])
            mirror.state["messages"].append(message)
        if data["role"] == "user" and mirror.busy:
            mirror._request_message_index = index
    elif kind in {"delta", "message_complete"} and "item" in data:
        item = data["item"]
        with mirror.lock:
            if item not in mirror._items and (kind == "delta" or data.get("text")):
                mirror._items[item] = len(mirror.state["messages"])
                mirror.state["messages"].append({"role": "assistant", "text": ""})
            if item in mirror._items:
                message = mirror.state["messages"][mirror._items[item]]
                text = str(data.get("text") or "")
                if kind == "delta":
                    message["text"] = mirror.redact(str(message.get("text", "")) + text)
                elif text:
                    message["text"] = mirror.redact(text)
    elif kind in {"thinking", "thinking_started", "thinking_completed"}:
        item = data.get("item", "thinking")
        with mirror.lock:
            if item not in mirror._thinking_messages:
                mirror._thinking_messages[item] = len(mirror.state["messages"])
                mirror._thinking_times[item] = time.perf_counter()   # This process's clock (live_progress).
                mirror.state["messages"].append({"role": "thinking", "text": "", "status": "running", "duration_ms": 0})
            message = mirror.state["messages"][mirror._thinking_messages[item]]
            if kind == "thinking":
                previous = mirror.thinking.get(item, "")
                message["text"] = mirror.thinking[item] = mirror.redact(previous + str(data.get("text", "")))[-16000:]
                mirror.status = plan_progress.thinking_status(mirror.status, message["text"],
                                                              prose=str(item).startswith("claude-thinking/"))
            if kind == "thinking_completed":
                message.update(status="completed",
                               duration_ms=round((time.perf_counter() - mirror._thinking_times[item]) * 1000))
    elif kind == "status" and "text" in data:
        mirror.status = mirror.redact(data["text"])
    elif kind == "activity" and type(data.get("id")) is str:
        activity_rows.store(mirror, data)
    elif kind == "models":
        mirror.models = list(data.get("models") or [])
        mirror.models_loading, mirror.models_error = False, str(data.get("error") or "")
    elif kind == "setup_required":
        mirror.setup_required = True
    elif kind == "finished":
        if _pair(data) is not None and _pair(data) == mirror._ended_request:
            return False   # One turn, one end: the first one shown wins (a close's and its provider's own).
        finish(mirror, data)
    else:
        return False   # native_session, voice, checkpoints, delegations: the worker's own (the journal shows them).
    return True


def finish(mirror, data):
    """The worker's turn ended: the mirror's live row ends as the window's does, and its cards go (the worker ends
    every call of the turn); nothing is cancelled or saved here."""
    mirror.busy = False
    mirror.pending = {}
    if getattr(mirror, "_request_metrics", None) is not None:
        mirror._request_metrics.finished = True   # The meter reads the ledger now (the worker's forwarded usage).
    mirror._ended_request = _pair(data) or mirror._worker_request
    status = str(data.get("status") or "completed")
    with mirror.lock:
        mirror.turn_outcome = (status, bool(mirror.cancelled), data.get("not_sent") is True,
                               len(mirror.state["messages"]))
        for item, index in mirror._thinking_messages.items():
            if index < len(mirror.state["messages"]):
                message = mirror.state["messages"][index]
                if message.get("status") == "running":
                    message.update(status="completed",
                                   duration_ms=round((time.perf_counter() - mirror._thinking_times[item]) * 1000))
    if data.get("not_sent") is True and getattr(mirror, "_host_request", None) is None and mirror._turn_message:
        mirror.unsent_draft = mirror._turn_message   # Back where the person types (``drafts``), as a window's chat.
        if not mirror.attachments:   # With its attachments, as a window's chat (``Controller._pump_events``).
            mirror.attachments = [dict(reference) for reference in getattr(mirror, "turn_attachments", ())]
    mirror.thinking.clear()
    mirror.status = status.title()


def signs(mirror, data):
    """A worker's live-row signs (``turn_activity.signs`` of its provider): ages in seconds at send time."""
    now = time.monotonic()
    if data.get("accepted") in (True, False):
        mirror._signs["accepted"] = data["accepted"]
    for name in ("last", "heard"):
        age = data.get(name + "_ago")
        if type(age) in (int, float) and 0 <= age < 86400:
            mirror._signs[name] = now - age


def metrics(mirror):
    """A new turn's request metrics in the mirror: what the live meter reads for a lead turn (``chat_usage``,
    ``usage_meter.turn_tokens``), fed by the worker's ``running_usage``; never a receipt (the worker's is applied)."""
    from .provider_registry import runtime_kind
    from .telemetry import RequestMetrics
    try:
        runtime = runtime_kind(mirror.state.get("provider"))
    except ValueError:
        runtime = "unknown"
    return RequestMetrics(runtime=runtime, session_id=mirror.state.get("id"))


def running(mirror, data):
    """A worker's request so far (``worker_chat_events.running``) into the mirror's turn metrics, checked as the
    stores check their own (numbers only, plain route values); ignored between turns."""
    current = getattr(mirror, "_request_metrics", None)
    if not mirror.busy or current is None or current.finished:
        return
    from .provider_usage import USAGE_KEYS

    def numbers(value):
        value = value if type(value) is dict else {}
        return {key: value[key] for key in USAGE_KEYS if type(value.get(key)) in (int, float) and value[key] >= 0}
    route = data.get("route") if type(data.get("route")) is dict else {}
    served = data.get("served_models") if type(data.get("served_models")) is list else []
    with current.lock:
        current.usage = numbers(data.get("usage"))
        current.path.running = numbers(data.get("running")) or None
        turns = data.get("turns")
        current.counts["provider_turn_requests"] = turns if type(turns) is int and turns >= 0 else 0
        current.route = {str(key): value for key, value in route.items()
                         if type(value) in (str, int, float, bool) and len(str(value)) <= 200}
        current.served_models = [model for model in served if type(model) is str][:16]


def news(mirror, data):
    """A worker's progress report (``angelo_worker_progress``) for its tile (``parallel_sessions.note_progress``)."""
    progress = data.get("progress")
    if type(progress) is dict and type(progress.get("done")) is int:
        from . import parallel_sessions
        parallel_sessions.note_progress(mirror.state["id"], progress["done"], progress.get("steps"))
