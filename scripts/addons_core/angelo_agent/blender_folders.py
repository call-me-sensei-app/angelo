# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a stock Blender keeps a person's settings: the one place Angelo names Blender's own user folder.

Angelo has its own user folder (``doc/angelo/app_identity.rst``); two features still have to find Blender's:
the explicit "Use my Blender settings" import (``settings_import``) and the one-time agent-state move
(``scripts/angelo/migrate_user_folder.py``). Both read these names; neither writes into Blender's folder.

* macOS: ``~/Library/Application Support/Blender/<version>``
* Windows: ``%APPDATA%\\Blender Foundation\\Blender\\<version>``
* Linux and other Unix: ``$XDG_CONFIG_HOME/blender/<version>`` (default ``~/.config/blender/<version>``)

The ``BLENDER_USER_*`` variables keep Blender's names (Angelo honours them too), so a person who set one already
points both apps at the same folder for that part. Standard library only: host tests and the migration command
use it without Blender.
"""

from pathlib import Path

FOLDER_MAC = "Blender"
FOLDER_WINDOWS = ("Blender Foundation", "Blender")
FOLDER_UNIX = "blender"

# Either one makes the whole settings folder an explicit, shared one: nothing is Blender's alone.
SHARED_VARIABLES = ("BLENDER_USER_RESOURCES", "BLENDER_USER_CONFIG")
# The per-version folder's parts and the variable that moves each one.
PART_VARIABLES = (("config", "BLENDER_USER_CONFIG"), ("scripts", "BLENDER_USER_SCRIPTS"),
                  ("datafiles", "BLENDER_USER_DATAFILES"), ("extensions", "BLENDER_USER_EXTENSIONS"))
PARTS = tuple(name for name, _ in PART_VARIABLES)

# Blender's own window for earlier settings (``PREFERENCES_OT_copy_prev.previous_version``): every minor release of
# the current major, then the previous major counting down from this minor.
PREVIOUS_MAJOR_FROM_MINOR = 10


def base(platform, home, environ):
    """The folder holding Blender's per-version folders (``.../Blender``) for ``platform`` (``sys.platform``)."""
    home = Path(home)
    if platform == "darwin":
        return home / "Library/Application Support" / FOLDER_MAC
    if platform == "win32":
        return Path(environ.get("APPDATA") or home / "AppData/Roaming").joinpath(*FOLDER_WINDOWS)
    return Path(environ.get("XDG_CONFIG_HOME") or home / ".config") / FOLDER_UNIX


def version_folder(platform, home, environ, version):
    """Blender's per-version user folder; ``version`` is ``(major, minor)`` or ``"5.2"``."""
    return base(platform, home, environ) / version_name(version)


def version_name(version):
    return version if isinstance(version, str) else "%d.%d" % tuple(version[:2])


def versions(current):
    """``(major, minor)`` newest first: ``current`` itself, every earlier minor of its major, then the previous
    major from ``.10`` down (the window Blender's own import searches)."""
    major, minor = current[:2]
    found = [(major, number) for number in range(minor, -1, -1)]
    if major > 0:
        found += [(major - 1, number) for number in range(PREVIOUS_MAJOR_FROM_MINOR, -1, -1)]
    return found


def shared_variable(environ):
    """The variable that makes Angelo and Blender share one explicit settings folder, or None."""
    return next((name for name in SHARED_VARIABLES if environ.get(name)), None)


def shared_parts(environ):
    """The parts (``scripts``, ``datafiles``, ``extensions``) a ``BLENDER_USER_*`` variable already shares."""
    return tuple(part for part, variable in PART_VARIABLES if environ.get(variable))
