# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's keyboard (``markup_input``): keys are the viewer's only while it holds the keyboard.

Break-it of 2298d608dd1, M3 and S6: the viewer's modal saw every key ahead of the chat's field. Letters typed into the
chat with the pointer over the 3D view became tool switches, a Return meant to send a message answered an open paid
card, and no control had keyboard focus. The real ``Session`` and layout plan; events are stand-ins. No Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_doc as md, markup_input as mi, markup_layout as ml, markup_tools as mt  # noqa: E402
from angelo_agent import markup_view as mv  # noqa: E402
from angelo_agent import composer_input, input_policy  # noqa: E402

IMAGE = "0b9c6f6e-7a4f-4e4f-9d43-0b1d6d1c2e3a"


def event(kind, value="PRESS", **modifiers):
    return SimpleNamespace(type=kind, value=value, ctrl=modifiers.get("ctrl", False), oskey=modifiers.get("oskey", False),
                           alt=False, shift=modifiers.get("shift", False), unicode=modifiers.get("unicode", ""),
                           is_repeat=False, mouse_x=0, mouse_y=0)


class Viewer:
    """What markup_input reads of the overlay's viewer: the session, the plan's hits, the ui dict, the keyboard."""

    def __init__(self, keyboard=True, mode="markup", card=None, over=True, chat=False, composing=False):
        self.session = mt.Session(md.MarkupDoc(IMAGE, (800, 600)), mv.View((800, 600), (0, 0, 1200, 800), (0, 0, 0, 0)))
        self.session.set_mode(mode)
        self.keyboard, self.ui = keyboard, {"hover": None, "pressed": None, "focus": None}
        self.plan = ml.layout(1200, 800, 1.0, mode, False, lambda text, size: len(text) * size * 0.55, card=card)
        self.acted, self.effects = [], []
        self.over, self.chat = over, chat   # The pointer on the viewer's region; a chat holding the keyboard.
        self.window = SimpleNamespace(is_ime_composing=composing)   # What keyboard_owner reads of the window.

    def route(self, *events):
        results = []
        for item in events:
            from unittest.mock import patch
            with patch.object(mi.keyboard_owner, "chat_has_it", lambda window, menus=False: self.chat):
                takes = mi.keyboard_owner.chat_takes(self.window, item, True)   # The real rule (keyboard_owner).
            results.append(mi.route(self, item, lambda state, *hit: self.acted.append(hit),
                                    lambda state, effect: self.effects.append(effect), lambda: "", lambda: None,
                                    over=self.over, chat_has_keyboard=self.chat, chat_takes=takes))
        return results


