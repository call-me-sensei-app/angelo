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


def canonical(path, platform=None):
    """``path`` as the system spells it: macOS F_GETPATH of the opened folder, else its real path."""
    if (platform or sys.platform) == "darwin":
        try:
            import fcntl
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
            finally:
                os.close(descriptor)
            return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
        except (OSError, AttributeError, ValueError):
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


def subtree_mounts(text=None):
    """Linux: the mount points whose mounted folder is not the root of its filesystem (a bind mount of a subfolder):
    a second path to a folder whose real ancestry cannot be checked, so file tools never cross into one below the
    workspace (break-it r2m4c round 3 M-2). Elsewhere none: macOS and Windows offer no such second path to a folder
    (a link or junction is refused anyway). ``text``: /proc/self/mountinfo's."""
    if text is None:
        if not sys.platform.startswith("linux"):
            return frozenset()
        try:
            text = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return frozenset()
    found = set()
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) > 4 and fields[3] != "/":
            found.add(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4]))
    return frozenset(found)
