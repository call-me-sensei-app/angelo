# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An Approve the worker never answered is settled from the worker's own account, never assumed (design A′, C6).

The person's Approve on a hosted chat's card shows Approving at once (``worker_mirror_opens.approval_sent``) and the
worker's events settle it: the card leaves when the call runs, or comes back when the worker refuses it. An Approve
with no answer yet may be running (an approved script runs inside its request, up to ``ANSWER_SECONDS``) or may never
have reached the worker (its frame lost). Here the lead asks the worker (``chat.calls``, read only, bounded) for the
chat's pending calls and that call's recorded outcome, and settles the card from the answer:

* the call no longer pending there: the worker settled it (it ran, was refused, or expired; the chat file records
  how): the card leaves, as the worker's own cards say;
* the call still waiting for the person there: the Approve never reached it, so nothing ran: the card is approvable
  again and the chat says so (``LOST``, with its support code);
* the call still running there, or no answer while the worker lives: asked again later, each wait twice the last,
  up to ``ANSWER_SECONDS``;
* the worker gone: nothing is asked again; its end settles the chat as every crash does (``_recovered``).

An answer settles only the call it was asked about, never the chat's other cards, and only when it is at least as new
as the cards the mirror shows (its ``pending_seq`` against the version of the last ``pending`` event applied, and its
open the mirror's): an older answer is asked again, so a delayed answer never erases newer cards or brings back
settled ones (audit2 #5).

When to ask: the worker takes its requests one at a time, in the order their frames arrive, on its main thread
(``WorkerChatHost.next_request``), so a ``chat.calls`` written after the Approve's frame is answered only once the
Approve was taken, or never came. It is asked ``ASK_SECONDS`` after the Approve went out (its frame is written at
once, on its own thread: the bound only has to cover that write, and a worker running the approved edit answers the
question after it, never "still waiting"), and at once when the Approve's own request returned unanswered
(``unanswered``: its frame was written before it returned). A worker that still waits for the person after that
never got the Approve, so the card is back within seconds, not after ``ANSWER_SECONDS``.

Main thread (``step`` from ``MirrorController._pump_events``) except the question itself, which goes on a thread of
its own. Standard library only, no bpy.
"""

import collections
import threading
import time

from .ui_text import n_, notice

LOST = n_("Your approval didn't reach this chat's worker, so nothing ran. Approve it again (support code {code}).")
ASK_SECONDS = 5.0   # The first question after an Approve went out (module docstring): one chat op's bound.
_hooks = {"spawn": None, "now": time.monotonic}


def bind(**hooks):
    """``spawn(fn)`` runs a question (a daemon thread by default) and ``now()`` is the clock; unknown names refuse."""
    unknown = set(hooks) - set(_hooks)
    if unknown:
        raise ValueError("Unknown approval settle seams: %s" % ", ".join(sorted(unknown)))
    _hooks.update(hooks)


def reset():
    _hooks.update(spawn=None, now=time.monotonic)


def _seconds():
    from .worker_mirror_ops import ANSWER_SECONDS
    return ANSWER_SECONDS


def sent(mirror, call_id):
    """The person's Approve of ``call_id`` went to the worker: it is settled by ``step`` if nothing else settles it."""
    mirror.__dict__.setdefault("_approving", {})[call_id] = {"due": _hooks["now"]() + ASK_SECONDS,
                                                             "wait": ASK_SECONDS, "asking": False}


def unanswered(mirror, call_id):
    """The Approve's own request returned with no answer (``unknown``): asked at the next ``step``."""
    record = mirror.__dict__.get("_approving", {}).get(call_id)
    if record is not None:
        record["due"] = _hooks["now"]()


def _later(record):
    """Still unknown: asked again after twice the last wait, up to ``ANSWER_SECONDS``."""
    record["wait"] = min(record.get("wait", ASK_SECONDS) * 2, _seconds())
    record["due"] = _hooks["now"]() + record["wait"]


def step(mirror):
    """Main thread: apply answers that came back, then ask about each Approve still unsettled past its bound. True
    when the card changed."""
    approving = mirror.__dict__.get("_approving")
    if not approving:
        return False
    answers = mirror.__dict__.setdefault("_settle_answers", collections.deque())
    changed = False
    while answers:
        call_id, answer = answers.popleft()
        changed = _apply(mirror, approving, call_id, answer) or changed
    now = _hooks["now"]()
    for call_id, record in list(approving.items()):
        if not _approving(mirror, call_id):
            del approving[call_id]   # Settled by the worker's own events (or a Stop): nothing to ask.
        elif not record["asking"] and now >= record["due"]:
            record["asking"] = True
            _spawn(lambda call_id=call_id: answers.append((call_id, _ask(mirror, call_id))))
    return changed


def _approving(mirror, call_id):
    call = mirror.pending.get(call_id)
    return call is not None and getattr(call, "approved", False) is True


def _spawn(function):
    spawn = _hooks["spawn"]
    if spawn is not None:
        spawn(function)
    else:
        threading.Thread(target=function, daemon=True, name="AngeloApprovalSettle").start()


def _ask(mirror, call_id):
    """Any thread: the worker's ``chat.calls`` answer, never raising (``worker_chat_wiring.send_op``)."""
    send = mirror.send_op
    if not callable(send):
        return {"error": "no worker", "gone": True}
    try:
        answer = send(mirror.state["id"], "chat.calls", call_id=call_id)
    except Exception as error:   # noqa: BLE001 - a transport that raised answered nothing.
        return {"error": str(error) or type(error).__name__}
    return answer if isinstance(answer, dict) else {"error": "no answer"}


def _worker_alive(mirror):
    from . import worker_chat_lifecycle
    worker = worker_chat_lifecycle.hosted(mirror.state.get("id"))
    return worker is not None and not worker_chat_lifecycle._process_ended(worker)


def _apply(mirror, approving, call_id, answer):
    record = approving.get(call_id)
    if record is None:
        return False
    record["asking"] = False
    if not _approving(mirror, call_id):
        del approving[call_id]
        return False
    calls, seq, last = answer.get("calls"), answer.get("pending_seq"), mirror.__dict__.get("pending_seq")
    stale = type(last) is int and (type(seq) is not int or seq < last) or answer.get("open_id") not in (
        None, getattr(mirror, "open_id", None))   # The mirror already shows a newer account, or another open's.
    if answer.get("ok") is not True or type(calls) is not list or stale:
        if answer.get("gone") is True or not _worker_alive(mirror):
            del approving[call_id]   # Its worker's end settles the chat (the crash path), never an assumption here.
        else:
            _later(record)   # Still unknown (or older than the cards shown): asked again later.
        return False
    entry = next((c for c in calls if type(c) is dict and c.get("id") == call_id), None)
    summary = entry.get("summary") if entry is not None and type(entry.get("summary")) is dict else {}
    if entry is not None and (summary.get("executing") is True or entry.get("needs_person") is not True):
        _later(record)   # Running there (or admitted without the person): its end tells.
        return False
    del approving[call_id]
    lost = entry is not None   # Still waiting for the person there: the Approve never reached it, nothing ran.
    if not lost:   # Settled there: this card leaves. Only the queried call changes (audit2 #5): the answer is one
        mirror.pending.pop(call_id, None)   # call's account, and the chat's newer events own every other card.
    else:
        card = mirror.pending[call_id]
        card.approved, card.approval = False, summary.get("approval", True) is True
        if entry.get("digest") == getattr(card, "approval_digest", None):   # Else its next event redraws it.
            from . import worker_consent_lead
            worker_consent_lead.note_call(mirror.state["id"], entry)
    if lost:
        from .worker_mirror_ops import add_row
        from .worker_reasons import APPROVAL_LOST
        add_row(mirror, "error", notice(LOST, code=APPROVAL_LOST))
    mirror.version += 1
    return True
