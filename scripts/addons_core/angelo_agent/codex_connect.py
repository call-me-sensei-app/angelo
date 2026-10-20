# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex chat's connect: its checks and native thread on its own or a shared app-server (``codex_runtime``).

``open_thread`` resumes the chat's thread, finds it still loaded, forks its
variant's warm base or starts one (doc/angelo/agent_integration.rst, "Shared
Codex app-server and forked openings"); ``prepare_opening`` readies that base
when the connection is ready, before the chat's first message. On a shared
app-server the chat's open turns are the process's own record
(``Runtime.turns``), and a thread whose turn never ended (``abandon``) is never
reused: the chat continues in a new context, as after a restart.

On a process of Angelo's agent runtime that accepts ``cacheAffinityKey``
(``Runtime.cache_affinity``) a new text chat starts its own thread with its
context's key (``affinity_key``) and reads the opening an earlier chat cached;
it never starts a keyless text thread there. A resumed thread sends no key: the
runtime keeps the key it was started with (persisted in its rollout), so its
cached history stays on its route whatever changed since. No base is resumed,
warmed or forked, and ``prepare_opening`` only starts and checks the process.
Forks and warm bases are the stock-Codex path. ``stamp_route`` records on the
request's receipt which Codex ran it and whether its thread was keyed.
"""

import hashlib
import json
import threading

from . import cache_affinity, codex_team_keys, context_rebase
from .codex_runtime import ELIGIBLE_MODES, Lease
from .codex_turns import STUCK_SHARED
from .protocol import RpcError

# Codex no longer has the thread (an ephemeral fork after a restart), or another app-server holds it.
LOST = ("no rollout found", "already has an active writer")


class ContextLost(RpcError):
    """The chat's native thread no longer exists in its runtime (an ephemeral fork after a restart)."""


# What of a turn's options shapes its cached opening. Never reasoning effort: a request parameter, not prompt bytes;
# caches are per model, so a warm opening serves every effort of its model and prefix (as for
# cache_affinity's key). A key per effort only re-warmed a chat whose effort differed from the base's.
VARIANT_TURN_FIELDS = ("model", "outputSchema")


def variant_inputs(parameters, turn_options):
    """What shapes a chat's cached opening: the thread parameters and the turn's model and response format."""
    return {"thread": {key: value for key, value in parameters.items() if key != "threadId"},
            "turn": {key: turn_options.get(key) for key in VARIANT_TURN_FIELDS}}


