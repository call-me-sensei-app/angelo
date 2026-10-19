# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real temporary assets/controllers; no Blender, network, secrets or paid calls.

Synthetic scene bytes test journal storage, not native scene serialization.
"""

import json
import errno
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import studio_agent
from angelo_agent.checkpoint_policy import BEFORE_FIRST_EDIT, MAX_CREATED_FILE, CheckpointPolicyStore
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.files import MAX_FILE
from angelo_agent.studio_agent import StudioAgent, StudioExportError
from angelo_agent.studio_state import StudioLibrary
from angelo_agent.studio_generation import MAX_MODEL


def glb_fixture():
    data = json.dumps({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": []}]}).encode()
    data += b" " * (-len(data) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(data)) + struct.pack("<II", len(data), 0x4E4F534A) + data


class StudioExportCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-export-")
        self.root = Path(self.temp.name).resolve()
        self.controller = Controller(self.root / "state")
        self.controller.set_document(("unbilled export fixture", 1))
        self.controller.busy = True
        self.library = StudioLibrary(self.root / "library")
        self.asset = self.library.add("3D", "Temporary local fixture", "No provider", status="ready")
        self.source = self.library.result_directory(self.asset["id"]) / "model.glb"
        self.source.write_bytes(glb_fixture())
        self.asset["files"] = [self.source.name]
        self.library.update(self.asset)
        self.jobs, self.binding = Mock(), Mock()
        self.service = StudioAgent(self.library, self.jobs, lambda: [], self.binding,
            self.controller, Mock(), Mock())
        service = self.service

        class Executor:
            def precondition(self, name, args):
                return service.precondition(name, args)

            def execute(self, name, args):
                return service.execute(name, args)

        self.executor = Executor()
        self.events = []
        self.controller.checkpoint_factory = self.capture
        self.args = {"asset_id": self.asset["id"], "path": "exports/model.glb"}
        self.target = self.controller.files.root / self.args["path"]

    def tearDown(self):
        self.controller.cancel_tools("Fixture complete")
        self.controller.close()
        self.temp.cleanup()

    def capture(self):
        self.events.append("capture")
        record, path = self.controller.checkpoints.reserve()
        path.write_bytes(b"synthetic host scene fixture")
        return self.controller.checkpoints.complete(record["id"])

    def active_snapshot(self):
        record = self.capture()
        self.controller.request_checkpoint = record["id"]
        return record

    def export(self):
        return self.service.execute("angelo_studio_export", self.args)

    def checkpoint_files(self, record):
        return self.controller.checkpoints.load(record["id"])["files"]

    def test_real_asset_export_has_truthful_receipt_and_restores_from_snapshot(self):
        record = self.active_snapshot()
        original = self.source.read_bytes()
        result = self.export()
        self.assertTrue(result["success"])
        self.assertTrue(result["journaled"])
        self.assertEqual(result["request_checkpoint"], record["id"])
        self.assertEqual(result["file_changes"], {"written": [self.args["path"]], "deleted": []})
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(result["bytes"], len(original))
        self.assertFalse(result["blender_undoable"])
        self.assertFalse(self.checkpoint_files(record)[0]["existed"])
        self.assertEqual(self.controller.checkpoints.restore_since(record["id"]), 1)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(), original)
        self.binding.assert_not_called()
        self.jobs.start.assert_not_called()

    def test_deferred_approved_export_captures_then_journals_before_creation(self):
        CheckpointPolicyStore(self.controller.store.directory).save(
            {"capture_mode": BEFORE_FIRST_EDIT}, user_confirmed=True)
        self.controller._initial_request_checkpoint()
        self.assertIsNone(self.controller.request_checkpoint)
        self.assertEqual(self.events, [])
        answers = []
        self.controller.request_tool("export-fixture", "angelo_studio_export", self.args, answers.append)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        call = next(iter(self.controller.pending.values()))
        self.assertTrue(call.approval)
        self.assertEqual(self.events, [])
        original_journal = self.controller.checkpoints.journal
        original_copy = studio_agent._copy_export

        def journal(*args, **kwargs):
            self.events.append("journal")
            self.assertFalse(self.target.exists())
            self.assertEqual(list(self.controller.files.root.rglob("*.tmp")), [])
            return original_journal(*args, **kwargs)

        def copy(source, destination):
            self.events.append("copy")
            self.assertFalse(self.target.exists(), "Publication waits for the complete copy")
            self.assertEqual(len(self.controller.checkpoints.load(self.controller.request_checkpoint)["files"]), 1)
            return original_copy(source, destination)

        with patch.object(self.controller.checkpoints, "journal", side_effect=journal), \
                patch.object(studio_agent, "_copy_export", side_effect=copy):
            self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertEqual(self.events, ["capture", "journal", "copy"])
        self.assertTrue(answers[0]["success"])
        self.assertEqual(self.controller.checkpoints.restore_since(self.controller.request_checkpoint), 1)
        self.assertFalse(self.target.exists())

    def test_missing_snapshot_refuses_export_without_output_or_provider_work(self):
        with self.assertRaises(StudioExportError) as caught:
            self.export()
        self.assertFalse(caught.exception.partial_changes_possible)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.controller.checkpoints.inspect()["checkpoints"], [])
        self.jobs.start.assert_not_called()

    def test_oversized_asset_refuses_before_journal_at_existing_model_output_limit(self):
        record = self.active_snapshot()
        with self.source.open("r+b") as source:
            source.truncate(MAX_CREATED_FILE + 1)
        self.assertEqual(MAX_CREATED_FILE, MAX_MODEL)
        with self.assertRaisesRegex(StudioExportError, "150 MiB"):
            self.export()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.checkpoint_files(record), [])

    def test_exact_size_limit_export_is_still_removable_by_snapshot_restore(self):
        record = self.active_snapshot()
        with self.source.open("r+b") as source:
            source.truncate(MAX_CREATED_FILE)
        result = self.export()
        self.assertTrue(result["success"])
        self.assertEqual(self.target.stat().st_size, MAX_CREATED_FILE)
        self.assertEqual(self.controller.checkpoints.restore_since(record["id"]), 1)
        self.assertFalse(self.target.exists())

    def test_journal_persistence_failure_does_not_create_any_export_file(self):
        record = self.active_snapshot()
        with patch.object(self.controller.checkpoints, "journal", side_effect=OSError("simulated full disk")):
            result = self.export()
        self.assertIn("full disk", result["error"])
        self.assertFalse(result["success"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertFalse(result["journaled"])
        self.assertFalse(self.target.exists())
        self.assertEqual(self.checkpoint_files(record), [])

    def test_large_export_round_trip_preserves_safety_undo_without_widening_regular_backups(self):
        record = self.active_snapshot()
        size = MAX_FILE + 1024 * 1024
        with self.source.open("r+b") as source:
            source.truncate(size)
        self.assertTrue(self.export()["success"])
        plan = self.controller.checkpoints.restore_plan(record["id"])
        self.assertEqual(plan[0][2]["kind"], "studio_export")
        regular, path = self.controller.checkpoints.reserve()
        path.write_bytes(b"ordinary checkpoint fixture")
        self.controller.checkpoints.complete(regular["id"])
        with self.assertRaises(ValueError):
            self.controller.checkpoints.journal(regular["id"], self.controller.files.root, self.args["path"])
        with self.assertRaises(ValueError):
            self.controller.checkpoints.journal(regular["id"], self.controller.files.root, self.args["path"], kind="studio_export")
        with self.assertRaises(ValueError):
            self.controller.checkpoints.journal(regular["id"], self.controller.files.root, self.args["path"], restore_entry=plan[0][2])
        safety, path = self.controller.checkpoints.reserve(purpose="safety")
        path.write_bytes(b"safety checkpoint fixture")
        self.controller.checkpoints.complete(safety["id"])
        self.controller.checkpoints.journal(safety["id"], self.controller.files.root, self.args["path"], restore_entry=plan[0][2])
        self.assertEqual(self.checkpoint_files(safety)[0]["kind"], "studio_export_safety")
        self.assertEqual(self.controller.checkpoints.restore_since(record["id"], plan), 1)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.controller.checkpoints.restore_since(safety["id"]), 1)
        self.assertEqual(self.target.stat().st_size, size)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.controller.checkpoints.inspect()["issues"], [])

    def test_unmarked_new_file_and_malformed_large_kind_do_not_bypass_twenty_mib_policy(self):
        record = self.active_snapshot()
        self.target.parent.mkdir()
        self.controller.checkpoints.journal(record["id"], self.controller.files.root, self.args["path"])
        with self.target.open("wb") as target:
            target.truncate(MAX_FILE + 1)
        with self.assertRaises(ValueError):
            self.controller.checkpoints.restore_since(record["id"])
        metadata = self.controller.checkpoints.load(record["id"])
        metadata["files"][0]["kind"] = "unlimited_export"
        with self.assertRaises(ValueError):
            self.controller.checkpoints.save(metadata)
        metadata["files"][0]["kind"] = "studio_export_safety"
        with self.assertRaises(ValueError):
            self.controller.checkpoints.save(metadata)
        self.assertTrue(self.target.exists())
        self.assertEqual(list(self.controller.files.root.rglob("*.tmp")), [])

    def test_journal_committed_then_reported_failure_releases_unused_entry(self):
        record = self.active_snapshot()
        original = self.controller.checkpoints.journal

        def failed_journal(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("durability acknowledgement failed")

        with patch.object(self.controller.checkpoints, "journal", side_effect=failed_journal):
            result = self.export()
        self.assertFalse(result["journaled"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertFalse(self.target.exists())
        self.assertEqual(self.checkpoint_files(record), [])

    def test_partial_copy_failure_removes_stage_and_unused_journal_without_false_changes(self):
        record = self.active_snapshot()

        def failed_copy(source, destination):
            destination.write(b"partial fixture data")
            raise OSError("simulated copy failure")

        with patch.object(studio_agent, "_copy_export", side_effect=failed_copy):
            result = self.export()
        self.assertIn("copy failure", result["error"])
        self.assertFalse(result["success"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertEqual(result["file_changes"], {"written": [], "deleted": []})
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])
        self.assertEqual(self.checkpoint_files(record), [])
        self.assertEqual(self.controller.checkpoints.inspect()["issues"], [])

    def test_copy_failure_preserves_an_earlier_pre_write_journal_for_the_same_path(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b"original request file")
        record = self.active_snapshot()
        self.controller.files.execute("angelo_delete_file", {"path": self.args["path"]},
            self.controller.checkpoints, record["id"])
        before = self.checkpoint_files(record)
        with patch.object(studio_agent, "_copy_export", side_effect=OSError("copy failed")):
            result = self.export()
        self.assertFalse(result["success"])
        self.assertEqual(self.checkpoint_files(record), before)
        self.assertEqual(self.controller.checkpoints.restore_since(record["id"]), 1)
        self.assertEqual(self.target.read_bytes(), b"original request file")

    def test_output_collision_during_copy_is_never_overwritten_or_deleted_by_restore(self):
        record = self.active_snapshot()
        original = studio_agent._copy_export

        def collided_copy(source, destination):
            copied = original(source, destination)
            self.target.write_bytes(b"another writer's file")
            return copied

        with patch.object(studio_agent, "_copy_export", side_effect=collided_copy):
            result = self.export()
        self.assertFalse(result["success"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertEqual(result["file_changes"], {"written": [], "deleted": []})
        self.assertEqual(self.target.read_bytes(), b"another writer's file")
        self.assertEqual(self.checkpoint_files(record), [])
        self.assertEqual(self.controller.checkpoints.restore_since(record["id"]), 0)
        self.assertEqual(self.target.read_bytes(), b"another writer's file")
        self.assertEqual(list(self.target.parent.glob("*.tmp")), [])

    def test_output_collision_during_journal_does_not_leave_a_backup_or_restore_entry(self):
        record = self.active_snapshot()
        original = self.controller.checkpoints.journal

        def collided_journal(*args, **kwargs):
            self.target.parent.mkdir()
            self.target.write_bytes(b"another writer's file")
            return original(*args, **kwargs)

        with patch.object(self.controller.checkpoints, "journal", side_effect=collided_journal):
            result = self.export()
        self.assertFalse(result["success"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertEqual(self.checkpoint_files(record), [])
        self.assertEqual(self.controller.checkpoints.inspect()["issues"], [])
        self.assertEqual(self.controller.checkpoints.restore_since(record["id"]), 0)
        self.assertEqual(self.target.read_bytes(), b"another writer's file")

    def test_publication_failure_has_no_visible_output_and_cleans_stage(self):
        record = self.active_snapshot()
        with patch.object(studio_agent, "_publish_export", side_effect=OSError("filesystem cannot publish")):
            result = self.export()
        self.assertFalse(result["success"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])
        self.assertEqual(self.checkpoint_files(record), [])

    def test_unsupported_publication_is_actionable_and_leaves_no_untracked_output(self):
        record = self.active_snapshot()
        # The anchored publication itself (a hard link through a descriptor or a Windows handle) is refused.
        with patch.object(studio_agent, "publish_new_file", side_effect=OSError(errno.ENOTSUP, "unsupported filesystem")):
            result = self.export()
        self.assertFalse(result["success"])
        self.assertIn("manual Studio Export", result["error"])
        self.assertFalse(result["partial_changes_possible"])
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])
        self.assertEqual(self.checkpoint_files(record), [])

    def test_error_after_actual_publication_reports_retained_recoverable_output(self):
        record = self.active_snapshot()
        original = studio_agent._publish_export

        def ambiguous_publish(*args):
            original(*args)
            raise OSError("publication acknowledgement failed")

        with patch.object(studio_agent, "_publish_export", side_effect=ambiguous_publish):
            result = self.export()
        self.assertFalse(result["success"])
        self.assertTrue(result["partial_changes_possible"])
        self.assertTrue(result["journaled"])
        self.assertEqual(result["file_changes"]["written"], [self.args["path"]])
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual(list(self.target.parent.glob("*.tmp")), [])
        self.assertEqual(self.controller.checkpoints.restore_since(record["id"]), 1)
        self.assertFalse(self.target.exists())

    def test_symlink_parent_escape_never_creates_or_journals_an_outside_file(self):
        record = self.active_snapshot()
        outside = self.root / "outside"
        outside.mkdir()
        self.target.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StudioExportError):
            self.export()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.checkpoint_files(record), [])


if __name__ == "__main__":
    unittest.main()
