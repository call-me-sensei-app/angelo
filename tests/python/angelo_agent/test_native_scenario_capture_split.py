# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The split ray-cast capture (``scenario_capture_split``) on the host: plan, deal, assemble, workers, no Blender.

The split must give exactly what one ``scenario_capture.capture`` process
gives. These tests run the real ``capture()`` against a deterministic fake
``Scene.ray_cast``, once serially and once band by band as the workers do
(``scenario_capture_worker.capture_band``, through JSON). They pin that the
bands cover every pixel once, that the rebuilt capture equals the serial one
bit for bit in any part order, that the archive ``save_capture`` writes is
byte-identical, that a failed or late worker stops the whole capture, and
that ``run_job`` routes only the capture jobs through the boot.
``capture_split_proof.py`` proves the same on real scenes in stock Blender.
Also here: the scorecard captures a one-shot scene with ``oneshot_job.py``
(never ``scenario_job.py``), and a failed capture is not relaunched over its
own log.
"""

import json
import random
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import numpy
except ImportError:  # CI installs NumPy (run_host_tests --max-skips 0); Blender bundles it.
    numpy = None

import scenario_capture as capture  # noqa: E402
import scenario_capture_split as split  # noqa: E402
import scenario_capture_worker as worker_module  # noqa: E402
from test_native_scenario_checks import FAKE_MATHUTILS  # noqa: E402

INF = float("inf")


class FakeScene:
    """``Scene.ray_cast`` as a pure function of the ray: misses, several objects, awkward depths."""

    NAMES = ("Wall", "Roof.001", "Tower", "Ground", "Ünïcode name", "Lamp")

    def ray_cast(self, depsgraph, origin, direction, distance=1.0e4):
        key = int(abs(direction.v[0]) * 1e6) * 31 + int(abs(direction.v[2]) * 1e6) * 17 + int(abs(origin.v[0]) * 1e3)
        if key % 5 == 0:
            return False, None, None, -1, None, None
        location = origin + direction * (1.0 / 3.0 + (key % 997) * 0.731)
        name = self.NAMES[key % len(self.NAMES)]
        return True, location, None, 0, NS(original=NS(name=name)), None


def fake_bpy():
    bpy = mock.Mock()
    parents = {"Roof.001": "Tower", "Lamp": "Ground"}

    def get(name):
        return NS(parent=NS(name=parents[name]) if name in parents else None)
    bpy.data.objects.get.side_effect = get
    bpy.data.objects.__getitem__ = lambda self, name: get(name)
    return bpy


CAMERAS = {"hero": {"location": (14.0, -18.0, 12.0), "target": (0.0, 0.0, 1.0), "lens": 35.0},
           "top": {"location": (0.0, 0.0, 30.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 26.0},
           "low": {"location": (-12.0, -3.0, 1.5), "target": (4.0, 0.0, 1.0), "lens": 24.0}}


def split_like_the_workers(bpy, scene, cameras, resolution, workers, shuffle=None):
    """What ``capture_split`` returns, with the bands computed in this process as the workers would, through JSON.

    Workers claim bands in queue order; here band ``i`` goes to worker ``i % workers`` (any split must do).
    """
    width, height = resolution
    parts = [{"chunks": []} for _ in range(workers)]
    for index, (view, start, stop) in enumerate(split.plan(list(cameras), height, workers)):
        data = worker_module.capture_band(bpy, scene, view, json.loads(json.dumps(cameras[view])), list(resolution),
                                          start, stop)
        parts[index % workers]["chunks"].append([view, start, stop, data])
    parts = [json.loads(json.dumps(part)) for part in parts]
    if shuffle is not None:
        shuffle.shuffle(parts)
        for part in parts:
            shuffle.shuffle(part["chunks"])
    traced, parents = split.assemble(list(cameras), width, height, parts)
    names, maps = split.index_hits(traced)
    return {"resolution": [width, height], "names": names, "views": maps,
            "parents": {name: parents[name] for name in names}}, parts


def bits(values):
    return [struct.pack("<d", value) for value in values]


class PlanTests(unittest.TestCase):
    def test_capture_parent_keeps_only_a_bounded_qa_process_owner(self):
        import os
        import scenario_references as references
        for owner in ("a" * 32, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as folder, \
                    mock.patch.dict(os.environ, {"ANGELO_QA_PROCESS_OWNER": owner}, clear=True):
                env = references.isolated_environment(folder)
            self.assertEqual(env.get("ANGELO_QA_PROCESS_OWNER"), owner if owner == "a" * 32 else None)

    def test_worker_count_is_cores_minus_headroom(self):
        self.assertEqual(split.HEADROOM, 2)
        self.assertEqual(split.worker_count(14), 12)
        self.assertEqual(split.worker_count(18), 16)
        self.assertEqual(split.worker_count(2), 1)
        self.assertEqual(split.worker_count(1), 1)
        self.assertEqual(split.worker_count(None), max(1, ((__import__("os").cpu_count() or 1) - 2)))
        self.assertEqual(split.worker_count(64, chunks=5), 5)   # Never more workers than chunks.

    def test_bands_cover_every_row_of_every_view_once(self):
        for views, height, workers in ((["a"], 1, 1), (["a", "b", "c"], 216, 12), (list("abcdefghijklm"), 216, 16),
                                       (["a", "b"], 7, 40), (list("abcdefghijklm"), 216, 1)):
            chunks = split.plan(views, height, workers)
            for view in views:
                rows = [row for name, start, stop in chunks if name == view for row in range(start, stop)]
                self.assertEqual(rows, list(range(height)), (views, height, workers))
            self.assertTrue(all(start < stop for _, start, stop in chunks))

    def test_each_band_is_claimed_by_one_worker_only(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(worker_module.claim(folder, 3))
            self.assertFalse(worker_module.claim(folder, 3))
            self.assertTrue(worker_module.claim(folder, 4))


class SplitEqualsSerialTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {"mathutils": FAKE_MATHUTILS})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bpy, self.scene, self.resolution = fake_bpy(), FakeScene(), (17, 11)
        self.serial = capture.capture(self.bpy, self.scene, CAMERAS, self.resolution)

    def test_split_rebuilds_the_serial_capture_exactly(self):
        hits = sum(number >= 0 for maps in self.serial["views"].values() for number in maps["ids"])
        self.assertTrue(0 < hits < 3 * 17 * 11 and len(self.serial["names"]) == len(FakeScene.NAMES))
        for workers in (1, 2, 5, 12, 200):
            data, _ = split_like_the_workers(self.bpy, self.scene, CAMERAS, self.resolution, workers,
                                             random.Random(workers))
            self.assertEqual(list(data["views"]), list(CAMERAS))
            self.assertEqual(data["names"], self.serial["names"])
            self.assertEqual(list(data["parents"].items()), list(self.serial["parents"].items()))
            for view in CAMERAS:
                self.assertEqual(data["views"][view]["ids"], self.serial["views"][view]["ids"])
                self.assertEqual(bits(data["views"][view]["depth"]), bits(self.serial["views"][view]["depth"]))

    def test_the_worker_restores_the_full_ray_grid(self):
        before = capture.rays
        worker_module.capture_band(self.bpy, self.scene, "hero", CAMERAS["hero"], self.resolution, 3, 5)
        self.assertIs(capture.rays, before)

    @unittest.skipIf(numpy is None, "NumPy")
    def test_saved_archive_is_byte_identical(self):
        data, _ = split_like_the_workers(self.bpy, self.scene, CAMERAS, self.resolution, 4, random.Random(1))

        def archive(capture_data):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder, "capture.npz")
                capture.save_capture(path, capture_data)
                with zipfile.ZipFile(path) as handle:
                    return {name: handle.read(name) for name in handle.namelist()}
        serial, rebuilt = archive(self.serial), archive(data)
        self.assertEqual(sorted(serial), sorted(rebuilt))
        for name in serial:
            self.assertEqual(serial[name], rebuilt[name], name)

    def test_json_hand_off_keeps_every_float_bit(self):
        values = [INF, 0.1, 1.0 / 3.0, 5e-324, 1.7976931348623157e308, 123.45600000000002, -0.0]
        self.assertEqual(bits(json.loads(json.dumps(values))), bits(values))

    def test_gaps_overlaps_and_bad_sizes_are_refused(self):
        views, (width, height) = list(CAMERAS), self.resolution
        _, parts = split_like_the_workers(self.bpy, self.scene, CAMERAS, self.resolution, 2)
        with self.assertRaisesRegex(ValueError, "missing"):
            split.assemble(views, width, height, parts[:1])
        with self.assertRaisesRegex(ValueError, "twice"):
            split.assemble(views, width, height, parts + parts[:1])
        broken = json.loads(json.dumps(parts))
        view = broken[0]["chunks"][0][0]
        broken[0]["chunks"][0][3]["views"][view]["depth"].pop()
        with self.assertRaisesRegex(ValueError, "depths"):
            split.assemble(views, width, height, broken)
        stray = json.loads(json.dumps(parts))
        stray[0]["chunks"][0][0] = "elsewhere"
        with self.assertRaisesRegex(ValueError, "outside"):
            split.assemble(views, width, height, stray)


class InstallTests(unittest.TestCase):
    def test_install_routes_capture_to_the_split_and_one_worker_runs_the_serial_code(self):
        serial = capture.capture
        self.addCleanup(setattr, capture, "capture", serial)
        with mock.patch.object(split, "capture_split", return_value="split") as routed:
            split.install("/out/.capture-split", workers=3)
            split.install("/out/.capture-split", workers=3)   # Idempotent.
            self.assertIs(capture.capture.serial, serial)
            self.assertEqual(capture.capture("bpy", "scene", {"a": {}}), "split")
        self.assertEqual(routed.call_args.args[3], Path("/out/.capture-split/capture-split-1"))
        self.assertEqual(routed.call_args.args[5], 3)
        with mock.patch.object(capture.capture, "serial", return_value="serial") as fallback:
            self.assertEqual(split.capture_split("bpy", "scene", {"a": {}}, "/unused", (2, 2), workers=1), "serial")
        fallback.assert_called_once()

    def test_boot_hands_the_job_its_own_arguments(self):
        argv = ["blender", "-b", "--python", "boot.py", "--", "/h/oneshot_job.py", "--job", "capture", "--out", "/o"]
        script, job_argv = split.job_arguments(argv)
        self.assertEqual(script, "/h/oneshot_job.py")
        self.assertEqual(job_argv, ["blender", "-b", "--python", "boot.py", "--", "--job", "capture", "--out", "/o"])
        self.assertEqual(split.scratch_of(job_argv[5:]), Path("/o/.capture-split"))
        boot = (HERE / "scenario_capture_boot.py").read_text()
        self.assertLess(boot.index("split.install("), boot.index("runpy.run_path(script, run_name=\"__main__\")"))

    def test_run_job_launches_only_the_capture_jobs_through_the_boot(self):
        import scenario_references as references
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(references.subprocess, "run") as run:
            run.return_value.returncode = 0
            for script in (None, references.HERE / "oneshot_job.py", references.HERE / "scenario_demo_job.py"):
                references.run_job("/blender", ["--job", "capture"], Path(folder, "s"), Path(folder, "log"),
                                   script=script)
        commands = [call.args[0] for call in run.call_args_list]
        boot = [str(references.CAPTURE_BOOT), "--", str(references.HERE / "scenario_job.py"), "--job", "capture"]
        self.assertEqual(commands[0][-5:], boot)
        self.assertEqual(commands[1][-4:-2], ["--", str(references.HERE / "oneshot_job.py")])
        self.assertEqual(commands[2][-4:], [str(references.HERE / "scenario_demo_job.py"), "--", "--job", "capture"])


class WorkerTests(unittest.TestCase):
    def run_all(self, codes, timeout=None):
        with tempfile.TemporaryDirectory() as folder:
            commands = [[sys.executable, "-c", code] for code in codes]
            logs = [Path(folder, "w%d.log" % index) for index in range(len(codes))]
            started = time.monotonic()
            try:
                split.run_workers(commands, logs, timeout)
            finally:
                elapsed = time.monotonic() - started
            return elapsed

    def test_all_workers_succeed(self):
        self.run_all(["pass", "import time; time.sleep(0.2)"])

    def test_a_failed_worker_stops_the_rest(self):
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "worker 1 exited 4"):
            self.run_all(["import time; time.sleep(30)", "import sys; print('broken'); sys.exit(4)"])
        self.assertLess(time.monotonic() - started, 10)

    def test_a_late_worker_times_out(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.run_all(["import time; time.sleep(30)"], timeout=1)
        self.assertLess(time.monotonic() - started, 10)


class ScorecardCaptureTests(unittest.TestCase):
    def setUp(self):
        import scenario_scorecard
        self.scorecard = scenario_scorecard
        self.scorecard._FAILED_CAPTURES.clear()

    def test_one_shot_scenes_are_captured_with_the_one_shot_job(self):
        self.assertEqual(self.scorecard.capture_script("oneshot_town_centre").name, "oneshot_job.py")
        self.assertEqual(self.scorecard.capture_script("bridge").name, "scenario_job.py")

    def test_a_failed_capture_is_not_relaunched_over_its_log(self):
        # Regression: a one-shot capture timed out, then the scorecard relaunched it with scenario_job.py,
        # which rejected the one-shot scenario and overwrote capture.log, the record of the timeout.
        with tempfile.TemporaryDirectory() as folder:
            blend = Path(folder, "final.blend")
            blend.write_bytes(b"BLENDER")
            with mock.patch.object(self.scorecard.references, "run_job", return_value="timeout") as job:
                self.assertFalse(self.scorecard.capture("oneshot_town_centre", blend, Path(folder, "cap"), "/b"))
                self.assertFalse(self.scorecard.capture("oneshot_town_centre", blend, Path(folder, "cap"), "/b"))
            self.assertEqual(job.call_count, 1)
            self.assertEqual(job.call_args.kwargs["script"].name, "oneshot_job.py")
            self.assertEqual(job.call_args.kwargs["timeout"], self.scorecard.CAPTURE_TIMEOUT_S)

    def test_one_shot_report_captures_through_the_scorecard(self):
        import oneshot_report
        with tempfile.TemporaryDirectory() as folder:
            blend = Path(folder, "final.blend")
            blend.write_bytes(b"BLENDER")
            with mock.patch.object(oneshot_report, "trial_folders", return_value=[(None, None, None, blend)]), \
                    mock.patch.object(self.scorecard, "capture", return_value=True) as shared:
                oneshot_report.prepare(Path(folder), "oneshot_town_centre", "/b")
        self.assertEqual(shared.call_args.args[:4], ("oneshot_town_centre", blend, blend.parent / "scenario-capture",
                                                     "/b"))


if __name__ == "__main__":
    unittest.main()
