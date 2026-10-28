# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's model runtimes for the chats its worker Blenders host: launch, probe, kill and prewarm (design A').

A worker chat's controller and provider Python run in its worker; the model runtime child and every credential stay
here (doc/angelo/parallel_agents.rst, "Worker chats host their own controller"). The worker asks over its channel
(``worker_channel``, ``{rseq, request}``) and ``RuntimeService.handle`` answers:

* ``runtime.launch`` ``{session, provider_kind, workspace, arguments, session_options, text_only}``: the command and
  environment are built here with the same code a chat in the window uses (``runtime_launch.codex_command``,
  ``child_environment``, ``profile_environment``, ``runtime_environment.session_environment`` and, for Claude,
  ``claude_transport.claude_arguments`` from the worker's arguments, which must be exactly ones it builds), from
  Angelo's own settings and key for the chat's connection. Nothing the worker sends names a program, a flag outside
  that grammar or an environment variable. A Codex API-key chat is signed in here (``initialize``, then
  ``account/login/start``) before the relay opens; the worker's own ``initialize`` is then answered from that one.
  The answer is ``{ok, relay, pid, warm}``: the relay is a connection of its own (``runtime_child``). A chat has at
  most ``SESSION_CHILDREN`` live children (a new launch beyond that ends its oldest) and a worker at most
  ``WORKER_CHILDREN`` (a launch beyond that is refused).
* ``runtime.probe`` ``{session, what}``: ``coding_models``, ``check_connection``, ``list_models`` (a team or
  external connection's catalog) or ``cache_affinity`` (the account's keyed digest, never the secret: a personal
  one keyed by the account the chat's own child named to ``account/read``, never an identity the worker sends).
  Only non-secret results return.
* ``runtime.kill`` ``{session, pid}``: ends that chat's child at once, whatever its worker is doing.

``chats(session)`` names what Angelo knows of a chat this worker hosts (``LaunchChat``), or None: a session the
worker does not host is refused; ``hosted()`` names the chats it hosts. Each child is bound to its chat's connection,
mode and key (``runtime_bindings.binding``: the key's fingerprint is Angelo's alone). A child whose chat this worker
no longer hosts (a closed chat's app-server, kept by the worker's Codex pool) serves only a hosted chat of the same
binding: the next request or line claims it for that chat (``_serves``), so Stop's ``kill_session`` and the worker's
``runtime.kill`` reach it; one no hosted chat can claim is refused every line but an interrupt, and ends at the next
request. A child whose own chat's binding changed (another key present now: ``runtime_bindings.matches``, where a key
Angelo does not hold this moment is unknown, never a mismatch) ends too, and a connection removed, re-keyed, withdrawn
or signed out ends all of its children at once (``runtime_bindings.end_connection``). The threads a chat may resume are
the ones Angelo saw its children open (``runtime_threads``), and a turn's local images must be in the chat's
reference folders (``LaunchChat.references``), read here and sent inline (``runtime_filter``). A chat starts at most
``runtime_bindings.LAUNCH_BURST`` children a minute. ``close`` (the worker ended) and Angelo's own exit end every
child, and ``close``, Stop's ``kill_session`` and a connection's ``end`` also cancel a launch or prewarm at any stage
for what they end: each is registered before its plan is built, checked after the plan, after the spawn and before it
is answered or kept warm, and refused (its child ended) once an end or its chat's close reached it. Lines to the
worker are redacted of every key Angelo holds (``secrets``).

Standard library only and no ``bpy``.
"""

import atexit
import os
import socket
import subprocess
import sys
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import runtime_threads, worker_channel
from .protocol import RpcError
from .runtime_bindings import Churn, binding, matches
from .runtime_child import RuntimeChild
from .runtime_filter import Filter
from .runtime_plan import CODEX_MODEL_PROVIDER, Plans
from .runtime_probes import Probes

RELAY_LIMIT = 64 * 1024 * 1024   # One relayed line: a tool result with its pictures fits; a larger one ends the relay.
PREWARM_SECONDS = 600.0          # A prewarmed child no launch adopted within this ends.
SESSION_CHILDREN = 3   # A chat's live children: its app-server, a model-catalog listing and a prewarm at most.
WORKER_CHILDREN = 4    # A worker's: its one chat's, and a closed chat's app-server its pool still holds.


@dataclass
class LaunchChat:
    """What Angelo knows of a hosted chat: its connection mode, settings and key, and its folders."""
    mode: str
    workspace: str
    store_directory: str
    settings: dict = field(default_factory=dict)
    api_key: str = ""
    connection_id: str = ""
    references: tuple = ()   # The folders a turn's local image may be in: the chat's reference stores.


@dataclass(eq=False)
class _Launch:
    """One launch or prewarm while it runs: compared, found and removed by identity alone, since two launches for one
    chat hold equal fields (external audit 3 #1: removed by equality, one could drop the other's record)."""
    session: str
    binding: tuple
    cancelled: bool = False


def _log(text):
    print(text, file=sys.stderr)   # English log lines, never shown.


def _spawn(command, env, cwd):
    """``cli_runtime.launch`` without its relay seam: Angelo's own child, in its own process group."""
    from . import child_process
    from .cli_runtime import runtime_command
    return child_process.popen(runtime_command(command, env), env=env, cwd=cwd, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               bufsize=1, start_new_session=os.name != "nt")


def _angelo_keys():
    """Every key Angelo holds in memory (the window's ``ui._memory_keys``), never imported here: none when unloaded."""
    ui = sys.modules.get(__package__ + ".ui")
    keys = getattr(ui, "_memory_keys", None)
    return [key for key in keys.values() if isinstance(key, str)] if isinstance(keys, dict) else []


def _executable(kind, settings):
    from .cli_runtime import find_executable
    return find_executable(kind, settings.get(kind + "_path", ""))


class Refused(RpcError):
    """No runtime for the chat a request names: its English reason (``worker_reasons``), and its support code, which
    crosses to the worker with it (``worker_channel``) so the chat's row says it translated (``runtime_relay``)."""

    def __init__(self, support):
        from .worker_reasons import BY_SUPPORT, NOT_OPEN, SUPPORT
        chosen = BY_SUPPORT.get(support, NOT_OPEN)
        self.support = SUPPORT[chosen]
        from .runtime_relay import NOT_STARTED
        super().__init__(NOT_STARTED.format(why=chosen, code=self.support))


class RuntimeService(Plans, Probes):
    def __init__(self, chats, worker_pid=None, platform=None, spawn=_spawn, executable=_executable, log=_log,
                 win32=None, secrets=_angelo_keys, hosted=None, threads=None, churn=None, refused=None):
        """``hosted()``: the chats the worker hosts now; ``threads(chat)``: its ``runtime_threads.ThreadRecord``;
        ``refused(session)``: why ``chats`` gave nothing for it, as a ``worker_reasons`` support code."""
        self.chats, self.worker_pid, self.platform = chats, worker_pid, platform or sys.platform
        self.refused = refused or (lambda session: "WRK-NOT-OPEN")
        self.hosted, self.churn = hosted, churn or Churn()
        self.threads = threads or (lambda chat: runtime_threads.record(chat.settings.get("auth_directory")))
        self.spawn, self.executable, self.log, self.win32 = spawn, executable, log, win32
        self.secrets = secrets
        self._lock = threading.Lock()
        self._children = {}      # pid -> RuntimeChild, in the order they started
        self._starting = 0       # Launches admitted and still starting (counted against ``WORKER_CHILDREN``)
        self._launching = []     # Their ``_Launch``: an end while one spawns ends it too (audit #3)
        self._warm = None        # (key, child, deadline): one prewarmed child at most
        self.closed = False
        _SERVICES.add(self)

    # Requests ----------------------------------------------------------------------------------------------------

    def handle(self, request):
        """One worker request's answer (``worker_channel.Client(on_reverse=...)``)."""
        op = request.get("op")
        chat = self.chats(request.get("session")) if isinstance(request.get("session"), str) else None
        if chat is None:
            raise Refused(self.refused(request.get("session")))
        try:
            if op == "runtime.launch":
                return self.launch(request, chat)
            if op == "runtime.probe":
                return self.probe(request, chat)
            if op == "runtime.kill":
                return self.kill(request)
        except Exception as error:   # Its words cross to the worker: never with the key.
            raise RpcError(self._redact(chat, str(error))) from None
        raise RpcError("Unknown runtime request")

    def launch(self, request, chat):
        with self._launch(request["session"], chat) as launch:
            planned = self.plan(request, chat)
            self._live(launch)
            self._claim(request["session"])
            child, warm = self._adopt(planned["key"]), True
            if child is None:
                child, warm = self._start(planned, chat, launch), False
            self._live(launch, child)
        relay, handle = self._relay(child)
        response = {"ok": True, "relay": relay, "pid": child.pid, "warm": warm, "kind": planned["kind"]}
        if planned.get("runtime") is not None:   # The Codex launched, as Angelo resolved it (``agent_runtime.launched``).
            response["runtime"] = planned["runtime"]
        return (response, handle) if handle is not None else response

    def prewarm(self, request):
        """Start ``request``'s child ahead of its ``runtime.launch`` (same request); the launch adopts it."""
        chat = self.chats(request.get("session"))
        if chat is None or self.closed:
            return False
        try:
            with self._launch(request["session"], chat) as launch:
                planned = self.plan(request, chat)
                self._live(launch)
                self._claim(request["session"])
                child = self._start(planned, chat, launch)
                with self._lock:   # Kept warm only if nothing ended its launch meanwhile (``_live``).
                    cancelled = self._ended(launch)
                    previous, self._warm = ((None, self._warm) if cancelled else
                                            (self._warm, (planned["key"], child, time.monotonic() + PREWARM_SECONDS)))
                if cancelled:
                    child.kill()
                    return False
        except RpcError:
            return False
        if previous is not None:
            previous[1].kill()
        timer = threading.Timer(PREWARM_SECONDS, self._expire, args=(child,))
        timer.daemon = True
        timer.start()
        return True

    def kill(self, request):
        pid = request.get("pid")
        self._claim(request.get("session"))
        with self._lock:
            child = self._children.get(pid) if type(pid) is int else None
        if child is None or child.session != request.get("session"):
            return {"ok": True, "found": False}
        child.kill()
        return {"ok": True, "found": True}

    def kill_session(self, session):
        """Stop, or a revocation the worker did not acknowledge: end every child serving ``session`` at once, a
        closed chat's app-server the worker's pool reused for it included; how many ended."""
        self._claim(session)
        with self._lock:
            children = [child for child in self._children.values() if child.session == session]
            if self._warm is not None and self._warm[1].session == session:
                self._warm = None   # Ended with the rest: never adopted.
            self._cancel(lambda launch: launch.session == session)
        for child in children:
            child.kill()
        return len(children)

    def _claim(self, session):
        """A request for ``session``: each child serves the hosted chat of its binding now, and a child that can
        serve none ends (module docstring)."""
        if not isinstance(session, str) or self.chats(session) is None:
            return
        candidates = list(self.hosted()) if self.hosted is not None else [session]
        with self._lock:
            children = list(self._children.values())
        ending = [child for child in children if self._serves(child, candidates) is None
                  and (self.hosted is not None or self.chats(child.session) is not None)]
        for child in ending:
            self.log("Angelo: a worker's model runtime serves no chat of its connection; it is stopped")
            child.kill()

    def _serves(self, child, candidates=None):
        """The hosted chat ``child`` serves now, or None: its own chat while its binding holds, else (its chat
        closed) the first of ``candidates`` (the hosted chats) with its binding, which claims it."""
        session = child.session
        chat = self.chats(session)
        if chat is not None:
            return session if matches(chat, child.binding) else None
        if candidates is None:
            candidates = list(self.hosted()) if self.hosted is not None else []
        for other in candidates:
            found = self.chats(other) if other != session else None
            if found is not None and matches(found, child.binding):
                with self._lock:
                    if child.session == session:
                        child.session = other
                        child.filter.reassigned()   # Its threads were its first chat's (``Filter.reassigned``).
                return other
        return None

    def end(self, matches):
        """End every child whose binding ``matches`` (``runtime_bindings.end_connection``); how many ended."""
        with self._lock:
            ending = [child for child in self._children.values() if matches(child.binding)]
            warm = self._warm
            if warm is not None and matches(warm[1].binding):
                self._warm = None
            self._cancel(lambda launch: matches(launch.binding))
        for child in ending:
            child.kill()
        return len(ending)

    def _cancel(self, which):
        """Under the lock: every launch an end reaches, at any stage (planning, spawning, signing in), is refused at
        its next check (``_live``) and its child, if any, ends (audit #3, audit2 #3)."""
        for launch in self._launching:
            launch.cancelled = launch.cancelled or bool(which(launch))

    @contextmanager
    def _launch(self, session, chat):
        """A launch or prewarm, cancellable from the moment it is asked: registered before its plan, under the lock
        ``close``, ``kill_session`` and ``end`` take, and kept until it returned."""
        with self._lock:
            if self.closed:
                raise RpcError("Angelo is closing its worker's runtimes")
            launch = _Launch(session, binding(chat))
            self._launching.append(launch)
        try:
            yield launch
        finally:
            with self._lock:
                self._launching.remove(launch)

    def _ended(self, launch):
        """Under the lock: whether ``launch`` may not go on (an end reached it, or its chat is no longer hosted)."""
        return launch.cancelled or self.closed or self.chats(launch.session) is None

    def _live(self, launch, child=None):
        """Between a launch's stages: refused (``child`` ended) once an end reached it."""
        with self._lock:
            ended = self._ended(launch)
        if ended:
            if child is not None:
                child.kill()
            raise RpcError("This chat's model runtime was stopped while it started")

    def _room(self, session, launch):
        """Admit one more child for ``session`` (``launch``: its registered launch): its oldest beyond
        ``SESSION_CHILDREN`` end, and none starts past ``WORKER_CHILDREN``, once closed or once ended (``RpcError``)."""
        with self._lock:
            if self._ended(launch):
                raise RpcError("This chat's model runtime was stopped while it started")
            live = [child for child in self._children.values() if child.alive()]
            own = [child for child in live if child.session == session]
            ending = own[:max(0, len(own) - SESSION_CHILDREN + 1)]
            if len(live) - len(ending) + self._starting >= WORKER_CHILDREN:
                raise RpcError("This worker already runs as many model runtimes as Angelo allows")
            self._starting += 1
            for child in ending:   # Ended here: never counted, reached or handed out again.
                self._children.pop(child.pid, None)
        for child in ending:
            self.log("Angelo: a worker chat's new model runtime ends its oldest one")
            child.kill()

    def close(self):
        """The worker ended or Angelo exits: every child of this service ends."""
        with self._lock:   # The lock a launch registers under: none spawning now outlives this (``_start``).
            self.closed = True
            children, self._children, self._warm = list(self._children.values()), {}, None
            self._cancel(lambda launch: True)
        for child in children:
            child.kill()

    # Children ----------------------------------------------------------------------------------------------------

    def _start(self, planned, chat, launch):
        if not self.churn.admit(self._session_of(planned)):
            raise RpcError("This chat started too many model runtimes in a short time; try again in a minute")
        self._room(self._session_of(planned), launch)
        try:
            process = self.spawn(planned["command"], planned["env"], planned["cwd"])
            flt = Filter(planned["kind"], planned["cwd"], planned["model_provider"], planned["secrets"])
            child = RuntimeChild(process, flt, self._session_of(planned), key=planned["key"], log=self.log)
            child.binding = binding(chat)   # Fixed at its start: the connection and key its environment holds.
            flt.recorded = lambda: self._recorded(child.session)
            flt.live_secrets = lambda: self._keys(child.session)
            flt.serves = lambda: self._serves(child) is not None
            flt.image_roots = lambda: self._references(child.session)
            flt.opened = lambda thread: self._opened(child, thread)
            flt.based = lambda thread: self._based(child, thread)
            flt.bases = lambda: self._bases(child)
            with self._lock:   # Registered under the lock close and kill take, unless one ended it meanwhile.
                cancelled = self._ended(launch)
                if not cancelled:
                    self._children[child.pid] = child
        finally:
            with self._lock:
                self._starting -= 1
        if cancelled:
            child.kill()
            raise RpcError("This chat's model runtime was stopped while it started")
        threading.Thread(target=self._forget, args=(child,), daemon=True, name="AngeloRelayReap").start()
        if planned["login"]:
            try:   # The key goes to the child from here only; the worker's initialize is answered from this one.
                child.filter.initialized = child.request("initialize", {
                    "clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True}}) or {}
                child.notify("initialized")
                child.request("account/login/start", {"type": "apiKey", "apiKey": chat.api_key})
            except Exception:
                child.kill()
                raise RpcError("OpenAI API-key authentication failed") from None
        return child

    @staticmethod
    def _session_of(planned):
        return planned["key"][0]

    def _adopt(self, key):
        with self._lock:
            warm = self._warm
            if warm is None:
                return None
            self._warm = None
        _key, child, deadline = warm
        if _key == key and child.alive() and time.monotonic() < deadline:
            return child
        child.kill()   # Another launch, or expired: never left running.
        return None

    def _expire(self, child):
        with self._lock:
            if self._warm is None or self._warm[1] is not child:
                return
            self._warm = None
        child.kill()

    def _forget(self, child):
        child.ended.wait()
        try:
            child.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        with self._lock:
            if self._children.get(child.pid) is child:
                del self._children[child.pid]

    def children(self):
        with self._lock:
            return list(self._children.values())

    def _keys(self, session):
        chat = self.chats(session)
        return ([chat.api_key] if chat is not None else []) + list(self.secrets())

    def _redact(self, chat, text):
        return Filter("codex", "", secrets=(chat.api_key,), live_secrets=self.secrets).redact(text)

    def _recorded(self, session):
        """The threads ``session``'s chat may resume or fork: ones Angelo saw its children open, and its
        connection's warm-up bases (``runtime_threads``), never what the worker wrote."""
        chat = self.chats(session)
        return self.threads(chat).known(session, binding(chat)[:2]) if chat is not None else ()

    def _opened(self, child, thread):
        chat = self.chats(child.session)
        if chat is not None and matches(chat, child.binding):
            self.threads(chat).opened(child.session, thread)

    def _based(self, child, thread):
        chat = self.chats(child.session)
        if chat is not None and matches(chat, child.binding):
            self.threads(chat).based(child.binding[:2], thread)

    def _bases(self, child):
        """The warm-up bases of ``child``'s connection: only ever forked, never given another turn (``Filter``)."""
        chat = self.chats(child.session)
        return self.threads(chat).bases(child.binding[:2]) if chat is not None else ()

    def _references(self, session):
        chat = self.chats(session)
        return tuple(chat.references) if chat is not None else ()

    def _account(self, session):
        """The account identity ``session``'s own children named to ``account/read`` (``Filter.account``)."""
        from .runtime_filter import CONFLICT
        self._claim(session)
        with self._lock:
            named = {child.filter.account for child in self._children.values()
                     if child.session == session and child.alive() and child.filter.account is not None}
        if len(named) != 1 or CONFLICT in named:
            raise RpcError("Angelo has not seen this chat's Codex account, so it keys no cache affinity")
        return named.pop()

    def _relay(self, child):
        """``(argument, handle)``: the worker's end of the child's new relay, and the descriptor to pass, if any."""
        if self.platform == "win32":
            listener = worker_channel.PipeListener(win32=self.win32)

            def accept():
                try:
                    child.attach(listener.accept(self.worker_pid, limits=(RELAY_LIMIT, RELAY_LIMIT)))
                except Exception as error:  # noqa: BLE001 - never connected: the child ends.
                    self.log("Angelo: a worker did not connect to its model runtime (%s)" % error)
                    listener.close()
                    child.kill()
            threading.Thread(target=accept, daemon=True, name="AngeloRelayAccept").start()
            return listener.child_argument(), None
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        ours.set_inheritable(False)
        theirs.set_inheritable(False)
        child.attach(worker_channel.socket_endpoint(ours, RELAY_LIMIT, RELAY_LIMIT))
        return "fd:passed", theirs.detach()   # Passed with the answer, then closed here (worker_channel.Client).


# Angelo's own exit ends every child it runs for a worker ------------------------------------------------------------

_SERVICES = weakref.WeakSet()


@atexit.register
def _close_all():
    for service in list(_SERVICES):
        service.close()
