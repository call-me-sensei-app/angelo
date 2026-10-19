# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of the trial driver's grading steps (``eval_grading_live.LiveGrading``) and their wiring.

The steps are run here on scripted chat rows and a stand-in scene, with ground
truth and fixtures patched in, so every outcome a question or an ungraded edit
can have is asserted without Blender or a model. ``test_blender_survey_tasks.py``
runs the same steps in the real application; ``test_native_grading_contract.py``
holds the contract itself, the guard and the frozen gate sets.
"""

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_answers as answers  # noqa: E402
import eval_grading as grading  # noqa: E402
import eval_grading_live  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_survey_tasks as survey  # noqa: E402
import survey_fixtures  # noqa: E402
import survey_truth  # noqa: E402

IMAGE = {"role": "activity", "tool": "angelo_view", "status": "completed",
         "result": {"views": [{"bytes": 512, "camera": {"source": answers.SCENE_CAMERA}}]}}


def said(text):
    return {"role": "assistant", "text": text}


# The receipt path of a turn whose scene-camera view reached the model (test_native_visual_answers holds the rest).
DELIVERED = {"edits": 0, "images_delivered": 1, "images": [[2, 0, {"view:SOLID:SCENE_CAMERA": 1}]],
             "image_delivery": "confirmed"}


def question_record(task, unchanged=True):
    """A question's record as the driver has it when it calls ``LiveGrading.after``."""
    return {"task": task["id"], "kind": "read_only", "status": "completed",
            "host_check": {"scene_unchanged": unchanged}, "receipt": {"path": dict(DELIVERED)}}


class Objects(list):
    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


class Scene:
    def __init__(self, *names):
        self.objects = Objects(mock.Mock(type="MESH") for _ in names)
        for obj, name in zip(self.objects, names):
            obj.name = name


