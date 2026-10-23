# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless child never has the source open: Blender opens a working copy next to it.

With the person's preferences (``headless_preferences``) their add-ons and startup scripts run in the child, and
any of them can save the open file at any moment: a ``load_post``, ``save_post`` or ``depsgraph_update_post``
handler, an undo or frame handler, a timer or a thread (break-it review, 2026-10-23). Blender saves to the file it
has open, so the child opens a copy of the source instead: the session's output name, claimed next to the source
(``headless_files.claim_output``, so ``//`` image and library paths stay valid), filled with the source's bytes
through ``headless_files.place``. Every plain save by anyone then lands in the copy and the source is only ever
read. The copy becomes the session's output with its first saved edit; a session that saved no edit removes it when
it ends, and the launcher removes the copy a crashed child left (``discard``). Where the folder cannot be written,
Blender opens the source itself: a plain save there cannot replace it either (Blender writes a temporary file in
the folder and renames it). The launcher's sweep of the private folders dead children left (``sweep``) lives here too: what children leave
behind. Contract: doc/angelo/external_mcp.rst, "Headless".
"""

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

from . import headless_files


def claim(source):
    """A new working copy of ``source`` next to it, or None where its folder is read-only. The launcher makes the one
    a child starts on, so a start that fails in any way (no ready, a crash, a kill) removes it again."""
    source = Path(source)
    if not headless_files.folder_writable(source):
        return None
    target = headless_files.claim_output(source)
    try:
        headless_files.place(source, target)
    except BaseException:
        _unlink(target)
        raise
    return target


def open_copy(bpy, source, copy=None):
    """Open the working copy ``copy`` the launcher made (one of this source's own, else a fresh one), or ``source``
    itself where its folder is read-only; returns the path Blender has open."""
    source = Path(source)
    if copy is not None:
        target = headless_files.check_resume(source, str(copy))   # Only a .blend next to it named as outputs are.
    else:
        target = claim(source) or source
    try:
        result = bpy.ops.wm.open_mainfile(filepath=str(target), load_ui=False, use_scripts=False)
    except RuntimeError:
        result = {"CANCELLED"}
    if "FINISHED" not in result:
        if target != source:
            _unlink(target)
        raise RuntimeError("Blender could not open the file")
    return target


def discard(path, source, identity):
    """Remove an unedited working copy ``path`` while it is still the file the session had (``identity``), with the
    ``.blend1`` backup a person's add-on's save may have left beside it. Never the source, never through a link."""
    if path is None or identity is None or Path(path) == Path(source):
        return False
    if headless_files.identity(path) != tuple(identity):
        return False
    try:
        if os.path.islink(path):
            return False
        os.unlink(path)
    except OSError:
        return False
    backup = str(path) + "1"
    if os.path.isfile(backup) and not os.path.islink(backup):
        _unlink(backup)
    return True


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def sweep(temporary, prefix):
    """Remove private folders (named ``prefix``...) of children that died without removing their own (a hard kill)."""
    root = Path(temporary or tempfile.gettempdir())
    try:
        candidates = [path for path in root.iterdir() if path.name.startswith(prefix)]
    except OSError:
        return
    for folder in candidates:
        info = None
        try:
            info = os.lstat(folder)
            if not os.path.isdir(folder) or os.path.islink(folder) or info.st_uid != os.getuid():
                continue
            pid = _read_pid(folder / "child.pid")
        except (OSError, ValueError):
            if info is not None and time.time() - info.st_mtime > 86400:   # Never started (no pid), a day old.
                shutil.rmtree(folder, ignore_errors=True)
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def _read_pid(path):
    """A child's PID from its own small regular file; never blocks on, or follows, anything else put there."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("not a regular file")
        return int(os.read(descriptor, 32))
    finally:
        os.close(descriptor)


def named_roots(source, scripts):
    """What the app named (the source's folder, where the output goes too, and script files it named) stays as named:
    each maps to itself, so it wins over a broader root such as <temp> or <home> (the longest match wins)."""
    named = ([os.path.dirname(str(source))] if source else []) + sorted(scripts)
    return [(path, path) for name in named for path in (name, os.path.realpath(name))]
