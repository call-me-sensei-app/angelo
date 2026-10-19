# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of the grading contract: no pass without a check, the guard, and the frozen gate sets.

The trial driver's grading steps that apply the contract to a running trial
are tested in ``test_native_grading_live.py``, and in the real application by
``test_blender_survey_tasks.py``.
"""

import hashlib
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_grading  # noqa: E402
import eval_answers as answers  # noqa: E402
import eval_gate  # noqa: E402
import eval_grading as grading  # noqa: E402
import eval_grading_live  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_survey_tasks as survey  # noqa: E402
import scenario_specs  # noqa: E402
import survey_checks  # noqa: E402
import survey_fixtures  # noqa: E402
import survey_truth  # noqa: E402
from angelo_agent.validation_spec import CHECK_KINDS, validate_checks  # noqa: E402

# sha256 of the four helpers both benchmark sides grade the gate sets with (bench_mcp_grading.SESSION_HELPERS).
GATE_HELPERS_SHA256 = "ab0b0be628175c3e79370ef465a2426e6ee65a7c1f396d0e6742a1ebd8754a02"


def every_set():
    return {name: live.tasks_for(name) for name in live.TASK_SETS}


class GateFreezeTests(unittest.TestCase):
    """Recorded gate results are compared across merges: a gate set and its grading change only as a new version
    (``test_native_gate_versions.py`` holds what each version is)."""

    def test_the_gate_sets_keep_their_recorded_digests_at_every_version(self):
        self.assertEqual(set(grading.GATE_DIGESTS), set(eval_gate.VERSIONS))
        for version, digests in grading.GATE_DIGESTS.items():
            with self.subTest(version=version):
                self.assertEqual(set(digests), set(eval_gate.SETS))
                self.assertEqual(grading.tasks_digest(live.tasks_for("default", version=version)), digests["default"])
                self.assertEqual(grading.tasks_digest(live.tasks_for("long", 40, version)), digests["long"])
        self.assertEqual(grading.tasks_digest(live.tasks_for("default")),
                         grading.GATE_DIGESTS[eval_gate.VERSION]["default"])
        self.assertEqual(live.LONG_SESSION_TASKS, 40)

    def test_the_gate_grading_helpers_are_unchanged(self):
        source = bench_mcp_grading.session_helpers_source()
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), GATE_HELPERS_SHA256,
                         "these helpers grade the gate sets on both benchmark sides: changing one changes gate grading")

    def test_gate_edits_carry_none_of_the_newer_grading_fields(self):
        newer = {"answer", "result_checks", "fixtures", "creates", "ungraded", "gate_version"}
        for name in ("default", "long", "coverage", "delegation"):
            for version in eval_gate.VERSIONS:
                for task in live.tasks_for(name, version=version):
                    with self.subTest(task_set=name, version=version, task=task["id"]):
                        # The coverage set is not versioned: it opens with the gate's question as asked now.
                        asked_newly = (task["kind"] == "read_only" and name != "delegation"
                                       and (version >= 2 or name == "coverage"))
                        self.assertEqual(newer & set(task), newer & set(task) & {"answer", "fixtures", "gate_version"}
                                         if asked_newly else set())

    def test_version_1_questions_are_ungraded_not_passed_and_none_is_left_in_a_current_set(self):
        self.assertEqual({name: [task["id"] for task in tasks if grading.legacy_question(task)]
                          for name, tasks in every_set().items()}, dict.fromkeys(live.TASK_SETS, []))
        recorded = {name: [task["id"] for task in live.tasks_for(name, version=1) if grading.legacy_question(task)]
                    for name in eval_gate.SETS}
        self.assertEqual(recorded, {
            "default": ["read_only_question"],
            "long": ["read_only_question"] + ["question_%02d" % number for number in range(5, 40, 5)]})
        task = live.tasks_for("default", version=1)[0]
        self.assertEqual((grading.basis(task), grading.ungraded_reason(task)), ("ungraded", grading.LEGACY_REASON))
        record = {"task": task["id"], "kind": "read_only", "status": "completed", "host_check_passed": True,
                  "host_check": {"scene_unchanged": True}}
        self.assertEqual(grading.state(record, task), "ungraded")
        self.assertEqual(grading.state(record), "ungraded", "also without the task: no answer was read")
        self.assertEqual(grading.state(dict(record, host_check_passed=False), task), "failed", "the scene changed")


class GuardTests(unittest.TestCase):
    """A task joins a graded set only with a check or with a stated reason for having none."""

    def test_every_task_of_every_set_is_checked_or_says_why_not(self):
        for name, tasks in every_set().items():
            with self.subTest(task_set=name):
                self.assertEqual(grading.unguarded(tasks), [])
                for task in tasks:
                    if grading.basis(task) == "ungraded":
                        self.assertGreater(len(grading.ungraded_reason(task)), 40, task["id"])
        self.assertEqual(grading.unguarded(live.tasks_for("long", 120)), [])
        self.assertEqual(set(every_set()),
                         {"default", "long", "delegation", "coverage", "survey", "survey-questions"}
                         | set(scenario_specs.TASK_SETS)
                         | set(scenario_specs.ONESHOT_TASK_SETS))   # One-shot tests: each brief's own closed checks.

    def test_a_task_without_a_check_or_a_reason_fails_the_guard(self):
        edit = {"id": "new_edit", "kind": "edit", "prompt": "Make it nicer.", "checks": []}
        question = {"id": "new_question", "kind": "read_only", "prompt": "Is it nice?", "checks": [],
                    "source": "survey: question"}
        self.assertEqual(grading.unguarded([edit, question]), ["new_edit", "new_question"])
        self.assertEqual(grading.unguarded([dict(edit, ungraded="   "), dict(edit, ungraded=True)]), ["new_edit"] * 2)
        fixed = [dict(edit, checks=[{"kind": "object_exists"}]), dict(edit, result_checks=[{"kind": "world_look"}]),
                 dict(edit, scenario_checks=[{"kind": "object"}]), dict(edit, ungraded="Its result is a file on disk."),
                 dict(question, answer={"form": "yes_no"}), dict(question, reply_includes=["Cube"])]
        self.assertEqual(grading.unguarded(fixed), [])
        self.assertEqual([grading.basis(task) for task in fixed],
                         ["checks", "result_checks", "scenario_checks", "ungraded", "answer", "includes"])
        self.assertIsNone(grading.basis(dict(question, source="long session: read-only question", kind="edit")),
                          "the frozen waiver is for the gate questions only")

    def test_the_survey_counts(self):
        tasks = survey.build_survey_tasks()
        bases = [grading.basis(task) for task in tasks]
        self.assertEqual({basis: bases.count(basis) for basis in set(bases)},
                         {"checks": 33, "result_checks": 18, "ungraded": 8, "answer": 6})
        self.assertEqual(sorted(task["id"] for task in tasks if grading.basis(task) == "ungraded"), [
            "ai_chair", "export_glb", "image_texture_disk", "render_to_file", "run_script", "save_blend",
            "sculpt_rock", "viewport_wireframe"])
        for task in tasks:
            if task["expect"] in {"approval", "never-native"}:
                self.assertEqual(grading.basis(task), "ungraded", "nothing the harness always declines can be graded")
        self.assertEqual(survey.VERSION, 2)
        self.assertEqual(grading.stamp("survey", tasks), {
            "contract": grading.CONTRACT, "task_set": "survey", "tasks": 65,
            "tasks_digest": grading.tasks_digest(tasks), "survey_version": 2})
        self.assertNotIn("survey_version", grading.stamp("default", live.tasks_for("default")))


class SurveySpecTests(unittest.TestCase):
    """The survey's result checks, answers and fixtures name only things that exist."""

    def setUp(self):
        self.tasks = survey.build_survey_tasks()
        self.by_id = {task["id"]: task for task in self.tasks}

    def test_result_check_kinds_exist_and_closed_ones_validate(self):
        self.assertFalse(set(survey_checks.KINDS) & set(CHECK_KINDS), "a harness kind must not shadow a closed kind")
        self.assertLessEqual(survey_checks.MOTION_KINDS, set(survey_checks.KINDS))
        created = {}
        for task in self.tasks:
            for check in task.get("result_checks", ()):
                with self.subTest(task=task["id"], kind=check["kind"]):
                    self.assertTrue(check["kind"] in survey_checks.KINDS or check["kind"] in CHECK_KINDS)
                    for key in ("target", "armature", "collider", "around"):
                        alias = check.get(key)
                        if alias is not None:
                            self.assertTrue(alias in {"$scene", "$new"} or alias in eval_grading_live.STATIC_ALIASES
                                            or alias in created, "%s is not made by an earlier task" % alias)
                    if check["kind"] in CHECK_KINDS:
                        bound = {key: "Modifier" if str(value).startswith(survey_checks.MODIFIER_NAME) else value
                                 for key, value in check.items()}
                        self.assertEqual(len(validate_checks([bound])), 1)
            created.update(task.get("creates") or {})
        self.assertEqual(created, {"$sphere": "MESH", "$ground": "MESH", "$cloth": "MESH"})

    def test_questions_ask_for_a_form_and_name_a_truth_the_harness_computes(self):
        questions = [task for task in self.tasks if task["kind"] == "read_only"]
        self.assertEqual([task["id"] for task in questions], ["scene_overview", "mesh_stats", "how_to_bevel",
                                                              "render_info", "view_check", "view_check_cropped"])
        for task in questions:
            spec = task["answer"]
            with self.subTest(task=task["id"]):
                self.assertIn(spec["form"], answers.FORMS)
                self.assertIn(spec["truth"]["kind"], survey_truth.TRUTHS)
                self.assertTrue(task["prompt"].endswith(answers.FORMS[spec["form"]][1]))
                self.assertEqual(task["checks"], [])
                self.assertNotIn("reply_includes", task)
                self.assertIn(spec.get("evidence"), (None, answers.SCENE_CAMERA, answers.ANY_IMAGE))
        self.assertEqual(self.by_id["how_to_bevel"]["answer"]["truth"]["operator"], "mesh.bevel")
        self.assertIn("mesh.bevel", survey_truth.DEFAULT_SHORTCUTS)

    def test_the_camera_question_runs_in_both_variants_and_needs_the_camera_view(self):
        pair = [task for task in self.tasks if (task.get("answer") or {}).get("pair") == "view_check"]
        self.assertEqual([task["id"] for task in pair], ["view_check", "view_check_cropped"])
        self.assertEqual([task["fixtures"][0]["state"] for task in pair], list(survey_fixtures.CAMERA_STATES))
        for task in pair:
            self.assertEqual(task["answer"]["evidence"], answers.SCENE_CAMERA)
            self.assertEqual(task["answer"]["truth"], {"kind": "camera_frame", "object": survey.EVAL})
            self.assertEqual(task["fixtures"][0]["object"], survey.EVAL)
        ids = [task["id"] for task in self.tasks]
        self.assertLess(ids.index("frame_object"), ids.index("view_check"))
        self.assertLess(ids.index("extrude_top"), ids.index("mesh_stats"), "so the counts are not a plain cube's")

    def test_fixtures_exist_and_only_questions_use_transient_ones(self):
        for task in self.tasks:
            for fixture in task.get("fixtures", ()):
                with self.subTest(task=task["id"], fixture=fixture["kind"]):
                    self.assertIn(fixture["kind"], survey_fixtures.FIXTURES)
                    self.assertEqual(fixture["kind"] in survey_fixtures.TRANSIENT, task["kind"] == "read_only")
        self.assertLessEqual(survey_fixtures.TRANSIENT, set(survey_fixtures.FIXTURES))
        self.assertGreater(survey_fixtures.MARGIN, survey_truth.AMBIGUOUS, "a made camera state is never ambiguous")
        self.assertNotIn(survey.PROBE_OBJECT, {"Cube", "Camera", "Light"})


