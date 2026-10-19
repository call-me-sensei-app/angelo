# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only checks of the fixture waits that honour the native runner's --timeout-scale."""

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qa_waits


def busy():
    return False


def idle():
    return True


class IdleWaitTests(unittest.TestCase):
    def test_scale_defaults_to_one_and_must_be_positive(self):
        self.assertEqual(1.0, qa_waits.timeout_scale({}))
        self.assertEqual(1.5, qa_waits.timeout_scale({qa_waits.SCALE_ENV: "1.5"}))
        for bad in ("0", "-2", "nan", "inf", "fast", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                qa_waits.timeout_scale({qa_waits.SCALE_ENV: bad})

    def test_budget_is_the_base_times_the_scale(self):
        now = [100.0]
        wait = qa_waits.IdleWait(10.0, {qa_waits.SCALE_ENV: "2"}, clock=lambda: now[0])
        self.assertTrue(wait.ready(idle))
        self.assertFalse(wait.ready(busy))  # The first busy poll starts this wait's clock.
        now[0] = 120.0
        self.assertFalse(wait.ready(busy))  # 20 s = 10 s base x 2: still inside the budget.
        now[0] = 120.1
        with self.assertRaisesRegex(RuntimeError, r"more than 20\.0 s \(10 s base x timeout scale 2\)"):
            wait.ready(busy)

    def test_seeing_idle_rearms_the_budget_for_the_next_wait(self):
        now = [0.0]
        wait = qa_waits.IdleWait(10.0, {}, clock=lambda: now[0])
        self.assertFalse(wait.ready(busy))
        now[0] = 9.0
        self.assertTrue(wait.ready(idle))
        now[0] = 15.0
        self.assertFalse(wait.ready(busy))  # A new wait gets its own 10 s, from 15.
        now[0] = 25.0
        self.assertFalse(wait.ready(busy))
        now[0] = 25.5
        with self.assertRaises(RuntimeError):
            wait.ready(busy)

    def test_other_probe_errors_are_never_waited_out(self):
        def probe():
            raise RuntimeError("The original-storage fixture requires a document")
        with self.assertRaisesRegex(RuntimeError, "requires a document"):
            qa_waits.IdleWait(10.0, {}).ready(probe)

    def test_checkpoint_fixtures_wait_with_the_scaled_budget_in_the_observing_tick(self):
        for name in ("test_blender_sculpt_checkpoint_bundle.py", "test_blender_sculpt_checkpoint_external.py"):
            with self.subTest(fixture=name):
                source = (HERE / name).read_text(encoding="utf-8")
                self.assertIn("IDLE = IdleWait(10.0)", source)
                self.assertNotIn("'polls'", source)
                # A job can start between ticks, so the first observing tick checks idle itself.
                self.assertRegex(source, r"elif phase == 'idle':\n\s*if not IDLE\.ready\(jobs_idle\):")
                self.assertRegex(source, r"elif phase == 'exercise':\n(?:\s*#.*\n)?\s*if not IDLE\.ready\(jobs_idle\):")


if __name__ == "__main__":
    unittest.main()
