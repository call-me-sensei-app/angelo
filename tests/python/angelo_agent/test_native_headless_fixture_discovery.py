# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The real headless fixture must witness one owned directory, then that same directory disappearing."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import private_roots, storage
from test_blender_headless_mcp import private_directories, one_private_directory, private_state_removed


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.home, self.tmp = self.root / "child-home", self.root / "child-tmp"
        self.home.mkdir()
        self.tmp.mkdir()
        self.environ = {"HOME": str(self.home), "TMPDIR": str(self.tmp)}
        self.scratch = storage.data_directory(self.environ, home=self.home) / private_roots.SCRATCH

    def directory(self, root, suffix="owned"):
        path = root / (private_roots.HEADLESS_PREFIX + suffix)
        path.mkdir(parents=True)
        return path

    def discover(self):
        return private_directories(self.home, self.tmp, self.environ)

    def test_app_scratch_is_observed_then_exact_path_removed(self):
        path = self.directory(self.scratch)
        # Old fixture discovery was empty despite a real live child directory.
        self.assertEqual(list(self.tmp.glob(private_roots.HEADLESS_PREFIX + "*")), [])
        paths = self.discover()
        self.assertEqual(paths, [path])
        self.assertTrue(one_private_directory(paths))
        self.assertFalse(private_state_removed(paths))
        path.rmdir()
        self.assertTrue(private_state_removed(paths))

    def test_temporary_folder_fallback_is_observed(self):
        path = self.directory(self.tmp)
        self.assertEqual(self.discover(), [path])
        self.assertTrue(one_private_directory(self.discover()))

    def test_missing_directory_cannot_pass_cleanup(self):
        self.assertEqual(self.discover(), [])
        self.assertFalse(one_private_directory([]))
        self.assertFalse(private_state_removed([]))

    def test_two_directories_are_ambiguous_even_across_roots(self):
        self.directory(self.tmp)
        self.directory(self.scratch)
        paths = self.discover()
        self.assertEqual(len(paths), 2)
        self.assertFalse(one_private_directory(paths))
        for path in paths:
            path.rmdir()
        self.assertFalse(private_state_removed(paths))

    def test_parent_home_is_never_used(self):
        path = self.directory(self.scratch)
        with patch.object(Path, "home", side_effect=AssertionError("parent HOME was consulted")):
            self.assertEqual(self.discover(), [path])

    def test_symlink_is_not_owned_and_dangling_link_is_not_removed(self):
        target = self.root / "target"
        target.mkdir()
        link = self.tmp / (private_roots.HEADLESS_PREFIX + "link")
        link.symlink_to(target, target_is_directory=True)
        paths = self.discover()
        self.assertFalse(one_private_directory(paths))
        target.rmdir()
        self.assertFalse(private_state_removed(paths))

    def test_prefixed_file_cannot_count_as_child_state(self):
        path = self.tmp / (private_roots.HEADLESS_PREFIX + "file")
        path.touch()
        self.assertFalse(one_private_directory(self.discover()))


if __name__ == "__main__":
    unittest.main()
