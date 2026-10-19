# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A RENDERED look reaching each runtime, with the stand-in providers and a real Controller.

* Claude: a typed plan's look is attached to the next round once, is never
  captured again after an edit, is not one of the retained views, and a look
  that ends without an image does not end the turn.
* Codex: an in-turn ``angelo_view`` call is answered with the image as an
  ``inputImage`` content item.
* The Controller keeps the call pending while the job renders, answers it when
  the job ends and stops the render with the turn.
* What each runtime is handed is decoded again: the pixels Blender rendered,
  a transparent hole included, are the pixels in its payload.
"""

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_fakes  # noqa: E402
from angelo_agent.claude_evidence import STALE_LOOK, VIEW_IMAGE_LIMIT, ClaudeVisualEvidence  # noqa: E402
from angelo_agent.view_picture import CHECKER  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.providers import ClaudeProvider, tool_content  # noqa: E402
from test_native_codex_plan import PlanTurnFixture  # noqa: E402
from rendered_view_case import LOOK, LookCase  # noqa: E402

EDIT = {"tool": "angelo_set_transform", "arguments": {"object": "Cube", "location": [0, 1, 0]}}
NO_IMAGE = {"read_only": True, "scene_changed": False, "views": [], "shading": "RENDERED",
            "rendered": {"image": False, "ended": "time_budget", "seconds": 120.4},
            "note": "No image: Blender was still preparing the scene for rendering when the look's 120 s budget ended"}


def look(**extra):
    return {"tool": "angelo_view", "arguments": dict(LOOK, **extra)}


def view(*views, shading="SOLID"):
    return {"tool": "angelo_view", "arguments": {"views": list(views), "shading": shading}}


def picture(host, name="SCENE_CAMERA view · rendered"):
    value = host.references.new_observation(name)
    Path(value["path"]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    return dict(value, view="SCENE_CAMERA", shading="RENDERED")


def rendered(image):
    return {"success": True, "read_only": True, "scene_changed": False, "views": [image], "shading": "RENDERED",
            "rendered": {"engine": "CYCLES", "complete": True, "seconds": 31.2}}


class ClaudeLookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Controller(self.directory.name)
        self.provider = ClaudeProvider(self.host, {})

    def tearDown(self):
        self.host.close()
        self.directory.cleanup()

    def run_plans(self, plans, results):
        self.provider._turn = Mock(side_effect=plans)
        self.provider._native_call = Mock(side_effect=results)
        return self.provider.run(None, "Is the lighting right?", observation={})

    def test_the_next_round_sees_the_look_and_a_later_edit_never_renders_it_again(self):
        image = picture(self.host)
        status = self.run_plans(
            [{"reply": "", "operations": [look()], "verify": True},
             {"reply": "", "operations": [EDIT], "verify": True},
             {"reply": "Warmer now.", "operations": [], "verify": False}],
            [rendered(image), {"objects": []}, {"success": True}, {"objects": []}])
        self.assertEqual(status, "completed")
        rounds = self.provider._turn.call_args_list
        self.assertEqual(rounds[1].kwargs["references"], [image], "the look is attached to the round after it")
        self.assertIn('"visual_evidence":[{"image":1', rounds[1].args[1])
        self.assertNotIn("references", rounds[2].kwargs, "nothing is attached after the edit")
        self.assertEqual([call.args[0] for call in self.provider._native_call.call_args_list],
                         ["angelo_view", "angelo_inspect_scene", "angelo_apply_batch", "angelo_inspect_scene"],
                         "the edit did not render the look again")

    def test_a_look_taken_before_an_edit_of_the_same_plan_is_kept_and_labelled(self):
        image = picture(self.host)
        self.run_plans([{"reply": "", "operations": [look(), EDIT], "verify": True},
                        {"reply": "Done.", "operations": [], "verify": False}],
                       [rendered(image), {"success": True}, {"objects": []}])
        (attached,) = self.provider._turn.call_args.kwargs["references"]
        self.assertEqual(attached["id"], image["id"])
        self.assertTrue(attached["name"].endswith(STALE_LOOK))
        self.assertIn(STALE_LOOK.strip(" ·"), self.provider._turn.call_args.args[1])

    def test_a_look_without_an_image_tells_the_model_and_the_turn_goes_on(self):
        status = self.run_plans([{"reply": "", "operations": [look()], "verify": True},
                                 {"reply": "The preview ran out of time; I have not seen the frame.",
                                  "operations": [], "verify": False}],
                                [NO_IMAGE, {"objects": []}])
        self.assertEqual(status, "completed")
        second = self.provider._turn.call_args
        self.assertNotIn("references", second.kwargs)
        self.assertIn("still preparing the scene", second.args[1])
        self.assertIn('"visual_evidence":[]', second.args[1])

    def test_a_view_that_cannot_be_captured_goes_back_to_the_model_and_the_turn_goes_on(self):
        failed = {"error": "A 3D Viewport is required to capture a view", "read_only": True, "scene_changed": False}
        status = self.run_plans([{"reply": "", "operations": [view("FRONT", shading="LIT"), look()], "verify": True},
                                 {"reply": "I could not look: no 3D Viewport is open.", "operations": [], "verify": False}],
                                [failed, {"error": "The scene has no active camera, and a RENDERED look renders the scene "
                                                   "camera", "read_only": True, "scene_changed": False}, {"objects": []}])
        self.assertEqual(status, "completed")
        told = self.provider._turn.call_args.args[1]
        self.assertIn("operations[0] (angelo_view) failed: A 3D Viewport is required to capture a view.", told)
        self.assertIn("operations[1] (angelo_view) failed: The scene has no active camera", told)
        self.assertIn("A failed lookup changes nothing.", told)
        self.assertNotIn("references", self.provider._turn.call_args.kwargs)

    def test_looks_and_finished_pictures_are_not_among_the_views_a_request_keeps(self):
        evidence = ClaudeVisualEvidence(self.host.references)
        kept = [view(axis, shading=shading) for shading in ("SOLID", "LIT")
                for axis in ("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM")]
        self.assertEqual(len(kept), VIEW_IMAGE_LIMIT)
        evidence.check_plan(kept + [look(), look(resolution=1024), look(region=[0.0, 0.0, 0.5, 0.5]),
                                    look(image="RENDER_RESULT"), look(image="j3", frame=12)])
        with self.assertRaisesRegex(Exception, "has %d of %d left" % (VIEW_IMAGE_LIMIT, VIEW_IMAGE_LIMIT)):
            evidence.check_plan(kept + [view("ISO")])

    def test_a_look_is_delivered_once_and_only_viewport_views_are_refreshed(self):
        evidence = ClaudeVisualEvidence(self.host.references)
        solid, image = picture(self.host, "FRONT view · solid"), picture(self.host)
        evidence.admit(view("FRONT"), {"views": [solid]})
        evidence.admit(look(), rendered(image))
        evidence.admit(look(), NO_IMAGE)  # No image, and no complaint about a missing one.
        self.assertEqual(len(evidence.requests), 1)
        evidence.invalidate()
        self.assertEqual([operation["arguments"]["shading"] for operation in evidence.refresh_operations()], ["SOLID"])
        (kept,) = evidence.take_images()
        self.assertEqual(kept["name"], image["name"] + STALE_LOOK)
        self.assertEqual(evidence.take_images(), [])


class CodexLookTests(PlanTurnFixture):
    def test_an_in_turn_look_is_answered_with_the_image(self):
        image = picture(self.c)
        self.results["angelo_view"] = [rendered(image)]
        self.run_turns([("tool", "angelo_view", look()["arguments"]), ("message", self.plan_text())])
        ((_identity, payload),) = self.p.rpc.responses
        self.assertTrue(payload["success"])
        self.assertEqual([item["type"] for item in payload["contentItems"]], ["inputText", "inputImage"])
        self.assertTrue(payload["contentItems"][1]["imageUrl"].startswith("data:image/png;base64,"))
        self.assertEqual(self.dispatched[0][1:], ("angelo_view", look()["arguments"]))

    def test_a_look_without_an_image_is_a_successful_text_answer(self):
        self.results["angelo_view"] = [NO_IMAGE]
        self.run_turns([("tool", "angelo_view", look()["arguments"]), ("message", self.plan_text())])
        ((_identity, payload),) = self.p.rpc.responses
        self.assertTrue(payload["success"])
        self.assertEqual([item["type"] for item in payload["contentItems"]], ["inputText"])
        self.assertIn("still preparing the scene", payload["contentItems"][0]["text"])

    def test_a_view_that_cannot_be_captured_is_a_failed_tool_output_and_the_turn_goes_on(self):
        self.results["angelo_view"] = [{"error": "A 3D Viewport is required to capture a view", "read_only": True,
                                        "scene_changed": False}]
        status = self.run_turns([("tool", "angelo_view", look()["arguments"]), ("message", self.plan_text())])
        ((_identity, payload),) = self.p.rpc.responses
        self.assertFalse(payload["success"])
        self.assertEqual([item["type"] for item in payload["contentItems"]], ["inputText"])
        self.assertIn("A 3D Viewport is required to capture a view", payload["contentItems"][0]["text"])
        self.assertEqual(status, "completed", "the model read the failure and ended its turn itself")

    def test_a_picture_whose_file_is_gone_is_a_failed_tool_output_too(self):
        image = picture(self.c)
        Path(image["path"]).unlink()
        self.results["angelo_view"] = [rendered(image)]
        status = self.run_turns([("tool", "angelo_view", look()["arguments"]), ("message", self.plan_text())])
        ((_identity, payload),) = self.p.rpc.responses
        self.assertFalse(payload["success"])
        self.assertEqual([item["type"] for item in payload["contentItems"]], ["inputText"])
        self.assertEqual(status, "completed")

    def test_a_text_only_model_gets_an_actionable_error_instead_of_a_picture(self):
        image = picture(self.c)
        controller = SimpleNamespace(references=self.c.references, models=[{"id": "text", "default": True, "images": False}],
                                     settings={}, state={"options": {}}, turn_options={})
        with self.assertRaisesRegex(ValueError, "image-capable model"):
            tool_content(controller, "angelo_view", rendered(image))


class ControllerLookTests(LookCase):
    """The whole host path: admission, the pending call, the job, the answer."""

    def setUp(self):
        super().setUp()
        self.host = Controller(self.temp.name)
        self.host.busy = True
        self.host.set_document(("scratch",))
        self.replies = []
        self.executor = SimpleNamespace(observation_store=None, studio_context=None, precondition=Mock(),
                                        execute=lambda name, arguments: self.render.begin(
                                            self.blender.window, arguments, self.executor.observation_store, self.host))

    def tearDown(self):
        self.host.close()
        super().tearDown()

    def start(self):
        self.host.request_tool("look", "angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED"}, self.replies.append)
        self.host.pump_tools(self.executor, self.host.document, budget=ToolDispatchBudget(seconds=60, started=self.clock()))
        return self.host.pending[self.host.state["id"] + "/look"]

    def test_the_call_waits_for_the_job_and_is_answered_with_the_image(self):
        call = self.start()
        self.assertEqual(self.replies, [], "no approval and no answer yet: the call is pending")
        self.assertFalse(call.approval)
        self.assertIn(call.id, self.host.media_jobs)
        record = self.manager.active()
        self.assertEqual((record["call_id"], record["session"]), (call.id, self.host.state["id"]))
        self.host.pump_events()
        self.assertEqual(self.replies, [])
        self.blender.progress = 1.0
        self.manager.poll()
        self.blender.end()
        self.manager.poll()
        self.host.pump_events()
        (result,) = self.replies
        self.assertTrue(result["success"])
        self.assertEqual(self.host.references.tool_images(result)[0]["id"], result["views"][0]["view_id"])
        self.assertEqual([item["type"] for item in tool_content(self.host, "angelo_view", result)],
                         ["inputText", "inputImage"])
        self.assertFalse(self.host.media_jobs or self.host.pending)
        row = next(message for message in self.host.state["messages"] if message.get("tool") == "angelo_view")
        self.assertEqual(row["status"], "completed")

    def test_a_look_that_cannot_start_is_answered_with_its_reason_and_nothing_is_left_pending(self):
        self.blender.scene.camera = None
        self.host.request_tool("look", "angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED"}, self.replies.append)
        self.host.pump_tools(self.executor, self.host.document, budget=ToolDispatchBudget(seconds=60, started=self.clock()))
        (result,) = self.replies
        self.assertIn("no active camera, and a RENDERED look renders the scene camera", result["error"])
        self.assertIn("LIT", result["error"], "and what to use instead")
        self.assertFalse(self.host.media_jobs or self.host.pending)
        self.assertIsNone(self.manager.active())
        self.assertEqual(self.blender.invoked, [], "nothing rendered")

    def test_a_pending_look_is_a_call_the_host_holds_so_no_turn_clock_runs(self):
        from angelo_agent.routing import RuntimeContext
        self.start()
        call = self.host.pending[self.host.state["id"] + "/look"]
        fence = SimpleNamespace(epoch=call.epoch, generation=call.generation)
        context = SimpleNamespace(host=self.host, request_fence=fence)
        self.assertTrue(RuntimeContext.tools_pending(context), "turn_activity rests while the look renders")
        self.blender.progress = 1.0
        self.manager.poll()
        self.blender.end()
        self.manager.poll()
        self.host.pump_events()
        self.assertFalse(RuntimeContext.tools_pending(context), "answered: the model's stretch starts again")

    def test_read_only_mode_allows_a_look(self):
        self.host.state["options"]["approval"] = "READ_ONLY"
        self.start()
        self.assertIsNotNone(self.manager.active(), "a look changes nothing, so a read-only chat may take one")

    def test_stop_ends_the_render_and_answers_without_the_provider_work_text(self):
        self.start()
        self.blender.progress = 0.3
        self.manager.poll()
        self.host.stop()
        (result,) = self.replies
        self.assertEqual(result["error"], "The look was stopped with its turn; nothing in the scene changed.")
        self.assertNotIn("billed", result["error"])
        self.assertEqual((self.blender.stops, self.manager.active()["reason"]), (1, "stopped"))
        self.blender.end()
        self.manager.poll()
        self.assertEqual(self.reference_files(), [])

    def test_the_pending_call_outlives_the_ordinary_approval_lifetime(self):
        import time
        call = self.start()
        self.assertGreater(call.expires - time.monotonic(), 120, "the budget, not the 180 s approval wait, bounds it")


class DeliveredPixelTests(LookCase):
    """What each runtime is handed is the picture Blender rendered: its payload decodes to the planted pixels."""

    OPERATION = {"tool": "angelo_view", "arguments": dict(LOOK)}

    def setUp(self):
        super().setUp()
        self.host = SimpleNamespace(references=self.references, models=[], settings={}, state={"options": {}},
                                    turn_options={})

    def codex_pixels(self, result):
        text, image = tool_content(self.host, "angelo_view", result)
        self.assertEqual((text["type"], image["type"]), ("inputText", "inputImage"))
        prefix = "data:image/png;base64,"
        self.assertTrue(image["imageUrl"].startswith(prefix))
        return picture_fakes.read_png(base64.b64decode(image["imageUrl"][len(prefix):]))

    def claude_pixels(self, result):
        evidence = ClaudeVisualEvidence(self.references)
        evidence.admit(self.OPERATION, result)
        (block,) = self.references.claude_inputs(evidence.take_images())
        self.assertEqual((block["type"], block["source"]["media_type"]), ("image", "image/png"))
        return picture_fakes.read_png(base64.b64decode(block["source"]["data"]))

    def test_the_rendered_colour_is_in_both_runtimes_payloads(self):
        self.blender.scene.pixels = (250, 20, 30, 255)  # A wrong, red light over the whole frame.
        _pending, result = self.run_look()
        for name, pixels in (("codex", self.codex_pixels(result)), ("claude", self.claude_pixels(result))):
            with self.subTest(runtime=name):
                self.assertEqual(pixels.shape, (432, 768, 4))
                self.assertEqual({tuple(pixel) for pixel in pixels[::37, ::41].reshape(-1, 4).tolist()}, {(250, 20, 30, 255)})

    def test_a_transparent_hole_reaches_both_runtimes_as_a_checkerboard_and_the_result_says_so(self):
        self.blender.scene.pixels = (0, 0, 0, 0)  # A transparent film: nothing but alpha.
        _pending, result = self.run_look()
        self.assertEqual(result["rendered"]["transparent"], 1.0)
        self.assertIn("100% of the picture is transparent or half transparent", result["note"])
        greys = {(CHECKER[0],) * 3 + (255,), (CHECKER[1],) * 3 + (255,)}
        for name, pixels in (("codex", self.codex_pixels(result)), ("claude", self.claude_pixels(result))):
            with self.subTest(runtime=name):
                self.assertEqual({tuple(pixel) for pixel in pixels[::7, ::5].reshape(-1, 4).tolist()}, greys,
                                 "neither black nor white: the checkerboard")


if __name__ == "__main__":
    unittest.main()
