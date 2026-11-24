# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's side of the grant authority held in C (doc/angelo/grant_operators.rst, "Authority in C").

Who may grant, and what has been granted, is decided by ``_bpy._angelo_guard``, which an agent script can call but not
change (the Copy setup break-it re-review, 2026-11-14: every guard Python held could be undone by Python). This module
is the add-on's one door to it:

* ``GUARD`` is the C module, read once at import (before any script). Without it (a build that lacks it) every grant
  is refused: nothing falls back to Python. Host tests stand a Python twin in its place (``angelo_guard_twin`` in the
  test suite), never the app, where the C module always wins.
* The ledger's standard namespaces (``APPROVED_SCRIPTS`` ... ``APPROVAL_MODES``) and helpers to read and write them.
  A write that widens takes effect only inside a grant scope (a ticket from the person's aimed click, or Angelo's own
  automatic decision) and never while an agent script runs; anything else raises ``PermissionError``.
* A script runs (``run_confined``) only when ``APPROVED_SCRIPTS`` holds its exact text for its call, once:
  ``approve_script`` writes it, from the person's Approve or from a standing consent read here (``holds``).
"""

import hashlib
import sys
import types

APPROVED_SCRIPTS = "approved_scripts"   # "<chat id>/<call id>" -> SHA-256 of the script's text.
TRUSTED_CHATS = "trusted_scripts"       # chat id -> True.
ALWAYS_ALLOW = "always_allow"           # "on" -> True.
CHAT_CLIENTS = "chat_clients"           # chat id -> the external client it belongs to.
APPROVED_CALLS = "approved_calls"       # "<chat id>/<call id>" -> the invocation digest the approval was for.
APPROVAL_MODES = "approval_modes"       # chat id -> "AUTO_BOUNDED" while the chat is in Auto (Ask narrows).
REVIEWED_SCRIPTS = "reviewed_scripts"   # "<project>/<sha256>" -> True: Always run this exact script (2.1.0).
CONTEXT_FOLDERS = "context_folders"     # chat id -> SHA-256 of the real path of its Folder as context (2.1.0).
RULE_DECISIONS = "rule_decisions"       # A rules text's SHA-256 -> True (Use) or False (Don't use) (2.1.0).
GLOBAL_RULES = "global_rules"           # "text" -> SHA-256 of the All projects rules (2.1.0).
NO_AUTHORITY = "This build has no grant authority: nothing can be granted."


def _native():
    try:
        import _bpy
    except ImportError:
        _bpy = None
    guard = getattr(_bpy, "_angelo_guard", None) if _bpy is not None else None
    if isinstance(guard, types.ModuleType) and callable(getattr(guard, "take", None)):
        return guard
    # Host tests only (suite_isolation installs it; their _bpy, if any, is a stand-in). Read once at import, when
    # Angelo loads, before any agent script can run, and never where the C module exists.
    return sys.modules.get("angelo_guard_twin")


GUARD = _native()


def available():
    return GUARD is not None


def _guard():
    if GUARD is None:
        raise PermissionError(NO_AUTHORITY)
    return GUARD


def call_key(chat_id, call_id):
    """The ledger's key for a script call; a chat id never holds a "/"."""
    return "%s/%s" % (chat_id, call_id)


def text_digest(source):
    """SHA-256 of a script's text, as ``run_confined`` digests it in C."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def holds(namespace, key):
    """Whether the ledger holds True for ``key``: a standing consent read from C, never from Python state."""
    return GUARD is not None and GUARD.get(namespace, key) is True


def value(namespace, key, default=None):
    return default if GUARD is None else GUARD.get(namespace, key, default)


def record(namespace, key, value):
    """Sets ``key``; True when it changed. Widening raises ``PermissionError`` outside a grant scope."""
    return _guard().set(namespace, key, value)


def forget(namespace, key):
    """Removes ``key``: narrowing, taken from anything but an agent script (PermissionError then)."""
    return GUARD is not None and GUARD.remove(namespace, key)


def granting():
    return GUARD is not None and GUARD.granting()


def automatic(function, *args, **kwargs):
    """``function`` as Angelo's own decision, inside a grant scope (refused while an agent script runs)."""
    return _guard().automatic(function, *args, **kwargs)


def approve_script(chat_id, call_id, source):
    """The ledger's approval of this exact text for this call (run once). Needs a grant scope."""
    return record(APPROVED_SCRIPTS, call_key(chat_id, call_id), text_digest(source))


def withdraw_script(chat_id, call_id):
    return forget(APPROVED_SCRIPTS, call_key(chat_id, call_id))


def mirror(namespace, pairs):
    """Angelo's own copy of consents it read from its private files (only Angelo writes them; the confinement keeps
    agent scripts out) into the ledger. Refused while an agent script runs; nothing is mirrored then."""
    if GUARD is None:
        return False
    try:
        GUARD.automatic(lambda: [GUARD.set(namespace, key, value) for key, value in pairs])
    except PermissionError:
        return False
    return True


def call_approved(chat_id, call):
    """Whether C holds the approval of ``call`` (its exact invocation) in chat ``chat_id``: what Angelo checks before
    it commits a staged change, never the call's own Python flags alone (the C guard review, SHOULD-4)."""
    digest = getattr(call, "invocation_digest", None)
    return bool(digest) and value(APPROVED_CALLS, call_key(chat_id, call.id)) == digest


def chat_in_auto(chat_id):
    """Whether C holds the chat's Auto (approval mode): Angelo's own automatic approvals need it as well."""
    return value(APPROVAL_MODES, chat_id) == "AUTO_BOUNDED"
