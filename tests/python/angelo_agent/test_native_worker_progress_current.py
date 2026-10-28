# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker's tile counts its own plan only while the count keeps up with its work (``parallel_sessions.progress_of``).

Director capture (2026-10-25, Sol desk_chair): both tiles read "0 of 3 steps · 0%" while their parts were visibly
built; a worker reports its plan when it likes, so a count it has not updated since it saved work says less than the
tile's own picture. A save after its last report hides the count (and the step it names) until it reports again; a
plan reported all done stays."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import parallel_sessions  # noqa: E402

STEPS = ["Block out", "Shape", "Detail"]


class ProgressTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("_order", ["w1"]), ("progress", {}), ("pending", {}), ("sizes", {}), ("boxes", {})):
            patch = mock.patch.object(parallel_sessions, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        for name in ("_bump", "_ensure_timer"):
            patch = mock.patch.object(parallel_sessions, name, lambda *args, **kwargs: None)
            patch.start()
            self.addCleanup(patch.stop)
        self.clock = [100.0]
        patch = mock.patch.object(parallel_sessions.time, "monotonic", lambda: self.clock[0])
        patch.start()
        self.addCleanup(patch.stop)
        self.executor = SimpleNamespace(studio_context=SimpleNamespace(state={"id": "w1"}), part_layout=None)

    def save(self):
        self.clock[0] += 1
        parallel_sessions._part_saved(self.executor)

    def report(self, done):
        self.clock[0] += 1
        parallel_sessions.note_progress("w1", done, STEPS)

    def test_a_count_shows_until_work_is_saved_after_it(self):
        self.report(0)
        self.assertEqual(parallel_sessions.progress_of("w1"), (0, 3))
        self.assertEqual(parallel_sessions.current_step("w1"), "Block out")
        self.save()   # It built the part without reporting: "0 of 3" would be behind what the tile shows.
        self.assertIsNone(parallel_sessions.progress_of("w1"))
        self.assertEqual(parallel_sessions.current_step("w1"), "")
        self.report(2)   # It caught up.
        self.assertEqual(parallel_sessions.progress_of("w1"), (2, 3))
        self.assertEqual(parallel_sessions.current_step("w1"), "Detail")

    def test_a_plan_reported_all_done_stays_whatever_is_saved_after(self):
        self.report(3)
        self.save()   # Finishing touches after its last step: still done (its tile can say Done).
        self.assertEqual(parallel_sessions.progress_of("w1"), (3, 3))

    def test_a_save_before_any_plan_shows_nothing_and_breaks_nothing(self):
        self.save()
        self.assertIsNone(parallel_sessions.progress_of("w1"))
        self.report(1)
        self.assertEqual(parallel_sessions.progress_of("w1"), (1, 3))


if __name__ == "__main__":
    unittest.main()
