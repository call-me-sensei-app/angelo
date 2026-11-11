# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles compiling its render kernels (the first render after an update) never costs a job its budget, its estimate
or its explanation, and nothing else is ever credited as one (angelo_render_device.kernels). No Blender needed.

Blender 5.2.4 changed Cycles' kernels, so every Mac's first render after the 1.0.11 update compiles them: 134 s on
an M4 Pro (81 archives), over 150 s for 5.3. That time counted against the job's budget (an agent's short budget was
cut off), went into the next render's estimate (a light still went to a child render, a 100-frame animation was
estimated at hours), and the card showed 0% with no word of why. Cycles says so in its own status text, which this
reads. The break-it review of 2026-11-11 (side-session-notes/cold-kernel-budget-break-it-2026-11-11.md) found the
credit could also be gained without a compile; each of its findings has a test here (F1-F10, and the wording pin).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import collections
import math
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(ROOT / "scripts/modules"), str(HERE)]
from angelo_render_device import facts, kernels  # noqa: E402
from angelo_agent import job_chat, job_deadline, job_render_child  # noqa: E402
from angelo_agent import view_render_timing as timing  # noqa: E402
from angelo_agent.job_manager import JobManager  # noqa: E402
from angelo_agent.job_store import JobStore  # noqa: E402

# What the render_stats handler receives (Cycles' raw infostr, blender/session.cpp update_status_progress): the
# status alone in the application; "Remaining: ... | Mem: NM | status | substatus" in a background child.
LOADING = "Loading render kernels (may take a few minutes the first time)"
COMPILING = "12 / 37 render kernels loaded (may take a few minutes the first time)"
CHILD_LOADING = "Mem: 12M | Loading render kernels (may take a few minutes the first time)"
CHILD_COMPILING = ("Remaining: 00:10.00 | Mem: 80M | 12 / 37 render kernels loaded (may take a few minutes the "
                   "first time)")
SAMPLING = "Sample 1/32"
DENOISER = "Loading denoising kernels (may take a few minutes the first time)"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Watch(unittest.TestCase):
    def test_a_compile_is_timed_from_cycles_own_words_and_ends_with_them(self):
        clock = Clock()
        watch = kernels.Watch(clock)
        watch.stats(LOADING)
        clock.now += 30
        watch.stats(COMPILING)
        self.assertTrue(watch.loading())
        self.assertEqual(watch.compile_s(), 30)
        clock.now += 104
        watch.stats(SAMPLING)
        self.assertFalse(watch.loading())
        self.assertEqual(watch.compile_s(), 134)
        watch.reset()   # The next render starts afresh (render_init, and every job's start).
        self.assertEqual((watch.loading(), watch.compile_s()), (False, 0))

    def test_a_background_childs_status_lines_are_read_too(self):
        clock = Clock()
        watch = kernels.Watch(clock)
        watch.stats(CHILD_LOADING)
        clock.now += 20
        watch.stats(CHILD_COMPILING)
        clock.now += 5
        watch.stats("Remaining: 00:01.00 | Mem: 90M | Sample 3/32")
        self.assertEqual(watch.compile_s(), 25)

    def test_a_warm_render_has_no_compile(self):
        watch = kernels.Watch(Clock())
        watch.stats(SAMPLING)
        self.assertEqual((watch.loading(), watch.compile_s()), (False, 0))

    def test_the_credit_is_capped(self):
        self.assertEqual(kernels.credited(134.0), 134.0)
        self.assertEqual(kernels.credited(5000.0), kernels.CAP_S)
        self.assertEqual(kernels.credited(-3), 0.0)
        self.assertEqual(kernels.credited(None), 0.0)
        self.assertEqual(kernels.CAP_S, 600.0)

    def test_the_status_text_it_reads_is_cycles_own(self):
        # Wording pin: if Cycles rewords its status, crediting stops (fails closed: the plain budget), and this says so.
        scene = (ROOT / "intern/cycles/scene/scene.cpp").read_text(encoding="utf-8")
        metal = (ROOT / "intern/cycles/device/metal/device_impl.mm").read_text(encoding="utf-8")
        denoiser = (ROOT / "intern/cycles/integrator/denoiser.cpp").read_text(encoding="utf-8")
        self.assertIn('set_status("%s")' % kernels.LOADING_STATUS, scene)
        self.assertIn('"%%d / %%d render kernels loaded (%s)"' % kernels.MARK, metal)
        self.assertTrue(kernels.is_loading(kernels.LOADING_STATUS))
        self.assertTrue(kernels.is_loading("3 / 81 render kernels loaded (%s)" % kernels.MARK))
        # The denoiser says the same words after the first sample: not a render-kernel compile, never credited (F9).
        self.assertIn('set_status("%s")' % DENOISER, denoiser)
        self.assertFalse(kernels.is_loading(DENOISER))


