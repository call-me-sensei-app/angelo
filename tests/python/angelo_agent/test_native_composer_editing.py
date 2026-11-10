# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer edits like the platform's own multi-line text field, and scrolls on its own: the pure parts.

``composer_keys`` is the per-platform shortcut table (data), ``composer_edit`` performs each action and keeps the
draft's own Undo, ``composer_scroll`` keeps which eight rows show. Every table entry is driven here on both tables.
1.0.9 (maintainer report): the composer had only the Console's single-line keys (no line, top or bottom, word moves
or draft Undo; Cmd+Z undid the scene while typing), and its view followed the caret alone, so a long draft's
earlier rows could not be scrolled to. ``test_native_composer_input`` drives the same through the chat's real input
handler and draw; ``test_blender_composer_gui`` in the running app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import composer_edit, composer_keys, composer_scroll  # noqa: E402
from angelo_agent.composer_edit import Draft  # noqa: E402

# Fixed-width rows, as a 10-character-wide composer wraps (``canvas_iter_wrap``'s rule: break after a space).
TEXT = "alpha beta gamma\ndelta epsilon"
ROWS = (("alpha ", 0), ("beta ", 6), ("gamma", 11), ("delta ", 17), ("epsilon", 23))


def vertical(rows):
    """Up and Down by visual row at the same column (the composer keeps the same x; equal-width glyphs here)."""
    def move(caret, up, current=None):
        index = composer_edit.row_index(rows, caret) if current is None else current
        target = max(0, min(len(rows) - 1, index + (-1 if up else 1)))
        column = caret - rows[index][1]
        text, start = rows[target]
        return start + min(column, len(text) - 1 if composer_edit.breaks_at_space(rows, target) else len(text))
    return move


def act(action, caret, anchor=None, extend=False, platform="pc", value=TEXT, rows=ROWS, **extra):
    return composer_edit.apply(action, extend, Draft(value, caret, anchor), rows, vertical(rows), platform, **extra)


def press(kind, **held):
    flags = dict(shift=False, ctrl=False, alt=False, oskey=False)
    flags.update(held)
    return SimpleNamespace(type=kind, **flags)


