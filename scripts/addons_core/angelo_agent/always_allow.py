# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's "Always allow scripts" choice: where it is saved, and the consent token it stands for.

Contract: doc/angelo/agent_execution.rst, "Always allow scripts".

The choice is off by default and saved in Angelo's state folder
(``always-allow-scripts.json``), per user and install, never in a .blend or a
chat. ``set_enabled`` writes it atomically before the change takes effect, on
and off alike, so a crash or a forced quit never brings back a choice the
person revoked (Blender writes its own preferences only at a clean exit). A
missing or unreadable file reads as off. ``ANGELO_Preferences.always_allow_scripts``
is a view of it whose setter accepts only the person (trusted_scripts_ui).

The choice covers scripts only in a chat whose approval is Auto: Ask still
asks and Read only still refuses (``TrustedScripts.script_consent_kind``).
This module holds the runtime consent: a new token object each time the choice
turns on, none while it is off or while ``AllowAutoApproval`` (managed or
organization policy) is off, so policy wins over an opt-in made before it
arrived. A script carries the token current at its admission, and runs unasked
only while that same token is still current at dispatch: turning the choice
off, or a policy arriving, reaches the next script, already queued ones
included, and an old token never comes back.

Scope (2.0.1): a consent saved by 2.0.1 or later also covers connected apps' chats (External conversations), whose
scripts Angelo's agent writes; it says so (``"external": true``, held in C as ``ALWAYS_ALLOW`` key ``external``). A
consent saved by 2.0.0, given when it never applied there, does not silently start to: an External chat's first
script shows its card, whose Always allow says it covers connected apps' chats, and that press widens the saved
consent (``cover_external``). Chats of the person's own are covered as before either way.

