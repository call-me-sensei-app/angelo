# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 3 on file-tool privacy: process folders, and every folder actually opened.

* M-1: a worker Blender's or a headless session's private folder (state, accounts, log), and one a crashed child
  left, is private to every chat's file tools: its creator registers it where it is made (``private_roots``).
* M-2: each folder opened on the way to a target is checked, whatever path reached it (a private folder moved under
  an admitted path after admission stands in for a mount made then), and a bind mount of a subfolder is never
  crossed (Linux; ``files.subtree_mounts``).
Synthetic folders only.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import files as files_module  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def read(files, relative):
    return files.execute("angelo_read_file", {"path": relative, "start": 0, "length": 64})


def names(files, relative="."):
    return [entry["name"] for entry in files.execute("angelo_list_files", {"path": relative})["entries"]]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-private-process-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def refused(self, call, *arguments):
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            call(*arguments)

    @staticmethod
    def synthetic(folder, name="synthetic.txt"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("SYNTHETIC")


class ProcessFolderTests(Fixture):
    def test_a_workers_or_headless_folder_in_the_temporary_folder_is_private(self):
        """Review probe 1: the project is saved in the temporary folder that also holds the children's folders,
        including one a crashed child left (made by no creator in this process)."""
        from angelo_agent import headless_session, worker_process  # noqa: F401 - their families register on import.
        self.synthetic(self.base / "public")
        for prefix in (worker_process.PRIVATE_PREFIX, headless_session.PRIVATE_PREFIX):
            leftover = self.base / (prefix + "crashed")
            self.synthetic(leftover, "worker.log")
            self.synthetic(leftover / "state" / "sessions", "chat.json")
        with patch.object(tempfile, "tempdir", str(self.base)):
            files = FileWorkspace(self.base, excluded=[])
            self.assertEqual(names(files), ["public"])
            for prefix in (worker_process.PRIVATE_PREFIX, headless_session.PRIVATE_PREFIX):
                with self.subTest(prefix=prefix):
                    self.refused(read, files, prefix + "crashed/worker.log")
                    self.refused(read, files, prefix + "crashed/state/sessions/chat.json")
                    self.refused(names, files, prefix + "crashed")

    def test_a_family_member_is_found_by_path_where_the_filesystem_has_no_inode(self):
        """Break-it r2m4c R8-M-6: the temporary folder reports no inode (st_ino 0); a crashed converter's scratch in
        it is still private, by its parent's path."""
        self.synthetic(self.base / "angelo-assets-crashed", "batch.json")
        self.synthetic(self.base / "public")
        info = os.stat(self.base)
        real = files_module.folder_identity

        def no_inode(status):
            return None if (status.st_dev, status.st_ino) == (info.st_dev, info.st_ino) else real(status)
        with patch.object(tempfile, "tempdir", str(self.base)), patch.object(files_module, "folder_identity", no_inode):
            files = FileWorkspace(self.base, excluded=[])
            self.assertEqual(names(files), ["public"])
            self.refused(read, files, "angelo-assets-crashed/batch.json")

    def test_a_folder_made_after_the_workspace_was_set_up_is_private_at_once(self):
        from angelo_agent import private_roots
        files = FileWorkspace(self.base, excluded=[])
        folder = private_roots.allocate(private_roots.SCRATCH_PREFIXES[0], self.base)
        self.synthetic(folder, "worker.log")
        self.refused(read, files, folder.name + "/worker.log")
        self.assertEqual(names(files), [])

    def test_the_creators_register_their_folders_where_they_make_them(self):
        for module in ("worker_process.py", "headless_session.py"):
            with self.subTest(module=module):
                source = (ROOT / "scripts/addons_core/angelo_agent" / module).read_text()
                self.assertIn("PRIVATE_PREFIX = private_roots.", source)   # The one table (round 5 M-1).
                self.assertIn("private_roots.allocate(PRIVATE_PREFIX", source)
                self.assertNotIn("mkdtemp(prefix=PRIVATE_PREFIX", source)


class OpenedFolderTests(Fixture):
    def test_a_private_folder_reached_after_admission_is_refused(self):
        """Review M-2 TOCTOU: the admitted path ``alias`` is public when checked; then the private folder is what
        opening it reaches (here moved there: macOS offers no bind mount without root)."""
        private, project = self.base / "profile", self.base / "project"
        self.synthetic(private / "sessions", "chat.json")
        self.synthetic(project / "alias" / "sessions", "chat.json")
        base = self.base

        class Swapping(FileWorkspace):
            def path(self, relative):
                admitted = super().path(relative)
                if (project / "alias").exists() and not (base / "away").exists():
                    os.rename(project / "alias", base / "away")
                    os.rename(private, project / "alias")
                return admitted
        files = Swapping(project, excluded=[private])
        self.refused(read, files, "alias/sessions/chat.json")

    def test_a_blend_reader_reaching_a_private_folder_after_admission_is_refused(self):
        """Break-it r2m4c R8-M-5: the .blend metadata reader opens its folder through the workspace's own admission;
        the private folder is swapped in after ``path()`` admitted ``public``."""
        from types import SimpleNamespace
        from angelo_agent import blend_file_listing
        private, project = self.base / "profile", self.base / "project"
        for folder in (private, project / "public"):
            folder.mkdir(parents=True)
            (folder / "checkpoint.blend").write_bytes(b"BLENDER-v405" + b"\0" * 64)
        files = FileWorkspace(project, excluded=[private])
        scans = []
        api = SimpleNamespace(metadata_scan=lambda *arguments: scans.append(arguments) or {"status": "INVALID"},
                              metadata_cancel_token=lambda seconds: object(), metadata_cancel=lambda token: None)

        def swap():
            if (project / "public").exists() and not (self.base / "away").exists():
                os.rename(project / "public", self.base / "away")
                os.rename(private, project / "public")
        real_files, real_listing = files_module._private_directory, blend_file_listing._private_directory
        with patch.object(files_module, "_private_directory", side_effect=lambda *a, **k: (swap(), real_files(*a, **k))[1]), \
                patch.object(blend_file_listing, "_private_directory",
                             side_effect=lambda *a, **k: (swap(), real_listing(*a, **k))[1]):
            result = blend_file_listing.read(None, {"file": "public/checkpoint.blend", "offset": 0, "limit": 10}, files,
                                             api=api, token=object())
        self.assertFalse(result["readable"])
        self.assertEqual(scans, [], "the private file was never scanned")

    def test_a_bind_mount_of_a_subfolder_is_never_crossed(self):
        """Review M-2: ``history`` is a bind mount of a private descendant (Linux). Listing hides it; reading
        through it is refused."""
        self.synthetic(self.base / "history", "chat.json")
        self.synthetic(self.base / "public")
        files = FileWorkspace(self.base, excluded=[])
        with patch.object(files_module, "subtree_mounts", return_value=frozenset({str(self.base / "history")})):
            self.assertEqual(names(files), ["public"])
            self.refused(read, files, "history/chat.json")
            self.refused(names, files, "history")
        self.assertEqual(read(files, "history/chat.json")["text"], "SYNTHETIC", "an ordinary folder is readable")

    def test_mountinfo_names_only_mounts_of_a_subfolder(self):
        text = ("22 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
                "40 22 8:1 /home/u/profile/sessions /work/history rw - ext4 /dev/sda1 rw\n"
                "41 22 8:1 /data /work/with\\040space rw - ext4 /dev/sda1 rw\n"
                "42 22 0:5 / /proc rw - proc proc rw\n")
        self.assertEqual(files_module.subtree_mounts(text), frozenset({"/work/history", "/work/with space"}))


class RenamedProcessFolderTests(Fixture):
    def test_a_process_folder_moved_under_a_neutral_name_stays_private(self):
        """Round 4 M-2: allocated, then moved into the workspace as ``archive`` before any file tool saw it."""
        from angelo_agent import private_roots
        (self.base / "temporary").mkdir()
        (self.base / "project").mkdir()
        folder = private_roots.allocate(private_roots.SCRATCH_PREFIXES[0], self.base / "temporary")
        self.synthetic(folder, "worker.log")
        self.synthetic(folder / "state" / "sessions", "chat.json")
        os.rename(folder, self.base / "project" / "archive")
        files = FileWorkspace(self.base / "project", excluded=[])
        self.assertEqual(names(files), [])
        self.refused(read, files, "archive/worker.log")
        self.refused(read, files, "archive/state/sessions/chat.json")
        self.refused(names, files, "archive/state")


class ReusedIdentityTests(Fixture):
    """C2 CI (Linux runner, shared /tmp): a removed allocated folder's inode number, which ext4 gives the next new
    folder, made ordinary workspaces and their entries private. Simulated here (APFS never reuses one): the registry
    holds a removed folder's identity that now names a new, ordinary folder."""

    def reuse(self, folder):
        from angelo_agent import private_roots
        allocated = private_roots.allocate(private_roots.WORKER_PREFIX, self.base)
        info = os.stat(folder)
        identity = (info.st_dev, info.st_ino)
        import shutil
        if hasattr(private_roots, "_owned"):
            old = next(key for key, value in private_roots._owned.items()
                       if (allocated / private_roots.OWNER).read_text() == value)
            shutil.rmtree(allocated)
            private_roots._owned[identity] = private_roots._owned.pop(old)
            held = getattr(private_roots, "_held", {})
            if old in held:
                held[identity] = held.pop(old)   # The handle of the removed folder, now under the reused number.
        else:
            private_roots._identities.add(identity)
            shutil.rmtree(allocated)

    def test_an_ordinary_folder_given_a_removed_folders_identity_stays_ordinary(self):
        project = self.base / "project"
        self.synthetic(project / "public")
        self.reuse(project / "public")
        files = FileWorkspace(project, excluded=[])
        self.assertEqual(names(files), ["public"])
        self.assertEqual(read(files, "public/synthetic.txt")["text"], "SYNTHETIC")
        self.reuse(project)   # The workspace itself.
        self.assertEqual(read(FileWorkspace(project, excluded=[]), "public/synthetic.txt")["text"], "SYNTHETIC")

    def test_a_folder_beside_a_process_folder_in_a_shared_temporary_folder_is_ordinary(self):
        """Held out: /tmp shared by everyone (Linux), the project beside a live worker's folder."""
        from angelo_agent import private_roots
        with patch.object(tempfile, "tempdir", str(self.base)):
            worker = private_roots.allocate(private_roots.WORKER_PREFIX, self.base)
            self.synthetic(worker, "worker.log")
            self.synthetic(self.base / "project-from-zip")
            files = FileWorkspace(self.base, excluded=[])
            self.assertEqual(names(files), ["project-from-zip"])
            inside = FileWorkspace(self.base / "project-from-zip", excluded=[])
            self.assertEqual(read(inside, "synthetic.txt")["text"], "SYNTHETIC")
            self.refused(read, files, worker.name + "/worker.log")


@unittest.skipIf(os.name == "nt", "the POSIX stand-in for the Windows backend; windows_ntfs_checks.py runs NTFS")
class WindowsWalkTests(Fixture):
    """Round 4 M-3, through the Windows backend's handles (``fake_nt_files.FakeNtApi``, as
    test_native_windows_anchoring): every opened folder is checked, and the workspace must be the one set up."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from angelo_agent import checkpoint_filesystem
        from fake_nt_files import FakeNtApi
        patcher = patch.object(checkpoint_filesystem, "_windows_api", return_value=FakeNtApi())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_private_folder_reached_after_admission_is_refused(self):
        """The swap happens as the folder is opened, after every path check (the walk alone must refuse it)."""
        private, project = self.base / "profile", self.base / "project"
        self.synthetic(private / "sessions", "chat.json")
        self.synthetic(project / "alias" / "sessions", "chat.json")
        files = FileWorkspace(project, excluded=[private])
        real = files_module._private_directory

        def swapping(*arguments, **options):
            if (project / "alias").exists() and not (self.base / "away").exists():
                os.rename(project / "alias", self.base / "away")
                os.rename(private, project / "alias")
            return real(*arguments, **options)
        with patch.object(files_module, "_private_directory", side_effect=swapping):
            self.refused(read, files, "alias/sessions/chat.json")

    def test_a_replaced_workspace_is_refused(self):
        project = self.base / "project"
        self.synthetic(project)
        files = FileWorkspace(project, excluded=[self.base / "profile"])
        self.assertEqual(read(files, "synthetic.txt")["text"], "SYNTHETIC")
        os.rename(project, self.base / "old-project")
        self.synthetic(project)   # Another folder at the same path.
        with self.assertRaisesRegex(ValueError, "replaced"):
            read(files, "synthetic.txt")

    def test_a_private_folder_inside_the_workspace_is_refused_on_the_walk(self):
        private = self.base / "project" / "middle" / "profile"
        self.synthetic(private / "sessions", "chat.json")
        files = FileWorkspace(self.base / "project", excluded=[private])
        self.assertEqual(names(files, "middle"), [])
        self.refused(read, files, "middle/profile/sessions/chat.json")


if __name__ == "__main__":
    unittest.main()
