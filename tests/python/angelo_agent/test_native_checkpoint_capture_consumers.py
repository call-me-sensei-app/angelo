# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned host fixtures for structural v4 preservation and scene refusal.

V4 metadata/payload bytes are synthetic declarations, never native-produced or
verified Sculpt companions. No producer, Blender, provider or native reader.
"""

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import blend_v1
from angelo_agent import (checkpoint_capture_state, checkpoint_filesystem, checkpoint_inventory, checkpoint_restore,
                          checkpoints, snapshot_ingest, snapshot_maintenance)
from angelo_agent.checkpoint_capture_pins import acquire_capture_pin, abandon_capture_pin, release_capture_pin
from angelo_agent.checkpoint_capture_records import validate_record
from angelo_agent.checkpoint_capture_state import decode_record, require_capture_idle
from angelo_agent.checkpoint_filesystem import anchored_files_supported
from angelo_agent.checkpoint_policy import checkpoint_ref, plan_retention
from angelo_agent.checkpoint_records import _record
from angelo_agent.checkpoint_restore import apply_file_restore
from angelo_agent.checkpoint_transactions import _transaction, holds_transaction
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.files import FileWorkspace
from angelo_agent.memory_projects import admit_restore_scope
from angelo_agent.snapshot_accounting import StoreAccounting, store_accounting
from angelo_agent.snapshot_store import SnapshotStore

MIB = 1024 * 1024
# On Windows a held transaction keeps the profile folder open without delete sharing, so the folder cannot be
# renamed or replaced while it is held: the race the path-backend tests stage cannot happen there.
WINDOWS_HOLDS_THE_PROFILE = os.name == "nt"


@contextmanager
def path_backend():
    with (patch.object(checkpoint_filesystem, "_windows_api", return_value=None),
          patch.object(os, "supports_dir_fd", set())):
        yield


CHILD = r'''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from angelo_agent.checkpoint_capture_pins import acquire_capture_pin,release_capture_pin
from angelo_agent.checkpoint_transactions import _transaction
root,session,identity,signal=Path(sys.argv[2]),sys.argv[3],sys.argv[4],Path(sys.argv[5])
with _transaction(root):
    pin=acquire_capture_pin(root,session,identity)
print("READY",flush=True)
try:
    deadline=time.monotonic()+10
    while not signal.exists():
        if time.monotonic()>deadline: raise RuntimeError("Owned consumer fixture timeout")
        time.sleep(.01)
finally:
    with _transaction(root): release_capture_pin(pin)
'''


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class CaptureConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-capture-consumers-")
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.other = CheckpointStore(self.store.root, str(uuid.uuid4()))
        self.files = FileWorkspace(self.project)
        self.chunks = SnapshotStore(self.store.root)
        self.pins, self.children = [], []

    def tearDown(self):
        try:
            for child in self.children:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=2)
            for pin in self.pins:
                abandon_capture_pin(pin)
        finally:
            self.temp.cleanup()

    def snapshot(self, store=None):
        store = store or self.store
        record, path = store.reserve()
        path.write_bytes(blend_v1([(b"DATA", b"synthetic ordinary host bytes", 1)]))
        return store.complete(record["id"])

    def metadata(self, store, identity):
        return store.folder(identity) / "checkpoint.json"

    def declare_v4(self, record, store=None, *, state="READY", validation="PENDING", storage="full"):
        store = store or self.store
        value = copy.deepcopy(record)
        value.update(version=4, state=state, capture_validation=validation)
        if state == "RESERVED":
            for key in ("scene_bytes", "scene_hash", "storage", "sculpt"):
                value.pop(key, None)
        else:
            value.update(storage=storage, scene_hash=value.get("scene_hash", "a" * 64),
                         sculpt={"filename": "sculpt.runtime", "format_version": 1,
                                 "bytes": 128, "hash": "b" * 64, "owner_count": 1})
        value = validate_record(value, record["id"])
        self.metadata(store, record["id"]).write_text(json.dumps(value))
        return value

    def pin(self, record, store=None):
        store = store or self.store
        with _transaction(store.root):
            pin = acquire_capture_pin(store.root, store.session_id, record["id"])
        self.pins.append(pin)
        return pin

    def unpin(self, pin):
        with _transaction(self.store.root):
            release_capture_pin(pin)

    def write(self, record, text, store=None, path="notes.md"):
        return self.files.execute("angelo_write_file", {"path": path, "content": text},
                                  store or self.store, record["id"])

    def test_dispatcher_rejects_hostile_containers_before_inspection(self):
        class Hostile(dict):
            def get(self, *_args):
                raise AssertionError("Borrowed inspection hook")
        with self.assertRaises(ValueError):
            decode_record(Hostile(version=4), str(uuid.uuid4()))
        class Collision:
            def __hash__(self):
                return hash("version")
            def __eq__(self, _other):
                raise AssertionError("Borrowed key equality hook")
        with self.assertRaises(ValueError):
            decode_record({Collision(): 4}, str(uuid.uuid4()))

    def test_all_v4_declarations_load_without_scene_or_producer_authority(self):
        record = self.snapshot()
        for state, validation in (("RESERVED", "PENDING"), ("READY", "PENDING"),
                                  ("READY", "VALIDATED"), ("READY", "INTERRUPTED_VALID"), ("READY", "INVALID")):
            with self.subTest(state=state, validation=validation):
                value = self.declare_v4(record, state=state, validation=validation)
                self.assertEqual(self.store.load(record["id"]), value)
                self.assertFalse(self.store.availability(record["id"])["available"])
                with self.assertRaises(ValueError):
                    self.store.recovery_copy(record["id"])
                with self.assertRaises(ValueError):
                    self.store.complete(record["id"])
                with self.assertRaises(ValueError):
                    self.store.save(value)
                with self.assertRaises(ValueError):
                    self.store.save(record)  # A legacy candidate cannot downgrade current v4.
                with self.assertRaises(ValueError):
                    _record(value, record["id"])
                self.assertIn("skipped", snapshot_ingest.ingest_snapshot(self.store, record["id"]))
                self.assertEqual(snapshot_ingest.pending_snapshots(self.store), [])
                self.assertEqual(snapshot_ingest.remove_leftover_copies(self.store), 0)
                self.assertEqual(self.store.prune_plan({"max_bytes": 1})["candidates"], [])
                self.assertEqual(self.store.incomplete_plan(older_than_seconds=300, now=time.time()+1000)["candidates"], [])
        self.assertFalse((self.store.directory / "recoveries").exists())
        self.assertFalse((self.store.folder(record["id"]) / "sculpt.runtime").exists())
        self.assertEqual(self.snapshot(self.other)["version"], 3)

    def test_v4_output_ledger_is_not_admitted(self):
        record = self.declare_v4(self.snapshot())
        record["capture_outputs"] = {}
        with self.assertRaises(ValueError):
            decode_record(record, record["id"])

    def test_missing_v4_components_preserve_journals_but_unknown_files_remain_issues(self):
        record = self.snapshot()
        (self.project / "notes.md").write_text("before")
        self.write(record, "after")
        self.declare_v4(self.store.load(record["id"]), validation="INVALID")
        (self.store.folder(record["id"]) / "scene.blend").unlink()
        (self.store.folder(record["id"]) / "sculpt.runtime").write_bytes(b"unverified synthetic runtime bytes")
        measured = self.store.inspect()
        self.assertEqual(measured["issues"], [])
        item = measured["checkpoints"][0]
        self.assertFalse(item["scene_usable"])
        self.assertEqual(len(item["files"]), 1)
        self.assertFalse(item["scene_file"])
        self.assertEqual(item["bytes"], sum(path.stat().st_size for path in self.store.folder(record["id"]).iterdir()))
        (self.store.folder(record["id"]) / "scene.blend@").write_bytes(b"unproved output")
        self.assertTrue(self.store.inspect()["issues"])
        self.assertTrue(self.store.prune_plan({"max_bytes": 1})["blocked"])

    def test_v4_journal_protection_commit_and_rollback_preserve_capture_facts(self):
        value = self.declare_v4(self.snapshot(), state="RESERVED")
        pin = self.pin(value)
        (self.project / "notes.md").write_text("before")
        entry = self.store.journal(value["id"], self.project, "notes.md", pending=True)
        self.store.commit_journal(value["id"], entry)
        protected = self.store.mark_protected(value["id"])
        self.assertEqual(protected["capture_validation"], "PENDING")
        self.assertEqual(protected["state"], "RESERVED")
        self.assertTrue(protected["protected"])
        self.assertEqual(protected["files"][0]["phase"], "ready")
        self.store.rollback_journal(value["id"], protected["files"][0])
        current = self.store.load(value["id"])
        self.assertEqual(current["files"], [])
        self.assertTrue(current["protected"])
        self.assertNotIn("sculpt", current)
        self.unpin(pin)

    def test_v4_journal_merge_refuses_stale_or_changed_capture_fields(self):
        original = self.declare_v4(self.snapshot())
        stale = copy.deepcopy(original)
        self.store.journal(original["id"], self.project, "one.md")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store._save_journal(stale, stale)
        before = self.store.load(original["id"])
        for key, value in (("scene_hash", "c"*64), ("capture_validation", "VALIDATED"), ("protected", True)):
            candidate = copy.deepcopy(before)
            candidate[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store._save_journal(before, candidate)
        self.assertEqual(self.store.load(original["id"]), before)

    def test_active_pin_blocks_direct_scene_consumers_but_not_unrelated_record(self):
        first, other = self.snapshot(), self.snapshot(self.other)
        pin = self.pin(first)
        self.assertNotIn((self.store.session_id, first["id"]), snapshot_ingest.pending_snapshots(self.store))
        self.assertFalse(self.store.availability(first["id"])["available"])
        with self.assertRaisesRegex(ValueError, "active"):
            self.store.recovery_copy(first["id"])
        with patch.object(snapshot_ingest, "_open_file", side_effect=AssertionError("No BASE open")):
            with self.assertRaisesRegex(ValueError, "active"):
                snapshot_ingest.ingest_snapshot(self.store, first["id"])
        self.assertTrue(self.other.availability(other["id"])["available"])
        self.assertTrue(snapshot_ingest.ingest_snapshot(self.other, other["id"])["adopted"])
        self.unpin(pin)

    def test_initial_ingest_long_store_work_does_not_hold_profile(self):
        record = self.snapshot()
        original, seen = snapshot_ingest._write_segments, []
        def observe(*args):
            seen.append(holds_transaction(self.store.root))
            return original(*args)
        with patch.object(snapshot_ingest, "_write_segments", observe):
            self.assertTrue(snapshot_ingest.ingest_snapshot(self.store, record["id"])["adopted"])
        self.assertEqual(seen, [False])

    def test_direct_adoption_and_leftover_deletion_honor_pin_and_v4(self):
        record = self.snapshot()
        path = self.store.folder(record["id"]) / "scene.blend"
        # The identity ingestion itself records: on Windows os.stat's st_ctime is the creation time, while the
        # descriptor-relative read (fstat, as adoption compares) gives the change time.
        with checkpoint_filesystem._private_directory(self.store.folder(record["id"])) as folder:
            info = checkpoint_filesystem._file_info(folder, "scene.blend")
        digest = hashlib.blake2b(path.read_bytes(), digest_size=32).digest()
        with patch.object(snapshot_ingest, "_adopt", return_value={"adopted": False}):
            snapshot_ingest.ingest_snapshot(self.store, record["id"])
        ref = checkpoint_ref(self.store.session_id, record["id"])
        pin = self.pin(record)
        with self.assertRaisesRegex(ValueError, "active"):
            snapshot_ingest._adopt(self.store, record["id"], ref, info, digest, self.chunks)
        self.assertTrue(path.exists())
        self.assertNotIn("storage", self.store.load(record["id"]))
        self.unpin(pin)
        with patch.object(snapshot_ingest, "_remove_copy", return_value=False):
            self.assertTrue(snapshot_ingest._adopt(self.store, record["id"], ref, info, digest, self.chunks)["adopted"])
        pin = self.pin(record)
        self.assertEqual(snapshot_ingest.remove_leftover_copies(self.store), 0)
        self.assertTrue(path.exists())
        self.unpin(pin)
        value = self.declare_v4(self.store.load(record["id"]), validation="VALIDATED", storage="store")
        self.assertEqual(snapshot_ingest.remove_leftover_copies(self.store), 0)
        self.assertFalse(snapshot_ingest._adopt(self.store, record["id"], ref, info, digest, self.chunks)["adopted"])
        self.assertTrue(path.exists())
        self.assertEqual(self.store.load(record["id"]), value)

    def test_leftover_cleanup_rechecks_a_new_v4_record_after_inventory(self):
        record = self.snapshot()
        with patch.object(snapshot_ingest, "_remove_copy", return_value=False):
            snapshot_ingest.ingest_snapshot(self.store, record["id"])
        current = self.store.load(record["id"])
        original_load, changed = CheckpointStore.load, []
        def substitute(owner, identity):
            if identity == record["id"] and not changed:
                changed.append(True)
                self.declare_v4(current, validation="INVALID", storage="store")
            return original_load(owner, identity)
        with patch.object(CheckpointStore, "load", substitute):
            self.assertEqual(snapshot_ingest.remove_leftover_copies(self.store), 0)
        self.assertEqual(changed, [True])
        self.assertEqual(self.store.load(record["id"])["version"], 4)
        self.assertTrue((self.store.folder(record["id"]) / "scene.blend").exists())

    def test_pin_io_error_cannot_be_interpreted_as_idle(self):
        record = self.snapshot()
        with patch.object(checkpoint_capture_state, "capture_is_active", side_effect=OSError("owned probe failure")):
            self.assertFalse(self.store.availability(record["id"])["available"])
            with self.assertRaises(OSError):
                self.store.recovery_copy(record["id"])
            with self.assertRaises(OSError):
                snapshot_ingest.ingest_snapshot(self.store, record["id"])
        self.assertTrue((self.store.folder(record["id"]) / "scene.blend").exists())

    def test_direct_batch_remove_preflights_later_pin_and_v4_before_first_tombstone(self):
        first, second = self.snapshot(), self.snapshot()
        records = sorted(self.store.inspect()["checkpoints"], key=lambda item: item["created"])
        candidates = [{key: item[key] for key in ("session_id", "id", "bytes", "fingerprint")}
                      for item in records]
        plan = {"kind": "retention", "candidates": candidates}
        before = [self.metadata(self.store, item["id"]).read_bytes() for item in (first, second)]
        pin = self.pin(second)
        with self.assertRaisesRegex(ValueError, "active"):
            self.store._remove(plan)
        self.assertEqual([self.metadata(self.store, item["id"]).read_bytes() for item in (first, second)], before)
        self.unpin(pin)
        self.declare_v4(second)
        first_before = self.metadata(self.store, first["id"]).read_bytes()
        with self.assertRaises(ValueError):
            self.store._remove(plan)
        self.assertEqual(self.metadata(self.store, first["id"]).read_bytes(), first_before)
        self.assertTrue((self.store.folder(first["id"]) / "scene.blend").exists())

    def restore_owners(self):
        selected = self.snapshot()
        backup = self.snapshot(self.other)
        effect_store = CheckpointStore(self.store.root, str(uuid.uuid4()))
        effect = self.snapshot(effect_store)
        (self.project / "notes.md").write_text("original")
        self.write(backup, "middle", self.other)
        self.write(effect, "current", effect_store)
        return selected, backup, effect_store, effect

    def test_restore_each_exact_participant_pin_refuses_before_mutation(self):
        selected, backup, effect_store, effect = self.restore_owners()
        public = self.store.restore_plan(selected["id"])
        for owner, record in ((self.store, selected), (self.other, backup), (effect_store, effect)):
            with self.subTest(owner=owner.session_id):
                pin = self.pin(record, owner)
                with self.assertRaisesRegex(ValueError, "active"):
                    self.store.restore_since(selected["id"], public)
                self.assertEqual((self.project / "notes.md").read_text(), "current")
                self.unpin(pin)
        self.assertEqual(public[0][1].parent, self.other.folder(backup["id"]))
        self.assertFalse({"owner", "participants", "effect_owner"} & public[0][2].keys())
        unrelated = self.snapshot()
        self.pin(unrelated)
        self.assertEqual(self.store.restore_since(selected["id"], public), 1)
        self.assertEqual((self.project / "notes.md").read_text(), "original")

    def test_unavailable_v4_journal_owners_still_restore_legacy_scene(self):
        selected, backup, effect_store, effect = self.restore_owners()
        self.declare_v4(self.other.load(backup["id"]), self.other, validation="INVALID")
        self.declare_v4(effect_store.load(effect["id"]), effect_store, state="RESERVED")
        self.assertFalse(self.other.availability(backup["id"])["available"])
        self.assertEqual(self.store.restore_since(selected["id"]), 1)
        self.assertEqual((self.project / "notes.md").read_text(), "original")
        changed = effect_store.load(effect["id"])
        self.assertEqual(changed["version"], 4)
        self.assertEqual(changed["capture_validation"], "PENDING")
        self.assertEqual(len(changed["files"]), 1)

    def test_unavailable_v4_journal_remains_a_retention_dependency_anchor(self):
        old, pending = self.snapshot(), self.snapshot()
        self.declare_v4(pending, state="RESERVED")
        self.store.journal(pending["id"], self.project, "separate.md")
        self.write(old, "new after the v4 boundary")
        self.snapshot()
        plan = self.store.prune_plan({"max_count": 1, "max_bytes": 0})
        self.assertNotIn(old["id"], [item["id"] for item in plan["candidates"]])
        self.assertIn(checkpoint_ref(self.store.session_id, old["id"]),
                      {item["ref"] for item in plan["protected"]})

    def test_retained_v4_ready_without_own_journal_anchors_later_before_image(self):
        (self.project / "notes.md").write_text("before B")
        first, retained = self.snapshot(), self.snapshot()
        self.declare_v4(retained)
        self.write(first, "after B")
        latest = self.snapshot()
        entry = self.store.load(first["id"])["files"][0]
        self.assertEqual((first["boundary"], retained["boundary"], entry["sequence"], latest["boundary"]),
                         (1, 2, 3, 4))
        self.assertEqual(self.store.load(retained["id"])["files"], [])
        self.assertFalse(self.store.availability(retained["id"])["available"])
        plan = self.store.prune_plan({"max_count": 1, "max_bytes": 0})
        self.assertEqual([item["id"] for item in plan["candidates"]], [latest["id"]])
        first_ref = checkpoint_ref(self.store.session_id, first["id"])
        reason = next(item["reason"] for item in plan["protected"] if item["ref"] == first_ref)
        self.assertIn("file restore chain", reason)
        self.assertEqual((self.store.folder(first["id"]) / entry["backup"]).read_text(), "before B")

    def test_direct_restore_requires_fresh_separate_participants(self):
        selected, backup, effect_store, effect = self.restore_owners()
        plan, participants = self.store._prepare_file_restore(selected["id"])
        io = dict(copy_file=checkpoints._copy_from, replace_file=checkpoints._replace_file,
                  unlink_file=checkpoints._unlink_file)
        with self.assertRaises(ValueError):
            apply_file_restore(self.store, plan, participants=participants, **io)
        with _transaction(self.store.root):
            with self.assertRaises(ValueError):
                apply_file_restore(self.store, plan, **io)
            self.write(effect, "later effect", effect_store)
            with self.assertRaisesRegex(ValueError, "participants changed"):
                apply_file_restore(self.store, plan, participants=participants, **io)
        self.assertEqual((self.project / "notes.md").read_text(), "later effect")

    def test_v4_cannot_issue_scene_recovery_scope(self):
        record = self.declare_v4(self.snapshot(), validation="VALIDATED")
        controller = SimpleNamespace(document=("owned", 1), checkpoints=self.store,
                                     store=SimpleNamespace(directory=self.store.root), _project_owner=object())
        with self.assertRaises(ValueError):
            admit_restore_scope(controller, record["id"])

    def test_private_pin_namespace_cannot_be_a_file_journal_target(self):
        record = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Private application state"):
            self.store.journal(record["id"], self.store.root, "checkpoint-captures/unowned")

    def test_inventory_pin_observation_is_authoritative_only_under_transaction(self):
        record = self.snapshot()
        self.pin(record)
        advisory = checkpoint_inventory.inventory(self.store, validate_targets=False)["checkpoints"][0]
        self.assertIsNone(advisory["capture_active"])
        observed = self.store.inspect()["checkpoints"][0]
        self.assertTrue(observed["capture_active"])
        self.assertEqual(self.store.prune_plan({"max_bytes": 1})["candidates"], [])

    def test_pending_bytes_do_not_create_eviction_pressure_but_age_count_still_apply(self):
        first, pending = self.snapshot(), self.snapshot()
        self.declare_v4(pending, state="RESERVED")
        inventory = self.store.inspect()
        inventory["checkpoints"].sort(key=lambda item: item["created"])
        inventory["checkpoints"][0]["bytes"] = 80*MIB
        inventory["checkpoints"][1]["bytes"] = 40*MIB
        inventory["total_bytes"] = 120*MIB
        plan = plan_retention(inventory, {"max_bytes": 100*MIB})
        self.assertEqual(plan["candidates"], [])
        self.assertEqual(plan["remaining_bytes"], 120*MIB)
        self.assertEqual(plan["pressure_bytes"], 80*MIB)
        self.assertFalse(plan["budget_met"])
        age = plan_retention(inventory, {"max_bytes": 100*MIB, "max_age_days": 1}, now=first["created"]+2*86400)
        self.assertEqual([item["id"] for item in age["candidates"]], [first["id"]])
        extra = copy.deepcopy(inventory["checkpoints"][0])
        extra_id = str(uuid.uuid4())
        extra.update(id=extra_id, ref=checkpoint_ref(self.store.session_id, extra_id),
                     created=first["created"]+1, bytes=40*MIB)
        inventory["checkpoints"].append(extra)
        inventory["total_bytes"] += 40*MIB
        count = plan_retention(inventory, {"max_count": 1, "max_bytes": 0})
        self.assertEqual(len(count["candidates"]), 1)
        pressure = plan_retention(inventory, {"max_bytes": 100*MIB})
        self.assertEqual([item["id"] for item in pressure["candidates"]], [first["id"]])

    def test_unavailable_manifest_owner_prevents_false_actual_chunk_release(self):
        actual = StoreAccounting({"usable": [1], "unavailable": [1, 2]}, {1: 80*MIB, 2: 40*MIB}, 0,
                                 pressure_refs={"usable"})
        self.assertEqual(actual.total_bytes, 120*MIB)
        self.assertEqual(actual.pressure_bytes, 80*MIB)
        self.assertEqual(actual.simulation().release("usable"), 0)
        self.assertEqual(actual.pressure_simulation().release("usable"), 80*MIB)

    def test_real_unpruned_v4_manifest_and_legacy_orphans_preserve_exact_pins(self):
        record = self.snapshot()
        snapshot_ingest.ingest_snapshot(self.store, record["id"])
        self.declare_v4(self.store.load(record["id"]), validation="INVALID", storage="store")
        inventory = self.store.inspect()
        accounting = store_accounting(self.store, inventory)
        ref = checkpoint_ref(self.store.session_id, record["id"])
        self.assertIn(ref, accounting._manifests)
        self.assertEqual(snapshot_maintenance.drop_orphan_manifests(self.store, self.chunks), 0)
        missing, removed = self.snapshot(), self.snapshot()
        for entry in (missing, removed):
            snapshot_ingest.ingest_snapshot(self.store, entry["id"])
        pin = self.pin(missing)
        self.metadata(self.store, missing["id"]).unlink()
        removed_ref = checkpoint_ref(self.store.session_id, removed["id"])
        self.metadata(self.store, removed["id"]).unlink()
        self.assertEqual(snapshot_maintenance.drop_orphan_manifests(self.store, self.chunks), 1)
        self.assertFalse(self.chunks.has_manifest(removed_ref))
        self.assertTrue(self.chunks.has_manifest(checkpoint_ref(self.store.session_id, missing["id"])))
        self.assertTrue(self.chunks.has_manifest(ref))
        self.unpin(pin)

    def test_pinned_pruned_manifest_is_not_dropped_until_pin_is_released(self):
        record = self.snapshot()
        snapshot_ingest.ingest_snapshot(self.store, record["id"])
        plan = self.store.prune_plan({"max_bytes": 1})
        self.store.prune(plan, user_confirmed=True)
        self.assertEqual(self.store.load(record["id"])["state"], "PRUNED")
        ref = checkpoint_ref(self.store.session_id, record["id"])
        pin = self.pin(record)
        self.assertEqual(snapshot_maintenance.drop_orphan_manifests(self.store, self.chunks), 0)
        self.assertTrue(self.chunks.has_manifest(ref))
        self.unpin(pin)
        self.assertEqual(snapshot_maintenance.drop_orphan_manifests(self.store, self.chunks), 1)

    def test_cooperating_child_pin_refuses_consumers_without_waiting(self):
        record = self.snapshot()
        output, signal = self.root / "child.log", self.root / "release-child"
        with output.open("wb") as log:
            child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(ADDONS), str(self.store.root),
                                      self.store.session_id, record["id"], str(signal)], stdout=log, stderr=subprocess.STDOUT)
        self.children.append(child)
        deadline = time.monotonic()+5
        while not output.read_text().startswith("READY") and child.poll() is None and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertTrue(output.read_text().startswith("READY"), output.read_text())
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "active"):
            self.store.recovery_copy(record["id"])
        with self.assertRaisesRegex(ValueError, "active"):
            snapshot_ingest.ingest_snapshot(self.store, record["id"])
        self.assertLess(time.monotonic()-started, 2)
        signal.write_text("release")
        self.assertEqual(child.wait(timeout=5), 0, output.read_text())
        self.assertTrue(self.store.availability(record["id"])["available"])

    def test_post_effect_unlink_sync_failure_preserves_original_error_and_one_effect(self):
        for error in (OSError("owned directory sync failure"), FileNotFoundError("owned post-effect missing directory")):
            with self.subTest(error_type=type(error).__name__):
                record = self.snapshot()
                self.write(record, "created after capture")
                before = self.store.load(record["id"])["files"][0]["effect_sequence"]
                with (patch.object(checkpoint_restore, "sync_directory", side_effect=error),
                      patch.object(self.store, "_record_restored_effect", wraps=self.store._record_restored_effect) as record_effect,
                      self.assertRaises(OSError) as caught):
                    self.store.restore_since(record["id"])
                self.assertIs(caught.exception, error)
                self.assertEqual(record_effect.call_count, 1)
                self.assertEqual(error.restored_files, 1)
                self.assertEqual(error.restored_paths, [str(self.project / "notes.md")])
                self.assertFalse((self.project / "notes.md").exists())
                self.assertGreater(self.store.load(record["id"])["files"][0]["effect_sequence"], before)

    def test_call_thrown_unlink_file_not_found_reads_actual_outcome(self):
        original_unlink = checkpoints._unlink_file
        for outcome in ("removed", "unchanged", "replaced"):
            with self.subTest(outcome=outcome):
                record = self.snapshot()
                name = outcome + ".md"
                target = self.project / name
                content = "created " + outcome
                self.write(record, content, path=name)
                sequence = self.store.load(record["id"])["files"][0]["effect_sequence"]
                error = FileNotFoundError("owned unlink acknowledgment failure")
                moved = self.project / (outcome + "-retained-original.md")
                def unlink_then_error(folder, child):
                    if child != name:
                        return original_unlink(folder, child)
                    if outcome == "removed":
                        original_unlink(folder, child)
                    elif outcome == "replaced":
                        target.rename(moved)  # Keep the original inode alive, proving replacement.
                        target.write_text("foreign replacement")
                    raise error
                with (patch.object(checkpoints, "_unlink_file", unlink_then_error),
                      patch.object(self.store, "_record_restored_effect", wraps=self.store._record_restored_effect) as effect,
                      self.assertRaises(FileNotFoundError) as caught):
                    self.store.restore_since(record["id"])
                self.assertIs(caught.exception, error)
                removed = outcome == "removed"
                self.assertEqual(effect.call_count, 1 if removed else 0)
                self.assertEqual(error.restored_files, 1 if removed else 0)
                self.assertEqual(error.restored_paths, [str(target)] if removed else [])
                self.assertEqual(error.partial_changes_possible, outcome != "unchanged")
                after_sequence = self.store.load(record["id"])["files"][0]["effect_sequence"]
                if removed:
                    self.assertFalse(target.exists())
                    self.assertGreater(after_sequence, sequence)
                else:
                    self.assertEqual(after_sequence, sequence)
                    self.assertEqual(target.read_text(), content if outcome == "unchanged" else "foreign replacement")
                    if outcome == "replaced":
                        self.assertEqual(moved.read_text(), content)

    def test_path_backend_absent_namespace_preserves_legacy_reads_without_pin_producer(self):
        record = self.snapshot()
        namespace = self.store.root / "checkpoint-captures"
        with path_backend():
            self.assertTrue(self.store.availability(record["id"])["available"])
            self.assertEqual(self.store.inspect()["issues"], [])
            with _transaction(self.store.root):
                self.assertFalse(checkpoint_capture_state.capture_activity(self.store.root, self.store.session_id, record["id"]))
                with self.assertRaises(ValueError):
                    acquire_capture_pin(self.store.root, self.store.session_id, record["id"])
        self.assertFalse(namespace.exists())
        self.declare_v4(record, validation="VALIDATED")
        with path_backend():
            self.assertFalse(self.store.availability(record["id"])["available"])

    def test_path_backend_present_namespace_never_implies_idle(self):
        record = self.snapshot()
        namespace = self.store.root / "checkpoint-captures"
        for kind in ("directory", "file", "dangling_link"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    namespace.mkdir()
                elif kind == "file":
                    namespace.write_bytes(b"owned unexpected namespace file")
                else:
                    namespace.symlink_to(self.root / "owned-absent-target")
                try:
                    with path_backend(), _transaction(self.store.root):
                        with self.assertRaisesRegex(ValueError, "descriptor-relative"):
                            require_capture_idle(self.store, record["id"])
                    with path_backend():
                        self.assertFalse(self.store.availability(record["id"])["available"])
                finally:
                    namespace.rmdir() if kind == "directory" else namespace.unlink()

    def test_path_backend_namespace_io_error_remains_error(self):
        record = self.snapshot()
        error = OSError("owned namespace lookup I/O error")
        with path_backend(), _transaction(self.store.root):
            with patch.object(checkpoint_capture_state, "entry_info", side_effect=error), self.assertRaises(OSError) as caught:
                require_capture_idle(self.store, record["id"])
        self.assertIs(caught.exception, error)

    def test_path_backend_missing_profile_is_not_absent_namespace(self):
        record = self.snapshot()
        moved = self.root / "owned-moved-profile"
        if WINDOWS_HOLDS_THE_PROFILE:
            with _transaction(self.store.root), self.assertRaises(PermissionError):
                self.store.root.rename(moved)
            return
        with _transaction(self.store.root), path_backend():
            self.store.root.rename(moved)
            try:
                with (patch.object(checkpoint_capture_state, "entry_info", side_effect=AssertionError("Root must fail first")),
                      self.assertRaises(FileNotFoundError)):
                    require_capture_idle(self.store, record["id"])
            finally:
                moved.rename(self.store.root)

    def test_path_backend_profile_replacement_between_observations_refuses(self):
        record = self.snapshot()
        moved = self.root / "owned-replaced-profile"
        if WINDOWS_HOLDS_THE_PROFILE:
            with _transaction(self.store.root), self.assertRaises(PermissionError):
                self.store.root.rename(moved)
            return
        original = checkpoint_capture_state.entry_info
        def replace_root(folder, name):
            self.store.root.rename(moved)
            self.store.root.mkdir()
            return original(folder, name)
        with _transaction(self.store.root), path_backend():
            try:
                with patch.object(checkpoint_capture_state, "entry_info", replace_root), self.assertRaisesRegex(ValueError, "profile directory changed"):
                    require_capture_idle(self.store, record["id"])
            finally:
                self.store.root.rmdir()
                moved.rename(self.store.root)


if __name__ == "__main__":
    unittest.main()
