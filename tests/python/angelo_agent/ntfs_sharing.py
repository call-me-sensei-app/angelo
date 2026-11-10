# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows' file sharing for host tests: a reader holding a file open refuses its replace as NTFS does.

A test makes one ``Sharing(case)``; it undoes itself at the case's cleanup.

* On Windows (``REAL``) nothing is emulated. A handle the test holds (``hold``) is a real one opened as Python's
  ``os.open`` does (sharing read and write, never delete, as the C runtime opens), and every replace is the real
  one, so a refusal is the real ``PermissionError`` ``[WinError 5]`` or ``[WinError 32]``.
* Elsewhere the host's real files are used and NTFS's rules are applied to them. Every handle opened through
  ``open``, ``io.open`` or ``os.open`` shares read and write only (the C runtime's ``_SH_DENYNO``), and one from
  ``session_journal.open_shared`` (the add-on's shared reader) shares read, write and delete. ``os.replace`` and
  ``os.rename`` (``MoveFileExW``) are refused with WinError 5 while any handle has either name open, or a file
  inside a folder being moved; ``os.rename`` onto an existing name raises ``FileExistsError`` (WinError 183);
  ``os.unlink``/``os.remove`` (``DeleteFileW``) are refused while a handle that does not share delete has the file.
  ``win32_replace``'s POSIX-semantics rename is refused with WinError 32 while a handle that does not share delete
  has either name. ``shared_replace._WINDOWS`` is set, so the add-on takes its Windows path.

On both, every open is watched: ``while_open(path, action)`` runs ``action`` (a writer) once, the first time any
reader opens ``path``, while that reader's handle is open. The retries' pauses are recorded (``pauses``) and never
slept, and ``hold(path, refusals=n)`` lets go of its handle after ``n`` refused replaces, as an indexer or a scan
does after a moment.
"""

import builtins
import io
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from angelo_agent import session_journal, shared_replace, win32_replace

REAL = os.name == "nt"
READ_WRITE, ALL = 0x3, 0x7   # FILE_SHARE_READ | FILE_SHARE_WRITE, and with FILE_SHARE_DELETE.
_DELETE = 0x4


class Sharing:
    def __init__(self, case):
        self._lock = threading.RLock()
        self.handles = {}    # descriptor -> (share, (device, inode), absolute path)
        self.refusals = []   # (target, Windows error) of each refused replace or rename, in order
        self.pauses = []     # Seconds each retry would have waited
        self._watch = []     # [(path, action)] waiting for a reader of that path (``while_open``)
        self._release = []   # [[descriptor, refusals left]] (``hold``)
        self._holds = set()  # Every descriptor ``hold`` opened and has not let go of
        self.action_errors = []   # What a ``while_open`` action raised (a reader may swallow it)
        self._real = SimpleNamespace(open=io.open, os_open=os.open, os_close=os.close, replace=os.replace,
                                     rename=os.rename, unlink=os.unlink, open_shared=session_journal.open_shared)
        patches = [(io, "open", self._io_open), (builtins, "open", self._io_open), (os, "open", self._os_open),
                   (os, "close", self._os_close), (session_journal, "open_shared", self._open_shared),
                   (shared_replace, "time", SimpleNamespace(sleep=self._pause, monotonic=time.monotonic))]
        if not REAL:
            patches += [(shared_replace, "_WINDOWS", True), (os, "replace", self._classic),
                        (os, "rename", self._rename), (win32_replace, "_posix", self._posix),
                        (win32_replace, "_logged", True), (os, "unlink", self._unlink), (os, "remove", self._unlink)]
        for target, name, value in patches:
            patcher = patch.object(target, name, value)
            patcher.start()
            case.addCleanup(patcher.stop)
        case.addCleanup(self.let_go_all)

    # The test's own handles ------------------------------------------------------------------------------------

    def hold(self, path, refusals=None):
        """A handle on ``path`` from another program (an editor, a scan): it shares read and write, never delete.
        With ``refusals``, it lets go after that many refused replaces of any file."""
        fd = self._real.os_open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self._held(fd, READ_WRITE, path)
        self._holds.add(fd)
        if refusals is not None:
            self._release.append([fd, refusals])
        return fd

    def let_go(self, fd):
        """Close a handle ``hold`` or ``session_journal.open_shared`` opened."""
        with self._lock:
            self.handles.pop(fd, None)
            self._release = [entry for entry in self._release if entry[0] != fd]
            self._holds.discard(fd)
        self._real.os_close(fd)

    def let_go_all(self):
        for fd in list(self._holds):
            self.let_go(fd)

    def while_open(self, path, action):
        """Run ``action()`` once, as soon as any reader opens ``path``, while that reader holds it."""
        self._watch.append((os.path.abspath(os.fspath(path)), action))

    # Opens ---------------------------------------------------------------------------------------------------

    def _held(self, fd, share, path):
        try:
            status = os.fstat(fd)
        except OSError:
            return
        with self._lock:
            self.handles[fd] = (share, (status.st_dev, status.st_ino), os.path.abspath(os.fspath(path)))
        self._fire((status.st_dev, status.st_ino))

    def _fire(self, key):
        for entry in list(self._watch):
            watched, action = entry
            try:
                status = os.stat(watched)
            except OSError:
                continue
            if (status.st_dev, status.st_ino) == key and entry in self._watch:
                self._watch.remove(entry)
                try:
                    action()
                except BaseException as error:  # noqa: BLE001 - kept for the test: the reader may swallow it.
                    self.action_errors.append(error)

    def _io_open(self, file, *args, **kwargs):
        handle = self._real.open(file, *args, **kwargs)
        if not isinstance(file, int):
            self._held(handle.fileno(), READ_WRITE, file)
        return handle

    def _os_open(self, path, flags, mode=0o777, *, dir_fd=None):
        fd = self._real.os_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is None:
            self._held(fd, READ_WRITE, path)
        return fd

    def _os_close(self, fd):
        with self._lock:
            self.handles.pop(fd, None)
        return self._real.os_close(fd)

    def _open_shared(self, path, follow=True):
        if REAL:
            fd = self._real.open_shared(path, follow)
        else:
            fd = self._real.os_open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        self._held(fd, ALL, path)
        return fd

    # NTFS's rules (elsewhere than Windows) ---------------------------------------------------------------------

    def _shares_on(self, name):
        """The share mode of every open handle on ``name``, or on a file inside it when it is a folder."""
        try:
            status = os.lstat(name)
        except OSError:
            return []
        key, inside = (status.st_dev, status.st_ino), os.path.abspath(os.fspath(name)) + os.sep
        found = []
        with self._lock:
            entries = list(self.handles.items())
        for fd, (share, opened, path) in entries:
            try:
                now = os.fstat(fd)
            except OSError:
                continue   # Closed (a file object closed by itself, never through os.close).
            if (now.st_dev, now.st_ino) != opened:
                continue   # Its number was reused for another file.
            if opened == key or (os.path.isdir(name) and path.startswith(inside)):
                found.append(share)
        return found

    def _refuse(self, source, target, code):
        self.refusals.append((os.fspath(target), code))
        for entry in list(self._release):
            entry[1] -= 1
            if entry[1] <= 0:
                self.let_go(entry[0])
        error = PermissionError(13, "[WinError %d] The process cannot access the file" % code, os.fspath(source),
                                None, os.fspath(target))
        error.winerror = code
        raise error

    def _classic(self, source, target, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is None:
            self._no_folder_for_a_file(source, target)
        if src_dir_fd is None and (self._shares_on(source) or self._shares_on(target)):
            self._refuse(source, target, 5)
        return self._real.replace(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def _rename(self, source, target, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is None and os.path.lexists(target):
            raise FileExistsError(17, "[WinError 183] Cannot create a file when that file already exists",
                                  os.fspath(source), None, os.fspath(target))
        return self._classic(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def _posix(self, source, target):
        if os.path.isdir(source):   # As CreateFileW without backup semantics: a folder is never opened (no reader).
            raise PermissionError(13, "[WinError 5] Access is denied", os.fspath(source), None, os.fspath(target))
        self._no_folder_for_a_file(source, target)
        if any(not share & _DELETE for share in self._shares_on(source) + self._shares_on(target)):
            self._refuse(source, target, 32)
        return self._real.replace(source, target)

    @staticmethod
    def _no_folder_for_a_file(source, target):
        """A folder at the name a file goes to, or a folder moved onto any existing name (``MoveFileExW`` replaces
        files only): NTFS answers access denied (never a reader to wait for)."""
        if (os.path.isdir(target) and not os.path.isdir(source)) or (os.path.isdir(source)
                                                                     and os.path.lexists(target)):
            raise PermissionError(13, "[WinError 5] Access is denied", os.fspath(source), None, os.fspath(target))

    def _unlink(self, path, *, dir_fd=None):
        """``DeleteFileW``: refused while a handle that does not share delete has the file open."""
        try:
            status = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            status = None
        if status is not None and any(not share & _DELETE for share in self._shares_of((status.st_dev, status.st_ino))):
            self._refuse(path, path, 32)   # Counted like a refused replace: a moment's holder lets go (``hold``).
        return self._real.unlink(path, dir_fd=dir_fd)

    def _shares_of(self, key):
        with self._lock:
            entries = list(self.handles.items())
        found = []
        for fd, (share, opened, _path) in entries:
            try:
                now = os.fstat(fd)
            except OSError:
                continue
            if (now.st_dev, now.st_ino) == opened == key:
                found.append(share)
        return found

    def _pause(self, seconds):
        self.pauses.append(seconds)
        if REAL:   # A real refusal precedes every pause: count it for ``hold(refusals=...)``.
            for entry in list(self._release):
                entry[1] -= 1
                if entry[1] <= 0:
                    self.let_go(entry[0])
