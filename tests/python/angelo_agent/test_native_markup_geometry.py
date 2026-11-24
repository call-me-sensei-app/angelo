# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lines a mark is drawn with (``markup_geometry``), shared by the viewer and the flattened picture. No Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_doc as md, markup_geometry as mg, markup_message as mm  # noqa: E402

IMAGE = "0b9c6f6e-7a4f-4e4f-9d43-0b1d6d1c2e3a"


class GeometryTests(unittest.TestCase):
    def test_an_ellipse_stays_on_its_box(self):
        points = mg.ellipse([100, 50, 200, 100])
        self.assertAlmostEqual(min(p[0] for p in points), 100)
        self.assertAlmostEqual(max(p[0] for p in points), 300)
        self.assertAlmostEqual(min(p[1] for p in points), 50, places=1)
        self.assertAlmostEqual(max(p[1] for p in points), 150, places=1)

    def test_an_arrows_head_points_at_its_end_and_never_outgrows_a_short_shaft(self):
        (shaft, _), (head, _) = mg.arrow([0, 0], [100, 0], 20)
        self.assertEqual(shaft[1], [100, 0])
        self.assertEqual(head[1], [100, 0])
        self.assertTrue(all(p[0] < 100 for p in (head[0], head[2])))
        (_, _), (short, _) = mg.arrow([0, 0], [10, 0], 20)
        self.assertLessEqual(math.hypot(short[0][0] - 10, short[0][1]), 6.01)

    def test_a_pending_drag_and_its_mark_draw_the_same_lines(self):
        d = md.MarkupDoc(IMAGE, (800, 600))
        identity = d.add_mark("rect", box=(10, 20, 30, 40))
        pending = {"kind": "rect", "start": [40, 60], "end": [10, 20], "width": md.DEFAULT_WIDTH}
        self.assertEqual(mg.lines(d.mark(identity), d.size), mg.lines(pending, d.size))

    def test_a_move_offsets_every_point(self):
        d = md.MarkupDoc(IMAGE, (800, 600))
        pen = d.mark(d.add_mark("pen", points=[(0, 0), (10, 30), (20, 0)]))
        moved = mg.lines(pen, d.size, offset=(5, 7))[0][0]
        self.assertEqual(moved, [[x + 5, y + 7] for x, y in pen["points"]])

    def test_widths_match_the_flattened_picture(self):
        d = md.MarkupDoc(IMAGE, (4096, 2048))
        mark = d.mark(d.add_mark("ellipse", box=(10, 10, 100, 100), width=3))
        self.assertEqual(mg.width(mark, d.size), mm.flatten_ops(d)[0][3])

    def test_bounds_cover_the_drawn_lines(self):
        d = md.MarkupDoc(IMAGE, (800, 600))
        arrow = d.mark(d.add_mark("arrow", start=(10, 10), end=(110, 10)))
        x, y, w, h = mg.bounds(arrow, d.size)
        self.assertEqual((x, x + w), (10, 110))
        self.assertGreater(h, 0)


if __name__ == "__main__":
    unittest.main()
