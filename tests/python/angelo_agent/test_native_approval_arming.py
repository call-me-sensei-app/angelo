# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A control that grants takes only a click the person aimed at it (approval_arming.py).

Found by the 2.1.0 external-access review (2026-11-14) and ruled a 2.0.0 defect: a card that appeared under a
resting pointer, or the click that brought Angelo's window forward, could land on Approve and start a paid generation
that spends credits. One test class per surface: a bounded edit's card, a paid generation's card, a script's card
(Review & approve, Always allow scripts, Always allow this file) and the preview review card. On every one a grant
takes a press only after its shield, after a pointer move into it, and not after a focus change until it is armed
again; the keyboard grants only a control the person focused, after its shield. Time is a fake; nothing sleeps.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import approval_arming  # noqa: E402
from angelo_agent.approval_wait import SHIELD_SECONDS  # noqa: E402

WINDOW = 7
# The hits each chat card registers (chat_card_draw._draw_approval, chat_cards.draw_always_allow), in its order.
BOUNDED = [((32, 10, 90, 28), "approve", "edit-1"), ((130, 10, 90, 28), "decline", "edit-1"),
           ((228, 10, 110, 28), "review", "edit-1")]
PAID = [((32, 10, 90, 28), "approve", "generate-1"), ((130, 10, 90, 28), "decline", "generate-1"),
        ((228, 10, 110, 28), "review", "generate-1")]
SCRIPT = [((32, 50, 260, 22), "script_always_allow", "script-1"), ((32, 10, 140, 28), "approve", "script-1"),
          ((180, 10, 90, 28), "decline", "script-1"), ((278, 10, 100, 28), "review", "script-1")]
SCRIPT_FILE = [((32, 50, 300, 22), "script_file_allow", "script-2"), ((32, 10, 140, 28), "approve", "script-2")]


def middle(rect):
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ArmingCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        original = approval_arming.clock
        approval_arming.clock = self.clock
        self.addCleanup(setattr, approval_arming, "clock", original)
        approval_arming.forget()
        self.addCleanup(approval_arming.forget)

    def later(self, seconds):
        self.clock.now += seconds


class ChatSurface:
    """The chat canvas's own calls (chat_view): draw, pointer moves, presses, window events and the keyboard."""

    hits = ()

    def setUp(self):
        super().setUp()
        self.state = {"hits": list(self.hits)}

    def draw(self, hits=None):
        self.state["hits"] = list(self.hits if hits is None else hits)
        approval_arming.view(self.state).drawn(approval_arming.granting(self.state["hits"]))

    def move(self, point):
        approval_arming.view(self.state, WINDOW).moved(point)

    def lands(self, hit):
        approval_arming.view(self.state, WINDOW)
        return approval_arming.press_lands(self.state, hit)

    def grants(self):
        return [hit for hit in self.hits if hit[1] in approval_arming.GRANTS]

    def aim(self, hit):
        """The person moves from outside the control into it."""
        self.move((-50, -50))
        self.move(middle(hit[0]))

    def test_a_card_that_appears_under_a_resting_pointer_takes_no_click(self):
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.move(middle(hit[0]))   # The pointer rests where the card is about to appear.
                self.draw()
                self.assertFalse(self.lands(hit), "the press in the same moment")
                self.later(SHIELD_SECONDS + 5)
                self.move(middle(hit[0]))   # Blender's motionless refresh move: no move.
                self.assertFalse(self.lands(hit), "long after, without a move into it")

    def test_a_move_into_it_during_its_shield_does_not_arm_it(self):
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS / 2)
                self.aim(hit)
                self.assertFalse(self.lands(hit))
                self.later(SHIELD_SECONDS)
                self.assertFalse(self.lands(hit), "still in it, but it moved in before the shield passed")

    def test_after_its_shield_a_move_into_it_arms_it(self):
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS)
                self.aim(hit)
                self.assertTrue(self.lands(hit))

    def test_losing_focus_disarms_it_and_the_click_that_brings_the_window_back_never_grants(self):
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS)
                self.aim(hit)
                self.assertTrue(self.lands(hit))
                approval_arming.window_input(WINDOW, "WINDOW_DEACTIVATE", "NOTHING")
                self.assertFalse(self.lands(hit), "another app took focus")
                self.later(10)
                self.aim(hit)   # Pointer moves over the background window (Windows delivers them).
                self.assertTrue(approval_arming.window_input(WINDOW, "LEFTMOUSE", "PRESS"))
                self.assertFalse(self.lands(hit), "the click that brought the window forward")
                self.later(SHIELD_SECONDS)
                self.aim(hit)
                self.assertFalse(approval_arming.window_input(WINDOW, "LEFTMOUSE", "PRESS"))
                self.assertTrue(self.lands(hit), "aimed again after the window came back")

    def test_a_control_that_moves_needs_its_shield_and_a_move_into_it_again(self):
        hit = self.grants()[0]
        self.draw()
        self.later(SHIELD_SECONDS)
        self.aim(hit)
        moved = ((hit[0][0], hit[0][1] + 40, hit[0][2], hit[0][3]), hit[1], hit[2])
        self.draw([moved if h == hit else h for h in self.hits])   # Scrolled, or the card laid out again.
        self.move(middle(moved[0]))
        self.assertFalse(self.lands(moved))
        self.later(SHIELD_SECONDS)
        self.aim(moved)
        self.assertTrue(self.lands(moved))

    def test_a_view_that_never_saw_the_pointer_takes_its_first_move_as_no_move_into_it(self):
        hit = self.grants()[0]
        self.draw()
        self.later(SHIELD_SECONDS + 1)
        self.move(middle(hit[0]))   # The first move this view sees: where the pointer came from is not known.
        self.assertFalse(self.lands(hit))

    def test_a_card_that_is_gone_and_comes_back_starts_again(self):
        hit = self.grants()[0]
        self.draw()
        self.later(SHIELD_SECONDS)
        self.aim(hit)
        self.draw([])   # Scrolled out of view.
        self.draw()
        self.assertFalse(self.lands(hit))

    def test_the_cards_other_controls_take_their_click_as_before(self):
        self.draw()
        for hit in self.hits:
            if hit[1] not in approval_arming.GRANTS:
                self.assertTrue(self.lands(hit), hit[1])

    def test_the_keyboard_grants_a_focused_control_only_after_its_shield(self):
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                key = (hit[1], hit[2])
                self.assertFalse(approval_arming.view(self.state, WINDOW).key_armed(key))
                self.later(SHIELD_SECONDS)
                self.assertTrue(approval_arming.view(self.state, WINDOW).key_armed(key))
                approval_arming.window_input(WINDOW, "WINDOW_DEACTIVATE", "NOTHING")
                self.later(10)
                self.assertTrue(approval_arming.window_input(WINDOW, "RET", "PRESS"))
                self.assertFalse(approval_arming.view(self.state, WINDOW).key_armed(key), "the first key back")


