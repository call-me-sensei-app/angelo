# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cottage and castle checks test exactly what their briefs state, the same on every side.

Names: a quoted name is that object (with Blender's .001 siblings); a differently named part, a child included, is
something else ("give anything else you add a meaningful name of its own"). Placement: no probe sits where the
brief allows something other than its target.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import castle_kit  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_oneshot_castle as castle  # noqa: E402
import scenario_oneshot_cottage as cottage  # noqa: E402


def check_named(module, label):
    return next(check for check in module.CHECKS if check["label"] == label)


class MatchingTests(unittest.TestCase):
    def test_exact_names_admit_blender_siblings_only(self):
        self.assertTrue(checks.matches("Cottage Walls", cottage.WALLS))
        self.assertTrue(checks.matches("Cottage Walls.003", cottage.WALLS))
        for other in ("Cottage Mortar", "Cottage Walls Recessed Opening Masonry", "Cottage Wall Left",
                      "Cottage Roof Underlay"):
            self.assertFalse(checks.matches(other, cottage.WALLS), other)
        self.assertFalse(checks.matches("Lantern Glass", cottage.LANTERN_PARTS))
        self.assertTrue(checks.matches("Lantern.001", cottage.LANTERN_PARTS))

    def test_any_except_and_word_prefixes(self):
        face = cottage.WALL_FACE
        for name in ("Cottage Walls", "Cottage Window Rear Right Frame", "Cottage Door"):
            self.assertTrue(checks.matches(name, face), name)
        self.assertFalse(checks.matches("Cottage Mortar", face))
        self.assertTrue(checks.matches("Castle Wall North 2", castle.WALL))
        self.assertTrue(checks.matches("Castle Wall.004", castle.WALL))
        self.assertFalse(checks.matches("Castle Wallflower Bed", castle.WALL))
        self.assertFalse(checks.matches("Castlegate Path", castle.CASTLE))
        others = check_named(castle, "the keep is the tallest part of the castle")["others"][0]
        self.assertTrue(checks.matches("Castle Flag", others))
        self.assertFalse(checks.matches("Castle Keep Roof", others))
        self.assertIn("but not", checks.target_text(others))


class CottagePlacementTests(unittest.TestCase):
    def test_wall_probes_count_a_window_or_the_door_set_in_the_wall(self):
        for label in ("front wall (right end)", "front wall reaches 2.3 m (left end)", "back wall"):
            self.assertEqual(check_named(cottage, label)["target"], cottage.WALL_FACE, label)

    def test_slopes_count_a_roof_window_and_the_ridge_probe_clears_the_chimney_band(self):
        for label in ("steep front slope", "steep back slope"):
            self.assertEqual(check_named(cottage, label)["target"], cottage.ROOF_FACE)
        chimney = check_named(cottage, "the chimney is at the -X end")
        allowed_east_edge = chimney["max"][0] + chimney["tolerance"]
        self.assertGreater(check_named(cottage, "ridge along X (left)")["origin"][0], allowed_east_edge)

    def test_windows_are_seen_just_above_the_stated_sill_from_close_by(self):
        for label in ("front window left of the door", "front window right of the door",
                      "the door can be seen from the front"):
            check = check_named(cottage, label)
            self.assertAlmostEqual(check["origin"][1], -cottage.HALF_Y - 0.6)
            self.assertLess(check["origin"][1] + check["distance"], 0.0)
        self.assertAlmostEqual(check_named(cottage, "front window left of the door")["origin"][2], cottage.SILL + 0.1)

    def test_no_unused_leftovers_means_none(self):
        for module in (cottage, castle):
            hygiene = next(check for check in module.CHECKS if check["kind"] == "hygiene")
            self.assertEqual(hygiene["max_unused"], 0)


class CastlePlacementTests(unittest.TestCase):
    def test_battlements_are_counted_on_the_outer_half_of_each_run(self):
        rule = next(check for check in castle.CHECKS if check["kind"] == "crenellations")
        self.assertTrue(all(0.0 < across <= 1.0 for across in rule["across"]))
        for x0, y0, x1, y1 in rule["lines"]:
            length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            ux, uy = (x1 - x0) / length, (y1 - y0) / length
            mid = ((x0 + x1) / 2, (y0 + y1) / 2)
            outward = (mid[0] - uy, mid[1] + ux)   # +across, as castle_checks samples it.
            self.assertGreater(abs(outward[0]) / castle_kit.HALF_X + abs(outward[1]) / castle_kit.HALF_Y,
                               abs(mid[0]) / castle_kit.HALF_X + abs(mid[1]) / castle_kit.HALF_Y)

    def test_towers_and_the_enclosure_count_windows_and_gate_towers_skip_the_gatehouse(self):
        for check in castle.CHECKS:
            if check["kind"] == "round_tower":
                self.assertEqual((check["targets"], check["set_in"]), ([castle.TOWER], [castle.WINDOW]))
                self.assertEqual(bool(check["skip"]), "Gate" in check["label"])
        self.assertIn(castle.WINDOW, next(c for c in castle.CHECKS if c["kind"] == "encloses")["targets"])

    def test_the_gateway_ray_covers_the_passage_only(self):
        clear = next(c for c in castle.CHECKS if c["kind"] == "arch")["clear"]
        self.assertGreater(clear["origin"][1], castle_kit.GATE_Y[0] - 2.0)
        self.assertLess(clear["origin"][1] + clear["distance"], castle_kit.GATE_Y[1] + 2.0)

    def test_unstated_checks_are_gone(self):
        labels = {check["label"] for check in castle.CHECKS}
        self.assertNotIn("the castle's overall size", labels)
        self.assertNotIn("the castle stands on the ground, not sunk into it", labels)
        frames = next(c for c in castle.CHECKS if c["kind"] == "frames")
        self.assertEqual(frames["fill"][0], 0.0)


if __name__ == "__main__":
    unittest.main()
