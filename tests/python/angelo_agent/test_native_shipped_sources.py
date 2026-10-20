# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A release ships only what its commit tracks (scripts/angelo/shipped_sources.py, release_checks.py).

The class: a folder install ships every file in the source tree, so any file git does not track (left by an earlier
extraction, a scratch file, a deleted module's leftover) would ship. These checks use a temporary repository, never a
particular file name.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import shipped_sources  # noqa: E402

RELEASE_CHECKS = ROOT / "scripts/angelo/release_checks.py"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin:/usr/local/bin",
                        "HOME": str(repo), "GIT_CONFIG_NOSYSTEM": "1"})


class Repo(unittest.TestCase):
    TRACKED = {
        "scripts/addons_core/angelo_agent/__init__.py": b"x = 1\n",
        "scripts/presets/theme.xml": b"<theme/>\n",
        "intern/cycles/blender/addon/engine.py": b"engine\n",
        "intern/cycles/kernel/osl/shaders/node_mix.osl": b"shader\n",
        "intern/cycles/kernel/osl/shaders/node_util.h": b"h\n",
        "intern/cycles/kernel/osl/services_optix.cu": b"cu\n",
        "intern/cycles/kernel/types.h": b"types\n",
        "intern/cycles/doc/license/MIT-license.txt": b"mit\n",
        ".gitignore": b"ignored.tmp\n",
    }

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.repo = self.root / "src"
        for name, data in self.TRACKED.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "c")
        self.head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                                   text=True).stdout.strip()
        self.lib = self.root / "lib"
        (self.lib / "osl/share/OSL/shaders").mkdir(parents=True)
        (self.lib / "osl/share/OSL/shaders/stdosl.h").write_bytes(b"std\n")

    def bundle(self, files):
        scripts = self.root / "bundle/scripts"
        for name, data in files.items():
            path = scripts / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return scripts


class SourceCleanTests(Repo):
    def test_a_tree_at_its_commit_with_nothing_untracked_is_clean(self):
        self.assertEqual(shipped_sources.source_clean(self.repo, self.head), [])

    def test_any_untracked_file_anywhere_is_refused(self):
        for name in ("scripts/addons_core/angelo_agent/leftover.py", "tests/files/new.blend", "notes.txt"):
            with self.subTest(name=name):
                path = self.repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
                problems = shipped_sources.source_clean(self.repo, self.head)
                self.assertTrue(problems and name in problems[0], problems)
                path.unlink()

    def test_a_modified_or_deleted_tracked_file_is_refused(self):
        (self.repo / "scripts/presets/theme.xml").write_text("changed")
        self.assertIn("differ from the commit", " ".join(shipped_sources.source_clean(self.repo, self.head)))
        git(self.repo, "checkout", "--", "scripts/presets/theme.xml")
        (self.repo / "intern/cycles/kernel/types.h").unlink()
        self.assertIn("intern/cycles/kernel/types.h", " ".join(shipped_sources.source_clean(self.repo, self.head)))

    def test_an_ignored_file_is_not_a_source_file(self):
        (self.repo / "ignored.tmp").write_text("x")
        self.assertEqual(shipped_sources.source_clean(self.repo, self.head), [])

    def test_a_tree_at_another_commit_is_refused(self):
        problems = shipped_sources.source_clean(self.repo, "0" * 40)
        self.assertTrue(any("not the commit sent" in problem for problem in problems))

    def test_a_folder_without_git_is_refused(self):
        self.assertIn("not a git work tree", shipped_sources.source_clean(self.root / "lib")[0])


class TrackedScriptsTests(Repo):
    def shipped(self, files, libdir=True):
        return shipped_sources.untracked_shipped(self.bundle(files), self.repo, libdir=self.lib if libdir else None)

    def test_tracked_and_built_files_pass_and_bytecode_is_ignored(self):
        files = {"addons_core/angelo_agent/__init__.py": b"x", "addons_core/angelo_agent/__pycache__/a.pyc": b"",
                 "presets/theme.xml": b"t", "addons_core/cycles/engine.py": b"e",
                 "addons_core/cycles/shader/node_mix.oso": b"compiled", "addons_core/cycles/shader/node_util.h": b"h",
                 "addons_core/cycles/shader/stdosl.h": b"std\n", "addons_core/cycles/source/kernel/types.h": b"t",
                 "addons_core/cycles/source/kernel/device/optix/services_optix.cu": b"cu",
                 "addons_core/cycles/license/MIT-license.txt": b"m",
                 "addons_core/io_scene_gltf2/bf_intern_draco_bridge.dll": b"dll"}
        self.assertEqual(self.shipped(files), [])

    def test_any_file_the_commit_lacks_is_listed(self):
        files = {"addons_core/angelo_agent/__init__.py": b"x", "addons_core/angelo_agent/leftover.py": b"y",
                 "presets/old_theme.xml": b"z", "addons_core/other/thing.py": b"w"}
        self.assertEqual(self.shipped(files), ["addons_core/angelo_agent/leftover.py", "addons_core/other/thing.py",
                                               "presets/old_theme.xml"])

    def test_built_output_needs_its_tracked_source(self):
        files = {"addons_core/cycles/shader/node_gone.oso": b"compiled", "addons_core/cycles/stale.py": b"s",
                 "addons_core/cycles/source/kernel/gone.h": b"g",
                 "addons_core/io_scene_gltf2/other.dll": b"d"}
        self.assertEqual(self.shipped(files), sorted(files))

    def test_a_library_file_must_be_byte_identical_to_the_pinned_one(self):
        self.assertEqual(self.shipped({"addons_core/cycles/shader/stdosl.h": b"changed\n"}),
                         ["addons_core/cycles/shader/stdosl.h"])
        self.assertEqual(self.shipped({"addons_core/cycles/shader/stdosl.h": b"std\n"}, libdir=False),
                         ["addons_core/cycles/shader/stdosl.h"])

    def test_the_command_line_fails_on_a_leftover_and_passes_without(self):
        scripts = self.bundle({"addons_core/angelo_agent/__init__.py": b"x"})
        run = lambda: subprocess.run([sys.executable, "-I", "-B", str(RELEASE_CHECKS), "tracked-scripts",
                                      str(scripts), "--source", str(self.repo)], capture_output=True, text=True)
        self.assertEqual(run().returncode, 0)
        (scripts / "addons_core/angelo_agent/leftover.py").write_text("y")
        failed = run()
        self.assertEqual(failed.returncode, 1)
        self.assertIn("leftover.py", failed.stderr)


if __name__ == "__main__":
    unittest.main()
