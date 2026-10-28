# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The worker host loop's answers that the lead relies on (design A′, ``worker_chat_host``), without Blender.

Each test is a break-it finding's proof turned around (review-concurrency F1, F2, F3, F6, F7, F8; review-ui 1, 11):

* a merge hold is granted only while still wanted (a release refuses its deferred hold) and only once the part is
  saved (a failed save refuses it);
* any refusal sends the chat's cards again, so a card whose one Approve the lead spent on it can be approved later;
* closing a busy chat forwards its turn's end before the close, and answers ``was_busy``; closing a chat the worker
  no longer hosts answers ``closed: False`` so the lead can take it back;
* a real ``WorkerHost`` lists the chats it hosts, so a part switch under an open chat is refused;
* a pending call's summary carries what the lead needs to draw a typed program's review card, and the worker's turn
  facts reach a mirror as the rounds and clock ``worker_facts`` reads.
"""

import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_native_review_preparation import call_for, drain
from test_native_worker_chat_host import ApprovalTests, FakeProvider, Fixture

from angelo_agent import approval_review, worker_chat_events, worker_consent, worker_switch, worker_values
from angelo_agent.pending_delivery import ToolCall
from angelo_agent.refusal_codes import RefusalError


class HoldTests(ApprovalTests):
    def answer(self, seq):
        found = [frame["response"] for frame in self.endpoint.responses() if frame["seq"] == seq]
        return found[-1] if found else None

    def test_a_release_refuses_a_deferred_hold_and_a_late_grant_never_comes(self):
        """F1: the lead's hold timed out and it released; the call then ended. The chat must not end up held."""
        chat, call = self.pending_call()
        drawn = self.drawn_digest(call)
        call.executing = True
        self.assertTrue(self.chats.defer(7, self.ask("chat.hold")))
        self.chats.pump()
        self.assertIsNone(self.answer(7))
        self.assertEqual(self.ask("chat.release"), {"ok": True})
        self.assertEqual(self.answer(7)["error"]["code"], "CANCELLED")   # Refused at once, never granted later.
        call.executing = False
        self.chats.pump()
        self.assertFalse(chat.holding or chat.held)
        self.assertEqual(self.ask("chat.approve", call_id=call.id, digest=drawn), {"ok": True, "approved": True})
        self.finish(chat)

    def test_a_hold_saves_the_part_first_and_is_refused_when_the_save_fails(self):
        """F8: the merge reads the part file, so a hold never holds with the last edit unsaved."""
        self.open()
        chat = self.chats.hosted(self.session)
        saves = []

        def failing_save():
            saves.append(1)
            raise OSError("the file is open in another process")
        self.host.save = failing_save
        self.bpy.data.is_dirty = True
        self.chats.pump()   # The loop's own save fails; its next try is a second later.
        self.chats.defer(5, self.ask("chat.hold"))
        answer = self.answer(5)
        self.assertEqual(answer["error"]["code"], "WRITER_BUSY")
        self.assertIn("could not save", answer["error"]["message"])
        self.assertEqual(len(saves), 2)   # The hold tried at once, whatever the retry delay.
        self.assertFalse(chat.holding or chat.held)
        self.host.save = lambda: (saves.append(2), setattr(self.bpy.data, "is_dirty", False), self.host.part)[2]
        self.chats.defer(6, self.ask("chat.hold"))
        self.assertTrue(self.answer(6)["held"] and chat.held)
        self.assertEqual((saves[-1], self.bpy.data.is_dirty), (2, False))   # Saved, then held.
        self.ask("chat.release")


class RefusalTests(ApprovalTests):
    def test_every_refusal_sends_the_cards_again(self):
        """F7: an Approve refused during a merge hold leaves a card the lead must be able to approve afterwards."""
        chat, call = self.pending_call()
        drawn = self.drawn_digest(call)
        self.chats.defer(9, self.ask("chat.hold"))
        self.chats.pump()
        before = len(self.endpoint.events("pending"))
        with self.assertRaises(RefusalError):
            self.ask("chat.approve", call_id=call.id, digest=drawn)
        self.ask("chat.release")
        self.chats.pump()
        self.chats.outbox.flush()
        cards = self.endpoint.events("pending")[before:]
        self.assertTrue(cards)
        self.assertEqual([card["digest"] for card in cards[-1]["data"]["calls"] if card["id"] == call.id], [drawn])
        self.ask("chat.consent", epoch=1, grants={"auto": False})
        self.assertIs(self.ask("chat.consent", epoch=0, grants={"auto": False})["ok"], False)   # Refused: cards again.
        count = len(self.endpoint.events("pending"))
        self.chats.pump()
        self.chats.outbox.flush()
        self.assertEqual(len(self.endpoint.events("pending")), count + 1)
        self.ask("chat.approve", call_id=call.id, digest=drawn)
        self.assertEqual(self.bpy.approved, [call.id])
        self.finish(chat)


