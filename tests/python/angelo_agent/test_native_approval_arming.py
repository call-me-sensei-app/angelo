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
import angelo_guard_twin as twin  # noqa: E402  (C's focus record: the window manager's notes, in Blender's order)
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
        twin.reset()
        self.addCleanup(twin.reset)

    # The window manager's notes to C, as Blender sends them (wm_window.cc, wm_event_system.cc): before any handler.
    def focus_lost(self):
        twin.note_input(WINDOW, "WINDOW_DEACTIVATE", "NOTHING", now=self.clock.now)

    def activated(self, point=(0, 0)):
        """The operating system brings the window forward: C's note, then the synthetic move where the pointer is."""
        twin.note_activate(WINDOW, now=self.clock.now)
        twin.note_input(WINDOW, "MOUSEMOVE", "NOTHING", *point, now=self.clock.now)

    def pressed(self, point=(0, 0), kind="LEFTMOUSE"):
        twin.note_input(WINDOW, kind, "PRESS", *point, now=self.clock.now)

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

    def move(self, point, real=True):
        approval_arming.view(self.state, WINDOW).moved(point, real=real)

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
                self.assertFalse(self.lands(hit), "a double-click's second press never grants")
                for _ in range(3):   # A press never aims (the break-it review of ca6f5ff137d, S1): only a move does.
                    self.later(SHIELD_SECONDS + 0.5)
                    self.assertFalse(self.lands(hit), "presses a second or more apart, no move")
                self.aim(hit)
                self.assertTrue(self.lands(hit), "the person's move onto it, then one click")

    def test_a_second_card_in_the_first_ones_place_takes_no_presses_without_a_move(self):
        """The review's S1: the person approves card A; card B appears where A was, under the resting pointer; they
        click again, twice, a second or more apart, thinking the first did not take. B is never granted."""
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.aim(hit)
                self.later(SHIELD_SECONDS)
                self.assertTrue(self.lands(hit), "card A: aimed and clicked")
                b = (hit[0], hit[1], ("card-b", hit[2]))
                self.draw([b if h is hit else h for h in self.hits])   # Card B, same place, a new control.
                for _ in range(3):
                    self.later(SHIELD_SECONDS + 0.2)
                    self.assertFalse(self.lands(b), "card B under the resting pointer")
                self.aim(b)
                self.assertTrue(self.lands(b), "card B after the person's move onto it")

    def test_a_move_into_it_during_its_shield_aims_and_one_press_after_the_shield_grants(self):
        """2.0.1 (the maintainer: "I have to double click approve"): the person moves onto Approve as the card shows
        and clicks once after its shield."""
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS / 2)
                self.aim(hit)
                self.assertFalse(self.lands(hit), "within its shield")
                self.later(SHIELD_SECONDS)
                self.assertTrue(self.lands(hit), "one press once the shield passed")

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
                twin.reset()   # Each subtest's window starts with C's record fresh, as a new window's.
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS)
                self.aim(hit)
                self.assertTrue(self.lands(hit))
                self.focus_lost()
                self.assertFalse(self.lands(hit), "another app took focus")
                self.later(10)
                self.aim(hit)   # Pointer moves over the background window (Windows delivers them).
                self.activated(middle(hit[0]))   # The click on it brings the window forward.
                self.move(middle(hit[0]))   # As the handler sees it
                self.pressed(middle(hit[0]))
                self.assertFalse(self.lands(hit), "the click that brought the window forward")
                self.later(SHIELD_SECONDS)
                self.aim(hit)
                self.pressed(middle(hit[0]))
                self.assertTrue(self.lands(hit), "aimed again after the window came back")

    def test_a_control_laid_out_away_from_the_pointer_keeps_its_shield_and_needs_an_aim(self):
        hit = self.grants()[0]
        self.draw()
        self.later(SHIELD_SECONDS)
        self.aim(hit)
        moved = ((hit[0][0], hit[0][1] + 40, hit[0][2], hit[0][3]), hit[1], hit[2])
        self.draw([moved if h == hit else h for h in self.hits])   # Scrolled, or the card laid out again.
        self.move(middle(moved[0]))   # Into it where it is now: its shield stood all along.
        self.assertTrue(self.lands(moved))

    def test_one_press_grants_while_rows_stream_in_and_shift_the_card_every_frame(self):
        """2.0.1 (the maintainer: 2, then 5, then 6-8 clicks while an app drove the chat): each streamed row re-laid
        the card out, restarting its shield and dropping the aim, so presses kept landing on nothing."""
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS / 2)
                self.aim(hit)
                for step in range(40):   # A row every 50 ms; the card shifts 1 px up and back.
                    self.later(0.05)
                    shift = step % 2
                    self.draw([((r[0], r[1] + shift, r[2], r[3]), a, v) for r, a, v in self.hits])
                current = next(h for h in self.state["hits"] if h[1:] == hit[1:])
                self.assertTrue(self.lands(current), "one press, the card still shifting")

    def test_an_aimed_control_shifted_under_a_pointer_that_left_it_grants_nothing(self):
        """The break-it review's M1, scenario 1: the pointer crossed Approve once and rests elsewhere; rows shift
        Approve under it. That control arrived under the pointer: neither a press nor a double-click grants."""
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS)
                self.aim(hit)
                rest = (middle(hit[0])[0], hit[0][1] + hit[0][3] + 30)
                self.move(rest)   # Leaves it.
                shifted = [((r[0], r[1] + 30, r[2], r[3]), a, v) for r, a, v in self.hits]
                self.draw(shifted)   # Now under the resting pointer.
                moved = next(h for h in shifted if h[1:] == hit[1:])
                self.assertFalse(self.lands(moved))
                self.later(0.09)
                self.assertFalse(self.lands(moved), "nor a double-click")

    def test_a_never_aimed_control_jumping_under_the_pointer_takes_no_double_click(self):
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                away = [((r[0], r[1] + 300, r[2], r[3]), a, v) for r, a, v in self.hits]
                self.draw(away)
                self.move((-50, -50))
                self.move(middle(hit[0]))   # Rests where the control will jump to.
                self.later(SHIELD_SECONDS * 2)
                self.draw()   # It jumps under the pointer.
                self.assertFalse(self.lands(hit), "within its new shield")
                self.later(SHIELD_SECONDS + 0.1)
                self.assertFalse(self.lands(hit), "after its shield, no move into it")
                self.later(0.09)
                self.assertFalse(self.lands(hit), "a double-click's second press")
                self.later(SHIELD_SECONDS)
                self.assertFalse(self.lands(hit), "a press never aims (review S1)")
                self.aim(hit)
                self.assertTrue(self.lands(hit), "the person's move onto it, then one click")

    def test_dragging_off_a_pressed_grant_cancels_it_even_if_it_is_laid_out_under_the_release(self):
        hit = self.grants()[0]
        self.draw()
        self.later(SHIELD_SECONDS)
        self.aim(hit)
        self.assertTrue(self.lands(hit))
        self.move((-50, -50))   # Dragged off.
        self.move(middle(hit[0]))   # And back, or the card re-laid out under it.
        self.assertFalse(approval_arming.view(self.state).stayed((hit[1], hit[2])))
        self.assertTrue(self.lands(hit))
        self.assertTrue(approval_arming.view(self.state).stayed((hit[1], hit[2])), "a press that stayed releases")

    def test_a_held_press_whose_control_is_not_drawn_for_a_frame_releases_nothing(self):
        hit = self.grants()[0]
        self.draw()
        self.later(SHIELD_SECONDS)
        self.aim(hit)
        self.assertTrue(self.lands(hit))
        self.draw([h for h in self.hits if h != hit])   # Gone for a frame (scrolled out, re-laid out).
        self.draw()
        self.assertFalse(approval_arming.view(self.state).stayed((hit[1], hit[2])))

    def test_the_click_that_brings_the_window_forward_aims_so_the_next_press_grants(self):
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS)
                twin.reset()
                self.focus_lost()
                self.later(10)
                self.activated(middle(hit[0]))   # A click on it brings the window forward.
                self.move(middle(hit[0]))   # As the handler sees it   # The activation's synthetic move: no move into it.
                self.pressed(middle(hit[0]))
                self.assertFalse(self.lands(hit), "the click that brought the window forward")
                self.later(SHIELD_SECONDS)
                self.pressed(middle(hit[0]))
                self.assertTrue(self.lands(hit), "the next press, without a move")

    def test_an_activation_without_a_press_leaves_the_resting_pointer_unaimed(self):
        """The re-review of acd0d465ee9, SHOULD-1: Cmd-Tab, a Dock click or ``open -a`` brings the window forward
        with no press; the person's later presses on a resting pointer are ordinary ones and never grant."""
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                twin.reset()
                self.state = {}
                self.draw()
                self.later(SHIELD_SECONDS)
                self.aim(hit)
                self.focus_lost()
                self.later(10)
                self.activated(middle(hit[0]))   # Cmd-Tab: no press comes with it.
                self.move(middle(hit[0]))   # As the handler sees it
                for _ in range(3):
                    self.later(SHIELD_SECONDS + 0.5)
                    self.pressed(middle(hit[0]))
                    self.assertFalse(self.lands(hit), "a resting pointer after the activation")
                self.aim(hit)
                self.pressed(middle(hit[0]))
                self.assertTrue(self.lands(hit), "the person's move onto it, then one click")

    def test_jitter_inside_a_control_after_its_shield_aims_and_a_still_pointer_never_does(self):
        """SHOULD-3: card B appears in card A's place under the pointer; a click there is refused, and the hand's
        smallest real move inside it then aims, so the next click works without leaving the button."""
        for hit in self.grants():
            with self.subTest(action=hit[1]):
                approval_arming.forget()
                self.state = {}
                x, y = middle(hit[0])
                self.move((x, y))   # The pointer rests where the card is about to appear.
                self.move((x, y))
                self.draw()
                self.move((x + 1, y), real=True)   # Within its shield: no aim yet.
                self.later(SHIELD_SECONDS + 0.1)
                self.assertFalse(self.lands(hit), "jitter during the shield does not aim")
                self.move((x + 1, y), real=False)   # A refresh: no motion.
                self.assertFalse(self.lands(hit), "nor does a pointer that did not move")
                self.move((x + 2, y))
                self.assertTrue(self.lands(hit), "a real move inside it after its shield aims")

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
                self.focus_lost()
                self.later(10)
                self.activated()
                self.pressed(kind="RET")
                self.assertFalse(approval_arming.view(self.state, WINDOW).key_armed(key), "the first key back")


