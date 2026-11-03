# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Seedance on a BytePlus ModelArk key: the task body, one billable create, Stop vs closing, resume, prices, and the
model pickers offering the same model on fal and BytePlus side by side with their provider and price per second."""

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import machine_clock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import byteplus_models, byteplus_video, fal_models, fal_video, flow_prices, video_resume  # noqa: E402
from angelo_agent.generative import GenerationBinding  # noqa: E402
from angelo_agent.provider_registry import SPECS, model_kind  # noqa: E402
from angelo_agent.studio_generation import StopSignal, StudioJobs  # noqa: E402
from angelo_agent.studio_http import GenerationCancelled, GenerationError  # noqa: E402
from angelo_agent.studio_state import StudioLibrary, StudioValueError, model_options  # noqa: E402
from angelo_agent.studio_video_ui import picker_note, rate_note  # noqa: E402

KEY = "ark-fixture-key-0123456789abcdef"
BASE = "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"
TASK = "cgt-20261030-abc123"
VIDEO_URL = "https://ark-content.bytepluses.com/out.mp4?sig=1"
MP4 = b"\0\0\0\x20ftypisom" + b"\0" * 64
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+axtoAAAAASUVORK5CYII=")
FAST = byteplus_models.DEFAULT_MODEL
OPTIONS = {"duration": 5, "resolution": "720p", "aspect": "16:9", "audio": False}


def binding():
    return GenerationBinding("ark", "seedream_api", tuple(m["id"] for m in SPECS["seedream_api"]["models"]), False, KEY)


class Ark:
    """A fake ModelArk: records every call; ``statuses`` are answered in order (the last repeats)."""

    def __init__(self, statuses=("queued", "running", "succeeded"), error=None, failing_reads=0, video=MP4,
                 read_status=None, video_status=None, task=TASK):
        self.calls, self.statuses, self.error, self.failing_reads = [], list(statuses), error, failing_reads
        self.video, self.read_status, self.bodies, self.video_status = video, read_status, [], video_status
        self.task, self.gone = task, False

    def __call__(self, url, body=None, headers=None, service=False, method=None, accept=(200, 201), limit=None,
                 timeout=None):
        verb = method or ("POST" if body is not None else "GET")
        self.calls.append((verb, url, (headers or {}).get("Authorization")))
        if url == BASE and verb == "POST":
            self.bodies.append(json.loads(body))
            return json.dumps({"id": self.task} if self.task else {}).encode()
        if url == BASE + "/" + TASK and verb == "DELETE":   # As BytePlus: a queued task is cancelled, a running one
            if self.statuses[0] == "queued":                # can't be, and a finished one's record is deleted.
                self.statuses = ["cancelled"]
            elif self.statuses[0] == "running":
                raise GenerationError("BytePlus rejected the request (HTTP 409); not retried")
            else:
                self.gone = True
            return b""
        if url == BASE + "/" + TASK:
            if self.read_status or self.gone:
                error = GenerationError("BytePlus rejected the request; not retried")
                error.status = self.read_status or 404
                raise error
            if self.failing_reads:
                self.failing_reads -= 1
                raise GenerationError("BytePlus returned a server error (HTTP 503); not retried")
            state = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            answer = {"id": TASK, "status": state}
            if state == "succeeded":
                answer["content"] = {"video_url": VIDEO_URL}
            if state == "failed" and self.error:
                answer["error"] = self.error
            return json.dumps(answer).encode()
        if url == VIDEO_URL:
            if self.video_status:
                error = GenerationError("The download host rejected the request (HTTP 403)")
                error.status = self.video_status
                raise error
            return self.video
        raise AssertionError("unexpected request " + url)

    def creates(self):
        return [c for c in self.calls if c[0] == "POST"]


class RequestTests(unittest.TestCase):
    def test_text_frames_and_references_become_content_with_roles(self):
        _route, text = byteplus_models.request(FAST, "A fox", OPTIONS)
        self.assertEqual(text["content"], [{"type": "text", "text": "A fox"}])
        self.assertEqual((text["duration"], text["resolution"], text["ratio"], text["watermark"]), (5, "720p", "16:9", False))
        _route, frames = byteplus_models.request(FAST, "", {"duration": 4}, first="data:a", last="data:b")
        self.assertEqual([(c["image_url"]["url"], c["role"]) for c in frames["content"]],
                         [("data:a", "first_frame"), ("data:b", "last_frame")])
        self.assertEqual(frames["ratio"], "adaptive", "image-to-video keeps the frame's own shape")
        _route, refs = byteplus_models.request(FAST, "A fox", {}, references=["data:a", "data:b"])
        self.assertEqual([c.get("role") for c in refs["content"]], [None, "reference_image", "reference_image"])

    def test_every_model_is_a_video_model_with_a_dated_price(self):
        for model in byteplus_models.MODELS:
            with self.subTest(model=model):
                self.assertTrue(fal_models.is_video_model(model))
                self.assertEqual(model_kind("seedream_api", model), "VIDEO")
                self.assertEqual(model_kind("seedream_team", model), "VIDEO")
                for resolution in fal_models.capabilities(model, fal_models.TEXT)["resolutions"]:
                    self.assertIsNotNone(flow_prices.rate("seedream_api", model, resolution), resolution)
        self.assertEqual(model_kind("seedream_api", "dola-seedream-5-0-pro-260628"), "IMAGE")

    def test_prices_follow_byteplus_token_formula(self):
        # BytePlus's own example: Seedance 2.5 at 1080p, 16:9, is about $0.569 a second.
        self.assertAlmostEqual(flow_prices.rate("seedream_api", "dreamina-seedance-2-5-260628", "1080p"), 0.569, 3)
        five = flow_prices.estimate("seedream_api", FAST, OPTIONS)
        self.assertAlmostEqual(five, 5 * 21600 * 5.6 / 1e6, 4)
        self.assertLess(five, flow_prices.estimate("fal_api", "seedance-2.0-fast", OPTIONS), "BytePlus is the cheaper")

    def test_a_stop_before_the_create_sends_nothing(self):
        cancel, ark = StopSignal(), Ark()
        cancel.set()
        with tempfile.TemporaryDirectory() as root, self.assertRaises(GenerationCancelled):
            byteplus_video.generate(binding(), FAST, "A fox", OPTIONS, {}, Path(root), lambda e: None, cancel, ark)
        self.assertEqual(ark.calls, [])

    def test_refusals_send_nothing(self):
        ark = Ark()
        with tempfile.TemporaryDirectory() as root:
            for options, inputs in (({"duration": 99}, {}), (OPTIONS, {"videos": [{"path": "x.mp4"}]}),
                                    (dict(OPTIONS, resolution="4k"), {})):
                with self.subTest(options=options), self.assertRaises((StudioValueError, ValueError)):
                    byteplus_video.generate(binding(), FAST, "A fox", options, inputs, Path(root), lambda e: None,
                                            StopSignal(), ark, poll_interval=0)
        self.assertEqual(ark.calls, [])

    def test_validate_any_routes_by_model(self):
        with self.assertRaises(StudioValueError):
            fal_video.validate_any(FAST, "A fox", OPTIONS, {"videos": [{"path": "x.mp4"}]})
        fal_video.validate_any("seedance-2.0-fast", "A fox", OPTIONS, {"videos": [{"path": "x.mp4"}]})


class GenerateTests(unittest.TestCase):
    def run_one(self, ark, inputs=None, cancel=None):
        """``cancel`` is set once the task is created (the person's Stop, or Angelo closing, while it runs)."""
        with tempfile.TemporaryDirectory() as root:
            events, signal = [], cancel or StopSignal()

            def emit(event):
                events.append(event)
                if cancel is not None and "byteplus_task" in event:
                    signal.set()
            result = byteplus_video.generate(binding(), FAST, "A fox", OPTIONS, inputs or {}, Path(root),
                                             emit, signal, ark, poll_interval=0)
            return result, events, (Path(root) / "video.mp4").read_bytes()

    def test_one_create_saved_before_following_and_the_key_only_on_the_api(self):
        with tempfile.TemporaryDirectory() as root:
            frame = Path(root) / "frame.png"
            frame.write_bytes(PNG)
            ark = Ark()
            result, events, video = self.run_one(ark, {"first": {"path": str(frame), "name": "frame"}})
        self.assertEqual((result["status"], result["files"], video), ("ready", ["video.mp4"], MP4))
        self.assertEqual(len(ark.creates()), 1)
        self.assertTrue(ark.bodies[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        saved = next(e for e in events if "byteplus_task" in e)
        self.assertLess(events.index(saved), [e.get("status") for e in events].index("downloading"))
        for _verb, url, authorization in ark.calls:
            with self.subTest(url=url):
                self.assertEqual(authorization, "Bearer " + KEY if url.startswith(BASE) else None)

    def test_a_failed_task_reports_the_reason_without_the_key(self):
        with self.assertRaises(GenerationError) as caught:
            self.run_one(Ark(statuses=("failed",), error={"code": "InputImageSensitiveContentDetected",
                                                         "message": "flagged " + KEY}))
        self.assertNotIn(KEY, str(caught.exception))

    def test_not_an_mp4_is_refused(self):
        with self.assertRaises(GenerationError):
            self.run_one(Ark(video=b"<html>nope</html>"))

    def test_stop_never_sends_delete_and_the_billed_video_is_saved(self):
        # BytePlus's DELETE cancels a task queued at that moment but deletes a finished one's record, with no
        # conditional form: a task read as queued can finish before the DELETE lands (the race an audit found:
        # GET queued -> DELETE -> 404 read as "Cancelled", the paid video erased). Stop never sends it.
        for statuses in (("queued", "succeeded"), ("queued", "queued", "running", "succeeded"), ("running", "succeeded")):
            cancel = StopSignal()
            cancel.remote = True   # The person's Stop, pressed before the first read.
            cancel.set()
            ark, events = Ark(statuses=statuses), []
            with tempfile.TemporaryDirectory() as root:
                result = byteplus_video.follow(binding(), TASK, Path(root), events.append, cancel, ark, poll_interval=0)
                video = (Path(root) / "video.mp4").read_bytes()
            self.assertEqual((result["status"], video), ("ready", MP4), statuses)
            self.assertFalse([c for c in ark.calls if c[0] == "DELETE"], statuses)
            self.assertFalse(ark.gone, statuses)
            self.assertIn("finishing", [e.get("status") for e in events], statuses)

    def test_a_task_stopped_before_a_close_resumes_finishing(self):
        ark, events = Ark(statuses=("queued", "queued", "queued", "running", "running", "succeeded")), []
        with tempfile.TemporaryDirectory() as root:
            result = byteplus_video.follow(binding(), TASK, Path(root), events.append, StopSignal(), ark, poll_interval=0,
                                           finishing=True)
        self.assertEqual(result["status"], "ready")
        # Six reads, three events: an unchanged read emits nothing (and rewrites no saved state).
        self.assertEqual([(e["status"], e["progress"]) for e in events], [("finishing", 5), ("finishing", 50), ("downloading", 95)])

    def test_no_code_path_can_send_delete_to_byteplus(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/byteplus_video.py").read_text(encoding="utf-8")
        self.assertNotIn('method="DELETE"', source)
        self.assertNotIn("method='DELETE'", source)

    def test_a_resumed_task_is_read_at_once(self):
        ark = Ark(statuses=("succeeded",))
        started = machine_clock.start(self)
        with tempfile.TemporaryDirectory() as root:
            byteplus_video.follow(binding(), TASK, Path(root), lambda e: None, StopSignal(), ark, poll_interval=30)
        started.assert_within(5)

    def test_stop_never_deletes_a_finished_task(self):
        # On a finished task DELETE erases the record and the billed video: Stop reads first and leaves it.
        cancel = StopSignal()
        cancel.remote = True
        with tempfile.TemporaryDirectory() as root:
            cancel.set()
            ark = Ark(statuses=("succeeded",))
            result = byteplus_video.follow(binding(), TASK, Path(root), lambda e: None, cancel, ark, poll_interval=0)
        self.assertEqual(result["status"], "ready")
        self.assertFalse([c for c in ark.calls if c[0] == "DELETE"])

    def test_stop_while_running_keeps_the_billed_video(self):
        cancel = StopSignal()
        cancel.remote = True   # The person's Stop.
        ark = Ark(statuses=("running", "succeeded"))
        result, events, video = self.run_one(ark, cancel=cancel)
        self.assertEqual((result["status"], video), ("ready", MP4))
        self.assertFalse([c for c in ark.calls if c[0] == "DELETE"], "a running task can't be cancelled")

    def test_closing_pauses_without_telling_byteplus(self):
        cancel = StopSignal()
        cancel.closing = True
        ark = Ark(statuses=("queued",))
        with self.assertRaises(fal_video.MonitoringPaused):
            self.run_one(ark, cancel=cancel)
        self.assertFalse([c for c in ark.calls if c[0] == "DELETE"])

    def test_a_gone_task_ends_but_lost_reads_only_pause(self):
        with self.assertRaises(GenerationError) as caught:
            self.run_one(Ark(read_status=404))
        self.assertNotIsInstance(caught.exception, fal_video.MonitoringPaused)
        with self.assertRaises(fal_video.MonitoringPaused):
            self.run_one(Ark(failing_reads=fal_video.STATUS_FAILURES))
        result, _events, _video = self.run_one(Ark(failing_reads=2))
        self.assertEqual(result["status"], "ready")


    def test_an_expired_link_is_final_and_names_the_task(self):
        with self.assertRaises(GenerationError) as caught:
            self.run_one(Ark(video_status=403))
        self.assertNotIsInstance(caught.exception, fal_video.MonitoringPaused)
        self.assertIn(TASK, str(caught.exception))

    def test_a_create_without_a_task_id_points_at_the_console(self):
        with self.assertRaises(GenerationError) as caught:
            self.run_one(Ark(task=""))
        self.assertIn("console", str(caught.exception))

    def test_a_body_over_the_routes_limit_sends_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            frame = Path(root) / "frame.png"
            frame.write_bytes(PNG)
            ark = Ark()
            with patch.object(byteplus_video, "MAX_BODY", 100), self.assertRaises(GenerationError) as caught:
                self.run_one(ark, {"first": {"path": str(frame), "name": "frame"}})
        self.assertIn("too large", str(caught.exception))
        self.assertEqual(ark.calls, [])
        self.assertLess(byteplus_video.MAX_TEAM_BODY, 32 * 2 ** 20, "the team gateway takes 32 MiB")


class JobTests(unittest.TestCase):
    def wait(self, jobs, identity):
        deadline = time.monotonic() + 5
        while identity in jobs.active and time.monotonic() < deadline:
            jobs.pump()
            time.sleep(0.01)
        jobs.pump()

    def test_closing_keeps_the_task_and_the_next_session_resumes_without_a_create(self):
        with tempfile.TemporaryDirectory() as root:
            library = StudioLibrary(root)
            asset = library.add("VIDEO", "A fox", "Seedance 2.0 Fast", "ark", mode="seedream_api", model_id=FAST,
                                options=OPTIONS)
            ark = Ark(statuses=("queued",))
            jobs = StudioJobs(library, ark)
            jobs.start(asset, binding(), OPTIONS, {"first": None, "last": None, "references": []})
            deadline = time.monotonic() + 5
            while not ark.creates() and time.monotonic() < deadline:
                time.sleep(0.01)
            jobs.close()
            self.wait(jobs, asset["id"])
            self.assertFalse([c for c in ark.calls if c[0] == "DELETE"])
            later = StudioLibrary(root)
            saved = later.assets[asset["id"]]
            self.assertIn(saved["status"], fal_video.RESUMABLE)
            self.assertEqual(saved["byteplus_task"], TASK)
            video_resume._next_resume.clear()
            ark_after = Ark()
            jobs = StudioJobs(later, ark_after)
            with patch.object(fal_video, "POLL_SECONDS", 0):
                video_resume.tick(later, jobs, lambda _i: binding(), lambda *a: False)
                self.wait(jobs, asset["id"])
            self.assertEqual(later.assets[asset["id"]]["status"], "ready")
            self.assertEqual(ark_after.creates(), [], "a resume only reads")

    def test_a_task_never_resumes_on_another_providers_connection(self):
        with tempfile.TemporaryDirectory() as root:
            library = StudioLibrary(root)
            asset = library.add("VIDEO", "A fox", "Seedance 2.0 Fast", "ark", mode="seedream_api", model_id=FAST)
            library.update(dict(asset, status="queued", byteplus_task=TASK))
            video_resume._next_resume.clear()
            fal = GenerationBinding("ark", "fal_api", ("seedance-2.0-fast",), True, "fal-key")
            ark = Ark()
            video_resume.tick(library, StudioJobs(library, ark), lambda _i: fal, lambda *a: False)
            self.assertEqual(ark.calls, [])
            self.assertEqual(library.assets[asset["id"]]["byteplus_task"], TASK)
            self.assertEqual(library.assets[asset["id"]]["error"], video_resume.BYTEPLUS[video_resume.RECONNECT])
            lost = library.add("VIDEO", "B", "Seedance 2.0 Fast", "ark", mode="seedream_team", status="submitting")
            video_resume.tick(library, StudioJobs(library, ark), lambda _i: fal, lambda *a: False)
            self.assertIn("BytePlus console", library.assets[lost["id"]]["error"])


class PickerTests(unittest.TestCase):
    def test_the_same_model_on_two_providers_reads_side_by_side_with_its_price(self):
        entries = model_options("VIDEO", [], {})
        fast = [(e["mode"], rate_note(e["mode"], e["id"], "720p")) for e in entries if e["name"] == "Seedance 2.0 Fast"]
        self.assertEqual({mode for mode, _note in fast}, {"fal_api", "seedream_api"})
        for _mode, note in fast:
            self.assertRegex(note, r"^≈ \$0\.\d+/s at 720p$")
        self.assertNotIn("Seedream 5.0 Pro", [e["name"] for e in entries])
        ark = next(e for e in entries if e["mode"] == "seedream_api" and e["id"] == FAST)
        self.assertRegex(picker_note(dict(ark, account="Studio key")), r"^BytePlus · Studio key · ≈ \$0\.\d+/s at 720p$")
        self.assertNotIn(FAST, [e["id"] for e in model_options("IMAGE", [], {})])


if __name__ == "__main__":
    unittest.main()


class LabelTests(unittest.TestCase):
    """A queued job's card names its own provider and what it is making."""

    def test_cards_name_the_jobs_provider_and_kind(self):
        from angelo_agent.studio_state import card_status, job_provider
        byteplus = {"kind": "VIDEO", "mode": "seedream_api", "status": "queued"}
        team = {"kind": "VIDEO", "mode": "seedream_team", "status": "submitting"}
        fal = {"kind": "VIDEO", "mode": "fal_api", "status": "queued"}
        model = {"kind": "3D", "mode": "fal_api", "status": "downloading"}
        self.assertEqual([job_provider(a) for a in (byteplus, team, fal, model)], ["BytePlus", "BytePlus", "fal", "fal"])
        self.assertEqual(card_status(byteplus), "Queued at BytePlus…")
        self.assertEqual(card_status(team), "Sending to BytePlus…")
        self.assertEqual(card_status(dict(byteplus, status="finishing")), "Finishing at BytePlus…")
        self.assertEqual(card_status(fal), "Queued at fal…")
        self.assertEqual(card_status(model), "Downloading model…")
        self.assertEqual(card_status(dict(model, status="uploading")), "Uploading images…")
        self.assertEqual(card_status(dict(fal, status="downloading")), "Downloading video…")
        self.assertEqual(card_status({"kind": "VIDEO", "status": "queued", "byteplus_task": TASK}), "Queued at BytePlus…")


class PumpTests(unittest.TestCase):
    """The main-thread pump writes the library only when an event changes something."""

    def test_an_unchanged_event_writes_nothing_and_a_finish_redraws(self):
        class Library:
            def __init__(self):
                self.assets, self.writes = {"a": {"id": "a", "status": "queued", "progress": 5}}, 0

            def update(self, asset):
                self.assets[asset["id"]], self.writes = asset, self.writes + 1
        library = Library()
        jobs = StudioJobs(library)
        jobs.active["a"], jobs.kinds["a"] = StopSignal(), "VIDEO"
        jobs.events.put(("a", {"status": "queued", "progress": 5}))
        self.assertFalse(jobs.pump())
        self.assertEqual(library.writes, 0)
        jobs.events.put(("a", {"status": "generating", "progress": 50}))
        self.assertTrue(jobs.pump())
        self.assertEqual(library.writes, 1)
        jobs.events.put(("a", {"_finished": True}))   # A paused job: nothing to write, but its Stop goes.
        self.assertTrue(jobs.pump())
        self.assertEqual((library.writes, jobs.active), (1, {}))
