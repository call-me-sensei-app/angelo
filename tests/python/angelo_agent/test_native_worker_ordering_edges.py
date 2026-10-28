# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ordering edges of a worker-hosted chat (design A′, round-3 concurrency review), each on the real modules:

* a hand-back (``CLOSED``, ``RECOVERED``) queued behind a relaunch's ``REOPEN`` changes nothing of the new open
  (review N1: it wiped the new open's cards), while one of the current open still ends its turn;
* a parked chat marked as needing approval takes its consent hook when its held Send goes, after the reopen
  registered it, so its first message reaches the worker (N2: it was always refused);
* every Stop is ordered: the lead stamps one that carries no order, and the worker records an unordered Stop after
  everything that reached it, so a Send it overtook is refused (R1 variant);
* a consent change made while any ``chat.open`` is unanswered waits for it, the switch's inline open included (R2
  variant);
* a stop that raises before its ended path still hands its chats back, once, and only after its process ended.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_worker_mirror_park import ParkedMirrorFixture  # noqa: E402
from angelo_agent import (worker_chat_arrival, worker_chat_lifecycle as lifecycle, worker_chat_wiring,  # noqa: E402
                          worker_consent_lead as lead, worker_journal_owner, worker_mirror_events as events,
                          worker_mirror_opens, worker_process)
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.worker_mirror import EventRouter, MirrorController  # noqa: E402


def until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def card(open_id, call_id="c1"):
    return {"open_id": open_id, "calls": [{"id": call_id, "name": "angelo_create_cube", "digest": "d" * 64,
                                           "needs_person": True, "summary": {"approval": True}}]}


class StaleHandBack(unittest.TestCase):
    """N1, the review's interleaving: the stop's ``_drop`` hands the chat back; while ``worker_ended`` (or the close's
    ``close_answered``) regains the file, a relaunch's ``prepare`` announces the next open and its worker sends a
    card; the hand-back's event is queued after both."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = str(uuid.uuid4())
        Controller(temporary.name, session_id=self.session).save()
        self.mirror = MirrorController(temporary.name, self.session, lambda *a, **k: {"ok": True})
        self.first = worker_mirror_opens.announce(self.mirror)
        self.mirror.pump_events()
        self.mirror.begin_turn()   # The first open's turn, running when its worker went.

    def relaunch(self):
        """The relaunch's ``prepare`` in the window: its open announced, its card sent."""
        self.second = worker_mirror_opens.announce(self.mirror)
        events.put(self.mirror, events.PENDING, card(self.second))

    def hand_back(self, closed, relaunch):
        worker = SimpleNamespace(chat_router=EventRouter(), ended_on_its_own=None)
        state = dict(self.mirror.state, title="as of the crash")

        def ended(session, directory, pending=()):
            if relaunch:
                self.relaunch()
            return worker_journal_owner.Recovered(state, [], [], True)
        with mock.patch.dict(worker_chat_wiring._chats, {self.session: (self.mirror, worker)}), \
                mock.patch.object(worker_journal_owner, "hosted", lambda session: True), \
                mock.patch.object(worker_journal_owner, "worker_ended", ended), \
                mock.patch.object(worker_journal_owner, "close_answered",
                                  lambda session: self.relaunch() if relaunch else None):
            worker_chat_wiring.handed_back(self.session, closed)
        self.mirror.pump_events()

    def test_a_recovered_behind_the_next_open_keeps_its_cards_and_state(self):
        self.hand_back(closed=False, relaunch=True)
        self.assertEqual(self.mirror.open_id, self.second)
        self.assertIn("c1", self.mirror.pending)   # The new open's card is still there to approve.
        self.assertNotEqual(self.mirror.state.get("title"), "as of the crash")
        self.assertTrue(self.mirror.busy)   # A turn shown as running is the new open's.

    def test_a_closed_behind_the_next_open_changes_nothing(self):
        self.hand_back(closed=True, relaunch=True)
        self.assertIn("c1", self.mirror.pending)
        self.assertTrue(self.mirror.busy)

    def test_the_current_opens_hand_back_still_ends_its_turn(self):
        events.put(self.mirror, events.PENDING, card(self.first))
        self.mirror.pump_events()
        self.hand_back(closed=False, relaunch=False)
        self.assertEqual((self.mirror.busy, self.mirror.pending), (False, {}))
        self.assertEqual(self.mirror.state.get("title"), "as of the crash")

    def test_an_unnamed_hand_back_still_ends_the_turn(self):
        events.put(self.mirror, events.RECOVERED, {"state": None, "finished": None})   # Fail closed.
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy)


