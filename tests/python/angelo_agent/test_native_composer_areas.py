# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer across areas, and in front of a live view, through the chat's real input handler and draw
(``test_native_composer_input``'s stand-ins). What break-it rounds 5-11 found (each test fails on the code before
it): an area that showed its chat again (Return to Dock, the native editor picker, a workspace tab) brought back
its own older rows and editing; two areas showing one chat at once wiped each other's selection and scroll, and
shared one draft Undo they cleared in turn; the draft's Undo stayed with its area when maximize moved the line,
and every hand-off of it to an equal draft on another line gave some draft another's text; a live view in front
of the chat dropped keys the composer acts on (Option and AltGr characters, Cmd/Ctrl+Z, PageUp, the Emacs keys, Tab) and
passed on ones it ignores (Ctrl+digit).
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


class Areas(composer_tests.ComposerCase):
    def test_an_area_that_shows_its_chat_again_takes_the_scroll_another_area_left(self):
        """R5-1: Return to Dock reuses the docked area, whose old state chat_view._views keeps: it showed its own
        older rows and saved them over the pop-out's, and its old selection came back."""
        self.line.body, self.line.current_character = composer_tests.LONG, len(composer_tests.LONG)
        self.draw()
        self.wheel('WHEELUPMOUSE', 20)
        self.draw()
        self.assertEqual(self.shown()[0], "line 00")
        self.state["selection"] = (0, 4)
        docked = self.state
        popped = {"focused": True}   # The pop-out's area: a state of its own.
        self.state = self.views[composer_tests.AREA] = popped
        self.draw()
        self.assertEqual(self.shown()[0], "line 00", "the pop-out opens at the rows the dock showed")
        self.wheel('WHEELDOWNMOUSE', 20)
        self.draw()
        bottom = self.shown()[0]
        self.assertNotEqual(bottom, "line 00")
        from angelo_agent import composer_input
        self.state = self.views[composer_tests.AREA] = docked   # Return to Dock: the old area and its old state,
        composer_input.rejoin(docked)   # made a chat again by editor_types.select_console.
        self.draw()
        self.assertEqual(self.shown()[0], bottom)
        self.assertIsNone(self.state.get("selection"))
        source = (composer_tests.ADDON / "editor_types.py").read_text()
        self.assertIn("chat_view.composer_input.rejoin(view)", source.split("def select_console", 1)[1])

    def test_two_areas_showing_the_chat_at_once_keep_their_own_editing(self):
        """R6-1: a per-draw "newest area" stamp made two chat areas (a split, a duplicated area) wipe each other's
        selection, drag and scroll on every other draw."""
        self.line.body, self.line.current_character = composer_tests.LONG, len(composer_tests.LONG)
        first, second = self.state, {"focused": True}
        self.draw()
        self.state = second
        self.draw()
        self.state = self.views[composer_tests.AREA] = first
        self.state["selection"] = (0, 4)
        self.state["composer_drag"] = {"unit": (0, 0), "count": 1}
        self.wheel('WHEELUPMOUSE', 20)
        for _ in range(3):
            self.draw()
            self.state = second
            self.draw()
            self.state = first
        self.draw()
        self.assertEqual((first["selection"], bool(first.get("composer_drag"))), ((0, 4), True))
        self.assertEqual(self.shown()[0], "line 00")

    def test_a_live_view_hands_the_chat_only_what_it_acts_on(self):
        """R6-3, R6-4: over a live view the rail dropped Option and AltGr characters the composer types, and passed
        Ctrl+digit, which the composer does nothing with, on to the view."""
        from angelo_agent import composer_input

        def key(kind, unicode="", **held):
            flags = dict(shift=False, ctrl=False, alt=False, oskey=False)
            flags.update(held)
            return SimpleNamespace(type=kind, unicode=unicode, **flags)
        self.assertTrue(composer_input.takes_off_chat(key('L', "@", alt=True), "mac"), "Option+L types @")
        self.assertTrue(composer_input.takes_off_chat(key('Q', "@", ctrl=True, alt=True), "win32"), "AltGr")
        self.assertTrue(composer_input.takes_off_chat(key('A', "a"), "linux"))
        self.assertTrue(composer_input.takes_off_chat(key('RET'), "mac"))
        self.assertFalse(composer_input.takes_off_chat(key('ONE', "1", ctrl=True), "mac"))
        self.assertFalse(composer_input.takes_off_chat(key('Q', "q", alt=True), "linux"), "Alt+Q: a shortcut")
        # R7-1: keys without text the chat acts on: draft Undo, the field's own keys, the Emacs keys, Tab's focus.
        for event, platform in ((key('Z', oskey=True), "mac"), (key('Z', ctrl=True), "win32"),
                                (key('PAGE_UP'), "mac"), (key('PAGE_DOWN', ctrl=True), "linux"),
                                (key('K', ctrl=True), "mac"), (key('TAB'), "mac")):
            self.assertTrue(composer_input.takes_off_chat(event, platform), (event, platform))
        self.assertFalse(composer_input.takes_off_chat(key('TAB', ctrl=True), "mac"))
        live = (composer_tests.ADDON / "session_rail_input.py").read_text().split("def _live", 1)[1]
        asked, held = live.index("composer_input.takes_off_chat(event)"), live.index("input_policy.edits_field(event)")
        self.assertLess(asked, held, "the chat's keys are handed on before typing keys are held")
        rail = (composer_tests.ADDON / "session_rail_input.py").read_text()
        focus = rail.split("def _chat_focused", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("popover", focus, "R8-1: an open picker is not the chat's keyboard focus over a live view")

    def test_a_key_another_module_binds_is_the_chats_off_its_area_too(self):
        from angelo_agent import composer_input, composer_keys
        entries = (('F13', "shift", "empty", "chat_pending.recall_newest"),)
        press = SimpleNamespace(type='F13', unicode="", shift=True, ctrl=False, alt=False, oskey=False)
        self.assertTrue(composer_keys.bound(press, entries))
        self.assertFalse(composer_keys.bound(SimpleNamespace(**{**vars(press), "shift": False}), entries))
        with mock.patch.object(composer_keys, "EXTERNAL", entries):
            self.assertTrue(composer_input.takes_off_chat(press, "mac"))
        # The chat's pending messages: Up, no modifier, in an empty composer only (steer-queue); the modal has no
        # inline Up branch of its own any more, so a draft's Up is always the caret's.
        self.assertIn(("UP_ARROW", "", "empty", "chat_pending.recall_newest"), composer_keys.EXTERNAL)
        modal = (composer_tests.ADDON / "chat_view.py").read_text().split("class ANGELO_OT_chat_input", 1)[1]
        self.assertNotIn("edit_newest", modal)

    def test_two_areas_with_their_own_drafts_each_undo_their_own(self):
        """R7-2: one history per chat, shared by a split's two Consoles (each with its own draft), was cleared by
        each area's edit in turn, so Cmd/Ctrl+Z in either fell through to the scene's Undo."""
        from angelo_agent import composer_input
        first, second = composer_tests.Line("hello"), composer_tests.Line("hello")
        first.current_character = second.current_character = 5
        here, there = {"focused": True}, {"focused": True}
        composer_input.insert(here, first, " world", session="chat")
        composer_input.insert(there, second, "!", session="chat")
        self.assertTrue(composer_input.key(self.undo(), here, first, self.wm, platform="mac", session="chat"))
        self.assertEqual(first.body, "hello")
        self.assertTrue(composer_input.key(self.undo(), there, second, self.wm, platform="mac", session="chat"))
        self.assertEqual(second.body, "hello")

    def undo(self, **held):
        flags = dict(shift=False, ctrl=False, alt=False, oskey=True)
        flags.update(held)
        return SimpleNamespace(type='Z', value='PRESS', unicode="", **flags)

    def test_the_drafts_undo_moves_with_its_line_and_a_copied_line_starts_its_own(self):
        """R9-1: maximize moves the Console line to another area and back; the history was the area's, so after the
        way back Cmd/Ctrl+Z found none and undid the scene. R8-3, R9-L1, R10-M1, R11-H1: every rule for handing a
        history to an equal draft on another line gave some draft another's text or lost it: a copied line (a
        pop-out, a dock, Swap Areas, the chat toggle) starts its own, as any draft written elsewhere does."""
        from angelo_agent import composer_input
        line, area, maximized = composer_tests.Line(""), {"focused": True}, {"focused": True}
        composer_input.insert(area, line, "hello", session="chat")
        composer_input.insert(maximized, line, " world", session="chat")   # Maximized: the same line elsewhere.
        self.assertTrue(composer_input.key(self.undo(), area, line, self.wm, platform="mac", session="chat"))
        self.assertEqual(line.body, "hello")
        for text in ("hello", ""):   # A copy equal to its draft, or to an older one.
            copied = composer_tests.Line(text)
            copied.current_character = len(text)
            self.assertFalse(composer_input.key(self.undo(), {"focused": True}, copied, self.wm, platform="mac",
                                                session="chat"), "nothing of its own: Blender's Undo")
            self.assertEqual(copied.body, text)
        line.body = "hello there"   # Written over (Return to Dock's old line): a new history, never older text.
        self.assertFalse(composer_input.key(self.undo(), area, line, self.wm, platform="mac", session="chat"))
        self.assertEqual(line.body, "hello there")

    def test_a_freed_lines_history_never_comes_back_at_its_address(self):
        """R10-2: a freed (or sent) line's history stayed for a new line at its address. The timer keeps only the
        histories of chat drafts: the native agent input (never a walk of the history), or a stock binary's last
        chat line (R12-1); a Python Console's last line is no chat draft either way."""
        from angelo_agent import composer_input
        sent, draft = composer_tests.Line(""), composer_tests.Line("")
        composer_input.insert({"focused": True}, sent, "fix the roof", session="chat")
        composer_input.insert({"focused": True}, draft, "x", session="chat")
        stock = composer_tests.Line("y")   # A stock binary's chat (no coding_agent_input): its last line (R12-1).
        composer_input.insert({"focused": True}, stock, "z", session="chat")

        class Native:   # One lookup per space: its history is never walked.
            type, coding_agent_input = 'CONSOLE', draft

            @property
            def history(self):
                raise AssertionError("the prune walked a Console history")
        python = SimpleNamespace(type='CONSOLE', history=[composer_tests.Line("print()")], coding_agent_input=None)
        stock_chat = SimpleNamespace(type='CONSOLE', language="angelo", history=[sent, stock])
        stock_python = SimpleNamespace(type='CONSOLE', language="python", history=[composer_tests.Line("1")])
        for other in (python.history[-1], stock_python.history[-1]):   # No chat draft: pruned, though last.
            composer_input.insert({"focused": True}, other, "#", session="chat")
        spaces = [Native(), python, stock_chat, stock_python, SimpleNamespace(type='VIEW_3D')]
        windows = SimpleNamespace(window_areas=lambda wm: [], every_window=lambda wm: [], settled=lambda wm: True,
                                  screen_areas=lambda screen: screen.areas)
        screens = [SimpleNamespace(areas=[SimpleNamespace(spaces=spaces)])]
        self.patch(sys.modules["angelo_agent"], "live_windows", windows)
        self.patch(composer_input, "_SWEPT", [float("-inf")])
        bpy = SimpleNamespace(data=SimpleNamespace(screens=screens))
        with mock.patch.dict(sys.modules, {"angelo_agent.live_windows": windows, "bpy": bpy}):
            composer_input.sweep({}, self.wm, now=1.0)
        self.assertEqual(sorted(key[0] for key in composer_input._HISTORIES), sorted([id(draft), id(stock)]))

    def test_another_areas_history_never_comes_back_into_a_sent_draft(self):
        """R8-3: an area whose draft was sent took another area's history of an equal (empty) draft, so Cmd+Z brought
        back the other area's text. A line has only its own history now (round 11)."""
        from angelo_agent import composer_input
        first, second = composer_tests.Line(""), composer_tests.Line("")
        here, there = {"focused": True}, {"focused": True}

        def press(kind, state, line, unicode="", **held):
            flags = dict(shift=False, ctrl=False, alt=False, oskey=False)
            flags.update(held)
            event = SimpleNamespace(type=kind, value='PRESS', unicode=unicode, **flags)
            return composer_input.key(event, state, line, self.wm, platform="mac", session="chat")
        press('X', there, second, "x")
        press('BACK_SPACE', there, second)
        for char in "hello":
            press(char.upper(), here, first, char)
        press('LEFT_ARROW', there, second)   # A caret key in the other area, last.
        first.body, first.current_character = "", 0   # Sent.
        self.assertFalse(press('Z', here, first, oskey=True), "no steps of its own: the scene's Undo")
        self.assertEqual(first.body, "")

    def test_an_area_no_chat_shows_any_more_shows_one_afresh(self):
        """R7-3: an area that stopped showing the chat without editor_types.select_console (the native editor
        picker, a workspace tab, a join) kept its own older rows when the chat showed there again."""
        from angelo_agent import composer_input
        gone = {"composer_session": "chat", "composer_first": 3, "selection": (0, 1), "popover": {"kind": "models"},
                "focused": True}
        shown = {"composer_session": "chat", "composer_first": 9}
        settled = [False]
        windows = SimpleNamespace(window_areas=lambda wm: [("window", self.area)], settled=lambda wm: settled[0],
                                  every_window=lambda wm: [SimpleNamespace(screen="screen")],
                                  screen_areas=lambda screen: [self.area] if screen else [])
        self.patch(sys.modules["angelo_agent"], "live_windows", windows)   # ``from . import`` reads the package first.
        self.patch(composer_input, "_SWEPT", [float("-inf")])
        self.patch(composer_input, "_drafts", lambda: set())
        with mock.patch.dict(sys.modules, {"angelo_agent.live_windows": windows}):
            composer_input.sweep({404: gone}, self.wm, now=10.0)   # A window between screens (R9-L2): none.
            self.assertEqual(gone["composer_session"], "chat")
            settled[0] = True
            composer_input.sweep({composer_tests.AREA: shown, 404: gone}, self.wm, now=10.0)
            self.assertNotIn("composer_session", gone)
            gone["composer_session"] = "again"
            composer_input.sweep({404: gone}, self.wm, now=10.1)   # Within SWEEP_S of the last look: none.
        self.assertEqual(gone.pop("composer_session"), "again")
        self.assertEqual((gone["focused"], gone.get("popover")), (True, None),
                         "its picker closes; its focus stays the modal's (R9-M1: a flag alone split them)")
        self.assertEqual(shown["composer_session"], "chat")
        composer_input._SCROLLS["chat"] = {"composer_first": 9}
        composer_input.adopt(gone, "chat")
        self.assertEqual((gone["composer_first"], gone.get("selection")), (9, None))
        self.assertIn("composer_input.sweep(chat_view._views", (composer_tests.ADDON / "ui.py").read_text())


if __name__ == "__main__":
    unittest.main()
