# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Restore's outcome reaches the journal with a real chat: its notice is marked, saved, and only persistence
errors are caught around that save (B1 break-it review, F1 and F2).

The Restore flow's own fault matrix (``test_native_checkpoint_restore_ui.py``) runs on stand-in chats whose
``save`` only records, so the journal's touch check never ran there. Here the active chat is a real ``Controller``
with its ``SessionStore``; the scene, the snapshot store and Blender stay the matrix's stand-ins. Before the fix,
a failure after the recovery scene opened changed the notice in place, unmarked and unsaved: the pump's owed save
(B1) then skipped it, and a crash left ``pending`` on disk where the scene was in fact replaced.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import test_native_checkpoint_restore_ui as base  # noqa: E402  (a module, so its tests are not collected twice)
from angelo_agent import checkpoint_restore_ui as flow, session_writer, sessions  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402


class RestoreOutcomeJournal(unittest.TestCase):
    def setUp(self):
        matrix = base.CheckpointRestoreUITests("test_ordinary_restore_persists_safety_before_open_and_rebinds_every_chat_"
                                               "before_saving")
        matrix.setUp()   # The scene, snapshot store and Blender stand-ins.
        self.addCleanup(matrix.doCleanups)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        for name, value in (("CHECK_TOUCHES", True), ("VERIFY_JOURNAL", True)):
            check = mock.patch.object(session_writer if name == "CHECK_TOUCHES" else sessions, name, value, create=True)
            check.start()
            self.addCleanup(check.stop)
        chat = self.chat = Controller(self.directory)
        self.addCleanup(chat.close)
        chat.set_document(("old", 1))
        with chat.lock:
            chat.state["messages"].append({"role": "user", "text": "Before request", "checkpoint": "selected",
                                           "checkpoint_scope": "before_request"})
        chat.save()
        chat.checkpoints = matrix.active.checkpoints
        chat.rebind_after_restore = mock.Mock(return_value={"warnings": []})
        chat.project_scope_for_restore = mock.Mock(return_value="host-issued-scope")
        self.ui, self.operator = matrix.ui, matrix.operator
        self.ui.get_controller, self.ui._controllers = (lambda: chat), {"active": chat}

        def open_scene(**kwargs):
            self.ui.bpy.data.filepath = kwargs["filepath"]
            return {"FINISHED"}
        self.ui.bpy.ops.wm.open_mainfile.side_effect = open_scene

    def notice_on_disk(self):
        """The safety notice as a relaunch reads it: the process died after the Restore, no further save."""
        rows = SessionStore(self.directory).load()["messages"]
        return next(row for row in rows if row.get("restore_checkpoint") == "safety")

    def test_a_failure_after_the_scene_opened_saves_the_partial_outcome(self):
        # Fails after the recovery scene replaced the document, before any chat is rebound (the unexpected branch).
        with mock.patch.object(flow.restored_document, "adopt_or_say", side_effect=RuntimeError("finalization fault")):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {"FINISHED"})
        self.assertEqual(self.notice_on_disk()["restore_state"], "partial")
        self.assertTrue(self.notice_on_disk()["scene_restored"])
        # The pump's next owed save (B1) writes what changed and finds nothing unmarked.
        self.chat.emit("setup_required", {})
        self.chat._last_save -= 1.0
        self.chat.pump_events()
        self.assertFalse(self.chat._save_owed)
        self.assertEqual(SessionStore(self.directory).load()["messages"], sessions.json.loads(
            sessions.json.dumps(self.chat.state["messages"])))

    def test_a_journal_check_failing_in_a_restores_save_is_not_swallowed(self):
        """Only persistence errors are caught around a Restore's saves: a check's AssertionError reaches the test."""
        def refused_open(**_kwargs):   # The open fails before replacing anything; a row changed meanwhile, unmarked.
            with self.chat.lock:
                self.chat.state["messages"][0]["text"] = "changed, never marked"   # A defect the touch check names.
            raise OSError("Invalid recovery scene")
        self.ui.bpy.ops.wm.open_mainfile.side_effect = refused_open
        with self.assertRaises(AssertionError) as raised:   # From the not-applied outcome's save, never swallowed.
            flow.execute_restore(self.ui, self.operator)
        self.assertIn("row 0", str(raised.exception))

    def test_a_persistence_failure_in_a_restores_save_is_reported_in_the_notice(self):
        real_save = SessionStore.save
        calls = []

        def failing_after_the_safety_link(store, state, **options):
            calls.append(1)
            if len(calls) > 1:   # The strict safety link saves; the outcome's save finds a full disk.
                raise OSError(28, "No space left on device")
            return real_save(store, state, **options)
        with mock.patch.object(SessionStore, "save", failing_after_the_safety_link), \
                mock.patch.object(flow.restored_document, "adopt_or_say", side_effect=RuntimeError("fault")):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {"FINISHED"})
        notice = next(row for row in self.chat.state["messages"] if row.get("restore_checkpoint") == "safety")
        self.assertEqual(notice["restore_state"], "partial")
        self.assertEqual(self.notice_on_disk()["restore_state"], "pending", "the disk was full")
        self.chat.emit("setup_required", {})   # Space again: the next owed save writes the marked outcome.
        self.chat._save_owed, self.chat._last_save = True, self.chat._last_save - 1.0
        self.chat.pump_events()
        self.assertEqual(self.notice_on_disk()["restore_state"], "partial")


if __name__ == "__main__":
    unittest.main()
