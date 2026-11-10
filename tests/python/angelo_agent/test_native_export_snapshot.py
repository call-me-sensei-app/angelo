# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``export_snapshot.take`` on each platform's path (doc/angelo/engine_preflight.rst, "Measurements"): Windows copies
with the system's ``CopyFile2`` and makes the copy writable, and falls back to a plain copy when the call is missing
or fails, starting from a clean target; this platform's own path still copies. ``_winapi`` is a stand-in here (the
host suite runs on Linux and macOS); bench_export_snapshot.py times the real calls on Windows."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import export_snapshot  # noqa: E402


class Folder(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-snapshot-test-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.source = self.folder / "scene.glb"
        self.source.write_bytes(b"as exported")
        self.target = self.folder / "copy.glb"

    def on_windows(self, winapi):
        patches = [mock.patch.object(export_snapshot, "sys", types.SimpleNamespace(platform="win32")),
                   mock.patch.dict(sys.modules, {"_winapi": winapi})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)


class WindowsTests(Folder):
    def test_the_system_copy_is_used_and_a_read_only_copy_made_writable(self):
        calls = []

        def copy_file2(source, target, flags):
            calls.append((source, target, flags))
            shutil.copy2(source, target)
            os.chmod(target, stat.S_IREAD)   # CopyFile2 carries the source's attributes, read-only among them.
        self.on_windows(types.SimpleNamespace(CopyFile2=copy_file2))
        identity = export_snapshot.take(str(self.source), str(self.target))
        self.assertEqual(calls, [(str(self.source), str(self.target), 0)])
        self.assertEqual(self.target.read_bytes(), b"as exported")
        self.assertTrue(os.stat(self.target).st_mode & stat.S_IWRITE, "the copy's folder could not remove it")
        self.assertEqual(identity, export_snapshot.identity(str(self.target)))

    def test_a_failed_system_copy_falls_back_to_a_clean_plain_copy(self):
        def copy_file2(source, target, flags):
            Path(target).write_bytes(b"half")   # What a copy that failed part way leaves.
            raise OSError(112, "There is not enough space on the disk")
        self.on_windows(types.SimpleNamespace(CopyFile2=copy_file2))
        identity = export_snapshot.take(str(self.source), str(self.target))
        self.assertEqual(self.target.read_bytes(), b"as exported")
        self.assertEqual(identity, export_snapshot.identity(str(self.target)))

    def test_without_copy_file2_the_plain_copy_runs(self):
        self.on_windows(types.SimpleNamespace())   # Python before 3.12 has no _winapi.CopyFile2.
        export_snapshot.take(str(self.source), str(self.target))
        self.assertEqual(self.target.read_bytes(), b"as exported")


class ThisPlatformTests(Folder):
    def test_a_later_write_over_the_source_never_reaches_the_copy(self):
        identity = export_snapshot.take(str(self.source), str(self.target))
        self.source.write_bytes(b"a later export over the same name")
        self.assertEqual(self.target.read_bytes(), b"as exported")
        self.assertEqual(export_snapshot.identity(str(self.target)), identity)


if __name__ == "__main__":
    unittest.main()
