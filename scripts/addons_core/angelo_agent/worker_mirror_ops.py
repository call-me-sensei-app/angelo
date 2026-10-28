# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker-hosted chat's mirror sends its actions without waiting (design A′, C2 and C9): no window frame ever waits
on a worker, however wedged it is.

* **Request ids.** Each asynchronous chat op (Send, an option change) carries a fresh ``request_id`` and goes out on
  its chat's own lane (``Lane``: one sender at a time per chat, so a Send and a later option change keep their order).
  The lane's thread only sends and keeps the answer; ``reconcile`` (the UI timer, ``worker_consent_lead.tick``)
  applies each answer once, on Blender's main thread, by its request id. A late answer, or one for a request already
  settled (withdrawn by Stop, or its worker ended), changes nothing: never applied twice.
* **The person's order.** Every op also carries ``lead_seq`` (``order``: one counter for every chat, taken on the
  main thread when the person acts), and so does a Stop, which goes out on a thread of its own because it must never
  wait behind a blocked op. Until a Send's frame is about to be written (its ``before`` hook, a consent
  acknowledgement, included) a Stop withdraws it here; after that, the worker refuses a Send ordered before a Stop it
  already received (``worker_chat_arrival``), so a Stop is never overtaken by the Send it was meant to stop.
* **Send.** The mirror's turn shows as sending at once. The worker's answer confirms it; a refusal, or a worker that
  ended first, returns the message where the person types, with its attachments, and says why with its support code.
  An answer that never came while the worker still runs leaves the turn sending: the worker may still take it, and
  its turn's own events then show it (a timed-out Send is never shown as failed and then run anyway). A Stop before
  the Send left its lane withdraws it: nothing was sent, so it comes back at once.
* **Error rows** the lead adds to a mirror (a refusal, an open that failed) are the lead's own: the chat file is the
  worker's, so they are kept here and put back after every reload of that file (``keep_rows``).

Standard library only, no bpy. The seams (``bind``) are the lane's thread and the clock, so tests run lanes inline or
on real threads.
"""

import collections
import itertools
import threading
import time
import uuid

from .ui_text import n_, notice

ANSWER_SECONDS = 300.0   # How long an asynchronous op's answer may take (an approved script runs in a request).
LEAD_ROWS = 32           # Error rows kept per mirror across reloads (the newest).
SENDING = n_("Sending")
SEND_REFUSED = n_("Your message was not sent, so it is back in the message box ({why}; support code {code}).")
OPTIONS_REFUSED = n_("This chat kept its settings: its worker did not apply the new ones ({why}; support code {code}).")
WORKER_GONE = n_("This chat's worker ended before it answered.")
NO_ANSWER = n_("This chat's worker did not answer.")
NOT_SENT = n_("Your message wasn't sent. Send it again when you're ready.")   # A window's chat's own words.

_hooks = {"spawn": None}
_answers = collections.deque()   # (Request, answer) from the lanes' threads; drained on the main thread.
_orders = itertools.count(time.time_ns() // 1000)   # Only forward; a later run of Angelo starts above this one.


def order():
    """The next ``lead_seq``: the person's order of a chat's ops (module docstring). Any thread."""
    return next(_orders)


def bind(**hooks):
    """``spawn(fn)`` runs a lane (a daemon thread by default; a test may run it inline). Unknown names refuse."""
    unknown = set(hooks) - set(_hooks)
    if unknown:
        raise ValueError("Unknown mirror op seams: %s" % ", ".join(sorted(unknown)))
    _hooks.update(hooks)


def reset():
    _answers.clear()
    _hooks.update(spawn=None)


class Request:
    """One asynchronous chat op: ``queued`` → ``preparing`` (its ``before`` hook) → ``sent`` → ``answered``, or
    ``withdrawn`` before it was sent."""

    def __init__(self, mirror, op, kind, fields):
        self.id, self.mirror, self.op, self.kind, self.fields = uuid.uuid4().hex, mirror, op, kind, fields
        self.lead_seq = order()
        self.send, self.state, self.settled, self.sent_at = mirror.send_op, "queued", False, None
        self.before = None   # A callable run on the lane before the send; False refuses it (``ConsentNotBack``).
        self.stopped = False   # The person's Stop came after it left its lane (``withdraw``).


class Lane:
    """One chat's sender: its requests go out in order, one at a time, off the main thread."""

    def __init__(self):
        self.lock, self.queue, self.running = threading.Lock(), collections.deque(), False

    def submit(self, request):
        with self.lock:
            self.queue.append(request)
            start, self.running = not self.running, True
        if start:
            spawn = _hooks["spawn"]
            if spawn is not None:
                spawn(self._run)
            else:
                threading.Thread(target=self._run, daemon=True, name="AngeloMirrorOps").start()

    def withdraw(self, request):
        """True when ``request`` had not left its lane: it never will."""
        with self.lock:
            if request.state not in ("queued", "preparing"):
                return False
            request.state = "withdrawn"
            return True

    def _run(self):
        while True:
            with self.lock:
                if not self.queue:
                    self.running = False
                    return
                request = self.queue.popleft()
                if request.state != "queued":
                    continue
                request.state = "preparing"
            answer = _call(request, self)
            if answer is not None:
                _answers.append((request, answer))

    def leaving(self, request):
        """The request's frame is written next: from here a Stop no longer withdraws it. False when one already
        did (it is never sent)."""
        with self.lock:
            if request.state == "withdrawn":
                return False
            request.state, request.sent_at = "sent", time.monotonic()
            return True


