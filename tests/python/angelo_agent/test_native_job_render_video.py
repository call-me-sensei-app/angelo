# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Format MP4: an animation render's frames, then an H.264 video encoded from them by a child Angelo. No Blender needed.

The encode itself (the child, Blender's Sequencer and FFmpeg) is proven by the Blender suite on a release build;
these tests hold the contract around it: what a request may ask, when the child starts, what a stop does, and that
the MP4 is published only beside a complete set of frames and only when it is a whole file.
"""

import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import job_outputs, job_recovery, job_render_video, job_spec  # noqa: E402
from angelo_agent import job_render_outputs as render_outputs  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
import job_frame_fixtures as fixtures  # noqa: E402
from test_native_job_recovery import PLAN, render_runner  # noqa: E402


def box(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mp4(*kinds):
    return b"".join(box(kind, b"x" * 16) for kind in kinds or (b"ftyp", b"mdat", b"moov"))


class FakeProcess:
    def __init__(self, code=None, output=b""):
        self.code, self.output, self.terminated = code, output, False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated, self.code = True, -15

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


def popen_writing(code, output):
    def popen(arguments, stdout, **_kwargs):
        stdout.write(output)
        process = FakeProcess(code)
        process.arguments = arguments
        return process
    return popen


class Requests(unittest.TestCase):
    def test_mp4_is_for_animations_only(self):
        request = job_spec.validate_job("angelo_job_start", {"kind": "RENDER_ANIMATION", "output": "r/a",
                                                             "format": "MP4"})
        self.assertEqual(request["format"], "MP4")
        with self.assertRaises(job_spec.JobRequestError) as raised:
            job_spec.validate_job("angelo_job_start", {"kind": "RENDER_STILL", "output": "r/a.png", "format": "MP4"})
        self.assertIn("RENDER_ANIMATION", str(raised.exception))
        self.assertEqual(job_spec.VIDEO_FORMATS["MP4"], "PNG")   # Its frames are PNG files, checked whole.

    def test_the_declaration_offers_mp4_as_one_more_format(self):
        # One enum value, no sentence: the declaration rides on every turn (job_spec, FORMATS), and a refusal says
        # which kind writes it.
        self.assertIn("MP4", job_spec.JOB_TOOLS[0]["inputSchema"]["properties"]["format"]["enum"])


class Names(unittest.TestCase):
    def test_the_video_is_named_after_its_folder_and_never_a_node_folder(self):
        self.assertEqual(job_render_video.video_name("renders/sh010 take 2"), "sh010_take_2.mp4")
        self.assertEqual(job_render_video.video_name("renders/{x}"), "x.mp4")
        self.assertEqual(job_render_video.video_name(""), "video.mp4")
        self.assertEqual(job_render_video.video_name("anim", {"ANIM.MP4"}), "anim-2.mp4")

    def test_sides_are_even(self):
        self.assertEqual(job_render_video.even_size(1921, 1081), (1920, 1080))
        self.assertEqual(job_render_video.even_size(1, 3), (2, 2))

    def test_the_child_reads_the_frames_by_pattern_at_the_scenes_speed(self):
        request = job_render_video.child_request("/s", PLAN[0], (1, 49, 2), 24, 1.0, (1281, 721), "anim.mp4")
        self.assertEqual((request["prefix"], request["extension"], request["digits"]), ("frame_", ".png", 4))
        self.assertEqual(request["frames"], [1, 49, 2])
        self.assertEqual((request["fps"], request["fps_base"]), (24, 2.0))   # Every second frame lasts two.
        self.assertEqual(request["size"], [1280, 720])
        self.assertIsInstance(request["parent"], int)   # The child ends itself once this app is gone.
        self.assertTrue(request["dest"].endswith("anim.mp4"))

    def test_the_child_runs_isolated(self):
        line = job_render_video.command("/Angelo", {"a": 1})
        for flag in ("--background", "--factory-startup", "--disable-autoexec", "--offline-mode"):
            self.assertIn(flag, line)
        self.assertTrue(job_render_video.CHILD.is_file())
        self.assertEqual(line[-1], '{"a": 1}')

    def test_admission_needs_ffmpeg_and_one_view(self):
        plan = SimpleNamespace(outputs=PLAN)
        with patch.object(job_render_video, "available", return_value=False):
            with self.assertRaises(job_spec.JobRequestError) as raised:
                job_render_video.admit(plan, "r/anim")
        self.assertEqual(raised.exception.code, "UNSUPPORTED")
        two = [dict(PLAN[0], suffix="_L"), dict(PLAN[0], suffix="_R")]
        with patch.object(job_render_video, "available", return_value=True):
            self.assertEqual(job_render_video.admit(plan, "r/anim"), "anim.mp4")
            with self.assertRaises(job_spec.JobRequestError):
                job_render_video.admit(SimpleNamespace(outputs=two), "r/anim")


class WholeFile(unittest.TestCase):
    def check(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(data)
        self.addCleanup(Path(handle.name).unlink)
        return job_render_video.mp4_whole(handle.name)

    def test_complete_and_incomplete_files(self):
        self.assertTrue(self.check(mp4()))
        self.assertTrue(self.check(mp4(b"ftyp", b"moov", b"mdat")))   # Fast start: the index first.
        self.assertFalse(self.check(mp4()[:-3]))                        # Cut short.
        self.assertFalse(self.check(mp4(b"ftyp", b"mdat")))             # No index yet: still being written.
        self.assertFalse(self.check(mp4(b"mdat", b"ftyp", b"moov")))     # Not an MP4's start.
        self.assertFalse(self.check(b""))
        self.assertFalse(self.check(struct.pack(">I4s", 4, b"ftyp")))   # A box shorter than its header.
        wide = struct.pack(">I4sQ", 1, b"mdat", 16 + 8) + b"y" * 8
        self.assertTrue(self.check(box(b"ftyp") + wide + box(b"moov")))
        self.assertTrue(self.check(box(b"ftyp") + box(b"moov") + struct.pack(">I4s", 0, b"mdat") + b"z" * 9))
        self.assertFalse(job_render_video.mp4_whole("/no/such/file.mp4"))


class Encoding(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-video-")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def encoder(self, code, output, data=None):
        dest = self.folder / "anim.mp4"
        if data is not None:
            dest.write_bytes(data)
        return job_render_video.Encoder(["x"], dest, popen_writing(code, output))

    def test_a_clean_exit_with_a_whole_file(self):
        line = (job_render_video.MARK + '{"file": "%s"}\n' % (self.folder / "anim.mp4")).encode()
        self.assertEqual(self.encoder(0, b"noise\n" + line, mp4()).result(), (True, ""))

    def test_every_other_ending_is_a_reason(self):
        line = (job_render_video.MARK + '{"file": "%s"}\n' % (self.folder / "anim.mp4")).encode()
        self.assertIn("exit status 1", self.encoder(1, line, mp4()).result()[1])
        self.assertIn("did not name", self.encoder(0, b"no line\n", mp4()).result()[1])
        self.assertIn("did not name", self.encoder(0, job_render_video.MARK.encode() + b"{bad\n", mp4()).result()[1])
        self.assertIn("incomplete", self.encoder(0, line, mp4()[:-2]).result()[1])

    def test_stop_ends_the_child(self):
        encoder = self.encoder(None, b"")
        self.assertTrue(encoder.running())
        encoder.stop()
        self.assertTrue(encoder.process.terminated)


class Phase(unittest.TestCase):
    """``begin``/``poll``/``halt`` on a runner: the child starts only after a complete render."""

    def runner(self, **fields):
        values = dict(video="anim.mp4", video_result=None, stopping=False, moved=None, encoder=None,
                      staging=SimpleNamespace(path=Path("/staging")), resume_into=None,
                      plan=SimpleNamespace(outputs=PLAN, nodes=[]), start_frame=1, end_frame=4, step=1, total=4,
                      fps=(24, 1.0), facts={"resolution": [640, 360]}, _check_output_path=lambda: None,
                      identity=None, restored=[])
        values.update(fields)
        runner = SimpleNamespace(**values)
        runner.restore_now = lambda: runner.restored.append(True)
        return runner

    def begin(self, runner, frames=(1, 2, 3, 4), popen=None):
        with patch.object(job_recovery, "whole_frames", return_value=(list(frames), [], [], {})):
            state = job_render_video.begin(runner, "/Angelo", popen or popen_writing(None, b""))
        if runner.encoder is not None:
            self.addCleanup(runner.encoder.close)
        return state

    def test_a_complete_render_starts_the_child(self):
        runner = self.runner()
        state = self.begin(runner)
        self.assertTrue(state["encoding"])
        self.assertEqual((state["frames_done"], state["frames_total"]), (4, 4))
        self.assertIn("/Angelo", runner.encoder.process.arguments)
        self.assertEqual(runner.restored, [True])   # The person's settings are back before the child starts.

    def test_the_time_budget_ends_the_job_instead_of_encoding(self):
        runner = self.runner()
        with patch.object(job_render_video, "_ended_by_budget", return_value=True):
            self.assertIsNone(self.begin(runner))
        self.assertIsNone(runner.encoder)
        self.assertEqual(runner.restored, [])

    def test_nothing_is_encoded_after_a_stop_an_early_end_or_for_a_resume(self):
        for fields, frames in (({"stopping": True}, (1, 2, 3, 4)), ({}, (1, 2, 3)),
                               ({"resume_into": "anim"}, (1, 2, 3, 4)), ({"video_result": (True, "")}, (1, 2, 3, 4)),
                               ({"moved": object()}, (1, 2, 3, 4))):
            with self.subTest(fields=fields, frames=frames):
                runner = self.runner(**fields)
                self.assertIsNone(self.begin(runner, frames))
                self.assertIsNone(runner.encoder)

    def test_a_child_that_cannot_start_is_a_reason(self):
        def refuse(*_args, **_kwargs):
            raise OSError("no such program")
        runner = self.runner()
        self.assertIsNone(self.begin(runner, popen=refuse))
        self.assertFalse(runner.video_result[0])

    def test_poll_keeps_the_job_running_until_the_child_ends(self):
        runner = self.runner()
        self.begin(runner)
        self.assertTrue(job_render_video.poll(runner)["encoding"])
        runner.encoder.process.code = 1
        self.assertIsNone(job_render_video.poll(runner))
        self.assertIsNone(runner.encoder)
        self.assertFalse(runner.video_result[0])

    def test_halt_stops_the_child_and_says_so(self):
        runner = self.runner()
        self.begin(runner)
        process = runner.encoder.process
        job_render_video.halt(runner)
        self.assertTrue(process.terminated)
        self.assertEqual(runner.video_result, (False, "the encoding was stopped"))
        job_render_video.halt(runner)   # Nothing running: nothing to do.

    def test_the_runner_stop_halts_the_child_instead_of_blenders_job(self):
        from test_native_job_recovery import render_runner as make
        runner, stand_ins = make(Path("/r"), None, None, PLAN, (1, 4))
        process = FakeProcess()
        runner.encoder = SimpleNamespace(stop=lambda: setattr(process, "terminated", True))
        with patch.dict(sys.modules, stand_ins):
            from angelo_agent import job_runners
            job_runners.RenderRunner.stop(runner)
        self.assertTrue(runner.stopping and process.terminated)
        self.assertIsNotNone(runner.encoder)   # Its poll looks at the child until it has ended (Stopping).


# A child that ignores the polite stop (SIGTERM; on Windows terminate already kills) and says when it is ready.
STUCK = ("import pathlib, signal, sys, time\n"
         "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
         "pathlib.Path(sys.argv[1]).write_text('up')\n"
         "time.sleep(60)\n")


class Stopping(unittest.TestCase):
    """Stopping never waits for the child (the audit's P2: Cancel and a closing document called a blocking wait of
    up to twice ``STOP_SECONDS`` on the main thread). A real child that ignores the stop: the interface's ticks go on,
    it is killed after ``STOP_SECONDS``, and the job's staging folder outlives it."""

    TICK = 0.02

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-video-stop-")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        patcher = patch.object(job_render_video, "STOP_SECONDS", 0.6)
        patcher.start()
        self.addCleanup(patcher.stop)

    def stuck(self, owner="job-1"):
        ready = self.folder / ("ready-%s" % owner)
        encoder = job_render_video.Encoder([sys.executable, "-c", STUCK, str(ready)], self.folder / "anim.mp4",
                                           subprocess.Popen, owner=owner)
        self.addCleanup(lambda: (encoder.process.kill(), encoder.process.wait(5), encoder.close()))
        limit = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < limit:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "the child did not start")
        return encoder

    def ticks(self, until, first, limit=10):
        """The interface's timer loop: ``first`` on its first tick, ``reap`` on every tick; returns the tick times
        and how long ``first`` held its tick."""
        times, held, end = [], None, time.monotonic() + limit
        while time.monotonic() < end:
            times.append(time.monotonic())
            if held is None:
                started = time.monotonic()
                first()
                held = time.monotonic() - started
            job_render_video.reap()
            if until():
                break
            time.sleep(self.TICK)
        return times, held

    def assert_never_held(self, times, held):
        self.assertLess(held, 0.1)
        gaps = [after - before for before, after in zip(times, times[1:])]
        self.assertLess(max(gaps), 0.25, "the interface stopped ticking")
        self.assertGreater(times[-1] - times[0], 0.5)   # It ran past the kill, ticking all along.

    def test_a_stop_returns_at_once_and_the_child_is_killed_later(self):
        encoder, ended = self.stuck(), []
        times, held = self.ticks(lambda: bool(ended),
                                 lambda: encoder.stop(lambda: ended.append(encoder.process.poll())))
        self.assert_never_held(times, held)
        self.assertTrue(encoder.killed)
        self.assertIsNotNone(ended[0])          # What waited on it ran only once the child had ended.
        self.assertIsNone(job_render_video.reap())
        self.assertNotIn(encoder, job_render_video._running)

    def test_a_cancelled_job_keeps_polling_until_its_child_has_ended(self):
        encoder = self.stuck()
        runner = SimpleNamespace(encoder=encoder, video_result=None, total=4, _check_output_path=lambda: None)
        states = []

        def polled():   # The manager's poll on each tick: the job stays (stopping) while its child runs.
            states.append(job_render_video.poll(runner))
            return states[-1] is None
        times, held = self.ticks(polled, lambda: job_render_video.halt(runner))
        self.assert_never_held(times, held)
        self.assertTrue(len(states) > 5 and all(state is not None for state in states[:-1]))
        self.assertFalse(states[0]["encoding"])   # Stopping, no longer making the MP4.
        self.assertIsNone(runner.encoder)
        self.assertEqual(runner.video_result, (False, "the encoding was stopped"))

    def test_a_discarded_job_removes_its_staging_only_after_its_child(self):
        class Base:
            def __init__(self):
                self.discarded = None

            def abandon(self):
                self.discarded = encoder.process.poll()

        class Job(job_render_video.EncodePhase, Base):
            pass
        encoder = self.stuck()
        job = Job()
        job.encoder, job.identity = encoder, "job-1"
        times, held = self.ticks(lambda: job.discarded is not None, job.abandon)
        self.assert_never_held(times, held)
        self.assertIsNone(job.encoder)
        self.assertIsNotNone(job.discarded)   # The child had ended when its folder was removed.

    def test_a_closing_documents_frames_wait_for_the_child(self):
        encoder = self.stuck("job-2")
        self.assertTrue(job_render_video.writing("job-2"))
        self.assertFalse(job_render_video.writing("job-3") or job_render_video.writing(None))
        times, held = self.ticks(lambda: not job_render_video.writing("job-2"),
                                 lambda: job_render_video.detach(SimpleNamespace(encoder=encoder, video_result=None)))
        self.assert_never_held(times, held)

    def test_a_finish_while_the_child_runs_keeps_the_frames_for_later(self):
        from angelo_agent import job_render_keep
        runner = SimpleNamespace(identity="job-4", plan=SimpleNamespace(outputs=PLAN), staging=object(),
                                 _retain=lambda receipt: ("failed", job_recovery.RETAINED, receipt))
        with patch.object(job_render_video, "writing", lambda owner: owner == "job-4"):
            status, reason, _ = job_render_keep.KeepFrames._finish_animation(runner, False)
        self.assertEqual((status, reason), ("failed", job_recovery.RETAINED))

    def test_quit_ends_every_child_without_bpy(self):
        encoder = self.stuck("job-5")
        with patch.object(job_render_video, "QUIT_SECONDS", 0.3):
            started = time.monotonic()
            job_render_video._end_all()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNotNone(encoder.process.poll())


