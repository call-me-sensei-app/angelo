# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows anchored-file backend, on any host through ``fake_nt_files.FakeNtApi``.

``checkpoint_filesystem`` selects the Windows backend whenever ``_windows_api()``
returns an API, so these tests run the real file tools, journals, restore and
snapshot retention through ``anchored_windows``. They prove its logic; the Windows
run proves NTFS' own semantics (doc/angelo/checkpoint_policy.rst, "Windows file
anchoring").
"""

import errno
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import anchored_windows as aw
from angelo_agent import checkpoint_filesystem as cf
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.files import FileWorkspace
from angelo_agent.snapshot_retention import enforce_retention
import fake_nt_files
from fake_nt_files import FakeNtApi

CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL


@unittest.skipIf(os.name == "nt", "The POSIX stand-in opens links with O_PATH or O_SYMLINK; on Windows "
                                   "windows_ntfs_checks.py runs the real backend")
class WindowsBackendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-windows-anchoring-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.api = FakeNtApi()
        patcher = patch.object(cf, "_windows_api", return_value=self.api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_new(self, folder, name, data):
        with os.fdopen(cf._open_file(folder, name, CREATE), "wb") as handle:
            handle.write(data)

    def test_private_storage_runs_on_verified_handles(self):
        with cf._private_directory(self.root / "state" / "nested", create=True) as folder:
            self.assertIsInstance(folder, aw.WindowsDirectory)
            cf._write_json(folder, "checkpoint.json", {"state": "READY"})
            cf._write_json(folder, "checkpoint.json", {"state": "PRUNED"})
            self.assertEqual(cf._read_json(folder, "checkpoint.json"), {"state": "PRUNED"})
            self.assertEqual(cf.list_names(folder), ["checkpoint.json"])  # No temporary left; no "." or "..".
            self.assertTrue(stat.S_ISDIR(cf.directory_info(folder).st_mode))
        self.assertIn(("rename_at", "checkpoint.json", True), self.api.calls)
        self.assertTrue(cf.anchored_files_supported())
        with patch.object(os, "supports_dir_fd", set()):
            cf.require_anchored_files()
            cf.require_safe_deletion()

    def test_link_or_file_in_the_directory_chain_is_refused_before_any_effect(self):
        real = self.root / "real"
        real.mkdir()
        (self.root / "link").symlink_to(real, target_is_directory=True)
        (self.root / "file").write_text("not a directory")
        for path in (self.root / "link" / "new", self.root / "file" / "new"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "cannot follow directory links"):
                with cf._private_directory(path, create=True):
                    pass
        self.assertEqual(list(real.iterdir()), [])

    def test_a_child_folder_gets_the_walks_checks_relative_to_its_held_parent(self):
        parent = self.root / "checkpoints"
        (parent / "chat").mkdir(parents=True)
        (parent / "chat" / "checkpoint.json").write_text("{}")
        (parent / "file").write_text("not a directory")
        (parent / "link").symlink_to(parent / "chat", target_is_directory=True)
        self.api.aliases[(str(parent), "CHAT~1")] = "chat"
        with cf._private_directory(parent) as folder:
            mark = len(self.api.calls)
            with cf._private_child(folder, "chat") as child:
                self.assertIsInstance(child, aw.WindowsDirectory)
                self.assertEqual(cf.list_names(child), ["checkpoint.json"])
            # One open relative to the held parent, not a new walk from the volume root.
            self.assertEqual([call[1] for call in self.api.calls[mark:] if call[0] == "open_at"], ["chat"])
            for name, error in (("link", "cannot follow directory links"), ("file", "cannot follow directory links"),
                                ("CHAT~1", "Short"), ("chat/x", "single child"), ("..", "single child")):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, error):
                    with cf._private_child(folder, name):
                        pass

    def test_a_link_entry_is_reported_and_never_followed(self):
        target = self.root / "target.txt"
        target.write_text("outside")
        (self.root / "link.txt").symlink_to(target)
        with cf._private_directory(self.root) as folder:
            self.assertTrue(stat.S_ISLNK(cf.entry_info(folder, "link.txt").st_mode))
            with self.assertRaisesRegex(ValueError, "invalid file/link"):
                cf._file_info(folder, "link.txt")
            for flags in (os.O_RDONLY, os.O_WRONLY | os.O_CREAT):
                with self.subTest(flags=flags), self.assertRaises(OSError) as raised:
                    cf._open_file(folder, "link.txt", flags)
                self.assertEqual(raised.exception.errno, errno.ELOOP)
            with self.assertRaises(FileExistsError):
                cf._open_file(folder, "link.txt", CREATE)
            cf._unlink_file(folder, "link.txt")  # Deletes the link itself.
        self.assertFalse((self.root / "link.txt").is_symlink())
        self.assertEqual(target.read_text(), "outside")

    def test_short_name_aliases_are_refused_for_directories_and_files(self):
        (self.root / ".git").mkdir()
        (self.root / ".env").write_text("TOKEN=secret")
        self.api.aliases.update({(str(self.root), "GIT~1"): ".git", (str(self.root), "ENV~1"): ".env"})
        with self.assertRaisesRegex(ValueError, "Short"):
            with cf._private_directory(self.root / "GIT~1"):
                pass
        with cf._private_directory(self.root) as folder:
            for operation in (lambda: cf._open_file(folder, "ENV~1", os.O_RDONLY), lambda: cf.entry_info(folder, "ENV~1"),
                              lambda: cf._unlink_file(folder, "ENV~1")):
                with self.assertRaisesRegex(ValueError, "Short"):
                    operation()
        self.assertTrue((self.root / ".env").exists())

    def test_names_windows_cannot_hold_exactly_are_refused(self):
        with cf._private_directory(self.root) as folder:
            for name in ("notes.md:hidden", "notes.md::$DATA", "NUL", "con.txt", "COM1.log", "lpt\u00b9", "dot.",
                         "space ", "a|b", "a?b", "a*b", "tab\tname", "..", ""):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Windows can't use"):
                    cf._open_file(folder, name, CREATE)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_publication_replacement_and_removal_act_on_the_opened_file(self):
        with cf._private_directory(self.root) as folder:
            self.write_new(folder, "staged.tmp", b"first")
            cf.publish_new_file(folder, "staged.tmp", "out.txt")
            self.assertEqual(cf.entry_info(folder, "out.txt").st_nlink, 2)  # Both names until the stage is removed.
            with self.assertRaises(FileExistsError):
                cf.publish_new_file(folder, "staged.tmp", "out.txt")  # Never overwrites.
            cf._unlink_file(folder, "staged.tmp")
            self.write_new(folder, "next.tmp", b"second")
            cf._replace_file(folder, "next.tmp", "out.txt")
            cf.chmod_file(folder, "out.txt", 0o444)
            self.assertFalse(cf.entry_info(folder, "out.txt").st_mode & stat.S_IWUSR)
            cf.chmod_file(folder, "out.txt", 0o644)
        self.assertEqual((self.root / "out.txt").read_bytes(), b"second")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["out.txt"])
        self.assertIn(("link_at", "out.txt"), self.api.calls)

    def test_truncation_happens_only_after_the_checks(self):
        (self.root / "data.txt").write_text("old contents")
        target = self.root / "target.txt"
        target.write_text("outside")
        (self.root / "link.txt").symlink_to(target)
        with cf._private_directory(self.root) as folder:
            os.close(cf._open_file(folder, "data.txt", os.O_WRONLY | os.O_TRUNC))
            with self.assertRaises(OSError) as raised:
                cf._open_file(folder, "link.txt", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            self.assertEqual(raised.exception.errno, errno.ELOOP)
        self.assertEqual((self.root / "data.txt").read_text(), "")
        self.assertTrue((self.root / "link.txt").is_symlink())
        self.assertEqual(target.read_text(), "outside")
        # Never an overwrite disposition: on NTFS it acts on a link before the link check (it emptied one).
        dispositions = {call[3] for call in self.api.calls if call[0] == "open_at"}
        self.assertFalse(dispositions & {aw.FILE_OVERWRITE, aw.FILE_OVERWRITE_IF})

    def test_provider_placeholders_are_read_through_the_same_file_only(self):
        cloud, other = self.root / "cloud.txt", self.root / "other.txt"
        cloud.write_text("hydrated")
        other.write_text("elsewhere")
        self.api.data_reparse.add(str(cloud))
        with cf._private_directory(self.root) as folder:
            self.assertTrue(stat.S_ISREG(cf.entry_info(folder, "cloud.txt").st_mode))
            with os.fdopen(cf._open_file(folder, "cloud.txt", os.O_RDONLY), "rb") as handle:
                self.assertEqual(handle.read(), b"hydrated")
            with self.assertRaises(OSError) as raised:
                cf._open_file(folder, "cloud.txt", os.O_RDWR)
            self.assertEqual(raised.exception.errno, errno.EPERM)
            self.api.redirect[str(cloud)] = other   # A reopen that reaches another file is a link.
            with self.assertRaises(OSError) as raised:
                cf._open_file(folder, "cloud.txt", os.O_RDONLY)
            self.assertEqual(raised.exception.errno, errno.ELOOP)

    def test_within_is_checked_lexically_and_on_final_paths(self):
        workspace = self.root / "workspace"
        (workspace / "sub").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "outside the selected workspace"):
            with cf._private_directory(self.root / "elsewhere", create=True, within=workspace):
                pass
        with cf._private_directory(workspace / "sub", within=workspace) as folder:
            self.assertEqual(folder.path, workspace / "sub")
        self.api.final_paths[str(workspace / "sub")] = "\\\\?\\D:\\moved\\sub"
        with self.assertRaisesRegex(ValueError, "resolved outside"):
            with cf._private_directory(workspace / "sub", within=workspace):
                pass


@unittest.skipIf(os.name == "nt", "The POSIX stand-in opens links with O_PATH or O_SYMLINK; on Windows "
                                   "windows_ntfs_checks.py runs the real backend")
class WindowsFileToolTests(unittest.TestCase):
    """The agent's file tools, journals and restore through the Windows backend."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-windows-file-tools-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.api = FakeNtApi()
        patcher = patch.object(cf, "_windows_api", return_value=self.api)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.workspace = FileWorkspace(self.project)

    def snapshot(self, data=b"synthetic host scene"):
        record, path = self.store.reserve()
        path.write_bytes(data)
        return self.store.complete(record["id"])

    def call(self, name, snapshot, **arguments):
        return self.workspace.execute(name, arguments, self.store, snapshot["id"])

    def test_write_read_list_delete_and_restore(self):
        first = self.snapshot()
        self.assertTrue(self.call("angelo_write_file", first, path="docs/notes.md", content="one")["success"])
        second = self.snapshot()
        self.call("angelo_write_file", second, path="docs/notes.md", content="two \u00e9")
        read = self.workspace.execute("angelo_read_file", {"path": "docs/notes.md", "start": 0, "length": 100})
        self.assertEqual(read["text"], "two \u00e9")
        listed = self.workspace.execute("angelo_list_files", {"path": "docs"})
        self.assertEqual(listed["entries"], [{"name": "notes.md", "directory": False, "bytes": 6}])
        self.call("angelo_delete_file", self.snapshot(), path="docs/notes.md")
        self.assertFalse((self.project / "docs/notes.md").exists())
        self.store.restore_since(second["id"])
        self.assertEqual((self.project / "docs/notes.md").read_text(), "one")
        self.assertEqual(self.store.inspect()["issues"], [])
        names = [call[0] for call in self.api.calls]
        self.assertIn("link_at", names)     # The new file: no-overwrite publication.
        self.assertIn("rename_at", names)   # The rewrite and the restore.
        self.assertIn("delete", names)

    def test_directory_swapped_for_a_link_after_journaling_is_refused(self):
        snapshot = self.snapshot()
        inside, outside = self.project / "inside", self.root / "outside"
        inside.mkdir()
        outside.mkdir()
        (inside / "notes.md").write_text("inside")
        (outside / "notes.md").write_text("outside")
        original = self.store.journal

        def swap(*args, **kwargs):
            result = original(*args, **kwargs)
            inside.rename(inside.with_name("moved"))
            inside.symlink_to(outside, target_is_directory=True)
            return result

        with patch.object(self.store, "journal", side_effect=swap), self.assertRaises((ValueError, OSError)):
            self.call("angelo_write_file", snapshot, path="inside/notes.md", content="escaped")
        self.assertEqual((outside / "notes.md").read_text(), "outside")
        self.assertEqual(self.store.load(snapshot["id"])["files"], [])

    def test_workspace_links_and_drive_relative_paths_are_refused(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        (self.project / "link.txt").symlink_to(outside)
        for path in ("link.txt", "../outside.txt", ".env", str(outside)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.workspace.execute("angelo_read_file", {"path": path, "start": 0, "length": 10})

    def test_retention_waits_for_a_snapshot_another_program_holds(self):
        snapshots = [self.snapshot(bytes(4096)) for _ in range(4)]
        held = self.store.folder(snapshots[0]["id"]) / "scene.blend"
        self.api.held.add(str(held))
        with self.assertRaisesRegex(PermissionError, "Another program has this file open; nothing was removed"):
            enforce_retention(self.store, {"max_count": 2})
        # Nothing changed: no tombstone without its removal, no storage the inventory refuses.
        self.assertEqual(self.store.inspect()["issues"], [])
        self.assertTrue(self.store.availability(snapshots[0]["id"])["available"])
        self.api.held.clear()
        summary = enforce_retention(self.store, {"max_count": 2})
        self.assertEqual(len(summary["removed"]), 2)
        self.assertEqual(self.store.inspect()["issues"], [])
        self.assertFalse(held.exists())

    def test_retention_removes_snapshots_through_handles(self):
        snapshots = [self.snapshot(bytes(4096)) for _ in range(4)]
        plan = self.store.prune_plan({"max_count": 2})
        result = self.store.prune(plan, user_confirmed=True)
        self.assertEqual(len(result["removed"]), 2)
        self.assertEqual([self.store.availability(item["id"])["available"] for item in snapshots],
                         [False, False, True, True])
        summary = enforce_retention(self.store, {"max_bytes": 6000})
        self.assertTrue(summary["removed"])
        self.assertLessEqual(summary["remaining_bytes"], 6000)
        self.assertTrue(summary["budget_met"])
        self.assertIn("delete", [call[0] for call in self.api.calls])


class StandInHostTests(unittest.TestCase):
    """The stand-in skips on a host whose real backend runs instead (Windows), never only where a class says so:
    on the Windows T4 (2026-10-25) two classes without their own skipIf ran it and errored 41 times."""

    def test_the_stand_in_skips_on_a_real_backend_host_and_runs_elsewhere(self):
        with patch.object(fake_nt_files, "REAL_BACKEND_HOST", True), self.assertRaises(unittest.SkipTest):
            FakeNtApi()
        with patch.object(fake_nt_files, "REAL_BACKEND_HOST", False):
            self.assertEqual(FakeNtApi().calls, [])


if __name__ == "__main__":
    unittest.main()
