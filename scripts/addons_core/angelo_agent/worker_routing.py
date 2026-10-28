# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which document a chat works on: Angelo's own open file, or a worker Blender's part file.

A chat bound to a worker (``bind``) keeps everything a chat has in Angelo (its provider session, approvals, journal,
rows, snapshots), but its executor is that worker's ``RemoteExecutor`` and its document is the worker's. The binding
lives on the chat itself (``Controller.remote_executor``), so every caller gets it without knowing about workers:
``Controller.pump_tools``, ``approve_tool``, ``execute_tool`` and ``set_document`` use the worker's executor and
document whatever they are passed (``controller_external.bound_target``). Angelo's window adds only:

* the approve operator applies a worker's edit with ``execute_tool`` directly, never through its Undo operator: the
  edit, and its one Undo step, are in the worker's document;
* Angelo's own Undo, Redo and file loads leave worker chats alone (they change Angelo's document, not a worker's);
* a worker's snapshots are kept under a session of their own (``worker_snapshot_session``), which Angelo's Restore
  never reads, and Restore refuses a worker chat outright (``checkpoint_restore_ui``); Angelo's Restore never
  rebinds a worker chat;
* a worker chat is recorded in the state folder (``REGISTRY``), so after a restart, or once unbound, it refuses
  every tool (``remote_executor.EndedWorker``) instead of following Angelo's own document;
* a message's turn observation comes from the worker (``prepare_turn``).

``start`` is the one entry point (``worker_sessions`` for the lead's ``angelo_workers`` tool and the rail; tests).
``bind`` gives the chat its live scene and session-rail tile (``parallel_sessions``). A done worker's chat is
**parked** (``worker_queue``): its worker stops once its part is saved (``unbind(parked=True)``: its tile keeps showing
how its turn ended, never ended), and a message to it (``deliver``) relaunches a worker on its own part file and binds
the chat again (``adopt(again=True)``), the same document to it.

Every way a chat leaves its worker or is ended (``unbind``, which every ending route calls: a load or a lead whose
document changed, a merge, a brief that could not be sent, a worker that crashed or ended between requests, a done
worker parked; ``stop_all`` as the add-on ends; ``worker_ending.end_parked``) ends the turn it has under way by the
person's Stop's own route (``end_turn``), so no model request runs on for a part that can never merge. Each says why
(``END_*``): a row in the chat, in the person's language, and the calls it cancels; a message of the person's that
was never sent goes back where they type (``unsent``).
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .ui_text import n_   # Marks the text for the catalogues; pure Python.

REGISTRY = "worker-chats.json"   # In the state folder: which chats work on a worker's part, kept across restarts.
SNAPSHOT_NAMESPACE = uuid.UUID("6f2e1c3a-58b4-4d0e-9a1f-7c2b5d8e4a90")
_workers = {}  # chat session id -> WorkerProcess this module started for it
_registries = {}  # state folder -> {chat session id: part path}, read once per folder
_stopping = []   # Threads stopping workers (``stop_later``): ``stop_all`` waits for them as the add-on ends.
STOP_ALL_SECONDS = 15.0   # The add-on's exit waits this long in all for its workers (each stop is bounded too).

# Why a worker chat's turn was ended without the person's Stop (``end_turn``): a notice in its chat, translated when
# drawn, and in English to the calls it cancels. Facts only.
END_REPLACED = n_("This worker ended: the lead's document changed.")   # A load; never another scene shown (R5-M-1).
END_CRASHED = n_("This worker ended: its background Angelo stopped running.")
END_MERGED = n_("This worker ended: its part was merged into the lead's scene.")
END_NOT_BRIEFED = n_("This worker ended: its brief could not be sent.")
END_DONE = n_("This worker ended: its part is done and saved.")
END_CLOSING = n_("This worker ended: Angelo is closing.")
NOT_SENT = n_("Your message wasn't sent. Send it again when you're ready.")


class DeliveryStopped(RuntimeError):
    """A Stop reached the chat while its message was prepared on its lane: the turn never starts (break-it E2E-1
    M-5). The person's Stop, or an ending's row (``end_turn``), says why; the message goes back (``unsent``)."""


def _stops(controller):
    return getattr(controller, "stops", 0)


def _check_stop(controller, stops):
    if _stops(controller) != stops:
        raise DeliveryStopped("Stopped before the message was sent; nothing was sent")


def worker_snapshot_session(session_id):
    """The snapshot-store session a worker chat's snapshots are kept under, in Angelo's own store (so its limits,
    deduplication and protection apply, break-it N3), never the chat's own session, which Restore reads (H1)."""
    return str(uuid.uuid5(SNAPSHOT_NAMESPACE, "angelo-worker:" + session_id))


def registry(directory):
    key = str(Path(directory).resolve())
    if key not in _registries:
        try:
            value = json.loads((Path(key) / REGISTRY).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        _registries[key] = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)} if (
            isinstance(value, dict)) else {}
    return _registries[key]


