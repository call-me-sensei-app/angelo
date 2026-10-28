# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for the request survey: its tasks, plan and the ranked latency-return report.

Nothing here launches Angelo or a provider; the report reads synthetic run
directories created in new temporary folders.
"""

import contextlib
import copy
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import eval_grading  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_survey_tasks as survey  # noqa: E402
import survey_report as report  # noqa: E402
from angelo_agent.validation_spec import validate_checks  # noqa: E402

# Aliases the trial driver's grading_targets resolves when a task names no targets.
DEFAULT_ALIASES = {"$eval", "$scene", "$copy", "$rig", "$sketch"}


def must_not_run(*_args, **_kwargs):
    raise AssertionError("A plan-only run must not launch or read anything")


class SurveyTaskTests(unittest.TestCase):
    def setUp(self):
        self.tasks = survey.build_survey_tasks()
        self.by_id = {task["id"]: task for task in self.tasks}

    def test_ids_weights_categories_and_expectations_are_valid(self):
        self.assertEqual(len(self.by_id), len(self.tasks), "task ids are unique")
        self.assertGreaterEqual(len(self.tasks), 55)
        for task in self.tasks:
            with self.subTest(task=task["id"]):
                self.assertIn(task["category"], survey.CATEGORIES)
                self.assertIn(task["expect"], survey.EXPECTS)
                self.assertIn(task["kind"], {"edit", "read_only"})
                self.assertIn(type(task["weight"]), {int, float})
                self.assertTrue(math.isfinite(task["weight"]) and task["weight"] > 0)
                self.assertTrue(task["prompt"].strip() and task["source"] == "survey: " + task["category"])
                self.assertEqual(task["kind"] == "read_only", task["category"] == "question")

    def test_checks_validate_and_name_only_resolvable_targets(self):
        graded = 0
        for task in self.tasks:
            with self.subTest(task=task["id"]):
                aliases = set(task["targets"]) if task.get("targets") else DEFAULT_ALIASES
                if task["kind"] == "read_only":
                    self.assertEqual(task["checks"], [], "a question is graded by an unchanged scene")
                    continue
                bound = [dict(check, name="Bevel") if check.get("name") == "$bevel" else check
                         for check in copy.deepcopy(task["checks"])]
                if bound:
                    graded += 1
                    self.assertEqual(len(validate_checks(bound)), len(bound))
                for check in bound:
                    if check["kind"] == "object_count":
                        self.assertNotIn("target", check)
                    else:
                        self.assertIn(check["target"], aliases)
                    if check["kind"] == "parent":
                        self.assertIn(check["expected"], aliases)
                if task.get("targets"):
                    self.assertTrue(set(task["targets"].values()) <= {survey.EVAL, survey.COPY,
                                                                       survey.FACTORY_CAMERA, survey.FACTORY_LIGHT})
        self.assertEqual(graded, 33)
        self.assertIn("33 edits are graded by the add-on's closed checks", survey.__doc__)

    def test_weights_match_the_documented_table(self):
        weights = survey.category_weights(self.tasks)
        self.assertEqual(weights, survey.CATEGORY_WEIGHTS)
        self.assertAlmostEqual(sum(weights.values()), 100)
        rows = {line.split()[0]: line.split()[1:3] for line in survey.__doc__.splitlines()
                if line.split() and line.split()[0] in survey.CATEGORIES}
        self.assertEqual(set(rows), set(survey.CATEGORIES))
        for name, (weight, count) in rows.items():
            with self.subTest(category=name):
                self.assertEqual(float(weight), survey.CATEGORY_WEIGHTS[name])
                self.assertEqual(int(count), sum(task["category"] == name for task in self.tasks))

    def test_routing_classes(self):
        self.assertEqual(survey.expect_counts(self.tasks), {"native": 49, "approval": 2, "python": 9, "never-native": 5})
        for task in self.tasks:
            with self.subTest(task=task["id"]):
                if task["expect"] in {"approval", "never-native"}:
                    self.assertEqual(task["checks"], [], "nothing the harness declines can pass a check")
                if "~/" in task["prompt"]:
                    # A file, render or script request: the harness never approves its tools.
                    self.assertIn(task["expect"], {"python", "never-native"})

    def test_order_keeps_each_tasks_preconditions(self):
        ids = [task["id"] for task in self.tasks]
        self.assertEqual(ids[:2], ["scene_overview", "create_cube"])
        self.assertEqual(self.tasks[0]["kind"], "read_only")
        self.assertFalse(self.tasks[1]["requires_object"])
        creators = {"$copy": "duplicate_copy", "$rig": "rig_chain", "$sketch": "gp_smiley"}
        for position, task in enumerate(self.tasks):
            with self.subTest(task=task["id"]):
                if position > 1 and survey.EVAL in task["prompt"] + json.dumps(task.get("targets", {})):
                    self.assertTrue(task["requires_object"] or task.get("targets"), "needs the eval cube")
                if task["kind"] == "edit" and task["checks"] and not task.get("targets") and position > 1:
                    self.assertTrue(task["requires_object"], "the driver grades only while the eval cube exists")
                used = {check.get("target") for check in task["checks"]} | set(task.get("targets", {}))
                for alias, creator in creators.items():
                    if alias in used and task["id"] != creator:
                        self.assertGreater(position, ids.index(creator))
        # Tasks on the factory Light run before three-point lighting may replace it.
        light_tasks = [ids.index(t["id"]) for t in self.tasks if survey.FACTORY_LIGHT in t.get("targets", {}).values()]
        self.assertLess(max(light_tasks), ids.index("three_point"))
        self.assertLess(ids.index("parent_copy"), ids.index("join_copy"))

    def test_task_set_is_registered_and_the_plan_counts_every_message(self):
        self.assertIn("survey", live.TASK_SETS)
        self.assertEqual(live.tasks_for("survey"), self.tasks)
        value = live.plan(1, self.tasks)
        self.assertEqual((value["user_messages"], value["local_preview_accepts"]), (65, 0))
        self.assertEqual(live.plan(3, self.tasks)["user_messages"], 195)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch.object(eval_live_agent, "isolated_connection", must_not_run), \
                patch.object(subprocess, "run", must_not_run):
            code = eval_live_agent.main(["--plan-only", "--task-set", "survey", "--trials", "1"])
        planned = json.loads(stdout.getvalue().split("PLANNED ", 1)[1].splitlines()[0])
        self.assertEqual((code, planned["user_messages"], planned["task_ids"]), (0, 65, [t["id"] for t in self.tasks]))

    def test_trial_driver_records_activity_and_ungraded_edits(self):
        text = (HERE / "eval_live_agent_session.py").read_text(encoding="utf-8")
        # The route is a host-testable function: a survey edit without checks is ungraded, and a
        # scenario turn (whose ``checks`` list is empty too) is never mistaken for one.
        import scenario_session
        self.assertIn('route = scenario_session.grading_route(task, self.scenario is not None)', text)
        self.assertIn('if route == "ungraded":', text)
        self.assertIn('self.record["host_check"] = {"ungraded": True}', text)
        routes = {scenario_session.grading_route(task, False) for task in self.tasks if not task["checks"]}
        self.assertLessEqual(routes, {"ungraded", "read_only"})
        self.assertIn("ungraded", routes)
        self.assertEqual({scenario_session.grading_route(task, False) for task in self.tasks if task["checks"]
                          and task["kind"] != "read_only"}, {"checks"})
        # Recorded once, in account(), which both paths that end a request run: after idle and on Stop.
        self.assertIn("activity=survey_report.turn_activity(", text[text.index("    def account(self"):])
        self.assertEqual(text.count("self.account()"), 2, "after idle and on Stop")
        self.assertIn("survey_report.turn_activity(self.controller.state[\"messages\"][self.first_row:])", text)


def _rows():
    return [{"role": "user", "text": "Make it red"},
            {"role": "activity", "tool": "angelo_agent_capabilities", "arguments": {"family": "ACTIONS"}},
            {"role": "activity", "tool": "angelo_agent_capabilities"},
            {"role": "assistant", "text": "Looking up the schema"},
            {"role": "activity", "tool": "angelo_agent_capabilities"},
            {"role": "activity", "tool": "angelo_scene_program", "result": {"secret": "never copied"}},
            {"role": "system", "text": "Host rejected the plan before running any operation; asking again"},
            {"role": "system", "text": "Nothing of the last plan ran; asking for a corrected plan:"},
            {"role": "system", "text": "Host skipped these steps of the plan because their arguments were invalid:\nx"},
            {"role": "activity", "tool": "not_a_tool"}, {"role": "activity"}, "not a row"]


class TurnActivityTests(unittest.TestCase):
    def test_counts_tools_lookup_rounds_and_rejected_plans_only(self):
        activity = report.turn_activity(_rows())
        self.assertEqual(activity, {"tools": {"angelo_agent_capabilities": 3, "angelo_scene_program": 1},
                                    "capability_lookups": 3, "lookup_rounds": 2, "plan_rejections": 2,
                                    "plans_refused_in_part": 1})
        self.assertNotIn("secret", json.dumps(activity))
        self.assertEqual(report.turn_activity([])["lookup_rounds"], 0)


def _record(task, total, *, status="completed", declined=(), rounds=2, activity=None, counts=None, passed=True,
            categories=None):
    """A task record shaped as eval_live_agent_session.Trial.complete writes it."""
    receipt = None
    if total is not None:
        receipt = {"status": "completed" if status == "completed" else "failed", "runtime": "claude",
                   "runtime_start": "warm", "phases": {"total_ms": total, "first_native_action_ms": 900.,
                                                       "first_verified_result_ms": total - 1500, "execution_ms": 20.,
                                                       "staging_ms": 150., "commit_ms": 30., "verification_ms": 40.},
                   "counts": dict({"python_fallbacks": len([d for d in declined if d == report.PYTHON_TOOL])},
                                  **(counts or {}))}
    python = receipt["counts"]["python_fallbacks"] if receipt else 0
    return {"trial": 1, "task": task, "kind": "edit", "status": status, "approved": [], "declined": list(declined),
            "receipt": receipt, "host_check_passed": passed if status == "completed" else False,
            "model_rounds": rounds, "provider_turn_requests": 1, "python_fallbacks": python,
            "fallback_categories": categories or {}, "activity": activity or report.turn_activity([])}


def _trial(extra=()):
    python = [report.PYTHON_TOOL]
    lookup = {"tools": {}, "capability_lookups": 2, "lookup_rounds": 1, "plan_rejections": 0}
    rejected = {"tools": {}, "capability_lookups": 0, "lookup_rounds": 0, "plan_rejections": 1}
    return {"trial": 1, "success": True, "grading": eval_grading.stamp("survey", survey.build_survey_tasks()), "tasks": [
        dict(_record("scene_overview", 3000), kind="read_only"),
        _record("create_cube", 4000), _record("extrude_top", 6000, rounds=3, activity=lookup),
        _record("red_plastic", 5000), _record("donut", 9000, declined=python, categories={"geometry": 1}),
        _record("vertex_colors", 8000, declined=python, categories={"geometry": 1}),
        _record("sun_light", 9000, rounds=3, activity=rejected, counts={"invalid_calls": 1}),
        _record("ai_chair", 7000, declined=["angelo_studio_generate"]),
        _record("save_blend", 6000, declined=python, categories={"external_io": 1}),
        dict(_record("parent_copy", None), status="skipped", receipt=None), *extra]}


class SurveyReportTests(unittest.TestCase):
    def write_run(self, folder):
        trials = [_trial([_record("table", 20000, status="failed"), _record("bake_sim", None, status="timeout")]),
                  _trial([_record("table", 7000), _record("unknown_task", 1000)]),
                  {"invalid": "rate_limited", "tasks": [_record("donut", 900000, declined=[report.PYTHON_TOOL])]}]
        (Path(folder) / "eval-live-agent.json").write_text(json.dumps({"trials": trials}))

    def test_outcomes_gaps_and_sinks_rank_by_weight_and_seconds(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write_run(folder)
            value = report.build_report([folder])
        self.assertEqual((value["trials"], value["invalid_trials"], value["unknown_tasks"]), (2, 1, {"unknown_task": 1}))
        rows = {row["id"]: row for row in value["tasks"]}
        expected = {"create_cube": "native", "scene_overview": "native", "donut": "declined-python",
                    "ai_chair": "approval", "save_blend": "declined-python", "parent_copy": "skipped",
                    "bake_sim": "failed"}
        self.assertEqual({key: rows[key]["outcome"] for key in expected}, expected)
        self.assertEqual(rows["table"]["outcomes"], {"failed": 1, "native": 1})
        self.assertEqual(rows["create_cube"]["host_ms"], 200.)
        self.assertEqual(rows["create_cube"]["after_verified_ms"], 1500.)
        self.assertEqual(rows["create_cube"]["first_native_action_ms"], 900.)
        gaps = value["gaps"]
        # Modeling-edit native baseline: median of 4, 4, 6, 6 and 7 s = 6 s.
        self.assertEqual([gap["id"] for gap in gaps], ["donut", "vertex_colors", "table", "bake_sim"])
        donut = gaps[0]
        self.assertEqual((donut["observed_s"], donut["native_baseline_s"], donut["saving_s"], donut["score"]),
                         (29., 6., 23., 34.5))
        self.assertEqual((gaps[2]["gap_rate"], gaps[2]["score"]), (.5, 14.))
        self.assertIsNone(gaps[3]["score"], "a run without a receipt has no observed time and ranks last")
        self.assertEqual(donut["fallback_categories"], {"geometry": 2})
        self.assertNotIn("save_blend", [gap["id"] for gap in gaps], "never-native is not a gap")
        self.assertNotIn("ai_chair", [gap["id"] for gap in gaps], "an approval-gated decline is expected")
        sinks = value["extra_round_sinks"]
        self.assertEqual([sink["id"] for sink in sinks], ["sun_light", "extrude_top"])
        self.assertEqual((sinks[0]["rejection_s"], sinks[0]["score"]), (6., 9.))
        self.assertEqual((sinks[1]["lookup_s"], sinks[1]["score"], sinks[1]["lookups"]), (2., 6., 2.))
        categories = {row["category"]: row for row in value["categories"]}
        self.assertEqual(categories["modeling"]["gap_weight"], 1.5 + 2)

    def test_the_approval_wait_is_a_parameter(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write_run(folder)
            value = report.build_report([folder], approval_wait_s=0)
        self.assertEqual([gap["id"] for gap in value["gaps"]][:3], ["table", "donut", "vertex_colors"])

    def test_main_writes_json_and_markdown_and_refuses_empty_input(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write_run(folder)
            output = Path(folder) / "report"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(report.main([folder, "--output", str(output)]), 0)
            written = json.loads((output / report.REPORT_JSON).read_text())
            text = (output / report.REPORT_MD).read_text()
            self.assertEqual(written["gaps"][0]["id"], "donut")
            for heading in ("## Gaps by weight", "## Extra-round sinks", "## Categories", "## Tasks"):
                self.assertIn(heading, text)
            empty = Path(folder) / "empty"
            empty.mkdir()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(report.main([str(empty)]), 2)

    def test_an_interrupted_run_is_read_from_its_trial_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            trial = Path(folder) / "trial-01" / "qa"
            trial.mkdir(parents=True)
            (trial / "trial.json").write_text(json.dumps(_trial()))
            value = report.build_report([folder])
        self.assertEqual((value["trials"], value["gaps"][0]["id"]), (1, "donut"))


if __name__ == "__main__":
    unittest.main()