Standard library only; no bpy. ``attach``, ``set_enabled`` and ``sync`` run on
the main thread; ``token`` may run on a provider thread at admission.
"""

import json
import os
import time

from . import grant_authority, managed_policy, owned_paths, shared_replace
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)

FILE = "always-allow-scripts.json"
_enabled = False   # The person's choice, as saved.
_external = False  # Whether it covers connected apps' chats too (a consent saved by 2.0.1 or later; module docstring).
_token = None      # The consent scripts admitted now carry; None while off or blocked by policy.
_path = None       # Where the choice is saved (ui.register: the state folder); None: in memory only (tests).
_owed = None       # When saving "off" was refused (monotonic): ``sync`` saves it again, so it never comes back on.
# Beside the file while an "off" is owed: a name no reader holds, so a launch before the save lands reads it off.
REVOKED = ".off"


def attach(directory):
    """Read the saved choice from ``directory`` (register). A missing or unreadable file is off."""
    global _path, _owed
    _path = os.path.join(os.fspath(directory), FILE)
    try:
        saved = json.loads(shared_replace.read_text(_path))   # Sharing delete: another window's save lands.
    except (OSError, ValueError):
        saved = None
    revoked = os.path.lexists(_path + REVOKED)   # An "off" whose save was refused: off, and saved again (sync).
    _owed = time.monotonic() - shared_replace.RETRY_SECONDS if revoked else None
    on = not revoked and isinstance(saved, dict) and saved.get("version") == 1 and saved.get("enabled") is True
    external = on and saved.get("external") is True
    grant_authority.mirror(grant_authority.ALWAYS_ALLOW, [("on", on), ("external", external)])   # Held in C.
    _remember(on, external)


def detach():
    """Forget the choice and its consent (unregister); registering again reads the file."""
    global _path, _owed
    if _owed is not None and _path is not None:   # One last try; a refusal leaves the marker, read at the next launch.
        try:
            _write(False)
        except OSError as error:
            print("Angelo: Always allow scripts stays off; saving it was refused again:", error)
    _path, _owed = None, None
    _remember(False)


def set_enabled(enabled):
    """The person's change (the script card's button, the Preferences toggle): saved first, then in effect.

    Raises ``OSError`` when it can't be saved; turning it off still takes
    effect for this session then, and turning it on doesn't. Raises
    ``PermissionError`` when C refuses it (on outside the person's own click,
    off from an agent script), before anything is saved or changed.
    """
    global _owed
    enabled = bool(enabled)
    if enabled == _enabled:
        return False
    if enabled:   # Held in C first: refused outside the person's own click (grant_authority).
        grant_authority.record(grant_authority.ALWAYS_ALLOW, "on", True)
        grant_authority.record(grant_authority.ALWAYS_ALLOW, "external", True)   # Given now: it says so (2.0.1).
    else:   # In C first too: C refuses an agent script's withdrawal (G2) before anything is saved or changed here.
        grant_authority.forget(grant_authority.ALWAYS_ALLOW, "on")
        grant_authority.forget(grant_authority.ALWAYS_ALLOW, "external")
    try:
        if _path is not None:
            _write(enabled, enabled)
    except OSError:
        if not enabled:
            _owed = time.monotonic()
            _mark_revoked()
        _remember(False if not enabled else _enabled, _external if enabled else False)   # On refused to save: unchanged.
        if enabled:
            grant_authority.forget(grant_authority.ALWAYS_ALLOW, "on")
            grant_authority.forget(grant_authority.ALWAYS_ALLOW, "external")
        else:
            _tell_workers()   # Off holds for this session: the worker chats stop too.
        raise
    _owed = None
    _remember(enabled, enabled)
    _tell_workers()
    return True


def cover_external():
    """The person's press of Always allow on a script card in a connected app's chat while their saved consent predates
    2.0.1 (on, not covering connected apps' chats): it covers them from now on, saved first (``OSError``: unchanged).
    ``PermissionError`` outside the person's own press, as turning it on."""
    global _external
    if not _enabled or _external:
        return False
    grant_authority.record(grant_authority.ALWAYS_ALLOW, "external", True)
    try:
        if _path is not None:
            _write(True, True)
    except OSError:
        grant_authority.forget(grant_authority.ALWAYS_ALLOW, "external")
        raise
    _external = True
    _tell_workers()
    return True


def covers_external():
    """Whether the person's Always allow covers connected apps' chats: on, saved by 2.0.1 or later (or widened there
    by their press), and held so in C. The policy still applies through ``token``."""
    return _enabled and _external and grant_authority.holds(grant_authority.ALWAYS_ALLOW, "external")


def _tell_workers():
    """The lead's worker chats hosted in their workers carry this consent in their lease: every change reaches them
    (worker_consent_lead.changed), whichever path made it (Preferences, a card's button), as Trust scripts does."""
    from . import worker_consent_lead
    worker_consent_lead.changed()


set_enabled = _grant_guard.entry(set_enabled, widens="enabled")
cover_external = _grant_guard.entry(cover_external)


def _write(enabled, external=False):
    temporary = _path + ".tmp"
    try:
        owned_paths.require_writable(_path)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "enabled": enabled, "external": bool(enabled and external)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, _path)   # Refused while held (shared_replace): raised, the old file stays.
    except OSError:
        shared_replace.discard(temporary)
        raise
    try:
        os.unlink(_path + REVOKED)   # The file says what the person chose now.
    except FileNotFoundError:
        pass
    except OSError as error:   # Left: the next launch reads it off (the safe side) until a save removes it.
        print("Angelo: Always allow scripts' owed-off marker could not be removed:", error)
    if os.name != "nt":   # The rename itself survives a crash once its folder is synced.
        folder = os.open(os.path.dirname(_path), os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def _mark_revoked():
    try:
        owned_paths.require_writable(_path)
        os.close(os.open(_path + REVOKED, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600))
    except OSError as error:
        print("Angelo: Always allow scripts is off for this session only (its save was refused):", error)


def _remember(enabled, external=None):
    global _enabled, _token, _external
    _enabled = bool(enabled)
    _external = _enabled and bool(_external if external is None else external)
    if not _enabled:
        grant_authority.forget(grant_authority.ALWAYS_ALLOW, "on")   # Narrowing: Angelo's own (C refuses a script, G2).
        grant_authority.forget(grant_authority.ALWAYS_ALLOW, "external")
    _token = object() if _enabled and managed_policy.current().allow_auto_approval else None


def sync():
    """Main thread (ui.pump): follow the policy, which can arrive or lift at any time, and save an "off" whose save
    was refused, no sooner than every ``shared_replace.RETRY_SECONDS`` (never at every tick)."""
    global _owed
    if (_token is None) == (_enabled and managed_policy.current().allow_auto_approval):
        _remember(_enabled)
    if _owed is not None and _path is not None and time.monotonic() - _owed >= shared_replace.RETRY_SECONDS:
        try:
            _write(_enabled, _external)
            _owed = None
        except OSError:   # Said when it was refused first (set_enabled); tried again later.
            _owed = time.monotonic()


def enabled():
    """Whether the person's choice is on (the policy may still block it: see ``token``)."""
    return _enabled


def token():
    """The consent a script admitted now carries, or None: off, or ``AllowAutoApproval`` is off.

    A policy that blocks it discards the token, so scripts admitted before the
    policy never run unasked after it lifts; ``sync`` makes a new one then.
    """
    global _token
    current = _token
    if current is not None and not managed_policy.current().allow_auto_approval:
        _token = current = None
    if current is not None and not grant_authority.holds(grant_authority.ALWAYS_ALLOW, "on"):
        return None   # The choice as held in C: Python's own copy alone never runs a script unasked.
    return current