def _remember(controller, part):
    """Record the chat as a worker chat before it binds, so no restart can turn it into a chat on Angelo's file."""
    directory = Path(controller.store.directory)
    chats = registry(directory)
    chats[controller.state["id"]] = str(part)
    temporary = directory / (REGISTRY + ".%s.tmp" % uuid.uuid4().hex)
    temporary.write_text(json.dumps(chats, sort_keys=True), encoding="utf-8")
    os.replace(temporary, directory / REGISTRY)


class WorkerChatFiles:
    """A worker chat's file workspace: the file tools are refused, nothing ran (break-it L-a). Their backups would go
    into the worker's snapshots, which Restore never reads; everything else (paths, the root) reads through."""

    def __init__(self, files):
        self.files = files

    def __getattr__(self, name):
        return getattr(self.files, name)

    def precondition(self, name, *_arguments):
        from .worker_refusals import NOT_IN_WORKER, nothing_ran as _nothing_ran
        from .refusal_codes import RefusalError
        raise _nothing_ran(RefusalError(NOT_IN_WORKER.format(name=name), code="UNSUPPORTED"))

    execute = precondition


class SealedFiles:
    """A ``Controller``'s ``files``: once sealed (``seal_files``), whatever workspace replaces it is sealed too, so no
    later assignment gives a worker chat the person's folder (break-it E2E-1 M-7: ``ui.prepare_controller``, which
    every message, connection change and voice start calls on the shown chat, assigned a new workspace)."""

    @property
    def files(self):
        return self.__dict__.get("_files")

    @files.setter
    def files(self, value):
        if self.__dict__.get("_files_sealed") and value is not None and not isinstance(value, WorkerChatFiles):
            value = WorkerChatFiles(value)
        self.__dict__["_files"] = value


def seal_files(controller):
    """Wrap the chat's file workspace (``WorkerChatFiles``), now and for every workspace it is given later."""
    controller._files_sealed = True
    files = getattr(controller, "files", None)
    if files is not None and not isinstance(files, WorkerChatFiles):
        controller.files = WorkerChatFiles(files)


def ended_worker(controller):
    """For a worker chat that is not bound (its worker is gone): an executor that refuses everything, else None."""
    if getattr(controller, "chat_host", None) is not None:   # Hosted by this process, its worker (A′, D2): its own
        return None                                          # document, though the lead's registry lists the chat.
    store, state = getattr(controller, "store", None), getattr(controller, "state", None)
    directory = getattr(store, "directory", None)
    if directory is None or not isinstance(state, dict):
        return None
    part = registry(directory).get(state.get("id"))
    if part is None:
        return None
    seal_files(controller)   # A chat rebuilt after a restart gets its workspace sealed here, at its first use.
    from . import worker_queue
    if state["id"] in worker_queue.parked:   # Its worker stopped once its part was done: a message starts it again.
        return worker_queue.ParkedWorker(state["id"], part)
    from .remote_executor import EndedWorker
    return EndedWorker(state["id"], part)


def bound(controller):
    """The chat's ``RemoteExecutor``, or None for a chat on Angelo's own document."""
    return getattr(controller, "remote_executor", None)


def hosted(controller):
    """Whether ``controller`` is the lead's mirror of a chat its worker hosts (design A′, ``worker_mirror``)."""
    return getattr(controller, "hosted_in_worker", False) is True


