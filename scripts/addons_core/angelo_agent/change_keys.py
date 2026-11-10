# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keys of files for a change detector: one key per file for as long as its bytes stay as they are, and another
whenever they change, in the tick of the file system's clock too (``change_clock``).

A change detector compares a file's key with the one it took before and acts on a difference (a live update, a
shader swap, a bake). A stat key can't see a same-size write in the clock's tick (Windows), and ``cache_key``'s
answer there, a fresh ``unique()`` per call, reads as a change at every call and once more when the clock passes the
file: right for a cache, whose miss recomputes the same value, never for a detector, whose miss is an action. So
``key`` gives each file one form for as long as it is unchanged:

* a file first seen once the clock has passed its times (``change_clock.settle``, which judges each volume: a
  volume that stamps fine-grained times at once, a tick's wait at most on Blender's main thread, never one that
  would freeze it) is keyed by its stat (the site's own key). Its bytes are never read.
* a file seen before that (one written moments ago on a volume that keeps times in coarse steps, such as FAT, exFAT
  or a network share, where the main thread can't wait the step out) is keyed by the SHA-256 of its bytes, read
  again at each call while the clock hasn't passed it, then once more when first seen settled; that digest stands
  for the settled stat from then on.

The form is decided by the first sight of each stat and kept for it: a stat once judged settled stays keyed by itself
even when the clock later reaches a time it carries (a modification time set ahead of the clock counts as past, as
``change_clock`` judges it; a write when the clock reaches that time could share it, the limit ``change_clock``
states). A change time ahead of the clock counts as passed (``change_clock.settled``): every later write stamps an
earlier time. Each detector keeps its own ``ChangeKeys`` (a file another detector saw first must not change form
there), holding the forms of at most ``limit`` files (16,384: about 8 MB), the least recently keyed dropped first; a
dropped file first seen unsettled reads as changed once if it is keyed again (never a missed change). The forms live
in this process: a key saved with a bake and read by another process after its file was first seen unsettled is a
stat key there (one bake more, never a stale one).
"""

import hashlib

from . import change_clock


class Deferred(Exception):
    """The bytes this call may hash are spent: the file is keyed in a later call."""


class ChangeKeys:
    """One detector's forms: path -> (stat key, its SHA-256 or None for the stat form, whether read once settled),
    least recently keyed first, at most ``limit``."""

    def __init__(self, limit=16384):
        self.limit = limit
        self.forms = {}

    def clear(self):
        self.forms.clear()

    def hashed(self):
        """{path: entry} of the files keyed by their bytes."""
        return {path: entry for path, entry in self.forms.items() if entry[1] is not None}


def key(witness, path, stat_key=None, *, keys, change=True, wait=True, budget=None, name=None):
    """The key of the file at ``path`` whose stat is ``witness``, for the detector whose ``ChangeKeys`` is ``keys``:
    ``stat_key`` (default: device, inode, size, modification and change times) or ``("sha256", digest)``. ``change``:
    whether ``stat_key`` holds the change time (``change_clock.settle``'s). ``wait``: False for a small file, whose
    bytes cost less to read than a tick's wait. ``budget``: a one-item list of the bytes this call may still read
    (None: no bound); past it, ``Deferred`` (the file that crosses it is read whole). ``name``: what the form is
    kept under (default ``path``; one name for every spelling of one file). An ``OSError`` reading the file is the
    caller's, as a stat's would be."""
    if stat_key is None:
        stat_key = (witness.st_dev, witness.st_ino, witness.st_size, witness.st_mtime_ns, witness.st_ctime_ns)
    forms, path_read, path = keys.forms, path, path if name is None else name
    known = forms.get(path)
    if known is not None and known[0] == stat_key and (known[1] is None or known[2]):
        forms[path] = forms.pop(path)   # Decided for this stat (most recently keyed last).
        return stat_key if known[1] is None else ("sha256", known[1])
    try:
        if not wait:
            raise change_clock.Unsettled("not waited for")
        change_clock.settle(witness, change=change)
        settled = True
    except change_clock.Unsettled:   # A change time ahead of the clock counts as past: a later write moves it.
        settled = change_clock.settled(witness, change=change)
    if settled and (known is None or known[0] != stat_key):
        entry, found = (stat_key, None, True), stat_key   # First seen settled: its stat, never its bytes.
    else:   # Unsettled, or hashed while unsettled and settled now: its bytes (once more, then kept).
        digest = _digest(path_read, witness.st_size, budget)
        entry, found = (stat_key, digest, settled), ("sha256", digest)
    forms.pop(path, None)
    forms[path] = entry
    while len(forms) > keys.limit:
        del forms[next(iter(forms))]
    return found


def _digest(path, size, budget):
    """The SHA-256 of a file's first ``size`` bytes (its stat's) and of whether more follow, read in 1 MiB blocks: a
    file still being written is never followed past what its stat said (a later poll sees its new stat; break-it
    round 3, M1). ``budget`` is charged the bytes read; a file that can't be opened costs nothing."""
    if budget is not None and budget[0] <= 0:
        raise Deferred(path)
    digest, left = hashlib.sha256(b"%d:" % size), size + 1   # One byte more tells a file that grew; the stat's
    # size first, so a file read past its stat never digests as one of that longer size (break-it round 4, L2).
    with open(path, "rb") as handle:
        while left > 0:
            block = handle.read(min(left, 1 << 20))
            if not block:
                break
            digest.update(block)
            left -= len(block)
            if budget is not None:
                budget[0] -= len(block)
    return digest.hexdigest()
