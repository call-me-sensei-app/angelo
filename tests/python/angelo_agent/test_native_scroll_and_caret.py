# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scroll direction and the chat caret, without Blender (two user-reported 1.0.3 bugs, fixed as classes).

Scrolling: every Angelo-drawn scrollable surface moves as Blender's own views do (``scroll_input``), which follows
the person's system setting. The sign Blender uses is read from its own source, so this fails if upstream changes
it. Before the fix, all four trackpad handlers (transcript, chat pickers, card scroll boxes, Generative AI columns)
used the opposite sign, so a swipe scrolled against the system setting on every surface.

Caret: text is measured as it is drawn (``chat_view.measure``: rasterised at ``size * scale``, scaled back). Before
the fix the composer's caret, selection and click-to-caret measured at the unscaled size; whole-pixel glyph
advances made that drift from the drawn glyphs on a 2x display, more with every character. The caret is a
character index (``ConsoleLine.current_character``), never a UTF-8 byte offset.
"""

import ast
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


class FakeBlf:
    """Whole-pixel advances at the rasterised size, as Blender's text renderer without subpixel positioning."""

    def __init__(self):
        self.sizes, self.drawn = {}, []

    def size(self, font, size):
        self.sizes[font] = size

    def position(self, font, x, y, z):
        pass

    def color(self, font, *rgba):
        pass

    def draw(self, font, value):
        self.drawn.append((value, self.sizes[font]))

    def dimensions(self, font, value):
        size = self.sizes[font]
        return (sum(round((1.0 if ord(c) > 0x2E80 else 0.55) * size) for c in value), size)


class StubbedCanvas(unittest.TestCase):
    """``chat_view`` and ``chat_composer`` imported with stub Blender modules for the class's tests only.

    The host suite runs in one process: ``patch.dict`` drops every module imported meanwhile when it stops, and the
    package's attributes for them are removed, so no later test imports a module bound to these stubs.
    """

    @classmethod
    def setUpClass(cls):
        stubs = {name: mock.MagicMock(name=name) for name in (
            "bpy", "bpy.props", "bpy.types", "bpy.app", "bpy.app.handlers", "blf", "gpu", "gpu_extras",
            "gpu_extras.batch", "mathutils", "bpy_extras", "bpy_extras.io_utils", "bl_math")}
        cls._before = set(sys.modules)
        cls._modules = mock.patch.dict(sys.modules, stubs)
        cls._modules.start()
        from angelo_agent import chat_composer, chat_view
        cls.view, cls.composer = chat_view, chat_composer

    @classmethod
    def tearDownClass(cls):
        added = [name for name in sys.modules if name not in cls._before and name.startswith("angelo_agent.")]
        cls._modules.stop()
        package = sys.modules.get("angelo_agent")
        for name in added:
            if package is not None and name.count(".") == 1:
                package.__dict__.pop(name.split(".", 1)[1], None)


def pan(prev_y, y):
    return SimpleNamespace(type='TRACKPADPAN', mouse_prev_y=prev_y, mouse_y=y)