def worker_snapshots(controller):
    """Whether ``controller`` is a worker chat (bound now, hosted in its worker, or recorded as one: after an unbind
    or a restart)."""
    state, store = getattr(controller, "state", None), getattr(controller, "checkpoints", None)
    return (bound(controller) is not None or hosted(controller) or ended_worker(controller) is not None
            or isinstance(state, dict) and getattr(store, "session_id", state.get("id")) != state.get("id"))


def bind(controller, worker, worker_id, again=False):
    """Make ``controller`` work on ``worker``'s document from now on. ``again``: a parked chat's relaunched worker, on
    its own part file: the chat's document again, so it keeps its work and its conversation (``worker_queue``)."""
    from .checkpoints import CheckpointStore
    from .document_work import holds_document_work
    from .remote_executor import RemoteExecutor
    if bound(controller) is not None or not again and (holds_document_work(controller) or controller.document_invalid):
        # A chat that already worked on a document keeps it (the window's own rule: Start a new chat).
        raise RuntimeError("A worker binds only to a new chat that is not bound to one already")
    executor = RemoteExecutor(worker, worker_id)
    executor.greet()   # Its document key, before the chat binds to it (answered off the main thread at its launch).
    _remember(controller, getattr(worker, "part", worker_id))
    executor.studio_context = controller
    controller.remote_captures = {}
    controller.remote_executor = executor
    seal_files(controller)
    # The worker's snapshots are of its part: never under the chat's own session, which Angelo's Restore reads
    # (also after an unbind or a restart, H1), but in Angelo's store all the same (its limits apply, N3).
    controller.checkpoints = CheckpointStore(controller.checkpoints.root, worker_snapshot_session(controller.state["id"]))
    controller.checkpoint_factory = lambda target=controller: capture(executor, target)
    controller.document_invalid = False
    controller.document = None
    controller.set_document(executor.document_key)
    from . import parallel_sessions
    parallel_sessions.bound(controller, executor)   # Its live scene and its tile on the session rail.
    return executor


def capture(executor, target):
    """A worker chat's request snapshot. Taken on its lane while a message is delivered: a Stop that arrived during it
    ends the delivery before the turn starts (``deliver``; the controller keeps the person's input)."""
    record = executor.capture_checkpoint(target)
    stops = getattr(target, "_delivery_stops", None)
    if stops is not None:
        _check_stop(target, stops)
    return record


def unbind(controller, reason, parked=False):
    """The chat leaves its worker (stopped when this module started it); it no longer edits any document. ``parked``:
    its part is done and saved (``worker_queue``), so its tile shows how its turn ended, never ended. A turn under
    way ends first (``end_turn``, saying ``reason``), as the person's Stop ends it; a Stop that fails is logged and
    the chat still leaves its worker, which still stops (break-it L-2)."""
    try:
        end_turn(controller, reason)
    except Exception as error:   # noqa: BLE001 - never keeps a worker bound or running, nor stops the sweep.
        print("Angelo: a worker chat's turn could not be stopped:", type(error).__name__, error)
    from . import worker_sessions, worker_queue
    worker_sessions.keep_layout(controller.state["id"], getattr(bound(controller), "part_layout", None))
    worker_queue._held.pop(controller.state["id"], None)
    worker_queue._parking.pop(controller.state["id"], None)   # Late save replies cannot revive an ended worker.
    controller.remote_executor = None   # It stays a worker chat (REGISTRY): ended_worker refuses its calls now.
    controller.checkpoint_factory = None
    controller.document_invalid = True
    from . import parallel_sessions
    if not parked:
        parallel_sessions.unbound(controller)   # The tile stays on its last saved part, marked ended.
    from . import worker_dispatch
    worker_dispatch.forget(controller)
    worker = _workers.pop(controller.state["id"], None)
    if worker is not None:
        stop_later(worker)


