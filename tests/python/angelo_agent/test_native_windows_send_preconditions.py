# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a send and the runs after it need to hold, from a second break-it review of the build machine's sends.

* Every MSI an install check accepts names its commit by the hash the built program reports, so every build whose
  MSI is checked carries Blender's build info. The identity build is Lite, whose cache script forces build info
  off; it now turns it on after configuring, and refuses to package without it.
* The bench reads its commit only through the proof, inside each mode's error handling: a refusal at the top once
  left the stage waiting out its billed hours for a session that never wrote DONE.
* A tree the send did not make proves nothing (``verify`` refuses it), so no commit is returned for it.
* The launcher refuses a commit the machine could never make exact: a tracked symbolic link, or two paths that
  differ only in letter case (file or folder). A stray linked folder on a deleted path is removed itself.
* A Windows junction is a link wherever links are checked (os.path.islink does not see one).
* Only the machine tools trust their own tree past git's ownership check; the shared release checks do not.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCRIPTS = ROOT / "scripts/angelo"
for folder in (HERE, SCRIPTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
import source_extract  # noqa: E402
import source_tree  # noqa: E402
from test_native_windows_source_send import ENV, LauncherFixture, git, git_lfs_works  # noqa: E402


def text(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("cmake"), "needs CMake")
class LiteBuildInfoTests(unittest.TestCase):
    def cache(self, build):
        return dict(line.split("=", 1) for line in (build / "CMakeCache.txt").read_text().splitlines()
                    if "=" in line and not line.startswith(("#", "//")))

    def test_a_lite_configured_msi_build_gets_build_info_and_nothing_else_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            source, build = Path(folder, "source"), Path(folder, "build")
            source.mkdir()
            (source / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.10)\nproject(lite NONE)\noption(WITH_BUILDINFO "" ON)\n')
            lite = ROOT / "build_files/cmake/config/blender_lite.cmake"

            def cmake(*args):
                done = subprocess.run(["cmake", *map(str, args)], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)

            # make.bat lite nobuild: the target's cache script, which forces build info off.
            cmake("-C", lite, "-S", source, "-B", build)
            self.assertEqual(self.cache(build)["WITH_BUILDINFO:BOOL"], "OFF")
            # windows_validate.ps1 then turns it on; the build's own reconfigure (no cache script) keeps it.
            cmake(build, "-DWITH_BUILDINFO=ON")
            cmake(build)
            cache = self.cache(build)
            self.assertEqual(cache["WITH_BUILDINFO:BOOL"], "ON")
            forced = {line.split()[0][4:]: line.split()[1] for line in lite.read_text().splitlines()
                      if line.startswith("set(WITH_")}
            changed = [name for name, value in forced.items()
                       if name != "WITH_BUILDINFO" and cache.get(name + ":BOOL") != value]
            self.assertEqual(changed, [], "Lite stays Lite: only build info changes")

    def test_the_identity_build_configures_then_turns_build_info_on_and_never_packages_without_it(self):
        script = text("windows_validate.ps1")
        build = script[script.index('Test-Check "make.bat $BuildTarget $MakeArgs"'):]
        order = [build.index(step) for step in ('make.bat $BuildTarget $MakeArgs nobuild verbose',
                                                "cmake $configured.FullName -DWITH_BUILDINFO=ON",
                                                'make.bat $BuildTarget $MakeArgs verbose"')]
        self.assertEqual(order, sorted(order))
        package = script[script.index('Test-Check "cpack -G WIX'):]
        self.assertLess(package.index('"^WITH_BUILDINFO:BOOL=ON$"'), package.index("cpack -G WIX | Out-Host"))
        release = text("build_release_windows.ps1")
        self.assertIn("if ($Package -and -not $history)", release, "the release MSI refuses to configure without it")


class BenchCommitTests(unittest.TestCase):
    def test_every_mode_reads_the_commit_inside_its_own_error_handling(self):
        bench = text("windows_final_runs.ps1")
        top = bench[:bench.index("function Prepare-Bench")]
        self.assertNotIn("= Get-SentCommit", top.replace("$script:commit = Get-SentCommit", ""),
                         "no proof at the top, outside every mode")
        session = bench[bench.index("if ($Session) {"):bench.index('if (-not $Stage) { throw')]
        order = [session.index(step) for step in ("try {", "Read-SentCommit", "$facts = Read-Facts", "} catch {",
                                                  '$reason = "$_"', "} finally {",
                                                  'Set-Content (Join-Path $out "DONE-$Nonce")')]
        self.assertEqual(order, sorted(order), "a refusal still writes DONE, with its reason")
        self.assertIn('" (failed: ', session)
        setup = bench[bench.index("if ($Setup) {"):bench.index("if ($SignIn) {")]
        self.assertLess(setup.index("Read-SentCommit"), setup.index("Prepare-Bench"))
        stage = bench[bench.index('if (-not $Stage) { throw'):]
        self.assertLess(stage.index("Read-SentCommit"), stage.index("Prepare-Bench"))


@unittest.skipUnless(shutil.which("git"), "needs git")
class SendableTests(unittest.TestCase):
    def repo(self, folder, entries):
        """A repository whose one commit holds ``entries`` (path, mode, bytes), made with plumbing: no file system
        rule (case, links) shapes it."""
        git(folder, "init", "-q")
        for path, mode, data in entries:
            blob = git(folder, "hash-object", "-w", "--stdin", input=data).decode().strip()
            git(folder, "update-index", "--add", "--cacheinfo", "%s,%s,%s" % (mode, blob, path))
        git(folder, "commit", "-q", "-m", "shape")

    def test_a_commit_the_machine_could_never_make_exact_is_refused(self):
        cases = {"tracked symbolic link": ([("real.txt", "100644", b"x"), ("alias.txt", "120000", b"real.txt")],
                                           "1 tracked symbolic link(s): alias.txt"),
                 "file names": ([("dup/Ünïcode.txt", "100644", b"a"), ("dup/ünïcode.txt", "100644", b"b")],
                                "dup/Ünïcode.txt / dup/ünïcode.txt"),
                 "folder names": ([("Docs/a.txt", "100644", b"a"), ("docs/b.txt", "100644", b"b")], "Docs / docs")}
        for label, (entries, expected) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as folder:
                self.repo(folder, entries)
                done = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "source_tree.py"), "sendable", "--tree",
                                       folder], env=ENV, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(done.returncode, 1)
                self.assertIn("cannot be made exact on the build machine", done.stderr)
                self.assertIn(expected.encode("ascii", "backslashreplace").decode(), done.stderr)
        with tempfile.TemporaryDirectory() as folder:
            self.repo(folder, [("Ünïcode/a.txt", "100644", b"a"), ("other/ünïcode.txt", "100644", b"b")])
            self.assertEqual(source_tree.unsendable(folder), [], "the same name in different folders is fine")

    def test_this_repository_is_sendable(self):
        listed = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True)
        if listed.returncode == 0:   # A source archive without Git has nothing to check; CI checks out the repository.
            self.assertEqual(source_tree.unsendable(ROOT), [])


