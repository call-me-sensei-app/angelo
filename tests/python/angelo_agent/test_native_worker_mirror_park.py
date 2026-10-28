# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A parked worker-hosted chat's next message (design A′, ``worker_mirror_park``): as a lane chat's
(``worker_queue.resume``), it relaunches a worker on the chat's own part, the chat reopens there with ``resume``, and
only then the message goes, once and in order, while the composer shows Sending. Covered: the person's route
(``worker_routing.deliver``) and a direct Send, more chats than places, a relaunch or reopen that fails (the message
comes back unsent with its attachments), a Stop during the relaunch, and a chat that can no longer start. The real
queue, routing and mirror; the worker process and its ``chat.open`` are fakes. No Blender.
"""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_worker_queue import ParkedFixture, Process, item, until  # noqa: E402
from angelo_agent import (parallel_sessions, session_rail, worker_chat_lifecycle as lifecycle,  # noqa: E402
                          worker_mirror_ops, worker_queue, worker_routing, worker_sessions)
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.worker_mirror import MirrorController, MirrorRefused  # noqa: E402


class Ops:
    """The reopened chat's ``send_op``: every op recorded, answered ok."""

    def __init__(self):
        self.calls = []

    def __call__(self, session, op, **fields):
        self.calls.append((op, fields.get("text")))
        return {"ok": True}


class ParkedMirrorFixture(ParkedFixture):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"}),
                        mock.patch.object(lifecycle, "opened", self.opened),
                        mock.patch.object(worker_routing, "launch", self.launch_worker)):
            patcher.start()
            self.addCleanup(patcher.stop)
        worker_mirror_ops.bind(spawn=lambda run: run())   # Lanes inline: the order of the ops is the calls'.
        self.addCleanup(worker_mirror_ops.reset)
        self.ops, self.opens, self.workers, self.fail_launch = Ops(), [], [], False

    def launch_worker(self, part):
        worker = Process(part, ready=not self.fail_launch)
        self.workers.append(worker)
        return worker

    def opened(self, controller, worker, resume, defer=False):
        """``worker_chat_lifecycle.opened``: the chat prepared (its ``send_op``) and its ``chat.open`` under way."""
        opening = lifecycle.Opening(controller)
        lifecycle._opening[controller.state["id"]] = opening
        self.opens.append({"resume": resume, "defer": defer, "opening": opening, "controller": controller})

    def answer_open(self, error=None):
        record = self.opens[-1]
        if error is None:
            record["controller"].send_op = self.ops
        record["opening"].error = error
        record["opening"].done.set()
        lifecycle.finish_opens()

    def mirror(self):
        """A parked hosted chat: its worker stopped once its part was saved, its chat closed there."""
        directory = Path(tempfile.mkdtemp())
        session = str(uuid.uuid4())
        Controller(directory, session_id=session).save()
        mirror = MirrorController(directory, session, None)
        mirror.state["messages"].append({"role": "user", "text": "Build the sofa."})
        self.ui._controllers[session] = mirror
        parallel_sessions._order.append(session)
        worker_sessions._started.setdefault("lead", []).append(session)
        worker_sessions._parts[session] = (1, "Sofa", directory / "part.blend")
        worker_routing._remember(mirror, worker_sessions._parts[session][2])
        worker_queue.parked.add(session)
        self.addCleanup(lifecycle._opening.pop, session, None)
        return mirror

    def relaunched(self):
        self.assertTrue(until(lambda: (worker_queue.tick(), bool(self.opens))[1]), "No reopen started")

    def sent(self):
        return [call for call in self.ops.calls if call[0] == "chat.send"]


