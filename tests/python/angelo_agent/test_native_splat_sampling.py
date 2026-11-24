# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The splat collision measure (``angelo_agent/splat_measure.py``), which the product and the splat suite share.

The surface sampler is unbiased on meshes with known answers: a unit cube whose faces carry very different numbers of
triangles gets points on each face in proportion to its area, every point lies on the cube, and within a triangle the
points are uniform (their mean is the centroid and each of its four half-scale triangles gets a quarter), so neither
triangle count nor corners weigh in. The same seed gives the same points. ``within_noise`` accepts two samples of one
distribution and refuses a shift several standard errors wide."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import splat_measure  # noqa: E402


def cube(fine=12):
    """A unit cube: five faces as two triangles, the sixth (z = 0) as a fine grid of 2 x fine^2 triangles."""
    coords, triangles = [], []

    def quad(a, b, c, d):
        base = len(coords)
        coords.extend([a, b, c, d])
        triangles.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])

    quad((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))
    quad((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))
    quad((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))
    quad((1, 1, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1))
    quad((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1))
    step = 1.0 / fine
    for i in range(fine):
        for j in range(fine):
            x, y = i * step, j * step
            quad((x, y, 0), (x + step, y, 0), (x + step, y + step, 0), (x, y + step, 0))
    return np.array(coords, dtype=float), np.array(triangles)


class AreaPointsTests(unittest.TestCase):
    def test_each_face_gets_points_by_area_not_by_triangle_count(self):
        coords, triangles = cube()
        points = splat_measure.area_points(coords, triangles, count=6000)
        bottom = np.mean(np.abs(points[:, 2]) < 1e-12)
        # 2 x 144 of the 298 triangles are on the bottom; a sample by triangle or vertex would put ~97% there.
        self.assertAlmostEqual(bottom, 1 / 6, delta=0.03)

    def test_every_point_lies_on_the_cube(self):
        coords, triangles = cube()
        points = splat_measure.area_points(coords, triangles, count=3000)
        inside = np.all((points > -1e-12) & (points < 1 + 1e-12), axis=1)
        on_face = np.min(np.minimum(np.abs(points), np.abs(points - 1)), axis=1)
        self.assertTrue(inside.all())
        self.assertLess(float(on_face.max()), 1e-12, "the offset of the cube to its own surface is ~0")

    def test_the_same_seed_gives_the_same_points(self):
        coords, triangles = cube()
        self.assertTrue(np.array_equal(splat_measure.area_points(coords, triangles, 500),
                                       splat_measure.area_points(coords, triangles, 500)))

    def test_within_a_triangle_points_are_uniform(self):
        corners = np.array([(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
        points = splat_measure.area_points(corners, np.array([(0, 1, 2)]), count=20000)
        self.assertTrue(np.allclose(points.mean(axis=0), corners.mean(axis=0), atol=0.02), "the mean is the centroid")
        # Barycentric coordinates; the four half-scale triangles: one at each corner, and the middle one.
        u, v = points[:, 0] / 2.0, points[:, 1]
        quarters = [np.mean((u >= 0.5)), np.mean(v >= 0.5), np.mean(u + v <= 0.5),
                    np.mean((u < 0.5) & (v < 0.5) & (u + v > 0.5))]
        for share in quarters:
            self.assertAlmostEqual(share, 0.25, delta=0.02)   # A corner-biased sampler puts ~0.5 in one corner.


class WithinNoiseTests(unittest.TestCase):
    def test_two_samples_of_one_distribution_are_within_noise_and_a_shift_is_not(self):
        rng = np.random.default_rng(7)
        a, b = rng.exponential(1.0, 2000), rng.exponential(1.0, 2000)
        self.assertTrue(splat_measure.within_noise(a, b))
        self.assertTrue(splat_measure.within_noise(a, b, "mean"))
        self.assertFalse(splat_measure.within_noise(a + 1.0, b))
        self.assertFalse(splat_measure.within_noise(a + 0.5, b, "mean"))
        self.assertTrue(splat_measure.within_noise(b - 0.5, b), "smaller is never refused")


if __name__ == "__main__":
    unittest.main()
