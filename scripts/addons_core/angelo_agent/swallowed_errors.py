# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Defects caught so one feature never breaks another (a live page, the apps' listener), printed and kept for tests.

A wrapper that catches everything so the rest goes on (``session_page.pump_safely``, ``session_page.link``,
``external_host.pump``, the CLI listener's handler) reports what it caught here. In the app that is a log line, as
before. A test harness reads ``caught()`` after each test and fails on anything it did not ``expect``: a swallowed
defect goes red instead of passing (2.0.1: a page crashing at every publish passed every host test, because
``pump_safely`` swallowed it). Standard library only; no bpy; safe from any thread.
"""

import contextlib
import threading

MAX_KEPT = 64   # Enough to name what went wrong; a defect repeating every tick never grows the list without end.
_lock = threading.Lock()
_caught, _expected = [], []


def report(where, error, quiet=False):
    """``where``: what ended or went on without it, as the log says it. ``error``: the exception caught. ``quiet``: a
    site that answers its caller instead of printing (the CLI listener's handler)."""
    if not quiet:
        print("Angelo: %s: %s %s" % (where, type(error).__name__, error))
    with _lock:
        if not any(isinstance(error, kind) for kind in _expected) and len(_caught) < MAX_KEPT:
            _caught.append("%s: %s: %s" % (where, type(error).__name__, error))


def caught():
    with _lock:
        return list(_caught)


def reset():
    with _lock:
        _caught.clear()


@contextlib.contextmanager
def expect(*kinds):
    """A test that injects a defect on purpose: ``kinds`` caught inside are not counted."""
    with _lock:
        _expected.extend(kinds)
    try:
        yield
    finally:
        with _lock:
            for kind in kinds:
                _expected.remove(kind)
