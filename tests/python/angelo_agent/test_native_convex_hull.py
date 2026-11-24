# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A collision hull holds every point it was made from, for any cloud (``convex_hull``, 2.1.0; fails first).

``collision_fit`` built its hulls with ``bmesh.ops.convex_hull``. On the dense, nearly coplanar clouds a voxel
decomposition gives it (a part's surface samples plus the corners of its seam cells), that hull held only 15-80% of
its own points in seven of a torus's ten parts (2026-11-15), so the collider left holes and the cap path's inflation
hid it. ``convex_hull.hull`` is a quickhull in float64 with a tolerance relative to the cloud's size: every point is
inside or within the tolerance of every face plane, each face is wound outward, and the faces close a sphere.

The clouds here are unlike each other and unlike any one scene: random balls and shells, a thin slab, voxel corners of
an L block, a torus band sampled like a decomposition part, a cube's corners with its interior, a cloud of repeated
points, and a cloud that lies in a plane (refused as flat). None is tuned to a case: each is checked by the hull's
definition, not by a stored answer.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import unittest
from pathlib import Path

import numpy as np

PATH = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/convex_hull.py"


def load():
    spec = importlib.util.spec_from_file_location("_angelo_convex_hull_under_test", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shell(rng, n, radius=1.0):
    v = rng.normal(size=(n, 3))
    return radius * v / np.linalg.norm(v, axis=1, keepdims=True)


def voxel_corners(cells, size=0.1):
    offsets = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)])
    return ((np.asarray(cells)[:, None, :] + offsets[None]) * size).reshape(-1, 3)


def torus_band(rng, n=4000, major=1.0, minor=0.3, start=0.0, stop=0.7):
    """A slice of a torus's surface (angles start..stop about its axis), densely sampled, plus the seam's grid."""
    u = rng.uniform(start, stop, n)
    v = rng.uniform(0, 2 * np.pi, n)
    surface = np.stack([(major + minor * np.cos(v)) * np.cos(u), (major + minor * np.cos(v)) * np.sin(u),
                        minor * np.sin(v)], axis=1)
    grid = np.array([[x, 0.0, z] for x in np.linspace(0.7, 1.3, 13) for z in np.linspace(-0.3, 0.3, 13)])
    return np.concatenate([surface, grid])


def clouds():
    rng = np.random.default_rng(20261118)
    l_block = [(x, y, z) for x in range(6) for y in range(6) for z in range(3) if x < 2 or y < 2]
    cube = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=float)
    return {
        "ball": rng.uniform(-1, 1, size=(3000, 3)),
        "shell": shell(rng, 2500),
        "thin_slab": np.concatenate([rng.uniform(-1, 1, size=(4000, 3)) * (1, 1, 1e-4), [[0, 0, 0.01]]]),
        "l_block_corners": voxel_corners(l_block),
        "torus_band": torus_band(rng),
        "torus_band_far": torus_band(rng, major=40.0, minor=0.5, start=1.0, stop=1.3) + 1000.0,
        "cube_and_inside": np.concatenate([cube, rng.uniform(0.01, 0.99, size=(500, 3))]),
        "repeated": np.repeat(rng.uniform(-1, 1, size=(40, 3)), 25, axis=0),
    }


class HullProperties(unittest.TestCase):
    def setUp(self):
        self.ch = load()

    def check(self, name, points):
        vertices, faces = self.ch.hull(points)
        extent = float(np.ptp(points, axis=0).max())
        tol = self.ch.tolerance(points)
        a, b, c = (points[faces[:, k]] for k in range(3))
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal, axis=1)
        self.assertTrue((length > 0).all(), name + ": a face has no area")
        normal /= length[:, None]
        reach = (points @ normal.T) - np.einsum("ij,ij->i", normal, a)[None, :]
        self.assertLessEqual(float(reach.max()), 4 * tol, name + ": a point lies outside a face's plane")
        centre = points[vertices].mean(axis=0)
        self.assertTrue((np.einsum("ij,ij->i", normal, a - centre) > 0).all(), name + ": a face is wound inward")
        edges = {}
        for face in faces:
            for u, v in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
                self.assertNotIn((u, v), edges, name + ": an edge is used twice in one direction")
                edges[(u, v)] = True
        self.assertTrue(all((v, u) in edges for u, v in edges), name + ": the faces do not close")
        self.assertEqual(len(vertices) - len(edges) // 2 + len(faces), 2, name + ": not a sphere (V - E + F)")
        self.assertEqual(sorted(set(faces.ravel().tolist())), sorted(vertices.tolist()), name)
        volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0
        self.assertGreater(volume, 0.0, name)
        self.assertLess(tol, extent * 1e-5, name + ": the tolerance is not small against the cloud")
        return vertices, faces, volume

    def test_every_cloud_is_held_by_a_closed_outward_hull(self):
        for name, points in clouds().items():
            with self.subTest(cloud=name):
                self.check(name, np.asarray(points, dtype=np.float64))

    def test_a_cube_with_points_inside_is_its_eight_corners(self):
        vertices, faces, volume = self.check("cube", clouds()["cube_and_inside"])
        self.assertEqual(len(vertices), 8)
        self.assertEqual(len(faces), 12)
        self.assertAlmostEqual(volume, 1.0, places=9)

    def test_points_on_a_face_are_not_vertices(self):
        rng = np.random.default_rng(7)
        cube = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=float)
        on_faces = rng.uniform(0, 1, size=(600, 3))
        on_faces[np.arange(600), rng.integers(0, 3, 600)] = rng.integers(0, 2, 600)
        vertices, _faces, volume = self.check("cube_faces", np.concatenate([cube, on_faces]))
        self.assertEqual(len(vertices), 8)
        self.assertAlmostEqual(volume, 1.0, places=9)

    def test_a_capped_hull_is_a_closed_hull_inside_the_full_one(self):
        """``max_vertices``: at most that many vertices, still closed and outward, never outside the full hull; more
        vertices never hold less of the solid (farthest first)."""
        for name, points in clouds().items():
            if name == "cube_and_inside":
                continue   # Eight corners: no cap below its own size is asked of it.
            with self.subTest(cloud=name):
                _, faces = self.ch.hull(points)
                full = self.ch.volume(points, faces)
                volumes = []
                for cap in (8, 16, 32):
                    vertices, capped = self.ch.hull(points, max_vertices=cap)
                    self.assertLessEqual(len(vertices), cap)
                    self.assertEqual(len(vertices) - (3 * len(capped)) // 2 + len(capped), 2)
                    volumes.append(self.ch.volume(points, capped))
                self.assertLessEqual(volumes[-1], full * (1 + 1e-9))
                self.assertEqual(volumes, sorted(volumes))

    def test_a_flat_cloud_is_refused(self):
        rng = np.random.default_rng(3)
        flat = np.concatenate([rng.uniform(-1, 1, size=(200, 2)), np.zeros((200, 1))], axis=1)
        for points in (flat, np.zeros((10, 3)), np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0.0]])):
            with self.assertRaises(self.ch.Flat):
                self.ch.hull(points)

    def test_order_does_not_change_the_hull(self):
        """The same solid whatever the points' order (a point on an edge may or may not be kept as a vertex)."""
        points = clouds()["torus_band"]
        order = np.random.default_rng(11).permutation(len(points))
        _, _, first = self.check("torus_band", points)
        _, _, second = self.check("torus_band_shuffled", points[order])
        self.assertAlmostEqual(first, second, delta=first * 1e-9)


if __name__ == "__main__":
    unittest.main()
