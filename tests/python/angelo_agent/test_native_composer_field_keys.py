# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's text-field keys and clicks through the chat's real input handler and draw
(``test_native_composer_input``'s stand-ins): a few table keys on this platform (``test_native_composer_editing``
drives every entry), and what the break-it rounds found.

Rounds 3 and 4 (each test fails on the code before it): typing over a selection joined the typing run, so Undo lost
the selected text, and Undo then brought it back unselected; a key the Console keymap under the chat would type
(Alt+letter on Windows and Linux) went through to it; a full row's last character was the scrollbar's; a chat's
draft Undo, scroll and selection were the area's; a double-click picked the word at the nearest caret boundary, not
under the pointer; Up and Down lost their column through a short row; Escape left a drag-select's autoscroll
running; a caret affinity outlived its draft. View keys stay the editor's under the pointer (round 3 had taken Home
and the backtick from the 3D view; round 4 reverted it to the documented contract). Rounds 5-8 (areas and the live
view) are in ``test_native_composer_areas``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_native_composer_input as composer_tests  # noqa: E402  (its stand-ins; its tests are not collected here)

SIZE = composer_tests.SIZE
MAC = sys.platform == "darwin"


class TextFieldKeys(composer_tests.ComposerCase):
    """A few table keys through the real handler on this platform; ``test_native_composer_editing`` has them all."""

    def test_the_drafts_undo_never_reaches_the_scene(self):
        self.line.body = ""
        self.draw()
        self.assertEqual(self.command('Z'), {'PASS_THROUGH'}, "no draft history: Blender's Undo, as before")
        self.type("abc")
        self.assertEqual(self.command('Z'), {'RUNNING_MODAL'})
        self.assertEqual(self.line.body, "")
        self.assertEqual(self.command('Z'), {'RUNNING_MODAL'}, "at the start of the draft's history: stops there")
        self.assertEqual(self.command('Z', shift=True), {'RUNNING_MODAL'})
        self.assertEqual(self.line.body, "abc")

    def test_line_start_and_end_and_the_drafts_top_and_bottom(self):
        self.long_draft()
        self.line.current_character = composer_tests.LONG.index("line 25") + 3
        mac = sys.platform == "darwin"
        self.send(self.event('LEFT_ARROW' if mac else 'HOME', *self.centre(), **({"oskey": True} if mac else {})))
        self.assertEqual(self.line.current_character, composer_tests.LONG.index("line 25"))
        self.send(self.event('RIGHT_ARROW' if mac else 'END', *self.centre(), shift=True,
                             **({"oskey": True} if mac else {})))
        self.assertEqual(self.state["selection"], (composer_tests.LONG.index("line 25"), composer_tests.LONG.index("line 25") + 7))
        self.send(self.event('UP_ARROW' if mac else 'HOME', *self.centre(), **({"oskey": True} if mac else
                                                                               {"ctrl": True})))
        self.assertEqual(self.line.current_character, 0)
        self.draw()
        self.assertEqual(self.shown()[0], "line 00", "the view follows the caret to the top")

    def test_an_option_or_altgr_character_types_over_the_selection_and_keeps_the_drafts_undo(self):
        """Break-it R1-1: "@" is Option+L on a German Mac (AltGr+Q, Ctrl+Alt, on Windows). It was left to the Console
        keymap, which typed past the selection and wiped the draft's Undo, so Cmd+Z then undid the scene."""
        for held in ([{"alt": True}] if sys.platform == "darwin" else [{"ctrl": True, "alt": True}]):
            self.line.body = "user"
            self.line.current_character = 4
            self.draw()
            self.command('A')
            self.assertEqual(self.send(self.event('L', *self.centre(), unicode="@", **held)), {'RUNNING_MODAL'})
            self.assertEqual(self.line.body, "@")
            self.assertEqual(self.command('Z'), {'RUNNING_MODAL'})
            self.assertEqual(self.line.body, "user")

    def test_another_chat_never_undoes_into_the_last_chats_text(self):
        """Break-it R1-3: two chats with empty drafts shared one history; Cmd+Z brought "secret" into the other."""
        self.line.body = ""
        self.draw()
        self.type("secret")
        self.command('A')
        self.send(self.event('BACK_SPACE', *self.centre()))
        self.controller.state["id"] = "another chat"
        self.assertEqual(self.command('Z'), {'PASS_THROUGH'})
        self.assertEqual(self.line.body, "")

    def test_a_selection_left_by_a_write_elsewhere_is_dropped_never_widened(self):
        """Break-it R1-5: a quick-connect example written over a selected draft; typing replaced it all."""
        self.line.body = "hello"
        self.draw()
        self.state["selection"] = (0, 5)
        self.line.body = "Make a low-poly fox, please."   # Written elsewhere, caret at its end.
        self.line.current_character = len(self.line.body)
        self.type("x")
        self.assertEqual(self.line.body, "Make a low-poly fox, please.x")

    def test_a_move_never_reads_the_clipboard(self):
        """Break-it R1-7: every key read the system clipboard (a large one copied each time; X11 may block)."""
        reads = []

        class Manager:
            @property
            def clipboard(self):
                reads.append(1)
                return "pasted"
        self.context.window_manager = Manager()
        self.line.body, self.line.current_character = "abc", 3
        self.draw()
        for _ in range(5):
            self.send(self.event('LEFT_ARROW', *self.centre()))
        self.assertEqual(reads, [])
        self.command('V')
        self.assertEqual((len(reads), self.line.body), (1, "pastedabc"))

    def test_end_on_a_row_wrapped_inside_a_word_stays_on_that_row(self):
        """Break-it R2-1: End put the caret at the next row's start, so Home did nothing and End walked down."""
        self.line.body = "x" * 150   # No space: rows break inside the word.
        self.line.current_character = 3
        self.draw()
        mac = sys.platform == "darwin"
        end = ('RIGHT_ARROW', {"oskey": True}) if mac else ('END', {})
        home = ('LEFT_ARROW', {"oskey": True}) if mac else ('HOME', {})
        self.send(self.event(end[0], *self.centre(), **end[1]))
        self.draw()
        first_row = self.rows[0]
        self.assertEqual(self.line.current_character, len(first_row[0]))
        (_, y), = self.carets()
        self.assertAlmostEqual(y, self.state["draft_rows"][0][2] - 3, msg="drawn at the end of the first row")
        self.send(self.event(end[0], *self.centre(), **end[1]))
        self.assertEqual(self.line.current_character, len(first_row[0]), "a second End stays")
        self.send(self.event(home[0], *self.centre(), **home[1]))
        self.assertEqual(self.line.current_character, 0)

    def test_select_all_then_type_replaces_the_draft(self):
        self.line.body = "old words"
        self.draw()
        self.command('A')
        self.type("n")
        self.assertEqual(self.line.body, "n")