class ParkedNeedsApprovalFirstMessage(ParkedMirrorFixture):
    """N2: the person's first message to a parked chat marked as needing approval reaches the worker once its
    reopen registered the chat and the worker acknowledged its grants again."""

    def setUp(self):
        super().setUp()
        self.consents = []

        def send(session, request, timeout):
            self.consents.append(request)
            return {"ok": True, "epoch": request["epoch"]}
        lead.bind(send=send, spawn=lambda run: run(), connection=lambda controller: True)
        self.addCleanup(lead.reset)

    def opened(self, controller, worker, resume, defer=False):
        super().opened(controller, worker, resume, defer)
        lead.register(controller)   # As ``worker_chat_wiring.prepare`` does before the open goes.

    def test_the_first_message_after_the_relaunch_is_sent(self):
        mirror = self.mirror()
        lead._needs.add(mirror.state["id"])
        mirror.send("Make it blue")
        self.relaunched()
        self.assertEqual(self.sent(), [])
        self.answer_open()
        self.assertEqual(self.sent(), [("chat.send", "Make it blue")])
        self.assertEqual([request["op"] for request in self.consents], ["chat.consent"])   # Re-sent, acknowledged.
        self.assertFalse(lead.needs_approval(mirror.state["id"]))

    def test_unacknowledged_grants_still_refuse_it(self):
        lead.bind(send=lambda session, request, timeout: {"error": "No answer", "timed_out": True})
        mirror = self.mirror()
        lead._needs.add(mirror.state["id"])
        mirror.send("Make it blue")
        self.relaunched()
        self.answer_open()
        self.assertEqual(self.sent(), [])   # Fail closed: nothing goes without the worker's acknowledgement.


class EveryStopOrdered(unittest.TestCase):
    """R1 variant."""

    def chat(self, session):
        return SimpleNamespace(state={"id": session}, lead_seq=None)

    def test_the_worker_refuses_a_send_an_unordered_stop_overtook(self):
        arrivals = worker_chat_arrival.Arrivals(SimpleNamespace(hosted=lambda session: None))
        self.assertFalse(arrivals.arrived(1, {"op": "chat.send", "session": "s", "lead_seq": 10}))   # Queued.
        self.assertFalse(arrivals.arrived(2, {"op": "chat.stop", "session": "s"}))   # No order.
        arrivals.handled(2)
        chat, started = self.chat("s"), []
        for request in ({"op": "chat.send", "lead_seq": 10}, {"op": "chat.send"}):
            with self.assertRaises(RefusalError):
                arrivals.send(chat, request, lambda: started.append(1))
        arrivals.send(chat, {"op": "chat.send", "lead_seq": 11}, lambda: started.append(1))   # A later one runs.
        self.assertEqual(started, [1])

    def test_an_unordered_stop_before_anything_arrived_still_refuses_an_unordered_send(self):
        arrivals = worker_chat_arrival.Arrivals(SimpleNamespace(hosted=lambda session: None))
        arrivals.arrived(1, {"op": "chat.stop", "session": "s"})
        arrivals.handled(1)
        with self.assertRaises(RefusalError):
            arrivals.send(self.chat("s"), {"op": "chat.send"}, lambda: None)

    def test_the_lead_stamps_every_stop_it_sends(self):
        frames = []
        worker = worker_process.WorkerProcess(Path(tempfile.gettempdir()) / "part.blend")
        worker.process = SimpleNamespace(poll=lambda: None)
        worker.client = SimpleNamespace(ended=threading.Event(),
                                        request=lambda frame, timeout: frames.append(frame) or {"ok": True})
        worker.request("chat.stop", 1, session="s", reason="")
        worker.request("chat.stop", 1, session="s", reason="", lead_seq=7)
        worker.request("chat.options", 1, session="s")
        self.assertIs(type(frames[0]["lead_seq"]), int)
        self.assertEqual(frames[1]["lead_seq"], 7)
        self.assertNotIn("lead_seq", frames[2])


