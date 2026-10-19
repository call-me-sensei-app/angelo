# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bytecode cache retention under attack: races, build-key edges, interrupted writes, mirror cleanup.

Pins of verified behaviour.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import pycache_retention as retention  # noqa: E402

DAY = 86400
POSIX = os.name != "nt"


def age(path, days):
    then = time.time() - days * DAY
    os.utime(path, (then, then))


class Fixture(unittest.TestCase):
    def setUp(self):
        # realpath: macOS's temporary folder is reached through /var -> /private/var.
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="angelo-retention-attack-")))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "Caches" / "Angelo" / "pycache"
        self.root.mkdir(parents=True)
        self.current = self.root / "6d9d717a6c8a"
        self.documents = self.base / "Documents"
        self.documents.mkdir()

    def old_build(self, name, files=("Applications/m.cpython-313.pyc",), days=30):
        folder = self.root / name
        for rel in files:
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes(b"pyc")
        mark = folder / retention.LAST_USED
        mark.touch()
        age(mark, days)
        return folder


class RaceTests(Fixture):
    def test_a_folder_swapped_for_a_link_after_its_check_is_not_followed(self):
        """TOCTOU: the checked folder becomes a link to user files before removal; the link is refused, nothing in
        its target goes, and the name is not reported."""
        self.old_build("afaca1a036ca")
        user = self.documents / "Project"
        user.mkdir()
        (user / "scene.blend").write_bytes(b"BLENDER")
        real_check = retention.is_bytecode_tree

        def check_then_swap(folder, allow_mark=False):
            result = real_check(folder, allow_mark)
            if Path(folder).name == "afaca1a036ca":
                shutil.rmtree(folder)
                os.symlink(user, folder)
            return result

        with mock.patch.object(retention, "is_bytecode_tree", check_then_swap):
            removed = retention.prune(self.root, self.current)
        self.assertEqual(removed, [])
        self.assertTrue((user / "scene.blend").exists())

    def test_a_root_swapped_for_a_link_after_its_check_removes_no_user_file(self):
        """TOCTOU on the root itself (only another process of the same user could do it): the content rule still
        protects anything that is not bytecode, even in a folder named like a build."""
        lookalike = self.documents / "deadbeef1"
        lookalike.mkdir()
        (lookalike / "notes.txt").write_text("keep")
        age(lookalike, 30)
        bytecode_only = self.documents / "cafebabe2"
        (bytecode_only / "x").mkdir(parents=True)
        (bytecode_only / "x" / "m.cpython-313.pyc").write_bytes(b"pyc")
        age(bytecode_only, 30)
        real_check_root = retention.check_root

        def check_then_swap(root):
            real_check_root(root)
            shutil.rmtree(root)
            os.symlink(self.documents, root)

        current = self.documents / "6d9d717a6c8a"  # Where the swapped root now leads.
        with mock.patch.object(retention, "check_root", check_then_swap):
            removed = retention.prune(self.root, self.root / current.name)
        self.assertTrue((lookalike / "notes.txt").exists())
        # A hex-named, bytecode-only, week-old folder is the one thing that can go through a raced root.
        self.assertEqual(removed, ["cafebabe2"])

    def test_two_sessions_pruning_at_once_never_fail(self):
        for name in ("0000000", "1111111", "2222222"):
            self.old_build(name)
        first = retention.prune(self.root, self.current)
        second = retention.prune(self.root, self.current)  # The other session finds them gone.
        self.assertEqual((first, second), (["0000000", "1111111", "2222222"], []))


class BuildKeyTests(Fixture):
    def test_build_key_edges(self):
        removable = ["abcdef1", "0123456789abcdef0123456789abcdef01234567", "dev-5.2", "dev-10.12", "20261016"]
        kept = ["abcdef", "ABCDEF9", "abcdef1g", "a" * 41, "dev-5", "dev-5.2.1", "dev-5.2-x", ".abcdef1",
                "abcdef1 ", "١٢٣٤٥٦٧", "dev-٥.٢", "abcdef1.bak"]
        for name in removable + kept:
            self.old_build(name)
        removed = retention.prune(self.root, self.current)
        self.assertEqual(sorted(removed), sorted(removable))
        for name in kept:
            self.assertTrue((self.root / name).is_dir(), name)

    def test_a_newline_after_the_key_does_not_match(self):
        if not POSIX:
            self.skipTest("a newline in a file name")
        self.old_build("abcdef1\n")
        self.assertEqual(retention.prune(self.root, self.current), [])


