# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A canvas's thin fills and strokes cover at least one device pixel at any interface scale (fail-first).

Under a fractional scale below 1 a fill narrower than one device pixel covered no pixel centre, so the rasteriser
drew nothing: the composer's 1-unit caret vanished at most positions at scale 0.875 (Xvfb at 75 dpi;
``test_blender_chat_scroll_caret``'s "drawn caret sits between the glyphs", 2.0.0; a screenshot pair showed no
pixel of it), and any other hairline could do the same. ``canvas_pixels`` keeps the thin side one pixel wide on the
pixel grid; ``chat_view.shape`` and ``chat_view.stroke`` (every canvas's fills and strokes) go through it."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import canvas_pixels  # noqa: E402


def matrix(scale, x_offset=0.0, y_offset=0.0, mirrored=False):
    """A model-view matrix as the canvases build it: the scale, and the right-to-left mirror about ``x_offset``."""
    sx = -scale if mirrored else scale
    return [[sx, 0, 0, x_offset], [0, scale, 0, y_offset], [0, 0, 1, 0], [0, 0, 0, 1]]


def device(start, size, scale, offset):
    a, b = scale * start + offset, scale * (start + size) + offset
    return min(a, b), max(a, b)


def pixel_centres(left, right):
    """How many device pixel centres (k + 0.5) the span [left, right) covers: what a rasteriser fills."""
    return max(0, math.ceil(right - 0.5) - math.ceil(left - 0.5))


SCALES = [0.5 + 0.0125 * i for i in range(40)]   # 0.5 .. 0.9875: every fractional scale a person can set below 1.
STARTS = [i / 7 for i in range(70)]               # Layout positions with every fractional part.


class CoverTests(unittest.TestCase):
    def test_a_one_unit_fill_covers_one_pixel_at_every_scale_below_1(self):
        missed = []
        for scale in SCALES:
            for mirrored, offset in ((False, 0.0), (False, 13.3), (True, 700.0), (True, 641.7)):
                for start in STARTS:
                    m = matrix(scale, offset, 0.4, mirrored)
                    x, y, width, height = canvas_pixels.covered(start, 10.0, 1.0, 1.0, m)
                    for (s, size, sc, off) in ((x, width, m[0][0], offset), (y, height, scale, 0.4)):
                        left, right = device(s, size, sc, off)
                        if pixel_centres(left, right) != 1 or abs((right - left) - 1) > 1e-9:
                            missed.append((round(scale, 4), mirrored, start, round(left, 3), round(right, 3)))
        self.assertEqual(missed[:5], [], "%d thin fills cover no pixel or more than one" % len(missed))

    def test_it_stays_where_it_was_laid_out(self):
        for scale in SCALES:
            for start in STARTS:
                m = matrix(scale, 7.25)
                x, _y, width, _h = canvas_pixels.covered(start, 0.0 + 10, 1.0, 20.0, m)
                before = sum(device(start, 1.0, scale, 7.25)) / 2
                after = sum(device(x, width, scale, 7.25)) / 2
                self.assertLessEqual(abs(after - before), 0.5 + 1e-9, (scale, start))

    def test_a_pixel_or_more_is_drawn_as_laid_out(self):
        for scale in (1.0, 1.37, 2.0, 0.875):
            m = matrix(scale, 3.0)
            rect = (12.3, 40.0, 8.0, 2.0 / scale)
            self.assertEqual(canvas_pixels.covered(*rect, m), rect)
        self.assertEqual(canvas_pixels.covered(5.5, 6.5, 1.0, 1.0, matrix(1.0)), (5.5, 6.5, 1.0, 1.0))
        self.assertEqual(canvas_pixels.covered(5.5, 6.5, 1.0, 1.0, matrix(2.0)), (5.5, 6.5, 1.0, 1.0))

    def test_nothing_is_still_nothing(self):
        self.assertEqual(canvas_pixels.covered(4.0, 4.0, 0.0, 3.0, matrix(0.5)), (4.0, 4.0, 0.0, 3.0))

    def test_a_stroke_is_at_least_one_pixel_thick(self):
        for scale in SCALES:
            self.assertGreaterEqual(canvas_pixels.thick(1.2, matrix(scale)) * scale, 1 - 1e-9, scale)
        self.assertEqual(canvas_pixels.thick(1.2, matrix(1.0)), 1.2)
        self.assertEqual(canvas_pixels.thick(1.2, matrix(0.9, mirrored=True)), max(1.2, 1 / 0.9))


class CanvasUsesItTests(unittest.TestCase):
    """Every canvas fills and strokes through chat_view.shape/stroke; both go through canvas_pixels."""

    def function(self, name):
        source = (ADDON / "chat_view.py").read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(source, node)

    def test_shape_covers_a_pixel_before_it_builds_its_quad(self):
        body = self.function("shape")
        self.assertIn("canvas_pixels.covered(", body)
        self.assertLess(body.index("canvas_pixels.covered("), body.index("batch_for_shader("))
        self.assertLess(body.index("note_shape("), body.index("canvas_pixels.covered("),
                        "The fixtures' record keeps the shape as laid out")

    def test_stroke_is_at_least_a_pixel_thick(self):
        body = self.function("stroke")
        self.assertIn("canvas_pixels.thick(", body)
        self.assertLess(body.index("canvas_pixels.thick("), body.index("batch_for_shader("))


if __name__ == "__main__":
    unittest.main()
