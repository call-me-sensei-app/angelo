# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Borrow existing Windows browser owners for launch/window evidence, never authentication.

No handle is opened, duplicated, closed or terminated here. Original CreateProcess argv is
bound through the SAME immutable tuple retained by Popen, not a mutable PEB command-line read.
Native calls and HWND enumeration have finite counts; kernel/driver latency is not a hard bound.
The backend must retain all existing owners on failure. WM_CLOSE posting is not exit evidence.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import math
import ntpath
import os
import time


class WindowsProofUnavailable(RuntimeError):
    def __init__(self, browser, code):
        super().__init__(code)
        self.owner = browser


class NormalCloseRequestUncertain(WindowsProofUnavailable):
    """Only a started PostMessage call lost/faulted its return; not a fresh proof refusal."""
    def __init__(self, browser, request):
        super().__init__(browser, 'WINDOWS_CLOSE_MESSAGE_RETURN_UNPROVED')
        self.request = request


@dataclass(frozen=True, eq=False, repr=False)
class _Binding:
    browser: object
    child: object
    job: object
    lease: object
    runtime: object
    epoch: str
    arguments: tuple
    process_handle: int
    job_handle: int
    pid: int
    creation: int
    profile_identity: tuple
    profile_generation: str
    profile_path: str
    program: str
    window: int = 0
    session: int = 0


class _WTSQuery:
    def __init__(self, c, w):
        self.pointer, self.bytes = c.c_void_p(), w.DWORD()
        self.issued = self.free_attempted = self.freed = self.complete = self.uncertain = False


class _CloseRequest:
    """Prepublished message intent; retains exact owners even when posting loses its return."""
    def __init__(self, binding, windows):
        self.binding, self.windows = binding, windows
        self.attempted = self.posted = 0
        self.complete = False
        self.uncertain = False


def _fail(browser, code):
    raise WindowsProofUnavailable(browser, code)


def _handle(value):
    raw = getattr(value, 'value', value)
    if raw is None:
        return 0
    return int(raw)


def _path(value):
    return ntpath.normcase(ntpath.normpath(value))


@contextmanager
def _borrow(browser, *, closing=False):
    # Lazy imports keep module loading free of native DLL/resource acquisition.
    from .fab_browser_process_windows import WindowsBrowserProcess
    from .fab_browser_profile_session_windows import WindowsProfileLease
    from .child_process import ChildProcess, _WindowsJob
    if type(browser) is not WindowsBrowserProcess:
        _fail(browser, 'WINDOWS_BROWSER_OWNER_REQUIRED')
    with browser._lock:
        child, lease = browser._process, browser._profile_lease
        if (type(child) is not ChildProcess or type(lease) is not WindowsProfileLease
                or (browser._closed and not closing) or browser._drained
                or browser._retention_revoked or lease._browser is not browser
                or lease._released):
            _fail(browser, 'WINDOWS_BROWSER_OWNER_UNAVAILABLE')
        # Camera's lifecycle lock serializes passive and forced cleanup. Never acquire it
        # from job.lock, or call a Job method which acquires that nonrecursive lock here.
        with child._job_lifecycle_lock:
            job = child._job
            if type(job) is not _WindowsJob:
                _fail(browser, 'WINDOWS_JOB_OWNER_REQUIRED')
            with job.lock:
                if (not child._retain_job or not child._job_ready or child._job_force_attempted
                        or child._passive_drained_job is not None or not job.required
                        or job.closed or job.close_unknown or job._close_attempted
                        or not _handle(child._handle) or not _handle(job.handle)):
                    _fail(browser, 'WINDOWS_JOB_OWNER_UNAVAILABLE')
                yield child, job, lease


