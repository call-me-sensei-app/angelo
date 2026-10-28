# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Warm worker Blenders: workers launched ahead of a lead's starts, and a done worker's process taking a waiting part.

A worker's spin-up, from its start to its brief, was 1.5 s and 3.9 s in the 2026-10-25 dev check (most of it the
worker Blender's launch); a running worker takes a new part file in tens of milliseconds (``worker_switch``). Two
ways spare a part the launch:

* **Launched ahead.** When a lead chat with parallel edit on looks up its workers' tools (``looked_up``: an
  ``angelo_agent_capabilities`` lookup naming one of them, which 7 of 7 recorded leads made 15-20 s before their first
  start), idle workers launch on scratch part files, up to the free places under ``parallel_access.worker_limit``: a
  worker launched ahead is a live worker and counts. A start takes one before launching a new one (``take``, from
  ``worker_sessions._launch``); its ``start`` then switches it to the part's own file, waiting first for a launch
  still under way. Untaken workers stop when the lead's turn ends without a start, after ``IDLE_SECONDS``, when live
  workers would otherwise be more than the limit, and when the add-on ends. Nothing billed happens ahead: a worker's
  chat is made, and its brief sent, only when a part starts. Never below the capability bar (``parallel_capability``,
  forced runs included), never for a worker's chat, voice, an app's task or a request not offered the family.
* **Warm reuse.** When a worker's chat is done and a part waits for a place, ``reuse`` (the queue's ``REUSE`` seam)
  moves the done worker's process to the waiting part: the done chat leaves it (``worker_routing.detach``: its saved
  part file and its layout stay, so it still merges, and its tile keeps its last save), and a new chat binds to the
  process for the waiting part with that part's own worker number, its brief its first message. With A' the done
  chat closes in the worker first (``chat.close``: a close that fails leaves the process to stop as usual) and the new
  chat opens there as it binds (``worker_chat_lifecycle``). One part per chat
  keeps every invariant keyed on the number (status, merge, the merge card, the tiles); the opening's prompt cache is
  shared across worker chats anyway.

Everything here runs on Blender's main thread except the launches themselves (``WorkerProcess.start``).
"""

import threading
import time
import uuid
from pathlib import Path

IDLE_SECONDS = 120.0   # An untaken worker launched ahead stops after this: about 6x the lookup-to-start time measured.
TICK = 0.5
SCRATCH = "ahead"      # Under the state folder's ``parallel``: each launched-ahead worker's scratch part, removed after.

_pool = []      # {"worker", "lead", "since", "done", "error"}: launched ahead, not taken
_starters = set()   # Lead chat session ids that started a part since their pool workers launched
_stopping = []  # Threads stopping pool workers (``release_all`` waits for them)


def _lead_names():
    from .worker_spec import WORKER_NAMES
    return WORKER_NAMES


def eligible(controller):
    """Whether ``controller``'s request may launch workers ahead: a lead chat's own turn, with the family offered, on a
    machine at the capability bar (a forced run on one below it never does)."""
    from . import parallel_access, parallel_capability, worker_routing
    if controller is None or getattr(controller, "busy", False) is not True:
        return False   # Only inside the lead's turn: its end releases them.
    if not parallel_access.enabled() or not parallel_capability.current().capable:
        return False
    if worker_routing.worker_snapshots(controller):
        return False
    if getattr(controller, "voice", None) is not None or getattr(controller, "external_client", ""):
        return False
    allowed = getattr(getattr(controller, "_runtime_context", None), "allowed_tools", None)
    return allowed is None or "angelo_workers_start" in allowed


def looked_up(controller, arguments, answer=None):
    """``angelo_agent_capabilities`` ran for ``controller``: a lookup naming a lead's worker tool launches workers
    ahead (``prelaunch``), and its ``answer`` says how many this lead has launching or ready (``AHEAD``: a lead decided
    its split not knowing its workers were already there; LANE-overheads). Never raises: the lookup's own answer is
    what the model asked for."""
    try:
        names = (arguments or {}).get("names") or ()
        if (threading.current_thread() is threading.main_thread() and any(name in _lead_names() for name in names)
                and eligible(controller)):
            prelaunch(controller)
            lead = controller.state["id"]
            ready = sum(1 for entry in _pool if entry["lead"] == lead and entry["error"] is None)
            if ready and isinstance(answer, dict):
                answer = dict(answer, **{AHEAD: ready})
    except Exception as error:   # noqa: BLE001 - a launch ahead never fails the lookup.
        print("Angelo: launching workers ahead:", type(error).__name__, error)
    return answer


AHEAD = "workers_launched_ahead"   # Model-visible (never translated); worker_spec.WORKER_GUIDANCE says what it is.


def places():
    """Free places under the worker limit, the queue's own count (``worker_places``): negative when over it."""
    from . import worker_places
    return worker_places.free()


