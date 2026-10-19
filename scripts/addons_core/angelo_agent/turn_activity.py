# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long the host waits for a provider turn: for silence, not for a flat time.

Both providers used to give up after 600 s whatever the runtime was doing. A
round that had been streaming its reasoning for ten minutes ended exactly like
one that never answered, and a Codex turn lost the minutes a person spent
reading a script it had asked to run. The host now gives up on a turn only
when

* nothing has come from the model for ``IDLE_SECONDS`` (a hung process or
  connection; the same 600 s, applied to silence), or
* one stretch of model work has lasted ``STRETCH_SECONDS`` without a result
  (a runaway response; Stop remains available the whole time).

While the host itself is working for the turn (a tool call waiting for
approval or running) neither clock runs, and the next stretch starts when it
hands back. Effort is not an input: a stream of events says directly what
effort could only suggest.
"""

import threading
import time

IDLE_SECONDS = 600
STRETCH_SECONDS = 1800
POLL_SECONDS = 1.0
SILENT, LONG = "silent", "long"


class TurnActivity:
    """When a turn last showed life, and when its current stretch of model work began."""

    def __init__(self, clock=time.monotonic):
        self._clock, self._lock = clock, threading.Lock()
        self.last = self.stretch = clock()

    def touch(self):
        """The runtime sent something."""
        with self._lock:
            self.last = self._clock()

    def rest(self):
        """The host is working for the turn, or just handed back: a new stretch begins."""
        with self._lock:
            self.last = self.stretch = self._clock()

    def overdue(self, idle=None, stretch=None):
        """``SILENT`` or ``LONG`` when the turn should be given up, else None."""
        idle = IDLE_SECONDS if idle is None else idle
        stretch = STRETCH_SECONDS if stretch is None else stretch
        with self._lock:
            now = self._clock()
            if now - self.last >= idle:
                return SILENT
            return LONG if now - self.stretch >= stretch else None


def wait(done, activity, *, busy=None):
    """Block until ``done`` is set. Returns None, or why the host stopped waiting.

    ``busy()`` says whether the host is working for the turn (Codex tool calls
    arrive inside it). The limits are read on every poll, so a change takes
    effect at once.
    """
    while not done.wait(POLL_SECONDS):
        if callable(busy) and busy():
            activity.rest()
            continue
        reason = activity.overdue()
        if reason:
            return reason
    return None


def problem(runtime, reason, advice):
    """The error a person reads when the host gave up on a turn; ``advice`` says what state that leaves."""
    if reason == SILENT:
        return "%s turn timed out: nothing came from the model for %d minutes. %s" % (runtime, IDLE_SECONDS // 60, advice)
    return ("%s turn timed out: one stretch of model work ran for %d minutes without a result. %s"
            % (runtime, STRETCH_SECONDS // 60, advice))
