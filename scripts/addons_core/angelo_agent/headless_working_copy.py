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

from . import change_clock, headless_files


def claim(source):
    """``(a new working copy of source next to it, the identity of the bytes written there)`` (``place``), or
    ``(None, None)`` where its folder is read-only. The launcher makes the one a child starts on, so a start that fails
    in any way (no ready, a crash, a kill) removes it again."""
    source = Path(source)
    if not headless_files.folder_writable(source):
        return None, None
    target = headless_files.claim_output(source)
    try:
        written = headless_files.place(source, target)
    except BaseException:
        _unlink(target)
        raise
    return target, written


def open_copy(bpy, source, copy=None):
    """Open the working copy ``copy`` the launcher made (one of this source's own, else a fresh one), or ``source``
    itself where its folder is read-only; returns ``(the path Blender has open, the identity of what it read)``
    (``open_read``)."""
    source = Path(source)
    if copy is not None:
        target = headless_files.check_resume(source, str(copy))   # Only a .blend next to it named as outputs are.
    else:
        target = claim(source)[0] or source
    try:
        identity = open_read(bpy, target)
    except RuntimeError:
        if target != source:
            _unlink(target)
        raise
    return target, identity


def open_read(bpy, path):
    """Open ``path`` in Blender; returns the identity (``headless_files.identity``) of the version it read, which
    the session's later saves and its removal of an unedited copy compare with. ``RuntimeError`` when Blender could
    not open it.

    The identity of the file the load read, on the descriptor Blender read it through
    (``document_file_identity.loaded``); never a stat after the open alone, which could be of a version another
    program wrote once Blender had read the file, held over the older document, so the session's next save would
    replace that version unnoticed. Where the process gives none, a stat before the open and again after, which must
    agree. A save in this process during the open (a person's add-on's ``load_post`` handler; the module docstring)
    writes the document Blender holds, so the identity that save gave stands for it. Otherwise the identity returned
    equals no other (``change_clock.unique``): the file reads as changed on disk, so the session claims a new output
    and never removes the copy."""
    before, saved = headless_files.identity(path), []
    handlers = getattr(getattr(bpy, "app", None), "handlers", None)

    def note(filepath=None, *_args):   # A save of this file only, as that save identified the file it wrote.
        if isinstance(filepath, str) and os.path.realpath(filepath) == os.path.realpath(path):
            saved.append(headless_files.saved_identity(filepath))
    if handlers is not None:   # Persistent: a load drops the others before its load_post handlers run.
        note = handlers.persistent(note)
        handlers.save_post.append(note)
    try:
        result = bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    except RuntimeError:
        result = {"CANCELLED"}
    finally:
        if handlers is not None and note in handlers.save_post:
            handlers.save_post.remove(note)
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not open the file")
    if saved:   # The document Blender holds, as that save wrote it (whatever is at the path by now).
        return saved[-1]
    from . import document_file_identity
    found = document_file_identity.loaded(str(path))   # The file the load read, on its own descriptor.
    if found is not None:
        return headless_files.settled_identity(found)
    after = headless_files.identity(path)
    return after if after == before else change_clock.unique()


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


LEGACY_SWEPT = ".headless-temp-swept"   # In the scratch root: the temporary folder's leftovers were swept once.


def sweep(temporary, prefix):
    """Remove private folders (named ``prefix``...) of children that died without removing their own (a hard kill).

    Only Angelo's own scratch root is listed (or ``temporary``, a test's): the system temporary folder can hold a great
    many unrelated entries (573,000 on a working Mac, 83 s to list), so it is listed for an earlier build's leftovers
    once per install (``LEGACY_SWEPT``), and never when it is the scratch root's fallback. ``sweep_in_background``
    runs this off the session's open."""
    from .private_roots import scratch_root   # Where they are made; an earlier build made them in the temporary folder.
    roots = [Path(temporary)] if temporary else [scratch_root()]
    system = Path(tempfile.gettempdir())
    if not temporary and roots[0] == system:
        return   # The scratch root fell back to the temporary folder: listing it is the cost this avoids.
    marker = roots[0] / LEGACY_SWEPT
    legacy = not temporary and not marker.exists()
    if legacy:
        roots.append(system)
    candidates = []
    for root in roots:
        try:
            candidates += [path for path in root.iterdir() if path.name.startswith(prefix)]
        except OSError:
            continue
    if legacy:
        try:
            marker.touch()
        except OSError:
            pass   # Tried again at the next sweep.
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


def sweep_in_background(temporary, prefix):
    """``sweep`` on a thread of its own: never on the path that opens a session. No bpy."""
    from . import thread_start
    try:
        thread_start.daemon("Angelo headless sweep", sweep, temporary, prefix)
    except RuntimeError:
        pass   # No thread to give: the leftovers wait for the next open.


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
