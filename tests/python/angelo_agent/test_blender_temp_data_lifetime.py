# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regressions for Blender's temp_data() ownership and single-use context lifecycle.

The cases follow upstream fixes 9b217bcbd65, fffc34b8571 and 45756ac053d
(which contain no upstream test files). The context manager and its returned
BlendData must each own a reference, regardless of which Python owner dies first.
Exiting still frees native temporary data and invalidates retained RNA wrappers.

Run through run_native_suites.py in an isolated background factory-startup app.
No add-on, provider, account or network is needed.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import gc
import json
import tempfile
import unittest
from pathlib import Path

import bpy

assert bpy.app.background, "Run with --background"
assert "--factory-startup" in sys.argv, "Use an isolated factory-startup app"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="angelo-temp-data-lifetime-"))
OUT.mkdir(parents=True, exist_ok=True)


class TempDataLifetimeTests(unittest.TestCase):
    def test_context_and_target_own_separate_references(self):
        manager = bpy.data.temp_data()
        data = manager.__enter__()
        try:
            # Manager, local target, and getrefcount's temporary argument.
            # Check before destroying either owner, without relying on allocator reuse.
            self.assertEqual(sys.getrefcount(data), 3)
            self.assertIsInstance(data.filepath, str)
        finally:
            manager.__exit__(None, None, None)
        del manager
        gc.collect()
        self.assertEqual(sys.getrefcount(data), 2)
        with self.assertRaises(ReferenceError):
            _ = data.filepath

    def test_with_target_outlives_its_context(self):
        with bpy.data.temp_data() as data:
            material = data.materials.new("Temporary material")
            mesh = data.meshes.new("Temporary mesh")
            self.assertEqual(material.name, "Temporary material")
            self.assertEqual(mesh.name, "Temporary mesh")
        gc.collect()
        # The ordinary `with ... as data` target remains an invalidated Python
        # object, rather than a pointer into a freed Python allocation.
        with self.assertRaises(ReferenceError):
            _ = data.filepath
        with self.assertRaises(ReferenceError):
            _ = material.name
        with self.assertRaises(ReferenceError):
            _ = mesh.name
        self.assertNotIn("Temporary material", bpy.data.materials)
        self.assertNotIn("Temporary mesh", bpy.data.meshes)
        del data, material, mesh
        gc.collect()

    def test_target_can_be_released_before_its_context(self):
        manager = bpy.data.temp_data()
        data = manager.__enter__()
        material = data.materials.new("Released target material")
        del data
        gc.collect()
        self.assertEqual(material.name, "Released target material")
        self.assertIsNone(manager.__exit__(None, None, None))
        with self.assertRaises(ReferenceError):
            _ = material.name
        del manager, material
        gc.collect()

    def test_discard_without_entering(self):
        manager = bpy.data.temp_data()
        del manager
        gc.collect()

    def test_single_entry_and_exit(self):
        manager = bpy.data.temp_data()
        with self.assertRaisesRegex(RuntimeError, "Context manager not entered"):
            manager.__exit__(None, None, None)
        data = manager.__enter__()
        try:
            with self.assertRaisesRegex(RuntimeError, "Context manager already entered"):
                manager.__enter__()
        finally:
            manager.__exit__(None, None, None)
        with self.assertRaisesRegex(RuntimeError, "Context manager already exited"):
            manager.__exit__(None, None, None)
        with self.assertRaisesRegex(RuntimeError, "Context manager already entered"):
            manager.__enter__()
        del manager
        gc.collect()
        with self.assertRaises(ReferenceError):
            _ = data.filepath


result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(TempDataLifetimeTests))
report = {"success": result.wasSuccessful(), "tests_run": result.testsRun,
          "failures": len(result.failures), "errors": len(result.errors),
          "paid_requests": 0, "binary": bpy.app.binary_path}
(OUT / "temp-data-lifetime.json").write_text(json.dumps(report, indent=2) + "\n")
print("ANGELO_TEMP_DATA_LIFETIME", json.dumps(report), flush=True)
assert result.wasSuccessful(), report
