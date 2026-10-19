# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How many views a Claude request keeps, and what a plan over that budget is told.

Every view a typed plan asks for is captured again and attached after each
later edit, so their number is bounded: twelve distinct images per request, at
most six of them sculpt views or reviews (which also hold native cameras). A
plan that asks for more is returned to the model with the number it has left,
as any other plan problem is; it does not end the turn. RENDERED looks and
finished pictures are delivered once and are not counted.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.claude_evidence import (CAPTURE_FAILED, CLAUDE_SCULPT_IMAGE_LIMIT, REFRESH_BUDGET,  # noqa: E402
                                          REFRESH_SECONDS, SCULPT_IMAGE_TOOLS, VIEW_IMAGE_LIMIT, VISUAL_IMAGE_TOOLS,
                                          ClaudeVisualEvidence)
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.plan_schema import PlanRejected  # noqa: E402
from angelo_agent.plan_validation import screen_plan  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.providers import CLAUDE_PLAN_REJECTIONS, ClaudeProvider  # noqa: E402

AXES = ("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "ISO")
EDIT = {"tool": "angelo_set_transform", "arguments": {"object": "Cube", "location": [0, 1, 0]}}


def view(axis, shading="SOLID", **extra):
    return {"tool": "angelo_view", "arguments": dict({"views": [axis], "shading": shading}, **extra)}


def sculpt(region):
    return {"tool": "angelo_sculpt_view", "arguments": {"object": "Sphere", "views": ["FRONT"], "resolution": 512,
                                                        "region": region}}


def look(**extra):
    return {"tool": "angelo_view", "arguments": dict({"views": ["SCENE_CAMERA"], "shading": "RENDERED"}, **extra)}


class BudgetCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Controller(self.directory.name)
        self.evidence = ClaudeVisualEvidence(self.host.references)

    def tearDown(self):
        self.host.close()
        self.directory.cleanup()

    def record(self, name):
        value = self.host.references.new_observation(name)
        Path(value["path"]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        return value

    def admit(self, operation):
        self.evidence.admit(operation, {"views": [self.record(operation["arguments"]["views"][0])]})


class BudgetTests(BudgetCase):
    def test_the_limits(self):
        self.assertEqual((VIEW_IMAGE_LIMIT, CLAUDE_SCULPT_IMAGE_LIMIT), (12, 6))
        self.assertEqual(VISUAL_IMAGE_TOOLS, SCULPT_IMAGE_TOOLS | {"angelo_view"})

    def test_twelve_distinct_views_fit_and_the_thirteenth_is_told_what_is_left(self):
        twelve = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES[:6]]
        self.assertIsNone(self.evidence.problem(twelve))
        self.assertIsNone(self.evidence.problem(twelve + twelve), "the same view asked twice is one view")
        for operation in twelve[:9]:
            self.admit(operation)
        self.assertIsNone(self.evidence.problem(twelve[9:]))
        self.assertIsNone(self.evidence.problem(twelve[:9] + twelve[9:]), "views already kept are not new")
        problem = self.evidence.problem(twelve[9:] + [view("ISO"), view("ISO", "WIREFRAME")])
        self.assertIn("this request keeps 9 distinct view images, each captured again and attached after every edit, "
                      "and has 3 of 12 left", problem)
        self.assertIn("this plan asks for 5 new ones", problem)
        self.assertIn("None of this plan's operations were run and earlier views are kept", problem)
        self.assertIn("Ask for at most 3 new views", problem)
        self.assertIn("RENDERED looks and finished pictures are not counted", problem)

    def test_another_resolution_target_or_shading_is_another_image(self):
        base = [view("FRONT")] * 3 + [view("FRONT", resolution=1024), view("FRONT", "MATERIAL"),
                                      view("FRONT", targets=["o1"])]
        self.evidence.problem(base)
        wanted = dict(self.evidence.requests)
        for operation in base:
            wanted.update(self.evidence._parts(operation))
        self.assertEqual(len(wanted), 4)

    def test_sculpt_views_keep_their_own_limit_within_the_twelve(self):
        six = [sculpt("Region %d" % index) for index in range(6)]
        self.assertIsNone(self.evidence.problem(six + [view(axis) for axis in AXES[:6]]))
        problem = self.evidence.problem(six + [sculpt("Seventh")])
        self.assertIn("has 12 of 12 left (6 of them for sculpt views or reviews, which are limited to 6); "
                      "this plan asks for 7 new ones", problem)
        for operation in six:
            self.evidence.admit(operation, {"views": [self.record(operation["arguments"]["region"])]})
        problem = self.evidence.problem([sculpt("Seventh")])
        self.assertIn("has 6 of 12 left (0 of them for sculpt views or reviews", problem)
        self.assertIsNone(self.evidence.problem([view(axis) for axis in AXES[:6]]), "six viewport views still fit")

    def test_looks_and_finished_pictures_are_never_counted(self):
        twelve = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES[:6]]
        extra = [look(), look(resolution=1024), look(region=[0.0, 0.0, 0.5, 0.5]), look(image="RENDER_RESULT"),
                 look(image="j2", frame=7)]
        self.assertIsNone(self.evidence.problem(twelve + extra))
        self.evidence.check_plan(twelve + extra)

    def test_the_budget_joins_a_plans_problems_only_when_it_has_no_other(self):
        thirteen = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES]
        invalid_edit = {"tool": "angelo_set_transform", "arguments": {"object": "Cube"}}
        screened = screen_plan([invalid_edit] + thirteen, None, None, self.evidence.problem)
        self.assertEqual(screened.problems, ["operations[0] (angelo_set_transform): Missing tool argument"])
        screened = screen_plan(thirteen, None, None, self.evidence.problem)
        self.assertEqual(len(screened.problems), 1)
        self.assertTrue(screened.problems[0].startswith("View budget:"))
        screened = screen_plan(thirteen[:12], None, None, self.evidence.problem)
        self.assertEqual((screened.problems, screened.operations), ([], thirteen[:12]))
        with self.assertRaisesRegex(RpcError, "View budget"):
            self.evidence.check_plan(thirteen)  # The check before the first mutation stays as a second line.


