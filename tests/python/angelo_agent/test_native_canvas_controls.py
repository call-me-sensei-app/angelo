# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hover, pressed, keyboard-focus and disabled states of canvas controls (``canvas_controls``), without Blender.

The window suite ``test_blender_canvas_states`` proves the same states in the
running app with screenshots; these tests pin the rules.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import canvas_controls as cc  # noqa: E402

TEAM = ((10, 100, 200, 40), "welcome_team", "")
CLAUDE = ((10, 50, 200, 40), "welcome_connect", "claude_subscription")
SEND = ((250, 50, 28, 28), cc.DISABLED, cc.SEND_EMPTY)
LINK = ((10, 10, 120, 20), "settings", None)
HITS = [TEAM, CLAUDE, SEND, LINK]


class Window:
    def __init__(self):
        self.cursors = []

    def cursor_set(self, cursor):
        self.cursors.append(cursor)


class Redraw:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1


def key(kind, shift=False, unicode=""):
    return SimpleNamespace(type=kind, value='PRESS', shift=shift, ctrl=False, oskey=False, alt=False, unicode=unicode)


class PointerTests(unittest.TestCase):
    def test_a_control_under_the_pointer_shows_a_hand_and_redraws_once(self):
        window, redraw, state = Window(), Redraw(), {}
        self.assertTrue(cc.pointer(window, redraw, state, 20, 60, HITS))
        self.assertEqual(state["hover"], ("welcome_connect", "claude_subscription"))
        self.assertEqual(window.cursors, ["HAND_POINT"])
        self.assertTrue(cc.pointer(window, redraw, state, 30, 70, HITS))
        self.assertEqual(redraw.count, 1, "Moving within the same control does not redraw again")
        self.assertFalse(cc.pointer(window, redraw, state, 400, 400, HITS))
        self.assertIsNone(state["hover"])
        self.assertEqual(redraw.count, 2)

    def test_a_disabled_control_gets_the_not_allowed_pointer_and_its_reason(self):
        window, state = Window(), {}
        self.assertTrue(cc.pointer(window, Redraw(), state, 260, 60, HITS))
        self.assertEqual(window.cursors, ["STOP"])
        self.assertEqual(state["hover"], (cc.DISABLED, cc.SEND_EMPTY))
        self.assertNotIn((cc.DISABLED, cc.SEND_EMPTY), cc.focus_order(HITS), "Disabled controls take no focus")

    def test_the_first_containing_hit_wins_as_on_click(self):
        overlapping = [((0, 0, 100, 100), "first", None), ((0, 0, 50, 50), "second", None)]
        self.assertEqual(cc.hit_at(overlapping, 10, 10)[1], "first")

    def test_press_shows_until_release_and_leaving_clears_everything(self):
        redraw, state = Redraw(), {"focus": ("settings", None)}
        cc.pointer(Window(), redraw, state, 20, 60, HITS)
        cc.press(redraw, state, CLAUDE)
        self.assertEqual(cc.state_of(state, *CLAUDE[1:]), "pressed")
        self.assertIsNone(state["focus"], "A pointer click ends keyboard focus")
        cc.release(redraw, state)
        self.assertEqual(cc.state_of(state, *CLAUDE[1:]), "hover")
        cc.press(redraw, state, CLAUDE)
        cc.leave(redraw, state)
        self.assertIsNone(state["hover"])
        self.assertIsNone(state["pressed"])
        self.assertNotIn("pointer", state)


