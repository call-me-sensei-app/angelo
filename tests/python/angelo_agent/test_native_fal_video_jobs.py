# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""fal video jobs: one billable submit, credentials only to fal, Stop vs closing, resume after a restart, the agent."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import fal_models, fal_queue, fal_video, video_resume  # noqa: E402
from angelo_agent.generative import GenerationBinding  # noqa: E402
from angelo_agent.studio_agent import StudioAgent, capabilities  # noqa: E402
from angelo_agent.studio_generation import StopSignal, StudioJobs  # noqa: E402
from angelo_agent.studio_http import GenerationCancelled, GenerationError  # noqa: E402
from angelo_agent.studio_state import StudioLibrary  # noqa: E402

KEY = "fal-fixture-key:0123456789abcdef"
REQUEST = "019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b"
APP = "https://queue.fal.run/bytedance/seedance-2.0/requests/" + REQUEST
MP4 = b"\0\0\0\x20ftypisom" + b"\0" * 64
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+axtoAAAAASUVORK5CYII=")
OPTIONS = {"duration": 5, "resolution": "720p", "aspect": "16:9", "audio": False}


def binding():
    return GenerationBinding("conn", "fal_api", tuple(fal_models.MODELS), True, KEY)


class Fal:
    """A fake fal: records every call; ``statuses`` are answered in order (the last repeats)."""

    def __init__(self, statuses=("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), status_error=None, result_status=200,
                 failing_reads=0, cancel_takes=True):
        self.calls, self.statuses, self.status_error = [], list(statuses), status_error
        self.result_status, self.failing_reads, self.cancel_takes = result_status, failing_reads, cancel_takes

    def __call__(self, url, body=None, headers=None, service=False, method=None, accept=(200, 201), limit=None, timeout=None):
        verb = method or ("POST" if body is not None else "GET")
        self.calls.append((verb, url, (headers or {}).get("Authorization"), service))
        if url.startswith("https://rest.fal.ai/storage/upload/initiate"):
            return json.dumps({"upload_url": "https://storage.example/upload/abc?sig=1",
                               "file_url": "https://v3b.fal.media/files/abc.png"}).encode()
        if url.startswith("https://storage.example/upload/"):
            return b""
        if url.endswith("/status"):
            if self.failing_reads:
                self.failing_reads -= 1
                raise GenerationError("fal Video returned a server error (HTTP 503); not retried")
            state = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return json.dumps(dict({"status": state, "request_id": REQUEST},
                                   **({"error": self.status_error} if state == "COMPLETED" and self.status_error else {}))).encode()
        if url.endswith("/cancel"):   # As fal: the request then completes with an error (unless it was too late).
            if self.cancel_takes:
                self.statuses, self.status_error = ["COMPLETED"], "Request was cancelled"
            return json.dumps({"status": "CANCELLATION_REQUESTED"}).encode()
        if url == APP:
            if self.result_status == 404:
                error = GenerationError("fal Video rejected the request (HTTP 404); not retried")
                error.status = 404
                raise error
            return json.dumps({"video": {"url": "https://v3b.fal.media/files/out.mp4"}, "seed": 1}).encode()
        if url == "https://v3b.fal.media/files/out.mp4":
            return MP4
        if url.startswith("https://queue.fal.run/bytedance/seedance-2.0/"):
            return json.dumps({"request_id": REQUEST, "status": "IN_QUEUE", "status_url": APP + "/status",
                               "response_url": APP, "cancel_url": APP + "/cancel"}).encode()
        raise AssertionError("unexpected request " + url)

    def submits(self):
        return [c for c in self.calls if c[0] == "POST" and c[1].startswith("https://queue.fal.run/")]


class GenerateTests(unittest.TestCase):
    def run_one(self, fal, inputs=None, cancel=None, **kwargs):
        with tempfile.TemporaryDirectory() as root:
            events = []
            result = fal_video.generate(binding(), "seedance-2.0", "A fox", OPTIONS, inputs or {}, Path(root),
                                        events.append, cancel or StopSignal(), fal, poll_interval=0, **kwargs)
            return result, events, (Path(root) / "video.mp4").read_bytes()

    def test_one_submit_saved_before_following_and_the_key_only_on_fal_hosts(self):
        with tempfile.TemporaryDirectory() as root:
            frame = Path(root) / "frame.png"
            frame.write_bytes(PNG)
            fal = Fal()
            result, events, video = self.run_one(fal, {"first": {"path": str(frame), "name": "frame"}})
        self.assertEqual((result["status"], result["files"], video), ("ready", ["video.mp4"], MP4))
        self.assertEqual(len(fal.submits()), 1)
        self.assertEqual(fal.submits()[0][1], "https://queue.fal.run/bytedance/seedance-2.0/image-to-video")
        saved = next(e for e in events if "fal" in e)
        self.assertEqual((saved["fal"]["request_id"], saved["task_id"]), (REQUEST, REQUEST))
        self.assertLess(events.index(saved), [e.get("status") for e in events].index("downloading"))
        for verb, url, authorization, _service in fal.calls:
            with self.subTest(url=url):
                admitted = url.startswith(("https://queue.fal.run/", "https://rest.fal.ai/"))
                self.assertEqual(authorization, "Key " + KEY if admitted else None, "the key goes to fal's API only")

    def test_a_failed_request_reports_fals_reason_without_the_key(self):
        with self.assertRaises(GenerationError) as caught:
            self.run_one(Fal(status_error="Content policy violation for key " + KEY))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIn("Content policy", str(caught.exception))

    def test_an_expired_result_points_at_the_dashboard(self):
        with self.assertRaises(GenerationError) as caught:
            self.run_one(Fal(result_status=404))
        self.assertIn(REQUEST, str(caught.exception))

    def test_status_reads_are_retried_never_resubmitted(self):
        fal = Fal(failing_reads=3)
        self.assertEqual(self.run_one(fal)[0]["status"], "ready")
        self.assertEqual(len(fal.submits()), 1)
        fal = Fal(failing_reads=fal_video.STATUS_FAILURES)
        with self.assertRaises(fal_video.MonitoringPaused):
            self.run_one(fal)
        self.assertEqual(len(fal.submits()), 1)

    def test_invalid_requests_send_nothing(self):
        fal = Fal()
        for options, inputs in (({"duration": 99}, {}), (OPTIONS, {"last": {"path": "x"}})):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_one(fal, inputs) if options is OPTIONS else fal_video.generate(
                    binding(), "seedance-2.0", "A fox", options, inputs, Path("."), print, StopSignal(), fal)
        self.assertEqual(fal.calls, [])

    def test_stop_asks_fal_to_cancel_but_closing_only_pauses(self):
        for remote in (True, False):
            with self.subTest(remote=remote):
                fal, stop = Fal(statuses=("IN_QUEUE",)), StopSignal()
                stop.remote = remote
                timer = threading.Timer(0.05, stop.set)
                timer.start()
                expected = GenerationCancelled if remote else fal_video.MonitoringPaused
                with self.assertRaises(expected) as caught:
                    self.run_one(fal, cancel=stop)
                timer.join()
                self.assertEqual(type(caught.exception), expected)
                cancels = [c for c in fal.calls if c[0] == "PUT"]
                self.assertEqual(cancels, [("PUT", APP + "/cancel", "Key " + KEY, True)] if remote else [])


class StopAndErrorTests(unittest.TestCase):
    def follow(self, fal, stop=None):
        handle = fal_queue.checked_handle("bytedance/seedance-2.0/text-to-video", json.loads(Fal()(
            "https://queue.fal.run/bytedance/seedance-2.0/text-to-video", body=b"{}")))
        with tempfile.TemporaryDirectory() as root:
            events = []
            result = fal_video.follow(binding(), handle, Path(root), events.append, stop or StopSignal(), fal, poll_interval=0)
            return result, events

    def test_stop_sends_one_cancel_and_a_video_fal_still_finishes_is_kept(self):
        fal, stop = Fal(statuses=("IN_PROGRESS",) * 3 + ("COMPLETED",), cancel_takes=False), StopSignal()
        stop.remote = True
        stop.set()
        result, events = self.follow(fal, stop)
        self.assertEqual(result["status"], "ready", "billed anyway, so downloaded")
        self.assertEqual(len([c for c in fal.calls if c[1].endswith("/cancel")]), 1)
        self.assertIn("cancelling", [e.get("status") for e in events])

    def test_a_request_that_finished_before_the_cancel_keeps_its_video(self):
        # The class of the BytePlus Stop race, on fal: Stop reaches fal after the request completed. fal's cancel is a
        # PUT that answers 400 ALREADY_COMPLETED and deletes nothing, so the billed video is still read and saved.
        class Finished(Fal):
            def __call__(self, url, **kwargs):
                if url.endswith("/cancel"):
                    self.calls.append(("PUT", url, None, True))
                    error = GenerationError("fal Video rejected the request (HTTP 400): ALREADY_COMPLETED; not retried")
                    error.status = 400
                    raise error
                return super().__call__(url, **kwargs)
        fal, stop = Finished(statuses=("COMPLETED",)), StopSignal()
        stop.remote = True
        stop.set()
        result, _events = self.follow(fal, stop)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([c[0] for c in fal.calls if "DELETE" == c[0]], [])
        self.assertIn(APP, [c[1] for c in fal.calls])   # The result was read after the refused cancel.

    def test_a_cancelled_request_ends_stopped(self):
        fal, stop = Fal(statuses=("COMPLETED",), status_error="Request cancelled by user"), StopSignal()
        stop.remote = True
        stop.set()
        with self.assertRaises(GenerationCancelled):
            self.follow(fal, stop)

    def test_closing_pauses_without_telling_fal(self):
        fal, stop = Fal(statuses=("IN_QUEUE",)), StopSignal()
        stop.closing = True
        stop.set()
        with self.assertRaises(fal_video.MonitoringPaused):
            self.follow(fal, stop)
        self.assertFalse([c for c in fal.calls if c[1].endswith("/cancel")])

    def test_a_refused_key_ends_the_job_but_a_lost_download_only_pauses(self):
        class Refused(Fal):
            def __call__(self, url, **kwargs):
                if url.endswith("/status"):
                    error = GenerationError("API key rejected; reconnect in AI Providers")
                    error.status = 401
                    raise error
                return super().__call__(url, **kwargs)

        class Dropped(Fal):
            def __call__(self, url, **kwargs):
                if url.endswith(".mp4"):
                    raise GenerationError("The request couldn't complete securely")
                return super().__call__(url, **kwargs)
        with self.assertRaises(GenerationError) as caught:
            self.follow(Refused())
        self.assertNotIsInstance(caught.exception, fal_video.MonitoringPaused)
        with self.assertRaises(fal_video.MonitoringPaused):
            self.follow(Dropped())

    def test_only_the_persons_stop_signals_a_video_job(self):
        with tempfile.TemporaryDirectory() as root:
            library = StudioLibrary(root)
            asset = library.add("VIDEO", "A fox", "Seedance 2.0", "conn", mode="fal_api", model_id="seedance-2.0")
            fal = Fal(statuses=("IN_QUEUE",))
            jobs = StudioJobs(library, fal)
            with patch.object(fal_video, "POLL_SECONDS", 0.01):
                jobs.start(asset, binding(), OPTIONS, {"first": None, "last": None, "references": []})
                jobs.stop(asset["id"], remote=False)   # A flow's file load, the agent's call ending.
                time.sleep(0.1)
                self.assertFalse(jobs.active[asset["id"]].is_set())
                jobs.close()
                deadline = time.monotonic() + 5   # The worker writes job-state.json until it pauses: let it finish
                while asset["id"] in jobs.active and time.monotonic() < deadline:   # before the folder is removed.
                    jobs.pump()
                    time.sleep(0.01)


class ResumePassTests(unittest.TestCase):
    def setUp(self):
        video_resume._next_resume.clear()

    def saved(self, library, **fields):
        handle = fal_queue.checked_handle("bytedance/seedance-2.0/text-to-video", json.loads(Fal()(
            "https://queue.fal.run/bytedance/seedance-2.0/text-to-video", body=b"{}"))).saved()
        asset = library.add("VIDEO", "A fox", "Seedance 2.0", "conn", mode="fal_api", model_id="seedance-2.0")
        asset = dict(asset, status="queued", fal=handle, **fields)
        library.update(asset)
        return asset

    def test_waiting_for_a_connection_says_so_and_keeps_the_request(self):
        with tempfile.TemporaryDirectory() as root:
            library = StudioLibrary(root)
            asset = self.saved(library)
            jobs = StudioJobs(library, Fal())

            def refuse(_identity):
                raise ValueError("The generation connection is unavailable; open AI Providers")
            self.assertTrue(video_resume.tick(library, jobs, refuse, lambda *a: False))
            current = library.assets[asset["id"]]
            self.assertEqual((current["status"], current["error"]), ("queued", video_resume.RECONNECT))
            self.assertFalse(video_resume.tick(library, jobs, refuse, lambda *a: False), "backs off; no busy loop")
            video_resume.stop_following(library, current)
            self.assertEqual(library.assets[asset["id"]]["status"], "stopped")

    def test_lost_submits_and_unreadable_requests_end_with_a_reason(self):
        with tempfile.TemporaryDirectory() as root:
            library = StudioLibrary(root)
            lost = library.add("VIDEO", "A", "Seedance 2.0", status="submitting")
            uploading = library.add("VIDEO", "B", "Seedance 2.0", status="uploading")
            broken = self.saved(library)
            library.update(dict(broken, fal=dict(broken["fal"], status_url="https://evil.example/s")))
            video_resume.tick(library, StudioJobs(library, Fal()), lambda _i: binding(), lambda *a: False)
            self.assertEqual((library.assets[lost["id"]]["status"], library.assets[lost["id"]]["error"]),
                             ("failed", video_resume.SUBMIT_LOST))
            self.assertEqual(library.assets[uploading["id"]]["status"], "stopped")
            self.assertEqual(library.assets[broken["id"]]["status"], "failed")

    def test_a_bound_saved_request_resumes_and_only_reads(self):
        with tempfile.TemporaryDirectory() as root:
            library = StudioLibrary(root)
            asset = self.saved(library)
            fal = Fal()
            jobs = StudioJobs(library, fal)
            with patch.object(fal_video, "POLL_SECONDS", 0):
                video_resume.tick(library, jobs, lambda _i: binding(), lambda *a: False)
                deadline = time.monotonic() + 5
                while jobs.active and time.monotonic() < deadline:
                    jobs.pump()
                    time.sleep(0.01)
                jobs.pump()
            self.assertEqual(library.assets[asset["id"]]["status"], "ready")
            self.assertEqual(fal.submits(), [])


class JobTests(unittest.TestCase):
    def wait(self, jobs, identity):
        deadline = time.monotonic() + 5
        while identity in jobs.active and time.monotonic() < deadline:
            jobs.pump()
            time.sleep(0.01)
        jobs.pump()

    def test_closing_keeps_the_saved_request_and_the_next_session_resumes_without_a_submit(self):
        with tempfile.TemporaryDirectory() as root:
            library = StudioLibrary(root)
            asset = library.add("VIDEO", "A fox", "Seedance 2.0", "conn", mode="fal_api", model_id="seedance-2.0",
                                options=OPTIONS)
            fal = Fal(statuses=("IN_QUEUE",))
            jobs = StudioJobs(library, fal)
            jobs.start(asset, binding(), OPTIONS, {"first": None, "last": None, "references": []})
            deadline = time.monotonic() + 5
            while not fal.submits() and time.monotonic() < deadline:
                time.sleep(0.01)
            jobs.close()   # Angelo quits: monitoring pauses, nothing is sent to fal.
            self.wait(jobs, asset["id"])
            self.assertFalse([c for c in fal.calls if c[1].endswith("/cancel")])
            later = StudioLibrary(root)   # The next session reads the saved state.
            saved = later.assets[asset["id"]]
            self.assertIn(saved["status"], fal_video.RESUMABLE)
            self.assertEqual(saved["fal"]["request_id"], REQUEST)
            fal_after = Fal()
            jobs = StudioJobs(later, fal_after)
            with patch.object(fal_video, "POLL_SECONDS", 0):
                jobs.resume(saved, binding())
                self.wait(jobs, asset["id"])
            self.assertEqual(later.assets[asset["id"]]["status"], "ready")
            self.assertEqual(fal_after.submits(), [], "a resume only reads")
            self.assertEqual(StudioLibrary(root).assets[asset["id"]]["fal"]["request_id"], REQUEST)

    def test_the_library_keeps_video_assets(self):
        with tempfile.TemporaryDirectory() as root:
            asset = StudioLibrary(root).add("VIDEO", "A fox", "Seedance 2.0", price=1.52)
            reloaded = StudioLibrary(root)
            self.assertEqual(reloaded.assets[asset["id"]]["kind"], "VIDEO")
            self.assertEqual([a["id"] for a in reloaded.visible("VIDEO")], [asset["id"]])
            self.assertEqual(reloaded.assets[asset["id"]]["price"], 1.52)


class AgentTests(unittest.TestCase):
    def agent(self, root, fal):
        library = StudioLibrary(root)
        jobs = StudioJobs(library, fal)
        entry = {"id": "seedance-2.0", "name": "Seedance 2.0", "connection": "11111111-1111-4111-8111-111111111111",
                 "mode": "fal_api", "account": "fal", "ready": True, "verified": True, "team": False}
        controller = type("C", (), {"turn_attachments": [], "state": {"messages": []}})()
        return StudioAgent(library, jobs, lambda: [entry], lambda identity: binding(), controller, None, None), library, jobs

    def test_video_generation_with_a_library_frame_prices_and_never_cancels_on_detach(self):
        with tempfile.TemporaryDirectory() as root:
            fal = Fal(statuses=("IN_QUEUE", "IN_PROGRESS", "COMPLETED"))
            agent, library, jobs = self.agent(root, fal)
            image = library.add("IMAGE", "Shot", "Imported image", status="ready")
            (library.result_directory(image["id"]) / "image.png").write_bytes(PNG)
            library.update(dict(image, files=["image.png"], thumbnail="image.png"))
            args = {"connection_id": "11111111-1111-4111-8111-111111111111", "model": "seedance-2.0", "prompt": "A fox",
                    "options": {"duration": 5, "resolution": "720p"}, "frames": [{"source": "ASSET", "id": image["id"]}]}
            label = agent.usage_label("angelo_studio_generate", args)
            self.assertIn("5 s 720p · ≈ $1.52", label)
            controls = capabilities("fal_api", "seedance-2.0")
            self.assertEqual(controls["usd_per_second"], {"480p": 0.1361, "720p": 0.3034, "1080p": 0.682, "4k": 1.5552})
            self.assertEqual(controls["modes"]["image"]["last_frame"], True)
            with patch.object(fal_video, "POLL_SECONDS", 0):
                pending = agent.execute("angelo_studio_generate", args)
                pending.cancel()   # The chat turn ended: the video keeps going.
                deadline = time.monotonic() + 5
                while jobs.active and time.monotonic() < deadline:
                    jobs.pump()
                    time.sleep(0.01)
                jobs.pump()
            asset = library.assets[pending.asset_id]
            self.assertEqual((asset["kind"], asset["status"], asset["price"]), ("VIDEO", "ready", 1.517))
            self.assertFalse([c for c in fal.calls if c[1].endswith("/cancel")])
            self.assertEqual(fal.submits()[0][1], "https://queue.fal.run/bytedance/seedance-2.0/image-to-video")

    def test_frames_are_refused_for_image_models_and_wrong_options_for_video(self):
        with tempfile.TemporaryDirectory() as root:
            agent, _library, _jobs = self.agent(root, Fal())
            base = {"connection_id": "11111111-1111-4111-8111-111111111111", "model": "seedance-2.0", "prompt": "A fox"}
            for options in ({"duration": 40}, {"quality": "high"}, {"resolution": "2K"}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    agent.usage_label("angelo_studio_generate", dict(base, options=options))


if __name__ == "__main__":
    unittest.main()