class ParkedSendTests(ParkedMirrorFixture):
    def test_the_persons_message_relaunches_reopens_resumed_and_is_sent_once_after(self):
        mirror = self.mirror()
        errors = []
        worker_routing.deliver(mirror, None, "Make it blue", lambda turn: mirror.send("Make it blue"), errors.append)
        self.assertEqual(errors, [])
        self.assertTrue(mirror.busy)
        self.assertEqual(mirror.status, worker_mirror_ops.SENDING)   # The composer shows Sending meanwhile.
        self.relaunched()
        self.assertEqual(self.opens[0]["resume"], True)   # Reopened on its own part, resumed.
        self.assertEqual(self.workers[0].part, worker_sessions._parts[mirror.state["id"]][2])
        self.assertEqual(self.sent(), [])   # Nothing goes before its open was answered.
        self.answer_open()
        self.assertEqual(self.sent(), [("chat.send", "Make it blue")])
        worker_mirror_ops.reconcile()
        worker_queue.tick()
        self.assertEqual(self.sent(), [("chat.send", "Make it blue")])   # Once.
        self.assertNotIn(mirror.state["id"], worker_queue.parked)
        self.assertIsNotNone(worker_routing.bound(mirror))
        self.assertEqual(mirror.status, "Thinking")

    def test_a_direct_send_relaunches_too_and_a_second_waits_for_the_first(self):
        mirror = self.mirror()
        mirror.send("first")
        with self.assertRaises(RuntimeError):   # One message at a time, as any Send: never reordered or doubled.
            mirror.send("second")
        self.relaunched()
        self.answer_open()
        self.assertEqual(self.sent(), [("chat.send", "first")])

    def test_more_chats_than_places_wait_in_line_showing_sending_then_go_in_order(self):
        self.LIMIT = 1
        self.running("w1")
        worker_queue.admit(self.lead, [item(self.lead, 2)])
        mirror = self.mirror()
        mirror.send("after the place frees")
        self.assertEqual([entry.get("kind", "part") for entry in worker_queue.queue], ["resume", "part"])
        self.assertEqual(worker_queue.resuming_kind(mirror.state["id"]), (session_rail.QUEUED, 1))
        worker_queue.tick()
        self.assertEqual((self.opens, mirror.status, self.launched), ([], worker_mirror_ops.SENDING, []))
        worker_routing.unbind(self.ui._controllers["w1"], worker_routing.END_DONE)   # The running worker ends.
        self.ui._controllers.pop("w1")
        self.relaunched()
        self.answer_open()
        self.assertEqual(self.sent(), [("chat.send", "after the place frees")])

    def test_a_relaunch_that_fails_returns_the_message_unsent_with_its_attachments(self):
        mirror = self.mirror()
        mirror.attachments = [{"path": "/refs/sofa.png", "kind": "image"}]
        self.fail_launch = True
        mirror.send("Make it blue")
        self.assertTrue(until(lambda: (worker_queue.tick(), not mirror.busy)[1]))
        self.assertEqual(self.sent(), [])
        self.assertEqual(mirror.unsent_draft, "Make it blue")
        self.assertEqual(mirror.attachments, [{"path": "/refs/sofa.png", "kind": "image"}])
        self.assertEqual(mirror.status, "Failed")
        self.assertEqual(mirror.state["messages"][-1]["role"], "error")
        self.assertIn(mirror.state["id"], worker_queue.parked)   # Still parked: the next message tries again.

    def test_a_reopen_that_fails_returns_the_message_unsent(self):
        mirror = self.mirror()
        mirror.send("Make it blue")
        self.relaunched()
        self.answer_open(lifecycle.ChatOpFailed("chat.open", mirror.state["id"], "refused"))
        self.assertEqual(self.sent(), [])
        self.assertEqual((mirror.busy, mirror.unsent_draft), (False, "Make it blue"))

    def test_a_stop_during_the_relaunch_withdraws_it_and_nothing_is_sent_after(self):
        mirror = self.mirror()
        mirror.attachments = [{"path": "/refs/sofa.png", "kind": "image"}]
        mirror.send("Make it blue")
        mirror.stop()
        self.assertFalse(mirror.busy)
        self.assertEqual((mirror.unsent_draft, len(mirror.attachments)), ("Make it blue", 1))
        self.relaunched()
        self.answer_open()
        worker_queue.tick()
        self.assertEqual(self.sent(), [])   # Withdrawn before it left: never sent once the chat reopened.
        self.assertFalse(mirror.busy)

    def test_a_chat_its_lead_can_no_longer_merge_never_launches_and_returns_the_message(self):
        mirror = self.mirror()
        with mock.patch.object(worker_queue, "current", lambda lead: False):
            mirror.send("Make it blue")
        self.assertEqual((self.workers, mirror.busy, mirror.unsent_draft), ([], False, "Make it blue"))

    def test_a_mirror_with_no_worker_that_is_not_parked_still_refuses_at_once(self):
        mirror = self.mirror()
        worker_queue.parked.discard(mirror.state["id"])
        with self.assertRaises(MirrorRefused):
            mirror.send("Make it blue")
        self.assertEqual((self.workers, mirror.busy), ([], False))



