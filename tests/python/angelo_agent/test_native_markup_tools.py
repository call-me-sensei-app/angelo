# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's tools (``markup_tools``): what a press, a drag, a release, the wheel and a key do in each mode.

Pure: events are Blender's names and region pixels; the document (``markup_doc``) and the view (``markup_view``) are
real. The overlay (``markup_overlay``) only forwards events here and draws what this reports.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_doc as md, markup_tools as mt, markup_view as mv  # noqa: E402

IMAGE = "0b9c6f6e-7a4f-4e4f-9d43-0b1d6d1c2e3a"


def session(mode="markup", tool="pen"):
    s = mt.Session(md.MarkupDoc(IMAGE, (800, 600)), mv.View((800, 600), (0, 0, 800, 600), margins=(0, 0, 0, 0)))
    s.set_mode(mode)
    if tool:
        s.set_tool(tool)
    return s


def at(s, ix, iy):
    return s.view.to_region(ix, iy)


def drag(s, points):
    s.press(*at(s, *points[0]))
    for point in points[1:]:
        s.move(*at(s, *point))
    return s.release(*at(s, *points[-1]))


class DrawingTests(unittest.TestCase):
    def test_a_pen_drag_is_one_stroke_and_one_undo_step(self):
        s = session(tool="pen")
        drag(s, [(10, 10), (20, 30), (40, 10)])
        self.assertEqual(len(s.doc.marks), 1)
        self.assertEqual(s.doc.marks[0]["kind"], "pen")
        self.assertEqual(s.doc.history_size, 1)
        self.assertIsNone(s.pending)

    def test_the_stroke_in_progress_is_shown_before_it_is_a_mark(self):
        s = session(tool="pen")
        s.press(*at(s, 10, 10))
        s.move(*at(s, 50, 50))
        self.assertEqual(s.pending["kind"], "pen")
        self.assertEqual(s.doc.marks, [])

    def test_shapes_and_arrows_follow_the_drag_with_the_chosen_style(self):
        for tool in ("arrow", "ellipse", "rect"):
            s = session(tool=tool)
            s.set_colour("teal")
            s.set_width(3)
            drag(s, [(100, 100), (160, 140)])
            mark = s.doc.marks[0]
            self.assertEqual((mark["kind"], mark["colour"], mark["width"]), (tool, "teal", 3))

    def test_a_click_without_a_drag_draws_nothing(self):
        for tool in ("arrow", "ellipse", "rect"):
            s = session(tool=tool)
            drag(s, [(100, 100), (101, 100)])
            self.assertEqual(s.doc.marks, [], tool)

    def test_drawing_outside_the_picture_starts_nothing(self):
        s = session(tool="pen")
        s.view.set_region((0, 0, 1600, 1200))
        s.view.fit()
        s.press(1, 1)    # The scrim, not the picture.
        self.assertIsNone(s.pending)

    def test_the_text_tool_places_a_field_that_becomes_a_mark(self):
        s = session(tool="text")
        drag(s, [(300, 200), (300, 200)])
        self.assertTrue(s.editing)
        for char in "here":
            s.key("A", unicode=char)
        s.key("RET")
        self.assertEqual(s.doc.marks[0]["text"], "here")
        self.assertFalse(s.editing)

    def test_escape_drops_an_unfinished_text(self):
        s = session(tool="text")
        drag(s, [(300, 200), (300, 200)])
        s.key("A", unicode="x")
        self.assertIsNone(s.key("ESC"))
        self.assertEqual(s.doc.marks, [])
        self.assertFalse(s.editing)


class EditingTests(unittest.TestCase):
    def test_select_drags_a_mark_as_one_step(self):
        s = session(tool="rect")
        drag(s, [(100, 100), (200, 200)])
        s.set_tool("select")
        drag(s, [(100, 150), (130, 170), (150, 190)])
        self.assertEqual(s.doc.marks[0]["box"], [150, 140, 100, 100])
        self.assertEqual(s.doc.history_size, 2)
        self.assertEqual(s.selected, s.doc.marks[0]["id"])

    def test_restyling_applies_to_the_selected_mark(self):
        s = session(tool="rect")
        drag(s, [(100, 100), (200, 200)])
        s.set_tool("select")
        drag(s, [(100, 150), (100, 150)])
        s.set_colour("blue")
        self.assertEqual(s.doc.marks[0]["colour"], "blue")

    def test_delete_removes_the_selected_mark(self):
        s = session(tool="rect")
        drag(s, [(100, 100), (200, 200)])
        s.set_tool("select")
        drag(s, [(100, 150), (100, 150)])
        s.key("DEL")
        self.assertEqual(s.doc.marks, [])
        self.assertIsNone(s.selected)

    def test_the_eraser_removes_each_mark_it_crosses(self):
        s = session(tool="pen")
        drag(s, [(100, 50), (100, 250)])
        drag(s, [(200, 50), (200, 250)])
        drag(s, [(400, 50), (400, 250)])
        s.set_tool("eraser")
        drag(s, [(50, 150), (150, 150), (250, 150)])
        self.assertEqual([m["points"][0][0] for m in s.doc.marks], [400])

    def test_undo_and_redo_keys(self):
        s = session(tool="rect")
        drag(s, [(100, 100), (200, 200)])
        s.key("Z", command=True)
        self.assertEqual(s.doc.marks, [])
        s.key("Z", command=True, shift=True)
        self.assertEqual(len(s.doc.marks), 1)


