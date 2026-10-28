# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's mirror across its opens and closes, and the answers it shows (design A′, C2, C9, C10).

One mirror outlives several hostings of its chat: a park and its relaunch, a ``switch_part`` move and a crash's
resume each ``chat.open`` it again, in a fresh worker ``Controller`` whose requests count from zero. Each step reaches
the mirror as an internal event in its own ordered queue, so it applies after every frame the earlier hosting sent:

* **open** (``REOPEN``, put by ``announce`` from ``worker_chat_wiring.prepare`` before each ``chat.open``): the
  hosting that follows is a new one, and its ``open_id`` is the lead's own: the ``chat.open`` carries it and the
  worker tags every frame of that open with it (an open the worker already hosts takes it too), so the mirror adopts
  exactly that id at the ``REOPEN`` and every other one retires there. A late frame of an earlier open (a close whose
  flush timed out, then a fast ``switch_part``) is dropped, never adopted, as is one of an open never announced.
  A ``REOPEN`` that names no id retires the current one and adopts the first id seen that is not retired; a worker
  that tags no frame is followed as one open per ``REOPEN``. The order of requests (``admit``) is compared within
  one open only. The cards and the ended request of the earlier hosting go. A turn shown as running is the new
  open's (the person's Send may come before the next pump): an earlier hosting's turn ended at its close
  (``CLOSED``) or crash (``RECOVERED``).
* **hand-back** (``CLOSED`` and ``RECOVERED``) names the open it hands back (``open_id``, read before the file is
  regained, so a relaunch's announce always comes after it). One queued behind a later open's ``REOPEN`` is that
  earlier open's and changes nothing (``superseded``): its file, cards and turn are the new open's now.
* **close** (``CLOSED``, from ``worker_chat_wiring.handed_back`` after ``chat.close`` was answered): a turn whose end
  the worker forwarded before its answer has ended already. One that is still running was cut by the close: it ends
  as after a worker's crash (``worker_journal_owner.recover``: the file is the lead's again), with a notice saying so.
  While a new open already hands the file to a worker, it ends on the lead's display only. The close names the
  mirror's turn it found (``generation``): a turn begun after it (a Send to the reopened chat before the next pump)
  is the new open's, and the close never ends it.
* **answers** (``ANSWER``): Approve and Decline change the card at once (``approval_sent``, ``declined``); a worker
  that refuses one puts the card back and says why in the chat.
* **notices** (``NOTICE``): a system notice the worker did not take. Once the chat is the lead's again (handed back,
  so no worker writes its file) it is saved through the chat's own store, as any chat's notice; otherwise it is
  shown only.

Main thread only (``MirrorController._pump_events``). Standard library only and no ``bpy``.
"""

import uuid

from .ui_text import n_, notice

CLOSED_MID_TURN = n_("This chat's worker closed it while a turn was running, so that turn stopped. "
                     "Send your message again to continue.")
APPROVE_REFUSED = n_("Nothing ran: your approval did not go through ({why}; support code {code}).")
DECLINE_REFUSED = n_("This chat's worker did not apply your Decline ({why}; support code {code}).")


def admits(mirror, data):
    """C9: whether a frame belongs to the chat's current open (module docstring)."""
    opened = data.get("open_id")
    if opened is None:
        return mirror.open_id is None and not mirror.retired_opens   # A worker that tags no frame.
    if type(opened) is not str or not opened or opened in mirror.retired_opens:
        return False
    if mirror.awaiting_open:
        mirror.awaiting_open = False
        if opened != mirror.open_id:
            _adopt(mirror, opened)
    return opened == mirror.open_id


def _adopt(mirror, opened):
    """A new open's first frame: the earlier open retires, with its order of requests and its cards."""
    if mirror.open_id is not None:
        mirror.retired_opens.add(mirror.open_id)
    mirror.open_id = opened
    mirror._worker_request = mirror._ended_request = None
    mirror.pending, mirror.pending_seq = {}, None   # Its cards and their version are the earlier open's.


def announce(mirror):
    """Before a ``chat.open`` of this chat (any thread): its new ``open_id``, announced to the mirror in order after
    every frame of the earlier opens (``REOPEN``); the open carries it to the worker (module docstring)."""
    from . import worker_mirror_events as events
    opened = mirror.announced_open = uuid.uuid4().hex
    events.put(mirror, events.REOPEN, {"open_id": opened})
    return opened


def reopen(mirror, data=None):
    """``REOPEN``: a new ``chat.open`` of this chat starts (module docstring): its announced id is the only one
    adopted from here, and every other one retires."""
    opened = (data or {}).get("open_id")
    if type(opened) is str and opened:
        _adopt(mirror, opened)
        mirror.awaiting_open = False
        return True
    if mirror.open_id is not None:   # No id announced: the first one seen that is not retired.
        mirror.retired_opens.add(mirror.open_id)
        mirror.open_id = None
    mirror.awaiting_open = True
    mirror._worker_request = mirror._ended_request = None
    mirror.pending, mirror.pending_seq = {}, None
    return True