class TurnTests(BudgetCase):
    def run_plans(self, plans, native):
        provider = ClaudeProvider(self.host, {})
        provider._turn = Mock(side_effect=plans)
        provider._native_call = Mock(side_effect=native)
        return provider, provider.run(None, "Look at it from everywhere", observation={})

    def test_a_plan_over_the_budget_is_corrected_and_the_turn_completes(self):
        thirteen = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES]

        def native(name, arguments, *, owner):
            if name == "angelo_view":
                return {"success": True, "views": [self.record(angle) for angle in arguments["views"]]}
            return {"objects": []}
        provider, status = self.run_plans(
            [{"reply": "", "operations": thirteen, "verify": True},
             {"reply": "", "operations": thirteen[:12], "verify": True},
             {"reply": "Seen from twelve sides.", "operations": [], "verify": False}], native)
        self.assertEqual(status, "completed")
        correction = provider._turn.call_args_list[1].args[1]
        self.assertIn("The host rejected your last plan before running any of its operations; nothing changed", correction)
        self.assertIn("has 12 of 12 left", correction)
        self.assertIn("this plan asks for 14 new ones", correction)
        self.assertEqual(len(provider._turn.call_args.kwargs["references"]), 12, "all twelve images reach the next round")
        self.assertEqual(provider._turn.call_count, 3)

    def test_a_model_that_keeps_asking_for_too_many_ends_with_the_plan_rejected_and_nothing_run(self):
        thirteen = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES]
        provider = ClaudeProvider(self.host, {})
        provider._turn = Mock(return_value={"reply": "", "operations": [EDIT] + thirteen, "verify": True})
        provider._native_call = Mock()
        with self.assertRaisesRegex(PlanRejected, "View budget"):
            provider.run(None, "Look at it from everywhere", observation={})
        self.assertEqual(provider._turn.call_count, 1 + CLAUDE_PLAN_REJECTIONS)
        provider._native_call.assert_not_called()


class Scene:
    """A stand-in for the native view tool: every view costs ``seconds`` of a clock the test reads."""

    def __init__(self, case, seconds=0.05, report=True):
        self.case, self.seconds, self.report, self.now, self.calls, self.fail = case, seconds, report, 100.0, [], {}

    def clock(self):
        return self.now

    def call(self, operation):
        views = operation["arguments"]["views"]
        self.calls.append((operation["arguments"].get("shading", "SOLID"), list(views)))
        self.now += self.seconds * len(views)
        error = next((self.fail[angle] for angle in views if angle in self.fail), None)
        if error:
            return {"error": error}
        result = {"success": True, "views": [dict(self.case.record(angle), view=angle) for angle in views]}
        if self.report:
            result["timing"] = {"capture_wall_ms": self.seconds * 1000 * len(views)}
        return result


