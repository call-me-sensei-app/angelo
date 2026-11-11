# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Heavy Cycles render jobs in a child Angelo (job_render_route, job_render_child). No Blender needed.

The child's render itself (a copy of the scene, Cycles on the GPU, the frames in staging) is proven by
``test_blender_render_child`` on a build. These tests hold the contract around it with real child processes (this
Python standing in for Angelo): which renders go there, that a stop, the time budget and memory end the process from
the watchdog's own thread whatever the main thread is doing, that a stop never reaches Blender's render job in this
application (which may be the person's F12), how each ending is recorded, and the memory projection.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import job_deadline, job_render_child, job_render_route  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
from angelo_agent.job_manager import JobManager  # noqa: E402
from angelo_agent.job_store import JobStore  # noqa: E402

MARK = job_render_child.MARK


def wait_until(condition, seconds=20.0):
    """Poll ``condition`` until it holds; False if it never did (a hang fails the test, it never blocks the suite)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def child_script(*lines, ignore_term=False, then_sleep=60):
    """A stand-in render child: prints ``lines`` (with a flush), then sleeps until it is ended."""
    body = ["import signal, sys, time"]
    if ignore_term:
        body.append("signal.signal(signal.SIGTERM, signal.SIG_IGN)")
    body += ["print(%r, flush=True)" % line for line in lines]
    body.append("time.sleep(%r)" % then_sleep)
    return [sys.executable, "-c", "\n".join(body)]


class Route(unittest.TestCase):
    def runner(self, kind="RENDER_STILL", engine="CYCLES", estimate=None, size=(1920, 1080), samples=128,
               frames=(1, 1), extensions=False, device="GPU (Metal: Apple M4 Pro)", **request):
        facts = {"engine": engine, "resolution": list(size), "samples": samples, "frames": list(frames),
                 "device": device}
        if estimate is not None:
            facts["estimate_s"] = estimate
        return SimpleNamespace(request=dict(kind=kind, **request), facts=facts, scene=None,
                               extensions=SimpleNamespace(any=extensions))

    def route(self, runner, local_state=(), env=None):
        with patch.dict(os.environ, {"ANGELO_RENDER_PROCESS": env} if env else {}, clear=False):
            if not env:
                os.environ.pop("ANGELO_RENDER_PROCESS", None)
            return job_render_route.route(runner, local_state=list(local_state))

    def test_heavy_cycles_stills_and_animations_go_to_a_child(self):
        for kind in ("RENDER_STILL", "RENDER_ANIMATION"):
            with self.subTest(kind=kind):
                self.assertIsNone(self.route(self.runner(kind, estimate=job_render_route.MIN_ESTIMATE_S)))

    def test_looks_regions_other_engines_and_engine_overrides_stay_here(self):
        for runner in (self.runner("RENDER_PREVIEW", estimate=600), self.runner("RENDER_REGION", estimate=600),
                       self.runner(engine="BLENDER_EEVEE", estimate=600),
                       self.runner(engine="BLENDER_WORKBENCH", estimate=600),
                       SimpleNamespace(request={"kind": "RENDER_STILL", "engine": "BLENDER_EEVEE"},
                                       facts={"engine": "CYCLES", "estimate_s": 600}, scene=None, extensions=None)):
            with self.subTest(kind=runner.request["kind"], facts=runner.facts):
                self.assertIsNotNone(self.route(runner))

    def test_the_estimate_decides_and_without_one_the_work(self):
        self.assertIn("under", self.route(self.runner(estimate=job_render_route.MIN_ESTIMATE_S - 1)))
        # Without an estimate: megapixel-samples x frames against MIN_WORK.
        light = self.runner(size=(640, 360), samples=16)
        heavy = self.runner(size=(3840, 2160), samples=1024)
        self.assertIn("no estimate", self.route(light))
        self.assertIsNone(self.route(heavy))
        animation = self.runner("RENDER_ANIMATION", size=(640, 360), samples=64, frames=(1, 500))
        self.assertIsNone(self.route(animation))   # 0.23 MP x 64 x 500 frames

    def test_what_a_copy_cannot_reproduce_stays_here_and_says_why(self):
        heavy = dict(estimate=600)
        self.assertIn("add-on or script Python", self.route(self.runner(extensions=True, **heavy)))
        self.assertIn("image pixels not saved: Painted",
                      self.route(self.runner(**heavy), local_state=["image pixels not saved: Painted"]))
        # Even when asked for: the copy would render another picture.
        self.assertIsNotNone(self.route(self.runner(extensions=True, **heavy), env="child"))
        self.assertIsNotNone(self.route(self.runner(**heavy), local_state=["cache of Cloth"], env="child"))

    def test_only_a_backend_measured_to_hold_up_the_interface_goes_to_a_child(self):
        self.assertIsNone(self.route(self.runner(estimate=600, device="GPU (Metal: Apple M4 Pro) + CPU")))
        for device in ("METAL", "METAL+CPU"):   # As Cycles' --cycles-device names it (review round 2, F6).
            self.assertIsNone(self.route(self.runner(estimate=600, device=device)))
        for device in ("GPU (OptiX: Tesla T4)", "GPU (CUDA: Tesla T4)", "GPU (HIP: Radeon)", "CPU", None):
            with self.subTest(device=device):
                self.assertIn("measured no interface stall", self.route(self.runner(estimate=600, device=device)))
                self.assertIsNone(self.route(self.runner(estimate=600, device=device), env="child"))

    def test_the_environment_switch_decides_either_way(self):
        self.assertIsNone(self.route(self.runner(estimate=1), env="child"))
        self.assertIn("ANGELO_RENDER_PROCESS", self.route(self.runner(estimate=600), env="app"))


def cache(baked=False, disk=False, external=False):
    return SimpleNamespace(is_baked=baked, use_disk_cache=disk, use_external=external)


def obj(name, modifiers=(), particles=()):
    return SimpleNamespace(name=name, modifiers=list(modifiers), particle_systems=list(particles))


class Tree:
    """A node tree: hashable by identity, as Blender's ID wrappers are."""

    def __init__(self, nodes):
        self.nodes = nodes


def nodes_tree(*idnames):
    return Tree([SimpleNamespace(bl_idname=idname, node_tree=None) for idname in idnames])


class CopyFidelity(unittest.TestCase):
    """What a copy of the scene would render differently stays in the application (break-it review F3)."""

    def scene(self, objects=(), world=None, background=None, name="S"):
        return SimpleNamespace(name=name, objects=list(objects), rigidbody_world=world, background_set=background)

    def test_simulation_state_the_copy_does_not_carry(self):
        group = nodes_tree("GeometryNodeSetPosition")
        nested = nodes_tree("GeometryNodeBake")
        outer = Tree([SimpleNamespace(bl_idname="GeometryNodeGroup", node_tree=nested)])
        cases = {
            "baked into the file": (obj("Cloth", [SimpleNamespace(type="CLOTH", point_cache=cache(baked=True))]), False),
            "external": (obj("Soft", [SimpleNamespace(type="SOFT_BODY", point_cache=cache(external=True))]), False),
            "a disk cache named after the .blend": (obj("Cloth", [SimpleNamespace(
                type="CLOTH", point_cache=cache(baked=True, disk=True))]), True),
            "not baked": (obj("Cloth", [SimpleNamespace(type="CLOTH", point_cache=cache())]), True),
            "plain geometry nodes": (obj("GN", [SimpleNamespace(type="NODES", node_group=group)]), False),
            "a nested bake node": (obj("GN", [SimpleNamespace(type="NODES", node_group=outer)]), True),
            "a fluid domain": (obj("Smoke", [SimpleNamespace(type="FLUID", fluid_type="DOMAIN")]), True),
            "hair without dynamics": (obj("Hair", particles=[SimpleNamespace(
                settings=SimpleNamespace(type="HAIR"), use_hair_dynamics=False, point_cache=cache())]), False),
            "emitted particles not baked": (obj("Sparks", particles=[SimpleNamespace(
                settings=SimpleNamespace(type="EMITTER"), use_hair_dynamics=False, point_cache=cache())]), True),
        }
        for label, (item, lost) in cases.items():
            with self.subTest(label):
                self.assertEqual(bool(job_render_route._caches(self.scene([item]))), lost)
        world = SimpleNamespace(enabled=True, point_cache=cache())
        self.assertEqual(job_render_route._caches(self.scene(world=world)), ["rigid body cache"])

    def test_every_scene_the_render_evaluates_is_looked_at(self):
        background = self.scene([obj("Cloth", [SimpleNamespace(type="CLOTH", point_cache=cache())])], name="Set")
        strip = self.scene(name="Strip", background=background)
        main = self.scene(name="Main")
        class Material:
            pass

        class Brush:
            pass

        class Image:   # Hashable by identity, as Blender's ID wrappers are.
            def __init__(self, **fields):
                self.__dict__.update(fields)
        painted = Image(name="Painted", is_dirty=True, users=1, type="UV_TEST")   # A generated image painted.
        stroke = Image(name="Stroke", is_dirty=True, users=1, type="IMAGE")       # Only a brush uses it.
        viewer = Image(name="Viewer Node", is_dirty=True, users=1, type="COMPOSITING")
        result = Image(name="Render Result", is_dirty=True, users=0, type="RENDER_RESULT")
        users = {id(painted): [Material()], id(stroke): [Brush()], id(viewer): [Material()], id(result): []}
        bpy = SimpleNamespace(data=SimpleNamespace(
            images=[painted, stroke, viewer, result, Image(name="Saved", is_dirty=False, users=1, type="IMAGE")],
            user_map=lambda subset: {image: users[id(image)] for image in subset}))
        from angelo_agent import render_drivers, render_reach
        with patch.dict(sys.modules, {"bpy": bpy}), \
                patch.object(render_reach, "rendered_scenes", return_value=[main, strip]), \
                patch.object(render_drivers, "python_drivers", return_value=[(1, "Cube: location")]):
            found = job_render_route.process_state(main)
        self.assertEqual(found, ["image pixels not saved: Painted", "a render result used as a texture: Viewer Node",
                                 "driver Python: Cube: location", "cache of Cloth in Set"])

    def test_a_geometry_nodes_import_reads_its_file_beside_the_copy(self):
        tree = nodes_tree("GeometryNodeImportOBJ")
        self.assertTrue(job_render_route._caches(self.scene([obj("Imported", [SimpleNamespace(type="NODES",
                                                                                                node_group=tree)])])))


class Memory(unittest.TestCase):
    def setUp(self):
        job_render_route._peaks.clear()
        self.addCleanup(job_render_route._peaks.clear)

    def finished(self, peak, size, uid="u1", device="GPU (Metal: M4)", status="done"):
        job_render_route.observe({"status": status, "detail": {"engine": "CYCLES", "device": device, "scene_uid": uid,
                                                               "peak_memory_mb": peak, "resolution": list(size)}})

    def facts(self, size, device="GPU (Metal: M4)"):
        return {"engine": "CYCLES", "device": device, "resolution": list(size)}

    def test_only_a_finished_render_of_the_same_scene_and_device_projects(self):
        self.finished(9000, (1280, 720), status="cancelled")
        self.assertIsNone(job_render_route.projected_mb(self.facts((1280, 720)), "u1"))
        self.finished(9000, (1280, 720))
        self.assertIsNone(job_render_route.projected_mb(self.facts((1280, 720)), "u2"))
        self.assertIsNone(job_render_route.projected_mb(self.facts((1280, 720), device="CPU"), "u1"))
        self.assertEqual(job_render_route.projected_mb(self.facts((1280, 720)), "u1")[0], 9000)

    def test_memory_follows_pixels_up_to_cycless_tile(self):
        self.finished(9000, (1280, 720))
        grown = job_render_route.projected_mb(self.facts((1920, 1080)), "u1")[0]
        tiled = job_render_route.projected_mb(self.facts((7680, 4320)), "u1")[0]
        capped = job_render_route.projected_mb(self.facts((4096, 4096)), "u1")[0]
        self.assertGreater(grown, 9000)
        self.assertEqual(tiled, capped)   # Past one tile the film is rendered tile by tile.
        self.assertLess(job_render_route.projected_mb(self.facts((320, 180)), "u1")[0], 9000)

    def test_a_projection_is_a_local_fact_and_never_a_refusal(self):
        """Break-it review round 2, F1-F2: memory follows the scene's geometry (16.5 MiB per megapixel up to the tile,
        nothing per sample), so a projection from the last render could only refuse at the margin, renders that fit."""
        self.assertFalse(hasattr(job_render_route, "admit_memory"))
        self.finished(44000, (1920, 1080))
        projected, peak, then = job_render_route.projected_mb(self.facts((3840, 2160)), "u1")
        self.assertEqual((peak, then), (44000, 1920 * 1080))
        self.assertLessEqual(projected - peak, 64)   # At most one tile's film: 2048 x 2048 x 16 bytes.

    def test_the_readers_read_this_process(self):
        self.assertGreater(job_render_route.physical_mb() or 0, 256)
        low, free = job_render_route.memory_low(job_render_route.physical_mb(), 2048)
        self.assertIsInstance(low, bool)
        self.assertTrue(free is None or 0 <= free <= job_render_route.physical_mb())
        own = job_render_route.footprint_mb(os.getpid())
        self.assertTrue(own is None or own > 0)
        self.assertEqual(job_render_route.reserve_mb(16384), 2048)
        self.assertEqual(job_render_route.reserve_mb(131072), 13107)


class StatusLines(unittest.TestCase):
    def test_progress_from_blenders_status_line(self):
        self.assertAlmostEqual(job_render_child.status_fraction(
            "Fra:1 Mem:15.33M (Peak 15.81M) | Time:00:00.21 | Mem:0.00M, Peak:0.00M | Scene, ViewLayer | Sample 3/12"), 0.25)
        self.assertAlmostEqual(job_render_child.status_fraction("Fra:1 | Rendered 1/4 Tiles, Sample 64/128"), 0.375)
        self.assertIsNone(job_render_child.status_fraction("Fra:1 Mem:15M | Synchronizing object | Cube"))
        self.assertIsNone(job_render_child.status_fraction("Sample 1/0"))

    def test_the_child_loads_the_copy_first_and_runs_isolated(self):
        arguments = job_render_child.command("/Angelo", "/private/render.blend", {"scene": "S"}, "CUDA")
        self.assertEqual(arguments[:6], ["/Angelo", "--background", "--factory-startup", "--disable-autoexec",
                                         "--offline-mode", "--python-exit-code"])
        self.assertLess(arguments.index("/private/render.blend"), arguments.index("--python"))
        self.assertEqual(json.loads(arguments[arguments.index("--") + 1]), {"scene": "S"})
        self.assertEqual(arguments[-2:], ["--cycles-device", "CUDA"])   # Cycles reads it after "--".
        self.assertNotIn("--cycles-device", job_render_child.command("/A", "/c.blend", {}))


class Watchdog(unittest.TestCase):
    def child(self, arguments, **kwargs):
        child = job_render_child.Child(arguments, "j9", **kwargs)
        self.addCleanup(lambda: (child.kill_now(), child.close()))
        return child

    def test_its_lines_become_progress_memory_device_and_the_end(self):
        child = self.child(child_script(
            "Fra:1 Mem:15M | Sample 6/12",
            MARK + json.dumps({"device": [{"device": "GPU (Metal: M4)"}, {"gpu": True}]}),
            MARK + json.dumps({"mem_peak_mb": 812, "frame": 1}), MARK + json.dumps({"mem_peak_mb": 640}),
            MARK + json.dumps({"done": True}), then_sleep=0), total_mb=0)
        self.assertTrue(wait_until(lambda: not child.running()))
        outcome = child.outcome()
        self.assertEqual((outcome["code"], outcome["why"], outcome["done"]), (0, None, {"done": True}))
        self.assertEqual((child.state(), child.peak_mb), (0.5, 812))   # The peak never goes down.
        self.assertEqual(child.device, ({"device": "GPU (Metal: M4)"}, {"gpu": True}))

    def test_a_stop_returns_at_once_and_ends_the_process(self):
        child = self.child(child_script("Fra:1 | Sample 1/100"), total_mb=0)
        child.stop()
        self.assertTrue(wait_until(lambda: not child.running()))
        self.assertEqual(child.reason(), "cancel")

    @unittest.skipIf(os.name == "nt", environment_skip(
        "a child cannot ignore the stop on Windows: terminate is TerminateProcess (its Job Object), not a signal"))
    def test_a_child_that_ignores_the_stop_is_killed_from_the_watchdogs_thread(self):
        ready = tempfile.NamedTemporaryFile(delete=False)
        ready.close()
        self.addCleanup(os.unlink, ready.name)
        arguments = [sys.executable, "-c", "import pathlib, signal, sys, time\n"
                     "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                     "pathlib.Path(sys.argv[1]).write_text('up')\ntime.sleep(60)\n", ready.name]
        with patch.object(job_render_child, "STOP_SECONDS", 0.3):
            child = self.child(arguments, total_mb=0)
            self.assertTrue(wait_until(lambda: Path(ready.name).read_text() == "up"))
            child.stop()
            self.assertTrue(wait_until(lambda: not child.running()))   # No main-thread call after the stop.
        self.assertTrue(child.killed)

    def test_the_budget_is_enforced_while_the_main_thread_is_held(self):
        child = self.child(child_script("Fra:1 | Sample 1/100"), deadline=time.monotonic() + 0.3, total_mb=0)
        time.sleep(0.6)   # The main thread does nothing for the child: as a GPU-paced interface would not.
        self.assertTrue(wait_until(lambda: not child.running()))
        self.assertEqual(child.reason(), "time_budget")

    def test_the_system_running_out_of_memory_ends_a_large_child(self):
        state = {"low": False, "child": 9000}
        child = self.child(child_script("Fra:1 | Sample 1/100"), total_mb=16384, footprint=lambda pid: state["child"],
                           memory_low=lambda total, reserve: (state["low"], 1500))
        time.sleep(0.2)
        self.assertTrue(child.running())   # The system is not low on memory yet.
        state["low"] = True
        self.assertTrue(wait_until(lambda: not child.running()))
        self.assertEqual(child.reason(), "memory")
        self.assertEqual(child.outcome()["used"], (9000, 1500, 2048))

    def test_a_small_child_is_never_blamed_for_the_systems_memory(self):
        child = self.child(child_script("Fra:1 | Sample 1/100"), total_mb=16384,
                           footprint=lambda pid: 900, memory_low=lambda total, reserve: (True, 500))   # Another app's.
        time.sleep(0.4)
        self.assertTrue(child.running())
        self.assertIsNone(child.reason())

    def test_a_render_that_reported_its_end_is_never_stopped_by_its_budget(self):
        """Break-it review F4: Blender's teardown after the render's end may outlast the budget."""
        child = self.child(child_script(MARK + json.dumps({"done": True})), deadline=time.monotonic() + 0.3,
                           total_mb=0)
        self.assertTrue(wait_until(child.finished))
        time.sleep(0.6)
        self.assertTrue(child.running())
        self.assertIsNone(child.reason())

    def test_memory_never_ends_a_render_that_reported_its_end(self):
        pressure = {"low": False}   # Raised once the child said its render ended: only Blender's teardown is left.
        child = self.child(child_script(MARK + json.dumps({"done": True})), total_mb=16384, footprint=lambda pid: 9000,
                           memory_low=lambda total, reserve: (pressure["low"], 100))
        self.assertTrue(wait_until(child.finished))
        pressure["low"] = True
        time.sleep(0.6)
        self.assertTrue(child.running())
        self.assertIsNone(child.reason())

    def test_the_first_reason_stays(self):
        child = self.child(child_script(), total_mb=0)
        child.stop("time_budget")
        child.stop("cancel")
        self.assertEqual(child.reason(), "time_budget")

    def test_close_removes_the_copy_only_once_the_child_has_ended_and_quit_ends_every_child(self):
        folder = Path(tempfile.mkdtemp(prefix="angelo-render-test-"))
        (folder / "render.blend").write_bytes(b"BLENDER")
        child = self.child(child_script(), folder=folder, total_mb=0)
        self.assertTrue(job_render_child.writing("j9"))
        self.assertFalse(child.close())
        self.assertTrue(folder.exists())
        job_render_child._end_all()   # What atexit runs: no bpy, nothing left running.
        self.assertFalse(child.running())
        self.assertFalse(folder.exists())
        self.assertFalse(job_render_child.writing("j9"))


class Sweep(unittest.TestCase):
    def test_only_old_copies_are_removed(self):
        with tempfile.TemporaryDirectory() as root:
            old, new, other = (Path(root) / name for name in ("angelo-render-old", "angelo-render-new", "keep-me"))
            for folder in (old, new, other):
                folder.mkdir()
                (folder / "render.blend").write_bytes(b"BLENDER")
            past = time.time() - job_render_child.STALE_SECONDS - 60
            os.utime(old, (past, past))
            os.utime(other, (past, past))
            job_render_child.sweep(root)
            self.assertEqual(sorted(path.name for path in Path(root).iterdir()), ["angelo-render-new", "keep-me"])


class FakeChild:
    def __init__(self, why=None, code=0, used=None, alive=False, done=False):
        self.why, self.code, self.used, self.alive, self.done = why, code, used, alive, done
        self.total_mb, self.peak_mb, self.device, self.stopped, self.closed = 16384, 900, None, [], False

    def running(self):
        return self.alive

    def reason(self):
        return self.why

    def finished(self):
        return self.done

    def state(self):
        return 0.4

    def stop(self, why="cancel"):
        self.stopped.append(why)

    def kill_now(self, wait=None):
        self.alive = False

    def outcome(self):
        return {"code": self.code, "why": self.why, "used": self.used, "done": {"done": True} if self.done else None,
                "tail": ["line"]}

    def close(self):
        self.closed = True
        return True


class Base:
    """Stands in for EncodePhase, KeepFrames and Runner behind ``ChildPhase``."""

    def __init__(self):
        self.calls, self.encoder, self.stopping, self.identity = [], None, False, None

    def stop(self):
        self.calls.append("native stop")

    def abandon(self):
        self.calls.append("abandon")

    def keep_frames(self):
        self.calls.append("keep")
        return True

    def finish(self, stopped):
        self.calls.append(("finish", stopped))
        return ("cancelled", "", {"note": "kept"}) if stopped else ("failed", "Angelo ended the render early", {})


class Job(job_render_child.ChildPhase, Base):
    pass


class Phase(unittest.TestCase):
    def test_a_stop_ends_the_child_and_never_blenders_render_job_here(self):
        job = Job()
        job.child = FakeChild(alive=True)
        job.stop()
        self.assertEqual((job.child.stopped, job.calls, job.stopping), (["cancel"], [], True))
        job.child.alive = False
        job.stop()   # Ended, nothing encoding: still never Blender's job (it may be the person's F12).
        self.assertEqual(job.calls, [])
        plain = Job()
        plain.stop()
        self.assertEqual(plain.calls, ["native stop"])

    def test_a_closing_document_kills_the_child_before_keeping_its_frames(self):
        job = Job()
        job.child = FakeChild(alive=True)
        self.assertTrue(job.keep_frames())
        self.assertFalse(job.child.alive)
        self.assertTrue(job.child.closed)
        job.abandon()
        self.assertEqual(job.calls, ["keep", "abandon"])

    def test_memory_is_a_failure_that_keeps_what_finished_and_says_why(self):
        job = Job()
        job.child = FakeChild(why="memory", code=-15, used=(13000, 1500, 2048))
        status, reason, receipt = job.finish(False)
        self.assertEqual((status, receipt), ("failed", {}))   # The early end's own receipt (frames kept, or none).
        self.assertIn("13,000 MB", reason)
        self.assertEqual(job.calls, [("finish", False)])
        job.child = FakeChild(why="time_budget", done=True)
        self.assertFalse(job.budget_fired)   # It reported its end first: its output is kept.
        done = Job()
        done.child = FakeChild(why="memory", done=True)   # The end raced the stop: a render that finished.
        self.assertEqual(done.finish(False)[1], "Angelo ended the render early")
        self.assertEqual(done.calls, [("finish", False)])

    def test_a_crash_says_so_and_its_lines_stay_local(self):
        record = {"detail": {}}
        job = Job()
        job.child = FakeChild(code=-11)
        with patch.object(job_render_child, "_record", return_value=record):
            status, reason, _receipt = job.finish(False)
        self.assertEqual(status, "failed")
        self.assertIn("exit status -11; it crashed", reason)
        self.assertIn("often for memory", job_render_child.crashed(-9))
        self.assertIn("0xC0000005", job_render_child.crashed(0xC0000005))
        self.assertEqual(job_render_child.crashed(7), "The render's process ended unexpectedly (exit status 7)")
        self.assertEqual(record["detail"]["process_tail"], ["line"])

    def test_the_real_render_runner_reaches_the_childs_ending(self):
        """Break-it review F1: ``RenderRunner._finish`` shadowed the phase's, so the copy leaked and no ending was
        said. Through the real class order: the child is ended, closed and its memory stop said."""
        from test_native_job_recovery import render_runner
        runner, stand_ins = render_runner(Path("/r"), None, None, [], (1, 4))
        runner.child, seen = FakeChild(why="memory", used=(13000, 1500, 2048), alive=True), []
        with patch.dict(sys.modules, stand_ins):
            from angelo_agent import job_runners
            with patch.object(job_runners.RenderRunner, "_finish",
                              lambda self, stopped: seen.append(stopped) or ("failed", "early", {"kept": 2})):
                status, reason, receipt = runner.finish(False)
        self.assertEqual((status, receipt, seen), ("failed", {"kept": 2}, [False]))   # Finished as an early end.
        self.assertIn("13,000 MB", reason)
        self.assertTrue(runner.child.closed and not runner.child.alive)   # Ended and its copy removed.

    def test_the_budget_is_said_only_once_the_child_ended_by_it(self):
        job = Job()
        job.child = FakeChild(why="time_budget", alive=True)
        self.assertFalse(job.budget_fired)
        job.child.alive = False
        self.assertTrue(job.budget_fired)
        self.assertFalse(Job().budget_fired)

    def test_progress_marks_a_stopped_child_so_no_video_is_made_after_it(self):
        runner = SimpleNamespace(child=FakeChild(alive=True), stopping=False)
        self.assertEqual(job_render_child.progress(runner), 0.4)
        runner.child.alive, runner.child.why = False, "memory"
        self.assertIsNone(job_render_child.progress(runner))
        self.assertTrue(runner.stopping)


class Begin(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(job_render_child, "sweep")   # Never the real scratch folder.
        patcher.start()
        self.addCleanup(patcher.stop)

    def runner(self):
        restored = []
        runner = SimpleNamespace(identity="j3", scene=SimpleNamespace(name="Shot"), window=None, start_frame=1,
                                 end_frame=1, step=1, child=None, restored=restored,
                                 restore_now=lambda: restored.append(True))
        return runner

    def test_a_routed_render_starts_on_a_copy_and_gives_the_person_their_settings_back(self):
        runner, record = self.runner(), {"detail": {job_deadline.ARMED: True}, "started": time.time(),
                                         "request": {"minutes": 30}}
        folder = Path(tempfile.mkdtemp(prefix="angelo-render-test-"))
        started = []
        with patch.object(job_render_route, "route", return_value=None), \
                patch.object(job_render_child, "_record", return_value=record), \
                patch("angelo_agent.private_roots.allocate", return_value=folder), \
                patch.object(job_render_child, "write_snapshot", return_value=folder / "render.blend"), \
                patch.object(job_render_child, "devices", return_value={"type": "METAL", "ids": ["m"]}), \
                patch.object(job_render_child, "Child", side_effect=lambda *a, **k: started.append((a, k)) or "child"), \
                patch.object(job_deadline, "disarm") as disarm:
            self.assertTrue(job_render_child.begin(runner, binary="/Angelo"))
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        arguments, options = started[0]
        self.assertEqual(json.loads(arguments[0][arguments[0].index("--") + 1])["devices"], {"type": "METAL", "ids": ["m"]})
        self.assertGreater(arguments[2], time.monotonic())   # The budget's end, on the watchdog's clock.
        self.assertEqual(options["folder"], folder)
        self.assertEqual((runner.child, runner.restored, record["detail"]["process"]), ("child", [True], "child"))
        self.assertNotIn(job_deadline.ARMED, record["detail"])   # The person's F12 never reads this job's deadline.
        disarm.assert_called_once()

    def test_a_copy_that_cannot_be_written_renders_here_and_says_why(self):
        runner, record = self.runner(), {"detail": {}, "started": time.time(), "request": {"minutes": 30}}
        folder = Path(tempfile.mkdtemp(prefix="angelo-render-test-"))
        with patch.object(job_render_route, "route", return_value=None), \
                patch.object(job_render_child, "_record", return_value=record), \
                patch("angelo_agent.private_roots.allocate", return_value=folder), \
                patch.object(job_render_child, "write_snapshot", side_effect=OSError("disk full")):
            self.assertFalse(job_render_child.begin(runner, binary="/Angelo"))
        self.assertEqual((record["detail"]["process"], runner.restored), ("app", []))
        self.assertIn("OSError", record["detail"]["process_why"])
        self.assertFalse(folder.exists())

    def test_a_render_routed_here_records_why(self):
        runner, record = self.runner(), {"detail": {}}
        with patch.object(job_render_route, "route", return_value="its estimate (4 s) is under 20 s"), \
                patch.object(job_render_child, "_record", return_value=record):
            self.assertFalse(job_render_child.begin(runner))
        self.assertEqual(record["detail"], {"process": "app", "process_why": "its estimate (4 s) is under 20 s"})


class ManagerBudget(unittest.TestCase):
    def test_a_child_ended_by_its_budget_is_recorded_as_a_budget_stop(self):
        class Runner:
            identity, staging, published, budget_fired = None, None, None, True

            def describe(self):
                return {"engine": "CYCLES"}

            def start(self):
                pass

            def poll(self):
                return None   # The watchdog ended the child.

            def finish(self, stopped):
                return ("cancelled", "", {}) if stopped else ("failed", "Angelo ended the render early", {})

        with tempfile.TemporaryDirectory() as folder:
            jobs = JobManager(JobStore(Path(folder)))
            record = jobs.submit("s", "c", {"kind": "RENDER_STILL", "minutes": 30, "queue": False}, Runner())
            jobs.poll()
        self.assertEqual((record["status"], record["reason"]), ("cancelled", "time_budget"))


if __name__ == "__main__":
    unittest.main()