class CloseTests(Fixture):
    def test_closing_a_busy_chat_forwards_its_turn_end_first_and_answers_was_busy(self):
        """F2: the lead's mirror hears the turn end before the close's answer, once."""
        self.open()
        FakeProvider.tools, FakeProvider.gate = (), threading.Event()
        self.ask("chat.send", text="long", attachments=[])
        self.chats.pump()
        chat = self.chats.hosted(self.session)
        self.assertTrue(chat.busy)
        answer = self.ask("chat.close", reason="its part is done and saved")
        self.assertEqual((answer["closed"], answer["was_busy"]), (True, True))
        finished = [event for event in self.endpoint.events("finished") if event["session"] == self.session]
        self.assertEqual([event["data"]["status"] for event in finished], ["interrupted"])
        self.assertEqual((finished[0]["data"]["_epoch"], finished[0]["data"]["_generation"]),
                         (chat._epoch, chat._request_generation))
        if chat._worker is not None:
            chat._worker.join(3)
        self.chats.outbox.flush()
        self.assertEqual(len([event for event in self.endpoint.events("finished")
                              if event["session"] == self.session]), 1)   # Its own late end is not sent again.

    def test_an_idle_close_forwards_no_turn_end(self):
        self.open()
        self.ask("chat.send", text="go", attachments=[])
        self.run_turn()
        before = len(self.endpoint.events("finished"))
        self.assertEqual(self.ask("chat.close", reason="parked")["was_busy"], False)
        self.chats.outbox.flush()
        self.assertEqual(len(self.endpoint.events("finished")), before)

    def test_a_retried_close_is_answered_not_hosted_so_the_lead_takes_the_chat_back(self):
        """F3: the first answer was lost; the retry must not be refused forever."""
        self.open()
        self.assertTrue(self.ask("chat.close", reason="park")["closed"])
        self.assertEqual(self.ask("chat.close", reason="retry"), {"ok": True, "closed": False})
        self.assertEqual(self.ask("chat.close", session="not-a-chat-id"), {"ok": True, "closed": False})


class SwitchTests(Fixture):
    def test_a_real_worker_host_lists_its_chats_and_a_switch_under_an_open_chat_is_refused(self):
        """F6: the guard read an attribute only the tests' fake host had."""
        self.assertEqual(self.host.hosted_sessions(), [])
        self.open(options={"approval": "ASK"})
        self.assertEqual(self.host.hosted_sessions(), [self.session])
        loaded = []
        self.bpy.ops.wm = SimpleNamespace(read_homefile=lambda **kwargs: loaded.append(kwargs) or {"FINISHED"})
        with mock.patch("angelo_agent.native_programs.runtime_is_staging", return_value=False, create=True), \
                mock.patch("angelo_agent.worker_main.open_part", lambda bpy, part: None), \
                mock.patch("angelo_agent.view_cache.clear", lambda: None), self.assertRaises(RefusalError):
            worker_switch.switch_part(self.host, str(Path(self.folder.name) / "other.blend"))
        self.assertEqual(loaded, [])
        self.assertIsNotNone(self.chats.hosted(self.session))
        self.host.chats = None   # The switch off: no hosted chats, never a refusal for them.
        self.assertEqual(self.host.hosted_sessions(), [])


