# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The operating system's power and attention calls a background job uses, through ctypes.

* ``KeepAwake`` holds the system awake while a job runs: on macOS an IOKit
  power assertion of type ``PreventUserIdleSystemSleep`` (the display may still
  sleep; ``pmset -g assertions`` lists it with its name), on Windows a power
  request of type ``PowerRequestSystemRequired`` (``powercfg /requests`` lists
  it with its reason). Neither stops a sleep the person asks for (closing a
  laptop's lid, the Sleep command); both end with the process, so a crash never
  leaves the machine awake. Elsewhere it does nothing and says so.
* ``flash_taskbar`` asks Windows to flash Angelo's taskbar button until its
  window comes to the front (``FlashWindowEx``), when it is not already there.

**Windows is off until verified on Windows** (``WINDOWS_VERIFIED``): both Windows
calls answer "unsupported" unless ``ANGELO_WINDOWS_JOB_DESKTOP=1`` is set, which
the verification steps in ``doc/angelo/long_jobs.rst`` ("Away from the
computer") use. Once a Windows build has passed them, ``WINDOWS_VERIFIED``
becomes True and the variable is no longer needed.

No call here blocks, prompts or raises: a failure is a returned status.
Native notifications (macOS Notification Center) are in ``_bpy._angelo_jobs``
(``bpy_angelo_desktop_apple.mm``); ``job_desktop`` decides when to use either.
"""

import ctypes
import os
import sys

WINDOWS_VERIFIED = False


def windows_enabled():
    from . import product_env
    return WINDOWS_VERIFIED or product_env.flag("WINDOWS_JOB_DESKTOP")


IOKIT_PATH = "/System/Library/Frameworks/IOKit.framework/IOKit"
CORE_FOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
UTF8 = 0x08000100                       # kCFStringEncodingUTF8
ASSERTION_ON = 255                      # kIOPMAssertionLevelOn
ASSERTION_TYPE = b"PreventUserIdleSystemSleep"

POWER_REQUEST_CONTEXT_VERSION, POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0, 0x1
POWER_REQUEST_SYSTEM_REQUIRED = 1
INVALID_HANDLE = ctypes.c_void_p(-1).value


class _Detailed(ctypes.Structure):
    _fields_ = [("module", ctypes.c_void_p), ("resource_id", ctypes.c_uint32), ("count", ctypes.c_uint32),
                ("strings", ctypes.c_void_p)]


class _Reason(ctypes.Union):
    _fields_ = [("detailed", _Detailed), ("simple", ctypes.c_wchar_p)]


class _ReasonContext(ctypes.Structure):   # REASON_CONTEXT (ULONG and DWORD are 32 bits on Windows)
    _fields_ = [("version", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("reason", _Reason)]


class _MacAssertion:
    def __init__(self):
        self.iokit = ctypes.CDLL(IOKIT_PATH)
        self.cf = ctypes.CDLL(CORE_FOUNDATION_PATH)
        self.cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self.cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
        self.iokit.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                           ctypes.POINTER(ctypes.c_uint32)]
        self.iokit.IOPMAssertionRelease.restype = ctypes.c_int32
        self.iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]

    def _string(self, value):
        return self.cf.CFStringCreateWithCString(None, value, UTF8)

    def hold(self, reason):
        kind, name = self._string(ASSERTION_TYPE), self._string(reason.encode("utf-8"))
        try:
            if not kind or not name:
                return None
            identity = ctypes.c_uint32(0)
            status = self.iokit.IOPMAssertionCreateWithName(kind, ASSERTION_ON, name, ctypes.byref(identity))
            return identity.value if status == 0 else None
        finally:
            for value in (kind, name):
                if value:
                    self.cf.CFRelease(value)

    def release(self, token):
        self.iokit.IOPMAssertionRelease(token)


class _WindowsRequest:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.PowerCreateRequest.restype = ctypes.c_void_p
        self.kernel.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
        self.kernel.PowerSetRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.kernel.PowerClearRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]

    def hold(self, reason):
        context = _ReasonContext(POWER_REQUEST_CONTEXT_VERSION, POWER_REQUEST_CONTEXT_SIMPLE_STRING)
        context.reason.simple = reason
        handle = self.kernel.PowerCreateRequest(ctypes.byref(context))
        if not handle or handle == INVALID_HANDLE:
            return None
        if not self.kernel.PowerSetRequest(handle, POWER_REQUEST_SYSTEM_REQUIRED):
            self.kernel.CloseHandle(handle)
            return None
        return handle

    def release(self, token):
        self.kernel.PowerClearRequest(token, POWER_REQUEST_SYSTEM_REQUIRED)
        self.kernel.CloseHandle(token)


def _backend(platform):
    if platform == "darwin":
        return _MacAssertion()
    if platform == "win32" and windows_enabled():
        return _WindowsRequest()
    return None


class KeepAwake:
    """One hold at a time: ``hold`` while a job runs, ``release`` when none does. Idempotent both ways."""

    def __init__(self, platform=sys.platform, backend=None):
        self.platform = platform
        self._backend = backend
        self._loaded = backend is not None
        self.token = None
        self.status = "idle"   # idle, held, unsupported or failed: what the last call did.

    def _get(self):
        if not self._loaded:
            self._loaded = True
            try:
                self._backend = _backend(self.platform)
            except (OSError, AttributeError):
                self._backend = None
        return self._backend

    @property
    def held(self):
        return self.token is not None

    def hold(self, reason):
        if self.token is not None:
            return True
        backend = self._get()
        if backend is None:
            self.status = "unsupported"
            return False
        try:
            self.token = backend.hold(reason)
        except (OSError, ValueError, ctypes.ArgumentError):
            self.token = None
        self.status = "held" if self.token is not None else "failed"
        return self.token is not None

    def release(self):
        if self.token is None:
            return
        token, self.token = self.token, None
        try:
            self._backend.release(token)
        except (OSError, ValueError, ctypes.ArgumentError):
            pass
        self.status = "idle"


FLASHW_ALL, FLASHW_TIMERNOFG = 0x3, 0xC


class _FlashInfo(ctypes.Structure):   # FLASHWINFO
    _fields_ = [("size", ctypes.c_uint), ("window", ctypes.c_void_p), ("flags", ctypes.c_uint),
                ("count", ctypes.c_uint), ("timeout", ctypes.c_uint)]


def _own_windows(user, process_id):
    """This process's visible top-level windows, in z-order."""
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def visit(window, _parameter):
        owner = ctypes.c_uint32(0)
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == process_id and user.IsWindowVisible(window):
            found.append(window)
        return 1
    user.EnumWindows(callback_type(visit), None)
    return found


def flash_taskbar(platform=sys.platform):
    """Flash Angelo's taskbar button until its window comes to the front. Returns a status word."""
    if platform != "win32" or not windows_enabled():
        return "unsupported"
    try:
        user = ctypes.WinDLL("user32")
        user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        user.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user.GetForegroundWindow.restype = ctypes.c_void_p   # A handle: the default int would truncate it.
        user.FlashWindowEx.argtypes = [ctypes.POINTER(_FlashInfo)]
        windows = _own_windows(user, os.getpid())
        if not windows:
            return "no_window"
        foreground = user.GetForegroundWindow()
        if foreground in windows:
            return "foreground"
        info = _FlashInfo(ctypes.sizeof(_FlashInfo), windows[0], FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
        user.FlashWindowEx(ctypes.byref(info))
        return "flashed"
    except (OSError, AttributeError, ValueError):
        return "failed"