def _capture(browser, child, job, lease, native, *, window=0):
    if lease.verify() is not True:
        _fail(browser, 'WINDOWS_PROFILE_PROOF_UNAVAILABLE')
    arguments = browser._launch_arguments
    program, runtime, epoch = browser._program, browser._runtime_generation, browser._runtime_epoch_token
    if (type(arguments) is not tuple or not 1 <= len(arguments) <= 32 or child.args is not arguments
            or any(type(item) is not str or len(item) > 32767 or '\0' in item for item in arguments)
            or sum(len(item) + 1 for item in arguments) > 32767
            or type(program) is not str or arguments[0] != program or not ntpath.isabs(program)
            or type(runtime) is not object or type(epoch) is not str or len(epoch) != 32
            or any(item not in '0123456789abcdef' for item in epoch)):
        _fail(browser, 'WINDOWS_LAUNCH_BINDING_UNPROVED')
    profile = str(lease.path)
    profile_flags = tuple(item for item in arguments[1:] if item.startswith('--user-data-dir'))
    pipe_flags = tuple(item for item in arguments[1:] if item.startswith('--remote-debugging-pipe'))
    if (profile_flags != ('--user-data-dir=' + profile,) or _path(str(browser.profile)) != _path(profile)
            or (browser._manual_login and (pipe_flags or browser._pipes is not None))
            or (not browser._manual_login and (pipe_flags != ('--remote-debugging-pipe',)
                                               or browser._pipes is None))):
        _fail(browser, 'WINDOWS_PROFILE_LAUNCH_MISMATCH')
    handle, job_handle = _handle(child._handle), _handle(job.handle)
    pid, created, image = native.process(handle, job_handle)
    if pid != child.pid or type(pid) is not int or pid <= 0 or created <= 0 or _path(image) != _path(program):
        _fail(browser, 'WINDOWS_NATIVE_LAUNCH_MISMATCH')
    session = native.interactive(browser, pid)
    if lease.verify() is not True:
        _fail(browser, 'WINDOWS_PROFILE_PROOF_UNAVAILABLE')
    if (browser._process is not child or child._job is not job or browser._profile_lease is not lease
            or browser._launch_arguments is not arguments or child.args is not arguments
            or browser._runtime_generation is not runtime or browser._runtime_epoch_token != epoch
            or browser._program != program or str(lease.path) != profile
            or _handle(child._handle) != handle or _handle(job.handle) != job_handle):
        _fail(browser, 'WINDOWS_NATIVE_BINDING_CHANGED')
    return _Binding(browser, child, job, lease, runtime, epoch, arguments, handle, job_handle,
                    pid, created, lease.identity, lease.generation, profile, program, window, session)


def _same(browser, before, after):
    if (before.child is not after.child or before.job is not after.job or before.lease is not after.lease
            or before.runtime is not after.runtime or before.arguments is not after.arguments
            or (before.epoch, before.process_handle, before.job_handle, before.pid, before.creation,
                before.profile_identity, before.profile_generation, before.profile_path, before.program, before.session)
            != (after.epoch, after.process_handle, after.job_handle, after.pid, after.creation,
                after.profile_identity, after.profile_generation, after.profile_path, after.program, after.session)):
        _fail(browser, 'WINDOWS_NATIVE_BINDING_CHANGED')


def prove(browser, *, foreground=False):
    """Fresh physical evidence only. foreground=True requests and verifies ONE owned window."""
    if type(foreground) is not bool:
        _fail(browser, 'WINDOWS_FOREGROUND_MODE_INVALID')
    try:
        with _borrow(browser) as (child, job, lease):
            native = _Native()
            before = _capture(browser, child, job, lease, native)
            window = 0
            if foreground:
                windows = native.windows(before.pid, visible=True, deadline=time.monotonic() + 2)
                if len(windows) != 1:
                    _fail(browser, 'WINDOWS_MANUAL_WINDOW_NOT_UNIQUE')
                window = windows[0]
                native.foreground(window, before.pid)
            after = _capture(browser, child, job, lease, native, window=window)
            _same(browser, before, after)
            if window:
                native.foreground_current(window, before.pid)
            return after
    except WindowsProofUnavailable:
        raise
    except BaseException as error:
        raise WindowsProofUnavailable(browser, 'WINDOWS_NATIVE_PROOF_UNAVAILABLE') from error