class BoundedEditCardTests(ChatSurface, ArmingCase):
    hits = BOUNDED


class PaidGenerationCardTests(ChatSurface, ArmingCase):
    hits = PAID


class ScriptCardTests(ChatSurface, ArmingCase):
    hits = SCRIPT


class ScriptFileCardTests(ChatSurface, ArmingCase):
    hits = SCRIPT_FILE


def _function(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


class ChatWiringTests(unittest.TestCase):
    """The chat canvas makes those calls on the real paths (chat_view needs Blender; its source is checked)."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.modal = ast.unparse(next(
            f for n in ast.walk(cls.tree) if isinstance(n, ast.ClassDef) and n.name == "ANGELO_OT_chat_input"
            for f in n.body if isinstance(f, ast.FunctionDef) and f.name == "modal"))

    def test_every_window_event_is_seen_before_anything_passes_it_on(self):
        first = self.modal.index("approval_arming.window_input(")
        self.assertLess(first, self.modal.index("overlay_passes(event"), "WINDOW_DEACTIVATE passes through there")

    def test_moves_presses_and_leaving_go_through_the_arming(self):
        self.assertIn("approval_arming.view(state, window.as_pointer()).moved((x, y))", self.modal)
        self.assertIn("approval_arming.left(", self.modal)
        self.assertIn("approval_arming.press_lands(state, (rect, action, value))", self.modal)

    def test_what_was_drawn_arms_after_the_approvals_were_collected(self):
        draw = self.source[self.source.index("chat_guard.shown_approvals(controller"):]
        self.assertIn("approval_arming.view(state).drawn(approval_arming.granting(state[\"hits\"]))",
                      draw[:400])

    def test_every_control_that_grants_is_armed(self):
        """Any dispatch branch that approves a call or allows scripts is a grant (``GRANTS``)."""
        granted = set()
        for name, module, marks in (("dispatch_action", "chat_view.py", ("ops.angelo.approve",)),
                                    ("dispatch", "chat_cards.py", ("allow_from_card",))):
            tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
            for node in ast.walk(_function(tree, name)):
                body = "".join(ast.unparse(statement) for statement in node.body) if isinstance(node, ast.If) else ""
                if any(mark in body for mark in marks):
                    for compare in ast.walk(node.test):
                        if isinstance(compare, ast.Compare) and getattr(compare.left, "id", None) == "action":
                            granted |= {c.value for c in ast.walk(compare) if isinstance(c, ast.Constant)
                                        and isinstance(c.value, str)}
        self.assertTrue(granted)
        self.assertLessEqual(granted, approval_arming.GRANTS)


class KeyboardGateTests(ArmingCase):
    """chat_key_arming dispatches a focused grant only once ``key_armed``."""

    def test_the_focused_approve_waits_for_its_shield(self):
        from test_native_chat_key_arming import KeyboardOwnership
        case = KeyboardOwnership("test_unchanged_production_toolcall_activates_once_on_release")
        case.setUp()
        self.addCleanup(case.tearDown)
        approval_arming.forget()
        case.state.pop("arming", None)
        approval_arming.view(case.state).drawn(approval_arming.granting(case.state["hits"]))
        case.press()
        case.release()
        self.assertEqual(case.invoked, [], "Return within the shield")
        self.later(SHIELD_SECONDS)
        case.press()
        case.release()
        self.assertEqual(len(case.invoked), 1)


class ReviewCardTests(ArmingCase):
    """The preview review card (review_card.ReviewAcceptCard) in card_host: its confirmation and Accept grant."""

    def setUp(self):
        super().setUp()
        from test_native_card_host import BUTTONS, FakeCard, HostCase, event, middle as button
        self.event, self.button = event, button

        class Card(FakeCard):
            grants = frozenset({"first"})   # As the review card's {"confirm", "accept"}.

        self.case = HostCase("show")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.card = self.case.show(Card())
        self.case.host.card_input.sync(self.case.host._active)   # Drawn now, as _draw records it (card_host).
        self.buttons = BUTTONS

    def send(self, *events):
        return self.case.send(*events)

    def click(self, name):
        x, y = self.button(name)
        self.send(self.event('LEFTMOUSE', 'PRESS', x, y), self.event('LEFTMOUSE', 'RELEASE', x, y))

    def aim(self, name):
        x, y = self.button(name)
        moved = self.event('MOUSEMOVE', x=x, y=y)
        moved.mouse_prev_x = x - 300
        self.send(SimpleNamespace(**dict(vars(self.event('MOUSEMOVE', x=x - 300, y=y)), mouse_prev_x=x - 301)), moved)

    def test_the_review_cards_grants_and_no_keyboard_default(self):
        from angelo_agent import review_card
        self.assertLessEqual({"confirm", "accept"}, review_card.ReviewAcceptCard.grants)
        self.assertNotIn(review_card.ReviewAcceptCard.default_action, review_card.ReviewAcceptCard.grants)
        wait = ast.unparse(_function(ast.parse((ADDON / "review_card.py").read_text(encoding="utf-8")), "wait"))
        self.assertNotIn("'RET'", wait, "Return anywhere no longer accepts")

    def test_a_click_under_the_resting_pointer_or_within_the_shield_lands_on_nothing(self):
        self.click("first")
        self.later(SHIELD_SECONDS + 3)
        self.click("first")
        self.assertEqual(self.card.acts, [])

    def test_after_its_shield_a_move_into_it_arms_it_and_other_controls_act_as_before(self):
        self.later(SHIELD_SECONDS)
        self.aim("first")
        self.click("first")
        self.assertEqual(self.card.acts, ["first"])
        self.click("second")   # Not a grant: its click as before (it closes the card).
        self.assertEqual(self.card.acts, ["first", "second"])

    def test_a_focus_change_disarms_it_and_the_returning_click_never_grants(self):
        self.later(SHIELD_SECONDS)
        self.aim("first")
        self.send(self.event('WINDOW_DEACTIVATE', x=300, y=200))
        self.later(10)
        self.aim("first")
        self.click("first")
        self.assertEqual(self.card.acts, [], "the click that brought the window forward")
        self.later(SHIELD_SECONDS)
        self.aim("first")
        self.click("first")
        self.assertEqual(self.card.acts, ["first"])

    def test_the_keyboard_grants_only_the_focused_control_after_its_shield(self):
        self.send(self.event('RET', 'PRESS', 900, 700))
        self.assertEqual(self.card.acts, [], "nothing focused: no default grants")
        self.send(self.event('TAB', 'PRESS', 900, 700), self.event('RET', 'PRESS', 900, 700))
        self.assertEqual(self.card.acts, [], "focused, within its shield")
        self.later(SHIELD_SECONDS)
        self.send(self.event('RET', 'PRESS', 900, 700))
        self.assertEqual(self.card.acts, ["first"])


if __name__ == "__main__":
    unittest.main()
