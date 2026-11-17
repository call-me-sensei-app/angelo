# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A cost check that compares sizes passes linear work under load and still fails work that grows too fast
(``cost_timing``; ``test_blender_web_link_cost``), without Blender.

1.1.0 candidate ce9ddce (Windows build3, four background suites at once): ``deciding_grows_linearly_with_instancers``
read 147.7 ms at 10,000 instancers against 8.24 ms at 1,000 (bound 1.5 x 10 x 8.24 + 5 = 128.6 ms), a median of 9
timed after the 1,000 ones had finished; it passed in all ten earlier runs, with no web-link change. ``LoadedClock``
replays such load deterministically: work advances the clock by its own cost, slowed by any burst running then and
lengthened by preemptions. Its sizes are this suite's (8 ms at 1,000, linear 88 ms at 10,000: the ratio measured
on Mac 2, 5.5 against 61 ms), and its bursts slow work by 1.5 to 2.5 times, as that run's did.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import random
import statistics
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cost_timing as cost  # noqa: E402

FEW_MS, RATIO = 8.0, 10.0
LINEAR_MS, QUADRATIC_MS = FEW_MS * 11.0, FEW_MS * RATIO * RATIO
SEEDS = range(300)


class LoadedClock:
    """A clock only work moves. ``bursts`` are (start s, end s, slowdown); a call that starts in one takes its cost
    times the largest slowdown running then, and ``preempt`` of the calls take up to 20 ms more."""

    def __init__(self, seed=0, bursts=None, preempt=0.25, slowdown=1.0):
        self.now, self.random, self.preempt, self.slowdown = 0.0, random.Random(seed), preempt, slowdown
        self.bursts = self._bursts() if bursts is None else bursts

    def _bursts(self):   # About a third of the time under a burst of 0.2-1.2 s, as concurrent suites start and stop.
        found, at = [], self.random.uniform(0.0, 1.0)
        while at < 120.0:
            length = self.random.uniform(0.2, 1.2)
            found.append((at, at + length, self.random.uniform(1.5, 2.5)))
            at += length + self.random.expovariate(1 / 1.5)
        return found

    def __call__(self):
        return self.now

    def work(self, ms):
        def run():
            factor = max([slow for start, end, slow in self.bursts if start <= self.now < end] or [1.0])
            extra = self.random.uniform(0.0, 20.0) if self.random.random() < self.preempt else 0.0
            self.now += (ms * factor * self.slowdown + extra) / 1000.0
        return run


def sequential_median_ms(clock, works, rounds=9, warm=2, gap_s=0.0):
    """What the suite did before: each size's median of ``rounds`` after ``warm``, one size after the other (its
    scene built in between, ``gap_s``)."""
    found = {}
    for name, work in works.items():
        clock.now += gap_s
        for _ in range(warm):
            work()
        times = []
        for _ in range(rounds):
            started = clock()
            work()
            times.append((clock() - started) * 1000.0)
        found[name] = round(statistics.median(times), 3)
    return found


def grows_linearly(found):
    return cost.bounded(found["few"], found["many"], 1.5 * RATIO, 5.0)


def best(clock, many_ms):
    return cost.best_ms({"few": clock.work(FEW_MS), "many": clock.work(many_ms)}, 15, 2, clock=clock)


class TheRecordedFailure(unittest.TestCase):
    """A burst that starts as the larger size is timed and outlasts it: the shape of ce9ddce's run."""

    BURST = [(0.088 + 0.5, 3.0, 1.8)]   # From the end of the 1,000 ones' 11 decisions and the next scene's build.

    def test_the_old_sequential_medians_fail_linear_work(self):
        clock = LoadedClock(bursts=self.BURST, preempt=0.0)
        found = sequential_median_ms(clock, {"few": clock.work(FEW_MS), "many": clock.work(LINEAR_MS)}, gap_s=0.5)
        self.assertEqual(found, {"few": 8.0, "many": 158.4})
        self.assertFalse(grows_linearly(found), "the false failure, reproduced")

    def test_interleaved_best_passes_the_same_burst(self):
        clock = LoadedClock(bursts=self.BURST, preempt=0.0)
        found = best(clock, LINEAR_MS)
        self.assertTrue(grows_linearly(found), found)