class ConsentHeldDuringEveryOpen(unittest.TestCase):
    """R2 variant: a switch's move opens inline (``open_chat``), never in ``_opening``."""

    def test_a_change_during_an_inline_open_waits_for_its_answer(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        session = str(uuid.uuid4())
        Controller(temporary.name, session_id=session).save()
        mirror = MirrorController(temporary.name, session, None)
        mirror._pins["approval"] = "AUTO_BOUNDED"   # Angelo's own confirmed approval (worker_mirror_pins).
        sent, during = [], []
        lead.bind(send=lambda s, request, timeout: sent.append(request) or {"ok": True, "epoch": request["epoch"]},
                  spawn=lambda run: run(), connection=lambda controller: True)
        self.addCleanup(lead.reset)

        def answer(session_id, op, worker, timeout, **fields):   # The main thread's tick, while the open waits.
            mirror._pins["approval"] = "ASK"   # The person's narrowing, as the lead confirmed it.
            during.extend(lead.sync())
            return {"ok": True}
        with mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"}), \
                mock.patch.dict(lifecycle._hosted, {}), mock.patch.dict(lifecycle._unanswered, {}), \
                mock.patch.object(lifecycle, "_prepare", lambda controller, worker: lead.register(controller)), \
                mock.patch.object(lifecycle, "_send", answer):
            lifecycle.open_chat(mirror, SimpleNamespace(binary=None), resume=True)
            self.assertEqual((during, sent), ([], []))   # Nothing reached the worker before its open.
            self.assertFalse(lifecycle.answering(session))
            self.assertEqual([record.grants for record in lead.sync()], [{"auto": False}])   # Then it goes.
        self.assertEqual([request["op"] for request in sent], ["chat.consent"])

    def test_a_failed_inline_open_holds_nothing_afterwards(self):
        session = str(uuid.uuid4())

        def refuse(*_args, **_kwargs):
            raise lifecycle.ChatOpFailed("chat.open", session, "refused")
        controller = SimpleNamespace(state={"id": session, "options": {}}, store=SimpleNamespace(directory="/x"))
        with mock.patch.dict(lifecycle._hosted, {}), mock.patch.dict(lifecycle._unanswered, {}), \
                mock.patch.object(lifecycle, "_prepare", lambda controller, worker: {}), \
                mock.patch.object(lifecycle, "_send", refuse):
            with self.assertRaises(lifecycle.ChatOpFailed):
                lifecycle.open_chat(controller, SimpleNamespace(binary=None), resume=True)
            self.assertFalse(lifecycle.answering(session))
            with mock.patch.object(lifecycle, "_prepare", refuse), self.assertRaises(lifecycle.ChatOpFailed):
                lifecycle.open_chat(controller, SimpleNamespace(binary=None), resume=True)
            self.assertFalse(lifecycle.answering(session))


class LingeringProcess:
    """A worker process a kill did not end within the stop's grace (``wait(grace)`` times out)."""

    def __init__(self, ended_after_kill=False):
        self.code, self.killed, self.reaped = None, False, threading.Event()
        self.ended_after_kill = ended_after_kill

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if timeout is None:
            self.reaped.wait(10)
            return self.code
        if self.killed and self.ended_after_kill:
            self.code = -9   # Reaped just after its wait gave up.
        raise subprocess.TimeoutExpired("worker", timeout)


class StopThatRaises(unittest.TestCase):
    def setUp(self):
        self.handoffs = []
        for patcher in (mock.patch.object(lifecycle, "_hosted", {}),
                        mock.patch.object(lifecycle, "HANDOFF", [lambda session, closed: self.handoffs.append(
                            (session, closed))])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def worker(self, process):
        worker = worker_process.WorkerProcess(Path(tempfile.gettempdir()) / "part.blend")
        worker.process = process
        lifecycle._hosted["chat"] = worker
        return worker

    def test_its_chats_are_handed_back_once_its_process_has_ended(self):
        process = LingeringProcess()
        worker = self.worker(process)
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.stop(0.01)
        self.assertEqual(self.handoffs, [])   # Still running: its chats' files stay its own (C7).
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.stop(0.01)   # A second stop while it lingers hands back nothing either.
        process.code = -9
        process.reaped.set()
        self.assertTrue(until(lambda: worker._stopped is process))
        self.assertEqual(self.handoffs, [("chat", False)])
        self.assertTrue(worker.ended.is_set())
        worker.stop(0.01)
        time.sleep(0.05)
        self.assertEqual(self.handoffs, [("chat", False)])   # Exactly once.

    def test_one_that_ended_as_its_wait_gave_up_is_handed_back_at_once(self):
        worker = self.worker(LingeringProcess(ended_after_kill=True))
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.stop(0.01)
        self.assertEqual(self.handoffs, [("chat", False)])
        worker.stop(0.01)
        self.assertEqual(self.handoffs, [("chat", False)])


if __name__ == "__main__":
    unittest.main()
