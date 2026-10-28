# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A build folder's installed add-on is made the checkout's before the release stage's native suites.

Dependency-free. ``prune_installed_addon.prune`` runs on a fake checkout and a fake Windows install (the layout
``native_addon_guard.bundled_scripts`` reads): it removes only the add-on modules the checkout lacks, with their
bytecode, and leaves every other difference for the guard to refuse. The release stage calls it before the suites.
"""

import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import native_addon_guard as guard  # noqa: E402
import prune_installed_addon  # noqa: E402

STAGE = (ROOT / "scripts/angelo/windows_release_validate.ps1").read_text(encoding="utf-8")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "src"
        self.binary = self.tmp / "install" / "Angelo.exe"
        write(self.binary, "exe")
        self.app = self.tmp / "install" / "5.2" / "scripts"
        for root in (self.source / "scripts", self.app):
            write(root / "addons_core/angelo_agent/__init__.py", "init")
            write(root / "addons_core/angelo_agent/common.py", "same")
            write(root / "startup/angelo_boot.py", "boot")

    def addon(self, name):
        return self.app / "addons_core/angelo_agent" / name

    def test_only_modules_the_checkout_lacks_go_with_their_bytecode(self):
        write(self.addon("live_view.py"), "stale")
        write(self.addon("__pycache__/live_view.cpython-313.pyc"), "pyc")
        write(self.addon("__pycache__/common.cpython-313.pyc"), "pyc")
        write(self.addon("parts/old_part.py"), "stale too")
        write(self.app / "startup/build_added.py", "the build adds this")
        self.assertFalse(guard.check(self.binary, self.source)["matches"])
        with redirect_stdout(StringIO()):
            removed, after = prune_installed_addon.prune(self.binary, self.source)
        self.assertEqual(sorted(removed), ["addon/live_view.py", "addon/parts/old_part.py"])
        self.assertTrue(after["matches"], after["differing"])
        self.assertFalse(self.addon("live_view.py").exists())
        self.assertFalse(self.addon("__pycache__/live_view.cpython-313.pyc").exists())
        self.assertTrue(self.addon("__pycache__/common.cpython-313.pyc").exists(), "another module's bytecode stays")
        self.assertTrue((self.app / "startup/build_added.py").exists(), "nothing outside the add-on is touched")

    def test_every_other_difference_is_left_for_the_guard_to_refuse(self):
        write(self.addon("common.py"), "changed in the app")
        write(self.source / "scripts/addons_core/angelo_agent/new_module.py", "only in the checkout")
        removed, after = prune_installed_addon.prune(self.binary, self.source)
        self.assertEqual(removed, [])
        self.assertEqual(sorted(after["differing"]), ["addon/common.py: differs", "addon/new_module.py: only in the checkout"])
        self.assertTrue(self.addon("common.py").exists())

    def test_an_app_without_an_add_on_is_left_alone(self):
        empty = self.tmp / "other" / "Angelo.exe"
        write(empty, "exe")
        removed, after = prune_installed_addon.prune(empty, self.source)
        self.assertEqual(removed, [])
        self.assertFalse(after["matches"])

    def test_the_release_stage_prunes_before_its_native_suites(self):
        check = STAGE[STAGE.index('Test-Check "release: BACKGROUND native suites on the full build"'):]
        prune_at = check.index("prune_installed_addon.py")
        self.assertLess(prune_at, check.index("run_native_suites.py"))
        self.assertIn("--binary", check[prune_at:prune_at + 200])
        self.assertIn("--source", check[prune_at:prune_at + 200])


if __name__ == "__main__":
    unittest.main()
