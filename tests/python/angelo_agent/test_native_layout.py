# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.layout import (GAP, MIN_EDITOR_WIDTH, MIN_VIEWPORT_WIDTH, AreaRect, border_gap, covers_height,
                                 is_edge_dock, neighbour_room, plan_dock)

ROOT = Path(__file__).resolve().parents[3]


class NativeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.areas = [AreaRect(1, "VIEW_3D", 0, 100, 1400, 900),
                      AreaRect(2, "DOPESHEET_EDITOR", 0, 0, 1400, 99),
                      AreaRect(3, "PROPERTIES", 1401, 0, 399, 699),
                      AreaRect(4, "OUTLINER", 1401, 700, 399, 300)]

    def test_right_dock_reserves_a_new_column_after_properties(self):
        plan = plan_dock(self.areas, "RIGHT", requested_width=400)
        self.assertTrue(plan.expand)
        self.assertEqual(plan.width, 400)
        self.assertEqual([a.key for a in plan.column], [3, 4])

    def test_left_dock_uses_viewport_and_timeline(self):
        plan = plan_dock(self.areas, "LEFT", requested_width=400)
        self.assertFalse(plan.expand)
        self.assertEqual([a.key for a in plan.column], [2, 1])

    def test_an_old_viewport_chat_is_not_the_rightmost_dock(self):
        middle = AreaRect(5, "CONSOLE", 1000, 100, 399, 900)
        self.assertFalse(is_edge_dock(self.areas + [middle], 5, "RIGHT"))

    def test_outer_chat_spans_the_full_editor_height(self):
        chat = AreaRect(5, "CONSOLE", 1801, 0, 400, 1000)
        self.assertTrue(is_edge_dock(self.areas + [chat], 5, "RIGHT"))
        self.assertFalse(is_edge_dock(self.areas + [chat], 5, "LEFT"))

    def test_insufficient_room_never_overlays_or_narrows_properties(self):
        narrow = [AreaRect(1, "VIEW_3D", 0, 0, 500, 800),
                  AreaRect(2, "PROPERTIES", 501, 0, 299, 800)]
        with self.assertRaises(ValueError):
            plan_dock(narrow, "RIGHT")

    def test_a_wide_column_gives_the_chat_its_room_when_the_viewport_has_none(self):
        # Blender's Close Area on the docked chat left this layout (1.0.3 GUI suite): Show must still dock the chat.
        squeezed = [AreaRect(1, "VIEW_3D", 2, 124, 356, 1258), AreaRect(2, "DOPESHEET_EDITOR", 2, 23, 356, 98),
                    AreaRect(3, "PROPERTIES", 361, 23, 1931, 1103), AreaRect(4, "OUTLINER", 361, 1129, 1931, 253)]
        plan = plan_dock(squeezed, "RIGHT")
        self.assertFalse(plan.expand, "taken from the wide column, not the 3D view")
        self.assertGreaterEqual(plan.width, 240)
        self.assertLessEqual(plan.width, 1931 - 160)
        self.assertEqual([a.key for a in plan.column], [3, 4])

    def test_discontinuous_edge_column_is_rejected(self):
        self.assertFalse(covers_height([self.areas[3]], 0, 1000))
        with self.assertRaises(ValueError):
            plan_dock(self.areas[:-1], "RIGHT")


