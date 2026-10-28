# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's mirror across its opens and closes, and the answers its cards show (A′ fix loop FC1). No Blender.

Regression tests for the break-it reviews of 086452d1bdb (``release-1.0.6.noindex/review-concurrency``, F9 and F2;
``review-ui``, items 9 and 10):

* C9: a chat reopened in a fresh worker ``Controller`` (park relaunch, ``switch_part`` move, crash resume) counts its
  requests from zero again; its mirror compares the order of requests within one open (``open_id``) only.
* C2: a busy chat whose ``chat.close`` was answered ends its turn on the lead as after a crash; a park that closed
  its chat parks even when a message reached it before the close.
* U9: Stop and a turn's end clear the cards; Approve and Decline show at once; a worker's refusal is shown.
* U10: a notice on a chat handed back is saved through the chat's own store.

The worker side is faked: frames are what ``worker_chat_events`` sends, each tagged with its open's ``open_id``.
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (sessions, worker_chat_lifecycle as lifecycle, worker_chat_wiring as wiring,  # noqa: E402
                          worker_consent_lead, worker_journal_owner, worker_mirror_events as events,
                          worker_mirror_opens as opens, worker_queue)
from angelo_agent import worker_reasons  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.worker_mirror import EventRouter  # noqa: E402
import test_native_worker_chat_lifecycle as lifecycle_tests  # noqa: E402
import test_native_worker_queue as queue_base  # noqa: E402
from test_native_worker_mirror import Fixture as MirrorFixture  # noqa: E402


class Worker:
    """A worker process as the wiring sees it: its router, an answering channel and a process that runs."""

    def __init__(self):
        self.chat_router, self.runtime, self.ended = EventRouter(), None, False
        self.process = SimpleNamespace(pid=1, poll=lambda: 0 if self.ended else None)
        self.requests = []

    def request(self, op, timeout, **fields):
        self.requests.append((op, fields))
        return {"ok": True}


class Fixture(MirrorFixture):
    """The mirror fixture with the wiring's tables and the journal's hand-off table empty for each test."""

    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(wiring, "_chats", {}), mock.patch.dict(worker_journal_owner._hosts, {}),
                        mock.patch.dict(sessions.HOSTED, {})):
            patcher.start()
            self.addCleanup(patcher.stop)
        worker_consent_lead.reset()
        worker_consent_lead.bind(send=lambda session, request, timeout: {"ok": True}, kill=lambda session: None,
                                 spawn=lambda function: function())
        self.addCleanup(worker_consent_lead.reset)
        self.router.close(self.session)   # Frames reach the mirror only through a worker opened on it.

    def open_on(self, worker):
        """``chat.open`` prepared on ``worker``: its frames carry the id the lead announced (C9), named here "open-a",
        "open-b"... in the order of the opens (``tagged``)."""
        wiring.prepare(self.mirror, worker)
        labels = self.__dict__.setdefault("open_ids", {})
        labels["open-" + "abcdefgh"[len(labels)]] = self.mirror.announced_open

    def close_answered(self):
        wiring.handed_back(self.session, closed=True)

    def tagged(self, worker, opened, kind, data, pair=None):
        data = dict(data, **({"_epoch": pair[0], "_generation": pair[1]} if pair else {}))
        opened = self.__dict__.get("open_ids", {}).get(opened, opened)   # An open never announced keeps its own.
        worker.chat_router({"session": self.session, "open_id": opened, "kind": kind, "data": data, "t": 0.0})

    def turn(self, worker, opened, generation, text):
        """A whole turn of the worker's chat: the person's row, a reply, its end."""
        pair = (0, generation)
        self.tagged(worker, opened, "message", {"role": "user", "text": text}, pair)
        self.tagged(worker, opened, "delta", {"item": "r%d" % generation, "text": "Reply to " + text}, pair)
        self.tagged(worker, opened, "finished", {"status": "completed"}, pair)

    def three_turns(self, worker, opened):
        for generation, text in enumerate(("one", "two", "three"), start=1):
            self.mirror.send(text)
            self.turn(worker, opened, generation, text)
            self.mirror.pump_events()
            self.assertFalse(self.mirror.busy)

    def assert_new_turn_shown(self, worker, opened):
        self.mirror.send("four")
        self.turn(worker, opened, 1, "four")   # A fresh worker Controller counts from (0, 1) again.
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy, "the resumed turn's end must be shown")
        self.assertEqual(self.mirror.status, "Completed")
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "Reply to four")


