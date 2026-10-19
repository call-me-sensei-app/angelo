# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent-review regressions: clock rollback, acknowledgements and order."""

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.checkpoint_filesystem import anchored_files_supported
from angelo_agent import checkpoints, files
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.checkpoint_timeline import NAME, allocate_event, forget_trust
from angelo_agent.files import FileWorkspace, MAX_FILE
from angelo_agent.studio_agent import StudioAgent
from angelo_agent.studio_state import StudioLibrary


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative I/O required")
class RecoveryAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-recovery-acks-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.a = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.b = CheckpointStore(self.a.root, str(uuid.uuid4()))
        self.files = FileWorkspace(self.project)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, store=None, purpose="request"):
        store = store or self.a
        record, path = store.reserve(purpose=purpose)
        path.write_bytes(b"private host-only scene fixture")
        return store.complete(record["id"])

    def write(self, store, record, content):
        return self.files.execute("angelo_write_file", {"path": "notes.md", "content": content}, store, record["id"])

    def test_stale_valid_clock_cannot_recycle_unused_gap_before_capture(self):
        target = self.project / "notes.md"
        target.write_text("before B")
        a = self.snapshot()
        stale = json.loads((self.a.root / NAME).read_text())
        with checkpoints._transaction(self.a.root):
            allocate_event(self.a)  # harmless failed-operation gap
        b = self.snapshot(self.b)
        self.write(self.b, b, "actual current state")
        clock = self.a.root / NAME
        correct = clock.read_text()
        clock.write_text(json.dumps(stale))
        with self.assertRaisesRegex(ValueError, "order conflicts"):
            self.snapshot()
        clock.write_text(correct)
        self.assertEqual(self.a.inspect()["issues"], [])
        self.assertEqual(target.read_text(), "actual current state")
        self.assertEqual(len(self.a.inspect()["checkpoints"]), 2)

    def test_stale_boundary_with_fresh_sequence_cannot_reuse_old_before_image(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        self.write(self.a, a, "first A")
        b = self.snapshot(self.b)
        clock = self.a.root / NAME
        correct = json.loads(clock.read_text())
        broken = dict(correct, boundary=a["boundary"])
        clock.write_text(json.dumps(broken))
        with self.assertRaisesRegex(ValueError, "order conflicts"):
            self.write(self.a, a, "not applied")
        self.assertEqual(target.read_text(), "first A")
        clock.write_text(json.dumps(correct))
        self.write(self.a, a, "second A")
        self.b.restore_since(b["id"])
        self.assertEqual(target.read_text(), "first A")

    def test_unlink_then_error_keeps_confirmed_applied_before_image(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        original = files._unlink_file
        def applied_then_failed(folder, name):
            original(folder, name)
            if name == "notes.md":
                raise OSError("unlink acknowledgement failed")
        with patch.object(files, "_unlink_file", side_effect=applied_then_failed), self.assertRaises(OSError) as caught:
            self.files.execute("angelo_delete_file", {"path": "notes.md"}, self.a, a["id"])
        self.assertTrue(caught.exception.partial_changes_possible)
        self.assertFalse(target.exists())
        self.assertEqual(self.a.load(a["id"])["files"][0]["phase"], "ready")
        self.a.restore_since(a["id"])
        self.assertEqual(target.read_text(), "original")

    def test_replaced_inode_after_delete_error_keeps_pending_owner_and_foreign_file(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        def unknown_unlink(folder, name):
            (self.project / "notes.md").rename(self.project / "moved-original.md")
            target.write_text("foreign collision")
            raise OSError("unlink outcome unknown")
        with patch.object(files, "_unlink_file", side_effect=unknown_unlink), self.assertRaises(OSError) as caught:
            self.files.execute("angelo_delete_file", {"path": "notes.md"}, self.a, a["id"])
        self.assertTrue(caught.exception.partial_changes_possible)
        entry = self.a.load(a["id"])["files"][0]
        self.assertEqual(entry["phase"], "pending")
        self.assertTrue((self.a.folder(a["id"]) / entry["backup"]).is_file())
        with self.assertRaisesRegex(ValueError, "pending"):
            self.a.restore_since(a["id"])
        self.assertEqual(target.read_text(), "foreign collision")

    def test_restore_publication_then_error_reports_confirmed_progress_and_safety_works(self):
        target = self.project / "notes.md"
        target.write_text("original")
        a = self.snapshot()
        self.write(self.a, a, "changed")
        plan = self.a.restore_plan(a["id"])
        safety = self.snapshot(purpose="safety")
        self.a.journal(safety["id"], self.project, "notes.md", restore_entry=plan[0][2])
        original = checkpoints._replace_file
        def applied_then_failed(folder, source, destination):
            original(folder, source, destination)
            raise OSError("restore acknowledgement failed")
        with patch.object(checkpoints, "_replace_file", side_effect=applied_then_failed), self.assertRaises(OSError) as caught:
            self.a.restore_since(a["id"], plan)
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(caught.exception.restored_files, 1)
        self.assertTrue(caught.exception.partial_changes_possible)
        self.a.restore_since(safety["id"])
        self.assertEqual(target.read_text(), "changed")

    def export(self, store, record):
        library = StudioLibrary(self.root / "library")
        asset = library.add("3D", "Private transport fixture", "No provider", status="ready")
        source = library.result_directory(asset["id"]) / "model.glb"
        with source.open("wb") as output:
            output.truncate(MAX_FILE + 2 * 1024 * 1024)
        asset["files"] = [source.name]
        library.update(asset)
        controller = SimpleNamespace(checkpoints=store, request_checkpoint=record["id"], files=self.files)
        service = StudioAgent(library, Mock(), lambda: [], Mock(), controller, Mock(), Mock())
        return service.execute("angelo_studio_export", {"asset_id": asset["id"], "path": "model.glb"})

    def test_restore_unlink_then_error_reports_completed_deletion_and_safety_works(self):
        a = self.snapshot()
        self.write(self.a, a, "created output")
        plan = self.a.restore_plan(a["id"])
        safety = self.snapshot(purpose="safety")
        self.a.journal(safety["id"], self.project, "notes.md", restore_entry=plan[0][2])
        original = checkpoints._unlink_file
        def applied_then_failed(folder, name):
            original(folder, name)
            if name == "notes.md":
                raise OSError("restore unlink acknowledgement failed")
        with patch.object(checkpoints, "_unlink_file", side_effect=applied_then_failed), self.assertRaises(OSError) as caught:
            self.a.restore_since(a["id"], plan)
        self.assertEqual(caught.exception.restored_files, 1)
        self.assertTrue(caught.exception.partial_changes_possible)
        self.a.restore_since(safety["id"])
        self.assertEqual((self.project / "notes.md").read_text(), "created output")

    def test_reused_before_image_does_not_hide_latest_large_export_kind(self):
        a, b = self.snapshot(), self.snapshot(self.b)
        self.files.execute("angelo_write_file", {"path": "model.glb", "content": "small A"}, self.a, a["id"])
        first = self.a.load(a["id"])["files"][0]
        self.files.execute("angelo_delete_file", {"path": "model.glb"}, self.b, b["id"])
        result = self.export(self.a, a)
        self.assertTrue(result["success"])
        latest = self.a.load(a["id"])["files"][0]
        self.assertEqual(latest["sequence"], first["sequence"])
        self.assertEqual(latest.get("kind", "file_tool"), "file_tool")
        self.assertGreater(latest["effect_sequence"], self.b.load(b["id"])["files"][0]["effect_sequence"])
        self.assertEqual(latest["effect_kind"], "studio_export")
        self.assertEqual(self.a.inspect()["issues"], [])
        self.b.restore_since(b["id"])
        self.assertFalse((self.project / "model.glb").exists())

    def test_small_edit_after_large_safety_then_undo_restore_keeps_current_large_bound(self):
        a = self.snapshot()
        self.assertTrue(self.export(self.a, a)["success"])
        plan = self.a.restore_plan(a["id"])
        large_safety = self.snapshot(purpose="safety")
        self.a.journal(large_safety["id"], self.project, "model.glb", restore_entry=plan[0][2])
        self.a.restore_since(a["id"], plan)
        small = self.snapshot(self.b)
        self.files.execute("angelo_write_file", {"path": "model.glb", "content": "small current"}, self.b, small["id"])
        undo_plan = self.a.restore_plan(large_safety["id"])
        small_safety = self.snapshot(purpose="safety")
        self.a.journal(small_safety["id"], self.project, "model.glb", restore_entry=undo_plan[0][2])
        self.a.restore_since(large_safety["id"], undo_plan)
        self.assertEqual((self.project / "model.glb").stat().st_size, MAX_FILE + 2 * 1024 * 1024)
        self.assertEqual(self.a.inspect()["issues"], [])
        self.a.restore_since(small_safety["id"])
        self.assertEqual((self.project / "model.glb").read_text(), "small current")
        self.assertEqual(self.a.inspect()["issues"], [])

    def test_in_root_file_and_parent_aliases_cannot_expose_hidden_or_excluded_data(self):
        (self.project / ".env").write_text("synthetic hidden value")
        hidden = self.project / ".ssh"
        hidden.mkdir()
        (hidden / "key.txt").write_text("synthetic hidden value")
        excluded = self.project / "app-state"
        excluded.mkdir()
        (excluded / "auth.json").write_text("synthetic excluded value")
        self.files = FileWorkspace(self.project, excluded=[excluded])
        (self.project / "public.txt").symlink_to(self.project / ".env")
        (self.project / "public-parent").symlink_to(hidden, target_is_directory=True)
        (self.project / "public-state").symlink_to(excluded, target_is_directory=True)
        a = self.snapshot()
        for path in ("public.txt", "public-parent/key.txt", "public-state/auth.json", "app-state/auth.json"):
            for name in ("angelo_read_file", "angelo_write_file", "angelo_delete_file"):
                args = {"path": path, "content": "not allowed", "start": 0, "length": 100}
                with self.subTest(path=path, name=name), self.assertRaises(ValueError):
                    self.files.execute(name, args, self.a, a["id"])
        self.assertEqual((self.project / ".env").read_text(), "synthetic hidden value")
        self.assertEqual((hidden / "key.txt").read_text(), "synthetic hidden value")
        self.assertEqual(self.a.load(a["id"])["files"], [])

    def test_new_legacy_capture_after_admission_is_not_silently_treated_as_older(self):
        modern = self.snapshot()
        identity = str(uuid.uuid4())
        folder = self.a.folder(identity)
        folder.mkdir()
        (folder / "scene.blend").write_bytes(b"legacy late capture")
        metadata = {"version": 2, "id": identity, "created": modern["created"] + 1,
                    "original_path": "", "files": [], "scene_bytes": 19}
        (folder / "checkpoint.json").write_text(json.dumps(metadata))
        # This process wrote the clock and still trusts it, so the next capture
        # skips the whole-history rescan; its order is later than every recorded one.
        admitted = self.snapshot()
        self.assertGreater(admitted["boundary"], modern["boundary"])
        with self.assertRaisesRegex(ValueError, "Legacy capture/journal history changed"):
            self.a.restore_since(identity)
        # Restore's locked whole-history check withdrew that trust.
        with self.assertRaisesRegex(ValueError, "Legacy capture/journal history changed"):
            self.snapshot()
        forget_trust(self.a.root)  # A new process validates everything before its first capture.
        with self.assertRaisesRegex(ValueError, "Legacy capture/journal history changed"):
            self.snapshot()
        self.assertEqual(json.loads((folder / "checkpoint.json").read_text()), metadata)


if __name__ == "__main__":
    unittest.main()
