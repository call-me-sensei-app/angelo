# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A private copy of a file just written, and a file's identity. No ``bpy`` here.

A check that runs later, in a background Angelo (``web_readback``, ``engine_preflight_link``), reads the copy made
when the file was written, so a later export or save over the original never changes what it compares. The copy is
an APFS clone where the system offers ``clonefile`` (no space, no time), else a plain copy. ``identity`` is what a
child compares before and after it reads a file it did not copy (the saved .blend it opens): a file replaced or
rewritten in between has another one.
"""

import ctypes
import os
import shutil
import sys


def identity(path):
    """[device, inode, size, modification time in ns] of the file at ``path``, or None when there is none."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def take(source, target):
    """Copy ``source`` to ``target`` (a name that doesn't exist yet) and return the copy's identity."""
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.clonefile(os.fsencode(source), os.fsencode(target), 0) == 0:
                return identity(target)
        except (AttributeError, OSError):
            pass
    shutil.copyfile(source, target)
    return identity(target)