class Outcomes(unittest.TestCase):
    def outcome(self, result, complete, resume=None):
        runner = SimpleNamespace(video="anim.mp4", video_result=result, resume_into=resume)
        return job_render_video.outcome(runner, complete)

    def test_every_ending_says_whether_there_is_a_video(self):
        self.assertEqual(self.outcome((True, ""), True), ("anim.mp4", {"video": "anim.mp4"}))
        self.assertEqual(self.outcome((False, "the encoding was stopped"), False)[1],
                         {"video_error": "the encoding was stopped"})
        self.assertEqual(self.outcome(None, False)[1], {"video_error": job_render_video.STOPPED_EARLY})
        self.assertIn("stopped", self.outcome((True, ""), False)[1]["video_error"])
        self.assertEqual(self.outcome((True, ""), True, resume="anim"), (None, {}))

    def test_a_resume_renders_frames_in_the_starts_engine(self):
        from angelo_agent import job_resume
        record = {"id": "j5", "request": {"kind": "RENDER_ANIMATION", "format": "MP4", "engine": "BLENDER_WORKBENCH",
                                          "minutes": 30.0, "output": "shots/c/motion"},
                  "detail": {"recovery": {"root": "/w", "plan": [], "frames": [1, 48], "step": 1},
                             "output_path": "/w/shots/c/motion"}}
        with patch.object(job_resume, "_usable", lambda saved: saved):
            request = job_resume.resume_request(record, 31, 48)
        self.assertEqual((request["format"], request["engine"]), ("PNG", "BLENDER_WORKBENCH"))