@unittest.skipUnless(git_lfs_works(), "the launcher is bash with git and Git LFS, run from the workstation")
class LauncherRefusalTests(LauncherFixture, unittest.TestCase):
    def test_the_launcher_refuses_to_send_one(self):
        self.assertEqual(self.redo().returncode, 0)
        os.symlink("keep.txt", self.repo / "alias.txt")
        self.commit({})
        done = self.redo()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("1 tracked symbolic link(s): alias.txt", done.stderr)


class JunctionTests(unittest.TestCase):
    """A junction is reported by os.path.isjunction only; here a folder plays one (no junctions off Windows)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tree = self.root / "tree"
        (self.tree / "junction").mkdir(parents=True)
        played = str(self.tree / "junction")
        real = os.path.isjunction
        patcher = mock.patch("os.path.isjunction", side_effect=lambda path: os.fspath(path) == played or real(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_junction_is_a_link_wherever_links_are_checked(self):
        with self.assertRaises(source_extract.SourceTreeError):
            source_extract.inside(self.tree, "junction/file.txt")
        listing = self.root / "deleted.txt"
        listing.write_text("junction/file.txt\n")
        source_extract.remove_listed(self.tree, listing)
        self.assertFalse(os.path.lexists(self.tree / "junction"), "the junction itself goes, nothing through it")
        (self.tree / "junction").mkdir()
        cleared = set()
        member = mock.Mock(isfile=lambda: True, isdir=lambda: False)
        source_extract._make_way(self.tree, self.tree / "junction/file.txt", member, cleared)
        self.assertFalse(os.path.lexists(self.tree / "junction"))


class TrustTests(unittest.TestCase):
    def test_only_the_machine_tools_trust_their_own_tree(self):
        self.assertNotIn("safe.directory", text("shipped_sources.py"), "release_checks.py keeps git's ownership check")
        # Only this process's settings: a runner's own global safe.directory (GitHub's checkout adds '*') is not
        # what trust() wrote.
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.example",
                                          "GIT_CONFIG_VALUE_0": "kept", "GIT_CONFIG_GLOBAL": os.devnull,
                                          "GIT_CONFIG_NOSYSTEM": "1"}):
            with tempfile.TemporaryDirectory() as folder:
                source_tree.trust(folder)
                self.assertEqual(os.environ["GIT_CONFIG_COUNT"], "2")
                self.assertEqual(os.environ["GIT_CONFIG_VALUE_0"], "kept")
                listed = subprocess.run(["git", "config", "--get-all", "safe.directory"], capture_output=True, text=True)
                self.assertEqual(listed.stdout.split(), [Path(folder).resolve().as_posix()])
        for name in ("source_tree.py", "source_send.py"):
            self.assertIn("trust(arguments.", text(name))


if __name__ == "__main__":
    unittest.main()
