# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Start records: one locked file per starting process, and how a later launch finds a start that never completed.

The mechanics of ``angelo_safe_start`` (its docstring and doc/angelo/app_identity.rst, "A start that did not
finish", hold the contract): creating, locking, naming and releasing a record, reading another process's, judging
its owner, and the bounded wait. Standard library only, no bpy; tests/python/angelo_agent/test_native_safe_start.py.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

SUFFIX = ".start"
INCOMPLETE_AGE = 60.0     # A record cut short is removed once this old (a fresh one may be mid-creation).
HELD_WAIT = 2.5           # A record another process still holds: how long a launch waits for a crashed owner to end.
HELD_POLL = 0.1
FOUND_MARK = ".found"     # Beside a found record that could not be removed yet: it is never found again.
_identity = {}            # This process's host and boot (``identity``), read once.


# Locks: one byte. On Windows a lock is mandatory (a locked byte refuses every read of it), so it is one byte far
# past the record (Windows allows locks beyond end of file): the record reads back while held, as on macOS and Linux,
# where ``flock`` is advisory and covers the file. Callers seek back to the record's start.
LOCK_OFFSET = 1 << 20
if sys.platform == "win32":
    import msvcrt

    def _open_record(path):
        """Read a record without ever holding up its owner's removal (``FILE_SHARE_DELETE``, which Python's ``open``
        does not ask for): a reader's handle must not keep a completed record behind."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = kernel32.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)   # GENERIC_READ, all shares
        if handle in (None, wintypes.HANDLE(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb", buffering=0)

    def _lock(handle):
        handle.seek(LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle):
        handle.seek(LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _open_record(path):
        return open(path, "rb", buffering=0)


def identity():
    """Where a record's process id means something: this host, and its boot and process-id namespace where the system
    names them (Linux ``boot_id`` and the pid namespace, macOS's boot session). A settings folder can be shared (a
    roaming profile on a share, an NFS home, a Flatpak sandbox): another machine's or namespace's id says nothing
    here."""
    if "identity" not in _identity:
        import platform
        boot = None
        try:
            if sys.platform.startswith("linux"):
                boot = "%s/%s" % (Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                                  os.readlink("/proc/self/ns/pid"))
            elif sys.platform == "darwin":
                import ctypes
                libc = ctypes.CDLL(None)
                size = ctypes.c_size_t(64)
                buffer = ctypes.create_string_buffer(64)
                if libc.sysctlbyname(b"kern.bootsessionuuid", buffer, ctypes.byref(size), None, 0) == 0:
                    boot = buffer.value.decode("ascii", "replace")
        except (OSError, AttributeError, ValueError):
            boot = None
        _identity["identity"] = {"host": platform.node(), "boot": boot}
    return dict(_identity["identity"])


def _here(record):
    """Whether a record's process id can be judged here (same host and boot); a record without them is not."""
    mine = identity()
    return record.get("host") == mine["host"] and record.get("boot") == mine["boot"]


def _pid(record):
    pid = record.get("pid")
    return pid if type(pid) is int and 0 < pid < 1 << 32 else None


class Held:
    """This process's start record: open and locked until ``release``."""

    def __init__(self, path, handle):
        self.path, self._handle = path, handle

    def close(self):
        """End the lock but keep the record: the next launch finds a start that never completed."""
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()

    def release(self):
        """End the record. It is emptied while still locked, so whatever later holds up its removal (another launch
        reading it, a scanner), nothing that reads as a start that never completed stays behind."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            handle.truncate(0)
        except OSError:
            pass
        try:
            _unlock(handle)
        except OSError:
            pass
        handle.close()   # Before the removal: Windows removes no open file.
        for attempt in range(4):
            try:
                self.path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.02 * (attempt + 1))   # Another process has it open a moment; an empty record is harmless.


def applies(background, factory_startup):
    """Whether this run keeps a start record: a window started with the person's own preferences."""
    return not background and not factory_startup


def begin(folder, now=None):
    """Create, lock and write this process's start record; None when the folder cannot hold one (the start goes on)."""
    folder = Path(folder)
    record = json.dumps(dict(identity(), pid=os.getpid(), started=time.time() if now is None else now)).encode("utf-8")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("%d-%s%s" % (os.getpid(), uuid.uuid4().hex[:12], SUFFIX))
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    except OSError:
        return None
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        _lock(handle)
        handle.seek(0)
        handle.write(record)
    except OSError:
        handle.close()
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return Held(path, handle)


def _complete(data):
    try:
        record = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return record if isinstance(record, dict) and type(record.get("pid")) is int else None


def _alive(pid):
    """Whether process ``pid`` is running (not ended, even if the system still holds its object or its locks). Not
    knowing counts as running: that never makes a running start a crash."""
    if type(pid) is not int or pid <= 0:
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            handle = kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        except (OverflowError, TypeError, ctypes.ArgumentError):
            return True
        if not handle:
            return ctypes.get_last_error() != 87   # ERROR_INVALID_PARAMETER: no such process. Else (denied): running.
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0   # WAIT_OBJECT_0: ended; a timeout or failure: running.
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except Exception:   # PermissionError (another user's), OverflowError, anything else: running.
        return True
    return True


def _examine(path, now):
    """``(state, record)`` of one record: ``found`` (a start that never completed), ``held`` (locked by another
    process; ``record`` as it reads, or None), ``remove`` (cut short and old) or ``keep`` (cut short, young)."""
    try:
        handle = _open_record(path)
    except OSError:
        return "held", None   # Windows: open in a way that refuses this one, or removed and going.
    try:
        try:
            _lock(handle)
        except OSError:
            handle.seek(0)   # The record reads while held: the lock is past it (Windows) or advisory.
            return "held", _complete(handle.read())
        handle.seek(0)
        record = _complete(handle.read())
        if record is not None:
            state = "found"
        else:
            try:
                state = "remove" if now - os.fstat(handle.fileno()).st_mtime > INCOMPLETE_AGE else "keep"
            except OSError:
                state = "keep"
        try:
            _unlock(handle)
        except OSError:
            pass
        return state, record
    finally:
        handle.close()


def _remove(path):
    try:
        path.unlink()
        return True
    except OSError:
        return False


def interrupted(folder, now=None, wait=None, clock=time.monotonic, sleep=time.sleep):
    """The records of starts that never completed, each removed as it is found (so it is found once). One cut short
    is removed once old, and is not a crash.

    A record another process still holds is a start still running, or a crashed process still ending: on Windows the
    crash dialog's Restart starts the next launch while the crashed process ends, and the system releases a dead
    process's lock in its own time. So a held record whose owner is gone is found at once; one whose owner still runs
    is waited for (every record shares one ``HELD_WAIT`` bound, ``wait`` overrides it): found if its lock goes and
    its owner with it, never while its owner runs (that start completed), else left alone: a live start. Only a
    record from this host and boot (``identity``) naming a valid process id is judged by its process; any other held
    record is left alone at once, as one held by a live start. A found record that cannot be removed yet (Windows
    removes no file another process still has open) is marked found (``FOUND_MARK``), so no later launch finds it
    again."""
    folder = Path(folder)
    now = time.time() if now is None else now
    try:
        paths = sorted(folder.glob("*" + SUFFIX))
        for mark in folder.glob("*" + SUFFIX + FOUND_MARK):   # Its record went since: the mark goes too.
            if not mark.with_name(mark.name[:-len(FOUND_MARK)]).exists():
                _remove(mark)
    except OSError:
        return []
    deadline = clock() + (HELD_WAIT if wait is None else wait)
    found = []
    for path in paths:
        mark = path.with_name(path.name + FOUND_MARK)
        if mark.exists():   # Found by an earlier launch: removed now if it can be, never found again.
            if _remove(path):
                _remove(mark)
            continue
        waited = False
        while True:
            state, record = _examine(path, now)
            pid = _pid(record) if record is not None else None
            if state == "found" and waited and (pid is None or _alive(pid)):
                state = "keep"   # Its lock went while its owner runs: that start completed, it was no crash.
                break
            if state != "held" or pid is None or pid == os.getpid() or not _here(record):
                break   # Not held, or held by a process this launch cannot judge (another host or boot): left alone.
            if not _alive(pid):
                state = "found"   # Its owner is gone, whatever the lock still says: that start never completed.
                break
            if clock() >= deadline:
                break   # A live start: left alone.
            waited = True
            sleep(HELD_POLL)
        if state == "found":
            found.append(record)
            if not _remove(path):
                try:
                    mark.write_bytes(b"")
                except OSError:
                    pass
        elif state == "remove":
            _remove(path)
    return found
