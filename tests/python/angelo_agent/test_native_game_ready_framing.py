# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check compares two meshes over everything either has (doc/angelo/game_ready_check.rst).

A comparison framed or sampled on one mesh alone cannot see what only the other has: silhouette views framed on the
source clipped away a LOD's geometry beyond the source's bounds (a detached cube read IoU 1.0, the audit of PR #135),
and attributes read at the source's sample points alone never reached a LOD's extra shell. These cases were not used
to build the fix: extra geometry on every side, just past the frame, inside the frame but outside the box, larger
copies, a missing part, and a flipped shell; identical meshes stay a perfect match. IoU and the attribute differences
are symmetric, so each case is also checked with the roles swapped."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_native_game_ready_check import G, K, cube, grid  # noqa: E402

RESOLUTION = 96
QUANTIZATION = 0.03   # Pixel-centre coverage at RESOLUTION moves an IoU by about this much at most.


def merged(*parts):
    """One geometry of several, each keeping its own corner normals, UVs and weights when all have them."""
    offsets = np.cumsum([0] + [len(p.positions) for p in parts[:-1]])
    tris = np.concatenate([p.tris + np.uint32(o) for p, o in zip(parts, offsets)])

    def joined(name):
        values = [getattr(p, name) for p in parts]
        return None if any(v is None for v in values) else np.concatenate(values)
    return G.Geometry(np.concatenate([p.positions for p in parts]), tris, joined("corner_uv"),
                      joined("corner_normal"), joined("weights"))


def scaled(geometry, factor):
    return G.Geometry(geometry.positions * np.float32(factor), geometry.tris)


def iou(source, lod):
    facts = G.silhouettes(source, [lod], K, resolution=RESOLUTION)[0]
    return facts["iou_min"], facts["iou_mean"]


class SilhouetteFramingTests(unittest.TestCase):
    def assertIou(self, source, lod, expected_min, expected_mean=None):
        """The expected IoU, and the same with source and LOD swapped (IoU does not depend on which is which)."""
        for a, b, way in ((source, lod, "source vs LOD"), (lod, source, "swapped")):
            low, mean = iou(a, b)
            self.assertAlmostEqual(low, expected_min, delta=QUANTIZATION, msg="iou_min, %s" % way)
            if expected_mean is not None:
                self.assertAlmostEqual(mean, expected_mean, delta=QUANTIZATION, msg="iou_mean, %s" % way)
        self.assertAlmostEqual(iou(source, lod)[1], iou(lod, source)[1], delta=QUANTIZATION, msg="iou_mean symmetric")

    def test_identical_meshes_are_a_perfect_match(self):
        for shape in (cube(), merged(cube(), cube(half=0.5, center=(3.0, 0.0, 0.0))), scaled(cube(), 40.0)):
            self.assertEqual(iou(shape, shape), (1.0, 1.0))

    def test_a_detached_part_on_any_side_lowers_the_match(self):
        """A cube half the size, apart from the source: in a view across the gap it adds a quarter of the source's
        area, so the worst view reads 1 / 1.25 whichever side it is on."""
        for axis in range(3):
            for sign in (1.0, -1.0):
                center = [0.0, 0.0, 0.0]
                center[axis] = 3.0 * sign
                with self.subTest(center=center):
                    self.assertIou(cube(), merged(cube(), cube(half=0.5, center=center)), 0.8)

    def test_a_part_just_past_the_frame_counts_whole(self):
        """Partly inside the source's views (they reach 1.73 from its centre) and partly beyond them."""
        self.assertIou(cube(), merged(cube(), cube(half=0.5, center=(0.0, -1.75, 0.0))), 0.8)

    def test_a_part_outside_the_box_but_inside_the_frame(self):
        """Inside the source's bounding sphere, so the source's own views see it all."""
        self.assertIou(cube(), merged(cube(), cube(half=0.25, center=(0.0, 0.0, 1.3))), 1.0 / 1.0625)

    def test_a_larger_copy_reads_its_area_ratio_in_every_view(self):
        """Scaled about the centre, every view's outline scales with it: IoU is 1 / factor² in all 7 views."""
        for factor in (1.25, 2.0, 3.0):
            with self.subTest(factor=factor):
                self.assertIou(cube(), scaled(cube(), factor), 1.0 / factor ** 2, 1.0 / factor ** 2)

    def test_a_missing_part_lowers_the_match(self):
        """The LOD lacks the source's detached cube; and a LOD smaller all over."""
        self.assertIou(merged(cube(), cube(half=0.5, center=(-3.0, 0.0, 0.0))), cube(), 0.8)
        self.assertIou(cube(), cube(half=0.5), 0.25, 0.25)

    def test_a_far_lod_shares_nothing(self):
        """Placed away from its source (``apart``), off every view direction: compared where it is, it shares no pixel
        in any of the 7 views."""
        self.assertEqual(iou(cube(), cube(center=(10.0, 7.0, 3.0))), (0.0, 0.0))


class AttributeSamplingTests(unittest.TestCase):
    def test_a_shell_only_the_lod_has_is_measured(self):
        """The LOD is the source plus a second shell a millimetre above it, flipped, with other UVs and weights:
        the source's points all find the faithful copy nearest, so only the LOD's own points can see the shell."""
        source = grid(2)
        n = len(source.positions)
        source.weights = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (n, 1))
        copy = grid(2)
        copy.weights = source.weights.copy()
        shell = grid(2, z=0.001)
        shell.corner_normal = -shell.corner_normal
        shell.corner_uv = shell.corner_uv + np.float32(0.5)
        shell.weights = np.tile(np.array([[0.0, 1.0]], dtype=np.float32), (n, 1))
        lod = merged(copy, shell)
        for a, b, way in ((source, lod, "source vs LOD"), (lod, source, "swapped")):
            facts = G.Comparison(a, G.Samples(a, count=64), b, K, count=64).attributes()
            self.assertAlmostEqual(facts["normals"]["max"], 180.0, places=2, msg=way)
            self.assertAlmostEqual(facts["uv_shift"]["max"], 0.5 * np.sqrt(2.0), places=4, msg=way)
            self.assertAlmostEqual(facts["skin_weights"]["max"], 1.0, places=5, msg=way)

    def test_the_same_mesh_has_no_attribute_difference(self):
        source = grid(3)
        source.weights = np.tile(np.array([[0.5, 0.5]], dtype=np.float32), (len(source.positions), 1))
        lod = grid(3)
        lod.weights = source.weights.copy()
        facts = G.Comparison(source, G.Samples(source, count=64), lod, K, count=64).attributes()
        for name in ("normals", "uv_shift", "skin_weights"):
            self.assertAlmostEqual(facts[name]["max"], 0.0, places=5, msg=name)


if __name__ == "__main__":
    unittest.main()
