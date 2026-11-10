# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's Approve on a card whose binding changed while it was shown asks again; it never runs unseen.

Selecting the object to look at it moves a script card's binding (the scene revision); the click used to answer
STALE_REVISION and end the request on Claude. Now only the person's own click (``review_again.mark_click``) puts the
same card back against the scene as it is, and only a click on that card runs it. Every other approval keeps the
refusal. Time and the scene are fakes; nothing sleeps.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import review_again  # noqa: E402
from angelo_agent.canvas_controls import INERT  # noqa: E402
from angelo_agent.claude_outcomes import correctable_failure  # noqa: E402
from test_native_tool_deadlines import RevocationFixture  # noqa: E402

STALE = {"error": "What this call is bound to changed before it ran (the frame). Nothing of it ran.",
         "error_code": "STALE_REVISION", "scene_changed": False, "partial_changes_possible": False}


class ReviewAgainTests(RevocationFixture):
    def click(self, call, pressed=None):
        """What the chat canvas does for the person's Approve (chat_view.dispatch_action), then the operator's run."""
        review_again.drew(self.controller, call.id)
        if not review_again.mark_click(self.controller, call.id, pressed):
            return False
        self.controller.execute_tool(call, self.executor, self.controller.document)
        return True

    def test_a_person_click_on_a_changed_card_asks_again_and_runs_nothing(self):
        replies, call = self.awaiting_approval()
        self.executor.precondition.return_value = "changed"
        self.assertTrue(self.click(call))
        self.executor.execute.assert_not_called()
        self.assertEqual(replies, [])
        self.assertIn(call.id, self.controller.pending)
        self.assertTrue(call.approval)
        self.assertFalse(call.approved)
        self.assertEqual((call.review_generation, call.precondition), (1, "changed"))
        self.assertIn("so it is asking again", review_again.note(call))

    def test_the_next_click_on_the_card_as_it_is_runs_it_and_says_so(self):
        replies, call = self.awaiting_approval()
        self.executor.precondition.return_value = "changed"
        self.click(call)
        review_again._shields.pop(call.id, None)   # The shield lapsed (its own test below).
        self.assertTrue(self.click(call))
        self.executor.execute.assert_called_once()
        self.assertEqual(replies[0]["approval_renewed"]["note"], review_again.FACT)

    def test_every_look_that_changes_it_again_asks_again(self):
        replies, call = self.awaiting_approval()
        for index, scene in enumerate(("changed once", "changed twice", "changed thrice"), 1):
            self.executor.precondition.return_value = scene
            review_again._shields.pop(call.id, None)
            self.click(call)
            self.assertEqual(call.review_generation, index)
        self.executor.execute.assert_not_called()

    def test_an_approval_that_is_not_the_persons_click_is_refused_as_before(self):
        replies, call = self.awaiting_approval()
        self.executor.precondition.return_value = "changed"
        self.controller.execute_tool(call, self.executor, self.controller.document)   # Auto, apps, workers, harnesses.
        self.assertEqual(replies[0]["error_code"], "STALE_REVISION")
        self.assertIs(replies[0]["scene_changed"], False)
        self.executor.execute.assert_not_called()

    def test_an_automatic_or_started_call_is_never_renewed(self):
        replies, call = self.awaiting_approval()
        for flag in ("automatic", "executing", "mutation_started"):
            with self.subTest(flag=flag):
                setattr(call, flag, True)
                self.assertFalse(review_again.renew(self.controller, call, self.executor, "changed", None, 0))
                setattr(call, flag, False)
        self.assertFalse(review_again.renew(self.controller, call, self.executor, None, None, 0))   # Unbindable.
        self.assertEqual(review_again.generation(call), 0)

    def test_a_click_mark_is_read_once(self):
        replies, call = self.awaiting_approval()
        review_again.mark_click(self.controller, call.id)
        self.controller.execute_tool(call, self.executor, self.controller.document)   # Runs (unchanged): mark used.
        self.assertNotIn("person_click", vars(call))

    def test_a_press_made_on_an_earlier_card_approves_nothing(self):
        replies, call = self.awaiting_approval()
        review_again.drew(self.controller, call.id)
        pressed = (call.id, review_again.drawn(self.controller, call.id))   # The press, before the renewal.
        self.executor.precondition.return_value = "changed"
        self.click(call)
        review_again._shields.pop(call.id, None)
        self.assertFalse(review_again.mark_click(self.controller, call.id, pressed))
        self.assertNotIn("person_click", vars(call))

    def test_the_renewed_approve_takes_no_click_for_a_moment_after_it_is_drawn(self):
        replies, call = self.awaiting_approval()
        self.executor.precondition.return_value = "changed"
        self.click(call)
        self.assertFalse(review_again.mark_click(self.controller, call.id))   # Renewed, not drawn yet.
        hits = review_again.shield_hits([((10, 10, 80, 30), call.id)], now=100.0)
        self.assertEqual(hits[0][1], INERT)
        self.assertEqual(hits[0][0], review_again.WHOLE)   # Its note moved its buttons: every spot is shielded.
        self.assertTrue(review_again.shielded(call.id, now=100.5))
        self.assertFalse(review_again.shielded(call.id, now=101.5))

    def test_the_rows_say_it_asks_again_and_never_keep_earlier_words(self):
        replies, call = self.awaiting_approval()
        self.executor.precondition.return_value = "changed"
        self.click(call)
        self.controller.pump_events()
        row = next(m for m in self.controller.state["messages"] if m.get("call_id") == call.id)
        self.assertEqual(row["status"], "approval")
        self.assertEqual(row.get("result"), {})   # Never None: rows are read as dicts (has_preview, tool_title).


    def test_an_approval_queued_before_the_renewal_never_runs_the_renewed_card(self):
        """Break-it H2: two clicks queue two applies (a lane chat); the first renews, the second must not run it."""
        replies, call = self.awaiting_approval()
        review_again.drew(self.controller, call.id)
        self.assertTrue(review_again.mark_click(self.controller, call.id))   # Click A.
        self.executor.precondition.return_value = "changed"
        self.controller.execute_tool(call, self.executor, self.controller.document)   # Apply A: renewed.
        self.assertEqual(call.review_generation, 1)
        call.person_click = 0   # Click B, made on the card as it was (queued before the renewal).
        self.controller.execute_tool(call, self.executor, self.controller.document)   # Apply B.
        self.executor.execute.assert_not_called()
        self.assertIn(call.id, self.controller.pending)
        self.controller.execute_tool(call, self.executor, self.controller.document)   # No mark at all.
        self.executor.execute.assert_not_called()

    def test_always_allow_takes_no_click_while_a_renewed_card_is_shielded(self):
        """Break-it H3: the renewed card's note moves Always allow under a double-click's second press."""
        from angelo_agent import chat_cards
        replies, call = self.awaiting_approval()
        self.executor.precondition.return_value = "changed"
        self.click(call)
        for action in ("script_always_allow", "script_file_allow"):
            self.assertIsNone(chat_cards.dispatch(None, self.controller, {}, action, call.id))
        self.assertFalse(self.controller.always_allow_scripts)


class ClaudeCorrectionTests(unittest.TestCase):
    def test_a_stale_script_that_ran_nothing_is_a_correction(self):
        self.assertTrue(correctable_failure([{"tool": "angelo_execute_python"}], [dict(STALE)]))

    def test_other_script_refusals_still_end_the_request(self):
        for result in (dict(STALE, error_code="VALIDATION"), dict(STALE, script_error="Traceback"),
                       dict(STALE, partial_changes_possible=True), dict(STALE, expired=True)):
            with self.subTest(result=result):
                self.assertIsNone(correctable_failure([{"tool": "angelo_execute_python"}], [result]))


class NoteTests(unittest.TestCase):
    def test_the_note_names_what_changed_in_the_persons_words(self):
        from types import SimpleNamespace
        call = SimpleNamespace(review_generation=1, renewed_parts=["frame", "objects"])
        self.assertEqual(review_again.note(call), "What this step uses changed while it waited (the frame, the "
                         "objects), so it is asking again. Check it, then approve.")
        other = SimpleNamespace(review_generation=2, renewed_parts=["connection"])
        self.assertIn("(what it uses)", review_again.note(other))
        self.assertEqual(review_again.note(SimpleNamespace()), "")


if __name__ == "__main__":
    unittest.main()
