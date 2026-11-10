# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Enabling Angelo's add-on after something imported it never reloads it (1.0.7).

``addon_utils.enable`` reloads an already-imported module whose ``__time__`` differs from its file's, and a module
imported by anything but ``enable`` had none: Angelo's start-up layout imports the package (``opted_out``) before it
enables the add-on, so every start-up printed "module changed on disk ... reloading" and ran the package twice (in
~110 of 155 GUI suites' logs on the 1.0.7 cut 744f498d0a6). The package records its own time on import, as
``enable`` does after its own import and as ``addon_utils`` does for ``bl_pkg``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "scripts/addons_core/angelo_agent/__init__.py"


def import_package(path):
    spec = importlib.util.spec_from_file_location("_angelo_agent_time_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enable_reloads(module):
    """``addon_utils.enable``'s test for an imported module, as its source states it."""
    return getattr(module, "__time__", 0) != os.path.getmtime(module.__file__)


class TimeStampTests(unittest.TestCase):
    def test_an_import_before_enable_records_the_files_time(self):
        package = import_package(PACKAGE)
        self.assertEqual(package.__time__, os.path.getmtime(PACKAGE))
        self.assertFalse(enable_reloads(package), "enable would reload the add-on it just found imported")

    def test_a_file_changed_on_disk_after_the_import_is_still_reloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "__init__.py"
            copy.write_text(PACKAGE.read_text(encoding="utf-8"), encoding="utf-8")
            package = import_package(copy)
            os.utime(copy, (package.__time__ + 10, package.__time__ + 10))
            self.assertTrue(enable_reloads(package))

    def test_enable_compares_the_recorded_time_with_the_files(self):
        """The contract the stamp relies on, in the bundled ``addon_utils``."""
        source = (ROOT / "scripts/modules/addon_utils.py").read_text(encoding="utf-8")
        self.assertIn('mtime_orig = getattr(mod, "__time__", 0)', source)
        self.assertIn("if mtime_orig != mtime_new:", source)

    def test_the_start_up_imports_the_package_before_enabling_it(self):
        """Why the stamp is needed: the start-up's opt-out check imports the package first."""
        layout = (ROOT / "scripts/startup/angelo_default_layout.py").read_text(encoding="utf-8")
        start = layout[layout.index("def open_default():"):]
        self.assertLess(start.index("if opted_out():"), start.index('addon_utils.enable("angelo_agent"'))
        self.assertIn("from angelo_agent import product_env", layout)


if __name__ == "__main__":
    unittest.main()
