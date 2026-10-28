# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""While a worker chat parks, nothing of the lead reads it as hosted or as gone. No Blender.

Pinned: a lead's batch never ends (its worker-done event never fires) while any of its parts is parking, so the
event's merge never meets a close under way (Mac 2, parallel_resume@hosted); a message to a hosted chat that is
parking waits for the park and then relaunches it as a parked chat's does, while the parts behind it start; a park
that ended without parking the chat refuses that message with nothing launched; a lane chat (switch 0) parking is
never sent that way.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_parking, worker_queue, worker_resume, worker_sessions  # noqa: E402


def chat(session, hosted=True):
    return SimpleNamespace(state={"id": session}, hosted_in_worker=hosted)


class BatchEnd(unittest.TestCase):
    def ended(self, parking):
        lead = chat("lead", hosted=False)
        with mock.patch.object(worker_queue, "waiting", lambda lead: False), \
                mock.patch.object(worker_sessions, "workers_of", lambda lead_id: ["w1", "w2"]), \
                mock.patch.object(worker_sessions, "finished", lambda session: True), \
                mock.patch.object(worker_resume, "_checking", lambda sessions: False), \
                mock.patch.object(worker_queue, "_parking", parking):
            return worker_resume._ended(lead)

    def test_a_batch_waits_while_any_of_its_parts_is_parking(self):
        self.assertFalse(self.ended({"w2": {}}))
        self.assertTrue(self.ended({}))
        self.assertTrue(self.ended({"other": {}}))   # Another lead's park is not this batch's.


class MessageWhileParking(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(worker_queue, "queue", []),
                        mock.patch.object(worker_queue, "parked", set()),
                        mock.patch.object(worker_queue, "_parking", {"w1": {}}),
                        mock.patch.object(worker_queue, "_resuming", {}),
                        mock.patch.object(worker_queue, "_relaunching", []),
                        mock.patch.object(worker_queue, "current", lambda lead: True),
                        mock.patch.object(worker_queue, "_lead_of", lambda session: "lead"),
                        mock.patch.object(worker_queue, "_changed", lambda: None),
                        mock.patch.object(worker_queue, "drive", lambda until=None: None),
                        mock.patch.object(worker_queue, "_room", lambda item: True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.launched, self.failed = [], []
        mock.patch.object(worker_sessions, "_launch", self.launched.append).start()
        mock.patch.object(worker_sessions, "_released", [False]).start()
        self.addCleanup(mock.patch.stopall)

    def test_only_a_hosted_chat_that_is_parking_waits_for_its_park(self):
        self.assertTrue(worker_parking.waits(chat("w1")))
        self.assertFalse(worker_parking.waits(chat("w1", hosted=False)))   # Lanes: the message reaches it.
        self.assertFalse(worker_parking.waits(chat("w2")))
        self.assertFalse(worker_queue.resume(chat("w1", hosted=False), lambda: None, self.failed.append))

    def test_a_message_waits_for_the_park_while_parts_behind_it_start_then_relaunches(self):
        part = {"lead": "lead", "number": 3, "part": {"name": "Lamp"}}
        worker_queue.queue.append(part)
        with mock.patch.object(worker_queue, "_relaunch", lambda item: self.launched.append(item["kind"])):
            self.assertTrue(worker_queue.resume(chat("w1"), lambda: None, self.failed.append))
            self.assertEqual(self.launched, [part])   # The part behind the waiting restart went.
            self.assertEqual([item.get("kind") for item in worker_queue.queue], [worker_queue.RESUME])
            worker_queue._parking.clear()
            worker_queue.parked.add("w1")   # Its park ended: parked.
            worker_queue._start_queued()
        self.assertEqual(self.launched, [part, worker_queue.RESUME])
        self.assertEqual(self.failed, [])

    def test_a_park_that_ended_without_parking_refuses_the_message_with_nothing_launched(self):
        self.assertTrue(worker_queue.resume(chat("w1"), lambda: None, self.failed.append))
        worker_queue._parking.clear()   # Its save failed (retried), or the chat ended meanwhile: not parked.
        with mock.patch("angelo_agent.worker_routing.launch", side_effect=AssertionError("launched")):
            worker_queue._start_queued()
        self.assertEqual(len(self.failed), 1)
        self.assertIn("could not start again", str(self.failed[0].args[0]))
        self.assertEqual(worker_queue.queue, [])
        self.assertNotIn("w1", worker_queue._resuming)


if __name__ == "__main__":
    unittest.main()