class SummaryTests(unittest.TestCase):
    def chat(self, call):
        return SimpleNamespace(pending={call.id: call}, state={"id": "chat"}, redact=str, lock=threading.RLock())

    def test_a_typed_program_card_can_be_drawn_on_the_lead_from_its_summary(self):
        """review-ui 1: the summary carries the scene binding and recovery fields the review needs."""
        for recipe in (False, True):
            with self.subTest(recipe=recipe):
                worker_call = call_for(recipe=recipe)
                worker_call.approval = True
                worker_call.recovery_review, worker_call.recovery_evidence = True, {"missing": ["Cube"]}
                card = worker_chat_events.pending_calls(self.chat(worker_call), worker_consent)[0]
                summary = card["summary"]
                self.assertNotIn("unencoded", summary)
                self.assertEqual(worker_values.decode(summary["precondition"]), worker_call.precondition)
                self.assertEqual(worker_values.decode(summary["recovery_evidence"]), {"missing": ["Cube"]})
                self.assertIs(summary["recovery_review"], True)
                lead_call = ToolCall(card["id"], card["name"], summary["arguments"], None,
                                     program_arguments=summary["program_arguments"])
                lead_call.recovery_review = summary["recovery_review"]
                lead_call.recovery_evidence = worker_values.decode(summary["recovery_evidence"])
                lead_call.precondition = approval_review.prepare_review(
                    lead_call, worker_values.decode(summary["precondition"]))
                rows, _details = drain(lead_call)
                self.assertTrue(rows)   # The card draws.

    def test_a_binding_that_cannot_be_encoded_is_named_never_sent_half(self):
        call = SimpleNamespace(id="c", name="angelo_scene_program", arguments={}, document=None, approval=True,
                               executing=False, authorized=False, automatic=False, approved=False, revoked=None,
                               expires=0.0, usage_label="", precondition=object(), invocation_digest="d",
                               program_arguments=None)
        summary = worker_chat_events.pending_calls(self.chat(call), worker_consent)[0]["summary"]
        self.assertEqual(summary["unencoded"], ["precondition"])
        self.assertNotIn("precondition", summary)


class TurnFactsTests(Fixture):
    def test_a_hosted_turn_sends_its_rounds_and_clock_and_a_mirror_reads_them_as_its_own(self):
        """review-ui 11: ``worker_facts`` reads ``_turn_requests`` and ``timings`` from the lead's mirror."""
        self.open()
        FakeProvider.tools, FakeProvider.gate = (), threading.Event()
        self.ask("chat.send", text="go", attachments=[])
        chat = self.chats.hosted(self.session)
        chat._turn_requests = 3   # Three model rounds so far (``ledger_events.record_round``).
        self.chats.pump()
        self.chats.outbox.flush()
        self.assertEqual(self.endpoint.events("turn_facts")[-1]["data"], {"rounds": 3})
        FakeProvider.gate.set()
        self.run_turn()
        facts = self.endpoint.events("turn_facts")[-1]["data"]
        self.assertEqual(facts["rounds"], 3)
        self.assertEqual(facts["total_ms"], chat.timings["total_ms"])
        mirror = SimpleNamespace(_turn_requests=0, timings={})
        worker_chat_events.apply_turn_facts(mirror, facts)
        ended = mirror.timings["started"] + mirror.timings["total_ms"] / 1000.0
        self.assertEqual(mirror._turn_requests, 3)
        self.assertLess(abs(time.monotonic() - ended), 1.0)   # The turn just ended, on the lead's own clock.
        for bad in ({"rounds": -1, "total_ms": "x"}, {"total_ms": float("nan"), "ended_at": 1.0}, None):
            worker_chat_events.apply_turn_facts(mirror, bad)
        self.assertEqual(mirror._turn_requests, 3)


def load_tests(loader, standard_tests, pattern):
    """This file's own cases only: the imported fixtures' tests run in their own files."""
    del standard_tests, pattern
    mine = (HoldTests, RefusalTests, CloseTests, SwitchTests, SummaryTests, TurnFactsTests)
    suite = unittest.TestSuite()
    for case in mine:
        for name in loader.getTestCaseNames(case):
            if name in case.__dict__:
                suite.addTest(case(name))
    return suite


if __name__ == "__main__":
    unittest.main()


class HostedChatIsNeverEndedTests(Fixture):
    """FE D2: a chat this worker hosts works on the worker's own document, though the lead's worker-chats registry in
    the shared store folder lists it; only a chat no process hosts here is an ended worker's."""

    def test_a_hosted_chat_listed_in_the_shared_registry_runs_here(self):
        from angelo_agent import controller_external, worker_routing
        self.assertTrue(self.open()["ok"])
        chat = self.chats.hosted(self.session)
        listed = {self.session: str(Path(self.folder.name) / "p.blend")}
        with mock.patch.object(worker_routing, "registry", lambda directory: listed):
            self.assertEqual(controller_external.bound_target(chat, "executor", "document"), ("executor", "document"))
            chat.chat_host = None   # Not (or no longer) hosted by this process: refused as its worker's ended.
            executor, _document = controller_external.bound_target(chat, "executor", "document")
        self.assertNotEqual(executor, "executor")
