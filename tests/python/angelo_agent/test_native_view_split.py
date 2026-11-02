# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""RENDERED with the scene camera and other named cameras is one direct call (``view_split``).

The schema offers RENDERED with several views; the tool refused every such call with a validation error (two recorded
direct calls of ``["SCENE_CAMERA", "FRONT"]``) although a Claude plan already ran it as a render and a LIT look. The
contract now takes the request that has that one meaning (``view_spec.rendered_split``): a direct call renders the
scene camera, then draws the others LIT, and answers as the plan's slot does, with every picture in ``views`` for
delivery. A plan still splits it into two calls, so the view budget counts its LIT views. Without Blender: the
render, the LIT capture and the window are stand-ins.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import plan_validation, studio_agent, tool_specs, view_spec, view_split  # noqa: E402

SPLIT = {"targets": [], "views": ["SCENE_CAMERA", "FRONT", "TOP"], "shading": "RENDERED", "resolution": 256}


def picture(name, shading):
    return {"id": name, "path": "/private/" + name + ".png", "view": name, "shading": shading}


class Pending(studio_agent.StudioPending):
    def __init__(self, timeout=150.0, stopped=None):
        super().__init__(lambda: self.answer, lambda: self.cancelled.append(True))
        self.answer, self.cancelled, self.timeout = None, [], timeout
        if stopped is not None:
            self.stopped_result = stopped


class Case(unittest.TestCase):
    def setUp(self):
        self.render, self.lit, self.asked = Pending(), None, []
        self.reads = []
        barrier = SimpleNamespace(read=lambda tool, records, result, credit: self.reads.append((tool, credit))
                                  or dict(result, recovery_read=True))
        self.controller = SimpleNamespace(executing_call=SimpleNamespace(id="call-1", parent_call=None),
                                          recovery_barrier=barrier)
        self.window = SimpleNamespace(name="window")

        def render(window, checked, references, controller):
            self.asked.append(("render", checked["views"], checked["shading"]))
            if isinstance(self.render, Exception):
                raise self.render
            return self.render

        def execute(window, args, references, controller, wait_shaders):
            self.asked.append(("lit", args["views"], args["shading"], wait_shaders))
            if isinstance(self.lit, Exception):
                raise self.lit
            return self.lit if self.lit is not None else {
                "success": True, "read_only": True, "scene_changed": False, "shading": "LIT",
                "views": [picture(view, "LIT") for view in args["views"]]}
        fakes = {"view_render": SimpleNamespace(begin=render), "view_capture": SimpleNamespace(execute=execute),
                 "view_warm": SimpleNamespace(WAIT_SECONDS=60.0)}
        for name, fake in fakes.items():
            module = ModuleType("angelo_agent." + name)
            module.__dict__.update(vars(fake))
            for each in (patch.dict(sys.modules, {"angelo_agent." + name: module}),
                         patch.object(angelo_agent, name, module, create=True)):
                each.start()
                self.addCleanup(each.stop)

    def begin(self, args=SPLIT):
        return view_split.begin(self.window, dict(args), "references", self.controller, True)

    def rendered(self, **fields):
        return dict({"success": True, "read_only": True, "scene_changed": False, "shading": "RENDERED",
                     "views": [picture("SCENE_CAMERA", "RENDERED")]}, **fields)


class ContractTests(Case):
    def test_the_contract_takes_it_and_a_plan_still_runs_it_as_two_calls(self):
        self.assertEqual(tool_specs.validate_call("angelo_view", dict(SPLIT))["views"], SPLIT["views"])
        problems, _ = plan_validation.plan_problems([{"tool": "angelo_view", "arguments": dict(SPLIT)}], None)
        self.assertEqual(problems, ["operations[0] (angelo_view): " + view_spec.RENDERED_CAMERA], "unchanged in a plan")
        screened = plan_validation.screen_plan([{"tool": "angelo_view", "arguments": dict(SPLIT)}], None)
        self.assertEqual([call["arguments"]["shading"] for call in screened.dispatched()], ["RENDERED", "LIT"])
        for refused in (dict(SPLIT, views=["FRONT", "TOP"]), dict(SPLIT, region=[0, 0, 0.5, 0.5]),
                        dict(SPLIT, views=["SCENE_CAMERA", "0f6c3c87-6b4e-4a35-9b7f-6c4c8f6f0c11"])):
            with self.subTest(arguments=refused), self.assertRaises(ValueError):
                view_spec.validate_view("angelo_view", refused)   # More than one meaning: refused as before.