class Findings(unittest.TestCase):
    """The break-it review's findings, one test each."""

    def test_f1_a_bake_after_a_cold_render_gets_no_credit_and_every_job_start_resets_the_watch(self):
        clock = Clock()
        watch = kernels.Watch(clock)
        watch.stats(LOADING)
        clock.now += 134
        watch.stats(SAMPLING)   # The person's (or an earlier job's) render compiled for 134 s.
        with mock.patch.object(kernels, "WATCH", watch):
            jobs, record = manager({"kind": "BAKE_TEXTURE", "minutes": 1.0, "queue": False}, BakeRunner())
            self.assertEqual(watch.compile_s(), 0, "the job's start reset the watch")
            watch.stats(LOADING)
            clock.now += 100
            watch.stats(SAMPLING)   # Whatever the watch says meanwhile, a bake is not credited (it says no stats).
            jobs.clock.now += 61
            jobs.poll()
        self.assertIn(record["status"], ("stopping", "cancelled"))
        self.assertNotIn("kernel_compile_s", record["detail"])
        self.assertEqual(job_deadline.budget_s(record), 60)

    def test_f2_only_cycles_exact_status_starts_the_watch_and_a_render_end_closes_it(self):
        clock = Clock()
        watch = kernels.Watch(clock)
        for text in ("Updating Geometry BVH %s 1/2" % LOADING, "Denoising | %s" % LOADING, "%s | Sample 1/4" % SAMPLING,
                     "Mem: 1M | Updating Geometry BVH x | %s" % LOADING, DENOISER, LOADING + " ", "x" + COMPILING):
            watch.stats(text)
            clock.now += 50
        self.assertEqual((watch.loading(), watch.compile_s()), (False, 0), "names carrying the words are not Cycles'")
        watch.stats(LOADING)
        clock.now += 30
        watch.close()   # render_complete / render_cancel / render_post: the render ended while its status matched.
        clock.now += 3600
        self.assertEqual((watch.loading(), watch.compile_s()), (False, 30), "an ended render's watch never grows")
        for name in ("render_complete", "render_cancel", "render_post", "render_init", "render_stats"):
            self.assertIn(name, dict(kernels._HANDLERS))

    def test_f3_a_looks_compile_is_capped_and_bounded_per_request_and_its_call_outlives_its_budget(self):
        found = timing.look_timing(seconds=2000.0, first_sample=1990.0, compiled=1980.0, compiled_setup=1980.0)
        self.assertEqual(found["kernel_compile_s"], kernels.CAP_S)
        self.assertEqual(found["spent_s"], 2000.0 - kernels.CAP_S)
        exempted = 0.0
        for _look in range(5):   # Kernels never cached: every look compiles; the request still runs out.
            this = timing.exempt(500.0, exempted)
            exempted += this
        self.assertEqual(exempted, kernels.CAP_S, "a request is credited at most one cap of compile in all")
        self.assertEqual(timing.exempt(500.0, kernels.CAP_S), 0.0)
        for minutes in (0.25, 1.0, 6.0):   # The call never expires before the look's job may end (its credited budget).
            record = {"request": {"minutes": minutes}, "detail": {"kernel_compile_s": 10_000.0}}
            self.assertGreaterEqual(timing.look_timeout(minutes, 45), job_deadline.budget_s(record) + 45)

    def test_f4_a_warm_load_is_no_compile_and_never_shows(self):
        clock = Clock()
        watch = kernels.Watch(clock)
        for _frame in range(100):   # An animation: each frame's warm load lasts tenths of a second.
            watch.stats(LOADING)
            self.assertFalse(watch.loading(), "the card does not flicker to compiling")
            clock.now += 0.4
            watch.stats(SAMPLING)
            clock.now += 5
        self.assertEqual(watch.compile_s(), 0)
        self.assertNotIn("compiling", timing.look_timing(seconds=6.0, first_sample=1.0, compiled=watch.compile_s(),
                                                         compiled_setup=0.0)["took"])
        watch.stats(LOADING)
        clock.now += kernels.MIN_COMPILE_S + 0.5   # Past the threshold: a compile, counted whole.
        self.assertTrue(watch.loading())
        self.assertEqual(watch.compile_s(), kernels.MIN_COMPILE_S + 0.5)

    def test_f5_reading_the_compile_while_cycles_ends_it_never_raises(self):
        watch = kernels.Watch()
        errors, done = [], threading.Event()

        def cycles():
            while not done.is_set():
                watch.stats(LOADING)
                watch.stats(SAMPLING)

        thread = threading.Thread(target=cycles, daemon=True)
        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            thread.start()
            for _read in range(200_000):
                try:
                    value = watch.compile_s()
                    watch.loading()
                    watch.compile_s(until=watch.clock())
                except Exception as error:   # float - None before the fix (probe P1).
                    errors.append(error)
                    break
                self.assertGreaterEqual(value, 0.0)
        finally:
            done.set()
            thread.join(5)
            sys.setswitchinterval(interval)
        self.assertEqual(errors, [])

    def test_f6_no_rearm_once_the_deadline_fired_or_the_job_is_not_running(self):
        native = mock.Mock()
        native.deadline_fired.return_value = True
        record = {"kind": "RENDER_STILL", "status": "running", "request": {"minutes": 1.0},
                  "detail": {"engine": "CYCLES", job_deadline.ARMED: True}}
        runner = Runner(loading=True, compile_s=120.0)
        runner.scene = object()
        with mock.patch.object(job_deadline, "_native", return_value=native):
            job_deadline.observe_kernels(record, runner, 70.0)
            native.deadline.assert_not_called()   # Rearming would clear deadline_fired: the stop is the budget's.
            native.deadline_fired.return_value = False
            record["status"], runner.state = "stopping", (True, 200.0)
            job_deadline.observe_kernels(record, runner, 71.0)
            native.deadline.assert_not_called()
            record["status"], runner.state = "running", (True, 240.0)
            job_deadline.observe_kernels(record, runner, 72.0)
            native.deadline.assert_called_once()

    def test_f7_a_texture_sets_rearm_and_the_manager_agree_on_its_budget(self):
        # Bakes are never credited (F1), so BAKE_MAPS' rearm (start + minutes) and the manager's budget are one.
        record = {"kind": "BAKE_MAPS", "status": "running", "request": {"minutes": 2.0},
                  "detail": {"engine": "CYCLES", "kernel_compile_s": 300.0}}
        self.assertIsNone(job_deadline.kernel_state(record, BakeRunner()))
        record["detail"].pop("kernel_compile_s")
        job_deadline.observe_kernels(record, BakeRunner(), 30.0)
        self.assertEqual(job_deadline.budget_s(record), 120.0)
        source = (ROOT / "scripts/addons_core/angelo_agent/job_bake_maps.py").read_text(encoding="utf-8")
        self.assertIn('self.deadline_at = time.monotonic() + float(self.request["minutes"]) * 60', source)

    def test_f8_a_childs_single_status_compile_is_credited_while_it_loads(self):
        clock = Clock()
        child = bare_child(clock)
        child._line(job_render_child.MARK + '{"kernels": [true, 0.0, 0.5]}')   # CUDA/OptiX: one status, then quiet.
        clock.now += 120
        loading, seconds = child.kernels()
        self.assertTrue(loading)
        self.assertEqual(seconds, 120.5)
        child.credit(seconds)
        self.assertEqual(child.deadline, 500.0 + 120.5)
        clock.now += 5000
        child.credit(child.kernels()[1])
        self.assertEqual(child.deadline, 500.0 + kernels.CAP_S, "a hung compile still ends the child")
        child._line(job_render_child.MARK + '{"kernels": [false, 0.0, 0.4]}')   # A warm load said as it ended.
        self.assertEqual(child.kernels(), (False, 0.0))

    def test_f9_a_load_after_the_first_sample_is_not_preparation(self):
        clock = Clock()
        watch = kernels.Watch(clock)
        clock.now += 10
        first_sample = clock.now   # Preparation: 10 s, no compile.
        clock.now += 5
        watch.stats(LOADING)
        clock.now += 30
        watch.stats(SAMPLING)
        self.assertEqual(watch.compile_s(until=first_sample), 0)
        self.assertEqual(watch.compile_s(), 30)
        found = timing.look_timing(seconds=50.0, first_sample=10.0, compiled=30.0, compiled_setup=0.0)
        self.assertEqual((found["prepare_seconds"], found["sampling_seconds"]), (10.0, 10.0))
        facts._rates.clear()
        self.addCleanup(facts._rates.clear)
        cold = estimates_record(seconds=50.0, setup=10.0, compile_s=30.0, compile_setup_s=0.0)
        warm = estimates_record(seconds=20.0, setup=10.0)
        facts.observe(cold)
        found = facts.estimate(QUERY, 7)["estimate_s"]
        facts._rates.clear()
        facts.observe(warm)
        self.assertEqual(found, facts.estimate(QUERY, 7)["estimate_s"])

    def test_f10_a_child_report_with_nan_or_infinity_is_refused(self):
        child = bare_child(Clock())
        for bad in ("[true, Infinity, 0.0]", "[true, 0.0, NaN]", "[true, -1.0, 0.0]", '[true, "1", 0.0]',
                    "[true, 1.0]"):
            child._line(job_render_child.MARK + '{"kernels": %s}' % bad)
            self.assertEqual(child.kernels(), (False, 0.0), bad)
        self.assertEqual(kernels.credited(math.inf), 0.0)
        self.assertEqual(kernels.credited(math.nan), 0.0)


