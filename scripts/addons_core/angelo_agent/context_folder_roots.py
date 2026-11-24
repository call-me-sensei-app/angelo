# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which folders a chat may be given as context, whoever picks them (``context_folder``; 2.1.0 break-it review F-1,
round 2 N-4, round 3 S-2 and its notes).

Refused: a folder that holds keys, credentials or a repository's internals, or where the platform keeps apps'
settings and private data (``PRIVATE``); the home folder, a folder holding it or one of those, or a whole drive
(``TOO_WIDE``). Judged as the system spells each path, never as typed, with the environment as Angelo started.
Standard library only; no bpy.
"""

import os
import tempfile
from pathlib import Path

from . import files, private_conventions


PRIVATE, TOO_WIDE, UNCHECKED = "private", "too_wide", "unchecked"   # FolderRefused.reason
# A folder with a mount point below it holds whole drives or shares: too wide, wherever it is (re-read of
# 1c2c4edaf48, S-1). With no mount table (Windows), children and grandchildren are looked at, up to SCAN_CAP folders.
SCAN_CAP = 4096


class FolderRefused(ValueError):
    """A folder no chat may read, whoever picks it (the 2.1.0 break-it review, F-1): one that holds keys, credentials
    or a repository's internals, or where the platform keeps apps' settings and private data (``PRIVATE``), or the
    home folder, a folder holding it, or a whole drive (``TOO_WIDE``), where those live too. An agent can ask for
    exactly that pick; the person can still pick a project inside a too-wide one."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# Read once, as Angelo starts (round 3, NIT f): code that changes the environment later (a script) moves no folder
# out of the refusal.
_HOME = os.path.expanduser("~")
_ENVIRON = {name: value for name in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "SystemRoot", "XDG_CONFIG_HOME",
                                     "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
            if (value := os.environ.get(name))}
_TEMP = tempfile.gettempdir()
# macOS's per-user cache and temporary parent (every app's caches, C/; the temporary folder T/ stays usable): N-2.
_USER_FOLDERS = "/private/var/folders"


def _home():
    return _HOME


def _platform():
    import sys
    return sys.platform


def _spelled(path, strict=False):
    """``path`` as the system spells it, never as typed (round 3, S-2; ``private_conventions.canonical``). ``strict``
    for the chosen folder: one macOS can't open is never judged by its typed spelling (re-read of 23413c6bae1, S-1)."""
    return Path(private_conventions.canonical(Path(str(path)), _platform(), strict=strict))


def _app_data_roots(home):
    """(roots, the person's own folders inside them): where the platform keeps apps' settings and private data (round 2,
    N-4; ``private_conventions`` and more), and macOS's iCloud and file-provider folders and the temporary folder where
    the platform keeps it inside one (Windows; round 3, NIT e), as ``private_conventions`` keeps them."""
    platform, stores = _platform(), []
    if platform == "darwin":
        roots = [home / "Library", "/Library", "/System", "/private/etc", "/private/var/db", "/private/var/root",
                 _USER_FOLDERS]
        stores = [home / "Library" / "Mobile Documents", home / "Library" / "CloudStorage"]
    elif platform == "win32":
        roots = [home / "AppData", *(_ENVIRON.get(name) for name in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                                                                     "SystemRoot"))]
    else:
        roots = [Path(_ENVIRON.get(name) or home / default) for name, default in (
            ("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state"),
            ("XDG_CACHE_HOME", ".cache"))] + ["/etc", "/var/lib", "/root", "/proc", "/sys", "/dev"]   # N-2.
    roots = [os.path.normcase(str(_spelled(root))) for root in roots if root]
    stores = [os.path.normcase(str(_spelled(store))) for store in stores]
    temp = os.path.normcase(str(_spelled(_TEMP)))
    # Only the platform's own temporary folder (macOS .../T, Windows ...\Temp), never a parent TMPDIR names (N-4).
    if any(_within(temp, root) for root in roots) and Path(temp).name.lower() in ("t", "temp"):
        stores.append(temp)
    return roots, stores


def _within(path, root):
    """Whether ``path`` is ``root`` or inside it, both as the platform compares paths (Windows: case and separators,
    whichever side was spelled how; the VM CI of 8e67f484400)."""
    path, root = os.path.normcase(path), os.path.normcase(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _deepest(path, folders):
    """The length of the narrowest of ``folders`` holding ``path``, or -1."""
    return max((len(folder) for folder in folders if _within(path, folder)), default=-1)


def _mount_points():
    """Every mount point from the system's own table (``private_conventions.mount_points``: Linux's mountinfo,
    macOS's getfsstat), or None where there is none to read."""
    return private_conventions.mount_points(platform=_platform())


def _mount_below(real):
    """Whether a mount point lies below ``real`` (a drive or a share inside it). With no mount table, its folders
    and theirs are looked at, up to ``SCAN_CAP`` folders; one it can't finish is too wide (fails closed, S-2)."""
    mounts = _mount_points()
    if mounts is not None:
        prefix = str(real).rstrip(os.sep) + os.sep
        return any(mount.startswith(prefix) for mount in mounts)
    seen, level = 0, [str(real)]
    for _depth in range(2):
        below = []
        for folder in level:
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        seen += 1
                        if seen > SCAN_CAP or os.path.ismount(entry.path):
                            return True
                        below.append(entry.path)
            except OSError:   # Unreadable now is unreadable to the file tools too; a drive that becomes
                continue      # readable later is refused at each read by its device (context_folder).
        level = below
    return False


def vet_root(folder):
    """``FolderRefused`` for a folder no chat may read, judged as the system spells it; None otherwise. Hidden and
    credential folders are judged below the home folder when the folder is in it, so a home whose own path has a dot
    is still a home. A folder holding one where apps keep private data is too wide, as the home folder is."""
    try:
        real = _spelled(folder, strict=True)
    except OSError:
        raise FolderRefused(UNCHECKED) from None
    home = _spelled(_home())
    parts = real.relative_to(home).parts if home in real.parents else real.parts[1:]   # Past the root or drive.
    if any(part.startswith(".") or part in files.BLOCKED for part in parts):
        raise FolderRefused(PRIVATE)
    roots, stores = _app_data_roots(home)
    # The narrowest decides: a store exempts only what no narrower root inside it holds (re-read of 1c2c4edaf48, S-2).
    if _deepest(str(real), roots) > _deepest(str(real), stores):
        raise FolderRefused(PRIVATE)
    if (real == home or real in home.parents or not real.parts[1:] or os.path.ismount(str(real))
            or any(_within(root, str(real)) for root in roots) or _mount_below(real)):
        raise FolderRefused(TOO_WIDE)
