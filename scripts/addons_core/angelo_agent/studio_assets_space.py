# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The disk the Asset Browser library uses, and the person's controls over it: its size, turning it off or on, and
freeing its copies (Media Studio's library row, ``studio_library_ops``).

Freeing deletes only files Angelo named itself in its own folder (``studio_assets.asset_directory``): each item's
``<id>.blend`` in ``models/`` and ``images/``, a writer's leftover ``<id>.blend.<suffix>.tmp`` there, and the manifest.
Regular files only, one folder level, never a link or a Windows junction (the folder itself, a catalog folder or a file)
and never a folder walk. A file the person put there (any other name) stays, and so does the catalogue file (it keeps
catalogues the person added) and ``registration.json`` (a library the person turned off stays off). It runs only while
the library is off and this process holds the writer's lock, so no writer (here or in another Angelo) is writing a
file it deletes; ``free_later`` runs it on a worker thread, never on the main one. A scene that linked a model from the
library shows it as missing until the library is turned on again (the writer rewrites every copy at the same path); an
appended or packed one keeps everything it needs.
"""

import os
import threading
import time
import uuid
from pathlib import Path

from . import studio_assets

FOLDERS = tuple(spec[2] for spec in studio_assets.CATALOGS.values())   # "models", "images"
TOP_FILES = (studio_assets.MANIFEST,)   # Never the catalogue file: it keeps the person's own catalogues.
SIZE_AGE = 30.0   # Seconds a measured size is shown before it is measured again (off the main thread).
LOCK_WAIT = 3.0   # Seconds a free waits (on its worker) for this Angelo's writer to stop; it stops within 0.25 s once off.

_size = {"bytes": None, "at": 0.0, "directory": "", "worker": None, "generation": 0}


def linked(path):
    """Whether ``path`` is a symbolic link or a Windows junction (which ``is_symlink`` does not report)."""
    path = Path(path)
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def angelo_name(name):
    """Whether ``name`` is one Angelo gives an item's file: ``<id>.blend`` or a writer's ``<id>.blend.<suffix>.tmp``, or
    Blender's own save temporary of either (a trailing ``@``, left when a writer is stopped mid-save)."""
    stem, dot, rest = name.partition(".blend")
    rest = rest[:-1] if rest.endswith("@") else rest
    if not dot or not (rest == "" or (rest.startswith(".") and rest.endswith(".tmp") and len(rest) > 5)):
        return False
    try:
        return str(uuid.UUID(stem)) == stem
    except ValueError:
        return False


def angelo_files(directory):
    """The files Angelo named itself in ``directory``: regular files only, never through a link or junction."""
    directory = Path(directory)
    if linked(directory):
        return []   # The whole folder elsewhere: nothing in it is known to be Angelo's.
    found = []
    for name in TOP_FILES:
        path = directory / name
        if path.is_file() and not linked(path):
            found.append(path)
    for folder in FOLDERS:
        if linked(directory / folder):
            continue
        try:
            entries = list(os.scandir(directory / folder))
        except OSError:
            continue
        for entry in entries:
            if angelo_name(entry.name) and entry.is_file(follow_symlinks=False) and not linked(entry.path):
                found.append(Path(entry.path))
    return found


def measure(directory):
    """Bytes the library's own files use (worker thread or tests: a stat per file)."""
    total = 0
    for path in angelo_files(directory):
        try:
            total += path.lstat().st_size
        except OSError:
            pass
    return total


def size(directory):
    """The last measured size in bytes, or None before the first; measures again on a worker thread when it is older
    than ``SIZE_AGE`` or the folder changed. Main thread: never touches the disk."""
    directory = str(directory)
    stale = _size["directory"] != directory or time.monotonic() - _size["at"] > SIZE_AGE
    worker = _size["worker"]
    if stale and (worker is None or not worker.is_alive()):
        generation = _size["generation"]

        def run():
            measured = measure(directory)
            if _size["generation"] == generation:   # A free since this began: its total is stale, never shown.
                _size.update(bytes=measured, at=time.monotonic(), directory=directory)
        _size["at"] = time.monotonic()   # One measure at a time, whatever the draw rate.
        _size["worker"] = threading.Thread(target=run, name="Angelo asset library size", daemon=True)
        _size["worker"].start()
    return _size["bytes"] if _size["directory"] == directory else None


def forget_size():
    _size.update(bytes=None, at=0.0, directory="", generation=_size["generation"] + 1)


ON = "on"   # free's result when the library was turned on again before it held the folder: nothing deleted.


def free(directory, active):
    """Delete the library's copies; ``(freed bytes, files)``, None when a writer holds the folder (try again), or
    ``ON`` when the library is on again by the time this holds the folder (checked under the lock).

    Refused (ValueError) while the library is on: its writer would only write everything again.
    """
    if active:
        raise ValueError("turn the library off first")
    with studio_assets.sync_lock(directory) as held:
        if not held:
            return None
        if not studio_assets.read_registration(directory).get("removed"):
            return ON   # Turned on (here or by another Angelo) since it was asked: its writer is writing again.
        freed, count = 0, 0
        for path in angelo_files(directory):
            try:
                length = path.lstat().st_size
                path.unlink()
            except OSError:
                continue
            freed, count = freed + length, count + 1
        for folder in FOLDERS:
            try:
                (Path(directory) / folder).rmdir()   # Only when empty: anything else in it is not Angelo's.
            except OSError:
                pass
    forget_size()
    return freed, count


def free_later(directory, active, done, wait=LOCK_WAIT):
    """Free on a worker thread, never blocking the main one: waits up to ``wait`` seconds for this Angelo's writer to
    stop, then calls ``done(result)`` from that thread with ``free``'s result (None: still busy). Raises ValueError
    at once while the library is on."""
    if active:
        raise ValueError("turn the library off first")

    def run():
        deadline = time.monotonic() + wait
        try:
            result = free(directory, False)
            while result is None and time.monotonic() < deadline:
                time.sleep(0.1)
                result = free(directory, False)
        except OSError:
            result = None
        done(result)   # Always: the caller's "running" ends here, whatever happened.
    worker = threading.Thread(target=run, name="Angelo asset library free", daemon=True)
    worker.start()
    return worker


def turn_off(libraries, directory):
    """Remove the Preferences entry and record the removal, as removing it in Preferences does."""
    entry = studio_assets.find_library(libraries, directory)
    if entry is not None:
        libraries.remove(entry)
    studio_assets.write_registration(directory, removed=True)


def readable(count_bytes):
    """``(template, value)`` for a byte count: whole units below ten, one decimal above."""
    from .ui_text import n_
    for limit, template in ((1 << 30, n_("{value} GB")), (1 << 20, n_("{value} MB")), (1 << 10, n_("{value} KB"))):
        if count_bytes >= limit:
            value = count_bytes / limit
            return template, (f"{value:.1f}" if value < 10 else f"{value:.0f}")
    return n_("{value} bytes"), str(int(count_bytes))
