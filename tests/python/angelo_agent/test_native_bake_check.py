# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How close a baked material looks to the material it was baked from (``bake_check``), without Blender.

Contract: doc/angelo/long_jobs.rst, "Bake check". The check draws the target with its own material and with the baked
one from the same views and light and compares the pictures: CIE76 colour difference in CIELAB (D65) over the pixels
either picture covers, the worst tile, how much of the difference is lightness (shading: roughness, normal, AO) and
how much is chroma (colour), and the share of pixels only one picture covers (alpha or a missing part).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import bake_check  # noqa: E402


def picture(rgb, size=64, alpha=1.0):
    out = np.zeros((size, size, 4), dtype=np.float32)
    out[..., :3] = rgb
    out[..., 3] = alpha
    return out


class LabTests(unittest.TestCase):
    def test_reference_colours(self):
        # Linear sRGB primaries and white in CIELAB (D65), published values.
        lab = bake_check.lab(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], dtype=np.float64))
        expected = [(53.24, 80.09, 67.20), (87.73, -86.18, 83.18), (32.30, 79.19, -107.86), (100.0, 0.0, 0.0)]
        for got, want in zip(lab, expected):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, delta=0.05)

    def test_black_is_zero(self):
        self.assertTrue(np.allclose(bake_check.lab(np.zeros((1, 3))), 0.0, atol=1e-9))


class CompareTests(unittest.TestCase):
    def test_same_pictures_differ_by_nothing(self):
        a = picture((0.2, 0.4, 0.1))
        facts = bake_check.compare([a], [a.copy()])
        self.assertEqual((facts["delta_e_mean"], facts["delta_e_p95"], facts["only_one_share"]), (0.0, 0.0, 0.0))

    def test_red_against_green_is_the_published_distance(self):
        facts = bake_check.compare([picture((1, 0, 0))], [picture((0, 1, 0))])
        self.assertAlmostEqual(facts["delta_e_mean"], 170.56, delta=0.1)

    def test_background_is_not_compared(self):
        a, b = picture((0.5, 0.5, 0.5)), picture((0.5, 0.5, 0.5))
        a[:32, :, :] = 0.0
        b[:32, :, :] = (1.0, 0.0, 0.0, 0.0)   # Transparent: what is drawn there is not the object.
        self.assertEqual(bake_check.compare([a], [b])["delta_e_mean"], 0.0)

    def test_coverage_only_one_picture_has(self):
        a, b = picture((0.5, 0.5, 0.5)), picture((0.5, 0.5, 0.5))
        b[:16, :, 3] = 0.0   # A quarter of the object is gone from the baked picture (alpha baked wrong).
        self.assertAlmostEqual(bake_check.compare([a], [b])["only_one_share"], 0.25, places=6)

    def test_worst_tile_is_found_and_named_by_view(self):
        a, b = picture((0.3, 0.3, 0.3)), picture((0.3, 0.3, 0.3))
        c = picture((0.3, 0.3, 0.3))
        d = c.copy()
        d[40:48, 8:16, :3] = (0.9, 0.1, 0.1)
        facts = bake_check.compare([a, c], [b, d], tile=8)
        worst = facts["worst"]
        self.assertEqual(worst["view"], 1)
        # Tile corners as fractions of the picture, x right and y up from the bottom left (row 40 from the top of 64).
        self.assertEqual(worst["box"], [0.125, 0.25, 0.25, 0.375])
        self.assertGreater(worst["delta_e"], facts["delta_e_mean"])

    def test_lightness_and_chroma_are_told_apart(self):
        grey, darker = picture((0.5, 0.5, 0.5)), picture((0.25, 0.25, 0.25))
        shading = bake_check.compare([grey], [darker])
        self.assertGreater(shading["lightness_share"], 0.99)
        red, blue = picture((0.2, 0.05, 0.05)), picture((0.05, 0.05, 0.2))
        colour = bake_check.compare([red], [blue])
        self.assertLess(colour["lightness_share"], 0.2)

    def test_sizes_must_match_and_views_pair_up(self):
        with self.assertRaises(ValueError):
            bake_check.compare([picture((0, 0, 0), 64)], [picture((0, 0, 0), 32)])
        with self.assertRaises(ValueError):
            bake_check.compare([picture((0, 0, 0))], [])

    def test_nothing_drawn_says_so(self):
        empty = picture((0, 0, 0), alpha=0.0)
        self.assertEqual(bake_check.compare([empty], [empty.copy()]), {"covered_pixels": 0})


class ViewTests(unittest.TestCase):
    def test_three_unlike_views_framing_the_object(self):
        views = bake_check.views(np.array([1.0, 2.0, 3.0]), 2.0)
        self.assertEqual(len(views), 3)
        directions = [np.asarray(view["look_at"]) - np.asarray(view["location"]) for view in views]
        for direction in directions:
            self.assertAlmostEqual(np.linalg.norm(direction), 2.0 * bake_check.DISTANCE, places=6)
        for i in range(3):
            for j in range(i + 1, 3):
                cosine = directions[i] @ directions[j] / (np.linalg.norm(directions[i]) * np.linalg.norm(directions[j]))
                self.assertLess(cosine, 0.95)
        for view in views:
            self.assertEqual(view["look_at"], [1.0, 2.0, 3.0])


if __name__ == "__main__":
    unittest.main()
