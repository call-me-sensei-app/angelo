# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private inherited Windows CDP pipes; no process launcher or browser capability.

The caller owns process/job/profile validation and serialized, cancellable CDP calls.
Use ``spawn_kwargs`` once around ChildProcess construction, then read/write the parent
ends. Windows Python 3.12+ nonblocking pipes are required; there is no blocking fallback.
This helper does not prove Windows browser support or private profile ACLs.
"""
from contextlib import contextmanager
import os
import subprocess
import sys
import threading

READ_CHUNK, WRITE_CHUNK = 65536, 4096  # Match the existing serialized CDP transport.
_INHERIT_LOCK = threading.Lock()


class WindowsPipeUnavailable(RuntimeError):
    """Closed reasons only; never include handles or browser protocol data."""
    def __init__(self, code):
        self.code = code
        super().__init__('Fab Windows pipe: ' + code)


class PipeCleanupUnavailable(WindowsPipeUnavailable):
    """Caller must retain ``owner`` and retry close; ``__cause__`` is the primary failure."""
    def __init__(self, owner, cleanup_error):
        super().__init__('PIPE_CLEANUP_UNPROVED')
        self.owner, self.cleanup_error = owner, cleanup_error


def _runtime():
    if os.name != 'nt' or sys.version_info < (3, 12):
        raise WindowsPipeUnavailable('PLATFORM_UNVERIFIED')
    import msvcrt
    return msvcrt


class WindowsPipes:
    """Own four descriptors until spawn, then only the two nonblocking parent ends.

    Each native I/O borrow is serialized against close. The inheritance lock only
    serializes this helper's inheritance windows, not unrelated inherit-all launchers
    in the application. Failed construction/cleanup returns ownership in the raised
    PipeCleanupUnavailable.owner; callers must retain it until close succeeds.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._fds = [None, None, None, None]  # Allocate ownership slots before any native pipe.
        self._read = self._write = None
        self._child_read = self._child_write = None
        self._spawned = False
        crt = _runtime()
        try:
            self._child_read, self._write = self._pair(0)
            self._read, self._child_write = self._pair(2)
            for fd in self._fds:
                os.set_inheritable(fd, False)
                crt.setmode(fd, os.O_BINARY)
            for fd in (self._read, self._write):
                os.set_blocking(fd, False)
                if os.get_blocking(fd):
                    raise WindowsPipeUnavailable('NONBLOCKING_UNAVAILABLE')
        except BaseException as primary:
            self._cleanup(tuple(self._fds), primary=primary)
            raise

    def _pair(self, start):
        pair = os.pipe()
        # Fixed list slots replace references without allocating/growing a container
        # after the native descriptors have been created.
        self._fds[start] = pair[0]
        self._fds[start + 1] = pair[1]
        return pair

    def _cleanup(self, descriptors, *, primary=None, restore=False):
        failure = None
        for fd in descriptors:
            if restore and fd is not None and fd in self._fds:
                try:
                    os.set_inheritable(fd, False)
                except OSError as error:
                    failure = failure or error
            if fd is not None and fd in self._fds:
                slot = self._fds.index(fd)
                try:
                    os.close(fd)
                    self._fds[slot] = None
                except OSError as error:
                    failure = failure or error
        if failure is not None:
            raise PipeCleanupUnavailable(self, failure) from (primary if primary is not None else failure)

    @contextmanager
    def spawn_kwargs(self):
        """Yield Chrome arguments/Popen kwargs once; close child copies on every exit.

        Chrome's AdoptPipes consumes unsigned 32-bit HANDLE values in read,write
        order, not CRT descriptor numbers. Only these child ends are inheritable.
        https://github.com/chromium/chromium/blob/main/content/browser/devtools/devtools_agent_host_impl.cc
        """
        with self._lock:
            if (self._spawned or self._child_read is None or self._child_write is None
                    or self._child_read not in self._fds or self._child_write not in self._fds):
                raise WindowsPipeUnavailable('SPAWN_ALREADY_USED')
            self._spawned = True
            children = (self._child_read, self._child_write)
            primary = None
            with _INHERIT_LOCK:
                try:
                    handles = tuple(_runtime().get_osfhandle(fd) for fd in children)
                    if any(type(handle) is not int or not 0 < handle <= 0xffffffff for handle in handles):
                        raise WindowsPipeUnavailable('INVALID_PIPE_HANDLE')
                    for fd in children:
                        os.set_inheritable(fd, True)
                    startup = subprocess.STARTUPINFO(lpAttributeList={'handle_list': list(handles)})
                    yield (['--remote-debugging-pipe', '--remote-debugging-io-pipes=%d,%d' % handles],
                           {'startupinfo': startup, 'close_fds': True})
                except BaseException as error:
                    primary = error
                    raise
                finally:
                    self._cleanup(children, primary=primary, restore=True)

    def read(self, maximum=READ_CHUNK):
        """Return bytes (empty means EOF), or None when no bytes are ready."""
        if type(maximum) is not int or not 0 < maximum <= READ_CHUNK:
            raise ValueError('Invalid pipe read size')
        with self._lock:
            if self._read is None or self._read not in self._fds:
                raise WindowsPipeUnavailable('CLOSED')
            try:
                return os.read(self._read, maximum)
            except BlockingIOError:
                return None

    def write(self, data):
        """Return bytes written, including partial progress; zero means retry later."""
        if type(data) is not bytes or not 0 < len(data) <= WRITE_CHUNK:
            raise ValueError('Invalid pipe write size')
        with self._lock:
            if self._write is None or self._write not in self._fds:
                raise WindowsPipeUnavailable('CLOSED')
            try:
                return os.write(self._write, data)
            except BlockingIOError:
                return 0

    def close(self):
        """Close all descriptors, retaining failed closes in exception.owner for retry."""
        with self._lock:
            self._cleanup(tuple(self._fds))


def create():
    return WindowsPipes()
