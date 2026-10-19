# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.layout import AreaRect, covers_height, is_edge_dock, plan_dock


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

    def test_discontinuous_edge_column_is_rejected(self):
        self.assertFalse(covers_height([self.areas[3]], 0, 1000))
        with self.assertRaises(ValueError):
            plan_dock(self.areas[:-1], "RIGHT")


if __name__ == "__main__":
    unittest.main()
