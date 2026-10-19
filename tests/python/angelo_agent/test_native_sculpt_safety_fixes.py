# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure regression checks for destination budgets and bounded depth precision."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.sculpt_spec import multires_working_points, depth_match_tolerance


class SculptSafetyTests(unittest.TestCase):
    def test_actual_grid_storage_not_displayed_level(self):
        self.assertEqual(multires_working_points(8, 24, 0), 8)
        self.assertEqual(multires_working_points(8, 24, 1), 96)
        self.assertEqual(multires_working_points(8, 24, 8), 399384)
        self.assertEqual(multires_working_points(8, 24, 9), 1585176)

    def test_non_quad_face_corner_grids_are_counted(self):
        self.assertEqual(multires_working_points(5, 5, 1), 20)
        self.assertEqual(multires_working_points(5, 5, 3), 125)

    def test_closeup_cannot_accept_a_five_centimeter_occluder(self):
        tolerance = depth_match_tolerance(404.158, .24 / 384)
        self.assertTrue(tolerance is None or tolerance < .0000625)
        self.assertLess(depth_match_tolerance(2.5, .24 / 384), .00004)

    def test_numerically_unresolvable_visibility_fails_closed(self):
        self.assertIsNone(depth_match_tolerance(100000, .000001))
        self.assertIsNone(depth_match_tolerance(float('inf'), 1))
        self.assertIsNone(depth_match_tolerance(10, 0))
        self.assertIsNone(depth_match_tolerance(10, float('nan')))
        self.assertIsNone(depth_match_tolerance(13.44, 2.24 / 512, 100000))

    def test_normal_framing_has_a_small_finite_allowance(self):
        tolerance = depth_match_tolerance(13.44, 2.24 / 512)
        self.assertGreaterEqual(tolerance, 13.44 * 8 * 2 ** -23)
        self.assertLessEqual(tolerance, 2.24 / 512 * .1)


if __name__ == '__main__':
    unittest.main()
