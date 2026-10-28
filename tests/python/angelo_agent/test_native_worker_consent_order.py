# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted worker chat's standing consent, lead side (design A′, C6), round 2: every change of the person's standing
consent reaches the chat in its worker in the order it was made (``worker_consent_lead``).

* D9: a worker chat whose connection is its provider's saved key (no connection record, as the window's
  ``connection_available`` allows) inherits its lead's Trust scripts as a lane chat does, also when the lead turns it
  on after the worker started; a widening is sent without a wait.
* Concurrency re-review R2: a change made while the chat's open is unanswered goes out after it.
* Review item B: Approve and Decline wait as asynchronous ops do; no answer is unknown, never "nothing changed".
* The e2e consent flake: deterministic interleavings of asynchronous consent sends (each on its own thread), where an
  older acknowledgement is still the newest one the lead has read while a newer widening has not reached the worker.
  The worker always ends on the newest lease it received, and an unacknowledged revocation always fails closed.
"""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import worker_chat_lifecycle, worker_consent, worker_consent_lead as lead_side  # noqa: E402
from angelo_agent import worker_mirror_ops  # noqa: E402
from angelo_agent.worker_mirror import MirrorRefused  # noqa: E402
from angelo_agent.worker_process import RequestRefused, WorkerUnavailable  # noqa: E402
import test_native_worker_mirror as mirror_tests  # noqa: E402 - its Fixture; its tests run in their own module
from test_native_worker_consent_lead import LeadFixture  # noqa: E402


class Pending(LeadFixture):
    """Every send held as its own thread would be, run one at a time in an order the test chooses."""

    def spawn(self, function):
        self.deferred.append(function)

    def run_send(self, index):
        self.deferred.pop(index)()


class LegacyKeyConnection(LeadFixture):
    def setUp(self):
        super().setUp()
        lead_side.bind(connection=None)   # The real rule, not the fixture's fake.
        self.keys = {}
        self.ui = SimpleNamespace(_memory_keys=self.keys)
        modules = patch.dict(sys.modules, {"angelo_agent.ui": self.ui})
        modules.start()
        self.addCleanup(modules.stop)

    def legacy_pair(self):
        lead, mirror, worker = self.hosted_pair()
        for chat in (lead, mirror):
            chat.state.update(provider="codex_api", connection_id=None)
        return lead, mirror, worker

    def test_a_chat_on_its_providers_saved_key_inherits_trust_turned_on_after_its_worker_started(self):
        lead, mirror, worker = self.legacy_pair()
        self.keys["codex_api"] = "sk-legacy"
        lead_side.changed()   # Its first lease: Auto (it opened before the key was read here).
        self.assertTrue(worker.consent_lease.granted_auto())
        lead.set_trusted_scripts(True)   # Turned on in the lead while the worker chat runs.
        self.assertIsNotNone(worker.consent_lease.token("scripts"))
        lead.set_trusted_scripts(False)
        self.assertIsNone(worker.consent_lease.token("scripts"))
        self.assertEqual(self.killed, [])

    def test_a_chat_with_no_connection_and_no_saved_key_is_granted_nothing(self):
        _lead, mirror, _worker = self.legacy_pair()
        self.assertEqual(lead_side.grants(mirror), {"auto": False})   # The window refuses to run it too.
        mirror.state["provider"] = "codex_subscription"   # Never the computer's default sign-in.
        self.keys["codex_subscription"] = "anything"
        self.assertEqual(lead_side.grants(mirror), {"auto": False})

    def test_a_removed_connection_record_still_grants_nothing(self):
        _lead, mirror, _worker = self.legacy_pair()
        mirror.state["connection_id"] = "gone"
        self.keys["codex_api"] = "sk-legacy"
        with patch("angelo_agent.connection_access.find_connection", lambda identity: None):
            self.assertEqual(lead_side.grants(mirror), {"auto": False})


class WideningWithoutWait(Pending):
    def test_turning_trust_on_returns_before_the_worker_answers_and_applies_on_arrival(self):
        lead, mirror, worker = self.hosted_pair()
        lead.set_trusted_scripts(True)   # Returns with the send still held: no window frame waits on a widening.
        self.assertIsNone(worker.consent_lease.token("scripts"))
        self.run_send(0)
        lead_side.tick()
        self.assertIsNotNone(worker.consent_lease.token("scripts"))
        self.assertEqual(lead_side._acked[mirror.state["id"]][1], lead_side.grants(mirror))


class Interleavings(Pending):
    """The order the e2e flake showed (traced, 33 of 500 runs): W1 acknowledged; N1 applied, its answer not read yet;
    W2 sent but held before the worker's reader; N2 sent to a frozen worker."""

    def setUp(self):
        super().setUp()
        self.lead, self.mirror, self.worker = self.hosted_pair()
        self.session = self.mirror.state["id"]
        self.answers, self.unread = [], []
        lead_side.bind(send=lambda session, request, timeout: self.channel.send(session, request, timeout))

    def deliver(self, index, read=True):
        """The worker applies send ``index`` and answers; with ``read`` False its answer is not read by the lead yet
        (its thread waits until ``read_all``), as a reply still in the socket."""
        function, real = self.deferred.pop(index), self.channel.send
        gate, applied = threading.Event(), threading.Event()

        def held(session, request, timeout):
            self.channel.send = real
            answer = real(session, request, timeout)
            self.answers.append(answer)
            applied.set()
            if not read:
                gate.wait(5)
            return answer
        self.channel.send = held
        if read:
            function()
            return
        thread = threading.Thread(target=function, daemon=True)
        thread.start()
        applied.wait(5)
        self.unread.append((gate, thread))

    def read_all(self):
        for gate, thread in self.unread:
            gate.set()
            thread.join(5)
        self.unread.clear()

    def mode(self, approval):
        self.mirror.state["options"]["approval"] = approval
        lead_side.changed()

    def test_a_stale_acknowledgement_never_hides_an_unacknowledged_revocation(self):
        self.mode("ASK")
        self.deliver(0)   # N0, acknowledged and read.
        lead_side.settle()
        self.mode("AUTO_BOUNDED")
        self.deliver(0)   # W1 acknowledged and read.
        lead_side.settle()
        self.assertTrue(lead_side._acked[self.session][1]["auto"])
        self.mode("ASK")   # N1: the worker applies it ...
        self.deliver(0, read=False)
        self.assertFalse(self.worker.consent_lease.granted_auto())
        self.mode("AUTO_BOUNDED")   # ... W2 sent; the lead still shows W1 as the newest acknowledgement it read.
        self.assertTrue(lead_side._acked[self.session][1]["auto"])
        self.mode("ASK")   # N2, to a worker frozen whole: W2 and N2 never arrive.
        self.clock.now += lead_side.ACK_SECONDS
        lead_side.tick()
        self.assertTrue(lead_side.needs_approval(self.session))
        self.assertEqual(self.killed, [self.session])
        self.assertFalse(self.worker.consent_lease.granted_auto())   # It holds N1, the narrower one.
        self.read_all()   # N1's answer, read after its deadline: still a loss, already handled.
        lead_side.tick()
        self.assertEqual(self.killed, [self.session])

    def test_every_delivery_order_ends_on_the_newest_lease_received(self):
        """Three changes (Ask, Auto, Ask) delivered in each of their six orders: the worker ends on the newest one it
        received, and none of the older answers is a loss."""
        import itertools
        for order in itertools.permutations(range(3)):
            with self.subTest(order=order):
                lead_side.reset()
                self.setUp()
                for approval in ("ASK", "AUTO_BOUNDED", "ASK"):
                    self.mode(approval)
                sends = list(self.deferred)
                self.deferred.clear()
                for index in order:
                    sends[index]()
                lead_side.tick()
                self.assertFalse(self.worker.consent_lease.granted_auto())
                newest = max(request["epoch"] for request in self.channel.ops("chat.consent"))
                self.assertEqual(self.worker.consent_lease.epoch, newest)
                self.assertEqual((self.killed, lead_side.needs_approval(self.session)), ([], False))

    def test_a_widening_received_after_a_newer_revocation_is_refused_and_the_worker_stays_narrow(self):
        self.mode("ASK")
        self.mode("AUTO_BOUNDED")
        self.mode("ASK")
        self.deliver(2)   # The newest revocation first ...
        self.deliver(1)   # ... then the older widening: refused as older.
        self.deliver(0)
        self.assertEqual(self.answers[1].get("error_code"), "STALE_REVISION")
        lead_side.tick()
        self.assertFalse(self.worker.consent_lease.granted_auto())
        self.assertEqual(self.killed, [])


