# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every folder Angelo allocates stays removable by its owner's own cleanup (discard, cancel, sweep), wherever Angelo
makes it (``private_cleanup.owns``).

Native programs, round 11 (test_blender_native_programs, partial_preview_keeps_discardable_private_bundle): the
discarded preview's private bundle stayed on disk, because round 10 moved Angelo's folders to the scratch root and
the discard still removed a bundle folder only when it sat directly in the system temporary folder.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import private_roots  # noqa: E402
from angelo_agent.program_storage import ProgramStorageMixin  # noqa: E402

PREFIX = "angelo-native-preview-"


class PreviewBundleCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-private-cleanup-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        (self.base / "tmp").mkdir()
        for patcher in (patch.dict(os.environ, {"HOME": str(self.base), "XDG_STATE_HOME": str(self.base / "state")}),
                        patch.object(tempfile, "tempdir", str(self.base / "tmp"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def bundle_in(self, folder):
        bundle = Path(folder) / "stage.blend"
        bundle.write_bytes(b"BLENDER synthetic stage")
        return bundle

    def test_a_discarded_preview_removes_its_bundle_wherever_angelo_made_it(self):
        folder = private_roots.allocate(PREFIX)   # As program_stage_bundle makes it.
        bundle = self.bundle_in(folder)
        ProgramStorageMixin._remove_bundle(object(), str(bundle))
        self.assertFalse(folder.exists(), "the owner's discard removes its own allocation")

    def test_an_earlier_processs_leftover_in_angelos_folders_is_removable(self):
        for parent in (private_roots.scratch_root(), Path(tempfile.gettempdir())):
            with self.subTest(parent=parent.name):
                folder = parent / (PREFIX + "leftover")
                folder.mkdir()
                ProgramStorageMixin._remove_bundle(object(), str(self.bundle_in(folder)))
                self.assertFalse(folder.exists())

    def test_no_other_path_enters_the_cleanup(self):
        for folder in (self.base / "project" / (PREFIX + "lookalike"), private_roots.scratch_root() / "other-folder"):
            with self.subTest(folder=folder.name):
                folder.mkdir(parents=True)
                ProgramStorageMixin._remove_bundle(object(), str(self.bundle_in(folder)))
                self.assertTrue(folder.exists(), "a caller's path is never removed")


if __name__ == "__main__":
    unittest.main()