class LiveGradingTests(unittest.TestCase):
    """The driver's grading steps on scripted rows: what each kind of task can come out as."""

    def setUp(self):
        self.tasks = survey.build_survey_tasks()
        self.by_id = {task["id"]: task for task in self.tasks}
        self.report = {"tasks": []}
        self.live = eval_grading_live.LiveGrading("survey", self.tasks, self.report, lambda checks, obj: checks,
                                                  lambda scene, obj: {})
        self.scene = Scene("Eval Cube", "Camera", "Light")
        self.restored = []

    def run_task(self, task, rows=(), passed=True, truth=None, fixture_error=None, held_before=False):
        """before, the driver's own result (``passed``), after and complete, with Blender's part patched in."""
        def apply(bpy, scene, fixture):
            if fixture_error:
                raise survey_fixtures.FixtureError(fixture_error)
            return ["fixture:" + fixture["kind"]], lambda: self.restored.append(fixture["kind"])
        record = {"task": task["id"], "kind": task["kind"], "status": "completed", "receipt": {"path": dict(DELIVERED)}}
        with mock.patch.object(survey_fixtures, "apply", apply), \
                mock.patch.object(survey_truth, "truth", lambda bpy, scene, spec: copy.deepcopy(truth)), \
                mock.patch.object(eval_grading_live.LiveGrading, "edit_holds", lambda *args: held_before):
            self.live.before(None, self.scene, task, record)
            if task["kind"] == "read_only":
                record["host_check"] = {"scene_unchanged": passed}
            elif not task["checks"]:
                record["host_check"], passed = {"ungraded": True}, None
            else:
                record["host_check"] = {"success": passed, "status": "pass" if passed else "fail"}
            record["host_check_passed"] = self.live.after(None, self.scene, task, record, passed, list(rows))
            self.report["tasks"].append(record)
            self.live.complete(None, self.scene, task, record)
        return record

    def outcome(self, record):
        return record["host_check"]["answer"]["outcome"], record["host_check_passed"], record["grade"]

    def test_a_visual_question_passes_only_on_the_right_answer_with_its_observation(self):
        task, truth = self.by_id["view_check"], {"accept": ["yes"], "detail": {"margin": 0.25}}
        cases = {"empty": [said("")], "no_answer": [IMAGE, said("I cannot see the scene.")],
                 "wrong": [IMAGE, said("ANSWER: no")], "unsupported": [said("ANSWER: yes")],
                 "right": [IMAGE, said("It is all in view.\nANSWER: yes")]}
        for expected, rows in cases.items():
            with self.subTest(case=expected):
                self.live.pairs.clear()
                record = self.run_task(task, rows, truth=truth)
                self.assertEqual(record["host_check"]["answer"]["truth"], {"margin": 0.25})
                self.assertEqual(record["harness_fixture"], ["fixture:camera_frame"])
                if expected != "right":
                    self.assertEqual(self.outcome(record), (expected, False, "failed"))
                    continue
                # Right, and held back until the other variant is graded too.
                self.assertEqual(self.outcome(record), ("ungraded", None, "ungraded"))
                self.run_task(self.by_id["view_check_cropped"], [IMAGE, said("ANSWER: no")], truth={"accept": ["no"]})
                self.assertEqual(self.outcome(record), ("right", True, "passed"))
        self.assertEqual(self.restored, ["camera_frame"] * 6, "the transient fixture is undone after every run")
        self.assertEqual(record["evidence"]["current_scene_camera"], 1)
        self.assertEqual(self.report["grading"]["totals"],
                         {"passed": 2, "failed": 4, "inconclusive": 0,
                          "ungraded": 0, "skipped": 0, "total": 6, "not_run": 59})

    def test_the_driver_watches_only_a_visual_question_while_it_runs(self):
        task, messages = self.by_id["view_check"], [{"role": "user", "text": "earlier turn"}]
        with mock.patch.object(survey_fixtures, "apply", lambda *args: (["fixture:camera_frame"], None)), \
                mock.patch.object(survey_truth, "truth", lambda *args: {"accept": ["yes"]}):
            record = question_record(task)
            self.live.before(None, self.scene, task, record)
            # One reply row, rewritten after the look, as the Claude runtime writes a turn.
            for turn in ([said("Let me look.")], [said("Let me look."), IMAGE], [said("ANSWER: yes"), IMAGE]):
                self.live.observe(messages + turn, 1)
            passed = self.live.after(None, self.scene, task, record, True, [said("ANSWER: yes"), IMAGE])
        self.assertIsNone(passed, "right and supported, and held until the other variant is graded")
        self.assertEqual((record["host_check"]["answer"]["held"], record["host_check"]["answer"]["supported"]),
                         ("right", True))
        self.assertEqual(record["host_check"]["answer"]["evidence_timing"], "sampled while the turn ran")
        self.live.pairs.clear()
        # A watch that breaks cannot time the answer: the question is ungraded, with the reason, not failed.
        with mock.patch.object(survey_fixtures, "apply", lambda *args: ([], None)), \
                mock.patch.object(survey_truth, "truth", lambda *args: {"accept": ["yes"]}), \
                mock.patch.object(answers.Watch, "observe", side_effect=RuntimeError("boom")):
            record = question_record(task)
            self.live.before(None, self.scene, task, record)
            self.live.observe(messages + [said("ANSWER: yes")], 1)
            self.assertIsNone(self.live.after(None, self.scene, task, record, True, [IMAGE, said("ANSWER: yes")]))
        self.assertIn("the evidence watch failed (RuntimeError: boom)", record["host_check"]["answer"]["reason"])
        self.live.before(None, self.scene, self.by_id["mesh_stats"], {})
        self.assertNotIn("watch", self.live.turn, "a question that needs no image is not sampled")
        self.live.observe(messages, 1)
        gate = eval_grading_live.LiveGrading("default", live.tasks_for("default"), {"tasks": []}, None, None)
        gate.before(None, self.scene, live.tasks_for("default")[1], {})
        gate.observe(None, 0)   # Nothing is read: a gate edit's tick costs one dictionary lookup.
        self.assertEqual(gate.turn, {"restore": [], "names": {"Eval Cube", "Camera", "Light"}})
        with mock.patch.object(survey_fixtures, "apply", lambda *args: (["fixture:probe_object"], None)), \
                mock.patch.object(survey_truth, "truth", lambda *args: {"accept": [["Cube"]]}):
            gate.before(None, self.scene, live.tasks_for("default")[0], {})
        gate.observe(None, 0)   # Nor is a gate question watched: it asks for no image.
        self.assertEqual(set(gate.turn), {"restore", "names", "truth"})

    def test_a_right_answer_does_not_excuse_a_changed_scene(self):
        record = self.run_task(self.by_id["mesh_stats"], [said("COUNTS: 12 20 10")], passed=False,
                               truth={"accept": [[12, 20, 10]]})
        self.assertEqual(self.outcome(record), ("right", False, "failed"))

    def test_a_constant_answer_fails_both_variants(self):
        for word, other in (("yes", "no"), ("no", "yes")):
            with self.subTest(always=word):
                rows = [IMAGE, said("ANSWER: " + word)]
                first = self.run_task(self.by_id["view_check"], rows, truth={"accept": ["yes"]})
                self.assertFalse(first["host_check_passed"], "no pass yet, whatever it said")
                second = self.run_task(self.by_id["view_check_cropped"], rows, truth={"accept": ["no"]})
                self.assertEqual([self.outcome(first), self.outcome(second)], [("constant", False, "failed")] * 2)
                self.assertNotIn("held", first["host_check"]["answer"])
        self.assertEqual(self.report["grading"]["totals"]["passed"], 0)
        first = self.run_task(self.by_id["view_check"], [IMAGE, said("ANSWER: yes")], truth={"accept": ["yes"]})
        second = self.run_task(self.by_id["view_check_cropped"], [IMAGE, said("ANSWER: no")], truth={"accept": ["no"]})
        self.assertEqual([self.outcome(first), self.outcome(second)], [("right", True, "passed")] * 2)
        self.assertEqual(self.report["grading"]["totals"]["passed"], 2)

    def test_one_variant_alone_cannot_pass(self):
        first, second = self.by_id["view_check"], self.by_id["view_check_cropped"]
        yes, no = [IMAGE, said("ANSWER: yes")], [IMAGE, said("ANSWER: no")]
        # Right, and the only one that ran (the trial stopped, or the other was skipped): ungraded.
        alone = self.run_task(first, yes, truth={"accept": ["yes"]})
        self.assertEqual(self.outcome(alone), ("ungraded", None, "ungraded"))
        self.assertEqual((alone["host_check"]["answer"]["reason"], alone["host_check"]["answer"]["held"]),
                         (eval_grading_live.UNPAIRED, "right"))
        self.assertEqual(self.report["grading"]["totals"]["passed"], 0)
        # The other one has no ground truth: the held pass stays ungraded.
        self.run_task(second, no, truth=None)
        self.assertEqual(self.outcome(alone), ("ungraded", None, "ungraded"))
        # The first variant has no ground truth: the second, though right, is ungraded.
        self.live.pairs.clear()
        self.run_task(first, yes, truth={"ungraded": "ambiguous"})
        alone = self.run_task(second, no, truth={"accept": ["no"]})
        self.assertEqual(self.outcome(alone), ("ungraded", None, "ungraded"))
        self.assertEqual(alone["host_check"]["answer"]["reason"], eval_grading_live.UNPAIRED)
        # A wrong variant stays failed whatever happens to the other, and the other can still pass beside it.
        self.live.pairs.clear()
        wrong = self.run_task(first, [IMAGE, said("ANSWER: maybe")], truth={"accept": ["yes"]})
        right = self.run_task(second, no, truth={"accept": ["no"]})
        self.assertEqual([self.outcome(wrong), self.outcome(right)],
                         [("no_answer", False, "failed"), ("right", True, "passed")])
        # A held pass stands once the other variant is graded, even when that one fails for its own reason.
        held = self.run_task(first, yes, truth={"accept": ["yes"]})
        blind = self.run_task(second, [said("ANSWER: no")], truth={"accept": ["no"]})
        self.assertEqual([self.outcome(held), self.outcome(blind)],
                         [("right", True, "passed"), ("unsupported", False, "failed")])

    def test_a_question_without_ground_truth_is_ungraded_never_passed(self):
        for kwargs, reason in (({"truth": {"ungraded": "the object's edge is on the frame edge"}}, "frame edge"),
                               ({"truth": None}, "no ground truth"),
                               ({"truth": {"accept": ["yes"]}, "fixture_error": "the camera does not face it"},
                                "camera")):
            with self.subTest(reason=reason):
                record = self.run_task(self.by_id["view_check"], [IMAGE, said("ANSWER: yes")], **kwargs)
                self.assertEqual(self.outcome(record), ("ungraded", None, "ungraded"))
                self.assertIn(reason, record["host_check"]["answer"]["reason"])
        changed = self.run_task(self.by_id["view_check"], [said("ANSWER: yes")], passed=False, truth=None)
        self.assertEqual(self.outcome(changed), ("ungraded", False, "failed"), "a changed scene still fails")

    def test_a_version_1_gate_question_keeps_its_result_and_is_reported_ungraded(self):
        task = live.tasks_for("default", version=1)[0]
        gate = eval_grading_live.LiveGrading("default", live.tasks_for("default", version=1), {"tasks": []}, None, None)
        for passed in (True, False):
            record = question_record(task, passed)
            gate.before(None, self.scene, task, record)
            self.assertEqual(gate.after(None, self.scene, task, record, passed, [said("One mesh: Cube.")]), passed)
            record["host_check_passed"] = passed
            gate.report["tasks"].append(record)
            gate.complete(None, self.scene, task, record)
            self.assertEqual((record["host_check"]["answer"], record["grade"]), (
                {"outcome": "unchecked", "reason": grading.LEGACY_REASON}, "ungraded" if passed else "failed"))
            self.assertNotIn("harness_fixture", record)
        self.assertFalse(gate.probe_edits, "only the survey's edits are probed before the turn")

    def test_an_unchecked_question_outside_the_gate_sets_is_ungraded(self):
        task = {"id": "new_question", "kind": "read_only", "prompt": "Is it nice?", "checks": [], "source": "new"}
        record = self.run_task(task, [said("Yes.")])
        self.assertEqual(self.outcome(record), ("unchecked", None, "ungraded"))
        changed = self.run_task(task, [said("Yes.")], passed=False)
        self.assertEqual(self.outcome(changed), ("unchecked", False, "failed"))

    def test_the_older_names_check_still_grades_a_delegation_question(self):
        task = next(task for task in live.tasks_for("delegation") if task["id"] == "audit_question")
        record = self.run_task(task, [said("Audit B and Audit D")])
        self.assertEqual(self.outcome(record), ("right", True, "passed"))

    def test_edits(self):
        declared = self.run_task(self.by_id["save_blend"])
        self.assertEqual((declared["host_check"], declared["host_check_passed"], declared["grade"]),
                         ({"ungraded": True, "reason": survey.FILE_RESULT}, None, "ungraded"))
        checked = self.by_id["create_cube"]
        self.assertEqual(self.run_task(checked)["grade"], "passed")
        self.assertEqual(self.run_task(checked, passed=False)["grade"], "failed")
        vacuous = self.run_task(checked, held_before=True)
        self.assertEqual((vacuous["host_check_passed"], vacuous["grade"], vacuous["host_check"]["vacuous"]),
                         (None, "ungraded", eval_grading_live.VACUOUS))
        self.assertEqual(self.run_task(checked, passed=False, held_before=True)["grade"], "failed")
        unmade = self.run_task(self.by_id["world_grey"], fixture_error="no world")
        self.assertEqual((unmade["host_check_passed"], unmade["grade"]), (None, "ungraded"))
        self.assertIn("no world", unmade["host_check"]["reason"])

    def test_a_grading_defect_never_raises_and_never_passes(self):
        task = self.by_id["view_check"]
        record = question_record(task)
        with mock.patch.object(survey_truth, "truth", side_effect=RuntimeError("boom")), \
                mock.patch.object(survey_fixtures, "apply", lambda *args: ([], None)):
            self.live.before(None, self.scene, task, record)
            passed = self.live.after(None, self.scene, task, record, True, [IMAGE, said("ANSWER: yes")])
        self.assertIsNone(passed)
        self.assertIn("boom", record["grading_error"])
        with mock.patch.object(answers, "visual_evidence", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.live.after(None, self.scene, task, {}, True, []))
            self.assertTrue(self.live.after(None, self.scene, self.by_id["create_cube"], {}, True, []),
                            "a task these steps do not grade keeps the driver's own result")

    def test_a_defect_never_touches_a_gate_edit_s_result(self):
        tasks = live.tasks_for("default")
        gate = eval_grading_live.LiveGrading("default", tasks, {"tasks": []}, None, None)
        edit, objects = tasks[1], mock.Mock(__iter__=mock.Mock(side_effect=RuntimeError("boom")))
        broken_scene = mock.Mock(objects=objects)
        for scene in (self.scene, broken_scene):
            record = {"task": edit["id"], "kind": "edit", "status": "completed", "host_check": {"success": True}}
            gate.before(None, scene, edit, record)
            gate.observe(None, 0)
            with mock.patch.object(grading, "totals", side_effect=RuntimeError("boom")):
                self.assertTrue(gate.after(None, scene, edit, record, True, [said("Done.")]))
                self.assertFalse(gate.after(None, scene, edit, dict(record), False, []))
                record["host_check_passed"] = True
                gate.complete(None, scene, edit, record)   # Must not raise, whatever broke.
            self.assertEqual(record["host_check"], {"success": True}, "the driver's own host check is untouched")
        self.assertIn("grading_error", record)
        watched = self.live
        watched.turn = {"watch": mock.Mock(observe=mock.Mock(side_effect=RuntimeError("boom")))}
        watched.observe([], 0)
        self.assertNotIn("watch", watched.turn, "a failing watch is dropped; the tick never raises")
        # A survey edit whose checks held before the turn is not passed by a defect either.
        with mock.patch.object(answers, "visual_evidence", side_effect=RuntimeError("boom")):
            self.live.turn = {"held_before": True}
            self.assertIsNone(self.live.after(None, self.scene, self.by_id["create_cube"], {}, True, []))

    def test_created_objects_answer_to_their_alias(self):
        self.live.before(None, self.scene, self.by_id["sphere_on_top"], {})
        self.scene.objects.extend(Scene("Sphere", "Lamp").objects)
        self.scene.objects[-1].type = "LIGHT"
        self.live._after(None, self.scene, self.by_id["sphere_on_top"], {"host_check": {}}, True, [])
        sphere = self.scene.objects.get("Sphere")
        self.assertEqual(self.live.created, {"$sphere": [(sphere.session_uid, "Sphere")]})
        self.live.before(None, self.scene, self.by_id["glass_sphere"], {})
        resolve = self.live.resolver(self.scene, self.by_id["glass_sphere"])
        self.assertEqual(resolve("$sphere"), [sphere])
        self.assertEqual((resolve("$eval").name, resolve("$scene"), resolve("$new"), resolve("$ground")),
                         ("Eval Cube", self.scene, [], []))
        # The agent renames it while making it glass: later tasks still find "the sphere".
        sphere.name = "Glass Sphere"
        for later in ("array_row", "bounce", "auto_weights"):
            self.live.before(None, self.scene, self.by_id[later], {})
            self.assertEqual(self.live.resolver(self.scene, self.by_id[later])("$sphere"), [sphere], later)
        # An object without an identity (or a replaced one) is still found by its name.
        self.live.created["$sphere"] = [(None, "Glass Sphere")]
        self.assertEqual(self.live.resolver(self.scene, self.by_id["bounce"])("$sphere"), [sphere])
        self.assertEqual(self.live.resolver(self.scene, self.by_id["bounce"])("$ground"), [],
                         "an identity of None matches nothing")