class ShortcutTable(unittest.TestCase):
    """The table is the contract: every entry resolves, every action exists, the keys people expect are in it."""

    EXPECTED = {
        "mac": {("LEFT_ARROW", "oskey"): "line_start", ("RIGHT_ARROW", "oskey"): "line_end",
                ("UP_ARROW", "oskey"): "doc_start", ("DOWN_ARROW", "oskey"): "doc_end",
                ("LEFT_ARROW", "alt"): "word_left", ("RIGHT_ARROW", "alt"): "word_right_end",
                ("A", "oskey"): "select_all", ("BACK_SPACE", "alt"): "delete_word_back",
                ("BACK_SPACE", "oskey"): "delete_line_start", ("DEL", ""): "delete_forward",
                ("A", "ctrl"): "para_start", ("E", "ctrl"): "para_end", ("K", "ctrl"): "kill_para_end",
                ("PAGE_UP", ""): "scroll_page_up", ("PAGE_DOWN", ""): "scroll_page_down",
                ("Z", "oskey"): "undo", ("Z", "oskey shift"): "redo"},
        "pc": {("HOME", ""): "line_start", ("END", ""): "line_end", ("HOME", "ctrl"): "doc_start",
               ("END", "ctrl"): "doc_end", ("LEFT_ARROW", "ctrl"): "word_left",
               ("RIGHT_ARROW", "ctrl"): "word_right_start", ("A", "ctrl"): "select_all",
               ("BACK_SPACE", "ctrl"): "delete_word_back", ("DEL", "ctrl"): "delete_word_forward_start",
               ("PAGE_UP", ""): "page_up", ("PAGE_DOWN", ""): "page_down", ("Z", "ctrl"): "undo",
               ("Y", "ctrl"): "redo"},
    }

    def test_the_keys_people_expect_resolve_on_each_platform(self):
        for platform, entries in self.EXPECTED.items():
            for (kind, held), action in entries.items():
                event = press(kind, **{flag: True for flag in held.split()})
                self.assertEqual(composer_keys.lookup(event, platform), (action, False), (platform, kind, held))

    def test_shift_extends_every_move_and_never_an_edit(self):
        for platform in ("mac", "pc"):
            for kind, modifiers, action, extends in composer_keys.TABLE[platform]:
                held = {composer_keys._EVENT_FLAG[m]: True for m in modifiers.split()}
                with_shift = "shift" in modifiers.split()
                self.assertEqual(composer_keys.lookup(press(kind, **held), platform),
                                 (action, extends and with_shift), (platform, kind, modifiers))
                if extends and not with_shift:
                    self.assertEqual(composer_keys.lookup(press(kind, shift=True, **held), platform), (action, True))

    def test_every_entry_is_unique_and_every_action_is_performed(self):
        draft = Draft("one two\nthree", 4, None)
        for platform in ("mac", "pc"):
            seen = set()
            for kind, modifiers, action, extends in composer_keys.TABLE[platform]:
                key = (kind, frozenset(modifiers.split()))
                self.assertNotIn(key, seen, (platform, kind, modifiers))
                seen.add(key)
                if action in {"undo", "redo"}:
                    continue   # History's (the input glue), below.
                composer_edit.apply(action, extends, draft, (("one two", 0), ("three", 8)),
                                    lambda caret, up, current: caret, platform, clipboard="x", kill="y")

    def test_platform_names(self):
        self.assertEqual(composer_keys.platform_of("darwin"), "mac")
        self.assertEqual(composer_keys.platform_of("win32"), "pc")
        self.assertEqual(composer_keys.platform_of("linux"), "pc")

    def test_an_unlisted_modifier_on_a_field_key_acts_as_the_key_alone_and_never_reaches_the_console(self):
        self.assertEqual(composer_keys.lookup(press("LEFT_ARROW", ctrl=True), "mac"), ("char_left", False))
        self.assertEqual(composer_keys.lookup(press("LEFT_ARROW", ctrl=True, shift=True), "mac"), ("char_left", True))
        self.assertEqual(composer_keys.lookup(press("UP_ARROW", alt=True), "pc"), ("row_up", False))
        self.assertIsNone(composer_keys.lookup(press("Q", ctrl=True), "pc"))
        self.assertEqual(composer_keys.lookup(press("BACK_SPACE", alt=True, shift=True), "mac"),
                         ("delete_word_back", False), "Shift changes no delete")
        self.assertEqual(composer_keys.lookup(press("BACK_SPACE", ctrl=True, shift=True), "pc"),
                         ("delete_word_back", False))
        self.assertIsNone(composer_keys.lookup(press("Z", alt=True), "mac"))

    def test_the_table_reads_as_shortcuts(self):
        self.assertIn(("Cmd+LEFT_ARROW", "line_start", True), composer_keys.rows("mac"))
        self.assertIn(("Ctrl+HOME", "doc_start", True), composer_keys.rows("pc"))