def variant_key(parameters, turn_options):
    """The digest of ``variant_inputs``: the key of a warm base, its stored opening and its forks."""
    shaped = variant_inputs(parameters, turn_options)
    return hashlib.sha256(json.dumps(shaped, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def initialize(rpc, controller, executable=None):
    """A new app-server's ``initialize`` (the Codex version floor and drift notice); its result.

    ``executable``: the process's Codex; when it is Angelo's agent runtime its pinned version is verified.
    """
    from .agent_runtime import is_agent_runtime
    from .cli_runtime import note_codex_runtime
    agent = executable if executable and is_agent_runtime(executable) else None
    result = note_codex_runtime(controller, rpc.request("initialize", {
        "clientInfo": {"name": "angelo_blender", "title": "Angelo", "version": "0.1.0"},
        "capabilities": {"experimentalApi": True}}), agent)
    rpc.notify("initialized")
    return result


def checks(rpc, controller, mode, external, models=None):
    """Every connect's checks, on its own process or a shared one; its models (an external connection's ``models``)."""
    from .chat_settings import normalize_models
    if mode == "codex_api" and controller.api_key:
        rpc.request("account/login/start", {"type": "apiKey", "apiKey": controller.api_key})
    if not external:
        account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        expected = "apiKey" if mode == "codex_api" else "chatgpt"
        if account.get("type") != expected:
            raise RpcError("Codex billing mode does not match. Sign in with ChatGPT in the official Codex CLI, then "
                           "reconnect" if expected == "chatgpt" else "OpenAI API-key authentication failed")
        if isinstance(rpc, Lease):
            rpc.note_account(account)  # Its identity, not only its type, must be the one the process started with.
    # Fail closed if a newer configuration source kept unrelated MCP servers enabled.
    configuration = rpc.request("config/read", {}).get("config", {})
    if any(server.get("enabled", True) for server in configuration.get("mcp_servers", {}).values()):
        raise RpcError("Unrelated MCP servers remain enabled; select an isolated Angelo Codex profile")
    # The desktop's catalog can include user-curated router models. This
    # connection is explicitly OpenAI: never silently route to a
    # slash-namespaced third-party account from a ChatGPT/API profile.
    if not external:
        models = [m for m in normalize_models(rpc.request("model/list", {}).get("data", [])) if "/" not in m["id"]]
    return models


def _turn_options(controller):
    """What a plan chat's turns will send that shapes the cached opening (``CodexProvider._run_turn``)."""
    from .chat_settings import clean_options, codex_turn_options
    from .codex_plan import output_schema
    options = controller.turn_options or clean_options(controller.state.get("options"))
    schema = output_schema(getattr(controller, "allowed_tools", None))
    return dict(codex_turn_options(controller, options), outputSchema=schema)


def affinity_key(runtime, start, controller):
    """The ``cacheAffinityKey`` of a thread opened with ``start`` (thread parameters with ``dynamicTools``).

    One per account, model and static prefix (``cache_affinity.inputs``): never per
    effort, connection copy, state folder, working directory, process or chat. It
    reads only the thread's model and the output schema, never the turn's checked
    options, so an unavailable model or an unoffered Fast tier (which the turn
    reports) still keys the thread. None outside ``ELIGIBLE_MODES``: an API-key or
    other account has no per-person identity to key.
    """
    if runtime.mode not in ELIGIBLE_MODES:
        return None
    return _derive((runtime.mode, runtime.account), None, start, controller)


def team_affinity_key(provider, start):
    """A team chat's ``cacheAffinityKey``: the team's, the same for every member (``codex_team_keys``).

    None when no team secret is known (the thread starts without one, as before).
    The process is checked once first (``codex_team_keys.supported``).
    """
    found = codex_team_keys.account(provider.controller.state.get("provider"))
    if found is None:
        return None
    codex_team_keys.supported(provider.rpc)
    account, secret = found
    return _derive(account, secret, start, provider.controller)


def _derive(account, secret, start, controller):
    """``cache_affinity.derive`` of ``account`` under ``secret`` (None: the per-user secret), traced."""
    from .codex_plan import output_schema
    from .routing import TOOLSET_VERSION
    turn = {"outputSchema": output_schema(getattr(controller, "allowed_tools", None))}
    values = cache_affinity.inputs(account, start, turn, TOOLSET_VERSION, secret)
    key = cache_affinity.derive(values)
    cache_affinity.trace(key, values)
    return key


def open_thread(provider, parameters, tools, fork=True):
    """The chat's native thread: resumed, still loaded, forked from a warm base or started.

    ``parameters`` are the provider's thread parameters and ``tools()`` the
    dynamic tools a new thread declares; ``fork`` False (voice) always starts
    one, and so does a rebased chat (``context_rebase.pending``), whose plain
    thread Codex compacts itself. A chat's
    thread that was forked is marked (``provider._thread_forked``). Raises ``ContextLost`` when Codex no longer has the chat's thread
    (module docstring). On a process with cache-affinity keys
    (``Runtime.cache_affinity``) a text chat's ``thread/start`` carries its
    ``cacheAffinityKey`` (``affinity_key``; a text thread without one is refused,
    never started), its ``thread/resume`` none (the thread keeps its own), and no
    base is forked, resumed or warmed. A team chat on its own process of the agent
    runtime starts its text thread with the team's key when one is known
    (``team_affinity_key``), else without one.
    """
    rpc, usage, native = provider.rpc, provider._usage, provider.controller.state["native_id"]
    runtime = rpc.runtime if isinstance(rpc, Lease) else None
    provider._thread_forked = provider._team_keyed = False
    keyed = runtime is not None and runtime.cache_affinity and fork
    if runtime is not None:
        provider._scope.turns = runtime.turns  # The process's open turns, whichever chat started them.
        if native and runtime.is_abandoned(native):
            raise ContextLost("A turn of the chat's Codex thread never ended; the chat continues in a new context")
    if native and runtime is not None and runtime.has_loaded(native):
        # Still loaded (and an ephemeral fork cannot be resumed even here): no request at all.
        usage.thread_loaded(native, runtime.total(native))
        result = {"thread": {"id": native}}
    elif native:
        try:  # No key: the runtime keeps the one the thread was started with, where its history is cached.
            result = rpc.request("thread/resume", dict(parameters, threadId=native))
        except RpcError as error:
            if any(text in str(error) for text in LOST):
                raise ContextLost("The chat's Codex thread is no longer available") from error
            raise
        usage.thread_resumed(result["thread"]["id"])
    elif keyed:
        start = dict(parameters, dynamicTools=tools())
        key = affinity_key(runtime, start, provider.controller)
        if key is None:  # Never a keyless text thread on a keyed runtime.
            raise RpcError("This connection cannot open a keyed thread on Angelo's agent runtime")
        result = rpc.request("thread/start", dict(start, cacheAffinityKey=key))
        usage.thread_started(result["thread"]["id"])
    elif runtime is None and fork and codex_team_keys.applies(provider):
        start = dict(parameters, dynamicTools=tools())
        key = team_affinity_key(provider, start)
        result = rpc.request("thread/start", dict(start, cacheAffinityKey=key) if key else start)
        usage.thread_started(result["thread"]["id"])
        provider._team_keyed = key is not None
    else:
        start = dict(parameters, dynamicTools=tools())
        turn = variant = forked = None
        if runtime is not None and fork:
            try:
                turn = _turn_options(provider.controller)
            except ValueError:  # An unavailable model: the turn itself reports it.
                turn = None
            if (turn is not None and runtime.has_base()  # The digest (a few ms) only when a fork may follow.
                    and not context_rebase.pending(provider.controller)):
                variant = variant_key(start, turn)
                forked = runtime.fork(variant)
        if forked:
            result, total = forked
            usage.thread_loaded(result["thread"]["id"], total)
            provider._thread_forked = True
        else:
            result = rpc.request("thread/start", start)
            usage.thread_started(result["thread"]["id"])
            if turn is not None:
                prepare_base(runtime, variant, start, turn, stored_only=True)  # Remembered: no model request.
                provider._warm_after_turn = (runtime, variant, start, turn)
    if runtime is not None:
        rpc.own(result["thread"]["id"], voice=not fork)  # Voice turns belong to the voice session (codex_turns).
    stamp_route(provider)
    return result


def stamp_route(provider):
    """The request's receipt: which Codex ran it (``agent_runtime.kind``) and whether its thread is keyed.

    A team chat counts as keyed when this connection started its thread with the team's key; a team
    thread resumed after a reconnect keeps its key in the runtime but is recorded as not keyed. Under
    the request's fence, as every receipt field (``routing.RuntimeContext``); nothing for a context
    without request metrics (an opening, a probe).
    """
    context = provider.controller
    host, fence = getattr(context, "host", None), getattr(context, "request_fence", None)
    metrics = getattr(host, "_request_metrics", None)
    if metrics is not None and fence is not None and fence.matches(host):
        rpc = provider.rpc
        metrics.set_route(agent_runtime=getattr(provider, "_runtime_kind", None),
                          cache_affinity=bool(isinstance(rpc, Lease) and rpc.runtime.cache_affinity
                                              or getattr(provider, "_team_keyed", False)))


def abandon(rpc, thread_id):
    """``OpenTurns.start``: a turn on ``thread_id`` never ended; the text the unsent request fails with.

    On a shared app-server closing this chat's lease does not end that turn, so
    the thread is abandoned and the chat's next message opens a new context with
    its handoff. The provider's own process is closed, as before (``STUCK``).
    """
    if not isinstance(rpc, Lease):
        return None
    rpc.runtime.abandon(thread_id)
    return STUCK_SHARED


def prepare_opening(provider, parameters, tools):
    """The connection is ready (launch, a sign-in): ready the base this chat's next new thread would fork.

    ``parameters`` and ``tools()`` are what ``open_thread`` would start with.
    The remembered base is resumed (no model request), else one is warmed now
    instead of after the first chat's first turn (``codex_bases.warm``: once per
    variant, ``MAX_WARM_UPS``). Whether readying began.
    """
    runtime = provider.rpc.runtime if isinstance(provider.rpc, Lease) else None
    if runtime is None or runtime.cache_affinity:
        return False  # A keyed process needs no base: its start and checks are what the opening readied.
    try:
        turn = _turn_options(provider.controller)
    except ValueError:  # An unavailable model: its chat's turn reports it.
        return False
    start = dict(parameters, dynamicTools=tools())
    return runtime.warm(variant_key(start, turn), start, turn, at_connect=True)


def prepare_base(runtime, variant, start, turn, stored_only=False):
    """Off the chat's path: the variant's digest when not known yet, then ``Runtime.warm``."""
    threading.Thread(target=lambda: runtime.warm(variant or variant_key(start, turn), start, turn, stored_only),
                     daemon=True, name="AngeloCodexBase").start()


def after_turn(provider, status):
    """After every turn: its receipt's runtime fields (``stamp_route``: a request on a connected chat opens no
    thread). After a started chat's first completed turn on stock Codex, warm its variant's base."""
    stamp_route(provider)
    pending, provider._warm_after_turn = getattr(provider, "_warm_after_turn", None), None
    if pending and status == "completed":
        prepare_base(*pending)
