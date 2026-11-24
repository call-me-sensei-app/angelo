# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where applications keep their state by the operating system's convention: private to file tools by default.

A registry of the folders Angelo knows it wrote (``private_roots``) can miss one (break-it r2m4c round 7: a reused
CLI home, an older version's state, a converter's scratch). So file tools also refuse, whoever wrote there:

* macOS: everything under ``~/Library``, except the person's own document stores there (``Mobile Documents``, iCloud
  Drive, and ``CloudStorage``, the file-provider folders of Dropbox, OneDrive, Google Drive and the like);
* Windows: everything under ``%APPDATA%`` and ``%LOCALAPPDATA%``, except the temporary folder (where Explorer opens
  archives; on every platform a project there keeps its file tools, and Angelo's own folders in it are registered);
* every hidden entry directly in the home folder (``~/.codex``, ``~/.claude``, ``~/.config``, ``~/.local``, ...);
* every folder named like an Angelo native CLI profile family (``codex-profile``, ``claude-profile``), wherever it
  is: the form every saved native profile has (``storage.valid_profile_directory``).

Paths are compared as the system spells them (``files._canonical_path`` of an opened folder) by the system's own path
rule (``os.path.normcase``), so a case or Unicode spelling cannot step around a rule.
"""

import os
import re
import sys
from pathlib import Path

from . import storage


def _strip(text):
    """A Windows final path without its ``\\\\?\\`` prefix (``\\\\?\\UNC\\server`` -> ``\\\\server``)."""
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    return text[4:] if text.startswith("\\\\?\\") else text


def key(path):
    return os.path.normcase(_strip(os.fspath(path)))


def inside(path_key, root_key):
    return path_key == root_key or path_key.startswith(root_key.rstrip(os.sep) + os.sep)


def canonical(path, platform=None, strict=False):
    """``path`` as the system spells it: macOS F_GETPATH of the opened folder, else its real path. ``strict``: on macOS
    an ``OSError`` when the folder can't be opened, never the typed spelling (a chosen folder is judged as spelled)."""
    if (platform or sys.platform) == "darwin":
        try:
            import fcntl
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
            finally:
                os.close(descriptor)
            return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
        except OSError:   # The folder can't be opened (privacy protection's EPERM, gone): never judged as typed.
            if strict:
                raise
        except (ImportError, AttributeError, ValueError):   # No fcntl or F_GETPATH (a simulated platform): realpath.
            pass
    try:
        return Path(os.path.realpath(path))
    except (OSError, ValueError):
        return Path(path)


class Convention:
    """The convention rules for this person (module docstring)."""

    def __init__(self, environ=None, platform=None, home=None):
        environ = os.environ if environ is None else environ
        platform = platform or sys.platform
        home = canonical(Path(home) if home is not None else Path.home(), platform)
        private, exempt = [], []
        if platform == "darwin":
            private.append(home / "Library")
            exempt += [home / "Library" / "Mobile Documents", home / "Library" / "CloudStorage"]
        elif platform == "win32":
            for name in ("APPDATA", "LOCALAPPDATA"):
                value = environ.get(name)
                if value and Path(value).is_absolute():
                    private.append(canonical(Path(value), platform))
        # The temporary folder is where archives are opened (Windows puts it under %LOCALAPPDATA%): a project there
        # keeps its file tools; Angelo's own process and scratch folders in it stay private by registration.
        import tempfile
        exempt.append(canonical(Path(tempfile.gettempdir()), platform))
        self.home = key(home)
        self.private = [key(path) for path in private]
        self.exempt = [key(path) for path in exempt]
        self.profiles = {os.path.normcase(name) for name in storage.NATIVE_PROFILE_FAMILIES}

    def private_path(self, path, below=None):
        """Whether ``path`` (as the system spells it) is application state by convention. ``below``: only its part
        under that folder counts (the chat's own default workspace: a profile-family folder inside it still does)."""
        path_key = key(path)
        if below is not None:
            start = key(below)
            if not inside(path_key, start):
                return False
            rest = path_key[len(start.rstrip(os.sep)):].lstrip(os.sep)
            return any(part in self.profiles for part in rest.split(os.sep) if part)
        if any(inside(path_key, root) for root in self.exempt):
            return any(part in self.profiles for part in path_key.split(os.sep) if part)
        if any(inside(path_key, root) for root in self.private):
            return True
        if path_key != self.home and inside(path_key, self.home):
            if path_key[len(self.home.rstrip(os.sep)):].lstrip(os.sep).startswith("."):
                return True
        return any(part in self.profiles for part in path_key.split(os.sep) if part)


def _mountinfo(text=None):
    """``[(root, mount point)]`` of /proc/self/mountinfo (``text``: its contents), or None when it can't be read.
    Its octal escapes (a space, a tab, a newline, a backslash, or any byte) are undone on bytes, then UTF-8 decoded,
    so a name in any script is read as written (re-read of ccdfea52edb, S-2)."""
    if text is None:
        try:
            raw = Path("/proc/self/mountinfo").read_bytes()
        except OSError:
            return None
    else:
        raw = text.encode("utf-8", "surrogateescape")
    found = []
    for line in raw.split(b"\n"):   # Lines end at a newline only: a name's \r is left as written (N-2).
        fields = line.split(b" ")
        if len(fields) > 4:
            root, point = (re.sub(rb"\\([0-7]{3})", lambda match: bytes([int(match.group(1), 8)]), field)
                           .decode("utf-8", "surrogateescape") for field in (fields[3], fields[4]))
            found.append((root, point))
    return found


def subtree_mounts(text=None):
    """Linux: the mount points whose mounted folder is not the root of its filesystem (a bind mount of a subfolder):
    a second path to a folder whose real ancestry cannot be checked, so file tools never cross into one below the
    workspace (break-it r2m4c round 3 M-2). Elsewhere none: macOS and Windows offer no such second path to a folder
    (a link or junction is refused anyway). ``text``: /proc/self/mountinfo's."""
    if text is None and not sys.platform.startswith("linux"):
        return frozenset()
    return frozenset(point for root, point in (_mountinfo(text) or ()) if root != "/")


# macOS's struct statfs (64-bit inodes): its size and where f_mntonname (MAXPATHLEN bytes) starts.
STATFS_SIZE, STATFS_MNTONNAME, MAXPATHLEN = 2168, 88, 1024


def statfs_mounts(buffer, count):
    """The mount points in ``count`` struct statfs records of ``buffer`` (getfsstat's)."""
    return [buffer[index * STATFS_SIZE + STATFS_MNTONNAME:index * STATFS_SIZE + STATFS_MNTONNAME + MAXPATHLEN]
            .split(b"\0", 1)[0].decode("utf-8", "surrogateescape") for index in range(count)]


def _getfsstat():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    for symbol in ("getfsstat$INODE64", "getfsstat"):   # Intel's 64-bit-inode name; Apple silicon's only one.
        function = getattr(libc, symbol, None)
        if function is not None:
            break
    else:
        return None
    function.restype, function.argtypes = ctypes.c_int, (ctypes.c_void_p, ctypes.c_int, ctypes.c_int)
    for _attempt in range(3):   # Mounts can come and go between the count and the read.
        count = function(None, 0, 2)   # MNT_NOWAIT
        if count < 0:
            return None
        buffer = ctypes.create_string_buffer(STATFS_SIZE * (count + 4))
        got = function(buffer, len(buffer), 2)
        if 0 <= got <= count + 4:
            return statfs_mounts(buffer.raw, got)
    return None


def mount_points(text=None, platform=None):
    """Every mount point (Linux: /proc/self/mountinfo, or ``text``; macOS: getfsstat), or None where there is no
    such table or it can't be read (Windows: drive letters are ``os.path.ismount``'s). The live macOS table is the
    running system's only: judging as macOS elsewhere (a host test) has none to read (VM CI on 70142a371dd)."""
    platform = platform or sys.platform
    if platform.startswith("linux"):
        found = _mountinfo(text)
        return None if found is None else frozenset(point for _root, point in found)
    if platform == "darwin" and text is None and sys.platform == "darwin":
        try:
            found = _getfsstat()
        except Exception:   # Any failure of the C call reads as no table: the caller's scan fails closed.
            return None
        return None if found is None else frozenset(found)
    return None
