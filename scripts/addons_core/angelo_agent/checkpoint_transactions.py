# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reentrant profile transactions shared by capture, journals and file effects."""

import os
import threading
import time
from contextlib import contextmanager
from .checkpoint_filesystem import _file_info, _open_file, _private_directory

_LOCK = threading.RLock()
_TRANSACTIONS = threading.local()


def lock_descriptor(descriptor, timeout, busy_message):
    """Take an exclusive inter-process lock on an open private lock file.

    Returns the release callable. Raises RuntimeError(busy_message) once
    ``timeout`` seconds pass, so callers never block the UI indefinitely.
    """
    if os.name == "nt":
        import msvcrt
        if not os.fstat(descriptor).st_size:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        acquire = lambda: msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        release = lambda: msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        acquire = lambda: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = lambda: fcntl.flock(descriptor, fcntl.LOCK_UN)
    deadline = time.monotonic() + timeout
    while True:
        try:
            acquire()
            return release
        except (BlockingIOError, PermissionError):
            if time.monotonic() >= deadline:
                raise RuntimeError(busy_message)
            time.sleep(0.01)


def holds_transaction(root):
    """True when the calling thread is inside ``_transaction(root)``."""
    return str(root) in getattr(_TRANSACTIONS, "held", {})


def holds_any_transaction():
    """True when the calling thread is inside any profile's transaction (background_pauses)."""
    return bool(getattr(_TRANSACTIONS, "held", None))


@contextmanager
def _transaction(root):
    """Serialize profile journals/pruning across threads and cooperating apps."""
    with _LOCK:
        held = getattr(_TRANSACTIONS, "held", {})
        if str(root) in held:
            yield
            return
        with _private_directory(root, create=True, lookup_only=True) as folder:
            descriptor = _open_file(folder, "checkpoint.lock", os.O_RDWR | os.O_CREAT)
            try:
                _file_info(folder, "checkpoint.lock")
                release = lock_descriptor(descriptor, 2, "Another app is updating snapshots; retry after it finishes")
                held[str(root)] = descriptor
                _TRANSACTIONS.held = held
                try:
                    yield
                finally:
                    del held[str(root)]
                    release()
            finally:
                os.close(descriptor)
