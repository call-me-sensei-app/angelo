# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A shot from the active camera: first and last frame in EEVEE, the move as a Workbench MP4. No Blender needed.

The renders themselves are render jobs (long_jobs.rst), proven by the Blender suites; here: sizes per aspect, the
requests a shot queues, refusals before anything starts, a part refused after another started, and delivery once
every part has ended, to the click's callback and every registered receiver.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import angelo_agent  # noqa: E402
from angelo_agent import job_spec, shot_capture  # noqa: E402


def stubbed(name, value):
    """A stand-in for ``angelo_agent.<name>`` where ``from . import <name>`` finds it: the package attribute (set by
    any earlier import of the real module in the same process) and ``sys.modules``."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch.dict(sys.modules, {"angelo_agent." + name: value}))
    stack.enter_context(patch.object(angelo_agent, name, value, create=True))
    return stack


class Sizes(unittest.TestCase):
    def test_every_aspect_has_even_sides_and_its_short_side(self):
        self.assertEqual(shot_capture.frame_size("16:9", 720), (1280, 720))
        self.assertEqual(shot_capture.frame_size("9:16", 720), (720, 1280))
        self.assertEqual(shot_capture.frame_size("1:1", 481), (480, 480))
        self.assertEqual(shot_capture.frame_size("21:9", 480), (1120, 480))
        for aspect in shot_capture.ASPECTS:
            for side in (480, 720, 1080):
                width, height = shot_capture.frame_size(aspect, side)
                self.assertEqual((width % 2, height % 2, min(width, height)), (0, 0, side), aspect)
        with self.assertRaises(ValueError):
            shot_capture.frame_size("2:1", 720)

    def test_each_capture_has_its_own_folder(self):
        self.assertEqual(shot_capture.folder_name("Cam 01/main", 0)[:20], "shots/Cam_01_main-19")
        self.assertTrue(shot_capture.folder_name("..", 0).startswith("shots/camera-"))


class Requests(unittest.TestCase):
    def test_a_shot_is_two_eevee_stills_and_a_workbench_mp4(self):
        parts = dict(shot_capture.requests((1, 120), "shots/c", aspect="9:16"))
        self.assertEqual(list(parts), ["first", "last", "motion"])
        self.assertEqual((parts["first"]["frame_start"], parts["last"]["frame_start"]), (1, 120))
        self.assertEqual(parts["first"]["engine"], "BLENDER_EEVEE")
        self.assertEqual(parts["first"]["resolution"], [720, 1280])
        motion = parts["motion"]
        self.assertEqual((motion["kind"], motion["format"], motion["engine"]),
                         ("RENDER_ANIMATION", "MP4", "BLENDER_WORKBENCH"))
        self.assertEqual((motion["frame_start"], motion["frame_end"], motion["resolution"]), (1, 120, [480, 854]))
        self.assertTrue(all(request["queue"] for request in parts.values()))   # Behind a running job, never refused.

    def test_a_one_frame_scene_is_one_still(self):
        self.assertEqual([part for part, _ in shot_capture.requests((5, 5), "s")], ["first"])
        self.assertEqual([part for part, _ in shot_capture.requests((1, 9), "s", parts=("first",))], ["first"])

    def test_one_part_alone(self):
        last = shot_capture.requests((1, 9), "s", parts=("last",))
        self.assertEqual([(part, request["frame_start"]) for part, request in last], [("last", 9)])
        self.assertEqual(shot_capture.requests((5, 5), "s", parts=("last",))[0][0], "last")   # Its only frame.
        self.assertEqual(shot_capture.requests((5, 5), "s", parts=("motion",)), [])          # No move to render.

    def test_the_engine_is_never_an_agent_field(self):
        schema = job_spec.JOB_TOOLS[0]["inputSchema"]["properties"]
        self.assertNotIn("engine", schema)
        with self.assertRaises(job_spec.JobRequestError):
            job_spec.validate_job("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png", "engine": "CYCLES"})


def window(camera=True, frames=(1, 48)):
    scene = SimpleNamespace(camera=SimpleNamespace(name="Shot Cam") if camera else None, frame_start=frames[0],
                            frame_end=frames[1])
    return SimpleNamespace(scene=scene)


class Starting(unittest.TestCase):
    def setUp(self):
        self.controller = SimpleNamespace(state={"id": "chat"}, files=SimpleNamespace(root="/w"))
        self.started, self.ids = [], iter(range(1, 99))
        patcher = stubbed("native_programs", SimpleNamespace(runtime_is_staging=lambda: False,
                                                             has_active_programs=lambda: False))
        patcher.__enter__()
        self.addCleanup(patcher.close)
        self.addCleanup(shot_capture.clear)

    def build(self, window, request, files):
        return SimpleNamespace(output=None)

    def submit(self, request, runner):
        self.started.append(request)
        return {"id": "j%d" % next(self.ids), "status": "queued"}

    def start(self, win, **kwargs):
        return shot_capture.start(win, self.controller, build=kwargs.pop("build", self.build),
                                  submit=kwargs.pop("submit", self.submit), now=0, **kwargs)

    def test_refusals_before_anything_starts(self):
        for win, controller in ((window(camera=False), self.controller), (window(), None)):
            with self.assertRaises(shot_capture.ShotError):
                shot_capture.start(win, controller, build=self.build, submit=self.submit)
        self.assertEqual(self.started, [])

    def test_the_parts_queue_and_deliver_once_all_ended(self):
        got = []
        shot = self.start(window(), on_done=got.append)
        self.assertEqual(shot.jobs, {"first": "j1", "last": "j2", "motion": "j3"})
        self.assertEqual([request["output"] for request in self.started][0][-9:], "first.png")
        with tempfile.TemporaryDirectory() as folder:
            first, motion = Path(folder) / "first.png", Path(folder) / "motion"
            first.write_bytes(b"png")
            motion.mkdir()
            (motion / "motion.mp4").write_bytes(b"mp4")
            outputs = {"j1": first, "j3": motion}
            fake_ui = SimpleNamespace(output_path=lambda record: outputs.get(record["id"]))
            with stubbed("job_ui", fake_ui):
                shot_capture.job_ended({"id": "j1", "status": "done", "receipt": {}})
                shot_capture.job_ended({"id": "j2", "status": "cancelled", "reason": "", "receipt": {}})
                self.assertEqual(got, [])
                shot_capture.job_ended({"id": "j3", "status": "done", "receipt": {"video": "motion.mp4"}})
                shot_capture.job_ended({"id": "j3", "status": "done", "receipt": {}})   # Once only.
        self.assertEqual(got, [shot])
        self.assertEqual(set(shot.files), {"first", "motion"})
        self.assertEqual(shot.files["motion"].name, "motion.mp4")
        self.assertEqual(shot.errors, {"last": "cancelled"})
        self.assertEqual(shot.as_dict()["errors"], {"last": "cancelled"})

    def test_a_still_of_several_files_hands_on_its_picture(self):
        shot = self.start(window(), parts=("first",))
        with tempfile.TemporaryDirectory() as folder:
            published = Path(folder) / "first"
            (published / "Passes").mkdir(parents=True)
            (published / "first_L.png").write_bytes(b"png")
            fake_ui = SimpleNamespace(output_path=lambda record: published)
            with stubbed("job_ui", fake_ui):
                shot_capture.job_ended({"id": shot.jobs["first"], "status": "done", "receipt": {"outputs": [
                    {"file": "Passes/Depth.png", "node": "Passes"}, {"file": "first_L.png", "view": "left"}]}})
            self.assertEqual(shot.files["first"], published / "first_L.png")

    def test_a_motion_without_its_video_says_why(self):
        shot = self.start(window(), parts=("first", "motion"))
        with tempfile.TemporaryDirectory() as folder:
            fake_ui = SimpleNamespace(output_path=lambda record: Path(folder))
            with stubbed("job_ui", fake_ui):
                shot_capture.job_ended({"id": shot.jobs["motion"], "status": "failed", "reason": "x",
                                        "receipt": {"video_error": "the encoder ended with exit status 1"}})
        self.assertIn("exit status 1", shot.errors["motion"])

    def test_a_refusal_after_a_start_keeps_the_others(self):
        from angelo_agent.job_spec import JobRequestError

        def build(window, request, files):
            if request["kind"] == "RENDER_ANIMATION":
                raise JobRequestError("This build of Angelo writes no video", code="UNSUPPORTED")
            return SimpleNamespace(output=None)
        shot = self.start(window(), build=build)
        self.assertEqual(set(shot.jobs), {"first", "last"})
        self.assertIn("no video", shot.errors["motion"])

    def test_a_first_refusal_is_the_shots(self):
        from angelo_agent.job_spec import JobRequestError

        def build(*_args):
            raise JobRequestError("The scene has no active camera to render from")
        with self.assertRaises(shot_capture.ShotError) as raised:
            self.start(window(), build=build)
        self.assertIn("no active camera", raised.exception.fields["reason"])

    def test_a_one_frame_scene_has_no_move_to_render(self):
        with self.assertRaises(shot_capture.ShotError):
            self.start(window(frames=(4, 4)), parts=("motion",))
        self.assertEqual(self.started, [])

    def test_a_part_a_receiver_asked_for_is_not_broadcast(self):
        heard, got = [], []
        with patch.object(shot_capture, "receivers", [heard.append]):
            shot = self.start(window(), parts=("last",), on_done=got.append, broadcast=False)
            with stubbed("job_ui", SimpleNamespace(output_path=lambda record: None)):
                shot_capture.job_ended({"id": shot.jobs["last"], "status": "failed", "reason": "no", "receipt": {}})
        self.assertEqual((got, heard), ([shot], []))

    def test_receivers_hear_every_shot_and_one_failing_never_stops_the_rest(self):
        heard = []
        broken = MagicMock(side_effect=RuntimeError("tab closed"))
        with patch.object(shot_capture, "receivers", [broken, heard.append]):
            shot = self.start(window(frames=(3, 3)))
            fake_ui = SimpleNamespace(output_path=lambda record: None)
            with stubbed("job_ui", fake_ui):
                shot_capture.job_ended({"id": shot.jobs["first"], "status": "failed", "reason": "no", "receipt": {}})
        self.assertEqual(heard, [shot])
        broken.assert_called_once_with(shot)


if __name__ == "__main__":
    unittest.main()
