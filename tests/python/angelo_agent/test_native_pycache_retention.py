# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Old builds' bytecode caches go, and nothing else ever does (``angelo_agent/pycache_retention.py``).

Dependency-free. Retention removes a sibling build folder of ``<bpy.app.cachedir>/pycache`` only when its name
is a build key the app generates, it is unused for a week, and it holds nothing but bytecode, folders and the
mark; it refuses a linked cache root (a symbolic link or a Windows junction, up to the Caches folder). Inside the
current build's folder it removes the mirrors of source folders that no longer exist (an App Translocation path,
a moved or deleted install) by the same content rule. The adversarial review's pins are
test_native_sealed_bundle_review.py.
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


def age(path, days):
    then = time.time() - days * DAY
    os.utime(path, (then, then))


class Case(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="angelo-pycache-test-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.cachedir = self.base / "Caches" / "Angelo"
        self.root = self.cachedir / "pycache"
        self.root.mkdir(parents=True)

    def build(self, name, used_days_ago=None, created_days_ago=0):
        folder = self.root / name
        (folder / "Applications/Angelo.app").mkdir(parents=True)
        (folder / "Applications/Angelo.app/module.cpython-313.pyc").write_bytes(b"pyc")
        if used_days_ago is not None:
            mark = folder / retention.LAST_USED
            mark.touch()
            age(mark, used_days_ago)
        age(folder, created_days_ago)
        return folder


class BuildFolderTests(Case):
    def test_old_builds_go_recent_and_current_stay(self):
        current = self.build("62eb10e43a0c")
        self.build("1234567890ab", used_days_ago=2)
        self.build("afaca1a036ca", used_days_ago=30)
        self.build("0f0f0f0f0f0f", created_days_ago=10)   # Never marked: its creation counts.
        self.build("dev-5.2", created_days_ago=10)         # A build without build information.
        self.build("abcdef012345", created_days_ago=1)
        (self.root / "stray.txt").write_text("not a folder")
        removed = retention.prune(self.root, current)
        self.assertEqual(removed, ["0f0f0f0f0f0f", "afaca1a036ca", "dev-5.2"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["1234567890ab", "62eb10e43a0c", "abcdef012345", "stray.txt"])
        self.assertLess(time.time() - (current / retention.LAST_USED).stat().st_mtime, 60)

    def test_a_folder_not_named_like_a_build_is_never_removed(self):
        current = self.build("62eb10e43a0c")
        mine = self.root / "My renders"
        mine.mkdir()
        (mine / "final.png").write_bytes(b"PNG")
        age(mine, 30)
        for name in ("old builds", "ABCDEF012345", "dev-5", "abc", "62eb10e43a0c.bak"):
            self.build(name, used_days_ago=30)
        self.assertEqual(retention.prune(self.root, current), [])
        self.assertTrue((mine / "final.png").exists())

    def test_a_build_folder_holding_anything_but_bytecode_is_left_alone(self):
        current = self.build("62eb10e43a0c")
        for name, extra in (("afaca1a036ca", "Applications/notes.txt"), ("1234567890ab", "scene.blend"),
                            ("abcdef012345", "Applications/Angelo.app/module.py")):
            folder = self.build(name, used_days_ago=30)
            (folder / extra).write_bytes(b"data")
        clean = self.build("0f0f0f0f0f0f", used_days_ago=30)
        (clean / "Applications/Angelo.app/__pycache__").mkdir()
        (clean / "Applications/Angelo.app/__pycache__/x.cpython-313.pyc").write_bytes(b"pyc")
        (clean / "Applications/Angelo.app/y.cpython-313.pyc.4402891184").write_bytes(b"interrupted write")
        self.assertEqual(retention.prune(self.root, current), ["0f0f0f0f0f0f"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["1234567890ab", "62eb10e43a0c", "abcdef012345", "afaca1a036ca"])

    def test_links_are_never_followed_or_removed(self):
        current = self.build("62eb10e43a0c")
        target = self.build("afaca1a036ca", used_days_ago=30)
        inside = self.build("1234567890ab", used_days_ago=30)
        outside = self.base / "Documents"
        outside.mkdir()
        (outside / "keep.txt").write_text("x")
        os.symlink(target, self.root / "0f0f0f0f0f0f")
        os.symlink(outside, inside / "Applications/link")
        self.assertEqual(retention.prune(self.root, current), ["afaca1a036ca"])
        self.assertTrue((self.root / "0f0f0f0f0f0f").is_symlink())
        self.assertTrue(inside.is_dir() and (outside / "keep.txt").exists())

    def test_a_folder_that_could_not_be_removed_is_not_reported(self):
        if os.name == "nt" or os.geteuid() == 0:
            self.skipTest("POSIX permissions as a normal user")
        current = self.build("62eb10e43a0c")
        locked = self.build("afaca1a036ca", used_days_ago=30)
        (locked / "Applications").chmod(0o500)
        self.addCleanup(lambda: (locked / "Applications").exists() and (locked / "Applications").chmod(0o700))
        self.assertEqual(retention.prune(self.root, current), [])
        self.assertTrue(locked.exists())


class LinkedRootTests(Case):
    def test_a_linked_root_or_ancestor_is_refused(self):
        documents = self.base / "Documents"
        (documents / "Old Project").mkdir(parents=True)
        (documents / "Old Project/scene.blend").write_bytes(b"BLENDER")
        age(documents / "Old Project", 30)
        for linked in ("root", "cachedir", "caches"):
            with self.subTest(linked=linked):
                shutil.rmtree(self.base / "Caches", ignore_errors=True)
                shutil.rmtree(self.base / "elsewhere", ignore_errors=True)
                if linked == "root":
                    self.cachedir.mkdir(parents=True)
                    os.symlink(documents, self.root)
                elif linked == "cachedir":
                    (self.base / "Caches").mkdir()
                    (self.base / "elsewhere/pycache").mkdir(parents=True)
                    os.symlink(self.base / "elsewhere", self.cachedir)
                else:
                    (self.base / "elsewhere/Angelo/pycache").mkdir(parents=True)
                    os.symlink(self.base / "elsewhere", self.base / "Caches")
                with self.assertRaises(retention.RetentionRefused):
                    retention.prune(self.root, self.root / "62eb10e43a0c")
                self.assertTrue((documents / "Old Project/scene.blend").exists())

    def test_a_junction_is_refused_like_a_link(self):
        """Windows: ``Path.is_symlink()`` is False for a junction; ``os.path.isjunction`` is asked too."""
        self.build("62eb10e43a0c")
        old = self.build("afaca1a036ca", used_days_ago=30)
        junction = str(self.root)
        with mock.patch.object(retention.os.path, "isjunction", lambda path: os.fspath(path) == junction,
                               create=True):
            with self.assertRaises(retention.RetentionRefused):
                retention.prune(self.root, self.root / "62eb10e43a0c")
        self.assertTrue(old.exists())

    def test_the_session_reports_a_refusal_and_removes_nothing(self):
        documents = self.base / "Documents"
        (documents / "Old").mkdir(parents=True)
        age(documents / "Old", 30)
        shutil.rmtree(self.root)
        os.symlink(documents, self.root)
        with mock.patch("builtins.print") as printed:
            retention._run(self.root, self.root / "62eb10e43a0c")
        self.assertIn("retention skipped", " ".join(str(a) for a in printed.call_args.args))
        self.assertTrue((documents / "Old").exists())


@unittest.skipIf(os.name == "nt", "Windows mirrors drop the drive letter; stale mirrors are pruned on POSIX only")
class StaleMirrorTests(Case):
    def current(self):
        """The current build's folder with only its mark: no mirror of a folder this machine may not have."""
        folder = self.root / "62eb10e43a0c"
        folder.mkdir()
        (folder / retention.LAST_USED).touch()
        return folder

    def mirror(self, current, source):
        """The mirror Python writes for ``source`` (an absolute folder) below ``current``, with one module."""
        folder = current / os.path.relpath(source, os.sep)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "module.cpython-313.pyc").write_bytes(b"pyc")
        return folder

    def test_mirrors_of_folders_that_no_longer_exist_go(self):
        current = self.current()
        live = self.base / "Applications/Angelo.app/Contents/Resources/5.2/scripts"
        live.mkdir(parents=True)
        translocated = self.base / "T/AppTranslocation"
        (translocated / "1D3E/d/Angelo.app").mkdir(parents=True)
        kept = self.mirror(current, live)
        running = self.mirror(current, translocated / "1D3E/d/Angelo.app")    # Its source exists (mounted).
        gone = self.mirror(current, translocated / "9A7C/d/Angelo.app")      # Unmounted: the folder is gone.
        user = self.mirror(current, translocated / "5B2F/d/Angelo.app")
        (user / "notes.txt").write_text("not bytecode")
        removed = retention.prune_stale_mirrors(current)
        self.assertEqual(removed, [os.path.relpath(translocated / "9A7C", os.sep)])
        self.assertFalse(gone.exists() or gone.parent.parent.exists())
        for folder in (kept, running, user):
            self.assertTrue(folder.is_dir(), folder)

    def test_the_mark_and_a_linked_mirror_are_left_alone(self):
        current = self.current()
        outside = self.base / "Documents"
        outside.mkdir()
        os.symlink(outside, current / "Volumes")   # /Volumes exists, but a link is never entered or removed.
        os.symlink(outside, current / "no-such-top-folder")
        self.assertEqual(retention.prune_stale_mirrors(current), [])
        self.assertTrue((current / retention.LAST_USED).exists())
        self.assertTrue((current / "no-such-top-folder").is_symlink() and outside.is_dir())


class OwnCacheTests(Case):
    def test_only_angelo_s_own_cache_is_managed(self):
        cachedir = str(self.cachedir) + os.sep
        own = retention.own_cache(str(self.root / "abc123"), cachedir)
        expected = (Path(os.path.normcase(str(self.root))), Path(os.path.normcase(str(self.root / "abc123"))))
        self.assertEqual(own, expected)
        for prefix in (None, "", str(self.cachedir / "elsewhere/abc"), str(self.root / "abc/deeper")):
            self.assertIsNone(retention.own_cache(prefix, cachedir), prefix)


if __name__ == "__main__":
    unittest.main()
