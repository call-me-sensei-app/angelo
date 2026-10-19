# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's agent runtime started while its next message is written, so the send does not wait for the launch.

Contract and measurements: doc/angelo/performance.rst, "Runtime prewarm".

The UI pump (``ui.pump``) calls ``observe`` about ten times a second with the
draft of the open chat editor. When the person changes a draft that is not
empty (typing, pasting; a draft merely shown, such as one restored at launch or
on switching chats, starts nothing) and the chat's next message would launch a
runtime (it has none: a new chat, or one whose runtime was closed),
``start`` creates the chat's provider for the request that message
will be (``PrewarmContext``) and starts its runtime in the background, with the
same checks the send runs:

* Claude Code: the sign-in check, version probe, launch and ``initialize``
  (``ClaudeProvider.start_prewarm``);
* Codex on an app-server of the chat's own (API-key, team and other-provider
  connections): the app-server and its ``initialize``
  (``CodexProvider.start_prewarm``). A ChatGPT plan chat shares its
  connection's app-server, which its opening starts when the connection is
  ready (``codex_opening``), so nothing is started for it here.

Nothing is sent: no message, thread or turn, so the person's message is the
first model request. The send adopts the runtime (``adopt``);
a request that arrives while the launch is still under way waits for it
(``Launch.settle``) instead of starting a second process, and a runtime whose
launch no longer matches the request is closed and replaced
(``ClaudeProvider._turn``, ``CodexProvider._adopt_prewarmed``).

Bounds (an idle Claude Code process holds about 190 MB, a Codex app-server
more): at most ``MAX_PREWARMED`` prewarmed runtimes in the app, so writing in
another chat releases the earlier one. A prewarmed runtime is released (closed,
never reused) when its chat's draft is emptied, ``IDLE_SECONDS`` after the draft
last changed (it starts again only when the draft changes), when what its launch
depends on changes (``key``: connection and its settings, options but
reasoning effort, model context, cache TTL, a diagnostic run's thinking
display, whether the team-library tools are offered; a matching one starts at
once),
and when the chat's runtime is closed for any other reason (a new chat, another
connection, a sign-in, the app closing). Never for a chat that is busy, already
has a runtime, is an External CLI conversation, uses voice, has a changed
document or a queued connection change, or whose connection is not ready.

