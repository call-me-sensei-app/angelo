"""Wall-clock bounds in host tests, relative to the machine they run on.

A fixed bound on elapsed time says as much about the machine as about the code: on the same tree, a 5 ms bound measured
18.6 ms on a loaded Mac and passed on a quiet one. Every bound in a test_native_* file goes through this module (a test
in test_native_machine_clock.py keeps it so), in one of two shapes:

``start(test)`` and ``assert_within``: "this returned at once, it did not wait". The test keeps its fixed limit while
the machine is well; the limit only grows for what the machine itself took away, measured two ways:

* a bystander thread that only sleeps: each late wake-up is time the machine stole from the process during the window,
  and is added to the limit. Code that truly waits (on a lock, a wedged worker, a timeout) leaves the bystander on time,
  so a real wait still fails on a loaded machine;
* a reference pass, run only after a miss: when it runs slower than ``NOMINAL_REFERENCE_SECONDS``, the limit grows by
  that factor (never shrinks: most of these limits cover real waits, which a fast machine does not shorten).

``assert_cost``: "this work stays this cheap". The work is timed beside a reference pass in the same process, and the
budget, stated in seconds on the nominal machine, scales with that pass both ways: tighter on a fast machine, looser on
a slow one. A round pairs a reference with the work; up to three rounds, so one stall in either half is not a failure,
and a round far past the bound ends at once. The work must be safe to run again.

The same idea as the script kit's sweep bound in test_blender_script_kit_reviews.py, where the reference is a pass over
the same scene. These tests have no common scene, so they share one reference pass.
"""

import json
import os
import threading
import time

# Bound at import: a test may patch these names on the time and threading modules while a window is open.
_clock = time.perf_counter
_sleep = time.sleep
_Thread = threading.Thread
_Event = threading.Event

# What reference_pass takes on the nominal machine, which only defines a unit: about what an M4 Pro at low load measures.
NOMINAL_REFERENCE_SECONDS = 0.002
# A reference pass is short, so a stall inside one says little: a miss or a round reads several and keeps the middle.
REFERENCE_READS = 5
_BYSTANDER_NAP = 0.002
# Sleeps overshoot a little on a well machine; only what lies past this much of each nap counts as stolen.
_BYSTANDER_JITTER = 0.002


def reference_pass():
    """One fixed piece of the kind of work host tests time: Python loops, dict and list churn, text and JSON, a stat."""
    total = 0
    for _ in range(20):
        rows = [{"name": str(i), "value": i * 3, "tags": [i, i + 1]} for i in range(120)]
        for row in json.loads(json.dumps(rows)):
            total += row["value"] + len(row["name"]) + sum(row["tags"])
        os.stat(__file__)
    return total


def reference_seconds(reads=REFERENCE_READS):
    """The middle of ``reads`` timed reference passes, on this machine, now."""
    times = []
    for _ in range(reads):
        started = _clock()
        reference_pass()
        times.append(_clock() - started)
    return sorted(times)[len(times) // 2]


class Elapsed(float):
    """Seconds a window measured, with the seconds the machine stole during it (read by ``assert_within``)."""

    stolen = 0.0

    def __new__(cls, seconds, stolen=0.0):
        value = super().__new__(cls, seconds)
        value.stolen = stolen
        return value


class Window:
    """From ``start`` to ``stop``: the elapsed time and what a bystander thread saw the machine take."""

    def __init__(self, test):
        self._test = test
        self._stolen = 0.0
        self._stop = _Event()
        self._ended = None
        self._thread = _Thread(target=self._watch, name="MachineClockBystander", daemon=True)
        test.addCleanup(self.stop)
        self._started = _clock()
        self._thread.start()

    def _watch(self):
        while not self._stop.is_set():
            before = _clock()
            _sleep(_BYSTANDER_NAP)
            late = _clock() - before - _BYSTANDER_NAP - _BYSTANDER_JITTER
            if late > 0:
                self._stolen += late

    def stop(self):
        """Ends the window (once; later calls return the same reading) and returns its ``Elapsed``."""
        if self._ended is None:
            self._ended = _clock()
            self._stop.set()
            self._thread.join()
        return Elapsed(self._ended - self._started, self._stolen)

    def assert_within(self, limit, message=None):
        """Ends the window and asserts it took no longer than ``limit`` allows on this machine."""
        assert_within(self._test, self.stop(), limit, message)


def start(test):
    """Opens a window for the unittest ``test``; a failure before its ``stop`` still ends it (a cleanup does)."""
    return Window(test)


def allowance(limit, stolen=0.0, reference=None):
    """``limit``, grown for the machine: slower than nominal by the reference's factor, plus what was stolen."""
    reference = reference_seconds() if reference is None else reference
    return limit * max(1.0, reference / NOMINAL_REFERENCE_SECONDS) + stolen


def assert_within(test, elapsed, limit, message=None):
    """Asserts ``elapsed`` (a Window's ``Elapsed`` carries what was stolen) is within ``limit`` on this machine.

    Nothing extra runs while the fixed limit holds. A miss states every number it weighed."""
    if elapsed < limit:
        return
    stolen = getattr(elapsed, "stolen", 0.0)
    reference = reference_seconds()
    allowed = allowance(limit, stolen, reference)
    if elapsed < allowed:
        return
    test.fail(test._formatMessage(message, (
        f"took {float(elapsed):.4f} s; limit {limit} s grows to {allowed:.4f} s on this machine "
        f"(reference pass {reference:.5f} s against nominal {NOMINAL_REFERENCE_SECONDS} s, {stolen:.4f} s stolen)")))


def assert_cost(test, work, budget, message=None, rounds=3):
    """Asserts ``work()`` costs no more than ``budget`` seconds on the nominal machine, scaled by a reference pass here.

    Returns the last value ``work`` returned, so a test can check it too."""
    readings = []
    for _ in range(rounds):
        reference = reference_seconds()
        started = _clock()
        value = work()
        seconds = _clock() - started
        bound = budget * reference / NOMINAL_REFERENCE_SECONDS
        readings.append((round(seconds, 5), round(reference, 5)))
        if seconds < bound:
            return value
        if seconds > 2 * bound:   # Far past what a stall explains.
            break
    test.fail(test._formatMessage(message, (
        f"cost past its budget of {budget} s on the nominal machine (reference pass {NOMINAL_REFERENCE_SECONDS} s); "
        f"(seconds, reference) per round: {readings}")))
    return None
