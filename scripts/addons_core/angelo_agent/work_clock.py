# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The clock of cooperative budgets: the calling thread's own CPU work.

A cooperative budget that skips optional evidence (the turn context's and the
post-commit read-back's exact revisions) must be spent by the work it bounds,
never by waiting. Measured in wall time it was not: under machine load, or
while this app's snapshot worker held a CPU and the GIL, a send waited tens of
milliseconds after the active object's revision and omitted the revision of
the object its message named; the recipe that left that binding to inference
was refused ("has no revision in your latest observation"). Process CPU time
is no better, since it also counts that worker (``test_native_revision_budgets.py``).

``WorkClock`` reads the calling thread's CPU time: ``time.thread_time`` where
Python has it, else, on macOS (Blender's bundled Python lacks it), the same
per-thread clock through ``clock_gettime_nsec_np``. Without either it falls back
to the smaller of wall and process CPU time, each an upper bound of the
thread's work. Read a clock only on the thread that created it. Where thread
time advances in scheduler ticks (Windows, about 15.6 ms), a budget can run up
to one tick over.
"""

import sys
import time

# Darwin <time.h>: _CLOCK_THREAD_CPUTIME_ID.
DARWIN_CLOCK_THREAD_CPUTIME_ID = 16


def _darwin_thread_time():
    """macOS per-thread CPU seconds from libSystem, or None when the call is unavailable."""
    try:
        import ctypes
        reader = ctypes.CDLL("/usr/lib/libSystem.B.dylib").clock_gettime_nsec_np
    except (ImportError, OSError, AttributeError):
        return None
    reader.restype, reader.argtypes = ctypes.c_uint64, (ctypes.c_int,)
    if not reader(DARWIN_CLOCK_THREAD_CPUTIME_ID):  # 0 is its error value; a running thread has used CPU.
        return None
    return lambda: reader(DARWIN_CLOCK_THREAD_CPUTIME_ID) * 1e-9


def _thread_clock():
    clock = getattr(time, "thread_time", None)
    if clock is not None:
        return clock
    return _darwin_thread_time() if sys.platform == "darwin" else None


# Seconds of the calling thread's CPU time, or None on a platform with neither clock.
THREAD_CLOCK = _thread_clock()


class WorkClock:
    """Seconds of the calling thread's own work since creation (module docstring)."""

    def __init__(self, thread_clock=THREAD_CLOCK):
        self._thread_clock = thread_clock
        if thread_clock is not None:
            self._start = thread_clock()
        else:
            self._wall, self._cpu = time.perf_counter(), time.process_time()

    def __call__(self):
        if self._thread_clock is not None:
            return self._thread_clock() - self._start
        return min(time.perf_counter() - self._wall, time.process_time() - self._cpu)
