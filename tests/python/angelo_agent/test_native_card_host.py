# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The notice cards' host (card_host.py): the queue and the modal operator's input rules, on a stand-in bpy.

Covered: one card of each kind waits, by priority; a corner card yields to a more important one and comes
back; a card is recorded as shown only when it is on screen; the operator's input rules (hover, a press
released on the same control acts and one released elsewhere does not, a disabled control can't be
clicked, Tab order and wrap, Return with nothing focused, Escape, a click outside What's New), a corner
card that lets the viewport have everything except presses on itself, timers, window events, Blender's
motionless hover refresh and the quit key that always pass through, and a closed card's operator (or one whose
area went) that is woken and ends at once, passing its event on. Pixels are ``test_blender_update_crash``'s.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import card_kit as kit  # noqa: E402
from notices_fakes import Notices  # noqa: E402

CARD_RECT = (100, 100, 400, 200)
BUTTONS = {"first": (120, 120, 80, 30), "second": (220, 120, 80, 30)}


class FakeCard(kit.BaseCard):
    """Two buttons and a card rectangle; ``act`` records."""

    def __init__(self, kind="crash", modal=True, priority=100, outside=False):
        self.kind, self.modal, self.priority, self.outside_dismisses = kind, modal, priority, outside
        self.cancel_action, self.default_action = "cancel", None
        self.acts, self.closes = [], []

    def layout(self, view, region_w, region_h, scale, ui):
        plan = kit.Plan(card=CARD_RECT)
        plan.hits = [(rect, action, None) for action, rect in BUTTONS.items()]
        plan.focus_order = [(action, None) for action in BUTTONS]
        return plan

    def act(self, action, value, host):
        self.acts.append(action)
        if action in {"cancel", "second"}:
            host.close(action)

    def closed(self, how):
        self.closes.append(how)


def event(kind, value='NOTHING', x=0, y=0, shift=False):
    return SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y, mouse_prev_y=y, shift=shift)


def middle(name):
    x, y, w, h = BUTTONS[name]
    return x + w / 2, y + h / 2


class HostCase(unittest.TestCase):
    def setUp(self):
        self.notices = Notices()
        self.n = self.notices.__enter__()
        self.addCleanup(self.notices.__exit__, None, None, None)
        self.host = self.n.card_host
        self.wm = self.n.bpy.context.window_manager
        self.window = self.wm.windows[0]
        self.context = SimpleNamespace(window=self.window, window_manager=self.wm)
        self.host._queue.clear()
        self.host.history.clear()
        self.n.bpy.ops.angelo.card_host = self.invoke

    def invoke(self, mode):
        self.operator = self.host.ANGELO_OT_card_host()
        return self.operator.invoke(self.context, event('NONE'))

    def show(self, card):
        self.host.request(card)
        self.assertTrue(self.host.pump(lambda: True))
        self.host._active.plan = card.layout(None, 1200, 800, 1.0, self.host._active.ui)
        return card

    def send(self, *events):
        return [self.operator.modal(self.context, e) for e in events]


class QueueTests(HostCase):
    def test_cards_wait_by_priority_one_of_each_kind(self):
        update, whatsnew, crash = FakeCard("update", False, 10), FakeCard("whatsnew", True, 50), FakeCard("crash", True, 100)
        for card in (update, whatsnew, crash, FakeCard("update", False, 10)):
            self.host.request(card)
        self.assertEqual([c.kind for c in self.host.queued()], ["crash", "whatsnew", "update"])
        self.assertFalse(self.host.pump(lambda: False), "not shown before the window is ready")
        self.assertTrue(self.host.pump(lambda: True))
        self.assertEqual((self.host.active_card(), self.host.history), (crash, ["crash"]))
        self.assertFalse(self.host.pump(lambda: True), "one card at a time")

    def test_a_corner_card_yields_to_a_more_important_one_and_comes_back(self):
        corner = self.show(FakeCard("update", False, 10))
        self.host.request(FakeCard("crash", True, 100))
        self.assertIsNone(self.host.active_card())
        self.assertEqual(corner.closes, ["replaced"])
        self.assertEqual([c.kind for c in self.host.queued()], ["crash", "update"])

    def test_a_canvas_above_the_card_passes_on_what_the_card_takes(self):
        # The showcase (connect_hero) asks before taking an event: its operator may sit above the card's.
        area = self.window.screen.areas[0].as_pointer()
        self.assertFalse(self.host.takes_input(area, 5, 5) or self.host.shown_on(area))
        modal = self.show(FakeCard("crash", True, 100))
        self.assertTrue(self.host.shown_on(area))
        self.assertTrue(self.host.takes_input(area, 5, 5), "A modal card owns its whole 3D view")
        self.assertFalse(self.host.takes_input(area + 1, 5, 5), "Only its own view")
        self.host.close("done")
        self.assertEqual(modal.closes, ["done"])
        self.show(FakeCard("update", False, 10))
        self.assertTrue(self.host.takes_input(area, 150, 150), "A corner card owns its rectangle")
        self.assertFalse(self.host.takes_input(area, 5, 5), "The rest of the view is not the card's")

    def test_a_modal_card_is_never_replaced(self):
        crash = self.show(FakeCard("crash", True, 100))
        self.host.request(FakeCard("other", True, 200))
        self.assertIs(self.host.active_card(), crash)

    def test_a_card_whose_operator_did_not_start_is_not_recorded_as_shown(self):
        self.n.bpy.ops.angelo.card_host = lambda mode: {'CANCELLED'}
        self.host.request(FakeCard())
        self.assertFalse(self.host.pump(lambda: True))
        self.assertEqual(self.host.history, [])

    def test_a_card_that_lost_its_view_returns_when_it_asks_to(self):
        card = self.show(FakeCard())
        card.retry_when_lost = True
        self.host.close("lost")
        self.assertEqual([c.kind for c in self.host.queued()], ["crash"])


