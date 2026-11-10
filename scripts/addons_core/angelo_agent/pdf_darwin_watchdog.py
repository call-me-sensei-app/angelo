# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""PDF-only Darwin sampled physical-footprint policy, NOT a hard allocation ceiling.

The owned unreaped child is identified by PID and proc start time. Before parsing it waits for the parent's
admission token; the parent samples before each bounded pipe read and while waiting for exit. Scheduling and
sampling permit overshoot, including a peak entirely between samples. No RSS/AS or kernel hard-cap claim is made.
ABI: Apple's public libproc.h proc_pid_rusage and sys/resource.h rusage_info_v0 (macOS 10.9+).
"""
import os
import sys

INTERVAL = 0.05  # Same maximum pipe wait: a polling target, never an OS scheduling guarantee.
TOKEN = b"angelo-pdf-footprint-v1:"
MAX_TOKEN = 128


class AccountingUnavailable(OSError):
    """The owned PDF child's physical-footprint accounting cannot be established or maintained."""


class FootprintExceeded(AccountingUnavailable):
    """A sampled physical footprint reached the finite termination threshold; peak overshoot is possible."""


def _budget(value):
    if type(value) is not int or not 0 < value < (1 << 63) - 1:
        raise ValueError("PDF footprint threshold must be positive finite bytes")
    return value


class Accounting:
    """Lazy Darwin-only adapter. Never invokes ps, a shell, private APIs, or another process."""
    def __init__(self):
        if sys.platform != "darwin":
            raise AccountingUnavailable("PDF physical-footprint accounting is Darwin-only")
        import ctypes as c
        class Usage(c.Structure):
            _fields_ = [("uuid", c.c_uint8 * 16)] + [(name, c.c_uint64) for name in (
                "user", "system", "idle", "interrupts", "pageins", "wired", "resident", "footprint", "start", "exit")]
        try:
            function = c.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_pid_rusage
            function.argtypes, function.restype = [c.c_int, c.c_int, c.c_void_p], c.c_int
        except (OSError, AttributeError) as error:
            raise AccountingUnavailable("PDF physical-footprint API is unavailable") from error
        self.c, self.Usage, self.function = c, Usage, function

    def sample(self, pid):
        usage = self.Usage()
        if self.function(pid, 0, self.c.byref(usage)) != 0:   # RUSAGE_INFO_V0; buffer is the struct, not a pointer slot.
            raise AccountingUnavailable(self.c.get_errno(), "PDF child accounting failed")
        return usage.start, usage.footprint, usage.exit


def _within_threshold(footprint, threshold):
    if type(footprint) is not int or footprint < 0:
        raise AccountingUnavailable("PDF child has no valid physical-footprint accounting")
    if footprint >= threshold:
        raise FootprintExceeded("The PDF child's sampled physical footprint reached its termination threshold")


def _checked(sample, threshold):
    start, footprint, exited = sample
    _within_threshold(footprint, threshold)
    if type(start) is not int or start <= 0 or type(footprint) is not int or footprint <= 0 or exited:
        raise AccountingUnavailable("PDF child has no live, valid physical-footprint identity")
    return start


class Watchdog:
    """One actual Popen child only. Do not reap it elsewhere while this owner reads its answer."""
    def __init__(self, process, threshold, *, accounting=None):
        self.process, self.threshold = process, _budget(threshold)
        self.pid = process.pid
        if type(self.pid) is not int or self.pid <= 0 or process.returncode is not None:
            raise AccountingUnavailable("PDF watchdog requires its live owned child")
        self.accounting = Accounting() if accounting is None else accounting
        self.start = _checked(self.accounting.sample(self.pid), self.threshold)
        self.granted = False

    def grant(self):
        """Only after another identity/accounting check, release the child's pre-parser stdin barrier once."""
        if self.granted:
            raise AccountingUnavailable("PDF parser admission was already granted")
        if not self.check():
            raise AccountingUnavailable("PDF child ended before parser admission")
        token = TOKEN + ("%d:%d:%d\n" % (self.pid, self.start, self.threshold)).encode("ascii")
        try:
            if len(token) > MAX_TOKEN or self.process.stdin.write(token) != len(token):
                raise AccountingUnavailable("PDF parser admission could not be delivered")
            self.process.stdin.close()
        except OSError as error:
            raise AccountingUnavailable("PDF parser admission pipe failed") from error
        self.granted = True

    def check(self):
        """True while live; accounting failure is tolerated only after this exact child is confirmed exited.

        poll() is used only on an accounting failure and then ends monitoring if it reaps this owned child. An
        unreaped direct child cannot have its PID recycled. A start-time mismatch always refuses, never signals a
        sampled replacement PID; cleanup remains the Popen owner's responsibility.
        """
        if self.process.pid != self.pid:
            raise AccountingUnavailable("PDF child identity changed")
        if self.process.returncode is not None:
            return False
        try:
            sample = self.accounting.sample(self.pid)
        except OSError:
            if self.process.poll() is not None:
                return False   # Normal exit can remove live accounting between pipe iterations.
            raise
        start, footprint, exited = sample
        if start != self.start:
            raise AccountingUnavailable("PDF child start identity changed")
        _within_threshold(footprint, self.threshold)   # An observed crossing refuses even in a final exit sample.
        if exited:
            if self.process.poll() is not None:
                return False
            raise AccountingUnavailable("PDF child exit accounting conflicts with its owner")
        _checked((start, footprint, exited), self.threshold)
        return True


def await_parent(threshold, *, source=None, accounting=None):
    """Worker barrier before ANY PDF access/parser import; EOF, malformed token or wrong self identity refuses.

    The pipe is created and written by the same parent that owns sampling/deadline/cleanup. This token is an
    ownership handshake, not a claim that the worker can cryptographically prove its parent continues sampling.
    The parent's existing hard deadline also covers a child blocked here.
    """
    threshold = _budget(threshold)
    source = sys.stdin.buffer if source is None else source
    token = source.readline(MAX_TOKEN + 1)
    if len(token) > MAX_TOKEN or not token.startswith(TOKEN) or not token.endswith(b"\n"):
        raise AccountingUnavailable("No valid PDF parent admission token")
    try:
        fields = token[len(TOKEN):-1].split(b":")
        if len(fields) != 3 or any(not field.isdigit() for field in fields):
            raise ValueError("Invalid PDF admission fields")
        pid, start, promised = map(int, fields)
    except ValueError as error:
        raise AccountingUnavailable("Invalid PDF parent admission token") from error
    if pid != os.getpid() or promised != threshold:
        raise AccountingUnavailable("PDF parent admission belongs to another child or policy")
    api = Accounting() if accounting is None else accounting
    if _checked(api.sample(pid), threshold) != start:
        raise AccountingUnavailable("PDF parent admission has a stale process identity")
