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
import struct
import subprocess
import threading
import time

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
IMAGE_SUBSYSTEM_WINDOWS_GUI = 2
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
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


class _WindowsJob:
    """A Job Object holding one process tree (kernel32 through ctypes)."""

    def __init__(self, process_handle, pid):
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
        self.kernel32, self.ctypes, self.handle, self.lock, self.closed = kernel32, ctypes, None, threading.Lock(), False
        job = kernel32.CreateJobObjectW(None, None)
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

    def close(self):
        with self.lock:
            self.closed = True
            if self.handle is not None:
                self.kernel32.CloseHandle(self.handle)
                self.handle = None


def _utf8(kwargs):
    if (kwargs.get("text") or kwargs.get("universal_newlines")) and not kwargs.get("encoding"):
        kwargs.update(encoding="utf-8", errors=kwargs.get("errors") or "replace")
    return kwargs


class ChildProcess(subprocess.Popen):
    """Windows only: started suspended in a Job Object, without a console window."""

    def __init__(self, args, keep_gui_programs=False, **kwargs):
        self._job, self._keep_gui = None, keep_gui_programs
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW | CREATE_SUSPENDED
        super().__init__(args, **kwargs)
        try:
            self._job = _WindowsJob(self._handle, self.pid)
        except BaseException:
            super().kill()
            super().wait()
            raise

    def _end_tree(self):
        if self._job is not None and self.returncode is None:
            self._job.terminate(self.pid, keep_gui=self._keep_gui)

    def terminate(self):
        self._end_tree()
        super().terminate()

    def kill(self):
        self._end_tree()
        super().kill()

    def poll(self):
        code = super().poll()
        if code is not None and self._job is not None:
            self._job.close()   # Survivors (a browser it opened) keep running.
        return code

    def wait(self, timeout=None):
        code = super().wait(timeout)
        if self._job is not None:
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
