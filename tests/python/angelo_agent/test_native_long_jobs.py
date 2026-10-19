# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background jobs without Blender: request bounds, records, the queue, cancellation and output publishing.

The Blender runners (``job_runners``) and the chat card in the running app are
covered by ``test_blender_long_jobs.py`` and ``test_blender_job_card.py``.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_store  # noqa: E402
from angelo_agent.job_manager import (FILE_CLOSED, NOT_STARTED, NOTHING_RAN, JobManager,  # noqa: E402
                                      JobStartError)
from angelo_agent.job_spec import (JOB_TOOLS, MAX_FRAMES, MAX_PIXELS, MAX_QUEUED, MAX_SAMPLES, MAX_SIDE,  # noqa: E402
                                   MAX_WORK, JobRequestError, check_frames, check_resolution, check_samples,
                                   check_work, validate_job)
from angelo_agent.job_store import JobStore, context_entries, context_entry, new_record  # noqa: E402
from angelo_agent.strict_schema import strict_problems, strict_schema  # noqa: E402

try:
    import jsonschema
except ImportError:  # CI installs it (run_host_tests --max-skips 0).
    jsonschema = None


HANDLE = "scn:" + "a" * 32 + ":1"
HIGH, CAGE = "scn:" + "a" * 32 + ":2", "scn:" + "a" * 32 + ":3"
IMAGE = "dat:" + "a" * 32 + ":IMAGE:7"


def start(**args):
    return validate_job("angelo_job_start", dict(args))


