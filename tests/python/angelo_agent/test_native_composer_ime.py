# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An input method (Japanese, Korean, Chinese) types into the composer as into a text field: through the chat's real
input handler (``chat_view.ANGELO_OT_chat_input.modal``) and the composer's real draw (``chat_composer.draw``).

1.1.0 check (the B2 report of 2026-11-11): Blender 5.3 made the Console an input method client, and the composer is
drawn over a Console area. The candidate window opened at the Console's own hidden line, nothing showed while
composing, and the committed text went in through ``console.insert`` past the composer: no selection replaced, no
Undo step, shown only at the next redraw. Each test here fails on that code: the handler passed the input method's
events to the Console (the draft unchanged) and the draw had no composition and no session at its caret.

The window is a stand-in with the input method's state as ``Window`` reports it (``ime_composition``, ...). The C++
side (the ``Script`` session, the events, ``Window.ime_simulate``) is pinned by ``WmImeSourceTests``; the running
app is ``test_blender_composer_ime_gui``, and ``test_blender_keymap_event_types`` checks no key-map item takes one.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import test_native_composer_input as composer_tests  # noqa: E402  (its stand-ins; its tests are not collected here)
import qa_hit  # noqa: E402

SIZE = composer_tests.SIZE
MAC = sys.platform == "darwin"


class InputMethod:
    """``Window``'s input method state, and the session calls the composer makes (``ime_attach``, ``ime_detach``)."""

    def __init__(self):
        self.composing, self.composition, self.cursor, self.segment, self.result = False, "", -1, (-1, -1), ""
        self.owner, self.attached = "NONE", []

    def apply(self, window):
        window.is_ime_composing = self.composing
        window.ime_composition, window.ime_composition_cursor = self.composition, self.cursor
        window.ime_composition_selection, window.ime_result = self.segment, self.result
        window.ime_owner = self.owner

    def attach(self, x, y, height):
        self.attached.append((x, y, height))
        self.owner = "SCRIPT"
        return True

    def detach(self):
        self.attached.append(None)
        self.owner = "NONE"


