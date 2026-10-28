# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 8 M-1: the chat's default workspace keeps its exception through journaling and recovery.

The default workspace lives in Angelo's state folder, which the OS convention makes private (macOS ~/Library, a hidden
folder in the home folder elsewhere). A journaled write, a delete, the store's inventory and Restore all go through the
checkpoint store's own workspace check, which takes the store's default workspace as the exception (from the store's
own folder, never from a journal's recorded root). A real controller, store and file tools; synthetic files only.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import private_roots  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402


class DefaultWorkspaceJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-default-workspace-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        (self.home / "tmp").mkdir()
        for patcher in (patch.dict(os.environ, {"HOME": str(self.home)}),
                        patch.object(tempfile, "tempdir", str(self.home / "tmp"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        convention = ("Library", "Application Support", "Angelo") if sys.platform == "darwin" else (".config", "angelo")
        self.state = self.home.joinpath(*convention, "5.2", "config", "angelo-agent")
        self.state.mkdir(parents=True)
        self.controller = Controller(self.state)
        self.addCleanup(self.controller.close)

    def workspaces(self):
        controller = self.controller
        yield controller.files   # The chat's own, before any project is prepared.
        roots = private_roots.private_roots(self.state, self.home / "accounts", [], environ={})
        yield FileWorkspace(controller.workspace, excluded=roots, own=controller.workspace)   # As ui.prepare_controller.

    def test_a_journaled_write_and_delete_in_the_default_workspace_restore(self):
        for files in self.workspaces():
            with self.subTest(files=files):
                workspace = self.controller.workspace
                (workspace / "data.txt").write_text("original")
                (workspace / "old.txt").write_text("kept")
                checkpoint, _ = self.controller.checkpoints.reserve()
                store = self.controller.checkpoints
                self.assertTrue(files.execute("angelo_write_file", {"path": "data.txt", "content": "changed"}, store,
                                              checkpoint["id"])["success"])
                files.execute("angelo_write_file", {"path": "notes/new.txt", "content": "new"}, store, checkpoint["id"])
                files.execute("angelo_delete_file", {"path": "old.txt"}, store, checkpoint["id"])
                self.assertEqual(store.inspect()["issues"], [])
                self.assertEqual(store.restore_since(checkpoint["id"]), 3)
                self.assertEqual((workspace / "data.txt").read_text(), "original")
                self.assertEqual((workspace / "old.txt").read_text(), "kept")
                self.assertFalse((workspace / "notes" / "new.txt").exists())

    def test_a_journal_root_elsewhere_in_the_state_folder_gets_no_exception(self):
        """The exception comes from the store's own folder: a journal naming another private folder is refused."""
        other = self.state / "elsewhere"
        other.mkdir()
        checkpoint, _ = self.controller.checkpoints.reserve()
        with self.assertRaises(ValueError):
            self.controller.checkpoints.journal(checkpoint["id"], other, "x.txt", pending=True)


if __name__ == "__main__":
    unittest.main()