class ReopenTests(Fixture):
    """C9 (review-concurrency F9)."""

    def test_a_park_relaunch_in_a_new_worker_shows_the_resumed_turn(self):
        first = Worker()
        self.open_on(first)
        self.three_turns(first, "open-a")
        self.close_answered()             # The park closes the chat in its worker.
        second = Worker()
        self.open_on(second)              # Its relaunch opens it in a new worker, resumed.
        self.assert_new_turn_shown(second, "open-b")

    def test_a_switch_part_move_drops_the_earlier_opens_late_frames(self):
        worker = Worker()
        self.open_on(worker)
        self.three_turns(worker, "open-a")
        self.close_answered()
        self.open_on(worker)              # The same worker, on its new part.
        self.mirror.send("four")
        self.tagged(worker, "open-b", "message", {"role": "user", "text": "four"}, (0, 1))
        self.tagged(worker, "open-a", "pending", {"calls": [{"id": "old", "name": "angelo_create_cube",
                                                            "digest": "d" * 64, "needs_person": True, "expires": 60,
                                                            "summary": {"arguments": {}}}]})
        self.tagged(worker, "open-a", "delta", {"item": "x", "text": "stale"}, (0, 9))
        self.tagged(worker, "open-b", "delta", {"item": "r1", "text": "Reply to four"}, (0, 1))
        self.tagged(worker, "open-b", "finished", {"status": "completed"}, (0, 1))
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "Reply to four")
        self.assertEqual(self.mirror.pending, {})
        self.assertNotIn("stale", [row.get("text") for row in self.mirror.state["messages"]])

    def test_a_crash_resume_shows_the_resumed_turn(self):
        first = Worker()
        self.open_on(first)
        self.three_turns(first, "open-a")
        self.mirror.send("lost")
        self.tagged(first, "open-a", "message", {"role": "user", "text": "lost"}, (0, 4))
        first.ended = True
        wiring.handed_back(self.session, closed=False)   # Its process ended: the file reopened as after a crash.
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)
        second = Worker()
        self.open_on(second)
        self.assert_new_turn_shown(second, "open-b")

    def test_a_frame_of_an_open_never_announced_is_dropped(self):
        worker = Worker()
        self.open_on(worker)
        self.three_turns(worker, "open-a")
        self.mirror.send("four")
        self.turn(worker, "open-x", 1, "four")   # No chat.open of it was prepared here.
        self.mirror.pump_events()
        self.assertTrue(self.mirror.busy)
        self.assertNotIn("Reply to four", [row.get("text") for row in self.mirror.state["messages"]])

    def test_a_worker_that_tags_no_frame_is_followed_one_open_at_a_time(self):
        """Only an open announced with no id (``REOPEN`` without one) follows untagged frames."""
        from angelo_agent import worker_mirror_events as events
        worker = Worker()
        events.put(self.mirror, events.REOPEN, {})
        worker.chat_router.open(self.mirror)
        self.mirror.send_op = wiring.send_op(worker)
        self.three_turns(worker, None)
        events.put(self.mirror, events.REOPEN, {})
        self.assert_new_turn_shown(worker, None)

    def test_an_untagged_frame_after_an_announced_open_is_dropped(self):
        worker = Worker()
        self.open_on(worker)              # Its open carries the lead's id: every frame of it is tagged (C9).
        self.mirror.send("one")
        self.turn(worker, None, 1, "one")
        self.mirror.pump_events()
        self.assertTrue(self.mirror.busy)
        self.assertNotIn("Reply to one", [row.get("text") for row in self.mirror.state["messages"]])


