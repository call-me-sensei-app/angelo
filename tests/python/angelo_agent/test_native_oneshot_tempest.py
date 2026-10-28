# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Long candidate 6, ``oneshot-tempest``: a ship in a bottle; one table of numbers writes its brief, stand-in and checks.

Host-only (no Blender): the brief states every number a check measures and fits the product's message limit in both
arms; every probe has a margin from the one rule; no check counts mesh pieces or targets a name the brief does not
state; no piece's check target admits another piece; the brief never says how to work.
"""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import eval_parallel  # noqa: E402
import long_kit_tempest_brief as brief  # noqa: E402
import long_kit_tempest_values as values  # noqa: E402
import long_probe_scaled  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_oneshot_tempest as tempest  # noqa: E402
import scenario_specs as specs  # noqa: E402

PIECES = re.findall(r"'((?:Study|Bottle|Asterion|Blue Whale)[^']*)'", tempest.PROMPT.replace(brief.CONVENTIONS, ""))


def _targets():
    return {check["target"].get("prefix") or check["target"].get("name") or check["target"].get("whole")
            for check in tempest.CHECKS
            if "target" in check}


class TempestTests(unittest.TestCase):
    def test_it_is_registered_apart_as_a_long_candidate(self):
        self.assertIn("tempest", specs.ONESHOT_LONG)
        self.assertEqual(specs.module("oneshot_tempest"), tempest)
        for source in ("long_kit_tempest.py", "long_kit_tempest_ship.py", "long_kit_tempest_values.py",
                       "long_kit_tempest_brief.py", "long_probe_scaled.py"):
            self.assertIn(source, specs.ONESHOT_SOURCES[tempest.ID])
        self.assertIn("oneshot_tempest", long_probe_scaled.CANDIDATES)

    def test_the_brief_states_every_number_a_check_measures(self):
        stated = set().union(*(brief.keys(paragraph) for paragraph in brief.PARAGRAPHS))
        self.assertTrue(tempest.USED)
        self.assertEqual(tempest.USED - stated, set())
        self.assertNotIn("{", tempest.PROMPT)
        self.assertNotIn("}", tempest.PROMPT)

    def test_every_check_target_is_a_name_the_brief_states(self):
        for target in _targets():
            with self.subTest(target=target):
                self.assertTrue(target == "Hero Camera" or "'%s" % target in tempest.PROMPT, target)

    def test_no_piece_target_admits_another_named_piece(self):
        parts = {"Blue Whale": ("Blue Whale Flukes", "Blue Whale Eyes"),   # Named as the whale's parts.
                 "Asterion Cabin": ("Asterion Cabin Bulkhead", "Asterion Cabin Chart", "Asterion Cabin Chair",
                                    "Asterion Cabin Shelf", "Asterion Cabin Lantern"),   # The cabin's contents.
                 "Asterion Hold": ("Asterion Hold Floor", "Asterion Hold Casks", "Asterion Hold Crates",
                                   "Asterion Hold Rope", "Asterion Hold Stanchions", "Asterion Hold Lamp"),
                 "Study Wall": ()}
        for target in _targets() - {"Hero Camera", "Study Floor"}:
            admitted = [name for name in set(PIECES) if name != target and checks.matches(name, {"prefix": target,
                                                                                                 "word": True})]
            with self.subTest(target=target):
                self.assertEqual(sorted(set(admitted) - set(parts.get(target, ()))), [])

    def test_probes_carry_a_margin_from_the_one_rule(self):
        probes = [check for check in tempest.CHECKS if check["kind"] == "probe"]
        self.assertGreaterEqual(len(probes), 10)
        for check in probes:
            with self.subTest(check=check["label"]):
                margin = tempest.probe_margin(check)
                self.assertGreater(margin, 0.0)
                self.assertLessEqual(margin, 0.05)
        self.assertEqual(len({check["label"] for check in tempest.CHECKS}), len(tempest.CHECKS))

    def test_tolerances_follow_one_rule(self):
        self.assertEqual(tempest.tolerance(2.5, 0.4), 0.1)
        self.assertEqual(tempest.tolerance(0.56, 0.04), 0.012)
        self.assertEqual(tempest.tolerance(0.05), 0.004)

    def test_what_a_person_sees_never_how_it_was_built(self):
        kinds = {check["kind"] for check in tempest.CHECKS}
        self.assertNotIn("parts", kinds)
        self.assertNotIn("count", kinds)
        self.assertNotIn("geometry", kinds)
        self.assertIn("volume", kinds)   # The clouds: a volume a renderer shows, however it is made.

    def test_detail_reaches_inside_the_bottle_and_the_hull(self):
        labels = " ".join(check["label"] for check in tempest.CHECKS)
        for word in ("sea", "storm clouds", "hull", "cannons", "Mast", "Course", "Topsail", "Topgallant", "helm",
                     "gun deck", "chart table", "chronometer", "dip pen", "hold", "whale", "flukes", "eyes"):
            self.assertIn(word, labels)
        self.assertEqual(len(values.SAILS), 9)
        self.assertGreaterEqual(len(tempest.CHECKS), 100)

    def test_rays_inside_the_bottle_pass_through_the_glass(self):
        # The grader's probe looks for its target by name and passes through every other object (the glass, the
        # clouds, the sea), so the rays down onto the sea and the whale start above the bottle and are not stopped.
        self.assertIn("passing through any other geometry", checks.probe.__doc__)
        top = values.V["axis_z"] + values.V["body_dia"] / 2
        inside = [check for check in tempest.CHECKS if check["kind"] == "probe"
                  and check["target"]["prefix"].startswith(("Bottle Sea", "Blue Whale"))]
        self.assertEqual(len(inside), 4)
        for check in inside:
            self.assertGreater(check["origin"][2], top, check["label"])

    def test_the_brief_as_sent_fits_the_message_limit_in_both_arms(self):
        for arm, text in (("single", tempest.PROMPT),
                          ("parallel", eval_parallel.with_request(tempest.PROMPT, eval_parallel.PARALLEL_REQUEST))):
            with self.subTest(arm=arm):
                self.assertLessEqual(len(text), 30000)

    def test_the_brief_never_says_how_to_work(self):
        lowered = tempest.PROMPT.lower()
        self.assertIsNone(re.search(r"\bsplit (the|it|up|into|between)", lowered))
        for word in ("parallel", "worker", "agent", "script", "copy", "duplicate", "instance", "array",
                     "geometry nodes", "modifier", "first,", "then build", "subagent"):
            self.assertIsNone(re.search(r"\b%s" % re.escape(word), lowered), word)


if __name__ == "__main__":
    unittest.main()