class DriverWiringTests(unittest.TestCase):
    def test_the_trial_driver_calls_the_three_steps_in_their_places(self):
        text = (HERE / "eval_live_agent_session.py").read_text(encoding="utf-8")
        self.assertIn("self.grading = eval_grading_live.LiveGrading(TASK_SET, self.tasks, self.report, bind_checks, "
                      "grading_targets)", text)
        before, digest = text.index("self.grading.before(bpy, scene, task, self.record)"), text.index(
            "self.digest = scene_digest(scene)")
        self.assertLess(before, digest, "fixtures are part of the starting scene a question must leave unchanged")
        self.assertRegex(text, r"passed = self\.grading\.after\(bpy, scene, task, self\.record, passed,\n\s+"
                               r"self\.controller\.state\[\"messages\"\]\[self\.message_index:\]\)\n\s+"
                               r"self\.record\[\"host_check_passed\"\] = passed\n")
        self.assertRegex(text, r"self\.report\[\"tasks\"\]\.append\(self\.record\)\n\s+self\.grading\.complete\(bpy, "
                               r"self\.window\.scene, self\.tasks\[self\.index\], self\.record\)\n\s+self\.write\(\)")
        self.assertIn("self.answer_approvals()\n        self.grading.observe(self.controller.state[\"messages\"], "
                      "self.message_index)\n", text)
        self.assertEqual(text.count("self.grading."), 4)
        self.assertIn('"grade", "host_check_passed"', text, "the per-task line prints the grade")
        self.assertIn('"grading": self.report["grading"]}', text,
                      "the trial line prints the totals and what was graded (set version, digest)")


if __name__ == "__main__":
    unittest.main()