class Residuals(unittest.TestCase):
    """The r2 re-review's residuals (side-session-notes/cold-kernel-budget-break-it-2026-11-11-r2.md), one test each."""

    def test_r1_only_the_compile_before_the_first_progress_is_credited(self):
        # A view layer named exactly Cycles' status is Cycles' status while it denoises and finishes: after the
        # first progress nothing more is credited or said.
        runner = Runner(loading=True, compile_s=0.0)
        jobs, record = manager({"kind": "RENDER_STILL", "minutes": 1.0, "queue": False}, runner)
        jobs.clock.now += 40
        runner.state = (False, 30.0)
        runner.poll = lambda: {"progress": 0.1}
        jobs.poll()
        self.assertEqual((record["detail"]["kernel_compile_s"], record["detail"]["kernel_compile_setup_s"]),
                         (30.0, 30.0))
        runner.state = (True, 230.0)
        runner.poll = lambda: {"progress": 0.9}
        jobs.clock.now += 55   # 95 s: past 60 + 30.
        jobs.poll()
        self.assertFalse(record["detail"]["kernels_loading"], "not said either")
        self.assertEqual(record["detail"]["kernel_compile_s"], 30.0)
        self.assertEqual(job_deadline.budget_s(record), 90.0)
        self.assertIn(record["status"], ("stopping", "cancelled"))

    def test_r2_only_a_cycles_looks_call_waits_for_a_compile(self):
        self.assertEqual(timing.look_timeout(0.25, 45, cycles=False), 15 + 45)
        self.assertEqual(timing.look_timeout(0.25, 45, cycles=True), 15 + kernels.CAP_S + 45)

    def test_r4_the_model_is_told_loading_or_compiling(self):
        self.assertIn("loading or compiling its render kernels", job_deadline.COMPILE_PHASE)
        found = timing.look_timing(seconds=30.0, first_sample=20.0, compiled=12.0, compiled_setup=12.0)
        self.assertIn("12.0 s loading or compiling Cycles' render kernels", found["took"])
        found = timing.look_timing(seconds=30.0, first_sample=None, compiled=12.0, compiled_setup=12.0)
        self.assertIn("loading or compiling Cycles' render kernels", found["took"])
        source = (ROOT / "scripts/modules/angelo_render_device/kernels.py").read_text(encoding="utf-8")
        self.assertNotIn("tenths of a second on any disk", source)