class RefreshTests(BudgetCase):
    """After an edit, kept views are captured again within a time budget, most recently asked first."""

    def keep(self, scene, operations):
        for operation in operations:
            self.evidence.admit(operation, scene.call(operation))
        scene.calls.clear()

    def refresh(self, scene):
        self.evidence.invalidate()
        started = scene.now
        self.evidence.clock = scene.clock
        report = self.evidence.refresh(scene.call, lambda: True)
        return report, scene.now - started

    def test_on_a_light_scene_every_kept_view_is_captured_again_in_grouped_calls(self):
        scene = Scene(self)
        twelve = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES[:6]]
        self.keep(scene, twelve)
        report, seconds = self.refresh(scene)
        self.assertEqual(sum(len(result["views"]) for result in report["refreshed_views"]), 12)
        self.assertNotIn("stale_views", report)
        self.assertEqual(len(scene.calls), 4, "three views a call, as before")
        self.assertLess(seconds, 1.0)
        self.assertEqual(len(self.evidence.take_images()), 12)
        self.assertFalse(self.evidence.dirty)

    def test_on_a_heavy_scene_one_edit_spends_a_few_seconds_and_names_the_views_it_left(self):
        scene = Scene(self, seconds=3.4)  # What one view of the heaviest island stage took.
        twelve = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES[:6]]
        self.keep(scene, twelve)
        report, seconds = self.refresh(scene)
        self.assertEqual(scene.calls, [("LIT", ["BOTTOM"])], "the view asked for last, alone")
        self.assertLessEqual(seconds, REFRESH_SECONDS)
        self.assertAlmostEqual(seconds, 3.4, msg="not the 40.8 s that all twelve would have taken")
        stale = report["stale_views"]
        self.assertEqual(len(stale), 11)
        self.assertEqual({entry["reason"] for entry in stale}, {REFRESH_BUDGET})
        self.assertEqual(stale[0], {"tool": "angelo_view", "arguments": {"views": ["TOP"], "shading": "LIT"},
                                    "reason": "refresh_budget"}, "as the model can ask for it again")
        self.assertIn("Ask for one again with the same arguments to see it now; it is not a new view",
                      report["stale_views_note"])
        self.assertIn("at most 5 s per edit", report["stale_views_note"])
        (fresh,) = self.evidence.take_images()
        self.assertEqual(fresh["view"], "BOTTOM", "a stale view is never attached")
        self.assertEqual(len(self.evidence.dirty), 11)

    def test_a_stale_view_asked_for_again_is_fresh_is_not_a_new_view_and_goes_first_next_time(self):
        scene = Scene(self, seconds=3.4)
        twelve = [view(axis, shading) for shading in ("SOLID", "LIT") for axis in AXES[:6]]
        self.keep(scene, twelve)
        self.refresh(scene)
        self.evidence.take_images()
        again = view("FRONT")  # The first one asked, stale since the edit.
        self.assertIsNone(self.evidence.problem([again]), "twelve are kept, and this is one of them")
        self.evidence.admit(again, scene.call(again))
        self.assertEqual([record["view"] for record in self.evidence.take_images()], ["FRONT"])
        scene.calls.clear()
        self.refresh(scene)
        self.assertEqual(scene.calls, [("SOLID", ["FRONT"])], "now the most recently asked view")

    def test_views_that_never_said_what_they_cost_are_measured_while_they_are_captured(self):
        scene = Scene(self, seconds=3.0, report=False)  # A sculpt view's result carries no timing.
        self.keep(scene, [view(axis) for axis in AXES[:6]])
        report, seconds = self.refresh(scene)
        self.assertEqual(scene.calls, [("SOLID", ["BOTTOM", "TOP", "RIGHT"])], "one grouped call, then the clock decides")
        self.assertEqual((round(seconds, 6), len(report["stale_views"])), (9.0, 3))

    def test_a_capture_that_fails_is_a_stale_view_with_its_error_and_the_others_are_still_captured(self):
        scene = Scene(self)
        self.keep(scene, [view(axis) for axis in ("FRONT", "RIGHT", "TOP")] + [view("ISO", "LIT")])
        scene.fail["ISO"] = "Finish the current Blender interaction before capturing a view"
        report, _seconds = self.refresh(scene)
        self.assertEqual(report["stale_views"], [{"tool": "angelo_view", "arguments": {"views": ["ISO"], "shading": "LIT"},
                                                  "reason": CAPTURE_FAILED,
                                                  "error": "Finish the current Blender interaction before capturing a view"}])
        self.assertEqual(sorted(record["view"] for record in self.evidence.take_images()), ["FRONT", "RIGHT", "TOP"])
        self.assertIn("Do not repeat edits because a view is stale", report["stale_views_note"])

    def test_a_stop_or_a_document_change_during_the_refresh_still_ends_it(self):
        scene = Scene(self)
        self.keep(scene, [view("FRONT")])
        scene.fail["FRONT"] = "Document changed or turn stopped"
        report, _seconds = self.refresh(scene)
        self.assertEqual(report, "Document changed or turn stopped")
        self.evidence.invalidate()
        self.assertIsNone(self.evidence.refresh(scene.call, lambda: False))


