# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded pipes and lifetime for the isolated PDF worker, never provider CLI defaults."""
import os
import select
import subprocess
import sys
import time

from . import pdf_darwin_watchdog, pdf_process_limits


class OutputLimitExceeded(ValueError):
    """The PDF worker exceeded its finite answer protocol."""


def _chunk(pipe, maximum, remaining):
    if os.name != "nt":
        if not select.select([pipe], [], [], min(remaining, pdf_darwin_watchdog.INTERVAL))[0]:
            return None
        return os.read(pipe.fileno(), min(65536, maximum))
    # Windows select cannot wait on anonymous pipes. Peek before each bounded read, without a reader thread
    # that could survive a timeout. The worker's Job Object owns any pipe-writing descendants as well.
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    peek = kernel.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    peek.restype = wintypes.BOOL
    available = wintypes.DWORD()
    if not peek(msvcrt.get_osfhandle(pipe.fileno()), None, 0, None, ctypes.byref(available), None):
        error = ctypes.get_last_error()
        if error == 109:   # ERROR_BROKEN_PIPE: all writers closed.
            return b""
        raise ctypes.WinError(error)
    if available.value:
        return os.read(pipe.fileno(), min(65536, maximum, available.value))
    time.sleep(min(remaining, 0.01))
    return None


def _cleanup(process):
    """Always release the PDF-only Job Object and attempt bounded reap, preserving any cleanup failure."""
    failures = []
    try:
        try:
            if process.poll() is None:
                process.kill()
        except OSError as error:
            failures.append(error)
        finally:
            close_job = getattr(process, "close_owned_job", None)
            if close_job is not None:
                try:
                    close_job()   # KILL_ON_CLOSE applies even when TerminateJobObject/TerminateProcess failed.
                except OSError as error:
                    failures.append(error)
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as error:
            failures.append(error)
    finally:
        for pipe in (getattr(process, "stdin", None), process.stdout):
            if pipe is not None and not pipe.closed:
                try:
                    pipe.close()
                except OSError as error:
                    failures.append(error)
    if failures:
        for later in failures[1:]:
            failures[0].add_note("PDF cleanup also failed: " + repr(later))
        raise failures[0]


def run(args, *, memory_bytes, output_bytes, timeout, env):
    """Read at most output_bytes+1, discard stderr, and kill/reap the owned helper on every failed read.

    Linux hard AS admission occurs in the worker, Windows Job admission while suspended. Darwin instead uses
    an explicit sampled physical-footprint termination threshold with a pre-parser parent handshake; it is NOT a
    hard allocation cap. Every route preserves bounded pipes/deadline and owned cleanup.
    """
    if type(output_bytes) is not int or output_bytes <= 0 or timeout <= 0:
        raise ValueError("PDF output/deadline bounds must be positive")
    darwin = sys.platform == "darwin"
    kwargs = dict(stdin=subprocess.PIPE if darwin else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                  env=env, bufsize=0)
    process = (pdf_process_limits.popen_windows(args, memory_bytes=memory_bytes, **kwargs)
               if os.name == "nt" else subprocess.Popen(args, **kwargs))
    deadline, output = time.monotonic() + timeout, bytearray()
    try:
        watchdog = pdf_darwin_watchdog.Watchdog(process, memory_bytes) if darwin else None
        if watchdog is not None:
            watchdog.grant()
        while True:
            if watchdog is not None:
                watchdog.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            chunk = _chunk(process.stdout, output_bytes + 1 - len(output), remaining)
            if chunk is None:
                continue
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > output_bytes:
                raise OutputLimitExceeded("The PDF worker answer exceeded its protocol limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, timeout)
        if watchdog is None:
            code = process.wait(timeout=remaining)
        else:
            while True:   # EOF alone is not process completion: keep sampling until the owned child exits.
                watchdog.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    code = process.wait(timeout=min(remaining, pdf_darwin_watchdog.INTERVAL))
                    break
                except subprocess.TimeoutExpired:
                    continue
        return subprocess.CompletedProcess(args, code, bytes(output))
    finally:
        _cleanup(process)
