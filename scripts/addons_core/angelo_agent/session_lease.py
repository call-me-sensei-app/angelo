# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One live Angelo process writes a chat: the chat's lease, and which of a process's stores is the chat's.

Every Angelo process reads the same state folder, and more than one can run at once: a second window started
from the dock, a background Angelo an engine starts to import a ``.blend`` (Godot does on every save of one),
a command-line render. Each loads the active chat like the first, and before 2.0.0 each wrote it: a file load
binds the chat and saves, a quit compacts. The chat's file then held another writer's content, the open chat's
store refused to write over it (``session_guard``, ``JournalTaken``) and went on refusing, so admission, which
journals a call before it starts, answered "The invocation journal could not be saved" for every call until
Angelo was started again (the showcase's Godot session, 2026-11-14: six times, after each save through the
engine link).

* **The lease.** A lead chat's store writes only while its process holds the chat's lease: an OS lock on
  ``leases/<chat id>.lease`` in the state folder (``fcntl.flock``; on Windows ``msvcrt.locking`` on one byte), taken
  without waiting when the store loads the chat or first writes it, shared by the process's stores of the chat
  and given up when the last of them is closed (``Controller.close``) or gone, or the process ends, so a crash
  frees it. A process that finds it held opens the chat as a view: every write raises ``JournalLeased`` and
  nothing of the chat is written there (``GuardedWriter._refuse``; its pending messages too, ``view``); when the
  holder ends, the view's next save takes the lease by itself. Where a lock cannot be had (the folder is not
  there yet, a file system without locks) there is no lease, and the store is guarded as before by
  ``session_guard`` alone. A worker Blender's store of a hosted chat takes none: its ownership is the hand-off's
  (``worker_journal_owner``), and a lease held by a parked worker would lock Angelo out of the chat it handed back.
* **The chat's store in this process.** The writer that loaded the chat last (``own``, ``owns``). Only it takes a
  file back from another writer (``GuardedWriter.take_back``): an older store of the same chat in the same
  process is superseded, and still writes nothing over what the newer one wrote.

The lease file is empty, is never removed (removing a lock file lets two processes each hold one), and stays
outside ``sessions/`` so a chat's own files (``conversations._parts``) never include a file a process holds open.

