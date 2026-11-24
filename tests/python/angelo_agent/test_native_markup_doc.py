# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture markup's document (``markup_doc``): marks and comments in image pixels, its own undo, its bounds.

No Blender here. The viewer (``markup_overlay``) only calls these commands; what reaches the model is built from the
document by ``markup_message`` (``test_native_markup_message``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_doc as md  # noqa: E402

IMAGE = "0b9c6f6e-7a4f-4e4f-9d43-0b1d6d1c2e3a"


def doc(w=1000, h=500):
    return md.MarkupDoc(IMAGE, (w, h))


class MarkTests(unittest.TestCase):
    def test_every_tool_makes_a_mark_in_image_pixels(self):
        d = doc()
        pen = d.add_mark("pen", points=[(10, 10), (20, 20), (30, 10)])
        arrow = d.add_mark("arrow", start=(5, 5), end=(50, 60))
        ellipse = d.add_mark("ellipse", box=(100, 100, 40, 20))
        rect = d.add_mark("rect", box=(200, 100, 40, 20))
        text = d.add_mark("text", at=(300, 300), text="this one")
        self.assertEqual([m["kind"] for m in d.marks], ["pen", "arrow", "ellipse", "rect", "text"])
        self.assertEqual(len({pen, arrow, ellipse, rect, text}), 5)
        self.assertEqual(d.mark(ellipse)["box"], [100, 100, 40, 20])
        self.assertEqual(d.mark(text)["text"], "this one")
        for mark in d.marks:
            self.assertIn(mark["colour"], md.COLOURS)
            self.assertIn(mark["width"], md.WIDTHS)

    def test_a_mark_is_kept_inside_the_picture(self):
        d = doc(100, 100)
        pen = d.add_mark("pen", points=[(-20, 50), (150, 50)])
        box = d.add_mark("rect", box=(90, 90, 40, 40))
        self.assertEqual(d.mark(pen)["points"], [[0, 50], [100, 50]])
        self.assertEqual(d.mark(box)["box"], [90, 90, 10, 10])

    def test_a_drag_written_backwards_is_the_same_box(self):
        d = doc()
        self.assertEqual(d.mark(d.add_mark("ellipse", box=(140, 120, -40, -20)))["box"], [100, 100, 40, 20])

    def test_unknown_tools_colours_and_widths_are_refused(self):
        d = doc()
        with self.assertRaises(ValueError):
            d.add_mark("spray", points=[(0, 0)])
        with self.assertRaises(ValueError):
            d.add_mark("pen", points=[(0, 0), (1, 1)], colour="chartreuse")
        with self.assertRaises(ValueError):
            d.add_mark("pen", points=[(0, 0), (1, 1)], width=9)

    def test_an_empty_stroke_or_box_or_text_is_no_mark(self):
        d = doc()
        for kind, fields in (("pen", {"points": []}), ("ellipse", {"box": (10, 10, 0, 5)}),
                             ("text", {"at": (1, 1), "text": "   "}), ("arrow", {"start": (4, 4), "end": (4, 4)})):
            self.assertIsNone(d.add_mark(kind, **fields), kind)
        self.assertEqual(d.marks, [])
        self.assertFalse(d.can_undo)

    def test_a_long_stroke_is_simplified_and_capped(self):
        d = doc(5000, 5000)
        straight = [(x, 100) for x in range(0, 4000)]
        self.assertEqual(d.mark(d.add_mark("pen", points=straight))["points"], [[0, 100], [3999, 100]])
        zigzag = [(x, 100 + (x % 2) * 50) for x in range(0, 4000)]
        self.assertLessEqual(len(d.mark(d.add_mark("pen", points=zigzag))["points"]), md.MAX_POINTS)

    def test_the_number_of_marks_is_bounded(self):
        d = doc()
        for index in range(md.MAX_MARKS):
            d.add_mark("rect", box=(index % 900, 10, 5, 5))
        with self.assertRaises(ValueError):
            d.add_mark("rect", box=(1, 1, 5, 5))

    def test_texts_are_bounded(self):
        d = doc()
        self.assertEqual(len(d.mark(d.add_mark("text", at=(1, 1), text="x" * 5000))["text"]), md.MAX_TEXT)


class CommentTests(unittest.TestCase):
    def test_comments_are_numbered_in_order_and_renumbered_after_a_removal(self):
        d = doc()
        a = d.add_comment(at=(10, 10), text="too dark")
        b = d.add_comment(box=(100, 100, 50, 50), text="keep this")
        c = d.add_comment(at=(400, 200), text="move left")
        self.assertEqual([d.number(x) for x in (a, b, c)], [1, 2, 3])
        d.remove(b)
        self.assertEqual([d.number(x) for x in (a, c)], [1, 2])
        d.undo()
        self.assertEqual([d.number(x) for x in (a, b, c)], [1, 2, 3])

    def test_a_comment_is_a_point_or_a_region_never_both(self):
        d = doc()
        with self.assertRaises(ValueError):
            d.add_comment(at=(1, 1), box=(1, 1, 5, 5))
        with self.assertRaises(ValueError):
            d.add_comment()

    def test_a_comment_may_wait_for_its_text_and_is_bounded(self):
        d = doc()
        pin = d.add_comment(at=(5, 5))
        self.assertEqual(d.comment(pin)["text"], "")
        d.set_text(pin, "y" * 9000)
        self.assertEqual(len(d.comment(pin)["text"]), md.MAX_COMMENT)
        for _ in range(md.MAX_COMMENTS - 1):
            d.add_comment(at=(5, 5))
        with self.assertRaises(ValueError):
            d.add_comment(at=(5, 5))


class UndoTests(unittest.TestCase):
    def test_every_command_undoes_and_redoes(self):
        d = doc()
        pen = d.add_mark("pen", points=[(0, 0), (10, 10)])
        d.move(pen, 5, 5)
        d.restyle(pen, colour="teal", width=3)
        note = d.add_comment(at=(50, 50), text="a")
        d.set_text(note, "b")
        d.remove(pen)
        states = []
        while d.can_undo:
            states.append(d.to_dict())
            d.undo()
        self.assertEqual(d.marks, [])
        self.assertEqual(d.comments, [])
        for expected in reversed(states):
            d.redo()
            self.assertEqual(d.to_dict(), expected)
        self.assertFalse(d.can_redo)

    def test_a_new_command_clears_redo(self):
        d = doc()
        d.add_mark("rect", box=(1, 1, 5, 5))
        d.undo()
        self.assertTrue(d.can_redo)
        d.add_mark("rect", box=(2, 2, 5, 5))
        self.assertFalse(d.can_redo)

    def test_retract_takes_back_a_step_without_a_redo(self):
        d = doc()
        d.add_mark("rect", box=(1, 1, 5, 5))
        d.add_comment(at=(3, 3))
        d.retract()
        self.assertEqual(d.comments, [])
        self.assertEqual(len(d.marks), 1)
        self.assertFalse(d.can_redo)
        self.assertEqual(d.history_size, 1)

    def test_the_history_is_bounded(self):
        d = doc()
        mark = d.add_mark("rect", box=(1, 1, 5, 5))
        for _ in range(md.MAX_HISTORY + 50):
            d.move(mark, 1, 0)
            d.move(mark, -1, 0)
        undone = 0
        while d.can_undo:
            d.undo()
            undone += 1
        self.assertEqual(undone, md.MAX_HISTORY)

    def test_a_move_stays_inside_the_picture(self):
        d = doc(100, 100)
        box = d.add_mark("rect", box=(80, 80, 10, 10))
        d.move(box, 50, 50)
        self.assertEqual(d.mark(box)["box"], [90, 90, 10, 10])
        pen = d.add_mark("pen", points=[(10, 10), (20, 20)])
        d.move(pen, -50, 0)
        self.assertEqual(d.mark(pen)["points"], [[0, 10], [10, 20]])

    def test_a_command_that_changes_nothing_is_not_a_step(self):
        d = doc()
        box = d.add_mark("rect", box=(10, 10, 5, 5))
        steps = d.history_size
        d.move(box, 0, 0)
        d.restyle(box, colour=d.mark(box)["colour"], width=d.mark(box)["width"])
        self.assertEqual(d.history_size, steps)

    def test_the_eraser_removes_the_topmost_mark_under_the_point(self):
        d = doc()
        low = d.add_mark("rect", box=(0, 0, 100, 100))
        high = d.add_mark("ellipse", box=(40, 40, 20, 20))
        self.assertEqual(d.hit((41, 50), tolerance=4), high)   # On the ellipse's outline, over the rectangle.
        self.assertEqual(d.hit((2, 50), tolerance=4), low)     # On the rectangle's edge.
        self.assertIsNone(d.hit((30, 30), tolerance=4))       # Inside an outline is not on it.
        self.assertIsNone(d.hit((50, 50), tolerance=4))       # Nor is the ellipse's centre.
        words = d.add_mark("text", at=(500, 400), text="here")
        self.assertEqual(d.hit((505, 405), tolerance=4), words)   # A text is hit anywhere on its words.
        line = d.add_mark("pen", points=[(200, 200), (300, 200)])
        self.assertEqual(d.hit((250, 203), tolerance=4), line)


class SaveTests(unittest.TestCase):
    def test_a_draft_round_trips_through_json(self):
        d = doc()
        d.add_mark("pen", points=[(0, 0), (10, 10)], colour="amber", width=1)
        d.add_mark("text", at=(3, 4), text="لون")
        d.add_comment(box=(10, 10, 20, 20), text="رأس")
        d.instructions = "Make the hair redder here"
        again = md.MarkupDoc.from_dict(json.loads(json.dumps(d.to_dict())))
        self.assertEqual(again.to_dict(), d.to_dict())
        self.assertEqual(again.next_id, d.next_id)

    def test_a_damaged_or_foreign_draft_is_refused_whole(self):
        good = doc().to_dict()
        for broken in ({**good, "schema": 99}, {**good, "image": "../etc/passwd"}, {**good, "size": [0, 10]},
                       {**good, "marks": [{"id": "m1", "kind": "spray"}]}, {**good, "marks": "x"},
                       {**good, "comments": [{"id": "c1", "text": "no place"}]}):
            with self.assertRaises(ValueError, msg=repr(broken)[:80]):
                md.MarkupDoc.from_dict(broken)

    def test_only_a_canonical_reference_id_names_the_picture(self):
        for bad in ("", "x", "../a", IMAGE.upper() + "/", None):
            with self.assertRaises(ValueError):
                md.MarkupDoc(bad, (10, 10))
        with self.assertRaises(ValueError):
            md.MarkupDoc(IMAGE, (0, 10))

    def test_an_empty_document_has_nothing_to_send(self):
        d = doc()
        self.assertTrue(d.empty)
        d.instructions = "  "
        self.assertTrue(d.empty)
        d.instructions = "crop it"
        self.assertFalse(d.empty)


if __name__ == "__main__":
    unittest.main()
