# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: a request capture's work does not grow with stored snapshots.

Every request captures a snapshot before the model is called, so anything the
capture does per stored snapshot is added to every message, and a long session
paid for its whole history (about 1.2 s per message after 700 snapshots). These
tests count the system calls and record reads of the capture path (reserve,
scene write, metadata save) with 10 and with 1,000 stored snapshots on every
file backend (POSIX descriptors, Windows handles through the NT stand-in
``fake_nt_files.py``, legacy paths), and pin when the whole-history check still
runs: a clock this process did not write, and after a locked check found
damaged history. The profile scan's per-folder walk must refuse a linked chat or
snapshot folder on every backend. They also pin that worker maintenance with
nothing to do never takes the profile transaction the main thread needs.
"""

import collections
import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import HISTORY_SCENE as SCENE, build_history  # noqa: E402
from fake_nt_files import FakeNtApi  # noqa: E402
from angelo_agent import anchored_windows as aw, checkpoint_filesystem as cf  # noqa: E402
from angelo_agent import snapshot_maintenance, snapshot_retention  # noqa: E402
from angelo_agent import checkpoints as checkpoint_store  # noqa: E402
from angelo_agent.checkpoint_policy import clean_policy  # noqa: E402
from angelo_agent.checkpoint_timeline import NAME, forget_trust  # noqa: E402
from angelo_agent.checkpoint_transactions import _transaction, holds_transaction  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402

COUNTED = ("open", "stat", "fstat", "listdir", "scandir", "mkdir", "replace", "unlink", "fsync")
# POSIX descriptors, Windows handles (the host tests' NT stand-in) and legacy paths.
BACKENDS = ("posix", "path") if os.name == "nt" else ("posix", "windows", "path")


@contextmanager
def counted_system_calls():
    """Count file-system calls through ``os``; ``record_reads`` counts checkpoint.json opens."""
    counts = collections.Counter()
    wrappers = {}
    for name in COUNTED:
        real = getattr(os, name)

        def call(*args, _name=name, _real=real, **kwargs):
            counts[_name] += 1
            if _name == "open" and os.fspath(args[0]).endswith("checkpoint.json"):
                counts["record_reads"] += 1  # Metadata is only ever written through temporary names.
            return _real(*args, **kwargs)
        wrappers[real] = call
    # Descriptor-relative support is checked by identity (``os.open in os.supports_dir_fd``).
    sets = {key: getattr(os, key) | {wrappers[f] for f in getattr(os, key) if f in wrappers}
            for key in ("supports_dir_fd", "supports_fd", "supports_follow_symlinks")}
    with patch.multiple(os, **{name: wrappers[getattr(os, name)] for name in COUNTED}, **sets):
        yield counts


def capture(store):
    """The CheckpointStore part of ui.capture_checkpoint; Blender's save is a plain write."""
    record, path = store.reserve("")
    path.write_bytes(SCENE)
    os.chmod(path, 0o600)
    record["scene_bytes"] = path.stat().st_size
    store.save(record)
    return record


