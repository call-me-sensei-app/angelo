# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled host tests: policy, measured storage, restore chains and adversaries.

Synthetic scene bytes exercise storage only, not Blender document validity.
test_blender_checkpoints.py is the separate real-document factory-startup gate.
"""

import copy
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
from angelo_agent import checkpoint_filesystem
from angelo_agent import checkpoints as checkpoint_module
from angelo_agent.checkpoint_policy import (BEFORE_FIRST_EDIT, EVERY_REQUEST,
    CheckpointPolicyStore, capture_scope, checkpoint_ref, clean_policy, requires_capture)
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.files import FileWorkspace
from angelo_agent.tool_specs import TOOL_MAP


class CheckpointPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="angelo-policy-")
        self.root = Path(self.temporary.name).resolve()
        self.profile = self.root / "state"
        self.profile.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.profile, str(uuid.uuid4()))
        self.files = FileWorkspace(self.project)

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self, store=None, scene=b"synthetic scene document", purpose="request"):
        store = store or self.store
        record, path = store.reserve(str(self.project / "original.blend"), purpose=purpose)
        path.write_bytes(scene)
        return store.complete(record["id"])

    def ref(self, record, store=None):
        return checkpoint_ref((store or self.store).session_id, record["id"])

    def write(self, store, record, path, content):
        return self.files.execute("angelo_write_file", {"path": path, "content": content}, store, record["id"])

    def test_defaults_preserve_every_request_and_storage_budget(self):
        defaults = clean_policy()
        self.assertEqual(defaults["capture_mode"], EVERY_REQUEST)
        # Deduplicated snapshot storage: storage has
        # a 2 GiB default budget enforced automatically; count and age stay
        # unlimited by default, and 0 still means unlimited for every limit.
        self.assertEqual([defaults[key] for key in ("max_count", "max_bytes", "max_age_days")], [0, 2 * 1024 ** 3, 0])
        self.assertTrue(requires_capture())
        self.assertEqual(capture_scope(), "before_request")
        records = [self.snapshot() for _ in range(4)]
        self.assertEqual(self.store.prune_plan()["candidates"], [])
        for record in records:
            self.assertTrue(self.store.availability(record["id"])["available"])

    def test_deferred_policy_is_explicit_human_choice_not_a_model_tool(self):
        settings = CheckpointPolicyStore(self.profile)
        with self.assertRaisesRegex(ValueError, "human"):
            settings.save({"capture_mode": BEFORE_FIRST_EDIT})
        saved = settings.save({"capture_mode": BEFORE_FIRST_EDIT}, user_confirmed=True)
        self.assertEqual(settings.load(), saved)
        self.assertFalse(requires_capture(saved))
        self.assertTrue(requires_capture(saved, before_edit=True))
        self.assertEqual(capture_scope(saved), "before_first_approved_edit")
        self.assertFalse(any("checkpoint_policy" in name or "prune_checkpoint" in name for name in TOOL_MAP))
        if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
            self.assertEqual((self.profile / "checkpoint-policy.json").stat().st_mode & 0o777, 0o600)

    def test_malformed_settings_fail_conservatively_and_do_not_coerce(self):
        for invalid in ({"max_count": True}, {"max_bytes": -1}, {"max_age_days": 1.5},
                        {"capture_mode": "automatic"}, {"version": True}, {"api_key": "never persist"}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                clean_policy(invalid)
        (self.profile / "checkpoint-policy.json").write_text('{"max_count":NaN}')
        settings = CheckpointPolicyStore(self.profile)
        self.assertEqual(settings.load(), clean_policy())
        self.assertIn("could not be read", settings.error)

    def test_policy_metadata_symlink_and_linked_root_are_refused(self):
        outside = self.project / "policy.json"
        outside.write_text('{"capture_mode":"BEFORE_FIRST_EDIT"}')
        (self.profile / "checkpoint-policy.json").symlink_to(outside)
        settings = CheckpointPolicyStore(self.profile)
        self.assertEqual(settings.load(), clean_policy())
        with self.assertRaises(ValueError):
            settings.save({}, user_confirmed=True)
        self.assertIn("BEFORE_FIRST_EDIT", outside.read_text())
        linked = self.root / "linked-state"
        linked.symlink_to(self.profile, target_is_directory=True)
        with self.assertRaises(ValueError):
            CheckpointPolicyStore(linked)

    def test_capture_metadata_contains_actual_time_and_bytes(self):
        record = self.snapshot(scene=b"synthetic" * 1024)
        self.assertEqual(record["scene_bytes"], 9 * 1024)
        self.assertGreaterEqual(record["capture_ms"], 0)
        self.assertEqual(record["state"], "READY")
        item = self.store.inspect()["checkpoints"][0]
        expected = sum(path.stat().st_size for path in self.store.folder(record["id"]).iterdir())
        self.assertEqual(item["bytes"], expected)
        self.assertEqual(item["scene_bytes"], record["scene_bytes"])
        self.assertEqual(item["capture_ms"], record["capture_ms"])
        self.assertTrue(self.store.availability(record["id"])["available"])

    def test_empty_or_unfinished_scene_is_not_presented_as_a_restore_point(self):
        record, path = self.store.reserve()
        path.write_bytes(b"incomplete native save")
        self.assertFalse(self.store.availability(record["id"])["available"])
        path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.store.complete(record["id"])
        self.assertFalse(self.store.availability(record["id"])["available"])

    def test_native_capture_compatibility_and_legacy_metadata(self):
        record, path = self.store.reserve()
        path.write_bytes(b"native caller saved scene")
        record["scene_bytes"] = 999999  # Never trust caller-supplied measurements.
        self.store.save(record)
        self.assertEqual(record["scene_bytes"], path.stat().st_size)
        record = {key: record[key] for key in ("id", "original_path", "created", "files", "scene_bytes")}
        (path.parent / "checkpoint.json").write_text(json.dumps(record))
        loaded = self.store.load(record["id"])
        self.assertEqual(loaded["capture_mode"], EVERY_REQUEST)
        self.assertEqual(loaded["state"], "READY")
        self.assertTrue(self.store.availability(record["id"])["available"])

    def test_count_limit_requires_exact_user_confirmed_plan_and_keeps_tombstones(self):
        original = self.project / "original.blend"
        original.write_bytes(b"user project must survive")
        first, second, third = [self.snapshot() for _ in range(3)]
        plan = self.store.prune_plan({"max_count": 1})
        self.assertEqual([item["id"] for item in plan["candidates"]], [first["id"], second["id"]])
        self.assertEqual(plan["candidate_bytes"], sum(item["bytes"] for item in plan["candidates"]))
        with self.assertRaisesRegex(ValueError, "human"):
            self.store.prune(plan)
        result = self.store.prune(plan, user_confirmed=True)
        self.assertEqual(result["removed"], [self.ref(first), self.ref(second)])
        self.assertEqual(result["bytes_freed"], first["scene_bytes"] + second["scene_bytes"])
        self.assertEqual(self.store.availability(first["id"])["state"], "PRUNED")
        self.assertTrue(self.store.availability(third["id"])["available"])
        self.assertEqual(original.read_bytes(), b"user project must survive")
        self.assertEqual(set(path.name for path in self.store.folder(first["id"]).iterdir()), {"checkpoint.json"})
        for operation in (self.store.restore_since, self.store.recovery_copy):
            with self.assertRaisesRegex(ValueError, "removed"):
                operation(first["id"])

    def test_active_request_and_safety_references_override_an_impossible_budget(self):
        first = self.snapshot()
        safety = self.snapshot(purpose="safety")
        current = self.snapshot()
        unused = self.snapshot()
        protected = [self.ref(first), (self.store.session_id, current["id"])]
        plan = self.store.prune_plan({"max_count": 1, "max_bytes": 1}, protected_refs=protected)
        self.assertEqual([item["id"] for item in plan["candidates"]], [unused["id"]])
        self.assertFalse(plan["budget_met"])
        self.assertEqual({item["ref"] for item in plan["protected"]}, {self.ref(first), self.ref(safety), self.ref(current)})
        self.store.prune(plan, user_confirmed=True, protected_refs=protected)
        for record in (first, safety, current):
            self.assertTrue(self.store.availability(record["id"])["available"])

    def test_cross_chat_scene_and_file_restore_chain_survives_retention(self):
        other = CheckpointStore(self.profile, str(uuid.uuid4()))
        (self.project / "notes.md").write_text("original")
        before = self.snapshot(scene=b"scene before first request")
        self.write(self.store, before, "notes.md", "first request")
        later = self.snapshot(other, scene=b"scene after first request")
        self.write(other, later, "notes.md", "second request")
        self.write(other, later, "created.md", "later new file")
        disposable_scene = self.snapshot(scene=b"scene with no journal")
        protected = [self.ref(before)]
        plan = self.store.prune_plan({"max_count": 1}, protected_refs=protected)
        self.assertEqual([item["id"] for item in plan["candidates"]], [disposable_scene["id"]])
        self.assertIn(self.ref(later, other), {item["ref"] for item in plan["protected"]})
        self.store.prune(plan, user_confirmed=True, protected_refs=protected)
        _, recovery = self.store.recovery_copy(before["id"])
        self.assertEqual(recovery.read_bytes(), b"scene before first request")
        self.assertEqual(self.store.restore_since(before["id"]), 2)
        self.assertEqual((self.project / "notes.md").read_text(), "original")
        self.assertFalse((self.project / "created.md").exists())

    def test_later_retained_point_does_not_depend_on_earlier_removed_journal(self):
        (self.project / "notes.md").write_text("original")
        old = self.snapshot()
        self.write(self.store, old, "notes.md", "first")
        retained = self.snapshot()
        self.write(self.store, retained, "notes.md", "second")
        plan = self.store.prune_plan({"max_count": 1})
        self.assertEqual([item["id"] for item in plan["candidates"]], [old["id"]])
        self.store.prune(plan, user_confirmed=True)
        self.assertEqual(self.store.restore_since(retained["id"]), 1)
        self.assertEqual((self.project / "notes.md").read_text(), "first")

    def test_age_and_byte_limits_are_opt_in_and_planning_never_deletes(self):
        first = self.snapshot()
        second = self.snapshot()
        now = second["created"] + 2 * 86400
        plan = self.store.prune_plan({"max_age_days": 1}, now=now)
        self.assertEqual(len(plan["candidates"]), 2)
        by_bytes = self.store.prune_plan({"max_bytes": 1})
        self.assertEqual(len(by_bytes["candidates"]), 2)
        self.assertFalse(by_bytes["budget_met"], "Metadata tombstones cannot fit a one-byte budget")
        self.assertGreaterEqual(by_bytes["remaining_bytes"], by_bytes["tombstone_reserve_bytes"])
        self.assertTrue(self.store.availability(first["id"])["available"])

    def test_stale_or_forged_plan_and_new_protection_are_refused_without_changes(self):
        first = self.snapshot()
        self.snapshot()
        plan = self.store.prune_plan({"max_count": 1})
        forged = copy.deepcopy(plan)
        forged["candidates"][0]["id"] = "../original.blend"
        for proposal, protected in ((forged, ()), (plan, [self.ref(first)])):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.store.prune(proposal, user_confirmed=True, protected_refs=protected)
        self.snapshot()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.prune(plan, user_confirmed=True)
        self.assertTrue(self.store.availability(first["id"])["available"])

    def test_bound_incomplete_cleanup_cannot_remove_scenes_journals_or_active_work(self):
        empty = [self.store.reserve()[0] for _ in range(4)]
        journal_only, _ = self.store.reserve()
        self.write(self.store, journal_only, "pending.md", "journal must survive")
        scene = self.snapshot()
        protected = [self.ref(empty[0])]
        plan = self.store.incomplete_plan(now=scene["created"] + 86401, max_count=2, protected_refs=protected)
        self.assertEqual([item["id"] for item in plan["candidates"]], [empty[1]["id"], empty[2]["id"]])
        self.store.prune(plan, user_confirmed=True, protected_refs=protected)
        self.assertEqual(self.store.load(journal_only["id"])["state"], "RESERVED")
        self.assertEqual(len(self.store.load(journal_only["id"])["files"]), 1)
        self.assertTrue(self.store.availability(scene["id"])["available"])
        with self.assertRaises(ValueError):
            self.store.incomplete_plan(max_count=65)

    def test_malformed_metadata_fails_closed_for_all_journals_and_pruning(self):
        first = self.snapshot()
        self.write(self.store, first, "notes.md", "current contents")
        later = self.snapshot()
        path = self.store.folder(later["id"]) / "checkpoint.json"
        for field, invalid in (("created", float("nan")), ("files", {}), ("state", []), ("id", "../escape")):
            broken = dict(later, **{field: invalid})
            path.write_text(json.dumps(broken))
            with self.subTest(field=field):
                self.assertTrue(self.store.prune_plan({"max_count": 1})["blocked"])
                with self.assertRaises(ValueError):
                    self.store.restore_since(first["id"])
                self.assertEqual((self.project / "notes.md").read_text(), "current contents")
        path.write_text(json.dumps(later))
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_malicious_journal_path_backup_root_and_mode_are_rejected(self):
        record = self.snapshot()
        self.write(self.store, record, "notes.md", "must remain")
        saved = self.store.load(record["id"])
        metadata = self.store.folder(record["id"]) / "checkpoint.json"
        for field, invalid in (("backup", "../secret.bin"), ("path", "../outside.md"),
                               ("path", ".env"), ("root", str(self.root / ".ssh")),
                               ("root", str(self.profile / "sessions")), ("mode", True)):
            broken = copy.deepcopy(saved)
            broken["files"][0][field] = invalid
            metadata.write_text(json.dumps(broken))
            with self.subTest(field=field, invalid=invalid):
                self.assertTrue(self.store.prune_plan({"max_count": 1})["blocked"])
                with self.assertRaises(ValueError):
                    self.store.restore_since(record["id"])
        self.assertEqual((self.project / "notes.md").read_text(), "must remain")

    def test_symlink_scene_metadata_backup_and_unexpected_directory_block_pruning(self):
        record = self.snapshot()
        outside = self.project / "original.blend"
        outside.write_bytes(b"do not erase user project")
        scene = self.store.folder(record["id"]) / "scene.blend"
        scene.unlink()
        scene.symlink_to(outside)
        self.assertTrue(self.store.prune_plan({"max_bytes": 1})["blocked"])
        self.assertFalse(self.store.availability(record["id"])["available"])
        scene.unlink()
        scene.write_bytes(b"synthetic scene document")
        metadata = scene.parent / "checkpoint.json"
        original_metadata = metadata.read_text()
        metadata.unlink()
        metadata.symlink_to(outside)
        self.assertTrue(self.store.prune_plan({"max_bytes": 1})["blocked"])
        metadata.unlink()
        metadata.write_text(original_metadata)
        unexpected = scene.parent / "user-project"
        unexpected.mkdir()
        (unexpected / "secret.txt").write_text("keep")
        self.assertTrue(self.store.prune_plan({"max_bytes": 1})["blocked"])
        self.assertEqual(outside.read_bytes(), b"do not erase user project")
        self.assertEqual((unexpected / "secret.txt").read_text(), "keep")

    def test_parent_and_recovery_directory_links_cannot_write_outside_storage(self):
        record = self.snapshot()
        outside = self.root / "outside"
        outside.mkdir()
        (self.store.directory / "recoveries").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.recovery_copy(record["id"])
        self.assertEqual(list(outside.iterdir()), [])
        another_profile = self.root / "another-state"
        another_profile.mkdir()
        (another_profile / "checkpoints").symlink_to(outside, target_is_directory=True)
        linked_store = CheckpointStore(another_profile, str(uuid.uuid4()))
        with self.assertRaises(ValueError):
            linked_store.reserve()
        self.assertEqual(list(outside.iterdir()), [])

    def test_restore_revalidates_all_targets_before_touching_any_file(self):
        record = self.snapshot()
        (self.project / "first.md").write_text("old first")
        (self.project / "second.md").write_text("old second")
        self.write(self.store, record, "first.md", "new first")
        self.write(self.store, record, "second.md", "new second")
        plan = self.store.restore_plan(record["id"])
        outside = self.root / "outside.md"
        outside.write_text("external content")
        (self.project / "second.md").unlink()
        (self.project / "second.md").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.restore_since(record["id"], plan)
        self.assertEqual((self.project / "first.md").read_text(), "new first")
        self.assertEqual(outside.read_text(), "external content")

    def test_directory_swap_during_prune_never_follows_outside_symlink(self):
        record = self.snapshot()
        self.snapshot()
        plan = self.store.prune_plan({"max_count": 1})
        snapshot_folder = self.store.folder(record["id"])
        outside = self.project / "outside"
        outside.mkdir()
        (outside / "scene.blend").write_bytes(b"outside user project")
        original_write = checkpoint_module._write_json
        swapped = refused = False

        def replace_folder(folder, name, value):
            nonlocal swapped, refused
            if value.get("state") == "PRUNED" and not swapped and not refused:
                try:
                    snapshot_folder.rename(self.root / "moved-owned-snapshot")
                except PermissionError:
                    # Windows: prune already holds the snapshot's files for deletion, and NTFS refuses to
                    # rename a folder with open files in it, so the swap cannot even begin.
                    refused = os.name == "nt"
                    if not refused:
                        raise
                else:
                    snapshot_folder.symlink_to(outside, target_is_directory=True)
                    swapped = True
            return original_write(folder, name, value)

        failure = None
        with patch.object(checkpoint_module, "_write_json", side_effect=replace_folder):
            try:
                self.store.prune(plan, user_confirmed=True)
            except ValueError as error:
                failure = error
        self.assertTrue(swapped or refused)
        if swapped:
            self.assertIsNotNone(failure)
            self.assertTrue(failure.partial_removal)
            self.assertTrue((self.root / "moved-owned-snapshot" / "scene.blend").exists())
        else:
            self.assertIsNone(failure)
        self.assertEqual((outside / "scene.blend").read_bytes(), b"outside user project")
        self.assertFalse((outside / "checkpoint.json").exists())

    def test_missing_backup_blocks_restore_and_prune_without_losing_current_files(self):
        record = self.snapshot()
        (self.project / "notes.md").write_text("before")
        self.write(self.store, record, "notes.md", "after")
        entry = self.store.load(record["id"])["files"][0]
        (self.store.folder(record["id"]) / entry["backup"]).unlink()
        self.assertTrue(self.store.prune_plan({"max_bytes": 1})["blocked"])
        with self.assertRaises(ValueError):
            self.store.restore_since(record["id"])
        self.assertEqual((self.project / "notes.md").read_text(), "after")

    def test_backup_links_and_hardlinked_metadata_never_touch_external_files(self):
        record = self.snapshot()
        (self.project / "notes.md").write_text("before")
        self.write(self.store, record, "notes.md", "after")
        entry = self.store.load(record["id"])["files"][0]
        backup = self.store.folder(record["id"]) / entry["backup"]
        outside = self.root / "external.txt"
        outside.write_text("external")
        backup.unlink()
        backup.symlink_to(outside)
        self.assertTrue(self.store.prune_plan({"max_bytes": 1})["blocked"])
        with self.assertRaises(ValueError):
            self.store.restore_since(record["id"])
        backup.unlink()
        backup.write_text("before")
        metadata = backup.parent / "checkpoint.json"
        os.link(metadata, self.root / "linked-metadata.json")
        self.assertTrue(self.store.prune_plan({"max_bytes": 1})["blocked"])
        self.assertEqual(outside.read_text(), "external")
        self.assertEqual((self.project / "notes.md").read_text(), "after")

    def test_concurrent_scoped_file_writes_keep_every_pre_write_version(self):
        record = self.snapshot()
        failures = []
        for index in range(8):
            (self.project / (str(index) + ".md")).write_text("before " + str(index))

        def write(index):
            try:
                self.write(self.store, record, str(index) + ".md", "after " + str(index))
            except Exception as error:
                failures.append(error)

        workers = [threading.Thread(target=write, args=(index,)) for index in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertEqual(len(self.store.load(record["id"])["files"]), 8)
        self.assertEqual(self.store.restore_since(record["id"]), 8)
        for index in range(8):
            self.assertEqual((self.project / (str(index) + ".md")).read_text(), "before " + str(index))

    def test_journal_persistence_failure_never_modifies_target_or_leaves_owned_backup(self):
        record = self.snapshot()
        (self.project / "notes.md").write_text("before")
        with patch.object(self.store, "save", side_effect=OSError("simulated full disk")):
            with self.assertRaises(OSError):
                self.write(self.store, record, "notes.md", "after")
        self.assertEqual((self.project / "notes.md").read_text(), "before")
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_unsupported_descriptor_deletion_fails_closed_and_fallback_can_read_write(self):
        record = self.snapshot()
        self.snapshot()
        plan = self.store.prune_plan({"max_count": 1})
        with patch.object(os, "supports_dir_fd", set()), patch.object(checkpoint_filesystem, "_windows_api", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.store.prune(plan, user_confirmed=True)
            settings = CheckpointPolicyStore(self.profile)
            settings.save({"max_count": 2}, user_confirmed=True)
            self.assertEqual(settings.load()["max_count"], 2)
            self.assertTrue(self.store.availability(record["id"])["available"])
        self.assertTrue(self.store.availability(record["id"])["available"])


if __name__ == "__main__":
    unittest.main()
