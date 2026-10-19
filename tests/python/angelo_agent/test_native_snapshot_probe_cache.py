# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The snapshot worker's lock-free probe re-reads only changed records, and gives what a full scan gives.

After every capture the worker probes the whole history (``inventory(...,
probe=True)``). It reuses a record's earlier measurement while the record's
folder and files are unchanged (``checkpoint_inventory_cache``). These tests
pin that the probe's result always equals a fresh scan's, through every kind
of change to a record, that an unchanged settled history is not read again,
that recent changes and anything under the transaction are always measured,
and that the worker's long passes pause for the main thread only where that
is safe (``background_pauses``).
"""

import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import HISTORY_SCENE, build_history  # noqa: E402
from angelo_agent import background_pauses, checkpoint_inventory, checkpoint_inventory_cache  # noqa: E402
from angelo_agent import snapshot_accounting, snapshot_maintenance  # noqa: E402
from angelo_agent import snapshot_retention, snapshot_store_writer  # noqa: E402
from angelo_agent.checkpoint_policy import clean_policy  # noqa: E402
from angelo_agent.checkpoint_transactions import _transaction  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402


@contextmanager
def measured():
    """Records measured (read and validated) during the block."""
    real, calls = checkpoint_inventory._measure, []

    def counting(owner, session, identity, folder):
        calls.append(identity)
        return real(owner, session, identity, folder)
    with patch.object(checkpoint_inventory, "_measure", counting):
        yield calls


def comparable(result):
    """Everything an inventory states but its token (a probe has none: it never backs a removal)."""
    return result["checkpoints"], sorted(map(repr, result["issues"])), result["total_bytes"]


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class ProbeCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-probe-")
        self.addCleanup(temp.cleanup)
        self.root = build_history(Path(temp.name).resolve() / "state", 30)
        self.store = CheckpointStore(self.root, str(uuid.uuid4()))
        self.addCleanup(checkpoint_inventory_cache.forget)
        racy = patch.object(checkpoint_inventory_cache, "RACY_SECONDS", 0)  # The fixture's files are seconds old.
        racy.start()
        self.addCleanup(racy.stop)

    def probe(self, validate_targets=True):
        return checkpoint_inventory.inventory(self.store, validate_targets=validate_targets, probe=True)

    def fresh(self, validate_targets=True):
        return checkpoint_inventory.inventory(self.store, validate_targets=validate_targets)

    def assert_probe_is_fresh(self):
        for validate in (True, False):
            self.assertEqual(comparable(self.probe(validate)), comparable(self.fresh(validate)))

    def folder(self, index):
        sessions = sorted((self.root_path / "checkpoints").iterdir())
        records = sorted(path for session in sessions for path in session.iterdir())
        return records[index]

    @property
    def root_path(self):
        return Path(self.root)

    def test_an_unchanged_history_is_not_read_again_and_equals_a_fresh_scan(self):
        with measured() as first:
            self.probe()
        with measured() as second:
            result = self.probe()
        self.assertEqual((len(first), len(second)), (30, 0))
        self.assertEqual(comparable(result), comparable(self.fresh()))
        self.assertIsNone(result["token"])

    def test_every_change_to_a_record_is_measured_again(self):
        self.probe()
        time.sleep(0.01)  # Every change below gets a later change time than the measurement kept.
        changes = {
            "record rewritten": lambda folder: (folder / "checkpoint.json").write_text(
                (folder / "checkpoint.json").read_text().replace('"protected": false', '"protected": true')),
            "scene written in place, same size": lambda folder: (folder / "scene.blend").write_bytes(
                bytes(reversed(HISTORY_SCENE))),
            "times set back": lambda folder: os.utime(folder / "scene.blend", ns=(1, 1)),
            "file added": lambda folder: (folder / "stray.bin").write_bytes(b"x"),
            "scene removed": lambda folder: (folder / "scene.blend").unlink(),
            "folder replaced": lambda folder: (os.rename(folder, folder.with_name("moved")), os.mkdir(folder),
                                               os.rename(folder.with_name("moved") / "checkpoint.json",
                                                         folder / "checkpoint.json")),
            "folder replaced by a link": lambda folder: (os.rename(folder, folder.with_name("aside")),
                                                         os.symlink(folder.with_name("aside"), folder)),
        }
        for index, (name, change) in enumerate(changes.items()):
            with self.subTest(change=name):
                folder = self.folder(index * 3)
                change(folder)
                with measured() as again:
                    self.assert_probe_is_fresh()
                if not folder.is_symlink():  # A link is refused before any measurement, as in a fresh scan.
                    self.assertIn(folder.name, again, "the changed record was measured again")

    def test_records_changed_within_the_racy_window_are_always_measured(self):
        with patch.object(checkpoint_inventory_cache, "RACY_SECONDS", 3600):
            self.probe()
            with measured() as again:
                self.probe()
        self.assertEqual(len(again), 30, "no measurement newer than the file-system clock's resolution is kept")

    def test_under_the_transaction_every_record_is_measured(self):
        self.probe()
        with _transaction(self.root):
            with measured() as again:
                locked = self.probe()
            fresh = self.fresh()  # Under the transaction a scan also states each record's capture activity.
        self.assertEqual(len(again), 30)
        self.assertEqual(comparable(locked), comparable(fresh))
        self.assertEqual(locked["token"], fresh["token"])

    def test_a_record_removed_or_added_is_seen(self):
        self.probe()
        (self.folder(0) / "checkpoint.json").unlink()
        (self.folder(0) / "scene.blend").unlink()
        os.rmdir(self.folder(0))
        session = sorted((self.root_path / "checkpoints").iterdir())[0]
        os.mkdir(session / str(uuid.uuid4()))  # A reservation still being written: an issue, as in a fresh scan.
        self.assert_probe_is_fresh()


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class GarbageCollectionTests(unittest.TestCase):
    """The per-capture cleanup recounts the store only when a dropped manifest can have left garbage."""

    def test_collects_once_per_process_then_only_after_a_manifest_is_dropped(self):
        with tempfile.TemporaryDirectory(prefix="angelo-gc-") as temp:
            root = build_history(Path(temp).resolve() / "state", 12)
            store = CheckpointStore(root, str(uuid.uuid4()))
            records = sorted(store.inspect()["checkpoints"], key=lambda item: item["boundary"])
            for item in records[-4:]:
                self.assertTrue(ingest_snapshot(CheckpointStore(root, item["session_id"]), item["id"])["adopted"])
            real, runs = snapshot_store_writer.StoreWriter.collect_garbage, []

            def counted(writer):
                runs.append(writer.store.directory)
                return real(writer)
            with patch.object(snapshot_store_writer.StoreWriter, "collect_garbage", counted):
                snapshot_store_writer._COLLECTED.discard(str(SnapshotStore(root).directory))  # A new process.
                snapshot_maintenance.clean_store(root)
                snapshot_maintenance.clean_store(root)
                self.assertEqual(len(runs), 1, "nothing was dropped since the first collection")
                with SnapshotStore(root).writer() as writer:  # Every manifest: the shared chunks become garbage.
                    self.assertEqual(writer.drop_manifests([item["ref"] for item in records[-4:]]), 4)
                result = snapshot_maintenance.clean_store(root)
                self.assertEqual(len(runs), 2)
                self.assertGreater(result["chunks_removed"] + result["bytes_released"], 0)
                snapshot_maintenance.clean_store(root)
                self.assertEqual(len(runs), 2)


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class StoreBoundTests(unittest.TestCase):
    """A probe under its limits plans from an upper bound of the store's bytes; near them, from every manifest."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-bound-")
        self.addCleanup(temp.cleanup)
        self.root = build_history(Path(temp.name).resolve() / "state", 12)
        self.store = CheckpointStore(self.root, str(uuid.uuid4()))
        for item in sorted(self.store.inspect()["checkpoints"], key=lambda item: item["boundary"])[-6:]:
            self.assertTrue(ingest_snapshot(CheckpointStore(self.root, item["session_id"]), item["id"])["adopted"])
        self.addCleanup(checkpoint_inventory_cache.forget)

    def enforce(self, policy):
        real, exact = snapshot_accounting.StoreAccounting.load, []

        def counted(store, live_refs, **kwargs):
            exact.append(store)
            return real(store, live_refs, **kwargs)
        with patch.object(snapshot_accounting.StoreAccounting, "load", counted):
            summary = snapshot_retention.enforce_retention(self.store, clean_policy(policy), watermark=10 ** 6)
        return summary, len(exact)

    def test_a_maintenance_run_that_removes_nothing_scans_the_history_once(self):
        from angelo_agent import checkpoints as checkpoint_module
        from angelo_agent.snapshot_worker import SnapshotWorker
        real, scans = checkpoint_inventory.inventory, []

        def counted(*args, **kwargs):
            scans.append(kwargs.get("probe"))
            return real(*args, **kwargs)
        with patch.object(checkpoint_module, "inventory", counted), \
                patch.object(snapshot_maintenance, "inventory", counted):
            SnapshotWorker(self.root)._maintain(clean_policy({}), (), 10 ** 6, None, False)
        self.assertEqual(scans, [True], "the orphan probe shares the retention probe's scan")

    def test_a_damaged_manifest_is_an_accounting_issue_not_a_crash(self):
        import sqlite3
        with sqlite3.connect(str(SnapshotStore(self.root).directory / "index.sqlite")) as connection:
            connection.execute("UPDATE manifests SET chunk_ids = ? WHERE rowid = (SELECT MIN(rowid) FROM manifests)",
                               (b"not zlib",))
        inventory = self.store.inspect()
        self.assertIsNone(snapshot_accounting.store_accounting(self.store, inventory))
        self.assertTrue(any("accounting is unavailable" in issue["error"] for issue in inventory["issues"]))

    def test_the_bound_is_never_below_the_exact_store_bytes(self):
        inventory = self.store.inspect()
        exact = snapshot_accounting.store_accounting(self.store, inventory)
        bound = snapshot_accounting.store_bound(self.store)
        self.assertGreaterEqual(bound.total_bytes, exact.total_bytes)
        self.assertGreaterEqual(bound.pressure_bytes, exact.pressure_bytes)

    def test_within_its_limits_a_probe_reads_no_manifest(self):
        summary, exact = self.enforce({})  # The default byte budget, far above this history.
        self.assertEqual((summary["removed"], summary["budget_met"], exact), ([], True, 0))

    def test_near_its_limits_a_probe_plans_exactly_and_removes_what_it_did_before(self):
        inventory = self.store.inspect()
        exact = snapshot_accounting.store_accounting(self.store, inventory)
        records = sum(item["bytes"] for item in inventory["checkpoints"])
        # Garbage the last collection has not removed yet: the bound counts it, the exact accounting does not.
        garbage = snapshot_accounting.StoreBound(exact.total_bytes + 64 * 1024)
        budget = records + exact.pressure_bytes + 2048  # Between the exact bytes and the bound.
        with patch.object(snapshot_accounting.StoreBound, "load", return_value=garbage):
            summary, reads = self.enforce({"max_bytes": budget})
        self.assertGreater(reads, 0, "the bound alone would call the budget unmet: the probe planned exactly")
        self.assertEqual((summary["removed"], summary["budget_met"]), ([], True))
        summary, _ = self.enforce({"max_count": 8})  # A count limit never rests on the bound.
        self.assertEqual(len(summary["removed"]), 4)


