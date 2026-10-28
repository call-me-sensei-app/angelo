# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stage bundles share one private folder per process (``stage_bundle_folder``), with the folder's guarantees.

Release gate 7ea90a2 (Mac 1): ``stage_bundle/dense_geometry/suspend_uncompressed`` at 1.10x its ceiling, because each
suspend allocated, registered and removed a private folder of its own (about 1.8 million instructions). These pin
one allocation for any number of bundles, fresh names, removal of exactly one bundle, privacy of the folder wherever
it goes, and a new folder when the old one is gone or moved.
"""

import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import private_roots, stage_bundle_folder as shared  # noqa: E402
from angelo_agent.program_storage import ProgramStorageMixin  # noqa: E402


class StageBundleFolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-stage-bundle-folder-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        (self.base / "tmp").mkdir()
        for patcher in (patch.dict(os.environ, {"HOME": str(self.base), "XDG_STATE_HOME": str(self.base / "state")}),
                        patch.object(tempfile, "tempdir", str(self.base / "tmp")),
                        patch.object(shared, "_folder", None), patch.object(shared, "_used", set())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, bundle, text="BLENDER synthetic stage"):
        Path(bundle).write_text(text)
        return Path(bundle)

    def test_one_allocation_serves_every_bundle_with_fresh_names(self):
        with patch.object(private_roots, "allocate", wraps=private_roots.allocate) as allocate:
            bundles = [self.write(shared.new_bundle()) for _ in range(20)]
        self.assertEqual(allocate.call_count, 1, "a suspend never pays for a folder of its own")
        self.assertEqual(len({bundle.name for bundle in bundles}), 20)
        folder = bundles[0].parent
        self.assertTrue(all(bundle.parent == folder for bundle in bundles))
        self.assertTrue(folder.name.startswith(shared.PREFIX) and folder.parent == private_roots.scratch_root())
        self.assertTrue(os.name == "nt" or stat.S_IMODE(folder.stat().st_mode) == 0o700)
        identity = (folder.stat().st_dev, folder.stat().st_ino)
        self.assertIn(identity, private_roots.identities())
        self.assertTrue((folder / private_roots.OWNER).is_file(), "the folder carries its owner token")

    def test_a_name_that_exists_is_never_given(self):
        taken = self.write(shared.new_bundle())
        with patch.object(shared.uuid, "uuid4", side_effect=[type("U", (), {"hex": taken.stem})(),
                                                             type("U", (), {"hex": "f" * 32})()]):
            fresh = shared.new_bundle()
        self.assertEqual(fresh.name, "f" * 32 + ".blend")
        self.assertEqual(taken.read_text(), "BLENDER synthetic stage")

    def test_removal_takes_one_bundle_and_its_partial_write_only(self):
        first, second = self.write(shared.new_bundle()), self.write(shared.new_bundle())
        partial = self.write(str(first) + "@")
        ProgramStorageMixin._remove_bundle(object(), str(first))
        self.assertFalse(first.exists() or partial.exists())
        self.assertTrue(second.exists() and (first.parent / private_roots.OWNER).exists())

    def test_nothing_else_enters_the_removal(self):
        bundle = self.write(shared.new_bundle())
        outside = self.base / "project" / bundle.name
        outside.parent.mkdir()
        self.write(outside)
        for path in (bundle.parent / private_roots.OWNER, bundle.parent / "other.blend", outside,
                     bundle.parent / "sub" / bundle.name):
            with self.subTest(path=path.name):
                self.assertFalse(shared.remove(path))
        self.assertTrue(outside.exists() and (bundle.parent / private_roots.OWNER).exists())

    def test_a_removed_folder_is_replaced(self):
        folder = self.write(shared.new_bundle()).parent
        shutil.rmtree(folder)
        fresh = self.write(shared.new_bundle())
        self.assertNotEqual(fresh.parent, folder)
        self.assertTrue((fresh.parent / private_roots.OWNER).is_file())

    def test_a_moved_folder_is_replaced_and_stays_private(self):
        bundle = self.write(shared.new_bundle())
        moved = self.base / "elsewhere"
        os.rename(bundle.parent, moved)
        fresh = self.write(shared.new_bundle())
        self.assertNotEqual(fresh.parent, moved)
        identity = (moved.stat().st_dev, moved.stat().st_ino)
        self.assertIn(identity, private_roots.identities(), "the moved folder keeps its identity's privacy")
        self.assertTrue((moved / private_roots.OWNER).is_file())

    def test_exit_removes_each_shared_folder(self):
        folder = self.write(shared.new_bundle()).parent
        shared._remove_folders()
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
