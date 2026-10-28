# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared Codex app-servers for ChatGPT subscription chats, and the warm base threads new chats fork.

Codex keys OpenAI's prompt cache by thread (``prompt_cache_key`` and the
``session-id`` header), so a new chat's first request found nothing of the
24k-token opening every chat shares. An ephemeral ``thread/fork`` in
``experimentalPredictionMode`` of a loaded base thread keeps the base's key: on
real servers such forks had 23,680 of 23,906 first-request tokens cached
(doc/angelo/performance.rst, "Fixed opening of a new chat"). The contract is
doc/angelo/agent_integration.rst, "Shared Codex app-server and forked openings":

* ``Runtime``: one app-server per eligible connection (``eligible``,
  ``pool_key``), started and initialized once and leased by each chat's
  ``CodexProvider`` (``Lease``). Events and host requests reach the lease that
  owns their thread; every connect still runs ``codex_connect.checks``, and an
  account other than the one the process started with retires it.
* Turns: one ``codex_turns.OpenTurns`` per process records every open turn,
  whichever chat started it, so a chat that connects again still settles its
  thread's open turn first. A turn whose chat let go (released before
  ``turn/started``, or a tool call no chat owns) is interrupted. A turn that
  never ends abandons its thread: the chat continues in a new context
  (``codex_connect``).
* Retirement: reauthentication, a sign-in, a sign-out or account switch made
  outside Angelo (the login file changed: ``login_stamp``), a status check
  that finds no account or a failed check retire a process. It admits no new
  chat and closes when its last chat lets go.
* Bases: warmed, resumed, forked and remembered by ``codex_bases``; readied as
  soon as a chat's connection is ready (``codex_connect.prepare_opening``).
  This is the stock-Codex path. A process of Angelo's agent runtime
  (``agent_runtime``) that accepts ``cacheAffinityKey`` (``cache_affinity``,
  checked once in ``prepare``) has no bases: every new chat starts a keyed
  thread whose first request reads the opening an earlier chat cached.
"""

import hashlib
import json
import os
import re
import threading
from pathlib import Path

from . import cache_affinity, codex_bases
from .codex_turns import OpenTurns
from .protocol import RpcError

ELIGIBLE_MODES = frozenset({"codex_subscription"})
_APPROVALS = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}
_UNOWNED_CALL = "This tool request does not belong to an active chat thread; no work was admitted."


class Retired(RpcError):
    """This app-server admits no new chat (retired or its account changed): connect to the pool's next one."""


def eligible(controller, mode, external, plan, catalog_only=False):
    """Whether this connect may use a shared runtime and forks (module docstring)."""
    return (mode in ELIGIBLE_MODES and not external and plan and not catalog_only
            and getattr(controller, "voice", None) is None)


def pool_key(connection_id, mode, command, env):
    """One shared app-server per connection, mode, profile and command: connections never share one."""
    return (connection_id, mode, env.get("CODEX_HOME"), tuple(command))


