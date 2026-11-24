# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""A fake ``time`` for the Fab browser tests that patch a product module's ``time``.

The clock moves only when the code under test sleeps or a test advances it. It never moves because the machine is
slow, so each test decides which deadline fires, not the host's load. Under real ``time.monotonic``, a loaded CI
machine let a startup deadline fire before the outcome the test checks (win-3, 2026-11-21: TIMEOUT instead of
MANUAL_FOREGROUND_UNPROVED)."""
from unittest.mock import patch


class FakeClock:
    """``monotonic`` and ``sleep``; the time stands still between calls."""

    def __init__(self, now=0.0):
        self.now = float(now)
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)

    def advance(self, seconds):
        self.now += seconds


def use_fake_clock(test, module, now=0.0):
    """Patch ``module.time`` with a new FakeClock for the rest of ``test``, and return the clock. A test can still
    patch ``module.time`` again inside, with its own clock."""
    clock = FakeClock(now)
    patcher = patch.object(module, 'time', clock)
    patcher.start()
    test.addCleanup(patcher.stop)
    return clock