class BorderGapTests(unittest.TestCase):
    """Neighbouring editors lie ``2 * pad - 1`` apart, pad = ``int(max(border_width * scale, scale))``
    (Blender's ``area_calc_totrct``); every layout check takes that tolerance, never a fixed one."""

    # Recorded by the Mac 1 dock-after-scale diagnostic (2026-10-23), the same window at UI scale 2: Properties ends
    # at 1107, the Outliner starts at 1114. Showing the chat here failed on shipped 1.0.4 ("must span the window height").
    SCALE_TWO = [AreaRect(1, "DOPESHEET_EDITOR", 4, 47, 352, 90), AreaRect(2, "VIEW_3D", 4, 144, 352, 1211),
                 AreaRect(3, "PROPERTIES", 363, 47, 1927, 1060), AreaRect(4, "OUTLINER", 363, 1114, 1927, 241)]

    def test_the_default_border_and_scale_keep_the_old_tolerance(self):
        self.assertEqual(border_gap(2, 1.0), GAP)
        self.assertEqual(border_gap(1, 1.0), GAP, "never tighter than before")

    def test_the_tolerance_covers_blenders_padding_at_any_scale_and_border(self):
        for border in range(1, 11):
            for scale in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0):
                pad = int(max(border * scale, scale))
                with self.subTest(border=border, scale=scale):
                    self.assertGreaterEqual(border_gap(border, scale), 2 * pad - 1)

    def test_a_scale_two_column_spans_the_window_with_its_own_tolerance(self):
        with self.assertRaises(ValueError):   # The fixed tolerance: what 1.0.4 did.
            plan_dock(self.SCALE_TWO, "RIGHT")
        plan = plan_dock(self.SCALE_TWO, "RIGHT", gap=border_gap(2, 2.0))
        self.assertEqual([a.key for a in plan.column], [3, 4])
        self.assertGreaterEqual(plan.width, 240)

    def test_a_wide_border_at_scale_one_too(self):
        pad = int(max(3 * 1.0, 1.0))   # Border Width 3: neighbours 5 px apart.
        areas = [AreaRect(1, "VIEW_3D", 3, 3, 1400, 994),
                 AreaRect(2, "PROPERTIES", 1408, 3, 389, 694), AreaRect(3, "OUTLINER", 1408, 697 + 2 * pad - 1, 389, 297)]
        self.assertFalse(covers_height(areas[1:], 3, 997))
        self.assertTrue(covers_height(areas[1:], 3, 997, border_gap(3, 1.0)))

    def test_docking_has_no_fixed_tolerance(self):
        docking = (ROOT / "scripts/addons_core/angelo_agent/docking.py").read_text()
        self.assertNotIn("GAP", docking, "every check takes gap(): the live border width and scale")
        self.assertIn("preferences.view.border_width, preferences.system.ui_scale", docking)
        ui = (ROOT / "scripts/addons_core/angelo_agent/ui.py").read_text()
        self.assertNotIn("is_edge_dock(rects, pointer, edge)", ui)
        self.assertIn("docking.is_edge_dock(rects, pointer, edge, docking.gap())", ui)

    def test_hide_gives_back_exactly_the_strip_show_took(self):
        """The chat's own width, not the border before it (that grew the 3D view 3 px at scale 1, 7 px at 2 per cycle)."""
        collapse = (ROOT / "scripts/addons_core/angelo_agent/docking.py").read_text().split(
            "\ndef collapse_chat(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("restore_delta = chat.width if restore_properties else 0", collapse)


def rects(rows):
    return [AreaRect(index, *row) for index, row in enumerate(rows)]


class PlanMatchesTheExecutorTests(unittest.TestCase):
    """A planned dock is one the executor performs: an expand takes the strip from each editor bordering the column
    (``docking.transfer_strip`` refuses below 160 px), so the room is counted there, never from the window's edge.
    Layouts recorded by the Mac 1 dock sweep (2026-10-23, Show in every Manual tab at UI scale 1 and 2)."""

    # UI scale 2 after the chat was hidden: Close Area gave its room to Properties (998 px); the 3D view beside it is
    # 609 px. The window-edge count promised 928 px and the strip failed ("Not enough space beside Properties").
    ANIMATION_2 = rects([("VIEW_3D", 4, 601, 665, 754), ("DOPESHEET_EDITOR", 4, 47, 1281, 547),
                         ("VIEW_3D", 676, 601, 609, 754), ("PROPERTIES", 1292, 47, 998, 1062),
                         ("OUTLINER", 1292, 1116, 998, 239)])
    # Held out (not used to develop the rule): a three-row column, a node layout, a narrow 3D view, and scale 1.
    HELD_OUT = {
        "scripting_2": rects([("INFO", 4, 47, 663, 212), ("TEXT_EDITOR", 674, 47, 611, 1308),
                              ("VIEW_3D", 4, 858, 663, 497), ("CONSOLE", 4, 266, 663, 585),
                              ("PROPERTIES", 1292, 47, 998, 765), ("OUTLINER", 1292, 819, 998, 274),
                              ("OUTLINER", 1292, 1100, 998, 255)]),
        "geometry_nodes_2": rects([("NODE_EDITOR", 4, 144, 1281, 604), ("SPREADSHEET", 4, 755, 778, 600),
                                   ("DOPESHEET_EDITOR", 4, 47, 1281, 90), ("VIEW_3D", 789, 755, 496, 600),
                                   ("PROPERTIES", 1292, 47, 998, 1062), ("OUTLINER", 1292, 1116, 998, 239)]),
        "uv_editing_2": rects([("IMAGE_EDITOR", 4, 47, 936, 1308), ("VIEW_3D", 947, 47, 337, 1308),
                               ("PROPERTIES", 1291, 47, 999, 1062), ("OUTLINER", 1291, 1116, 999, 239)]),
        "texture_paint_1": rects([("PROPERTIES", 1886, 23, 406, 1106), ("OUTLINER", 1886, 1132, 406, 250),
                                  ("IMAGE_EDITOR", 2, 23, 940, 1359), ("VIEW_3D", 945, 23, 938, 1359)]),
    }
    SCALE = {"scripting_2": 2.0, "geometry_nodes_2": 2.0, "uv_editing_2": 2.0, "texture_paint_1": 1.0}

    def assert_performable(self, areas, gap):
        plan = plan_dock(areas, "RIGHT", gap=gap)
        self.assertGreaterEqual(plan.width, 240)
        column_x = min(a.x for a in plan.column)
        bottom, top = min(a.y for a in plan.column), max(a.top for a in plan.column)
        givers = ([a for a in areas if a not in plan.column and abs(a.right - column_x) <= gap
                   and a.y >= bottom - gap and a.top <= top + gap] if plan.expand else list(plan.column))
        for area in givers:
            floor = MIN_VIEWPORT_WIDTH if plan.expand and area.kind == "VIEW_3D" else MIN_EDITOR_WIDTH
            self.assertGreaterEqual(area.width - plan.width, floor, area)
        return plan

    def test_the_wide_column_gives_the_room_its_neighbour_lacks(self):
        gap = border_gap(2, 2.0)
        self.assertLess(neighbour_room(self.ANIMATION_2, self.ANIMATION_2[3:], gap), 594)
        plan = self.assert_performable(self.ANIMATION_2, gap)
        self.assertFalse(plan.expand, "taken from the 998 px column, not the 609 px 3D view")

    def test_held_out_layouts_plan_what_the_executor_performs(self):
        for name, areas in self.HELD_OUT.items():
            with self.subTest(layout=name):
                self.assert_performable(areas, border_gap(2, self.SCALE[name]))

    def test_neighbours_that_do_not_line_up_give_no_room(self):
        column = [AreaRect(3, "PROPERTIES", 1401, 0, 399, 1000)]
        half = [AreaRect(1, "VIEW_3D", 0, 0, 1397, 500)]   # Covers only the column's lower half.
        self.assertEqual(neighbour_room(half + column, column), 0)


if __name__ == "__main__":
    unittest.main()
