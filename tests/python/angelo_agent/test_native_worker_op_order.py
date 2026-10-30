# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's order of a worker chat's ops, and one end per turn (design A′, C2, C5, C9; re-review R1, F2, F9).

* A Stop is never overtaken by the Send it stops: the lane withdraws a Send still in its ``before`` hook, and the
  worker refuses a Send ordered before a Stop it already received, whichever frame came first (``lead_seq``).
* A close (or a crash) ends only the turn it found, never a Send made to the reopened chat before the next pump.
* After a reopen the mirror adopts only the open id the lead announced: a late frame of the earlier open never wins.
* One turn, one end: a second end of the same turn changes nothing.

Each interleaving is forced deterministically (gates, inline frames). No Blender.
"""

import threading
import time
import unittest
from types import SimpleNamespace

import test_native_worker_chat_host as host_tests  # noqa: F401 - puts the add-on on sys.path first.
from test_native_worker_chat_host import FakeProvider, Fixture as WorkerFixture
from test_native_worker_mirror_opens import Fixture as MirrorFixture, Worker
from angelo_agent import worker_chat_wiring as wiring, worker_mirror_events as events, worker_mirror_ops as ops
from angelo_agent import worker_values
from angelo_agent.refusal_codes import RefusalError


def wait_for(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.001)
    return predicate()


class LaneOrder(unittest.TestCase):
    """R1, lead side: real lane threads."""

    def setUp(self):
        ops.reset()
        self.addCleanup(ops.reset)
        self.sent = []
        self.mirror = SimpleNamespace(state={"id": "s"}, send_op=lambda *a, **k: self.sent.append((a[1], k)) or {
            "ok": True})

    def test_a_stop_while_the_send_waits_in_its_before_hook_withdraws_it(self):
        gate, lane = threading.Event(), ops.Lane()
        request = ops.Request(self.mirror, "chat.send", "send", {})
        request.before = lambda: gate.wait(5)   # ``resume_later``: the consent acknowledgement it waits for.
        lane.submit(request)
        self.assertTrue(wait_for(lambda: request.state == "preparing"))
        self.assertTrue(lane.withdraw(request))   # The person's Stop: nothing has been written yet.
        gate.set()
        self.assertTrue(wait_for(lambda: not lane.running))
        self.assertEqual(self.sent, [])           # Withdrawn: never sent after the Stop.
        self.assertEqual(request.state, "withdrawn")

    def test_once_its_frame_is_written_a_stop_no_longer_withdraws_it_and_marks_it_stopped(self):
        gate, lane = threading.Event(), ops.Lane()
        self.mirror.send_op = lambda *a, **k: gate.wait(5) and {"ok": True}
        request = ops.Request(self.mirror, "chat.send", "send", {})
        lane.submit(request)
        self.assertTrue(wait_for(lambda: request.state == "sent"))
        self.assertFalse(lane.withdraw(request))
        gate.set()
        self.assertTrue(wait_for(lambda: not lane.running))
        ops.reset()

    def test_every_op_carries_the_persons_order_and_a_later_stop_orders_after_it(self):
        lane = ops.Lane()
        request = ops.Request(self.mirror, "chat.send", "send", {"text": "go"})
        lane.submit(request)
        self.assertTrue(wait_for(lambda: self.sent))
        self.assertEqual(self.sent[0][1]["lead_seq"], request.lead_seq)
        self.assertGreater(ops.order(), request.lead_seq)


class WorkerOrder(WorkerFixture):
    """R1, worker side: the Stop's frame and the Send's reach the worker in either order."""

    def setUp(self):
        super().setUp()
        self.open()
        FakeProvider.tools, FakeProvider.gate = (), threading.Event()
        self.addCleanup(FakeProvider.gate.set)

    def stop_arrives(self, seq, lead_seq, handled=True):
        request = {"op": "chat.stop", "session": self.session, "reason": ""}
        if lead_seq is not None:
            request["lead_seq"] = lead_seq
        self.assertFalse(self.chats.arrivals.arrived(seq, request))
        if handled:
            self.chats.handle(request)
            self.chats.arrivals.handled(seq)

    def test_a_send_ordered_before_a_stop_already_handled_is_refused_never_run(self):
        self.stop_arrives(11, 10)                 # The Stop overtook the Send the person made before it.
        with self.assertRaises(RefusalError) as refused:
            self.ask("chat.send", text="go", attachments=[], lead_seq=5)
        self.assertEqual(refused.exception.code, "CANCELLED")
        self.assertFalse(self.chats.hosted(self.session).busy)

    def test_a_send_ordered_before_a_stop_still_queued_is_refused_never_run(self):
        self.stop_arrives(11, 10, handled=False)  # The Stop arrived while the Send waited in the loop's queue.
        with self.assertRaises(RefusalError):
            self.ask("chat.send", text="go", attachments=[], lead_seq=5)
        self.assertFalse(self.chats.hosted(self.session).busy)
        self.chats.arrivals.handled(11)

    def test_a_send_with_no_order_after_an_ordered_stop_is_refused(self):
        self.stop_arrives(11, 10)
        with self.assertRaises(RefusalError):
            self.ask("chat.send", text="go", attachments=[])   # Fail closed: its place cannot be known.

    def test_a_send_ordered_after_a_stop_runs_unstopped(self):
        self.stop_arrives(11, 10)
        self.ask("chat.send", text="go", attachments=[], lead_seq=20)
        chat = self.chats.hosted(self.session)
        self.assertTrue(chat.busy and not chat.cancelled)
        FakeProvider.gate.set()
        self.run_turn()

    def test_a_stop_ordered_before_the_running_turns_send_stops_nothing_of_it(self):
        self.ask("chat.send", text="go", attachments=[], lead_seq=20)
        chat = self.chats.hosted(self.session)
        self.stop_arrives(11, 10, handled=False)  # Made before this Send, its frame came after.
        self.assertFalse(chat.cancelled)
        self.assertFalse(chat.stop_arrived())     # Its calls start: the barrier is not this turn's.
        self.assertEqual(self.ask("chat.stop", reason="", lead_seq=10), {"ok": True, "stopped": False})
        self.chats.arrivals.handled(11)
        self.assertTrue(chat.busy and not chat.cancelled)
        FakeProvider.gate.set()
        self.run_turn()

    def test_a_stop_ordered_after_the_running_turns_send_stops_it_at_its_arrival(self):
        self.ask("chat.send", text="go", attachments=[], lead_seq=20)
        chat = self.chats.hosted(self.session)
        self.stop_arrives(11, 30, handled=False)
        self.assertTrue(chat.cancelled and chat.stop_arrived())
        self.chats.arrivals.handled(11)
        FakeProvider.gate.set()

    def test_a_refused_send_leaves_the_running_turns_order(self):
        self.ask("chat.send", text="go", attachments=[], lead_seq=20)
        with self.assertRaises(RuntimeError):     # "An agent turn is already running"
            self.ask("chat.send", text="again", attachments=[], lead_seq=40)
        chat = self.chats.hosted(self.session)
        self.stop_arrives(11, 30, handled=False)  # After the running turn's Send: it stops that turn.
        self.assertTrue(chat.cancelled)
        self.chats.arrivals.handled(11)
        FakeProvider.gate.set()


class MirrorStopOfASentSend(MirrorFixture):
    """R1 end to end on the lead: the worker refused the Send because the person's Stop came first."""

    def test_it_comes_back_where_the_person_types_with_no_error_row(self):
        gate = threading.Event()
        encoded = worker_values.encode_error(RefusalError("stopped first", code="CANCELLED"))
        worker = Worker()
        self.open_on(worker)
        self.mirror.send_op = lambda *a, **k: gate.wait(5) and {"error": encoded}
        ops.bind(spawn=lambda run: threading.Thread(target=run, daemon=True).start())
        self.mirror.send("go")
        self.assertTrue(wait_for(lambda: self.mirror._sending.state == "sent"))
        self.mirror.stop()                        # Too late to withdraw: its Stop goes out ordered after it.
        gate.set()
        request = self.mirror._sending
        self.assertTrue(wait_for(lambda: any(entry[0] is request for entry in list(ops._answers))))
        ops.reconcile()
        self.assertFalse(self.mirror.busy)
        self.assertEqual(self.mirror.unsent_draft, "go")
        self.assertNotIn("error", [row.get("role") for row in self.mirror.state["messages"]])


