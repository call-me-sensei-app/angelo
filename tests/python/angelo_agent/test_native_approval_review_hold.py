# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A card the person is reviewing does not expire under them (``approval_wait.review_hold``).

An approval waits ``tool_deadlines.tool_seconds``: ten minutes for a script, three for another step. A showcase
reviewer was still reading a script when its card expired (2026-11-14, F4): the step read "Nobody answered in
time" and the agent had to ask again. While a waiting card is being reviewed its lifetime does not run, by the
arithmetic the scene and editing-tool holds already use (``call.expires`` moves later by the time that passed), so
the card's "Waits for your answer until" line and a provider's wait (``tool_deadlines.await_native``) follow it.

Reviewed means: the chat is shown, and its waiting card has its code or details open, or has any part in view
while Angelo's window has focus (the person's last input went to Angelo; a timer or a motionless pointer refresh
is not input). Nothing about the approval changes: the same call, the same card, the same click.

Pinned, with a counted clock and the card placed as a draw places it: each way a card counts as reviewed keeps it
from expiring across far more than its lifetime; a card nobody looks at (the chat hidden, another chat shown, the
window behind another app with the card closed, the card scrolled away and left) expires exactly as before; only
the card being reviewed is held, never another pending call; what is not the person's input never brings the window
to the front; a chat already held for its scene or the person's
tool is not credited twice; and the real ``Controller`` pump expires an unreviewed script card and keeps a
reviewed one.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from agent_runtime_fixtures import AgentRuntimeFixture  # noqa: E402
from angelo_agent import approval_wait, tool_deadlines  # noqa: E402

LIFETIME = tool_deadlines.PYTHON_REVIEW_SECONDS
VISIBLE = 600   # The transcript's height in a draw.
CARD = 2400     # A long script's card: four screens of code.


def waiting(identity="call-1", name="angelo_execute_python", expires=LIFETIME):
    """A pending call whose card waits for the person (``worker_consent.awaiting_person``)."""
    return SimpleNamespace(id=identity, name=name, arguments={"description": "A step", "code": "x = 1"},
                           approval=True, expires=float(expires), epoch=1, generation=1, delivery_owner=None)


def chat(*calls, identity="chat-1"):
    return SimpleNamespace(state={"id": identity}, pending={call.id: call for call in calls}, status="",
                           _epoch=1, _request_generation=1, _worker_request=None)


def draw(view, controller, scroll=0, expanded=False, card=CARD, below=()):
    """One draw of ``controller``'s transcript in ``view``: older rows, its waiting card, then ``below`` (heights of
    rows under the card). ``scroll`` is measured up from the newest row, as ``chat_view`` measures it."""
    call = approval_wait.awaiting(controller)
    blocks = [("user", {}, (), 300, "m-1", None)]
    if call is not None:
        blocks.append(("approval", {}, (), card, call.id, None))
        view.setdefault("expanded", set()).discard(("approval", call.id))
        if expanded:
            view["expanded"].add(("approval", call.id))
    blocks += [("assistant", {}, (), height, "m-%d" % index, None) for index, height in enumerate(below, 2)]
    view["message_viewport"] = (0, 0, 480, VISIBLE)
    approval_wait.place(view, controller, blocks, scroll, VISIBLE)


class Clock:
    """A counted clock: the pump's ticks move it, nothing sleeps."""

    def __init__(self):
        self.now = 1000.0

    def pump(self, controller, seconds=0.25, held=False):
        self.now += seconds
        return approval_wait.review_hold(controller, held, now=self.now)

    def run(self, controller, seconds, step=0.25, held=False):
        answers = set()
        for _ in range(int(seconds / step)):
            answers.add(self.pump(controller, step, held))
        return answers

    def expired(self, call):
        return self.now >= call.expires