class Moves(unittest.TestCase):

    def test_a_row_wrapped_inside_a_word_or_cjk_text_ends_after_its_last_character(self):
        """Break-it R1-2: ``line_break`` wraps Chinese, Japanese and long words at a character, not a space."""
        rows, value = (("日本語のテ", 0), ("キスト", 5)), "日本語のテキスト"
        edit = act("line_end", 0, extend=True, value=value, rows=rows)
        self.assertEqual(value[slice(*edit.draft.selection)], "日本語のテ")
        # Break-it R2-1: the caret stays on the row it ended (upstream), so Home and End stay on that row.
        end = act("line_end", 0, value=value, rows=rows).draft
        self.assertEqual((end.caret, end.upstream), (5, True))
        self.assertEqual(composer_edit.caret_row(rows, end.caret, end.upstream), 0)
        self.assertEqual(composer_edit.apply("line_end", False, end, rows, vertical(rows)).draft.caret, 5)
        self.assertEqual(composer_edit.apply("line_start", False, end, rows, vertical(rows)).draft.caret, 0)
        down = composer_edit.apply("row_down", False, end, rows, vertical(rows)).draft
        self.assertEqual(composer_edit.caret_row(rows, down.caret, down.upstream), 1, "Down goes one row, from row 0")
        self.assertFalse(act("line_end", 6, value=value, rows=rows).draft.upstream, "the last row's end")
        self.assertEqual(act("delete_line_end", 0, value=value, rows=rows).draft.value, "キスト")
        url = (("https://example.com/a", 0), ("/b", 21))
        self.assertEqual(act("line_end", 3, value="https://example.com/a/b", rows=url).draft.caret, 21)

    def test_visual_line_start_and_end_stop_inside_a_soft_wrapped_row(self):
        self.assertEqual(act("line_start", 8).draft.caret, 6)          # "beta |" row starts at 6
        self.assertEqual(act("line_end", 7).draft.caret, 10)           # Before the row's trailing space.
        self.assertEqual(act("line_end", 12).draft.caret, 16)          # "gamma" ends its paragraph: after it.
        self.assertEqual(act("line_end", 29).draft.caret, len(TEXT))

    def test_document_start_and_end(self):
        self.assertEqual(act("doc_start", 20).draft.caret, 0)
        self.assertEqual(act("doc_end", 3).draft.caret, len(TEXT))

    def test_words_on_each_platform(self):
        value = "can't stop, won't stop"
        self.assertEqual(composer_edit.word_left(value, 11), 6)
        self.assertEqual(composer_edit.word_left(value, 4), 0, "an apostrophe inside a word is the word's")
        self.assertEqual(composer_edit.word_right_end(value, 0), 5)
        self.assertEqual(composer_edit.word_right_end(value, 5), 10)
        self.assertEqual(composer_edit.word_right_start(value, 0), 6, "Windows: to the next word's start")
        self.assertEqual(composer_edit.word_right_start(value, 6), 10, "a mark is a stop of its own")
        self.assertEqual(composer_edit.word_right_start("ab\ncd", 2), 3, "a line's end is a stop")
        self.assertEqual(composer_edit.word_left("日本語 text", 4), 0, "a run of CJK letters is one word")
        self.assertEqual(composer_edit.word_right_end("مرحبا بالعالم", 0), 5, "Arabic words, by characters")

    def test_paragraphs(self):
        self.assertEqual(act("para_start", 12).draft.caret, 0)
        self.assertEqual(act("para_end", 3).draft.caret, 16)
        self.assertEqual(act("para_up", 17).draft.caret, 0, "again at a paragraph's start: the previous one's")
        self.assertEqual(act("para_down", 16).draft.caret, len(TEXT))

    def test_rows_and_the_macos_first_and_last_rows(self):
        self.assertEqual(act("row_down", 2).draft.caret, 8)
        self.assertEqual(act("row_up", 2, platform="pc").draft.caret, 2, "Windows: the first row stays")
        self.assertEqual(act("row_up", 2, platform="mac").draft.caret, 0)
        self.assertEqual(act("row_down", 25, platform="mac").draft.caret, len(TEXT), "NSTextView: last row to end")
        self.assertEqual(act("row_down", 25, platform="pc").draft.caret, 25, "Windows: stays")

    def test_shift_extends_from_the_anchor_and_a_plain_arrow_collapses_the_selection(self):
        edit = act("word_left", 10, extend=True)
        self.assertEqual((edit.draft.caret, edit.draft.selection), (6, (6, 10)))
        edit = act("line_start", 6, anchor=10, extend=True)   # The anchor stays where the selection began.
        self.assertEqual(edit.draft.selection, (6, 10))
        self.assertEqual(act("char_left", 10, anchor=2).draft, Draft(TEXT, 2))
        self.assertEqual(act("char_right", 2, anchor=10).draft, Draft(TEXT, 10))
        self.assertEqual(act("select_all", 3).draft.selection, (0, len(TEXT)))

    def test_pages_move_seven_rows_on_windows_and_only_scroll_on_macos(self):
        rows = tuple(("row%02d " % i, i * 6) for i in range(30))
        value = "".join(text for text, _ in rows)
        self.assertEqual(act("page_down", 0, value=value, rows=rows).draft.caret, 7 * 6)
        self.assertEqual(act("page_up", 20 * 6, value=value, rows=rows).draft.caret, 13 * 6)
        edit = act("scroll_page_down", 0, platform="mac", value=value, rows=rows)
        self.assertEqual((edit.draft.caret, edit.scroll), (0, "page_down"))
        self.assertEqual(act("scroll_top", 9, platform="mac").scroll, "top")