def end_turn(controller, reason):
    """Main thread: end the turn ``controller`` has under way by the person's Stop's own route (``Controller.stop``):
    its provider request is stopped, the calls it cancels are told ``reason``, a message being prepared on its lane is
    never sent (``DeliveryStopped``, handed back by ``unsent``), and its request context closes as the turn ends
    (``RuntimeContext.emit``'s ``finished``). The chat says ``reason`` (an ``END_*`` notice). A turn already ended
    (completed, failed, a done worker parked) keeps how it ended, even while its thread closes its provider (break-it
    L-1). Every route that ends a worker calls it (module docstring): the maintainer's audit (P1) found a document
    replacement that stopped the workers but left their chats' model requests running, busy and never cancelled. True
    when a turn was stopped."""
    running = (getattr(controller, "busy", False) is True   # Set from a turn's start until its end is handled.
               or isinstance(getattr(controller, "_delivery_stops", None), int))   # A message prepared on its lane.
    if not running or not callable(getattr(controller, "stop", None)):
        return False
    from .ui_text import notice
    controller.stop(reason=reason + " No operation was applied.")
    controller.emit("message", {"role": "system", **notice(reason)})
    return True


def unsent(controller, message, error):
    """Main thread: ``on_error`` of the person's ``message`` to a worker chat (``console.execute``). A message its lane
    never sent (a Stop, or its worker ended first: ``DeliveryStopped``, ``worker_dispatch.WorkerGone``) goes back where
    they type, as a message the agent never got does (``Controller.unsent_draft``), never lost. True when nothing else
    needs showing; False for any other error, and for ``WorkerGone``, whose own row says nothing was sent."""
    from .worker_dispatch import WorkerGone
    if not isinstance(error, (DeliveryStopped, WorkerGone)):
        return False
    if not getattr(controller, "unsent_draft", None):
        controller.unsent_draft = message
    if isinstance(error, WorkerGone):
        return False
    from .ui_text import notice
    controller.emit("message", {"role": "system", **notice(NOT_SENT)})
    return True


def detach(controller):
    """The chat leaves its worker as ``unbind`` makes it leave (its layout kept, its tile on its last save, its calls
    refused from now on), but the worker keeps running: its process is returned for another part
    (``worker_pool.reuse``), never stopped here; None when this module did not start it."""
    worker = _workers.pop(controller.state["id"], None)
    unbind(controller, END_DONE, parked=True)   # Done and saved: its tile shows how its turn ended, never ended.
    return worker


def stop_later(worker):
    """Stop ``worker`` on a thread of its own: a stop waits for the worker to exit (seconds when it is slow), and
    Blender's main thread never waits for it (break-it E2E-2 M1: a merge stopped its workers inside the Undo
    operator, one after another)."""
    thread = threading.Thread(target=worker.stop, daemon=True, name="AngeloWorkerStop")
    _stopping[:] = [item for item in _stopping if item.is_alive()] + [thread]
    thread.start()


def start(controller, part, **options):
    """Start a worker Blender on ``part`` (a .blend; made empty if missing, its folder too) and bind ``controller``."""
    worker = launch(part, **options)
    ready(worker)
    return adopt(controller, worker)


def ready(worker):
    """Off the main thread, before a chat binds to it: ``worker`` started, and its first answer (``hello``: its document
    key) kept, so binding sends nothing and a main-thread step (a status wait's) never waits on a worker that stalls
    after ready (break-it OVH4-M-1; ``RemoteExecutor.greet`` takes it)."""
    from .remote_executor import READ_SECONDS
    answer = worker.start()
    worker.hello = worker.request("hello", READ_SECONDS)
    return answer


def launch(part, **options):
    """A worker for ``part``, not started yet: ``WorkerProcess.start`` (stdlib only) may then run on any thread, and
    ``adopt`` binds it on Blender's main thread (``worker_sessions`` starts workers this way, never waiting)."""
    from .worker_process import WorkerProcess, default_binary, default_script
    Path(part).parent.mkdir(parents=True, exist_ok=True)   # A worker saving into a missing folder cannot start.
    options.setdefault("binary", default_binary())   # Read here, on the main thread (bpy), never on a worker thread.
    options.setdefault("script", default_script())
    return WorkerProcess(part, **options)