QUERY = {"device": "GPU (Metal: fixture)", "resolution": [640, 360], "samples": 32, "frames": [1, 1]}


def estimates_record(seconds, setup, compile_s=None, compile_setup_s=None, frames=(1, 1)):
    detail = {"engine": "CYCLES", "device": "GPU (Metal: fixture)", "resolution": [640, 360], "samples": 32,
              "frames": list(frames), "first_progress_s": setup, "scene_uid": 7}
    if compile_s is not None:
        detail["kernel_compile_s"] = compile_s
    if compile_setup_s is not None:
        detail["kernel_compile_setup_s"] = compile_setup_s
    return {"status": "done", "detail": detail, "started": 100.0, "ended": 100.0 + seconds}


class Estimates(unittest.TestCase):
    def setUp(self):
        facts._rates.clear()
        self.addCleanup(facts._rates.clear)

    def test_the_next_estimate_never_counts_a_one_time_compile(self):
        facts.observe(estimates_record(seconds=140.0, setup=136.0, compile_s=134.0, compile_setup_s=134.0))
        cold = facts.estimate(QUERY, 7)["estimate_s"]
        facts._rates.clear()
        facts.observe(estimates_record(seconds=6.0, setup=2.0))   # The same render, warm.
        warm = facts.estimate(QUERY, 7)["estimate_s"]
        self.assertEqual(cold, warm)
        self.assertLess(cold, 20, "a light still stays under the child route's threshold")

    def test_an_animation_after_a_cold_render_is_not_charged_the_compile_per_frame(self):
        facts.observe(estimates_record(seconds=140.0, setup=136.0, compile_s=134.0, compile_setup_s=134.0))
        found = facts.estimate(dict(QUERY, frames=[1, 100]), 7)["estimate_s"]
        self.assertLess(found, 700, "100 frames of a 6 s render, never 100 compiles")