class SplitTests(Case):
    def test_the_render_then_the_others_lit_in_one_answer(self):
        split = self.begin()
        self.assertIsInstance(split, studio_agent.StudioPending)
        self.assertEqual(split.timeout, 150.0 + 90.0, "the render's, then a LIT view's shader wait")
        self.assertEqual(self.asked, [("render", ["SCENE_CAMERA"], "RENDERED")], "the render starts in the call")
        self.assertIsNone(split.poll())
        self.render.answer = self.rendered()
        result = split.poll()
        self.assertEqual(self.asked[1], ("lit", ["FRONT", "TOP"], "LIT", True))
        self.assertEqual(result["normalized"], view_spec.RENDERED_SPLIT % "FRONT, TOP")
        self.assertTrue(result["success"])
        self.assertEqual([view["view"] for view in result["views"]], ["SCENE_CAMERA", "FRONT", "TOP"],
                         "every picture where the providers deliver them")
        self.assertEqual([part["shading"] for part in result["results"]], ["RENDERED", "LIT"])
        self.assertFalse(any("views" in part for part in result["results"]), "each picture listed once")
        self.assertIs(result["scene_changed"], False)
        self.assertTrue(result["results"][1]["recovery_read"], "the LIT read drawn later counts, as view_warm's")
        self.assertEqual(self.reads, [("angelo_view", True)])

    def test_a_refused_render_still_draws_the_others_and_answers_at_once(self):
        self.render = RuntimeError("The scene has no camera")
        result = self.begin()
        self.assertIsInstance(result, dict, "nothing pending")
        # Its error stays in its part: with one at the top, the providers deliver no picture (break-it review).
        self.assertEqual((result["success"], "error" in result), (False, False))
        self.assertEqual(result["results"][0]["error"], "The scene has no camera")
        self.assertIn("A call failed: The scene has no camera", result["normalized"])
        self.assertEqual([view["view"] for view in result["views"]], ["FRONT", "TOP"])
        self.assertEqual(self.reads, [], "read inside the call: the controller's own barrier reads it")

    def test_a_decided_render_ends_it_and_the_note_says_so(self):
        split = self.begin()
        self.render.answer = {"error": "The look was stopped with its turn.", "cancelled": True,
                              "read_only": True, "scene_changed": True}
        result = split.poll()
        self.assertEqual(len(self.asked), 1, "no LIT look after a stop")
        self.assertIn("Only 1 of its 2 calls were made", result["normalized"])
        self.assertTrue(result["cancelled"])
        self.assertIs(result["scene_changed"], True, "never unchanged when a part may have changed it")

    def test_a_lit_part_waiting_for_its_shaders_is_polled_and_a_stop_reaches_the_part_waiting(self):
        self.lit = Pending(stopped={"error": "LIT stopped"})
        split = self.begin()
        self.render.answer = self.rendered()
        self.assertIsNone(split.poll(), "the LIT part is pending (view_warm)")
        self.assertEqual(split.stopped_result, {"error": "LIT stopped"})
        split.cancel()
        self.assertEqual((self.lit.cancelled, self.render.cancelled), ([True], []))

    def test_a_window_gone_before_the_lit_part_is_its_failure(self):
        from angelo_agent import live_target
        split = self.begin()
        self.render.answer = self.rendered()
        with patch.object(live_target, "require", side_effect=RuntimeError("The window this view was asked in is "
                                                                              "gone; ask again.")):
            result = split.poll()
        self.assertNotIn("error", result, "the render's picture is delivered")
        self.assertEqual(result["results"][1]["error"], "The window this view was asked in is gone; ask again.")
        self.assertEqual([view["view"] for view in result["views"]], ["SCENE_CAMERA"], "the render is kept")

    def test_the_persons_cancel_on_the_render_card_ends_the_call(self):
        split = self.begin()
        self.render.answer = {"read_only": True, "scene_changed": False, "views": [], "shading": "RENDERED",
                              "rendered": {"image": False, "ended": "user", "seconds": 1.2},
                              "note": "No image: the person cancelled this look. Do not start another unless they ask."}
        result = split.poll()
        self.assertEqual(len(self.asked), 1, "no LIT look after the person's cancel")
        self.assertIn("Only 1 of its 2 calls were made: the host stopped after No image: the person cancelled",
                      result["normalized"])
        self.assertEqual(result["views"], [])

    def test_on_a_plan_too_the_persons_cancel_ends_the_lit_companion(self):
        from angelo_agent import plan_recovery
        cancelled = {"read_only": True, "scene_changed": False, "views": [], "shading": "RENDERED",
                     "rendered": {"image": False, "ended": "user", "seconds": 0.8}, "note": "No image: the person "
                     "cancelled this look. Do not start another unless they ask."}
        called = []
        slot = plan_recovery._normalized(cancelled, [{"tool": "angelo_view", "arguments": {}}], "Split.",
                                         lambda extra: called.append(extra) or {}, lambda: True,
                                         SimpleNamespace(pin_cameras=lambda d: None, admit=lambda e, p: None), [])
        self.assertEqual(called, [], "no LIT look after the person's cancel")
        self.assertIn("Only 1 of its 2 calls were made", slot["normalized"])
        stopped = dict(cancelled, rendered=dict(cancelled["rendered"], ended="stopped"))
        self.assertFalse(plan_recovery.cancelled_by_person(stopped), "a turn's stop is a stop, not the person's cancel")

    def test_a_decided_failure_keeps_its_error_with_pictures_drawn(self):
        self.lit = RuntimeError("Document changed or turn stopped before the LIT views")
        split = self.begin()
        self.render.answer = self.rendered()
        result = split.poll()
        self.assertEqual([view["view"] for view in result["views"]], ["SCENE_CAMERA"])
        self.assertEqual(result["error"], "Document changed or turn stopped before the LIT views", "a decision stays one")


if __name__ == "__main__":
    unittest.main()
