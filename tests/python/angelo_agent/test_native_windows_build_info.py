# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows release build names its exact commit: Blender's build info from a one-commit checkout.

About, crash reports and support identify a build by the hash Blender's ``buildinfo.cmake`` reads from git. The
Windows validation machine receives the source as an archive (nothing is pushed), so ``release-build`` also sends
the commit object and its tree, and the bootstrap installs them as a shallow commit under the extracted files.
These tests run that hand-off with real git repositories (the same pipeline the launcher runs), then check the
git calls buildinfo makes, and read the scripts that refuse a release MSI without build info.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/angelo"
LAUNCHER = (SCRIPTS / "gcp_windows_validate.sh").read_text(encoding="utf-8")
SENDING = (SCRIPTS / "gcp_windows_source.sh").read_text(encoding="utf-8")   # sourced by the launcher
SOURCE_TREE = SCRIPTS / "source_tree.py"
BOOTSTRAP = (SCRIPTS / "windows_cloud_bootstrap.ps1").read_text(encoding="utf-8")
BUILD = (SCRIPTS / "build_release_windows.ps1").read_text(encoding="utf-8")
# Never a large-file download, and a fixed identity for the commits made here.
ENV = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_AUTHOR_NAME="Angelo Tests", GIT_AUTHOR_EMAIL="tests@angelo.invalid",
           GIT_COMMITTER_NAME="Angelo Tests", GIT_COMMITTER_EMAIL="tests@angelo.invalid", GIT_CONFIG_NOSYSTEM="1")


def git(folder, *args, **kwargs):
    return subprocess.run(["git", "-C", str(folder), *args], env=ENV, check=True, capture_output=True, **kwargs).stdout


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class CommitHandOffTests(unittest.TestCase):
    def test_a_one_commit_pack_gives_buildinfo_the_true_hash_branch_and_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, machine = Path(temporary, "source"), Path(temporary, "machine")
            source.mkdir()
            git(source, "init", "-q", "-b", "release/1.0")
            (source / "first.txt").write_text("before\n")
            git(source, "add", "-A")
            git(source, "commit", "-q", "-m", "first")
            (source / "dir with space").mkdir()
            (source / "dir with space" / "file.txt").write_text("inside\n")
            (source / "first.txt").write_text("after\n")
            git(source, "add", "-A")
            git(source, "commit", "-q", "-m", "second")
            # The launcher's own function (gcp_windows_source.sh pack_commit, all of the tree: no confirmed base).
            subprocess.run(["bash", "-c", 'ROOT=unused; source "$1"; cd "$2" && pack_commit "$3" "" "$(git rev-parse HEAD)"', "pack",
                            str(SCRIPTS / "gcp_windows_source.sh"), str(source), temporary], env=ENV, check=True)
            # The machine: the archive's files in an empty repository, then the bootstrap's steps.
            machine.mkdir()
            git(machine, "init", "-q")
            archive = subprocess.run(["git", "-C", str(source), "archive", "HEAD"], env=ENV, check=True,
                                     capture_output=True).stdout
            subprocess.run(["tar", "-x", "-C", str(machine)], input=archive, check=True)
            sha = git(source, "rev-parse", "HEAD").decode().strip()
            # The bootstrap's step 3a: source_tree.py commit.
            subprocess.run([sys.executable, "-I", "-B", str(SOURCE_TREE), "commit", "--tree", str(machine), "--commit", sha,
                            "--pack", str(Path(temporary, "angelo-commit.pack")), "--branch", "release/1.0"],
                           env=ENV, check=True, capture_output=True)
            # What buildinfo.cmake asks git.
            self.assertEqual(git(machine, "rev-parse", "--abbrev-ref", "HEAD").decode().strip(), "release/1.0")
            self.assertEqual(git(machine, "rev-parse", "--short=12", "HEAD"), git(source, "rev-parse", "--short=12", "HEAD"))
            self.assertEqual(git(machine, "log", "-1", "--format=%ct"), git(source, "log", "-1", "--format=%ct"))
            git(machine, "update-index", "-q", "--refresh")
            self.assertEqual(git(machine, "diff-index", "--name-only", "HEAD", "--"), b"", "not '(modified)'")
            # One commit only: the history stays on the workstation.
            self.assertEqual(git(machine, "rev-list", "--count", "HEAD").decode().strip(), "1")


class ScriptTests(unittest.TestCase):
    def test_the_launcher_never_smudges_large_files_and_sends_the_commit_for_release(self):
        self.assertLess(LAUNCHER.index("export GIT_LFS_SKIP_SMUDGE=1"),
                        LAUNCHER.index('source "$ROOT/scripts/angelo/gcp_windows_source.sh"'))
        self.assertIn("git archive --format", SENDING)
        # Every send carries the commit itself (the machine proves its tree against it before any stage runs).
        send = SENDING[SENDING.index("send_source() {"):SENDING.index("\n}\n", SENDING.index("send_source() {"))]
        self.assertIn('pack_commit "$work" "$base"', send)
        self.assertIn('"$work/angelo-commit.pack"', send[send.index("g storage cp --quiet \"${files[@]}\""):])
        for command in ("up() {", "redo() {"):
            body = LAUNCHER[LAUNCHER.index(command):LAUNCHER.index("\n}\n", LAUNCHER.index(command))]
            self.assertIn('send_source "$work" "$nonce"', body)

    def test_the_bootstrap_installs_exactly_that_commit(self):
        stage = BOOTSTRAP[BOOTSTRAP.index("--- 3. Source"):BOOTSTRAP.index("3b. The full-feature build")]
        self.assertIn('Receive-Send $folder $src $gcloud $bucket $sentCommit', stage)
        receive = (SCRIPTS / "source_send.py").read_text(encoding="utf-8")
        self.assertIn('source_tree.install_commit(tree, commit, folder / "angelo-commit.pack"', receive)
        tool = SOURCE_TREE.read_text(encoding="utf-8")
        install = tool[tool.index("def install_commit"):tool.index("def refusal")]
        for step in ('"index-pack", "--stdin"', 'joinpath("shallow")', '"symbolic-ref", "HEAD"', '"read-tree", commit',
                     "the checkout's HEAD is"):
            self.assertIn(step, install)

    def test_a_release_msi_needs_build_info_and_its_hash_is_the_commits(self):
        refuse = BUILD.index("if ($Package -and -not $history)")
        self.assertLess(refuse, BUILD.index("$code = Invoke-Logged $configureLog"), "refused before configuring")
        self.assertIn("A release MSI needs Blender's build info", BUILD[refuse:refuse + 300])
        check = BUILD[BUILD.index("$manifest.binary = $exe"):BUILD.index("# --- Package")]
        self.assertTrue(re.search(r"build hash:.*\$history\.StartsWith\(\$reported\)", check, re.S))


if __name__ == "__main__":
    unittest.main()
