# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The asset write-back runs in a background Angelo from the saved file, without Blender (doc/angelo/engine_link.rst).

A stand-in "Angelo" (a small script) records how it was started. Covered: the child runs in the background, with
factory settings, from the saved .blend, with the target, the exporter and its options; a failure is reported with the
child's last error line; a save during a write-back runs once it ends, with the newest file only; a glTF write-back
assigns the planned baked materials in the child's copy (doc/angelo/long_jobs.rst, "Baked materials at export").
"""

import hashlib
import inspect
import json
import os
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_writeback  # noqa: E402

STAND_IN = """#!%s
import json, sys, time
log, = %r,
with open(log, "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
time.sleep(0.3)
if "fail" in sys.argv[-4]:
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
        self.assertEqual(arguments[-4:], ["/p/a.glb", "gltf", json.dumps({"export_apply": True}), "[]"])

    def test_the_child_is_told_which_exporter_writes_the_target(self):
        self.writer.request("/p/a.fbx", "/p/.angelo/a.fbx.blend", "/p/a.fbx", "fbx", {"object_types": ["MESH"]})
        self.assertEqual(self.wait(1), [("/p/a.fbx", None)])
        self.assertEqual(self.calls()[0][-4:], ["/p/a.fbx", "fbx", json.dumps({"object_types": ["MESH"]}), "[]"])
        child = engine_link_writeback.CHILD
        self.assertIn('bpy.ops.export_scene.fbx(', child)
        self.assertIn('set(options["object_types"])', child)   # The FBX exporter takes a set, JSON carries a list.
        compile(child, "child", "exec")

    def test_the_child_gives_planned_objects_their_baked_material_in_its_own_copy(self):
        self.writer.request("/p/a.glb", "/p/a.blend", "/p/a.glb", "gltf", {}, baked=[("Crate", "Crate Baked")])
        self.assertEqual(self.wait(1), [("/p/a.glb", None)])
        self.assertEqual(json.loads(self.calls()[0][-1]), [["Crate", "Crate Baked"]])
        child = engine_link_writeback.CHILD
        gltf = child[child.index("else:"):]
        # On the mesh (the exporter reads a modified mesh's own materials), a shared mesh copied first so another
        # object sharing it keeps its own; only glTF (FBX binding is the engines').
        self.assertIn("slot.link, slot.material = 'DATA', material", gltf)
        self.assertLess(gltf.index("obj.data = obj.data.copy()"), gltf.index("slot.link, slot.material"))
        self.assertLess(gltf.index("for object_name, material_name in baked"), gltf.index("export_scene.gltf("))
        self.assertNotIn("slot.material", child[child.index('if exporter == "fbx"'):child.index("else:")])
        self.assertNotIn("wm.save", child)   # The child's copy is never saved.

    def run_child(self, folder, stamp):
        """The child's own code, with a stand-in exporter that writes the file, as a background Angelo runs it."""
        target = Path(folder, "a.glb")
        target.write_bytes(b"old")

        def gltf(filepath, **_options):
            Path(filepath).write_bytes(b"new bytes")
            return {"FINISHED"}
        bpy = types.ModuleType("bpy")
        bpy.ops = SimpleNamespace(export_scene=SimpleNamespace(gltf=gltf))
        bpy.data = SimpleNamespace(objects={}, materials={})
        utils = types.ModuleType("addon_utils")
        utils.enable = lambda *_args, **_kwargs: None
        argv = ["angelo", "--", json.dumps(stamp), str(target), "gltf", "{}", "[]"]
        with mock.patch.dict(sys.modules, {"bpy": bpy, "addon_utils": utils}), mock.patch.object(sys, "argv", argv):
            exec(compile(engine_link_writeback.CHILD, "child", "exec"), {})
        return target

    def test_the_child_stamps_what_it_wrote_so_a_session_gone_meanwhile_cant_lose_the_working_file(self):
        with tempfile.TemporaryDirectory() as folder:
            stamp = Path(folder, "a.glb.blend.json")
            target = self.run_child(folder, {"path": str(stamp), "asset": "res://a.glb"})
            self.assertEqual(json.loads(stamp.read_text()), {"sha256": hashlib.sha256(b"new bytes").hexdigest(),
                                                             "asset": "res://a.glb"})
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["a.glb", "a.glb.blend.json"])
            self.assertEqual(target.read_bytes(), b"new bytes")

    def test_a_stamp_that_cant_be_written_leaves_the_written_asset_a_success(self):
        with tempfile.TemporaryDirectory() as folder:
            target = self.run_child(folder, {"path": str(Path(folder, "gone", "a.json")), "asset": "res://a.glb"})
            self.assertEqual(target.read_bytes(), b"new bytes")

    def test_a_save_ended_is_never_reported_busy(self):
        """Its end reported while ``busy`` still names it would make the preflight skip it for a newer save."""
        self.writer.request("one", "/p/a.blend", "/p/a.glb", "gltf", {})
        self.wait(1)
        self.assertFalse(self.writer.busy())
        source = inspect.getsource(engine_link_writeback.WriteBack._run)
        self.assertLess(source.index("self._running ="), source.index("self._done.put"))

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
