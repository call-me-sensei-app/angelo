# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's opening readied when its connection is ready, before its first message.

When a chat's model catalog loads (at launch, after a sign-in or a refresh:
``Controller.refresh_models``), ``prepare`` starts the connection's shared Codex
app-server off the UI thread, runs its checks and readies the base the chat's
next new thread would fork: the remembered one is resumed (no model request),
else one is warmed (``codex_connect.prepare_opening``, ``codex_bases``). The
first chat of a launch then forks it instead of paying its opening uncached
(doc/angelo/agent_integration.rst, "Shared Codex app-server and forked
openings"; doc/angelo/performance.rst, "Eager openings"). On Angelo's agent
runtime with cache-affinity keys there is no base: preparing only starts and
checks the shared app-server, and the first chat's keyed thread reads the
opening an earlier chat cached (``codex_connect.affinity_key``).

Only a ChatGPT subscription plan chat prepares (``codex_runtime.eligible``).
Nothing is shown and no chat is changed; a failure leaves the chat's own
connect to check and report as before. A stored team sign-in still being
restored decides which tools the chat has, so the opening waits for it, at
most ``TEAM_RESTORE_SECONDS``, and prepares nothing if it has not settled.
"""

import threading
import time

from .codex_runtime import ELIGIBLE_MODES
from .routing import RuntimeContext

TEAM_RESTORE_SECONDS = 20.0  # Off the UI thread; the chat's own connect needs no opening.


class OpeningContext(RuntimeContext):
    """A text chat's next new thread before any message.

    Its options, tools and instructions are the ones that chat's next request
    would use and, like every new text context, it holds a short-identifier
    table, so its opening (``codex_connect.variant_key``) is that request's. It
    opens no thread, runs no turn and shows nothing: its events go nowhere.
    """

    def __init__(self, host, mode, settings, api_key, connection_id, epoch):
        super().__init__(host, mode, settings, api_key, connection_id, epoch)
        self.state["native_id"] = None
        self.begin(host.state.get("options"), [], [])

    @property
    def short_identifiers(self):
        return self.voice is None

    def emit(self, kind, data=None):
        if kind == "models":
            self.models = list((data or {}).get("models", []))


def prepare(controller, epoch=None):
    """Ready ``controller``'s next new Codex thread's opening in the background: the thread, or None.

    ``epoch``: the chat's epoch when its catalog was asked for; a chat replaced
    since (a new chat, an invalidated connection) prepares nothing.
    """
    if controller.state["provider"] not in ELIGIBLE_MODES or epoch not in (None, controller._epoch):
        return None  # Nothing else shares an app-server.
    thread = threading.Thread(target=_prepare, args=(controller,), daemon=True, name="AngeloCodexOpening")
    thread.start()
    return thread


def team_settled(deadline, poll=0.1):
    """Whether no stored team sign-in is being restored (it decides the team-library tools), by ``deadline``."""
    from .team_library_access import account
    from .team_view import RESTORING
    while True:
        current = account()
        if getattr(current.view() if current is not None else None, "status", None) != RESTORING:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def _prepare(controller):
    """Off the UI thread, never raising: lease, check, ready the base, let go."""
    from .providers import CodexProvider
    provider = None
    try:
        if not team_settled(time.monotonic() + TEAM_RESTORE_SECONDS):
            return  # The chat's tools are not known yet: its own first turn warms its variant, as before.
        context = OpeningContext(controller, controller.state["provider"], controller.settings, controller.api_key,
                                 controller.state.get("connection_id"), controller._epoch)
        provider = CodexProvider(context, context.settings)
        provider.connect(opening=True)
    except Exception:  # Nothing was readied: the chat's own connect checks and reports as before.
        pass
    finally:
        if provider is not None:
            provider.close()