class Runner:
    """A running in-app Cycles render whose kernel state the test sets."""
    identity, staging, published, child, budget_fired = None, None, None, None, False

    def __init__(self, loading=False, compile_s=0.0):
        self.state = (loading, compile_s)

    def describe(self):
        return {"engine": "CYCLES"}

    def start(self):
        pass

    def poll(self):
        return {"progress": 0.0}

    def finish(self, stopped):
        return ("cancelled" if stopped else "done", "", {})

    def kernels(self):
        return self.state


class BakeRunner(Runner):
    """A Cycles bake: it says no render stats, so it has no kernel state of its own."""
    kernels = None


def manager(request, runner):
    clock = Clock()
    jobs = JobManager(JobStore(Path(tempfile.mkdtemp())), clock=clock)
    jobs.clock = clock
    record = jobs.submit("s", "c", request, runner)
    jobs.poll()
    return jobs, record


def bare_child(clock):
    child = job_render_child.Child.__new__(job_render_child.Child)
    child.lock, child.clock = threading.Lock(), clock
    child.fraction = child.peak_mb = child.device = child.done = None
    child.tail, child.kernels_state = collections.deque(maxlen=4), None
    child.deadline, child.base_deadline = 500.0, 500.0
    return child


class Budget(unittest.TestCase):
    def test_a_short_budget_is_not_spent_on_a_compile(self):
        runner = Runner(loading=True, compile_s=0.0)
        jobs, record = manager({"kind": "RENDER_STILL", "minutes": 1.0, "queue": False}, runner)
        jobs.clock.now += 130
        runner.state = (True, 130.0)
        jobs.poll()
        self.assertEqual(record["status"], "running", "130 s compiling does not use the 60 s budget")
        self.assertTrue(record["detail"]["kernels_loading"])
        self.assertEqual(record["detail"]["kernel_compile_s"], 130.0)
        runner.state = (False, 134.0)
        jobs.clock.now += 50   # 180 s since the start, 134 s of them compiling: 46 s of rendering.
        jobs.poll()
        self.assertEqual(record["status"], "running")
        jobs.clock.now += 20   # 66 s of rendering: past the 60 s budget.
        jobs.poll()
        self.assertIn(record["status"], ("stopping", "cancelled"))
        self.assertEqual(record["reason"], "time_budget")

    def test_the_credit_never_passes_its_cap(self):
        runner = Runner(loading=True, compile_s=0.0)
        jobs, record = manager({"kind": "RENDER_STILL", "minutes": 1.0, "queue": False}, runner)
        jobs.clock.now += 60 + kernels.CAP_S + 1
        runner.state = (True, 60 + kernels.CAP_S + 1)   # A compile that never ends: the cap still ends the job.
        jobs.poll()
        self.assertIn(record["status"], ("stopping", "cancelled"))
        self.assertEqual(job_deadline.budget_s(record), 60 + kernels.CAP_S)

    def test_the_first_progress_keeps_the_compile_before_it(self):
        runner = Runner(loading=True, compile_s=0.0)
        jobs, record = manager({"kind": "RENDER_STILL", "minutes": 10.0, "queue": False}, runner)
        jobs.clock.now += 40
        runner.state = (False, 35.0)
        runner.poll = lambda: {"progress": 0.1}
        jobs.poll()
        self.assertEqual(record["detail"]["kernel_compile_setup_s"], 35.0)