class ReviewRoundThree(composer_tests.ComposerCase):
    def primary(self):
        return {"oskey": True} if MAC else {"ctrl": True}

    def double_click(self, text, column, right_half=False):
        from angelo_agent import chat_composer
        left = chat_composer.column_x(text, column, SIZE, self.SCALE)
        right = chat_composer.column_x(text, column + 1, SIZE, self.SCALE)
        x = left + (right - left) * (0.8 if right_half else 0.2)
        y = self.row_y(text) + SIZE * 0.35
        for _ in range(2):
            self.send(self.event('LEFTMOUSE', x, y))
            self.send(self.event('LEFTMOUSE', x, y, 'RELEASE'))

    def test_typing_over_a_selection_is_its_own_undo_step(self):
        """R3-1: "hello world", double-click "world", "x": Undo gave "" (the whole run), not "hello world"."""
        self.line.body = ""
        self.draw()
        self.type("hello world")
        self.draw()
        self.double_click("hello world", 7)
        self.assertEqual(self.state["selection"], (6, 11))
        self.type("x")
        self.command('Z')
        self.assertEqual(self.line.body, "hello world")
        self.command('A')   # The same after select all.
        self.type("y")
        self.command('Z')
        self.assertEqual(self.line.body, "hello world")

    def test_a_key_the_console_keymap_would_type_stays_with_the_chat(self):
        """R3-2: Alt+Q over the chat on Windows or Linux (a shortcut, not typing) reached the Console keymap under
        the chat, which typed "q" past the selection and wiped the draft's Undo."""
        from angelo_agent import composer_keys
        for system in ("win32", "linux"):   # Windows and Linux keep the character for Alt (GHOST); macOS types it.
            with mock.patch.object(composer_keys, "sys", SimpleNamespace(platform=system)):
                self.line.body, self.line.current_character = "abc", 3
                self.draw()
                self.assertEqual(self.send(self.event('Q', *self.centre(), unicode="q", alt=True)), {'RUNNING_MODAL'})
                self.assertEqual(self.line.body, "abc")
        # R5-2: the Console types Ctrl+digit's character and inserts a tab for Ctrl+Tab, on every platform.
        for kind, unicode in (('ONE', "1"), ('TAB', "")):
            self.assertEqual(self.send(self.event(kind, *self.centre(), unicode=unicode, ctrl=True)), {'RUNNING_MODAL'},
                             kind)
        self.assertEqual(self.line.body, "abc")
        self.assertEqual(self.send(self.event('F5', *self.centre())), {'PASS_THROUGH'}, "a key that types nothing")

    def test_alt_is_a_shortcut_on_windows_and_linux_and_altgr_types(self):
        """R4-6: Linux's AltGr is ISO_Level3_Shift, which GHOST reports as no modifier (GHOST_SystemX11 reads only
        Alt_L and Alt_R), so "@" arrives unmodified and types; Alt+letter stays a shortcut on every layout, a
        Cyrillic or Greek one included (a heuristic that typed any changed character took Alt+G there)."""
        from angelo_agent import composer_input

        def key(kind, unicode, **held):
            flags = dict(shift=False, ctrl=False, alt=False, oskey=False)
            flags.update(held)
            return SimpleNamespace(type=kind, unicode=unicode, **flags)
        for system in ("linux", "win32"):
            self.assertFalse(composer_input.typed(key('G', "п", alt=True), system), "Alt+G on a Russian layout")
            self.assertFalse(composer_input.typed(key('Q', "q", alt=True), system))
            self.assertTrue(composer_input.typed(key('Q', "@"), system), "AltGr on Linux: no modifier")
        self.assertTrue(composer_input.typed(key('Q', "@", alt=True, ctrl=True), "win32"), "Windows' AltGr")

    def test_a_full_rows_last_character_is_the_texts_not_the_scrollbars(self):
        """R3-3: the bar's hit zone (and the bar) covered the last column of a row as wide as the composer."""
        self.line.body = ("x" * 400) + "\n" + composer_tests.LONG
        self.line.current_character = 0
        self.draw()
        self.wheel('WHEELDOWNMOUSE')   # The bar shows.
        self.draw()
        track, _ = self.state["composer_bar"]
        text, offset = self.rows[0]
        from angelo_agent import chat_composer, composer_scroll
        right = chat_composer.column_x(text, len(text), SIZE, self.SCALE)
        reach = max(composer_scroll.ui_kit_rows.BAR_GROW, composer_scroll._HIT)   # The grown bar, or its hit zone.
        self.assertLess(right, track[0] - reach, "the text ends before the bar and its hit zone")

    def test_view_keys_follow_the_pointer_while_the_composer_has_the_keyboard(self):
        """R4-1 (round 3's Home change reverted): the composer takes the keyboard at start and after every send, so
        a composer that kept Home and the backtick took View All and the view pie from the 3D view, and on macOS
        swallowed Home with nothing to scroll. View keys reach the editor under the pointer (input_policy.POINTER_KEYS);
        Home and End are the composer's with the pointer over the chat."""
        self.line.body, self.line.current_character = "first\nsecond", 9
        self.draw()
        outside = (-200, -200)   # Over another editor.
        for kind, unicode in (('HOME', ""), ('ACCENT_GRAVE', "`"), ('NUMPAD_1', "1")):
            self.assertEqual(self.send(self.event(kind, *outside, unicode=unicode)), {'PASS_THROUGH'}, kind)
        self.assertEqual((self.line.body, self.line.current_character), ("first\nsecond", 9))
        self.assertEqual(self.send(self.event('END', *outside)), {'RUNNING_MODAL'}, "a field key, not a view key")

    def test_a_chats_undo_and_scroll_come_back_with_it(self):
        """R3-5, R3-10: the draft's Undo and the composer's scroll were the area's; leaving a chat and coming back
        (or the draft moving to another area) lost them, and Cmd+Z then undid the scene."""
        self.line.body = composer_tests.LONG
        self.line.current_character = len(self.line.body)
        self.draw()
        self.type("!")
        self.draw()
        self.wheel('WHEELUPMOUSE', 20)
        self.draw()
        self.assertEqual(self.shown()[0], "line 00")
        left = (self.line.body, self.line.current_character)
        self.controller.state["id"] = "other chat"   # Another chat in this area, with its own draft.
        self.line.body, self.line.current_character = "", 0
        self.draw()
        self.type("z")
        self.controller.state["id"] = "chat"   # Back: its draft as it was.
        self.line.body, self.line.current_character = left
        self.draw()
        self.assertEqual(self.shown()[0], "line 00", "the rows it was scrolled to")
        self.assertEqual(self.command('Z'), {'RUNNING_MODAL'})
        self.assertEqual(self.line.body, composer_tests.LONG)
        self.type("q")
        self.state.clear()   # The line moves to a new area (maximize): its Undo goes with it.
        self.state["focused"] = True
        self.draw()
        self.assertEqual(self.command('Z'), {'RUNNING_MODAL'})
        self.assertEqual(self.line.body, composer_tests.LONG)

    def test_a_double_click_selects_the_word_under_the_pointer(self):
        """R3-6: the right half of "hello"'s "o" picked the space after it; past a line's end, its newline."""
        self.line.body = "hello world\nnext"
        self.draw()
        self.double_click("hello world", 4, right_half=True)
        self.assertEqual(self.state["selection"], (0, 5))
        self.double_click("hello world", 10, right_half=True)
        self.assertEqual(self.state["selection"], (6, 11))

    def test_up_and_down_keep_their_column_through_a_short_row(self):
        """R3-7: from column 8, Down onto a two-character row and Down again landed at column 2, not 8."""
        self.line.body = "0123456789\nab\n0123456789"
        self.line.current_character = 8
        self.draw()
        self.send(self.event('DOWN_ARROW', *self.centre()))
        self.assertEqual(self.line.current_character, 13)
        self.send(self.event('DOWN_ARROW', *self.centre()))
        self.assertEqual(self.line.current_character, 22)
        self.send(self.event('UP_ARROW', *self.centre()))
        self.send(self.event('UP_ARROW', *self.centre()))
        self.assertEqual(self.line.current_character, 8)
        self.send(self.event('LEFT_ARROW', *self.centre()))   # Another move: a new column.
        self.send(self.event('DOWN_ARROW', *self.centre()))
        self.assertEqual(self.line.current_character, 13)

    def test_escape_ends_a_drag_select_and_its_autoscroll(self):
        """R3-8: Escape cleared the selection but kept the drag, whose autoscroll put the selection back."""
        self.long_draft()
        self.state["composer_drag"] = {"unit": (3, 3), "count": 1, "scrolling": True}
        self.operator.dragging = True
        self.send(self.event('ESC', *self.centre()))
        self.assertNotIn("composer_drag", self.state)

    def test_a_caret_affinity_ends_with_its_draft(self):
        """R3-9: the upstream mark was the caret index alone, so a draft written elsewhere inherited it."""
        from angelo_agent import composer_input
        self.line.body, self.line.current_character = "x" * 150, 45
        self.state["composer_upstream"] = ("x" * 150, 45)
        self.assertTrue(composer_input._draft(self.state, self.line).upstream)
        self.line.body = "y" * 150   # A voice transcript, a restore: another draft, the same caret index.
        self.assertFalse(composer_input._draft(self.state, self.line).upstream)

    def test_undo_after_typing_over_a_selection_selects_the_text_again(self):
        """R4-4: as a native field does, Undo brings the replaced text back selected."""
        self.line.body, self.line.current_character = "hello world", 11
        self.draw()
        self.command('A')
        self.type("x")
        self.command('Z')
        self.assertEqual((self.line.body, self.state["selection"]), ("hello world", (0, 11)))

    def test_a_chat_switch_drops_the_last_chats_selection_and_a_new_area_keeps_the_scroll(self):
        """R4-2, R4-3: the selection outlived a chat switch in an area with no event yet; a new area showing the
        chat restored the scroll of an old switch, not the one last drawn."""
        self.line.body, self.line.current_character = composer_tests.LONG, len(composer_tests.LONG)
        self.draw()
        self.controller.state["id"] = "other chat"
        self.draw()
        self.controller.state["id"] = "chat"
        self.draw()
        self.wheel('WHEELUPMOUSE', 3)
        self.draw()
        shown = self.shown()[0]
        self.state["selection"] = (0, 4)
        self.controller.state["id"] = "other chat"
        self.draw()
        self.assertIsNone(self.state.get("selection"))
        self.controller.state["id"] = "chat"
        self.state.clear()   # The chat in a new area (pop-out, dock).
        self.state["focused"] = True
        self.draw()
        self.assertEqual(self.shown()[0], shown)

    def test_every_external_entry_resolves_and_a_failing_one_leaves_the_key_to_the_table(self):
        from angelo_agent import composer_input, composer_keys
        for kind, modifiers, condition, action in composer_keys.EXTERNAL:
            self.assertIn(condition, composer_keys.CONDITIONS, action)
            self.assertTrue(callable(composer_input.external_function(action)), action)
        entry = (('UP_ARROW', "", "any", "no_such_module.recall"),)
        self.line.body, self.line.current_character = "one\ntwo", 7
        self.draw()
        with mock.patch.object(composer_keys, "EXTERNAL", entry), mock.patch("traceback.print_exc"):
            self.assertEqual(self.send(self.event('UP_ARROW', *self.centre())), {'RUNNING_MODAL'})
        self.assertEqual(self.line.current_character, 3, "the table's Up")

    def test_another_modules_entry_answers_its_key_before_the_table(self):
        """composer_keys.EXTERNAL: e.g. Up in an EMPTY composer brings a pending message back; in a draft, Up moves
        the caret a row as before."""
        from angelo_agent import composer_keys
        calls = []
        fake = SimpleNamespace(recall=lambda line, session: calls.append((line.body, session)) or True)
        entry = (('UP_ARROW', "", "empty", "fake_pending.recall"),)
        with mock.patch.dict(sys.modules, {"angelo_agent.fake_pending": fake}), \
                mock.patch.object(composer_keys, "EXTERNAL", entry, create=True):
            self.line.body, self.line.current_character = "", 0
            self.draw()
            self.assertEqual(self.send(self.event('UP_ARROW', *self.centre())), {'RUNNING_MODAL'})
            self.assertEqual(calls, [("", "chat")])
            self.send(self.event('UP_ARROW', *self.centre(), shift=True))
            self.assertEqual(len(calls), 1, "exact modifiers: Shift+Up is the table's")
            self.line.body, self.line.current_character = "one\ntwo", 7
            self.draw()
            self.send(self.event('UP_ARROW', *self.centre()))
            self.assertEqual((len(calls), self.line.current_character), (1, 3), "a draft: Up moves the caret")


if __name__ == "__main__":
    unittest.main()