def require_query_cleanup(browser):
    """Existing backend close must retain its owner if the helper's native allocation is unknown.

    No retry/free/query is attempted here: an unknown WTSFreeMemory return is not reusable memory.
    """
    query = getattr(browser, '_windows_wts_query', None)
    if query is not None and (type(query) is not _WTSQuery or not query.complete or not query.freed):
        _fail(browser, 'WINDOWS_WTS_QUERY_CLEANUP_UNPROVED')


def request_normal_close(browser, deadline):
    """Post WM_CLOSE to ALL owned top-level windows; actual zero-exit+empty-Job is separate.

    Repeated calls never repost a possibly consumed message. A partial/lost posting return keeps
    this request attached to the browser; backend still owns its passive drain/unknown handling.
    """
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
        _fail(browser, 'WINDOWS_CLOSE_DEADLINE')
    try:
        with _borrow(browser, closing=True) as (child, job, lease):
            previous = getattr(browser, '_windows_close_request', None)
            if previous is not None:
                if (type(previous) is not _CloseRequest or previous.binding.browser is not browser
                        or previous.binding.child is not child or previous.binding.job is not job
                        or previous.binding.lease is not lease
                        or previous.binding.runtime is not browser._runtime_generation
                        or previous.binding.epoch != browser._runtime_epoch_token):
                    _fail(browser, 'WINDOWS_CLOSE_OWNER_CHANGED')
                binding = previous.binding
                # A prior posted request may now have an exited leader. Requiring fresh LIVE
                # process proof would prevent normal lost-return settlement. Revalidate only
                # retained owner/handle/launch/lease identity; this never posts another message.
                if lease.verify() is not True:
                    _fail(browser, 'WINDOWS_PROFILE_PROOF_UNAVAILABLE')
                require_query_cleanup(browser)
                if (_handle(child._handle) != binding.process_handle
                        or _handle(job.handle) != binding.job_handle or child.pid != binding.pid
                        or browser._launch_arguments is not binding.arguments
                        or child.args is not binding.arguments or browser._program != binding.program
                        or lease.identity != binding.profile_identity
                        or lease.generation != binding.profile_generation
                        or str(lease.path) != binding.profile_path
                        or str(browser.profile) != binding.profile_path):
                    _fail(browser, 'WINDOWS_CLOSE_BINDING_CHANGED')
                return previous
            native = _Native()
            before = _capture(browser, child, job, lease, native)
            windows = native.windows(before.pid, visible=False, deadline=deadline)
            if not windows:
                _fail(browser, 'WINDOWS_CLOSE_WINDOW_UNPROVED')
            after = _capture(browser, child, job, lease, native)
            _same(browser, before, after)
            request = _CloseRequest(after, windows)
            browser._windows_close_request = request  # Before ANY message can be delivered.
            try:
                for window in windows:
                    if time.monotonic() >= deadline:
                        _fail(browser, 'WINDOWS_CLOSE_DEADLINE')
                    native.window_current(window, before.pid)
                    request.attempted += 1  # Lost return cannot be retried as an unissued request.
                    try:
                        native.post_close(window)
                    except BaseException as error:
                        request.uncertain = True
                        raise NormalCloseRequestUncertain(browser, request) from error
                    request.posted += 1
                request.complete = True  # All posted only; never means closed/drained.
                return request
            except BaseException:
                request.uncertain = True
                raise
    except WindowsProofUnavailable:
        raise
    except BaseException as error:
        raise WindowsProofUnavailable(browser, 'WINDOWS_NORMAL_CLOSE_UNPROVED') from error


