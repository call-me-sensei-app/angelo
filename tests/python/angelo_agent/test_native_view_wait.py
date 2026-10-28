# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Waiting for a render job inside a turn, then looking at it (``view_wait``), on the host.

A view of a job that has not ended stays pending until the job ends, and then
answers with its picture; the wait is bounded per request, a job that will not
end in time is not waited for, and every ending is a result the model can act
on. The stand-in Blender, the reference store, ``view_finished`` and
``view_picture`` are those of ``rendered_view_case``; the job's record is
driven by the test as the job manager would drive it. The compiled suite
``test_blender_view_wait`` does the same with a real render job.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import GREY, FinishedCase, picture  # noqa: E402
from angelo_agent import view_spec, view_wait  # noqa: E402


class WaitCase(FinishedCase):
    def setUp(self):
        super().setUp()
        self.now = 500.0
        clock = patch.object(view_wait, "_now", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.path = self.folder / "review.png"
        self.job(self.path, status="running", progress=0.0, eta_s=None, started=1000.0, reason="")

    def advance(self, seconds, **changes):
        """Time passes and the job's record changes as the manager would change it."""
        self.now += seconds
        self.records["j3"].update(changes)

    def finish(self, colour=GREY):
        self.file("review.png", picture(1920, 1080, colour))
        self.records["j3"].update(status="done", progress=1.0, eta_s=None)


class WaitTests(WaitCase):
    def test_a_view_of_a_running_job_waits_and_then_answers_with_its_picture(self):
        pending = self.show()
        self.assertIsInstance(pending, view_wait.JobWait, "pending, as a look is: the interface is not blocked")
        self.assertIsNone(pending.poll())
        self.advance(40, progress=0.6, eta_s=27.0)
        self.assertIsNone(pending.poll(), "still rendering, and it will end in time")
        self.advance(30)
        self.finish((200, 60, 20, 255))
        result = pending.poll()
        self.assertEqual(self.delivered(result)[5, 5].tolist(), [200, 60, 20, 255])
        view = result["views"][0]
        self.assertEqual((view["width"], view["height"], view["framing"]), (768, 432, "FINISHED_IMAGE"))
        self.assertEqual((result["rendered"]["job"], result["rendered"]["waited_seconds"]), ("j3", 70))
        self.assertTrue(result["success"] and result["read_only"] and not result["scene_changed"])
        self.assertEqual(round(view_wait.allowance(self.controller)), view_spec.WAIT_SECONDS - 70)

    def test_the_last_job_is_this_conversations_latest_render_job(self):
        self.job(self.folder / "old.png", identity="j1")
        self.job(self.folder / "bake", identity="j9", kind="BAKE_TEXTURE")
        self.job(self.folder / "theirs.png", identity="j12", session="other", status="running")
        pending = self.show("LAST_JOB")
        self.assertEqual(pending.identity, "j3", "not a bake, not another conversation's job")
        self.finish()
        self.assertEqual(pending.poll()["rendered"]["job"], "j3")
        self.records.clear()
        with self.assertRaisesRegex(RuntimeError, "This conversation has started no render job yet"):
            self.show("LAST_JOB")

    def test_a_queued_job_is_waited_for_too(self):
        self.records["j3"].update(status="queued", started=None)
        pending = self.show()
        self.assertIsNone(pending.poll())
        self.advance(20, status="running", started=1020.0)
        self.assertIsNone(pending.poll())
        self.finish()
        self.assertEqual(len(pending.poll()["views"]), 1)

    def test_a_frame_of_a_running_animation_is_shown_when_it_ends(self):
        self.records["j3"].update(kind="RENDER_ANIMATION", detail={"output_path": str(self.folder / "shot"),
                                                                   "frames_done": 1, "frames_total": 3})
        pending = self.show(frame=2)
        self.assertIsNone(pending.poll())
        for number, colour in ((1, (255, 0, 0, 255)), (2, (0, 255, 0, 255)), (3, (0, 0, 255, 255))):
            self.file("shot/frame_%04d.png" % number, picture(320, 180, colour))
        self.records["j3"].update(status="done")
        self.assertEqual(self.delivered(pending.poll())[4, 4].tolist(), [0, 255, 0, 255])


class BoundTests(WaitCase):
    def watch(self, pending, *steps):
        """Poll after each (seconds, progress) step; the answers."""
        answers = []
        for seconds, progress in steps:
            self.advance(seconds, progress=progress)
            answers.append(pending.poll())
        return answers

    def test_a_job_that_will_not_end_in_time_is_not_waited_for(self):
        """A 48-minute frame returns some tens of seconds after its first progress, not after ten minutes."""
        pending = self.show()
        self.assertIsNone(pending.poll())
        *early, result = self.watch(pending, (60, 0.0), (5, 0.004), (10, 0.008), (12, 0.012))
        self.assertEqual(early, [None, None, None], "preparing, then a pace that has not settled yet")
        self.assertNotIn("error", result)
        self.assertEqual(result["views"], [])
        self.assertEqual(result["waiting"], {"waited_seconds": 87, "request_seconds_left": 513,
                                             "ended": "will_not_end_in_time"})
        self.assertEqual(result["job"], {"id": "j3", "kind": "RENDER_STILL", "status": "running", "progress": 0.012,
                                         "elapsed_seconds": result["job"]["elapsed_seconds"], "eta_seconds": 2717})
        self.assertIn("job j3 is 1% done", result["note"])
        self.assertIn("with about 45 min to go; this request can wait 9 min more for jobs (10 min per request), so the "
                      "call stopped waiting", result["note"])
        self.assertIn("End your turn and tell the person that the job is still running", result["note"])
        self.assertIn("image j3 in a later message", result["note"])
        self.assertEqual(self.records["j3"]["status"], "running", "the job goes on")
        self.advance(30)
        again = self.show()
        self.assertEqual((again["waiting"]["ended"], again["waiting"]["waited_seconds"], again["job"]["eta_seconds"]),
                         ("will_not_end_in_time", 0, 2687), "asked again in the same request: no second wait")
        self.controller._request_generation += 1
        self.assertIsInstance(self.show(), view_wait.JobWait, "the next request watches the job again")

    def test_a_scene_slow_to_prepare_and_quick_to_sample_is_waited_for(self):
        """95 s at 0% and then a sample a second: the job's own estimate (elapsed over progress) says 50 minutes."""
        pending = self.show()
        pending.poll()
        self.records["j3"]["eta_s"] = 3100.0
        self.assertEqual(self.watch(pending, (95, 0.0), (1, 0.03), (12, 0.5), (8, 0.9)), [None] * 4)
        self.advance(3)
        self.finish()
        self.assertEqual(len(pending.poll()["views"]), 1)

    def test_a_projection_a_little_over_the_time_left_is_still_waited_for(self):
        pending = self.show()
        pending.poll()
        # 0.1% a second with 68% to go is 680 s; 575 s are left: within one and a half times of it.
        self.assertEqual(self.watch(pending, (5, 0.30), (20, 0.32)), [None, None])

    def test_a_queued_job_is_judged_by_the_job_ahead_of_it(self):
        self.records["j3"].update(status="queued", started=None)
        self.job(self.folder / "theirs.png", identity="j2", session="other", status="running", progress=0.0,
                 eta_s=None, started=900.0, reason="")
        self.active = self.records["j2"]
        pending = self.show()
        pending.poll()
        for seconds, progress in ((5, 0.004), (10, 0.008), (12, 0.012)):
            self.now += seconds
            self.records["j2"]["progress"] = progress
            result = pending.poll()
        self.assertEqual((result["waiting"]["ended"], result["job"]["status"]), ("will_not_end_in_time", "queued"))
        self.assertIn("job j3 is queued behind another job, with about 45 min to go", result["note"])
        self.assertNotIn("j2", result["note"], "another conversation's job is not named")

    def test_durations_read_as_a_person_says_them(self):
        self.assertEqual([view_wait._clock(seconds) for seconds in (45, 89, 240, 2880, 5399, 54600)],
                         ["45 s", "89 s", "4 min", "48 min", "90 min", "15 h"])

    def test_the_guidance_states_the_allowance(self):
        from angelo_agent.agent_guidance import JOB_GUIDANCE
        self.assertIn("%d minutes per request at most" % (view_spec.WAIT_SECONDS // 60), " ".join(JOB_GUIDANCE.split()))
        self.assertIn("image LAST_JOB", " ".join(JOB_GUIDANCE.split()))

    def test_the_requests_allowance_ends_the_wait_and_the_next_request_has_its_own(self):
        pending = self.show()
        self.advance(599, progress=0.5)  # No estimate at all: nothing to decide on but the clock.
        self.assertIsNone(pending.poll())
        self.advance(2)
        result = pending.poll()
        self.assertEqual((result["waiting"]["ended"], result["waiting"]["request_seconds_left"]), ("wait_used", 0))
        self.assertIn("this request has used its 10 min of waiting for jobs", result["note"])
        self.assertIn("Do not ask again in this turn", result["note"])
        again = self.show()
        self.assertEqual(again["waiting"]["ended"], "wait_used", "asked again in the same request: answered at once")
        self.controller._request_generation += 1
        self.assertIsInstance(self.show(), view_wait.JobWait)

    def test_two_waits_of_one_request_share_the_allowance(self):
        first = self.show()
        self.advance(400)
        self.finish()
        first.poll()
        self.job(self.folder / "second.png", identity="j4", status="running", progress=0.0, eta_s=None, started=1400.0)
        second = self.show("j4")
        self.assertEqual(round(second.left), 200)
        self.assertEqual(round(second.timeout), 200 + view_wait.GRACE_SECONDS, "the call's expiry follows what is left")


class EndingTests(WaitCase):
    def ended(self, **changes):
        pending = self.show()
        self.advance(12, **changes)
        result = pending.poll()
        self.assertNotIn("error", result, "an ending is a result the model reads, not the end of its turn")
        self.assertEqual(result["views"], [])
        return result

    def test_a_job_that_fails_says_why(self):
        result = self.ended(status="failed", reason="Angelo ended the render without writing the image; see its Info log")
        self.assertEqual((result["waiting"]["ended"], result["job"]["status"]), ("job_failed", "failed"))
        self.assertIn("job j3 failed: Angelo ended the render without writing the image; see its Info log.", result["note"])

    def test_the_person_cancelling_the_job_is_said(self):
        result = self.ended(status="cancelled", reason="user")
        self.assertIn("the person cancelled job j3. Do not start it again unless they ask", result["note"])

    def test_a_job_stopped_at_its_own_time_budget_is_said(self):
        result = self.ended(status="cancelled", reason="time_budget")
        self.assertIn("stopped at its own time budget", result["note"])

    def test_a_document_closed_under_the_job_is_said(self):
        result = self.ended(status="interrupted", reason="file_closed")
        self.assertIn("was interrupted (the document was closed or reloaded)", result["note"])

    def test_a_job_that_ended_with_a_file_that_cannot_be_shown_says_so(self):
        self.file("review.png", b"not a picture")
        result = self.ended(status="done")
        self.assertIn("No picture: Job j3 image is not a PNG, JPEG or OpenEXR picture", result["note"])

    def test_a_job_that_ended_while_another_has_locked_blender_says_so(self):
        from angelo_agent import job_host
        busy = job_host.JobBusyError("Job j4 (BAKE_PHYSICS, 3%) has locked Blender while it runs")
        with patch.object(job_host, "guard", side_effect=busy):
            self.file("review.png", picture(64, 36))
            result = self.ended(status="done")
        self.assertIn("No picture: Job j4 (BAKE_PHYSICS, 3%) has locked Blender", result["note"])
        self.assertEqual(result["waiting"]["ended"], "job_done")

    def test_stop_ends_the_wait_and_not_the_job(self):
        pending = self.show()
        self.advance(30, progress=0.4)
        pending.cancel()  # Controller.cancel_tools: the turn was stopped.
        self.assertEqual(self.records["j3"]["status"], "running")
        self.assertIn("the job itself goes on", pending.stopped_result["error"])
        self.assertEqual(round(view_wait.allowance(self.controller)), view_spec.WAIT_SECONDS - 30)

    def test_a_job_of_another_conversation_or_kind_is_refused_as_before(self):
        self.records["j3"]["session"] = "other"
        with self.assertRaisesRegex(RuntimeError, "No job j3 in this conversation"):
            self.show()
        self.records["j3"].update(session="chat", kind="EXPORT_USD")
        with self.assertRaisesRegex(RuntimeError, "only render and texture bake jobs have pictures"):
            self.show()


if __name__ == "__main__":
    unittest.main()
