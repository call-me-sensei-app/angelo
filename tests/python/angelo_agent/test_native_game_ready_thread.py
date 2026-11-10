# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check off the main thread, without Blender (doc/angelo/game_ready_check.rst, "Off the main thread"):
the call reads the objects and returns a pending result at once; every reduction and measurement, Decimate and the
convex hull included, runs on the check's own thread; the result equals the one-call route's; a failure while
measuring is the same refusal as one raised at once; Stop ends the measuring at its next step. The reference kernels
of test_native_game_ready_check stand in for ``_bpy._angelo_reduce``; the compiled module is checked in the app
(test_blender_game_ready_check)."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
_spec = importlib.util.spec_from_file_location("game_ready_reference", HERE / "test_native_game_ready_check.py")
reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reference)
from angelo_agent import game_ready_host as H, game_ready_mesh as M  # noqa: E402
from angelo_agent.game_ready_spec import CHECK, validate_game_ready  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402

SOURCE, COLLIDER = reference.H1, reference.H2


class Kernels(reference.ReferenceKernels):
    """The reference kernels, recording which thread each runs on; Decimate's fake can be held until released."""

    def __init__(self):
        self.threads, self.hold = {}, threading.Event()
        self.hold.set()

    def _on(self, name):
        self.threads.setdefault(name, set()).add(threading.get_ident())

    def status(self):
        return {"available": False, "reason": "not in this test"}

    def snapshot(self, mesh):
        self._on("snapshot")
        return ("snapshot", mesh)

    def vertex_weights(self, mesh, groups):
        raise AssertionError("no skinned meshes here")

    def decimate(self, snapshot, ratio, triangulate=True, groups=None):
        self._on("decimate")
        assert snapshot[0] == "snapshot", "Decimate reads the snapshot, never a Blender mesh"
        assert self.hold.wait(10)
        geometry = reference.cube(1.0)
        corners = geometry.tris.astype(np.int32)
        return {"positions": geometry.positions.tobytes(), "tris": corners.tobytes(), "corner_uvs": None,
                "corner_normals": np.zeros((corners.size, 3), np.float32).tobytes(), "weights": None}

    def convex_hull(self, points):
        self._on("convex_hull")
        geometry = reference.cube(1.0)
        return geometry.positions.tobytes(), geometry.tris.astype(np.int32).tobytes()

    def surface(self, positions, tris):
        self._on("surface")
        return super().surface(positions, tris)

    def nearest(self, surface, points):
        """The reference's answer, one triangle at a time against every point (the meshes here are small)."""
        self._on("nearest")
        positions, tris = surface
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        best = np.full(len(points), np.inf)
        index = np.zeros(len(points), dtype=np.int32)
        closest = np.zeros((len(points), 3))
        for k, (a, b, c) in enumerate(positions[tris]):
            q = reference.closest_on_triangles(points, a, b, c)
            d = np.linalg.norm(q - points, axis=1)
            nearer = d < best
            best[nearer], index[nearer], closest[nearer] = d[nearer], k, q[nearer]
        return best.astype(np.float32).tobytes(), index.tobytes(), closest.astype(np.float32).tobytes()

    def rasterize(self, positions, tris, views, resolution):
        self._on("rasterize")
        return super().rasterize(positions, tris, views, resolution)


class FakeObject:
    def __init__(self, name, half):
        self.name, self.half = name, half


def fake_read(kernels, half_of=None):
    """``game_ready_mesh.read`` over a cube's arrays (what ``_arrays`` copies out of a Blender mesh)."""
    def read(obj, depsgraph, unit_scale, reduce, snapshot=False):
        kernels._on("read")
        half = (half_of or {}).get(obj.name, obj.half)
        geometry = reference.cube(half)
        tris = geometry.tris.astype(np.int32)
        corner_tris = np.arange(tris.size, dtype=np.int32).reshape(-1, 3)
        normals = np.tile(np.array([0.0, 0.0, 1.0], np.float32), (tris.size, 1))
        raw = (geometry.positions, tris, corner_tris, np.zeros(len(tris), np.int32), normals, None, None)
        return M.MeshRead(raw, "the evaluated mesh", reduce.snapshot(obj) if snapshot else None, [], [],
                          np.eye(4), 0, unit_scale)
    return read


class Window:
    class scene:
        class unit_settings:
            system, scale_length = "NONE", 1.0

    class view_layer:
        depsgraph = None


OBJECTS = {SOURCE: FakeObject("Source", 1.0), COLLIDER: FakeObject("Box", 1.2)}


def request(**extra):
    return validate_game_ready(CHECK, dict({"object": SOURCE, "simplify": {"ratios": [0.5], "methods": ["DECIMATE"]},
                                            "collider": COLLIDER}, **extra))