class _Native:
    """Documented Win32 queries/messages using borrowed handles only; no process-memory reads."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        if os.name != 'nt':
            raise OSError('Windows required')
        self.c, self.w = c, w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.u = c.WinDLL('user32', use_last_error=True)
        self.d = c.WinDLL('dwmapi', use_last_error=True)
        self.wts = c.WinDLL('wtsapi32', use_last_error=True)
        self.callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        functions = (
            (self.k, 'GetCurrentProcessId', w.DWORD, []),
            (self.k, 'GetCurrentThreadId', w.DWORD, []),
            (self.k, 'ProcessIdToSessionId', w.BOOL, [w.DWORD, c.POINTER(w.DWORD)]),
            (self.u, 'GetProcessWindowStation', w.HANDLE, []),
            (self.u, 'GetThreadDesktop', w.HANDLE, [w.DWORD]),
            (self.u, 'GetUserObjectInformationW', w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]),
            (self.wts, 'WTSQuerySessionInformationW', w.BOOL, [w.HANDLE, w.DWORD, c.c_int, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)]),
            (self.wts, 'WTSFreeMemory', None, [c.c_void_p]),
            (self.k, 'WaitForSingleObject', w.DWORD, [w.HANDLE, w.DWORD]),
            (self.k, 'GetProcessId', w.DWORD, [w.HANDLE]),
            (self.k, 'GetProcessTimes', w.BOOL, [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4),
            (self.k, 'IsProcessInJob', w.BOOL, [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]),
            (self.k, 'QueryFullProcessImageNameW', w.BOOL, [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]),
            (self.u, 'EnumWindows', w.BOOL, [self.callback, w.LPARAM]),
            (self.u, 'GetWindowThreadProcessId', w.DWORD, [w.HWND, c.POINTER(w.DWORD)]),
            (self.u, 'IsWindow', w.BOOL, [w.HWND]),
            (self.u, 'IsWindowVisible', w.BOOL, [w.HWND]),
            (self.u, 'IsIconic', w.BOOL, [w.HWND]),
            (self.u, 'GetAncestor', w.HWND, [w.HWND, w.UINT]),
            (self.u, 'SetForegroundWindow', w.BOOL, [w.HWND]),
            (self.u, 'GetForegroundWindow', w.HWND, []),
            (self.u, 'PostMessageW', w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
            (self.d, 'DwmGetWindowAttribute', c.c_long, [w.HWND, w.DWORD, c.c_void_p, w.DWORD]),
        )
        for library, name, result, arguments in functions:
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments

    @staticmethod
    def _ok(value):
        if not value:
            raise OSError('Native query/message unavailable')

    def process(self, handle, job):
        c, w = self.c, self.w
        if self.k.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT, not exit-code259 guessing.
            raise OSError('Owned process not live')
        pid = int(self.k.GetProcessId(handle))
        self._ok(pid)
        created, exited, kernel, user = (w.FILETIME() for _ in range(4))
        self._ok(self.k.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(kernel), c.byref(user)))
        member = w.BOOL()
        self._ok(self.k.IsProcessInJob(handle, job, c.byref(member)))
        self._ok(member.value)
        size, image = w.DWORD(32768), c.create_unicode_buffer(32768)
        self._ok(self.k.QueryFullProcessImageNameW(handle, 0, image, c.byref(size)))
        if not 0 < size.value < 32768:
            raise OSError('Native image path bound')
        if self.k.WaitForSingleObject(handle, 0) != 258:
            raise OSError('Owned process exited during query')
        return pid, (int(created.dwHighDateTime) << 32) | created.dwLowDateTime, image[:size.value]

    def interactive(self, browser, pid):
        """Same nonzero active console OR RDP session, WinSta0/Default actually receiving input.

        GetThreadDesktop/GetProcessWindowStation are borrowed, never closed. Only WTS allocates
        a tiny response: publish its pointer holder BEFORE query, and never retry an unknown free.
        """
        c, w = self.c, self.w
        own, remote = w.DWORD(), w.DWORD()
        self._ok(self.k.ProcessIdToSessionId(self.k.GetCurrentProcessId(), c.byref(own)))
        self._ok(self.k.ProcessIdToSessionId(pid, c.byref(remote)))
        if not own.value or own.value != remote.value:
            raise OSError('Not the same interactive session')
        previous = getattr(browser, '_windows_wts_query', None)
        if previous is not None and (not previous.complete or not previous.freed):
            raise OSError('WTS query/free ownership unknown; retained')
        query = _WTSQuery(c, w)
        browser._windows_wts_query = query
        query.issued = True
        try:
            success = self.wts.WTSQuerySessionInformationW(None, own.value, 8,
                c.byref(query.pointer), c.byref(query.bytes))  # WTSConnectState, local server only.
        except BaseException:
            query.uncertain = True
            raise
        if not success:
            if query.pointer.value:
                query.uncertain = True
            else:
                query.complete = query.freed = True  # Known no allocation, not unknown cleanup.
            raise OSError('WTS session query unavailable')
        if not query.pointer.value:
            query.uncertain = True
            raise OSError('WTS successful query lacks its owned output')
        # Only the fixed enum payload is read. Wrong-size replies are freed without dereference.
        state = None
        if query.bytes.value == c.sizeof(c.c_int32):
            state = c.cast(query.pointer, c.POINTER(c.c_int32))[0]
        query.free_attempted = True
        try:
            self.wts.WTSFreeMemory(query.pointer)
        except BaseException:
            query.uncertain = True
            raise
        query.freed = query.complete = True
        query.pointer.value = None
        if state != 0:  # WTSActive, not WTSConnected or a disconnected future desktop.
            raise OSError('Session not active or unexpected fixed reply')
        station = self.u.GetProcessWindowStation()
        desktop = self.u.GetThreadDesktop(self.k.GetCurrentThreadId())
        self._ok(station)
        self._ok(desktop)
        for handle, expected in ((station, 'winsta0'), (desktop, 'default')):
            text, needed = c.create_unicode_buffer(64), w.DWORD()
            self._ok(self.u.GetUserObjectInformationW(handle, 2, text, c.sizeof(text), c.byref(needed)))
            if not 0 < needed.value <= c.sizeof(text) or text.value.casefold() != expected:
                raise OSError('Not the ordinary interactive window station/desktop')
        receiving, needed = w.BOOL(), w.DWORD()
        self._ok(self.u.GetUserObjectInformationW(desktop, 6, c.byref(receiving),
                                                c.sizeof(receiving), c.byref(needed)))
        if not receiving.value or needed.value != c.sizeof(receiving):
            raise OSError('Current desktop is not receiving input')
        return int(own.value)

    def window_current(self, window, pid):
        owner = self.w.DWORD()
        self._ok(self.u.IsWindow(window))
        self._ok(self.u.GetWindowThreadProcessId(window, self.c.byref(owner)))
        if owner.value != pid or _handle(self.u.GetAncestor(window, 2)) != window:  # GA_ROOT
            raise OSError('Window owner changed')

    def windows(self, pid, *, visible, deadline):
        result, errors = [], []
        visited = 0
        @self.callback
        def visit(window, _):
            nonlocal visited
            try:
                visited += 1
                if visited > 4096 or time.monotonic() >= deadline:
                    raise OSError('Window enumeration bound')
                owner = self.w.DWORD()
                self._ok(self.u.GetWindowThreadProcessId(window, self.c.byref(owner)))
                if owner.value != pid:
                    return True
                raw = _handle(window)
                self.window_current(raw, pid)
                if visible:
                    if not self.u.IsWindowVisible(window) or self.u.IsIconic(window):
                        return True
                    cloaked = self.w.DWORD()
                    self._ok(self.d.DwmGetWindowAttribute(window, 14, self.c.byref(cloaked), self.c.sizeof(cloaked)) == 0)
                    if cloaked.value:
                        return True
                if len(result) == 64:
                    raise OSError('Owned window bound')
                result.append(raw)
                return True
            except BaseException as error:
                errors.append(error)
                return False
        succeeded = self.u.EnumWindows(visit, 0)
        if errors:
            raise errors[0]
        self._ok(succeeded)
        return tuple(result)

    def foreground_current(self, window, pid):
        self.window_current(window, pid)
        if _handle(self.u.GetForegroundWindow()) != window:
            raise OSError('Foreground not granted')

    def foreground(self, window, pid):
        self.window_current(window, pid)
        self._ok(self.u.SetForegroundWindow(window))
        self.foreground_current(window, pid)

    def post_close(self, window):
        self._ok(self.u.PostMessageW(window, 0x0010, 0, 0))  # WM_CLOSE, asynchronous request only.
