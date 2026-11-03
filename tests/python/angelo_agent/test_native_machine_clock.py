# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""machine_clock: a wall-clock bound fails for slow code, never for a slow machine, and no fixed bound comes back."""

import ast
from pathlib import Path
import threading
import time
import unittest
from unittest import mock
import warnings

import machine_clock
from machine_clock import NOMINAL_REFERENCE_SECONDS as NOMINAL

HERE = Path(__file__).resolve().parent
CLOCKS = {"monotonic", "perf_counter", "time"}
UPPER_BOUNDS = {"assertLess", "assertLessEqual"}
# These fall as time passes, so a slow machine can only help them: a deadline's remaining time.
NOT_A_SPEED_BOUND = {("test_native_codex_opening_guards.py", "until"),
                     ("test_native_view_wait_delivery.py", "remaining")}


def reads_clock(node):
    return any(isinstance(call, ast.Call) and getattr(call.func, "attr", "") in CLOCKS for call in ast.walk(node))


def is_clock_difference(node):
    return any(isinstance(part, ast.BinOp) and isinstance(part.op, ast.Sub) and reads_clock(part)
               for part in ast.walk(node))


def fixed_bounds(source):
    """Each upper bound that compares a real clock's difference with something, as ``(name or text, line)``."""
    found = []
    tree = ast.parse(source)
    for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        timed = {target.id for node in ast.walk(function) if isinstance(node, ast.Assign)
                 and is_clock_difference(node.value) for target in node.targets if isinstance(target, ast.Name)}
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            method = getattr(node.func, "attr", "")
            if method in UPPER_BOUNDS and node.args:
                first = node.args[0]
                if is_clock_difference(first):
                    found.append((ast.unparse(first), node.lineno))
                elif isinstance(first, ast.Name) and first.id in timed:
                    found.append((first.id, node.lineno))
            elif method in {"assertTrue", "assertFalse"} and node.args:
                for compare in ast.walk(node.args[0]):
                    if isinstance(compare, ast.Compare) and is_clock_difference(compare.left) \
                            and any(isinstance(op, (ast.Lt, ast.LtE)) for op in compare.ops):
                        found.append((ast.unparse(compare), node.lineno))
    return sorted(set(found), key=lambda row: row[1])


class WithinTests(unittest.TestCase):
    def outcome(self, elapsed, limit, reference):
        """True when ``assert_within`` passes with the reference pass reading ``reference``."""
        probe = unittest.TestCase()
        with mock.patch.object(machine_clock, "reference_seconds", return_value=reference):
            try:
                machine_clock.assert_within(probe, elapsed, limit)
            except AssertionError as miss:
                self.last_miss = str(miss)
                return False
        return True

    def test_within_the_fixed_limit_nothing_is_measured(self):
        with mock.patch.object(machine_clock, "reference_seconds", side_effect=AssertionError("measured")):
            machine_clock.assert_within(self, 0.4, 0.5)

    def test_a_miss_on_a_well_machine_fails_and_says_what_it_weighed(self):
        self.assertFalse(self.outcome(0.6, 0.5, NOMINAL))
        self.assertIn("took 0.6000 s", self.last_miss)
        self.assertIn("limit 0.5 s", self.last_miss)
        self.assertIn("stolen", self.last_miss)

    def test_a_slow_machine_earns_its_factor_and_no_more(self):
        self.assertTrue(self.outcome(1.4, 0.5, 3 * NOMINAL))
        self.assertFalse(self.outcome(1.6, 0.5, 3 * NOMINAL))

    def test_a_fast_machine_never_tightens_a_limit_that_covers_a_real_wait(self):
        self.assertTrue(self.outcome(0.49, 0.5, NOMINAL / 4))
        self.assertEqual(machine_clock.allowance(0.5, reference=NOMINAL / 4), 0.5)

    def test_time_the_machine_stole_is_added_to_the_limit(self):
        self.assertTrue(self.outcome(machine_clock.Elapsed(0.9, stolen=0.5), 0.5, NOMINAL))
        self.assertFalse(self.outcome(machine_clock.Elapsed(1.1, stolen=0.5), 0.5, NOMINAL))