Standard library only; no bpy.
"""

import threading
import weakref
from dataclasses import dataclass

from . import context_rebase, team_library_access
from .chat_settings import clean_options
from .provider_registry import runtime_kind
from .routing import RuntimeContext

IDLE_SECONDS = 120.0       # Since the draft last changed: a pause to think keeps it, a draft left alone does not.
MAX_PREWARMED = 1          # The chat being written; another chat's draft takes the slot.
LAUNCH_WAIT_SECONDS = 10.0  # A request waits this long for a launch under way (a healthy one takes about 0.5 s).


class PrewarmOwner:
    """The authority of a runtime started before its request, with ``claude_transport.ClaudeRequest``'s interface.

    It outlives request generations: the send begins a new request while the
    launch may still be under way, and that request adopts it. It ends when the
    chat's epoch changes (another connection, model context or chat), the chat
    closes, or the prewarm is released.
    """

    def __init__(self, context):
        self.controller = context
        self.host = getattr(context, "host", context)
        self.fence = getattr(context, "request_fence", None)
        self.epoch = self.host._epoch
        self._released = threading.Event()

    def release(self):
        self._released.set()

    @property
    def released(self):
        return self._released.is_set()

    def owns_request(self):
        return not self._released.is_set() and self.host._epoch == self.epoch and not self.host._closing

    def same_request(self, other):
        return other is self


class PrewarmContext(RuntimeContext):
    """The request the chat's next message will be, before it is sent (``context``).

    Its options, tools, cache TTL and native context are that request's, so the
    launch is the one the send would make. It shows nothing except a host-only
    notice its launch emits, which may be said once per process and would
    otherwise be lost.
    """

    def emit(self, kind, data=None):
        data = data or {}
        if (kind == "message" and data.get("role") == "system" and data.get("notice") is True
                and not self.host._closing and self.epoch == self.host._epoch):
            self.host.emit(kind, dict(data, provider=self.state["provider"], connection_id=self.connection_id))


class Launch:
    """A provider's launch begun before its request: claimed on the UI thread, run on its own thread.

    ``settle`` (the request's adoption, on its worker thread) waits for a launch
    still under way, at most ``LAUNCH_WAIT_SECONDS``. The launch began earlier,
    so waiting for it takes no longer than a launch of the request's own unless
    it is stuck; a stuck one is released (it closes what it started at its next
    check) and the request starts its own.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._done.set()
        self._owner = None

    def start(self, owner, work, name):
        """Run ``work(owner)`` on a new thread; None when a launch is already under way."""
        with self._lock:
            if not self._done.is_set():
                return None
            self._owner = owner
            self._done.clear()

        def run():
            try:
                work(owner)
            except Exception as error:  # noqa: BLE001 - the send repeats every check and reports it.
                _failed(owner, error)
            finally:
                with self._lock:
                    self._owner = None
                    self._done.set()
        thread = threading.Thread(target=run, daemon=True, name=name)
        thread.start()
        return thread

    @property
    def running(self):
        return not self._done.is_set()

    def settle(self, seconds=LAUNCH_WAIT_SECONDS):
        """Wait for a launch under way; True when none is left running."""
        if self._done.wait(seconds):
            return True
        with self._lock:
            owner = self._owner
        if owner is not None:
            owner.release()
        return False


def _failed(owner, error):
    """A failed prewarm shows nothing: the send runs the same checks and shows their error.

    Expected failures (no sign-in, a missing or unsupported runtime, a refused
    connection) are runtime errors or OS errors; anything else is a host defect
    and is printed.
    """
    if isinstance(error, (RuntimeError, OSError)):
        return
    from .pump_guard import report_defect
    host = getattr(owner, "host", None)
    report_defect("the runtime prewarm", error, getattr(host, "redact", str))


@dataclass(eq=False)
class Prewarm:
    """One chat's prewarmed runtime: its provider, owner, launch key and when its draft last changed."""
    provider: object
    owner: PrewarmOwner
    key: tuple
    touched: float


# Each chat's draft (a digest, None when empty) at the last tick that showed it: a change is the person writing.
_seen = weakref.WeakKeyDictionary()


def observe(active, draft, controllers, now):
    """One UI tick: start, keep or release prewarmed runtimes.

    ``active``: the open chat's controller (or None); ``draft``: its composer
    text, "" when empty, None when no chat editor shows it (then only the idle
    bound applies). ``controllers``: every chat.
    """
    replaced = False  # The open chat's runtime was closed by another change (a connection, a sign-in).
    for controller in controllers:
        record = controller.prewarmed
        if record is not None and (record.provider is not controller.provider or now - record.touched >= IDLE_SECONDS):
            replaced = replaced or (controller is active and record.provider is not controller.provider)
            release(controller)
    if active is None or draft is None:
        return
    digest = hash(draft) if draft.strip() else None
    written = (active in _seen and _seen[active] != digest) or replaced
    _seen[active] = digest
    if digest is None:
        release(active)
        return
    record, launch_key = active.prewarmed, key(active)
    if record is not None and record.key != launch_key:
        release(active)  # The launch no longer matches the message's request: a matching one starts below.
        record, written = None, True
    if record is not None:
        if written:
            record.touched = now
        return
    if not written or launch_key is None or active.provider is not None:
        return
    others = sorted((c for c in controllers if c is not active and c.prewarmed is not None),
                    key=lambda c: c.prewarmed.touched)
    for controller in others[:max(0, len(others) + 1 - MAX_PREWARMED)]:
        release(controller)
    start(active, launch_key, now)


def next_options(controller):
    """The options the chat's next request will use: its own, with the model Auto would choose (``Controller.send``)."""
    options = clean_options(controller.state.get("options"))
    route = controller._auto_route(options)
    if route is not None:
        options["model"] = route.model
    return options


# The options a prewarmed runtime's launch may differ in from the request's: effort is a request parameter.
EFFORT_FREE = ("effort",)


def launch_options(options):
    """``options`` without what no runtime launch depends on (``EFFORT_FREE``)."""
    return {name: value for name, value in options.items() if name not in EFFORT_FREE}


def key(controller):
    """What the next message's runtime launch depends on, or None when no runtime may start before it.

    ``observe`` compares it each tick: a change releases the prewarmed runtime.
    None while the chat is busy or not ready (module docstring), and when the
    message would first switch the Claude model context or start a fresh one
    (``Controller._select_model_context``, ``_rebase_context_if_due``), which
    closes any runtime. A connection's settings and key (its profile, and so
    whether a Claude key route may launch, ``require_angelo_profile``) change
    ``_epoch``. Two inputs are checked only where the runtime is used, since
    reading them needs the launch itself: the runtime's version against its
    floor (every launch probes it) and what a team gateway decodes (a Codex
    app-server is adopted only with an unchanged command and environment,
    ``runtime_launch.launch_binding``, so a gateway that starts naming zstd
    replaces it). The send compares the whole launch once more
    (``ClaudeProvider._runtime_binding``, ``CodexProvider._adopt_prewarmed``).

    Never reasoning effort (``EFFORT_FREE``): a runtime started for one effort
    serves any. A Codex app-server's launch does not depend on it (each turn
    states it), and Claude Code 2.1.292 and later change a running session's
    effort before the turn (``claude_transport.apply_effort``), relaunching with
    ``--effort`` only when the session does not acknowledge the change.
    """
    if (controller.external_client or controller.busy or (controller._worker and controller._worker.is_alive())
            or controller._closing or controller.document_invalid or controller.voice is not None
            or controller._next_binding is not None or controller.connection_error or not controller.settings):
        return None
    try:
        runtime = runtime_kind(controller.state["provider"])
    except ValueError:
        return None
    options, native_id = next_options(controller), controller.state.get("native_id")
    if runtime == "claude":
        model = options["model"] or controller.settings.get("model") or ""
        if controller._model_context_slot(model) != controller.state.get("context_slot"):
            return None
        entry = controller.state["runtime_sessions"].get(controller.context_key(), {})
        if context_rebase.due(controller.context_rebase_tokens, entry.get("context_tokens"), native_id=native_id,
                              runtime=runtime):
            return None
    return (controller._epoch, controller.state["provider"], controller.state.get("connection_id"), native_id,
            controller.state.get("context_slot"), tuple(sorted(launch_options(options).items())),
            controller.session_cache_ttl(),
            controller.single_model_runs, controller.debug_thinking is True, team_library_access.enabled())


def context(controller):
    """The context of the request the next message will be, as ``Controller.runtime_context`` builds it."""
    prewarm = PrewarmContext(controller, controller.state["provider"], controller.settings, controller.api_key,
                             controller.state.get("connection_id"), controller._epoch)
    prewarm.begin(next_options(controller), [], controller.state["messages"])
    controller._bind_session(prewarm)
    return prewarm


def start(controller, launch_key, now):
    """Start the runtime the chat's next message would launch, sending nothing; the record, or None."""
    from .providers import ClaudeProvider, CodexProvider
    if controller.provider is not None or controller.prewarmed is not None:
        return None
    request = context(controller)
    provider = (CodexProvider if runtime_kind(request.state["provider"]) == "codex" else ClaudeProvider)(
        request, request.settings)
    owner = PrewarmOwner(request)
    if not provider.start_prewarm(owner):
        return None
    controller.provider = provider
    controller.prewarmed = Prewarm(provider, owner, launch_key, now)
    return controller.prewarmed


def release(controller):
    """Close a runtime started for a message that was not sent: never reused."""
    record, controller.prewarmed = controller.prewarmed, None
    if record is None:
        return
    record.owner.release()  # A launch still under way closes what it started at its next check.
    if controller.provider is record.provider:
        controller.provider = None
    threading.Thread(target=record.provider.close, daemon=True, name="AngeloPrewarmRelease").start()


def adopt(controller):
    """The send: whether its request uses the runtime started while it was written (still the chat's)."""
    record = controller.prewarmed
    if record is not None and controller.provider is record.provider:
        controller.prewarmed = None  # Its provider is the chat's: the request rebinds it or replaces a stale launch.
        return True
    release(controller)
    return False


def forget(controller):
    """The chat is closing: its provider, the prewarmed one included, is closed with it."""
    record, controller.prewarmed = controller.prewarmed, None
    if record is not None:
        record.owner.release()
