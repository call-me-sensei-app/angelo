# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's tool work, without Blender's main thread ever waiting on its worker Blender.

A chat bound to a worker (``worker_routing``) has one **lane**: a thread that runs that chat's tool pump
(``Controller.pump_tools``) and its queued actions (an approval click, the brief), with the controller's own code,
unchanged: admission, approval modes, the journal and the results are exactly the window's. One **baton** decides
who may run the chat's controller code, so that code never runs on two threads at once:

* Blender's main thread takes a step (``step``, from the UI pump): it hands the baton to the lane and waits while the
  lane runs controller code, which is the same bookkeeping the window's chats do on the main thread;
* whenever the lane sends the worker a request (``RemoteExecutor.ask`` → ``io``), it hands the baton back at once and
  waits for the answer on its own thread. The main thread goes on drawing and handling input, and may run that chat's
  events in the meantime (a Stop, a streamed reply): the controller already re-checks its call after every request
  that can take long (``_tool_available``), as it does for a Stop that arrives during a precondition read;
* once the answer is there, the next ``step`` hands the baton back and the lane carries on.

So the main thread waits only for controller code, never for a worker. A worker request made on the main thread
itself (outside every lane) still blocks as before; each is counted with its duration (``STATS``), so a run shows
whether any remain. Standard library only and no ``bpy``: the lane runs nothing that touches Blender's data. Code the
controller's callbacks reach that does (a saved part's listeners, a snapshot's bookkeeping) goes through ``on_main``:
it runs on the main thread when the lane hands the baton back, in the same step (break-it E2E-1 M-3).

Work queued on a lane whose chat leaves its worker is never dropped silently: each action's owner hears that it did
not run, on the main thread (E2E-1 M-4).

A closed lane ends its thread and lets its controller go (audit P2: forgotten idle lanes kept both for the app's
life). An idle one ends at once. One waiting on its worker ends when the answer comes, and drops it: no controller code
runs after the close, so the chat never touches its document again. A chat that left its worker while a step was under
way is first stepped to the end of that step (``forget``), which reports how its call ended.

