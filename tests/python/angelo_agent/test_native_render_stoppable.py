# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A render job is always stoppable by its budget; its estimate is a fact, never a refusal.

Found on 2026-10-22 (island 1.0.3 Sol trial 3): a 4K, 512-sample GPU render sat for hours in Cycles' Metal BVH
build, waiting for GPU work a memory-starved machine never finished; the main thread, paced by Metal
presentation, ran the budget check hours late and the stop was never read. Pinned here:

* the budget is a deadline the job's own break check reads (``job_deadline``; ``render_breakjob`` and
  ``bake_break`` read ``BKE_angelo_job_deadline.hh``), set at start for that one job (kind and scene) and
  cleared by the job's own end; a job is recorded as stopped by its budget only when the deadline stopped it
  (``deadline_fired``), never one that finished on its own after its budget had run out;
* Cycles' Metal BVH throttler waits read the session's cancel, and a cancelled wait never leaves a completion
  handler writing into a freed BVH (source-level: Metal is not built here);
* an estimate is fitted only to renders of the same scene and frame count whose times are far enough apart for
  the 0.25 s end stamp; it is never a refusal; the approval card shows it; a finished Cycles job says its peak
  memory.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from angelo_agent import job_approvals, job_deadline, job_store  # noqa: E402
from angelo_agent.job_manager import JobManager  # noqa: E402
from angelo_agent.job_store import JobStore  # noqa: E402
from test_native_long_jobs import Clock, FakeRunner  # noqa: E402
from angelo_agent.job_manager import JobStartError  # noqa: E402


def job_runners_error(message):
    return JobStartError(message, code="STALE_CONTEXT")

REAL_NATIVE = job_deadline._native   # DeadlineTests patch it; the build-compatibility test reads the real one.