class BoundedEditCardTests(ChatSurface, ArmingCase):
    hits = BOUNDED


class PaidGenerationCardTests(ChatSurface, ArmingCase):
    hits = PAID


class ScriptCardTests(ChatSurface, ArmingCase):
    hits = SCRIPT


class OneListTests(unittest.TestCase):
    def test_every_chat_grant_is_shielded_in_c_and_aimed_by_the_gui_suites(self):
        """The break-it review's S2: Allow for this session was armed in Python but not shielded in C, so C kept the
        0.3 s rule for it. Every chat grant (approval_arming.GRANTS) is in grant_guard.SHIELDED, which C receives,
        and in qa_input.AIMED, which the GUI suites aim."""
        source = (ADDON / "grant_guard.py").read_text(encoding="utf-8")
        shielded = ast.literal_eval(next(node.value.args[0] for node in ast.parse(source).body
                                         if isinstance(node, ast.Assign) and node.targets[0].id == "SHIELDED"))
        self.assertLessEqual(approval_arming.GRANTS, shielded)
        import qa_input
        self.assertLessEqual(approval_arming.GRANTS, qa_input.AIMED | {"approve"})
        self.assertLessEqual(qa_input.AIMED, shielded)


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

    def test_no_handler_keeps_its_own_record_of_focus(self):
        """SHOULD-2: the window manager notes every event to C once, before any handler; every handler's arming
        reads C's record (``approval_arming.window_state``), so a second handler of the same press agrees."""
        for name in ("chat_view.py", "card_host.py", "card_host_input.py", "approval_arming.py"):
            with self.subTest(module=name):
                self.assertNotIn("window_input", (ADDON / name).read_text(encoding="utf-8"))
        self.assertIn("BPY_angelo_guard_note_activate(win)", (ROOT / "source/blender/windowmanager/intern/"
                                                              "wm_window.cc").read_text(encoding="utf-8"))

    def test_moves_presses_and_leaving_go_through_the_arming(self):
        self.assertIn("approval_arming.view(state, window.as_pointer()).moved((x, y))", self.modal)
        self.assertNotIn("refresh_move(event))", self.modal.split("moved((x, y)")[1].split("\n")[0],
                         "a simulated move has no motion either (input_policy.refresh_move): the view tracks its own")
        self.assertIn("approval_arming.refused(window, region, rect, {'RUNNING_MODAL'})", self.modal)
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
        """Each event as Blender delivers it: the window manager's note to C first, then the card host's handler."""
        window = self.case.window.as_pointer()
        results = []
        for one in events:
            if one.type == "WINDOW_ACTIVATE":   # Not an event Blender delivers: the OS's activation, noted in C.
                twin.note_activate(window, now=self.clock.now)
                one = self.event('MOUSEMOVE', x=one.mouse_x, y=one.mouse_y)
                one.mouse_prev_x, one.mouse_prev_y = one.mouse_x, one.mouse_y   # Its synthetic move: no motion.
            twin.note_input(window, one.type, one.value, one.mouse_x, one.mouse_y, now=self.clock.now)
            results.extend(self.case.send(one))
        return results

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
        x, y = self.button("first")
        self.send(self.event('WINDOW_ACTIVATE', x=x, y=y))   # The click on it brings the window forward.
        self.click("first")
        self.assertEqual(self.card.acts, [], "the click that brought the window forward")
        self.later(SHIELD_SECONDS)
        self.aim("first")
        self.click("first")
        self.assertEqual(self.card.acts, ["first"])

    def test_one_press_seen_by_the_card_host_and_the_chat_is_the_window_forward_click_for_both(self):
        """SHOULD-2 (NIT-2: through the real card host handler, in Blender's order): the window manager notes the
        activation, its synthetic move and the press to C once; the card host's handler and the chat's then both
        read that one record, so the second handler of the press no longer loses the window-forward click."""
        window = self.case.window.as_pointer()
        chat, hit = {}, ((32, 10, 90, 28), "approve", "edit-1")
        approval_arming.view(chat, window).drawn(approval_arming.granting([hit]))
        self.later(SHIELD_SECONDS)
        self.send(self.event('WINDOW_DEACTIVATE', x=300, y=200))
        self.later(10)
        x, y = self.button("first")
        self.send(self.event('WINDOW_ACTIVATE', x=x, y=y))
        self.click("first")   # Noted in C, then the card host's modal handler...
        self.assertEqual(self.card.acts, [], "the card host: the click that brought the window forward")
        self.assertFalse(approval_arming.press_lands(chat, hit), "...then the chat's handler of the same press")
        self.assertTrue(approval_arming.view(chat).controls[("approve", "edit-1")][2], "it aimed the chat's control")
        self.later(SHIELD_SECONDS)
        self.click("first")
        self.assertEqual(self.card.acts, ["first"], "the next press grants on the card")

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
