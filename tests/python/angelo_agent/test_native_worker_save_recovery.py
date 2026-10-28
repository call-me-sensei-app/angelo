# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Failed saves retain data, expose their cause and recover an idle lead without model work."""

import tempfile
from unittest import mock

import test_native_worker_queue as base
from angelo_agent import worker_queue, worker_resume, worker_routing, worker_sessions, worker_save_recovery as recovery


class SaveRecoveryTests(base.Fixture):
    def setUp(self):
        super().setUp()
        self.LIMIT, self.clock, self.notices = 1, 100.0, []
        self.lead.busy, self.lead.pending = False, {}
        self.lead.state["messages"] = [{"role": "user", "text": "Build parts"}]
        self.lead.emit = lambda *args: self.notices.append(("lead", args))
        for patch in (mock.patch.object(recovery.time, "monotonic", lambda: self.clock),
                      mock.patch.object(worker_resume, "_batches", {}),
                      mock.patch.object(worker_resume, "_blender_busy", lambda: False)):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def failed_worker(self, session="w1", number=1):
        chat, process = self.running(session, base.Process(save={"error": {"message": "disk full"}}), number)
        chat.emit = lambda *args: self.notices.append((session, args))
        chat.remote_executor.document_key = ("angelo-worker", "w-" + session, ("serial", 7))
        chat.remote_executor.part_layout = {"revision": 2}
        return chat, process

    def park(self, session):
        worker_queue.tick()
        self.assertTrue(worker_queue._parking[session]["done"].wait(1))
        worker_queue.tick()

    def success(self, process, revision=3):
        process.save = {"value": str(process.part), "revision": revision}

    def test_idle_lead_queued_part_recovers_without_a_message_or_model_request(self):
        chat, process = self.failed_worker()
        worker_queue.admit(self.lead, [base.item(self.lead, 2, folder=self.folder.name)])
        worker_resume.started(self.lead, [1, 2])
        self.park("w1")
        self.assertTrue(worker_resume._idle(self.lead))
        self.assertFalse(worker_resume._ended(self.lead))
        self.assertIn("save failure", worker_sessions.merge_blocker(chat))
        self.assertEqual(recovery.facts("w1")["save_blocked"]["retry_in_s"], 5)
        self.assertEqual([who for who, _ in self.notices], ["w1", "lead"])
        for _ in range(20):
            worker_queue.tick()
        self.assertEqual(len(process.requests), 1)
        self.assertEqual(self.launched, [])
        self.success(process)
        self.clock += 5
        self.park("w1")
        self.assertEqual(process.requests[-1], {"op": "save", "if_dirty": False})
        self.assertNotIn("w1", worker_queue._held)
        self.assertEqual(recovery.facts("w1"), {})
        self.assertEqual(self.launched, [2])
        self.assertIsNone(worker_sessions.merge_blocker(chat))
        self.assertEqual(len(self.notices), 4)

    def test_continued_failure_is_backed_off_capped_and_never_spams(self):
        _chat, process = self.failed_worker()
        self.park("w1")
        waits = []
        for _ in range(7):
            record = worker_queue._held["w1"]
            waits.append(record["retry_at"] - self.clock)
            self.clock = record["retry_at"]
            self.park("w1")
        self.assertEqual(waits, [5, 10, 20, 40, 60, 60, 60])
        self.assertEqual(len(self.notices), 2)
        self.assertEqual(len(process.requests), 8)
        self.assertFalse(process.stopped.is_set())

    def test_retry_rejects_clean_noop_old_revision_wrong_file_and_document(self):
        chat, process = self.failed_worker()
        self.park("w1")
        for answer in ({"value": None}, {"value": str(process.part), "revision": 2},
                       {"value": "/old/part.blend", "revision": 3},
                       {"value": str(process.part), "revision": 3, "document": ["old", 7]}):
            process.save = answer
            self.clock = worker_queue._held["w1"]["retry_at"]
            self.park("w1")
            self.assertIn("w1", worker_queue._held)
            self.assertNotIn("w1", worker_queue.parked)
            self.assertFalse(process.stopped.is_set())
            self.assertIsNotNone(worker_sessions.merge_blocker(chat))

    def test_reply_from_earlier_document_or_save_cannot_clear_hold(self):
        chat, process = self.failed_worker()
        self.park("w1")
        record = {"executor": chat.remote_executor, "retry": recovery.retry_binding(chat.remote_executor)}
        answer = {"value": str(process.part), "revision": 3, "document": ["serial", 7]}
        chat.remote_executor.part_layout = {"revision": 4}
        self.assertIsNotNone(recovery.validate_retry(record, answer))
        chat.remote_executor.part_layout = {"revision": 2}
        chat.remote_executor.document_key = ("angelo-worker", "w-w1", ("new", 8))
        self.assertIsNotNone(recovery.validate_retry(record, answer))

    def test_failed_slot_does_not_block_another_free_slot(self):
        self.LIMIT = 2
        self.failed_worker()
        self.running("w2", number=2)
        worker_queue.admit(self.lead, [base.item(self.lead, 3, folder=self.folder.name)])
        worker_queue.tick()
        for record in list(worker_queue._parking.values()):
            self.assertTrue(record["done"].wait(1))
        worker_queue.tick()
        self.assertIn("w1", worker_queue._held)
        self.assertIn("w2", worker_queue.parked)
        self.assertEqual(self.launched, [3])

    def test_no_queued_parts_waits_for_failed_save_before_completion(self):
        self.failed_worker()
        self.park("w1")
        self.assertTrue(worker_queue.waiting(self.lead))
        self.assertFalse(worker_resume._ended(self.lead))

    def test_unbinding_cancels_retry_and_late_reply(self):
        chat, process = self.failed_worker()
        self.park("w1")
        worker_routing.unbind(chat, worker_routing.END_DONE)
        self.assertNotIn("w1", worker_queue._held)
        self.assertNotIn("w1", worker_queue._parking)
        self.clock += 100
        worker_queue.tick()
        self.assertEqual(len(process.requests), 1)

    def test_document_change_cancels_retry_before_next_save(self):
        _chat, process = self.failed_worker()
        self.park("w1")
        self.lead.document_invalid = True
        self.clock += 100
        worker_queue.tick()
        self.assertNotIn("w1", worker_queue._held)
        self.assertEqual(len(process.requests), 1)

    def test_successful_first_save_keeps_path_without_notices_or_retry(self):
        _chat, process = self.running("w1")
        with mock.patch.object(recovery, "retry_binding") as binding:
            self.park("w1")
        self.assertEqual(process.requests, [{"op": "save", "if_dirty": True}])
        binding.assert_not_called()
        self.assertEqual(self.notices, [])

    def test_newer_completed_or_busy_turn_never_parks_from_old_save_reply(self):
        for retry in (False, True):
            for busy in (False, True):
                session = "race-%s-%s" % (retry, busy)
                chat, process = self.failed_worker(session, number=10)
                chat._request_generation = 1
                if retry:
                    self.park(session)
                    self.clock = worker_queue._held[session]["retry_at"]
                self.success(process)
                worker_queue._park(session, self.ui)
                self.assertTrue(worker_queue._parking[session]["done"].wait(1))
                chat._request_generation = 2
                chat.state["messages"].extend([{"role": "user", "text": "New edit"},
                                               {"role": "error", "text": "New edit save failed"}])
                chat.busy = busy
                worker_queue._finish_parks(self.ui)
                self.assertIn(session, worker_queue._held)
                self.assertNotIn(session, worker_queue.parked)
                self.assertIsNotNone(worker_routing.bound(chat))
                self.assertFalse(process.stopped.is_set())
                worker_routing.unbind(chat, worker_routing.END_DONE)


if __name__ == "__main__":
    import unittest
    unittest.main()
