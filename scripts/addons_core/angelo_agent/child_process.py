# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Child processes for provider CLIs and helper tools: UTF-8 text, no console window, whole-tree kill.

``popen`` and ``run`` differ from ``subprocess`` in three ways:

* Text pipes are UTF-8 (undecodable bytes replaced) unless a caller names an
  encoding. Windows would otherwise decode with the ANSI code page, which turns
  a CLI's UTF-8 into mojibake or a UnicodeDecodeError in a reader thread.
* On Windows the child gets ``CREATE_NO_WINDOW``: a console program (node.exe,
  codex.exe, git.exe) started from the GUI app would open a console window, and
  its own children share the hidden console instead of opening theirs.
* On Windows the child is started suspended, put in a Job Object, then resumed,
  so every descendant (npm's ``node codex.js`` starts ``codex.exe``) is in the
  job from its first instruction. ``terminate()`` and ``kill()`` end the whole
  job, as killing a POSIX process group would. A normal exit kills nothing:
  a browser a CLI opened for sign-in stays open. If Windows refuses the job
  (a parent job that forbids nesting), ``taskkill /T`` ends the tree instead.
* ``keep_gui_programs=True`` (sign-in commands): a browser the CLI opens through
  ShellExecute joins the job too, and must never be closed with the person's
  other tabs. Ending such a child ends only the job's console programs (the CLI,
  ``node``, ``codex.exe``, ``cmd``, ``powershell``: every one of its own helpers)
  and leaves its windowed programs running; without a job it ends only the
  child itself. A process whose program cannot be read counts as console.