class WindowTests(HostCase):
    """A card shows in the window holding the area it asks for, whichever window that is (Media Studio's own)."""

    def setUp(self):
        super().setUp()
        Area, Window = type(self.window.screen.areas[0]), type(self.window)
        self.studio = Area('CONSOLE', 22)
        self.wm.windows.append(Window(pointer=2, areas=[self.studio]))
        self.shown = []
        self.host._show = lambda card, window: self.shown.append((card, window.as_pointer())) or True

    def card_over(self, pointer):
        card = FakeCard()
        card.target_area = lambda window: next((a for a in window.screen.areas if a.as_pointer() == pointer), None)
        return card

    def test_a_card_asked_for_over_an_area_in_another_window_shows_there(self):
        card = self.card_over(self.studio.as_pointer())
        self.host.request(card)
        self.assertTrue(self.host.pump(lambda: True))
        self.assertEqual(self.shown, [(card, 2)], "Studio's window, never the main window's 3D view")
        self.assertIs(self.host._target_area(self.wm.windows[1], card), self.studio)

    def test_a_card_asked_for_over_preferences_shows_in_its_temporary_window(self):
        self.wm.windows[1].screen.is_temporary = True   # Preferences' own window.
        card = self.card_over(self.studio.as_pointer())
        self.host.request(card)
        self.assertTrue(self.host.pump(lambda: True))
        self.assertEqual(self.shown, [(card, 2)])

    def test_a_card_without_an_area_never_falls_back_to_a_temporary_window(self):
        self.wm.windows[0].screen.is_temporary = True
        self.wm.windows[1].screen.areas.append(type(self.studio)('VIEW_3D', 23))
        card = FakeCard()
        self.host.request(card)
        self.assertTrue(self.host.pump(lambda: True))
        self.assertEqual(self.shown, [(card, 2)])

    def test_a_card_whose_area_is_nowhere_falls_back_to_a_3d_view(self):
        card = self.card_over(999)
        self.host.request(card)
        self.assertTrue(self.host.pump(lambda: True))
        self.assertEqual(self.shown, [(card, 1)])

    def test_a_card_that_asks_for_no_area_shows_over_the_3d_view(self):
        card = FakeCard()
        self.host.request(card)
        self.assertTrue(self.host.pump(lambda: True))
        self.assertEqual(self.shown, [(card, 1)])


