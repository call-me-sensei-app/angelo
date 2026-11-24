# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A restamped tolerance is at least three times the operation's within-run spread (no Blender).

Maintainer ruling 7 for 2.0.0 (2026-11-18, noisy operations on the restamped ceilings): 2.1.0 fixes the tolerance rule
so a tolerance is at least 3x the within-run spread. Tonight's restamp of 2.1.0 (56a636f56fb) failed its gate as noisy
on 24 operations, every one inside its band: their tolerances came from the range of the 8 runs' medians only, while
the noisy rule compares a run's own spread with a third of the tolerance. Within-run spread is a run's own recorded
spread for the operation (``ratchet.spread``: (max - min) / median of its valid samples, ``perf_counters``); the floor
is 3x the largest of the runs'. The cap (``RESTAMP_MAX_TOLERANCE``) still holds; nothing else changes.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perf_ratchet  # noqa: E402
import perf_rebaseline  # noqa: E402

PLATFORM = "darwin-arm64"
SUM = "snapshot/history_10_repeat_capture_three_write_sum"


def run(spreads, **operations):
    return {"binary_sha256": "b" * 64, "ratchet": {"metric": "instructions", "statistic": "median",
                                                    "platform": PLATFORM, "operations": operations,
                                                    "spread": spreads}}


class Rule(unittest.TestCase):
    def tolerance(self, name, values, spreads, **extra):
        return perf_ratchet.restamp_tolerance(name, values, spreads=spreads, **extra)

    def test_a_quiet_operation_keeps_the_standing_rule(self):
        for spreads in ([.01, .012, .005], [], [None, None]):
            with self.subTest(spreads=spreads):
                self.assertEqual(self.tolerance("default/x", [1000, 1003, 1006], spreads), .05)
                self.assertEqual(self.tolerance("default/x", [1000, 1080], spreads), .16)
                self.assertEqual(self.tolerance("default/x", [1000, 1003], spreads, writes_files=True), .1)

    def test_a_noisy_operation_gets_three_times_its_largest_within_run_spread(self):
        self.assertEqual(self.tolerance("default/x", [1000, 1003, 1006], [.02, .04, None, .03]), .12)
        self.assertEqual(self.tolerance("default/x", [1000, 1003], [.04], writes_files=True), .12)
        self.assertEqual(self.tolerance(SUM, [1000, 1003, 1006], [.006, .004]), .018,
                         "a three-write sum keeps no 5% floor, but a run's own spread still sets one")

    def test_the_cap_holds(self):
        self.assertEqual(self.tolerance("default/x", [1000, 1003], [.2]), perf_ratchet.RESTAMP_MAX_TOLERANCE)
        self.assertEqual(self.tolerance(SUM, [1000, 1003], [.4]), perf_ratchet.RESTAMP_MAX_TOLERANCE)

    def test_a_spread_must_be_a_number_from_zero_up(self):
        for bad in (-.01, float("nan"), "0.1", True):
            with self.subTest(spread=bad), self.assertRaises(perf_ratchet.RatchetError):
                self.tolerance("default/x", [1000, 1003], [bad])

    def test_within_its_band_an_operation_restamped_from_noisy_runs_is_not_noisy(self):
        runs = [run({"default/x": spread}, **{"default/x": value})
                for value, spread in ((1000, .02), (1004, .045), (1002, .03))]
        updated = perf_rebaseline.rebaseline({"version": 1, "metric": perf_ratchet.METRIC, "default_tolerance": .05,
                                              "platforms": {}}, runs, ["default/x"], today="2026-11-21")
        entry = updated["platforms"][PLATFORM]
        self.assertEqual(entry["operations"]["default/x"], {"ceiling": 1004, "tolerance": .135})
        self.assertEqual(entry["rebaselined"][0]["operations"]["default/x"],
                         {"runs": [1000, 1004, 1002], "tolerance": .135, "within_run_spread": .045})
        row = perf_ratchet.compare(updated, PLATFORM, {"default/x": 1003}, {"spread": {"default/x": .045}})[0]
        self.assertEqual(row["status"], "within", "the largest spread the runs showed is no longer noisy")


if __name__ == "__main__":
    unittest.main()
