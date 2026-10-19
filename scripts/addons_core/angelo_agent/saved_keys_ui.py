# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread glue between AI Providers connections and saved API keys.

``ui`` (the add-on's UI module) stays the owner of in-memory keys, connection
checks and controllers. A key read from the OS key store (macOS Keychain,
Windows Credential Manager or the Linux keyring; see ``key_store``) goes
through exactly the path of a key typed into the connection form:
``ui.replace_connection_key`` (validation, catalog/runtime invalidation, chat
binding) and, for HTTP connections, the same non-billable connection check.
Loading never selects a connection for a chat, changes billing or falls back to
another account. When the worker's probe finds the store unusable, the session
switches to memory-only with that reason, as on a platform without a store.

Only API-key connections whose key the app itself holds use this module. The
OpenAI coding API mode keeps its key in Codex's own OS keyring, and
subscription sign-ins stay inside the official CLIs.

No function here does I/O; draw code may call the status helpers. Every
user-facing sentence names this platform's store through ``key_store.StoreText``
and is a ``display_text.Text`` (English for tests and logs, translated where the
UI shows it with ``display_text.shown``).
"""

import os
import sys

from . import key_store
from .display_text import Text
from .key_store import MEMORY_ONLY, SERVICE, UNUSABLE
from .provider_registry import HTTP_MODES, spec_for
from .saved_keys import FAILURES, SavedKeys, profile_id
from .ui_text import n_

# This OS's store names, for labels fixed at registration (operator and property names).
TEXT = key_store.store_text()
_text = TEXT  # The open store's names; ``start`` may choose another platform in tests.
_vault = None
_reason = MEMORY_ONLY
_notices = []
NOTICE_LIMIT = 5


def store_text():
    return _text


def _load_status(outcome):
    text = _text
    return {
        "running": Text(n_("Loading saved key from {name}…"), name=text.name),
        "waiting": text.waiting,
        "approval": text.approval,
        "denied": Text(n_("{title} access denied · re-enter the key"), title=text.title),
        "unavailable": Text(n_("{title} unavailable · re-enter the key"), title=text.title),
        "failed": Text(n_("{title} error · re-enter the key"), title=text.title),
        "timeout": Text(n_("{title} didn't respond · re-enter the key"), title=text.title),
        "rejected": Text(n_("Saved key is not a valid API key · re-enter the key")),
    }.get(outcome)


def _short_reason(outcome):
    text = _text
    return {"approval": text.locked, "denied": Text(n_("access denied")),
            "unavailable": Text(n_("{title} unavailable"), title=text.title),
            "failed": Text(n_("{title} error"), title=text.title),
            "timeout": Text(n_("no response")), "too_long": Text(n_("key too long"))}.get(outcome, outcome)


def uses_saved_key(record):
    return record["method"] == "API_KEY" and record["mode"] != "codex_api"


def start(ui, *, background, environ=None, platform=None, backend=None):
    """Open this profile's saved keys and queue prompt-free loads. True when enabled.

    Only local checks run here (the main thread); the store's own availability
    check and every read run on the SavedKeys worker.
    """
    global _vault, _reason, _text
    close()
    platform = sys.platform if platform is None else platform
    _text = key_store.store_text(platform)
    if backend is None:
        backend, reason = key_store.open_store(os.environ if environ is None else environ, platform, background)
        if backend is None:
            _reason = reason
            return False
    _vault, _reason = SavedKeys(backend, profile_id(ui.connection_directory())), ""
    for record in ui.connections().records.values():
        if uses_saved_key(record) and not ui._memory_keys.get(record["id"]):
            _vault.load(record["id"])
    return True


def close():
    global _vault, _reason
    if _vault is not None:
        _vault.close()
    _vault, _reason = None, MEMORY_ONLY
    _notices.clear()


def _switch_to_memory_only(reason):
    """The store proved unusable for this session: same state as a platform without one."""
    global _vault, _reason
    _vault.close()
    _vault, _reason = None, Text(UNUSABLE, reason=reason)


def available():
    return _vault is not None


def unavailable_reason():
    return _reason


def label_for(record):
    return "%s · %s" % (SERVICE, spec_for(record["mode"])["name"])


def remember(record, secret):
    """Save (create or replace) this connection's key. No-op when the store is off."""
    if _vault is not None and uses_saved_key(record):
        _vault.save(record["id"], secret, label_for(record))


def forget(identity):
    """Delete a saved key, e.g. after removal or choosing memory-only. No-op when off."""
    if _vault is not None:
        _vault.forget(identity)


def load(record, interactive=False):
    if _vault is None:
        raise RuntimeError(_reason)
    if not uses_saved_key(record):
        raise ValueError(Text(n_("This connection doesn't use a saved API key")))
    if _vault.busy(record["id"]):
        raise RuntimeError(Text(n_("A saved-key request for this connection is still running")))
    _vault.load(record["id"], interactive=interactive)


def mark_memory_only(identity):
    """A new connection whose key the person chose not to remember."""
    if _vault is not None:
        _vault.stored[identity] = False


def discard(identity):
    if _vault is not None:
        _vault.discard(identity)


def is_remembered(identity):
    """True/False when known; None when a saved key may exist but couldn't be read."""
    return _vault.stored.get(identity) if _vault is not None else False


def can_load(record, has_key):
    """Offer the explicit (prompting) load for a saved key this build couldn't read."""
    if _vault is None or not uses_saved_key(record) or _vault.busy(record["id"]):
        return False
    state = _vault.state.get(record["id"])
    if state is None or _vault.stored.get(record["id"]) is not None:
        return False
    # With a key in memory, only to approve this build for the key it just saved.
    return not has_key or state == ("save", "saved_needs_approval")


def missing_key_status(identity):
    """Status for a connection without a key in memory, or None for the default text."""
    if _vault is None:
        return None
    kind, outcome = _vault.state.get(identity, (None, None))
    return _load_status(outcome) if kind == "load" else None


def storage_label(record, has_key):
    """Where this connection's key lives, for the AI Providers list; None when not applicable."""
    if not uses_saved_key(record):
        return None
    if _vault is None:
        return _reason if has_key else None
    kind, outcome = _vault.state.get(record["id"], (None, None))
    name = _text.name
    if kind == "save" and outcome == "running":
        return Text(n_("Saving key to {name}…"), name=name)
    if kind == "save" and outcome == "saved_needs_approval":
        return Text(n_("Key saved in {name} · this build needs approval to read it (Load Saved Key)"), name=name)
    if kind == "forget" and outcome == "erased":
        return Text(n_("Saved key erased · this build can't delete the empty {name} item; delete it in {manager}"),
                    name=name, manager=_text.manager)
    if kind == "save" and outcome in FAILURES:
        return Text(n_("Not saved in {name} ({reason}) · key kept in memory for this session"),
                    name=name, reason=_short_reason(outcome))
    if kind == "forget" and outcome == "running":
        return Text(n_("Removing saved key from {name}…"), name=name)
    if kind == "forget" and outcome in FAILURES:
        return Text(n_("Couldn't remove the saved key from {name} ({reason})"), name=name, reason=_short_reason(outcome))
    stored = _vault.stored.get(record["id"])
    if stored is True:
        return Text(n_("Key saved in {name}"), name=name)
    if has_key and stored is False:
        return Text(n_("Key in memory only · enter it again after restarting"))
    return None


def notices():
    return list(_notices)


def _notice(identity, outcome):
    manager = _text.manager
    if outcome.startswith("erased"):
        text = Text(n_("A removed connection's saved key was erased, but this build can't delete its empty "
                       "“{service}” item (account {account}…); delete it in {manager} if you like."),
                    service=SERVICE, account=identity, manager=manager)
    else:
        text = Text(n_("Couldn't remove a removed connection's saved key ({reason}). In {manager}, delete the "
                       "“{service}” item whose account starts with {account}."),
                    reason=_short_reason(outcome), manager=manager, service=SERVICE, account=identity)
    _notices.append(text)
    del _notices[:-NOTICE_LIMIT]


def pump(ui):
    """Apply finished store work on the main thread. True when anything changed."""
    if _vault is None:
        return False
    changes = _vault.pump()
    for identity, kind, outcome, secret in changes:
        record = ui.connections().records.get(identity)
        if record is None:
            if kind == "forget" and (outcome in FAILURES or outcome.startswith("erased")):
                _notice(identity, outcome)
            _vault.discard(identity)
            continue
        if kind == "load" and outcome == "loaded":
            _apply_loaded_key(ui, record, secret)
    if _vault.unavailable:
        _switch_to_memory_only(_vault.unavailable)
        return True
    return bool(changes)


def _apply_loaded_key(ui, record, secret):
    identity = record["id"]
    if ui._memory_keys.get(identity):
        return  # A key typed meanwhile wins; never replace it with an older saved one.
    try:
        ui.replace_connection_key(record, secret)
    except (ValueError, RuntimeError):
        _vault.state[identity] = ("load", "rejected")
        _vault.stored[identity] = None
        return
    if record["mode"] in HTTP_MODES:
        try:
            ui._api_connections.start(record, secret)
        except (RuntimeError, ValueError):
            pass  # A check already running for this connection reports on its own.
