# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one private folder this process's stage bundles share (``program_stage_bundle``).

A suspend used to allocate, register and remove a private folder of its own: the owner file, the held handle and the
scratch root on every yield, and the owner file again on removal, about 1.8 million retired instructions per suspend
(Mac 1, release gate 7ea90a2: ``stage_bundle/*/suspend_uncompressed`` at 1.10x its ceiling). The guarantees belong to
the folder, not to each bundle, so one ``private_roots.allocate`` per process carries them for every bundle in it:
the private family name, the owner token and the held identity, wherever the folder is moved. Each bundle is a fresh
name in it, written once by this process's main thread and removed alone.
"""

import atexit
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import uuid

from . import private_roots

PREFIX = "angelo-native-preview-"
_NAME = re.compile(r"[0-9a-f]{32}\.blend\Z")   # The only names ``new_bundle`` gives; nothing else is removed here.
_lock = threading.Lock()
_folder = None   # (path, identity) of the folder bundles are written to now.
_used = set()    # Every folder this process gave bundles in: their bundles are removed one by one, never the folder.


def _identity(info):
    return (info.st_dev, info.st_ino)


def _current():
    """The folder while its path still names the allocated folder, which is still registered; otherwise None (gone,
    moved or replaced: a new one is allocated and the moved one stays private by its owner token and handle)."""
    if _folder is None:
        return None
    path, identity = _folder
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode) or _identity(info) != identity or private_roots.owner_token(identity) is None:
        return None
    return path


def new_bundle():
    """A bundle path no file has: a fresh name in this process's private folder (allocated on first use)."""
    global _folder
    with _lock:
        folder = _current()
        if folder is None:
            folder = private_roots.allocate(PREFIX)
            os.chmod(folder, 0o700)
            _folder = (folder, _identity(os.lstat(folder)))
            _used.add(folder)
        while True:   # A name is never reused, so a write never replaces another bundle.
            bundle = folder / (uuid.uuid4().hex + ".blend")
            if not os.path.lexists(bundle):
                return bundle


def owns(bundle):
    """Whether ``bundle`` is one this process named in one of its shared folders."""
    path = Path(bundle)
    with _lock:
        return path.parent in _used and bool(_NAME.match(path.name))


def remove(bundle):
    """Remove one bundle of this process's (and a partial write beside it); True when ``bundle`` was one."""
    if not owns(bundle):
        return False
    for path in (str(bundle), str(bundle) + "@"):   # Blender writes ``<file>@`` and renames it into place.
        try:
            os.unlink(path)
        except OSError:
            pass
    return True


def _remove_folders():
    """At exit: each shared folder still at its path is removed (bundles left by an unclean stop go with it)."""
    with _lock:
        folders = list(_used)
    for folder in folders:
        try:
            if stat.S_ISDIR(os.lstat(folder).st_mode):
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass
    private_roots.prune()


atexit.register(_remove_folders)
