# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each check of the bridge, footbridge, tree and mini tests asks what its brief states, no more and no less (no Blender).

Names a brief quotes as names are matched exactly (with Blender's .001
siblings); names it says parts start with are prefixes. A probe reads the
part its brief sentence is about, not every part sharing a prefix. The
stand-ins prove each check can be met (oneshot_variants.py and the
reference self-checks run them in Blender).
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import scenario_bridge as bridge  # noqa: E402
import scenario_checks  # noqa: E402
import scenario_oneshot_bridge as oneshot_bridge  # noqa: E402
import scenario_oneshot_footbridge as footbridge  # noqa: E402
import scenario_oneshot_footbridge_conflict as conflict  # noqa: E402
import scenario_oneshot_mini_camera as mini_camera  # noqa: E402
import scenario_oneshot_mini_cube as mini_cube  # noqa: E402
import scenario_oneshot_mini_lighting as mini_lighting  # noqa: E402
import scenario_oneshot_tree as tree  # noqa: E402

matches = scenario_checks.matches


def labelled(checks, label):
    return next(check for check in checks if check["label"] == label)


class NamingTests(unittest.TestCase):
    def test_quoted_names_are_exact_and_their_blender_siblings(self):
        for target, name, prompt in ((tree.TRUNK, "Tree Trunk", tree.PROMPT),
                                     (tree.FOLIAGE, "Tree Foliage", tree.PROMPT),
                                     (footbridge.DECK, "Footbridge Deck", footbridge.PROMPT),
                                     (bridge.RIVER, "River", oneshot_bridge.PROMPT),
                                     (bridge.PIERS, "Bridge Piers", oneshot_bridge.PROMPT),
                                     (bridge.ABUTMENTS, "Bridge Abutments", oneshot_bridge.PROMPT)):
            with self.subTest(name=name):
                self.assertIn("'%s'" % name, prompt)
                self.assertTrue(matches(name, target) and matches(name + ".001", target))
                self.assertFalse(matches(name + " Extra", target))
        self.assertIn("name them exactly as written here", tree.PROMPT)

    def test_the_default_cube_is_the_object_named_cube_only(self):
        removed = labelled(bridge.TURNS[0]["checks"], "default cube removed")
        self.assertTrue(matches("Cube", removed["target"]))
        self.assertFalse(matches("Cube.001", removed["target"]))


class TreeTests(unittest.TestCase):
    def test_the_trunk_is_measured_through_other_tree_parts(self):
        thickness = [check for check in tree.CHECKS if check["kind"] == "probe" and check.get("expect") == "hit"
                     and check["label"].startswith(("trunk 0.35", "the trunk at"))]
        self.assertEqual(len(thickness), 12)
        self.assertTrue(all(check["target"] == tree.TRUNK for check in thickness))
        self.assertFalse(matches("Tree Root Flare", tree.TRUNK))

    def test_the_first_1_8_m_is_clear_of_branches_and_leaves_only(self):
        self.assertIn("stays clear of branches and leaves for the first 1.8 m", tree.PROMPT)
        clear = [check for check in tree.CHECKS if check.get("target") == tree.LIMB_OR_LEAF]
        self.assertEqual(len(clear), 6)
        for check in clear:
            self.assertEqual((check["expect"], check["distance"]), ("miss", 2.0))
            self.assertLess(check["origin"][2], 1.8)
        for name in ("Tree Branch 03", "Tree Foliage", "Tree Foliage.012"):
            self.assertTrue(matches(name, tree.LIMB_OR_LEAF), name)
        for name in ("Tree Trunk", "Tree Root Flare", "Tree Swing"):
            self.assertFalse(matches(name, tree.LIMB_OR_LEAF), name)


class TidyFileTests(unittest.TestCase):
    def test_no_unused_leftovers_means_none(self):
        for module, checks in ((tree, tree.CHECKS), (footbridge, footbridge.CHECKS),
                               (oneshot_bridge, oneshot_bridge.TURNS[0]["checks"])):
            with self.subTest(test=module.ID):
                self.assertIn("no unused leftovers", module.PROMPT)
                hygiene = next(check for check in checks if check["kind"] == "hygiene")
                self.assertEqual(hygiene["max_unused"], 0)
                self.assertEqual(hygiene.get("unused_counted", "saved"), "saved")   # What a save writes.


