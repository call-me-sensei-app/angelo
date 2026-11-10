# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A private copy of a file just written, and a file's identity. No ``bpy`` here.

A check that runs later, in a background Angelo (``web_readback``, ``engine_preflight_link``), reads the copy made
when the file was written, so a later export or save over the original never changes what it compares. It is taken
on the main thread, right after the write, so the next write can never overtake it:

* macOS: an APFS clone (``clonefile``: no space, no time).
* Windows: the system's own copy (``CopyFile2``), which clones the file's blocks on a volume that can (ReFS, a Dev
  Drive) and otherwise copies with the system's large unbuffered transfers, about 1.7 times as fast as Python's
  read and write loop from 10 MB up (doc/angelo/engine_preflight.rst, "Measurements"). Attributes come with it, so a
  read-only source would leave a read-only copy that its folder's cleanup could not remove: the copy is made
  writable.
* Anywhere else, or when either call fails: ``shutil.copyfile``.

A copy in the background would free the main thread for the copy's time, but Windows refuses to replace a file that
is open (the next export's atomic publish would fail), and an in-place rewrite would change what is being copied.

``identity`` is what a child compares before and after it reads a file it did not copy (the saved .blend it opens):
a file replaced or rewritten in between has another one. It is taken once the file system clock is past the file's
stamp (``change_clock``: at most a tick, on Windows), so a write in the tick of the save still changes it.
"""

import ctypes
import os
import shutil
import stat
import sys

try:
    from . import change_clock
except ImportError:   # Loaded by path, outside the package (bench_export_snapshot.py): the helper, by its path too.
    import importlib.util
    _spec = importlib.util.spec_from_file_location("angelo_change_clock", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "modules", "angelo_change_clock.py"))
    change_clock = sys.modules.setdefault("angelo_change_clock", importlib.util.module_from_spec(_spec))
    if not hasattr(change_clock, "settle"):
        _spec.loader.exec_module(change_clock)


def identity(path):
    """[device, inode, size, modification time in ns] of the file at ``path``, or None when there is none."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return settled_identity(info)


def settled_identity(info):
    """``identity`` of a stat taken earlier, once the change clock has passed its stamp: a thread waits a coarse step
    out, where Blender's main thread would fail closed (``engine_preflight_link`` settles a save's stat so)."""
    try:
        change_clock.settle(info, change=False)
    except change_clock.Unsettled:
        return list(change_clock.unique())   # A stamp ahead of the clock: equal to no identity (fail closed).
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


identity_of = settled_identity   # A save-handler stat follows the same settlement policy as a captured path stat.


def take(source, target):
    """Copy ``source`` to ``target`` (a name that doesn't exist yet) and return the copy's identity."""
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.clonefile(os.fsencode(source), os.fsencode(target), 0) == 0:
                return identity(target)
        except (AttributeError, OSError):
            pass
    elif sys.platform == "win32":
        try:
            import _winapi
            _winapi.CopyFile2(source, target, 0)
            if not os.stat(target).st_mode & stat.S_IWRITE:   # A read-only source's attribute came along.
                os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
            return identity(target)
        except (AttributeError, ImportError, OSError):   # No CopyFile2 (before Python 3.12), or it failed.
            try:
                os.remove(target)   # Whatever a failed copy left, so the plain copy starts clean.
            except OSError:
                pass
    shutil.copyfile(source, target)
    return identity(target)