class RequestTests(unittest.TestCase):
    def test_each_kind_is_normalized_with_its_defaults(self):
        self.assertEqual(start(kind="RENDER_STILL", output="renders/hero"),
                         {"kind": "RENDER_STILL", "queue": False, "minutes": 30.0, "output": "renders/hero.png",
                          "format": "PNG"})
        animation = start(kind="RENDER_ANIMATION", output="renders/walk/", frame_start=1, frame_end=48,
                          samples=64, resolution=[1920, 1080], format="JPEG", queue=True, minutes=90)
        self.assertEqual((animation["output"], animation["queue"], animation["minutes"], animation["format"]),
                         ("renders/walk", True, 90.0, "JPEG"))
        self.assertEqual(start(kind="BAKE_TEXTURE", target=HANDLE)["bake_type"], "COMBINED")
        self.assertEqual(start(kind="BAKE_PHYSICS"), {"kind": "BAKE_PHYSICS", "queue": False, "minutes": 120.0})
        self.assertEqual(start(kind="BAKE_SIMULATION", target=HANDLE, output="caches/smoke/")["output"], "caches/smoke")
        self.assertNotIn("output", start(kind="BAKE_SIMULATION", target=HANDLE))  # Packed into the file.
        self.assertEqual(start(kind="EXPORT_ALEMBIC", output="out/shot.abc")["output"], "out/shot.abc")

    def test_requests_outside_the_contract_are_refused_with_the_reason(self):
        cases = [
            ({"kind": "RENDER_MOVIE", "output": "x"}, "kind must be one of"),
            ({"kind": "BAKE_PHYSICS", "output": "x"}, "BAKE_PHYSICS does not take: output"),
            ({"kind": "RENDER_STILL"}, "RENDER_STILL needs: output"),
            ({"kind": "BAKE_SIMULATION"}, "BAKE_SIMULATION needs: target"),
            ({"kind": "BAKE_TEXTURE", "target": "Cube"}, "observed object handle"),
            ({"kind": "RENDER_ANIMATION", "output": "a", "frame_start": 10, "frame_end": 9}, "must not be before"),
            ({"kind": "RENDER_ANIMATION", "output": "a", "frame_start": 1, "frame_end": MAX_FRAMES + 1},
             "ask the person whether to split"),
            ({"kind": "RENDER_STILL", "output": "a", "resolution": [16384, 16384]}, "pixels per frame"),
            ({"kind": "RENDER_STILL", "output": "a", "resolution": [3]}, "[width, height]"),
            ({"kind": "RENDER_STILL", "output": "a.jpg"}, "PNG still is written to a .png file"),
            ({"kind": "EXPORT_USD", "output": "scene.abc"}, "EXPORT_USD writes a .usd or .usdc or .usda file"),
            ({"kind": "RENDER_STILL", "output": "a", "queue": "yes"}, "queue must be true or false"),
            ({"kind": "RENDER_STILL", "output": "a", "minutes": 0}, "time budget"),
            ({"kind": "RENDER_STILL", "output": "a", "minutes": True}, "time budget"),
            ({"kind": "RENDER_STILL", "output": "a\nb"}, "printable"),
            ({"kind": "RENDER_STILL", "output": "a", "samples": 0}, "samples must be"),
        ]
        for arguments, message in cases:
            with self.subTest(arguments=arguments), self.assertRaises(JobRequestError) as caught:
                validate_job("angelo_job_start", arguments)
            self.assertIn(message, str(caught.exception))

    def test_a_high_to_low_bake_names_its_sources_cage_rays_and_image(self):
        request = start(kind="BAKE_TEXTURE", target=HANDLE, sources=[HIGH], cage=CAGE, extrusion=0.25, ray_distance=2,
                        image=IMAGE, bake_type="NORMAL")
        self.assertEqual({key: request[key] for key in ("sources", "cage", "extrusion", "ray_distance", "image")},
                         {"sources": [HIGH], "cage": CAGE, "extrusion": 0.25, "ray_distance": 2.0, "image": IMAGE})
        self.assertNotIn("sources", start(kind="BAKE_TEXTURE", target=HANDLE, image=IMAGE))  # A bake of its own surface.
        self.assertEqual(start(kind="BAKE_TEXTURE", target=HANDLE, resolution=[2048, 1024])["resolution"], [2048, 1024])
        base = {"kind": "BAKE_TEXTURE", "target": HANDLE}
        cases = [
            (dict(base, sources=[]), "sources lists 1 to 8"),
            (dict(base, sources=[HIGH] * 9), "sources lists 1 to 8"),
            (dict(base, sources=[HIGH, HIGH]), "each named once"),
            (dict(base, sources=[HANDLE]), "other objects than target"),
            (dict(base, sources=["High"]), "observed object handle"),
            (dict(base, cage=CAGE), "cage applies to a bake from sources"),
            (dict(base, extrusion=0.1), "extrusion applies to a bake from sources"),
            (dict(base, sources=[HIGH], cage=HIGH), "cage is its own object"),
            (dict(base, sources=[HIGH], extrusion=-1), "distance from 0 to 1000"),
            (dict(base, sources=[HIGH], ray_distance=float("inf")), "distance from 0 to 1000"),
            (dict(base, sources=[HIGH], extrusion=True), "distance from 0 to 1000"),
            (dict(base, image="NormalMap"), "resource ID of a loaded image"),
            (dict(base, image=IMAGE, resolution=[64, 64]), "not both"),
            ({"kind": "RENDER_STILL", "output": "a", "sources": [HIGH]}, "RENDER_STILL does not take: sources"),
            ({"kind": "BAKE_SIMULATION", "target": HANDLE, "image": IMAGE}, "BAKE_SIMULATION does not take: image"),
        ]
        for arguments, message in cases:
            with self.subTest(arguments=arguments), self.assertRaises(JobRequestError) as caught:
                validate_job("angelo_job_start", arguments)
            self.assertIn(message, str(caught.exception))

    def test_cancel_names_one_job(self):
        self.assertEqual(validate_job("angelo_job_cancel", {"job": "j12"}), {"job": "j12"})
        for arguments in ({"job": "12"}, {"job": "j0"}, {"job": "j1", "why": "x"}, {}):
            with self.subTest(arguments=arguments), self.assertRaises(JobRequestError):
                validate_job("angelo_job_cancel", arguments)

    def test_effective_values_meet_every_bound_whatever_their_source(self):
        """Regression: a scene's own 20,000-frame range was admitted against the 10,000 maximum."""
        self.assertEqual(check_frames(1, MAX_FRAMES, "the scene"), MAX_FRAMES)
        cases = [(lambda: check_frames(1, 20000, "the scene"),
                  "The frame range of the scene, 1 to 20000, is 20,000 frames; a job does at most 10,000"),
                 (lambda: check_frames(5, 4, "the scene"), "ends before it starts"),
                 (lambda: check_resolution(20000, 100, "the scene"), "The resolution of the scene is 20000x100"),
                 (lambda: check_resolution(12000, 12000, "the image Albedo"), "100,000,000 in all"),
                 (lambda: check_resolution(0, 0, "the image Missing"), "is 0x0"),
                 (lambda: check_samples(20000, "the scene"), "The sample count of the scene is 20,000")]
        for call, message in cases:
            with self.subTest(message=message), self.assertRaises(JobRequestError) as caught:
                call()
            self.assertIn(message, str(caught.exception))
            if "ends before" not in message:
                self.assertIn("Ask the person", str(caught.exception))
        check_resolution(MAX_SIDE, MAX_PIXELS // MAX_SIDE, "the scene")
        check_samples(MAX_SAMPLES, "the scene")

    def test_work_bound_counts_pixels_samples_and_frames(self):
        self.assertEqual(check_work({}, 100, 100, 10, 2), 200000)
        with self.assertRaises(JobRequestError) as caught:
            check_work({}, 3840, 2160, 4096, 700)
        self.assertGreater(3840 * 2160 * 4096 * 700, MAX_WORK)
        self.assertIn("Ask the person", str(caught.exception))

    @unittest.skipIf(jsonschema is None, "jsonschema is not installed")
    def test_declarations_are_valid_and_fit_the_strict_subset(self):
        for tool in JOB_TOOLS:
            with self.subTest(tool=tool["name"]):
                jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
                self.assertEqual(strict_problems(strict_schema(tool["inputSchema"])), [])
        self.assertTrue(JOB_TOOLS[1]["annotations"]["readOnlyHint"])  # Cancel needs no approval.
        self.assertFalse(JOB_TOOLS[0]["annotations"]["readOnlyHint"])


def record(identity, session="chat", status="queued", **fields):
    value = new_record(identity, session, "call", {"kind": "RENDER_STILL", "output": "r.png", "minutes": 30.0},
                       now=fields.pop("created", 100.0))
    value.update(status=status, **fields)
    return value


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_records_round_trip_and_ids_never_repeat(self):
        store = JobStore(self.folder.name)
        first = store.allocate()
        store.add(record(first, status="done", ended=120.0))
        again = JobStore(self.folder.name)
        self.assertEqual(again.get(first)["status"], "done")
        self.assertEqual(again.allocate(), "j2")
        self.assertFalse((Path(self.folder.name) / "jobs.json.tmp").exists())
        if os.name != "nt":  # Windows has no mode bits.
            self.assertEqual(os.stat(Path(self.folder.name) / "jobs.json").st_mode & 0o777, 0o600)

    def test_invalid_history_is_dropped_never_trusted(self):
        path = Path(self.folder.name) / "jobs.json"
        path.write_text(json.dumps({"version": 1, "next": 9, "jobs": [
            record("j3", status="done"), {"id": "../x", "status": "done"}, record("j4", status="exploded"),
            dict(record("j5"), progress="half", detail=[], eta_s="soon")]}))
        store = JobStore(self.folder.name)
        self.assertEqual(sorted(store.records), ["j3", "j5"])
        self.assertEqual((store.get("j5")["progress"], store.get("j5")["detail"], store.get("j5")["eta_s"]), (0.0, {}, None))
        self.assertEqual(store.allocate(), "j9")
        path.write_text("{not json")
        self.assertEqual(JobStore(self.folder.name).records, {})

    def test_only_finished_records_are_trimmed(self):
        store = JobStore(self.folder.name)
        store.add(record("j1", status="running"))
        for number in range(2, job_store.MAX_RECORDS + 3):
            store.add(record("j%d" % number, status="done"))
        self.assertEqual(len(store.records), job_store.MAX_RECORDS)
        self.assertIn("j1", store.records)
        self.assertNotIn("j2", store.records)

    def test_startup_marks_active_records_interrupted(self):
        store = JobStore(self.folder.name)
        store.add(record("j1", status="running"))
        store.add(record("j2", status="queued"))
        store.add(record("j3", status="done"))
        changed = JobStore(self.folder.name).interrupted(now=200.0)
        self.assertEqual(sorted(r["id"] for r in changed), ["j1", "j2"])
        reloaded = JobStore(self.folder.name)
        self.assertEqual({r["id"]: (r["status"], r["reason"]) for r in reloaded.records.values()},
                         {"j1": ("interrupted", "app_closed"), "j2": ("interrupted", "app_closed"), "j3": ("done", "")})

    def test_context_entries_are_compact_and_scoped_to_one_chat(self):
        running = record("j1", status="running", progress=0.4567, eta_s=63.0, started=100.0,
                         detail={"frames_done": 3, "frames_total": 10, "staging": "/abs/.angelo-job-j1",
                                 "output_path": "/abs/r.png", "engine": "CYCLES"})
        self.assertEqual(context_entry(running), {"job": "j1", "kind": "RENDER_STILL", "status": "running",
                                                  "output": "r.png", "progress": 0.46, "eta_s": 65, "frames": "3/10"})
        failed = record("j2", status="failed", reason="x" * 400, started=100.0, ended=112.34,
                        receipt={"note": "Nothing was written.", "output": "r-j2.png"})
        entry = context_entry(failed)
        self.assertEqual((entry["seconds"], len(entry["reason"]), entry["output"]), (12.3, 300, "r-j2.png"))
        self.assertNotIn("reason", context_entry(record("j3", status="done", reason="ignored", ended=1.0)))
        store = JobStore(self.folder.name)
        for number in range(1, 9):
            store.add(record("j%d" % number, status="done", ended=1000.0 + number))
        store.add(record("j9", status="queued"))
        store.add(record("j10", status="queued"))
        store.add(record("j11", session="other", status="running"))
        store.add(record("j12", status="done", ended=1000.0 - job_store.RECENT_SECONDS - 1))
        entries = context_entries(store, "chat", ["j10", "j9"], now=1010.0)
        self.assertEqual(sorted(entries, key=lambda key: int(key[1:])), ["j4", "j5", "j6", "j7", "j8", "j9", "j10"])
        self.assertEqual((entries["j10"]["position"], entries["j9"]["position"]), (1, 2))
        self.assertNotIn("/abs", json.dumps(entries))

    def test_a_finished_job_lists_each_output_with_its_identity(self):
        outputs = [{"file": "NormalMap.png", "image": IMAGE, "name": "NormalMap", "map": "NORMAL"}]
        done = record("j1", status="done", started=1.0, ended=2.0,
                      receipt={"images": ["NormalMap"], "outputs": outputs, "files": 1, "unrestored": ["cycles.samples"]})
        entry = context_entry(done)
        self.assertEqual(entry["outputs"], outputs)
        self.assertNotIn("images", entry)  # Each output names its image; the names are not sent twice.
        self.assertEqual(entry["unrestored"], ["cycles.samples"])
        self.assertEqual(context_entry(record("j2", status="done", receipt={"images": ["Albedo"]}))["images"], ["Albedo"])
        many = record("j3", status="done", receipt={"outputs": [{"file": "f%d.png" % n} for n in range(30)]})
        entry = context_entry(many)
        self.assertEqual((len(entry["outputs"]), entry["outputs_more"]), (job_store.MAX_OUTPUTS, 30 - job_store.MAX_OUTPUTS))
        # A record on disk is data: only known keys with bounded scalar values reach a model.
        hostile = record("j4", status="done", receipt={"outputs": [
            {"file": "ok.png", "path": "/Users/x/secret", "view": {"nested": 1}, "tile": 1001, "name": "n" * 300},
            "not a dict", {"unknown": 1}, {"layers": ["Beauty", "Mist"], "node": "Passes"}]})
        self.assertEqual(job_store.output_entries(hostile["receipt"]),
                         [{"file": "ok.png", "tile": 1001}, {"node": "Passes", "layers": ["Beauty", "Mist"]}])
        self.assertEqual(job_store.output_entries({"outputs": "x"}), [])

    def test_an_output_is_reached_only_by_the_name_the_job_reported(self):
        still = record("j1", status="done", receipt={"outputs": [{"file": "frame_0001_L.png", "view": "left"}]})
        self.assertEqual(job_store.output_file_name(still, "frame_0001_L.png"), "frame_0001_L.png")
        for name in ("frame_0001_R.png", "../frame_0001_L.png", "", None, 7):
            self.assertIsNone(job_store.output_file_name(still, name))
        self.assertIsNone(job_store.output_file_name(still, "frame_0001_L.png", frame=1))  # A still has no frames.
        animation = record("j2", status="done", detail={"frames": [-2, 12]}, receipt={"outputs": [
            {"file": "Passes/Mask####_R.png", "node": "Passes", "slot": "Mask", "view": "right"}]})
        self.assertEqual(job_store.output_file_name(animation, "Passes/Mask####_R.png", frame=7), "Passes/Mask0007_R.png")
        self.assertEqual(job_store.output_file_name(animation, "Passes/Mask####_R.png", frame=-2), "Passes/Mask-0002_R.png")
        for frame in (None, 13, -3, 1.0, "7"):
            self.assertIsNone(job_store.output_file_name(animation, "Passes/Mask####_R.png", frame=frame))
        self.assertIsNone(job_store.output_file_name(record("j3", status="done"), "r.png"))  # Lists no outputs.

    def test_a_job_is_named_by_the_output_it_really_writes(self):
        """A still of several files is a folder: the agent hears that name from the first message on."""
        running = record("j1", status="running", detail={"output": "r", "outputs": [{"file": "frame_0001_L.png"}]})
        self.assertEqual((job_store.output_name(running), context_entry(running)["output"]), ("r", "r"))
        taken = record("j2", status="done", detail={"output": "r"}, receipt={"output": "r-j2", "files": 2})
        self.assertEqual(context_entry(taken)["output"], "r-j2")  # The receipt's when the name was taken meanwhile.
        self.assertEqual(context_entry(record("j3"))["output"], "r.png")
        refused = record("j4", status="failed", reason="x", receipt={"unexpected": ["stray.png"], "note": "n"})
        self.assertEqual(context_entry(refused)["unexpected"], ["stray.png"])


class FakeRunner:
    def __init__(self, *, fail_start=None, fail_finish=None, outcome=("done", "", {"files": 1}), locks=False, facts=None):
        self.identity = None
        self.events, self.state = [], {"progress": 0.0}
        self.fail_start, self.fail_finish, self.outcome, self.locks = fail_start, fail_finish, outcome, locks
        self.facts, self.staging, self.published, self.ended = facts or {"engine": "CYCLES"}, None, None, False

    @property
    def locks_interface(self):
        return self.locks

    def describe(self):
        return dict(self.facts)

    def start(self):
        self.events.append("start")
        if self.fail_start:
            self.startup_effects_possible = False  # This fixture refuses before its staging/mutable work.
            raise self.fail_start
        self.staging = Path("/tmp/.angelo-job-" + self.identity)

    def poll(self):
        return None if self.ended else dict(self.state)

    def stop(self):
        self.events.append("stop")

    def finish(self, stopped):
        self.events.append(("finish", stopped))
        if self.fail_finish:
            raise self.fail_finish
        if not stopped and self.outcome[0] == "done":
            self.published = Path("/workspace/" + self.identity)
        return ("cancelled", "", {"note": "stopped"}) if stopped else self.outcome

    def abandon(self):
        self.events.append("abandon")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.clock, self.changed, self.finished = Clock(), [], []
        self.manager = JobManager(JobStore(self.folder.name), clock=self.clock,
                                  on_change=lambda r: self.changed.append((r["id"], r["status"])),
                                  on_finish=lambda r: self.finished.append(r["id"]))

    def submit(self, runner=None, session="chat", **request):
        request = dict({"kind": "RENDER_STILL", "output": "r.png", "minutes": 30.0}, **request)
        return self.manager.submit(session, "call-1", request, runner or FakeRunner())

    def test_one_job_runs_and_others_wait_only_when_asked(self):
        first = self.submit()
        self.assertEqual((first["id"], first["status"], first["detail"]["staging"]), ("j1", "running", str(Path("/tmp/.angelo-job-j1"))))
        with self.assertRaises(JobRequestError) as caught:
            self.manager.admit({"kind": "RENDER_STILL"})  # What an approval checks first.
        self.assertIn("Job j1 (RENDER_STILL) is still running", str(caught.exception))
        self.assertIs(self.manager.admit({"kind": "RENDER_STILL", "queue": True}), first)
        with self.assertRaises(JobRequestError):
            self.submit()
        queued = [self.submit(queue=True) for _ in range(MAX_QUEUED)]
        self.assertEqual([r["status"] for r in queued], ["queued"] * MAX_QUEUED)
        with self.assertRaises(JobRequestError):
            self.submit(queue=True)
        self.assertEqual(self.manager.queue, ["j2", "j3", "j4", "j5"])

    def test_progress_eta_and_publish(self):
        runner = FakeRunner()
        job = self.submit(runner)
        runner.state = {"progress": 0.01, "frames_done": 0, "frames_total": 4}
        self.clock.now += 10
        self.manager.poll()
        self.assertIsNone(job["eta_s"])  # Too little progress to estimate.
        runner.state = {"progress": 0.25, "frames_done": 1, "frames_total": 4}
        self.clock.now += 10
        self.manager.poll()
        self.assertEqual((job["progress"], job["eta_s"], job["detail"]["frames_done"]), (0.25, 60.0, 1))
        runner.ended = True
        self.manager.poll()
        self.assertEqual((job["status"], job["progress"], job["eta_s"]), ("done", 1.0, None))
        self.assertEqual(job["detail"]["output_path"], str(Path("/workspace/j1")))
        self.assertNotIn("staging", job["detail"])
        self.assertEqual(self.finished, ["j1"])
        self.assertFalse(self.manager.has_work())

    def test_cancel_is_scoped_and_stops_at_the_next_poll(self):
        runner = FakeRunner()
        job = self.submit(runner)
        waiting = self.submit(queue=True)
        with self.assertRaises(JobRequestError):
            self.manager.cancel("j1", by="agent", session="another chat")
        self.manager.cancel("j2", by="agent", session="chat")
        self.assertEqual((waiting["status"], waiting["reason"], waiting["receipt"]["note"]),
                         ("cancelled", "agent", NOT_STARTED))
        self.manager.cancel("j1", by="user")
        self.assertEqual((job["status"], runner.events[-1]), ("stopping", "stop"))
        self.manager.cancel("j1", by="user")  # A second request does not stop twice.
        self.assertEqual(runner.events.count("stop"), 1)
        runner.ended = True
        self.manager.poll()
        self.assertEqual((job["status"], job["reason"]), ("cancelled", "user"))
        self.assertIn(("finish", True), runner.events)
        with self.assertRaises(JobRequestError) as caught:
            self.manager.cancel("j1", by="user")
        self.assertIn("already ended", str(caught.exception))

    def test_time_budget_stops_the_job(self):
        runner = FakeRunner()
        job = self.submit(runner, minutes=1.0)
        runner.state = {"progress": 0.5}
        self.clock.now += 61
        self.manager.poll()
        self.assertEqual((job["status"], runner.events[-1]), ("stopping", "stop"))
        runner.ended = True
        self.manager.poll()
        self.assertEqual((job["status"], job["reason"]), ("cancelled", "time_budget"))

    def test_a_refused_start_fails_that_job_and_the_queue_moves_on(self):
        first = self.submit()
        broken = self.submit(FakeRunner(fail_start=JobStartError("No camera", code="VALIDATION")), queue=True)
        crashed = self.submit(FakeRunner(fail_start=KeyError("x")), queue=True)
        last = self.submit(queue=True)
        self.manager.runners["j1"].ended = True
        self.manager.poll()
        self.assertEqual((first["status"], broken["status"], broken["reason"]), ("done", "failed", "No camera"))
        self.assertEqual(broken["receipt"]["note"], NOTHING_RAN)
        self.assertEqual(crashed["reason"], "Blender couldn't start the job (KeyError)")
        self.assertEqual(last["status"], "running")

    def test_a_failed_finish_never_leaves_staging(self):
        runner = FakeRunner(fail_finish=FileExistsError("taken"))
        job = self.submit(runner)
        runner.ended = True
        self.manager.poll()
        self.assertEqual(job["status"], "failed")
        self.assertIn("FileExistsError", job["reason"])
        self.assertEqual(runner.events[-1], "abandon")

    def test_a_queued_job_is_measured_again_when_it_starts(self):
        self.submit()
        later = FakeRunner(facts={"resolution": [96, 96]})
        job = self.submit(later, queue=True)
        self.assertEqual(job["detail"]["resolution"], [96, 96])
        later.facts = {"resolution": [64, 64]}
        self.manager.runners["j1"].ended = True
        self.manager.poll()
        self.assertEqual(job["detail"]["resolution"], [64, 64])

    def test_closing_the_document_interrupts_every_active_job(self):
        runner = FakeRunner()
        running, waiting = self.submit(runner), self.submit(queue=True)
        self.manager.document_closed()
        self.assertEqual({running["status"], waiting["status"]}, {"interrupted"})
        self.assertEqual((running["reason"], running["receipt"]["note"]), ("file_closed", FILE_CLOSED))
        self.assertIn("abandon", runner.events)
        self.assertFalse(self.manager.has_work())

    def test_records_survive_in_the_file(self):
        self.submit()
        saved = json.loads((Path(self.folder.name) / "jobs.json").read_text())
        self.assertEqual([job["status"] for job in saved["jobs"]], ["running"])


if __name__ == "__main__":
    unittest.main()