class FocusTests(unittest.TestCase):
    def test_reading_order_is_top_to_bottom_then_left_to_right(self):
        row = [((300, 50, 40, 20), "right", None), ((10, 50, 40, 20), "left", None), ((10, 200, 40, 20), "top", None)]
        self.assertEqual([action for action, _ in cc.focus_order(row)], ["top", "left", "right"])

    def test_tab_walks_the_controls_and_returns_to_the_text_field(self):
        state, redraw = {"hits": HITS}, Redraw()
        order = cc.focus_order(HITS)
        self.assertFalse(cc.key(key('TAB'), state, redraw))
        self.assertEqual(state["focus"], order[0])
        for expected in order[1:]:
            cc.key(key('TAB'), state, redraw)
            self.assertEqual(state["focus"], expected)
        cc.key(key('TAB'), state, redraw)
        self.assertIsNone(state["focus"], "Past the last control focus returns to the text field")
        cc.key(key('TAB', shift=True), state, redraw)
        self.assertEqual(state["focus"], order[-1])

    def test_return_or_space_activates_escape_and_typing_return_to_the_text_field(self):
        state, redraw = {"hits": HITS, "focus": ("welcome_team", "")}, Redraw()
        self.assertEqual(cc.key(key('RET'), state, redraw), ("welcome_team", ""))
        self.assertEqual(cc.key(key('SPACE'), state, redraw), ("welcome_team", ""))
        self.assertFalse(cc.key(key('ESC'), state, redraw))
        self.assertIsNone(state["focus"])
        state["focus"] = ("welcome_team", "")
        self.assertIsNone(cc.key(key('A', unicode="a"), state, redraw), "The letter still goes to the text field")
        self.assertIsNone(state["focus"])
        self.assertIsNone(cc.key(key('RET'), state, redraw), "Without focus, Return sends the message")

    def test_one_focus_indicator_a_resting_pointer_hides_its_hover_until_it_moves(self):
        """1.0.5 review: Tab drew the focus ring while a resting pointer kept another control's hover outline."""
        window, redraw, state = Window(), Redraw(), {"hits": HITS}
        cc.pointer(window, redraw, state, 20, 60, HITS)
        self.assertEqual(cc.hover_shown(state), CLAUDE[1:])
        cc.key(key('TAB'), state, redraw)
        self.assertEqual(state["focus"], cc.focus_order(HITS)[0])
        self.assertIsNone(cc.hover_shown(state), "Keyboard focus shows alone")
        cc.pointer(window, redraw, state, 20, 60, HITS)   # A motionless refresh is not a move.
        self.assertIsNone(cc.hover_shown(state))
        cc.pointer(window, redraw, state, 24, 62, HITS)
        self.assertEqual(cc.hover_shown(state), CLAUDE[1:], "Moving the pointer shows its hover again")

    def test_focus_survives_a_redraw_by_identity(self):
        state = {"hits": HITS, "focus": ("welcome_connect", "claude_subscription")}
        state["hits"] = [((10, 300, 200, 40), "welcome_connect", "claude_subscription")]  # Moved by a redraw.
        self.assertEqual(cc.focused_hit(state)[0], (10, 300, 200, 40))