class StateTests(unittest.TestCase):
    def record(self, status="completed", passed=True, **more):
        return dict({"task": "t", "kind": "edit", "status": status, "host_check_passed": passed}, **more)

    def test_states(self):
        cases = [(self.record(), "passed"), (self.record(passed=False), "failed"),
                 (self.record(passed=None), "ungraded"), (self.record("failed", None), "ungraded"),
                 (self.record("timeout", None), "failed"), (self.record("error", None), "failed"),
                 (self.record("skipped", None), "skipped"),
                 (self.record("timeout", True), "passed"), (self.record(grade="ungraded"), "ungraded"),
                 (self.record(grade="nonsense"), "passed"), ({"task": "t", "status": "timeout"}, "failed")]
        for record, expected in cases:
            with self.subTest(record=record):
                self.assertEqual(grading.state(record), expected)
        question = self.record(kind="read_only", host_check={"scene_unchanged": True})
        self.assertEqual(grading.state(question), "ungraded", "an unchanged scene says nothing about the answer")
        for checked in ({"scene_unchanged": True, "reply_missing": []},
                        {"scene_unchanged": True, "answer": {"outcome": "right"}}):
            self.assertEqual(grading.state(dict(question, host_check=checked)), "passed")
        # Only a right answer is a read answer, whatever else the record claims.
        for outcome in ("unchecked", "ungraded", "wrong", "unsupported", None):
            unread = dict(question, host_check={"scene_unchanged": True, "answer": {"outcome": outcome}})
            self.assertEqual(grading.state(unread), "ungraded", outcome)
        self.assertEqual(grading.state(dict(question, host_check={"scene_unchanged": True, "answer": {}})), "ungraded")
        bare, task = self.record(kind="read_only"), {"id": "t", "kind": "read_only", "answer": {"form": "yes_no"}}
        self.assertEqual((grading.state(bare), grading.state(bare, task)), ("ungraded", "ungraded"),
                         "a record without its host check shows no read answer, whatever its task could check")

    def test_totals_state_every_run_and_drop_none(self):
        records = [self.record(), self.record(passed=False), self.record(passed=None), self.record("skipped", None),
                   self.record("timeout", None), self.record(kind="read_only", host_check={"scene_unchanged": True})]
        counts = grading.totals(records)
        self.assertEqual(counts, {"passed": 1, "failed": 2, "inconclusive": 0, "ungraded": 2, "skipped": 1,
                                  "total": 6})
        self.assertEqual(sum(counts[state] for state in grading.STATES), counts["total"])
        self.assertEqual(grading.describe(counts), "passed 1, failed 2, inconclusive 0, ungraded 2, skipped 1 of 6")
        stopped = grading.totals(records[:3], planned=65)
        self.assertEqual((stopped["total"], stopped["not_run"]), (3, 62))
        self.assertEqual(grading.describe(stopped),
                         "passed 1, failed 1, inconclusive 0, ungraded 1, skipped 0 of 3; 62 planned runs never ran")
        self.assertEqual(grading.totals(records, planned=6)["not_run"], 0)
        self.assertNotIn("never ran", grading.describe(grading.totals(records, planned=6)))
        self.assertEqual(grading.totals([]), {"passed": 0, "failed": 0, "inconclusive": 0, "ungraded": 0,
                                              "skipped": 0, "total": 0})

    def test_task_outcomes_keep_the_older_count_beside_the_contracts(self):
        tasks = live.tasks_for("default")
        records = [{"task": "read_only_question", "kind": "read_only", "status": "completed", "host_check_passed": True,
                    "receipt": {"status": "completed"}},
                   {"task": "transform", "kind": "edit", "status": "completed", "host_check_passed": True},
                   {"task": "transform", "kind": "edit", "status": "skipped"}]
        for outcomes in (live.task_outcomes(records, tasks), live.task_outcomes(records)):
            self.assertEqual(outcomes["read_only_question"], {"runs": 1, "completed": 1, "host_checks_passed": 1,
                                                              "skipped": 0, "passed": 0, "failed": 0,
                                                              "inconclusive": 0, "ungraded": 1})
            self.assertEqual(outcomes["transform"], {"runs": 2, "completed": 0, "host_checks_passed": 1, "skipped": 1,
                                                     "passed": 1, "failed": 0, "inconclusive": 0, "ungraded": 0})


