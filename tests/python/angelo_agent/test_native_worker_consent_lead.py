# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat hosted in its worker (design A′, contracts C5 Stop and C6), lead side: the consent lease the lead
sends, its acknowledgement deadline, Approve relaying and Stop's kill (``worker_consent_lead``).

Real ``Controller`` objects stand for the lead chat and the hosted chat's mirror; the channel, the runtime kill and
the clock are fakes (``worker_consent_lead.bind``), and a fake channel can deliver to a real worker-side
``worker_consent`` lease. Threats pinned: a lost acknowledgement, a late one, epochs delivered out of order (an
older one never rolls the worker back, and its refusal is not a loss), a widening never acknowledged, a duplicated
Approve, an Approve after Stop or close, a card drawn before Stop, a worker that never answers Stop, and an Approve of
a card the lead never drew (the mirror's digest) next to one drawn once (never the mirror's).
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_consent, worker_consent_lead as lead_side, worker_trust  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.chat_settings import APPROVALS  # noqa: E402
from test_native_dispatch_fixes import ui_class  # noqa: E402
from test_native_worker_consent import Hosted, Part  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Channel:
    """The chat-op transport to each worker. ``mode``: "ack" (a real worker lease answers), "lost" (the request
    times out), "hold" (sent, answered only by ``release``)."""

    def __init__(self, clock):
        self.clock, self.mode, self.requests, self.held, self.workers = clock, "ack", [], [], {}

    def send(self, session, request, timeout):
        self.requests.append(dict(request))
        if self.mode == "lost":
            raise TimeoutError("No answer within %.1f s" % timeout)
        worker = self.workers.get(session)
        if request["op"] == "chat.consent" and worker is not None:
            return worker_consent.apply_consent(worker, request["epoch"], request["grants"])
        return {"ok": True}

    def ops(self, op):
        return [request for request in self.requests if request["op"] == op]


class LeadFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(lead_side.reset)
        switch = patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"})   # A′'s lead side, whatever the run's switch.
        switch.start()
        self.addCleanup(switch.stop)
        self.clock = Clock()
        self.channel = Channel(self.clock)
        self.killed, self.deferred, self.connected = [], [], True
        lead_side.reset()
        lead_side.bind(send=self.channel.send, kill=self.killed.append, clock=self.clock,
                       connection=lambda controller: self.connected, spawn=self.spawn, wait=self.wait)

    def spawn(self, function):
        if self.channel.mode == "hold":
            self.deferred.append(function)
        else:
            function()

    def wait(self, record, seconds):
        self.clock.now += seconds   # Nothing answered while it waited the whole deadline.
        return record.done.is_set()

    def release(self, reverse=False):
        for function in (reversed(self.deferred) if reverse else list(self.deferred)):
            function()
        self.deferred.clear()

    def show(self, mirror, *cards):
        """``(id, name, digest)`` cards: the ``pending`` event, the mirror's calls and each card drawn (``drew``)."""
        lead_side.note_pending(mirror.state["id"], [{"id": i, "name": n, "digest": d} for i, n, d in cards])
        for call_id, name, digest in cards:
            mirror.pending[call_id] = SimpleNamespace(id=call_id, name=name, approval_digest=digest,
                                                      reviewed_script=None)
            self.addCleanup(mirror.pending.pop, call_id, None)   # Stand-ins: never cancelled by the chat's close.
            lead_side.drew(mirror, call_id)

    def chat(self, mode="AUTO_BOUNDED", cls=Controller):
        folder = Path(self.directory.name) / uuid.uuid4().hex
        folder.mkdir()
        chat = cls(str(folder))
        self.addCleanup(chat.close)
        chat.state["options"]["approval"] = mode
        chat.set_document(("part", 3))
        return chat

    def hosted_pair(self, mode="AUTO_BOUNDED"):
        """A lead chat, the mirror of a worker chat it started, and that worker's real hosted chat."""
        lead = self.chat(mode)
        mirror = self.chat(mode)
        mirror.remote_executor = object()   # Satisfies ``worker_trust.is_worker`` (the mirror of a worker chat).
        worker_trust.link(lead, mirror)
        worker = self.chat(mode, Hosted)
        opened = lead_side.register(mirror)
        worker_consent.apply_consent(worker, opened["epoch"], opened["grants"])   # chat.open carries it (C2).
        self.channel.workers[mirror.state["id"]] = worker
        return lead, mirror, worker


