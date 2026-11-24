# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first-hour case registry (``eval_firsthour_tasks``): sources, splits, messages and wiring. No Blender.

``test_blender_firsthour_cases`` proves in Blender that each start fails its checks and each reference fix passes.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_firsthour_tasks as cases  # noqa: E402
import eval_grading  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import scenario_session  # noqa: E402

# A message is the person's own words: what they want, never how, and nothing about the harness or the agent.
HOW = re.compile(r"(?i)\b(auto normali[sz]e|proportional|normals?|lock(ed)? weight|select mode|hide_select|outliner|"
                 r"transform_apply|apply scale|origin|samples|denois|device|resolution|shading|material preview|"
                 r"camera object|python|script|tool|angelo|agent)\b")


class RegistryTests(unittest.TestCase):
    def test_ids_are_unique_and_every_case_names_a_source(self):
        ids = [c["id"] for c in cases.CASES]
        self.assertEqual(len(ids), len(set(ids)))
        for c in cases.CASES:
            with self.subTest(case=c["id"]):
                self.assertTrue(re.search(r"\w+\.\w+", c["source"]), "a public source")
                self.assertIn(c["split"], (cases.DEV, cases.HELDOUT))

    def test_held_out_is_separate_from_dev_and_larger(self):
        dev, held = cases.cases("firsthour-dev"), cases.cases("firsthour-heldout")
        self.assertTrue({c["id"] for c in dev}.isdisjoint(c["id"] for c in held))
        self.assertGreater(len(held), len(dev))
        self.assertEqual(len(cases.cases("firsthour")), len(dev) + len(held))

    def test_held_out_cases_come_from_sources_other_than_the_launch_thread(self):
        for c in cases.cases("firsthour-heldout"):
            with self.subTest(case=c["id"]):
                self.assertNotIn("x.com", c["source"])

    def test_messages_say_what_the_person_wants_never_how(self):
        for c in cases.CASES:
            with self.subTest(case=c["id"]):
                self.assertIsNone(HOW.search(c["prompt"]), c["prompt"])

    def test_messages_are_paraphrased_and_short(self):
        for c in cases.CASES:
            with self.subTest(case=c["id"]):
                self.assertLess(len(c["prompt"]), 220)


class WiringTests(unittest.TestCase):
    def test_each_case_is_its_own_task_set_with_one_graded_task(self):
        for name in cases.TASK_SETS:
            with self.subTest(task_set=name):
                self.assertIn(name, live.TASK_SETS)
                tasks = live.tasks_for(name)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(eval_grading.basis(tasks[0]), "firsthour_checks")
                self.assertIsNone(eval_grading.ungraded_reason(tasks[0]))
                self.assertEqual(scenario_session.grading_route(tasks[0], False), "firsthour")

    def test_the_groups_are_not_task_sets(self):
        for group in cases.GROUPS:
            self.assertNotIn(group, live.TASK_SETS)
        with self.assertRaises(ValueError):
            cases.cases("firsthour-everything")


class TableTests(unittest.TestCase):
    """``firsthour_run.tabulate`` counts only finished records (eval_send_record): a record written at a send, or
    between trials, is a run that has not ended, shown as such and never counted."""

    def arm(self, root, label, records):
        folder = Path(root) / label
        folder.mkdir()
        (folder / "arm.json").write_text(json.dumps({"label": label}))
        for case_id, record in records.items():
            (folder / case_id).mkdir()
            if record is not None:
                (folder / case_id / "eval-live-agent.json").write_text(json.dumps(record))
        return folder

    def test_an_unfinished_run_is_shown_and_never_counted(self):
        import firsthour_run
        done = {"trials": [{"tasks": [{"trial": 1, "grade": "passed", "host_check": {"checks": {"a": True}}}]}]}
        sent = dict(done, in_progress=True)   # Written at the send: the trial had not ended.
        first, second, third = (c["id"] for c in cases.CASES[:3])
        with tempfile.TemporaryDirectory() as root:
            table = firsthour_run.tabulate([self.arm(root, "arm", {first: done, second: sent, third: None})])
        rows = {line.split("|")[1].strip(): line for line in table.splitlines()[2:]}
        self.assertIn("| 1/1 |", rows[first[len("firsthour_"):]])
        self.assertIn("| unfinished |", rows[second[len("firsthour_"):]])
        self.assertIn("| unfinished |", rows[third[len("firsthour_"):]])   # A folder with no record yet.
        self.assertIn("1/1 (+0 invalid, 2 unfinished)", rows["**total**"])


if __name__ == "__main__":
    unittest.main()