class FakeNative:
    def __init__(self):
        self.deadlines, self.stats, self.fired, self.raises = [], None, False, None

    def deadline(self, *args):
        if self.raises is not None:
            raise self.raises
        self.deadlines.append(None if args == (None,) else args)
        if args != (None,) and args[2] is not None:
            self.fired = False

    def deadline_fired(self):
        return self.fired

    def render_stats(self, scene):
        return self.stats


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        patcher = mock.patch.object(job_deadline, "_native", return_value=self.native)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.clock = Clock()
        self.manager = JobManager(JobStore(folder.name), clock=self.clock)

    def submit(self, runner, **request):
        request = dict({"kind": "RENDER_STILL", "output": "r.png", "minutes": 30.0}, **request)
        runner.scene = "Scene"
        return self.manager.submit("chat", "call-1", request, runner)

    def test_a_render_sets_its_budget_as_its_own_deadline_and_clears_it_when_it_ends(self):
        runner = FakeRunner()
        job = self.submit(runner)
        self.assertEqual(self.native.deadlines, [("Scene", "RENDER", 1800.0)])   # That scene's render job only.
        runner.ended = True
        self.clock.now += 10
        self.manager.poll()
        # Its end clears that job's deadline only (another manager's job keeps its own: review round 2, P3).
        self.assertEqual((job["status"], self.native.deadlines[-1]), ("done", ("Scene", "RENDER", None)))

    def test_a_bake_sets_the_bake_jobs_deadline(self):
        self.submit(FakeRunner(), kind="BAKE_TEXTURE")
        self.assertEqual(self.native.deadlines[0][1], "OBJECT_BAKE")

    def test_a_job_its_deadline_stopped_is_recorded_as_stopped_by_its_budget(self):
        runner = FakeRunner()
        job = self.submit(runner, minutes=1.0)
        runner.ended, self.native.fired = True, True   # Its break check returned true because of the deadline.
        self.clock.now += 60.2
        self.manager.poll()
        self.assertEqual((job["status"], job["reason"]), ("cancelled", "time_budget"))
        self.assertIn(("finish", True), runner.events)
        self.assertNotIn("stop", runner.events)   # Nothing asked it on the main thread: it stopped itself.

    def test_a_job_that_finished_before_its_deadline_keeps_its_output_however_late_its_end_is_seen(self):
        # Break-it review 2026-10-23 (R1): finished at 59.9 s of a 1 min budget, seen by the timer at 60.15 s (or
        # much later, on a held-up main thread). The clock said "past the budget" and threw the finished still away.
        runner = FakeRunner()
        job = self.submit(runner, minutes=1.0)
        self.clock.now += 59.9
        self.manager.poll()
        runner.ended = True
        self.clock.now += 0.25
        self.manager.poll()
        self.assertEqual(job["status"], "done")
        self.assertIn(("finish", False), runner.events)

    def test_exports_and_the_agents_looks_never_set_a_deadline(self):
        self.submit(FakeRunner(), kind="EXPORT_USD", output="x.usd")
        self.assertEqual(self.native.deadlines, [])
        job_deadline.arm({"kind": "RENDER_PREVIEW", "request": {"minutes": 0.05}, "detail": {}}, "Scene")   # Waits for samples.
        self.assertEqual(self.native.deadlines, [])

    def test_a_cycles_job_keeps_its_peak_memory_and_the_model_reads_it_when_it_ends(self):
        runner = FakeRunner()
        runner.scene = object()
        job = self.submit(runner)
        self.native.stats = {"mem_used_mb": 5000, "mem_peak_mb": 5000}   # The scene's last render's, not this job's.
        self.clock.now += 1
        self.manager.poll()
        self.assertNotIn("peak_memory_mb", job["detail"])   # Nothing kept before the job's own first progress.
        runner.state = {"progress": 0.1}
        self.manager.poll()   # First progress seen (first_progress_s): its renderer has reported since.
        for peak in (900, 1400, 1200):
            self.native.stats = {"mem_used_mb": 100, "mem_peak_mb": peak}
            self.clock.now += 1
            self.manager.poll()
        self.assertEqual(job["detail"]["peak_memory_mb"], 1400)
        runner.ended = True
        self.manager.poll()
        self.assertEqual(job_store.context_entry(job)["peak_memory_mb"], 1400)

    def test_a_deadline_that_cannot_be_set_never_decides_a_start(self):
        # Break-it review round 2 (C1): a queued render whose scene was deleted made the native deadline raise
        # (ReferenceError) before the runner's own admission, which refuses it with its reason (STALE_CONTEXT); the
        # job was left "starting" and ended as an internal finishing error.
        self.native.raises = ReferenceError("StructRNA of type Scene has been removed")
        failing = FakeRunner(fail_start=job_runners_error("The job's scene is no longer shown in its window"))
        job = self.submit(failing)
        self.assertEqual((job["status"], job["reason"]), ("failed", "The job's scene is no longer shown in its window"))
        self.assertIsNone(self.manager.running)
        running = FakeRunner()   # A scene that is there but refuses the deadline: the job runs, the timer keeps the budget.
        job = self.submit(running)
        self.assertEqual((job["status"], running.events), ("running", ["start"]))

    def test_a_job_whose_deadline_was_not_set_never_reads_an_earlier_jobs_stop_by_it(self):
        # Review round 3: job A stopped by its deadline leaves deadline_fired set; job B's arm failed, B finished on
        # its own and was recorded as stopped by its budget, its output thrown away.
        first = FakeRunner()
        job = self.submit(first, minutes=1.0)
        first.ended, self.native.fired = True, True
        self.clock.now += 61
        self.manager.poll()
        self.assertEqual(job["reason"], "time_budget")
        self.native.raises = RuntimeError("no window manager")   # B's arm fails; its start goes on.
        second = FakeRunner()
        job = self.submit(second)
        self.native.raises = None
        second.ended = True
        self.clock.now += 5
        self.manager.poll()
        self.assertEqual(job["status"], "done")
        self.assertIn(("finish", False), second.events)
        self.assertNotIn("deadline_armed", job_store.context_entry(job))

    def test_without_the_native_entries_the_timer_alone_works(self):
        with mock.patch.object(job_deadline, "_native", return_value=None):
            job_deadline.arm({"kind": "RENDER_STILL", "request": {"minutes": 1}, "detail": {}}, "Scene")
            job_deadline.disarm({"kind": "RENDER_STILL"}, "Scene")
            self.assertFalse(job_deadline.ended_by_budget({"kind": "RENDER_STILL", "status": "running", "started": 0}))
            job_deadline.observe_memory({"kind": "RENDER_STILL", "detail": {"engine": "CYCLES"}}, FakeRunner())

    def test_a_build_with_the_earlier_process_wide_deadline_is_not_used(self):
        # Its deadline outlived its job and stopped the person's own renders (break-it review 2026-10-23, R2).
        earlier = SimpleNamespace(deadline=mock.Mock(), render_stats=mock.Mock())
        with mock.patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_jobs=earlier)}):
            self.assertIsNone(REAL_NATIVE())
        current = SimpleNamespace(deadline=mock.Mock(), deadline_fired=mock.Mock(), render_stats=mock.Mock())
        with mock.patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_jobs=current)}):
            self.assertIs(REAL_NATIVE(), current)