Standard library only; no bpy.
"""

import errno
import os
import threading
import weakref
from pathlib import Path

from .session_journal import JournalTaken
from . import owned_paths

FOLDER = "leases"
# CreateFileW: read and write access (read alone for ``holder``), sharing read, write and delete, the file made
# when missing (or only opened, for ``holder``).
_GENERIC_READ, _GENERIC_WRITE, _ATTRIBUTE_NORMAL = 0x80000000, 0x40000000, 0x80
_SHARE_ALL, _OPEN_EXISTING, _OPEN_ALWAYS = 0x7, 3, 4
# Windows: the one byte the lease locks, far past anything the file holds. A locked byte cannot be read through any
# other handle there, and the file holds the holder's process number: locked at its start, the file could not be
# read or copied while held (Angelo's carry-over from an earlier version's folder, a backup, Blender's copy of the
# previous version's settings). Locking past the end of a file is allowed and does not lengthen it.
_LOCK_AT = 1 << 30
_windows_api = None
_guard = threading.RLock()   # Re-entrant: a store's finalizer can run while this thread is inside ``held``.
_held = {}                   # (device, index) of a lease file -> this process's ``_Lock`` on it.
_owners = {}                 # Chat file (resolved) -> a weak reference to the writer that loaded it last here.


class JournalLeased(JournalTaken):
    """Another live Angelo process has this chat open: this one writes nothing to it."""


class _Lock:
    """This process's lock on one chat's lease file and how many of its stores share it."""

    __slots__ = ("descriptor", "users")

    def __init__(self, descriptor):
        self.descriptor, self.users = descriptor, 0


def lease_path(path):
    """The lease file of the chat at ``path``: beside ``sessions/``, never in it."""
    path = Path(path)
    base = path.parent.parent if path.parent.name == "sessions" else path.parent
    return base / FOLDER / (path.stem + ".lease")


def _open(lease, make=True):
    """A descriptor for the lease file, made when missing; ``make`` False only opens it, to read (``holder``).

    The lease opens its own file: never through ``session_journal``'s calls, which tests replace with stand-ins
    (one returned a plain descriptor, and on Windows a store's lease then kept its folder from being removed).
    On Windows the handle shares read, write and delete (``CreateFileW``), so a held lease never stops its
    folder's removal or a rename over it; elsewhere a descriptor never does.
    """
    owned_paths.require_writable(lease)
    if make:
        lease.parent.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        return os.open(lease, (os.O_RDWR | os.O_CREAT if make else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)
                       | getattr(os, "O_CLOEXEC", 0), 0o600)
    global _windows_api
    if _windows_api is None:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                           wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        _windows_api = (create, kernel32.CloseHandle, ctypes.get_last_error, msvcrt.open_osfhandle,
                        ctypes.c_void_p(-1).value)
    create, close, last_error, descriptor, invalid = _windows_api
    handle = create(str(lease), _GENERIC_READ | (_GENERIC_WRITE if make else 0), _SHARE_ALL, None,
                    _OPEN_ALWAYS if make else _OPEN_EXISTING, _ATTRIBUTE_NORMAL, None)
    if handle is None or handle == invalid:
        raise OSError(errno.EACCES, "The chat's lease file could not be opened (Windows error %d)" % last_error())
    try:
        return descriptor(handle, (os.O_RDWR if make else os.O_RDONLY) | getattr(os, "O_BINARY", 0))
    except BaseException:
        close(handle)
        raise


def _lock(descriptor):
    """True when the lock was taken, False when another process holds it; ``OSError`` when no lock can be had."""
    if os.name == "nt":
        import msvcrt
        os.lseek(descriptor, _LOCK_AT, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except PermissionError:   # The byte is locked: another process's lease.
            return False
        return True
    import fcntl
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _name_holder(descriptor):
    """Write this process's number into the lease it just took: the file's whole content, one line.

    Read by ``holder`` in a process the lease refuses, so its refusal can say which process has the chat
    (``save_failure``), and readable by anyone: the lock is far past it (``_LOCK_AT``). Never the reason a lease
    is not held: a lease that cannot be written is held without a name."""
    try:
        record = b"%d\n" % os.getpid()
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, record)
        os.ftruncate(descriptor, len(record))
    except OSError:
        pass


def holder(writer):
    """The number of the process that holds the lease of ``writer``'s chat, as that process wrote it; None when
    the lease file is missing, unreadable or names none. Only what the file says: whether the lease is held is
    ``held``'s answer, which a refusal already has when it asks this."""
    try:
        descriptor = _open(lease_path(writer.path), make=False)
    except OSError:
        return None
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        text = os.read(descriptor, 24).split(b"\n", 1)[0]
        return int(text) if text.isdigit() else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(descriptor)


def held(writer):
    """True when ``writer`` (a store's, with its chat's ``path``) may write: its process holds the chat's lease,
    taken now when it is free, or no lease can be had here. False while another live process holds it."""
    if "_lease" in vars(writer):
        return True
    try:
        descriptor = _open(lease_path(writer.path))
    except OSError:
        return True   # No lease file can be made (no state folder yet, a read-only one): the save itself says why.
    with _guard:
        try:
            status = os.fstat(descriptor)
            identity = (status.st_dev, status.st_ino)
            lock = _held.get(identity)
            if lock is None:
                if not _lock(descriptor):
                    os.close(descriptor)
                    return False
                lock = _held[identity] = _Lock(descriptor)
                _name_holder(descriptor)
            else:   # Another store of this process holds it: one lock, shared.
                os.close(descriptor)
        except OSError:   # This file system gives no lock: there is nothing to hold.
            os.close(descriptor)
            return True
        lock.users += 1
        writer._lease = weakref.finalize(writer, _release, identity)
        return True


def _release(identity):
    with _guard:
        lock = _held.get(identity)
        if lock is None:
            return
        lock.users -= 1
        if lock.users <= 0:
            del _held[identity]
            try:
                status = os.fstat(lock.descriptor)
                if (status.st_dev, status.st_ino) == identity:   # Still the lease file: never a descriptor put in
                    os.close(lock.descriptor)   # its place in Python state. The lock goes with its descriptor.
            except OSError:
                pass


def release(store):
    """``store``'s chat was closed here (``Controller.close``): its share of the lease is given up, and with the
    last share the lease. A later write by the same store takes it again when it is free."""
    writer = getattr(store, "_writer", store)
    finalizer = getattr(writer, "__dict__", {}).pop("_lease", None)
    if finalizer is not None:
        finalizer()


def view(store):
    """True when ``store`` is a view of a chat another live process has open: nothing of the chat is written here."""
    writer = getattr(store, "_writer", None)
    return bool(getattr(writer, "leased", False)) and not held(writer)


def _key(writer):
    """The chat's file, resolved once per writer: a resolve is a system call per path component, and a mirror's
    store loads its chat on the UI thread at every change (``worker_mirror_journal``)."""
    key = vars(writer).get("_chat_key")
    if key is None:
        key = writer._chat_key = os.path.realpath(os.fspath(writer.path))
    return key


def own(writer):
    """``writer`` just loaded its chat: it is the chat's store in this process."""
    key = _key(writer)
    with _guard:
        _owners[key] = weakref.ref(writer)


def owns(writer):
    """True when ``writer`` is the chat's store in this process: it loaded the chat last, or no other has."""
    key = _key(writer)
    with _guard:
        current = _owners.get(key)
        current = current() if current is not None else None
        if current is None:
            _owners[key] = weakref.ref(writer)
            return True
        return current is writer
