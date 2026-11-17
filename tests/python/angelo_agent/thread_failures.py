# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An uncaught exception in a thread a host test starts, or an unraisable one (a finaliser's, a callback's), fails
that test. Python only prints such an exception and unittest never sees it, so a test could pass with its worker
thread dead: test_native_tool_deadlines_start_gap's race test did on 1.1.0's cut 08e2b0f7842 (external audit,
2026-11-13). ``install`` (every host test's first import, ``suite_isolation``, outside Blender) records each one and
adds it as an error to the test that was running, innermost first; ``expected()`` keeps out the ones a test causes
on purpose. The previous hooks still print. Pinned by test_native_thread_failures.
"""

import contextlib
import sys
import threading
import traceback
import unittest

_lock = threading.Lock()
_seen = []          # [(text, claimed)]: every exception recorded, in order.
_expected = [0]     # Depth of ``expected()`` blocks now open.
_previous = {}


class ThreadFailure(Exception):
    """An exception a thread or a finaliser dropped while the test ran."""


def _record(where, exc_type, exc_value, exc_traceback):
    text = "%s:\n%s" % (where, "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).rstrip())
    with _lock:
        _seen.append([text, _expected[0] > 0])   # Expected: recorded as already claimed, never reported.


def thread_hook(args):
    if args.exc_type is not SystemExit:
        name = args.thread.name if args.thread is not None else "unknown"
        _record("uncaught exception in thread %r" % name, args.exc_type, args.exc_value, args.exc_traceback)
    _previous["thread"](args)


def unraisable_hook(unraisable):
    _record("unraisable exception (%s%s)" % (unraisable.err_msg or "Exception ignored in",
                                             ": %r" % (unraisable.object,) if unraisable.object is not None else ""),
            unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback)
    _previous["unraisable"](unraisable)


@contextlib.contextmanager
def expected():
    """A block whose thread and unraisable exceptions the test causes on purpose: they fail nothing."""
    with _lock:
        _expected[0] += 1
    try:
        yield
    finally:
        with _lock:
            _expected[0] -= 1


def _run(original):
    def run(self, result=None):
        with _lock:
            start = len(_seen)
        result = original(self, result)
        with _lock:
            unclaimed = [entry for entry in _seen[start:] if not entry[1]]
            for entry in unclaimed:
                entry[1] = True
        if result is not None:
            for text, _claimed in unclaimed:
                result.addError(self, (ThreadFailure, ThreadFailure(text), None))
        return result
    run.thread_failures = True
    return run


def install():
    """Install the hooks and wrap ``unittest.TestCase.run`` once per process."""
    if getattr(unittest.TestCase.run, "thread_failures", False):
        return
    _previous["thread"], _previous["unraisable"] = threading.excepthook, sys.unraisablehook
    threading.excepthook, sys.unraisablehook = thread_hook, unraisable_hook
    unittest.TestCase.run = _run(unittest.TestCase.run)
