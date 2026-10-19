# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: the snapshot worker thread and its main-thread host glue."""

import ast
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import blend_v1, scene_blocks  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent import snapshot_service, snapshot_worker  # noqa: E402
from angelo_agent.checkpoint_policy import CheckpointPolicyStore, checkpoint_ref  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.snapshot_worker import SnapshotWorker  # noqa: E402

WORKER_MODULES = ("snapshot_accounting", "snapshot_blocks", "snapshot_git", "snapshot_ingest", "snapshot_maintenance",
                  "snapshot_retention", "snapshot_store", "snapshot_store_writer", "snapshot_worker")


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class SnapshotWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-worker-")
        self.root = Path(self.temp.name).resolve() / "state"
        self.store = CheckpointStore(self.root, str(uuid.uuid4()))

    def tearDown(self):
        snapshot_service.stop()
        self.temp.cleanup()

    def capture(self, data=None, store=None):
        store = store or self.store
        record, path = store.reserve("")
        path.write_bytes(data or blend_v1(scene_blocks(objects=6, array_bytes=512 * 1024)))
        return store.complete(record["id"])

    def test_worker_modules_never_import_bpy(self):
        for name in WORKER_MODULES:
            tree = ast.parse((ADDON / "angelo_agent" / (name + ".py")).read_text())
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            with self.subTest(module=name):
                self.assertFalse(any(module.split(".")[0] == "bpy" or module in {"ui", "snapshot_service"}
                                     for module in imported))

    def test_background_thread_ingests_and_reports(self):
        record = self.capture()
        worker = SnapshotWorker(self.root)
        worker.start()
        try:
            worker.submit_ingest(self.store.session_id, record["id"], notify="chat")
            event = worker.events.get(timeout=20)
        finally:
            self.assertTrue(worker.stop())
        self.assertEqual(event["kind"], "ingested")
        self.assertEqual(event["notify"], "chat")
        self.assertTrue(event["stats"]["adopted"])
        self.assertEqual(self.store.load(record["id"])["storage"], "store")

    def test_failures_become_events_and_the_thread_keeps_working(self):
        first, second = self.capture(), self.capture()
        worker = SnapshotWorker(self.root)
        calls = []

        def flaky(store, identity, **kwargs):
            calls.append(identity)
            if len(calls) == 1:
                raise RuntimeError("unexpected failure")
            return {"ref": identity, "skipped": "test"}

        with patch.object(snapshot_worker, "ingest_snapshot", flaky):
            worker.start()
            worker.submit_ingest(self.store.session_id, first["id"])
            worker.submit_ingest(self.store.session_id, second["id"])
            events = [worker.events.get(timeout=10), worker.events.get(timeout=10)]
            worker.stop()
        self.assertEqual([event["kind"] for event in events], ["failed", "ingested"])
        self.assertIn("unexpected failure", events[0]["error"])

    def test_only_the_latest_maintenance_request_is_kept(self):
        worker = SnapshotWorker(self.root)
        for refs in ((), ("a",), ("b",)):
            worker.submit_maintenance({"max_bytes": 0}, refs, 0)
        self.assertEqual(worker.pending(), 1)
        self.assertEqual(worker._tasks[0][3], ("b",))

    def test_stop_during_ingestion_is_prompt_and_keeps_the_full_copy(self):
        record = self.capture(blend_v1(scene_blocks(objects=6, array_bytes=48 * 1024 * 1024)))
        worker = SnapshotWorker(self.root)
        worker.start()
        worker.submit_ingest(self.store.session_id, record["id"])
        time.sleep(0.05)
        started = time.monotonic()
        self.assertTrue(worker.stop(timeout=10))
        self.assertLess(time.monotonic() - started, 10)
        current = self.store.load(record["id"])
        self.assertTrue(self.store.availability(record["id"])["available"])
        if current.get("storage", "full") == "full":
            self.assertTrue((self.store.folder(record["id"]) / "scene.blend").is_file())


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class SnapshotServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-service-")
        self.root = Path(self.temp.name).resolve() / "state"
        self.store = CheckpointStore(self.root, str(uuid.uuid4()))
        self.controller = SimpleNamespace(checkpoints=self.store, state={"id": self.store.session_id},
                                          request_checkpoint=None, emit=Mock())
        self.ui = SimpleNamespace(_controllers={self.store.session_id: self.controller}, _controller=self.controller)

    def tearDown(self):
        snapshot_service.stop()
        self.temp.cleanup()

    def capture(self, seed):
        record, path = self.store.reserve("")
        path.write_bytes(blend_v1([(b"OB\0\0", b"%d" % seed, 1), (b"DATA", os.urandom(1024 * 1024), 2)]))
        return self.store.complete(record["id"])

    def pump_until(self, predicate):
        return wait_for(lambda: (snapshot_service.pump(self.ui), predicate())[1])

    def test_capture_without_a_running_service_stays_a_full_copy(self):
        record = self.capture(1)
        self.assertFalse(snapshot_service.after_capture(self.controller, record))
        self.assertNotIn("storage", self.store.load(record["id"]))

    def test_capture_ingestion_retention_and_notice_round_trip(self):
        self.root.mkdir(parents=True, exist_ok=True)
        CheckpointPolicyStore(self.root).save({"max_bytes": 0, "max_count": 2}, user_confirmed=True)
        linked = Path(self.temp.name) / "linked"
        linked.symlink_to(self.temp.name)
        snapshot_service.start(linked / "state")  # Same profile through a link, like /tmp and /private/tmp.
        records = []
        for seed in range(3):
            records.append(self.capture(seed))
            self.controller.request_checkpoint = records[-1]["id"]
            self.assertTrue(snapshot_service.after_capture(self.controller, records[-1]))
        self.assertTrue(self.pump_until(lambda: self.store.availability(records[0]["id"])["state"] == "PRUNED"))
        self.assertTrue(self.pump_until(lambda: self.controller.emit.called))
        message = self.controller.emit.call_args[0][1]
        self.assertEqual(message["role"], "system")
        self.assertIn("removed 1 oldest restore point", message["text"])
        for record in records[1:]:
            # The reason and every record's state, so a failure says which window it was (a flake seen once in 30
            # full-suite runs, 2026-10-19, reported only "False is not true").
            found = self.store.availability(record["id"])
            self.assertTrue(found["available"], (found, [(item["id"][:8], self.store.load(item["id"])["state"],
                                                          self.store.load(item["id"]).get("storage")) for item in records],
                                                 [call[0][1].get("text") for call in self.controller.emit.call_args_list]))
        self.assertTrue(self.pump_until(lambda: all(self.store.load(record["id"]).get("storage") == "store"
                                                    for record in records[1:])))
        self.assertIn(checkpoint_ref(self.store.session_id, records[-1]["id"]),
                      snapshot_service.protected_snapshot_refs(self.ui._controllers.values()))


if __name__ == "__main__":
    unittest.main()