Elsewhere they call ``subprocess`` itself (only the UTF-8 default differs).
"""

import os
import math
import struct
import subprocess
import threading
import time

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
IMAGE_SUBSYSTEM_WINDOWS_GUI = 2
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
PROCESS_TERMINATE, PROCESS_QUERY_LIMITED_INFORMATION = 0x0001, 0x1000


def pe_subsystem(path):
    """The Subsystem field of a Windows program's PE header (2 windowed, 3 console), or None if unreadable."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return None
            offset = struct.unpack_from("<I", header, 0x3C)[0]
            handle.seek(offset)
            pe = handle.read(24 + 70)   # "PE\0\0", the COFF header, then the optional header up to Subsystem.
    except OSError:
        return None
    if len(pe) < 94 or pe[:4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", pe, 24 + 68)[0]   # Same offset in PE32 and PE32+.


def console_members(pids, program_of):
    """The processes to end when a sign-in child ends: all but windowed (GUI-subsystem) programs."""
    return [pid for pid in pids if pe_subsystem(program_of(pid) or "") != IMAGE_SUBSYSTEM_WINDOWS_GUI]


class ChildCleanupUnavailable(RuntimeError):
    """Retain exact owner; unknown native-close outcomes permanently forbid handle reuse.

    Known pending exit/member state may be observed again with the same owner.
    The original operation remains __cause__; no passive-to-force fallback is implied.
    """
    def __init__(self, owner, cleanup_error):
        super().__init__("Owned Windows child cleanup is unproved")
        self.owner, self.cleanup_error = owner, cleanup_error


def _deadline(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("A finite monotonic deadline is required")
    return value


class _WindowsJob:
    """A Job Object holding one process tree (kernel32 through ctypes)."""

    def __init__(self, process_handle, pid, *, required=False):
        self.handle, self.lock, self.closed = None, threading.Lock(), False
        self.required = required
        self._close_attempted = False
        self.close_unknown = False
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, restype, argtypes in (
                ("CreateJobObjectW", wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
                ("AssignProcessToJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
                ("TerminateJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
                ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]),
                ("CreateToolhelp32Snapshot", wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
                ("Thread32First", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_void_p]),
                ("Thread32Next", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_void_p]),
                ("OpenThread", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
                ("ResumeThread", wintypes.DWORD, [wintypes.HANDLE]),
                ("QueryInformationJobObject", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                              wintypes.DWORD, ctypes.c_void_p]),
                ("OpenProcess", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
                ("QueryFullProcessImageNameW", wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                               ctypes.POINTER(wintypes.DWORD)]),
                ("TerminateProcess", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT])):
            function = getattr(kernel32, name)
            function.restype, function.argtypes = restype, argtypes
        # A reader thread's wait() may close the job while the UI thread terminates it: never use a closed handle.
        self.kernel32, self.ctypes = kernel32, ctypes
        job = kernel32.CreateJobObjectW(None, None)
        if required:
            self.handle = job or None  # Retain even an unassigned job if setup fails.
            if not job or not kernel32.AssignProcessToJobObject(job, int(process_handle)):
                raise OSError("The required process job could not be attached")
            self._resume(pid)
            return
        if job and kernel32.AssignProcessToJobObject(job, int(process_handle)):
            self.handle = job
        elif job:
            kernel32.CloseHandle(job)
        self._resume(pid)

    def _resume(self, pid):
        """Resume the suspended main thread (Toolhelp: the documented way to find it)."""
        ctypes, kernel32 = self.ctypes, self.kernel32

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulong) for name in ("dwSize", "cntUsage", "th32ThreadID", "th32OwnerProcessID",
                                                           "tpBasePri", "tpDeltaPri", "dwFlags")]

        snapshot = kernel32.CreateToolhelp32Snapshot(0x4, 0)   # TH32CS_SNAPTHREAD
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        resumed = 0
        try:
            entry = THREADENTRY32(dwSize=ctypes.sizeof(THREADENTRY32))
            more = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == pid:
                    thread = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)   # THREAD_SUSPEND_RESUME
                    if thread:
                        if kernel32.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                        kernel32.CloseHandle(thread)
                more = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        if not resumed:
            raise OSError("The child process could not be started (its main thread was not found)")

    def terminate(self, pid, keep_gui=False):
        with self.lock:
            self._require_known_locked()
            if self.handle is not None:
                if keep_gui:
                    self._end_console_members()
                else:
                    self.kernel32.TerminateJobObject(self.handle, 1)
                return
            if self.closed or keep_gui:   # keep_gui without a job: Popen ends the child itself, nothing more.
                return
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                       timeout=10, check=False)

    def _member_pids(self):
        ctypes = self.ctypes

        class PROCESS_ID_LIST(ctypes.Structure):   # JOBOBJECT_BASIC_PROCESS_ID_LIST with room for 1024 ids
            _fields_ = [("assigned", ctypes.c_ulong), ("listed", ctypes.c_ulong), ("ids", ctypes.c_size_t * 1024)]

        found = PROCESS_ID_LIST()
        if not self.kernel32.QueryInformationJobObject(self.handle, JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                                                       ctypes.byref(found), ctypes.sizeof(found), None):
            return []
        return list(found.ids[:found.listed])

    def _program(self, pid):
        from ctypes import wintypes
        process = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return None
        try:
            buffer, size = self.ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            ok = self.kernel32.QueryFullProcessImageNameW(process, 0, buffer, self.ctypes.byref(size))
            return buffer.value if ok else None
        finally:
            self.kernel32.CloseHandle(process)

    def _end_console_members(self):
        """End every console program in the job, again until none is left (one may start another meanwhile)."""
        for _ in range(20):
            pids = console_members(self._member_pids(), self._program)
            if not pids:
                return
            for pid in pids:
                process = self.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if process:
                    self.kernel32.TerminateProcess(process, 1)
                    self.kernel32.CloseHandle(process)
            time.sleep(0.05)

    def _require_known_locked(self):
        # Once native CloseHandle was attempted without a known success, its numeric
        # value may be closed/reused. No query, signal or close may use it again.
        if (getattr(self, "required", False) and
                (getattr(self, "close_unknown", False) or
                 (getattr(self, "_close_attempted", False) and not self.closed))):
            raise OSError("The retained process job close outcome is unknown")

    def _active_count_locked(self):
        """Checked BASIC_ACCOUNTING_INFORMATION, Windows LARGE_INTEGER/DWORD widths."""
        self._require_known_locked()
        ctypes = self.ctypes
        class ACCOUNTING(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ("user", "kernel", "period_user", "period_kernel")] + [
                        (name, ctypes.c_uint32) for name in
                        ("faults", "total", "active", "terminated")]
        if self.handle is None or ctypes.sizeof(ACCOUNTING) != 48:
            raise OSError("The retained process job is unavailable")
        found = ACCOUNTING()
        if not self.kernel32.QueryInformationJobObject(self.handle, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(found),
                                                       ctypes.sizeof(found), None):
            raise OSError("The retained process job could not be inspected")
        return found.active

    def active_count(self):
        with self.lock:
            return self._active_count_locked()

    def terminate_owned(self):
        """No PID fallback, including when the leader has already exited."""
        with self.lock:
            self._require_known_locked()
            if self.handle is None or not self.kernel32.TerminateJobObject(self.handle, 1):
                raise OSError("The retained process job could not be terminated")

    def wait_empty(self, deadline):
        deadline = _deadline(deadline)
        while time.monotonic() < deadline:
            if self.active_count() == 0:
                return
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        raise subprocess.TimeoutExpired("owned Windows job", 0)

    def close(self):
        with self.lock:
            if getattr(self, "required", False):
                if self.closed:
                    return
                if self._active_count_locked() != 0:
                    raise OSError("The retained process job still has members")
                self._require_known_locked()
                self._close_attempted = True
                self.close_unknown = True  # Publish uncertainty BEFORE the sole native attempt.
                if not self.kernel32.CloseHandle(self.handle):
                    raise OSError("The retained process job handle could not be closed")
                self.handle, self.closed, self.close_unknown = None, True, False
                return
            self.closed = True
            if self.handle is not None:
                self.kernel32.CloseHandle(self.handle)
                self.handle = None


