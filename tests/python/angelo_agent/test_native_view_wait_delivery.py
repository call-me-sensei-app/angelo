# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Start a render job, wait for it and look at it in one turn: the whole path on each runtime. No Blender.

* A real ``Controller`` holds the waiting call as it holds a look: pending,
  counted as a call the host holds (so the turn's clocks rest), answered with
  the picture when the job ends, and ended by Stop without touching the job.
* A Claude plan starts the job and asks for ``LAST_JOB`` in the same plan; a
  Codex turn starts it and asks for its ID. Over the real providers and the
  scripted runtimes, the picture each model receives is decoded again.
* A job that will not end in time is an answer the model reads; the turn goes
  on to its reply.
"""

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import picture_fakes  # noqa: E402
from angelo_agent import job_host, view_spec, view_wait  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.routing import RuntimeContext  # noqa: E402
from angelo_agent.view_spec import validate_view  # noqa: E402
from read_tools_stand_ins import ClaudeStandIn, CodexStandIn, final, plan  # noqa: E402
from test_native_view_wait import WaitCase  # noqa: E402

START, VIEW = "angelo_job_start", "angelo_view"
STILL = {"kind": "RENDER_STILL", "output": "renders/review.png"}
WARM = (230, 150, 60)


class ControllerWaitTests(WaitCase):
    """The host path: admission without approval, the pending call, the answer, Stop."""

    def setUp(self):
        super().setUp()
        self.host = Controller(self.temp.name)
        self.host.busy = True
        self.host.set_document(("scratch",))
        self.records["j3"]["session"] = self.host.state["id"]
        self.replies = []
        self.executor = SimpleNamespace(observation_store=None, studio_context=None, precondition=Mock(),
                                        execute=lambda name, arguments: self.finished.finished_image(
                                            self.blender.window, validate_view(name, arguments),
                                            self.executor.observation_store, self.host))

    def tearDown(self):
        self.host.close()
        super().tearDown()

    def start(self, image="j3"):
        self.host.request_tool("wait", VIEW, {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": image},
                               self.replies.append)
        self.host.pump_tools(self.executor, self.host.document, budget=ToolDispatchBudget(seconds=60))
        return self.host.pending[self.host.state["id"] + "/wait"]

    def test_the_call_waits_for_the_job_and_is_answered_with_its_picture(self):
        call = self.start()
        self.assertEqual(self.replies, [], "no approval and no answer yet")
        self.assertFalse(call.approval)
        self.assertIn(call.id, self.host.media_jobs)
        context = SimpleNamespace(host=self.host, request_fence=SimpleNamespace(epoch=call.epoch, generation=call.generation))
        self.assertTrue(RuntimeContext.tools_pending(context), "a call the host holds: turn_activity rests")
        self.host.pump_events()
        self.assertEqual(self.replies, [])
        self.advance(45)
        self.finish((230, 150, 60, 255))
        self.host.pump_events()
        (result,) = self.replies
        self.assertEqual(result["rendered"]["waited_seconds"], 45)
        self.assertEqual(self.host.references.tool_images(result)[0]["id"], result["views"][0]["view_id"])
        self.assertFalse(self.host.media_jobs or self.host.pending)
        self.assertFalse(RuntimeContext.tools_pending(context))

    def test_the_call_lives_as_long_as_the_request_may_wait(self):
        import time
        call = self.start()
        remaining = call.expires - time.monotonic()
        self.assertGreater(remaining, view_spec.WAIT_SECONDS, "the wait's allowance, not the 180 s approval wait")
        self.assertLess(remaining, view_spec.WAIT_SECONDS + view_wait.GRACE_SECONDS + 5)

    def test_stop_answers_the_call_and_leaves_the_job_running(self):
        self.start()
        self.advance(20, progress=0.3)
        self.host.stop()
        (result,) = self.replies
        self.assertIn("The wait for job j3 was stopped with its turn; the job itself goes on", result["error"])
        self.assertNotIn("billed", result["error"])
        self.assertEqual(self.records["j3"]["status"], "running")

    def test_a_read_only_chat_may_wait_and_look(self):
        self.host.state["options"]["approval"] = "READ_ONLY"
        self.start("LAST_JOB")
        self.assertEqual(len(self.host.media_jobs), 1)

    def test_a_wait_passes_the_guard_of_a_job_that_locks_blender(self):
        manager = SimpleNamespace(active=lambda: {"id": "j3", "kind": "RENDER_STILL", "progress": 0.2},
                                  locks_interface=lambda: True)
        from unittest.mock import patch
        with patch.object(job_host, "_manager", lambda: manager):
            job_host.guard(VIEW, {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "j3"})
            job_host.guard(VIEW, {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "LAST_JOB"})
            for arguments in ({"views": ["SCENE_CAMERA"], "shading": "RENDERED"}, {"views": ["FRONT"]},
                              {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "RENDER_RESULT"}):
                with self.subTest(arguments=arguments), self.assertRaisesRegex(job_host.JobBusyError, "has locked Blender"):
                    job_host.guard(VIEW, arguments)


class Renderer:
    """The host side for the scripted runtimes: a job that is started, and the view of it."""

    def __init__(self, references):
        self.references, self.started, self.slow = references, [], False

    def execute(self, name, arguments):
        if name == START:
            self.started.append(arguments)
            return {"job": "j1", "status": "running", "kind": arguments["kind"], "output": arguments["output"],
                    "note": "Runs in the background. To see its picture in this turn, call angelo_view with shading "
                            "RENDERED and image j1: that call waits for the job, within bounds."}
        if name == VIEW:
            if not self.started:
                return {"error": "This conversation has started no render job yet"}
            record = {"id": "j1", "kind": "RENDER_STILL", "status": "running", "progress": 0.007, "eta_s": 2840.0,
                      "started": None, "detail": {}}
            if self.slow:
                return view_wait.still_running(record, 20, 580, "will_not_end_in_time")
            image = self.references.new_observation("Job j1 image · rendered")
            pixels = numpy.empty((36, 64, 3), dtype=numpy.uint8)
            pixels[...] = WARM
            Path(image["path"]).write_bytes(picture_fakes.png_bytes(pixels))
            return {"success": True, "read_only": True, "scene_changed": False, "shading": "RENDERED",
                    "views": [dict(image, view="SCENE_CAMERA", shading="RENDERED", width=64, height=36)],
                    "rendered": {"job": "j1", "complete": True, "waited_seconds": 41, "source_size": [1920, 1080]}}
        return {"scene": "Scene", "object_count": 1, "objects": [], "truncated": False}


def colour_of(png):
    return tuple(int(value) for value in picture_fakes.read_png(png)[10, 10, :3])


class ClaudeOneTurnTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        references = ReferenceStore(self.folder.name, "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        self.renderer = Renderer(references)
        self.claude = ClaudeStandIn(self.renderer.execute)
        self.addCleanup(self.claude.close)
        self.claude.host.references = references
        self.attached = []
        encode = references.claude_inputs

        def attach(records):
            blocks = encode(records)
            self.attached.append([colour_of(base64.b64decode(block["source"]["data"])) for block in blocks])
            return blocks
        references.claude_inputs = attach

    def test_one_plan_starts_the_render_and_asks_for_its_picture(self):
        status = self.claude.run(
            "Render a review still and tell me whether the light reads as warm.",
            plan((START, STILL), (VIEW, {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "LAST_JOB"}), verify=True),
            plan(reply="Rendered; the light reads as warm."))
        self.assertEqual(status, "completed")
        self.assertEqual([name for name, _arguments, _result in self.claude.dispatcher.calls][:2], [START, VIEW])
        self.assertEqual(self.attached, [[WARM]], "the job's picture is in the round after the plan")
        self.assertEqual(len(self.claude.prompts), 2, "two model rounds: the plan, then the reply")
        self.assertIn('"waited_seconds":41', self.claude.prompts[1])

    def test_a_job_too_long_for_the_turn_is_an_answer_and_the_turn_ends_with_a_reply(self):
        self.renderer.slow = True
        status = self.claude.run(
            "Render the final 4K frame and show me.",
            plan((START, STILL), (VIEW, {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "LAST_JOB"}), verify=True),
            plan(reply="The final frame is rendering (about 47 minutes left). I will show it when it is done."))
        self.assertEqual(status, "completed")
        self.assertEqual(self.attached, [])
        told = self.claude.prompts[1]
        self.assertIn("will_not_end_in_time", told)
        self.assertIn("End your turn and tell the person that the job is still running", told)
        self.assertFalse([row for row in self.claude.system_rows() if "failed" in row or "stopped" in row])


class CodexOneTurnTests(unittest.TestCase):
    def setUp(self):
        self.codex = CodexStandIn(lambda name, arguments: self.renderer.execute(name, arguments))
        self.addCleanup(self.codex.close)
        self.renderer = Renderer(self.codex.controller.references)

    def test_one_turn_starts_the_render_and_asks_for_its_picture(self):
        status = self.codex.run("Render a review still and tell me whether the light reads as warm.", [
            ("tool", START, STILL, "more"),
            ("tool", VIEW, {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "j1"}),
            final("Rendered; the light reads as warm.")])
        self.assertEqual(status, "completed")
        (_start, started), (_view, shown) = self.codex.server.answers
        self.assertTrue(started["success"] and shown["success"])
        self.assertIn("image j1: that call waits for the job", started["contentItems"][0]["text"])
        prefix = "data:image/png;base64,"
        (image,) = [item for item in shown["contentItems"] if item["type"] == "inputImage"]
        self.assertEqual(colour_of(base64.b64decode(image["imageUrl"][len(prefix):])), WARM)

    def test_a_job_too_long_for_the_turn_is_a_successful_answer_without_a_picture(self):
        self.renderer.slow = True
        status = self.codex.run("Render the final 4K frame and show me.", [
            ("tool", START, STILL, "more"),
            ("tool", VIEW, {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "j1"}),
            final("The final frame is rendering; about 47 minutes left.")])
        self.assertEqual(status, "completed")
        _started, shown = self.codex.server.answers
        self.assertTrue(shown[1]["success"], "not a failed tool call")
        self.assertEqual([item["type"] for item in shown[1]["contentItems"]], ["inputText"])
        self.assertIn("with about 47 min to go", shown[1]["contentItems"][0]["text"])


if __name__ == "__main__":
    unittest.main()
