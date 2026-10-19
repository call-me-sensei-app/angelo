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

Standard library only; no bpy. ``attach``, ``set_enabled`` and ``sync`` run on
the main thread; ``token`` may run on a provider thread at admission.
"""

import json
import os

from . import managed_policy

FILE = "always-allow-scripts.json"
_enabled = False   # The person's choice, as saved.
_token = None      # The consent scripts admitted now carry; None while off or blocked by policy.
_path = None       # Where the choice is saved (ui.register: the state folder); None: in memory only (tests).


def attach(directory):
    """Read the saved choice from ``directory`` (register). A missing or unreadable file is off."""
    global _path
    _path = os.path.join(os.fspath(directory), FILE)
    try:
        with open(_path, encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        saved = None
    _remember(isinstance(saved, dict) and saved.get("version") == 1 and saved.get("enabled") is True)


def detach():
    """Forget the choice and its consent (unregister); registering again reads the file."""
    global _path
    _path = None
    _remember(False)


def set_enabled(enabled):
    """The person's change (the script card's button, the Preferences toggle): saved first, then in effect.

    Raises ``OSError`` when it can't be saved; turning it off still takes
    effect for this session then, and turning it on doesn't.
    """
    enabled = bool(enabled)
    if enabled == _enabled:
        return False
    try:
        if _path is not None:
            _write(enabled)
    except OSError:
        if not enabled:
            _remember(False)
        raise
    _remember(enabled)
    return True


def _write(enabled):
    temporary = _path + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "enabled": enabled}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, _path)
    if os.name != "nt":   # The rename itself survives a crash once its folder is synced.
        folder = os.open(os.path.dirname(_path), os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def _remember(enabled):
    global _enabled, _token
    _enabled = bool(enabled)
    _token = object() if _enabled and managed_policy.current().allow_auto_approval else None


def sync():
    """Main thread (ui.pump): follow the policy, which can arrive or lift at any time."""
    if (_token is None) == (_enabled and managed_policy.current().allow_auto_approval):
        _remember(_enabled)


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
    return current
