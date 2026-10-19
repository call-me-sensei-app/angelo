# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Suggest an existing native sign-in; never set one up.

The rule: auto discovery, but not auto setup; the person creates a
connection. Angelo still asks the official Codex and Claude
Code CLIs, off the main thread and without inference, whether the person's
everyday sign-in exists (``authentication.NativeAuthentication``). This module
turns that status into a suggestion in Preferences > AI Providers. It never
creates a connection, binds a chat or reads a credential: the person opens the
ordinary Add-connection form, prefilled to reuse that sign-in, and confirms it.
The chat's own Connect buttons (``first_run.py``, ``quick_connect.py``) read
the same statuses; a click there is that explicit choice. AI Providers offers
the same Connect buttons as the chat's welcome card (``connect_offers``): every
subscription whose runtime is available, not only one already signed in.

Pure Python (no ``bpy``) so the rules are testable on any host.
"""

from .provider_registry import CODING, MODE_BY_CONNECTION

# Default native sign-ins that can be offered, in display order, with the
# Add-connection form values that reuse them. Codex's API-key mode is detected
# in Angelo's own older shared profile, which the form cannot reuse, so it is
# never offered (and, like the others, never added automatically).
SUGGESTIONS = {
    "codex_subscription": {"title": "Found your ChatGPT sign-in (Codex)", "provider": "OPENAI",
                           "method": "SUBSCRIPTION"},
    "claude_subscription": {"title": "Found your Claude Code sign-in", "provider": "ANTHROPIC",
                            "method": "SUBSCRIPTION"},
}
SUGGESTION_NOTE = "Reuses the sign-in already on this computer. Nothing is added until you confirm."
# An unbound chat may still run on a key typed into the legacy per-mode API-key
# operator: an explicit action, not a detected sign-in.
LEGACY_KEY_MODES = frozenset({"codex_api", "claude_api"})


def signed_in(status):
    """The native check reported a usable subscription sign-in."""
    return str(status or "").startswith("Connected to ")


def usable(status):
    """The native check reported a usable sign-in or Codex API credential."""
    return signed_in(status) or status == "Native API credential configured"


def suggestions(store, statuses):
    """Detected default sign-ins to offer, in display order. Creates nothing."""
    return [dict(SUGGESTIONS[mode], mode=mode) for mode in SUGGESTIONS
            if signed_in(statuses.get(mode)) and store.suggests_native(mode)]


def connect_offers(store, statuses):
    """AI Providers' one-click Connect rows: what the chat's welcome card offers, in its order. Creates nothing.

    Every subscription whose runtime is available (``first_run.runtime_available``), signed in or not, as
    ``{"mode", "signed_in"}``. A dismissed mode is never offered; a signed-in one until a connection reuses
    its sign-in (``suggests_native``), a signed-out one until any connection of its mode exists (that
    connection's own row then offers Sign in again).
    """
    from . import first_run
    offers = []
    for mode in first_run.CONNECT:
        status = statuses.get(mode)
        if not first_run.runtime_available(mode, status) or mode in store.ignored_native_modes:
            continue
        found = signed_in(status)
        if store.suggests_native(mode) if found else not any(r["mode"] == mode for r in store.records.values()):
            offers.append({"mode": mode, "signed_in": found})
    return offers


def reuse_by_default(provider, statuses):
    """Whether the Add-connection form starts with "Use existing sign-in on this computer" ticked: only when
    ``provider``'s subscription sign-in was found here. Otherwise Connect runs the provider's own browser
    sign-in, and the form says so instead of offering a sign-in that does not exist."""
    mode = MODE_BY_CONNECTION.get((CODING, provider, "SUBSCRIPTION"))
    return bool(mode) and signed_in(statuses.get(mode))


def form_properties(mode):
    """Add-connection form values that reuse ``mode``'s default sign-in.

    Ordered: setting ``group`` resets ``provider`` and ``provider`` may reset
    ``method`` through the form's update callbacks.
    """
    spec = SUGGESTIONS[mode]
    return {"group": CODING, "provider": spec["provider"], "method": spec["method"], "reuse_existing": True}


def chats_to_refresh(store, controllers, mode, status):
    """After a default sign-in check, the connection reusing it and its chats awaiting models.

    Only chats the person already bound to that connection are returned; an
    unbound chat is never bound and no connection is ever created here.
    """
    record = store.native_connection(mode) if usable(status) else None
    if record is None:
        return None, []
    return record, [c for c in controllers if c.state.get("connection_id") == record["id"]
                    and not c.models and not c.models_loading]


def adoptable_connection(store, mode):
    """The store's active connection when it matches an unbound chat's mode.

    The only connection an unbound (legacy) chat may adopt when it sends. A
    detected sign-in without a connection never qualifies.
    """
    record = store.records.get(store.active)
    return record if record and record["mode"] == mode else None
