# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resize a picture (``markup_resize``): a crop drawn on it and a scale, planned in its pixels. No Blender here."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_resize as mr  # noqa: E402


class PlanTests(unittest.TestCase):
    def test_no_crop_and_full_scale_is_the_picture_itself(self):
        plan = mr.plan((1600, 900))
        self.assertEqual((plan["crop"], plan["size"], plan["changed"]), ([0, 0, 1600, 900], (1600, 900), False))

    def test_scale_shrinks_keeping_the_shape(self):
        self.assertEqual(mr.plan((1600, 900), scale=50)["size"], (800, 450))
        self.assertEqual(mr.plan((1600, 900), scale=25)["size"], (400, 225))

    def test_a_crop_is_clipped_to_the_picture_and_whole_pixels(self):
        plan = mr.plan((1000, 800), crop=(900.4, 700.6, 300, 300))
        self.assertEqual(plan["crop"], [900, 701, 100, 99])
        self.assertTrue(plan["changed"])

    def test_a_crop_drawn_backwards_is_the_same_crop(self):
        self.assertEqual(mr.plan((1000, 800), crop=(300, 300, -200, -100))["crop"], [100, 200, 200, 100])

    def test_a_sliver_crop_is_refused(self):
        with self.assertRaises(ValueError):
            mr.plan((1000, 800), crop=(10, 10, 3, 400))

    def test_the_output_never_passes_the_encoders_limit(self):
        plan = mr.plan((8000, 2000))
        self.assertEqual(plan["size"], (mr.MAX_SIDE, 1024))
        self.assertTrue(plan["changed"])

    def test_unknown_scales_are_refused(self):
        with self.assertRaises(ValueError):
            mr.plan((100, 100), scale=33)

    def test_the_label_names_the_output_size(self):
        self.assertEqual(mr.plan((1600, 900), scale=50)["label"], (mr.SIZE_LABEL, {"width": 800, "height": 450}))


class PixelTests(unittest.TestCase):
    def test_rgba_png_keeps_alpha_and_reads_back(self):
        import zlib
        rows = bytes([255, 0, 0, 0, 0, 255, 0, 128, 0, 0, 255, 255, 9, 9, 9, 9])   # 2 x 2, bottom row first.
        png = mr.rgba_png(rows, 2, 2)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png[25], 6)   # Colour type: RGBA.
        start = png.index(b"IDAT") + 4
        length = int.from_bytes(png[start - 8:start - 4], "big")
        data = zlib.decompress(png[start:start + length])
        self.assertEqual(data, b"\x00" + rows[8:] + b"\x00" + rows[:8])   # Top row first in the file.

    def test_rgba_png_refuses_a_buffer_of_another_size(self):
        with self.assertRaises(ValueError):
            mr.rgba_png(b"\x00" * 15, 2, 2)

    def test_bilinear_keeps_flat_colour_and_the_requested_size(self):
        import numpy as np
        grid = np.full((90, 160, 4), 0.25, dtype=np.float32)
        out = mr.bilinear(grid, 40, 23)
        self.assertEqual(out.shape, (23, 40, 4))
        self.assertTrue(np.allclose(out, 0.25))

    def test_bilinear_keeps_the_corners(self):
        import numpy as np
        grid = np.zeros((4, 4, 4), dtype=np.float32)
        grid[0, 0] = 1.0
        out = mr.bilinear(grid, 7, 7)
        self.assertAlmostEqual(float(out[0, 0, 0]), 1.0)
        self.assertAlmostEqual(float(out[-1, -1, 0]), 0.0)


if __name__ == "__main__":
    unittest.main()
