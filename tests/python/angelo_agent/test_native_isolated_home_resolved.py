# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An isolated test home is a real path, no symlink above it, as a person's home is (provider_isolation.new_home).

macOS's temporary folder is ``/var/folders/...`` and ``/var`` is a symlink to ``/private/var``. From 1.0.10 every host
test ran with its home there (provider isolation), so Angelo code that refuses a private folder below a symlink (the
Fab browser's scratch-root check, a planted-link guard) refused in tests where a person's app never does:
``test_native_fab_browser_profile``'s partial-constructor case got ``PROFILE_ROOT`` before the failure it injects.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provider_isolation as isolation  # noqa: E402


class IsolatedHomeResolved(unittest.TestCase):
    def setUp(self):
        self.real = Path(tempfile.mkdtemp(prefix="home-real-"))
        self.addCleanup(shutil.rmtree, self.real, True)
        self.link = self.real.parent / (self.real.name + "-link")
        try:
            os.symlink(self.real, self.link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:   # Windows without the symlink privilege.
            self.skipTest("this machine cannot make a directory symlink: %s" % error)
        self.addCleanup(os.unlink, self.link)

    def assert_real(self, home):
        self.assertEqual(os.path.realpath(str(home.parent)), str(home.parent))
        self.assertTrue(str(home).startswith(os.path.realpath(str(self.real))))

    def test_a_temporary_folder_below_a_symlink_gives_a_real_home(self):
        with mock.patch.object(tempfile, "tempdir", str(self.link)):
            home, remove = isolation.new_home({})
        self.assertTrue(remove)
        self.assert_real(home)

    def test_a_qa_folder_named_through_a_symlink_gives_a_real_home(self):
        home, remove = isolation.new_home({"ANGELO_QA_DIR": str(self.link / "qa")})
        self.assertFalse(remove)
        self.assert_real(home)


if __name__ == "__main__":
    unittest.main()