class ComposerFitTests(unittest.TestCase):
    """1.0.5 review row 18: a 260 px chat drew "Approve …" and "Defau…" side by side."""

    def fit(self, right, label="Default", short="Default", approval="Approve for me", chip=True):
        from angelo_agent import chat_composer
        view = SimpleNamespace(measure=lambda value, size: 7 * len(value))
        return chat_composer.footer_fit(view, approval, label, short, 9, right, chip)

    def test_the_model_stays_whole_and_the_approval_keeps_its_icon_in_a_narrow_chat(self):
        text, width, label = self.fit(190)
        self.assertEqual((text, label), ("", "Default"))
        self.assertGreaterEqual(width, 7 * len("Default") + 9 + 14, "The chip holds its label without an ellipsis")

    def test_a_wide_chat_shows_everything_and_the_effort_goes_before_the_approval_label(self):
        self.assertEqual(self.fit(600, "GPT-6.1 Sol Low", "GPT-6.1 Sol")[::2], ("Approve for me", "GPT-6.1 Sol Low"))
        text, _, label = self.fit(290, "GPT-6.1 Sol Low", "GPT-6.1 Sol", approval="Read only")
        self.assertEqual((text, label), ("Read only", "GPT-6.1 Sol"))

    def test_a_label_too_long_for_the_widest_chip_drops_its_effort_before_an_ellipsis(self):
        long_name = "Claude Opus 5.5 Extended Thinking"   # 33 * 7 + 32 > 205
        self.assertEqual(self.fit(900, long_name + " High", long_name[:20])[2], long_name[:20])

    def test_a_trailing_qualifier_goes_before_an_ellipsis(self):
        """The narrow-chat GUI check met "Default (recommended) Low" (a catalogue's display name) at 299 px."""
        self.assertEqual(self.fit(190, "Default (recommended) Low", "Default (recommended)")[::2], ("", "Default"))

    def test_without_a_model_chip_the_approval_label_takes_the_room(self):
        self.assertEqual(self.fit(190, chip=False)[:2], ("Approve for me", 0))

    def test_only_a_chat_too_narrow_for_the_icon_and_chip_elides_the_model(self):
        _, width, label = self.fit(120, "A very long model name")
        self.assertEqual(label, "A very long model name")
        self.assertLess(width, 7 * len(label) + 9 + 14)

    def test_a_tooltip_for_a_composer_control_opens_above_the_composer(self):
        composer = (16, 10, 260, 90)
        send = (230, 28, 28, 28)
        x, y, w, h = cc.tip_rect(send, 200, 40, region_width=292, region_height=600, clear_of=composer)
        self.assertGreaterEqual(y, composer[1] + composer[3])
        self.assertEqual(cc.tip_rect(send, 200, 40, region_width=292, region_height=600)[1], 28 + 28 + 6,
                         "Without a card it stays right above the control")


class ShapeTests(unittest.TestCase):
    def test_states_follow_the_corners_of_the_shape_drawn_for_the_control(self):
        cc._radii.clear()
        cc.note_shape(10, 50, 200, 40, 8)
        self.assertEqual(cc.radius_for((10, 50, 200, 40)), 8)
        self.assertEqual(cc.radius_for((0, 0, 28, 28)), 14, "Icon buttons are round")
        self.assertEqual(cc.radius_for((0, 0, 200, 60)), 12, "Cards' corners otherwise")
        self.assertEqual(cc.radius_for((0, 0, 200, 60), 4), 4)

    def test_a_light_control_is_recognised_from_its_fill(self):
        cc._radii.clear()
        cc.note_shape(0, 0, 90, 28, 8, (0.95, 0.95, 0.95, 1))
        cc.note_shape(0, 40, 90, 28, 8, (0.205, 0.205, 0.205, 1))
        cc.note_shape(0, 80, 90, 28, 8, (1, 1, 1, 0.07))
        self.assertTrue(cc.is_light((0, 0, 90, 28)), "The primary Approve darkens on hover")
        self.assertFalse(cc.is_light((0, 40, 90, 28)))
        self.assertFalse(cc.is_light((0, 80, 90, 28)), "A translucent layer is not a light fill")
        self.assertFalse(cc.is_light((5, 5, 5, 5)), "Unknown shapes are treated as dark")

    def test_the_radius_memory_is_bounded(self):
        cc._radii.clear()
        for index in range(5000):
            cc.note_shape(index, 0, 10, 10, 2)
        self.assertLessEqual(len(cc._radii), 4097)


