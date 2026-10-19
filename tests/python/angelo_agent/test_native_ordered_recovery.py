# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private, unbilled regression fixtures for ordered cross-chat file recovery."""

import copy
import json
import os
import pickle
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.checkpoint_filesystem import anchored_files_supported
from angelo_agent import checkpoints, checkpoint_filesystem
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.checkpoint_policy import MAX_CREATED_FILE, checkpoint_ref, clean_journal
from angelo_agent.checkpoint_timeline import NAME
from angelo_agent.files import FileWorkspace, MAX_FILE
from angelo_agent.studio_agent import StudioAgent
from angelo_agent.studio_state import StudioLibrary


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative workspace I/O required")
class OrderedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-ordered-recovery-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.other = CheckpointStore(self.store.root, str(uuid.uuid4()))
        self.files = FileWorkspace(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, store=None, purpose="request"):
        store = store or self.store
        record, path = store.reserve(str(self.project / "original.blend"), purpose=purpose)
        path.write_bytes(b"synthetic host scene, never opened by Blender")
        return store.complete(record["id"])

    def write(self, record, text, store=None, path="notes.md"):
        return self.files.execute("angelo_write_file", {"path": path, "content": text}, store or self.store, record["id"])

    def test_old_request_late_first_write_restores_newer_point_and_keeps_chain(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a, b = self.snapshot(), self.snapshot(self.other)
        self.write(a, "late A")
        self.assertEqual(self.other.restore_since(b["id"]), 1)
        self.assertEqual(target.read_text(), "original")
        refs = [checkpoint_ref(self.other.session_id, b["id"])]
        plan = self.other.prune_plan({"max_count": 1}, protected_refs=refs)
        self.assertEqual(plan["candidates"], [])
        self.assertIn(checkpoint_ref(self.store.session_id, a["id"]), {item["ref"] for item in plan["protected"]})

    def test_straddled_write_restores_exact_intermediate_bytes_after_retention(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        self.write(a, "first A")
        b = self.snapshot(self.other)
        self.write(a, "second A")
        self.write(a, "third A within B window")
        entries = self.store.load(a["id"])["files"]
        self.assertEqual(len(entries), 2)
        self.assertLess(entries[0]["sequence"], b["boundary"])
        self.assertGreater(entries[1]["sequence"], b["boundary"])
        self.assertEqual((self.store.folder(a["id"]) / entries[1]["backup"]).read_text(), "first A")
        unused = self.snapshot(self.other)
        refs = [checkpoint_ref(self.other.session_id, b["id"])]
        plan = self.other.prune_plan({"max_count": 1}, protected_refs=refs)
        self.assertEqual([item["id"] for item in plan["candidates"]], [unused["id"]])
        self.other.prune(plan, user_confirmed=True, protected_refs=refs)
        self.other.restore_since(b["id"])
        self.assertEqual(target.read_text(), "first A")
        self.store.restore_since(a["id"])
        self.assertEqual(target.read_text(), "original")

    def test_three_chats_capture_windows_choose_earliest_actual_write_not_owner(self):
        third = CheckpointStore(self.store.root, str(uuid.uuid4()))
        target = self.project / "notes.md"
        target.write_text("original")
        a, b, c = self.snapshot(), self.snapshot(self.other), self.snapshot(third)
        self.write(b, "B first", self.other)
        self.write(a, "A second")
        self.write(c, "C third", third)
        third.restore_since(c["id"])
        self.assertEqual(target.read_text(), "original")

    def export_fixture(self, record, size):
        library = StudioLibrary(self.root / "library")
        asset = library.add("3D", "Unbilled local fixture", "No provider", status="ready")
        source = library.result_directory(asset["id"]) / "model.glb"
        with source.open("wb") as handle:
            handle.truncate(size)
        asset["files"] = [source.name]
        library.update(asset)
        controller = SimpleNamespace(checkpoints=self.store, request_checkpoint=record["id"], files=self.files)
        service = StudioAgent(library, Mock(), lambda: [], Mock(), controller, Mock(), Mock())
        return service.execute("angelo_studio_export", {"asset_id": asset["id"], "path": "model.glb"}), source

    def test_twenty_two_mib_export_after_earlier_delete_has_valid_restore_and_safety_chain(self):
        target = self.project / "model.glb"
        target.write_bytes(b"original small model")
        a = self.snapshot()
        self.files.execute("angelo_delete_file", {"path": "model.glb"}, self.store, a["id"])
        b = self.snapshot()
        result, source = self.export_fixture(b, MAX_FILE + 2 * 1024 * 1024)
        self.assertTrue(result["success"])
        self.assertEqual(self.store.inspect()["issues"], [])
        plan = self.store.restore_plan(a["id"])
        self.assertEqual(plan[0][2].get("kind", "file_tool"), "file_tool")
        self.assertEqual(plan[0][2]["current_kind"], "studio_export")
        safety = self.snapshot(purpose="safety")
        self.store.journal(safety["id"], self.project, "model.glb", restore_entry=plan[0][2])
        saved = self.store.load(safety["id"])["files"][0]
        self.assertEqual(saved["kind"], "studio_export_safety")
        self.store.restore_since(a["id"], plan)
        self.assertEqual(target.read_bytes(), b"original small model")
        self.store.restore_since(safety["id"])
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_copy_failure_cleans_partial_backup_and_preserves_future_restore(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        def fail_copy(folder, name, destination, maximum=None):
            destination.write(b"partial")
            raise OSError("simulated disk full")
        with patch.object(checkpoints, "_copy_from", side_effect=fail_copy):
            with self.assertRaises(OSError):
                self.write(a, "not applied")
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(self.store.load(a["id"])["files"], [])
        self.assertEqual(list(self.store.folder(a["id"]).glob("*.bin")), [])
        b = self.snapshot()
        self.assertEqual(self.store.restore_since(b["id"]), 0)
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_growing_source_is_refused_without_delete_or_oversized_backup(self):
        target = self.project / "notes.md"
        target.write_text("tiny before-image")
        a = self.snapshot()
        original = checkpoint_filesystem._open_file
        grew = False
        def grow_source(folder, name, flags, mode=0o600):
            nonlocal grew
            descriptor = original(folder, name, flags, mode)
            if name == "notes.md" and flags == os.O_RDONLY and not grew:
                grew = True
                with target.open("r+b") as writer:
                    writer.truncate(MAX_FILE + 1)
            return descriptor
        with patch.object(checkpoint_filesystem, "_open_file", side_effect=grow_source):
            with self.assertRaisesRegex(ValueError, "grew"):
                self.files.execute("angelo_delete_file", {"path": "notes.md"}, self.store, a["id"])
        self.assertTrue(target.exists())
        self.assertEqual(self.store.load(a["id"])["files"], [])
        self.assertEqual(list(self.store.folder(a["id"]).glob("*.bin")), [])
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_copy_failure_unavailable_unlink_retains_inert_owned_cleanup(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        original = checkpoints._unlink_file
        def unavailable_unlink(folder, name):
            if name.endswith(".bin"):
                raise OSError("backup cleanup unavailable")
            return original(folder, name)
        def fail_copy(folder, name, destination, maximum=None):
            destination.write(b"partial")
            raise OSError("copy failed")
        with patch.object(checkpoints, "_copy_from", side_effect=fail_copy), patch.object(checkpoints, "_unlink_file", side_effect=unavailable_unlink):
            with self.assertRaises(OSError):
                self.write(a, "not applied")
        entry = self.store.load(a["id"])["files"][0]
        self.assertEqual(entry["phase"], "discarded")
        self.assertEqual(self.store.inspect()["issues"], [])
        self.assertEqual(self.store.restore_since(a["id"]), 0)
        self.store.rollback_journal(a["id"], entry)
        self.assertEqual(self.store.load(a["id"])["files"], [])

    def test_pending_unknown_effect_blocks_restore_and_retention(self):
        a = self.snapshot()
        entry = self.store.journal(a["id"], self.project, "new.md", pending=True)
        (self.project / "new.md").write_text("foreign file")
        with self.assertRaisesRegex(ValueError, "pending"):
            self.store.restore_since(a["id"])
        self.assertTrue(self.store.prune_plan({"max_count": 1})["blocked"])
        self.store.rollback_journal(a["id"], entry)
        self.assertEqual(self.store.restore_since(a["id"]), 0)
        self.assertEqual((self.project / "new.md").read_text(), "foreign file")

    def test_missing_or_rolled_back_timeline_never_reinterprets_order(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        self.write(a, "current")
        clock = self.store.root / NAME
        original = clock.read_text()
        clock.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.snapshot()
        with self.assertRaises(ValueError):
            self.store.restore_since(a["id"])
        clock.write_text(original)
        invalid = json.loads(original)
        invalid["sequence"] = invalid["boundary"]
        clock.write_text(json.dumps(invalid))
        self.assertTrue(self.store.inspect()["issues"])
        self.assertEqual(target.read_text(), "current")

    def legacy_fixture(self, with_journal):
        identity = str(uuid.uuid4())
        folder = self.store.folder(identity)
        folder.mkdir(parents=True)
        (folder / "scene.blend").write_bytes(b"legacy host scene")
        entries = []
        if with_journal:
            backup = str(uuid.uuid4()) + ".bin"
            (folder / backup).write_bytes(b"legacy original")
            entries = [{"root": str(self.project), "path": "legacy.md", "existed": True, "backup": backup, "mode": 0o600}]
            (self.project / "legacy.md").write_text("legacy current")
        record = {"version": 1, "id": identity, "created": 10, "original_path": "", "files": entries, "scene_bytes": 17}
        metadata = folder / "checkpoint.json"
        metadata.write_text(json.dumps(record))
        return record, metadata.read_bytes()

    def test_legacy_journals_preserved_fail_closed_while_new_ordered_points_work(self):
        legacy, original = self.legacy_fixture(True)
        current = self.snapshot()
        self.write(current, "new current", path="legacy.md")
        self.store.restore_since(current["id"])
        self.assertEqual((self.project / "legacy.md").read_text(), "legacy current")
        with self.assertRaisesRegex(ValueError, "Legacy file-write timing"):
            self.store.restore_since(legacy["id"])
        self.assertEqual((self.store.folder(legacy["id"]) / "checkpoint.json").read_bytes(), original)
        self.assertTrue(self.store.availability(legacy["id"])["available"])
        plan = self.store.prune_plan({"max_count": 1})
        self.assertNotIn(legacy["id"], [item["id"] for item in plan["candidates"]])

    def test_legacy_scene_without_ambiguous_journal_still_recovers(self):
        legacy, original = self.legacy_fixture(False)
        current = self.snapshot()
        self.write(current, "new file")
        self.store.restore_since(legacy["id"])
        self.assertFalse((self.project / "notes.md").exists())
        self.assertEqual((self.store.folder(legacy["id"]) / "checkpoint.json").read_bytes(), original)

    def test_legacy_empty_point_retention_preserves_timeline_admission(self):
        legacy, _ = self.legacy_fixture(False)
        current = self.snapshot()
        plan = self.store.prune_plan({"max_count": 1})
        self.assertEqual([item["id"] for item in plan["candidates"]], [legacy["id"]])
        self.store.prune(plan, user_confirmed=True)
        self.assertEqual(self.store.inspect()["issues"], [])
        self.write(current, "new file")
        self.store.restore_since(current["id"])
        self.assertFalse((self.project / "notes.md").exists())

    def test_strict_order_metadata_rejects_partial_float_boolean_and_unknown_phase(self):
        a = self.snapshot()
        self.write(a, "file")
        entry = self.store.load(a["id"])["files"][0]
        for key, value in (("sequence", True), ("sequence", 2.0), ("window", float("nan")), ("phase", []), ("phase", "fake")):
            broken = dict(entry, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                clean_journal([broken])
        missing = copy.deepcopy(entry)
        del missing["window"]
        with self.assertRaises(ValueError):
            clean_journal([missing])

    def test_restore_counts_only_files_it_changed(self):
        # A rewrite of identical bytes and mode, or an already-absent new file, restores without a change
        # (the Undo restore notice reported "reverted 1 file changes" for a no-op rewrite).
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        self.write(a, "agent text")
        self.write(a, "brand new", path="new.md")
        restored = self.store.restore_since(a["id"])
        self.assertEqual((restored, restored.changed), (2, 2))
        self.assertEqual(target.read_text(), "original")
        self.assertFalse((self.project / "new.md").exists())
        again = self.store.restore_since(a["id"])
        self.assertEqual((again, again.changed), (2, 0))
        for copied in (copy.deepcopy(again), pickle.loads(pickle.dumps(again)), json.loads(json.dumps(again))):
            self.assertEqual(copied, 2)  # A saved chat state may hold the count.
        self.assertEqual(copy.deepcopy(again).changed, 0)
        original_mode = os.stat(target).st_mode & 0o777
        # Same bytes, another mode: a change. Windows keeps only the read-only flag, so there the change is that flag
        # (and restoring it means replacing a read-only file).
        if os.name == "nt":
            target.chmod(0o444 if original_mode & 0o200 else 0o666)
        else:
            target.chmod(0o600 if original_mode != 0o600 else 0o640)
        self.assertEqual(self.store.restore_since(a["id"]).changed, 1)
        self.assertEqual(os.stat(target).st_mode & 0o777, original_mode)
        target.write_text("edited by the person")
        self.assertEqual(self.store.restore_since(a["id"]).changed, 1)
        self.assertEqual(target.read_text(), "original")

    def test_file_restore_failure_exposes_completed_progress_and_preserves_exception_type(self):
        a = self.snapshot()
        for name in ("one.md", "two.md"):
            (self.project / name).write_text("original " + name)
            self.write(a, "new " + name, path=name)
        original = checkpoints._replace_file
        def fail_second(folder, source, destination):
            if destination == "two.md":
                raise OSError("second publication failed")
            return original(folder, source, destination)
        with patch.object(checkpoints, "_replace_file", side_effect=fail_second):
            with self.assertRaises(OSError) as caught:
                self.store.restore_since(a["id"])
        self.assertEqual(caught.exception.restored_files, 1)
        self.assertEqual(caught.exception.restored_paths, [str(self.project / "one.md")])
        self.assertTrue(caught.exception.partial_changes_possible)
        self.assertEqual((self.project / "one.md").read_text(), "original one.md")
        self.assertEqual((self.project / "two.md").read_text(), "new two.md")


if __name__ == "__main__":
    unittest.main()