class Reviewed(unittest.TestCase):
    def setUp(self):
        approval_wait.forget_reviews()
        self.addCleanup(approval_wait.forget_reviews)
        shown = patch.object(approval_wait, "_chat_shown", lambda: True)
        shown.start()
        self.addCleanup(shown.stop)
        self.clock, self.view = Clock(), {}
        self.call = waiting(expires=self.clock.now + LIFETIME)
        self.chat = chat(self.call)
        approval_wait.note_event("MOUSEMOVE", "NOTHING", now=self.clock.now)   # Angelo's window has the person's input.

    def test_a_card_in_view_in_the_focused_window_does_not_expire(self):
        draw(self.view, self.chat)
        self.assertEqual(self.clock.run(self.chat, LIFETIME * 3), {True})
        self.assertFalse(self.clock.expired(self.call))
        self.assertAlmostEqual(self.call.expires - self.clock.now, LIFETIME, delta=1.0)   # Its whole lifetime is left.

    def test_a_card_with_its_code_open_does_not_expire_when_the_window_loses_focus(self):
        draw(self.view, self.chat, expanded=True)
        approval_wait.note_event("WINDOW_DEACTIVATE", "NOTHING", now=self.clock.now)   # Looking something up elsewhere.
        self.assertEqual(self.clock.run(self.chat, LIFETIME * 3), {True})
        self.assertFalse(self.clock.expired(self.call))

    def test_a_card_scrolled_out_of_view_with_its_code_closed_is_not_held(self):
        draw(self.view, self.chat, scroll=0, below=(VISIBLE * 3,))   # Rows below fill the view: the card is above it.
        approval_wait.note_event("WHEELUPMOUSE", "PRESS", now=self.clock.now)
        self.assertEqual(self.clock.run(self.chat, LIFETIME + 5), {False})
        self.assertTrue(self.clock.expired(self.call), "a card left out of view runs out as before")
        draw(self.view, self.chat, scroll=VISIBLE * 3)   # Scrolled back to it: in view again.
        self.assertTrue(self.clock.pump(self.chat))

    def test_a_timer_or_a_motionless_pointer_refresh_is_not_the_persons_input(self):
        draw(self.view, self.chat)
        approval_wait.note_event("WINDOW_DEACTIVATE", "NOTHING", now=self.clock.now)
        for event, value, moved in (("TIMER", "NOTHING", True), ("TIMER_REGION", "NOTHING", True),
                                    ("NONE", "NOTHING", True), ("MOUSEMOVE", "NOTHING", False)):
            approval_wait.note_event(event, value, moved, now=self.clock.now)
            with self.subTest(event=event, moved=moved):
                self.assertFalse(self.clock.pump(self.chat), "Angelo's window was brought to the front by no input")
        approval_wait.note_event("MOUSEMOVE", "NOTHING", True, now=self.clock.now)
        self.assertTrue(self.clock.pump(self.chat))

    def test_what_nobody_looks_at_expires_as_before(self):
        cases = {
            "never drawn": lambda: None,
            "the window is behind another app, the card closed": lambda: (
                draw(self.view, self.chat),
                approval_wait.note_event("WINDOW_DEACTIVATE", "NOTHING", now=self.clock.now)),
            "another chat is shown in its place": lambda: (
                draw(self.view, self.chat), draw(self.view, chat(identity="chat-2"))),
        }
        for name, arrange in cases.items():
            with self.subTest(case=name):
                self.setUp()
                arrange()
                before = self.call.expires
                self.assertEqual(self.clock.run(self.chat, LIFETIME + 5), {False})
                self.assertEqual(self.call.expires, before)
                self.assertTrue(self.clock.expired(self.call))

    def test_a_hidden_chat_is_not_reviewed_whatever_was_drawn_before(self):
        draw(self.view, self.chat, expanded=True)
        with patch.object(approval_wait, "_chat_shown", lambda: False):
            self.assertEqual(self.clock.run(self.chat, LIFETIME + 5), {False})
        self.assertTrue(self.clock.expired(self.call))

    def test_the_window_coming_back_counts_from_the_persons_next_input(self):
        draw(self.view, self.chat)
        approval_wait.note_event("WINDOW_DEACTIVATE", "NOTHING", now=self.clock.now)
        self.assertEqual(self.clock.run(self.chat, 60), {False})
        left = self.call.expires - self.clock.now
        approval_wait.note_event("MOUSEMOVE", "NOTHING", now=self.clock.now)
        self.assertEqual(self.clock.run(self.chat, LIFETIME * 2), {True})
        self.assertAlmostEqual(self.call.expires - self.clock.now, left, delta=1.0)   # What was left then is left now.

    def test_only_the_card_being_reviewed_is_held(self):
        other = waiting("call-2", "angelo_create_cube", expires=self.clock.now + tool_deadlines.APPROVAL_SECONDS)
        other.approval, other.automatic = False, True   # Queued work with no card.
        self.chat.pending[other.id] = other
        draw(self.view, self.chat)
        before = other.expires
        self.clock.run(self.chat, 120)
        self.assertEqual(other.expires, before)
        self.assertGreater(self.call.expires, self.clock.now + LIFETIME - 2)

    def test_a_chat_already_held_is_not_credited_twice(self):
        draw(self.view, self.chat)
        before = self.call.expires
        self.assertEqual(self.clock.run(self.chat, 120, held=True), {False})   # The scene or tool hold moved it.
        self.assertEqual(self.call.expires, before)
        self.clock.pump(self.chat)   # A review's first pump credits nothing: there is no earlier one to count from.
        self.assertEqual(self.call.expires, before)

    def test_an_answered_card_is_forgotten_and_the_next_card_starts_unreviewed(self):
        draw(self.view, self.chat, expanded=True)
        self.clock.run(self.chat, 30)
        self.chat.pending.clear()
        draw(self.view, self.chat)   # The card is gone from the transcript.
        following = waiting("call-3", expires=self.clock.now + LIFETIME)
        self.chat.pending[following.id] = following
        approval_wait.note_event("WINDOW_DEACTIVATE", "NOTHING", now=self.clock.now)
        self.assertEqual(self.clock.run(self.chat, LIFETIME + 5), {False},
                         "the earlier card's open code is not this one's")
        self.assertTrue(self.clock.expired(following))

    def test_the_until_line_follows_the_held_deadline(self):
        draw(self.view, self.chat)
        wall = 1_800_000_000.0
        first = approval_wait.until_text(self.call, wall=wall, now=self.clock.now)
        self.clock.run(self.chat, 300)
        later = approval_wait.until_text(self.call, wall=wall + 300, now=self.clock.now)
        self.assertTrue(first and later)
        self.assertNotEqual(first, later, "five minutes of review later the card says a later time")