class SurfaceSourceTests(unittest.TestCase):
    """Guards for draw sites a window fixture reaches only partly."""

    def test_every_connect_choice_is_drawn_the_same_way(self):
        source = (ADDON / "chat_welcome.py").read_text()
        option = source.split("\ndef _draw_option(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("ghost(view, rect, 12, working)", option)
        self.assertNotIn("view.USER", option)
        self.assertNotIn("view.CARD", option)
        self.assertNotIn("welcome_api_key", option, "No special case for the API key row")

    def test_preferences_is_a_link_and_cancel_is_a_button(self):
        source = (ADDON / "chat_welcome.py").read_text()
        self.assertIn('_draw_link(view, state, [label], y, size, "settings", None, left)', source)
        self.assertIn('_draw_link(view, state, tip, y, size, "url", first_run.TIP_URL, left)', source,
                      "The tip is the same link")
        draw_link = source.split("\ndef _draw_link(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('canvas_controls.style(state, action, value, "link")', draw_link)
        self.assertIn('pill_button(view, hits, iface_("Cancel")', source)

    def test_canvases_keep_the_hand_by_consuming_moves_over_controls(self):
        for name in ("chat_view.py", "studio_input.py"):
            source = (ADDON / name).read_text()
            self.assertIn("canvas_controls.pointer(window, area.tag_redraw, state, x, y,", source, name)
        intro = (ADDON / "intro.py").read_text()
        self.assertIn("_open[\"cursor\"] = 'HAND_POINT' if control else 'DEFAULT'", intro)

    def test_send_without_anything_to_send_is_disabled_with_a_reason(self):
        source = (ADDON / "chat_composer.py").read_text()
        self.assertIn("(canvas_controls.DISABLED, canvas_controls.SEND_EMPTY)", source)

    def test_the_field_waits_until_the_chat_can_be_used_while_the_bar_navigates(self):
        """1.0.5: the chat bar is Angelo's navigation, so it is drawn before any account; only its field waits."""
        # The chat's full draw (``draw`` itself only reuses or keeps it while a turn waits, marble_frames).
        draw = (ADDON / "chat_view.py").read_text().split("\ndef _draw_chat():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("waiting = not (connected or blocks)", draw)
        self.assertNotIn("if connected or blocks:", draw, "The composer is always drawn")
        self.assertIn("waiting=waiting)", draw)
        self.assertIn('state["composer_hidden"] = True', draw)
        self.assertIn('state["focus_request"] = state["focused"] = True', draw, "The field takes focus once usable")
        composer = (ADDON / "chat_composer.py").read_text()
        self.assertIn("if line and not waiting and", composer, "No caret in a waiting field")
        modal = (ADDON / "chat_view.py").read_text().split("class ANGELO_OT_chat_input", 1)[1]
        self.assertIn('if state.get("composer_hidden"):', modal, "Nothing types into a hidden draft")

    def test_a_waiting_field_takes_no_typing_from_the_console_under_it(self):
        """The chat is a Console space: a key the waiting field passed through reached the Console keymap, which typed
        it into the field (slot studio-disc-105). Over the chat, keys that would edit it are swallowed; others pass."""
        from angelo_agent import input_policy
        modal = (ADDON / "chat_view.py").read_text().split("class ANGELO_OT_chat_input", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("return {'RUNNING_MODAL'} if over_chat and edits_field(event) else {'PASS_THROUGH'}", modal)

        def key(kind, unicode="", ctrl=False, oskey=False, alt=False):
            return SimpleNamespace(type=kind, unicode=unicode, ctrl=ctrl, oskey=oskey, alt=alt)
        for event in (key("A", "a"), key("A", "\u00e5", alt=True), key("BACK_SPACE"), key("BACK_SPACE", ctrl=True),
                      key("UP_ARROW"), key("RET"), key("V", oskey=True), key("V", ctrl=True), key("ESC")):
            self.assertTrue(input_policy.edits_field(event), event)
        for event in (key("TAB"), key("S", oskey=True), key("Z", ctrl=True), key("F3"), key("N", ctrl=True)):
            self.assertFalse(input_policy.edits_field(event), event)

    def test_a_team_sign_in_in_progress_is_busy_with_cancel(self):
        source = (ADDON / "team_chat.py").read_text()
        self.assertIn('row.update(busy=True, cancel="welcome_team_cancel" if note == "signing_in" else None)', source)
        self.assertIn('elif action == "welcome_team_cancel":', (ADDON / "chat_welcome.py").read_text())


if __name__ == "__main__":
    unittest.main()
