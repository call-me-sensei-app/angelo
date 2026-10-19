# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The variant job's table against the tests it grades (no Blender).

``oneshot_variants.py`` runs in stock Blender; these tests pin what the host
can: every variant names a one-shot test and, when it breaks something, the
labels of checks that test really has, so a renamed check cannot leave a
variant expecting a failure nothing reports.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_variants as variants  # noqa: E402
import scenario_specs as specs  # noqa: E402


class VariantTableTests(unittest.TestCase):
    def test_every_variant_names_a_test_and_checks_it_has(self):
        self.assertEqual(set(variants.VARIANTS), {"oneshot_tree", "oneshot_cottage", "oneshot_castle"})
        for name, table in variants.VARIANTS.items():
            labels = [check["label"] for check in specs.module(name).TURNS[0]["checks"]]
            self.assertEqual(len(labels), len(set(labels)), name)          # A label names one check.
            self.assertEqual(len({label for label, _, _ in table}), len(table))
            for label, builder, expected in table:
                with self.subTest(test=name, variant=label):
                    self.assertTrue(callable(builder))
                    self.assertTrue(set(expected) <= set(labels), expected)
                    self.assertEqual(len(expected), len(set(expected)))

    def test_each_test_is_built_other_ways_and_broken(self):
        for name, table in variants.VARIANTS.items():
            follows = [label for label, _, expected in table if not expected]
            breaks = [label for label, _, expected in table if expected]
            self.assertGreaterEqual(len(follows), 3, name)                 # Other ways that must pass everything.
            self.assertGreaterEqual(len(breaks), 2, name)                  # Broken builds that must fail their own.
        tree = dict((label, expected) for label, _, expected in variants.VARIANTS["oneshot_tree"])
        self.assertEqual(tree["leaves as instances of one leaf on points"], ())   # The route the first checks failed.
        self.assertEqual(tree["seven smooth blobs for foliage"], (variants.PIECES,))
        cottage = dict((label, expected) for label, _, expected in variants.VARIANTS["oneshot_cottage"])
        self.assertEqual(cottage["the picture's slate roof"], (variants.ROOF,))   # Copying the picture fails the brief.
        self.assertEqual(cottage["a 1.6 m doorstep beside a 0.9 m leaf"], ())
        # Differently named parts are other parts: overlays hide the door (the brief's exact names).
        self.assertEqual(cottage["decorative named planks over the door leaf"], (variants.DOOR_VISIBLE,))
        for label in ("a door mesh of planks with a 7 mm seam on its centre line",
                      "a door of six sibling plank objects with a 6 mm seam on its centre line"):
            self.assertEqual(cottage[label], (variants.DOOR_POSITION, variants.DOOR_VISIBLE))   # No sampling.
        self.assertEqual(cottage["a door leaf 1.6 m wide"], (variants.DOOR_SIZE,))
        self.assertIn(variants.DOOR_POSITION, cottage["the door moved to the back"])
        self.assertIn(variants.DOOR_VISIBLE, cottage["most of the door obscured by stone"])
        self.assertIn(variants.DOOR_VISIBLE, cottage["only two narrow door edge strips"])
        self.assertIn(variants.DOOR_SIZE, cottage["no door"])
        castle = dict((label, expected) for label, _, expected in variants.VARIANTS["oneshot_castle"])
        for label in ("eight-sided towers", "each wall run and its merlons as one object",
                      "every merlon its own object, one shared mesh",
                      "the south wall run on through the gatehouse, cut by the same arch"):
            self.assertEqual(castle[label], ())                           # Other ways to follow the brief.
        self.assertEqual(castle["a door closing the gateway"], ("an open round-arched gateway through the gatehouse",))
        self.assertEqual(len(castle["square corner towers"]), 4)          # One check per corner tower.

    def test_an_unknown_test_is_refused_before_blender_is_needed(self):
        with self.assertRaises(SystemExit) as refused:
            variants.main(["oneshot_room"])
        self.assertIn("oneshot_castle, oneshot_cottage, oneshot_tree", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