def _call(request, lane):
    """The request's answer, never raising: ``{"ok": True, ...}``, or ``{"error", "timed_out"?, "gone"?}``; None
    for a request a Stop withdrew before it was sent."""
    try:
        if request.before is not None and request.before() is False:
            from .worker_consent_lead import NEEDS_APPROVAL
            return {"error": NEEDS_APPROVAL, "consent": True}
        if not lane.leaving(request):
            return None
        if not callable(request.send):
            return {"error": WORKER_GONE, "gone": True}
        answer = request.send(request.mirror.state["id"], request.op, request_id=request.id,
                              lead_seq=request.lead_seq, **request.fields)
    except Exception as error:   # noqa: BLE001 - a transport that raised answered nothing.
        return {"error": str(error) or type(error).__name__}
    return answer if isinstance(answer, dict) else {"error": NO_ANSWER, "timed_out": True}


def submit(mirror, op, kind, before=None, **fields):
    """Queue ``op`` for ``mirror``'s worker on its lane; the ``Request`` (its ``id`` goes with it)."""
    request = Request(mirror, op, kind, fields)
    request.before = before
    lane = mirror.__dict__.get("_op_lane")
    if lane is None:
        lane = mirror.__dict__["_op_lane"] = Lane()
    lane.submit(request)
    return request


# Main thread ----------------------------------------------------------------------------------------------------------

def reconcile():
    """Apply every answer that came back, each once (module docstring); True when anything shown changed."""
    changed = False
    while True:
        try:
            request, answer = _answers.popleft()
        except IndexError:
            return changed
        if request.settled:
            continue   # Already settled here (withdrawn, or its worker ended): a late answer changes nothing.
        if answer.get("ok") is True:
            request.settled = True
            changed = _confirmed(request) or changed
        elif answer.get("timed_out") is True and _worker_alive(request):
            continue   # Unknown: the worker may still apply it; its own events (or its end) settle it.
        else:
            request.settled = True
            changed = _refused(request, answer) or changed
        from . import worker_mirror_pins   # A chat.options taken or refused: the lead's own record of it.
        changed = worker_mirror_pins.settled(request, answer) or changed


def _worker_alive(request):
    from . import worker_chat_lifecycle
    worker = worker_chat_lifecycle.hosted(request.mirror.state.get("id"))
    return worker is not None and not worker_chat_lifecycle._process_ended(worker)


def _confirmed(request):
    mirror = request.mirror
    if request.kind != "send" or getattr(mirror, "_sending", None) is not request:
        return False
    mirror._sending = None
    if mirror.status == SENDING:
        mirror.status = "Thinking"
    mirror.version += 1
    return True


def took(mirror):
    """A frame of the Send's own turn came (its person's row): the worker took it, so a late answer, or the worker's
    end before that answer, never puts it back where the person types (it ran)."""
    request = getattr(mirror, "_sending", None)
    if request is not None:
        request.settled = True
        _confirmed(request)


def why(answer, op="request"):
    """``(reason, support code)`` for a refused answer: a plain, translatable reason (``worker_reasons``) chosen by the
    answer's kind or the worker's refusal code (``refusal``), and its ``WRK-*`` support code; the worker's own words
    and its refusal code go to the log only, never a chat id or an op name to the person."""
    from .worker_reasons import explain
    return explain(answer, refusal(answer), log=op)


def refusal(answer):
    """The refusal code (``refusal_codes``) a refused answer carries: the worker's own record's, or the lead's
    ``error_code`` for a send that raised (``worker_reasons.answer_of``); UNKNOWN when it names none."""
    from .refusal_codes import CODES, refusal_code
    answer = answer if isinstance(answer, dict) else {}
    error = answer.get("error")
    if isinstance(error, dict):
        from .worker_values import decode_error
        return refusal_code(decode_error(error))
    if answer.get("gone") is True:
        return "OWNERSHIP"
    if answer.get("consent") is True:
        return "CANCELLED"
    return answer["error_code"] if answer.get("error_code") in CODES else "UNKNOWN"


def _refused(request, answer):
    mirror = request.mirror
    text, code = why(answer, request.op)
    if request.kind == "send":
        if getattr(mirror, "_sending", None) is not request:
            return False
        # The person's Stop reached the worker first, which refused the Send for it (``worker_chat_arrival``): it
        # never ran, so it ends as a Send withdrawn before it left does (interrupted, not sent), with no error row.
        stopped = request.stopped and isinstance(answer.get("error"), dict) and refusal(answer) == "CANCELLED"
        return_unsent(mirror, request, stopped=stopped)
        if not stopped:
            add_row(mirror, "error", notice(SEND_REFUSED, why=text, code=code))
        return True
    if request.kind == "options":
        add_row(mirror, "error", notice(OPTIONS_REFUSED, why=text, code=code))
        return True
    return False   # Logged by ``why``.