@contextmanager
def backend(kind):
    """Select ``checkpoint_filesystem``'s backend; yields the NT stand-in for ``windows``."""
    if kind == "windows":
        api = FakeNtApi()
        with patch.object(cf, "_windows_api", return_value=api):
            yield api
    elif kind == "path":
        with patch.object(os, "supports_dir_fd", set()):
            yield None
    else:
        yield None


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class CaptureHistoryScalingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-history-")
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def profile(self, count, label=""):
        folder = self.base / ("profile-%s%d" % (label, count))
        os.mkdir(folder)
        return CheckpointStore(build_history(folder / "state", count), str(uuid.uuid4()))

    def capture_work(self, kind, count):
        """[first, steady] capture work with ``count`` stored snapshots on backend ``kind``.

        The Windows stand-in's handle requests are counted too, and a record read
        there is an ``open_at`` of checkpoint.json for reading (its ``lstat`` is an
        attribute-only open, which the os-level heuristic would also count).
        """
        store = self.profile(count, kind)
        work = []
        with backend(kind) as api:
            for _ in range(2):
                mark = len(api.calls) if api else 0
                current_reads, original_read = [], checkpoint_store._read_json
                def read_current(*args):
                    record = original_read(*args)
                    current_reads.append(record["id"])
                    return record
                with counted_system_calls() as counts, patch.object(checkpoint_store, "_read_json", read_current):
                    record = capture(store)
                self.assertEqual(current_reads, [record["id"]])
                counts["current_record_reads"] = len(current_reads)
                if api:
                    calls = api.calls[mark:]
                    counts.update("nt_" + call[0] for call in calls)
                    counts["record_reads"] = sum(1 for call in calls if call[:2] == ("open_at", "checkpoint.json")
                                                 and call[2] == aw.FILE_GENERIC_READ)
                work.append(counts)
        return work

    def test_capture_work_is_the_same_with_10_and_1000_stored_snapshots(self):
        for kind in BACKENDS:
            with self.subTest(backend=kind):
                small_first, small = self.capture_work(kind, 10)
                large_first, large = self.capture_work(kind, 1000)
                self.assertEqual(small, large)
                # One freshly checked current record; no history reads/listings.
                # Remaining calls depend on profile depth, never history size.
                self.assertEqual((large["record_reads"], large["current_record_reads"],
                                  large["listdir"], large["scandir"]), (1, 1, 0, 0))
                # First capture reads each historical record plus its own current one.
                self.assertEqual((small_first["record_reads"], large_first["record_reads"]), (11, 1001))
                self.assertEqual((small_first["current_record_reads"], large_first["current_record_reads"]), (1, 1))

    def test_every_backend_lists_the_same_history_and_captures(self):
        store = self.profile(30)
        capture(store)
        tokens = set()
        for kind in BACKENDS:  # Records, sizes, file identities and fingerprints all agree.
            with backend(kind):
                view = store.inspect()
            self.assertEqual((len(view["checkpoints"]), view["issues"]), (31, []))
            tokens.add(view["token"])
        self.assertEqual(len(tokens), 1)
        for kind in BACKENDS:
            with self.subTest(backend=kind), backend(kind):
                record = capture(store)
                self.assertEqual(store.load(record["id"])["state"], "READY")
        self.assertEqual(len(store.inspect()["checkpoints"]), 31 + len(BACKENDS))
        self.assertEqual(store.inspect()["issues"], [])

    def test_a_linked_chat_or_snapshot_folder_is_refused_on_every_backend(self):
        store = self.profile(14)  # Two chats of seven.
        foreign = self.profile(7, "foreign").root / "checkpoints"
        foreign_chat = next(foreign.iterdir())
        chats = sorted((store.root / "checkpoints").iterdir())
        os.rename(chats[0], self.base / "moved-chat")
        os.symlink(foreign_chat, chats[0], target_is_directory=True)
        snapshot = sorted(chats[1].iterdir())[0]
        os.rename(snapshot, self.base / "moved-snapshot")
        os.symlink(next(foreign_chat.iterdir()), snapshot, target_is_directory=True)
        for kind in BACKENDS:
            with self.subTest(backend=kind), backend(kind):
                view = store.inspect()
                self.assertEqual(len(view["checkpoints"]), 6)  # The other chat's real snapshots only.
                linked = [issue for issue in view["issues"] if "cannot follow directory links" in issue["error"]]
                self.assertEqual(sorted(("id" in issue, issue["session_id"]) for issue in linked),
                                 sorted([(False, chats[0].name), (True, chats[1].name)]))

    def test_clock_written_elsewhere_is_validated_against_the_whole_history(self):
        store = self.profile(40)
        capture(store)
        clock = store.root / NAME
        replacement = store.root / "foreign-clock.tmp"
        replacement.write_text(clock.read_text())  # Another process's identical, atomic rewrite.
        os.replace(replacement, clock)
        with counted_system_calls() as foreign:
            capture(store)
        with counted_system_calls() as trusted:
            capture(store)
        self.assertEqual((foreign["record_reads"], trusted["record_reads"]), (42, 1))
        stale = json.loads(clock.read_text())
        clock.write_text(json.dumps(dict(stale, sequence=stale["sequence"] - 2, boundary=stale["sequence"] - 2)))
        with self.assertRaisesRegex(ValueError, "order conflicts"):
            capture(store)

    def test_damaged_history_found_under_the_transaction_stops_captures(self):
        store = self.profile(20)
        capture(store)
        victim = next(item for item in store.inspect()["checkpoints"] if item["session_id"] != store.session_id)
        (store.root / "checkpoints" / victim["session_id"] / victim["id"] / "checkpoint.json").write_text("{")
        # Admitted with a later order: the trusted clock is newer than every recorded order.
        admitted = capture(store)
        self.assertEqual(admitted["boundary"], 22)
        self.assertTrue(store.inspect()["issues"])  # The locked whole-history check withdraws the trust.
        with self.assertRaisesRegex(ValueError, "cannot admit another event"):
            capture(store)
        forget_trust(store.root)
        with self.assertRaisesRegex(ValueError, "cannot admit another event"):
            capture(store)


