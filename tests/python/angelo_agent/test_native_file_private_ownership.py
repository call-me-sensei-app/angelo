# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 9 on file-tool privacy: ownership that survives moves, restarts, temporary-folder and
account-store changes, registry loss, and never outlives the folder.

* M-1: an allocated folder moved under a neutral name stays private when its owner file is deleted (a held handle),
  and any folder holding an owner file is private after a restart.
* M-2: Angelo's own folders are made in a stable scratch folder inside the per-user data folder, not the system
  temporary folder.
* M-3: a workspace consumes a later used-store registration (every registry file invalidates its cache).
* M-4: a runtime home stays private after the account store changes (an account-independent record).
* M-5: a missing list whose last good copy exists is a loss: the copy stands (profiles and used stores).
* M-6: a durable root's identity stops counting once that folder is gone.
Synthetic folders only.
"""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import private_roots, storage  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402


def read(files, relative):
    return files.execute("angelo_read_file", {"path": relative, "start": 0, "length": 64})


def names(files, relative="."):
    return [entry["name"] for entry in files.execute("angelo_list_files", {"path": relative})["entries"]]


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-private-ownership-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        (self.base / "tmp").mkdir()
        self.project = self.base / "Documents" / "Project"
        self.project.mkdir(parents=True)
        for patcher in (patch.dict(os.environ, {"HOME": str(self.base), "XDG_STATE_HOME": str(self.base / "state-home")}),
                        patch.object(tempfile, "tempdir", str(self.base / "tmp"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        getattr(private_roots, "_retained_cache", {}).clear()
        self.addCleanup(getattr(private_roots, "_retained_cache", {}).clear)

    def refused(self, call, *arguments):
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            call(*arguments)

    @staticmethod
    def synthetic(folder, name="synthetic.txt"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("SYNTHETIC")


class AllocationTests(Fixture):
    def test_a_moved_allocation_stays_private_when_its_owner_file_is_deleted(self):
        folder = private_roots.allocate(private_roots.SCRATCH_PREFIXES[0])
        self.synthetic(folder, "batch.json")
        os.rename(folder, self.project / "archive")
        (self.project / "archive" / private_roots.OWNER).unlink()
        files = FileWorkspace(self.project, excluded=[])
        self.assertEqual(names(files), [])
        self.refused(read, files, "archive/batch.json")

    def test_a_folder_holding_an_owner_file_is_private_after_a_restart(self):
        self.synthetic(self.project / "archive", "batch.json")
        (self.project / "archive" / ".angelo-private-owner").write_text("an earlier process's token")
        files = FileWorkspace(self.project, excluded=[])
        self.assertEqual(names(files), [])
        self.refused(read, files, "archive/batch.json")

    def test_angelos_folders_are_made_in_a_stable_scratch_folder_inside_the_data_folder(self):
        folder = private_roots.allocate(private_roots.WORKER_PREFIX)
        self.assertTrue(folder.is_relative_to(storage.data_directory()), folder)
        self.assertFalse(folder.is_relative_to(self.base / "tmp"))
        with private_roots.TemporaryDirectory(private_roots.SCRATCH_PREFIXES[0]) as scratch:
            self.assertTrue(Path(scratch).is_relative_to(storage.data_directory()))


class RegistryTests(Fixture):
    def accounts(self, name):
        folder = self.base / name
        folder.mkdir(exist_ok=True)
        return folder

    def test_a_workspace_consumes_a_later_used_store(self):
        files = FileWorkspace(self.project, excluded=private_roots.private_roots(self.base / "state", self.accounts("a"),
                                                                                 [], environ={}))
        self.synthetic(self.project / "chat-cache" / "sessions", "chat.json")
        self.assertIn("chat-cache", names(files))   # Warmed: the registry has been read.
        private_roots.private_roots(self.project / "chat-cache", self.accounts("b"), [], environ={})   # Elsewhere.
        self.refused(read, files, "chat-cache/sessions/chat.json")

    def test_a_runtime_home_stays_private_after_the_account_store_changes(self):
        self.synthetic(self.project / "native-home", "auth.json")
        private_roots.private_roots(self.base / "state", self.accounts("a"), [],
                                    environ={"CODEX_HOME": str(self.project / "native-home")})
        private_roots._retained_cache.clear()
        roots = private_roots.private_roots(self.base / "state", self.accounts("b"), [], environ={})
        self.refused(read, FileWorkspace(self.project, excluded=roots), "native-home/auth.json")

    def test_a_missing_list_beside_its_last_good_copy_is_a_loss(self):
        self.synthetic(self.project / "native-home", "auth.json")
        self.synthetic(self.project / "old-state" / "sessions", "chat.json")
        accounts = self.accounts("a")
        private_roots.private_roots(self.project / "old-state", accounts, [],
                                    environ={"CODEX_HOME": str(self.project / "native-home")})
        (accounts / private_roots.RETAINED_PROFILES).unlink()
        used = getattr(private_roots, "USED_STORES", None)
        if used:
            (storage.data_directory() / used).unlink()
        private_roots._retained_cache.clear()
        roots = private_roots.private_roots(self.base / "usual-state", self.accounts("b"), [], environ={})
        files = FileWorkspace(self.project, excluded=roots)
        self.refused(read, files, "native-home/auth.json")
        self.refused(read, files, "old-state/sessions/chat.json")

    def test_the_used_store_list_is_written_where_it_is_read(self):
        """Linux CI (round 10): with XDG_STATE_HOME set and runtime homes named by another environment, the used-store
        list went to ~/.local/state while it was read from XDG_STATE_HOME. Both follow the process's environment."""
        with patch.object(storage.sys, "platform", "linux"):
            private_roots.private_roots(self.base / "state", self.accounts("a"), [], environ={})
            written = storage.data_directory() / private_roots.USED_STORES
            self.assertTrue(written.is_file(), written)
            self.assertTrue(written.is_relative_to(self.base / "state-home"))

    def test_a_removed_roots_identity_stops_counting(self):
        root = self.project / "custom-root"
        root.mkdir()
        files = FileWorkspace(self.project, excluded=[root])
        identity = (os.stat(root).st_dev, os.stat(root).st_ino)
        self.assertIn(identity, files._private())
        shutil.rmtree(root)
        self.assertNotIn(identity, files._private(), "a bare number never outlives its folder")


if __name__ == "__main__":
    unittest.main()
