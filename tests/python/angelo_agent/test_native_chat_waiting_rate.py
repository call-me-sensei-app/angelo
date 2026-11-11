# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``test_blender_chat_waiting``'s loop-rate cross-check is held to its own run's noise, without Blender.

1.0.11 (Mac 1, build-angelo-rc1011): ``5_and_the_loop_rate_agrees_within_its_noise`` failed one run in twelve, alone
(the suite is ``serial``: it never runs beside another). Its rule was a fixed band, the median "on" share at most
0.03, while the "off" windows of the same runs differ from each other by up to 10 %: the main loop's own spread, not
load. The composed share (one frame's CPU time, the 2 % gate) was 0.6-1.1 % in every run. The rule is now
``chat_waiting_measure.agreement``: the loop rate agrees with the composed share unless they differ by more than this
run's off-against-off noise allows at the 0.001 level, over 30 windows.

``RUNS`` are the window rates (loop iterations a second) and composed shares of real runs on Mac 1: the first failed
the fixed band, the rest passed it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
with mock.patch.dict(sys.modules, {"bpy": types.ModuleType("bpy")}):
    sys.modules.pop("chat_waiting_measure", None)
    import chat_waiting_measure as gauge  # noqa: E402

# (on window rates, off window rates, composed share), in time order on, off, on, off...
RUNS = (
    ([34827, 35030, 33394, 32250, 32075], [35919, 35459, 34190, 32658, 33925], 0.0084),   # Failed the fixed band.
    ([31941, 32671, 32109, 27636, 33504], [32083, 34768, 33744, 31798, 33758], 0.0092),
    ([34992, 34108, 34967, 32811, 33025], [33387, 35053, 34389, 30494, 33256], 0.0108),
    ([34594, 36282, 36419, 34532, 35571], [36773, 37261, 34909, 35671, 33667], 0.008),
    ([31892, 32810, 33673, 30903, 32941], [31936, 33867, 33806, 33687, 33240], 0.0067),
    ([32935, 32843, 34072, 30715, 31111], [33358, 32972, 34387, 32529, 30695], 0.0061),
    ([31549, 32715, 32425, 32128, 31735], [32252, 32891, 32552, 31668, 32013], 0.0074),
    ([30651, 31157, 31707, 30358, 31731], [29734, 31259, 30769, 31024, 31761], 0.0084),
    ([33430, 34513, 32409, 32550, 31138], [34366, 34548, 34026, 31887, 32031], 0.0085),
)
SECONDS = 0.8   # A window's counted part (one second, less the settle after a switch).


def samples(on, off, repeat=1, extra=0.0):
    """``Windows.samples`` for alternating windows (on first); ``repeat`` tiles the run (more windows, the same
    spread); ``extra`` takes that share of the "on" windows' loop iterations (work no frame shows)."""
    out, t = {True: [], False: []}, 0.0
    for _ in range(repeat):
        for rate_on, rate_off in zip(on, off):
            for flag, rate in ((True, rate_on * (1 - extra)), (False, rate_off)):
                out[flag].append(((t, (0, 0, 0, 0)), (t + SECONDS, (rate * SECONDS, 0, 0, 0))))
                t += 1.0
    return out


class FixedBandWasNoise(unittest.TestCase):
    def test_the_failed_run_was_inside_its_own_noise(self):
        on, off, composed = RUNS[0]
        share = gauge.median(gauge.paired_shares(samples(on, off)))
        self.assertGreater(share, 0.03, "the fixed band's failure, reproduced from the run's windows")
        self.assertGreater(max(abs(v) for v in gauge.paired_shares(samples(on, off), null=True)), 0.03,
                           "its off windows differ from each other by more than the band")


class Agreement(unittest.TestCase):
    def test_every_recorded_run_agrees_at_thirty_windows(self):
        for index, (on, off, composed) in enumerate(RUNS):
            with self.subTest(run=index):
                result = gauge.agreement(samples(on, off, repeat=3), composed)
                self.assertTrue(result["agrees"], result)
                self.assertEqual((result["windows"], result["null_windows"]), (15, 15))

    def test_work_no_frame_shows_is_caught_above_the_runs_noise(self):
        """The band is what this run's noise allows: 1.8-8.4 % over the recorded runs at 30 windows (the noisiest's
        off windows differ by 6 %). A cost of 15 % of the main thread, outside every frame, is caught in each."""
        for index, (on, off, composed) in enumerate(RUNS):
            with self.subTest(run=index):
                result = gauge.agreement(samples(on, off, repeat=3, extra=0.15), composed)
                self.assertFalse(result["agrees"], result)
                self.assertLess(result["band"], 0.09)

    def test_too_few_windows_to_know_the_noise_never_agree(self):
        on, off, composed = RUNS[1]
        result = gauge.agreement(samples(on[:2], off[:2]), composed)
        self.assertFalse(result["agrees"])
        self.assertIsNone(result["band"])

    def test_the_null_pairs_compare_off_windows_only(self):
        shares = gauge.paired_shares(samples([100, 100, 100], [50, 50, 50]), null=True)
        self.assertEqual(len(shares), 3)
        for share in shares:   # An off window against the off windows two places away: nothing differs.
            self.assertAlmostEqual(share, 0.0)
        self.assertAlmostEqual(gauge.median(gauge.paired_shares(samples([100, 100, 100], [50, 50, 50]))), -1.0)

    def test_the_band_is_this_runs_noise_at_the_0001_level(self):
        on, off, composed = RUNS[2]
        result = gauge.agreement(samples(on, off, repeat=3), composed)
        expected = gauge._T_0001[14] * 1.2533 * result["noise"] / 15 ** 0.5
        self.assertAlmostEqual(result["band"], expected)


class SuiteUsesIt(unittest.TestCase):
    def test_the_suite_checks_agreement_over_thirty_windows(self):
        source = (HERE / "test_blender_chat_waiting.py").read_text(encoding="utf-8")
        self.assertIn('checks["5_and_the_loop_rate_agrees_within_its_noise"] = agreement["agrees"]', source)
        self.assertIn("RATE_WINDOWS = 30", source)
        self.assertIn('gauge.alternate(marks, "rate", reading, 1.5, RATE_WINDOWS, t)', source)
        self.assertNotIn("share <= 0.03", source)
        self.assertIn('agreement = gauge.agreement(marks["rate"].samples, composed)', source)   # This run's windows.


if __name__ == "__main__":
    unittest.main()