class RefreshTurnTests(BudgetCase):
    """The whole turn: an edit, a bounded refresh, and what the next round reads."""

    def test_a_failed_refresh_goes_to_the_model_and_the_turn_completes(self):
        looks = [view("FRONT"), view("ISO", "LIT")]
        scene_fail = {"ISO": "A 3D Viewport is required to capture a view"}
        provider = ClaudeProvider(self.host, {})
        scene = Scene(self)
        edited = []

        def native(name, arguments, *, owner):
            if name == "angelo_view":
                scene.fail = scene_fail if edited else {}
                return scene.call({"tool": name, "arguments": arguments})
            if name == "angelo_apply_batch":
                edited.append(arguments)
                return {"success": True}
            return {"objects": []}
        provider._turn = Mock(side_effect=[
            {"reply": "", "operations": looks, "verify": True},
            {"reply": "", "operations": [EDIT], "verify": True},
            {"reply": "Moved; the lit view could not be taken again.", "operations": [], "verify": False}])
        provider._native_call = Mock(side_effect=native)
        self.assertEqual(provider.run(None, "Move it and check", observation={}), "completed")
        self.assertEqual(len(edited), 1, "the edit ran once and was not retried")
        last = provider._turn.call_args
        self.assertEqual([record["view"] for record in last.kwargs["references"]], ["FRONT"], "only the fresh view is attached")
        told = json.loads(last.args[1].split("\n", 1)[1])["result"]
        self.assertEqual(told["stale_views"], [{"tool": "angelo_view", "arguments": {"views": ["ISO"], "shading": "LIT"},
                                                "reason": "capture_failed",
                                                "error": "A 3D Viewport is required to capture a view"}])
        self.assertIn("Do not repeat edits because a view is stale", told["stale_views_note"])
        self.assertEqual(len(told["refreshed_views"]), 1)
        rows = [str(message) for message in self.host.state["messages"]]
        self.assertFalse(any("fresh visual verification failed" in row for row in rows))


    def test_a_turn_on_a_heavy_scene_spends_seconds_not_a_minute_and_a_stale_view_is_fresh_when_asked_again(self):
        scene = Scene(self, seconds=3.4)
        four = [view("FRONT"), view("RIGHT"), view("TOP"), view("ISO", "LIT")]
        marks = {}

        def native(name, arguments, *, owner):
            if name == "angelo_view":
                return scene.call({"tool": name, "arguments": arguments})
            if name == "angelo_apply_batch":
                marks["edit"] = (scene.now, len(scene.calls))
                return {"success": True}
            return {"objects": []}
        provider = ClaudeProvider(self.host, {})
        provider._turn = Mock(side_effect=[
            {"reply": "", "operations": four, "verify": True},
            {"reply": "", "operations": [EDIT], "verify": True},
            {"reply": "", "operations": [view("FRONT")], "verify": True},
            {"reply": "Checked from the front and in the lit view.", "operations": [], "verify": False}])
        provider._native_call = Mock(side_effect=native)
        with patch.object(ClaudeVisualEvidence, "clock", staticmethod(scene.clock)):
            self.assertEqual(provider.run(None, "Move it and check", observation={}), "completed")
        rounds = provider._turn.call_args_list
        after_edit = json.loads(rounds[2].args[1].split("\n", 1)[1])["result"]
        self.assertEqual([record["view"] for record in rounds[2].kwargs["references"]], ["ISO"],
                         "the view asked for last is fresh; nothing stale is attached")
        self.assertEqual([entry["arguments"]["views"][0] for entry in after_edit["stale_views"]], ["TOP", "RIGHT", "FRONT"])
        self.assertEqual({entry["reason"] for entry in after_edit["stale_views"]}, {"refresh_budget"})
        self.assertEqual(scene.calls[marks["edit"][1]:marks["edit"][1] + 1], [("LIT", ["ISO"])])
        # The model asks for one stale view again: one capture, no further refresh without an edit.
        self.assertEqual(scene.calls[marks["edit"][1] + 1:], [("SOLID", ["FRONT"])])
        self.assertEqual([record["view"] for record in rounds[3].kwargs["references"]], ["FRONT"])
        self.assertNotIn("stale_views", rounds[3].args[1], "said once, after the edit")


if __name__ == "__main__":
    unittest.main()