class ContentRuleTests(Fixture):
    def test_interrupted_writes_count_as_bytecode(self):
        self.old_build("abcdef1", files=["Applications/m.cpython-313.pyc.4398046511104",
                                         "Applications/n.cpython-313.opt-1.pyc"])
        self.assertEqual(retention.prune(self.root, self.current), ["abcdef1"])

    def test_anything_else_keeps_the_folder(self):
        cases = {"abcdef1": "Applications/m.cpython-313.pyc.tmp", "abcdef2": "Applications/.pyc",
                 "abcdef3": "Applications/m.py", "abcdef4": "Applications/.DS_Store",
                 "abcdef5": "Applications/" + retention.LAST_USED, "abcdef6": "Applications/m.pyc.-1"}
        for name, rel in cases.items():
            self.old_build(name, files=["Applications/ok.cpython-313.pyc", rel])
        self.assertEqual(retention.prune(self.root, self.current), [])

    def test_an_unreadable_subfolder_is_kept_with_what_it_holds(self):
        if not POSIX or os.geteuid() == 0:
            self.skipTest("POSIX permissions as a normal user")
        folder = self.old_build("abcdef1")
        hidden = folder / "Applications" / "locked"
        hidden.mkdir()
        (hidden / "user-file.txt").write_text("unseen by the content check")
        hidden.chmod(0o300)  # Searchable, not listable: os.walk cannot see inside.
        self.addCleanup(lambda: hidden.exists() and hidden.chmod(0o700))
        removed = retention.prune(self.root, self.current)
        hidden.chmod(0o700)
        self.assertEqual(removed, [])
        self.assertTrue((hidden / "user-file.txt").exists())


@unittest.skipUnless(POSIX, "mirror cleanup is POSIX only")
class MirrorTests(Fixture):
    def mirror(self, source, name="m"):
        folder = self.current / str(source).lstrip(os.sep)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("%s.cpython-313.pyc" % name)).write_bytes(b"pyc")
        return folder

    def test_existing_sources_keep_their_mirrors_and_only_missing_ones_go(self):
        live = self.base / "Applications" / "Angelo.app" / "scripts"
        live.mkdir(parents=True)
        gone = self.base / "AppTranslocation" / "1F2E" / "d" / "Angelo.app" / "scripts"
        kept, dropped = self.mirror(live), self.mirror(gone)
        self.mirror(self.base / "Applications" / "Angelo.app", "app")  # Bytecode beside a live child mirror.
        self.current.joinpath(retention.LAST_USED).touch()
        removed = retention.prune_stale_mirrors(self.current)
        self.assertTrue(kept.is_dir())
        self.assertFalse(dropped.exists())
        self.assertEqual(removed, [os.path.relpath(self.current / str(self.base).lstrip(os.sep) / "AppTranslocation",
                                                   self.current)])
        self.assertTrue((self.current / retention.LAST_USED).exists())

    def test_a_mirror_holding_anything_else_or_a_link_is_kept(self):
        gone = self.base / "gone"
        folder = self.mirror(gone)
        (folder / "notes.txt").write_text("keep")
        linked = self.base / "gone2"
        mirror2 = self.current / str(linked).lstrip(os.sep)
        mirror2.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(self.documents, mirror2)
        (self.documents / "scene.blend").write_bytes(b"BLENDER")
        self.assertEqual(retention.prune_stale_mirrors(self.current), [])
        self.assertTrue((folder / "notes.txt").exists())
        self.assertTrue((self.documents / "scene.blend").exists())

    def test_a_mirror_swapped_for_a_link_after_its_check_is_not_followed(self):
        gone = self.base / "gone"
        folder = self.mirror(gone)
        (self.documents / "scene.blend").write_bytes(b"BLENDER")
        real_check = retention.is_bytecode_tree

        def check_then_swap(path, allow_mark=False):
            result = real_check(path, allow_mark)
            if Path(path) == folder.parents[0] or Path(path) == folder:
                shutil.rmtree(path)
                os.symlink(self.documents, path)
            return result

        with mock.patch.object(retention, "is_bytecode_tree", check_then_swap):
            retention.prune_stale_mirrors(self.current)
        self.assertTrue((self.documents / "scene.blend").exists())

    def test_a_source_this_process_cannot_see_loses_its_mirror(self):
        """Known cost, not a hazard: a source folder that exists but cannot be looked up (an unsearchable parent,
        an unmounted volume) is treated as gone, so its bytecode is compiled again when it is next imported."""
        if os.geteuid() == 0:
            self.skipTest("root ignores permissions")
        parent = self.base / "private-parent"
        source = parent / "addons"
        source.mkdir(parents=True)
        folder = self.mirror(source)
        parent.chmod(0o000)
        self.addCleanup(parent.chmod, 0o700)
        removed = retention.prune_stale_mirrors(self.current)
        parent.chmod(0o700)
        self.assertFalse(folder.exists())
        self.assertTrue(source.is_dir())
        self.assertEqual(len(removed), 1)

    def test_nothing_outside_the_current_build_folder_is_touched(self):
        outside = self.root / "abcdef1" / str(self.base / "gone").lstrip(os.sep)
        outside.mkdir(parents=True)
        (outside / "m.cpython-313.pyc").write_bytes(b"pyc")
        self.mirror(self.base / "gone")
        retention.prune_stale_mirrors(self.current)
        self.assertTrue((outside / "m.cpython-313.pyc").exists())


if __name__ == "__main__":
    unittest.main()