class ConsentTests(LeadFixture):
    def test_the_open_carries_the_lease_and_each_change_a_higher_epoch(self):
        lead, mirror, worker = self.hosted_pair()
        self.assertEqual(worker.consent_lease.grants(), {"auto": True})
        lead.set_trusted_scripts(True)   # The lead's trust reaches its worker chats.
        first = self.channel.ops("chat.consent")[-1]
        self.assertTrue(first["grants"]["scripts"])
        self.assertIsNotNone(worker.consent_lease.token("scripts"))
        lead.set_trusted_scripts(False)
        second = self.channel.ops("chat.consent")[-1]
        self.assertGreater(second["epoch"], first["epoch"])
        self.assertEqual(worker.consent_lease.grants(), {"auto": True})
        self.assertEqual(self.killed, [])

    def test_ask_read_only_and_a_missing_connection_grant_nothing(self):
        _lead, mirror, _worker = self.hosted_pair()
        for mode in ("ASK", "READ_ONLY"):
            mirror.state["options"]["approval"] = mode
            self.assertEqual(lead_side.grants(mirror), {"auto": False})
        mirror.state["options"]["approval"] = "AUTO_BOUNDED"
        self.connected = False   # The key was removed, or the Team sign-in ended.
        self.assertEqual(lead_side.grants(mirror), {"auto": False})
        self.connected, mirror.connection_error = True, True
        self.assertEqual(lead_side.grants(mirror), {"auto": False})

    def test_a_revocation_returns_only_after_the_worker_acknowledged_it(self):
        _lead, mirror, worker = self.hosted_pair()
        mirror.state["options"]["approval"] = "ASK"
        lead_side.changed()
        self.assertFalse(worker.consent_lease.granted_auto())   # Applied before changed() returned.
        self.assertEqual((self.killed, lead_side.needs_approval(mirror.state["id"])), ([], False))

    def test_a_lost_acknowledgement_stops_the_turn_and_marks_the_chat(self):
        _lead, mirror, _worker = self.hosted_pair()
        session = mirror.state["id"]
        mirror.busy = True
        self.channel.mode = "lost"
        mirror.state["options"]["approval"] = "ASK"
        lead_side.changed()
        self.assertEqual(self.killed, [session])
        self.assertTrue(lead_side.needs_approval(session))
        self.assertTrue(self.channel.ops("chat.stop"))
        mirror.pump_events()
        self.assertEqual(mirror.state["messages"][-1]["text"], lead_side.NEEDS_APPROVAL)
        mirror.state["options"]["approval"] = "AUTO_BOUNDED"
        self.assertEqual(lead_side.grants(mirror), {"auto": False})   # Nothing unasked until the person acts.
        lead_side.tick()
        lead_side.changed()
        self.assertEqual(self.killed, [session])   # A worker still not answering is not stopped twice.

    def test_an_acknowledgement_that_never_comes_stops_the_turn_after_the_deadline(self):
        _lead, mirror, worker = self.hosted_pair()
        session = mirror.state["id"]
        self.channel.mode = "hold"   # The worker is wedged: the request is never answered.
        mirror.state["options"]["approval"] = "ASK"   # An implicit change: found by the UI timer.
        lead_side.tick()
        self.clock.now += lead_side.ACK_SECONDS - 0.1
        lead_side.tick()
        self.assertEqual(self.killed, [])
        self.clock.now += 0.1
        lead_side.tick()
        self.assertEqual(self.killed, [session])
        self.assertTrue(worker.consent_lease.granted_auto())   # Unchanged in the worker: its turn was stopped instead.

    def test_a_late_acknowledgement_after_the_deadline_still_counts_as_lost(self):
        _lead, mirror, _worker = self.hosted_pair()
        self.channel.mode = "hold"
        mirror.state["options"]["approval"] = "ASK"
        lead_side.changed()   # Returns at once: the window never waits on a worker.
        self.clock.now += lead_side.ACK_SECONDS   # The answer comes after the deadline.
        self.channel.mode = "ack"
        self.release()
        lead_side.tick()
        self.assertEqual(self.killed, [mirror.state["id"]])

    def test_an_acknowledgement_answered_after_the_deadline_counts_as_lost_whenever_it_is_read(self):
        _lead, mirror, _worker = self.hosted_pair()
        self.channel.mode = "hold"
        mirror.state["options"]["approval"] = "ASK"
        lead_side.tick()
        self.clock.now += lead_side.ACK_SECONDS + 0.5
        self.channel.mode = "ack"
        self.release()   # Answered late, before the timer looked again.
        lead_side.tick()
        self.assertEqual(self.killed, [mirror.state["id"]])

    def test_epochs_delivered_out_of_order_never_roll_the_worker_back_and_are_not_a_loss(self):
        lead, mirror, worker = self.hosted_pair()
        self.channel.mode = "hold"
        lead.set_trusted_scripts(True)
        lead_side.tick()
        mirror.state["options"]["approval"] = "ASK"
        lead_side.tick()
        self.channel.mode = "ack"
        self.release(reverse=True)   # The newer revocation arrives first, then the older widening.
        lead_side.tick()
        self.assertEqual(worker.consent_lease.grants(), {"auto": False})
        self.assertEqual(self.killed, [])

    def test_an_older_revocation_refused_after_a_newer_one_was_applied_is_not_a_loss(self):
        lead, mirror, worker = self.hosted_pair()
        lead.set_trusted_scripts(True)   # Acknowledged.
        self.channel.mode = "hold"
        lead._script_consent = None   # Trust ends (a New Chat in the lead): found by the timer.
        lead_side.tick()
        mirror.state["options"]["approval"] = "ASK"
        lead_side.tick()
        self.channel.mode = "ack"
        self.release(reverse=True)
        lead_side.tick()
        self.assertEqual(worker.consent_lease.grants(), {"auto": False})
        self.assertEqual(self.killed, [])

    def test_a_widening_never_acknowledged_is_sent_again_and_stops_nothing(self):
        lead, mirror, worker = self.hosted_pair()
        self.channel.mode = "lost"
        lead.set_trusted_scripts(True)
        self.assertEqual(self.killed, [])
        self.assertIsNone(worker.consent_lease.token("scripts"))   # Narrower, never wider.
        self.channel.mode = "ack"
        lead_side.tick()
        self.assertIsNotNone(worker.consent_lease.token("scripts"))

    def test_the_persons_next_message_sends_the_real_grants_again(self):
        _lead, mirror, worker = self.hosted_pair()
        session = mirror.state["id"]
        self.channel.mode = "lost"
        mirror.state["options"]["approval"] = "ASK"
        lead_side.changed()
        mirror.state["options"]["approval"] = "AUTO_BOUNDED"
        self.assertFalse(lead_side.resume(session))   # Still no answer: still marked.
        self.channel.mode = "ack"
        self.assertTrue(lead_side.resume(session))
        self.assertFalse(lead_side.needs_approval(session))
        self.assertTrue(worker.consent_lease.granted_auto())

    def test_with_the_switch_off_nothing_is_sent(self):
        lead = self.chat()
        lead.set_trusted_scripts(True)   # No chat is hosted: today's lanes.
        lead_side.tick()
        self.assertEqual(self.channel.requests, [])
        _lead, mirror, _worker = self.hosted_pair()
        mirror.state["options"]["approval"] = "ASK"
        with patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"}):
            self.assertEqual(lead_side.sync(), [])
        self.assertFalse(lead_side.enabled({"ANGELO_WORKER_CHAT_HOST": "0"}))
        self.assertTrue(lead_side.enabled({}))
        self.assertTrue(lead_side.enabled({"ANGELO_WORKER_CHAT_HOST": "1"}))


