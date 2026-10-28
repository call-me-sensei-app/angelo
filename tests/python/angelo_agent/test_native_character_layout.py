# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Place Character's placement math (character_layout) on synthetic meshes, without Blender."""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.character_layout import (Frame, body_transform, depth_centre, place, uniform_scale,
                                           width_correction)

BODY_BOX = [-100, 50, 100, 950]  # 900 px tall drawn body


def box_mesh(x, y, z, size=(1, 1, 1), n=6):
    """Vertices of a solid box grid centred at (x, y, z)."""
    g = np.linspace(-0.5, 0.5, n)
    v = np.array([[a, b, c] for a in g for b in g for c in g])
    return v * np.array(size) + np.array([x, y, z])


def body(width=0.4, height=1.7, depth=0.25):
    return box_mesh(0, 0, height / 2, (width, depth, height), n=12)


class LayoutTests(unittest.TestCase):
    def test_frame_maps_soles_to_zero_and_height(self):
        frame = Frame(BODY_BOX, 1.7)
        self.assertAlmostEqual(frame.z(950), 0.0)
        self.assertAlmostEqual(frame.z(50), 1.7)
        self.assertAlmostEqual(frame.x(0), 0.0)

    def test_body_transform(self):
        scale, move = body_transform(box_mesh(3, 2, 7, (2, 1, 4)), 1.7)
        v = box_mesh(3, 2, 7, (2, 1, 4)) * np.array(scale) + np.array(move)
        self.assertAlmostEqual(v[:, 2].min(), 0.0)
        self.assertAlmostEqual(v[:, 2].max(), 1.7)
        self.assertAlmostEqual((v[:, 0].min() + v[:, 0].max()) / 2, 0.0)

    def test_uniform_scale_is_the_geometric_mean(self):
        mesh = box_mesh(0, 0, 0, (2, 1, 1))
        self.assertAlmostEqual(uniform_scale(mesh, (0, 1, 0, 2)), np.sqrt(0.5 * 2.0))

    def test_part_lands_centred_on_its_box(self):
        frame = Frame(BODY_BOX, 1.7)
        coat_box = [-120, 250, 120, 650]
        mesh = box_mesh(5, 5, 5, (3, 1, 5))
        scale, move, _ = place("ACCESSORY", mesh, coat_box, frame, [], body())
        v = mesh * np.array(scale) + np.array(move)
        xmin, xmax, zmin, zmax = frame.box(coat_box)
        self.assertAlmostEqual((v[:, 0].min() + v[:, 0].max()) / 2, (xmin + xmax) / 2, places=6)
        self.assertAlmostEqual((v[:, 2].min() + v[:, 2].max()) / 2, (zmin + zmax) / 2, places=6)

    def test_clothing_follows_a_narrower_generated_body(self):
        frame = Frame(BODY_BOX, 1.7)
        profile = [[y, -100, 100] for y in range(60, 940, 20)]  # drawn body: 200 px = 0.378 m wide
        narrow = body(width=0.30)                                 # generated body is narrower
        target = frame.box([-120, 250, 120, 650])
        c = width_correction("OUTER", target, frame, profile, narrow)
        self.assertAlmostEqual(c, 0.30 / (200 * frame.k), places=2)
        self.assertEqual(width_correction("ACCESSORY", target, frame, profile, narrow), 1.0)
        very_narrow = body(width=0.05)
        self.assertEqual(width_correction("OUTER", target, frame, profile, very_narrow), 0.7, "clamped")

    def test_depth_follows_the_body_under_the_footprint(self):
        hand = box_mesh(0.5, 0.3, 1.0, (0.1, 0.1, 0.1))
        torso = box_mesh(0.0, 0.0, 1.0, (0.4, 0.25, 0.6))
        combined = np.vstack([torso, hand])
        self.assertAlmostEqual(depth_centre(combined, (0.45, 0.55, 0.95, 1.05)), 0.3, places=6)
        self.assertAlmostEqual(depth_centre(combined, (5, 6, 0.95, 1.05)), depth_centre(combined, (-9, 9, 0.95, 1.05)))


    def test_a_box_on_the_character_image_places_like_the_same_box_on_the_sheet(self):
        """A part fitted to the character image is boxed in that image's pixels; the body profile stays the sheet's."""
        sheet = Frame(BODY_BOX, 1.7)
        image = Frame([380, 120, 420, 1820], 1.7)        # another scale and origin: 1700 px for the same 1.7 m
        profile = [[y, -100, 100] for y in range(60, 940, 20)]
        narrow = body(width=0.3)
        coat = box_mesh(0, 0, 0, (2, 1, 3))
        on_sheet = [-120, 200, 120, 600]
        x0, x1, z0, z1 = sheet.box(on_sheet)
        on_image = [image.cx + x0 / image.k, image.sole - z1 / image.k, image.cx + x1 / image.k, image.sole - z0 / image.k]
        expected = place("OUTER", coat, on_sheet, sheet, profile, narrow)
        found = place("OUTER", coat, on_image, image, profile, narrow, sheet_frame=sheet)
        for a, b in zip(expected, found):
            np.testing.assert_allclose(np.asarray(list(a.values()) if isinstance(a, dict) else a, float),
                                       np.asarray(list(b.values()) if isinstance(b, dict) else b, float), atol=1e-9)
        self.assertLess(found[2]["width_correction"], 1.0, "the coat still follows the narrower generated body")


if __name__ == "__main__":
    unittest.main()