class InconclusiveTests(unittest.TestCase):
    """A run whose only unmet checks could not be decided is inconclusive: never passed, apart from failed."""

    @staticmethod
    def checked(*entries, status="completed", **more):
        results = [dict(entry, label="check %d" % index) for index, entry in enumerate(entries)]
        host_check = dict({"success": False, "results": results}, **more)
        return {"task": "t", "kind": "edit", "status": status, "host_check_passed": False, "host_check": host_check}

    def test_only_undecided_checks_unmet_is_inconclusive_and_never_passed(self):
        record = self.checked({"passed": True}, {"passed": False, "inconclusive": True})
        self.assertEqual((grading.derive(record), grading.state(record)), ("inconclusive", "inconclusive"))
        counts = grading.totals([record, dict(record, host_check_passed=True)])
        self.assertEqual((counts["passed"], counts["inconclusive"], counts["failed"]), (1, 1, 0))
        self.assertIn("inconclusive 1", grading.describe(counts))

    def test_a_measured_fail_beside_it_or_an_unmarked_one_is_failed(self):
        for record in (self.checked({"passed": False, "inconclusive": True}, {"passed": False}),
                       self.checked({"passed": False}),
                       self.checked({"passed": False, "inconclusive": True}, status="timeout"),
                       self.checked({"passed": False, "inconclusive": True}, reply_missing=["Cube"]),
                       self.checked({"passed": False, "inconclusive": True}, smooth_shaded=False),
                       self.checked({"passed": False, "inconclusive": True},
                                    closed_checks={"success": False, "checks": [{"status": "fail"}]}),
                       dict(self.checked(), host_check={"error": "grading error"}),
                       dict(self.checked(), host_check={"scene_unchanged": False})):
            self.assertEqual(grading.state(record), "failed", record["host_check"])

    def test_the_closed_verifiers_inconclusive_status_counts(self):
        record = dict(self.checked(), host_check={"success": False, "status": "inconclusive", "checks": [
            {"status": "pass", "passed": True}, {"status": "inconclusive", "passed": False}]})
        self.assertEqual(grading.state(record), "inconclusive")
        record["host_check"]["checks"].append({"status": "unobservable", "passed": False})
        self.assertEqual(grading.state(record), "failed", "unobservable is not the grader's undecided verdict")

    def test_a_record_stamped_failed_before_the_third_verdict_reads_inconclusive(self):
        record = dict(self.checked({"passed": False, "inconclusive": True}), grade="failed")
        self.assertEqual(grading.state(record), "inconclusive")
        self.assertEqual(grading.state(dict(record, graded_status="completed", status="invalid")), "inconclusive")
        self.assertEqual(grading.state(dict(record, grade="passed", host_check_passed=True)), "passed")
        self.assertEqual(grading.state(dict(self.checked({"passed": False}), grade="failed")), "failed")

    def test_verdict_and_check_counts_and_cells(self):
        self.assertEqual(grading.verdict_counts(["passed", "failed", "inconclusive", "inconclusive"]),
                         {"passed": 1, "failed": 1, "inconclusive": 2, "total": 4})
        self.assertEqual(grading.verdict_text({"passed": 3, "failed": 1, "inconclusive": 1, "total": 5}, "trials"),
                         "passed 3, failed 1, inconclusive 1 of 5 trials")
        host_check = self.checked({"passed": True}, {"passed": False}, {"passed": False, "inconclusive": True})
        self.assertEqual(grading.check_counts(host_check["host_check"]),
                         {"passed": 1, "failed": 1, "inconclusive": 1, "total": 3})
        self.assertEqual(grading.check_counts({"error": "boom"})["failed"], 1)
        self.assertEqual(grading.pass_cell(3, 5, {"failed": 1, "inconclusive": 1}), "3/5 (failed 1, inconclusive 1)")


if __name__ == "__main__":
    unittest.main()