class BlenderConvention(unittest.TestCase):
    """What ``scroll_input`` mirrors, read from Blender's own source."""

    def test_view2d_pans_its_view_by_prev_minus_current_for_a_trackpad_pan(self):
        source = (ROOT / "source/blender/editors/interface/view2d/view2d_ops.cc").read_text()
        self.assertIn('RNA_int_set(op->ptr, "deltay", event->prev_xy[1] - event->xy[1]);', source)
        apply = source.split("static void view_pan_apply_ex", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("v2d->cur.ymin += dy;", apply)  # A positive deltay moves the view up.

    def test_the_window_manager_keeps_the_os_delta_and_its_direction(self):
        source = (ROOT / "source/blender/windowmanager/intern/wm_event_system.cc").read_text()
        self.assertIn("int delta[2] = {pd->deltaX, -pd->deltaY};", source)
        self.assertIn("event_new->prev_xy[1] = event_new->xy[1] - deltay;", source)


class ScrollInput(unittest.TestCase):
    def setUp(self):
        from angelo_agent import scroll_input
        self.view_up = scroll_input.view_up

    def test_wheel_notches(self):
        self.assertEqual(self.view_up(SimpleNamespace(type='WHEELUPMOUSE'), 66), 66)
        self.assertEqual(self.view_up(SimpleNamespace(type='WHEELDOWNMOUSE'), 66), -66)
        self.assertIsNone(self.view_up(SimpleNamespace(type='MOUSEMOVE'), 66))

    def test_a_trackpad_pan_moves_the_view_as_view2d_and_follows_the_fingers_pixel_for_pixel(self):
        self.assertEqual(self.view_up(pan(prev_y=340, y=300), 66), 40)
        self.assertEqual(self.view_up(pan(prev_y=300, y=340), 66), -40)
        self.assertEqual(self.view_up(pan(prev_y=340, y=300), 66, scale=2.0), 20)  # 40 window px, 2 px per unit.

    def test_content_travel_is_the_views_travel_reversed(self):
        """A sideways carousel (the welcome's slides) moves its content with the fingers, as a scrolling view's
        content does: always the opposite of the view's own travel, so both follow the system's scroll direction."""
        from angelo_agent import scroll_input
        event = SimpleNamespace(type='TRACKPADPAN', mouse_prev_x=500, mouse_x=440, mouse_prev_y=340, mouse_y=300)
        self.assertEqual(scroll_input.content_travel(event), (-60, -40))
        self.assertEqual(scroll_input.content_travel(event)[1], -self.view_up(event, 66))
        self.assertEqual(scroll_input.content_travel(event, scale=2.0), (-30, -20))
        self.assertIsNone(scroll_input.content_travel(SimpleNamespace(type='WHEELUPMOUSE')))


class EverySurfaceScrollsTheSameWay(unittest.TestCase):
    """No Angelo surface reads trackpad or wheel deltas itself; each goes through ``scroll_input``."""

    # Modules that read ``mouse_prev_*`` for something other than scrolling, with the reason.
    NOT_SCROLLING = {
        "input_policy.py": "detects a real pointer move to refresh hover; no scroll",
        "scroll_input.py": "the one place a scroll delta is read",
    }
    SURFACES = ("chat_scrollbar.py", "chat_view.py", "card_host.py", "studio_input.py")

    def test_only_scroll_input_reads_a_pointer_delta(self):
        for path in sorted(ADDON.glob("*.py")):
            if path.name not in self.NOT_SCROLLING:   # Attribute reads, getattr strings and comments alike.
                self.assertNotIn("mouse_prev_", path.read_text(),
                                 "%s reads a pointer delta itself; use scroll_input.view_up" % path.name)

    def test_every_scrolling_surface_uses_scroll_input(self):
        for name in self.SURFACES:
            self.assertIn("scroll_input.view_up(", (ADDON / name).read_text(), name)

    def test_a_swipe_shows_earlier_content_on_every_surface_when_blender_views_would(self):
        """One swipe Blender's views follow upward; each surface's offset must move toward its top."""
        source = {name: (ADDON / name).read_text() for name in self.SURFACES}
        # Bottom-anchored (0 = newest row): the transcript adds the upward travel.
        self.assertRegex(source["chat_scrollbar.py"], r"scrolls\.get\(session, 0\) \+ scroll_input\.view_up\(")
        # Top-anchored lists and boxes subtract it.
        self.assertIn("delta = -scroll_input.view_up(event, 58, ui_scale())", source["chat_view.py"])
        self.assertIn("delta = -scroll_input.view_up(event, 54 * plan.scale)", source["card_host.py"])
        self.assertIn("step = -scroll_input.view_up(event, 48)", source["studio_input.py"])
        self.assertIn('travel = rest - scroll_input.view_up(event, POPUP_ROW)', source["studio_input.py"])


class CaretMeasuredAsDrawn(StubbedCanvas):
    def setUp(self):
        self.blf = FakeBlf()
        for module in (self.view, self.view.canvas_wrap):
            patch = mock.patch.object(module, "blf", self.blf)
            patch.start()
            self.addCleanup(patch.stop)
        self.view.wrap.cache_clear()
        font, scale = self.view._font, self.view._scale
        self.addCleanup(setattr, self.view, "_font", font)
        self.addCleanup(setattr, self.view, "_scale", scale)
        self.view._font, self.view._scale = 7, 1.0

    def drawn_pen(self, value, size, scale):
        """Where ``chat_view.text`` puts the pen after ``value``: whole-pixel advances at ``size * scale``."""
        self.blf.size(7, size * scale)
        return self.blf.dimensions(7, value)[0] / scale

    def test_measure_matches_the_rasterised_size_on_every_scale(self):
        line = "Make the roof steeper and add a chimney on the left side of the cottage"
        for scale in (1.0, 1.25, 1.5, 2.0, 3.0):
            for size in (13, 14, 17.5):
                self.assertAlmostEqual(self.view.measure(line, size, scale=scale), self.drawn_pen(line, size, scale))

    def test_measure_uses_the_size_text_rasterises_at(self):
        """The real ``text`` draws; ``measure`` must have measured at the size it drew with, on every scale."""
        line = "Steeper roof, brick chimney, two windows"
        for scale in (1.0, 1.37, 2.0):
            self.view._scale, self.blf.drawn = scale, []
            self.view.text(line, 0, 0, 14, shaped=False)
            (drawn, drawn_size), = self.blf.drawn
            self.assertEqual((drawn, drawn_size), (line, 14 * scale))
            self.blf.size(7, drawn_size)
            self.assertAlmostEqual(self.view.measure(line, 14, shaped=False), self.blf.dimensions(7, line)[0] / scale)

    def test_the_unscaled_measure_the_caret_used_drifted_with_line_length(self):
        """The bug's mechanism, so the fix stays justified: 14 px rounds 7.7 to 8; 28 px rounds 15.4 to 15."""
        line = "a" * 80
        self.blf.size(7, 14)
        unscaled = self.blf.dimensions(7, line)[0]
        self.assertGreater(abs(unscaled - self.drawn_pen(line, 14, 2.0)), 30)

    def test_caret_and_click_land_on_the_drawn_glyph_boundaries(self):
        cases = ("plain ascii text that runs long enough to drift a caret by many pixels",
                 "Café crème brûlée, s'il vous plaît", "日本語の文章とEnglish mixed", "emoji 🧊🔥 and ✓ marks")
        for scale in (1.0, 2.0, 1.37):
            self.view._scale = scale
            for value in cases:
                for column in (0, 1, len(value) // 2, len(value)):
                    x = self.composer.column_x(value, column, 14)
                    self.assertAlmostEqual(x, self.composer.DRAFT_LEFT + self.drawn_pen(value[:column], 14, scale))
                    self.assertEqual(self.composer.column_at(value, x, 14), column, (scale, value, column))

    def test_a_click_handled_after_the_draw_uses_the_scale_the_row_was_drawn_at(self):
        self.view._scale = 2.0
        value = "m" * 60
        x = self.composer.column_x(value, 45, 14)
        self.view._scale = 1.0  # The draw has ended; the modal handler runs with the canvas's scale reset.
        self.assertEqual(self.composer.column_at(value, x, 14, scale=2.0), 45)

    def test_wrapped_rows_fit_their_width_when_drawn(self):
        self.view._scale = 2.0
        value = " ".join(["steeper"] * 40)
        for row, _ in self.view.wrap(value, 300, 14, 7, ordered=False):
            self.assertLessEqual(self.drawn_pen(row.rstrip(), 14, 2.0), 300)

    def test_no_chat_drawn_module_measures_text_at_a_size_it_does_not_draw(self):
        """``blf.dimensions`` only where text is measured as drawn, or for something that is not drawn text."""
        allowed = {
            "chat_view.py": "measure(): the one chat measure",
            "canvas_wrap.py": "iter_wrap: measured at size * scale",
            "chat_card_draw.py": "_measure: at the canvas scale, on the shaped text",
            "ui_kit.py": "measure(): the fallback for a view without its own measure",
            "font_watch.py": "probes whether a font still measures; nothing drawn",
            "top_bar_fit.py": "Blender's own header buttons, at the native UI font size",
            "flow_text_field.py": "Blender's own node labels, at the native UI font size, in node units",
            "asset_shelf_add.py": "its pill's label, measured at the size and font it is drawn with",
        }
        for path in sorted(ADDON.glob("*.py")):
            if path.name not in allowed:
                self.assertNotIn("blf.dimensions", path.read_text(), path.name)
        self.assertNotIn("import blf", (ADDON / "chat_composer.py").read_text())


class CaretIsACharacterIndex(StubbedCanvas):
    """``ConsoleLine.current_character`` reads and writes characters (RNA ``BLI_str_utf8_offset_*_index``)."""

    class Line:
        """RNA's behaviour: a character index, clamped to the text on write."""

        def __init__(self, body=""):
            self.body, self._index = body, 0

        @property
        def current_character(self):
            return self._index

        @current_character.setter
        def current_character(self, index):
            self._index = max(0, min(len(self.body), index))

    def test_rna_documents_a_character_index(self):
        source = (ROOT / "source/blender/makesrna/intern/rna_space.cc").read_text()
        getter = source.split("static int rna_ConsoleLine_current_character_get", 1)[1].split("}", 1)[0]
        self.assertIn("BLI_str_utf8_offset_to_index", getter)
        ops = (ROOT / "source/blender/editors/space_console/console_ops.cc").read_text()
        append = ops.split("static wmOperatorStatus console_history_append_exec", 1)[-1].split("\n}\n", 1)[0]
        self.assertIn("console_line_cursor_set(ci, cursor);", append)  # The operator takes bytes.

    def test_every_caret_survives_a_round_trip_in_any_script(self):
        for value in ("plain", "Café ✓", "日本語abc", "🧊 ice 🔥", "mixed é日🧊e"):
            line = self.Line()
            for caret in range(len(value) + 1):
                self.composer.set_draft(line, value, caret)
                self.assertEqual(self.composer.cursor_position(line), caret, (value, caret))

    def test_typing_after_non_ascii_text_inserts_at_the_caret(self):
        line = self.Line()
        self.composer.set_draft(line, "日本語abc", 4)   # After "a": the byte reading put it after "日本".
        value, caret = line.body, self.composer.cursor_position(line)
        self.composer.set_draft(line, value[:caret] + "X" + value[caret:], caret + 1)
        self.assertEqual((line.body, self.composer.cursor_position(line)), ("日本語aXbc", 5))

    def test_history_append_gets_a_byte_offset(self):
        from angelo_agent.drafts import console_byte_offset
        self.assertEqual(console_byte_offset("Café ✓", 6), len("Café ✓".encode("utf-8")))
        self.assertEqual(console_byte_offset("日本語abc", 4), 10)
        source = (ADDON / "editor_types.py").read_text()
        calls = re.findall(r"history_append\(text=body, current_character=([^=]+?\)),", source)
        self.assertEqual(calls, ["console_byte_offset(body, cursor)"])


if __name__ == "__main__":
    unittest.main()