class _Shown(MirrorFixture):
    def shown_after(self, worker, opened):
        """The Send already made: its worker's turn (a fresh Controller counts from (0, 1)) is what the chat shows."""
        self.turn(worker, opened, 1, "four")
        self.mirror.pump_events()
        self.assertFalse(self.mirror.busy, "the new open's turn must be shown")
        self.assertEqual(self.mirror.status, "Completed")
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "Reply to four")


class CloseEndsOnlyItsTurn(_Shown):
    """The native ``switch_reopen_with_resume`` failure: the close of the earlier part, pumped after the person's Send
    to the reopened chat, ended that Send's turn, so the message showed as never taken."""

    def test_a_send_after_the_reopen_is_not_ended_by_the_earlier_close(self):
        worker = Worker()
        self.open_on(worker)
        self.three_turns(worker, "open-a")
        self.close_answered()                     # ``before_switch``: CLOSED queued, not pumped yet.
        self.open_on(worker)                      # ``after_switch``: REOPEN queued behind it.
        self.mirror.send("four")                  # The next message, before any pump.
        self.mirror.pump_events()
        self.assertTrue(self.mirror.busy, "the close of the earlier open ended the new open's turn")
        self.shown_after(worker, "open-b")

    def test_a_send_after_a_crash_resume_is_not_ended_by_the_crash(self):
        first = Worker()
        self.open_on(first)
        self.three_turns(first, "open-a")
        first.ended = True
        wiring.handed_back(self.session, closed=False)   # RECOVERED queued, not pumped yet.
        second = Worker()
        self.open_on(second)
        self.mirror.send("four")
        self.mirror.pump_events()
        self.assertTrue(self.mirror.busy)
        self.shown_after(second, "open-b")


