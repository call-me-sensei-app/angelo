# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A job's record says whether its result, or its picture, reached the model, and when.

A real JobStore and JobManager with a stand-in runner, the context ledger that
composes a message's jobs block, and the request context's picture hook. No
model, process or Blender. ``test_native_request_path`` runs the same marks
through a whole turn.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import job_manager, request_path
from angelo_agent.context_ledger import TurnBlocks
from angelo_agent.job_manager import JobManager
from angelo_agent.job_store import JobStore, context_entry, mark_shown
from angelo_agent.telemetry import RequestMetrics
from test_native_long_jobs import FakeRunner
from test_native_request_path import view, view_result


class JobShownTests(unittest.TestCase):
    """Whether a job's result, or its picture, reached the model: a fact of the job's own record."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.manager = JobManager(JobStore(self.folder.name))
        job_manager.install(self.manager)
        self.addCleanup(job_manager.install, None)

    def job(self, done=True):
        runner = FakeRunner()
        record = self.manager.submit("chat", "call-1", {"kind": "RENDER_STILL", "output": "renders/final.png",
                                                        "minutes": 30.0}, runner)
        if done:
            runner.ended = True
            self.manager.poll()
        return record

    def test_a_finished_job_is_shown_when_the_message_that_lists_it_was_delivered(self):
        record = self.job()
        self.assertEqual((record["status"], record["shown"]), ("done", {}), "A job nobody was told about")
        blocks = TurnBlocks()
        self.assertIn('"job":"j1"', blocks.jobs("chat"))
        self.assertEqual((blocks.jobs_sent, record["shown"]), (["j1"], {}), "Composed, not delivered")
        self.assertIsNone(blocks.ledger(None), "A message no native context is known to hold")
        self.assertEqual(record["shown"], {})
        before = time.time()
        blocks.ledger("native-1")  # The controller commits the ledger of a delivered, unstopped turn.
        shown = record["shown"]
        self.assertEqual(sorted(shown), ["result_at"])
        self.assertTrue(before - 1 <= shown["result_at"] <= time.time() + 1)
        self.assertEqual(JobStore(self.folder.name).get("j1")["shown"], shown, "Saved with the commit")
        blocks.ledger("native-1")
        self.assertEqual(record["shown"], shown, "The first time stays")

    def test_a_running_job_in_the_block_is_not_a_result_and_another_chats_job_is_not_listed(self):
        record = self.job(done=False)
        blocks = TurnBlocks()
        self.assertIn('"status":"running"', blocks.jobs("chat"))
        blocks.ledger("native-1")
        self.assertEqual((blocks.jobs_sent, record["shown"]), ([], {}))
        other = TurnBlocks()
        self.assertEqual(other.jobs("another chat"), "")
        self.assertEqual(other.jobs_sent, [])

    def test_a_view_that_shows_the_jobs_picture_marks_it_at_delivery(self):
        record = self.job()
        metrics = RequestMetrics("claude", session_id="chat")
        host = SimpleNamespace(_request_metrics=metrics)
        context = SimpleNamespace(host=host, request_fence=SimpleNamespace(matches=lambda other: other is host))
        picture = view("r1", "RENDERED", "FINISHED_IMAGE")
        request_path.dispatched(context, "angelo_view", {"image": "j1"}, lambda result: None)(
            view_result(picture, rendered={"job": "j1", "output": "renders/final.png"}))
        self.assertEqual(record["shown"], {}, "Returned by the tool, not yet handed to the model")
        request_path.pictures(context, [picture], True)
        self.assertEqual(record["shown"], {}, "Staged for a message the provider has not accepted")
        request_path.accepted(context)
        self.assertEqual((sorted(record["shown"]), record["shown"]["images"]), (["image_at", "images"], 1))
        self.assertEqual(metrics.snapshot()["path"]["images"], [[1, None, {"job:j1": 1}]])
        self.assertTrue(self.manager.store.unsaved, "Marked on a provider's thread; written by the next save")
        self.assertTrue(self.manager.store.save())
        self.assertEqual(JobStore(self.folder.name).get("j1")["shown"], record["shown"])

    def test_the_mark_is_closed_and_not_part_of_what_the_model_reads(self):
        record = self.job()
        store = self.manager.store
        self.assertFalse(mark_shown(store, "j1", "liked"))
        self.assertFalse(mark_shown(store, "j99", "result"))
        self.assertFalse(mark_shown(store, None, "image"))
        self.assertTrue(mark_shown(store, "j1", "image", now=1700000000.26))
        self.assertTrue(mark_shown(store, "j1", "image", now=1700000050.0))
        self.assertEqual(record["shown"], {"image_at": 1700000000.3, "images": 2})
        self.assertNotIn("shown", context_entry(record), "The jobs block is byte for byte what it was")
        old = dict(record)
        del old["shown"]  # A record written before this field.
        store.records["j1"] = old
        store.save()
        self.assertEqual(JobStore(self.folder.name).get("j1")["shown"], {})

    def test_legacy_delivery_marks_reload_as_unknown_without_losing_history(self):
        record = self.job()
        legacy = {"image_at": 1700000000.0, "images": 2, "result_at": 1700000010.0}
        record["shown"] = dict(legacy)
        record.pop("shown_version")
        self.manager.store.save()
        restored = JobStore(self.folder.name)
        self.assertEqual(restored.get("j1")["shown"], {})
        self.assertEqual(restored.get("j1")["shown_unconfirmed"], legacy)
        mark_shown(restored, "j1", "image", now=1700000020.0)
        restored.save()
        again = JobStore(self.folder.name).get("j1")
        self.assertEqual(again["shown"], {"image_at": 1700000020.0, "images": 1})
        self.assertEqual(again["shown_unconfirmed"], legacy)
        self.assertNotIn("shown_version", context_entry(again), "The model-visible job block stays byte-identical")


if __name__ == "__main__":
    unittest.main()
