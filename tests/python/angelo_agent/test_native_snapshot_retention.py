# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: automatic snapshot retention over deduplicated storage.

The saved policy is enforced automatically, oldest first,
with a 2 GiB default storage budget; every existing protection still applies.
"""

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import blend_v1  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent import snapshot_service  # noqa: E402
from angelo_agent.checkpoint_policy import (DEFAULT_MAX_BYTES, CheckpointPolicyStore,  # noqa: E402
                                             checkpoint_ref, clean_policy)
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.snapshot_maintenance import clean_store  # noqa: E402
from angelo_agent.snapshot_retention import enforce_retention  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402
from angelo_agent.snapshot_worker import SnapshotWorker  # noqa: E402

MIB = 1024 * 1024


def document(seed, shared=b"", size=MIB):
    """Mostly distinct incompressible data, optionally with a shared block."""
    blocks = [(b"OB\0\0", b"object %d" % seed + bytes(500), 1), (b"DATA", os.urandom(size), 2)]
    if shared:
        blocks.append((b"DATA", shared, 3))
    return blend_v1(blocks)


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class AutomaticRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-retention-")
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.other = CheckpointStore(self.store.root, str(uuid.uuid4()))
        self.chunks = SnapshotStore(self.store.root)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, store=None, data=None, purpose="request", original=None):
        store = store or self.store
        record, path = store.reserve(str(original or self.project / "original.blend"), purpose=purpose)
        path.write_bytes(data if data is not None else document(len(os.listdir(store.directory)) if store.directory.exists() else 0))
        record = store.complete(record["id"])
        ingest_snapshot(store, record["id"])
        return store.load(record["id"])

    def ref(self, record, store=None):
        return checkpoint_ref((store or self.store).session_id, record["id"])

    def total_bytes(self):
        inventory = self.store.inspect()
        usage = self.chunks.usage()
        return inventory["total_bytes"] + usage["live_bytes"] + usage["index_bytes"]

    def test_default_budget_is_two_gib_and_small_history_is_untouched(self):
        self.assertEqual(clean_policy()["max_bytes"], 2 * 1024 ** 3)
        records = [self.snapshot() for _ in range(3)]
        summary = enforce_retention(self.store, None)
        self.assertEqual(summary["removed"], [])
        self.assertTrue(summary["budget_met"])
        self.assertTrue(all(self.store.availability(record["id"])["available"] for record in records))

    def test_storage_budget_removes_oldest_first_and_leaves_tombstones(self):
        records = [self.snapshot(data=document(index)) for index in range(5)]
        budget = int(2.6 * MIB) + 400 * 1024
        summary = enforce_retention(self.store, {"max_bytes": budget})
        self.assertEqual(summary["removed"], [self.ref(record) for record in records[:3]])
        self.assertTrue(summary["budget_met"])
        for record in records[:3]:
            self.assertEqual(self.store.availability(record["id"])["state"], "PRUNED")
        for record in records[3:]:
            self.assertTrue(self.store.availability(record["id"])["available"])
        clean_store(self.store.root)
        self.assertLessEqual(self.total_bytes(), budget)
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_shared_blocks_count_once_and_removal_releases_only_exclusive_data(self):
        shared = os.urandom(3 * MIB)
        records = [self.snapshot(data=document(index, shared, size=200 * 1024)) for index in range(4)]
        plan = self.store.prune_plan({"max_count": 3, "max_bytes": 0})
        self.assertEqual([item["id"] for item in plan["candidates"]], [records[0]["id"]])
        self.assertLess(plan["candidates"][0]["store_bytes"], MIB, "the shared 3 MiB stays referenced")
        self.assertLess(plan["store_bytes"], 5 * MIB, "four snapshots share one copy of the large block")
        everything = self.store.prune_plan({"max_count": 1, "max_bytes": 0})
        self.assertEqual(len(everything["candidates"]), 3)
        self.assertLess(sum(item["store_bytes"] for item in everything["candidates"]), MIB)

    def test_every_protection_survives_an_impossible_budget(self):
        (self.project / "notes.md").write_text("original")
        files = FileWorkspace(self.project)
        active_a = self.snapshot(data=document(1))
        safety = self.snapshot(data=document(2), purpose="safety")
        chain_owner = self.snapshot(data=document(3))
        active_b = self.snapshot(self.other, data=document(4))
        files.execute("angelo_write_file", {"path": "notes.md", "content": "late write"}, self.store, chain_owner["id"])
        files.execute("angelo_write_file", {"path": "notes.md", "content": "later"}, self.store, active_a["id"])
        unprotected = self.snapshot(data=document(5))
        refs = [self.ref(active_a), self.ref(active_b, self.other)]
        summary = enforce_retention(self.store, {"max_bytes": 1}, protected_refs=refs)
        self.assertEqual(summary["removed"], [self.ref(unprotected)])
        self.assertFalse(summary["budget_met"])
        reasons = {item["ref"]: item["reason"] for item in summary["protected"]}
        self.assertEqual(reasons[self.ref(active_a)], "active/request reference")
        self.assertEqual(reasons[self.ref(active_b, self.other)], "active/request reference")
        self.assertEqual(reasons[self.ref(safety)], "safety/protected snapshot")
        self.assertEqual(reasons[self.ref(chain_owner)], "required by a retained file restore chain")
        for record in (active_a, safety, chain_owner):
            self.assertTrue(self.store.availability(record["id"])["available"])
        self.assertIn("protected", snapshot_service.notice_text({"kind": "maintained", "summary": summary}))
        # The retained chain owner's before-image is what makes this restore exact.
        self.assertEqual(self.store.restore_since(active_a["id"]), 1)
        self.assertEqual((self.project / "notes.md").read_text(), "original")

    def test_snapshots_captured_after_protection_was_collected_are_kept(self):
        older = self.snapshot(data=document(1))
        watermark = json.loads((self.store.root / "checkpoint-timeline.json").read_text())["sequence"]
        newer = self.snapshot(data=document(2))
        summary = enforce_retention(self.store, {"max_bytes": 1}, watermark=watermark)
        self.assertEqual(summary["removed"], [self.ref(older)])
        self.assertTrue(self.store.availability(newer["id"])["available"])
        self.assertIn({"ref": self.ref(newer), "reason": "active/request reference"}, summary["protected"])

    def test_unlimited_and_count_and_age_limits(self):
        records = [self.snapshot(data=document(index)) for index in range(3)]
        self.assertEqual(enforce_retention(self.store, {"max_bytes": 0})["removed"], [])
        self.assertEqual(enforce_retention(self.store, {"max_bytes": 0, "max_count": 2})["removed"], [self.ref(records[0])])
        later = records[2]["created"] + 3 * 86400
        summary = enforce_retention(self.store, {"max_bytes": 0, "max_age_days": 1}, now=later)
        self.assertEqual(summary["removed"], [self.ref(records[1]), self.ref(records[2])])

    def test_preferred_git_superseded_points_go_first_but_protection_wins(self):
        committed = self.root / "repo" / "scene.blend"
        committed.parent.mkdir()
        old_other = self.snapshot(data=document(1))
        superseded = self.snapshot(data=document(2), original=committed)
        protected = self.snapshot(data=document(3), original=committed)
        current = self.snapshot(data=document(4), original=committed)
        signal = Mock(return_value={str(committed): current["created"]})
        summary = enforce_retention(self.store, {"max_count": 3, "max_bytes": 0},
                                    protected_refs=[self.ref(protected)], git_signal=signal)
        self.assertEqual(summary["removed"], [self.ref(superseded)])
        self.assertEqual(summary["preferred"], [self.ref(superseded)])
        self.assertTrue(self.store.availability(old_other["id"])["available"])
        signal.assert_called_once()
        self.assertIn("git commit", snapshot_service.notice_text({"kind": "maintained", "summary": summary}))
        broken = Mock(side_effect=RuntimeError("git exploded"))
        summary = enforce_retention(self.store, {"max_count": 2, "max_bytes": 0}, git_signal=broken)
        self.assertEqual(summary["removed"], [self.ref(old_other)])

    def test_git_is_not_consulted_when_nothing_must_be_removed(self):
        self.snapshot()
        signal = Mock(return_value={})
        enforce_retention(self.store, None, git_signal=signal)
        signal.assert_not_called()

    def test_malformed_history_blocks_removal(self):
        records = [self.snapshot(data=document(index)) for index in range(2)]
        (self.store.folder(records[1]["id"]) / "unexpected.txt").write_text("keep")
        summary = enforce_retention(self.store, {"max_bytes": 1})
        self.assertTrue(summary["blocked"])
        self.assertEqual(summary["removed"], [])
        self.assertIn("needs review", snapshot_service.notice_text({"kind": "maintained", "summary": summary}))

    def test_version_one_settings_migrate_to_the_storage_default(self):
        self.assertEqual(clean_policy({"version": 1, "max_bytes": 0})["max_bytes"], DEFAULT_MAX_BYTES)
        self.assertEqual(clean_policy({"version": 1, "max_bytes": 5})["max_bytes"], 5)
        self.assertEqual(clean_policy({"version": 1})["version"], 2)
        self.assertEqual(clean_policy({"version": 2, "max_bytes": 0})["max_bytes"], 0)
        settings = CheckpointPolicyStore(self.store.root)
        self.store.root.mkdir(parents=True, exist_ok=True)
        (self.store.root / "checkpoint-policy.json").write_text(json.dumps(
            {"version": 1, "capture_mode": "EVERY_REQUEST", "max_count": 0, "max_bytes": 0, "max_age_days": 0}))
        self.assertEqual(settings.load()["max_bytes"], DEFAULT_MAX_BYTES)
        self.assertEqual(settings.error, "")

    def test_unreadable_settings_pause_automatic_removal(self):
        records = [self.snapshot(data=document(index)) for index in range(2)]
        (self.store.root / "checkpoint-policy.json").write_text("{not json")
        settings = CheckpointPolicyStore(self.store.root)
        policy = settings.load()
        self.assertIn("paused", settings.error)
        worker = SnapshotWorker(self.store.root)
        worker.submit_maintenance(policy, (), 0, paused=bool(settings.error))
        worker.run_pending()
        event = worker.events.get_nowait()
        self.assertTrue(event["summary"]["paused"])
        self.assertIn("paused", snapshot_service.notice_text(event))
        self.assertTrue(all(self.store.availability(record["id"])["available"] for record in records))


class RetentionNoticeTests(unittest.TestCase):
    def setUp(self):
        snapshot_service._shown.clear()
        self.controller = SimpleNamespace(emit=Mock())
        self.ui = SimpleNamespace(_controllers={"chat": self.controller}, _controller=self.controller)

    def summary(self, **values):
        base = {"removed": [], "budget_met": True, "blocked": False, "paused": False, "protected": [],
                "preferred": [], "policy": clean_policy(), "remaining_bytes": 3 * 1024 ** 3, "remaining_count": 9,
                "released_bytes": 0}
        return dict(base, **values)

    def test_removals_are_always_reported_and_repeated_conditions_are_not(self):
        removed = {"kind": "maintained", "notify": "chat",
                   "summary": self.summary(removed=["a/b"], released_bytes=3 * MIB)}
        text = snapshot_service.notice_text(removed)
        self.assertIn("removed 1 oldest restore point (3.0 MiB)", text)
        self.assertIn("unavailable", text)
        unmet = {"kind": "maintained", "notify": "chat",
                 "summary": self.summary(budget_met=False, protected=[{"ref": "a/c", "reason": "safety/protected snapshot"}])}
        self.assertIn("3.0 GiB of 2.0 GiB", snapshot_service.notice_text(unmet))
        for event in (removed, removed, unmet, unmet):
            snapshot_service._post(self.ui, event, snapshot_service.notice_text(event))
        self.assertEqual(self.controller.emit.call_count, 3)
        snapshot_service._forget_resolved(self.summary())
        snapshot_service._post(self.ui, unmet, snapshot_service.notice_text(unmet))
        self.assertEqual(self.controller.emit.call_count, 4)

    def test_quiet_success_and_failure_wording(self):
        self.assertIsNone(snapshot_service.notice_text({"kind": "maintained", "summary": self.summary()}))
        failed = snapshot_service.notice_text({"kind": "failed", "task": "ingest", "error": "disk full"})
        self.assertIn("deduplicating a snapshot: disk full", failed)
        self.assertIn("stay available", failed)


if __name__ == "__main__":
    unittest.main()