class KeyboardTests(unittest.TestCase):
    def test_away_from_the_viewer_without_the_keyboard_every_key_goes_on(self):
        for chat in (True, False):
            viewer = Viewer(keyboard=False, over=False, chat=chat)
            for kind in ("P", "A", "RET", "ESC", "Z", "SPACE", "TAB", "DEL", "NUMPAD_7"):
                with self.subTest(chat=chat, key=kind):
                    self.assertEqual(viewer.route(event(kind, unicode=kind.lower() if len(kind) == 1 else ""))[0],
                                     mi.PASS)
            self.assertEqual((viewer.session.tool, viewer.acted, viewer.effects), ("pen", [], []))

    def test_over_the_viewer_a_chat_holding_the_keyboard_gets_only_what_it_acts_on(self):
        """Re-review of b5808fd5a2e, M1: with a chat holding the keyboard and the pointer on the picture, the viewer
        handed every key to the chat, which passed what it didn't use to the 3D view under the viewer: Ctrl+J joined
        the meshes, Ctrl+Numpad 0 set the camera, Numpad 7 turned the view. The chat gets what it acts on (the live
        view's rule, keyboard_owner.chat_takes); the rest goes nowhere."""
        viewer = Viewer(keyboard=False, over=True, chat=True)
        for item in (event("P", unicode="p"), event("A", unicode="a"), event("SPACE", unicode=" "), event("RET"),
                     event("BACK_SPACE"), event("DEL"), event("ESC"), event("TAB")):
            with self.subTest(key=item.type):
                self.assertEqual(viewer.route(item)[0], mi.PASS, "the chat's own")
        for item in (event("J", ctrl=True), event("NUMPAD_0", ctrl=True), event("NUMPAD_7"), event("NUMPAD_PERIOD"),
                     event("HOME"), event("ACCENT_GRAVE"), event("F2"), event("F3")):
            with self.subTest(key=item.type, ctrl=item.ctrl):
                self.assertEqual(viewer.route(item)[0], mi.RUNNING, "nobody's: never the 3D view under the viewer")
        self.assertEqual((viewer.keyboard, viewer.session.tool, viewer.acted, viewer.effects), (False, "pen", [], []))

    def test_a_chat_holding_the_keyboard_gets_its_input_methods_events_over_the_viewer(self):
        """Re-read of 0690d5c, M1: the input method's events carry no text, so takes_off_chat refused them and the
        viewer held them: Chinese, Japanese and Korean typing into the chat was lost with the pointer on the picture.
        They are the chat's whenever it holds the keyboard, and so is every key while the window composes."""
        viewer = Viewer(keyboard=False, over=True, chat=True)
        for kind in ("IME_COMPOSITE_START", "IME_COMPOSITE_EVENT", "IME_COMPOSITE_END"):
            with self.subTest(kind=kind):
                self.assertEqual(viewer.route(event(kind))[0], mi.PASS)
        composing = Viewer(keyboard=False, over=True, chat=True, composing=True)
        for item in (event("NUMPAD_7"), event("RET"), event("SPACE"), event("DOWN_ARROW"), event("J", ctrl=True)):
            with self.subTest(composing=item.type):
                self.assertEqual(composing.route(item)[0], mi.PASS, "the input method's key while it composes")
        self.assertEqual(Viewer(keyboard=False, over=True, chat=True).route(event("NUMPAD_7"))[0], mi.RUNNING,
                         "not composing: a key the chat doesn't use stays held")

    def test_with_nobody_holding_the_keyboard_input_method_events_and_releases_stay_off_the_scene(self):
        """N5: only a key press makes the viewer the owner; an input method's event or a release goes nowhere."""
        for item in (event("IME_COMPOSITE_START"), event("IME_COMPOSITE_EVENT"), event("A", "RELEASE")):
            with self.subTest(kind=item.type, value=item.value):
                viewer = Viewer(keyboard=False, over=True, chat=False)
                self.assertEqual(viewer.route(item)[0], mi.RUNNING)
                self.assertFalse(viewer.keyboard)

    def test_with_nobody_holding_the_keyboard_a_key_over_the_viewer_is_the_viewers(self):
        """Re-review of b5808fd5a2e, S2: with nobody holding the keyboard, Escape and the viewer's keys over the
        picture did nothing until a click. Such a key makes the viewer the owner, as a press does."""
        viewer = Viewer(keyboard=False, over=True, chat=False)
        self.assertEqual(viewer.route(event("A", unicode="a"))[0], mi.RUNNING)
        self.assertTrue(viewer.keyboard)
        self.assertEqual(viewer.session.tool, "arrow")
        shown = Viewer(keyboard=False, mode="view", over=True)
        shown.route(event("ESC"))
        self.assertIn("close", shown.effects, "Escape closes the viewer")

    def test_save_is_handed_on_whoever_holds_the_keyboard(self):
        """N8: Cmd/Ctrl+S saves the file while the viewer is open, never swallowed."""
        save = event("S", **({"oskey": True} if sys.platform == "darwin" else {"ctrl": True}))
        for keyboard, over, chat in ((True, True, False), (False, True, False), (False, True, True)):
            with self.subTest(keyboard=keyboard, over=over, chat=chat):
                self.assertEqual(Viewer(keyboard=keyboard, over=over, chat=chat).route(save)[0], mi.PASS)

    def test_no_key_reaches_the_3d_view_under_the_viewer(self):
        """Re-review of 4e712847d3b, M1: keys the viewer doesn't use passed on to the 3D view it covers, where Delete
        deleted the selected objects unseen and Space played the animation. Over the viewer, a key the viewer doesn't
        use goes nowhere, whoever holds the keyboard, unless a chat holds it; with the keyboard, nowhere at all."""
        scene_keys = ("DEL", "BACK_SPACE", "SPACE", "LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "G", "S", "R", "X", "H",
                      "ONE", "RET", "NUMPAD_ENTER", "K", "F3", "TAB")
        for mode in ("view", "markup", "comment"):
            for keyboard, over in ((True, True), (True, False), (False, True)):
                viewer = Viewer(keyboard=keyboard, mode=mode, over=over)
                for kind in scene_keys:
                    for value in ("PRESS", "RELEASE"):
                        with self.subTest(mode=mode, keyboard=keyboard, over=over, key=kind, value=value):
                            self.assertEqual(viewer.route(event(kind, value))[0], mi.RUNNING)

    def test_a_press_on_the_viewer_takes_the_keyboard_from_the_chat(self):
        """Re-review of 4e712847d3b, M2: the viewer took the keyboard on a press it consumed, and the chat, which
        never saw that press, kept it: letters went into its draft and Return sent it. The press hands the keyboard
        over through the one owner (keyboard_owner.take)."""
        from unittest.mock import patch
        taken = []
        viewer = Viewer(keyboard=False)
        with patch.object(mi.keyboard_owner, "take", lambda window: taken.append(window)):
            mi.owns(viewer, event("LEFTMOUSE"), over=True, window="win")
            mi.owns(viewer, event("LEFTMOUSE"), over=True, window="win")
        self.assertTrue(viewer.keyboard)
        self.assertEqual(taken, ["win", "win"], "every press on the viewer, even one that finds it holding it")

    def test_a_press_on_the_viewer_takes_the_keyboard_and_one_elsewhere_gives_it_back(self):
        viewer = Viewer(keyboard=False)
        from unittest.mock import patch
        with patch.object(mi.keyboard_owner, "take", lambda window: None):
            mi.owns(viewer, event("LEFTMOUSE"), over=True, window=None)
        self.assertTrue(viewer.keyboard)
        viewer.ui["focus"] = ("mode", "comment")
        mi.owns(viewer, event("LEFTMOUSE"), over=False, window=None)   # A click in the chat's field.
        self.assertFalse(viewer.keyboard)
        self.assertIsNone(viewer.ui["focus"], "the ring goes with the keyboard")
        mi.owns(viewer, event("MOUSEMOVE"), over=True, window=None)
        self.assertFalse(viewer.keyboard, "moving the pointer over the viewer takes nothing")

    def test_a_return_never_answers_a_paid_card_by_itself(self):
        """The review's scenario: a card open, Return pressed (for the chat, or anywhere): no job starts."""
        for keyboard in (False, True):
            with self.subTest(keyboard=keyboard):
                viewer = Viewer(keyboard=keyboard, mode="view", card=2)
                viewer.session.confirm = "remove_bg"
                viewer.route(event("RET"), event("RET", "RELEASE"), event("NUMPAD_ENTER"))
                self.assertEqual(viewer.session.confirm, "remove_bg", "still asking")
                self.assertEqual(viewer.acted, [])
                self.assertNotIn("run", viewer.effects)

    def test_the_card_answers_its_focused_button_on_the_keys_release(self):
        viewer = Viewer(mode="view", card=2)
        viewer.session.confirm = "remove_bg"
        viewer.route(event("TAB"))
        focused = viewer.ui["focus"]
        self.assertEqual(focused[0], "answer")
        viewer.route(event("RET"))
        self.assertEqual(viewer.acted, [], "armed on the press, never run by it")
        viewer.route(event("RET", "RELEASE"))
        self.assertEqual(viewer.acted, [focused], "run on the release, as a click on that button")

    def test_escape_cancels_a_card(self):
        viewer = Viewer(mode="view", card=2)
        viewer.session.confirm = "erase"
        viewer.route(event("ESC"))
        self.assertIsNone(viewer.session.confirm)

    def test_tab_reaches_every_control_and_return_runs_it_as_its_click(self):
        viewer = Viewer(mode="view")
        seen = []
        for _ in range(len(viewer.plan["hits"]) + 1):
            viewer.route(event("TAB"))
            seen.append(viewer.ui["focus"])
        self.assertEqual(set(seen[:-1]), {(h[1], h[2]) for h in viewer.plan["hits"]}, "every control a stop")
        viewer.ui["focus"] = ("mode", "markup")
        viewer.route(event("RET"), event("RET", "RELEASE"))
        self.assertEqual(viewer.acted, [("mode", "markup")])

    def test_the_viewers_own_keys_work_while_it_holds_the_keyboard_and_others_go_nowhere(self):
        viewer = Viewer()
        self.assertEqual(viewer.route(event("A"))[0], mi.RUNNING)
        self.assertEqual(viewer.session.tool, "arrow")
        for kind in ("K", "F3", "S"):
            with self.subTest(key=kind):
                self.assertEqual(viewer.route(event(kind))[0], mi.RUNNING, "a key the viewer does not use is kept")
        self.assertEqual(viewer.session.tool, "arrow")

    def test_a_field_being_typed_in_takes_its_keys(self):
        viewer = Viewer()
        viewer.session.instructions.editing = True
        viewer.route(event("A", unicode="a"), event("TAB"))
        self.assertEqual(viewer.session.tool, "pen", "a letter, never a tool")
        self.assertIn("a", viewer.session.instructions.text)

    def test_the_agent_opens_the_viewer_without_the_keyboard(self):
        tree = ast.parse((ADDON / "app_feature_host.py").read_text(encoding="utf-8"))
        calls = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(
            "open_reference")]
        self.assertTrue(calls)
        self.assertTrue(all("keyboard=False" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