class ApproveAnswers(mirror_tests.Fixture):
    """Review item B (UI re-review r2): the worker runs an approved edit or script before it answers ``chat.approve``,
    so an Approve or Decline waits as long as any asynchronous op, on its own thread, and no answer is unknown, never
    "nothing changed"; only the worker's own refusal (or a request never sent) says nothing ran."""

    def setUp(self):
        super().setUp()
        self.lane()
        self.timeouts, self.raised, self.answer = [], None, {"ok": True}

        def send(session, request, timeout):
            self.timeouts.append((request["op"], timeout))
            if self.raised is not None:
                raise self.raised
            return self.answer
        lead_side.bind(send=send)
        self.pending({"id": "s1", "name": "angelo_create_cube", "digest": "e" * 64, "needs_person": True,
                      "expires": 120, "summary": {"arguments": {"name": "A", "size": 1}, "approval": True}})
        self.mirror.pump_events()

    def errors(self):
        return [m["text"] for m in self.mirror.state["messages"] if m.get("role") == "error"]

    def approve_drawn(self):
        lead_side.drew(self.mirror, "s1")
        self.assertIsNone(lead_side.approve(self.mirror, "s1"))
        self.mirror.pump_events()
        return self.mirror.pending["s1"]

    def test_approve_and_decline_wait_as_long_as_an_asynchronous_op_never_the_stop_deadline(self):
        self.approve_drawn()
        lead_side.decline(self.mirror, "s1")
        self.assertEqual(self.timeouts, [("chat.approve", worker_mirror_ops.ANSWER_SECONDS),
                                         ("chat.decline", worker_mirror_ops.ANSWER_SECONDS)])

    def test_an_approve_not_answered_in_time_stays_approving_and_says_nothing_ran_nowhere(self):
        self.raised = WorkerUnavailable("no answer within 300 s", timed_out=True)
        call = self.approve_drawn()
        self.assertTrue(call.approved and not call.approval)   # Approving: never offered again while it may run.
        self.assertEqual(self.errors(), [])
        self.assertEqual(lead_side.approve(self.mirror, "s1"), lead_side.NOT_SHOWN)   # No second send.
        self.assertEqual([op for op, _timeout in self.timeouts], ["chat.approve"])

    def test_a_worker_that_ended_after_the_approve_was_sent_is_unknown_too(self):
        self.raised = WorkerUnavailable("The worker ended before it answered")
        self.approve_drawn()
        self.assertEqual(self.errors(), [])

    def test_the_workers_own_refusal_puts_the_card_back_and_says_nothing_ran(self):
        self.answer = {"error": "This call changed after it was shown for approval"}
        call = self.approve_drawn()
        self.assertTrue(call.approval and not call.approved)
        self.assertEqual(len(self.errors()), 1)
        self.assertIn("Nothing ran", self.errors()[0])

    def test_a_request_never_sent_is_known_not_run(self):
        for error in (RequestRefused("too large", "LIMIT_EXCEEDED"),
                      WorkerUnavailable("The worker has ended", sent=False)):
            with self.subTest(error=type(error).__name__):
                self.raised = error
                self.mirror.state["messages"] = []
                lead_side.forget(self.session)
                lead_side.register(self.mirror)
                self.pending({"id": "s1", "name": "angelo_create_cube", "digest": "e" * 64, "needs_person": True,
                              "expires": 120, "summary": {"arguments": {"name": "A", "size": 1}, "approval": True}})
                self.mirror.pump_events()
                self.mirror.pending["s1"].approved, self.mirror.pending["s1"].approval = False, True
                self.approve_drawn()
                self.assertIn("Nothing ran", " ".join(self.errors()))

    def test_a_decline_not_answered_in_time_is_not_reported_as_refused(self):
        self.raised = WorkerUnavailable("no answer within 300 s", timed_out=True)
        lead_side.decline(self.mirror, "s1")
        self.mirror.pump_events()
        self.assertNotIn("s1", self.mirror.pending)
        self.assertEqual(self.errors(), [])

    def test_the_mirrors_own_approve_sends_only_the_drawn_digest(self):
        """``MirrorController.approve_call`` (the approve operator's lane route) goes through the consent lane."""
        lead_side.drew(self.mirror, "s1")
        self.pending({"id": "s1", "name": "angelo_create_cube", "digest": "f" * 64, "needs_person": True,
                      "expires": 120, "summary": {"arguments": {"name": "B", "size": 1}, "approval": True}})
        self.mirror.pump_events()   # Its call changed after it was drawn: the redraw shows the new one.
        with self.assertRaises(MirrorRefused):
            self.mirror.approve_call("s1")
        self.assertEqual(self.timeouts, [])
        lead_side.drew(self.mirror, "s1")
        self.assertTrue(self.mirror.approve_call("s1"))
        self.assertEqual([op for op, _timeout in self.timeouts], ["chat.approve"])

    def test_the_mirrors_own_approve_of_a_card_never_drawn_sends_the_mirrors_digest(self):
        """78e17c6e1a1's Town benchmark: refused, the worker waited until its approval expired."""
        self.assertTrue(self.mirror.approve_call("s1"))
        self.assertEqual([op for op, _timeout in self.timeouts], ["chat.approve"])