class NoisyLinearPasses(unittest.TestCase):
    def test_linear_work_passes_under_every_seeds_load(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertTrue(grows_linearly(best(LoadedClock(seed), LINEAR_MS)))

    def test_the_old_rule_fails_some_of_the_same_runs(self):
        """The same loads fail the old rule, so the seeds exercise what failed (not a load nothing notices)."""
        failed = 0
        for seed in SEEDS:
            clock = LoadedClock(seed)
            failed += not grows_linearly(sequential_median_ms(
                clock, {"few": clock.work(FEW_MS), "many": clock.work(LINEAR_MS)}, gap_s=0.5))
        self.assertGreater(failed, 0)

    def test_a_machine_slowed_throughout_still_passes(self):
        """Steady load slows both sizes alike: their ratio, which the check judges, holds."""
        self.assertTrue(grows_linearly(best(LoadedClock(7, bursts=[], slowdown=3.0), LINEAR_MS)))


class TooFastGrowthFails(unittest.TestCase):
    def test_quadratic_work_fails_under_every_seeds_load_and_none(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertFalse(grows_linearly(best(LoadedClock(seed), QUADRATIC_MS)))
        self.assertFalse(grows_linearly(best(LoadedClock(bursts=[], preempt=0.0), QUADRATIC_MS)))

    def test_twice_linear_still_fails_under_load(self):
        """A regression well short of quadratic (twice the linear cost) is still caught: the best repeat of the
        larger size can't be faster than its work."""
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertFalse(grows_linearly(best(LoadedClock(seed), 2 * FEW_MS * RATIO * 1.1)))

    def test_load_never_makes_work_look_cheaper(self):
        for seed in SEEDS[:50]:
            found = best(LoadedClock(seed), QUADRATIC_MS)
            self.assertGreaterEqual(found["few"], FEW_MS)
            self.assertGreaterEqual(found["many"], QUADRATIC_MS)


class BestMs(unittest.TestCase):
    def test_rounds_rotate_and_warm_up_untimed(self):
        calls, clock = [], LoadedClock(bursts=[], preempt=0.0)
        works = {name: (lambda name=name: (calls.append(name), clock.work(1.0)())) for name in "abc"}
        found = cost.best_ms(works, 4, warm=1, clock=clock)
        self.assertEqual("".join(calls), "abc" + "abc" + "bca" + "cab" + "abc")
        self.assertEqual(found, {"a": 1.0, "b": 1.0, "c": 1.0})

    def test_the_least_repeat_is_the_answer(self):
        times = iter([5.0, 3.0, 9.0])
        clock = LoadedClock(bursts=[], preempt=0.0)
        found = cost.best_ms({"only": lambda: clock.work(next(times))()}, 3, warm=0, clock=clock)
        self.assertEqual(found, {"only": 3.0})

    def test_bounded_is_the_checks_own_bound(self):
        self.assertTrue(cost.bounded(8.24, 128.6, 15.0, 5.0))
        self.assertFalse(cost.bounded(8.24, 128.7, 15.0, 5.0))


class SuiteUsesIt(unittest.TestCase):
    SOURCE = (HERE / "test_blender_web_link_cost.py").read_text(encoding="utf-8")

    def test_both_ratio_checks_judge_interleaved_bests_with_their_own_bounds(self):
        self.assertIn('cost.bounded(small, large, 2.0, 2.0)', self.SOURCE)
        self.assertIn('cost.bounded(few, many, 1.5 * ratio, 5.0)', self.SOURCE)
        self.assertIn("cost.best_ms(works, ROUNDS, WARM)", self.SOURCE)
        self.assertIn("ROUNDS, WARM = 15, 2", self.SOURCE)

    def test_no_decision_is_a_median(self):
        self.assertNotIn("median_ms(decide", self.SOURCE)
        self.assertNotIn("<= 1.5 * ratio * few", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
