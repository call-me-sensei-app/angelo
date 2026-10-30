# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verified saved continuity versus unsaved restart, forgery and recovery."""

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller
from angelo_agent import memory_projects
from angelo_agent.memory_projects import LoadedProjectScope, SavedProjectReceipt, admit_saved_project
from angelo_agent.task_memory import on_verified_result, read_memory, scope_token, update_memory

DOC_A, DOC_B = ("launch-a", 31), ("launch-b", 89)


class MemoryProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.project = self.directory / "approved-project.blend"
        self.project.write_bytes(b"BLENDER-v502" + b"fixture scene" * 12)
        self.controllers = []
        self.c = self.controller(DOC_A)

    def tearDown(self):
        for controller in self.controllers:
            controller.close()
        self.temp.cleanup()

    def controller(self, document, *, session_id=None):
        controller = Controller(self.directory, session_id=session_id or str(uuid.uuid4()))
        self.controllers.append(controller)
        controller.set_document(document)
        return controller

    def receipt(self, controller, path=None):
        target = path or self.project
        # Only the native fixture proves bpy attestation. These file-only tests
        # supply its explicit runtime output to test continuity/failure logic.
        loaded = self.loaded(controller, target)
        return admit_saved_project(target, controller.document, str(self.directory.resolve()), loaded_project=loaded)

    def loaded(self, controller, path=None):
        return LoadedProjectScope(scope_token(str(self.directory.resolve())), scope_token(controller.document),
                                  str(Path(path or self.project).resolve()), memory_projects._ISSUER)

    def brief(self, controller):
        args = {"expected_revision": controller.state["task_memory"]["revision"], "patch": {
            "objective": "Keep this exact project brief", "constraints": ["No destructive cleanup"],
            "dimensions": [{"label": "width", "value": 2, "unit": "m"}]}}
        controller.state = update_memory(controller.state, args, controller.document,
            provenance={"kind": "approved_update", "id": "host:human-approval"}, profile_id=str(self.directory.resolve()))
        controller.state = on_verified_result(controller.state, controller.document, call_id="native:read", tool="angelo_scene_object",
            result={"success": True, "objects": [{"object": "scn:old-object-handle", "name": "Original", "mesh_revision": "old-revision"}]},
            profile_id=str(self.directory.resolve()), object_handles=["old-object-id"])
        controller.save()

    def restart(self):
        self.c.save()
        return self.controller(DOC_B, session_id=self.c.state["id"])

    def checkpoint(self, controller):
        record, path = controller.checkpoints.reserve("")
        path.write_bytes(b"BLENDER-v502" + b"recovery scene")
        controller.checkpoints.complete(record["id"])
        return record["id"]

    def test_saved_project_restart_retains_brief_but_never_live_evidence_or_authority(self):
        self.assertTrue(self.c.bind_project(self.receipt(self.c)))
        self.brief(self.c)
        resumed = self.restart()
        self.assertTrue(read_memory(resumed.state, DOC_B)["scope_mismatch"])
        self.assertTrue(resumed.bind_project(self.receipt(resumed)))
        memory = read_memory(resumed.state, DOC_B)
        self.assertEqual(memory["brief"]["objective"], "Keep this exact project brief")
        self.assertEqual(memory["brief"]["constraints"], ["No destructive cleanup"])
        self.assertEqual(memory["authority"], "none")
        self.assertTrue(memory["stale"])
        self.assertTrue(all(result["stale"] and not result["object_handles"] for result in memory["verified_results"]))
        encoded = json.dumps(memory)
        self.assertNotIn("scn:old-object-handle", encoded)
        self.assertNotIn("old-revision", encoded)
        self.assertNotIn(str(self.project), encoded)
        replies = []
        resumed.request_tool("memory-is-not-approval", "angelo_create_cube",
                             {"name": "No Authority", "size": 1, "location": [0, 0, 0]}, replies.append)
        self.assertIn("error", replies[0])
        self.assertFalse(resumed.pending)

    def test_different_saved_path_even_with_identical_bytes_stays_isolated(self):
        self.c.bind_project(self.receipt(self.c))
        self.brief(self.c)
        other = self.directory / "other-project.blend"
        other.write_bytes(self.project.read_bytes())
        resumed = self.restart()
        self.assertFalse(resumed.bind_project(self.receipt(resumed, other)))
        self.assertEqual(read_memory(resumed.state, DOC_B)["brief"]["objective"], "")

    def test_replaced_or_changed_saved_file_does_not_claim_project_continuity(self):
        self.c.bind_project(self.receipt(self.c))
        self.brief(self.c)
        resumed = self.restart()
        self.project.write_bytes(b"BLENDER-v502" + b"different real file")
        self.assertFalse(resumed.bind_project(self.receipt(resumed)))
        self.assertTrue(read_memory(resumed.state, DOC_B)["scope_mismatch"])

    def test_unsaved_and_legacy_restart_never_auto_bind_to_new_factory_or_saved_file(self):
        self.brief(self.c)  # No saved-project receipt in the old launch.
        resumed = self.restart()
        self.assertTrue(read_memory(resumed.state, DOC_B)["scope_mismatch"])
        self.assertFalse(resumed.bind_project(self.receipt(resumed)))
        with self.assertRaises(ValueError):
            admit_saved_project("", DOC_B, str(self.directory))

    def test_plain_json_and_direct_unadmitted_receipt_are_refused(self):
        valid = self.receipt(self.c)
        with self.assertRaises(ValueError):
            self.c.bind_project({"project": valid.project, "revision": valid.revision, "approved": True})
        forged = SavedProjectReceipt(valid.project, valid.revision, valid.profile, valid.document,
                                     valid._path, valid._file_stamp)
        with self.assertRaises(ValueError):
            self.c.bind_project(forged)
        with self.assertRaises(ValueError):
            admit_saved_project(self.project, DOC_A, str(self.directory.resolve()))

    def test_receipt_is_invalid_after_file_changes_or_for_another_host_document(self):
        receipt = self.receipt(self.c)
        self.project.write_bytes(b"BLENDER-v502" + b"updated content")
        with self.assertRaises(ValueError):
            self.c.bind_project(receipt)
        self.assertFalse(receipt.valid_for(DOC_B, str(self.directory)))

    def test_a_receipt_reads_its_file_as_admission_did(self):
        # Windows' os.stat reports the creation time as st_ctime where os.fstat reports the last change: a receipt
        # checked with one against a stamp from the other never matched a rewritten project.
        self.project.write_bytes(b"BLENDER-v502" + b"an explicitly saved revision")
        receipt = self.receipt(self.c)
        real_stat = os.stat

        def creation_time_stat(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            extra = {name: getattr(info, name) for name in ("st_atime", "st_mtime", "st_atime_ns", "st_mtime_ns")}
            return os.stat_result(tuple(info)[:10], dict(extra, st_ctime=info.st_ctime - 1.0,
                                                          st_ctime_ns=info.st_ctime_ns - 10 ** 9))

        with patch.object(memory_projects.os, "stat", side_effect=creation_time_stat):
            self.assertTrue(receipt.valid_for(DOC_A, str(self.directory.resolve())))
            self.assertTrue(self.c.bind_project(receipt))

    def test_same_live_document_can_refresh_its_saved_revision_without_restart_authority(self):
        self.c.bind_project(self.receipt(self.c))
        self.brief(self.c)
        revision = self.c.state["task_memory"]["scope"]["project_revision"]
        self.project.write_bytes(b"BLENDER-v502" + b"an explicitly saved revision")
        self.assertTrue(self.c.bind_project(self.receipt(self.c)))
        self.assertNotEqual(self.c.state["task_memory"]["scope"]["project_revision"], revision)
        self.assertEqual(read_memory(self.c.state, DOC_A)["brief"]["objective"], "Keep this exact project brief")

    def test_same_live_unsaved_snapshot_recovery_rebinds_without_calling_save(self):
        self.brief(self.c)
        checkpoint = self.checkpoint(self.c)
        scope = self.c.project_scope_for_restore(checkpoint)
        self.c.state["native_id"] = "old-native-thread"
        self.c.state["runtime_sessions"] = {"codex_subscription": {"native_id": "old-native-thread"}}
        self.c._begin_metrics()
        context = self.c.runtime_context()
        old_epoch, old_generation = self.c._epoch, self.c._request_generation
        with patch.object(self.c, "save", side_effect=OSError("Injected post-load save failure")) as save:
            result = self.c.rebind_after_restore(DOC_B, checkpoint, project_scope=scope)
            save.assert_not_called()
        self.assertTrue(result["memory_rebound"])
        self.assertEqual(self.c.document, DOC_B)
        self.assertFalse(self.c.document_invalid)
        self.assertFalse(self.c.busy)
        self.assertTrue(context.cancelled)
        self.assertGreater(self.c._epoch, old_epoch)
        self.assertGreater(self.c._request_generation, old_generation)
        self.assertIsNone(self.c.state["native_id"])
        self.assertFalse(self.c.state["runtime_sessions"])
        memory = read_memory(self.c.state, DOC_B)
        self.assertEqual(memory["brief"]["objective"], "Keep this exact project brief")
        self.assertTrue(memory["stale"])
        self.assertNotIn("scn:", json.dumps(memory))

    def test_recovery_without_scope_revokes_all_chats_but_isolates_prior_briefs(self):
        self.brief(self.c)
        self.c.state["native_id"] = "old-thread"
        result = self.c.rebind_after_restore(DOC_B, "human-selected-checkpoint")
        self.assertFalse(result["memory_rebound"])
        self.assertEqual(self.c.document, DOC_B)
        self.assertIsNone(self.c.state["native_id"])
        self.assertTrue(read_memory(self.c.state, DOC_B)["scope_mismatch"])

    def test_restore_scope_from_new_launch_cannot_revive_old_unsaved_brief(self):
        self.brief(self.c)
        checkpoint = self.checkpoint(self.c)
        resumed = self.restart()
        scope = resumed.project_scope_for_restore(checkpoint)
        result = resumed.rebind_after_restore(("restored-new-launch", 99), checkpoint, project_scope=scope)
        self.assertFalse(result["memory_rebound"])
        self.assertTrue(read_memory(resumed.state, resumed.document)["scope_mismatch"])

    def test_recovery_working_copy_keeps_original_verified_saved_identity(self):
        self.c.bind_project(self.receipt(self.c))
        self.brief(self.c)
        original = dict(self.c.state["task_memory"]["scope"])
        checkpoint = self.checkpoint(self.c)
        scope = self.c.project_scope_for_restore(checkpoint)
        self.assertTrue(self.c.rebind_after_restore(DOC_B, checkpoint, project_scope=scope)["memory_rebound"])
        restored = self.c.state["task_memory"]["scope"]
        self.assertEqual(restored["project"], original["project"])
        self.assertEqual(restored["project_revision"], original["project_revision"])
        self.assertNotEqual(restored["document"], original["document"])

    def test_recovery_cancels_pending_calls_and_returns_cleanup_warning_without_skipping_fence(self):
        self.c.busy = True
        replies = []
        self.c.request_tool("queued-old", "angelo_inspect_scene", {}, replies.append)
        with patch.object(self.c, "save", side_effect=OSError("After restore")) as save:
            self.c.rebind_after_restore(DOC_B, "human-checkpoint")
            save.assert_not_called()
        self.assertFalse(self.c.pending)
        self.assertEqual(len(replies), 1)
        self.assertIn("error", replies[0])
        self.assertFalse(replies[0]["journal_durable"])
        self.assertTrue(self.c.tools.empty())

    def test_hashing_budget_and_unknown_file_format_fail_closed(self):
        with patch("angelo_agent.memory_projects.MAX_PROJECT_BYTES", 16):
            with self.assertRaises(ValueError):
                self.receipt(self.c)
        self.project.write_bytes(b"not an actual Blender scene" * 2)
        with self.assertRaises(ValueError):
            self.receipt(self.c)

    def test_explicit_human_rebind_resumes_large_or_unknown_project_notes_without_authority(self):
        self.brief(self.c)
        resumed = self.restart()
        review = resumed.project_rebind_review()
        self.assertEqual(review["brief"]["objective"], "Keep this exact project brief")
        with patch("angelo_agent.memory_projects.admit_saved_project", side_effect=AssertionError("No hashing in confirmation")):
            self.assertTrue(resumed.confirm_project_rebind(self.loaded(resumed), review["revision"]))
        memory = read_memory(resumed.state, DOC_B)
        self.assertEqual(memory["brief"]["objective"], review["brief"]["objective"])
        self.assertTrue(memory["stale"])
        self.assertEqual(memory["authority"], "none")
        self.assertIsNone(memory["scope"]["project"])
        self.assertNotIn("scn:", json.dumps(memory))
        self.assertEqual(resumed.state["messages"][-1]["request_kind"], "task_memory_rebind")
        self.assertFalse(resumed.busy)

    def test_human_rebind_refuses_json_stale_review_and_wrong_native_document(self):
        self.brief(self.c)
        resumed = self.restart()
        revision = resumed.project_rebind_review()["revision"]
        for receipt, expected in [({"approved": True}, revision), (self.loaded(self.c), revision),
                                  (self.loaded(resumed), revision + 1)]:
            with self.subTest(receipt=type(receipt).__name__, expected=expected):
                with self.assertRaises(ValueError):
                    resumed.confirm_project_rebind(receipt, expected)
        self.assertTrue(read_memory(resumed.state, DOC_B)["scope_mismatch"])

    def test_cross_chat_recovery_accepts_only_ready_source_with_same_live_document(self):
        self.brief(self.c)
        sibling = self.controller(DOC_A)
        self.brief(sibling)
        checkpoint = self.checkpoint(self.c)
        scope = sibling.project_scope_for_restore(checkpoint, source_controller=self.c)
        self.assertTrue(sibling.rebind_after_restore(DOC_B, checkpoint, project_scope=scope)["memory_rebound"])
        unrelated = self.controller(("unrelated-runtime", 77))
        with self.assertRaises(ValueError):
            unrelated.project_scope_for_restore(checkpoint, source_controller=self.c)

    def test_a_sibling_on_another_scene_of_the_same_load_shares_the_recovery_scope(self):
        """The scope belongs to the open document, not to one scene of it: a chat on another scene of the same load
        (``document_scene``) is a sibling, and its scope binds to its own scene's document."""
        self.brief(self.c)
        other_scene = self.controller((DOC_A[0], DOC_A[1] + 1))
        self.brief(other_scene)
        checkpoint = self.checkpoint(self.c)
        scope = other_scene.project_scope_for_restore(checkpoint, source_controller=self.c)
        self.assertEqual(scope.document, scope_token(other_scene.document))
        self.assertTrue(other_scene.rebind_after_restore(DOC_B, checkpoint, project_scope=scope)["memory_rebound"])


if __name__ == "__main__":
    unittest.main()
