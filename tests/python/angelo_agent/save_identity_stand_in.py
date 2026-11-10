# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for Blender's save naming the file it wrote to its ``save_post`` handlers (``_bpy._angelo_saved``), for
host tests that have no Blender: ``saving(path)`` answers with the file at ``path`` now, the one the stand-in save just
wrote. A test of a write landing after the save changes the file after entering it."""

import contextlib
import itertools
import os
import sys
import types
from types import SimpleNamespace
from unittest import mock


@contextlib.contextmanager
def saving(path):
    info = os.stat(path)
    birth = getattr(info, "st_birthtime_ns", None)
    if birth is None:
        birth = int(getattr(info, "st_birthtime", 0) * 1_000_000_000)
    answer = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, birth)
    native = types.ModuleType("_bpy")
    native._angelo_saved = SimpleNamespace(saved_file=lambda: answer)
    with mock.patch.dict(sys.modules, {"_bpy": native}):
        yield


def written_at_the_handler(path):
    """``document_file_identity.written`` as the stand-in save gives it: the file at ``path`` when the handler runs,
    or, where a test's save writes no file, an identity of its own (Blender's save always names the file it wrote)."""
    try:
        return os.stat(path)
    except (OSError, ValueError, TypeError):
        return SimpleNamespace(st_dev=1, st_ino=next(_UNWRITTEN), st_size=0, st_mtime_ns=0)


_UNWRITTEN = itertools.count(1 << 40)


def stand_in_for_saves(case):
    """For the whole test: every save names the file at its path when its handler runs (no other program writes in
    these tests). A test of a write landing after the save enters ``saving`` (or patches ``written``) itself."""
    from angelo_agent import document_file_identity
    patcher = mock.patch.object(document_file_identity, "written", written_at_the_handler)
    patcher.start()
    case.addCleanup(patcher.stop)
