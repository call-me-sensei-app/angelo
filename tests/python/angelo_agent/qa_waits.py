# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixture waits that honour the native runner's ``--timeout-scale``.

``run_native_suites.py`` exports its scale as ``ANGELO_QA_TIMEOUT_SCALE``; a
fixture started by hand gets 1. Each wait keeps the fixture's own base budget
and multiplies it by that scale, so a loaded machine can be given more time
without editing a fixture. Standard library only: it runs in the app and on
the host.
"""

import math
import os
import time

SCALE_ENV = "ANGELO_QA_TIMEOUT_SCALE"


def timeout_scale(environ=None):
    """The runner's scale, or 1 outside the runner; anything but a positive number is an error."""
    raw = (os.environ if environ is None else environ).get(SCALE_ENV, "1")
    try:
        scale = float(raw)
    except ValueError:
        scale = math.nan
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("%s must be a positive number, not %r" % (SCALE_ENV, raw))
    return scale


class IdleWait:
    """A bounded wait for native jobs to go idle: ``base_seconds`` x the scale, per wait.

    ``ready(probe)`` is True once ``probe()`` reports idle (which rearms the
    budget for the next wait), False while still inside the budget, and raises
    once the budget is spent. Observe in the same tick that saw idle: a job can
    start whenever control returns to the event loop.
    """

    def __init__(self, base_seconds, environ=None, clock=time.monotonic):
        self.base = base_seconds
        self.scale = timeout_scale(environ)
        self.budget = base_seconds * self.scale
        self._clock = clock
        self._since = None

    def ready(self, probe):
        if probe():
            self._since = None
            return True
        now = self._clock()
        if self._since is None:
            self._since = now
        if now - self._since > self.budget:
            raise RuntimeError("Native jobs stayed busy for more than %.1f s (%g s base x timeout scale %g)"
                               % (self.budget, self.base, self.scale))
        return False


FACTOR_ENV = "ANGELO_QA_MACHINE_FACTOR"


def machine_factor(environ=None):
    """The runner's measured machine factor (qa_machine.py), or 1 outside the runner; never below 1."""
    raw = (os.environ if environ is None else environ).get(FACTOR_ENV, "1")
    try:
        factor = float(raw)
    except ValueError:
        factor = math.nan
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("%s must be a positive number, not %r" % (FACTOR_ENV, raw))
    return max(1.0, factor)


def machine_limit(seconds, environ=None):
    """An absolute timing limit calibrated on the reference Mac, scaled to this machine.

    The factor is the reference workload's time here over its time on the reference Mac (qa_machine.py),
    measured by run_native_suites.py in the same app binary before the suites start; a faster machine keeps
    the calibrated limit. Use it for every absolute timing check, so a slower machine is judged against
    what it can do and a regression on it still fails.
    """
    return seconds * machine_factor(environ)
