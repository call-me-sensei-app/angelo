# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release stages bring back every native suite run that did not pass, variant runs included.

R6 (the 1.0.6 rehearsal at e837c972c40): both Windows stages took failed suites' names out of ``summary.txt`` with
``(test_blender_\\w+)``, which stops at the ``@`` of a ``<suite>@<variant>`` run, so none of the 21 failed variant runs'
folders came back; and only ``FAILED`` lines counted, never a crashed, timed-out or errored run. The stages now read
the runner's ``summary.json``, and no shipped tooling parses a suite name out of text.
"""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import native_suite_manifest as manifest  # noqa: E402
from native_suite_summary import STATUS_ORDER, summarize  # noqa: E402

STAGES = ("scripts/angelo/windows_agent_validate.ps1", "scripts/angelo/windows_release_validate.ps1")
TOOLING = ROOT / "scripts/angelo"


class FailedSuiteUploadTests(unittest.TestCase):
    def test_no_shipped_tooling_parses_a_suite_name_out_of_text(self):
        """A pattern naming ``test_blender_`` is a name parsed out of a log or summary line: the class R6 found."""
        found = []
        for path in sorted(TOOLING.rglob("*")):
            if path.suffix not in (".ps1", ".sh", ".py", ".cmake") or not path.is_file():
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if "test_blender_" in line and re.search(r"-match\b|\bre\.\w+\(|\[regex\]|grep|sed ", line):
                    found.append("%s:%d" % (path.relative_to(ROOT), number))
        self.assertEqual(found, [])

    def test_each_stage_collects_every_run_that_did_not_pass_from_summary_json(self):
        for name in STAGES:
            with self.subTest(stage=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn('Join-Path $summary.Directory.FullName "summary.json"', text)
                self.assertIn("ConvertFrom-Json).suites", text)
                self.assertIn('$run.status -ne "passed"', text)
                self.assertIn('("native-failed\\" + $run.name)', text)
                self.assertIn("Join-Path $summary.Directory.FullName $run.name", text)
                self.assertNotIn("$Matches[1]", text[text.index("summary.json"):])

    def test_summary_json_names_every_declared_run_as_its_folder_is_named(self):
        """What the stages read: each run's ``name`` (``<suite>@<variant>`` for a variant) and lower-case status."""
        runs = manifest.expand(manifest.SUITES)
        self.assertTrue(any("@" in run.name for run in runs), "the manifest declares variant runs")
        results = [{"name": run.name, "status": STATUS_ORDER[index % len(STATUS_ORDER)], "duration_seconds": 1.0,
                    "mode": "background", "reasons": []} for index, run in enumerate(runs)]
        summary = summarize(results, [], {"jobs": 1, "output": "out"})
        self.assertEqual([row["name"] for row in summary["suites"]], [run.name for run in runs])
        self.assertTrue(all(row["status"] in STATUS_ORDER for row in summary["suites"]))
        self.assertIn("passed", STATUS_ORDER)


if __name__ == "__main__":
    unittest.main()