class Edits(unittest.TestCase):

    def test_deletes(self):
        self.assertEqual(act("delete_word_back", 10).draft.value, "alpha  gamma\ndelta epsilon")
        self.assertEqual(act("delete_word_forward_start", 0).draft.value, "beta gamma\ndelta epsilon")
        self.assertEqual(act("delete_word_forward_end", 5).draft.value, "alpha gamma\ndelta epsilon")
        self.assertEqual(act("delete_line_start", 9).draft.value, "alpha a gamma\ndelta epsilon")
        self.assertEqual(act("delete_line_end", 6).draft.value, "alpha  gamma\ndelta epsilon")
        self.assertEqual(act("delete_forward", 16).draft.value, "alpha beta gammadelta epsilon")
        self.assertEqual(act("delete_back", 0).draft, Draft(TEXT, 0))

    def test_a_selection_is_what_every_delete_removes(self):
        for action in ("delete_back", "delete_forward", "delete_word_back", "delete_line_start", "kill_para_end"):
            self.assertEqual(act(action, 10, anchor=6).draft, Draft("alpha  gamma\ndelta epsilon", 6), action)

    def test_emacs_kill_yank_and_transpose(self):
        edit = act("kill_para_end", 6)
        self.assertEqual((edit.draft.value, edit.kill), ("alpha \ndelta epsilon", "beta gamma"))
        self.assertEqual(act("kill_para_end", 16).draft.value, "alpha beta gammadelta epsilon", "takes the newline")
        self.assertEqual(act("yank", 0, kill="X").draft, Draft("X" + TEXT, 1))
        self.assertEqual(act("transpose", 1, value="abc", rows=(("abc", 0),)).draft, Draft("bac", 2))
        self.assertEqual(act("transpose", 2, value="abc", rows=(("abc", 0),)).draft, Draft("acb", 3))
        self.assertEqual(act("transpose", 3, value="abc", rows=(("abc", 0),)).draft, Draft("acb", 3), "at the end")

    def test_clipboard(self):
        self.assertEqual(act("copy", 10, anchor=6).clipboard, "beta")
        edit = act("cut", 10, anchor=6)
        self.assertEqual((edit.draft.value, edit.clipboard), ("alpha  gamma\ndelta epsilon", "beta"))
        self.assertIsNone(act("copy", 3).clipboard, "nothing selected: the clipboard keeps what it holds")
        self.assertEqual(act("paste", 0, clipboard="a\r\nb").draft, Draft("a\nb" + TEXT, 3))

    def test_the_message_limit(self):
        edit = composer_edit.insert(Draft("x" * (composer_edit.LIMIT - 2), 0), "abcdef")
        self.assertEqual(len(edit.draft.value), composer_edit.LIMIT)
        self.assertEqual(edit.draft.caret, 2)

    def test_double_and_triple_click_units(self):
        self.assertEqual(composer_edit.word_at("hello world", 7), (6, 11))
        self.assertEqual(composer_edit.word_at("hello   world", 6), (5, 8), "a space run")
        self.assertEqual(composer_edit.word_at("a, b", 1), (1, 2), "a mark alone")
        self.assertEqual(composer_edit.paragraph_at(TEXT, 3), (0, 17), "with its newline")
        self.assertEqual(composer_edit.paragraph_at(TEXT, 20), (17, len(TEXT)))