class InputTests(HostCase):
    def test_hover_then_press_and_release_on_the_same_control_acts(self):
        card = self.show(FakeCard())
        x, y = middle("first")
        self.send(event('MOUSEMOVE', x=x, y=y))
        self.assertEqual((self.host._active.ui.hover, self.window.cursors[-1]), (("first", None), "HAND"))
        self.send(event('LEFTMOUSE', 'PRESS', x, y))
        self.assertEqual(self.host._active.ui.pressed, ("first", None))
        self.send(event('LEFTMOUSE', 'RELEASE', x, y))
        self.assertEqual(card.acts, ["first"])

    def test_a_press_released_elsewhere_does_nothing(self):
        card = self.show(FakeCard())
        x, y = middle("first")
        results = self.send(event('LEFTMOUSE', 'PRESS', x, y), event('LEFTMOUSE', 'RELEASE', x, y + 120))
        self.assertEqual((card.acts, results), ([], [{'RUNNING_MODAL'}, {'RUNNING_MODAL'}]))

    def test_a_control_without_a_hit_area_cannot_be_clicked(self):
        card = self.show(FakeCard())
        self.host._active.plan.hits = [h for h in self.host._active.plan.hits if h[1] != "first"]   # As a disabled one.
        x, y = middle("first")
        self.send(event('MOUSEMOVE', x=x, y=y), event('LEFTMOUSE', 'PRESS', x, y), event('LEFTMOUSE', 'RELEASE', x, y))
        self.assertEqual((card.acts, self.host._active.ui.hover), ([], None))

    def test_tab_walks_the_focus_order_and_wraps_shift_tab_goes_back(self):
        card = self.show(FakeCard())
        ui = self.host._active.ui
        self.send(event('TAB', 'PRESS', 900, 700))
        self.assertEqual((ui.focus, self.host._active.keyboard), (("first", None), True))
        self.send(event('TAB', 'PRESS', 900, 700), event('TAB', 'PRESS', 900, 700))
        self.assertEqual(ui.focus, ("first", None))
        self.send(event('TAB', 'PRESS', 900, 700, shift=True))
        self.assertEqual(ui.focus, ("second", None))
        self.send(event('RET', 'PRESS', 900, 700))
        self.assertEqual(card.acts, ["second"])

    def test_return_with_nothing_focused_does_nothing_and_escape_cancels(self):
        card = self.show(FakeCard())
        self.send(event('RET', 'PRESS', 900, 700), event('SPACE', 'PRESS', 900, 700))
        self.assertEqual(card.acts, [])
        self.send(event('ESC', 'PRESS', 900, 700))
        self.assertEqual((card.acts, card.closes, self.host.active_card()), (["cancel"], ["cancel"], None))

    def test_a_mouse_press_hides_the_focus_ring(self):
        self.show(FakeCard())
        self.send(event('TAB', 'PRESS', 900, 700), event('LEFTMOUSE', 'PRESS', 900, 700))
        self.assertEqual((self.host._active.keyboard, self.host._active.ui.focus), (False, None))

    def test_a_click_outside_whats_new_closes_it(self):
        card = self.show(FakeCard("whatsnew", True, 50, outside=True))
        self.send(event('LEFTMOUSE', 'PRESS', 900, 700))
        self.assertEqual(card.acts, ["cancel"])

    def test_a_modal_card_takes_input_over_the_view_and_passes_it_elsewhere(self):
        self.show(FakeCard())
        self.assertEqual(self.send(event('MIDDLEMOUSE', 'PRESS', 900, 700))[0], {'RUNNING_MODAL'})
        self.assertEqual(self.send(event('MIDDLEMOUSE', 'PRESS', 1300, 700))[0], {'PASS_THROUGH'}, "outside the view")

    def test_a_corner_card_takes_only_presses_on_itself(self):
        card = self.show(FakeCard("update", False, 10))
        inside, outside = (150, 280), (900, 700)
        for kind in ('MIDDLEMOUSE', 'RIGHTMOUSE', 'WHEELUPMOUSE'):
            self.assertEqual(self.send(event(kind, 'PRESS', *outside))[0], {'PASS_THROUGH'}, kind)
            self.assertEqual(self.send(event(kind, 'PRESS', *inside))[0], {'RUNNING_MODAL'}, kind)
        self.assertEqual(self.send(event('LEFTMOUSE', 'PRESS', *inside))[0], {'RUNNING_MODAL'}, "the card's background")
        self.assertEqual(self.send(event('LEFTMOUSE', 'PRESS', *outside))[0], {'PASS_THROUGH'})
        self.assertEqual(self.send(event('MOUSEMOVE', x=inside[0], y=inside[1]))[0], {'PASS_THROUGH'})
        for key in ('ESC', 'TAB', 'RET', 'A'):
            self.assertEqual(self.send(event(key, 'PRESS', *inside))[0], {'PASS_THROUGH'}, key)
        self.assertEqual(card.acts, [])

    def test_timers_always_pass_through(self):
        self.show(FakeCard())
        for kind in ('TIMER', 'TIMER_REPORT', 'TIMERREGION', 'NONE'):
            self.assertEqual(self.send(event(kind, x=300, y=200))[0], {'PASS_THROUGH'}, kind)

    def test_a_modal_card_passes_blenders_refresh_window_events_and_the_quit_key(self):
        """A quit waits for one motionless move; a card opened after the request sits in front of it."""
        self.show(FakeCard())
        self.wm.keyconfigs = SimpleNamespace(user=SimpleNamespace(keymaps={"Window": SimpleNamespace(keymap_items=[
            SimpleNamespace(idname="wm.quit_blender", active=True, type='Q', value='PRESS', shift=0, ctrl=1, alt=0,
                            oskey=0)])}))
        inside = middle("first")
        refresh = SimpleNamespace(type='MOUSEMOVE', value='NOTHING', mouse_x=inside[0], mouse_y=inside[1],
                                  mouse_prev_x=inside[0], mouse_prev_y=inside[1])
        self.assertEqual(self.send(refresh)[0], {'PASS_THROUGH'})
        self.assertEqual(self.host._active.ui.hover, ("first", None), "after showing its hover")
        moved = SimpleNamespace(**dict(vars(refresh), mouse_prev_x=inside[0] - 3))
        self.assertEqual(self.send(moved)[0], {'RUNNING_MODAL'}, "the person's move stays with the card")
        self.assertEqual(self.send(event('WINDOW_DEACTIVATE', x=300, y=200))[0], {'PASS_THROUGH'})
        quit_key = SimpleNamespace(**dict(vars(event('Q', 'PRESS', *inside)), ctrl=True, alt=False, oskey=False))
        self.assertEqual(self.send(quit_key)[0], {'PASS_THROUGH'})
        self.assertEqual(self.send(event('Q', 'PRESS', *inside))[0], {'RUNNING_MODAL'}, "Q alone stays")

    def test_a_card_that_lost_its_area_passes_its_event_on(self):
        self.show(FakeCard())
        self.window.screen.areas.clear()
        self.assertEqual(self.send(event('MOUSEMOVE', x=300, y=200))[0], {'FINISHED', 'PASS_THROUGH'})

    def test_a_card_whose_area_closed_without_an_event_waits_again(self):
        # The chat hint's own Hide chat closes the chat it stands over; no input may follow (1.0.3 GUI suite).
        card = self.show(FakeCard("chat_hint", False, 5))
        card.retry_when_lost = True
        self.assertFalse(self.host.check_area(), "its area is still there")
        self.window.screen.areas.clear()
        self.assertTrue(self.host.check_area())
        self.assertIsNone(self.host.active_card())
        self.assertEqual(card.closes, ["lost"])
        self.assertEqual(self.host.queued(), [card], "shown again by the next pump, where it belongs now")
        self.assertFalse(self.host.check_area(), "nothing visible: nothing to do")

    def test_a_closed_cards_operator_is_woken_and_ends_passing_its_event_on(self):
        self.show(FakeCard())
        operator = self.operator
        self.host.close("done")
        self.assertEqual(len(self.wm.timers), 1, "a one-shot timer wakes the operator")
        self.assertIs(self.wm.timers[0].window, self.window)
        self.assertEqual(operator.modal(self.context, event('TIMER')), {'FINISHED', 'PASS_THROUGH'})
        self.assertEqual(self.wm.removed_timers, self.wm.timers)
        self.assertEqual(operator.modal(self.context, event('LEFTMOUSE', 'PRESS', 150, 130)), {'FINISHED', 'PASS_THROUGH'})

    def test_the_wake_up_timer_is_removed_by_half_a_second_at_the_latest(self):
        self.show(FakeCard())
        self.host.close("done")
        timers = self.n.bpy.app.timers.registered
        self.assertEqual(timers.get(self.host._end_nudge), 0.5)
        self.assertIsNone(self.host._end_nudge())
        self.assertEqual(self.wm.removed_timers, self.wm.timers, "even when no operator ever saw it")

    def test_opening_a_file_closes_the_card_as_lost_without_a_wake_up(self):
        self.host.register()
        self.assertIn(self.host._load_pre, self.n.bpy.app.handlers.load_pre)
        card = self.show(FakeCard())
        card.retry_when_lost = True
        self.host._load_pre(None)
        self.assertEqual((card.closes, self.host.active_card(), self.wm.timers), (["lost"], None, []))
        self.assertEqual([c.kind for c in self.host.queued()], ["crash"], "shown again once the file is open")
        self.host.unregister()
        self.assertEqual(self.n.bpy.app.handlers.load_pre, [])

    def test_the_view_reads_the_chat_fonts_at_every_use(self):
        chat = SimpleNamespace(_font=3, _mono=0, wrap=None, shape=None, text=None, stroke=None)
        view = self.n.card_host._View.__new__(self.n.card_host._View)
        view._chat = chat
        self.assertEqual((view._font, view._mono), (3, 3))
        chat._font, chat._mono = 7, 8   # Reloaded after a file was opened or a theme applied.
        self.assertEqual((view._font, view._mono), (7, 8))


if __name__ == "__main__":
    unittest.main()
