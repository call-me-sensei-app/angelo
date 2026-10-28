# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's side of a worker chat hosted in its worker Blender (design A′): where the lanes' seams meet.

Per worker (``attach``, from ``WorkerProcess._launch``) its connection gets one event router (``on_event``: each
chat's frames to its mirror, ``worker_mirror.EventRouter``; usage and telemetry written here once,
``worker_usage.apply``; a part save to the bound executor's live layout, a failed one to each chat it hosts,
``worker_part_failures``) and one runtime service (``on_reverse``:
the model runtimes Angelo runs for that worker's chats, ``runtime_service``). The service closes when the connection
ends, which ends every child it runs.

Per chat, before its ``chat.open`` (``prepare``, from ``worker_chat_lifecycle.open_chat``): the chat must be a
mirror (``worker_sessions._new_chat`` makes one), its actions go to its worker (``send_op``), its frames reach it,
its file is handed to the worker (``worker_journal_owner.hand_off``) and its standing consent is registered
(``worker_consent_lead.register``): the lease the open carries. When the worker no longer writes the file
(``worker_chat_lifecycle.HANDOFF``) the chat is handed back: after ``chat.close`` (``close_answered``), or after
its process ended (``worker_ended``: the file reopened as after a crash, every call that was admitted but did not
finish ``interrupted``, the mirror's turn ended from the file). A worker that ended on its own
(``WorkerProcess.watch``) also leaves its binding as a dead worker's does (``CRASHED``, on the main thread:
``RemoteExecutor._died``), without waiting for Angelo to ask it something. A turn still running when ``chat.close`` was answered
ends the same way (``worker_mirror_opens.closed``). Each open starts a new run of the chat's frames in its mirror
(C9: ``open_id``, ``worker_mirror_opens``).

The consent lane's seams are bound here: its chat ops go to the chat's worker, and its kill ends the chat's model
children in that worker's runtime service, which works however wedged the worker is. Standard library only, no bpy.
"""

import threading
from pathlib import Path

from . import worker_chat_lifecycle as lifecycle

OP_SECONDS = 5.0   # A mirror's chat op (send, options, notice): the worker answers between two pumps.
CRASHED = "worker_crashed"   # The lead's own mirror event (a ``worker_`` kind no worker frame can pose as).

_lock = threading.Lock()
_chats = {}   # session -> (mirror, worker) from its prepare until it is handed back


class NotMirrored(lifecycle.ChatOpFailed):
    pass


def attach(worker):
    """The ``worker_channel.Client`` keywords for ``worker``'s connection: its event router and runtime service."""
    from .runtime_service import RuntimeService
    from .worker_mirror import EventRouter, LEAD_KINDS
    LEAD_KINDS.setdefault(CRASHED, crash_mark)
    worker.chat_router = EventRouter()
    process = getattr(worker, "process", None)
    worker.runtime = RuntimeService(chats=lambda session: launch_chat(worker, session),
                                    worker_pid=getattr(process, "pid", None), hosted=lambda: lifecycle.hosted_on(worker),
                                    refused=lambda session: launch_refusal(worker, session))
    return {"on_event": lambda body: route(worker, body), "on_reverse": worker.runtime.handle}


def ended(worker):
    """``worker``'s connection ended: every model child Angelo runs for it ends too (C5)."""
    from .worker_part_failures import saved
    saved(worker)   # Its failure episode, if any, is forgotten with it.
    runtime = getattr(worker, "runtime", None)
    if runtime is not None:
        runtime.close()


def route(worker, body):
    """The channel's reader thread: one ``{"event"}`` body of ``worker``."""
    from . import worker_usage
    kind = body.get("kind") if isinstance(body, dict) else None
    if kind in (worker_usage.USAGE, worker_usage.TELEMETRY):
        directory = getattr(worker, "chat_state", None)
        if directory is not None:   # Written once, in Angelo's own state folder, never one the worker names (C8).
            worker_usage.apply(body, directory)
        return
    if kind == "part_saved" and body.get("session") is None:
        _part_saved(worker, body.get("data"))
        return
    if kind == "part_save_failed" and body.get("session") is None:   # Every chat it hosts says so (audit #6).
        from .worker_part_failures import failed
        failed(worker, body.get("data"), lifecycle.hosted_on(worker), _mirror)
        return
    router = getattr(worker, "chat_router", None)
    if router is not None:
        router(body)


def _part_saved(worker, data):
    """The worker saved its part after an edit: its live view reads it again (``RemoteExecutor.note_layout``), and its
    reported object count is kept for that version (``worker_part_counts.note``)."""
    if not isinstance(data, dict) or type(data.get("revision")) is not int:
        return
    from .worker_part_failures import saved
    saved(worker)
    from . import worker_part_counts   # Its count, trusted only for the file version it names (``note``).
    worker_part_counts.note(worker, data.get("part"))
    for session in lifecycle.hosted_on(worker):
        mirror = _mirror(session)
        executor = getattr(mirror, "remote_executor", None)
        if executor is None:
            continue
        layout = data.get("layout")
        if layout is None and executor.part_layout:
            layout = dict(executor.part_layout, revision=data["revision"])
        executor.note_layout(layout)


def _mirror(session):
    with _lock:
        entry = _chats.get(session)
    return entry[0] if entry is not None else None


def key_of(mirror):
    """The chat's credential from Angelo's own keys (the window's lookup) for the route Angelo pinned
    (``worker_mirror_pins.route``), never from the worker or its file; "" when it has none."""
    from .worker_mirror_pins import route
    pinned = route(mirror)
    try:
        from . import ui
    except ImportError:
        return ""
    if pinned is None:
        return ""
    keys = getattr(ui, "_memory_keys", {})
    return keys.get(pinned[1], keys.get(pinned[0], "")) or ""


def launch_chat(worker, session):
    """``RuntimeService``'s ``chats``: what Angelo knows of ``session`` when ``worker`` hosts it, else None. Its
    resumable threads are Angelo's own record (``runtime_threads``), never the worker-written ``native_id``; a turn's
    local images may be in the chat's references in the worker's state folder or in the store it opened. Its mode,
    connection and key are the route Angelo pinned (``worker_mirror_pins``), never the worker-written file; a chat
    whose worker reported another one, or whose connection is no longer usable (removed, withdrawn, refused by
    policy: ``worker_consent_lead._connected``), launches nothing."""
    import uuid
    from .runtime_service import LaunchChat
    from .worker_consent_lead import _connected
    from .worker_mirror_pins import route
    if not isinstance(session, str) or lifecycle.hosted(session) is not worker:
        return None
    mirror = _mirror(session)
    pinned = route(mirror) if mirror is not None else None
    if pinned is None or not _connected(mirror):
        return None
    try:
        name = str(uuid.UUID(session))
    except ValueError:
        return None
    references = [Path(mirror.store.directory) / "references" / name]
    if getattr(worker, "private", None):
        from .worker_process import references_of
        references.append(references_of(worker.private, session))
    return LaunchChat(mode=str(pinned[0] or ""), workspace=str(mirror.workspace),
                      store_directory=str(mirror.store.directory), settings=dict(mirror.settings or {}),
                      api_key=key_of(mirror), connection_id=str(pinned[1] or ""),
                      references=tuple(str(path) for path in references))


def launch_refusal(worker, session):
    """Why ``launch_chat`` launches nothing for ``session``, as a support code (``worker_reasons``): a chat ``worker``
    hosts on a pinned connection that is no longer usable, or one it does not host (or is not open in it)."""
    from .worker_consent_lead import _connected
    from .worker_mirror_pins import route
    mirror = _mirror(session) if isinstance(session, str) and lifecycle.hosted(session) is worker else None
    if mirror is not None and route(mirror) is not None and not _connected(mirror):
        return "WRK-NO-CONNECTION"
    return "WRK-NOT-OPEN"


def catalog(controller, lead):
    """``worker_sessions._new_chat``: the new worker chat's models. Off: loaded as any chat's
    (``refresh_models``). With A′ no listing starts for a chat that closes at once: the catalog a chat of Angelo's
    already loaded for the same connection (``lead_catalog``), else none, and the worker then asks Angelo for its own
    (``WorkerChatHost._open``) and forwards it to the mirror as ``models``."""
    if not lifecycle.enabled():
        return controller.refresh_models()
    controller.models = lead_catalog(controller, lead)


def lead_catalog(controller, lead):
    """The models a chat of Angelo's (the lead first) loaded for ``controller``'s connection, or [] (none loaded,
    still loading, failed, or a connection change waiting)."""
    try:
        from . import ui
        chats = [lead] + list(getattr(ui, "_controllers", {}).values())
    except ImportError:
        chats = [lead]
    route = (controller.state.get("provider"), controller.state.get("connection_id"))
    for chat in chats:
        if (getattr(chat, "hosted_in_worker", False) is not True and getattr(chat, "models", None)
                and (chat.state.get("provider"), chat.state.get("connection_id")) == route
                and not getattr(chat, "models_loading", False) and not getattr(chat, "models_error", "")
                and getattr(chat, "_next_binding", None) is None):
            return [dict(model) for model in chat.models]
    return []


def mirror_of(controller, lead):
    """``worker_sessions._new_chat`` with A′ on: the new worker chat's mirror, made from its saved file once the chat
    made in Angelo (its connection's settings and models read with Angelo's own keys) is closed; its trust follows
    ``lead``'s as the chat's did. Off: ``controller`` itself."""
    if not lifecycle.enabled():
        return controller
    from . import worker_trust
    from .worker_mirror import MirrorController
    settings, models = dict(controller.settings or {}), list(getattr(controller, "models", None) or ())
    controller.close()   # Its file stays as it saved it: the worker opens that file (C7).
    mirror = MirrorController(str(controller.store.directory), controller.state["id"], settings=settings)
    mirror.models = models
    mirror.delegation_resolver = getattr(controller, "delegation_resolver", None)
    worker_trust.link(lead, mirror)
    return mirror


def send_op(worker):
    """A mirror's ``send_op``: its chat op to ``worker``, bounded; the answer, or ``{"error", "timed_out", "gone"}``
    (never raises) where ``error`` is the worker's own encoded record when it refused (``decode_error``), else the
    lead's reason. An op with a ``request_id`` is asynchronous (``worker_mirror_ops``: nobody waits on it), so its
    answer may take as long as the worker needs (``ANSWER_SECONDS``)."""
    def send(session, op, **fields):
        from .worker_mirror_ops import ANSWER_SECONDS
        timeout = ANSWER_SECONDS if fields.get("request_id") else OP_SECONDS
        try:
            return lifecycle.send_op(session, op, worker=worker, timeout=timeout, **fields)
        except lifecycle.ChatOpFailed as error:
            return {"error": error.record if error.record is not None else error.reason,
                    "timed_out": error.timed_out, "gone": error.gone}
    return send


def prepare(controller, worker):
    """Before ``controller``'s ``chat.open`` on ``worker`` (module docstring): the consent lease the open carries.
    ``NotMirrored`` (fail closed) for a chat that is not a mirror: a chat that runs its own provider never opens in
    a worker too."""
    from . import worker_consent_lead, worker_journal_owner
    from .worker_mirror import hosted
    session = controller.state["id"]
    if not hosted(controller):
        raise NotMirrored("chat.open", session, "only a worker-hosted chat's mirror opens in a worker")
    try:   # Another worker still writing this chat's file refuses the open (C7: one writer).
        worker_journal_owner.hand_off(session, ended=lambda: lifecycle._process_ended(worker))
    except worker_journal_owner.JournalTaken as error:
        raise lifecycle.ChatOpFailed("chat.open", session, str(error)) from None
    with _lock:
        _chats[session] = (controller, worker)
    worker.chat_state = str(controller.store.directory)
    controller.send_op = send_op(worker)
    from . import worker_mirror_opens   # C9: after every frame of its earlier opens, a new one starts.
    worker_mirror_opens.announce(controller)   # Its ``open_id``, which the ``chat.open`` carries.
    worker.chat_router.open(controller)
    _bind_consent()
    _prewarm_shared(worker, controller)
    return worker_consent_lead.register(controller)


def _prewarm_shared(worker, mirror):
    """A ChatGPT plan chat runs on its connection's shared app-server, which a window's chat leases already running
    and a worker's prewarm does not start (``CodexProvider.start_prewarm``): its process starts here as the chat
    opens (``RuntimeService.prewarm``, off the main thread), and the worker's first launch adopts it. Its other
    runtimes are prewarmed by the worker's own chat (``WorkerChatHost._warm``)."""
    from .codex_runtime import ELIGIBLE_MODES
    from .worker_mirror_pins import route
    runtime, pinned = getattr(worker, "runtime", None), route(mirror)
    if runtime is None or pinned is None or pinned[0] not in ELIGIBLE_MODES:
        return
    request = {"session": mirror.state["id"], "provider_kind": "codex", "workspace": str(mirror.workspace),
               "text_only": getattr(mirror, "text_only", False) is True}

    def run():
        try:
            runtime.prewarm(request)
        except Exception as error:  # noqa: BLE001 - only a head start: the worker's launch starts its own.
            print("Angelo: a worker chat's runtime prewarm did not start:", type(error).__name__)
    threading.Thread(target=run, daemon=True, name="AngeloWorkerRuntimePrewarm").start()


def handed_back(session, closed):
    """``worker_chat_lifecycle.HANDOFF``: the worker no longer writes ``session``'s file (module docstring)."""
    from . import worker_consent_lead, worker_journal_owner
    with _lock:
        mirror, worker = _chats.pop(session, (None, None))
    if worker is None:
        return   # Never handed off from here (its open was refused before): another writer's file is left alone.
    opened = getattr(mirror, "announced_open", None)   # The open handed back: a relaunch's announce comes after.
    worker_consent_lead.forget(session)
    worker.chat_router.close(session)
    mirror.send_op = None
    if not worker_journal_owner.hosted(session):
        return
    from . import worker_mirror_events as events   # Applied on the main thread with the chat's other frames.
    if closed:   # C2: a turn its worker did not end before answering ends here, as after a crash.
        worker_journal_owner.close_answered(session)
        events.put(mirror, events.CLOSED, {"directory": str(mirror.store.directory),   # With the turn it found.
                                           "generation": getattr(mirror, "_request_generation", None),
                                           "open_id": opened})
        return
    recovered = worker_journal_owner.worker_ended(session, str(mirror.store.directory), pending=list(mirror.pending))
    events.put(mirror, events.RECOVERED, {"state": recovered.state, "finished": recovered.finished,
                                          "generation": getattr(mirror, "_request_generation", None),
                                          "open_id": opened})
    why = getattr(worker, "ended_on_its_own", None)
    if why:   # Never a stop of Angelo's: the route that stopped it already decided how its binding ends.
        events.put(mirror, CRASHED, {"worker": worker, "why": why})


def crash_mark(mirror, data):
    """Main thread (the mirror's pump, after ``RECOVERED``): the chat bound to the worker that ended on its own leaves
    it as a chat whose worker died does when it next asks (``RemoteExecutor._died``: the death recorded as a failure,
    its session and slot ended, ``END_CRASHED`` said). A binding to another worker is left alone."""
    executor = getattr(mirror, "remote_executor", None)
    if executor is None or getattr(executor, "worker", None) is not data.get("worker"):
        return False
    executor.document_key = ("angelo-worker", executor.worker_id, "ended")
    executor._died("The worker ended on its own: %s" % data.get("why"))
    return True


def _consent_send(session, request, timeout):
    worker = lifecycle.hosted(session)
    if worker is None:
        raise lifecycle.ChatOpFailed(request.get("op"), session, "its chat is not open in a worker", sent=False)
    fields = {key: value for key, value in request.items() if key not in ("op", "session")}
    return worker.request(request["op"], timeout, session=session, **fields)


def kill(session):
    """C5: end every model child Angelo runs for ``session``, whatever its worker is doing."""
    with _lock:
        entry = _chats.get(session)
    runtime = getattr(entry[1], "runtime", None) if entry is not None else None
    if runtime is not None:   # A closed chat's app-server its worker's pool reused for this chat included (C10).
        runtime.kill_session(session)


def _bind_consent():
    """The consent lane's transport and kill (unless already bound, as a test's fakes are)."""
    from . import worker_consent_lead
    if worker_consent_lead._hooks["send"] is None:
        worker_consent_lead.bind(send=_consent_send, kill=kill)


lifecycle.HANDOFF.append(handed_back)
