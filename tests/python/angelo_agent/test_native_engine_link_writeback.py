# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The asset write-back runs in a background Angelo from the saved file, without Blender (doc/angelo/engine_link.rst).

A stand-in "Angelo" (a small script) records how it was started. Covered: the child runs in the background, with
factory settings, from the saved .blend, with the target, the exporter and its options; a failure is reported with the
child's last error line; a save during a write-back runs once it ends, with the newest file only.
"""

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_writeback  # noqa: E402

STAND_IN = """#!%s
import json, sys, time
log, = %r,
with open(log, "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
time.sleep(0.3)
if "fail" in sys.argv[-3]:
    print("Error: the exporter failed", file=sys.stderr)
    sys.exit(1)
"""


@unittest.skipIf(sys.platform == "win32", "a script stands in for the app")
class WriteBackTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.log = self.folder / "calls.jsonl"
        self.binary = self.folder / "Angelo"
        self.binary.write_text(STAND_IN % (sys.executable, str(self.log)))
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.writer = engine_link_writeback.WriteBack(str(self.binary))

    def wait(self, count):
        done, deadline = [], time.monotonic() + 15
        while len(done) < count and time.monotonic() < deadline:
            done += self.writer.finished()
            time.sleep(0.02)
        return done

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_the_child_exports_from_the_saved_file_in_the_background(self):
        self.writer.request("/p/a.glb", "/p/.angelo/a.glb.blend", "/p/a.glb", "gltf", {"export_apply": True})
        self.assertEqual(self.wait(1), [("/p/a.glb", None)])
        arguments = self.calls()[0]
        self.assertEqual(arguments[:3], ["--background", "--factory-startup", "/p/.angelo/a.glb.blend"])
        self.assertEqual(arguments[-3:], ["/p/a.glb", "gltf", json.dumps({"export_apply": True})])

    def test_the_child_is_told_which_exporter_writes_the_target(self):
        self.writer.request("/p/a.fbx", "/p/.angelo/a.fbx.blend", "/p/a.fbx", "fbx", {"object_types": ["MESH"]})
        self.assertEqual(self.wait(1), [("/p/a.fbx", None)])
        self.assertEqual(self.calls()[0][-3:], ["/p/a.fbx", "fbx", json.dumps({"object_types": ["MESH"]})])
        child = engine_link_writeback.CHILD
        self.assertIn('bpy.ops.export_scene.fbx(', child)
        self.assertIn('set(options["object_types"])', child)   # The FBX exporter takes a set, JSON carries a list.
        compile(child, "child", "exec")

    def test_a_failure_says_why(self):
        self.writer.request("k", "/p/f.blend", "/p/fail.glb", "gltf", {})
        (key, error), = self.wait(1)
        self.assertIn("the exporter failed", error)

    def test_saves_during_a_write_back_run_once_with_the_newest_file(self):
        self.writer.request("k", "/p/1.blend", "/p/a.glb", "gltf", {})
        self.writer.request("k", "/p/2.blend", "/p/a.glb", "gltf", {})
        self.writer.request("k", "/p/3.blend", "/p/a.glb", "gltf", {})
        self.assertEqual(len(self.wait(2)), 2)
        self.assertEqual([call[2] for call in self.calls()], ["/p/1.blend", "/p/3.blend"])
        self.assertFalse(self.writer.busy())


if __name__ == "__main__":
    unittest.main()