class WindowTests(unittest.TestCase):
    def test_a_real_wait_is_not_stolen_time_and_fails(self):
        """The discrimination the module exists for: a thread blocked on a lock leaves the bystander on time."""
        lock = threading.Lock()
        lock.acquire()
        window = machine_clock.start(self)
        lock.acquire(timeout=0.3)
        lock.release()
        elapsed = window.stop()
        self.assertLess(elapsed.stolen, elapsed / 2, "the bystander stayed on time through a wait")

    def test_a_process_that_loses_the_cpu_shows_up_as_stolen(self):
        window = machine_clock.start(self)
        with mock.patch.object(machine_clock, "_sleep", lambda seconds: time.sleep(seconds + 0.05)):
            time.sleep(0.3)
        self.assertGreater(window.stop().stolen, 0.03)

    def test_it_ends_once_and_its_thread_goes(self):
        window = machine_clock.start(self)
        first = window.stop()
        time.sleep(0.01)
        self.assertEqual(window.stop(), first)
        self.assertFalse([thread for thread in threading.enumerate() if thread.name == "MachineClockBystander"])

    def test_a_failure_before_the_stop_still_ends_it(self):
        class Inner(unittest.TestCase):
            def runTest(inner):
                machine_clock.start(inner)
                inner.fail("before the stop")
        Inner().run(unittest.TestResult())
        self.assertFalse([thread for thread in threading.enumerate() if thread.name == "MachineClockBystander"])

    def test_a_test_patching_the_clock_or_threads_does_not_reach_it(self):
        with mock.patch("time.sleep", side_effect=AssertionError("patched")), \
                mock.patch("threading.Thread", side_effect=AssertionError("patched")), \
                mock.patch("time.perf_counter", return_value=0.0):
            window = machine_clock.start(self)
        self.assertGreaterEqual(window.stop(), 0.0)


class CostTests(unittest.TestCase):
    def cost(self, seconds, budget, references, rounds=3):
        """Runs ``assert_cost`` on a clock this test drives: the work takes ``seconds[i]``, the reference ``references[i]``."""
        now, works = [0.0], iter(seconds)

        def work():
            now[0] += next(works)
            return "value"
        with mock.patch.object(machine_clock, "_clock", lambda: now[0]), \
                mock.patch.object(machine_clock, "reference_seconds", side_effect=references):
            return machine_clock.assert_cost(self, work, budget, rounds=rounds)

    def test_the_budget_scales_with_the_machine_both_ways(self):
        self.assertEqual(self.cost([0.04], 0.01, [5 * NOMINAL]), "value")   # Slow machine: 50 ms allowed.
        with self.assertRaises(AssertionError):
            self.cost([0.009] * 3, 0.01, [NOMINAL / 2] * 3)   # Fast machine: 5 ms allowed, 9 ms is a regression there.

    def test_one_stall_is_not_a_failure(self):
        self.assertEqual(self.cost([0.015, 0.005], 0.01, [NOMINAL, NOMINAL]), "value")

    def test_a_steady_miss_fails_and_states_each_round(self):
        with self.assertRaises(AssertionError) as caught:
            self.cost([0.015] * 3, 0.01, [NOMINAL] * 3)
        self.assertIn("per round: [(0.015, 0.002), (0.015, 0.002), (0.015, 0.002)]", str(caught.exception))

    def test_a_round_far_past_the_bound_ends_at_once(self):
        with self.assertRaises(AssertionError) as caught:
            self.cost([0.5, 0.0], 0.01, [NOMINAL, NOMINAL])
        self.assertIn("per round: [(0.5, 0.002)]", str(caught.exception))

    def test_the_reference_pass_is_a_real_measurement_of_this_machine(self):
        self.assertGreater(machine_clock.reference_seconds(), NOMINAL / 100)


class NoFixedBoundTests(unittest.TestCase):
    def test_the_scan_finds_each_shape_a_fixed_bound_takes(self):
        source = (
            "def t(self):\n"
            "    started = time.monotonic()\n"
            "    self.assertLess(time.monotonic() - started, 5)\n"
            "    self.assertLessEqual((time.perf_counter() - started) / 2, 5)\n"
            "    took = time.monotonic() - started\n"
            "    self.assertLess(took, 5)\n"
            "    self.assertTrue(time.time() - started < 5)\n")
        self.assertEqual([line for _text, line in fixed_bounds(source)], [3, 4, 6, 7])

    def test_it_leaves_lower_bounds_and_simulated_clocks(self):
        source = (
            "def t(self):\n"
            "    self.assertGreater(time.monotonic() - started, 1)\n"
            "    self.assertLess(scene.now - started, 5)\n")
        self.assertEqual(fixed_bounds(source), [])

    def test_no_host_test_holds_a_fixed_wall_clock_bound(self):
        """Every speed bound goes through machine_clock, so the machine never decides a host test's result."""
        left = []
        for path in sorted(HERE.glob("test_native_*.py")):
            if path.name == Path(__file__).name:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)   # Another test file's own text; not this scan's concern.
                bounds = fixed_bounds(path.read_text(encoding="utf-8"))
            for text, line in bounds:
                if not any(path.name == name and word in text for name, word in NOT_A_SPEED_BOUND):
                    left.append("%s:%d %s" % (path.name, line, text))
        self.assertEqual(left, [], "use machine_clock.start(self) and assert_within, or assert_cost")


if __name__ == "__main__":
    unittest.main()