def login_file(env):
    """The ChatGPT login file of the app-server's profile (``CODEX_HOME``, else ``~/.codex``, as ``codex_command``)."""
    return Path(env.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"


def login_stamp(path):
    """The login file's presence, device, inode, modification time and size: one ``stat``, nothing read.

    A running app-server keeps the login it started with, so a sign-out or account
    switch made outside Angelo (the Codex CLI or app sharing the profile) changes
    only this file. Codex rewrites it on a token refresh too (rarely: a profile used
    for five days of runs kept the same file); that retires the process as well,
    which costs the next chat one process start.
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


def _thread_of(params):
    if not isinstance(params, dict):
        return None
    thread = params.get("threadId")
    return thread if thread is not None else (params.get("thread") or {}).get("id")


def _turn_of(params):
    if not isinstance(params, dict):
        return None
    turn = params.get("turnId")
    return turn if turn is not None else (params.get("turn") or {}).get("id")


class Lease:
    """One chat provider's view of a shared runtime, with the transport methods the provider uses."""

    def __init__(self, runtime, on_event, on_request):
        self.runtime, self.on_event, self.on_request = runtime, on_event, on_request
        self.closed = threading.Event()
        self.threads = set()

    @property
    def stderr_tail(self):
        return self.runtime.rpc.stderr_tail

    def _live(self):
        if self.closed.is_set():
            raise RpcError("Agent process has disconnected")

    def request(self, method, params=None, timeout=60):
        self._live()
        result = self.runtime.rpc.request(method, params, timeout=timeout)
        if method == "turn/start":
            self.runtime.started(self, params, result)
        return result

    def respond(self, request_id, result):
        self._live()
        self.runtime.rpc.respond(request_id, result)

    def write(self, message):
        self._live()
        self.runtime.rpc.write(message)

    def notify(self, method, params=None):
        self._live()
        self.runtime.rpc.notify(method, params)

    def own(self, thread_id, voice=False):
        self.runtime.own(self, thread_id, voice)

    def note_account(self, account):
        self.runtime.note_account(account)

    def close(self):
        """Release the lease: the process and every thread it loaded stay for the connection's other chats."""
        self.runtime.release(self)
        self.closed.set()


class Runtime:
    """One shared app-server process, its leases, loaded threads, open turns and warm bases."""

    def __init__(self, key, connection_id, spawn, store=None, mode=None, login=None, agent_runtime=False):
        self.key, self.connection_id, self.mode, self.store, self.scope = key, connection_id, mode, store, None
        # Angelo's agent runtime (agent_runtime): checked for cacheAffinityKey once initialized (prepare).
        self.agent_runtime, self.cache_affinity = agent_runtime, False
        # Taken before the process reads its login: a later change can only be noticed, never missed.
        self.login, self.login_stamp = login, login_stamp(login) if login is not None else None
        self.lock = threading.RLock()
        self.leases, self.owners, self.loaded, self.voice, self.abandoned = [], {}, set(), set(), set()
        self.turns = OpenTurns()
        self.turns.shared = True  # Left, never cleared, when a chat's provider leaves (request_fences).
        self.bases, self.warming, self.totals = {}, {}, {}  # variant -> base; warming thread -> record; totals
        self.forks = None  # Prediction forks: None until probed, then True or False.
        self.resumes = {}  # variant -> remembered-base resumes in this process (at most MAX_WARM_UPS)
        self.held = set()  # Variants whose remembered base another app-server holds (codex_bases._resume).
        self.account = None  # Digest of the account the process started with (never the account itself).
        self.initialized, self.prepared, self.dead, self.retired = False, threading.Lock(), False, False
        self.rpc = spawn(self._event, self._request)

    # Dispatch -------------------------------------------------------------------

    def _event(self, method, params):
        if method == "host/disconnected":
            self._died()
            self.turns.observe(method, params)
            targets = list(self.leases)
        else:
            thread, orphan = _thread_of(params), None
            with self.lock:
                voice = thread in self.voice
                if not voice:  # codex_turns: realtime voice turns belong to the voice session.
                    self.turns.observe(method, params)
                if thread is not None and method == "thread/tokenUsage/updated" and isinstance(
                        params.get("tokenUsage"), dict):
                    self.totals[thread] = params["tokenUsage"].get("total")
                owner, record = self.owners.get(thread), self.warming.get(thread)
                targets = [owner] if owner else ([] if thread else list(self.leases))
                if method == "turn/started" and thread is not None and not (owner or record or voice):
                    orphan = _turn_of(params)  # Its chat let go before Codex reported the turn.
            if record is not None:
                codex_bases.observe(record, method, params)
            if orphan:
                self.turns.interrupt(self.rpc, thread, orphan)
        for lease in targets:
            lease.on_event(method, params)

    def _request(self, request_id, method, params):
        thread = _thread_of(params)
        with self.lock:
            owner, record, voice = self.owners.get(thread), self.warming.get(thread), thread in self.voice
        if owner is not None:
            owner.on_request(request_id, method, params)
        elif method == "item/tool/call":
            # Refused, then its turn ended (codex_delivery.refuse_stale_call): an unanswered or orphaned turn
            # stays open and takes the next turn/start on its thread. A warm-up that calls a tool is no base.
            self.rpc.respond(request_id, {"success": False, "contentItems": [{"type": "inputText",
                                                                              "text": _UNOWNED_CALL}]})
            if record is not None:
                record["polluted"] = True
            if not voice:
                self.turns.observe(method, params)
                self.turns.interrupt(self.rpc, thread, _turn_of(params))
        elif method in _APPROVALS:
            self.rpc.respond(request_id, {"decision": "decline"})
        else:
            self.rpc.write({"id": request_id, "error": {"code": -32601, "message":
                "This runtime request isn't enabled in the native Blender integration"}})

    def started(self, lease, params, result):
        """A ``turn/start`` answered on ``lease``: its turn is open, and ends at once if its chat let go first."""
        thread = params.get("threadId") if isinstance(params, dict) else None
        turn = (result.get("turn") or {}).get("id") if isinstance(result, dict) else None
        with self.lock:
            if thread in self.voice or not isinstance(turn, str):
                return
            self.turns.opened(thread, turn)
            orphan = self.owners.get(thread) is not lease
        if orphan:
            self.turns.interrupt(self.rpc, thread, turn)

    # Leases ---------------------------------------------------------------------

    def lease(self, on_event, on_request):
        lease = Lease(self, on_event, on_request)
        with self.lock:
            if self.dead or self.retired:  # The same lock as retire(): a retired process admits no new chat.
                raise Retired("This Codex app-server admits no new chat")
            self.leases.append(lease)
        return lease

    def own(self, lease, thread_id, voice=False):
        with self.lock:
            self.owners[thread_id] = lease
            self.loaded.add(thread_id)
            lease.threads.add(thread_id)
            if voice:
                self.voice.add(thread_id)

    def release(self, lease):
        with self.lock:
            if lease in self.leases:
                self.leases.remove(lease)
            owned = [thread for thread in lease.threads if self.owners.get(thread) is lease]
            for thread in owned:
                del self.owners[thread]
            last = self.retired and not self.leases
            ending = [] if last or self.dead else [(thread, turn) for thread in owned if thread not in self.voice
                                                   for turn in self.turns.open_on(thread)]
        if last:
            self.close()  # A retired process ends with its last chat, as that chat's own process did.
        for thread, turn in ending:  # As closing its own process ended it (interrupts run off this thread).
            self.turns.interrupt(self.rpc, thread, turn)

    def abandon(self, thread_id):
        """A turn on this thread never ended (codex_turns): no chat continues on it."""
        with self.lock:
            self.loaded.discard(thread_id)
            self.abandoned.add(thread_id)

    def is_abandoned(self, thread_id):
        with self.lock:
            return thread_id in self.abandoned

    def has_loaded(self, thread_id):
        with self.lock:
            return not self.dead and thread_id in self.loaded

    def has_base(self):
        """Whether a base is ready or being readied (a fork may follow: ``codex_bases.fork``).

        Never on a process with cache-affinity keys: its new chats start keyed threads instead.
        """
        with self.lock:
            return not self.dead and not self.cache_affinity and self.forks is not False and any(
                base["state"] in ("ready", "warming") for base in self.bases.values())

    def total(self, thread_id):
        with self.lock:
            return self.totals.get(thread_id)

    def check_login(self):
        """A connect: a login file changed since the process started retires it (``login_stamp``)."""
        if self.login is not None and login_stamp(self.login) != self.login_stamp:
            self.retire()
            raise Retired("The Codex login changed since this app-server started")

    def note_account(self, account):
        """A connect's ``account/read``: another identity than the one the process started with retires it."""
        from .runtime_filter import account_identity   # The same digest Angelo keys a worker chat's affinity by.
        digest = account_identity(account)
        with self.lock:
            if self.account is None:
                self.account = digest
            changed = digest != self.account
        if changed:
            self.retire()
            raise Retired("The Codex sign-in changed since this app-server started")

    def prepare(self, initialize):
        """``initialize`` the process once; every lease still runs the connection checks.

        The remembered bases' scope is the process key and the Codex build it reports (``codex_build``).
        Angelo's agent runtime must accept ``cacheAffinityKey`` (``cache_affinity.check``, two local
        requests): its keyed threads replace warm bases and forks. One that does not, exits or does not
        answer is refused with the reason (``cache_affinity.CheckFailed``), never quietly run as stock Codex.
        """
        with self.prepared:
            if not self.initialized:
                agent = (initialize() or {}).get("userAgent")
                from .runtime_relay import launched
                lead = launched(self.rpc)   # A worker chat's process: whether it is the agent runtime is Angelo's word.
                self.agent_runtime = self.agent_runtime if lead is None else lead["kind"] != "own"
                scope = json.dumps([[str(part) for part in self.key], codex_build(agent)]).encode()
                self.scope = hashlib.sha256(scope).hexdigest()[:24]
                if self.agent_runtime:
                    verdict = cache_affinity.check(self.rpc)
                    if verdict != cache_affinity.SUPPORTED:  # Exited, no answer or the wrong build, each said so.
                        raise cache_affinity.CheckFailed(verdict)
                    self.cache_affinity = True
                self.initialized = True

    def retire(self):
        """Admit no new chat and close once the last one lets go (reauthentication, a sign-in, a failed check)."""
        with _POOL_LOCK:
            if _POOL.get(self.key) is self:
                del _POOL[self.key]
            _RETIRED.add(self)
        with self.lock:
            self.retired = True
            idle = not self.leases
        if idle:
            self.close()

    def _died(self):
        with self.lock:
            self.dead = True
            leases, warming = list(self.leases), list(self.warming.values())
        for record in warming:  # Its warm-up ends now, unfinished (no base), not after WARM_UP_SECONDS.
            record["done"].set()
        for lease in leases:
            lease.closed.set()
        _discard(self)

    def close(self):
        self._died()
        self.rpc.close()

    # Bases (codex_bases) --------------------------------------------------------

    def fork(self, variant):
        return codex_bases.fork(self, variant)

    def warm(self, variant, parameters, turn_options, stored_only=False, at_connect=False):
        return codex_bases.warm(self, variant, parameters, turn_options, stored_only, at_connect)


def codex_build(agent):
    """The Codex build in an ``initialize`` user agent, without what does not shape an opening.

    codex-cli 0.162 reports ``client/<build> (<OS and version>; <arch>) <terminal>/<version> (<client>)``: the
    terminal it was launched from (a Dock or a terminal launch) and the macOS version would otherwise make each
    a new scope, and each new scope bills a warm-up. An agent without that shape is kept whole.
    """
    match = re.match(r"\s*[^\s/]+/(\S+)", agent) if isinstance(agent, str) else None
    return match.group(1) if match else agent


def transport(spawn, on_event, on_request, start, verify, key=None, connection_id=None, store=None, mode=None,
              login=None, agent_runtime=False):
    """``(rpc, models)``: a lease of ``key``'s shared runtime, or with no key a process of the provider's own.

    ``agent_runtime``: the process runs Angelo's agent runtime (``Runtime.prepare`` checks its key support).

    ``spawn(on_event, on_request)`` starts an app-server, ``start(rpc)``
    initializes a new one and ``verify(rpc)`` runs ``checks``: on every
    connect, as before, so a shared process saves only its start. A failed
    check closes the provider's own process; a shared one is retired. A process
    retired before this chat's lease or while it started (a sign-in during its
    ``initialize``), or whose login or account changed, is passed over for the
    pool's next one.
    """
    if key is None:
        rpc = spawn(on_event, on_request)
        try:
            start(rpc)
            return rpc, verify(rpc)
        except Exception:
            rpc.close()
            raise
    for _attempt in range(3):
        runtime, lease = runtime_for(key, connection_id, spawn, store, mode, login, agent_runtime), None
        try:
            runtime.prepare(lambda: start(runtime.rpc))  # noqa: B023 (called at once)
            runtime.check_login()
            lease = runtime.lease(on_event, on_request)
            return lease, verify(lease)
        except Exception as error:
            raced = isinstance(error, Retired) or runtime.retired  # Retired meanwhile, its process maybe closed.
            if not raced:
                runtime.retire()
            if lease is not None:
                lease.close()  # A retired process closes with its last chat.
            if not raced:
                raise
    raise RpcError("The Codex connection kept changing while connecting; send the message again")


# The process-wide pool ------------------------------------------------------------

_POOL, _RETIRED, _POOL_LOCK = {}, set(), threading.Lock()


def runtime_for(key, connection_id, spawn, store=None, mode=None, login=None, agent_runtime=False):
    """The live runtime for ``key``; an idle runtime of the connection under another key (old settings) closes.

    The key holds the command, so the agent runtime and the person's own Codex never share a process.
    """
    with _POOL_LOCK:
        runtime = _POOL.get(key)
        if runtime is not None and runtime.dead:
            del _POOL[key]
            runtime = None
        stale = [other for other_key, other in _POOL.items()
                 if other.connection_id == connection_id and other_key != key and not other.leases]
        if runtime is None:
            runtime = _POOL[key] = Runtime(key, connection_id, spawn, store, mode, login, agent_runtime)
    for other in stale:
        other.close()
    return runtime


def _discard(runtime):
    with _POOL_LOCK:
        if _POOL.get(runtime.key) is runtime:
            del _POOL[runtime.key]
        _RETIRED.discard(runtime)


def _retire(matches):
    with _POOL_LOCK:
        retiring = [runtime for runtime in _POOL.values() if matches(runtime)]
    for runtime in retiring:
        runtime.retire()


def retire_connection(connection_id):
    """Reauthentication, removal or a replaced key: the connection's next chat starts a new process.

    Each old one closes when its last chat lets go (``Controller.invalidate_runtime``
    closes those chats' providers), so no running turn is cut short first.
    """
    _retire(lambda runtime: runtime.connection_id == connection_id)


def retire_mode(mode):
    """A sign-in of the mode's default (reused native) login: every app-server of that mode is retired."""
    _retire(lambda runtime: runtime.mode == mode)


def retire_login(path):
    """A status check found the login file's profile signed out: its app-servers are retired."""
    _retire(lambda runtime: runtime.login == path)


def close_all():
    """The add-on unloads: every shared process closes."""
    with _POOL_LOCK:
        closing = list(_POOL.values()) + list(_RETIRED)
    for runtime in closing:
        runtime.close()