class ThroughTheController(AgentRuntimeFixture, unittest.TestCase):
    """The real pump: an unreviewed script card expires at its deadline, a reviewed one does not."""

    def setUp(self):
        super().setUp()
        approval_wait.forget_reviews()
        self.addCleanup(approval_wait.forget_reviews)
        self.answers = []
        self.c.request_tool("script-1", "angelo_execute_python", {"description": "A step", "code": "x = 1"},
                            self.answers.append)
        self.call = self.c.pending[self.c.state["id"] + "/script-1"]
        self.pump()
        self.assertIs(approval_wait.awaiting(self.c), self.call, "the script's card waits for the person")

    def late(self, seconds):
        """Move the call's deadline and the review's last pump ``seconds`` into the past: that much time went by."""
        self.call.expires -= seconds
        if getattr(self.c, "_review_at", None) is not None:
            self.c._review_at -= seconds

    def test_an_unreviewed_card_expires_at_its_deadline(self):
        self.late(LIFETIME + 1)
        self.pump()
        self.assertEqual(len(self.answers), 1)
        self.assertEqual(self.answers[0].get("error_code"), "EXPIRED")

    def test_a_reviewed_card_is_still_there_after_its_lifetime(self):
        view = {}
        with patch.object(approval_wait, "_chat_shown", lambda: True):
            approval_wait.note_event("MOUSEMOVE", "NOTHING")
            draw(view, self.c, expanded=True)
            self.pump()   # The review's first pump.
            deadline = self.call.expires
            for _ in range(4):
                self.late(LIFETIME / 2)
                self.pump()
        self.assertEqual(self.answers, [])
        self.assertIs(self.c.pending.get(self.call.id), self.call)
        # Two lifetimes went by under review and the deadline is no earlier than it was: each pump gave back at
        # least what had passed. Counted on the card's own deadline, never against this machine's clock.
        self.assertGreaterEqual(self.call.expires, deadline)


if __name__ == "__main__":
    unittest.main()