class ReopenAdoptsTheAnnouncedOpen(_Shown):
    """F9 residual: after a fast ``switch_part`` a late frame of the earlier open comes before the new open's."""

    def test_a_late_frame_of_the_earlier_open_never_wins(self):
        worker = Worker()
        self.open_on(worker)
        self.three_turns(worker, "open-a")
        self.close_answered()
        self.open_on(worker)
        self.mirror.pump_events()
        self.mirror.send("four")
        self.tagged(worker, "open-a", "status", {"text": "late"}, (0, 3))   # The earlier close's flush timed out.
        self.shown_after(worker, "open-b")

    def test_the_chat_open_carries_the_announced_id(self):
        from unittest import mock
        from angelo_agent import worker_chat_lifecycle as lifecycle
        worker = Worker()
        with mock.patch.object(lifecycle, "_prepare", lambda controller, worker: self.open_on(worker) or {}), \
                mock.patch.dict(lifecycle._hosted, {}), mock.patch.dict(lifecycle._unanswered, {}):
            fields = lifecycle._open_fields(self.mirror, worker, resume=False)
        self.assertEqual(fields["open_id"], self.mirror.announced_open)

    def test_the_worker_tags_its_frames_with_the_leads_id_even_when_already_open(self):
        from angelo_agent import worker_chat_events
        self.assertEqual(worker_chat_events.open_id({"open_id": "ab12"}), "ab12")
        for bad in (None, "", 7, "x" * 65, "a/b"):
            self.assertIsNone(worker_chat_events.open_id({"open_id": bad}))


class OneTurnOneEnd(MirrorFixture):
    """F2 variant: a close's forwarded end and its provider's own both reach the mirror."""

    def test_the_first_end_of_a_turn_wins(self):
        worker = Worker()
        self.open_on(worker)
        self.mirror.send("one")
        self.turn(worker, "open-a", 1, "one")
        self.tagged(worker, "open-a", "finished", {"status": "interrupted"}, (0, 1))
        self.mirror.pump_events()
        self.assertEqual(self.mirror.status, "Completed")
        self.assertEqual(self.mirror.turn_outcome[0], "completed")

    def test_a_late_second_end_never_ends_the_next_turn(self):
        worker = Worker()
        self.open_on(worker)
        self.mirror.send("one")
        self.turn(worker, "open-a", 1, "one")
        self.mirror.pump_events()
        self.mirror.send("two")
        self.tagged(worker, "open-a", "finished", {"status": "interrupted"}, (0, 1))
        self.mirror.pump_events()
        self.assertTrue(self.mirror.busy)


class WorkerReopenTags(WorkerFixture):
    def test_an_open_answered_as_already_open_takes_the_leads_new_id(self):
        first = self.open(open_id="aaaa")
        self.assertEqual(first["open_id"], "aaaa")
        again = self.open(open_id="bbbb")
        self.assertEqual((again["opened"], again["open_id"]), (False, "bbbb"))
        self.assertEqual(events.OPEN_ID, "open_id")


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