**Away regions** (lane g3b, item 2b): while the main thread runs another chat's tools (``tool_pump`` marks the
``pump_tools`` of a chat that has work: a view, a script, a merge, a commit), it runs no worker chat's code, so nothing
steps the lanes. One clock thread wakes every lane once, if the region is still open ``AWAY_AFTER`` after it began (a
short tick behaves as before; outside regions lanes wait untimed, break-it 19C M-1). A lane then takes the baton
itself: it carries on when its worker answers, and runs queued actions and admitted calls (admission notifies it) with
a pump step of its own (``free``). ``back`` ends the region at the UI pump's level: it waits only for each lane's
current section (to its next worker request or the end of its step), then runs what the lanes left for the main
thread (``on_main`` calls, errors, failed actions), as ``step`` does; a lane forgotten, closed or replaced runs them
then too (M-2). Every main-thread entry that touches a worker chat (``step``, ``forget``, ``close_all``, the worker
family's tools, the chat's own ``pump_events``, ``stop`` and ``set_document``) calls ``reclaim`` first, which ends the
region early. A lane stops starting sections once its chat's events back up (``event_queue``: none is ever dropped).
"""

import threading
import time

from .ui_text import n_   # noqa: E402 - marks the text for the catalogs; pure Python.
from .worker_away import AWAY_AFTER, STATS, _away, _clock, _lanes, away, back, reclaim   # noqa: F401,E501

GONE = n_("This chat's worker ended (its part was merged, or it stopped) before this ran; nothing was sent to it.")


class WorkerGone(RuntimeError):
    """A queued action that never ran: its chat left its worker first."""


class LaneClosed(BaseException):
    """Raised on a closed lane when its worker's answer arrives: the answer is dropped and no controller code runs
    after it (a BaseException, so no ``except Exception`` in the controller's code carries on with it)."""


POLL = 0.05          # In a region the clock opened: how often a lane in a turn looks for work nobody announced.
GAP = 0.001          # Between two free steps: the UI pump's own retry while a chat has dispatchable work.
_counting = threading.Lock()   # Lanes count at once (break-it 19C L-3).


def _count(key):
    with _counting:
        STATS[key] += 1


class Lane:
    """One worker chat's thread and its baton (module docstring)."""

    def __init__(self, controller):
        self.controller = controller
        self._turn = threading.Condition()
        self._lane_runs = False    # The baton: True while the lane may run controller code.
        self._work = None          # The step the lane runs now, or None when idle.
        self._waiting = False      # The lane is waiting on its worker (io).
        self._writing = False      # ... with a request that may write the part file (an edit: ``io(effects=True)``).
        self._answered = False     # ... and the answer (or its timeout) is there.
        self.actions = []          # (action, on_error) run first in the next step, on the lane, with the baton.
        self.failed = []           # (on_error, error) of actions that raised: handled on the main thread.
        self.main_calls = []       # Calls the lane's code made for the main thread (``on_main``), run by ``step``.
        self.closed = False        # No step runs again (the add-on ends).
        self.closing = False       # No new work; a step under way still finishes (its chat left its worker).
        self._free = False         # The lane took the baton itself, in an away region (``reclaim`` waits it out).
        self.error = None
        self.thread = threading.Thread(target=self._loop, daemon=True, name="AngeloWorkerLane")
        original = controller._main_thread_block

        def block(metrics, started):   # The lane's own sections are not main-thread time (``step`` records that).
            if threading.current_thread() is not self.thread:
                original(metrics, started)
        controller._main_thread_block = self._block = block
        self._record_block = original
        # The main thread's own entries into the chat (its events, a Stop) end an away region first, wherever they
        # are reached from (a nested UI pump would be one): never while its lane runs on (module docstring).
        self._entries = {name: getattr(controller, name, None) for name in ("pump_events", "stop", "set_document")}
        for name, method in self._entries.items():
            if callable(method):
                setattr(controller, name, lambda *a, _method=method, **k: (reclaim(), _method(*a, **k))[1])
        self._tools = getattr(controller, "tools", None)   # An admitted call wakes the lane (a provider's thread).
        put = getattr(self._tools, "put_nowait", None)
        if callable(put):
            self._tools.put_nowait = lambda item, _put=put: (_put(item), self._wake())
        self.thread.start()

    def _wake(self):
        with self._turn:
            self._turn.notify_all()

    # The main thread ------------------------------------------------------------------------------------------

    @property
    def waiting(self):
        """The lane is waiting on its worker: its chat's next request has not been answered yet."""
        return self._waiting and not self._answered

    def step(self, run):
        """Let the lane run ``run`` (or carry on where it waits), and wait only while it runs controller code.
        False when its worker has not answered yet, so nothing ran."""
        reclaim()
        with self._turn:
            if self.closed or self.waiting:
                return False
            if self._work is None:
                if self.closing or run is None and not self.actions:
                    return False
                self._work = run or (lambda: None)
            started = time.monotonic()
            self._lane_runs = True
            self._turn.notify_all()
            while self._lane_runs:
                self._turn.wait()
            waited = (time.monotonic() - started) * 1000
        STATS["steps"] += 1
        STATS["main_waits_ms_max"] = max(STATS["main_waits_ms_max"], waited)
        self._record_block(getattr(self.controller, "_request_metrics", None), time.monotonic() - waited / 1000)
        self._drain()
        return True

    def _drain(self):
        """Main thread, the lane not running: what its code left for the main thread."""
        while self.main_calls:
            call = self.main_calls.pop(0)
            try:
                call()
            except Exception as error:   # noqa: BLE001 - as the listeners it runs: never fails the step.
                print("Angelo: worker chat main-thread call:", type(error).__name__, error)
        if self.error is not None:
            error, self.error = self.error, None
            print("Angelo: worker chat step:", type(error).__name__, error)
        while self.failed:
            on_error, error = self.failed.pop(0)
            if on_error is not None:
                on_error(error)
            else:
                print("Angelo: worker chat action did not run:", error)

    @property
    def writing(self):
        """The lane waits on a request that may write its worker's part file (an edit, which saves it): a read of the
        part waits for it, while a read request, a picture or a turn's preparation never holds the part back."""
        return self.waiting and self._writing

    @property
    def busy(self):
        """A step is under way (running, or waiting on its worker)."""
        return self._work is not None

    def close(self):
        """No further steps: the lane's thread ends (at once when idle, when its worker answers when it waits on it,
        dropping the answer) and the controller gets its own main-thread clock back (audit P2)."""
        with self._turn:
            self.closed = True
            self._turn.notify_all()
        if getattr(self.controller, "_main_thread_block", None) is self._block:
            self.controller._main_thread_block = self._record_block
        for name, method in self._entries.items():
            if callable(method) and getattr(type(self.controller), name, None) is getattr(method, "__func__", None):
                self.controller.__dict__.pop(name, None)   # Its class's own method again.
        getattr(self._tools, "__dict__", {}).pop("put_nowait", None)

    def drop(self):
        """Queued actions that will never run go to their owners as failures (handled on the main thread)."""
        from .display_text import Text   # Shown in the chat: translated when drawn (pure Python, no bpy).
        while self.actions:
            _action, on_error = self.actions.pop(0)
            self.failed.append((on_error, WorkerGone(Text(GONE))))

    # The lane -------------------------------------------------------------------------------------------------

    def _open(self):
        """Under ``_turn``: an away region has lasted ``AWAY_AFTER``, and this lane may take the baton in it."""
        events = getattr(self.controller, "events", None)   # A quarter: an answer streaming meanwhile has room.
        room = events is None or not events.maxsize or events.qsize() * 4 < events.maxsize
        held = getattr(self.controller, "__dict__", {}).get("_held_events")   # ``event_queue`` holds the rest.
        return _away["on"] and _away["woken"] and not self.closing and not self.closed and room and not held

    def _free_step(self):
        from .controller import ToolDispatchBudget
        self.controller.pump_tools(None, None, apply_mutation=lambda call: apply(self.controller, call),
                                   budget=ToolDispatchBudget())

    def _loop(self):
        while True:
            with self._turn:
                while not self._lane_runs and not self.closed and not self._open():
                    self._turn.wait()   # A step, the clock, an action or an admission wakes it (module docstring).
                if not self._lane_runs and self.closed:   # Closed while idle: the thread ends (audit P2).
                    return
                stepped = self._lane_runs
            if not stepped:   # An away region: look for the chat's work outside the lock (its own state only).
                try:
                    wants = bool(self.actions) or bool(getattr(self.controller, "has_dispatchable_tools", False))
                except Exception:   # noqa: BLE001 - never ends the lane: the step reports it (main thread).
                    wants = True
                with self._turn:
                    if not wants or self._lane_runs or self.closed or not self._open():
                        if not self._lane_runs and not self.closed:   # A turn's work may come unannounced.
                            self._turn.wait(POLL if getattr(self.controller, "busy", False) else None)
                        continue
                    self._work, self._lane_runs, self._free = self._free_step, True, True
                _count("free_steps")
            with self._turn:
                work = self._work
            try:
                while self.actions:
                    action, on_error = self.actions.pop(0)
                    try:
                        action()
                    except Exception as error:   # noqa: BLE001 - its owner hears of it on the main thread.
                        if on_error is None:
                            raise
                        self.failed.append((on_error, error))
                work()
            except LaneClosed:   # Closed while its worker answered: nothing more runs, nobody waits for it.
                with self._turn:
                    self._work, self._lane_runs, self._free = None, False, False
                    self._turn.notify_all()
                return
            except BaseException as error:   # noqa: BLE001 - reported on the main thread; the lane carries on.
                self.error = error
            if self.closing:   # Its chat left its worker: queued work never runs; its owners hear it (``step``).
                self.drop()
            with self._turn:
                free, self._free = self._free, False
                self._work = None
                self._lane_runs = False
                self._turn.notify_all()
                if free and not self.closed:
                    self._turn.wait(GAP)

    def io(self, request, effects=False):
        """``request()`` (a blocking call to the worker) without the baton; the baton back before returning.
        ``effects``: the request may change the worker's document and save its part file."""
        lock = getattr(self.controller, "lock", None)
        if getattr(lock, "_is_owned", lambda: False)():
            # Controller code holds its lock across this request: the main thread would block on that lock anyway,
            # so keep the baton (a counted stall rather than an unsafe interleaving).
            _count("io_under_lock")
            return request()
        with self._turn:
            self._waiting, self._answered, self._writing = True, False, effects
            self._lane_runs = self._free = False
            self._turn.notify_all()
        _count("io")
        try:
            return request()
        finally:
            with self._turn:
                self._answered = True
                while not self._lane_runs and not self.closed:
                    if self._open():   # The main thread is away: carry on now, never stepped (module docstring).
                        self._lane_runs = self._free = True
                        break
                    self._turn.wait()   # A step or the clock.
                if self._free:
                    _count("free_resumes")
                self._waiting = self._writing = False
                if not self._lane_runs:   # Closed meanwhile: drop the answer, run no controller code (audit P2).
                    raise LaneClosed()


# The rest of Angelo -----------------------------------------------------------------------------------------------

def lane(controller, create=True):
    identity = controller.state["id"]
    current = _lanes.get(identity)
    if current is not None and current.controller is not controller:
        reclaim()
        current.close()
        current._drain()   # What it left for the main thread still runs (break-it 19C M-2).
        current = None
    if current is None and create:
        current = _lanes[identity] = Lane(controller)
    return current


def current():
    """The lane running on this thread, or None (the main thread, or any other)."""
    thread = threading.current_thread()
    return next((item for item in list(_lanes.values()) if item.thread is thread), None)


def ask(request, effects=False):
    """A blocking worker request, made the lane's way when on a lane (``Lane.io``); counted when it is not.
    ``effects``: it may change the worker's document and save its part (``writing``)."""
    running = current()
    if running is not None:
        return running.io(request, effects)
    started = time.monotonic()
    try:
        return request()
    finally:
        if threading.current_thread() is threading.main_thread():
            spent = (time.monotonic() - started) * 1000
            STATS["main_asks"] += 1
            STATS["main_asks_ms_max"] = max(STATS["main_asks_ms_max"], spent)


def on_main(call):
    """``call()`` on Blender's main thread: at once there, or when the lane running this hands the baton back (in the
    same step). For code the lane reaches that touches ``bpy``."""
    running = current()
    if running is None:
        return call()
    running.main_calls.append(call)
    return None


def owns(controller):
    """Whether ``controller``'s tool work belongs on a lane: it is bound to a worker, or a step it began there is
    still under way (it left its worker meanwhile: that step reports how its call ended)."""
    if getattr(controller, "hosted_in_worker", False) is True:
        return False   # Design A′: its worker runs its tools; the mirror has none to pump (worker_mirror).
    if getattr(controller, "remote_executor", None) is not None:
        return True
    if not _lanes:
        return False
    current_lane = _lanes.get((getattr(controller, "state", None) or {}).get("id"))
    return current_lane is not None and current_lane.controller is controller and current_lane.busy


def pump(controller, budget=None):
    """The UI pump's turn for a worker chat: its tool pump, on its lane."""
    from .controller import ToolDispatchBudget
    current_lane = lane(controller, create=getattr(controller, "remote_executor", None) is not None)
    if current_lane is None:
        return False
    if current_lane.closing and not current_lane.busy:
        forget(controller, at_once=True)
        return False

    seconds = getattr(budget, "seconds", 0.008)
    if getattr(budget, "started", None) is not None:   # What the tick has left (``tool_pump``): one shared budget.
        seconds = max(0.001, seconds - (time.monotonic() - budget.started))

    def run():
        controller.pump_tools(None, None, apply_mutation=lambda call: apply(controller, call),
                              budget=ToolDispatchBudget(seconds=seconds))
    return current_lane.step(run)


def apply(controller, call):
    """An approved edit of a worker chat: the worker stages and commits it (no Undo operator runs here)."""
    controller.approve_tool(call, None, None) or controller.execute_tool(call, None, None)


def approve(controller, call):
    """The person's Approve click on a worker chat's card: applied on the chat's lane at its next step. A click its
    chat's worker never gets (the chat left it first) ends the call, nothing ran."""
    def gone(error):
        if controller.pending.get(call.id) is call and not call.executing:
            controller.finish_tool(call, {"error_code": "CANCELLED", "error": str(error), "scene_changed": False})
    _queue(controller, (lambda: apply(controller, call), gone))


def submit(controller, action, on_error=None):
    """``action()`` on the chat's lane at its next step (a message to the chat, prepared on the worker's document);
    ``on_error(error)`` runs on the main thread when it raises."""
    _queue(controller, (action, on_error))


def _queue(controller, item):
    current_lane = lane(controller)
    current_lane.actions.append(item)
    with current_lane._turn:   # An idle lane waits untimed: it now has work for the next region (or step).
        current_lane._turn.notify_all()


def settle(controller, timeout=30.0):
    """Step ``controller``'s lane until its queued actions ran and no step is under way (a background Blender has no
    UI timer to do it: ``worker_sessions`` sends its briefs this way). False if that took longer than ``timeout``."""
    current_lane = _lanes.get(controller.state["id"])
    deadline = time.monotonic() + timeout
    while current_lane is not None and (current_lane.actions or current_lane.busy):
        if time.monotonic() > deadline:
            return False
        if not current_lane.step(None):
            time.sleep(0.002)
    return True


def waiting(controller):
    """The chat's worker is answering a request now: its part file may be written (``live_schedule`` waits)."""
    current_lane = _lanes.get(controller.state["id"])
    return current_lane is not None and current_lane.controller is controller and current_lane.waiting


def writing(controller):
    """The chat's worker is answering a request that may write its part file now (an edit): the part's read waits
    for it (``parallel_sessions``); any other request in flight leaves the part readable (break-it P1-9 P2). A
    worker-hosted chat's mirror says so from its worker's pending summaries (``MirrorController.writing``)."""
    if getattr(controller, "hosted_in_worker", False) is True:
        return controller.writing()
    current_lane = _lanes.get(controller.state["id"])
    return current_lane is not None and current_lane.controller is controller and current_lane.writing


def any_waiting():
    return any(item.waiting for item in list(_lanes.values()))


def dispatchable(controller):
    """Whether the UI pump should come back at once for ``controller`` (never while it only waits on its worker)."""
    current_lane = _lanes.get(controller.state["id"])
    queued = current_lane is not None and current_lane.controller is controller and bool(current_lane.actions)
    return (controller.has_dispatchable_tools or queued) and not waiting(controller)


def forget(controller, at_once=False):
    """The chat left its worker: its lane takes no new work, and ends once a step under way has finished."""
    current_lane = _lanes.get(controller.state["id"])
    if current_lane is None or current_lane.controller is not controller:
        return
    reclaim()
    current_lane.closing = True
    if at_once or not current_lane.busy:
        _lanes.pop(controller.state["id"], None)
        current_lane.close()
        current_lane.drop()   # Main thread here: the owners hear it now (break-it E2E-1 M-4) ...
        current_lane._drain()   # ... and what its last section left for the main thread runs (break-it 19C M-2).


def close_all():
    reclaim()
    for identity in list(_lanes):
        closed = _lanes.pop(identity)
        closed.close()
        closed._drain()
    with _clock:   # No lane left: the clock ends.
        _clock.notify_all()
