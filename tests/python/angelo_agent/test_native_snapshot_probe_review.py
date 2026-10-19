# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial pins of the snapshot worker's incremental probe, store bound, GC skip and pauses.

Companion of ``test_native_snapshot_probe_cache.py``. Each states what it attacks.

* No scan under the profile transaction ever reuses a kept measurement, across
  a whole maintenance run that removes snapshots and drops orphans.
* Over a grid of count, byte and age limits, protected refs and watermarks, a
  probe that the store bound ends ("nothing to remove, budget met") is one an
  exact plan also ends, with the same count and protection.
* A damaged manifest: under its limits the bound ends the run without reading
  manifests, so it is not "blocked" where the exact plan is; no removal
  differs (pinned as the current behaviour).
* Garbage collection that fails after its commit, or that another process's
  drop made necessary, never loses a referenced chunk, and a failed one runs
  again.
"""

import itertools
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import build_history  # noqa: E402
from angelo_agent import checkpoint_inventory, checkpoint_inventory_cache, checkpoint_transactions  # noqa: E402
from angelo_agent import snapshot_accounting, snapshot_maintenance, snapshot_retention  # noqa: E402
from angelo_agent import snapshot_store_writer  # noqa: E402
from angelo_agent.checkpoint_policy import clean_policy, plan_retention  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402
from angelo_agent.snapshot_worker import SnapshotWorker  # noqa: E402


def history(test, count=14, ingested=7, distinct=False):
    """``build_history`` with the newest ``ingested`` adopted by the store; ``distinct`` scenes share no chunk."""
    temp = tempfile.TemporaryDirectory(prefix="angelo-review-")
    test.addCleanup(temp.cleanup)
    test.addCleanup(checkpoint_inventory_cache.forget)
    root = build_history(Path(temp.name).resolve() / "state", count)
    store = CheckpointStore(root, str(uuid.uuid4()))
    records = sorted(store.inspect()["checkpoints"], key=lambda item: item["boundary"])
    if distinct:
        for index, item in enumerate(records):
            scene = Path(root) / "checkpoints" / item["session_id"] / item["id"] / "scene.blend"
            size = scene.stat().st_size
            scene.write_bytes(os.urandom(size))  # Same size as recorded, content of its own.
            os.utime(scene, ns=(1, 1))
        checkpoint_inventory_cache.forget()
        records = sorted(store.inspect()["checkpoints"], key=lambda item: item["boundary"])
    for item in records[-ingested:]:
        test.assertTrue(ingest_snapshot(CheckpointStore(root, item["session_id"]), item["id"])["adopted"])
    return root, store, records


def index_file(root):
    return next(path for path in SnapshotStore(root).directory.iterdir() if path.suffix in {".sqlite3", ".sqlite", ".db"})


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class LockedScanTests(unittest.TestCase):
    def test_no_scan_under_the_transaction_reuses_a_measurement_in_a_run_that_removes(self):
        root, store, records = history(self)
        with patch.object(checkpoint_inventory_cache, "RACY_SECONDS", 0):
            checkpoint_inventory.inventory(store, probe=True)  # Every record kept: reuse is possible.
            calls, real = [], checkpoint_inventory_cache.scan

            def watched(owner, found, issues, measure):
                calls.append(checkpoint_transactions.holds_any_transaction())
                return real(owner, found, issues, measure)
            fresh, real_measure = [], checkpoint_inventory._measure

            def measured(owner, session, identity, folder):
                if checkpoint_transactions.holds_transaction(owner.root):
                    fresh.append(identity)
                return real_measure(owner, session, identity, folder)
            with patch.object(checkpoint_inventory_cache, "scan", watched), \
                    patch.object(checkpoint_inventory, "_measure", measured):
                SnapshotWorker(root)._maintain(clean_policy({"max_count": 4}), (), 10 ** 6, None, False)
        self.assertTrue(calls, "the probe ran")
        self.assertEqual(set(calls), {False}, "the cache is consulted only outside the transaction")
        self.assertEqual(set(fresh), {item["id"] for item in records}, "the locked scan measured every record afresh")
        remaining = [item for item in store.inspect()["checkpoints"] if item["state"] != "PRUNED"]
        self.assertEqual(len(remaining), 4)


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class StoreBoundTests(unittest.TestCase):
    def setUp(self):
        self.root, self.store, self.records = history(self)

    def test_when_the_bound_ends_a_probe_an_exact_plan_ends_it_the_same_way(self):
        inventory = self.store.inspect()
        exact = snapshot_accounting.store_accounting(self.store, inventory)
        bound = snapshot_accounting.store_bound(self.store)
        files = sum(item["bytes"] for item in inventory["checkpoints"])
        refs = [item["ref"] for item in self.records]
        now = max(item["created"] for item in self.records) + 1
        byte_limits = [0, files + exact.total_bytes - 1, files + exact.total_bytes, files + bound.total_bytes,
                       files + bound.total_bytes + 1, 10 ** 12]
        checked = ended = 0
        for count, size, days, protected in itertools.product((0, 3, 13, 14, 15), byte_limits, (0, 1),
                                                              ((), refs[:2], refs[-3:])):
            policy = clean_policy({"max_count": count, "max_bytes": size, "max_age_days": days})
            for at in (now, now + 86400 * 2):
                with self.subTest(count=count, size=size, days=days, protected=len(protected), at=at - now):
                    by_bound = plan_retention(inventory, policy, protected_refs=protected, now=at, accounting=bound)
                    checked += 1
                    if by_bound["candidates"] or not by_bound["budget_met"] or by_bound["blocked"]:
                        continue
                    ended += 1
                    by_exact = plan_retention(inventory, policy, protected_refs=protected, now=at, accounting=exact)
                    self.assertEqual(by_exact["candidates"], [])
                    self.assertTrue(by_exact["budget_met"])
                    self.assertFalse(by_exact["blocked"])
                    self.assertEqual(by_exact["remaining_count"], by_bound["remaining_count"])
                    self.assertEqual(by_exact["protected"], by_bound["protected"])
                    self.assertLessEqual(by_exact["remaining_bytes"], by_bound["remaining_bytes"])
        self.assertGreater(ended, 10)
        self.assertGreater(checked - ended, 10, "the grid also has runs the bound cannot end")

    def test_a_damaged_manifest_is_not_reported_while_the_bound_ends_the_run(self):
        """NOTE: exact accounting reads every manifest and reports a damaged one ("blocked", a notice asking for
        review); a probe the bound ends reads none. Nothing is removed either way. The first collection of each
        process still reads every manifest and fails on it."""
        connection = sqlite3.connect(str(index_file(self.root)))
        connection.execute("UPDATE manifests SET chunk_ids=? WHERE ref=?", (zlib.compress(b"abc"), self.records[-1]["ref"]))
        connection.commit()
        connection.close()
        snapshot_store_writer._COLLECTED.add(str(SnapshotStore(self.root).directory))  # Collected earlier.
        self.addCleanup(snapshot_store_writer._COLLECTED.discard, str(SnapshotStore(self.root).directory))
        summaries = {}
        for mode in ("bound", "exact"):
            checkpoint_inventory_cache.forget()
            bound = snapshot_accounting.store_bound if mode == "bound" else (lambda checkpoints: None)
            with patch.object(snapshot_retention, "store_bound", bound):
                summaries[mode] = snapshot_retention.enforce_retention(self.store, clean_policy({}), watermark=10 ** 6)
        self.assertEqual((summaries["bound"]["blocked"], summaries["exact"]["blocked"]), (False, True))
        self.assertEqual((summaries["bound"]["removed"], summaries["exact"]["removed"]), ([], []))


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class CollectionSkipTests(unittest.TestCase):
    def setUp(self):
        self.root, self.store, self.records = history(self, distinct=True)
        self.key = str(SnapshotStore(self.root).directory)
        self.addCleanup(snapshot_store_writer._COLLECTED.discard, self.key)

    def referenced_chunks_present(self):
        with SnapshotStore(self.root).reader() as connection:
            ids = {chunk for (blob,) in connection.execute("SELECT chunk_ids FROM manifests")
                   for chunk in snapshot_store_writer.decode_ids(blob)}
            present = {chunk for (chunk,) in connection.execute("SELECT id FROM chunks")}
        return ids <= present

    def test_a_collection_that_fails_after_its_commit_runs_again_and_loses_nothing(self):
        with SnapshotStore(self.root).writer() as writer:
            writer.drop_manifests([self.records[-1]["ref"], self.records[-2]["ref"]])
        real = snapshot_store_writer.StoreWriter.collect_garbage

        def crash_after_commit(writer):  # The process dies after the recount's COMMIT, before marking it collected.
            with patch.object(snapshot_store_writer.StoreWriter, "_checkpoint_wal",
                              side_effect=RuntimeError("crash after commit")):
                return real(writer)
        with patch.object(snapshot_store_writer.StoreWriter, "collect_garbage", crash_after_commit):
            with self.assertRaises(RuntimeError):
                snapshot_maintenance.clean_store(self.root)
        self.assertTrue(snapshot_store_writer.garbage_possible(SnapshotStore(self.root)), "it runs again")
        self.assertTrue(self.referenced_chunks_present())
        snapshot_maintenance.clean_store(self.root)
        self.assertFalse(snapshot_store_writer.garbage_possible(SnapshotStore(self.root)))
        self.assertTrue(self.referenced_chunks_present())

    def test_another_processs_drop_only_leaves_garbage_until_a_later_collection(self):
        snapshot_maintenance.clean_store(self.root)  # This process collected: it skips from now on.
        connection = sqlite3.connect(str(index_file(self.root)))  # Another app instance drops a manifest.
        blob, = connection.execute("SELECT chunk_ids FROM manifests WHERE ref=?", (self.records[-1]["ref"],)).fetchone()
        connection.executemany("UPDATE chunks SET refs = refs - 1 WHERE id = ?",
                               [(chunk,) for chunk in set(snapshot_store_writer.decode_ids(blob))])
        connection.execute("DELETE FROM manifests WHERE ref=?", (self.records[-1]["ref"],))
        connection.commit()
        connection.close()
        result = snapshot_maintenance.clean_store(self.root)
        self.assertEqual(result["chunks_removed"], 0, "skipped: the garbage stays")
        self.assertTrue(self.referenced_chunks_present())
        inventory = self.store.inspect()
        exact = snapshot_accounting.store_accounting(self.store, inventory)
        self.assertGreater(snapshot_accounting.store_bound(self.store).total_bytes, exact.total_bytes,
                           "the bound counts the garbage, so it stays above the exact bytes")
        snapshot_store_writer._COLLECTED.discard(self.key)  # A later process.
        self.assertGreater(snapshot_maintenance.clean_store(self.root)["chunks_removed"], 0)


if __name__ == "__main__":
    unittest.main()