class ComposerIme(composer_tests.ComposerCase):
    def window_point(self, region, x, y):
        """Composer text point (x, y) in window pixels at this case's scale: scaled, never mirrored (qa_hit)."""
        with mock.patch.object(qa_hit, "ui_scale", return_value=self.SCALE):
            return qa_hit.xy(qa_hit.COMPOSER_TEXT, region, x, y)

    def setUp(self):
        super().setUp()
        from angelo_agent import composer_ime
        self.patch(composer_ime, "_ATTACHED", {})
        self.ime = InputMethod()
        window = self.context.window
        window.ime_attach, window.ime_detach = self.ime.attach, self.ime.detach
        self.ime.apply(window)
        region = self.area.regions[0]
        region.x, region.y = 100, 40   # Window pixels: the candidate window is placed in them.
        self.bpy_context = SimpleNamespace(window=window, region=region, area=self.area,
                                           window_manager=SimpleNamespace(windows=[window]))
        patcher = mock.patch.dict(sys.modules, {"bpy": SimpleNamespace(context=self.bpy_context)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def compose(self, kind, composition="", cursor=-1, result="", segment=(-1, -1)):
        """One input method event, as GHOST reports it (``WM_event_IME_apply``), through the handler."""
        self.ime.composing = kind != "IME_COMPOSITE_END" and bool(composition or kind == "IME_COMPOSITE_START")
        self.ime.composition, self.ime.cursor, self.ime.result, self.ime.segment = composition, cursor, result, segment
        self.ime.apply(self.context.window)
        return self.send(self.event(kind, *self.centre()))

    def commit(self, text):
        """Composing ``text`` in kana and committing it, as a Japanese input method does."""
        self.compose("IME_COMPOSITE_START", "", 0)
        self.compose("IME_COMPOSITE_EVENT", "か", 1)
        self.compose("IME_COMPOSITE_EVENT", "かな", 2)
        result = self.compose("IME_COMPOSITE_EVENT", "", -1, result=text)
        self.compose("IME_COMPOSITE_END")
        return result

    def drawn_text(self):
        return "".join(value for value, x, y in self.texts if value not in {"Send (Enter)"})


class CommitThroughTheComposer(ComposerIme):
    def test_the_commit_types_into_the_draft_at_the_caret(self):
        self.line.body, self.line.current_character = "hello ", 6
        self.draw()
        self.assertEqual(self.commit("世界"), {'RUNNING_MODAL'})
        self.assertEqual(self.line.body, "hello 世界")
        self.assertEqual(self.line.current_character, 8)

    def test_every_composition_event_is_the_composers_never_the_consoles(self):
        self.draw()
        for kind, text in (("IME_COMPOSITE_START", ""), ("IME_COMPOSITE_EVENT", "か"), ("IME_COMPOSITE_END", "")):
            with self.subTest(kind=kind):
                self.assertEqual(self.compose(kind, text, len(text)), {'RUNNING_MODAL'},
                                 "passed on, console.insert types it past the composer")

    def test_the_commit_replaces_the_selection(self):
        self.line.body, self.line.current_character = "hello world", 11
        self.draw()   # The chat's first frame here adopts it, dropping any other chat's selection: select after.
        self.state["selection"] = (6, 11)
        self.commit("世界")
        self.assertEqual(self.line.body, "hello 世界")
        self.assertIsNone(self.state["selection"])

    def test_one_undo_takes_the_commit_back_with_the_selection(self):
        self.line.body, self.line.current_character = "hello world", 11
        self.draw()   # The chat's first frame here adopts it, dropping any other chat's selection: select after.
        self.state["selection"] = (6, 11)
        self.commit("世界")
        self.assertEqual(self.command('Z'), {'RUNNING_MODAL'})
        self.assertEqual(self.line.body, "hello world")
        self.assertEqual(self.state["selection"], (6, 11))

    def test_commits_typed_in_a_row_are_one_undo_step_like_typing(self):
        self.line.body = ""
        self.draw()
        self.commit("日本")
        self.commit("語")
        self.assertEqual(self.line.body, "日本語")
        self.command('Z')
        self.assertEqual(self.line.body, "")

    def test_the_commit_redraws_at_once(self):
        self.draw()
        before = self.area.redraws
        self.compose("IME_COMPOSITE_EVENT", "", -1, result="字")
        self.assertGreater(self.area.redraws, before, "shown only at the next unrelated redraw")

    def test_a_hidden_composer_takes_no_text_and_leaves_it_to_no_one(self):
        self.line.body = ""
        self.draw()
        self.state["composer_hidden"] = True   # No connection yet: Tab and Return pick a connect option.
        self.assertEqual(self.compose("IME_COMPOSITE_EVENT", "", -1, result="字"), {'RUNNING_MODAL'})
        self.assertEqual(self.line.body, "")


class KeysWhileComposing(ComposerIme):
    def test_the_input_methods_keys_never_edit_the_draft(self):
        self.line.body, self.line.current_character = "abc", 3
        self.draw()
        self.compose("IME_COMPOSITE_EVENT", "かな", 2)
        for kind in ('BACK_SPACE', 'LEFT_ARROW', 'DEL', 'A'):
            with self.subTest(key=kind):
                self.assertEqual(self.send(self.event(kind, *self.centre(), unicode="a" if kind == 'A' else "")),
                                 {'RUNNING_MODAL'})
                self.assertEqual((self.line.body, self.line.current_character), ("abc", 3))

    def test_return_converts_the_composition_and_never_sends(self):
        sent = []
        self.patch(sys.modules["angelo_agent.console"], "execute", lambda context: sent.append(True))
        self.line.body = "abc"
        self.draw()
        self.compose("IME_COMPOSITE_EVENT", "かな", 2)
        self.assertEqual(self.send(self.event('RET', *self.centre())), {'RUNNING_MODAL'})
        self.assertEqual(sent, [])

    def test_after_the_composition_the_keys_are_the_composers_again(self):
        self.line.body, self.line.current_character = "abc", 3
        self.draw()
        self.commit("字")
        self.send(self.event('BACK_SPACE', *self.centre()))
        self.assertEqual(self.line.body, "abc")


class ShownInline(ComposerIme):
    def test_the_composition_is_drawn_in_the_draft_where_it_lands(self):
        self.line.body, self.line.current_character = "hello world", 6
        self.draw()
        self.compose("IME_COMPOSITE_EVENT", "にほん", 3)
        self.draw()
        self.assertIn("hello にほんworld", self.drawn_text())
        self.assertEqual(self.line.body, "hello world", "nothing is in the draft until it is committed")

    def test_the_caret_is_drawn_inside_the_composition(self):
        from angelo_agent import chat_composer
        self.line.body, self.line.current_character = "ab", 2
        self.draw()
        self.compose("IME_COMPOSITE_EVENT", "かな", 1)
        self.draw()
        (x, y), = self.carets()
        self.assertAlmostEqual(x, chat_composer.column_x("abかな", 3, SIZE))

    def test_the_composition_is_underlined_and_its_converted_part_more_thickly(self):
        from angelo_agent import chat_composer
        self.line.body, self.line.current_character = "", 0
        self.draw()
        self.compose("IME_COMPOSITE_EVENT", "日本語", 3, segment=(0, 2))
        self.draw()
        lines = [(x, w, h) for x, y, w, h in self.shapes if h in (1, 2) and w > 1]
        start = chat_composer.column_x("日本語", 0, SIZE)
        self.assertIn((start, chat_composer.column_x("日本語", 3, SIZE) - start, 1), lines)
        self.assertIn((start, chat_composer.column_x("日本語", 2, SIZE) - start, 2), lines)

    def test_a_composition_over_a_selection_shows_in_its_place(self):
        self.line.body, self.line.current_character = "hello world", 11
        self.draw()   # The chat's first frame here adopts it, dropping any other chat's selection: select after.
        self.state["selection"] = (6, 11)
        self.compose("IME_COMPOSITE_EVENT", "せかい", 3)
        self.draw()
        self.assertIn("hello せかい", self.drawn_text())
        self.assertNotIn("world", self.drawn_text())

    def test_the_composition_is_gone_when_it_ends_uncommitted(self):
        self.line.body = "abc"
        self.draw()
        self.compose("IME_COMPOSITE_EVENT", "かな", 2)
        self.compose("IME_COMPOSITE_END")
        self.draw()
        self.assertNotIn("かな", self.drawn_text())
        self.assertEqual(self.line.body, "abc")


class CandidateWindowAtTheCaret(ComposerIme):
    def test_the_composer_with_the_keyboard_holds_the_input_method_at_its_caret(self):
        self.line.body, self.line.current_character = "abc", 3
        self.draw()
        (x, y), = self.carets()
        region = self.area.regions[0]
        self.assertEqual(self.ime.attached[-1][:2], tuple(int(v) for v in self.window_point(region, x, y)))
        self.assertEqual(self.ime.owner, "SCRIPT")

    def test_the_session_follows_the_caret_while_composing(self):
        from angelo_agent import chat_composer
        self.line.body, self.line.current_character = "", 0
        self.draw()
        self.compose("IME_COMPOSITE_EVENT", "かな", 2)
        self.draw()
        self.assertEqual(self.ime.attached[-1][0], int(self.window_point(
            self.area.regions[0], chat_composer.column_x("かな", 2, SIZE), 0)[0]))

    def test_a_composer_without_the_keyboard_leaves_the_input_method(self):
        self.state["focused"] = False
        self.draw()
        self.assertEqual(self.ime.attached, [])

    def test_escape_lets_the_keyboard_and_the_input_method_go(self):
        self.draw()
        self.assertEqual(self.ime.owner, "SCRIPT")
        self.send(self.event('ESC', *self.centre()))
        self.assertEqual(self.ime.attached[-1], None, "the session would outlive the chat's keyboard")

    def test_a_waiting_composer_holds_no_session(self):
        from angelo_agent import composer_ime
        composer_ime._ATTACHED[self.context.window.as_pointer()] = composer_tests.AREA
        self.state["focused"] = True
        with mock.patch.object(self.view, "_scale", self.SCALE):
            composer_ime.place(self.state, 10, 10, 10, False)
        self.assertEqual(self.ime.attached, [None])


class ComposerImeAtOneAndAHalf(CandidateWindowAtTheCaret):
    SCALE = 1.5


class WmImeSourceTests(unittest.TestCase):
    """The C++ the composer relies on (the host suite builds no Blender; the GUI suite runs it)."""

    @staticmethod
    def read(relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_the_composition_events_and_state_reach_python(self):
        rna = self.read("source/blender/makesrna/intern/rna_wm.cc")
        for name in ("IME_COMPOSITE_START", "IME_COMPOSITE_EVENT", "IME_COMPOSITE_END"):
            self.assertIn('"%s"' % name, rna)
        for name in ("ime_owner", "is_ime_composing", "ime_composition", "ime_composition_cursor",
                     "ime_composition_selection", "ime_result"):
            self.assertIn('RNA_def_property(srna, "%s"' % name, rna)
        self.assertIn("add_definitions(-DWITH_INPUT_IME)", self.read("source/blender/makesrna/intern/CMakeLists.txt"))
        api = self.read("source/blender/makesrna/intern/rna_wm_api.cc")
        for name in ("ime_attach", "ime_detach", "ime_simulate"):
            self.assertIn('RNA_def_function(srna, "%s"' % name, api)

    def test_the_composition_events_are_never_a_key_maps(self):
        """Only ``Event.type`` lists them: the key-map's list (``rna_enum_event_type_items``, also
        ``keymap_items.new``) stays Blender's, so no key-map item is ever bound to an event no key press makes.
        ``test_blender_keymap_event_types`` checks it in the running Blender."""
        rna = self.read("source/blender/makesrna/intern/rna_wm.cc")
        shared = rna[rna.index("const EnumPropertyItem rna_enum_event_type_items[] = {"):]
        shared = shared[:shared.index("{0, nullptr, 0, nullptr, nullptr},")]
        self.assertNotIn("IME_COMPOSITE", shared)
        event = rna[rna.index('RNA_def_struct(brna, "Event", nullptr);'):]
        for name in ('"type"', '"type_prev"'):
            definition = event[event.index("RNA_def_property(srna, %s, PROP_ENUM" % name):]
            self.assertIn('"rna_Event_type_itemf"', definition[:definition.index("RNA_def_property_ui_text")])

    def test_a_script_session_is_left_to_the_script_and_ends_with_a_popup(self):
        window = self.read("source/blender/windowmanager/intern/wm_window.cc")
        refresh = window[window.index("bool WM_window_IME_region_refresh("):]
        self.assertIn("wmIMEOwnerType::Script", refresh[:refresh.index("WM_window_IME_end(win);")])
        handlers = self.read("source/blender/editors/interface/interface_handlers.cc")
        popup = handlers[handlers.index("void popup_handlers_add("):]
        self.assertRegex(popup[:900], r"ELEM\(win->runtime->ime_owner,[^)]*wmIMEOwnerType::Script")

    def test_real_and_simulated_compositions_take_one_path(self):
        events = self.read("source/blender/windowmanager/intern/wm_event_system.cc")
        for phase in ("Start", "Update", "End"):
            self.assertRegex(events, r"case GHOST_kEventImeComposition\w*: \{\s*(BLI_assert[^;]*;\s*)?"
                                     r"WM_event_IME_apply\(win, wmIMEPhase::%s" % phase)
        self.assertIn("WM_event_IME_apply(win, wmIMEPhase(phase)", self.read("source/blender/makesrna/intern/rna_wm_api.cc"))

    def test_the_chat_routes_the_input_method_before_its_keys(self):
        view = self.read("scripts/addons_core/angelo_agent/chat_view.py")
        modal = view[view.index("    def modal(self, context, event):"):]
        self.assertLess(modal.index("composer_ime.handle("), modal.index("chat_key_arming.key("))
        draw = view[view.index("def _draw_chat():"):]
        self.assertLess(draw.index("composer_ime.preview("), draw.index("draft_lines = wrap(draft"))


if __name__ == "__main__":
    unittest.main()