def prelaunch(controller):
    """Launch an idle worker in every free place for ``controller``'s turn; its idle time restarts for those already
    waiting. The workers launched (the pool's entries)."""
    from . import ui, worker_routing
    lead = controller.state["id"]
    now = time.monotonic()
    mine = [entry for entry in _pool if entry["lead"] == lead]
    for entry in mine:
        entry["since"] = now
    if not mine:
        _starters.discard(lead)   # A new pool for this turn: only its own starts keep its workers past its end.
    launched = []
    for _index in range(max(0, places())):
        folder = Path(ui.state_directory()) / "parallel" / SCRATCH / uuid.uuid4().hex
        worker = worker_routing.launch(folder / "part.blend")
        worker.scratch, worker.ahead = folder, True
        entry = {"worker": worker, "lead": lead, "since": now, "done": threading.Event(), "error": None,
                 "kept": False}
        threading.Thread(target=_start, args=(entry,), daemon=True, name="AngeloWorkerAhead").start()
        _pool.append(entry)
        launched.append(entry)
    if launched:
        _schedule()
    return launched


def _start(entry):
    try:
        entry["worker"].start()
    except BaseException as error:   # noqa: BLE001 - a launch that failed is dropped; a start then launches anew.
        entry["error"] = error
    finally:
        entry["done"].set()


def takeable():
    """Workers launched ahead that a starting part can take now (``take``): neither failed nor ended."""
    return sum(1 for entry in _pool if entry["error"] is None and not (entry["done"].is_set()
                                                                        and not entry["worker"].alive()))


def yield_place():
    """A queued restart (a person's message) needs a place the pool holds: the newest worker launched ahead stops,
    unless one is stopping already (its place frees when it has; asking again meanwhile would stop them all)."""
    if _pool and not any(thread.is_alive() for thread in _stopping):
        _release(_newest())


def _freeing():
    """Workers launched ahead that are stopping: their places free once their processes exit."""
    return sum(1 for thread in _stopping if thread.is_alive())


def _newest():
    """The pool's newest worker (the latest lookup's; of those, the last launched)."""
    return max(reversed(_pool), key=lambda entry: entry["since"])


def take(part, lead=None):
    """A worker launched ahead for a part starting now, or None (``worker_sessions._launch``): a ready one first,
    else the one launched longest ago (its part's own start waits for that launch, then switches). From here it is
    the part's: its ``start`` switches it to ``part``. ``lead``: the chat starting it (its turn has started one)."""
    if lead is not None and any(entry["lead"] == lead.state["id"] for entry in _pool):
        _starters.add(lead.state["id"])
    for entry in sorted(_pool, key=lambda item: (not item["done"].is_set(), item["since"])):
        if entry["error"] is not None or (entry["done"].is_set() and not entry["worker"].alive()):
            continue
        _pool.remove(entry)
        Path(part).parent.mkdir(parents=True, exist_ok=True)   # A worker saving into a missing folder cannot start.
        entry["worker"].part = Path(part)
        return entry["worker"]
    return None


def reuse(done_session_id, item):
    """The queue's ``REUSE`` seam: the done worker chat ``done_session_id``'s process takes ``item``, a part waiting
    for a place (``worker_sessions._prepare``'s item, its worker not started). True when taken: the done chat has left
    the process, which switches to the part's file in ``worker_sessions._launching`` and is adopted there as any
    launch (a new chat, the part's number, its brief); the caller then neither launches the part nor stops the done
    worker. False, with nothing changed, when the process cannot take it: not a live worker this Angelo started, its
    chat not finished (a turn, a lane step or a card of the person's still open), its worker unresponsive or ended,
    or a part whose own worker was already launched or taken from those launched ahead."""
    from . import parallel_access, ui, worker_routing, worker_sessions
    if not isinstance(item, dict):
        return False
    if getattr(worker_routing._workers.get(done_session_id), "ran_python", False):
        # A part's script can leave handlers, timers, classes or module state behind that no reset proves gone: such
        # a Blender stops instead (the audit of 948d877: a persistent handler built into the next part).
        return False
    chat = ui._controllers.get(done_session_id)
    worker = worker_routing._workers.get(done_session_id)
    executor = worker_routing.bound(chat) if chat is not None else None
    cold = item.get("worker")
    if (not parallel_access.enabled() or worker is None or getattr(executor, "worker", None) is not worker
            or not hasattr(worker, "serving") or not worker.alive() or getattr(executor, "unresponsive", True)
            or done_session_id in worker_sessions.died or not worker_sessions.finished(done_session_id)
            or worker_sessions.awaiting_person(done_session_id)
            or getattr(cold, "part", None) is None or getattr(cold, "process", None) is not None
            or getattr(cold, "ahead", False) or item.get("thread") is not None):
        return False
    from . import worker_chat_lifecycle as lifecycle
    try:   # A': the done chat's journal is sealed before its process takes the part (its park closed it already).
        lifecycle.close_chat(done_session_id, "its worker takes another part")
    except lifecycle.ChatOpFailed as error:
        print("Angelo: worker reuse:", error)
        return False
    worker_routing.detach(chat)
    Path(cold.part).parent.mkdir(parents=True, exist_ok=True)
    worker.part = Path(cold.part)
    item["worker"] = worker
    worker_sessions._launch(item, take=False)
    _schedule_adoption()
    return True


