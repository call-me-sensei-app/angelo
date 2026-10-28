# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker's hosted chats tell the lead unasked: ``{"event": {...}}`` frames on the worker's channel (C3).

Every event a hosted ``Controller`` emits goes to the lead as ``{"session", "open_id", "kind", "data", "t"}``, in
emit order (``open_id``: the chat's current ``chat.open``, C9), with its data reduced to plain JSON values (anything
else becomes ``None``) and every string passed through the chat's ``redact``. Besides those:

* ``journal`` ``{"session", "seq", "path", "records"}`` after each write of the chat's file: ``seq`` counts the
  writes, ``records`` the records since the file's last rewrite (0: the lead reads it whole again);
* ``pending`` ``{"session", "calls": [...], "seq"}`` whenever the chat's pending calls change, each call as its card
  needs it (``pending_calls``); ``seq`` is their version (``pending_version``), which ``chat.calls`` answers carry
  too, so the lead never replaces newer cards with an older account (audit2 #5);
* ``news`` ``{"progress": {"done", "steps"}}`` for each progress report, and ``signs`` ``{"accepted", "last_ago",
  "heard_ago"}`` for the lead's live row while a turn runs;
* ``turn_facts`` ``{"rounds", "total_ms", "ended_at"}`` when they change: the model requests of the chat's current
  turn (``ledger_events.record_round``) and, once its request ended, its length and wall-clock end, which the lead's
  ``worker_facts`` reads as it reads a lead-hosted chat's (``apply_turn_facts``);
* ``part_saved`` ``{"path", "revision", "layout", "part"}`` after the worker saved its part, ``layout`` only when it
  changed, ``part`` that version's ``{"objects", "identity"}`` or None (``worker_part_counts.saved_facts``), and
  ``part_save_failed`` ``{"error"}`` when that save failed (tried again a second later).

Frames leave on one sender thread, so a provider thread that emits never waits on the pipe, and frames keep the order
they were emitted in. A frame too large for the channel is replaced by a ``dropped`` event naming its kind; the
journal is the chat's record either way. Standard library only and no ``bpy``.
"""

import itertools
import math
import queue
import threading
import time

FLUSH_SECONDS = 2.0
TURN_FACTS = "turn_facts"
OPENS = {}   # session -> the ``open_id`` of its current ``chat.open`` here (``WorkerChatHost._open``).
_VERSIONS = itertools.count(1)   # Pending-call versions, rising across every chat of this worker.


def plain(value, redact=str, depth=0):
    """``value`` as plain JSON values, strings redacted; anything else (a callback, a result object) is ``None``."""
    if depth > 64:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {redact(str(key)): plain(item, redact, depth + 1) for key, item in list(value.items())}
    if isinstance(value, (list, tuple)):
        return [plain(item, redact, depth + 1) for item in list(value)]
    return None


class Outbox:
    """The worker's unsolicited frames, sent in order by one daemon thread."""

    def __init__(self, endpoint):
        from .worker_channel import RESPONSE_LIMIT
        self.endpoint, self.limit = endpoint, RESPONSE_LIMIT
        self._queue = queue.Queue()
        self._done = threading.Condition()
        self._queued = self._sent = 0
        self.broken = False
        threading.Thread(target=self._run, daemon=True, name="AngeloWorkerEvents").start()

    def put(self, event):
        with self._done:
            if self.broken:
                return
            self._queued += 1
        self._queue.put(event)

    def flush(self, timeout=FLUSH_SECONDS):
        """Wait until every event put so far was sent (or the channel broke), at most ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        with self._done:
            target = self._queued
            while self._sent < target and not self.broken:
                left = deadline - time.monotonic()
                if left <= 0 or not self._done.wait(left):
                    return False
        return True

    def close(self):
        self.flush()
        self._queue.put(None)

    def _run(self):
        from . import external_cli_protocol as protocol
        while True:
            event = self._queue.get()
            if event is None:
                return
            try:
                try:
                    self.endpoint.send({"event": event})
                except protocol.FrameError:   # Too large for one frame: say what was left out.
                    self.endpoint.send({"event": {"session": event.get("session"), "kind": "dropped", "t": event.get("t"),
                                                  "data": {"kind": event.get("kind"), "reason": "too_large"}}})
            except (OSError, ValueError):   # The lead is gone: nothing more can reach it.
                with self._done:
                    self.broken = True
                    self._done.notify_all()
                return
            with self._done:
                self._sent += 1
                self._done.notify_all()


def open_id(request):
    """The lead's ``open_id`` for a ``chat.open`` (``worker_mirror_opens.announce``), or None."""
    value = request.get("open_id")
    return value if type(value) is str and 0 < len(value) <= 64 and value.isalnum() else None


def event(session, kind, data, redact=str):
    frame = {"session": session, "kind": kind, "data": plain(data, redact), "t": time.time()}
    if OPENS.get(session) is not None:
        frame["open_id"] = OPENS[session]   # C9: the lead orders a chat's requests within one open only.
    return frame


def pending_calls(controller, consent):
    """The chat's pending calls as the lead's cards are drawn from them (``worker_mirror_events.pending_calls``): id,
    name, a summary of the call's own data (the lead builds the card's words in its own language), the approval
    digest (``consent.pending_digests``: only a call awaiting the person has one, and only that one ``chat.approve``
    covers), whether a person must answer, and the seconds left before it expires (clocks differ across processes)."""
    digests = consent.pending_digests(controller)
    now, calls = time.monotonic(), []
    for call in list(controller.pending.values()):
        summary = {"arguments": plain(call.arguments, controller.redact),
                   "program_arguments": plain(getattr(call, "program_arguments", None), controller.redact),
                   "approval": bool(call.approval), "executing": bool(call.executing),
                   "usage_label": plain(call.usage_label, controller.redact) or "",
                   "recovery_review": bool(getattr(call, "recovery_review", False))}
        summary.update(review_fields(call))
        calls.append({"id": call.id, "name": call.name, "summary": summary, "digest": digests.get(call.id),
                      "needs_person": call.id in digests, "expires": max(0.0, call.expires - now)})
    return calls


def calls_answer(host, chat, call_id):
    """``chat.calls``: the chat's pending calls as its cards are drawn (``pending_calls``) and ``call_id``'s outcome as
    its file records it (``phase`` and ``error_code``, or None while it has none); its cards also go again at the next
    pump. Read only: nothing runs."""
    host.resend_cards(chat)
    outcome = None
    if isinstance(call_id, str) and call_id:
        for key, record in list((chat.state.get("results") or {}).items()):
            if (key == call_id or key.endswith("/" + call_id)) and isinstance(record, dict):
                result = record.get("result") if isinstance(record.get("result"), dict) else {}
                outcome = {"phase": record.get("phase"), "error_code": result.get("error_code")}
    return {"ok": True, "calls": pending_calls(chat, host.consent), "outcome": outcome, "open_id": OPENS.get(
        chat.state.get("id")), "pending_seq": pending_version(getattr(host, "_versions", {}), chat)}


def pending_version(versions, chat):
    """The version of ``chat``'s pending calls (``versions``: its host's ``session -> (fingerprint, version)``): it
    rises only when they change, so cards sent again keep theirs."""
    session, mark = chat.state.get("id"), fingerprint(chat)
    last = versions.get(session)
    if last is None or last[0] != mark:
        last = versions[session] = (mark, next(_VERSIONS))
    return last[1]


def review_fields(call):
    """What the lead's ``approval_review.prepare_review`` needs beyond the arguments to draw the card the worker's
    own review would (typed programs, recipes, recovery reviews): the call's scene binding (``precondition``) and
    recovery evidence, exact host values (``worker_values.encode``, decoded by the lead). A value that cannot be
    encoded is left out and named in ``unencoded``: the lead then draws no review it cannot make whole."""
    from . import worker_values
    fields, missing = {}, []
    for name in ("precondition", "recovery_evidence"):
        try:
            fields[name] = worker_values.encode(getattr(call, name, None))
        except (TypeError, ValueError, RecursionError):
            missing.append(name)
    if missing:
        fields["unencoded"] = missing
    return fields


def turn_facts(controller):
    """The chat's turn facts (module docstring), or None before its first turn."""
    timings = getattr(controller, "timings", None) or {}
    rounds = getattr(controller, "_turn_requests", None)
    if type(rounds) is not int and not timings:
        return None
    facts = {"rounds": rounds if type(rounds) is int and rounds >= 0 else 0}
    started, total = timings.get("started"), timings.get("total_ms")
    if isinstance(started, (int, float)) and isinstance(total, (int, float)) and total >= 0:
        # The worker's monotonic clock is its own: the end goes as wall time, which both processes share.
        facts.update(total_ms=float(total), ended_at=time.time() - (time.monotonic() - (started + total / 1000.0)))
    return facts


def apply_turn_facts(mirror, data):
    """The lead: a hosted chat's ``turn_facts`` onto its mirror as its own ``_turn_requests`` and ``timings`` (on the
    lead's monotonic clock), which ``worker_facts`` reads for ``rounds`` and ``built_in_s``. Malformed facts are
    ignored."""
    if type(data) is not dict:
        return
    rounds = data.get("rounds")
    if type(rounds) is int and rounds >= 0:
        mirror._turn_requests = rounds
    total, ended_at = data.get("total_ms"), data.get("ended_at")
    if (type(total) in (int, float) and type(ended_at) in (int, float) and math.isfinite(total)
            and math.isfinite(ended_at) and total >= 0):
        ended = time.monotonic() - max(0.0, time.time() - ended_at)
        mirror.timings = {"started": ended - total / 1000.0, "total_ms": float(total)}


def signs(controller, now):
    """The live row's signs of a busy chat (``turn_activity.signs`` of its provider) as ages in seconds, rounded to
    tenths so an unchanged sign sends nothing; None between turns or without a provider that keeps them."""
    provider = getattr(controller, "provider", None)
    if not controller.busy or provider is None:
        return None
    from .turn_activity import signs as provider_signs
    try:
        _watched, found = provider_signs(provider)
    except Exception:  # noqa: BLE001 - a provider mid-teardown: no signs this pump.
        return None
    if found is None:
        return None
    accepted, last, heard = found
    age = (lambda stamp: round(max(0.0, now - stamp), 1) if isinstance(stamp, (int, float)) else None)
    return {"accepted": accepted if accepted in (True, False) else None, "last_ago": age(last), "heard_ago": age(heard)}


def running(controller):
    """A busy chat's request so far for the lead's live meter (``worker_mirror_events.running``): its finished provider
    turns' usage, the turn in progress (``record_running_usage``), its route and served models. None between turns."""
    metrics = getattr(controller, "_request_metrics", None)
    if not controller.busy or metrics is None or metrics.finished:
        return None
    from .provider_usage import USAGE_KEYS
    with metrics.lock:
        partial = dict(getattr(metrics.path, "running", None) or {})
        return {"usage": {key: value for key, value in metrics.usage.items() if key in USAGE_KEYS},
                "running": {key: partial[key] for key in USAGE_KEYS if key in partial},
                "turns": metrics.counts.get("provider_turn_requests", 0), "route": dict(metrics.route),
                "served_models": list(metrics.served_models)}


def fingerprint(controller):
    """What changes the pending event: the calls and each one's approval state (cheap; no digest)."""
    return tuple((call.id, call.approval, call.executing, call.authorized, call.automatic, call.approved,
                  bool(call.revoked), id(call.precondition), id(call.arguments))
                 for call in list(controller.pending.values()))