def _utf8(kwargs):
    if (kwargs.get("text") or kwargs.get("universal_newlines")) and not kwargs.get("encoding"):
        kwargs.update(encoding="utf-8", errors=kwargs.get("errors") or "replace")
    return kwargs


class ChildProcess(subprocess.Popen):
    """Windows child, suspended until Job Object setup, without a console window.

    retain_job=True is an explicit owner API: requires attachment, retains the job
    after leader exit, and requires explicit drain_job(deadline) or the signal-free
    finish_job_passive(deadline) normal-retention proof. Defaults preserve CLI/browser
    sign-in behavior. Strict startup failure cleanup is bounded to five seconds; a
    ChildCleanupUnavailable retains the partial process owner for retry.
    """

    def __init__(self, args, keep_gui_programs=False, *, retain_job=False, **kwargs):
        if type(retain_job) is not bool or (retain_job and keep_gui_programs):
            raise ValueError("A retained job cannot preserve unrelated GUI programs")
        self._retain_job = retain_job
        self._job_lifecycle_lock = threading.RLock()
        self._job_force_attempted = False
        self._passive_drained_job = None
        self._job_ready = False
        self._job, self._keep_gui = None, keep_gui_programs
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW | CREATE_SUSPENDED
        super().__init__(args, **kwargs)
        try:
            if retain_job:
                # Publish the partial owner before attachment/resume can fail.
                self._job = _WindowsJob.__new__(_WindowsJob)
                self._job.__init__(self._handle, self.pid, required=True)
            else:
                self._job = _WindowsJob(self._handle, self.pid)
            self._job_ready = True
        except BaseException as primary:
            if retain_job:
                try:
                    self._failed_start_cleanup(time.monotonic() + 5)
                except BaseException as cleanup:
                    raise ChildCleanupUnavailable(self, cleanup) from primary
            else:
                super().kill()
                super().wait()
            raise

    def _finish_job_drain(self, deadline, *, kill_leader=False):
        with self._job_lifecycle_lock:
            self._job_force_attempted = True  # Before any termination/partial-start cleanup.
            return self._finish_job_drain_locked(deadline, kill_leader=kill_leader)

    def _finish_job_drain_locked(self, deadline, *, kill_leader=False):
        failure, reaped, empty = None, False, False
        job = self._job
        if job is not None and getattr(job, "handle", None) is not None:
            try:
                job.terminate_owned()
            except OSError as error:
                failure = error
        if kill_leader:
            try:
                super().kill()  # Partial attachment may have left this own child outside the job.
            except OSError as error:
                failure = failure or error
        try:
            super().wait(timeout=max(0, deadline - time.monotonic()))
            reaped = True
        except (OSError, subprocess.TimeoutExpired) as error:
            failure = failure or error
        if job is not None and getattr(job, "handle", None) is not None:
            try:
                job.wait_empty(deadline)
                empty = True
            except (OSError, subprocess.TimeoutExpired) as error:
                failure = failure or error
            if reaped and empty:
                try:
                    job.close()
                except OSError as error:
                    failure = failure or error
        if failure is not None:
            raise failure

    def _failed_start_cleanup(self, deadline):
        self._finish_job_drain(deadline, kill_leader=True)

    def drain_job(self, deadline):
        """Opt-in owner: terminate, prove all members gone and reap leader before releasing job.

        Failure retains this object/job for retry. A leader exit alone is never drain proof.
        No native Windows support, profile ACL or browser capability is implied.
        """
        deadline = _deadline(deadline)
        if not self._retain_job or self._job is None:
            raise ValueError("No retained job was requested")
        with self._job_lifecycle_lock:
            self._job_force_attempted = True  # A force/default cancellation can never earn retention.
        try:
            if self._job.closed:
                return
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, 0)
            if not self._job_ready:
                self._failed_start_cleanup(deadline)
                return
            self._finish_job_drain(deadline)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ChildCleanupUnavailable(self, error) from error

    def finish_job_passive(self, deadline):
        """Normal-retention owner: zero exit, whole Job empty and one known close.

        Uses this existing child/Job only. Never signals, kills, reopens a PID or
        falls back to force drain. Any uncertainty retains ChildCleanupUnavailable.owner.
        Only this method's own completed proof is idempotent; job.closed alone is not.
        """
        deadline = _deadline(deadline)
        if not self._retain_job or self._job is None:
            raise ValueError("No retained job was requested")
        with self._job_lifecycle_lock:
            job = self._job
            try:
                if self._job_force_attempted or not self._job_ready:
                    raise OSError("This child cannot prove a passive normal exit")
                if self._passive_drained_job is job:
                    return
                if job.closed or getattr(job, "close_unknown", False):
                    raise OSError("No passive completion proof exists for this Job")
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args, 0)
                code = super().wait(timeout=max(0, deadline - time.monotonic()))
                if type(code) is not int or code != 0:
                    raise OSError("The owned child did not exit normally with code zero")
                job.wait_empty(deadline)  # Checked whole-Job accounting, not leader exit alone.
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args, 0)
                job.close()  # Same Job checks empty again and latches native-close uncertainty.
                if not job.closed or job.handle is not None or getattr(job, "close_unknown", False):
                    raise OSError("The retained process Job close is not known")
                self._passive_drained_job = job  # Set only after all native proofs above.
            except BaseException as error:
                raise ChildCleanupUnavailable(self, error) from error

    def _end_tree(self):
        with self._job_lifecycle_lock:
            if self._retain_job:
                self._job_force_attempted = True
            return self._end_tree_locked()

    def _end_tree_locked(self):
        if self._retain_job:
            if self._job is None:
                raise OSError("The retained process job is unavailable")
            self._job.terminate_owned()
        elif self._job is not None and self.returncode is None:
            self._job.terminate(self.pid, keep_gui=self._keep_gui)

    def terminate(self):
        self._end_tree()
        super().terminate()

    def kill(self):
        self._end_tree()
        super().kill()

    def poll(self):
        code = super().poll()
        if code is not None and self._job is not None and not self._retain_job:
            self._job.close()   # Survivors (a browser it opened) keep running.
        return code

    def wait(self, timeout=None):
        code = super().wait(timeout)
        if self._job is not None and not self._retain_job:
            self._job.close()
        return code


def popen(args, *, keep_gui_programs=False, **kwargs):
    """A ``ChildProcess`` on Windows; elsewhere ``subprocess.Popen`` (looked up per call) with UTF-8 text.

    ``keep_gui_programs``: ending the child never ends a windowed program it started (a sign-in's browser).
    POSIX ``terminate()`` ends only the child itself already.
    """
    if os.name != "nt":
        return subprocess.Popen(args, **_utf8(kwargs))
    return ChildProcess(args, keep_gui_programs=keep_gui_programs, **_utf8(kwargs))


def run(args, **kwargs):
    """``subprocess.run`` with UTF-8 text; on Windows on a ``ChildProcess`` whose timeout ends the whole tree."""
    if os.name != "nt":
        return subprocess.run(args, **_utf8(kwargs))
    return _run_windows(args, **kwargs)


def _run_windows(args, *, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with ChildProcess(args, **_utf8(kwargs)) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        except BaseException:
            process.kill()
            raise
    completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    if check:
        completed.check_returncode()
    return completed
