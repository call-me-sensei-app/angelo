# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a host test waits for asynchronous work: on the work's own completion signal, capped only against a hang.

A test that gave a fixture's worker a fixed few seconds of wall clock failed whenever the machine was slow:
``test_native_agent_stack`` on a loaded Windows VM (VM CI on 3d19e295337, 2026-11-11, "Claude fixture turn did not
finish" after 5 s), reproduced every time by delaying the fixture's worker 5.5 s. A wait returns the moment its
signal shows, so its cap costs a passing run nothing; it only bounds how long a real hang takes to fail. ``CAP_S``
gives the slowest machine a margin of more than ten times the old caps.

A test that asserts something does NOT happen within a window (``assertFalse(event.wait(0.05))``, a worker still
alive after ``join(0.1)``) waits for no signal: its short window stays, marked ``# window:`` with its reason
(``test_native_host_waits`` checks both). Given the cap instead, such a wait always runs the whole cap: the sweep that
introduced ``CAP_S`` capped some (``test_native_team_account``'s ``writer.join(1.0)`` before ``writer.is_alive()``),
and VM CI's Linux suite on 1.1 4383f9fab8d ran past its 50-minute hang guard (35-40 min without them). So
``watch()`` (``run_host_tests`` installs it) records every wait a test file gives exactly ``CAP_S`` that runs out,
and the runner fails the test it ran out in: either a hang, or a window given the cap. A deadline loop
(``time.monotonic() + CAP_S``) cannot be watched, and a test's length proves nothing (a source scan took 90 s on a
Windows VM, 07e3d91a8e9): the runner prints each test of a second or more with its time (``SLOW``), so a loop that
runs to the cap shows as a test that grew by it.
"""

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

CAP_S = 60.0       # The longest any wait for a completion signal waits: a hang, never a slow machine, ends it.
POLL_S = 0.002     # Between two looks at a signal that cannot be waited on (state another thread changes).
HERE = str(Path(__file__).resolve().parent)
ran_out = []       # "file:line (what)" of each capped wait that ran out since the runner last took them.


def until(condition, *, pump=None, cap=None, poll=POLL_S, what="the awaited work"):
    """``condition()``'s first truthy value, calling ``pump()`` (main-thread work the signal depends on, such as a
    controller's events) before each look; ``AssertionError`` naming ``what`` after ``cap`` seconds (``CAP_S``)
    without it."""
    capped, cap = cap is None, CAP_S if cap is None else cap
    deadline = time.monotonic() + cap
    while True:
        if pump is not None:
            pump()
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            if capped:
                _ran_out("until")
            raise AssertionError("%s did not finish within %.0f s" % (what, cap))
        time.sleep(poll)


def controller_idle(controller):
    """A Controller's turn is over: it is not busy and its worker thread has ended."""
    return not controller.busy and not (controller._worker and controller._worker.is_alive())


def _ran_out(what):
    """Record the test-folder line that gave ``CAP_S`` to a wait that ran out (product code's own waits are not)."""
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_code.co_filename
        if name.startswith(HERE) and Path(name).name != "host_waits.py":
            ran_out.append("%s:%d (%s)" % (Path(name).name, frame.f_lineno, what))
            return
        frame = frame.f_back


def _capped(timeout):
    return timeout is not None and timeout == CAP_S


def watch():
    """Record in ``ran_out`` every Event/Condition wait, Thread join, Queue get and Popen wait or communicate given
    ``CAP_S`` that runs out. Idempotent; a passing run's waits return on their signal, so this costs it nothing."""
    if getattr(threading.Event.wait, "_host_waits", False):
        return
    event_wait, condition_wait, join = threading.Event.wait, threading.Condition.wait, threading.Thread.join
    get, popen_wait, communicate = queue.Queue.get, subprocess.Popen.wait, subprocess.Popen.communicate

    def _event_wait(self, timeout=None):
        done = event_wait(self, timeout)
        if not done and _capped(timeout):
            _ran_out("Event.wait")
        return done

    def _condition_wait(self, timeout=None):
        done = condition_wait(self, timeout)
        if not done and _capped(timeout) and sys._getframe(1).f_code is not event_wait.__code__:
            _ran_out("Condition.wait")
        return done

    def _join(self, timeout=None):
        join(self, timeout)
        if _capped(timeout) and self.is_alive():
            _ran_out("Thread.join")

    def _get(self, block=True, timeout=None):
        try:
            return get(self, block, timeout)
        except queue.Empty:
            if _capped(timeout):
                _ran_out("Queue.get")
            raise

    def _popen_wait(self, timeout=None):
        try:
            return popen_wait(self, timeout)
        except subprocess.TimeoutExpired:
            if _capped(timeout):
                _ran_out("Popen.wait")
            raise

    def _communicate(self, input=None, timeout=None):
        try:
            return communicate(self, input, timeout)
        except subprocess.TimeoutExpired:
            if _capped(timeout):
                _ran_out("Popen.communicate")
            raise

    for owner, name, wrapper in ((threading.Event, "wait", _event_wait), (threading.Condition, "wait", _condition_wait),
                                 (threading.Thread, "join", _join), (queue.Queue, "get", _get),
                                 (subprocess.Popen, "wait", _popen_wait),
                                 (subprocess.Popen, "communicate", _communicate)):
        wrapper._host_waits = True
        setattr(owner, name, wrapper)


def take():
    """The capped waits that ran out since the last call, emptied."""
    taken = list(dict.fromkeys(ran_out))
    del ran_out[:len(ran_out)]
    return taken