class CloseTests(Fixture):
    """C2, lead side (review-concurrency F2/F2b), and U10."""

    def test_an_answered_close_of_a_busy_chat_ends_its_turn_and_says_so_in_its_file(self):
        worker = Worker()
        self.open_on(worker)
        self.mirror.send("long")
        self.tagged(worker, "open-a", "message", {"role": "user", "text": "long"}, (0, 1))
        self.close_answered()             # The worker forwarded no end before its answer.
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)
        self.assertEqual(self.mirror.status, "Interrupted")
        saved = [row.get("text") for row in self.loaded().state["messages"]]
        self.assertIn(opens.CLOSED_MID_TURN, saved)
        self.assertEqual(self.mirror.state["messages"][-1]["text"], opens.CLOSED_MID_TURN)

    def test_a_turn_whose_end_came_before_the_answer_changes_nothing(self):
        worker = Worker()
        self.open_on(worker)
        self.mirror.send("short")
        self.turn(worker, "open-a", 1, "short")
        self.close_answered()
        self.mirror.pump_events()
        self.assertEqual(self.mirror.status, "Completed")
        self.assertNotIn(opens.CLOSED_MID_TURN, [row.get("text") for row in self.loaded().state["messages"]])

    def test_a_chat_handed_to_a_worker_again_is_never_written_here(self):
        worker = Worker()
        self.open_on(worker)
        self.mirror.send("long")
        self.close_answered()
        self.open_on(worker)              # Opened again before the lead's next pump.
        before = Path(self.loaded().store.path).read_bytes()
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)
        self.assertEqual(Path(self.loaded().store.path).read_bytes(), before)

    def test_a_notice_on_a_chat_handed_back_is_saved_through_its_store(self):
        worker = Worker()
        self.open_on(worker)
        self.close_answered()
        self.mirror.pump_events()
        self.mirror._notice({"role": "system", "text": "The part was merged"})   # emit's thread, run here.
        self.mirror.pump_events()
        self.assertIn("The part was merged", [row.get("text") for row in self.loaded().state["messages"]])
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "The part was merged")

    def test_a_notice_while_a_worker_writes_the_file_is_shown_only(self):
        worker = Worker()
        self.open_on(worker)
        self.mirror.send_op = lambda *args, **fields: {"error": "busy"}
        self.mirror._notice({"role": "system", "text": "Shown here"})
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "Shown here")
        self.assertNotIn("Shown here", [row.get("text") for row in self.loaded().state["messages"]])


class ParkRaceTests(queue_base.Fixture, lifecycle_tests.Base):
    """C2: the park decided, a message reached the chat, then the park's close stopped that turn."""

    def setUp(self):
        lifecycle_tests.Base.setUp(self)
        queue_base.Fixture.setUp(self)

    def test_a_park_whose_close_was_answered_parks_even_when_the_turn_changed(self):
        process = queue_base.Process(save={"value": None})
        original = process.request
        process.request = lambda op, timeout, **fields: (process.requests.append(dict(fields, op=op)), {"ok": True})[1] \
            if op == "chat.close" else original(op, timeout, **fields)
        chat, process = self.running("w1", process)
        self.host("w1", process)
        gate = __import__("threading").Event()
        with mock.patch.object(lifecycle, "close_chat", lambda session, reason: gate.wait(1) or True):
            worker_queue.tick()
            chat._request_generation = getattr(chat, "_request_generation", 0) + 1   # The person's message.
            gate.set()
            self.assertTrue(worker_queue._parking["w1"]["done"].wait(1))
        worker_queue._finish_parks(self.ui)
        self.assertIn("w1", worker_queue.parked)
        self.assertNotIn("w1", worker_queue._held)


