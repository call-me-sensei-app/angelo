# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The grader-only ``opaque`` check: a surface a brief states as solid has no see-through gaps.

Noise: on 45 recorded cottage builds without open joints (Opus 5.5 and MCP, both sides) and the stand-in with
all its variants, 0 of about 110,000 rays crossed, so ``NOISE`` is 0.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import castle_checks  # noqa: E402
import oneshot_checks  # noqa: E402
import oneshot_opacity as opacity  # noqa: E402
import scenario_oneshot_bridge as bridge  # noqa: E402
import scenario_oneshot_castle as castle  # noqa: E402
import scenario_oneshot_cottage as cottage  # noqa: E402

TARGET = {"prefix": "Cottage"}


def grade(areas, closed, **rule):
    """Grade ``areas`` where ``closed(origin)`` says whether a closing part lies on the ray from ``origin``."""
    def first_hit(scene, depsgraph, origin, direction, depth, targets, accept=None):
        return ((0, 0, 0), None, 0) if closed(origin) else None
    with mock.patch.object(castle_checks, "first_hit", side_effect=first_hit):
        return opacity._check_opaque(None, None, None, dict({"target": TARGET, "areas": areas}, **rule))


class OpacityTests(unittest.TestCase):
    def setUp(self):
        self.walls = opacity.wall_areas(cottage.HALF_X, cottage.HALF_Y, cottage.EAVES, cottage.RIDGE)

    def test_registered_and_stated_without_slack(self):
        self.assertIs(oneshot_checks.CHECKS["opaque"], opacity._check_opaque)
        self.assertEqual(opacity.NOISE, 0.0)
        for check in cottage.CHECKS + castle.CHECKS + bridge.TURNS[0]["checks"]:
            if check["kind"] == "opaque":
                self.assertNotIn("max_open", check, check["label"])

    def test_a_closed_wall_passes_and_one_open_joint_fails(self):
        passed, details = grade(self.walls, lambda origin: True)
        self.assertTrue(passed)
        self.assertEqual(details["open"], 0)
        # Stones 0.5 m long with a 2 cm open joint between each pair: about 4 % of the rays cross.
        joints = lambda origin: abs((origin[0] + 4.0) % 0.5 - 0.25) < 0.24 if abs(origin[1]) > 3 else True
        passed, details = grade(self.walls, joints)
        self.assertFalse(passed)
        self.assertGreater(details["open"], 0)
        self.assertEqual({area["name"] for area in details["areas"] if area["open"]}, {"front wall", "back wall"})
        one = iter([False] + [True] * 10000)
        self.assertFalse(grade(self.walls, lambda origin: next(one))[0])   # A single crossing ray fails.

    def test_the_gable_triangle_keeps_under_the_roof_line(self):
        gable = next(area for area in self.walls if area["name"] == "left gable end (gable)")
        points = opacity.area_points(gable, (40, 12))
        self.assertTrue(points)
        for s, t, origin in points:
            self.assertTrue(opacity.inside((s, t), gable["keep"]))
            ridge_line = cottage.EAVES + (cottage.RIDGE - cottage.EAVES) * (1 - abs(origin[1]) / cottage.HALF_Y)
            self.assertLess(origin[2], ridge_line)

    def test_rays_start_outside_and_stop_short_of_the_opposite_wall(self):
        front = next(area for area in self.walls if area["name"] == "front wall")
        self.assertLess(front["corner"][1], -cottage.HALF_Y)
        self.assertLess(front["corner"][1] + front["depth"], cottage.HALF_Y - 1.0)
        (roof,) = opacity.roof_areas(cottage.HALF_X, cottage.HALF_Y, cottage.EAVES)
        self.assertAlmostEqual(roof["corner"][2] - roof["depth"], cottage.EAVES - 0.2)

    def test_grid_avoids_round_coordinates_and_points_are_used_as_given(self):
        front = next(area for area in self.walls if area["name"] == "front wall")
        for _s, _t, origin in opacity.area_points(front, (40, 12)):
            self.assertNotAlmostEqual(origin[0] * 2, round(origin[0] * 2), places=3)
        ring = {"points": [[1, 2, 3]], "direction": [0, 1, 0], "depth": 1}
        self.assertEqual(opacity.area_points(ring, (40, 12)), [(None, None, [1, 2, 3])])

    def test_box_area_faces(self):
        deck = opacity.box_areas("deck", (-12, -2.5, 0.4), (12, 2.5, 1.0), 2, -1, 9.0, 9.55, edge=0.5)
        self.assertEqual(deck["corner"], [-11.5, -2.0, 10.0])
        self.assertEqual(deck["direction"], [0.0, 0.0, -1.0])
        self.assertAlmostEqual(deck["corner"][2] - deck["depth"], 0.45)
        wall = opacity.box_areas("w", (19, -11, 0.5), (21, 11, 8.5), 0, -1, 2.0, 5.0, edge=0.0)
        self.assertEqual((wall["corner"][0], wall["direction"]), (23.0, [-1.0, 0.0, 0.0]))

    def test_the_ray_budget_is_bounded(self):
        many = [dict(self.walls[0], name="w%d" % index) for index in range(60)]
        with self.assertRaises(ValueError):
            grade(many, lambda origin: True)


if __name__ == "__main__":
    unittest.main()
