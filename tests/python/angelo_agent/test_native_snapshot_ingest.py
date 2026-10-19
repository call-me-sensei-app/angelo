# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: request snapshots moving from full copies into the store.

Restore must work in every intermediate state, the full copy is deleted only
after verified adoption, and every existing journal/retention/restore rule
still holds for deduplicated points. Synthetic bytes; Blender never opens them
(test_blender_snapshot_dedup.py restores real documents).
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import blend_v1, scene_blocks  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent import snapshot_ingest  # noqa: E402
from angelo_agent.checkpoint_policy import checkpoint_ref  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.snapshot_ingest import (IngestCancelled, IngestError, ingest_snapshot,  # noqa: E402
                                           pending_snapshots, remove_leftover_copies)
from angelo_agent.snapshot_maintenance import clean_store  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402
from angelo_agent.snapshot_worker import SnapshotWorker  # noqa: E402


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class SnapshotIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-ingest-")
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.other = CheckpointStore(self.store.root, str(uuid.uuid4()))
        self.chunks = SnapshotStore(self.store.root)
        self.files = FileWorkspace(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, store=None, data=None, purpose="request", ingest=True):
        store = store or self.store
        data = data if data is not None else blend_v1(scene_blocks(objects=12, array_bytes=1024 * 1024))
        record, path = store.reserve(str(self.project / "original.blend"), purpose=purpose)
        path.write_bytes(data)
        record = store.complete(record["id"])
        if ingest:
            ingest_snapshot(store, record["id"])
        return store.load(record["id"]), data

    def recovered(self, store, record):
        _, target = store.recovery_copy(record["id"])
        try:
            return target.read_bytes()
        finally:
            target.unlink()

    def write(self, store, record, path, content):
        return self.files.execute("angelo_write_file", {"path": path, "content": content}, store, record["id"])

    def test_adoption_replaces_the_full_copy_with_a_verified_manifest(self):
        record, data = self.snapshot()
        self.assertEqual(record["storage"], "store")
        self.assertEqual(record["scene_hash"], hashlib.blake2b(data, digest_size=32).hexdigest())
        self.assertEqual(record["scene_bytes"], len(data))
        self.assertEqual(sorted(path.name for path in self.store.folder(record["id"]).iterdir()), ["checkpoint.json"])
        self.assertTrue(self.store.availability(record["id"])["available"])
        self.assertEqual(self.recovered(self.store, record), data)
        inventory = self.store.inspect()
        self.assertEqual(inventory["issues"], [])
        item = inventory["checkpoints"][0]
        self.assertEqual((item["storage"], item["scene_bytes"], item["scene_file"]), ("store", len(data), False))
        self.assertEqual(ingest_snapshot(self.store, record["id"])["skipped"], "not a ready ordered full-copy snapshot")

    def test_restore_works_before_ingestion_and_after_an_interrupted_adoption(self):
        record, data = self.snapshot(ingest=False)
        self.assertEqual(self.recovered(self.store, record), data)
        with patch.object(snapshot_ingest, "_adopt", side_effect=RuntimeError("simulated crash before adoption")):
            with self.assertRaises(RuntimeError):
                ingest_snapshot(self.store, record["id"])
        current = self.store.load(record["id"])
        self.assertNotIn("storage", current)
        self.assertTrue(self.chunks.has_manifest(checkpoint_ref(self.store.session_id, record["id"])))
        self.assertTrue(self.store.availability(record["id"])["available"])
        self.assertEqual(self.store.inspect()["issues"], [])
        self.assertEqual(self.recovered(self.store, current), data)
        self.assertEqual(pending_snapshots(self.store), [(self.store.session_id, record["id"])])
        self.assertTrue(ingest_snapshot(self.store, record["id"])["adopted"])
        self.assertEqual(self.recovered(self.store, self.store.load(record["id"])), data)
        self.assertEqual(self.chunks.usage()["manifests"], 1, "re-ingestion replaces, never duplicates")

    def test_leftover_full_copy_after_adoption_is_harmless_and_removed_later(self):
        with patch.object(snapshot_ingest, "_remove_copy", return_value=False):
            record, data = self.snapshot()
        self.assertEqual(record["storage"], "store")
        self.assertTrue((self.store.folder(record["id"]) / "scene.blend").is_file())
        self.assertEqual(self.store.inspect()["issues"], [])
        self.assertTrue(self.store.availability(record["id"])["available"])
        self.assertEqual(self.recovered(self.store, record), data)
        self.assertEqual(remove_leftover_copies(self.store), 1)
        self.assertFalse((self.store.folder(record["id"]) / "scene.blend").exists())
        self.assertEqual(self.recovered(self.store, record), data)

    def test_cancelled_ingestion_leaves_the_full_copy_and_no_reachable_data(self):
        record, data = self.snapshot(data=blend_v1(scene_blocks(objects=12, array_bytes=12 * 1024 * 1024)), ingest=False)
        with self.assertRaises(IngestCancelled):
            ingest_snapshot(self.store, record["id"], cancelled=lambda: True)
        self.assertNotIn("storage", self.store.load(record["id"]))
        self.assertFalse(self.chunks.has_manifest(checkpoint_ref(self.store.session_id, record["id"])))
        self.assertEqual(self.chunks.usage()["pack_bytes"], 0)
        self.assertEqual(self.recovered(self.store, record), data)

    def test_failed_verification_keeps_the_full_copy(self):
        record, data = self.snapshot(ingest=False)
        with patch.object(SnapshotStore, "reassemble", side_effect=ValueError("simulated corrupt pack")):
            with self.assertRaisesRegex(IngestError, "full copy was kept"):
                ingest_snapshot(self.store, record["id"])
        self.assertNotIn("storage", self.store.load(record["id"]))
        self.assertTrue((self.store.folder(record["id"]) / "scene.blend").is_file())
        self.assertFalse(self.chunks.has_manifest(checkpoint_ref(self.store.session_id, record["id"])))
        self.assertEqual(self.recovered(self.store, record), data)

    def test_a_point_removed_during_ingestion_is_skipped_not_failed(self):
        record, _ = self.snapshot(ingest=False)
        self.snapshot()
        original = snapshot_ingest._write_segments

        def prune_midway(*args, **kwargs):
            result = original(*args, **kwargs)
            plan = self.store.prune_plan({"max_count": 1, "max_bytes": 0})
            self.store.prune(plan, user_confirmed=True)  # Unlinks the full copy under the reader.
            return result

        with patch.object(snapshot_ingest, "_write_segments", prune_midway):
            result = ingest_snapshot(self.store, record["id"])
        self.assertIn("removed while it was being stored", result["skipped"])
        self.assertEqual(self.store.availability(record["id"])["state"], "PRUNED")
        self.assertFalse(self.chunks.has_manifest(checkpoint_ref(self.store.session_id, record["id"])))

    def test_changed_or_linked_scene_files_are_never_adopted(self):
        record, data = self.snapshot(ingest=False)
        scene = self.store.folder(record["id"]) / "scene.blend"
        scene.write_bytes(data + b"appended after capture")
        with self.assertRaisesRegex(IngestError, "changed"):
            ingest_snapshot(self.store, record["id"])
        outside = self.root / "outside.blend"
        outside.write_bytes(data)
        scene.unlink()
        scene.symlink_to(outside)
        with self.assertRaises(ValueError):
            ingest_snapshot(self.store, record["id"])
        self.assertNotIn("storage", self.store.load(record["id"]))
        self.assertEqual(outside.read_bytes(), data)

    def test_journals_and_cross_chat_file_restore_work_on_deduplicated_points(self):
        (self.project / "notes.md").write_text("original")
        before, before_data = self.snapshot()
        self.write(self.store, before, "notes.md", "first request")
        later, _ = self.snapshot(self.other)
        self.write(self.other, later, "notes.md", "second request")
        self.write(self.other, later, "created.md", "later new file")
        self.assertEqual(self.other.load(later["id"])["storage"], "store")
        disposable, _ = self.snapshot()
        protected = [checkpoint_ref(self.store.session_id, before["id"])]
        plan = self.store.prune_plan({"max_count": 1, "max_bytes": 0}, protected_refs=protected)
        self.assertEqual([item["id"] for item in plan["candidates"]], [disposable["id"]])
        self.store.prune(plan, user_confirmed=True, protected_refs=protected)
        self.assertEqual(self.recovered(self.store, self.store.load(before["id"])), before_data)
        self.assertEqual(self.store.restore_since(before["id"]), 2)
        self.assertEqual((self.project / "notes.md").read_text(), "original")
        self.assertFalse((self.project / "created.md").exists())

    def test_pruned_points_keep_tombstones_and_maintenance_releases_their_data(self):
        first, _ = self.snapshot(data=blend_v1(scene_blocks(objects=12, seed=5)))
        second, second_data = self.snapshot(data=blend_v1(scene_blocks(objects=12, seed=6)))
        live_before = self.chunks.usage()["live_bytes"]
        plan = self.store.prune_plan({"max_count": 1, "max_bytes": 0})
        self.assertEqual([item["id"] for item in plan["candidates"]], [first["id"]])
        self.assertGreater(plan["candidates"][0]["store_bytes"], 1024 * 1024)
        result = self.store.prune(plan, user_confirmed=True)
        self.assertEqual(result["store_bytes_pending"], plan["candidates"][0]["store_bytes"])
        self.assertEqual(self.store.availability(first["id"])["state"], "PRUNED")
        with self.assertRaisesRegex(ValueError, "removed"):
            self.store.recovery_copy(first["id"])
        cleanup = clean_store(self.store.root)
        self.assertEqual(cleanup["orphans"], 1)
        self.assertGreater(cleanup["bytes_released"], 1024 * 1024)
        self.assertLess(self.chunks.usage()["live_bytes"], live_before)
        self.assertEqual(self.recovered(self.store, second), second_data)
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_legacy_records_are_never_rewritten_or_migrated(self):
        identity = str(uuid.uuid4())
        folder = self.store.folder(identity)
        folder.mkdir(parents=True, mode=0o700)
        (folder / "scene.blend").write_bytes(b"legacy host scene")
        metadata = folder / "checkpoint.json"
        metadata.write_text(json.dumps({"version": 1, "id": identity, "created": 10, "original_path": "",
                                        "files": [], "scene_bytes": 17}))
        original = metadata.read_bytes()
        self.assertEqual(pending_snapshots(self.store), [])
        self.assertIn("skipped", ingest_snapshot(self.store, identity))
        self.assertEqual(metadata.read_bytes(), original)

    def test_existing_full_copies_migrate_only_after_byte_identical_verification(self):
        records = [self.snapshot(store, data=blend_v1(scene_blocks(objects=10, seed=index)), ingest=False)
                   for index, store in enumerate((self.store, self.other, self.store))]
        broken = records[1][0]["id"]
        original = SnapshotStore.reassemble

        def fail_one(store, ref, *args, **kwargs):
            if ref.endswith(broken):
                raise ValueError("simulated mismatch")
            return original(store, ref, *args, **kwargs)

        worker = SnapshotWorker(self.store.root)
        worker.submit_recovery()
        with patch.object(SnapshotStore, "reassemble", fail_one):
            worker.run_pending()
        events = []
        while not worker.events.empty():
            events.append(worker.events.get())
        self.assertEqual(events[0]["kind"], "recovered")
        self.assertEqual(events[0]["queued"], 3)
        self.assertEqual([event["kind"] for event in events[1:]].count("ingested"), 2)
        self.assertEqual([event["kind"] for event in events[1:]].count("failed"), 1)
        for (record, data), store in zip(records, (self.store, self.other, self.store)):
            current = store.load(record["id"])
            self.assertEqual(current.get("storage", "full"), "full" if record["id"] == broken else "store")
            self.assertEqual(self.recovered(store, current), data)

    def test_concurrent_captures_journals_and_ingestion_stay_consistent(self):
        (self.project / "data.md").write_text("start")
        big, big_data = self.snapshot(data=blend_v1(scene_blocks(objects=20, array_bytes=16 * 1024 * 1024)), ingest=False)
        failures = []

        def ingest():
            try:
                ingest_snapshot(self.store, big["id"])
            except Exception as error:  # Reported below; the test must not hang.
                failures.append(error)

        worker = threading.Thread(target=ingest)
        worker.start()
        captured = []
        for index in range(6):
            record, data = self.snapshot(self.other, data=blend_v1(scene_blocks(objects=8, seed=index + 20)), ingest=False)
            self.write(self.other, record, "data.md", "write %d" % index)
            captured.append((record, data))
        worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        for record, _ in captured:
            ingest_snapshot(self.other, record["id"])
        self.assertEqual(self.store.inspect()["issues"], [])
        self.assertEqual(self.recovered(self.store, self.store.load(big["id"])), big_data)
        for record, data in captured:
            self.assertEqual(self.recovered(self.other, self.other.load(record["id"])), data)
        self.assertEqual(self.other.restore_since(captured[0][0]["id"]), 1)
        self.assertEqual((self.project / "data.md").read_text(), "start")

    def test_invalid_storage_metadata_is_rejected(self):
        record, _ = self.snapshot()
        metadata = self.store.folder(record["id"]) / "checkpoint.json"
        saved = json.loads(metadata.read_text())
        for change in ({"scene_hash": "XYZ"}, {"storage": "cloud"}, {"state": "RESERVED"}, {"scene_hash": None}):
            broken = dict(saved, **change)
            metadata.write_text(json.dumps({key: value for key, value in broken.items() if value is not None}))
            with self.subTest(change=change):
                self.assertTrue(self.store.inspect()["issues"])
                self.assertFalse(self.store.availability(record["id"])["available"])
        metadata.write_text(json.dumps(saved))
        self.assertEqual(self.store.inspect()["issues"], [])


if __name__ == "__main__":
    unittest.main()