class CommentTests(unittest.TestCase):
    def test_a_click_pins_a_comment_and_opens_its_note(self):
        s = session(mode="comment", tool=None)
        drag(s, [(120, 80), (120, 80)])
        self.assertEqual(s.doc.comments[0]["at"], [120, 80])
        self.assertTrue(s.editing)
        for char in "too dark":
            s.key("A", unicode=char)
        s.key("RET")
        self.assertEqual(s.doc.comments[0]["text"], "too dark")

    def test_a_drag_comments_on_a_region(self):
        s = session(mode="comment", tool=None)
        drag(s, [(100, 100), (180, 160)])
        self.assertEqual(s.doc.comments[0]["box"], [100, 100, 80, 60])

    def test_escape_on_a_new_empty_pin_removes_it(self):
        s = session(mode="comment", tool=None)
        drag(s, [(120, 80), (120, 80)])
        s.key("ESC")
        self.assertEqual(s.doc.comments, [])


class ViewAndKeysTests(unittest.TestCase):
    def test_the_wheel_zooms_about_the_pointer_and_f_fits(self):
        s = session(mode="view", tool=None)
        before = s.view.scale
        s.wheel(400, 300, 1)
        self.assertGreater(s.view.scale, before)
        s.key("F")
        self.assertEqual(s.view.scale, before)

    def test_a_middle_drag_pans_in_every_mode(self):
        s = session(tool="pen")
        origin = s.view.to_region(0, 0)
        s.press(400, 300, button="MIDDLEMOUSE")
        s.move(450, 280)
        s.release(450, 280, button="MIDDLEMOUSE")
        moved = s.view.to_region(0, 0)
        self.assertAlmostEqual(moved[0] - origin[0], 50)
        self.assertEqual(s.doc.marks, [])

    def test_tool_keys_come_from_one_table(self):
        s = session(tool="pen")
        for key, tool in mt.TOOL_KEYS.items():
            s.key(key)
            self.assertEqual(s.tool, tool)
        self.assertEqual(set(mt.TOOL_KEYS.values()) - {"comment"}, set(mt.TOOLS))

    def test_escape_closes_an_empty_viewer_and_asks_before_dropping_marks(self):
        self.assertEqual(session(mode="view", tool=None).key("ESC"), "close")
        s = session(tool="rect")
        drag(s, [(100, 100), (200, 200)])
        self.assertEqual(s.key("ESC"), "ask_discard")

    def test_typing_goes_to_the_instructions_and_return_sends(self):
        s = session(tool="pen")
        s.focus_instructions()
        for char in "fix it":
            s.key("A", unicode=char)
        self.assertEqual(s.doc.instructions, "fix it")
        self.assertEqual(s.key("RET"), "send")

    def test_send_needs_something_to_send(self):
        s = session(tool="pen")
        s.focus_instructions()
        self.assertIsNone(s.key("RET"))
        self.assertFalse(s.can_send)

    def test_a_tool_key_while_typing_is_a_letter(self):
        s = session(tool="pen")
        s.focus_instructions()
        s.key("R", unicode="r")
        self.assertEqual(s.tool, "pen")
        self.assertEqual(s.doc.instructions, "r")