class ParkedReasonTests(ParkedMirrorFixture):
    """R3 H3: a parked chat that cannot start again says why as a window's chat does (``RESTART_FAILED``), its
    reason kept translatable, never "something went wrong" with a raw code."""

    def restart_row(self, mirror):
        row = [m for m in mirror.state["messages"] if m.get("role") == "error"][-1]   # An ending's notice may follow.
        self.assertEqual(row["i18n"][0], worker_queue.RESTART_FAILED)
        self.assertNotIn("UNKNOWN", row["text"])
        return row

    def test_a_relaunch_that_fails_says_the_worker_could_not_start_again_and_why(self):
        mirror = self.mirror()
        self.fail_launch = True
        mirror.send("Make it blue")
        self.assertTrue(until(lambda: (worker_queue.tick(), not mirror.busy)[1]))
        self.assertTrue(self.restart_row(mirror)["text"].startswith("This chat's worker could not start again ("))

    def test_a_replaced_lead_ends_the_send_saying_why_never_an_internal_fault(self):
        mirror = self.mirror()
        with mock.patch.object(worker_queue, "current", lambda lead: False), mock.patch("builtins.print"):
            mirror.send("Make it blue")
            mirror.pump_events()
        self.assertEqual(mirror.unsent_draft, "Make it blue")
        said = [m.get("i18n", [None])[0] for m in mirror.state["messages"]]
        self.assertIn(worker_routing.END_REPLACED, said)   # Its ending's own notice: the lead's document changed.
        self.assertFalse(any(m.get("role") == "error" for m in mirror.state["messages"]))

    def test_a_chat_resume_refuses_at_once_says_why_its_worker_stopped(self):
        """``resume`` refused with the turn still sending (no ending withdrew it): ``RESTART_FAILED`` with the
        recorded reason, a nested recipe."""
        mirror = self.mirror()
        worker_sessions.died[mirror.state["id"]] = worker_queue.REPLACED
        self.addCleanup(worker_sessions.died.pop, mirror.state["id"], None)
        with mock.patch.object(worker_queue, "resume", lambda *args: False):
            mirror.send("Make it blue")
        row = self.restart_row(mirror)
        self.assertEqual(row["i18n"][1]["why"], [worker_queue.REPLACED, {}])   # Translated whole where shown.
        self.assertEqual(mirror.unsent_draft, "Make it blue")

    def test_a_lead_that_merged_the_part_meanwhile_says_so_translatably(self):
        mirror = self.mirror()
        mirror.send("Make it blue")
        worker_queue.end_parked(mirror.state["id"], worker_queue.MERGED, worker_routing.END_MERGED)
        self.assertTrue(until(lambda: (worker_queue.tick(), not mirror.busy)[1]))
        rows = [m for m in mirror.state["messages"] if m.get("i18n", [None])[0] == worker_queue.RESTART_FAILED]
        self.assertEqual(rows[-1]["i18n"][1]["why"], [worker_queue.MERGED, {}])
        self.assertEqual(mirror.unsent_draft, "Make it blue")

    def test_a_reopen_refused_by_its_worker_shows_no_op_or_chat_id(self):
        mirror = self.mirror()
        mirror.send("Make it blue")
        self.relaunched()
        with mock.patch("builtins.print"):
            self.answer_open(lifecycle.ChatOpFailed("chat.open", mirror.state["id"], "refused"))
            mirror.pump_events()
        self.assertEqual(mirror.unsent_draft, "Make it blue")
        for row in mirror.state["messages"][1:]:
            for leak in (mirror.state["id"], "chat.open", "UNKNOWN"):
                self.assertNotIn(leak, row.get("text", ""))

    def test_a_chat_op_failure_reads_as_its_plain_reason_in_the_restart_notice(self):
        from angelo_agent.worker_ending import stop_reason
        with mock.patch("builtins.print"):
            why = stop_reason(lifecycle.ChatOpFailed("chat.open", "s-1", "no answer", timed_out=True))
        from angelo_agent.worker_reasons import TIMED_OUT
        self.assertEqual(why.template, TIMED_OUT)
        self.assertEqual(stop_reason(RuntimeError(worker_queue.MERGED)), worker_queue.MERGED)
        self.assertEqual(stop_reason(OSError("disk full")), "disk full")   # As base shows any other cause.

if __name__ == "__main__":
    unittest.main()
