# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests wait for asynchronous work on its own completion signal, never on a short fixed time (host_waits).

Evidence: VM CI on 3d19e295337 (2026-11-11) failed test_native_agent_stack on a loaded Windows VM, "Claude fixture
turn did not finish": test_native_cache_diet's turn() gave the fixture's worker 5 s of wall clock. Delaying that
worker 5.5 s fails it every time; with host_waits.CAP_S it passes. The same short caps stood in ~440 places.

Every host-test module (not a GUI suite's: those wait on their own signals, d8c02f78fdb) is checked line by line for
a completion wait with a short literal: a deadline ``NAME = time.monotonic() + N``, an ``Event.wait(N)``,
``Thread.join(N)`` or ``Queue.get(timeout=N)``, a wait helper's default, all for N up to ``SHORT_S``. Allowed: a
negative check (``assertFalse(... .wait(N))``), and a line marked ``# window:`` saying why its short time is the point
(an absence asserted over a window, an expiry under test).

A window given the cap instead runs the whole cap every time (VM CI on 1.1 4383f9fab8d: the Linux suite ran past its
50-minute hang guard). ``run_host_tests`` fails the test in which a wait given exactly ``CAP_S`` ran out
(``host_waits.watch``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import io
import queue
import re
import sys
import threading
import tokenize
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import host_waits  # noqa: E402
import run_host_tests as runner  # noqa: E402

SHORT_S = 15.0
OWN = {"host_waits.py", "test_native_host_waits.py"}   # This check's own examples.
NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
WAITS = (
    re.compile(r"^\s*[\w.]+(?:\s*,\s*[\w.]+)*\s*=\s*(?:[\w.\[\]]+\s*,\s*)*time\.monotonic\(\)\s*\+\s*" + NUMBER + r"\b"),
    re.compile(r"\.(?:wait|join)\(\s*(?:timeout\s*=\s*)?" + NUMBER + r"\s*\)"),
    re.compile(r"\.get\([^)]*\btimeout\s*=\s*" + NUMBER + r"\s*\)"),
    re.compile(r"def (?:wait\w*|until\w*|eventually\w*)\([^)]*\b(?:seconds|timeout|limit|cap)\s*=\s*" + NUMBER),
)
# A negative check, or a loop's pace (``while not stop.wait(0.01)``: it waits for no work, it wakes to look again).
NEGATIVE = re.compile(r"assertFalse\([^#]*\.wait\(|while not [\w.\[\]\"']+\.wait\(")


def host_modules():
    """The modules host tests run: test_native_* and every helper beside them that no GUI suite alone uses."""
    for path in sorted(HERE.glob("*.py")):
        if path.name.startswith(("test_blender_", "gui_", "bench_", "benchmark_")) or path.name in OWN:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"^import bpy\b", text, re.M):
            continue   # Runs inside Blender: a GUI suite's helper.
        yield path, text


def text_lines(text):
    """Lines inside a multi-line string (a docstring, a child script's source): prose or another process's code."""
    lines = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.STRING and token.start[0] != token.end[0]:
                lines.update(range(token.start[0], token.end[0] + 1))
    except (tokenize.TokenError, IndentationError):
        pass
    return lines


def short_waits(text):
    found, strings = [], text_lines(text)
    for number, line in enumerate(text.split("\n"), 1):   # As the tokenizer numbers lines (not splitlines()).
        if "# window:" in line or NEGATIVE.search(line) or number in strings:
            continue
        for pattern in WAITS:
            match = pattern.search(line)
            if match and float(match.group(1)) <= SHORT_S:
                found.append((number, line.strip()[:100]))
                break
    return found


class HostWaitTests(unittest.TestCase):
    def test_no_host_test_waits_for_work_on_a_short_fixed_time(self):
        found = ["%s:%d %s" % (path.name, number, line)
                 for path, text in host_modules() for number, line in short_waits(text)]
        self.assertEqual(found, [], "wait on the work's own signal (host_waits.until, host_waits.CAP_S), or mark a "
                                    "deliberate window with '# window: <why>'")

    def test_the_check_tells_a_completion_wait_from_a_window(self):
        flagged = short_waits("deadline = time.monotonic() + 5\n"
                              "self.assertTrue(done.wait(2))\n"
                              "worker.join(5)\n"
                              "item = results.get(timeout=3)\n"
                              "def wait_for(condition, seconds=5):\n")
        self.assertEqual([number for number, _line in flagged], [1, 2, 3, 4, 5])
        self.assertEqual(short_waits("self.assertFalse(done.wait(0.05))\n"
                                     "while not stop.wait(0.01):\n"
                                     "worker.join(0.1)  # window: it must still be blocked\n"
                                     "deadline = time.monotonic() + host_waits.CAP_S\n"
                                     "self.assertTrue(done.wait(host_waits.CAP_S))\n"
                                     "runtime = Batch(jobs, time.monotonic() + 3)\n"), [])

    def test_until_returns_at_once_and_names_what_hung(self):
        self.assertEqual(host_waits.until(lambda: 7), 7)
        with self.assertRaisesRegex(AssertionError, "the fixture's turn did not finish within 0 s"):
            host_waits.until(lambda: False, cap=0.01, what="the fixture's turn")
        self.assertGreaterEqual(host_waits.CAP_S, 10 * 5, "ten times the 5 s cap a loaded VM missed")


class CapRunsOutTests(unittest.TestCase):
    """The runner fails the test a capped wait ran out in, naming its line; a window, or a signal that comes, passes."""

    def run_fixture(self, fixture):
        """``fixture``'s tests as the runner runs them (its result class, ``watch`` installed), the cap 50 ms."""
        host_waits.watch()
        stream = io.StringIO()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(fixture)
        with mock.patch.object(host_waits, "CAP_S", 0.05), contextlib.redirect_stdout(stream):
            result = unittest.TextTestRunner(stream=stream, resultclass=runner.IsolatedResult).run(suite)
        return (0 if result.wasSuccessful() else 1), stream.getvalue()

    def test_each_kind_of_capped_wait_that_runs_out_fails_its_test_and_names_its_line(self):
        held = threading.Event()
        alive = threading.Thread(target=held.wait, daemon=True)
        alive.start()
        self.addCleanup(held.set)
        waits = {"Event.wait": lambda: threading.Event().wait(host_waits.CAP_S),
                 "Condition.wait": self.condition,
                 "Thread.join": lambda: alive.join(host_waits.CAP_S),
                 "Queue.get": self.empty_get,
                 "until": lambda: host_waits.until(lambda: False)}
        for kind, wait in waits.items():
            with self.subTest(kind):
                class Fixture(unittest.TestCase):
                    def test_it(self):
                        with contextlib.suppress(queue.Empty, AssertionError):
                            wait()
                status, output = self.run_fixture(Fixture)
                self.assertEqual(status, 1, output)
                self.assertRegex(output, r"a wait given host_waits.CAP_S ran out at test_native_host_waits.py:\d+ "
                                         r"\(%s\)" % re.escape(kind))

    @staticmethod
    def condition():
        condition = threading.Condition()
        with condition:
            condition.wait(host_waits.CAP_S)

    @staticmethod
    def empty_get():
        queue.Queue().get(timeout=host_waits.CAP_S)

    def test_a_capped_wait_that_runs_out_in_a_class_set_up_fails_the_test_after_it(self):
        class Fixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                threading.Event().wait(host_waits.CAP_S)

            def test_it(self):
                pass
        status, output = self.run_fixture(Fixture)
        self.assertEqual(status, 1, output)
        self.assertRegex(output, r"ran out in the set-ups and tear-downs before this test at "
                                 r"test_native_host_waits.py:\d+ \(Event.wait\)")

    def test_a_window_and_a_signal_that_comes_pass(self):
        class Fixture(unittest.TestCase):
            def test_window(self):
                self.assertFalse(threading.Event().wait(0.01))  # window: nothing sets it

            def test_signal(self):
                done = threading.Event()
                threading.Timer(0.005, done.set).start()
                self.assertTrue(done.wait(host_waits.CAP_S))
        status, output = self.run_fixture(Fixture)
        self.assertEqual(status, 0, output)


if __name__ == "__main__":
    unittest.main()