class ApproveTests(LeadFixture):
    def card(self, mirror, call_id="c1", digest="d1", name="angelo_create_cube"):
        self.show(mirror, (call_id, name, digest))

    def test_approve_sends_the_drawn_digest_once(self):
        _lead, mirror, _worker = self.hosted_pair()
        self.card(mirror)
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)   # A double click.
        sent = self.channel.ops("chat.approve")
        self.assertEqual([(request["call_id"], request["digest"]) for request in sent], [("c1", "d1")])

    def test_no_approve_during_stop_or_for_a_card_drawn_before_it(self):
        _lead, mirror, _worker = self.hosted_pair()
        mirror.busy = True
        self.card(mirror)
        lead_side.stop(mirror)
        self.card(mirror, "c2")   # A late pending event, emitted before the worker read the Stop.
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.STOPPING)
        mirror.busy = False
        lead_side.tick()   # The turn ended.
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)
        self.assertEqual(lead_side.approve(mirror, "c2"), lead_side.NOT_SHOWN)
        self.assertEqual(self.channel.ops("chat.approve"), [])

    def test_no_approve_after_close(self):
        _lead, mirror, _worker = self.hosted_pair()
        self.card(mirror)
        lead_side.forget(mirror.state["id"])
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)
        self.assertFalse(lead_side.hosted(mirror))
        self.assertEqual(self.channel.ops("chat.approve"), [])

    def test_a_card_without_a_digest_is_never_approvable(self):
        _lead, mirror, _worker = self.hosted_pair()
        lead_side.note_pending(mirror.state["id"], [{"id": "c1", "name": "angelo_create_cube", "digest": None}])
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)

    def undrawn(self, mirror, call_id="c1", digest="d1", held=None):
        """A ``pending`` card no chat area drew (the rail's tile shows it); ``held``: the mirror call's own digest."""
        lead_side.note_pending(mirror.state["id"], [{"id": call_id, "name": "angelo_create_cube", "digest": digest}])
        mirror.pending[call_id] = SimpleNamespace(id=call_id, name="angelo_create_cube", reviewed_script=None,
                                                  approval_digest=digest if held is None else held)
        self.addCleanup(mirror.pending.pop, call_id, None)

    def test_a_card_never_drawn_sends_the_mirrors_digest_once(self):
        """78e17c6e1a1's Town benchmark: Approve from the lead (no chat area showed the worker's chat) was refused,
        and each worker waited until its approval expired. It approves the call as the mirror holds it, once."""
        _lead, mirror, _worker = self.hosted_pair()
        self.undrawn(mirror)
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)   # A second call sends nothing.
        self.assertEqual([(r["call_id"], r["digest"]) for r in self.channel.ops("chat.approve")], [("c1", "d1")])

    def test_a_card_never_drawn_is_sent_again_after_a_lost_or_refused_answer(self):
        """Break-it review of the fix: marking an undrawn card spent left a lost Approve, or one the worker refused and
        whose card it sent again, unanswerable until the approval expired: the benchmark's symptom again."""
        _lead, mirror, _worker = self.hosted_pair()
        self.undrawn(mirror)
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        lead_side.note_call(mirror.state["id"], {"id": "c1", "name": "angelo_create_cube", "digest": "d1"})   # Lost.
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        self.undrawn(mirror, "c1", "d2")   # Refused there; its card sent again, changed.
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        lead_side.decline(mirror, "c1")
        self.undrawn(mirror, "c1", "d3")   # A Decline whose answer was lost: the card comes back.
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        self.assertEqual([r["digest"] for r in self.channel.ops("chat.approve")], ["d1", "d1", "d2", "d3"])

    def test_a_card_never_drawn_whose_mirror_call_differs_is_refused(self):
        _lead, mirror, _worker = self.hosted_pair()
        self.undrawn(mirror, held="d0")   # The summary arrived; the mirror's call is still the earlier one.
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)
        self.assertEqual(self.channel.ops("chat.approve"), [])

    def test_a_card_drawn_once_never_takes_the_mirrors_digest(self):
        """Answered, then sent again changed (a refusal resends the cards): a click on the old drawing sends nothing."""
        _lead, mirror, _worker = self.hosted_pair()
        self.card(mirror, "c1", "d1")
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        self.undrawn(mirror, "c1", "d2")
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)
        lead_side.drew(mirror, "c1")   # Its redraw shows the new call.
        self.assertIsNone(lead_side.approve(mirror, "c1"))
        self.assertEqual([r["digest"] for r in self.channel.ops("chat.approve")], ["d1", "d2"])

    def test_a_card_declined_or_drawn_before_a_stop_never_takes_the_mirrors_digest(self):
        _lead, mirror, _worker = self.hosted_pair()
        self.card(mirror, "c1", "d1")
        lead_side.decline(mirror, "c1")
        self.undrawn(mirror, "c1", "d1")
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)
        mirror.busy = True
        self.card(mirror, "c2", "d2")
        lead_side.stop(mirror)
        mirror.busy = False
        lead_side.tick()   # The turn ended; the same call is offered again.
        self.undrawn(mirror, "c2", "d2")
        self.assertEqual(lead_side.approve(mirror, "c2"), lead_side.NOT_SHOWN)
        self.assertEqual(self.channel.ops("chat.approve"), [])

    def test_a_card_drawn_before_a_close_never_takes_the_mirrors_digest_after_a_reopen(self):
        _lead, mirror, _worker = self.hosted_pair()
        self.card(mirror, "c1", "d1")
        lead_side.forget(mirror.state["id"])
        lead_side.register(mirror)   # A park's relaunch reopens the chat; its pending call comes back.
        self.undrawn(mirror, "c1", "d1")
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)
        self.undrawn(mirror, "c3", "d3")   # A card the reopened chat never drew.
        self.assertIsNone(lead_side.approve(mirror, "c3"))
        self.assertEqual([r["call_id"] for r in self.channel.ops("chat.approve")], ["c3"])

    def test_an_undrawn_card_noted_before_a_stop_is_stale(self):
        _lead, mirror, _worker = self.hosted_pair()
        mirror.busy = True
        self.undrawn(mirror)
        lead_side.stop(mirror)
        mirror.busy = False
        lead_side.tick()
        self.assertEqual(lead_side.approve(mirror, "c1"), lead_side.NOT_SHOWN)
        self.assertEqual(self.channel.ops("chat.approve"), [])


