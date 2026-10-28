# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker places: parts past the free places wait their turn, and a done worker frees its place once its part is saved.

A **place** is one live worker process (``parallel_access.worker_limit``: this machine's count, or a forced run's cap):
the workers bound to a chat (``worker_sessions._running``) and those starting. ``angelo_workers_start``
(``worker_sessions.execute``) hands its prepared parts to ``admit``: each starts at once while a place is free, the rest
wait here in order (``queue``), and ``tick`` starts the next one as soon as a place frees. A queued part has not
started, so nothing is billed for it; the lead can take it back (``take_back``) and build it itself. ``tick`` runs on
the start timer (``worker_sessions._tick``, every ``POLL`` while anything here is live); a background Angelo runs no
timer, so ``drive`` steps it there (each start, each status call).

A worker is **done** once its brief's turn ended with nothing queued on its lane (``worker_sessions.finished``): the
status wait answers then (``worker_status``) and the merge admits it. Its place frees once its part is saved: off the
main thread (with design A' its chat closes in its worker first, and a closed chat parks whatever reached it since), its
worker saves only if anything is unsaved (every edit already saved its file), then its process
stops (``worker_routing.unbind(parked=True)``). It is **parked**: never died or failed (its tile and status show how
its turn ended), and the merge reads its saved part file and kept layout as for any worker. A message to a parked chat
(``worker_routing.deliver``: the person's, or any other) waits for a place ahead of queued parts, relaunches a worker on
the chat's own part file and is delivered there (``resume``). A failed save keeps the worker in its place and retries
with bounded backoff (``worker_save_recovery``); notices and status expose the block until a fresh save succeeds.

A worker whose process ended between requests (nothing asked it, so nothing found out) is reconciled as a confirmed
death is, at each step, each start and each status (``reap``): it never holds a place or keeps a part waiting.

S2's seam (warm-worker reuse): once a done worker's part is saved and a part is queued, each callable in ``REUSE`` is
called with ``(done_session_id, queued_item)`` on the main thread, the done worker still bound and running. One that
returns True took the part into that warm worker: the item leaves the queue and nothing stops. Otherwise the done
worker stops and the part launches as usual. A queued item is ``worker_sessions._prepare``'s: ``lead``, ``part``
(``name``, ``brief``), ``number``, ``references`` and an unstarted ``worker`` whose ``part`` is its own new part file.
No bpy at import.
"""

import threading
import time

from .remote_executor import EndedWorker, _nothing_ran
from .refusal_codes import RefusalError
from .ui_text import n_
from . import worker_chat_lifecycle, worker_parking, worker_save_recovery
from .worker_save_recovery import blocker as save_problem

REUSE = []          # Callables (done_session_id, queued_item) -> bool, True when that warm worker took the part: the
#                     done chat is then parked (worker_pool.reuse adds itself).
SAVE_SECONDS = 60.0   # A done worker's save before it stops (the worker's own save takes well under a second).
SETTLE_SECONDS = 30.0
RESUME = "resume"   # A queue item that relaunches a parked chat's worker (its "kind").
queue = []          # Waiting for a place, in order: worker_sessions' prepared parts, and resumes (ahead of parts).
parked = set()      # Worker chats whose worker stopped once their part was done and saved (1a).
_parking = {}       # session id -> {"executor", "done", "answer", "error"}: saving before it stops.
_held = {}          # session id -> failed-save recovery record (worker_save_recovery); the live worker stays.
_relaunching = []   # Resume items whose worker is starting.
_resuming = {}      # session id -> its resume item, queued or starting.


# Model-visible (never translated): the start's answer and a parked chat's refusal.
NOTE = "Each worker builds its part in its own file and replies there; angelo_workers_status reads them."
QUEUED_NOTE = ("The queued parts start in order, each once a running worker is done; angelo_workers_status take_back "
               "takes one back before it starts.")
PARKED = ("This chat's worker stopped once its part was done and saved ({part}); nothing ran. A message to this chat "
          "starts it again on that part.")
RESTART_FAILED = n_("This chat's worker could not start again ({why}); nothing was sent.")   # Shown in the chat.


class ParkedWorker(EndedWorker):
    """The executor of a parked chat between its worker's stop and a message that relaunches it: every call is refused
    with nothing run (``worker_routing.ended_worker``), saying how to start it again (voice cannot)."""

    def _refuse(self, *args, **kwargs):
        import os
        raise _nothing_ran(RefusalError(PARKED.format(part=os.path.basename(self.part)), code="CANCELLED"))

    precondition = precondition_change = rebind_program = execute = recipe_program = host_program = _refuse
    start_native_job = prepare_turn = capture_checkpoint = _refuse


def _ui():
    try:
        from . import ui
    except ImportError:   # Host tests of other modules: no Blender, nothing to schedule.
        return None
    return ui


def free():
    """Places free now, from the one count the pool reads too (``worker_places``)."""
    from . import worker_places
    return max(0, worker_places.free())


def _room(item):
    """Whether ``item`` may start now: a free place, or, for a part, a worker launched ahead that its start takes
    (``worker_sessions._launch``). A restart opens its own part file, so it never takes one (a switch makes a new part)."""
    from . import worker_pool
    return free() > 0 or item.get("kind") != RESUME and worker_pool.takeable() > 0


def _changed():
    """The rail shows queued and starting parts: draw it again."""
    from . import parallel_sessions
    parallel_sessions._bump()


def admit(lead, items):
    """``worker_sessions.execute``'s prepared parts, in request order: each starts while a place is free, else waits
    behind what already waits. The start's answer: the parts starting and those queued, with their place in line."""
    from . import worker_sessions
    reap()
    _start_queued()
    starting, waiting = [], []
    for item in items:
        if not queue and _room(item):
            worker_sessions._launch(item)
            starting.append({"worker": item["number"], "name": item["part"]["name"]})
        else:
            queue.append(item)
            waiting.append({"worker": item["number"], "name": item["part"]["name"], "position": len(queue)})
    _changed()
    drive()
    result = {"success": True, "starting": starting, "note": NOTE}
    if waiting:
        result.update(queued=waiting, note=NOTE + " " + QUEUED_NOTE)
    from . import worker_facts   # How long the line is, measured after ``drive`` (``queue_facts``): only while one waits.
    queue_line = worker_facts.queue_facts(lead.state["id"])
    if queue_line:
        result[worker_facts.QUEUE] = queue_line
    return result


def take_back(lead, numbers):
    """Remove ``lead``'s queued parts ``numbers`` before they start (the lead builds them itself): ``(taken, refused)``
    where each refusal says why (started already, or no such part). Their empty folders go too."""
    from . import worker_sessions
    taken, refused = [], []
    started = {worker_sessions._parts[s][0] for s in worker_sessions._started.get(lead.state["id"], ())
               if s in worker_sessions._parts}
    starting = {item["number"] for item in worker_sessions._launching if item["lead"] is lead}
    for number in numbers:
        item = next((i for i in queue if i.get("kind") != RESUME and i["lead"] is lead and i["number"] == number), None)
        if item is not None:
            queue.remove(item)
            taken.append(number)
            _remove_folder(item)
        else:
            refused.append({"worker": number, "why": "it already started" if number in started | starting
                            else "no queued part of this conversation has that number"})
    if taken:
        _changed()
    return taken, refused


def _remove_folder(item):
    folder = getattr(getattr(item.get("worker"), "part", None), "parent", None)
    try:
        if folder is not None:
            folder.rmdir()   # Only an empty one: a part never started wrote nothing there.
    except OSError:
        pass


def waiting(lead):
    """Whether any part of ``lead`` has not reached its chat yet (queued, or starting), or a message to one of its
    parked workers waits for its worker to start again (break-it E2E-20 M-2)."""
    from . import worker_sessions
    return any(item["lead"] is lead for item in worker_sessions._launching) or any(
        item.get("kind") != RESUME and item["lead"] is lead for item in queue) or any(
        owner(item) == lead.state["id"] for item in _resuming.values()) or worker_save_recovery.waiting(lead)


def position(item):
    return queue.index(item) + 1 if item in queue else None


def parts_waiting():
    """Every part not yet bound to a chat, for the rail: ``{"key", "number", "name", "kind", "position"}``, queued
    (with its place in line) or starting, by number. ``key`` stands in for the session it does not have yet."""
    from . import session_rail, worker_sessions
    found = [(item, session_rail.QUEUED, index + 1) for index, item in enumerate(queue) if item.get("kind") != RESUME]
    found += [(item, session_rail.STARTING, None) for item in worker_sessions._launching]
    return sorted(({"key": "part:%s/%d" % (item["lead"].state["id"], item["number"]), "number": item["number"],
                    "name": item["part"]["name"], "kind": kind, "position": place} for item, kind, place in found),
                  key=lambda entry: entry["number"])


def resuming_kind(session_id):
    """A parked chat's tile while its worker comes back: queued (its place in line) or starting; else None."""
    from . import session_rail
    item = _resuming.get(session_id)
    if item is None:
        return None
    return (session_rail.STARTING, None) if item in _relaunching else (session_rail.QUEUED, position(item))


# The step ----------------------------------------------------------------------------------------------------------

def tick():
    """Main thread: relaunched workers bind, saved done workers stop (or go to ``REUSE``), workers that became done
    start saving, and what waits starts while places are free. True while anything here needs another step."""
    ui = _ui()
    if ui is None:
        return False
    reap(ui)
    _finish_relaunches(ui)
    _finish_parks(ui)
    for session in _newly_done(ui):
        _park(session, ui)
    leads_ended()   # A lead invalidated by any route stops its workers before anything of it starts (R4-M-1).
    _start_queued()
    from . import worker_resume
    worker_resume.step(ui)   # A lead whose workers have all ended, idle meanwhile, hears it once.
    return active(ui)


def reap(ui=None):
    """Worker chats whose worker process ended between requests: each is reconciled as a confirmed death is
    (``RemoteExecutor._died``: ``worker_sessions.died``, its status ``failed``, the merge's warning, its place freed;
    the maintainer's audit of 34f111d: a process that died between requests held one of four places and blocked its
    replacement). A chat whose lane waits on its worker is left to that request, which finds out itself. A waitpid per
    worker; main thread. How many were found."""
    from . import parallel_sessions, worker_dispatch, worker_routing
    ui = ui or _ui()
    found = 0
    for session in (parallel_sessions.workers() if ui is not None else ()):
        chat = ui._controllers.get(session)
        executor = worker_routing.bound(chat)
        alive = getattr(getattr(executor, "worker", None), "alive", None)
        if executor is None or not callable(alive) or alive() or worker_dispatch.waiting(chat):
            continue
        process = getattr(executor.worker, "process", None)
        code = process.poll() if process is not None else None
        executor.document_key = ("angelo-worker", executor.worker_id, "ended")
        executor._died("The worker ended between requests (%s)" % (
            "exit code %s" % code if code is not None else "its connection closed"))
        found += 1
    if found:
        _changed()
    return found


def active(ui=None):
    from . import worker_routing, worker_sessions
    ui = ui or _ui()
    if queue or _parking or _relaunching:
        return True
    controllers = getattr(ui, "_controllers", {})
    return any(session not in parked and worker_routing.bound(controllers.get(session)) is not None
               for sessions in list(worker_sessions._started.values()) for session in sessions)


def _newly_done(ui):
    from . import worker_routing, worker_sessions
    found = []
    for sessions in list(worker_sessions._started.values()):
        for session in sessions:
            chat = ui._controllers.get(session)
            if session in parked or session in _parking or worker_routing.bound(chat) is None:
                continue
            if not worker_save_recovery.due(session):
                continue   # A failed save keeps its place until the bounded retry is due.
            if worker_sessions.finished(session):
                found.append(session)
    return found


def _park(session, ui):
    """Save a done worker's part off the main thread (only if anything is unsaved); ``_finish_parks`` stops it."""
    from . import worker_routing
    executor = worker_routing.bound(ui._controllers[session])
    record = {"executor": executor, "done": threading.Event(), "answer": None, "error": None}
    record["turn"] = worker_save_recovery.turn_binding(ui._controllers[session])
    if session in _held:
        record["retry"] = worker_save_recovery.retry_binding(executor)

    def run():
        try:   # A': the chat closes in its worker first (C10); a close that fails is a failed park, retried.
            record["closed"] = worker_chat_lifecycle.close_chat(session, "its part is done and saved")
            record["answer"] = executor.worker.request("save", SAVE_SECONDS, if_dirty="retry" not in record)
        except BaseException as error:   # noqa: BLE001 - the worker keeps its place; said on the main thread.
            record["error"] = error
        finally:
            record["done"].set()
    _parking[session] = record
    threading.Thread(target=run, daemon=True, name="AngeloWorkerPark").start()


def _finish_parks(ui):
    from . import worker_routing, worker_sessions
    for session, record in [(s, r) for s, r in _parking.items() if r["done"].is_set()]:
        del _parking[session]
        chat = ui._controllers.get(session)
        executor = worker_routing.bound(chat) if chat is not None else None
        if executor is not record["executor"]:
            continue   # Merged, ended or stopped meanwhile: nothing to park.
        answer, error = record["answer"], record["error"]
        if error is None and (not isinstance(answer, dict) or answer.get("error") is not None):
            error = (answer or {}).get("error") if isinstance(answer, dict) else "no answer"
        if error is None:
            error = worker_save_recovery.validate_retry(record, answer)
        if error is None and not record.get("closed") and not worker_save_recovery.current_turn(record, chat):
            error = "The worker started another request while saving; its current part needs a fresh save"
        if error is not None:
            worker_save_recovery.failed(session, executor, error, ui)
            continue
        if not record.get("closed") and not worker_sessions.finished(session):
            continue   # A message reached it: it carries on (one closed parks: its save followed the close, C2).
        if answer.get("value") and answer.get("layout"):
            executor.note_layout(answer["layout"])   # It saved something unsaved: the live part reads it again.
        worker_save_recovery.recovered(session, ui)
        # Only the head of the line takes its place: a person's restart waiting there goes first, its place freed by
        # this worker's stop (break-it E2E-20 M-1: a reuse handed it to the first queued part behind the restart).
        item = queue[0] if queue and queue[0].get("kind") != RESUME else None
        if item is not None and _reused(session, item):
            queue.remove(item)   # Its warm worker took the part (worker_pool): the chat is parked, as a stopped one is.
            parked.add(session)
            _held.pop(session, None)
            _changed()
            continue
        worker_routing.unbind(chat, worker_routing.END_DONE, parked=True)
        parked.add(session)
        _held.pop(session, None)
        _changed()


def _reused(session, item):
    from . import worker_pool   # noqa: F401 - its reuse is in REUSE before the first offer (it adds itself).
    for handler in list(REUSE):
        try:
            if handler(session, item) is True:
                return True
        except Exception as error:   # noqa: BLE001 - one handler never stops the part from starting.
            print("Angelo: worker reuse:", type(error).__name__, error)
    return False


def _start_queued():
    from . import worker_sessions
    head = worker_parking.first(queue)   # A restart waiting for its chat's park lets what follows go first.
    if head is not None and head.get("kind") == RESUME and not _room(head):
        from . import worker_pool   # A person's message outranks a worker launched ahead: the newest yields its place.
        worker_pool.yield_place()
    while not worker_sessions._released[0] and (item := worker_parking.first(queue)) is not None and _room(item):
        queue.remove(item)
        if item.get("kind") == RESUME:
            _relaunch(item)
        else:
            worker_sessions._launch(item)
        _changed()


# A message to a parked chat ----------------------------------------------------------------------------------------

def resume(controller, deliver, on_error):
    """For a parked chat: queue its worker's relaunch (ahead of queued parts) and ``deliver()`` once it is bound again,
    ``on_error(error)`` if it cannot start; True. Any other chat: False, nothing done."""
    session = controller.state["id"]
    item = _resuming.get(session)
    if item is None:
        if session not in parked and not worker_parking.waits(controller):   # A hosted chat parking waits for it.
            return False
        if not current(_lead_of(session)):   # Its lead can never merge it: nothing launches (break-it E2E-19A L-1).
            from . import worker_sessions
            worker_sessions.died.setdefault(session, REPLACED)
            from . import worker_routing
            end_parked(session, REPLACED, worker_routing.END_REPLACED)
            return False
        item = _resuming[session] = {"kind": RESUME, "session": session, "controller": controller,
                                     "lead": _lead_of(session), "deliveries": []}
        queue.insert(sum(1 for i in queue if i.get("kind") == RESUME), item)
        _changed()
    item["deliveries"].append((deliver, on_error))
    _start_queued()
    drive()
    return True


def _lead_of(session):
    from . import worker_sessions
    return next((lead for lead, sessions in worker_sessions._started.items() if session in sessions), None)


def _relaunch(item):
    from . import worker_routing, worker_sessions
    session = item["session"]
    if session not in parked:   # Its park ended without parking it (merged, ended or stopped meanwhile).
        return _resume_failed(item, RuntimeError(worker_parking.NOT_PARKED))
    part = (worker_sessions._parts.get(session) or (None, None, None))[2]
    try:
        if part is None:
            part = worker_routing.registry(item["controller"].store.directory)[session]
        item["worker"] = worker_routing.launch(part)
    except Exception as error:   # noqa: BLE001 - its messages hear why.
        _resume_failed(item, error)
        return
    item["done"], item["error"] = threading.Event(), None

    def run():
        try:
            from .worker_routing import ready
            ready(item["worker"])   # Started and greeted here: its rebinding sends nothing on the main thread.
        except BaseException as error:   # noqa: BLE001 - reported on the main thread.
            item["error"] = error
        finally:
            item["done"].set()
    _relaunching.append(item)
    threading.Thread(target=run, daemon=True, name="AngeloWorkerRelaunch").start()


def _finish_relaunches(ui):
    from . import worker_routing, worker_sessions
    for item in [i for i in _relaunching if i["done"].is_set()]:
        _relaunching.remove(item)
        if item["error"] is not None or worker_sessions._released[0] or not current(item.get("lead")):
            worker_routing.stop_later(item["worker"])   # Never bound for a document a load replaced (948d877).
            _resume_failed(item, item["error"] or RuntimeError(
                CLOSING if worker_sessions._released[0] else REPLACED))
            continue
        try:   # A': its chat.open goes off the main thread; its messages go once it opened (``_resumed``).
            worker_routing.adopt(item["controller"], item["worker"], again=True, defer=True)
        except Exception as error:   # noqa: BLE001 - adopt stopped the worker; the messages hear why.
            _resume_failed(item, error)
            continue
        worker_chat_lifecycle.when_open(item["controller"], lambda error, i=item: _resumed(i, error)) or _resumed(item, None)


def _resumed(item, error):
    if error is not None:   # It left its worker (``finish_opens``), which stops; the messages hear why.
        return _resume_failed(item, error)
    parked.discard(item["session"])
    _resuming.pop(item["session"], None)
    _changed()
    for deliver, on_error in item["deliveries"]:
        try:
            deliver()
        except Exception as error:   # noqa: BLE001 - as a delivery's own failure.
            on_error(error)


def _resume_failed(item, error):
    from .display_text import Text
    _resuming.pop(item["session"], None)
    _changed()
    for _deliver, on_error in item["deliveries"]:
        on_error(RuntimeError(Text(RESTART_FAILED, why=stop_reason(error))))


# Background Angelo, the add-on's end -------------------------------------------------------------------------------

def drive(until=None):
    """Keep scheduling going: the start timer in a window. A background Angelo runs no timer, so here, until nothing
    is starting or saving, or until ``until`` (a ``time.monotonic`` deadline: a status wait's own, so a slow launch never
    holds a wait past its timeout, break-it OVH2-M-5): starts and relaunches bind, done workers stop, and each new
    chat's first message is sent on its lane (``worker_dispatch.settle``). A lane stays owed a settle until it
    settled or its chat left, whatever cut its settle short (a deadline, or the settle's own timeout: OVH3-M-4), and
    the next step goes on with it."""
    from . import worker_sessions
    try:
        import bpy
    except ImportError:
        return
    if not bpy.app.background:
        if not bpy.app.timers.is_registered(worker_sessions._tick):
            bpy.app.timers.register(worker_sessions._tick, first_interval=worker_sessions.POLL, persistent=True)
        return
    from . import ui, worker_dispatch, worker_pool
    worker_pool.step()   # Its pool too: workers launched ahead and their releases (break-it E2E-20 L-3).
    before = {s for sessions in worker_sessions._started.values() for s in sessions}
    _unsettled.update(item["session"] for item in queue + _relaunching if item.get("kind") == RESUME)
    deadline = min(time.monotonic() + 600.0, until if until is not None else float("inf"))   # Starts end by
    while True:   # themselves (their ready timeout), saves by SAVE_SECONDS.
        worker_sessions._tick()
        if not (worker_sessions._launching or _relaunching or _parking or worker_chat_lifecycle.opening()) \
                or time.monotonic() >= deadline:
            break
        time.sleep(worker_sessions.POLL / 4)
    _unsettled.update({s for sessions in worker_sessions._started.values() for s in sessions} - before)
    for session in sorted(_unsettled):   # Kept until its lane settled, or its chat left (break-it OVH3-M-4).
        left = SETTLE_SECONDS if until is None else min(SETTLE_SECONDS, until - time.monotonic())
        if left <= 0:
            break
        if session not in ui._controllers or worker_dispatch.settle(ui._controllers[session], left):
            _unsettled.discard(session)


_unsettled = set()   # Background Angelo: new or restarted worker chats whose lane has not settled yet.


def release():
    """The add-on ends: nothing queued starts, a relaunching worker stops once ready, parked chats stay ended."""
    from . import worker_routing
    queue.clear()
    _unsettled.clear()
    for item in list(_relaunching):
        threading.Thread(target=lambda item=item: (item["done"].wait(), worker_routing.stop_later(item["worker"])),
                         daemon=True, name="AngeloWorkerLaunchStop").start()
    for table in (_relaunching, _resuming, _parking, _held, parked):
        table.clear()


from .worker_ending import (CLOSING, MERGED, REPLACED, current, document_replaced, end_parked,  # noqa: E402,F401
                           leads_ended, owner, stop_reason)
