# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One worker or removal at a time in an update's folder (``update_flow``).

Stopping a preparation only asks it to stop: the download watches the request,
but unpacking and verifying run to the end. So whatever comes next for that
folder (the same version prepared again, a newer version's clean-up, Stay's
removal) waits for the worker to let go:

* **In this process**, a table of held folders (kept through a reload of this
  module, whose workers may still run). A worker waits its turn; a removal
  asked for while a worker holds the folder happens as that worker lets go.
* **Across processes** (another Angelo of the same install shares the cache
  folder), an OS lock on ``<root>/.<version>.lock``. A worker waits for it; a
  removal skips a folder another process holds, for a later clean to take.
  A removal deletes the lock file too: on macOS and Linux while it holds it,
  and a lock taken on a file that was deleted meanwhile is let go and taken
  again on the new one, so two processes never hold different files; on
  Windows right after letting go, which fails while anyone has the file open
  (Windows deletes no file another handle opened without sharing deletion). When the OS can't lock here, only this
  process's hold applies; a removal queued on a worker that never got the OS
  lock runs only if it can take it then.

Standard library only; no bpy. ``hold`` blocks: call it from a worker thread.
"""

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:   # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:   # macOS, Linux
    msvcrt = None

POLL = 0.1      # Seconds between looks while waiting for this process's holder.
OS_POLL = 0.25  # Seconds between tries of another process's lock.
OS_TOLD = 300   # Seconds of waiting for another process before the log says so (once).
_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLK", -1),
         getattr(errno, "EDEADLOCK", -1)}
# Kept through a reload of this module, like everything a running worker of the code it replaces still uses.
_HELD_ELSEWHERE = globals().get("_HELD_ELSEWHERE") or object()
_PENDING = globals().get("_PENDING") or object()   # A holder that has not got the OS lock (yet).
# Held folder -> {"removals": [(remove, unless)], "lock": the OS lock's file, None or _PENDING, "folder"}.
_held = globals().get("_held") or threading.Condition()
_holders = globals().get("_holders", {})
_earlier_stopped = globals().get("Stopped")


class Stopped(Exception):
    """The worker was asked to stop while it waited; the folder was not touched."""


Stopped = _earlier_stopped or Stopped


def _key(folder):
    return os.path.normcase(os.path.realpath(str(folder)))


def lock_path(folder):
    return Path(folder).parent / ("." + Path(folder).name + ".lock")


def _os_lock(folder):
    """The locked file, ``_HELD_ELSEWHERE``, or None when the OS can't lock here."""
    path = lock_path(folder)
    for _attempt in range(3):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "a+b")
        except OSError:
            return None
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                handle.close()
                return None
        except OSError as error:
            handle.close()
            return _HELD_ELSEWHERE if error.errno in _BUSY else None
        if fcntl is None:
            return handle   # Windows: nobody deletes a file another handle has open.
        try:
            if os.path.samestat(os.fstat(handle.fileno()), os.stat(path)):
                return handle
        except OSError:
            pass
        _os_unlock(handle)   # Its holder deleted the file as it let go: lock the one at the path now.
    return None   # Never the same file twice: this process's hold only, rather than waiting on it forever.


def _os_unlock(handle):
    if not hasattr(handle, "fileno"):
        return   # None, _PENDING: no OS lock.
    try:
        if fcntl is None and msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        handle.close()


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _wanted_off(unless):
    try:
        return bool(unless())
    except Exception:
        return True   # Unknown: the folder stays, for a later clean.


def _release(key):
    """Let go of a folder: first the removals asked for while it was held (it goes unless every one of them was
    called off), including any asked for while those ran, then the OS lock, then the next holder may start."""
    done, elsewhere = 0, False
    try:
        with _held:
            holder = _holders.get(key)
            pending = holder is not None and holder["lock"] is _PENDING
        if pending:   # Stopped while another process held the folder: its removals need the OS lock first.
            lock = _os_lock(holder["folder"])
            elsewhere = lock is _HELD_ELSEWHERE
            with _held:
                holder["lock"] = None if elsewhere else lock
        while True:
            with _held:
                removals = list(_holders.get(key, {}).get("removals", ()))
                if len(removals) <= done:
                    break
            asked, done = removals[done:], len(removals)
            if elsewhere:
                continue   # Another process's folder: a later clean takes it.
            if any(unless is None or not _wanted_off(unless) for _remove, unless in asked):
                try:
                    asked[0][0]()
                except Exception as error:   # The holder's own result stands; the next clean tries again.
                    print("Angelo update: an update folder could not be removed (%s)" % type(error).__name__)
    finally:
        with _held:
            holder = _holders.get(key)
        # The OS lock goes before the entry, so a removal asked for meanwhile is still queued here, never refused by
        # this process's own lock.
        if holder is not None and hasattr(holder["lock"], "fileno"):
            gone = done and not os.path.lexists(holder["folder"])
            if gone and fcntl is not None:
                _unlink(lock_path(holder["folder"]))   # Still held: a waiter takes the new file (``_os_lock``).
            _os_unlock(holder["lock"])
            if gone and fcntl is None:
                _unlink(lock_path(holder["folder"]))   # Refused while any other handle has it open.
        with _held:
            late = _holders.pop(key, {}).get("removals", ())[done:]
            _held.notify_all()
        if late and not elsewhere and any(unless is None or not _wanted_off(unless) for _remove, unless in late):
            remove(holder["folder"], late[0][0])   # Asked for while the OS lock went: as any removal now.


@contextmanager
def hold(folder, cancel=None):
    """Hold ``folder`` alone, once every other holder, here or in another process, let go. Raises ``Stopped``
    when ``cancel`` is set before that."""
    key = _key(folder)
    with _held:
        while True:
            if cancel is not None and cancel.is_set():
                raise Stopped()
            if key not in _holders:
                break
            _held.wait(POLL)
        _holders[key] = {"removals": [], "lock": _PENDING, "folder": str(folder)}
    try:
        since = time.monotonic()
        while True:
            lock = _os_lock(folder)
            if lock is not _HELD_ELSEWHERE:
                break
            if since is not None and time.monotonic() - since > OS_TOLD:
                print("Angelo update: waiting for another Angelo to let go of %s" % Path(folder).name)
                since = None
            if cancel is None:
                time.sleep(OS_POLL)
            elif cancel.wait(OS_POLL):
                raise Stopped()
        with _held:
            _holders[key]["lock"] = lock
        yield
    finally:
        _release(key)


def remove(folder, action, unless=None):
    """Run ``action`` (which removes ``folder``) now when nobody holds the folder; when a worker of this process
    does, as it lets go; when another process does, not at all. ``unless`` is asked right before: true calls this
    removal off. Never blocks. True when the folder is gone now."""
    key = _key(folder)
    with _held:
        if key in _holders:
            _holders[key]["removals"].append((action, unless))
            return False
        _holders[key] = {"removals": [(action, unless)], "lock": _PENDING, "folder": str(folder)}
    lock = _os_lock(folder)
    if lock is _HELD_ELSEWHERE:
        with _held:
            _holders.pop(key, None)
            _held.notify_all()
        return False
    with _held:
        _holders[key]["lock"] = lock
    _release(key)
    return not Path(folder).exists()