class DraftHistory(unittest.TestCase):
    """The draft's own Undo: typing runs are one step, a sent message never comes back, the scene is never touched."""

    def type(self, history, draft, text):
        for char in text:
            edit = composer_edit.insert(draft, char)
            history.sync(draft.value)
            history.record(draft, edit.draft, edit.kind)
            draft = edit.draft
        return draft

    def test_a_typing_run_is_one_step_and_redo_brings_it_back(self):
        history = composer_edit.History()
        history.sync("")
        self.assertFalse(history.active, "an untouched draft: Cmd/Ctrl+Z is Blender's")
        draft = self.type(history, Draft("", 0), "hello")
        self.assertTrue(history.active)
        undone = history.step(draft, back=True)
        self.assertEqual(undone, Draft("", 0))
        self.assertIsNone(history.step(undone, back=True), "the end of the draft's history")
        self.assertEqual(history.step(undone, back=False), draft)

    def test_a_caret_move_or_another_kind_of_edit_ends_a_run(self):
        history = composer_edit.History()
        history.sync("")
        draft = self.type(history, Draft("", 0), "ab")
        moved = Draft(draft.value, 0)
        history.record(draft, moved, None)
        draft = self.type(history, moved, "X")
        edit = composer_edit.apply("paste", False, draft, (), lambda c, u: c, clipboard="PASTE")
        history.record(draft, edit.draft, edit.kind)
        values = []
        current = edit.draft
        while (current := history.step(current, back=True)) is not None:
            values.append(current.value)
        self.assertEqual(values, ["Xab", "ab", ""])

    def test_a_draft_changed_elsewhere_starts_a_new_history(self):
        history = composer_edit.History()
        history.sync("")
        self.type(history, Draft("", 0), "sent message")
        history.sync("")   # Sent: the console cleared the draft.
        self.assertFalse(history.active, "Undo after sending is the scene's again; the message never comes back")


class Typing(unittest.TestCase):
    """Which modified keys type (break-it R1-1, R2-5)."""

    def key(self, unicode, **held):
        flags = dict(shift=False, ctrl=False, alt=False, oskey=False)
        flags.update(held)
        return SimpleNamespace(type="L", unicode=unicode, **flags)

    def test_option_types_on_macos_and_altgr_on_windows_and_linux(self):
        from angelo_agent import composer_input
        self.assertTrue(composer_input.typed(self.key("@", alt=True), "mac"))
        self.assertFalse(composer_input.typed(self.key("@", alt=True, ctrl=True), "mac"))
        self.assertFalse(composer_input.typed(self.key("l", alt=True), "pc"), "Alt+L is a shortcut on Windows")
        self.assertTrue(composer_input.typed(self.key("@", alt=True, ctrl=True), "pc"), "AltGr")
        self.assertFalse(composer_input.typed(self.key("l", oskey=True), "mac"))
        self.assertTrue(composer_input.typed(self.key("L", shift=True), "pc"))

    def test_a_macos_dead_key_is_the_fields(self):
        from angelo_agent import composer_input
        dead = SimpleNamespace(type="E", unicode="", shift=False, ctrl=False, alt=True, oskey=False)
        self.assertTrue(composer_input.dead_key(dead, "mac"))
        self.assertFalse(composer_input.dead_key(dead, "pc"))


