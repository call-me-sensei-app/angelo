# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Put a file's access and modification times back through its descriptor, on every host.

The capture tests change a file's bytes and then restore its times, to prove the
change witness still sees the change. POSIX does that with ``os.utime(fd, ...)``;
Windows' ``os.utime`` takes no descriptor, so there the times go through the
descriptor's handle with ``SetFileTime``.
"""

import os

_EPOCH_AS_FILETIME = 116444736000000000  # 1970-01-01 in 100-nanosecond steps since 1601-01-01.


def set_times(fd, ns):
    """``os.utime(fd, ns=ns)`` that also works on Windows."""
    if os.utime in os.supports_fd:
        os.utime(fd, ns=ns)
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    def filetime(value):
        ticks = value // 100 + _EPOCH_AS_FILETIME
        return wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetFileTime.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME))
    kernel32.SetFileTime.restype = wintypes.BOOL
    access, modified = filetime(ns[0]), filetime(ns[1])
    if not kernel32.SetFileTime(msvcrt.get_osfhandle(fd), None, ctypes.byref(access), ctypes.byref(modified)):
        raise ctypes.WinError(ctypes.get_last_error())