class BridgeTests(unittest.TestCase):
    def test_each_probe_reads_the_part_its_sentence_names(self):
        checks = bridge.TURNS[1]["checks"]
        for label, target in (("deck top above the crown", bridge.DECK), ("arch ring at x=-6", bridge.ARCH),
                              ("pier at x=5.5", bridge.PIERS), ("abutment under the deck end", bridge.ABUTMENTS)):
            self.assertEqual(labelled(checks, label)["target"], target, label)

    def test_eight_rocks_a_side_as_the_briefs_say(self):
        self.assertIn("Sixteen rocks", oneshot_bridge.PROMPT)
        self.assertIn("eight a side", oneshot_bridge.PROMPT)
        self.assertIn("eight per side", bridge.TURNS[4]["prompt"])
        for side, label in ((-1, "rocks along the left edge"), (1, "rocks along the right edge")):
            check = labelled(bridge.TURNS[4]["checks"], label)
            self.assertEqual(check["count"], [8, 8])
            self.assertEqual(sum(1 for x, _, _ in bridge.ROCKS if x * side > 0), 8)


class FootbridgeTests(unittest.TestCase):
    def test_the_bridge_clears_the_stated_water_level(self):
        self.assertIn("about 0.45 m below the banks", footbridge.PROMPT)
        clear = labelled(footbridge.CHECKS, "the bridge clears the water")
        self.assertEqual(clear["expect"], "miss")
        self.assertTrue(footbridge.WATER_Z < clear["origin"][2] <= footbridge.WATER_Z + 0.05)

    def test_the_handrail_top_is_graded_not_every_railing_parts(self):
        self.assertIn("A handrail on each side with its top 1 m above the deck (z = 1.3 m)", footbridge.PROMPT)
        railing = [check for check in footbridge.CHECKS if check.get("target") == footbridge.RAILING]
        self.assertFalse([check for check in railing if check["kind"] == "bbox" and check.get("max")
                          and check["max"][2] is not None])
        handrails = [check for check in railing if check["label"].startswith("handrail top")]
        posts = [check for check in railing if check["label"].startswith("at least four posts a side")]
        for pair, count in ((handrails, 1), (posts, 4)):
            self.assertEqual(len(pair), 2)                                # One check for each side, as stated.
            self.assertEqual(sorted(check["region_max"][1] > 0 for check in pair), [False, True])
            self.assertTrue(all(check["count"][0] == count for check in pair))
        low, high = handrails[0]["region_min"][2], handrails[0]["region_max"][2]
        self.assertTrue(low < footbridge.RAIL_TOP < high)
        self.assertGreaterEqual(posts[0]["region_min"][2], -0.2)
        self.assertLessEqual(posts[0]["region_max"][2], low)            # A post's middle is below a handrail's.

    def test_the_walkway_is_the_bridge_only(self):
        for label in ("walkway clear of railings", "walkway clear of the bridge"):
            probe = labelled(footbridge.CHECKS, label)
            self.assertEqual(probe["origin"][0], -footbridge.DECK_LENGTH / 2)
            self.assertEqual(probe["distance"], footbridge.DECK_LENGTH)

    def test_the_conflict_brief_admits_either_railing_rule_and_changes_nothing_else(self):
        self.assertIn("every part's name starts with 'Footbridge'.", conflict.PROMPT)
        self.assertIn("Every railing part's name starts with 'Railing'.", conflict.PROMPT)
        for name in ("Railing Posts", "Footbridge Railing Posts"):
            self.assertTrue(matches(name, conflict.RAILING), name)
        self.assertFalse(matches("Footbridge Deck", conflict.RAILING))
        self.assertIs(conflict.TURNS[0]["checks"], conflict.CHECKS)
        swap = lambda check: dict(check, target=footbridge.RAILING) if check.get("target") == conflict.RAILING else check
        self.assertEqual([swap(check) for check in conflict.CHECKS], footbridge.CHECKS)


class MiniTests(unittest.TestCase):
    def test_the_cube_is_held_unchanged_only_where_the_message_edits_it(self):
        for module in (mini_camera, mini_lighting):
            with self.subTest(test=module.ID):
                self.assertFalse([check for check in module.CHECKS if check.get("target") == {"name": "Cube"}])
        self.assertTrue([check for check in mini_cube.CHECKS if check.get("target") == {"name": "Cube"}])


if __name__ == "__main__":
    unittest.main()
