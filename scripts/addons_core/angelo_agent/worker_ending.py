# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Workers whose part can never land: a lead whose document changed (another file was loaded, or its own file loaded
again: ``reloaded``; another scene of its file in Angelo's main window never ends it, break-it E2E-20 R5-M-1), and a
parked chat whose part was merged. Whatever kind of entry holds such a worker (a start launching, a queued part or
restart, a restart launching, a worker launched ahead, a bound or a parked chat), it stops or ends, and anything waiting on it hears why. The queue's tables
live in ``worker_queue``, which exports these names. No bpy.
"""

import threading

from .display_text import Text
from .ui_text import n_

# Why such a worker stopped: a ``Text`` (a ``str``), translated where a chat says why (``stop_reason``).
REPLACED = Text(n_("the lead's document changed (another file was loaded), so its workers were stopped"))
MERGED = Text(n_("its part was merged into the lead's scene, so its worker stays stopped"))
CLOSING = Text(n_("Angelo is closing"))   # A restart under way as the add-on ends.
REOPENED = Text(n_("the lead's file was opened again, so its workers were stopped"))
# The lead's own row when ``end_after_change`` could not end them all (never only a console line).
WORKERS_LEFT = n_("Angelo couldn't stop all of this chat's workers after its file changed. Stop them before you merge "
                  "their parts.")
_reloaded = set()   # Lead chat ids whose document's content changed (``reloaded``), until ``document_replaced`` ends them.
_reasons = {}       # Their reasons (``REOPENED`` unless the load replaced it with another file: ``REPLACED``).
_unstopped = {}     # Lead chat id -> its worker sessions a failed ``end_after_change`` left running (``unstopped``).
# Model-facing (English): a merge of such a worker's part, refused (``worker_merge.precondition``).
UNSTOPPED = ("Worker %d was not stopped when this chat's file changed, so its part was built before that change; such a "
             "part is not merged while that worker remains this chat's")


def stop_reason(error):
    """Why ``error`` stopped a restart, for ``worker_queue.RESTART_FAILED``: its own ``Text`` reason as it is (a stop's,
    such as ``REPLACED``: translated where shown), a worker chat op's plain reason (``ChatOpFailed.shown``: never its
    op or chat id), else its words."""
    from .worker_chat_lifecycle import ChatOpFailed
    first = error.args[0] if error.args else None
    if isinstance(first, Text):
        return first
    return error.shown()[0] if isinstance(error, ChatOpFailed) else str(error)[:200]


def _queue():
    from . import worker_queue
    return worker_queue


def owner(entry):
    """The lead chat id any queued or in-flight entry belongs to: a part holds its lead's chat, a restart its id."""
    lead = entry.get("lead")
    return lead if lead is None or isinstance(lead, str) else lead.state["id"]


def current(lead):
    """Whether ``lead`` (its chat, or its id: then still open) was never invalidated by a change of Angelo's document
    (``Controller.set_document`` with another file's key: a load). Without Blender there is nothing to check."""
    if lead is None or not isinstance(lead, str):
        return lead is None or not getattr(lead, "document_invalid", False)
    ui = _queue()._ui()
    chat = ui._controllers.get(lead) if ui is not None else True
    return chat is not None and not getattr(chat, "document_invalid", False)


def _stop_relaunch(item):
    """A restart under way is dropped: its worker stops once its launch has ended."""
    from . import worker_routing
    threading.Thread(target=lambda: (item["done"].wait(), worker_routing.stop_later(item["worker"])),
                     daemon=True, name="AngeloWorkerLaunchStop").start()


def end_parked(session, why, ending):
    """A parked chat whose part can never land again (merged already, or its lead's document replaced) ends as a
    stopped worker does: out of ``parked``, its tile ended, a restart waiting or launching told ``why`` (break-it
    E2E-19A M-3, L-1), and anything under way ends saying ``ending`` (``worker_routing.end_turn``). A later message is
    refused with nothing launched (``worker_routing.ended_worker``). True when it was parked."""
    from . import parallel_sessions
    queue = _queue()
    if session not in queue.parked:
        return False
    queue.parked.discard(session)
    item = queue._resuming.get(session)
    if item in queue.queue:
        queue.queue.remove(item)
    elif item in queue._relaunching:
        queue._relaunching.remove(item)
        _stop_relaunch(item)
    if item is not None:
        queue._resume_failed(item, RuntimeError(why))
    ui = queue._ui()
    chat = ui._controllers.get(session) if ui is not None else None
    if chat is not None:
        from . import worker_routing
        worker_routing.end_turn(chat, ending)   # Idle while parked; anything under way ends as every ending's does.
        parallel_sessions.unbound(chat)
    queue._changed()
    return True


def _holding():
    """The leads (chats or ids) that hold anything that runs or would start: an entry of any kind, a bound worker or a
    parked chat."""
    from . import worker_pool, worker_routing, worker_sessions
    queue = _queue()
    ui = queue._ui()
    entries = list(worker_sessions._launching) + queue.queue + queue._relaunching + worker_pool._pool
    held = {owner(entry) for entry in entries}
    for lead, sessions in list(worker_sessions._started.items()):
        if any(session in queue.parked or (ui is not None and worker_routing.bound(ui._controllers.get(session))
                                           is not None) for session in sessions):
            held.add(lead)
    return held - {None}