def superseded(mirror, data):
    """A hand-back (``CLOSED``, ``RECOVERED``) of an open a later ``REOPEN`` replaced (module docstring)."""
    opened = data.get("open_id")
    return type(opened) is str and opened != mirror.open_id   # Unnamed: as before, its turn ends (fail closed).


def closed(mirror, data):
    """``CLOSED``: its worker answered ``chat.close`` (module docstring). True when anything shown changed."""
    from . import worker_journal_owner, worker_mirror_events as events
    if superseded(mirror, data) or not mirror.busy or data.get("generation") not in (None, mirror._request_generation):
        return False   # Ended, or a turn begun after the close: the new open's (module docstring).
    session = mirror.state["id"]
    directory = data.get("directory")
    if isinstance(directory, str) and not worker_journal_owner.hosted(session):
        try:
            recovered = worker_journal_owner.recover(directory, session, pending=list(mirror.pending))
        except Exception as error:  # noqa: BLE001 - the turn still ends on the lead's display.
            print("Angelo: a worker chat closed mid-turn could not be reopened:", type(error).__name__)
        else:
            mirror._recovered({"state": recovered.state, "finished": recovered.finished})
            keep_notice(mirror, notice(CLOSED_MID_TURN))
            return True
    mirror.pending = {}
    events.finish(mirror, {"status": "interrupted"})
    return True


def keep_notice(mirror, data):
    """``NOTICE``: a system notice its worker did not take, saved once the file is the lead's (module docstring)."""
    from . import worker_journal_owner
    from .tool_finalization import PERSISTENCE_ERRORS
    message = {key: value for key, value in dict(data).items() if not key.startswith("_")}
    message["role"] = "system"
    if not worker_journal_owner.hosted(mirror.state["id"]):
        store = mirror.store._store
        try:
            state = store.load()   # The worker's last writes: a store loaded before the hand-off never writes.
            with mirror.lock:
                state["messages"].append(message)
            store.save(state)
        except PERSISTENCE_ERRORS as error:
            print("Angelo: a worker chat's notice could not be saved:", type(error).__name__)
        else:
            with mirror.lock:
                mirror.state.clear()
                mirror.state.update(state)
            mirror.store.seal(len(state["messages"]))
            from .worker_mirror_ops import keep_rows
            keep_rows(mirror)   # The file read in whole: the lead's own rows go back, as after any reload.
            return True
    from .worker_mirror_ops import add_row
    add_row(mirror, "system", message)   # Shown only, and kept across the worker's reloads (the lead's own row).
    return True


def approval_sent(mirror, call_id):
    """The person's Approve went to the worker: the card gives way to the live row at once, as a window's does."""
    call = mirror.pending.get(call_id)
    if call is None:
        return
    call.approved, call.approval = True, False
    from . import worker_mirror_settle
    worker_mirror_settle.sent(mirror, call_id)   # Settled from the worker's account if no answer ever settles it.
    mirror.version += 1


def declined(mirror, call_id):
    """The person's Decline went to the worker: its card goes at once."""
    if mirror.pending.pop(call_id, None) is not None:
        mirror.version += 1


def answered(mirror, data):
    """``ANSWER``: the worker's answer to an Approve or Decline. A refusal puts an Approve's card back (the worker
    re-sends its cards with the refusal) and says why; nothing is assumed done. No answer (``unknown``: it may have
    reached the worker, which runs the edit before it answers) is not a refusal: the card stays approving, and the
    worker's events and journal settle it."""
    answer = data.get("answer")
    approve = data.get("op") == "chat.approve"
    if type(answer) is dict and answer.get("unknown") is True:
        if approve:   # Its request returned unanswered: the worker's account settles it now (``worker_mirror_settle``).
            from . import worker_mirror_settle
            worker_mirror_settle.unanswered(mirror, data.get("call_id"))
        return False
    if type(answer) is dict and answer.get("ok") is True and answer.get("error") is None:
        return False
    from .worker_mirror_ops import add_row, why
    call = mirror.pending.get(data.get("call_id"))
    if approve and call is not None and call.approved:
        call.approved, call.approval = False, True
    reason, code = why(answer if type(answer) is dict else {}, data.get("op"))   # A plain reason; raw words logged.
    add_row(mirror, "error", notice(APPROVE_REFUSED if approve else DECLINE_REFUSED, why=reason, code=code))   # Kept.
    return True


def settle(mirror):
    """Each pump: an Approve still unsettled past its bound is settled from the worker's account."""
    from . import worker_mirror_settle
    return worker_mirror_settle.step(mirror)