class StopTests(LeadFixture):
    def test_a_turn_that_ends_within_the_deadline_is_not_killed(self):
        _lead, mirror, _worker = self.hosted_pair()
        mirror.busy = True
        lead_side.stop(mirror, "The user stopped")
        self.assertEqual(self.channel.ops("chat.stop")[-1]["reason"], "The user stopped")
        self.clock.now += lead_side.STOP_SECONDS - 0.5
        lead_side.tick()
        mirror.busy = False
        self.clock.now += 1.0
        lead_side.tick()
        self.assertEqual(self.killed, [])
        self.assertFalse(lead_side.stopping(mirror.state["id"]))

    def test_a_turn_not_ended_after_the_deadline_has_its_model_killed_once(self):
        _lead, mirror, _worker = self.hosted_pair()
        mirror.busy = True
        lead_side.stop(mirror)
        self.clock.now += lead_side.STOP_SECONDS - 0.01
        lead_side.tick()
        self.assertEqual(self.killed, [])
        self.clock.now += 0.01
        lead_side.tick()
        lead_side.tick()
        self.assertEqual(self.killed, [mirror.state["id"]])

    def test_a_wedged_worker_still_stops(self):
        _lead, mirror, _worker = self.hosted_pair()
        mirror.busy = True
        self.channel.mode = "hold"   # chat.stop is never answered.
        lead_side.stop(mirror)   # Returns at once.
        self.clock.now += lead_side.STOP_SECONDS
        lead_side.tick()
        self.assertEqual(self.killed, [mirror.state["id"]])
        self.assertTrue(lead_side.stopping(mirror.state["id"]))   # Approvals stay refused until the turn ends.

    def test_a_late_approve_after_stop_is_never_applied(self):
        """End to end through the real worker side: the card's Approve after Stop runs nothing on either side."""
        _lead, mirror, worker = self.hosted_pair("ASK")
        worker.busy = mirror.busy = True
        part = Part()
        worker.request_tool("c1", "angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]},
                            lambda result: None)
        worker.pump_tools(part, worker.document, budget=ToolDispatchBudget(seconds=60))
        (call_id, digest), = worker_consent.pending_digests(worker).items()
        lead_side.note_pending(mirror.state["id"], [{"id": call_id, "name": "angelo_create_cube", "digest": digest}])
        lead_side.stop(mirror)
        worker.stop()   # chat.stop reached the worker.
        self.assertEqual(lead_side.approve(mirror, call_id), lead_side.STOPPING)
        self.assertFalse(worker_consent.approve(worker, call_id, digest, lambda call: self.fail("ran"))["approved"])
        self.assertEqual(part.ran, [])