def adopt(controller, worker, again=False, defer=False):
    """Bind ``controller`` to ``worker``, which is ready; the worker is stopped if the binding fails. ``again``: a
    parked chat's relaunch (``bind``). With A' the chat then opens in the worker, ``resume`` when ``again``; one that
    cannot open leaves it as every ending does (``worker_chat_lifecycle.opened``). ``defer`` (the main thread): the
    open is sent off it and ended by ``finish_opens`` (``when_open`` waits for it)."""
    try:
        executor = bind(controller, worker, "w%d" % worker.process.pid, again)
    except BaseException:
        stop_later(worker)
        raise
    _workers[controller.state["id"]] = worker
    from . import worker_chat_lifecycle
    worker_chat_lifecycle.opened(controller, worker, resume=again, defer=defer)   # A': opens in its worker (C10).
    return executor


def stop_all():
    from . import worker_dispatch, worker_pool, worker_queue
    controllers = getattr(worker_queue._ui(), "_controllers", {})
    for session in list(_workers):   # Their chats' turns end as every ending's does (``end_turn``).
        try:
            end_turn(controllers.get(session), END_CLOSING)
        except Exception as error:   # noqa: BLE001 - every worker still stops as the add-on ends.
            print("Angelo: a worker chat's turn could not be stopped:", type(error).__name__, error)
    worker_dispatch.close_all()   # No lane runs a chat's code once its worker is going.
    worker_pool.release_all()   # Workers launched ahead and never taken stop too.
    for session in list(_workers):   # In parallel: each closes its hosted chats (A′), bounded, at the same time.
        stop_later(_workers.pop(session))
    deadline = time.monotonic() + STOP_ALL_SECONDS
    for thread in list(_stopping):   # The add-on ends: no worker it started outlives it, and all wait one bound.
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    _stopping.clear()


def deliver(controller, window, message, send, on_error):
    """Prepare a turn for ``message`` and ``send(turn)`` it, then acknowledge the observation (``console.execute``'s
    order). A worker chat does it on its lane (``worker_dispatch``): the worker may still be answering an earlier
    call, and Blender's main thread never waits for it. ``on_error(error)`` runs on the main thread either way. A
    Stop that reaches the chat before its turn starts (while its lane waits for the worker) ends the delivery:
    nothing is sent (``DeliveryStopped``). A parked chat's message waits for its relaunched worker (``worker_queue``)."""
    from . import live_target, worker_queue
    held, had_window = live_target.window_ref(window), window is not None   # It may run ticks later (SW-M-2).
    if not hosted(controller) and worker_queue.resume(   # A hosted chat's Send relaunches it (worker_mirror_park).
            controller, lambda: deliver(controller, held(), message, send, on_error), on_error):
        return None
    stops = _stops(controller)

    def run(window):
        _check_stop(controller, stops)
        controller._delivery_stops = stops
        try:
            turn = prepare_turn(controller, window, message)
            _check_stop(controller, stops)
            from . import worker_facts
            worker_facts.turn_started(controller)   # A worker chat's turn clock and request count (E2E-20 L-6).
            send(turn)
        finally:
            controller._delivery_stops = None
        turn.commit()   # Only a delivered observation is a planning boundary for the change feed.
    if hosted(controller):   # The worker observes its own document and sends the message itself (A′, C2).
        try:
            return run(None)
        except Exception as error:   # noqa: BLE001 - reported as the lane's are.
            return on_error(error)
    if bound(controller) is None:
        try:
            live = held()   # On the main thread, live (live_target): never a local window read on a lane.
            if had_window and live is None:
                raise RuntimeError("The window this message was sent from closed (or another file was opened); nothing was sent")
            return run(live)
        except Exception as error:   # noqa: BLE001 - reported as the lane's are.
            return on_error(error)
    from . import worker_dispatch   # A bound chat's turn is the worker's document: no local window (break-it SW2-H-2).
    worker_dispatch.submit(controller, lambda: run(None), on_error)


def prepare_turn(controller, window, message):
    """``turn_context.prepare`` for a message to ``controller``: on the worker's document when it is bound."""
    if hosted(controller):
        from .worker_mirror import HostedTurn
        return HostedTurn()
    executor = bound(controller) or ended_worker(controller)   # An ended worker chat refuses: nothing to observe.
    if executor is not None:
        return executor.prepare_turn(None, controller.state["id"], message=message)
    from . import turn_context
    return turn_context.prepare(window, controller.state["id"], message=message)