def return_unsent(mirror, request=None, stopped=False):
    """The person's message the worker never ran goes back where they type, with its attachments (as the window's
    chat does for a message never sent), and the mirror's turn ends here: failed (a refusal says why in its own
    row), or, ``stopped`` by the person before it left, interrupted and not sent, as a window's chat ends a turn
    stopped before its message went (``Controller``: ``finished`` with ``interrupted`` and ``not_sent``)."""
    request = request or getattr(mirror, "_sending", None)
    mirror._sending = None
    if request is not None:
        request.settled = True
    mine = getattr(mirror, "_host_request", None) is None and bool(getattr(mirror, "_turn_message", None))
    if mine:
        mirror.unsent_draft = mirror._turn_message
        if not mirror.attachments:
            mirror.attachments = [dict(reference) for reference in mirror.turn_attachments]
    mirror.busy, mirror.cancelled = False, False
    mirror.status = "Interrupted" if stopped else "Failed"
    with mirror.lock:   # As a window's chat records a message never sent (``session_rail.turn_end``).
        mirror.turn_outcome = ("interrupted" if stopped else "failed", stopped, True,
                               len(mirror.state.get("messages") or []))
    if stopped and mine:
        add_row(mirror, "system", notice(NOT_SENT))
    mirror.version += 1


def withdraw(mirror):
    """A Stop of a Send that never left its lane (queued, or in its ``before`` hook): nothing reached the worker, so
    it comes back now. True if so."""
    request = getattr(mirror, "_sending", None)
    lane = mirror.__dict__.get("_op_lane")
    if request is None or lane is None or not lane.withdraw(request):
        if request is not None:
            request.stopped = True   # Sent: the worker refuses it if this Stop reaches it first (``lead_seq``).
        return False
    return_unsent(mirror, request, stopped=True)
    return True


def worker_ended(mirror):
    """Its worker ended (``MirrorController._recovered``): a Send it never answered is settled now, never twice."""
    request = getattr(mirror, "_sending", None)
    if request is not None:
        request.settled = True
        mirror._sending = None


# The lead's own rows --------------------------------------------------------------------------------------------------

def add_row(mirror, role, body):
    """Show ``body`` as a ``role`` row the lead adds (display only: the file is the worker's), and keep it."""
    row = dict(body, role=role)
    anchor = getattr(mirror, "_file_rows", None)
    with mirror.lock:
        if anchor is None:
            anchor = len(mirror.state.get("messages") or [])
        kept = mirror.__dict__.setdefault("_lead_rows", [])
        kept.append((anchor, row))
        del kept[:-LEAD_ROWS]
        shown = dict(row)
        mirror.state.setdefault("messages", []).append(shown)
        mirror.__dict__.setdefault("_lead_shown", []).append(shown)
    mirror.version += 1


def reload(mirror, records=None):
    """``worker_mirror_events.reload`` with the lead's rows taken out first and put back after, so the mirror's
    indexes into later rows are moved once per row, never once more per read (the reads are incremental, C3)."""
    from . import worker_mirror_events
    drop_rows(mirror)
    try:
        return worker_mirror_events.reload(mirror, records)
    finally:
        keep_rows(mirror)


def drop_rows(mirror):
    """The lead's rows shown now leave the rows, and the indexes into later rows move back with them."""
    with mirror.lock:
        rows = mirror.state.get("messages") or []
        for shown in mirror.__dict__.pop("_lead_shown", None) or []:
            position = next((i for i, row in enumerate(rows) if row is shown), None)
            if position is not None:
                del rows[position]
                _shift(mirror, position + 1, -1)


def keep_rows(mirror):
    """After a reload of the chat's file: the lead's rows go back where they were, each after the file rows it
    followed; the mirror's indexes into later rows move with them."""
    with mirror.lock:
        rows = mirror.state.setdefault("messages", [])
        mirror._file_rows = len(rows)
        mirror.__dict__.pop("_lead_shown", None)   # A state put in whole (a recovery) holds none of them.
        kept = mirror.__dict__.get("_lead_rows") or []
        for offset, (anchor, row) in enumerate(sorted(kept, key=lambda item: item[0])):
            position = min(anchor, mirror._file_rows) + offset
            shown = dict(row)
            rows.insert(position, shown)
            mirror.__dict__.setdefault("_lead_shown", []).append(shown)
            _shift(mirror, position, 1)
    from . import worker_mirror_pins   # The lead's route and options go back over the file's too.
    worker_mirror_pins.restore(mirror)


def _shift(mirror, position, step):
    """The mirror's indexes at or after ``position`` move by ``step`` (a lead row put in or taken out)."""
    for table in (mirror._items, mirror._thinking_messages, mirror._activities):
        for key, index in list(table.items()):
            if type(index) is int and index >= position:
                table[key] = index + step
    index = getattr(mirror, "_request_message_index", None)
    if type(index) is int and index >= position:
        mirror._request_message_index = index + step