class WiringTests(LeadFixture):
    """The window's real operators reach the lead side for a hosted chat, and only for one."""

    def test_the_approve_and_decline_operators_route_a_hosted_card_to_its_worker(self):
        _lead, mirror, _worker = self.hosted_pair()
        session = mirror.state["id"]
        self.show(mirror, ("c1", "angelo_create_cube", "d1"), ("c2", "angelo_create_cube", "d2"))
        names = {"_controllers": {session: mirror}, "get_controller": lambda: None}
        approve = ui_class("ANGELO_OT_approve", names)()
        approve.call_id, approve.session_id, approve.report = "c1", session, Mock()
        self.assertEqual(approve.execute(None), {'FINISHED'})
        self.assertEqual(approve.execute(None), {'CANCELLED'})   # Its second click sends nothing.
        decline = ui_class("ANGELO_OT_decline", names)()
        decline.call_id, decline.session_id = "c2", session
        decline.execute(None)
        self.assertEqual([request["op"] for request in self.channel.requests], ["chat.approve", "chat.decline"])

    def test_a_hosted_script_asks_for_its_review_before_approve(self):
        _lead, mirror, _worker = self.hosted_pair()
        session = mirror.state["id"]
        self.show(mirror, ("s1", "angelo_execute_python", "d1"))
        bpy = Mock()
        with patch.dict(sys.modules, {"bpy": bpy}):
            approve = ui_class("ANGELO_OT_approve", {"_controllers": {session: mirror}, "get_controller": None})()
            approve.call_id, approve.session_id, approve.report = "s1", session, Mock()
            self.assertEqual(approve.execute(None), {'CANCELLED'})
        bpy.ops.angelo.review_tool.assert_called_once_with(call_id="s1", session_id=session)
        mirror.reviewed_calls.add("s1")
        mirror.pending["s1"].reviewed_script = "d1"   # Reviewed: that (id, digest) call object.
        self.assertEqual(approve.execute(None), {'FINISHED'})
        self.assertEqual(self.channel.ops("chat.approve")[0]["digest"], "d1")

    def test_a_hosted_script_no_chat_area_drew_is_reviewed_then_approved_from_the_lead(self):
        """The lanes' ``CardBindingTests`` case under A′: the person (or the benchmark's tester) approves a worker's
        script while the lead's chat is shown. 78e17c6e1a1 refused it as not shown, and the worker waited until its
        approval expired."""
        _lead, mirror, _worker = self.hosted_pair()
        session = mirror.state["id"]
        lead_side.note_pending(session, [{"id": "s1", "name": "angelo_execute_python", "digest": "d1"}])
        mirror.pending["s1"] = SimpleNamespace(id="s1", name="angelo_execute_python", approval_digest="d1",
                                               reviewed_script=None)
        self.addCleanup(mirror.pending.pop, "s1", None)
        bpy = Mock()
        with patch.dict(sys.modules, {"bpy": bpy}):
            approve = ui_class("ANGELO_OT_approve", {"_controllers": {session: mirror}, "get_controller": None})()
            approve.call_id, approve.session_id, approve.report = "s1", session, Mock()
            self.assertEqual(approve.execute(None), {'CANCELLED'})   # Its exact-code review first.
        bpy.ops.angelo.review_tool.assert_called_once_with(call_id="s1", session_id=session)
        self.assertEqual(self.channel.ops("chat.approve"), [])
        mirror.reviewed_calls.add("s1")
        mirror.pending["s1"].reviewed_script = "d1"
        self.assertEqual(approve.execute(None), {'FINISHED'})
        self.assertEqual([r["digest"] for r in self.channel.ops("chat.approve")], ["d1"])

    def test_the_approval_menu_revokes_auto_before_it_returns(self):
        _lead, mirror, worker = self.hosted_pair()
        operator = ui_class("ANGELO_OT_set_approval", {"__package__": "angelo_agent", "__name__": "angelo_agent.ui",
                                                      "get_controller": lambda: mirror, "APPROVALS": APPROVALS})()
        operator.report, operator.mode = Mock(), "ASK"
        self.assertEqual(operator.execute(None), {'FINISHED'})
        self.assertFalse(worker.consent_lease.granted_auto())

    def test_every_place_standing_consent_changes_sends_the_leases(self):
        """Source pin: each consent toggle calls ``changed`` (revocations wait for their acknowledgement), and the UI
        timer calls ``tick`` for what moves on its own (a policy arriving, a document change in the lead)."""
        import ast
        addon = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        sites = {("trusted_scripts.py", "set_trusted_scripts"): "changed",
                 ("trusted_scripts_ui.py", "ANGELO_OT_set_approval.execute"): "changed",
                 ("trusted_scripts_ui.py", "always_allow_set"): "changed",
                 ("team_account_ui.py", "ANGELO_OT_team_sign_out.execute"): "changed",
                 ("team_account_ui.py", "close"): "changed",
                 ("ui.py", "ANGELO_OT_connection_remove.execute"): "changed",
                 ("ui.py", "pump"): "tick"}
        for (name, path), call in sites.items():
            with self.subTest(site=name + ":" + path):
                node = ast.parse((addon / name).read_text())
                for part in path.split("."):
                    node = next(child for child in ast.walk(node) if isinstance(
                        child, (ast.ClassDef, ast.FunctionDef)) and child.name == part)
                calls = {(child.func.value.id, child.func.attr) for child in ast.walk(node)
                         if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                         and isinstance(child.func.value, ast.Name)}
                self.assertIn(("worker_consent_lead", call), calls)


if __name__ == "__main__":
    unittest.main()