def wait(pending, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = pending.poll()
        if result is not None:
            return result
        time.sleep(0.01)
    raise AssertionError("the check never finished")


class OffTheMainThread(unittest.TestCase):
    def setUp(self):
        self.kernels = Kernels()
        patches = [mock.patch.object(H, "_reduce", lambda: self.kernels),
                   mock.patch.object(H, "_mesh_object", lambda window, handle, what: OBJECTS[handle]),
                   mock.patch.object(M, "read", fake_read(self.kernels))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_call_returns_at_once_and_everything_after_the_read_runs_elsewhere(self):
        self.kernels.hold.clear()   # Decimate waits until released below: the call returns before it ends.
        main = threading.get_ident()
        pending = H.execute(Window, CHECK, request())
        self.assertIsInstance(pending, StudioPending)
        self.assertIsNone(pending.poll(), "nothing is ready while Decimate runs")
        self.assertEqual(pending.status_text, H.STATUS)
        self.kernels.hold.set()
        result = wait(pending)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.kernels.threads["read"], {main})
        self.assertEqual(self.kernels.threads["snapshot"], {main})
        for name in ("decimate", "convex_hull", "surface", "nearest", "rasterize"):
            self.assertNotIn(main, self.kernels.threads[name], "%s ran on the calling thread" % name)

    def test_the_result_is_the_one_call_route_s(self):
        deferred = wait(H.execute(Window, CHECK, request()))
        direct = H.check(Window, request())
        for result in (deferred, direct):
            result.pop("native_ms")
            for lod in result["lods"]:
                lod.pop("reduce_ms", None)
        self.assertEqual(deferred, direct)
        self.assertIn("collider", deferred)
        self.assertEqual([lod["method"] for lod in deferred["lods"]], ["DECIMATE"])

    def test_a_failure_while_measuring_is_the_refusal_raised_at_once(self):
        with mock.patch.object(M, "read", fake_read(self.kernels, {"Source": 0.0})), \
                mock.patch.object(reference.G.Geometry, "triangle_count", property(lambda self: 0)):
            result = wait(H.execute(Window, CHECK, request()))
            with self.assertRaises(H.RefusalError) as raised:
                H.check(Window, request())
        self.assertEqual(result["error_code"], "VALIDATION")
        self.assertEqual(result["error"], str(raised.exception))

    def test_a_source_without_faces_is_refused_at_once(self):
        def empty(obj, depsgraph, unit_scale, reduce, snapshot=False):
            raw = (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32), np.zeros((0, 3), np.int32),
                   np.zeros(0, np.int32), np.zeros((0, 3), np.float32), None, None)
            return M.MeshRead(raw, "the evaluated mesh", None, [], [], np.eye(4), 0, unit_scale)
        with mock.patch.object(M, "read", empty):
            with self.assertRaises(H.RefusalError) as raised:
                H.execute(Window, CHECK, request())
        self.assertEqual(raised.exception.code, "VALIDATION")
        self.assertIn("no faces", str(raised.exception))

    def test_a_refusal_before_the_read_is_raised_at_once(self):
        with mock.patch.object(M, "read", side_effect=ValueError("Big has 3000000 triangles")):
            with self.assertRaises(H.RefusalError) as raised:
                H.execute(Window, CHECK, request())
        self.assertEqual(raised.exception.code, "VALIDATION")

    def test_stop_ends_the_measuring_at_its_next_step(self):
        self.kernels.hold.clear()
        pending = H.execute(Window, CHECK, request())
        pending.cancel()
        self.kernels.hold.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and "convex_hull" not in self.kernels.threads:
            if not any(t.name == "Angelo game-ready check" for t in threading.enumerate()):
                break
            time.sleep(0.01)
        self.assertNotIn("convex_hull", self.kernels.threads, "the collider was still measured after Stop")
        self.assertNotIn("rasterize", self.kernels.threads)

    def test_a_check_that_outlives_its_bound_says_why(self):
        pending = H.execute(Window, CHECK, request())
        wait(pending)
        self.assertEqual(pending.timeout, H.CHECK_SECONDS)
        self.assertEqual(pending.stopped_result["error_code"], "LIMIT_EXCEEDED")
        self.assertIs(pending.stopped_result["scene_changed"], False)


class TheMainThreadReadsOnly(unittest.TestCase):
    def test_the_host_and_mesh_modules_never_reach_blender_data_off_the_read(self):
        """``Taken`` (the main thread's part) is the only place that resolves objects or reads Blender meshes, and
        neither module imports bpy or bmesh: the worker's code has nothing of Blender's to reach."""
        host = (ADDON / "game_ready_host.py").read_text(encoding="utf-8")
        mesh = (ADDON / "game_ready_mesh.py").read_text(encoding="utf-8")
        for text in (host, mesh):
            self.assertNotIn("import bpy", text)
            self.assertNotIn("import bmesh", text)
        taken = host[host.index("class Taken:"):host.index("def check(window, request):")]
        rest = host.replace(taken, "")
        self.assertIn("M.read(", taken)
        self.assertNotIn("M.read(", rest)
        self.assertNotIn("_mesh_object(", rest[rest.index("def _measure("):])
        self.assertIn(".snapshot", mesh[mesh.index("def decimate("):])


if __name__ == "__main__":
    unittest.main()
