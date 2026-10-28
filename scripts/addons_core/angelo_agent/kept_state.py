# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A state file a store could not read is never written over as if it were empty. No Blender dependency.

A store that reads its JSON file at start and writes it whole later (the job records, the chat list, the
connections, the team usage, the local apps' conversations) used to read a file it could not read as empty, and
its next write replaced it: every job record, every chat in the switcher, every saved connection gone (release
audit of 1.0.4/1.0.5). Each such store reads through ``read``, which tells four states apart:

* ``NONE``: no file. Nothing to keep.
* ``OK``: the file's JSON value.
* ``UNKNOWN``: a file is there that can't be read now (a permission or I/O error). It may be whole: it is never
  replaced as if it were empty. A store either refuses to write while it can't read it (the team usage and the local
  apps raise, so their caller keeps the change and tries again), or keeps it beside it first (``keep_before_write``)
  and reads that copy back at its next load (``kept_values``), removing it once a save holds all of it (the job
  records, the connections, the chat list).
* ``DAMAGED``: a file no build can read (not JSON, or a value of the wrong shape). It is kept beside it the same way
  (``session_kept.preserve``: ``<name>.<UTC time>.unreadable``, a hard link or a copy, as for a chat this build cannot
  read) and only then replaced; when it can't be kept now, the store does not write.

``present`` answers whether a file is there without taking an error for "no": ``os.path.isfile`` and
``os.path.exists`` answer False for a name they can't stat (``Path.is_file`` raises then, but a broad ``except``
around it turned that into the same "no").
"""

import json
import os
import stat
from pathlib import Path

from . import session_kept

NONE, OK, UNKNOWN, DAMAGED = "none", "ok", "unknown", "damaged"


def read(path):
    """``(value, state)``: the file's JSON value and ``OK``; otherwise None and ``NONE``, ``UNKNOWN`` or ``DAMAGED``."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None, NONE
    except IsADirectoryError:
        return None, DAMAGED   # Something that is no file is at the name; a write's replace fails on it anyway.
    except OSError:
        return None, UNKNOWN
    try:
        return json.loads(data), OK
    except (ValueError, RecursionError):
        return None, DAMAGED


def kept_values(path):
    """``[(copy, value)]``: the copies kept beside ``path`` (``session_kept``) that read as JSON now, oldest first. A
    store takes their content back in at its next load and removes a copy once a save holds all of it."""
    try:
        copies = session_kept.kept_copies(Path(path))
    except OSError:
        return []
    found = []
    for copy in copies:
        value, state = read(copy)
        if state == OK:
            found.append((copy, value))
    return found


def remove_copies(copies):
    """Remove kept copies whose whole content a save now holds (an error leaves one for a later save)."""
    for copy in copies:
        try:
            Path(copy).unlink()
        except OSError:
            pass


def keep_before_write(path):
    """Keep a damaged file beside it before a store writes over it. True when the write may go ahead (kept, or
    nothing regular is there now); False when it can't be kept now, and the store must not write."""
    try:
        kept = session_kept.preserve(Path(path))
    except OSError:
        return False
    if kept:
        print("Angelo: %s could not be read; it is kept beside it as %s" % (Path(path).name, kept))
    return True


def present(path):
    """True when a regular file is at ``path``, False when nothing (or something else) is, None when it can't be
    told now (a permission or I/O error): never "no" for an error."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return None