class Publication(unittest.TestCase):
    """The finish of a format MP4 render, over real staging: the video goes beside a complete set of frames."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-video-end-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()

    def render(self, video_data, result, frames=4):
        output = job_outputs.admit_output(FileWorkspace(self.root), "renders/anim")
        staging = job_outputs.Staging(self.root, output, "j4").create()
        for frame in range(1, frames + 1):
            (staging.path / render_outputs.file_name(PLAN[0], frame)).write_bytes(fixtures.png())
        if video_data is not None:
            (staging.path / "anim.mp4").write_bytes(video_data)
        runner, stand_ins = render_runner(self.root, output, staging, PLAN, (1, frames))
        runner.request["format"] = "MP4"
        runner.video, runner.video_result, runner.encoder = "anim.mp4", result, None
        return runner, stand_ins, staging

    def finish(self, runner, stand_ins, stopped=False):
        with patch.dict(sys.modules, stand_ins):
            return runner.finish(stopped)

    def test_the_video_is_published_with_its_frames(self):
        runner, stand_ins, staging = self.render(mp4(), (True, ""))
        status, reason, receipt = self.finish(runner, stand_ins)
        self.assertEqual((status, reason, receipt["video"]), ("done", "", "anim.mp4"))
        published = sorted(path.name for path in (self.root / "renders/anim").iterdir())
        self.assertEqual(published, ["anim.mp4"] + ["frame_%04d.png" % frame for frame in range(1, 5)])
        self.assertEqual(receipt["files"], 5)
        self.assertFalse(staging.path.exists())

    def test_a_failed_encode_publishes_the_frames_and_says_why(self):
        runner, stand_ins, _staging = self.render(mp4()[:-4], (False, "the encoder ended with exit status 1"))
        status, reason, receipt = self.finish(runner, stand_ins)
        self.assertEqual((status, reason), ("failed", job_render_video.FAILED))
        self.assertIn("exit status 1", receipt["video_error"])
        self.assertNotIn("unexpected", receipt)   # Its own partial file is no stranger's.
        published = sorted(path.name for path in (self.root / "renders/anim").iterdir())
        self.assertEqual(published, ["frame_%04d.png" % frame for frame in range(1, 5)])

    def test_a_stopped_render_publishes_frames_only(self):
        runner, stand_ins, _staging = self.render(mp4(), (False, "the encoding was stopped"))
        status, _reason, receipt = self.finish(runner, stand_ins, stopped=True)
        self.assertEqual(status, "cancelled")
        self.assertNotIn("video", receipt)
        self.assertFalse((self.root / "renders/anim/anim.mp4").exists())


class Approval(unittest.TestCase):
    def test_the_approval_names_the_video(self):
        from angelo_agent import job_approvals
        text = job_approvals.message("angelo_job_start", {"kind": "RENDER_ANIMATION", "output": "renders/anim",
                                                          "_facts": {"video": {"file": "anim.mp4", "codec": "H.264"}}})
        self.assertIn("anim.mp4", text["body"])


if __name__ == "__main__":
    unittest.main()