class Words(unittest.TestCase):
    def test_the_card_says_what_it_waits_for(self):
        record = {"status": "running", "detail": {"kernels_loading": True}, "receipt": {}, "progress": 0.0}
        text = job_chat.progress_text(record)
        self.assertIn("render kernels", text)
        self.assertNotIn("0%", text)

    def test_the_agent_is_told_it_is_a_one_time_compile_outside_the_budget(self):
        text = job_deadline.kernel_note({"kernels_loading": True, "kernel_compile_s": 42.0})
        self.assertIn("compiling its render kernels", text)
        self.assertIn("not counted against the time budget", text)
        self.assertEqual("", job_deadline.kernel_note({}))


class Looks(unittest.TestCase):
    def test_a_look_says_the_compile_apart_from_the_scenes_preparation(self):
        found = timing.look_timing(seconds=160.0, first_sample=140.0, compiled=134.0, compiled_setup=134.0)
        self.assertEqual(found["kernel_compile_s"], 134.0)
        self.assertEqual(found["prepare_seconds"], 6.0)
        self.assertEqual(found["sampling_seconds"], 20.0)
        self.assertEqual(found["spent_s"], 26.0, "the request's look budget is not spent on the compile")
        self.assertIn("compiling Cycles' render kernels", found["took"])
        self.assertNotIn("iterate with LIT", found["took"], "6 s of preparation is not a heavy scene")

    def test_a_warm_look_reads_as_before(self):
        found = timing.look_timing(seconds=30.0, first_sample=12.0, compiled=0.0, compiled_setup=0.0)
        self.assertNotIn("kernel_compile_s", found)
        self.assertEqual(found["spent_s"], 30.0)
        self.assertIn("12.0 s preparing the scene for rendering and 18.0 s sampling", found["took"])
        self.assertIn("iterate with LIT", found["took"])