class ConsentDuringOpen(Pending):
    """Concurrency re-review R2: a deferred ``chat.open`` goes on its own thread; a consent change made before it is
    answered waits for it (the open carries the lease registered with it), so it never reaches the worker first,
    is refused there for a chat it does not host and is read as a lost revocation."""

    def test_a_change_during_the_open_goes_out_after_it_and_kills_nothing(self):
        lead, mirror, worker = self.hosted_pair()
        session = mirror.state["id"]
        with patch.dict(worker_chat_lifecycle._unanswered, {session: 1}):   # Its open sent, not answered yet.
            mirror.state["options"]["approval"] = "ASK"
            lead_side.changed()
            self.clock.now += lead_side.ACK_SECONDS * 3   # A slow open: nothing was sent, so nothing is lost.
            lead_side.tick()
            self.assertEqual((self.channel.ops("chat.consent"), self.deferred), ([], []))
            self.assertEqual((self.killed, lead_side.needs_approval(session)), ([], False))
            worker_chat_lifecycle._answered(session)   # Answered: the worker hosts the chat now.
            lead_side.tick()
            self.run_send(0)
            lead_side.tick()
        self.assertFalse(worker.consent_lease.granted_auto())
        self.assertEqual((self.killed, lead_side.needs_approval(session)), ([], False))
        self.assertFalse(lead_side._inflight.get(session))


if __name__ == "__main__":
    unittest.main()
