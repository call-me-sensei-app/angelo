# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's Angelo preferences in the headless child, read-only (doc/angelo/external_mcp.rst, "Preferences").

The child matches Angelo's window: the add-ons, extensions and script folders the person turned on and their render
devices. The launcher copies the person's ``userpref.blend`` into the child's private folder (``stage``) and starts
the child without ``--factory-startup`` and with ``BLENDER_USER_CONFIG`` pointing at that copy's folder, so Blender
itself loads them as the window does. Read-only by construction: the person's file is only ever opened for reading,
and anything that saves preferences in the child (Blender, an add-on, an approved script) writes the copy, which is
removed with the private folder. The child also turns preference saving off in memory (``protect``).

When there is no file, it cannot be read, Blender cannot read it, or the child does not start with it, the child runs
with factory settings (``--factory-startup``, as before) and every result says so (``describe``).

The person's folder is the one Angelo's window uses by default, found from the account's home folder
(``CFFIXED_USER_HOME`` on macOS, as Blender's own lookup honours it; ``HOME`` elsewhere), never from a
``BLENDER_USER_*`` variable in the connected app's environment: the app never chooses what the child loads.
Launcher half: standard library only, no ``bpy``.
"""

import json
import os
import stat
from pathlib import Path

USERPREF = "userpref.blend"
CONFIG = "config"                 # The child's own config folder, inside its private folder.
MAX_BYTES = 64 * 1024 * 1024      # A preferences file is well under a megabyte; anything this big is not one.
YOURS = "yours"
# Why the child runs with factory settings: the code the launcher passes (``--preferences``) and the result's words.
FACTORY = {
    "missing": "no saved preferences were found",
    "unreadable": "yours could not be read",
    "not_blend": "yours could not be read: Blender could not read the file",
    "start_failed": "Angelo did not start with yours",
}
NOT_READABLE = "preferences_unreadable"   # The child's ready code when Blender cannot read the copy.
USED = "Preferences: yours ({count} add-ons on, Blender's defaults included)"
USED_FAILED = "Preferences: yours ({count} add-ons on, Blender's defaults included; did not load: {failed})"
FACTORY_USED = "Preferences: factory settings ({why})"


# A start with the person's preferences gets its own budget (break-it F4, 2026-10-23: an add-on that hung cost the
# full 120 s ready timeout, then a factory start, on every start). Blender reaches Angelo's own start-up (the child
# writes child.pid) within a few seconds even with many add-ons, so STARTUP_BUDGET is generous; READY_BUDGET also
# covers opening the file with them. Both plus a factory start fit a 60 s MCP tool timeout (Codex's default) for an
# ordinary file. A file that takes longer than READY_BUDGET to open with the person's add-ons opens with factory
# settings instead, and says so.
STARTUP_BUDGET = 20.0
READY_BUDGET = 35.0
FILE_PROBLEMS = ("invalid", "stale", "output_unwritable")   # A start that failed for the file, not the preferences.
_failed = {}   # The person's preferences file identity -> why its start failed: for this launcher's life.


def known_failure(config_directory):
    """Why a start with this preferences file failed before, while the file is unchanged, else None."""
    from .headless_files import identity
    key = identity(Path(config_directory) / USERPREF)
    return _failed.get(key) if key else None


def remember_failure(config_directory, code):
    from .headless_files import identity
    key = identity(Path(config_directory) / USERPREF)
    if key:
        _failed[key] = code


def failed(ready):
    """Whether a start with the person's preferences failed for them (no ready, a crash, a hang, an error that is
    not the file's): the launcher then starts with factory settings."""
    return not ready or (not ready.get("ok") and ready.get("code") not in FILE_PROBLEMS)


def wait_ready(child, private, code):
    """The child's ready record, or None; a start with the person's preferences within its budgets."""
    if code != YOURS:
        return child.wait_ready()
    import time
    started, marker = time.monotonic(), Path(private) / "child.pid"
    while True:
        ready = child.wait_ready(0.1)
        if ready is not None or not child.alive():
            return ready
        elapsed = time.monotonic() - started
        if elapsed >= READY_BUDGET or (elapsed >= STARTUP_BUDGET and not marker.exists()):
            return None


def person_config_directory(environ, platform, home=None):
    """The config folder Angelo's window uses by default for this account (where its ``userpref.blend`` is)."""
    from .cli_client import _IDENTITY_FILE, BLENDER_SERIES
    names = json.loads(_IDENTITY_FILE.read_text(encoding="utf-8"))["paths"]
    if home is None:
        home = environ.get("CFFIXED_USER_HOME") if platform == "darwin" else environ.get("HOME")
        if not home:
            import pwd
            home = pwd.getpwuid(os.getuid()).pw_dir
    home = Path(home)
    if platform == "darwin":
        return home / "Library/Application Support" / names["user_dir_name"] / BLENDER_SERIES / CONFIG
    return home / ".config" / names["user_dir_name_unix"] / BLENDER_SERIES / CONFIG


def stage(source_directory, private):
    """Copy the person's preferences into ``private``; returns ``YOURS`` or a ``FACTORY`` code.

    Opens the person's file for reading only (a link to it is followed, as Blender follows it) and writes a new
    owner-only file in the private folder; anything but a regular file of a sane size counts as unreadable."""
    source = Path(source_directory) / USERPREF
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            return "unreadable"
        data = b""
        while len(data) <= MAX_BYTES:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_BYTES:
            return "unreadable"
    except OSError:
        return "unreadable"
    finally:
        os.close(descriptor)
    folder = Path(private) / CONFIG
    try:
        folder.mkdir(mode=0o700)
        target = os.open(folder / USERPREF, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o600)
        try:
            os.write(target, data)
        finally:
            os.close(target)
    except OSError:
        return "unreadable"
    return YOURS


def describe(code, count=0, failed=()):
    """The ready record's ``preferences``: which preferences the child uses, in words for the app."""
    if code == YOURS:
        note = (USED_FAILED.format(count=count, failed=", ".join(failed)) if failed
                else USED.format(count=count))
        return {"used": YOURS, "addons_on": count, "addons_failed": list(failed), "note": note}
    if code is None:   # A launcher that passed no preferences at all.
        return {"used": "factory", "note": "Preferences: factory settings"}
    return {"used": "factory", "note": FACTORY_USED.format(why=FACTORY.get(code, FACTORY["unreadable"]))}


# Child half (inside ``Angelo --background``) -----------------------------------------------------------------------

def protect(bpy):
    """Turn preference saving off in memory, first thing the child's own code does. Belt and braces: Blender never
    saves preferences when a background process exits, and any explicit save lands in the private copy."""
    bpy.context.preferences.use_preferences_save = False
    bpy.app.use_userpref_skip_save_on_exit = True


def copy_readable(bpy, private):
    """Whether Blender reads the child's copy as a .blend (a corrupt file makes Blender use factory settings without
    saying so; this read says so, with Blender's own reader)."""
    try:
        with bpy.data.libraries.load(str(Path(private) / CONFIG / USERPREF)) as (_source, _target):
            pass
    except (OSError, ValueError, RuntimeError):
        return False
    return True


def addons(bpy, own):
    """``(on, failed)``: how many of the person's enabled add-ons are on, and the names of those that did not load
    (an exception in their register, a missing module): reported, never fatal. Angelo's own add-on is not counted."""
    import addon_utils
    enabled = [addon.module for addon in bpy.context.preferences.addons if addon.module and addon.module != own]
    failed = [name for name in enabled if addon_utils.check(name)[1] is not True]
    return len(enabled) - len(failed), failed
