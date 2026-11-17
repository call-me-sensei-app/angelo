# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``start_record_checks``: a completed start leaves no record of its own, and any other process's record left is
marked found (a crashed process still showing its crash dialog keeps its record open: the 1.1 L5 run with
--launch-while-dialog-open, 2026-11-12). Host only, every platform."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/modules"))
import angelo_safe_start as safe  # noqa: E402
import start_record_checks  # noqa: E402

ME, CRASHED = 1234, 999


class RecordCheckTests(unittest.TestCase):
    def folder(self, *names):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        for name in names:
            (folder / name).write_bytes(b"")
        return folder

    def judged(self, *names):
        left = start_record_checks.leftover(self.folder(*names), pid=ME)
        return left, start_record_checks.checks(left)

    def test_nothing_left_passes(self):
        left, checks = self.judged()
        self.assertEqual(left, {"mine": [], "others": {}})
        self.assertTrue(all(checks.values()))

    def test_a_crashed_process_s_record_marked_found_passes(self):
        """Its process still holds it under the open dialog: found, marked, not removable yet."""
        record = "%d-abc%s" % (CRASHED, safe.SUFFIX)
        left, checks = self.judged(record, record + safe.FOUND_MARK, record + safe.CRASHED_MARK)
        self.assertEqual(left["others"], {record: [safe.FOUND_MARK, safe.CRASHED_MARK]})
        self.assertTrue(all(checks.values()), checks)

    def test_its_own_record_left_fails(self):
        _, checks = self.judged("%d-abc%s" % (ME, safe.SUFFIX))
        self.assertFalse(checks["this_start_completed"])

    def test_another_record_not_marked_found_fails(self):
        """A record no launch took (a missed crash would be found again): never passed as someone else's."""
        record = "%d-abc%s" % (CRASHED, safe.SUFFIX)
        _, checks = self.judged(record, record + safe.CRASHED_MARK)
        self.assertTrue(checks["this_start_completed"])
        self.assertFalse(checks["no_other_start_left_to_be_found_again"])

    def test_a_pid_prefix_is_not_mistaken_for_its_own(self):
        _, checks = self.judged("%d5-abc%s" % (ME, safe.SUFFIX))   # 12345-..., not 1234-...
        self.assertTrue(checks["this_start_completed"])


class SourceTests(unittest.TestCase):
    def test_the_crash_start_phases_judge_records_through_it(self):
        """No phase asserts on a bare count of the folder's records (which a crashed instance's may be in)."""
        for name in ("update_carry_over_phases.py", "settings_import_crash_phases.py"):
            with self.subTest(name):
                text = (HERE / name).read_text(encoding="utf-8")
                self.assertIn("start_record_checks.record(", text)
                self.assertNotIn("start_records() == []", text)
                ast.parse(text)


if __name__ == "__main__":
    unittest.main()
