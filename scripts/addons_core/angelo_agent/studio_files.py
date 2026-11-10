# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading and writing the asset library's files without ever losing what is in them (``studio_assets``).

A file that is there but cannot be read now (no permission, a sync placeholder, a stale mount) is never written from
nothing: only a missing file is empty (1.0.10 audit re-check). The catalogue file is also written by Blender, which
saves it by two renames (``cats`` -> ``cats~``, then ``cats.writing`` -> ``cats``; asset_catalog_definition_file.cc):
between them the file is missing, and a save can land between Angelo's read and its write. So a write lands only on
the file that was read, and a file read as missing is only ever created, never replaced. "The file that was read" is
its bytes, read again right before the replace: a time stamp can't tell (NTFS moves in ~15.6 ms ticks, FAT and exFAT
in 2 s steps, so a same-size rewrite in place can keep inode, size and time; the change-clock rule,
``angelo_change_clock``). A file that can't be read again then is not written. What is left is the time from that
re-read's open to the rename (measured on Mac 2 under load, p99: 0.2 ms for a 1 KB file and 1 ms for 100 KB on APFS,
about 2-5 ms on FAT/exFAT), and only when both of Blender's renames land inside it: the person's newest catalogue edit
is then replaced (their earlier catalogues stay, and Blender still holds the edit). One rename alone is harmless: the
second puts the newest file back over Angelo's. No cheap fix closes it without a lock Blender doesn't take: a replace
can't be undone once it lands, an atomic swap isn't safe on every file system (macOS's FAT32 driver overwrote), and
moving the file aside leaves it missing for a save to merge against.
"""

import hashlib
import json
import os
import sys
import uuid

from . import shared_replace

DAMAGED = "damaged"   # A file Angelo writes that holds no record it could have written (empty, broken JSON).


def read_own(path):
    """A JSON file Angelo writes: its value, ``{}`` when missing, ``DAMAGED`` when it is not JSON, None when it is there
    but cannot be read now."""
    try:
        text = shared_replace.read_text(path)   # Sharing delete: never refuses a writer's replace.
    except FileNotFoundError:
        return {}
    except UnicodeError:
        return DAMAGED
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return DAMAGED


def _read_bytes(path):
    """``(identity, bytes)``: the file's inode, device and size with a digest of its bytes. Raises OSError."""
    with shared_replace.open_file(path) as handle:   # Sharing delete: a catalogue save lands.
        stat = os.fstat(handle.fileno())
        data = handle.read()
    return (stat.st_ino, stat.st_dev, len(data), hashlib.sha256(data).digest()), data


def shared_read(path):
    """``(text, identity)`` of a file the person shares with Angelo: ``("", None)`` when missing. Bytes that are not
    UTF-8 are kept as they are (``surrogateescape``). Raises ``OSError`` when it is there but cannot be read now."""
    try:
        identity, data = _read_bytes(path)
    except FileNotFoundError:
        return "", None
    return data.decode("utf-8", "surrogateescape"), identity


def write_if_unchanged(path, text, identity):
    """Write ``text`` only over the file ``shared_read`` read (``identity``; None: only while it is still missing).
    True when written; False when the file changed meanwhile (the next sync reads it again)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(text.encode("utf-8", "surrogateescape"))
        if identity is None:
            return _create(temporary, path)
        try:
            if _read_bytes(path)[0] != identity:
                return False
        except OSError:   # Mid-save (moved to cats~: the person's newest file takes its place), or not readable now.
            return False
        shared_replace.replace(temporary, path)   # Blender's own reader may hold it: retried, else raised.
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _create(temporary, path):
    """Put ``temporary`` at ``path`` only if nothing is there: a rename on Windows and a hard link elsewhere both
    refuse an existing name; a file system without links gets an exclusive create written in place."""
    try:
        if sys.platform == "win32":
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
        return True
    except FileExistsError:
        return False
    except OSError:
        pass
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(temporary.read_bytes())
    except BaseException:
        path.unlink(missing_ok=True)   # Created here, so Angelo's alone: never left half-written (a full disk).
        raise
    return True