class OwnScroll(unittest.TestCase):

    def test_the_caret_is_followed_only_as_far_as_needed(self):
        self.assertEqual(composer_scroll.follow(5, 7), 5)
        self.assertEqual(composer_scroll.follow(5, 13), 6)
        self.assertEqual(composer_scroll.follow(5, 2), 2)

    @staticmethod
    def rows(count, width):
        """``count`` source characters' worth of rows ``width`` characters wide (offsets only matter here)."""
        return [("x" * width, offset) for offset in range(0, count, width)]

    def test_a_redraw_never_moves_a_view_the_person_scrolled(self):
        state, rows = {}, self.rows(200, 10)
        self.assertEqual(composer_scroll.shown_first(state, rows, 19, ("draft", 99)), 12, "opens at the caret")
        state["composer_first"] = 3   # Wheel: reading earlier rows.
        self.assertEqual(composer_scroll.shown_first(state, rows, 19, ("draft", 99)), 3, "the blink redraw keeps it")
        self.assertEqual(composer_scroll.shown_first(state, self.rows(210, 10), 20, ("draftX", 100)), 13,
                         "typing: caret in view")

    def test_a_rewrap_keeps_the_text_at_the_top_and_a_shown_caret_in_view(self):
        """Break-it R1-4: a narrower chat rewrapped 20 rows into 30; the row index stayed, the caret went out of view."""
        state = {}
        self.assertEqual(composer_scroll.shown_first(state, self.rows(200, 10), 19, ("d", 199), (400, 14, 1.0)), 12)
        first = composer_scroll.shown_first(state, self.rows(200, 7), 28, ("d", 199), (280, 14, 1.0))
        self.assertLessEqual(first, 28)
        self.assertGreater(first + composer_scroll.ROWS, 28, "the caret stays in view")
        composer_scroll.shown_first(state, self.rows(200, 10), 19, ("d", 199), (400, 14, 1.0))   # Wider again.
        state["composer_first"] = 2   # Reading rows 2.. (source offset 20) with the caret out of view.
        self.assertEqual(composer_scroll.shown_first(state, self.rows(200, 10), 19, ("d", 199), (400, 14, 1.0)), 2)
        self.assertEqual(composer_scroll.shown_first(state, self.rows(200, 5), 39, ("d", 199), (200, 14, 1.0)), 4,
                         "the same text (offset 20) stays at the top while reading")

    def test_wheel_clamps_at_both_ends_and_carries_trackpad_fractions(self):
        self.assertEqual(composer_scroll.wheel(0, 0.0, 66.0, 22, 20), (0, 0.0), "at the top: stays, no carry")
        self.assertEqual(composer_scroll.wheel(12, 0.0, -66.0, 22, 20), (12, 0.0), "at the bottom")
        self.assertEqual(composer_scroll.wheel(10, 0.0, 66.0, 22, 20), (7, 0.0))
        first, carry = composer_scroll.wheel(10, 0.0, 10.0, 22, 20)
        self.assertEqual((first, carry), (10, 10.0))
        self.assertEqual(composer_scroll.wheel(first, carry, 15.0, 22, 20)[0], 9, "two small swipes make a row")

    def test_rows_under_the_pointer_follow_the_offset(self):
        state = {"composer_first": 5}
        composer_scroll.place(state, (24, 100, 300, 160), 250, 20, 20)
        self.assertEqual(composer_scroll.row_at(state, 260), 5)
        self.assertEqual(composer_scroll.row_at(state, 246), 6, "below the first row's band: the next row")
        self.assertEqual(composer_scroll.row_at(state, 900), 5, "above the rows: the first shown")
        self.assertEqual(composer_scroll.row_at(state, -50), 12, "below them: the last shown")
        self.assertEqual(composer_scroll.drag_edge(state, 300), -1)
        self.assertEqual(composer_scroll.drag_edge(state, 50), 1)
        self.assertEqual(composer_scroll.drag_edge(state, 150), 0)


if __name__ == "__main__":
    unittest.main()
