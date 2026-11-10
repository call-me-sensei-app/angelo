# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""PDF helper-only OS memory admission. Never changes provider CLI process defaults.

The caller supplies a fixed, reviewed byte ceiling; this module never chooses or expands it. POSIX admission runs
inside the isolated worker before importing the parser or touching PDF bytes. It limits address space, not RSS.
Windows admission configures committed-memory limits on a suspended child's Job Object before resuming it.
Readback is a setup check, not proof that an OS/interpreter combination enforces allocations: supported combinations
and the product ceiling require separate bounded actual-OS allocation probes. Parents must also bound pipes/time.
"""
import errno
import os
import subprocess
import sys
import threading

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
PROCESS_MEMORY = 0x00000100
JOB_MEMORY = 0x00000200
KILL_ON_CLOSE = 0x00002000
FLAGS = PROCESS_MEMORY | JOB_MEMORY | KILL_ON_CLOSE


class ResourceLimitUnavailable(OSError):
    """The helper cannot establish its required OS protection; never parse unbounded instead."""


def _verify_address_space(effective, module):
    """A positive one-page mapping and denied over-ceiling mapping, without committing memory.

    Merely accepting/getting RLIMIT_AS is insufficient. No PDF bytes are touched by this probe. A platform that
    allows the oversized reservation is refused even if another memory mechanism might later restrict residency.
    """
    page = module.PAGESIZE
    if type(page) is not int or page <= 0 or effective > sys.maxsize - page:
        raise ResourceLimitUnavailable('PDF address-space probe has no bounded representable mapping')
    flags = module.MAP_PRIVATE | module.MAP_ANONYMOUS
    with module.mmap(-1, page, flags=flags, prot=0):
        pass
    try:
        mapping = module.mmap(-1, effective + page, flags=flags, prot=0)
    except OSError as error:
        if error.errno == errno.ENOMEM:
            return
        raise ResourceLimitUnavailable('PDF address-space denial could not be verified') from error
    else:
        mapping.close()
        raise ResourceLimitUnavailable('PDF address-space ceiling is not enforced')


def _budget(value):
    if type(value) is not int or not 0 < value < (1 << 63) - 1:
        raise ValueError('PDF memory ceiling must be positive finite bytes')
    return value


def admit_address_space(memory_bytes, *, _resource=None, _platform=None, _mmap=None):
    """Set equal finite hard/soft RLIMIT_AS in this PDF worker; refuse a tighter inherited limit unchanged.

    Return the effective byte ceiling. Failure must end this worker before parser import/file opening. No RSS
    polling, RLIMIT_RSS assertion, parent preexec_fn, or automatic baseline-plus-headroom fallback is used.
    """
    requested = _budget(memory_bytes)
    platform = sys.platform if _platform is None else _platform
    if platform != 'darwin' and not platform.startswith('linux'):
        raise ResourceLimitUnavailable('PDF address-space protection is unavailable on this platform')
    try:
        if _resource is None:
            import resource as _resource
        kind = _resource.RLIMIT_AS
        prior = _resource.getrlimit(kind)
        if any(value != _resource.RLIM_INFINITY and value < requested for value in prior):
            raise ValueError('Inherited memory ceiling is tighter than the fixed PDF policy')
        effective = requested
        _resource.setrlimit(kind, (effective, effective))
        if _resource.getrlimit(kind) != (effective, effective):
            raise OSError('PDF address-space ceiling could not be verified')
        if _mmap is None:
            import mmap as _mmap
        _verify_address_space(effective, _mmap)
        return effective
    except (ImportError, AttributeError, OSError, ValueError, OverflowError, MemoryError) as error:
        raise ResourceLimitUnavailable('PDF address-space protection could not be established') from error


class _WindowsAPI:
    """Small ctypes adapter; created only on Windows. Handles are non-inheritable."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        signatures = (
            ('GetCurrentProcess', w.HANDLE, []),
            ('CreateJobObjectW', w.HANDLE, [c.c_void_p, w.LPCWSTR]),
            ('SetInformationJobObject', w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]),
            ('QueryInformationJobObject', w.BOOL, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]),
            ('AssignProcessToJobObject', w.BOOL, [w.HANDLE, w.HANDLE]),
            ('IsProcessInJob', w.BOOL, [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]),
            ('TerminateJobObject', w.BOOL, [w.HANDLE, w.UINT]),
            ('CloseHandle', w.BOOL, [w.HANDLE]),
            ('CreateToolhelp32Snapshot', w.HANDLE, [w.DWORD, w.DWORD]),
            ('Thread32First', w.BOOL, [w.HANDLE, c.c_void_p]),
            ('Thread32Next', w.BOOL, [w.HANDLE, c.c_void_p]),
            ('OpenThread', w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
            ('ResumeThread', w.DWORD, [w.HANDLE]),
        )
        for name, restype, argtypes in signatures:
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = restype, argtypes

        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64), ('flags', w.DWORD),
                        ('min_working_set', c.c_size_t), ('max_working_set', c.c_size_t),
                        ('active_processes', w.DWORD), ('affinity', c.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops',
                                                      'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t),
                        ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
        self.Extended = Extended

    def _check(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())
        return result

    def current_process(self):
        return self.kernel.GetCurrentProcess()

    def create(self):
        return self._check(self.kernel.CreateJobObjectW(None, None))

    def configure(self, job, memory_bytes):
        info = self.Extended()
        info.basic.flags = FLAGS   # No BREAKAWAY_OK / SILENT_BREAKAWAY_OK.
        info.process_memory = info.job_memory = memory_bytes
        self._check(self.kernel.SetInformationJobObject(job, 9, self.c.byref(info), self.c.sizeof(info)))

    def assign(self, job, process):
        self._check(self.kernel.AssignProcessToJobObject(job, int(process)))

    def query(self, job):
        info = self.Extended()
        self._check(self.kernel.QueryInformationJobObject(job, 9, self.c.byref(info), self.c.sizeof(info), None))
        return info.basic.flags, info.process_memory, info.job_memory

    def contains(self, job, process):
        found = self.w.BOOL()
        self._check(self.kernel.IsProcessInJob(int(process), job, self.c.byref(found)))
        return bool(found.value)

    def resume(self, pid):
        c, k = self.c, self.kernel
        class Thread(c.Structure):
            _fields_ = [(name, self.w.DWORD) for name in ('size', 'usage', 'id', 'owner')]
            _fields_ += [('priority', self.w.LONG), ('delta', self.w.LONG), ('flags', self.w.DWORD)]
        snapshot = k.CreateToolhelp32Snapshot(0x4, 0)
        if not snapshot or snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        threads = []
        try:
            entry = Thread(size=c.sizeof(Thread))
            more = k.Thread32First(snapshot, c.byref(entry))
            while more:
                if entry.owner == pid:
                    threads.append(entry.id)
                more = k.Thread32Next(snapshot, c.byref(entry))
            # Created suspended: exactly one initial thread exists. Do not silently resume arbitrary threads.
            if len(threads) != 1:
                raise OSError('The PDF child initial thread could not be identified')
            thread = self._check(k.OpenThread(0x0002, False, threads[0]))
            try:
                previous = k.ResumeThread(thread)
                if previous != 1:
                    raise OSError('The PDF child was not suspended exactly once')
            finally:
                k.CloseHandle(thread)
        finally:
            k.CloseHandle(snapshot)

    def terminate(self, job):
        self._check(self.kernel.TerminateJobObject(job, 1))

    def close(self, job):
        self._check(self.kernel.CloseHandle(job))


class _LimitedJob:
    def __init__(self, api, process, pid, memory_bytes):
        self.api, self.handle, self.lock = api, None, threading.Lock()
        try:
            self.handle = api.create()
            api.configure(self.handle, _budget(memory_bytes))
            api.assign(self.handle, process)
            if api.query(self.handle) != (FLAGS, memory_bytes, memory_bytes):
                raise ResourceLimitUnavailable('PDF Job Object memory ceiling could not be verified')
            if not api.contains(self.handle, process):
                raise ResourceLimitUnavailable('PDF child is not in its limited Job Object')
            api.resume(pid)
        except BaseException as error:
            try:
                self.close()  # KILL_ON_CLOSE when assigned; caller also kills/reaps an unassigned child.
            except OSError as failure:
                error.add_note('PDF Job close failed; handle retained: ' + repr(failure))
            raise

    def terminate(self):
        with self.lock:
            if self.handle is not None:
                self.api.terminate(self.handle)

    def close(self):
        with self.lock:
            if self.handle is not None:
                self.api.close(self.handle)
                self.handle = None


class _PDFWindowsProcess(subprocess.Popen):
    def __init__(self, args, *, memory_bytes, **kwargs):
        _budget(memory_bytes)
        self._limited_job = None
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | CREATE_SUSPENDED | CREATE_NO_WINDOW
        super().__init__(args, **kwargs)
        try:
            api = _WindowsAPI()
            self._limited_job = _LimitedJob.__new__(_LimitedJob)
            self._limited_job.__init__(api, self._handle, self.pid, memory_bytes)
        except BaseException as error:
            try:
                self.close_owned_job()
            except OSError as failure:
                error.add_note('PDF Job close retry failed: ' + repr(failure))
            try:
                super().kill()
            except OSError:
                pass  # KILL_ON_CLOSE may already have ended an assigned child.
            super().wait(timeout=10)
            raise ResourceLimitUnavailable('The PDF child could not start with its required memory ceiling') from error

    def close_owned_job(self):
        """Close the owned Job handle; failed closes retain ownership for an explicit retry."""
        if self._limited_job is not None:
            self._limited_job.close()

    def kill(self):
        error = None

        def remember(failure):
            nonlocal error
            if error is None:
                error = failure
            else:
                error.add_note('Additional PDF cleanup failure: ' + repr(failure))

        try:
            if self.returncode is None:
                if self._limited_job is not None:
                    try:
                        self._limited_job.terminate()
                    except OSError as failure:
                        if super().poll() is None:
                            remember(failure)
                # Independently attempt process termination even if Job termination failed.
                try:
                    super().kill()
                except OSError as failure:
                    if super().poll() is None:
                        remember(failure)
        finally:
            try:
                self.close_owned_job()  # KILL_ON_CLOSE also covers descendants and termination failure.
            except OSError as failure:
                remember(failure)
        if error is not None:
            try:
                super().wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired) as failure:
                remember(failure)
            raise error

    def terminate(self):
        self.kill()

    def poll(self):
        code = super().poll()
        if code is not None and self._limited_job is not None:
            self._limited_job.close()
        return code

    def wait(self, timeout=None):
        code = super().wait(timeout=timeout)
        if self._limited_job is not None:
            self._limited_job.close()
        return code


def admit_windows(memory_bytes, *, _api=None):
    """Worker-side proof of actual current-job memory admission; no argv/environment marker supplies it."""
    requested = _budget(memory_bytes)
    if _api is None and os.name != 'nt':
        raise ResourceLimitUnavailable('PDF Job Object protection is Windows-only')
    try:
        api = _WindowsAPI() if _api is None else _api
        if not api.contains(None, api.current_process()):
            raise ResourceLimitUnavailable('PDF worker is not in a memory-limited Job Object')
        if api.query(None) != (FLAGS, requested, requested):
            raise ResourceLimitUnavailable('PDF worker current-job memory ceiling could not be verified')
        return requested
    except (OSError, AttributeError, ValueError, OverflowError) as error:
        raise ResourceLimitUnavailable('PDF worker memory protection could not be established') from error


def popen_windows(args, *, memory_bytes, **kwargs):
    """PDF-only strict Windows launcher. Parent must kill/reap on timeout and bound stdout/stderr."""
    if os.name != 'nt':
        raise ResourceLimitUnavailable('PDF Job Object protection is Windows-only')
    return _PDFWindowsProcess(args, memory_bytes=memory_bytes, **kwargs)
