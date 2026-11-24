# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's maths (``markup_view``): fit, zoom about the pointer, pan, and region pixels <-> picture pixels.

Blender's region pixels have their origin bottom-left with y up; a picture's pixels have theirs top-left with y down.
Every mark is stored in picture pixels, so these two directions must agree at every zoom. No Blender here.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_view as mv  # noqa: E402


def close(test, a, b, places=6):
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=places)


class FitTests(unittest.TestCase):
    def test_a_large_picture_fits_inside_the_margins_centred(self):
        view = mv.View((4000, 2000), (0, 0, 1000, 800), margins=(40, 40, 40, 120))
        self.assertAlmostEqual(view.scale, 920 / 4000)
        left, bottom = view.to_region(0, 2000)
        right, top = view.to_region(4000, 0)
        self.assertAlmostEqual(right - left, 920)
        self.assertAlmostEqual((left + right) / 2, 500)
        self.assertAlmostEqual((bottom + top) / 2, 120 + (800 - 160) / 2)

    def test_a_small_picture_is_shown_at_its_own_size_not_blown_up(self):
        view = mv.View((200, 100), (0, 0, 1000, 800))
        self.assertEqual(view.scale, 1.0)

    def test_the_region_offset_is_respected(self):
        view = mv.View((400, 400), (300, 50, 400, 400), margins=(0, 0, 0, 0))
        close(self, view.to_region(0, 400), (300, 50))
        close(self, view.to_region(400, 0), (700, 450))


class TransformTests(unittest.TestCase):
    def test_region_and_picture_pixels_round_trip_with_y_flipped(self):
        view = mv.View((800, 600), (0, 0, 1200, 900))
        for point in ((0, 0), (800, 600), (123.5, 456.25)):
            close(self, view.to_image(*view.to_region(*point)), point)
        top_left = view.to_region(0, 0)
        bottom_left = view.to_region(0, 600)
        self.assertGreater(top_left[1], bottom_left[1])   # The picture's top is up on screen.

    def test_zoom_keeps_the_point_under_the_pointer(self):
        view = mv.View((800, 600), (0, 0, 1200, 900))
        pointer = (700.0, 300.0)
        under = view.to_image(*pointer)
        for factor in (1.25, 4.0, 0.5, 0.1):
            view.zoom_at(*pointer, factor)
            close(self, view.to_image(*pointer), under, places=4)

    def test_zoom_is_bounded(self):
        view = mv.View((800, 600), (0, 0, 1200, 900))
        view.zoom_at(10, 10, 1e9)
        self.assertEqual(view.scale, mv.MAX_SCALE)
        view.zoom_at(10, 10, 1e-9)
        self.assertEqual(view.scale, mv.MIN_SCALE)

    def test_pan_moves_the_picture_with_the_pointer(self):
        view = mv.View((800, 600), (0, 0, 1200, 900))
        before = view.to_region(100, 100)
        view.pan(30, -20)
        close(self, view.to_region(100, 100), (before[0] + 30, before[1] - 20))

    def test_fit_after_zoom_and_pan_returns_to_the_fitted_view(self):
        view = mv.View((800, 600), (0, 0, 1200, 900))
        fitted = (view.scale, view.to_region(0, 0))
        view.zoom_at(5, 5, 3)
        view.pan(100, 100)
        view.fit()
        self.assertEqual((view.scale, view.to_region(0, 0)), fitted)

    def test_a_resized_region_refits_unless_the_person_zoomed(self):
        view = mv.View((800, 600), (0, 0, 1200, 900))
        view.set_region((0, 0, 600, 450))
        self.assertAlmostEqual(view.scale, view.fitted_scale())
        view.zoom_at(10, 10, 2)
        zoomed = view.scale
        view.set_region((0, 0, 1200, 900))
        self.assertEqual(view.scale, zoomed)

    def test_contains_and_picture_rect(self):
        view = mv.View((800, 600), (0, 0, 1200, 900))
        self.assertTrue(view.contains_region(*view.to_region(400, 300)))
        self.assertFalse(view.contains_region(*view.to_region(-5, 300)))
        x, y, w, h = view.picture_rect()
        close(self, (x, y), view.to_region(0, 600))
        self.assertAlmostEqual(w, 800 * view.scale)
        self.assertAlmostEqual(h, 600 * view.scale)

    def test_fill_covers_a_region_of_the_pictures_shape_exactly(self):
        view = mv.View((4000, 2000), (0, 0, 1000, 500), margins=(0, 0, 0, 0))
        view.fill()
        close(self, view.to_region(0, 2000), (0, 0))
        close(self, view.to_region(4000, 0), (1000, 500))

    def test_screen_lengths_become_picture_lengths(self):
        view = mv.View((4000, 2000), (0, 0, 1000, 800))
        self.assertAlmostEqual(view.image_length(8), 8 / view.scale)


if __name__ == "__main__":
    unittest.main()
