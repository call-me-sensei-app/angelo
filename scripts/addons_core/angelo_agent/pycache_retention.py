# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Old builds' bytecode caches: removed once no launch has used them for a week; nothing else, ever.

Angelo keeps Python bytecode out of the installed app, in ``<bpy.app.cachedir>/pycache/<build key>``
(``source/blender/python/intern/bpy_interface_pycache.cc``; doc/angelo/releasing.rst, "Nothing is written
inside the app"). Each build has its own folder, so every update starts a new one; with daily updates the old
ones would pile up. A window session, ``DELAY`` seconds after the add-on registers and on a daemon thread:

1. marks its own build's folder as used (``LAST_USED``, its time);
2. removes a sibling folder only when **all** of these hold: its name is a build key the app generates
   (``BUILD_KEY``), its mark (or, never marked, the folder itself) is older than ``KEEP_DAYS``, and its whole
   tree is bytecode (``is_bytecode_tree``: ``.pyc`` files, an interrupted write of one, folders and the mark;
   no link, junction or other file). A folder anyone else put there is never touched;
3. inside its own folder, removes the mirrors of source folders that no longer exist by the same content
   rule (``prune_stale_mirrors``): an App Translocation path gets a new random folder on each launch of a
   quarantined app, and a moved or deleted install leaves its mirror behind. POSIX only: Windows mirrors drop
   the drive letter, so a missing folder cannot be told from one on another drive.

It refuses to run (``RetentionRefused``) when the cache root, or a folder above it up to the Caches folder, is
a symbolic link or a Windows junction (``Path.is_symlink`` misses junctions): following one could delete
anything. Nothing happens in a background session or when ``PYTHONPYCACHEPREFIX`` chose a folder outside
Angelo's cache root. An interrupted or failed removal is not reported and is tried again next session.
"""

import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

KEEP_DAYS = 7
LAST_USED = ".angelo-last-used"
DELAY = 60.0
# The folder names bpy_interface_pycache.cc generates: a commit hash, or dev-<major>.<minor> without build info.
BUILD_KEY = re.compile(r"(?:[0-9a-f]{7,40}|dev-[0-9]+\.[0-9]+)\Z")
# A module's bytecode, or Python's interrupted atomic write of one (``<name>.pyc.<id>``).
BYTECODE = re.compile(r".+\.pyc(?:\.[0-9]+)?\Z")
# The cache root and the folders above it that must be real folders: pycache, Angelo's cache folder, Caches.
GUARDED_LEVELS = 3
_timer = None


class RetentionRefused(OSError):
    """The cache root is reached through a link: retention does nothing."""


def linked(path):
    """True for a symbolic link or a Windows junction (``os.path.isjunction``, Python 3.12+)."""
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(isjunction and isjunction(path))


def check_root(root):
    """Refuse a cache root that is, or sits below, a link or junction, up to the Caches folder."""
    path = Path(root)
    for _level in range(GUARDED_LEVELS):
        if linked(path):
            raise RetentionRefused("%s is a link or junction; bytecode cache retention does nothing" % path)
        path = path.parent


def is_bytecode_tree(folder, allow_mark=False):
    """True when everything below ``folder`` is bytecode or a folder (the mark too, at its top, if allowed)."""
    if linked(folder) or not os.path.isdir(folder):
        return False
    for parent, dirs, files in os.walk(folder):
        if any(linked(os.path.join(parent, name)) for name in dirs):
            return False
        for name in files:
            path = os.path.join(parent, name)
            if os.path.islink(path):
                return False
            if not (BYTECODE.match(name) or (allow_mark and name == LAST_USED and parent == os.fspath(folder))):
                return False
    return True


def remove(folder):
    """Remove a checked tree; True only when it is gone (a failed removal is retried next session)."""
    shutil.rmtree(folder, ignore_errors=True)
    return not os.path.lexists(folder)


def prune(root, current, now=None, keep_days=KEEP_DAYS):
    """Mark ``current`` used and remove ``root``'s other unused build folders; returns the names removed."""
    root, current = Path(root), Path(current)
    check_root(root)
    if linked(current):
        raise RetentionRefused("%s is a link or junction; bytecode cache retention does nothing" % current)
    now = time.time() if now is None else now
    current.mkdir(parents=True, exist_ok=True)
    mark = current / LAST_USED
    mark.touch()
    os.utime(mark, (now, now))
    removed = []
    for entry in sorted(root.iterdir()):
        if entry.name == current.name or not BUILD_KEY.match(entry.name) or linked(entry) or not entry.is_dir():
            continue
        try:
            used = (entry / LAST_USED).stat().st_mtime
        except OSError:
            used = entry.stat().st_mtime  # Never marked (only background sessions used it): its creation.
        if now - used > keep_days * 86400 and is_bytecode_tree(entry, allow_mark=True) and remove(entry):
            removed.append(entry.name)
    return removed


def prune_stale_mirrors(current):
    """Remove the mirrors below ``current`` whose source folder no longer exists; returns their source paths."""
    if os.name == "nt":
        return []
    removed = []
    pending = [Path(current)]
    while pending:
        folder = pending.pop()
        for entry in sorted(folder.iterdir()):
            if linked(entry) or not entry.is_dir():
                continue
            relative = os.path.relpath(entry, current)
            if os.path.isdir(os.sep + relative):
                pending.append(entry)
            elif is_bytecode_tree(entry) and remove(entry):
                removed.append(relative)
    return sorted(removed)


def own_cache(prefix, cachedir):
    """``(root, current)`` when ``prefix`` is a build folder of Angelo's own cache below ``cachedir``, else None."""
    if not prefix or not cachedir:
        return None
    root = os.path.normcase(os.path.normpath(os.path.join(cachedir, "pycache")))
    current = os.path.normcase(os.path.normpath(prefix))
    return (Path(root), Path(current)) if os.path.dirname(current) == root else None


def _run(root, current):
    try:
        removed = prune(root, current)
        stale = prune_stale_mirrors(current)
    except OSError as error:  # A refused or unreadable cache is no reason to disturb the session.
        print("Angelo: bytecode cache retention skipped:", error)
        return
    if removed or stale:
        print("Angelo: removed %d unused bytecode cache folder(s) of earlier builds and %d of moved or removed "
              "installs" % (len(removed), len(stale)))


def schedule():
    """From the add-on's ``register()``: once per window session, ``DELAY`` seconds later, on a thread."""
    global _timer
    import bpy
    folders = own_cache(sys.pycache_prefix, bpy.app.cachedir)
    if bpy.app.background or folders is None or _timer is not None:
        return

    def start():
        global _timer
        _timer = None
        threading.Thread(target=_run, args=folders, name="angelo-pycache-retention", daemon=True).start()

    _timer = start
    bpy.app.timers.register(start, first_interval=DELAY, persistent=True)


def cancel():
    global _timer
    import bpy
    if _timer is not None and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer = None