class BudgetTests(unittest.TestCase):
    def test_an_estimate_far_over_the_budget_is_a_fact_never_a_refusal(self):
        # Coordinator decision 2026-10-23 (A): with the scene changed between the two fitted renders (a small look,
        # more building, a big render), 25-30% of fits on the Mac 1 bench were over 2x the real time, so a refusal
        # would turn down renders that fit. The deadline ends a render at its budget; no admission check refuses.
        self.assertFalse((ROOT / "scripts/addons_core/angelo_agent/job_budget.py").exists())
        source = (ROOT / "scripts/addons_core/angelo_agent/job_runners.py").read_text(encoding="utf-8")
        self.assertNotIn("job_budget", source)

    def test_the_approval_card_shows_the_estimate(self):
        args = {"kind": "RENDER_STILL", "output": "r.png", "minutes": 30,
                "_facts": {"engine": "CYCLES", "resolution": [1920, 1080], "samples": 64, "estimate_s": 300}}
        self.assertIn("Estimated time: about 5 min 0 s, from the last render on this device",
                      job_approvals.message("angelo_job_start", args)["body"])


class EstimateTests(unittest.TestCase):
    """Two renders of different sizes fit the fixed part; one render scales (a fact, never a refusal)."""

    DEVICE = "GPU (Metal: M)"

    def setUp(self):
        from angelo_agent import render_device
        self.rd = render_device
        render_device._rates.clear()
        self.addCleanup(render_device._rates.clear)

    def observe(self, seconds, resolution, samples, setup=None, scene=1, frames=(1, 1)):
        detail = {"engine": "CYCLES", "device": self.DEVICE, "resolution": list(resolution), "samples": samples,
                  "frames": list(frames), "scene_uid": scene}
        if setup is not None:
            detail["first_progress_s"] = setup
        self.rd.observe({"status": "done", "started": 100.0, "ended": 100.0 + seconds, "detail": detail})

    def target(self, frames=(1, 1)):
        return {"device": self.DEVICE, "resolution": [3840, 2160], "samples": 512, "frames": list(frames)}

    def test_two_sizes_fit_the_fixed_part(self):
        self.observe(2.0, (640, 360), 64)     # 14.7 megapixel-samples
        self.observe(5.0, (1280, 720), 64)    # 59.0: 3 s more for 44.2 more
        setup, rate = self.rd.fitted(self.target(), 1)
        self.assertAlmostEqual(rate, 3.0 / (1280 * 720 * 64 / 1e6 - 640 * 360 * 64 / 1e6))
        self.assertAlmostEqual(setup, 1.0, places=3)
        found = self.rd.estimate(self.target(), 1)
        self.assertEqual(found["estimate_s"], int(round(setup + rate * 3840 * 2160 * 512 / 1e6)))
        self.assertIn("fitted to two earlier finished renders of this scene", found["estimate_from"])

    def test_no_fit_from_renders_closer_than_the_end_stamps_error_a_scene_that_grew_or_one_size(self):
        # Each end is stamped by the 0.25 s poll: with the larger render merely over 1 s, the fit reached 3.2x the
        # real time on the Mac 1 bench (2026-10-23); the two times must be FIT_MIN_SPREAD apart.
        self.observe(0.7, (640, 360), 64)
        self.observe(1.2, (1280, 720), 64)
        self.assertIsNone(self.rd.fitted(self.target(), 1))
        self.rd._rates.clear()
        self.observe(39.7, (1920, 1080), 128)   # The island trial: j5, then j6 smaller but slower (the scene grew).
        self.observe(219.9, (1280, 720), 96)
        self.assertIsNone(self.rd.fitted(self.target(), 1))
        self.rd._rates.clear()
        self.observe(5.0, (1280, 720), 64)
        self.observe(5.2, (1280, 720), 64)
        self.assertIsNone(self.rd.fitted(self.target(), 1))

    def test_renders_of_another_scene_or_frame_count_are_never_fitted(self):
        # Break-it review 2026-10-23 (D3): a 2 s still and a 100-frame animation (about 1 s of setup per frame) fitted
        # its per-frame setup as work: 698 s estimated for a 73 s still, refused at a 5 min budget.
        self.observe(2.0, (1280, 720), 64)
        self.observe(130.0, (640, 360), 32, frames=(1, 100))
        self.assertIsNone(self.rd.fitted(self.target(), 1))
        self.assertIsNone(self.rd.fitted(self.target(frames=(1, 100)), 1))
        self.observe(300.0, (1280, 720), 32, frames=(1, 100))   # Two animations of 100 frames: a fit for that length.
        self.assertIsNotNone(self.rd.fitted(self.target(frames=(1, 100)), 1))
        self.assertIsNone(self.rd.fitted(self.target(frames=(1, 50)), 1))
        # Another scene (a second scene tab) has its own fixed part and rate.
        self.rd._rates.clear()
        self.observe(2.0, (640, 360), 64, scene=1)
        self.observe(5.0, (1280, 720), 64, scene=2)
        self.assertIsNone(self.rd.fitted(self.target(), 1))
        self.assertIsNone(self.rd.fitted(self.target(), 2))
        self.assertIsNone(self.rd.fitted(self.target(), None))   # An unknown scene is never fitted.

    def test_one_render_scales_its_sampling_time_when_its_setup_was_seen(self):
        self.observe(1.5, (1280, 720), 96, setup=1.4)
        found = self.rd.estimate(self.target(), 1)
        self.assertEqual(found["estimate_s"], int(round(1.4 + 0.1 * (3840 * 2160 * 512) / (1280 * 720 * 96))))
        self.assertIn("1.4 s of its first frame before its first sample", found["estimate_from"])

    def test_one_render_pays_its_setup_once_per_frame_and_says_its_basis(self):
        # Break-it review round 2 (C2): a 10 s still, 8 s of it before its first sample, scaled to a 100-frame
        # animation added that setup once and read 0.26x the real time (every frame syncs and builds again).
        self.observe(10.0, (1280, 720), 64, setup=8.0)
        animation = {"device": self.DEVICE, "resolution": [1280, 720], "samples": 64, "frames": [1, 100]}
        found = self.rd.estimate(animation, 1)
        self.assertEqual(found["estimate_s"], 1000)   # 100 x (8 s setup + 2 s sampling).
        self.assertIn("1 frames x views against this one's 100", found["estimate_from"])
        found = self.rd.estimate(animation, 2)   # The only render known is another scene's: said so.
        self.assertIn("of another scene", found["estimate_from"])
        self.rd._rates.clear()   # An animation scaled back to a still: its first frame's setup, not 100 of them.
        self.observe(802.0, (1280, 720), 64, setup=8.0, frames=(1, 100))
        self.assertEqual(self.rd.estimate(self.target(), 1)["estimate_s"],
                         int(round(8.0 + (8.02 - 8.0) * (3840 * 2160 * 512) / (1280 * 720 * 64))))

    def test_a_stereo_render_is_twice_the_work_and_never_fitted_with_a_mono_one(self):
        # Break-it review round 2 (C3): views were never recorded, so stereo and mono renders were fitted together.
        self.observe(2.0, (640, 360), 64)
        detail = {"engine": "CYCLES", "device": self.DEVICE, "resolution": [1280, 720], "samples": 64,
                  "frames": [1, 1], "views": 2, "scene_uid": 1}
        self.rd.observe({"status": "done", "started": 100.0, "ended": 110.0, "detail": detail})
        self.assertIsNone(self.rd.fitted(self.target(), 1))
        self.assertEqual(self.rd.work(detail), 2 * self.rd.work(dict(detail, views=1)))
        source = (ROOT / "scripts/addons_core/angelo_agent/job_runners.py").read_text(encoding="utf-8")
        self.assertIn('facts["views"] = views', source)

    def test_the_manager_records_when_a_job_first_showed_progress(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        clock = Clock()
        manager = JobManager(JobStore(folder.name), clock=clock)
        runner = FakeRunner()
        runner.scene_uid = 7
        job = manager.submit("chat", "call-1", {"kind": "RENDER_STILL", "output": "r.png", "minutes": 30.0}, runner)
        self.assertEqual(job["detail"]["scene_uid"], 7)   # Which scene's renders a fit may compare: local only.
        self.assertNotIn("scene_uid", job_store.context_entry(job))
        clock.now += 2.0
        manager.poll()
        self.assertNotIn("first_progress_s", job["detail"])
        runner.state = {"progress": 0.05}
        clock.now += 1.5
        manager.poll()
        self.assertEqual(job["detail"]["first_progress_s"], 3.5)
        self.assertNotIn("first_progress_s", job_store.context_entry(job))


class NativeSourceTests(unittest.TestCase):
    """The C++ side, read as source (no Metal or Blender build here)."""

    def test_the_metal_bvh_throttler_waits_read_the_sessions_cancel(self):
        source = (ROOT / "intern/cycles/device/metal/bvh.mm").read_text(encoding="utf-8")
        self.assertIn("bool acquire(const size_t bytes_to_be_wired, Progress &progress)", source)
        self.assertIn("bool wait_for_all(Progress &progress)", source)
        self.assertEqual(source.count("if (progress.get_cancel()) {\n        return false;"), 2)
        self.assertIn("if (!g_bvh_build_throttler.wait_for_all(progress)) {", source)
        self.assertEqual(source.count("if (!g_bvh_build_throttler.acquire(wired_size, progress)) {"), 3)
        # Acquire comes before any command buffer exists: an uncommitted command buffer that is dropped still runs
        # its completion handlers on deallocation (macOS 26), which would release every buffer a second time.
        for name in ("build_BLAS_mesh", "build_BLAS_hair", "build_BLAS_pointcloud"):
            body = source[source.index("bool BVHMetal::%s(" % name):]
            body = body[:body.index("\nbool BVHMetal::", 10)]
            self.assertLess(body.index("g_bvh_build_throttler.acquire(wired_size, progress)"),
                            body.index("[queue commandBuffer]"), name)
        # Completion handlers write only through the build owner, never through a BVH a cancel may have freed.
        handlers = re.findall(r"addCompletedHandler:\^\(id<MTLCommandBuffer> /\*command_buffer\*/\) \{(.*?)\}\];",
                              source, re.S)
        self.assertTrue(handlers)
        self.assertFalse([h for h in handlers if re.search(r"(?<!built_)set_accel_struct\(", h)])
        self.assertEqual(source.count("set_built_accel_struct(owner, "), 6)
        self.assertIn("build_owner->bvh = nullptr;", source)

    def test_only_the_jobs_own_break_check_reads_its_deadline_and_its_end_clears_it(self):
        render = (ROOT / "source/blender/editors/render/render_internal.cc").read_text(encoding="utf-8")
        self.assertIn("job_deadline_passed(WM_JOB_TYPE_RENDER, rj->scene->id.session_uid)",
                      render[render.index("static bool render_breakjob("):])
        end = render[render.index("static void render_endjob("):]
        self.assertIn("job_deadline_job_ended(WM_JOB_TYPE_RENDER, rj->scene->id.session_uid)",
                      end[:end.index("\n}\n")])
        bake = (ROOT / "source/blender/editors/object/object_bake_api.cc").read_text(encoding="utf-8")
        # The bake's check gets the scene's session_uid as a value: the scene's Render keeps the handle after the job's
        # data is freed (and a render waiting on the bake shares that display, so it is never reset; review round 3).
        self.assertIn("job_deadline_passed(WM_JOB_TYPE_OBJECT_BAKE, uid)", bake)
        self.assertIn("RE_test_break_cb(re, POINTER_FROM_UINT(bkr->scene->id.session_uid), bake_break);", bake)
        free = bake[bake.index("static void bake_freejob("):]
        self.assertIn("job_deadline_job_ended(WM_JOB_TYPE_OBJECT_BAKE, bkr->scene->id.session_uid)",
                      free[:free.index("\n}\n")])
        # Review round 2: a render job killed before it ran gets only its free (P4); the bake's Render outlives the
        # bake, so its break check drops the handle to the freed job data (P1).
        render_free = render[render.index("static void render_freejob("):]
        self.assertIn("job_deadline_job_ended(WM_JOB_TYPE_RENDER, rj->angelo_scene_uid)",
                      render_free[:render_free.index("\n}\n")])
        self.assertNotIn("RE_test_break_cb", free[:free.index("\n}\n")])
        header = (ROOT / "source/blender/blenkernel/BKE_angelo_job_deadline.hh").read_text(encoding="utf-8")
        self.assertIn("job_deadline_fired.store(true", header)
        self.assertIn("std::min(seconds, job_deadline_max_seconds)", header)   # No int64 overflow (P9).
        self.assertNotIn("job_deadline_passed()", render + bake)   # No process-wide deadline is left.

    def test_the_native_module_offers_deadline_deadline_fired_and_render_stats(self):
        source = (ROOT / "source/blender/python/intern/bpy_angelo_jobs.cc").read_text(encoding="utf-8")
        for name in ("deadline", "deadline_fired", "render_stats"):
            self.assertIn('{"%s",' % name, source)
        # deadline(scene, job_type, None) clears that job's deadline only (review round 2, P3).
        self.assertIn("bke::angelo::job_deadline_job_ended(int(type->type), scene->id.session_uid);", source)


if __name__ == "__main__":
    unittest.main()