def _invalidated(lead):
    """An open lead chat that a change of Angelo's document invalidated. A lead that is gone is a load's to end."""
    if not isinstance(lead, str):
        return getattr(lead, "document_invalid", False)
    ui = _queue()._ui()
    chat = ui._controllers.get(lead) if ui is not None else None
    return chat is not None and getattr(chat, "document_invalid", False)


def leads_ended():
    """Main thread, each queue step: one cleanup for every way a lead becomes invalid. A load runs it at once
    (``session_rail_input._document_loaded``); a lead ``Controller.set_document`` invalidated otherwise is found at the
    next step (break-it E2E-20 R4-M-1: its workers ran on billed, its queue still launched). Another scene of its file
    never invalidates it (R5-M-1). Nothing to stop: nothing done."""
    if any(_invalidated(lead) for lead in _holding()):
        document_replaced()


def _lead_id(lead):
    return lead if isinstance(lead, str) else lead.state["id"]


def reloaded(lead_id, why=None):
    """The one place every rebind after the lead's document content changed calls: its own file loaded again (Revert,
    Open Recent: ``document_identity``), a Restore or an Undo restore (``controller_recovery.rebind_after_restore``).
    Its batch fires no event and everything of its workers ends as for a load of another file, so no part built
    before the change merges; the person starts again (the maintainer, 2026-10-29)."""
    from . import worker_resume
    worker_resume.cancel(lead_id)
    if any(_lead_id(lead) == lead_id for lead in _holding()):
        _reloaded.add(lead_id)
        _reasons[lead_id] = why or REOPENED
        try:
            document_replaced()
        finally:   # Never left behind by a failure part-way: a later sweep would end the lead's next workers.
            _reloaded.discard(lead_id)
            _reasons.pop(lead_id, None)


def end_after_change(controller, why=None):
    """``reloaded`` for ``controller`` (its document's content just changed: a reload, a Restore, an Undo restore, or
    another file replaced it: ``why`` ``REPLACED``), never raising: when its workers could not all be ended, the chat
    says so. True when they were."""
    lead = controller.state["id"]
    try:
        reloaded(lead, why)
    except Exception:
        from . import worker_sessions
        from .ui_text import notice
        _unstopped[lead] = _unstopped.get(lead, set()) | set(worker_sessions._started.get(lead, ()))
        controller.emit("message", {"role": "system", **notice(WORKERS_LEFT)})
        controller.version += 1
        return False
    _unstopped.pop(lead, None)   # Every worker of it was ended: the ordinary rules govern its parts again.
    return True


def unstopped(lead_id, session=None):
    """Whether a merge of ``session``'s part (any worker's, for None) must be refused: a failed ``end_after_change``
    left that worker of ``lead_id`` running after its document changed, so its part predates the change. A worker the
    person discarded (no longer the lead's) no longer counts; a later successful ending clears the lead."""
    from . import worker_sessions
    left = _unstopped.get(lead_id)
    if left:
        left &= set(worker_sessions._started.get(lead_id, ()))
    if not left:
        _unstopped.pop(lead_id, None)
        return False
    return session is None or session in left


def document_replaced():
    """After a change of Angelo's document: a lead it invalidated, or that is gone, can never merge again, so everything
    of it stops, whatever kind of entry holds it: starts launching, queued parts and restarts, restarts launching,
    workers launched ahead, bound and parked workers (each failed with why). Break-it E2E-18 M-3; the audit of 948d877:
    a queued restart holds its lead's id, not its chat, and restarts already launching escaped to bind later."""
    from . import worker_dispatch, worker_pool, worker_routing, worker_sessions
    worker_dispatch.reclaim()   # A lead script's load runs in its away region: no lane runs on while this unbinds.
    queue = _queue()
    ui = queue._ui()
    from . import worker_resume
    try:
        for lead in [lead for lead in _holding() if not current(lead) or _lead_id(lead) in _reloaded]:
            why = _reasons.get(_lead_id(lead), REOPENED) if _lead_id(lead) in _reloaded else REPLACED
            worker_resume.cancel(_lead_id(lead))   # Its batch's event too.
            for item in [i for i in worker_sessions._launching if owner(i) == lead]:
                worker_sessions.cancel_starts(item["lead"])
            for item in [i for i in queue.queue if owner(i) == lead]:
                queue.queue.remove(item)
                if item.get("kind") == queue.RESUME:
                    queue._resume_failed(item, RuntimeError(why))
                else:
                    queue._remove_folder(item)   # A queued part's worker never started: nothing of it runs.
            for item in [i for i in queue._relaunching if owner(i) == lead]:
                queue._relaunching.remove(item)
                _stop_relaunch(item)
                queue._resume_failed(item, RuntimeError(why))
            for entry in [e for e in worker_pool._pool if e["lead"] == lead]:
                worker_pool._release(entry)
            for session in worker_sessions._started.get(lead, ()):
                chat = ui._controllers.get(session) if ui is not None else None
                if chat is not None and worker_routing.bound(chat) is not None:
                    worker_sessions.died.setdefault(session, why)
                    worker_routing.unbind(chat, worker_routing.END_REPLACED)   # Its turn ends as the person's Stop ends
                    # it, then its worker stops off the main thread, as every unbind does (``end_turn``; audit P1).
                elif session in queue.parked:   # Its worker already stopped: a message would launch one for nothing (L-1).
                    worker_sessions.died.setdefault(session, why)
                    end_parked(session, why, worker_routing.END_REPLACED)
    finally:
        _reloaded.clear()
        _reasons.clear()
    queue._changed()