@unittest.skipUnless(os.open in os.supports_dir_fd, "descriptor-relative storage required")
class MaintenanceProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-history-probe-")
        root = build_history(Path(self.temp.name).resolve() / "state", 60)
        self.store = CheckpointStore(root, str(uuid.uuid4()))
        # Manifests for the newest ten, as the snapshot worker stores them.
        for item in sorted(self.store.inspect()["checkpoints"], key=lambda item: item["boundary"])[-10:]:
            self.assertTrue(ingest_snapshot(CheckpointStore(root, item["session_id"]), item["id"])["adopted"])

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def counted_transactions(self):
        holds = []

        @contextmanager
        def counting(root):
            holds.append(root)
            with _transaction(root):
                yield
        with patch.object(snapshot_retention, "_transaction", counting), \
                patch.object(snapshot_maintenance, "_transaction", counting):
            yield holds

    def test_under_budget_retention_and_orphan_cleanup_never_lock(self):
        with self.counted_transactions() as holds:
            summary = snapshot_retention.enforce_retention(self.store, clean_policy(), watermark=10 ** 6)
            orphans = snapshot_maintenance.drop_orphan_manifests(self.store, SnapshotStore(self.store.root))
        self.assertEqual((summary["removed"], summary["budget_met"], summary["blocked"], orphans), ([], True, False, 0))
        self.assertEqual(holds, [])

    def test_over_limit_retention_plans_and_removes_under_the_transaction(self):
        with self.counted_transactions() as holds:
            summary = snapshot_retention.enforce_retention(self.store, clean_policy({"max_count": 50}), watermark=10 ** 6)
        self.assertEqual((len(summary["removed"]), summary["budget_met"]), (10, True))
        self.assertEqual(len(holds), 1)
        self.assertEqual(len([item for item in self.store.inspect()["checkpoints"] if item["state"] == "READY"]), 50)

    def test_probe_that_sees_a_write_in_progress_repeats_the_plan_under_the_transaction(self):
        real, calls = snapshot_retention._plan, []

        def racing(checkpoints, *args, **kwargs):
            plan, inventory = real(checkpoints, *args, **kwargs)
            calls.append(holds_transaction(checkpoints.root))
            if len(calls) == 1:  # The unlocked probe met a reservation still being written.
                plan = dict(plan, blocked=True, candidates=[])
            return plan, inventory
        with patch.object(snapshot_retention, "_plan", racing):
            summary = snapshot_retention.enforce_retention(self.store, clean_policy({"max_count": 55}), watermark=10 ** 6)
        self.assertEqual(calls, [False, True, True])
        self.assertEqual((len(summary["removed"]), summary["blocked"]), (5, False))


if __name__ == "__main__":
    unittest.main()
