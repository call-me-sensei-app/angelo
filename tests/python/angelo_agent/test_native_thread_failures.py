# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An uncaught exception in a thread a host test starts, or an unraisable one, fails that test (thread_failures).

Found by the external audit of 1.1.0's cut 08e2b0f7842 (2026-11-13): test_native_tool_deadlines_start_gap's race test
passed although its worker thread crashed (its stand-in call had no ``id``), because Python only prints a thread's
uncaught exception and unittest never sees it. Every host test's first import (suite_isolation) installs the hooks
outside Blender: an exception a thread or a finaliser drops during a test is added to that test as an error, with the
thread and its traceback; ``thread_failures.expected()`` keeps out the ones a test causes on purpose.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import gc
import io
import sys
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import thread_failures  # noqa: E402


def outcome(test_class):
    """Run ``test_class`` in a fresh result: (errors, failures) as text."""
    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(test_class).run(result)
    return [text for _test, text in result.errors], [text for _test, text in result.failures]


def crashing_thread():
    thread = threading.Thread(target=lambda: {}["missing"], name="worker-under-test")
    thread.start()
    thread.join(CAP_S)


def case(kind):
    """A one-test TestCase, made here so the loader never collects it on its own."""
    def crash(self):
        crashing_thread()

    def asked_for(self):
        with thread_failures.expected():
            crashing_thread()

    def clean(self):
        thread = threading.Thread(target=lambda: None)
        thread.start()
        thread.join(CAP_S)

    def finaliser(self):
        class Broken:
            def __del__(self):
                raise ValueError("finaliser broke")
        Broken()
        gc.collect()

    body = {"crash": crash, "asked_for": asked_for, "clean": clean, "finaliser": finaliser}[kind]
    return type("Case_" + kind, (unittest.TestCase,), {"test_it": body})


class ThreadFailureTests(unittest.TestCase):
    def setUp(self):
        stderr = io.StringIO()   # The previous hooks still print; keep this file's own output quiet.
        self.addCleanup(setattr, sys, "stderr", sys.stderr)
        sys.stderr = stderr

    def test_the_hooks_are_installed_by_every_host_tests_first_import(self):
        self.assertIs(threading.excepthook, thread_failures.thread_hook)
        self.assertIs(sys.unraisablehook, thread_failures.unraisable_hook)

    def test_a_crash_in_a_thread_the_test_started_fails_the_test(self):
        errors, failures = outcome(case("crash"))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("worker-under-test", errors[0])
        self.assertIn("KeyError: 'missing'", errors[0])

    def test_a_crash_the_test_asked_for_does_not(self):
        self.assertEqual(outcome(case("asked_for")), ([], []))

    def test_a_clean_thread_changes_nothing(self):
        self.assertEqual(outcome(case("clean")), ([], []))

    def test_an_unraisable_exception_fails_the_test(self):
        errors, _failures = outcome(case("finaliser"))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("finaliser broke", errors[0])

    def test_a_crash_never_counts_against_a_later_test(self):
        outcome(case("crash"))
        self.assertEqual(outcome(case("clean")), ([], []))


if __name__ == "__main__":
    unittest.main()