class ChildRegistration(unittest.TestCase):
    def test_a_child_render_registers_the_kernel_watch_before_its_own_reports(self):
        # A child is a background run, where Cycles' register sets no Angelo device module up (it waits for a render),
        # so nothing registered the watch there and a child's compile was never credited: the cold GUI proof of
        # 2026-11-11 found a 101 s child compile charged to the next estimate. render_child registers it itself, first
        # (its handlers then read the watch the same status already updated).
        import ast
        source = (ROOT / "scripts/addons_core/angelo_agent/render_child.py").read_text(encoding="utf-8")
        main = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = [ast.unparse(node) for node in ast.walk(main) if isinstance(node, ast.Call)]
        order = [line for line in ast.unparse(main).splitlines()
                 if "kernels.register()" in line or "handlers.render_stats.append" in line
                 or "handlers.render_post.append(report_kernels)" in line]
        self.assertTrue(any(call.endswith("kernels.register()") for call in calls), calls)
        self.assertIn("kernels.register()", order[0], order)
        cycles = (ROOT / "intern/cycles/blender/addon/__init__.py").read_text(encoding="utf-8")
        self.assertIn("if not bpy.app.background:", cycles, "if Cycles registers it in the background too, say so")


class ChildCache(unittest.TestCase):
    """A child render resolves the kernel cache its parent does, in every launch environment: Cycles reads it from
    $XDG_CACHE_HOME, else $HOME/.cache (``path_xdg_cache_get``), once per process. Evidence (1.0.11 GUI round on
    116c309, 2026-11-11): the child loaded the archives its parent wrote in the same folder (it marked them used)."""

    def spawned_environment(self, environ):
        seen = {}

        class Process:
            stdout = iter(())

            def poll(self):
                return 0

        def popen(arguments, **kwargs):
            seen.update(kwargs.get("env") or {})
            return Process()

        with mock.patch.dict(os.environ, environ, clear=True):
            child = job_render_child.Child(["Angelo"], owner=None, deadline=None, popen=popen,
                                           footprint=lambda *a: 0, memory_low=lambda *a: False, total_mb=1)
        child.reader.join(60)   # Completion waits, capped only against a hang (host_waits.CAP_S, where it exists).
        child.watcher.join(60)
        job_render_child._running.discard(child)
        return seen

    def test_a_child_gets_the_variables_cycles_resolves_its_kernel_cache_from(self):
        for environ in ({"HOME": "/Users/a", "XDG_CACHE_HOME": "/Volumes/x/cache", "CFFIXED_USER_HOME": "/Users/a"},
                        {"HOME": "/Users/b"}, {}):
            with self.subTest(environ=environ):
                seen = self.spawned_environment(environ)
                for name in ("HOME", "XDG_CACHE_HOME", "CFFIXED_USER_HOME"):
                    self.assertEqual(seen.get(name), environ.get(name), name)

    def test_nothing_shipped_rewrites_them_in_its_own_process(self):
        # The parent's Cycles keeps the path it read first; a later rewrite would reach only the child.
        names = r"(HOME|XDG_CACHE_HOME|CFFIXED_USER_HOME)"
        writes = re.compile(r"os\.environ\[[\"']%s[\"']\]\s*=|os\.putenv\(\s*[\"']%s|"
                            r"os\.environ\.(update|setdefault|pop)\([^)]*\b%s\b" % (names, names, names))
        found = []
        for folder in ("scripts/addons_core/angelo_agent", "scripts/modules", "scripts/startup"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if writes.search(line):
                        found.append("%s:%d" % (path.relative_to(ROOT), number))
        self.assertEqual(found, [])


class Child(unittest.TestCase):
    def test_the_child_reports_its_compile_and_its_watchdog_moves_with_it(self):
        clock = Clock()
        child = bare_child(clock)
        child._line(job_render_child.MARK + '{"kernels": [true, 0.0, 120.0]}')
        self.assertEqual(child.kernels(), (True, 120.0))
        child.credit(120.0)
        self.assertEqual(child.deadline, 620.0)
        child.credit(5000.0)
        self.assertEqual(child.deadline, 500.0 + kernels.CAP_S)
        child._line(job_render_child.MARK + '{"kernels": [false, 134.0, 0.0]}')
        clock.now += 100
        self.assertEqual(child.kernels(), (False, 134.0), "an ended compile does not grow")


if __name__ == "__main__":
    unittest.main()
