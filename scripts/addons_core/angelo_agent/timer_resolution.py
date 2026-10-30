# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows: Angelo asks for a 1 ms system timer while worker chats are hosted (``worker_mirror.EventRouter``).

Python threads hand the GIL to each other with a timed wait of the switch interval (5 ms). Windows rounds a timed
wait up to its timer tick, 15.6 ms unless a process asks for less, so while a worker chat's frames are read and
answered on other threads the lead's UI thread could wait a whole tick for the GIL (the 1.0.7 T4 pumps of 16 to 27 ms
against a 10 ms bound). ``timeBeginPeriod(1)`` makes those waits what they are elsewhere. A finer timer costs a
laptop's battery, so it is held only while a chat is hosted in a worker: asked for once when the first opens, given
back (``timeEndPeriod``) when the last closes (every end of a hosted chat closes its router entry: handed back,
its worker ended or crashed) and at Angelo's exit (``atexit``; Windows also ends a process's request with it).
Elsewhere nothing happens.
Standard library only and no ``bpy``.
"""

import atexit
import sys
import threading

PERIOD_MS = 1

_lock = threading.Lock()
_holders = set()
_winmm = None


def _api():
    global _winmm
    if _winmm is None:
        import ctypes
        _winmm = ctypes.WinDLL("winmm")
    return _winmm


def hold(owner, platform=None):
    """``owner`` (a worker) needs the fine timer; the first holder asks Windows for it."""
    if (platform or sys.platform) != "win32":
        return False
    with _lock:
        if owner in _holders:
            return False
        if not _holders and not _call("timeBeginPeriod"):
            return False
        _holders.add(owner)
        return True


def release(owner, platform=None):
    """``owner`` no longer needs it; the last holder gives it back."""
    if (platform or sys.platform) != "win32":
        return False
    with _lock:
        if owner not in _holders:
            return False
        _holders.discard(owner)
        if not _holders:
            _call("timeEndPeriod")
        return True


def held():
    with _lock:
        return len(_holders)


def _call(name):
    try:
        return getattr(_api(), name)(PERIOD_MS) == 0   # TIMERR_NOERROR
    except (OSError, AttributeError):
        return False


@atexit.register
def release_all():
    """Angelo's exit: whatever is still held is given back."""
    with _lock:
        if _holders:
            _holders.clear()
            _call("timeEndPeriod")
