# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One lock across every Angelo process of a person: the team sign-in's single flight.

All Angelo windows and instances on a computer share one team sign-in (one
grant), and a refresh token presented twice ends that grant for everyone
(reuse detection). So a refresh, and the store read and write around it,
happen while holding this lock, and the other processes re-read the stored
token after waiting (``team_account``). Grant-key issuing holds it too, so
instances share one key instead of each issuing their own.

The lock is an OS file lock on ``team-account.lock`` in the connections
folder, so it is released when its process exits, even after a crash:
``fcntl.flock`` on macOS and Linux, ``msvcrt.locking`` on one byte on
Windows. Both are taken without blocking and retried until ``timeout``:
``msvcrt.LK_LOCK`` would give up on its own after about 10 seconds, shorter
than one refresh may take. A timeout raises ``LockTimeout``; the caller keeps
its token and tries again later, never presents it unlocked.

Standard library only; no bpy.
"""

import contextlib
import os
import threading
import time
from pathlib import Path

LOCK_NAME = "team-account.lock"
TIMEOUT = 60.0   # seconds: longer than a refresh's network deadline plus the store calls
POLL = 0.05


class LockTimeout(TimeoutError):
    """Another Angelo process held the team sign-in lock for longer than the timeout."""


def _try_lock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def held(directory, timeout=TIMEOUT, *, name=LOCK_NAME, clock=time.monotonic, sleep=time.sleep):
    """Hold the lock file ``directory/name`` exclusively; ``LockTimeout`` after ``timeout`` seconds."""
    path = Path(directory) / name
    with open(path, "a+b") as handle:
        deadline = clock() + timeout
        while not _try_lock(handle):
            if clock() >= deadline:
                raise LockTimeout("Another Angelo window is renewing the team sign-in")
            sleep(POLL)
        try:
            yield
        finally:
            _unlock(handle)


class Holder:
    """An OS file lock a process keeps for as long as it holds something the others wait for.

    ``take`` locks ``directory/name`` without waiting (True when this process holds it now); ``release`` lets it go;
    ``held_elsewhere`` tells whether another live process holds it. The OS releases it when its process exits, so a
    closed or crashed holder reads as gone (``team_refresh_marks``: the holder of an unsaved team sign-in).
    """

    def __init__(self, directory, name):
        self.path, self._handle = Path(directory) / name, None
        self._guard = threading.Lock()  # ``take`` and ``release`` run on the worker and on chat threads.

    @property
    def held(self):
        return self._handle is not None

    def take(self):
        with self._guard:
            return self._take()

    def _take(self):
        if self._handle is None:
            handle = open(self.path, "a+b")
            try:
                locked = _try_lock(handle)
            except OSError:
                handle.close()
                raise
            if not locked:
                handle.close()
                return False
            self._handle = handle
        return True

    def release(self):
        with self._guard:
            handle, self._handle = self._handle, None
            if handle is not None:
                try:
                    _unlock(handle)
                finally:
                    handle.close()

    def held_elsewhere(self):
        """True when another process holds the lock (this process's own hold counts as not elsewhere)."""
        if self._handle is not None:
            return False
        with open(self.path, "a+b") as handle:
            if not _try_lock(handle):
                return True
            _unlock(handle)
            return False


def factory(directory, timeout=TIMEOUT):
    """A context-manager factory, as ``TeamAccount(process_lock=…)`` takes: no argument, or a shorter wait."""
    return lambda wait=timeout: held(directory, wait)