class Clock:
    """A fake ``perf_counter`` and ``sleep``: work advances it by ``step``, a sleep by ``overrun`` x the time asked."""

    def __init__(self, step, overrun=1.0):
        self.now, self.step, self.overrun, self.slept = 0.0, step, overrun, []

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds * self.overrun

    def work(self):
        self.now += self.step


class BackgroundPauseTests(unittest.TestCase):
    def run_pass(self, enabled, transaction_root=None, items=5, clock=None):
        """``items`` items of a pass on a new thread, each a work step then ``pause()``; returns the clock."""
        clock = clock or Clock(step=background_pauses.WORK_SECONDS * 1.2)

        def work():
            with patch.object(background_pauses.time, "perf_counter", clock.perf_counter), \
                    patch.object(background_pauses.time, "sleep", clock.sleep):
                if enabled:
                    background_pauses.enable()
                for _ in range(items):
                    clock.work()
                    if transaction_root is not None:
                        with _transaction(transaction_root):
                            background_pauses.pause()
                    else:
                        background_pauses.pause()
        thread = threading.Thread(target=work)
        thread.start()
        thread.join(10)
        return clock

    def test_an_enabled_thread_pauses_after_each_work_interval(self):
        self.assertEqual(self.run_pass(True).slept, [background_pauses.PAUSE_SECONDS] * 5)

    def test_a_sleep_that_overruns_never_raises_the_paused_share_above_its_budget(self):
        clock = self.run_pass(True, items=2000, clock=Clock(step=background_pauses.WORK_SECONDS / 10, overrun=3.0))
        paused = sum(clock.slept) * 3.0
        self.assertTrue(clock.slept)
        self.assertLessEqual(paused / clock.now, background_pauses.PAUSE_SHARE + 0.01)

    def test_a_sleep_stretched_far_past_its_time_buys_at_most_the_largest_work_budget(self):
        clock = self.run_pass(True, items=400, clock=Clock(step=background_pauses.WORK_SECONDS / 10, overrun=100.0))
        # 0.2 ms asked, 20 ms slept: without the cap the next 47 ms would be work without a pause.
        self.assertGreaterEqual(len(clock.slept), 400 * background_pauses.WORK_SECONDS / 10
                                / background_pauses.MAX_WORK_SECONDS - 1)

    def test_any_other_thread_never_pauses(self):
        self.assertEqual(self.run_pass(False).slept, [])
        background_pauses.pause()  # This (main) thread never enabled pauses: a no-op.

    def test_no_pause_while_the_thread_holds_a_profile_transaction(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.run_pass(True, transaction_root=Path(root).resolve()).slept, [])

    def test_garbage_collection_holding_the_store_lock_never_pauses(self):
        import ast
        source = (ADDON / "angelo_agent/snapshot_store_writer.py").read_text(encoding="utf-8")
        calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
                 and getattr(node.func, "id", getattr(node.func, "attr", None)) == "pause"]
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
