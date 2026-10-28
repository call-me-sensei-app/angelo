# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""BENCH-PREREG, "Benchmark of record": the maintainer's words, "Benchmark of record: rerun the Sol 1-worker
benchmark on C2, the shipping build; C1's run is an early verdict only.", then "Benchmark grader: keep the
preregistered arm-blind Opus 5.5 grade for C3's run of record only; C1 and C2 early runs hold their grades (not
graded)." (the shipping build became C3 when the rail setting went in).

Pinned here: the runner journals what a run is (``--record early`` for C1 and C2, ``of-record`` for C3) and never
mixes two in one run; the report states it above the verdict, and an unmarked run reads as not of record. What a run is
never changes the verdict: the same frozen rules decide every run. An early run without grades says its grade is held,
never that grades are missing.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bench_parallel_report as report
import bench_parallel_vs_single as runner
import test_native_bench_messages as messages   # Its DECIDED fields.
import test_native_bench_parallel as verdicts   # Its run fixture; its tests run there.
import test_native_bench_parallel_runs as runs

WALLS = {"single": [600, 620], "parallel": [300, 320]}


class RecordTests(unittest.TestCase):
    def test_the_runner_journals_what_the_run_is_and_never_mixes_two(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(runs.run(folder, runs.FakeBench(), 1, "--record", "early"), 0)
            self.assertEqual(runs.journal(folder)["record"], "early")
            launched = mock.Mock(side_effect=AssertionError("launched"))
            self.assertEqual(runs.run(folder, launched, 1, "--record", "of-record"), runner.REFUSED)
            self.assertEqual(runs.run(folder, launched, 1), runner.REFUSED)   # Unmarked is another run too.
        with self.assertRaises(SystemExit):
            runner.parse(["--authorize-billed-runs", "--task-set", "t", "--output", "/r", "--record", "final"])

    def test_the_report_says_what_the_run_is_and_the_verdict_never_changes(self):
        fixture = verdicts.VerdictTests()
        folder = fixture.run_folder(WALLS, {"single": {}, "parallel": {}})
        plan = Path(folder) / "interleave.json"
        results = {}
        for record in ("early", "of-record", None):
            value = json.loads(plan.read_text())
            value.pop("record", None)
            plan.write_text(json.dumps(dict(value, **({"record": record} if record else {}))))
            results[record] = report.verdict(folder)
        lines = {record: report.markdown(result) for record, result in results.items()}
        self.assertIn("**Record: EARLY VERDICT, not of record: the 1.0.6 claim and the ship rule read the shipping build's run.**",
                      lines["early"])
        self.assertIn("**Record: the run of record (the shipping build).**", lines["of-record"])
        self.assertIn("**Record: NOT MARKED (no --record), so not of record.**", lines[None])
        decided = messages.DECIDED + ("ships", "ship_rule_holds", "complete", "missing")
        for record in ("of-record", None):
            self.assertEqual({key: results[record][key] for key in decided},
                             {key: results["early"][key] for key in decided})
        plan.write_text("{")   # Unreadable: unmarked, never of record.
        self.assertIsNone(report.verdict(folder)["record"])

    def test_an_early_run_holds_its_grade_and_says_so(self):
        folder = Path(verdicts.VerdictTests().run_folder(WALLS, {"single": {}, "parallel": {}}))
        (folder / "grades.json").unlink()   # Not graded: the maintainer's rule for C1's and C2's early runs.
        plan = folder / "interleave.json"
        for record in ("early", "of-record", None):
            value = json.loads(plan.read_text())
            value.pop("record", None)
            plan.write_text(json.dumps(dict(value, **({"record": record} if record else {}))))
            result = report.verdict(folder)
            grade_gaps = [item for item in result["missing"] if "grade of" in item]
            # Held only in an early run; a run of record (or an unmarked one) without grades still misses them.
            self.assertEqual((result["grade_held"], bool(grade_gaps)), (record == "early", record != "early"), record)
        plan.write_text(json.dumps(dict(json.loads(plan.read_text()), record="early")))
        text = report.markdown(report.verdict(folder))
        self.assertIn("**Verdict: grade held: early verdict, not graded**", text)
        self.assertIn("Ship rule: not read (grade held).", text)

if __name__ == "__main__":
    unittest.main()
