# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a marked-up picture sends Angelo's agent (``markup_message``): the marked picture, the original, and one
structured block whose comment numbers are the numbers drawn on the picture. No Blender here.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_doc as md, markup_message as mm  # noqa: E402

ORIGINAL = "0b9c6f6e-7a4f-4e4f-9d43-0b1d6d1c2e3a"
MARKED = "5f0e1c1a-2b2c-4d4e-8f8f-123456789abc"


def sample(w=2048, h=1024):
    d = md.MarkupDoc(ORIGINAL, (w, h))
    d.add_mark("ellipse", box=(100.4, 200.6, 50, 40), colour="red")
    d.add_mark("arrow", start=(10, 10), end=(90, 80), colour="amber")
    d.add_mark("pen", points=[(500, 500), (520, 540), (560, 505)], colour="teal")
    d.add_mark("text", at=(700, 100), text="this strand")
    d.add_comment(at=(130, 220), text="Too dark")
    d.add_comment(box=(300, 300, 100, 80), text="Make these curls tighter")   # Inside every sample size.
    d.instructions = "  Fix both, keep the colours.  "
    return d


class BlockTests(unittest.TestCase):
    def test_the_block_names_both_pictures_and_the_coordinates(self):
        block = mm.model_block(sample(), MARKED)
        self.assertEqual(block["kind"], "picture_markup")
        self.assertEqual(block["original_picture"], ORIGINAL)
        self.assertEqual(block["marked_picture"], MARKED)
        self.assertEqual(block["size"], [2048, 1024])
        self.assertIn("top-left", block["coordinates"])
        json.dumps(block)   # Plain JSON: it rides in the turn's attachment context.

    def test_comments_carry_the_numbers_drawn_on_their_pins(self):
        d = sample()
        block = mm.model_block(d, MARKED)
        self.assertEqual([c["n"] for c in block["comments"]], [1, 2])
        self.assertEqual(block["comments"][0], {"n": 1, "at": [130, 220], "text": "Too dark"})
        self.assertEqual(block["comments"][1]["box"], [300, 300, 100, 80])
        self.assertEqual([pin["n"] for pin in mm.pins(d)], [1, 2])

    def test_marks_are_described_by_kind_colour_and_place_in_whole_pixels(self):
        marks = mm.model_block(sample(), MARKED)["marks"]
        self.assertEqual(marks[0], {"kind": "ellipse", "colour": "red", "box": [100, 201, 50, 40]})
        self.assertEqual(marks[1], {"kind": "arrow", "colour": "amber", "from": [10, 10], "to": [90, 80]})
        self.assertEqual(marks[2], {"kind": "pen", "colour": "teal", "box": [500, 500, 60, 40]})
        self.assertEqual(marks[3]["text"], "this strand")

    def test_instructions_are_the_persons_words_trimmed(self):
        self.assertEqual(mm.model_block(sample(), MARKED)["instructions"], "Fix both, keep the colours.")

    def test_blank_comments_are_left_out_but_keep_their_numbers(self):
        d = md.MarkupDoc(ORIGINAL, (100, 100))
        d.add_comment(at=(1, 1))
        d.add_comment(at=(2, 2), text="here")
        self.assertEqual([c["n"] for c in mm.model_block(d, MARKED)["comments"]], [1, 2])
        self.assertNotIn("text", mm.model_block(d, MARKED)["comments"][0])

    def test_the_explanation_is_fixed_english_for_the_model(self):
        about = mm.model_block(sample(), MARKED)["about"]
        self.assertEqual(about, mm.ABOUT)
        self.assertIn("numbered", about)


class ReferenceTests(unittest.TestCase):
    def test_the_marked_picture_goes_first_then_its_original(self):
        original = {"id": ORIGINAL, "kind": "image", "mime": "image/png", "name": "hair.png"}
        marked = {"id": MARKED, "kind": "image", "mime": "image/png", "name": "hair.png (marked up)"}
        sent = mm.send_references(original, marked)
        self.assertEqual([r["id"] for r in sent], [MARKED, ORIGINAL])
        self.assertEqual(sent[0]["markup_of"], ORIGINAL)
        self.assertNotIn("markup_of", original)   # The caller's record is left as it was.


class FlattenTests(unittest.TestCase):
    def test_every_mark_and_pin_is_drawn_marks_first(self):
        ops = mm.flatten_ops(sample())
        kinds = [op[0] for op in ops]
        self.assertEqual(kinds, ["ellipse", "arrow", "stroke", "text", "pin", "region", "pin"])
        self.assertEqual(ops[0][1], [100.4, 200.6, 50, 40])

    def test_widths_and_text_grow_with_the_picture_so_marks_read_the_same(self):
        small = mm.flatten_ops(sample(1024, 512))
        large = mm.flatten_ops(sample(4096, 2048))
        self.assertAlmostEqual(large[0][3] / small[0][3], 4.0)
        self.assertGreater(large[3][4], small[3][4])

    def test_colours_are_rgba(self):
        ops = mm.flatten_ops(sample())
        self.assertEqual(ops[0][2], md.COLOURS["red"])
        self.assertEqual(ops[1][2], md.COLOURS["amber"])
        for op in ops:
            if op[0] in ("ellipse", "rect", "arrow", "stroke", "text", "region"):
                self.assertEqual(len(op[2]), 4)

    def test_the_flattened_size_is_capped_keeping_the_shape(self):
        self.assertEqual(mm.flat_size((1000, 500)), (1000, 500))
        self.assertEqual(mm.flat_size((8192, 4096)), (mm.MAX_FLAT, mm.MAX_FLAT // 2))
        self.assertEqual(mm.flat_size((3, 9000)), (1, 3000))   # A whole step of 3: never past MAX_FLAT.
        self.assertEqual(mm.flat_size((5000, 3000)), (2500, 1500))


if __name__ == "__main__":
    unittest.main()
