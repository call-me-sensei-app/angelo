# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a break-it review of the build machine's source sends found, each turned into the class it belongs to.

* A Git LFS pointer passed as "exactly the commit" (git status runs it through the clean filter unchanged): sync
  never refused one, and a renamed or re-moded large file kept the old digest and arrived as its pointer. Every send
  refuses pointers now, the machine applies the large-file archive whenever a file differs, and the proof fails
  while any file the build needs real is a pointer.
* A file that became a folder (or the reverse), or a folder kept by an ignored __pycache__, failed every send for
  good; a case-only rename in a full send kept the old spelling, yet the tree was proven.
* Files extracted kept their commit's time, so an older commit sent again was older than the last build.
* Extraction could write outside the tree (links, ``.git`` members, a listed deletion through a linked folder).
* A proven send received again (the machine stopped before its boot recorded it) was refused.

These run the real launcher and machine tools (test_native_windows_source_send.py's fixture) on names and shapes
of their own, not the review's.
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[2] / "scripts/angelo"
for folder in (HERE, SCRIPTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
import source_tree  # noqa: E402
from test_native_windows_source_send import ENV, LauncherFixture, git, git_lfs_works  # noqa: E402


def case_insensitive(folder):
    probe = Path(folder, "Case-Probe")
    probe.write_text("x")
    try:
        return Path(folder, "case-probe").exists()
    finally:
        probe.unlink()


@unittest.skipUnless(git_lfs_works(), "the launcher is bash with git and Git LFS, run from the workstation")
class SendHardeningTests(LauncherFixture, unittest.TestCase):
    def launcher(self, command, *extra):
        env = dict(ENV, PATH="%s%s%s" % (self.fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(self.fake),
                   ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a", ANGELO_GCP_NAME="example-vm",
                   ANGELO_GCP_BUCKET="example-bucket", GIT_LFS_SKIP_SMUDGE="1")
        return subprocess.run(["bash", str(self.repo / "scripts/angelo/gcp_windows_validate.sh"), command, *extra],
                              env=env, capture_output=True, text=True, timeout=120)

    def send_and_receive(self, *extra):
        done = self.redo(None, *extra)
        self.assertEqual(done.returncode, 0, done.stderr)
        got = self.boot()
        self.assertEqual(got.returncode, 0, got.stdout)
        self.assert_tree_is(self.head())
        return got

    def verify(self):
        return self.tool(self.tools, "verify", "--tree", self.tree, "--state", self.state)

    def test_a_large_file_the_build_needs_never_passes_as_its_pointer(self):
        self.send_and_receive()
        # Renamed, then made executable: each arrives real although only its pointer changed in the commit.
        git(self.repo, "mv", "assets/large.bin", "assets/moved.bin")
        git(self.repo, "commit", "-q", "-m", "rename")
        self.send_and_receive()
        os.chmod(self.repo / "assets/moved.bin", 0o755)
        git(self.repo, "add", "assets/moved.bin")
        git(self.repo, "commit", "-q", "-m", "mode")
        self.send_and_receive()
        self.assertEqual(Path(self.tree, "assets/moved.bin").read_text(), "large v1\n")
        # The proof itself fails while one is a pointer (git status alone sees nothing).
        pointer = git(self.repo, "show", "HEAD:assets/moved.bin")
        Path(self.tree, "assets/moved.bin").write_bytes(pointer)
        # As right after a receive's read-tree (no file sizes in the index), git status hashes the file through the
        # LFS clean filter, which passes a pointer unchanged: it cannot tell.
        subprocess.run(["git", "-C", str(self.tree), "read-tree", "HEAD"], env=ENV, capture_output=True, check=True)
        self.assertEqual(self.source_clean(self.head()), [], "git status cannot tell")
        done = self.verify()
        self.assertEqual(done.returncode, 3)
        self.assertIn("1 file(s) the build needs real are Git LFS pointers: assets/moved.bin", done.stdout)
        # The launcher refuses to send a pointer from its own checkout (a clean one, checked out with smudging skipped),
        # by every path.
        Path(self.repo, "assets/moved.bin").unlink()
        subprocess.run(["git", "-C", str(self.repo), "checkout", "--", "assets/moved.bin"],
                       env=dict(ENV, GIT_LFS_SKIP_SMUDGE="1"), check=True, capture_output=True)
        self.assertEqual(Path(self.repo, "assets/moved.bin").read_bytes(), pointer)
        for command in ("redo", "sync"):
            with self.subTest(command):
                done = self.launcher(command)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Git LFS pointers in place of files the build ships", done.stderr)

    def test_a_path_whose_type_changes_arrives_by_full_and_delta_sends(self):
        self.commit({"shape/was-file": "file\n", "shape/was-folder/inner.txt": "inner\n", "pkg/mod.py": "x = 1\n",
                     ".gitignore": "__pycache__/\n"})
        self.send_and_receive()
        Path(self.tree, "pkg/__pycache__").mkdir()   # an ignored leftover keeps the folder
        Path(self.tree, "pkg/__pycache__/mod.cpython-313.pyc").write_bytes(b"pyc")
        self.commit({"shape/was-file/now-inside.txt": "x\n", "shape/was-folder": "now a file\n", "pkg": "now a file\n"},
                    removed=["shape/was-file", "shape/was-folder/inner.txt", "pkg/mod.py"])
        self.assertEqual(self.send_and_receive().returncode, 0)
        self.assertEqual(self.send()["mode"], "delta")
        # And back again, as a full send.
        self.commit({"shape/was-file": "file again\n", "shape/was-folder/inner.txt": "inner again\n"},
                    removed=["shape/was-file/now-inside.txt", "shape/was-folder"])
        self.send_and_receive("--full")
        self.assertEqual(self.send()["mode"], "full")

    def test_a_folder_holding_a_repository_is_never_removed_to_make_way(self):
        self.commit({"vendor/readme.txt": "r\n"})
        self.send_and_receive()
        git(self.tree / "vendor", "init", "-q")
        self.commit({"vendor": "now a file\n"}, removed=["vendor/readme.txt"])
        self.redo(None, "--full")
        done = self.boot()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("holds a git repository", done.stdout)
        self.assertTrue((self.tree / "vendor/.git").is_dir())

    def test_a_case_only_rename_keeps_the_commits_spelling(self):
        self.commit({"scripts/startup/Notes_Mixed.TXT": "one\n", "Folder_Case/item.txt": "two\n"})
        self.send_and_receive()
        git(self.repo, "mv", "scripts/startup/Notes_Mixed.TXT", "scripts/startup/notes_mixed.txt")
        git(self.repo, "mv", "Folder_Case", "folder-case-step")   # one step at a time where case is ignored
        git(self.repo, "mv", "folder-case-step", "folder_case")
        git(self.repo, "commit", "-q", "-m", "case-only renames")
        self.send_and_receive("--full")
        self.assertIn("notes_mixed.txt", os.listdir(self.tree / "scripts/startup"))
        self.assertIn("folder_case", os.listdir(self.tree))
        # A tree spelled otherwise is not the commit.
        os.rename(self.tree / "folder_case", self.tree / "tmp-name")
        os.rename(self.tree / "tmp-name", self.tree / "FOLDER_CASE")
        done = self.verify()
        self.assertEqual(done.returncode, 3)
        if case_insensitive(self.tree):
            self.assertIn("tracked path(s) are spelled otherwise on disk: folder_case/item.txt (on disk: FOLDER_CASE)",
                          done.stdout)

    def test_a_changed_file_is_newer_than_the_last_build_and_an_unchanged_one_keeps_its_time(self):
        self.commit({"source.cpp": "int f() { return 1; }\n"})
        older = self.head()
        self.commit({"source.cpp": "int f() { return 2; }\n"})
        self.send_and_receive()
        kept = Path(self.tree, "keep.txt").stat().st_mtime_ns
        built = time.time()
        time.sleep(0.05)
        git(self.repo, "checkout", "-q", "--detach", older)
        self.send_and_receive("--full")
        self.assertGreater(Path(self.tree, "source.cpp").stat().st_mtime, built)
        self.assertEqual(Path(self.tree, "source.cpp").read_text(), "int f() { return 1; }\n")
        self.assertEqual(Path(self.tree, "keep.txt").stat().st_mtime_ns, kept, "an unchanged file is not rewritten")

    def test_a_proven_send_received_again_is_done(self):
        self.send_and_receive()
        self.commit({"edit.txt": "again\n"})
        self.assertEqual(self.redo().returncode, 0)
        self.assertEqual(self.boot().returncode, 0)
        (self.state / "src.nonce").unlink()   # stopped after the proof, before the boot recorded the send
        done = self.boot()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("is already exactly", done.stdout)

    def test_a_stage_that_writes_into_the_tree_is_named(self):
        self.send_and_receive()
        Path(self.tree, "written-by-a-stage.log.txt").write_text("x\n")
        done = self.tool(self.tools, "leftovers", "--tree", self.tree)
        self.assertEqual(done.returncode, 0, "a report: it removes nothing")
        self.assertIn("leftovers: 1 untracked file(s)", done.stdout)
        self.assertTrue(Path(self.tree, "written-by-a-stage.log.txt").exists())


class ExtractionStaysInsideTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "victim.txt").write_text("precious\n")

    def archive(self, name, members):
        path = self.root / name
        with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            for member, kind, data in members:
                info = tarfile.TarInfo(member)
                if kind == "file":
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                else:
                    info.type, info.linkname = {"sym": tarfile.SYMTYPE, "hard": tarfile.LNKTYPE}[kind], data
                    tar.addfile(info)
        return path

    def test_no_member_reaches_outside_the_tree_or_into_its_repository(self):
        cases = {"absolute": [("/" + str(self.outside / "abs.txt").lstrip("/"), "file", b"x")],
                 "parent": [("../outside/up.txt", "file", b"x")],
                 "link out, then through it": [("way", "sym", str(self.outside)), ("way/through.txt", "file", b"x")],
                 "relative link out": [("rel", "sym", "../outside")],
                 "hard link to a file outside": [("hard", "hard", str(self.outside / "victim.txt"))],
                 # A commit never holds one (git archive writes none for this tree); NTFS would need privileges.
                 "link inside the tree": [("plain.txt", "file", b"x"), ("alias", "sym", "plain.txt")],
                 "repository member": [(".git/hooks/post-checkout", "file", b"#!/bin/sh\n")],
                 "nested repository member": [("sub/.GIT/config", "file", b"[core]\n")],
                 "stream": [("ok/name:stream", "file", b"x")]}
        for label, members in cases.items():
            with self.subTest(label):
                tree = self.root / ("tree-" + label.replace(" ", "-"))
                (tree / ".git").mkdir(parents=True)
                with self.assertRaises(source_tree.SourceTreeError):
                    source_tree.extract(tree, [str(self.archive(tree.name + ".tar.gz", members))])
                self.assertEqual(sorted(os.listdir(tree)), [".git"], "nothing was written")
        self.assertEqual(sorted(os.listdir(self.outside)), ["victim.txt"])
        self.assertEqual((self.outside / "victim.txt").read_text(), "precious\n")

    @unittest.skipIf(os.name == "nt", "needs symlinks without the privilege Windows asks for")
    def test_an_existing_link_in_the_tree_is_replaced_never_written_or_deleted_through(self):
        tree = self.root / "tree"
        tree.mkdir()
        os.symlink(self.outside, tree / "linked")
        os.link(self.outside / "victim.txt", tree / "same-inode.txt")
        listing = self.root / "deleted.txt"
        listing.write_text("linked/victim.txt\n")
        source_tree.extract(tree, [], str(listing))   # a stray linked folder on a deleted path: the link goes
        self.assertFalse(os.path.lexists(tree / "linked"))
        self.assertEqual((self.outside / "victim.txt").read_text(), "precious\n")
        os.symlink(self.outside, tree / "linked")
        source_tree.extract(tree, [str(self.archive("over.tar.gz", [("linked/new.txt", "file", b"inside\n"),
                                                                     ("same-inode.txt", "file", b"replaced\n")]))])
        self.assertFalse((tree / "linked").is_symlink())
        self.assertEqual((tree / "linked/new.txt").read_text(), "inside\n")
        self.assertEqual((tree / "same-inode.txt").read_text(), "replaced\n")
        self.assertEqual(sorted(os.listdir(self.outside)), ["victim.txt"])
        self.assertEqual((self.outside / "victim.txt").read_text(), "precious\n")


class ContractTests(unittest.TestCase):
    def text(self, name):
        return (SCRIPTS / name).read_text(encoding="utf-8")

    def test_the_machine_reports_what_the_stages_leave_and_a_failed_marker_write(self):
        boot = self.text("windows_cloud_bootstrap.ps1")
        stages = boot + self.text("windows_bootstrap_stages.ps1")   # The sent tree's stages, dot-sourced.
        for stage in ('"validate"', '"desktop session"', '"bench"'):
            self.assertIn("Report-Leftovers $src %s" % stage, stages)
        self.assertLess(boot.index('if ($stages -contains "release") { Upload-Results }'),
                        boot.index('if ($stages -contains "thumbs")'), "wait-off sees a release FAIL when it happens")
        self.assertIn('Write-Host ("source: FAILED to set {0} {1}', self.text("windows_source_send.ps1"))

    def test_the_tools_travel_with_every_send(self):
        sending = self.text("gcp_windows_source.sh")
        for name in ("source_tree.py", "source_extract.py", "source_send.py", "shipped_sources.py",
                     "windows_source_send.ps1"):
            self.assertIn('"$ROOT/scripts/angelo/%s"' % name, sending)


if __name__ == "__main__":
    unittest.main()
