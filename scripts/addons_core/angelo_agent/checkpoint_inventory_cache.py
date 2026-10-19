# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The snapshot worker's lock-free probe re-reads only the snapshot records that changed.

After every capture the worker probes the whole history, without the profile
transaction, to decide whether retention or orphan cleanup has anything to do
(``snapshot_retention``, ``snapshot_maintenance``). Measuring a record reads
and validates its ``checkpoint.json``, lists its folder and fingerprints it
(``checkpoint_inventory._measure``). A record whose folder and files are the
same objects in the same state gives the same measurement, so this probe keeps
each measurement with the state it was taken from and measures again only when
that state differs (``checkpoint_policy.rst``, "Per-request cost and stored
history"):

* the state is the ``lstat`` of the record's folder (device, inode,
  modification and change time) and of every file in it (device, inode, size,
  mode, links, modification and change time), observed *before* the record is
  read, so a change during the read shows up as a difference next time;
* an entry is kept only when every one of those times is more than
  ``RACY_SECONDS`` older than the scan, so a later change can never share a
  timestamp with it (coarse file-system clocks included); the change time
  cannot be set back by a program, unlike the modification time (FAT and
  exFAT report the modification time as the change time, but on macOS
  snapshot storage there already fails closed on its AppleDouble ``._``
  files, and the profile lives on the system volume);
* checking an entry costs one ``lstat`` of the folder and one of each file; a
  file added, removed, replaced or written in place changes one of them.

The result is exactly what ``_measure`` returns for an unchanged record. Only
the worker's lock-free probes use it (``inventory(..., probe=True)``); every
scan under the transaction (restore plans, removal plans and reviews, worker
recovery, timeline validation) measures every record afresh. It runs on the
POSIX descriptor backend; elsewhere (Windows handles, the legacy path backend)
the probe measures every record, as before.
"""

import os
import stat
import threading
import time

from .background_pauses import pause
from .checkpoint_filesystem import _private_child, _private_directory, list_names
from .checkpoint_policy import _identity

RACY_SECONDS = 2
_caches = {}
_lock = threading.Lock()


def _folder_state(info):
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


def _file_state(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns


class _Entry:
    __slots__ = ("folder", "files", "measured")

    def __init__(self, folder, files, measured):
        self.folder, self.files, self.measured = folder, files, measured

    def current(self, session_folder, identity):
        """Whether the record's folder and files are still exactly as measured (lstat only, nothing read)."""
        try:
            info = os.stat(identity, dir_fd=session_folder, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or _folder_state(info) != self.folder:
                return False
            # The folder is the one measured (same inode, same entries), so its files are named through it.
            return all(_file_state(os.stat(identity + "/" + name, dir_fd=session_folder, follow_symlinks=False)) == state
                       for name, state in self.files)
        except OSError:
            return False


def _settled(folder, files, before_ns):
    times = [folder[2], folder[3]] + [value for _, state in files for value in state[5:7]]
    return all(value < before_ns for value in times)


def _measure_entry(measure, owner, session, identity, session_folder, before_ns):
    """Measure one record; returns ``(measurement, entry or None)``."""
    info = os.stat(identity, dir_fd=session_folder, follow_symlinks=False)
    with _private_child(session_folder, identity) as folder:
        files = tuple((name, _file_state(os.stat(name, dir_fd=folder, follow_symlinks=False)))
                      for name in sorted(list_names(folder)))
        measured = measure(owner, session, identity, folder)
    state = _folder_state(info)
    entry = _Entry(state, files, measured) if _settled(state, files, before_ns) else None
    return measured, entry


def scan(owner, records, issues, measure):
    """``checkpoint_inventory._scan`` for a lock-free probe, re-measuring only changed records.

    ``measure`` is ``checkpoint_inventory._measure``. Returns False when this backend cannot probe this way
    (nothing was scanned: the caller scans every record).
    """
    key = str(owner.root)
    before_ns = time.time_ns() - RACY_SECONDS * 1_000_000_000
    with _lock:
        cache = _caches.get(key, {})
    fresh = {}
    try:
        with _private_directory(owner.root / "checkpoints") as base:
            if type(base) is not int:
                return False
            for session in sorted(list_names(base)):
                try:
                    _identity(session)
                    with _private_child(base, session) as session_folder:
                        for identity in sorted(list_names(session_folder)):
                            if identity == "recoveries":
                                continue
                            pause()
                            try:
                                entry = cache.get((session, identity))  # Its name was validated when measured.
                                if entry is None:
                                    _identity(identity)
                                elif entry.current(session_folder, identity):
                                    records.append(dict(entry.measured))
                                    fresh[session, identity] = entry
                                    continue
                                measured, entry = _measure_entry(measure, owner, session, identity, session_folder,
                                                                 before_ns)
                                records.append(dict(measured))
                                if entry is not None:
                                    fresh[session, identity] = entry
                            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                                issues.append({"session_id": session, "id": identity, "error": str(error)})
                except (OSError, ValueError, TypeError, RecursionError) as error:
                    issues.append({"session_id": session, "error": str(error)})
    except FileNotFoundError:
        pass
    with _lock:
        _caches[key] = fresh
    return True


def forget(root=None):
    """Drop the kept measurements (all, or one profile's)."""
    with _lock:
        if root is None:
            _caches.clear()
        else:
            _caches.pop(str(root), None)
