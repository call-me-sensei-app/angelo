# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A high-to-low bake's ray distances measured from the meshes (``bake_cage``), without Blender.

Contract: doc/angelo/long_jobs.rst, "High to low" (measured distances). Blender casts each texel's ray from the
target's surface pushed out by the extrusion, back along its normal, as far as the maximum ray distance, and bakes
the first source surface it meets. What it should meet is the sources' outer surface, so at each sample point the
need is measured along the normal: outward to where the point leaves every source volume it is inside (parts that
overlap, as kitbashed high-poly parts do, count as one), or inward to the first source surface when the point is
outside them. Boxes give closed forms on every face.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import bake_cage  # noqa: E402
from angelo_agent.game_ready_metrics import Geometry  # noqa: E402

# A box's triangles, wound so every face's normal points out.
BOX_TRIS = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
FEW = 2048   # Samples: the reference caster below is NumPy; the application's is Blender's BVH.


def box(half=(1.0, 1.0, 1.0), center=(0.0, 0.0, 0.0)):
    x, y, z = half
    corners = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    return Geometry(np.asarray(corners) + np.asarray(center), BOX_TRIS)


class Caster:
    """Every hit of a ray on the sources' triangles (Moller-Trumbore), as ``bake_cage.separation`` asks for."""

    def __init__(self, *sources):
        corners = np.concatenate([source.corners().astype(np.float64) for source in sources])
        self.a, self.e1, self.e2 = corners[:, 0], corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
        self.normal = np.cross(self.e1, self.e2)
        self.calls = 0
        self.reaches = set()

    def __call__(self, origin, direction, reach):
        self.calls += 1
        self.reaches.add(reach)
        p = np.cross(direction, self.e2)
        det = (self.e1 * p).sum(1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = origin - self.a
        u = (s * p).sum(1) * inv
        q = np.cross(s, self.e1)
        v = (q * direction).sum(1) * inv
        t = (self.e2 * q).sum(1) * inv
        limit = np.inf if reach is None else reach   # None: to the end of the sources.
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9) & (t <= limit)
        exits = (self.normal[hit] * direction).sum(1) > 0
        order = np.argsort(t[hit], kind="stable")
        return [(float(t[hit][i]), bool(exits[i])) for i in order]


def measure(target, *sources, samples=FEW):
    return bake_cage.separation(target, Caster(*sources), samples=samples)


