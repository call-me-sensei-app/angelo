# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Original grading semantics survive report rebuilding, and different parsers are never silently pooled."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
for path in (HERE, HERE.parents[2] / "scripts/addons_core"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import answer_forms_report
import eval_grading as grading
import eval_live_agent
import eval_survey_tasks
import survey_report


class GradingProvenanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.tasks = eval_survey_tasks.build_survey_tasks()

    def trial(self, contract):
        stamp = dict(grading.stamp("survey", self.tasks), contract=contract)
        return {"grading": stamp, "tasks": [{
            "task": "scene_overview", "kind": "read_only", "status": "completed",
            "host_check_passed": True, "grade": "passed",
            "host_check": {"answer": {"form": "names", "outcome": "right", "parsed": "exactly",
                                       "stated": "Camera, Cube, Light"}}}]}

    def write(self, contract):
        root = self.root / str(contract)
        root.mkdir()
        (root / "eval-live-agent.json").write_text(json.dumps({"trials": [self.trial(contract)]}))
        return str(root)

    def test_live_aggregation_preserves_an_old_contract_and_marks_mixed_contracts(self):
        plan = {"grading": self.trial(3)["grading"], "tasks_per_trial": len(self.tasks)}
        report = eval_live_agent.aggregate(plan, {}, [self.trial(3)])
        self.assertEqual(report["grading"]["contract"], 3)
        report = eval_live_agent.aggregate(plan, {}, [self.trial(3), self.trial(4)])
        self.assertIsNone(report["grading"]["contract"])
        self.assertEqual(report["grading"]["contracts"], [3, 4])

    def test_survey_report_preserves_an_old_contract_and_refuses_mixed_ones(self):
        old, new = self.write(3), self.write(4)
        self.assertEqual(survey_report.build_report([old])["grading"]["contract"], 3)
        mixed = survey_report.build_report([old, new])
        self.assertIsNone(mixed["grading"]["contract"])
        self.assertEqual(mixed["grading"]["contracts"], [3, 4])
        self.assertIn("different grading contracts (3, 4)", survey_report.markdown(mixed))
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(survey_report.main([old, new]), 2)
        self.assertIn("different grading contracts (3, 4)", errors.getvalue())
        self.assertFalse((Path(old) / survey_report.REPORT_JSON).exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(survey_report.main([old, new, "--mixed-versions"]), 0)
        self.assertIn("different grading contracts (3, 4)", (Path(old) / survey_report.REPORT_MD).read_text())

    def test_answer_form_report_preserves_and_checks_the_contract(self):
        old, new = self.write(3), self.write(4)
        old_runs, new_runs = answer_forms_report.question_runs(old), answer_forms_report.question_runs(new)
        self.assertEqual(old_runs[0]["grading_contract"], 3)
        self.assertIn("Grading contract: 3.", answer_forms_report.text(old_runs))
        with self.assertRaisesRegex(ValueError, "different grading contracts"):
            answer_forms_report.totals(old_runs + new_runs)
        with self.assertRaisesRegex(ValueError, "different grading contracts"):
            answer_forms_report.text(old_runs + new_runs)
        self.assertIn("different grading contracts (3, 4)", answer_forms_report.text(old_runs + new_runs, True))
        errors, printed = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(printed):
            self.assertEqual(answer_forms_report.main([old, new]), 2)
            self.assertEqual(answer_forms_report.main([old, new, "--mixed-versions"]), 0)
        self.assertIn("different grading contracts (3, 4)", errors.getvalue())
        self.assertIn("different grading contracts (3, 4)", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
