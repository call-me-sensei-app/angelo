# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Split Sheet geometry (flow_parts) on synthetic parts sheets, without Blender."""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.flow_parts import SheetError, alpha_mask, cell_grid, split_sheet

GREY = (214, 214, 214)
SLOTS = [{"id": "body", "kind": "BODY"}, {"id": "hair", "kind": "HAIR"},
         {"id": "outer", "kind": "OUTER"}, {"id": "accessories", "kind": "ACCESSORY", "items": "split"}]
EDGES = [0, 120, 230, 360, 480]  # Columns of different widths, as generated sheets have.
HEIGHT = 400


def sheet(lines=True):
    """A 4-column x 2-row white sheet with grey separators and simple drawn parts."""
    image = np.full((HEIGHT, EDGES[-1], 3), 255, np.uint8)
    if lines:
        for x in EDGES[1:-1]:
            image[:, x - 1:x + 2] = GREY
        image[HEIGHT // 2 - 1:HEIGHT // 2 + 2, :] = GREY
    for row in (0, 1):
        top = row * HEIGHT // 2
        # Body: a tall dark figure in column 0.
        image[top + 15:top + 190, 40:80] = (90, 70, 60)
        # Outer layer: a shaded white coat (opaque) with an opening to the outside at its top.
        x0, x1 = EDGES[2] + 20, EDGES[3] - 20
        coat = np.linspace(232, 244, x1 - x0).astype(np.uint8)
        image[top + 40:top + 170, x0:x1] = coat[None, :, None]
        image[top + 40:top + 170, x0:x0 + 3] = (40, 40, 40)
        image[top + 40:top + 170, x1 - 3:x1] = (40, 40, 40)
        image[top + 40:top + 43, x0:x1] = (40, 40, 40)
        image[top + 40:top + 43, x0 + 50:x0 + 70] = 255          # a gap in the outline: the collar opening
        image[top + 43:top + 90, x0 + 50:x0 + 70] = 255          # flat white channel reaching the gap
    # Accessories (front row only): a ring with a flat white hole, and a separate block.
    cx, cy = EDGES[3] + 40, 60
    yy, xx = np.mgrid[0:HEIGHT, 0:EDGES[-1]]
    radius = np.hypot(yy - cy, xx - cx)
    image[(radius >= 14) & (radius <= 22)] = (200, 160, 40)
    image[120:150, EDGES[3] + 60:EDGES[3] + 100] = (60, 90, 140)
    return image


class GridTest(unittest.TestCase):
    def test_detects_unequal_columns(self):
        xb, yb, found = cell_grid(sheet(), 4, 2)
        self.assertTrue(found)
        self.assertEqual([abs(a - b) <= 1 for a, b in zip(xb, EDGES)], [True] * 5)
        self.assertLessEqual(abs(yb[1] - HEIGHT // 2), 1)

    def test_falls_back_to_an_even_split_without_lines(self):
        xb, yb, found = cell_grid(sheet(lines=False), 4, 2)
        self.assertFalse(found)
        self.assertEqual(xb, [0, 120, 240, 360, 480])

    def test_a_wide_grey_figure_is_not_a_separator(self):
        image = sheet()
        image[:, EDGES[1] + 20:EDGES[1] + 60] = GREY      # a grey full-height body suit, wider than a line
        xb, _, found = cell_grid(image, 4, 2)
        self.assertTrue(found)
        self.assertLessEqual(abs(xb[1] - EDGES[1]), 1)


class AlphaTest(unittest.TestCase):
    def test_openings_and_flat_holes_are_transparent_and_shaded_white_is_opaque(self):
        image = sheet()
        coat_cell = image[6:HEIGHT // 2 - 6, EDGES[2] + 6:EDGES[3] - 6].astype(np.int16)
        alpha, foreground = alpha_mask(coat_cell)
        x0 = 20 - 6
        self.assertLess(alpha[60 - 6, x0 + 60], 0.5, "the channel open to the outside is background")
        self.assertGreater(alpha[120 - 6, x0 + 30], 0.5, "shaded white fabric stays opaque")
        ring_cell = image[6:HEIGHT // 2 - 6, EDGES[3] + 6:EDGES[4] - 6].astype(np.int16)
        ring_alpha, _ = alpha_mask(ring_cell)
        self.assertLess(ring_alpha[60 - 6, 40 - 6], 0.5, "a flat white enclosed hole is transparent")
        self.assertGreater(ring_alpha[60 - 6, 40 + 18 - 6], 0.5, "the ring itself is opaque")


class SplitTest(unittest.TestCase):
    def test_parts_per_slot_with_centred_boxes_and_empty_columns_skipped(self):
        result = split_sheet(sheet(), SLOTS)
        self.assertTrue(result["grid_found"])
        slots = [(p["slot"], p["item"]) for p in result["parts"]]
        self.assertEqual(slots, [("body", 0), ("outer", 0), ("accessories", 0), ("accessories", 1)])
        body = result["parts"][0]
        width = EDGES[1] - 2 * 6
        self.assertAlmostEqual(body["front"]["box"][0], 40 - 6 - width / 2, delta=1)
        self.assertEqual(body["front"]["box"][1], 15 - 6)
        self.assertEqual(body["front"]["rgba"].shape[2], 4)
        self.assertIsNotNone(body["back"], "a whole part keeps its back view")
        profile = body["profile"]
        self.assertGreater(len(profile), 10)
        self.assertTrue(all(abs((x1 - x0) - 39) <= 1 for _, x0, x1 in profile), "the drawn body is 40 px wide")

    def test_split_items_have_their_own_boxes_and_no_back_view(self):
        parts = [p for p in split_sheet(sheet(), SLOTS)["parts"] if p["slot"] == "accessories"]
        self.assertEqual(len(parts), 2)
        ring, block = parts
        self.assertLess(ring["front"]["box"][3], block["front"]["box"][1], "items are ordered top to bottom")
        self.assertIsNone(ring["back"])

    def test_items_are_separated_by_their_pixels_not_their_boxes(self):
        """A scarf whose tail runs under a bag: the scarf's box contains the bag, their pixels don't touch."""
        image = sheet()
        image[6:HEIGHT // 2 - 6, EDGES[3] + 4:EDGES[4] - 4] = 255
        x0 = EDGES[3] + 10
        image[20:170, x0:x0 + 14] = (120, 60, 120)                 # the scarf, hanging down
        image[160:170, x0:x0 + 100] = (120, 60, 120)               # and its tail along the bottom
        image[100:145, x0 + 55:x0 + 95] = (110, 70, 40)            # a bag above the tail, inside the scarf's box
        parts = [p for p in split_sheet(image, SLOTS)["parts"] if p["slot"] == "accessories"]
        self.assertEqual(len(parts), 2)
        scarf, bag = parts
        sx0, sy0, sx1, sy1 = scarf["front"]["box"]
        bx0, by0, bx1, by1 = bag["front"]["box"]
        self.assertTrue(sx0 < bx0 and bx1 < sx1 and sy0 < by0 and by1 < sy1, "the bag lies inside the scarf's box")
        # Each cut-out holds only its own item: the bag's pixels in the scarf's cut-out are transparent.
        top, left = int(by0 - sy0) + 5, int(bx0 - sx0) + 5
        self.assertTrue((scarf["front"]["rgba"][top:top + 20, left:left + 20, 3] == 0).all())
        self.assertTrue((bag["front"]["rgba"][5:25, 5:25, 3] == 255).all())

    def test_a_separator_the_inset_missed_is_not_an_item(self):
        image = sheet()
        image[HEIGHT // 2 - 9:HEIGHT // 2 - 7, EDGES[3]:EDGES[4]] = GREY   # a line drawn a little off the detected one
        parts = [p for p in split_sheet(image, SLOTS)["parts"] if p["slot"] == "accessories"]
        self.assertEqual(len(parts), 2, "the ring and the block, not the line")

    def test_a_piece_inside_another_items_loop_belongs_to_it(self):
        """A pendant hanging inside its chain's loop is one item, however far from the chain."""
        image = sheet()
        image[6:HEIGHT // 2 - 6, EDGES[3] + 4:EDGES[4] - 4] = 255
        cx, cy = EDGES[3] + 60, 90
        yy, xx = np.mgrid[0:HEIGHT, 0:EDGES[-1]]
        ring = np.hypot(yy - cy, xx - cx)
        image[(ring >= 40) & (ring <= 43) & (yy < HEIGHT // 2)] = (180, 150, 40)   # the chain's loop
        image[cy - 6:cy + 6, cx - 6:cx + 6] = (150, 20, 20)                     # the pendant, 30 px inside it
        parts = [p for p in split_sheet(image, SLOTS)["parts"] if p["slot"] == "accessories"]
        self.assertEqual(len(parts), 1)

    def test_a_thin_item_across_the_cell_is_kept(self):
        """A staff drawn edge to edge across the middle of the cell is an item, not a separator."""
        image = sheet()
        image[6:HEIGHT // 2 - 6, EDGES[3] + 4:EDGES[4] - 4] = 255
        image[95:99, EDGES[3]:EDGES[4]] = (110, 70, 40)
        parts = [p for p in split_sheet(image, SLOTS)["parts"] if p["slot"] == "accessories"]
        self.assertEqual(len(parts), 1)

    def test_a_sheet_without_a_body_is_refused(self):
        image = sheet()
        image[:, :EDGES[1] - 1] = 255
        with self.assertRaises(SheetError):
            split_sheet(image, SLOTS)

    def test_float_pixels_are_accepted(self):
        result = split_sheet(sheet().astype(np.float32) / 255.0, SLOTS)
        self.assertEqual(len(result["parts"]), 4)


if __name__ == "__main__":
    unittest.main()