class SeparationTests(unittest.TestCase):
    def test_a_source_outside_needs_extrusion(self):
        result = measure(box(), box((1.1, 1.1, 1.1)))
        self.assertEqual(result["covered_share"], 1.0)
        self.assertAlmostEqual(result["outward"], 0.1, places=5)
        self.assertEqual(result["inward"], 0.0)
        self.assertAlmostEqual(result["extrusion"], 0.1 * bake_cage.MARGIN, places=5)
        # Never 0 and always beyond the extrusion: Blender reads a maximum ray distance of 0 as "no limit", which
        # bakes surfaces far away.
        self.assertGreater(result["ray_distance"], result["extrusion"])
        self.assertEqual(result["missed_share"], 0.0)

    def test_a_source_inside_needs_reach_and_no_extrusion(self):
        result = measure(box(), box((0.85, 0.85, 0.85)))
        self.assertAlmostEqual(result["inward"], 0.15, places=5)
        self.assertEqual(result["extrusion"], 0.0)
        self.assertAlmostEqual(result["ray_distance"], 0.15 * bake_cage.MARGIN, places=5)

    def test_both_ways_add(self):
        # Wider along x, narrower along y and z: the x faces are 0.2 inside the source, the others 0.1 outside it.
        result = measure(box(), box((1.2, 0.9, 0.9)))
        self.assertAlmostEqual(result["outward"], 0.2, places=5)
        self.assertAlmostEqual(result["inward"], 0.1, places=5)
        self.assertAlmostEqual(result["extrusion"], 0.2 * bake_cage.MARGIN, places=5)
        self.assertAlmostEqual(result["ray_distance"], 0.3 * bake_cage.MARGIN, places=5)

    def test_overlapping_parts_are_one_surface(self):
        # Two slabs that overlap in the middle make the same outer box; their inner faces are never the surface.
        left, right = box((0.6, 1.1, 1.1), (-0.5, 0, 0)), box((0.6, 1.1, 1.1), (0.5, 0, 0))
        result = measure(box(), left, right)
        self.assertAlmostEqual(result["outward"], 0.1, places=5)
        self.assertEqual(result["missed_share"], 0.0)

    def test_a_part_that_rises_from_the_surface_is_its_outer_surface(self):
        # A shell 0.1 out and a block sunk into its top that rises 0.9 above the target's top face. Where the block
        # covers a quarter of the top face, the extrusion must reach its top. 0.9 is beyond REACH of this target
        # (0.866): the reach bounds only the search for a surface outside a point, never the walk out of one inside.
        self.assertGreater(0.9, bake_cage.REACH * 2.0 * np.sqrt(3.0))
        shell, block = box((1.1, 1.1, 1.1)), box((0.5, 0.5, 0.5), (0, 0, 1.4))
        result = measure(box(), shell, block)
        self.assertEqual(result["covered_share"], 1.0)
        self.assertAlmostEqual(result["outward"], 0.9, places=5)
        self.assertAlmostEqual(result["extrusion"], 0.9 * bake_cage.MARGIN, places=5)
        self.assertEqual(result["missed_share"], 0.0)

    def test_a_point_inside_is_covered_however_thick_the_source(self):
        # Every face 1.0 inside a source shell (beyond REACH): all covered, all needing 1.0 outward.
        result = measure(box(), box((2.0, 2.0, 2.0)))
        self.assertEqual(result["covered_share"], 1.0)
        self.assertAlmostEqual(result["outward"], 1.0, places=5)
        self.assertEqual(result["missed_share"], 0.0)

    def test_reach_bounds_only_the_inward_search(self):
        caster = Caster(box((1.1, 1.1, 1.1)))
        bake_cage.separation(box(), caster, samples=64)
        self.assertEqual(caster.reaches, {None})   # Every point is inside: the walk is never cut at the reach.
        caster = Caster(box((0.85, 0.85, 0.85)))
        bake_cage.separation(box(), caster, samples=64)
        bounded = [reach for reach in caster.reaches if reach is not None]   # Every point is outside: one search.
        self.assertIn(None, caster.reaches)
        self.assertEqual(len(bounded), 1)
        self.assertAlmostEqual(bounded[0], bake_cage.REACH * 2.0 * np.sqrt(3.0))

    def test_a_rare_spike_is_counted_not_followed(self):
        # The same block, small: a share of points under the quantile's tail. They are covered (inside the sources)
        # and reported as missed, never dropped from the accounting.
        shell, spike = box((1.1, 1.1, 1.1)), box((0.1, 0.1, 0.5), (0, 0, 1.4))   # 0.17% of the area
        result = measure(box(), shell, spike, samples=8192)
        self.assertEqual(result["covered_share"], 1.0)
        self.assertAlmostEqual(result["outward"], 0.1, places=5)
        self.assertGreater(result["missed_share"], 0.0)
        self.assertLessEqual(result["missed_share"], 1.0 - bake_cage.QUANTILE)

    def test_a_gap_stops_the_walk(self):
        # A shell 0.1 out, then open space, then a separate wall beyond it: the wall is another surface, never this
        # point's (a point in a crevice would otherwise reach across it).
        shell, wall = box((1.1, 1.1, 1.1)), box((2.0, 2.0, 0.1), (0, 0, 1.5))
        result = measure(box(), shell, wall)
        self.assertAlmostEqual(result["outward"], 0.1, places=5)

    def test_scale_free(self):
        small, large = measure(box(), box((1.2, 0.9, 0.9))), measure(box((50, 50, 50)), box((60, 45, 45)))
        for key in ("outward", "inward", "extrusion", "ray_distance"):
            self.assertAlmostEqual(large[key], small[key] * 50, places=3, msg=key)

    def test_a_source_out_of_reach_is_not_covered(self):
        result = measure(box(), box(center=(10, 0, 0)))
        self.assertEqual(result["covered_share"], 0.0)
        self.assertNotIn("extrusion", result)

    def test_partly_covered(self):
        # A source over only the target's +x half: the other half's points find nothing within reach.
        result = measure(box(), box((0.55, 1.1, 1.1), (0.55, 0, 0)))
        self.assertGreater(result["covered_share"], 0.3)
        self.assertLess(result["covered_share"], 0.8)
        self.assertIn("extrusion", result)

    def test_same_meshes_same_distances_and_rounded(self):
        first, second = measure(box(), box((1.2, 0.9, 0.9))), measure(box(), box((1.2, 0.9, 0.9)))
        self.assertEqual(first, second)
        for key in ("extrusion", "ray_distance", "outward", "inward", "covered_share", "missed_share"):
            self.assertEqual(first[key], round(first[key], 6), key)

    def test_casts_are_bounded(self):
        caster = Caster(box((1.1, 1.1, 1.1)))
        bake_cage.separation(box(), caster, samples=256)
        points = 256 + 8   # Samples add every vertex of the target.
        self.assertLessEqual(caster.calls, 2 * points)


class DefaultTests(unittest.TestCase):
    def test_measured_only_when_the_request_and_the_scene_leave_it_open(self):
        self.assertTrue(bake_cage.wanted({}, cage=None, scene_extrusion=0.0, scene_ray=0.0))
        self.assertFalse(bake_cage.wanted({"extrusion": 0.1}, cage=None, scene_extrusion=0.0, scene_ray=0.0))
        self.assertFalse(bake_cage.wanted({"ray_distance": 0.5}, cage=None, scene_extrusion=0.0, scene_ray=0.0))
        self.assertFalse(bake_cage.wanted({}, cage=object(), scene_extrusion=0.0, scene_ray=0.0))
        # The person's own bake settings, once changed from Blender's defaults, are theirs.
        self.assertFalse(bake_cage.wanted({}, cage=None, scene_extrusion=0.02, scene_ray=0.0))
        self.assertFalse(bake_cage.wanted({}, cage=None, scene_extrusion=0.0, scene_ray=0.3))


if __name__ == "__main__":
    unittest.main()