class CardTests(Fixture):
    """U9 (review-ui item 9)."""

    CARD = {"id": "c1", "name": "angelo_create_cube", "digest": "f" * 64, "needs_person": True, "expires": 120,
            "summary": {"arguments": {"name": "Seat"}, "approval": True}}

    def drawn(self, worker):
        self.mirror.send("Go")
        self.tagged(worker, "open-a", "pending", {"calls": [dict(self.CARD)]})
        self.mirror.pump_events()
        self.assertTrue(self.mirror.pending["c1"].approval)
        worker_consent_lead.drew(self.mirror, "c1")   # The card is drawn: Approve sends what it showed (S3).

    def test_stop_and_a_turns_end_clear_the_cards(self):
        worker = Worker()
        self.open_on(worker)
        self.drawn(worker)
        self.mirror.stop()
        self.assertEqual(self.mirror.pending, {})
        self.tagged(worker, "open-a", "pending", {"calls": [dict(self.CARD)]})   # Sent before the worker stopped.
        self.mirror.pump_events()
        self.assertEqual(self.mirror.pending, {})
        self.tagged(worker, "open-a", "finished", {"status": "cancelled"}, (0, 1))
        self.mirror.pump_events()
        self.assertEqual(self.mirror.pending, {})

    def test_a_turns_end_clears_the_cards(self):
        worker = Worker()
        self.open_on(worker)
        self.drawn(worker)
        self.tagged(worker, "open-a", "finished", {"status": "completed"}, (0, 1))
        self.mirror.pump_events()
        self.assertEqual(self.mirror.pending, {})

    def test_approve_shows_at_once_and_a_refusal_puts_the_card_back_and_says_why(self):
        worker = Worker()
        self.open_on(worker)
        refusal = {"error": {"type": "RefusalError", "message": "The chat is held for a merge", "code": "WRITER_BUSY"}}
        worker_consent_lead.bind(send=lambda session, request, timeout: refusal)
        self.drawn(worker)
        self.assertIsNone(worker_consent_lead.approve(self.mirror, "c1"))
        self.assertFalse(self.mirror.pending["c1"].approval)   # The card gives way at once.
        self.assertTrue(self.mirror.pending["c1"].approved)
        self.tagged(worker, "open-a", "pending", {"calls": [dict(self.CARD)]})   # The worker re-sends its cards.
        self.mirror.pump_events()
        self.assertTrue(self.mirror.pending["c1"].approval)
        self.assertFalse(self.mirror.pending["c1"].approved)
        self.assertEqual(self.mirror.state["messages"][-1]["role"], "error")
        self.assertIn(worker_reasons.BUSY, self.mirror.state["messages"][-1]["text"])   # Its code's plain reason.
        self.assertNotIn("held for a merge", self.mirror.state["messages"][-1]["text"])   # Its words: the log only.

    def test_an_accepted_approve_keeps_the_card_away(self):
        worker = Worker()
        self.open_on(worker)
        self.drawn(worker)
        worker_consent_lead.approve(self.mirror, "c1")
        self.tagged(worker, "open-a", "pending", {"calls": [dict(self.CARD)]})   # A pending set sent before it ran.
        self.mirror.pump_events()
        self.assertFalse(self.mirror.pending["c1"].approval)
        self.assertNotEqual(self.mirror.state["messages"][-1:] and self.mirror.state["messages"][-1]["role"], "error")

    def test_decline_removes_the_card_at_once_and_a_refusal_is_shown(self):
        worker = Worker()
        self.open_on(worker)
        self.drawn(worker)
        worker_consent_lead.bind(send=lambda session, request, timeout: {"error": "no answer within 2 s"})
        worker_consent_lead.decline(self.mirror, "c1")
        self.assertNotIn("c1", self.mirror.pending)
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"][-1]["role"], "error")
        self.assertIn("did not apply your Decline", self.mirror.state["messages"][-1]["text"])
        self.assertNotIn("no answer within 2 s", self.mirror.state["messages"][-1]["text"])   # The log only.


if __name__ == "__main__":
    unittest.main()
