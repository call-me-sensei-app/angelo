# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Long candidate 5, ``oneshot-grand-apartment``: one table writes its brief, stand-in and checks.

Host-only (no Blender): the table's pieces are distinct by name and by description, the brief states every number
a check measures, the checks verify each key piece's own sizes (so the pieces cannot all be one design copied), and
no check target admits another piece by its name.
"""

import collections
import math
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import eval_parallel  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_oneshot_grand_apartment as flat  # noqa: E402
import scenario_specs as specs  # noqa: E402


class GrandApartmentTests(unittest.TestCase):
    def test_it_is_registered_apart_as_a_long_candidate(self):
        self.assertIn("grand-apartment", specs.ONESHOT_LONG)
        self.assertEqual(specs.module("oneshot_grand_apartment"), flat)
        for source in ("long_kit_flat.py", "long_kit_flat_north.py", "long_kit_flat_south.py", "long_probe_check.py"):
            self.assertIn(source, specs.ONESHOT_SOURCES[flat.ID])

    def test_about_two_hundred_distinct_pieces_in_twelve_rooms_a_hall_and_a_balcony(self):
        names = [piece["name"] for piece in flat.PIECES]
        self.assertGreaterEqual(len(names), 200)
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(set(piece["text"] for piece in flat.PIECES)), len(names))   # Each described its own way.
        self.assertEqual(len(flat.ROOM_ORDER), 14)
        for piece in flat.PIECES:
            with self.subTest(piece=piece["name"]):
                self.assertTrue(piece["name"].startswith(piece["room"] + " "))
                x0, x1, y0, y1 = flat.flat.ROOMS[piece["room"]]
                self.assertTrue(x0 - 0.05 <= piece["at"][0] <= x1 + 0.05 and y0 - 0.05 <= piece["at"][1] <= y1 + 0.05)

    def test_no_check_target_admits_another_piece_by_its_name(self):
        names = [piece["name"] for piece in flat.PIECES] + ["Entrance Hall Front Door", "Balcony Door", "Balcony Deck",
                                                            "Balcony Balustrade"]
        for piece in flat.PIECES:
            target = {"prefix": piece["name"], "word": True}
            with self.subTest(piece=piece["name"]):
                self.assertEqual([name for name in names if checks.matches(name, target)], [piece["name"]])

    def test_the_brief_states_every_number_a_piece_check_measures(self):
        prompt = flat.PROMPT
        self.assertNotIn("{", prompt)
        for piece in flat.PIECES:
            with self.subTest(piece=piece["name"]):
                self.assertIn("'%s'," % piece["name"], prompt)
                letters = piece["checks"]
                text = piece["text"]
                if set(letters) & set("tTblhr"):
                    self.assertTrue("{at}" in text or "x = " in text, "its place")
                if "b" in letters:   # Its footprint and height: a width (or its run "from x = ..."), a depth unless
                    round_ = piece["kind"] in ("round", "cyl", "stool", "table3", "teepee") or "square" in text
                    self.assertTrue("{w}" in text or "from x = " in text, "its width")
                    self.assertTrue("{d}" in text or round_, "its depth")   # round or square, and a height unless
                    flat_ = piece["size"][2] <= 0.02 or piece["size"][2] == piece["top"]   # a rug or its own seat.
                    self.assertTrue("{h}" in text or flat_ and ("{top}" in text or piece["size"][2] <= 0.02), "height")
                if "w" in letters:
                    self.assertTrue("{x}" in text or "{y}" in text)
                    self.assertIn("{z}", text)
                if set(letters) & set("tT"):
                    self.assertTrue("{top}" in text or "{h}" in text)

    def test_the_dining_chairs_are_six_designs_with_their_own_sizes(self):
        chairs = [piece for piece in flat.PIECES if piece["room"] == "Dining Room" and "Chair" in piece["name"]]
        self.assertEqual(len(chairs), 6)
        self.assertEqual(len({piece["size"][0] for piece in chairs}), 6)    # Widths all differ.
        self.assertEqual(len({piece["size"][2] for piece in chairs}), 6)    # Back heights all differ.
        self.assertEqual(len({piece["mat"] for piece in chairs}), 6)
        for chair in chairs:
            box = [check for check in flat.CHECKS if check["kind"] == "bbox" and check["target"].get("prefix") == chair["name"]]
            self.assertEqual(len(box), 1, chair["name"])
            self.assertEqual(box[0]["max"][2], chair["size"][2])

    def test_detail_checks_cover_legs_knobs_frames_and_basins(self):
        kinds = collections.Counter()
        for check in flat.CHECKS:
            label = check["label"]
            for word in ("open underneath", "knobs or pulls", "on its wall", "hollow", "proud of its front", "in colour"):
                if word in label:
                    kinds[word] += 1
        self.assertGreaterEqual(kinds["open underneath"], 15)
        self.assertGreaterEqual(kinds["knobs or pulls"], 15)
        self.assertGreaterEqual(kinds["on its wall"], 15)
        self.assertGreaterEqual(kinds["hollow"], 5)
        self.assertGreaterEqual(kinds["in colour"], 25)
        self.assertEqual(len([c for c in flat.CHECKS if c["label"].endswith(": a window in it")]), len(flat.flat.WINDOWS))
        # What a person sees, never how it was built: no check counts pieces of mesh.
        self.assertNotIn("parts", {check["kind"] for check in flat.CHECKS})

    def test_every_ray_starts_off_its_pieces_centre_lines(self):
        for piece in flat.PIECES:
            for check in flat.piece_checks(piece):
                if check["kind"] != "probe" or check["direction"] != [0, 0, -1]:
                    continue
                with self.subTest(check=check["label"]):   # In the piece's own frame (front toward -Y).
                    x, y = piece["at"][:2]
                    dx, dy = check["origin"][0] - x, check["origin"][1] - y
                    a = math.radians(-(piece["facing"] + 90.0))
                    local = (dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a))
                    self.assertGreater(abs(local[0]), 0.03)
                    self.assertGreater(abs(local[1]), 0.03)

    def test_the_brief_never_says_how_to_work(self):
        lowered = flat.PROMPT.lower()
        for word in ("parallel", "worker", "split", "agent", "script", "copy", "duplicate", "instance", "array"):
            self.assertIsNone(re.search(r"\b%s" % word, lowered), word)


class SentTextLimitTests(unittest.TestCase):
    """Every long candidate's brief fits the product's message limit as sent, in both arms, with margin (candidate 5's
    first version, 36,903 characters, was refused by controller.send: "Enter a message of 1-32000 characters")."""

    PRODUCT_LIMIT = 32000
    SENT_MAX = 30000   # The margin: the text as sent, the parallel arm's request included.

    def test_the_product_limit_is_still_32000_characters(self):
        controller = HERE.parents[2] / "scripts" / "addons_core" / "angelo_agent" / "controller.py"
        if not controller.is_file():
            self.skipTest("the add-on is not in this tree")
        self.assertIn("len(text) > %d" % self.PRODUCT_LIMIT, controller.read_text(encoding="utf-8"))

    def test_every_long_brief_as_sent_fits_with_margin_in_both_arms(self):
        for name in specs.ONESHOT_LONG:
            module = specs.module(specs.scenario_id(specs.ONESHOT_PREFIX + name))
            prompt = module.TURNS[0]["prompt"]
            for arm, text in (("single", prompt), ("parallel", eval_parallel.with_request(prompt, eval_parallel.PARALLEL_REQUEST))):
                with self.subTest(test=name, arm=arm):
                    self.assertLessEqual(len(text), self.SENT_MAX)
                    self.assertLess(len(text), self.PRODUCT_LIMIT)


if __name__ == "__main__":
    unittest.main()
