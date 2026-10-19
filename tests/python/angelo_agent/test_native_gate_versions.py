# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of the gate sets' versions: what version 2 changes, what it may not, and how a run states it.

Version 1 is the gate as it was recorded; version 2 reads the questions'
answers. The edits must be byte-identical between them, so the figures of the
edits stay comparable across the line, and every recorded result must say
which version it is. ``test_blender_gate_answers.py`` walks the version-2 long
set in the real application.
"""

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_answers as answers  # noqa: E402
import eval_gate  # noqa: E402
import eval_grading as grading  # noqa: E402
import eval_grading_live  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import survey_fixtures  # noqa: E402
import survey_truth  # noqa: E402

OLD_OPENING = ("How many mesh objects are in the scene, and what are their names? Answer in one short sentence. "
               "Do not change anything.")
OLD_CUBE = ("What are the current dimensions and location of 'Eval Cube'? Answer in one short sentence. "
            "Do not change anything.")
SETS = (("default", 40), ("long", 40), ("long", 120), ("long", 7))


class VersionTests(unittest.TestCase):
    def test_the_digests_of_both_versions_are_the_recorded_ones(self):
        self.assertEqual((eval_gate.VERSION, eval_gate.VERSIONS, eval_gate.SETS), (2, (1, 2), ("default", "long")))
        self.assertEqual(grading.GATE_DIGESTS, {1: {"default": "ab9e568a361c7c6c", "long": "a4ea35af28319d93"},
                                                2: {"default": "962e64ea09e53e92", "long": "94ca4135caaabc3a"}})
        self.assertEqual(grading.tasks_digest(live.tasks_for("long", 120, 1)), "74bfe637dcb8a5e4")

    def test_every_edit_is_byte_identical_between_the_versions(self):
        for name, count in SETS:
            with self.subTest(task_set=name, count=count):
                old, new = live.tasks_for(name, count, 1), live.tasks_for(name, count, 2)
                self.assertEqual([task["id"] for task in old], [task["id"] for task in new])
                edits = [(a, b) for a, b in zip(old, new) if a["kind"] != "read_only"]
                self.assertEqual(len(edits), len(old) - sum(task["kind"] == "read_only" for task in old))
                for before, after in edits:
                    self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))
                self.assertEqual(live.tasks_for(name, count), new, "the current version is the default")

    def test_a_question_keeps_its_wording_and_gains_one_sentence_an_answer_and_its_version(self):
        for name, count in SETS:
            for before, after in zip(live.tasks_for(name, count, 1), live.tasks_for(name, count, 2)):
                if before["kind"] != "read_only":
                    continue
                with self.subTest(task_set=name, task=before["id"]):
                    opening = before["source"] == eval_gate.OPENING
                    form = "mesh_names" if opening else "box"
                    self.assertEqual(before["prompt"], OLD_OPENING if opening else OLD_CUBE)
                    self.assertEqual(after["prompt"], before["prompt"] + " " + answers.FORMS[form][1])
                    self.assertEqual(set(after) - set(before), {"answer", "gate_version", "fixtures"})
                    self.assertEqual({key: after[key] for key in before if key != "prompt"},
                                     {key: before[key] for key in before if key != "prompt"})
                    self.assertEqual((after["gate_version"], after["answer"]["form"]), (2, form))
                    self.assertIn(after["answer"]["truth"]["kind"], survey_truth.TRUTHS)
                    self.assertNotIn("evidence", after["answer"], "neither question needs an image")
                    self.assertNotIn("pair", after["answer"], "neither is a yes/no question")
                    for fixture in after.get("fixtures", ()):
                        self.assertIn(fixture["kind"], survey_fixtures.TRANSIENT, "undone after the turn")
        opening = live.tasks_for("default")[0]
        self.assertEqual(opening["fixtures"], [{"kind": "probe_object", "name": eval_gate.PROBE_MESH, "mesh": True}])
        self.assertEqual(opening["answer"]["truth"], {"kind": "object_names", "type": "MESH"})
        cube = live.tasks_for("long", 7)[-1]
        self.assertEqual(cube["answer"]["truth"], {"kind": "dimensions_location", "object": live.EVAL_OBJECT})
        # Where the chat's own edits put the cube could be recalled: it is asked with the cube moved for the turn.
        self.assertEqual(cube["fixtures"], [{"kind": "object_offset", "object": live.EVAL_OBJECT,
                                             "location": [0.25, -0.5, 0.75]}])

    def test_the_cube_question_s_answer_differs_from_the_cycle_before(self):
        asked, tasks = [], live.tasks_for("long", 120)
        for index, task in enumerate(tasks):
            if task["source"] == eval_gate.CUBE:
                earlier = [check for other in tasks[:index] for check in other["checks"]]
                asked.append(([check["expected"] for check in earlier if check["kind"] == "dimensions"][-1],
                              [check["expected"] for check in earlier if check["kind"] == "transform"][-1]))
        self.assertEqual(len(asked), 23)
        self.assertTrue(all(a != b for a, b in zip(asked, asked[1:])), "a repeated answer could be right twice")
        self.assertNotIn(([3, 0.25, 5], [6, -4, 2.5]), asked, "the prompt's example is never the answer")

    def test_versioning_is_idempotent_and_refuses_an_unknown_version(self):
        tasks = live.tasks_for("long", 12)
        self.assertEqual(eval_gate.versioned(tasks, 2, live.EVAL_OBJECT), tasks)
        self.assertEqual(eval_gate.versioned(live.tasks_for("long", 12, 1), None, live.EVAL_OBJECT), tasks)
        with self.assertRaises(ValueError):
            live.tasks_for("default", version=3)
        self.assertEqual([eval_gate.tasks_version(live.tasks_for("default", version=v)) for v in (1, 2)], [1, 2])
        self.assertEqual(eval_gate.tasks_version(live.tasks_for("delegation")), 1)

    def test_other_sets_are_not_versioned_gate_sets(self):
        self.assertEqual(grading.tasks_digest(live.tasks_for("delegation")), "c2885fd623893da1")
        self.assertEqual(grading.tasks_digest(live.tasks_for("survey")), "0ffe0390d1394f22")
        for name in ("delegation", "coverage", "survey", "survey-questions"):
            self.assertIsNone(eval_gate.set_version(name, live.tasks_for(name)), name)
            self.assertNotIn("gate_version", grading.stamp(name, live.tasks_for(name)))
        # The coverage set opens with the gate's own question, as asked now.
        self.assertEqual(live.tasks_for("coverage")[0], live.tasks_for("default")[0])
        self.assertEqual([task["id"] for task in live.tasks_for("survey-questions")], [
            "scene_overview", "create_cube", "extrude_top", "mesh_stats", "how_to_bevel", "render_info",
            "view_check", "view_check_cropped"])
        self.assertEqual([task for task in live.tasks_for("survey") if task["id"] in set(
            task["id"] for task in live.tasks_for("survey-questions"))], live.tasks_for("survey-questions"))
        self.assertEqual(grading.stamp("survey-questions", live.tasks_for("survey-questions"))["survey_version"], 2)


class RecordedVersionTests(unittest.TestCase):
    def test_a_trial_states_its_version_and_a_result_without_one_is_version_1(self):
        new = grading.stamp("default", live.tasks_for("default"))
        old = grading.stamp("long", live.tasks_for("long", 40, 1))
        self.assertEqual((new["gate_version"], new["tasks_digest"]), (2, grading.GATE_DIGESTS[2]["default"]))
        self.assertEqual((old["gate_version"], old["tasks_digest"]), (1, grading.GATE_DIGESTS[1]["long"]))
        self.assertEqual([eval_gate.stamp_version(stamp) for stamp in (new, old, {}, None, {"contract": 2})],
                         [2, 1, 1, 1, 1])
        records = ({"gate_version": 2}, {}, {"gate_version": None})
        self.assertEqual([eval_gate.record_version(record) for record in records], [2, 1, 1])

    def test_versions_are_comparable_only_with_themselves(self):
        self.assertTrue(eval_gate.comparable([]) and eval_gate.comparable([2, 2]) and eval_gate.comparable({1}))
        self.assertFalse(eval_gate.comparable([1, 2]))
        self.assertIn("(version 1, version 2)", eval_gate.NOT_COMPARABLE % eval_gate.describe({2, 1}))
        self.assertIn("without reading its answer", eval_gate.NOT_COMPARABLE)


class Objects(list):
    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


class GateQuestionGradingTests(unittest.TestCase):
    """A version-2 gate question through the trial driver's grading steps, with Blender's part patched in."""

    def setUp(self):
        self.tasks, self.report = live.tasks_for("long", 7), {"tasks": []}
        self.gate = eval_grading_live.LiveGrading("long", self.tasks, self.report, None, None)
        self.scene = mock.Mock(objects=Objects())
        self.restored = []

    def ask(self, task, reply, truth, unchanged=True):
        record = {"task": task["id"], "kind": "read_only", "status": "completed"}
        apply = lambda bpy, scene, fixture: (["fixture:" + fixture["kind"]],
                                             lambda: self.restored.append(fixture["kind"]))
        with mock.patch.object(survey_fixtures, "apply", apply), \
                mock.patch.object(survey_truth, "truth", lambda bpy, scene, spec: copy.deepcopy(truth)):
            self.gate.before(None, self.scene, task, record)
            record["host_check"] = {"scene_unchanged": unchanged}
            record["host_check_passed"] = self.gate.after(None, self.scene, task, record, unchanged,
                                                          [{"role": "assistant", "text": reply}])
            self.report["tasks"].append(record)
            self.gate.complete(None, self.scene, task, record)
        answer = record["host_check"]["answer"]
        return answer["outcome"], answer.get("parsed"), record["host_check_passed"], record["grade"]

    def test_the_opening_question_passes_only_on_the_meshes_the_scene_holds(self):
        task, truth = self.tasks[0], {"accept": [["Brass Marker", "Cube"]]}
        cases = {"Two meshes.\nMESHES: Cube, Brass Marker": ("right", "exactly", True, "passed"),
                 "MESHES: 2 (Brass Marker and Cube)": ("right", "leniently", True, "passed"),
                 "One mesh.\nMESHES: Cube": ("wrong", "exactly", False, "failed"),
                 "There are two mesh objects: Cube and Brass Marker.": ("no_answer", None, False, "failed"),
                 "I cannot see the scene.": ("no_answer", None, False, "failed"), "": ("empty", None, False, "failed")}
        for reply, expected in cases.items():
            with self.subTest(reply=reply):
                self.assertEqual(self.ask(task, reply, truth), expected)
        self.assertEqual(self.ask(task, "MESHES: Cube, Brass Marker", truth, unchanged=False),
                         ("right", "exactly", False, "failed"), "a right answer does not excuse a changed scene")
        self.assertEqual(self.restored, ["probe_object"] * 7, "the extra mesh is removed after every turn")
        self.assertEqual(self.report["grading"]["answers"], {"read": 4, "parsed_leniently": 1})
        self.assertEqual(self.report["grading"]["gate_version"], 2)

    def test_the_cube_question_passes_on_six_numbers_within_half_a_centimetre(self):
        task, truth = self.tasks[-1], {"accept": [[1.0, 2.0, 1.0, -2.0, -1.0, 0.0]], "detail": {"object": "Eval Cube"}}
        cases = {"DIMENSIONS: 1 2 1; LOCATION: -2 -1 0": ("right", "exactly", True, "passed"),
                 "DIMENSIONS: 1.00 2.004 1; LOCATION: -2.0 -1 0.0": ("right", "exactly", True, "passed"),
                 "It is 1 x 2 x 1 m.\nDIMENSIONS: 1 m x 2 m x 1 m\nLOCATION: (-2, -1, 0)": (
                     "right", "leniently", True, "passed"),
                 "DIMENSIONS: 1 2 1; LOCATION: -2 -1 0.02": ("wrong", "exactly", False, "failed"),
                 "DIMENSIONS: 2 1 1; LOCATION: -2 -1 0": ("wrong", "exactly", False, "failed"),
                 "DIMENSIONS: 3 0.25 5; LOCATION: 6 -4 2.5": ("wrong", "exactly", False, "failed"),
                 "DIMENSIONS: 1 2 1": ("no_answer", None, False, "failed"),
                 "The cube is 1 by 2 by 1 m at (-2, -1, 0).": ("no_answer", None, False, "failed")}
        for reply, expected in cases.items():
            with self.subTest(reply=reply):
                self.assertEqual(self.ask(task, reply, truth), expected)
        self.assertEqual(self.ask(task, "DIMENSIONS: 1 2 1; LOCATION: -2 -1 0", {"ungraded": "the object is missing"}),
                         ("ungraded", None, None, "ungraded"), "no truth: never passed")
        self.assertEqual(self.restored, ["object_offset"] * 9, "the cube is put back after every turn")


if __name__ == "__main__":
    unittest.main()
