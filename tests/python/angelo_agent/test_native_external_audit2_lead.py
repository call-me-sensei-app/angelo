# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The second external audit of A′ (e837c972c40), the lead's findings:

* #1: a widening whose ack failed or timed out was dropped, though the worker may hold it, so withdrawing it later
  was not a revocation: no kill, no needs-approval. Uncertain leases now stay accounted for until a newer
  acknowledged lease supersedes them (``worker_consent_held``).
* #5: settling one lost Approve replaced the mirror's whole pending set with the poll's snapshot; a delayed answer
  erased a newer card. A settle now changes only the queried call, and only from an answer at least as new as the
  cards shown (the worker's pending version).

Each class is one finding's reproduction and failed while the finding stood.
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import worker_consent_lead as lead, worker_mirror_settle as settle  # noqa: E402
from angelo_agent.worker_mirror_ops import ANSWER_SECONDS  # noqa: E402
from test_native_worker_mirror_settle import DIGEST, SettleFixture, waiting  # noqa: E402

TRUSTED, PLAIN = {"auto": True, "scripts": "nonce-1"}, {"auto": True}


class UncertainGrantsStayAccountedFor(unittest.TestCase):
    def setUp(self):
        switch = patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"})
        switch.start()
        self.addCleanup(switch.stop)
        lead.reset()
        self.addCleanup(lead.reset)
        self.now, self.held, self.killed, self.current = [1000.0], [], [], dict(PLAIN)
        self.answer = lambda request: {"ok": True, "epoch": request.get("epoch")}
        lead.bind(send=lambda session, request, timeout: self.answer(request), kill=self.killed.append,
                  clock=lambda: self.now[0], connection=lambda c: True, spawn=self.held.append)
        granting = patch.object(lead, "grants", lambda controller: dict(self.current))
        granting.start()
        self.addCleanup(granting.stop)
        lead.register(NS(state={"id": "chat-1"}, emit=Mock(), busy=True))

    def change(self, grants):
        self.current = dict(grants)
        return lead.sync()

    def answered(self, answer):
        """The send just made runs now, answered with ``answer(request)``; then the lead settles."""
        self.answer = answer
        self.held.pop()()
        lead.settle()

    def past_the_deadline(self):
        self.held.clear()   # Unanswered: the worker does not respond.
        self.now[0] += lead.ACK_SECONDS + 0.5
        lead.settle()

    def raise_timeout(self, request):
        from angelo_agent.worker_process import WorkerUnavailable
        raise WorkerUnavailable("no answer within 2 s", timed_out=True)

    def test_the_audit_poc_withdrawing_a_trust_whose_ack_failed_stops_the_chat(self):
        self.change(TRUSTED)
        self.answered(self.raise_timeout)   # The worker applied it; its ack was lost.
        self.change(PLAIN)                  # Trust scripts off while the worker is unresponsive.
        self.past_the_deadline()
        self.assertEqual(self.killed, ["chat-1"])
        self.assertTrue(lead.needs_approval("chat-1"))

    def test_a_trust_whose_ack_came_late_or_never_is_maybe_held(self):
        for name in ("late ack", "never answered"):
            with self.subTest(case=name):
                self.setUp()
                self.change(TRUSTED)
                if name == "late ack":
                    self.now[0] += lead.ACK_SECONDS + 0.5
                    self.answered(lambda request: {"ok": True, "epoch": request["epoch"]})
                else:
                    self.past_the_deadline()
                records = self.change(PLAIN)
                self.assertTrue(records[-1].revoking)

    def test_a_trust_that_never_left_or_was_refused_in_time_is_not_held(self):
        from angelo_agent.worker_process import RequestRefused
        refusals = {"never sent": lambda request: (_ for _ in ()).throw(RequestRefused("too large", "LIMIT")),
                    "refused": lambda request: {"ok": False, "error_code": "STALE_REVISION", "epoch": 1}}
        for name, answer in refusals.items():
            with self.subTest(case=name):
                self.setUp()
                self.change(TRUSTED)
                self.answered(answer)
                self.assertFalse(lead._uncertain.get("chat-1"))
                self.assertFalse(self.change(PLAIN)[-1].revoking)

    def test_a_newer_acknowledged_lease_supersedes_an_uncertain_one(self):
        self.change(TRUSTED)
        self.answered(self.raise_timeout)
        self.assertEqual([grants for _epoch, grants in lead._uncertain["chat-1"]], [TRUSTED])
        resent = lead.sync()   # The widening goes again, and this time it is acknowledged.
        self.assertEqual(resent[-1].grants, TRUSTED)
        self.answered(lambda request: {"ok": True, "epoch": request["epoch"]})
        self.assertNotIn("chat-1", lead._uncertain)
        records = self.change(PLAIN)
        self.assertTrue(records[-1].revoking)   # Narrows what it acknowledged.
        self.answered(lambda request: {"ok": True, "epoch": request["epoch"]})
        self.now[0] += lead.ACK_SECONDS + 0.5
        lead.settle()
        self.assertEqual(self.killed, [])   # Acknowledged in time: nothing is stopped.

    def test_a_new_open_holds_exactly_its_lease(self):
        self.change(TRUSTED)
        self.answered(self.raise_timeout)
        lead.register(NS(state={"id": "chat-1"}, emit=Mock(), busy=True))
        self.assertNotIn("chat-1", lead._uncertain)
        lead.forget("chat-1")
        self.assertNotIn("chat-1", lead._uncertain)


class DelayedSettleAnswersNeverEraseNewerCards(SettleFixture):
    def card(self, call_id):
        return dict(waiting(), id=call_id)

    def versioned(self, seq, *calls):
        self.frame("pending", {"calls": list(calls), "seq": seq})
        self.mirror.pump_events()

    def delayed_question(self):
        """An Approve of c1 drawn at version 1; the settle's question is asked, its answer held back."""
        self.lane()
        self.versioned(1, self.card("c1"))
        self.mirror.approval_sent("c1")
        held = []
        settle.bind(spawn=held.append, now=lambda: self.clock[0])
        self.clock[0] += ANSWER_SECONDS
        self.mirror.pump_events()   # Asks: the question's thread is held.
        self.assertEqual(len(held), 1)
        return held

    def test_the_audit_poc_a_delayed_answer_keeps_a_newer_card(self):
        for calls in ([waiting()], []):   # The delayed answer: c1 still waiting there, or settled there.
            with self.subTest(answer=[call["id"] for call in calls]):
                self.setUp()
                held = self.delayed_question()
                self.calls_answer = {"ok": True, "calls": calls, "outcome": None, "pending_seq": 1}   # Captured,
                self.versioned(2, self.card("c1"), self.card("c2"))   # then a newer event shows c2,
                held.pop()()                  # then the delayed answer arrives
                self.mirror.pump_events()     # and is applied after it.
                self.assertIn("c2", self.mirror.pending, "a delayed settle answer erased a newer card")
                self.assertIn("c1", self.mirror.pending)
                self.assertTrue(self.mirror.pending["c1"].approved)   # Unknown yet: asked again, never assumed.

    def test_an_answer_at_least_as_new_settles_only_the_queried_call(self):
        held = self.delayed_question()
        self.versioned(2, self.card("c1"), self.card("c2"))
        self.calls_answer = {"ok": True, "calls": [waiting()], "outcome": None, "pending_seq": 3}
        held.pop()()
        self.mirror.pump_events()
        self.assertEqual(sorted(self.mirror.pending), ["c1", "c2"])   # c2 is not in the answer: it stays.
        call = self.mirror.pending["c1"]
        self.assertEqual((call.approved, call.approval), (False, True))   # Lost: approvable again ...
        self.assertEqual(lead.drawn(self.session, "c1"), ("angelo_execute_python", DIGEST))   # ... with its digest.

    def test_a_settled_call_leaves_alone(self):
        held = self.delayed_question()
        self.versioned(2, self.card("c1"), self.card("c2"))
        self.calls_answer = {"ok": True, "calls": [self.card("c2")], "outcome": None, "pending_seq": 2}
        held.pop()()
        self.mirror.pump_events()
        self.assertEqual(sorted(self.mirror.pending), ["c2"])

    def test_an_older_pending_event_never_replaces_newer_cards(self):
        self.versioned(5, self.card("c1"), self.card("c2"))
        self.versioned(4, self.card("c1"))
        self.assertEqual(sorted(self.mirror.pending), ["c1", "c2"])

    def test_an_unversioned_answer_or_another_opens_is_asked_again(self):
        for stale in ({}, {"pending_seq": 9, "open_id": "an-earlier-open"}):
            with self.subTest(answer=stale):
                self.setUp()
                held = self.delayed_question()
                self.calls_answer = dict({"ok": True, "calls": [], "outcome": None}, **stale)
                held.pop()()
                self.mirror.pump_events()
                self.assertIn("c1", self.mirror.pending)
                self.assertTrue(self.mirror.pending["c1"].approved)

    def test_a_new_open_forgets_the_version(self):
        from angelo_agent import worker_mirror_opens
        self.versioned(7, self.card("c1"))
        worker_mirror_opens.reopen(self.mirror, {"open_id": "b" * 32})
        self.assertIsNone(self.mirror.pending_seq)


if __name__ == "__main__":
    unittest.main()
