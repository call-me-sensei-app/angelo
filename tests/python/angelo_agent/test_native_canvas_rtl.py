# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Right-to-left canvases: what is mirrored, what stays upright, and that input follows the drawing.

Dependency-free: a recording stand-in for ``gpu.matrix`` proves the mirror is
pushed and popped in pairs and undone for upright content; the text helpers
run on real Arabic.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import canvas_rtl, chat_titles, rtl_text, text_layout, ui_text  # noqa: E402

ARABIC = "تم تطبيق التغييرات على المشهد بنجاح"


class FakeMatrix:
    def __init__(self):
        self.depth, self.calls, self.flips = 0, [], 0

    def push(self):
        self.depth += 1
        self.calls.append("push")

    def pop(self):
        self.depth -= 1
        self.calls.append("pop")

    def translate(self, offset):
        self.calls.append(("translate", tuple(offset)))

    def scale(self, factors):
        self.flips += factors[0] < 0
        self.calls.append(("scale", tuple(factors)))


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.matrix = FakeMatrix()
        patcher = patch.dict(sys.modules, {"gpu": types.SimpleNamespace(matrix=self.matrix)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(canvas_rtl.end)

    def rtl(self, value=True):
        return patch.object(ui_text, "language", lambda: "ar" if value else "ja")

    def test_a_left_to_right_language_draws_as_laid_out(self):
        with self.rtl(False):
            canvas_rtl.begin(400)
            self.assertFalse(canvas_rtl.mirrored())
            with canvas_rtl.upright(10, 50) as x:
                self.assertEqual(10, x)
            self.assertEqual((10, 5, 30, 20), canvas_rtl.scissor(10, 5, 30, 20))
            self.assertEqual(10, canvas_rtl.pointer_x(10, 400))
            canvas_rtl.end()
        self.assertEqual([], self.matrix.calls, "no GPU state is touched outside right-to-left languages")

    def test_arabic_mirrors_the_region_and_pops_it_again(self):
        with self.rtl():
            canvas_rtl.begin(400)
            self.assertTrue(canvas_rtl.mirrored())
            self.assertEqual(1, self.matrix.depth)
            self.assertEqual(["push", ("translate", (400, 0)), ("scale", (-1, 1))], self.matrix.calls)
            canvas_rtl.end()
        self.assertEqual(0, self.matrix.depth)
        self.assertFalse(canvas_rtl.mirrored())

    def test_text_and_pictures_stay_upright_at_the_mirrored_place(self):
        with self.rtl():
            canvas_rtl.begin(400)
            with canvas_rtl.upright(10, 50) as x:
                self.assertEqual(340, x, "a box laid out at 10..60 is drawn at 340..390")
                self.assertFalse(canvas_rtl.mirrored(), "inside, drawing is plain left to right")
                self.assertEqual(2, self.matrix.flips, "the second mirror undoes the first")
                self.assertEqual(2, self.matrix.depth)
            self.assertTrue(canvas_rtl.mirrored())
            self.assertEqual(1, self.matrix.depth)
            canvas_rtl.end()

    def test_the_composer_text_field_is_drawn_unmirrored(self):
        with self.rtl():
            canvas_rtl.begin(400)
            with canvas_rtl.unmirrored():
                with canvas_rtl.upright(10, 50) as x:
                    self.assertEqual(10, x, "nested content is not mirrored a second time")
            canvas_rtl.end()
        self.assertEqual(0, self.matrix.depth)

    def test_scissors_and_the_pointer_mirror_with_the_drawing(self):
        with self.rtl():
            canvas_rtl.begin(400)
            self.assertEqual((360, 5, 30, 20), canvas_rtl.scissor(10, 5, 30, 20))
            self.assertEqual((10, 5, 30, 20), canvas_rtl.scissor(*canvas_rtl.scissor(10, 5, 30, 20)), "its own inverse")
            canvas_rtl.end()
            # A button laid out at 10..60 is drawn at 340..390; a click at 350 lands on it.
            self.assertEqual(50, canvas_rtl.pointer_x(350, 400))

    def test_a_failed_draw_still_pops_the_mirror(self):
        with self.rtl():
            canvas_rtl.begin(400)
            try:
                with canvas_rtl.upright(0, 10):
                    raise RuntimeError("draw failed")
            except RuntimeError:
                pass
            canvas_rtl.end()
        self.assertEqual(0, self.matrix.depth)


class TextTests(unittest.TestCase):
    def test_raw_arabic_is_shaped_and_catalogue_text_is_left_alone(self):
        shaped = canvas_rtl.visual(ARABIC)
        self.assertTrue(rtl_text.is_visual(shaped))
        self.assertEqual(ARABIC, rtl_text.to_logical(shaped))
        self.assertEqual(shaped, canvas_rtl.visual(shaped), "already visual: never reordered twice")
        self.assertEqual("Accept · 3 files", canvas_rtl.visual("Accept · 3 files"))

    def test_a_catalogue_comma_does_not_make_visual_text_raw(self):
        visual = rtl_text.to_visual("نعم، تم الحفظ")
        self.assertEqual(visual, canvas_rtl.visual(visual))

    def test_elide_keeps_the_old_left_to_right_result(self):
        def old(value, width, measure):
            if measure(value) <= width:
                return value
            while len(value) > 1 and measure(value + "…") > width:
                value = value[:-1]
            return value + "…"
        measure = lambda text: 7.0 * len(text)  # noqa: E731
        for value in ("Search models or providers", "A", "", "Claude Opus 5.5 · Hyperbond Studio"):
            for width in (0, 7, 30, 100, 400):
                with self.subTest(value=value, width=width):
                    self.assertEqual(old(value, width, measure), canvas_rtl.elide(value, measure, width))

    def test_elide_cuts_arabic_at_the_end_of_the_sentence(self):
        measure = lambda text: 7.0 * len(text)  # noqa: E731
        visual = rtl_text.to_visual(ARABIC)
        cut = canvas_rtl.elide(visual, measure, 100)
        self.assertLessEqual(measure(cut), 100)
        logical = rtl_text.to_logical(cut)
        self.assertTrue(logical.endswith("…"))
        self.assertTrue(ARABIC.startswith(logical[:-1].rstrip()), "the first words survive, the last ones go")
        self.assertTrue(cut.startswith("…"), "drawn left to right, the ellipsis is on the left")

    def test_rows_wrap_in_reading_order(self):
        rows = canvas_rtl.rows(rtl_text.to_visual(ARABIC), lambda text: 7.0 * len(text), 80)
        self.assertGreater(len(rows), 1)
        self.assertEqual(ARABIC, " ".join(rtl_text.to_logical(row) for row in rows))

    def test_has_rtl_is_a_fast_scan_of_hebrew_arabic_and_presentation_forms(self):
        self.assertTrue(canvas_rtl.has_rtl(ARABIC))
        self.assertTrue(canvas_rtl.has_rtl(rtl_text.to_visual(ARABIC)))
        self.assertTrue(canvas_rtl.has_rtl("שלום"))
        for value in ("Angelo", "日本語のテキスト", "ภาษาไทย", "हिन्दी", "Привет"):
            self.assertFalse(canvas_rtl.has_rtl(value), value)


class LineBreakTests(unittest.TestCase):
    """Chinese and Japanese break between characters; spaces elsewhere; never before a combining mark."""

    def test_japanese_breaks_at_the_fit_not_at_a_far_space(self):
        text = "エージェントはこれらの変更を行うために Blender Python を実行します。"
        fits = text.index("Python") + 3
        self.assertEqual(text.index(" Python") + 1, text_layout.line_break(text, text.index(" Python") + 2),
                         "between Latin words the space still wins")
        cut = text_layout.line_break(text, len("エージェントはこれらの変更"))
        self.assertEqual(len("エージェントはこれらの変更"), cut, "the space before Blender is not used")
        self.assertLessEqual(text_layout.line_break(text, fits), fits)

    def test_no_line_starts_with_closing_punctuation(self):
        text = "スナップショットを復元しました。チャットは保持されます。"
        fits = text.index("。")  # The full stop would start the next line.
        cut = text_layout.line_break(text, fits)
        self.assertNotEqual("。", text[cut])
        self.assertLess(cut, fits)

    def test_a_thai_or_devanagari_mark_stays_with_its_letter(self):
        for text in ("เพิ่มการลบมุมที่ขอบของลูกบาศก์แล้ว", "किनारों"):
            for fits in range(1, len(text)):
                with self.subTest(text=text, fits=fits):
                    cut = text_layout.line_break(text, fits)
                    self.assertFalse(cut < len(text) and text_layout._mark(text[cut]), (text[:cut], text[cut:]))

    def test_thai_breaks_between_the_words_its_catalogue_marks(self):
        words = ["ทำริก", "และ", "จัดท่า", "ตัวละคร"]  # "Rig and pose a character", word breaks as U+200B.
        text = text_layout.BREAK.join(words)
        for fits in range(len(words[0]) + 1, len(text)):
            with self.subTest(fits=fits):
                cut = text_layout.line_break(text, fits)
                self.assertEqual(text_layout.BREAK, text[cut - 1], "the line ends at a word boundary")
                self.assertLessEqual(cut, fits + 1)
        self.assertEqual(text_layout.columns("ทำริกและ"), text_layout.columns("ทำริก" + text_layout.BREAK + "และ"))

    def test_english_keeps_breaking_at_spaces(self):
        text = "Review the exact code before approving."
        self.assertEqual(len("Review the "), text_layout.line_break(text, len("Review the ex")))

    def test_native_label_rows_count_wide_characters_twice(self):
        rows = text_layout.label_rows("这是一个很长的中文句子，" * 10, 40)
        self.assertTrue(all(text_layout.columns(row) <= 40 for row in rows))
        self.assertGreater(len(rows), 5)
        self.assertEqual(["Short line."], text_layout.label_rows("Short line."))

    def test_native_label_rows_wrap_arabic_in_reading_order(self):
        sentence = " ".join([ARABIC] * 4)
        rows = text_layout.label_rows(rtl_text.to_visual(sentence), 40)
        self.assertGreater(len(rows), 1)
        self.assertEqual(sentence, " ".join(rtl_text.to_logical(row) for row in rows))

    def test_a_conversation_title_typed_in_arabic_is_readable_in_a_menu(self):
        title = chat_titles.session_title({"title": ARABIC})
        self.assertTrue(rtl_text.is_visual(title))
        self.assertEqual("Make a cube", chat_titles.session_title({"title": "Make a cube"}))


class CardTests(unittest.TestCase):
    """Notice cards mirror like the chat; a box of left-to-right content inside them keeps its reading layout."""

    def draw(self, box_text):
        matrix, drawn = FakeMatrix(), []
        state = types.SimpleNamespace(blend_set=lambda *a: None, scissor_test_set=lambda *a: None,
                                      scissor_set=lambda *a: None)
        stubs = {"gpu": types.SimpleNamespace(matrix=matrix, state=state),
                 "blf": types.SimpleNamespace(size=lambda *a: None, dimensions=lambda *a: (10, 10))}
        with patch.dict(sys.modules, stubs), patch.object(ui_text, "language", lambda: "ar"):
            sys.modules.pop("angelo_agent.card_draw", None)
            from angelo_agent import card_draw
            view = types.SimpleNamespace(_font=0, _mono=0, text=lambda value, *a: drawn.append(
                (value, canvas_rtl.mirrored())))
            plan = types.SimpleNamespace(ops=[("text", ARABIC, 0, 0, 12, None), ("clip", (20, 20, 360, 100)),
                                              ("mono", box_text, 30, 30, 11, None), ("unclip",),
                                              ("text", "Angelo", 0, 0, 12, None)])
            canvas_rtl.begin(400)
            try:
                card_draw.draw(view, plan)
            finally:
                canvas_rtl.end()
        sys.modules.pop("angelo_agent.card_draw", None)
        self.assertEqual(0, matrix.depth, "every push is popped")
        return [mirrored for _, mirrored in drawn]

    def test_an_english_report_box_is_drawn_as_laid_out(self):
        self.assertEqual([True, False, True], self.draw("Traceback (most recent call last):"))

    def test_an_arabic_box_is_mirrored_with_the_card(self):
        self.assertEqual([True, True, True], self.draw(ARABIC))


class WiringTests(unittest.TestCase):
    """Every mirrored canvas begins and ends its draw, and its input un-mirrors the pointer."""

    ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"

    def source(self, name):
        return (self.ADDON / name).read_text(encoding="utf-8")

    def test_each_mirrored_canvas_pairs_begin_and_end(self):
        for name in ("chat_view.py", "intro.py", "connect_hero.py", "card_host.py"):
            with self.subTest(module=name):
                source = self.source(name)
                self.assertEqual(1, source.count("canvas_rtl.begin("))
                self.assertEqual(1, source.count("canvas_rtl.end()"))
                self.assertIn("finally:", source[source.index("canvas_rtl.begin("):source.index("canvas_rtl.end()")])

    def test_each_mirrored_canvas_mirrors_its_pointer(self):
        for name in ("chat_view.py", "intro.py", "connect_hero.py", "card_host.py"):
            with self.subTest(module=name):
                self.assertIn("canvas_rtl.pointer_x(event.mouse_x - region.x, region.width)", self.source(name))

    def test_pictures_and_scissors_follow_the_mirror(self):
        for name in ("studio.py", "native_preview_images.py", "connect_hero.py", "welcome_screen.py"):
            with self.subTest(module=name):
                self.assertIn("upright(", self.source(name))
        for name in ("chat_view.py", "card_draw.py"):
            self.assertNotIn("gpu.state.scissor_set(int(", self.source(name), "scissors go through canvas_rtl.scissor")


if __name__ == "__main__":
    unittest.main()