def _schedule_adoption():
    import bpy
    from . import worker_sessions
    if not bpy.app.background and not bpy.app.timers.is_registered(worker_sessions._tick):
        bpy.app.timers.register(worker_sessions._tick, first_interval=worker_sessions.POLL, persistent=True)


def _schedule():
    try:
        import bpy
    except ImportError:   # Host tests of other modules: no Blender, no timer.
        return
    if not bpy.app.background and not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK, persistent=True)


def _timer():
    """``tick`` as Blender's timer runs it: one that fails stops the pool's workers rather than leave them unwatched."""
    try:
        return tick()
    except Exception as error:   # noqa: BLE001 - nothing is left running without a timer to stop it.
        print("Angelo: workers launched ahead:", type(error).__name__, error)
        for entry in list(_pool):
            _release(entry)
        return None


def step():
    """The pool's step where no timer runs it (a background Angelo: ``worker_queue.drive``): its workers launched ahead
    stop at their turn's end, their idle bound and past the limit there too (break-it E2E-20 L-3)."""
    if _pool:
        tick()


def tick(now=None):
    """Main thread: stop the pool's workers that are no longer wanted (module docstring); the timer's next interval,
    or None once the pool is empty."""
    from . import parallel_access, ui
    now = time.monotonic() if now is None else now
    ended = {lead for lead in {entry["lead"] for entry in _pool} | _starters
             if getattr(ui._controllers.get(lead), "busy", False) is not True}   # Its turn ended (or it is gone).
    for entry in list(_pool):
        if entry["lead"] in ended and entry["lead"] in _starters:
            entry["kept"] = True   # Its turn started parts: the rest wait for a later start, up to the idle bound.
        failed = entry["error"] is not None or (entry["done"].is_set() and not entry["worker"].alive())
        if (failed or (entry["lead"] in ended and not entry["kept"]) or now - entry["since"] >= IDLE_SECONDS
                or not parallel_access.enabled()):
            _release(entry)
    _starters.difference_update(ended)   # The next turn counts its own starts.
    while _pool and places() + _freeing() < 0:   # A place over the limit: the newest yields it, one each, never all
        _release(_newest())                         # (a released worker counts until its process exits: E2E-20 L-1).
    return TICK if _pool else None


def _release(entry):
    """Stop one untaken worker, off the main thread; one still launching stops once its launch ends."""
    if entry in _pool:
        _pool.remove(entry)

    def stop():
        entry["done"].wait()
        entry["worker"].stop()
    thread = threading.Thread(target=stop, daemon=True, name="AngeloWorkerAheadStop")
    _stopping[:] = [item for item in _stopping if item.is_alive()] + [thread]
    thread.start()


def release_all(timeout=15.0):
    """The add-on ends: every untaken worker stops, and this waits for them, ``timeout`` seconds in all."""
    import bpy
    for entry in list(_pool):
        _release(entry)
    _starters.clear()
    if not bpy.app.background and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    deadline = time.monotonic() + timeout   # One bound for all: each exits anyway once Angelo's end closes it.
    for thread in list(_stopping):
        thread.join(max(0.0, deadline - time.monotonic()))
    _stopping.clear()


def pooled():
    """The workers launched ahead and not taken, oldest first (status of fixtures and the app log)."""
    return [entry["worker"] for entry in _pool]


from . import worker_queue as _queue  # noqa: E402 - the queue's seam: a done worker's Blender takes the next part.
if reuse not in _queue.REUSE:
    _queue.REUSE.append(reuse)
