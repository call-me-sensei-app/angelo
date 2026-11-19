# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A module made while Angelo runs is importable across a script's boundaries (script_confinement_sweep).

Python's FileFinder lists a folder once and lists it again only when the folder's mtime moves. Where it does not
(seen on Windows: the 2.0.1 Windows run's test_blender_confinement_escapes made a package, and neither the script nor
Angelo's next import found it), a package made before a script, or by a script for Angelo, stays invisible until the
caches are invalidated. Both sweeps mark every cached FileFinder stale at a script's start and end, as
``importlib.invalidate_caches`` does for them, without calling any finder (a script may have patched one). The
folder's mtime is pinned exactly here, so the stale listing happens on every file system.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from angelo_agent import script_confinement_sweep as sweep

SWEEPS = {"python": sweep.python_sweep, "native": sweep.native_python_sweep}


class NewModulesAreFoundAcrossAScript(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="v201-sweep-"))
        self.pinned = (int(time.time()) - 10) * 1_000_000_000   # Whole seconds: exact on every file system.
        os.utime(self.folder, ns=(self.pinned, self.pinned))
        sys.path.insert(0, str(self.folder))
        self.addCleanup(self.cleanup)
        self.assertIsNone(importlib.util.find_spec("v201_sweep_absent"))   # Its FileFinder lists the folder now.
        self.finder = sys.path_importer_cache[str(self.folder)]
        self.assertIs(type(self.finder), importlib.machinery.FileFinder)

    def cleanup(self):
        sys.path.remove(str(self.folder))
        sys.path_importer_cache.pop(str(self.folder), None)
        for name in [name for name in sys.modules if name.startswith("v201_sweep_")]:
            del sys.modules[name]

    def make_package(self, name):
        (self.folder / name).mkdir()
        (self.folder / name / "__init__.py").write_text("VALUE = 7\n")
        os.utime(self.folder, ns=(self.pinned, self.pinned))   # The folder's mtime does not move.

    def sweeps(self):
        return [(label, make("angelo_agent", lambda path: True)) for label, make in SWEEPS.items()]

    def test_the_listing_is_stale_without_a_sweep(self):   # The failure this guards, reproduced: no flakiness.
        self.make_package("v201_sweep_control")
        self.assertIsNone(importlib.util.find_spec("v201_sweep_control"))

    def test_a_package_made_before_a_script_is_found_in_it(self):
        for label, (begin, end) in self.sweeps():
            with self.subTest(sweep=label):
                name = "v201_sweep_before_" + label
                self.make_package(name)
                snapshot = begin()
                self.assertIsNotNone(importlib.util.find_spec(name), "the script finds what was made before it")
                end(snapshot)

    def test_a_package_made_during_a_script_is_found_after_it(self):
        for label, (begin, end) in self.sweeps():
            with self.subTest(sweep=label):
                name = "v201_sweep_during_" + label
                snapshot = begin()
                importlib.util.find_spec("v201_sweep_absent")   # The script's own look refreshes nothing new.
                self.make_package(name)
                end(snapshot)
                self.assertEqual(importlib.import_module(name).VALUE, 7, "Angelo's next import finds it")
                self.assertIs(sys.path_importer_cache[str(self.folder)], self.finder, "the finder is not replaced")

    def test_no_finder_method_runs_and_only_exact_file_finders_are_marked(self):
        calls = []

        class Lookalike(importlib.machinery.FileFinder):
            pass

        other = Lookalike(str(self.folder))
        other._path_mtime = 5
        key = str(self.folder) + os.sep + "lookalike"
        sys.path_importer_cache[key] = other
        self.addCleanup(sys.path_importer_cache.pop, key, None)
        self.finder.invalidate_caches = lambda: calls.append("instance")   # As a script's patch would be.
        self.addCleanup(vars(self.finder).pop, "invalidate_caches", None)
        for label, (begin, end) in self.sweeps():
            with self.subTest(sweep=label):
                self.finder._path_mtime = 1
                end(begin())
                self.assertEqual(self.finder._path_mtime, -1)
                self.assertEqual(other._path_mtime, 5, "a finder of any other class is not touched")
        self.assertEqual(calls, [], "no finder's method is called")


if __name__ == "__main__":
    unittest.main()
