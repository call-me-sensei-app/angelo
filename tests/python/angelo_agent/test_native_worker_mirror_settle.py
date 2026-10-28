# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An Approve no answer settled (``worker_mirror_settle``; the worker's ``chat.calls``): past ``ANSWER_SECONDS`` the
lead asks the worker for the chat's pending calls and the call's outcome, and settles the card from that answer:
approvable again when the worker still waits for the person (nothing ran), gone when the worker settled it, asked again
while it runs or no answer comes, and left to the crash path when the worker is gone. Never an assumed outcome. The
mirror is real (``test_native_worker_mirror.Fixture``); the clock and the question's thread are seams. No Blender.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_worker_mirror import Fixture  # noqa: E402
from angelo_agent import worker_chat_events, worker_mirror_events, worker_mirror_settle as settle  # noqa: E402
from angelo_agent.worker_mirror_ops import ANSWER_SECONDS  # noqa: E402
from unittest import mock  # noqa: E402

DIGEST = "a" * 64


def waiting(executing=False, needs_person=True):
    """The worker's ``chat.calls`` entry for c1."""
    return {"id": "c1", "name": "angelo_execute_python", "digest": DIGEST if needs_person else None,
            "needs_person": needs_person, "expires": 60,
            "summary": {"arguments": {"code": "print(1)"}, "approval": not executing, "executing": executing}}


class SettleFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.clock = [100.0]
        settle.bind(spawn=lambda run: run(), now=lambda: self.clock[0])
        self.addCleanup(settle.reset)
        self.calls_answer = {"ok": True, "calls": [], "outcome": None}
        self.ops.answer = lambda op: self.calls_answer if op == "chat.calls" else {"ok": True}
        patcher = mock.patch.object(settle, "_worker_alive", lambda mirror: self.alive)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.alive = True

    def approving(self):
        """A drawn card whose Approve went out and was never answered (its card shows Approving)."""
        self.pending(waiting())
        self.mirror.pump_events()
        call = self.mirror.pending["c1"]
        self.mirror.approval_sent("c1")
        self.assertTrue(call.approved)
        return call

    def later(self, seconds=ANSWER_SECONDS):
        self.clock[0] += seconds
        self.mirror.pump_events()   # Asks (inline here).
        self.mirror.pump_events()   # Applies the answer.

    def asked(self):
        return self.ops.ops().count("chat.calls")


class SettleTests(SettleFixture):
    def test_nothing_is_asked_before_the_bound(self):
        self.approving()
        self.later(settle.ASK_SECONDS - 1)
        self.assertEqual(self.asked(), 0)
        self.later(1)
        self.assertEqual(self.asked(), 1)   # Within seconds, never ANSWER_SECONDS (R3 H2).

    def test_an_approve_the_worker_never_got_is_approvable_again_and_says_nothing_ran(self):
        self.approving()
        self.calls_answer = {"ok": True, "calls": [waiting()], "outcome": None}
        self.later()
        self.assertEqual(self.asked(), 1)
        call = self.mirror.pending["c1"]
        self.assertEqual((call.approved, call.approval), (False, True))   # Approve again: its card is back.
        self.assertEqual(self.mirror.state["messages"][-1]["role"], "error")
        self.later()
        self.assertEqual(self.asked(), 1)   # Settled: never asked again.

    def test_one_the_worker_settled_leaves_as_its_cards_say(self):
        self.approving()
        self.calls_answer = {"ok": True, "calls": [], "outcome": {"phase": "completed", "error_code": None}}
        self.later()
        self.assertNotIn("c1", self.mirror.pending)
        self.assertFalse(any(m.get("role") == "error" for m in self.mirror.state["messages"]))   # Nothing assumed.

    def test_one_still_running_there_stays_approving_and_is_asked_again(self):
        call = self.approving()
        self.calls_answer = {"ok": True, "calls": [waiting(executing=True)], "outcome": None}
        self.later()
        self.assertTrue(call.approved)
        self.later()
        self.assertEqual(self.asked(), 2)

    def test_no_answer_while_the_worker_lives_stays_approving_and_asks_again(self):
        call = self.approving()
        self.calls_answer = {"error": "This chat's worker did not answer.", "timed_out": True}
        self.later()
        self.assertTrue(call.approved)
        self.later()
        self.assertEqual(self.asked(), 2)

    def test_a_gone_worker_is_left_to_the_crash_path_which_settles_the_card(self):
        call = self.approving()
        self.alive = False
        self.calls_answer = {"error": "gone", "gone": True}
        self.later()
        self.assertTrue(call.approved)   # Not settled by assumption...
        self.later()
        self.assertEqual(self.asked(), 1)   # ...nor asked again,
        self.mirror.receive(worker_mirror_events.RECOVERED, {"state": None, "finished": None})
        self.mirror.pump_events()
        self.assertEqual(self.mirror.pending, {})   # and its end settles it as every crash does.

    def test_one_its_worker_settled_meanwhile_is_never_asked(self):
        self.approving()
        self.pending()   # The worker's own cards: c1 is no longer pending.
        self.mirror.pump_events()
        self.later()
        self.assertEqual(self.asked(), 0)

    def test_a_mirror_without_a_worker_asks_nothing_it_can_send(self):
        call = self.approving()
        self.mirror.send_op, self.alive = None, False
        self.later()
        self.assertTrue(call.approved)
        self.assertEqual(self.asked(), 0)


class WorkerCallsTests(unittest.TestCase):
    def test_the_worker_answers_its_pending_calls_and_the_recorded_outcome_and_resends_its_cards(self):
        resent = []
        host = SimpleNamespace(resend_cards=resent.append, _versions={},
                               consent=SimpleNamespace(pending_digests=lambda chat: {}))
        chat = SimpleNamespace(pending={}, state={"results": {"s/c1": {"phase": "completed",
                                                                       "result": {"error_code": None}}}})
        answer = worker_chat_events.calls_answer(host, chat, "c1")
        version = answer.pop("pending_seq")   # The version of its cards (audit2 #5), and the open it answers in.
        self.assertEqual(answer, {"ok": True, "calls": [], "outcome": {"phase": "completed", "error_code": None},
                                  "open_id": None})
        self.assertEqual(worker_chat_events.calls_answer(host, chat, "c1")["pending_seq"], version)   # Unchanged.
        self.assertEqual(resent, [chat, chat])
        self.assertIsNone(worker_chat_events.calls_answer(host, chat, "c9")["outcome"])

    def test_calls_is_a_worker_op(self):
        from angelo_agent import worker_chat_host
        self.assertIn("calls", worker_chat_host.OPS)
        self.assertTrue(callable(getattr(worker_chat_host.WorkerChatHost, "_calls", None)))


if __name__ == "__main__":
    unittest.main()
