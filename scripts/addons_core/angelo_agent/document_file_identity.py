# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The open document's file as Blender read or wrote it, never as a later stat finds its path. No ``bpy``.

A stat of the path taken after Blender read the file (``load_post``) or after its save renamed the written file into
place (``save_post``) could find a file another program put there in between (a sync client restoring an older copy
of the document), and the document's chats would bind to that file (stat before read, release-1010 audit).

* A load: the file Blender read, identified on the descriptor it read through (``loaded``). Where a process has no
  such identity, the file is identified at ``load_pre``, before Blender reads it (``reading``), and again after
  (``read``); when the two differ it is unknown (None), so a chat is offered the document, never rebound to it.
* A save: Blender holds the finished temporary file open across its rename and identifies it through that handle
  (``BlendFileWriteParams::written``), and hands that to the save's ``save_post`` handlers (``_bpy._angelo_saved``,
  ``written``, ``saved``): the file it wrote, whatever is at the path by then.

The document open when the add-on registers was read before any handler ran: its identity is the one at first use
(``document_identity.current``), the one case with none from before.
"""

import os
import sys
from types import SimpleNamespace

_reading = None   # At load_pre: (the file Blender is about to read, its identity then).


def _canonical(path):
    return os.path.normcase(os.path.abspath(path))


def reading(filepath, identify):
    """``load_pre``: ``filepath`` (the file Blender is about to read) and ``identify(filepath)`` now, before the read."""
    global _reading
    _reading = (filepath, identify(filepath)) if filepath else None


def done_reading():
    """The load's identity came from Blender's read itself (``loaded``): the one taken before it is dropped."""
    global _reading
    _reading = None


def read(path, now, same_file):
    """The identity of the file Blender read at ``path``: ``now`` (taken after the read) when it is the file identified
    before the read (``same_file``), else None (unknown). A load whose read file is not at ``path`` (Recover reads the
    session file) has none from before and keeps ``now``, as before."""
    global _reading
    before, _reading = _reading, None
    if before is None or _canonical(before[0]) != _canonical(path):
        return now
    return now if same_file(before[1], now) else None


def _native(name="saved_file"):
    """``_bpy._angelo_saved.<name>()``, or None where this process has no such binary (a test, an older build)."""
    try:
        from _bpy import _angelo_saved
        return getattr(_angelo_saved, name)()
    except Exception:  # noqa: BLE001 - no such binary (a test, an older build), or it could not answer: none.
        return None


def written(path):
    """``st_dev``, ``st_ino``, ``st_size``, ``st_mtime_ns`` (and on macOS ``st_birthtime``) of the file the save
    whose ``save_post`` handlers run now wrote at ``path``, as the save identified the file it wrote; None where
    the save gave none (a process without it, a write it could not identify, a ``save_post`` of another kind)."""
    return _stat_like(_native(), path)


def loaded(path):
    """The same fields of the file the last load read at ``path``, identified on the descriptor Blender read it
    through as it opened it (``_bpy._angelo_saved.read_file``): never a stat of the path before or after the load,
    either of which can be of another file (one put there and moved away again around the read). None where the
    load gave none (a process without it, another file read last, a file it could not identify)."""
    return _stat_like(_native("read_file"), path)


def _stat_like(found, path):
    if found is None or not path or _canonical(found[0]) != _canonical(path):
        return None
    _path, device, inode, size, modified_ns, created_ns = found
    fields = dict(st_dev=device, st_ino=inode, st_size=size, st_mtime_ns=modified_ns)
    if sys.platform == "darwin":   # The birth time as this CPython's os.stat gives it (3.13: a float only).
        fields["st_birthtime"] = created_ns // 1_000_000_000 + (created_ns % 1_000_000_000) * 1e-9
        if hasattr(os.stat_result, "st_birthtime_ns"):
            fields["st_birthtime_ns"] = created_ns
    return SimpleNamespace(**fields)


def saved(path):
    """``written(path)``; None where the save gave none (unknown): a stat of ``path`` now could find a file another
    program put there after the save, which is the order this module exists to avoid (release-1010 audit re-check 3).
    Every caller treats None as a file it can't vouch for (the engine-link write-back refuses; a chat is offered)."""
    return written(path)


def identity_of(info):
    """``document_identity.file_identity`` of a stat (or ``written``/``loaded``) taken already: [device, file number,
    creation stamp], or None without a file number."""
    if not info.st_ino:
        return None
    stamp = 0
    if sys.platform == "darwin":
        stamp = getattr(info, "st_birthtime_ns", None)
        if stamp is None:
            stamp = int(getattr(info, "st_birthtime", 0) * 1_000_000_000)
    return [int(info.st_dev), int(info.st_ino), int(stamp)]


def at_load(path, identify, same_file):
    """In ``load_post``: the identity (``identity_of``) of the file the load read at ``path`` (``loaded``); where the
    process gives none, ``identify(path)`` after the read, which must be the file identified before it (``read``)."""
    found = loaded(path)
    if found is None:
        return read(path, identify(path), same_file)
    done_reading()
    return identity_of(found)


def at_save(path):
    """In a ``save_post`` handler: the identity (``identity_of``) of the file the running save wrote at ``path``
    (``written``); None (unknown) when it gave none, never a stat of ``path`` after the save's rename: a later load
    then offers the chats the document, never rebinds them."""
    found = written(path)
    return identity_of(found) if found is not None else None
