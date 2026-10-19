# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The graders judge the scene as built: no rule forgives a gap, seam or hole the build left.

A check tests what its brief states, the same on every side. Tolerances that remain absorb measurement error only.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import castle_checks  # noqa: E402
import oneshot_mini_checks as minis  # noqa: E402
import oneshot_minis  # noqa: E402
import scenario_checks  # noqa: E402
import scenario_oneshot_castle as castle  # noqa: E402
import scenario_oneshot_cottage as cottage  # noqa: E402
import scenario_specs  # noqa: E402


def every_check():
    for name in scenario_specs.ONESHOT_NAMES:
        for check in scenario_specs.all_checks(name):
            yield name, check


class NoSamplingTests(unittest.TestCase):
    def test_no_brief_samples_several_rays_for_one_check(self):
        for name, check in every_check():
            with self.subTest(scenario=name, check=check["label"]):
                self.assertNotIn("origins", check)
                self.assertNotIn("min_hits", check)
        self.assertFalse(hasattr(scenario_checks, "sample_rays"))

    def test_a_seam_at_the_door_probe_fails_the_door(self):
        door = next(check for check in cottage.CHECKS if check["label"] == "the door is in the front wall")
        self.assertEqual(door["origin"], [0, -9, 1.1])
        # The ray passes the seam between two planks and meets the back wall: no part of the door is hit.
        with mock.patch.object(scenario_checks, "probe", return_value={"hit": False, "passed_through": "Cottage Walls"}):
            passed, details = scenario_checks._check_probe(None, None, None, door)
        self.assertFalse(passed)
        self.assertEqual(details["passed_through"], "Cottage Walls")


def box(low, high):
    return (list(low), list(high))


class SupportedTests(unittest.TestCase):
    def supported(self, pieces, ground=(), **rule):
        def boxes(_depsgraph, target):
            return list(ground) if target == castle.GROUND else list(pieces)
        check = dict({"target": castle.CASTLE, "ground_target": castle.GROUND}, **rule)
        with mock.patch.object(castle_checks, "_boxes", side_effect=boxes):
            return castle_checks._check_supported(None, None, None, check)

    def test_the_brief_check_states_no_slack(self):
        check = next(check for check in castle.CHECKS if check["kind"] == "supported")
        self.assertNotIn("gap", check)
        self.assertNotIn("ground", check)
        self.assertEqual(check["ground_target"], castle.GROUND)

    def test_a_piece_a_few_centimetres_up_floats(self):
        tower = box((0, 0, 0), (2, 2, 10))
        roof = box((0, 0, 10.05), (2, 2, 14))     # 5 cm above its tower: the old 0.12 m slack let it pass.
        passed, details = self.supported([tower, roof])
        self.assertFalse(passed)
        self.assertEqual(details["floating"], 1)
        self.assertTrue(self.supported([tower, box((0, 0, 10.0004), (2, 2, 14))])[0])   # Float error only.

    def test_the_ground_is_the_scenes_ground_not_a_band_above_zero(self):
        hovering = box((0, 0, 0.2), (2, 2, 5))     # 20 cm above a ground at 0: the old 0.3 m band let it pass.
        self.assertFalse(self.supported([hovering], ground=[box((-80, -80, -0.5), (80, 80, 0))])[0])
        # On a meadow whose top is 0.2 m up (its own checks allow that), the same piece rests on it.
        self.assertTrue(self.supported([hovering], ground=[box((-80, -80, -0.5), (80, 80, 0.2))])[0])
        self.assertTrue(self.supported([box((0, 0, -1), (2, 2, 5))])[0])   # Sunk in: on the ground.


class RoundTowerTests(unittest.TestCase):
    def test_every_ray_must_meet_the_tower(self):
        brief = next(check for check in castle.CHECKS if check["kind"] == "round_tower")
        self.assertNotIn("misses", brief)
        check = dict(brief, centre=[0, 0], height=10.0, radius=[2.0, 5.0], rays=16)
        calls = []

        def first_hit(scene, depsgraph, origin, direction, reach, targets, around=None):
            calls.append(origin)
            if len(calls) == 1:          # One side of the tower is open.
                return None
            tower = NS(original=NS(name="Castle Tower NE"))
            if origin[2] == 10.0:
                return ((origin[0] + direction[0] * (reach - 3.5), origin[1] + direction[1] * (reach - 3.5), 10), tower,
                        0)
            return ((origin[0], origin[1], 20.0), tower, 0)
        with mock.patch.object(castle_checks, "first_hit", side_effect=first_hit), \
                mock.patch.object(castle_checks, "_hit_colour", return_value=(True, "blue")):
            passed, details = castle_checks._check_round_tower(None, None, None, check)
        self.assertFalse(passed)
        self.assertIn("1 of 16 rays met no tower", details["problems"])


class SlabTests(unittest.TestCase):
    def slab(self, top, rule):
        scene = NS()
        components = [{"names": ["Deck"], "bounds": ([-2.0, -0.6, 0.0], [2.0, 0.6, 0.3])}]
        with mock.patch.object(minis, "_built_names", return_value={"Deck"}), \
                mock.patch.object(minis, "components", return_value=components), \
                mock.patch.object(minis, "_extent", return_value=([-2.0, -0.6, 0.0], [2.0, 0.6, 0.3])), \
                mock.patch.object(minis, "top_height", side_effect=lambda scene, graph, x, y, names: top(x, y)):
            return minis._check_slab(None, scene, None, rule)

    def test_every_sampled_ray_must_meet_the_deck(self):
        rule = next(check for check in oneshot_minis.footbridge_checks((-0.05, 0.05), 0.3) if check["kind"] == "slab")
        self.assertNotIn("cover", rule)
        self.assertAlmostEqual(rule["span"], 3.7 - 0.24)
        whole = lambda x, y: 0.3
        self.assertTrue(self.slab(whole, rule)[0])
        holed = lambda x, y: None if abs(x) < 0.05 else 0.3    # One gap across the middle of the deck.
        passed, details = self.slab(holed, rule)
        self.assertFalse(passed)
        self.assertIn("cover", details["problems"])
        # The shortest deck the plan check allows still meets every sampled ray.
        shortest = lambda x, y: 0.3 if abs(x) <= 3.7 / 2 else None
        self.assertTrue(self.slab(shortest, rule)[0])


if __name__ == "__main__":
    unittest.main()