class BoundTests(unittest.TestCase):
    """Break-it of 2298d608dd1, M2: the 21st comment, the 201st mark and a comment drag with no area raised out of the
    viewer's modal, which left a dead scrim over the 3D view. Each is refused with a sentence and the session goes on."""

    def test_the_21st_comment_is_refused_with_a_sentence(self):
        s = session(mode="comment", tool=None)
        for n in range(md.MAX_COMMENTS):
            s.doc.add_comment(at=(10 + n, 10), text="n%d" % n)
        drag(s, [(300, 300)])
        self.assertEqual(len(s.doc.comments), md.MAX_COMMENTS)
        self.assertEqual(s.notice, (mt.FULL_COMMENTS, {"count": md.MAX_COMMENTS}))
        self.assertIsNone(s.editing, "no note opens for a pin that was not placed")

    def test_the_201st_mark_is_refused_with_a_sentence(self):
        for tool, points in (("pen", [(10, 10), (40, 40)]), ("arrow", [(10, 10), (60, 60)]),
                             ("rect", [(10, 10), (60, 60)])):
            with self.subTest(tool=tool):
                s = session(tool=tool)
                for n in range(md.MAX_MARKS):
                    s.doc.add_mark("pen", points=[[n, n], [n + 5, n + 5]])
                drag(s, points)
                self.assertEqual(len(s.doc.marks), md.MAX_MARKS)
                self.assertEqual(s.notice, (mt.FULL_MARKS, {"count": md.MAX_MARKS}))

    def test_a_comment_drag_with_no_area_pins_a_point(self):
        for end in ((260, 100), (100, 260)):   # Exactly horizontal, exactly vertical.
            with self.subTest(end=end):
                s = session(mode="comment", tool=None)
                drag(s, [(100, 100), end])
                self.assertEqual(len(s.doc.comments), 1)
                self.assertEqual(s.doc.comments[0]["at"], [100, 100])

    def test_a_notice_lasts_until_the_next_press(self):
        s = session(mode="comment", tool=None)
        for n in range(md.MAX_COMMENTS):
            s.doc.add_comment(at=(10 + n, 10))
        drag(s, [(300, 300)])
        self.assertIsNotNone(s.notice)
        s.set_mode("markup")
        s.set_tool("pen")
        s.press(*at(s, 50, 50))
        self.assertIsNone(s.notice)


class EraseTests(unittest.TestCase):
    """Erase as it behaves with the switch on (``markup_tools.AI_EDITS``, off until a live proof): the on state stays
    tested, so turning it on ships tested behaviour."""

    def setUp(self):
        from unittest import mock
        patcher = mock.patch.object(mt, "AI_EDITS", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_brush_drag_is_an_erase_stroke_never_a_mark(self):
        s = session(mode="erase", tool=None)
        drag(s, [(100, 100), (200, 120)])
        self.assertEqual(len(s.erase_strokes), 1)
        self.assertEqual(s.doc.marks, [])
        self.assertGreater(s.erase_strokes[0]["radius"], 0)

    def test_the_brush_size_sets_the_radius(self):
        s = session(mode="erase", tool=None)
        s.set_width(1)
        drag(s, [(100, 100), (200, 120)])
        s.set_width(3)
        drag(s, [(100, 300), (200, 320)])
        self.assertLess(s.erase_strokes[0]["radius"], s.erase_strokes[1]["radius"])

    def test_undo_takes_back_the_last_stroke_and_clear_takes_them_all(self):
        s = session(mode="erase", tool=None)
        drag(s, [(100, 100), (200, 120)])
        drag(s, [(100, 300), (200, 320)])
        s.key("Z", command=True)
        self.assertEqual(len(s.erase_strokes), 1)
        s.clear_erase()
        self.assertEqual(s.erase_strokes, [])

    def test_erase_asks_before_it_runs_and_only_with_strokes(self):
        s = session(mode="erase", tool=None)
        self.assertIsNone(s.ask("erase"))
        drag(s, [(100, 100), (200, 120)])
        s.ask("erase")
        self.assertEqual(s.confirm, "erase")
        self.assertIsNone(s.key("RET"), "a bare Return never answers it (break-it of 2298d608dd1, M3)")
        self.assertEqual(s.confirm, "erase")
        self.assertEqual(s.answer(True), "run")   # Its Run button: a click, or the keyboard's armed press on it.
        self.assertIsNone(s.confirm)

    def test_escape_cancels_a_question_before_anything_else(self):
        s = session(mode="view", tool=None)
        s.ask("remove_bg")
        self.assertIsNone(s.key("ESC"))
        self.assertIsNone(s.confirm)
        self.assertEqual(s.key("ESC"), "close")

    def test_drawing_waits_while_a_question_is_open(self):
        s = session(mode="erase", tool=None)
        drag(s, [(100, 100), (200, 120)])
        s.ask("erase")
        drag(s, [(300, 300), (400, 320)])
        self.assertEqual(len(s.erase_strokes), 1)


class ResizeTests(unittest.TestCase):
    def test_a_drag_crops_and_a_click_clears_the_crop(self):
        s = session(mode="resize", tool=None)
        drag(s, [(100, 100), (300, 250)])
        self.assertEqual(s.crop, (100, 100, 200, 150))
        drag(s, [(50, 50), (50, 50)])
        self.assertIsNone(s.crop)

    def test_the_plan_follows_the_crop_and_scale(self):
        s = session(mode="resize", tool=None)
        drag(s, [(100, 100), (300, 250)])
        s.set_scale(50)
        self.assertEqual(s.resize_plan()["size"], (100, 75))
        self.assertTrue(s.resize_plan()["changed"])


if __name__ == "__main__":
    unittest.main()
