# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The asset write-back runs in a background Angelo from the saved file, without Blender (doc/angelo/engine_link.rst).

A stand-in "Angelo" (a small script) records how it was started. Covered: the child runs in the background, with
factory settings, from the saved .blend, with the target, the exporter and its options; a failure is reported with the
child's last error line; a save during a write-back runs once it ends, with the newest file only; a glTF write-back
assigns the planned baked materials in the child's copy (doc/angelo/long_jobs.rst, "Baked materials at export").
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import hashlib
import inspect
import json
import os
import stat
import subprocess
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
with open(sys.argv[-1], encoding="utf-8") as handle:   # The spec file, there while the child runs.
    spec = json.load(handle)
with open(log, "a") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "spec": spec}) + "\\n")
time.sleep(0.3)
if "fail" in spec["target"]:
    print("Error: the exporter failed", file=sys.stderr)
    sys.exit(1)
"""


class WriteBackTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.log = self.folder / "calls.jsonl"
        self.binary = self.folder / "Angelo"
        self.binary.write_text(STAND_IN % (sys.executable, str(self.log)))
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        if sys.platform == "win32":   # Windows starts no "#!" script: Python runs the stand-in, its arguments as sent.
            run = subprocess.run
            self.enterContext(mock.patch.object(engine_link_writeback.subprocess, "run",
                                                lambda command, **options: run([sys.executable, *command], **options)))
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
        call = self.calls()[0]
        # The child opens the .blend itself, between two stats of it: never from its arguments.
        self.assertEqual(call["argv"][:3], ["--background", "--factory-startup", "--python-exit-code"])
        self.assertNotIn("/p/.angelo/a.glb.blend", call["argv"])
        self.assertEqual(call["spec"], {"stamp": None, "target": "/p/a.glb", "exporter": "gltf",
                                        "options": {"export_apply": True}, "baked": [], "saved": None,
                                        "superseded": engine_link_writeback.SUPERSEDED,
                                        "blend": "/p/.angelo/a.glb.blend"})

    def test_the_child_is_told_which_exporter_writes_the_target(self):
        self.writer.request("/p/a.fbx", "/p/.angelo/a.fbx.blend", "/p/a.fbx", "fbx", {"object_types": ["MESH"]})
        self.assertEqual(self.wait(1), [("/p/a.fbx", None)])
        spec = self.calls()[0]["spec"]
        self.assertEqual((spec["target"], spec["exporter"], spec["options"]), ("/p/a.fbx", "fbx", {"object_types": ["MESH"]}))
        child = engine_link_writeback.CHILD
        self.assertIn('bpy.ops.export_scene.fbx(', child)
        self.assertIn('set(options["object_types"])', child)   # The FBX exporter takes a set, JSON carries a list.
        compile(child, "child", "exec")

    def test_the_child_gives_planned_objects_their_baked_material_in_its_own_copy(self):
        self.writer.request("/p/a.glb", "/p/a.blend", "/p/a.glb", "gltf", {}, baked=[("Crate", "Crate Baked")])
        self.assertEqual(self.wait(1), [("/p/a.glb", None)])
        self.assertEqual(self.calls()[0]["spec"]["baked"], [["Crate", "Crate Baked"]])
        child = engine_link_writeback.CHILD
        gltf = child[child.index("else:"):]
        # On the mesh (the exporter reads a modified mesh's own materials), a shared mesh copied first so another
        # object sharing it keeps its own; only glTF (FBX binding is the engines').
        self.assertIn("slot.link, slot.material = 'DATA', material", gltf)
        self.assertLess(gltf.index("obj.data = data.copy()"), gltf.index("slot.link, slot.material"))
        self.assertLess(gltf.index("for item in baked"), gltf.index("export_scene.gltf("))
        self.assertNotIn("slot.material", child[child.index('if exporter == "fbx"'):child.index("else:")])
        self.assertNotIn("wm.save", child)   # The child's copy is never saved.

    def test_a_linked_object_gets_its_baked_material_in_the_childs_copy_too(self):
        """Before, the child skipped a linked object, so the asset kept its own materials while the live export and the
        preflight's expectation had the baked one (release-1.0.9 review). It stays linked (made local, its linked
        children lost their parent: break-it 1.0.10 #1) and is found by name and library, never by name alone, which
        names the local one first (break-it 1.0.10 #4)."""
        library = object()
        linked_mesh = SimpleNamespace(library=library, override_library=None, users=1, materials=[])
        local_mesh = SimpleNamespace(library=None, override_library=None, users=1, materials=[])
        linked_mesh.copy = lambda: local_mesh
        slots = [SimpleNamespace(link="OBJECT", material="Wood"), SimpleNamespace(link="OBJECT", material="Metal")]
        crate = SimpleNamespace(library=library, data=linked_mesh, material_slots=slots)
        crate.make_local = mock.Mock(side_effect=AssertionError("never made local"))
        other = SimpleNamespace(library=None, data=SimpleNamespace(library=None, override_library=None, users=1),
                                material_slots=[SimpleNamespace(link="OBJECT", material="Paint")])
        baked = SimpleNamespace(name="Crate Baked")
        written = {}
        def gltf(filepath, **_options):
            written["slots"] = [(slot.link, slot.material) for slot in crate.material_slots]
            written["other"] = [(slot.link, slot.material) for slot in other.material_slots]
            Path(filepath).write_bytes(b"new bytes")
            return {"FINISHED"}
        bpy = types.ModuleType("bpy")
        bpy.ops = SimpleNamespace(export_scene=SimpleNamespace(gltf=gltf),
                                  wm=SimpleNamespace(open_mainfile=lambda **_kwargs: {"FINISHED"}))
        bpy.data = SimpleNamespace(objects={("Crate", "//lib.blend"): crate, ("Crate", None): other, "Crate": other},
                                   materials={("Crate Baked", None): baked})
        utils = types.ModuleType("addon_utils")
        utils.enable = lambda *_args, **_kwargs: None
        with tempfile.TemporaryDirectory() as folder:
            spec = Path(folder, "spec.json")
            spec.write_text(json.dumps({"stamp": None, "target": str(Path(folder, "a.glb")), "exporter": "gltf",
                                        "options": {}, "baked": [["Crate", "Crate Baked", "//lib.blend", None]],
                                        "blend": str(Path(folder, "work.blend"))}))
            with mock.patch.dict(sys.modules, {"bpy": bpy, "addon_utils": utils}), \
                    mock.patch.object(sys, "argv", ["angelo", "--", str(spec)]):
                exec(compile(engine_link_writeback.CHILD, "child", "exec"), {})
        self.assertIs(crate.data, local_mesh)   # The linked mesh is never written to.
        self.assertEqual(written["slots"], [("DATA", baked), ("DATA", baked)])
        self.assertEqual(written["other"], [("OBJECT", "Paint")], "the local object of the same name keeps its own")

    def run_child(self, folder, stamp, blend=None, saved=None, after_spec=None, exported=None):
        """The child's own code, with a stand-in exporter that writes the file, as a background Angelo runs it
        (``blend``: the file it opens, ``saved``: its stat at the save that asked). Blender's own load is a stand-in
        that reads the file's bytes: at the start where an older child had it opened from its arguments, again
        where the child opens it itself; ``exported`` gets the bytes the export came from. ``after_spec`` runs once
        the child has read its arguments."""
        target = Path(folder, "a.glb")
        target.write_bytes(b"old")
        loaded = [Path(blend).read_bytes() if blend and Path(blend).exists() else b""]

        def gltf(filepath, **_options):
            if exported is not None:
                exported.append(loaded[0])
            Path(filepath).write_bytes(b"new bytes")
            return {"FINISHED"}

        def open_mainfile(filepath, **_kwargs):
            loaded[0] = Path(filepath).read_bytes() if Path(filepath).exists() else b""
            return {"FINISHED"}
        bpy = types.ModuleType("bpy")
        bpy.ops = SimpleNamespace(export_scene=SimpleNamespace(gltf=gltf),
                                  wm=SimpleNamespace(open_mainfile=open_mainfile))
        bpy.data = SimpleNamespace(objects={}, materials={}, filepath=str(blend or ""))
        utils = types.ModuleType("addon_utils")
        utils.enable = lambda *_args, **_kwargs: None
        spec = Path(folder, "spec.json")
        spec.write_text(json.dumps({"stamp": stamp, "target": str(target), "exporter": "gltf", "options": {},
                                    "baked": [], "saved": saved, "superseded": engine_link_writeback.SUPERSEDED,
                                    "blend": str(blend or Path(folder, "none.blend"))}))
        argv = ["angelo", "--", str(spec)]
        real_load = json.load

        def load(handle):
            value = real_load(handle)
            if after_spec is not None:
                after_spec()
            return value
        json_module = types.ModuleType("json")
        json_module.__dict__.update(json.__dict__)
        json_module.load = load
        with mock.patch.dict(sys.modules, {"bpy": bpy, "addon_utils": utils, "json": json_module}), \
                mock.patch.object(sys, "argv", argv):
            exec(compile(engine_link_writeback.CHILD, "child", "exec"), {})
        return target

    def test_a_child_never_exports_a_version_read_while_another_was_moved_in_and_out(self):
        """Release-1010 break-it (M7): A at the path for both of the child's stats, B there only while Blender read it.
        The child compares the file the load read (``_bpy._angelo_saved.read_file``, its own descriptor)."""
        import types
        with tempfile.TemporaryDirectory() as folder:
            blend = Path(folder, "work.blend")
            blend.write_bytes(b"BLENDER as saved")
            info = os.stat(blend)
            saved = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
            other = Path(folder, "other.blend")
            other.write_bytes(b"BLENDER another version, read")
            read_as = os.stat(other)
            native = types.ModuleType("_bpy")
            native._angelo_saved = SimpleNamespace(read_file=lambda: (str(blend), read_as.st_dev, read_as.st_ino,
                                                                      read_as.st_size, read_as.st_mtime_ns, 0))
            exported = []
            with mock.patch.dict(sys.modules, {"_bpy": native}), \
                    self.assertRaisesRegex(RuntimeError, engine_link_writeback.SUPERSEDED):
                self.run_child(folder, None, blend, saved, exported=exported)
            self.assertEqual(exported, [], "nothing exported")
            self.assertEqual(Path(folder, "a.glb").read_bytes(), b"old")

    def test_a_child_never_exports_a_version_moved_back_after_it_read_another(self):
        """Release-1010 audit re-check 3 (P3, ABA): the child stat the .blend only after Blender had loaded it from its
        arguments, so a version another program put at the path before that load, and moved away again before the
        stat, was exported as the save that asked. It now opens the file itself between two stats of it."""
        with tempfile.TemporaryDirectory() as folder:
            blend, aside = Path(folder, "work.blend"), Path(folder, "aside.blend")
            blend.write_bytes(b"BLENDER as saved")
            info = os.stat(blend)
            saved = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
            os.replace(blend, aside)   # Moved away; another version at the path when the child starts.
            blend.write_bytes(b"BLENDER another version")
            exported = []
            try:
                self.run_child(folder, None, blend, saved, after_spec=lambda: os.replace(aside, blend),
                               exported=exported)
            except RuntimeError as error:
                self.assertIn(engine_link_writeback.SUPERSEDED, str(error))
            self.assertIn(exported, ([], [b"BLENDER as saved"]), "only ever the save that asked is exported")

    def test_the_child_stamps_what_it_wrote_so_a_session_gone_meanwhile_cant_lose_the_working_file(self):
        with tempfile.TemporaryDirectory() as folder:
            stamp = Path(folder, "a.glb.blend.json")
            target = self.run_child(folder, {"path": str(stamp), "asset": "res://a.glb", "save": 4, "link": 2,
                                                 "writer": "this Angelo"})
            # The save and link it wrote for: the session takes this digest for that save only (written_stamp).
            self.assertEqual(json.loads(stamp.read_text()), {"sha256": hashlib.sha256(b"new bytes").hexdigest(),
                                                             "asset": "res://a.glb", "save": 4, "link": 2,
                                                             "writer": "this Angelo"})
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()),
                             ["a.glb", "a.glb.blend.json", "spec.json"])
            self.assertEqual(target.read_bytes(), b"new bytes")

    def test_a_child_that_read_another_version_of_the_blend_writes_nothing(self):
        """Stat before read (release-1010 break-it, M1): the child read the .blend by its path seconds after the save,
        so a version written there meanwhile (a newer save, another program) was written back and stamped as the save
        that asked. It now compares the file it read with the save's stat and refuses."""
        with tempfile.TemporaryDirectory() as folder:
            blend, stamp = Path(folder, "work.blend"), Path(folder, "a.glb.blend.json")
            blend.write_bytes(b"BLENDER as saved")
            info = os.stat(blend)
            saved = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
            replacement = Path(folder, "other.blend")
            replacement.write_bytes(b"BLENDER written after the save")
            os.replace(replacement, blend)
            with self.assertRaisesRegex(RuntimeError, engine_link_writeback.SUPERSEDED):
                self.run_child(folder, {"path": str(stamp), "asset": "res://a.glb"}, blend, saved)
            self.assertEqual(Path(folder, "a.glb").read_bytes(), b"old", "nothing written back")
            self.assertFalse(stamp.exists(), "nothing stamped")
            info = os.stat(blend)
            target = self.run_child(folder, {"path": str(stamp), "asset": "res://a.glb"}, blend,
                                    [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])
            self.assertEqual(target.read_bytes(), b"new bytes", "the save it was asked for: written")

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
        self.assertEqual([call["spec"]["blend"] for call in self.calls()], ["/p/1.blend", "/p/3.blend"])
        self.assertFalse(self.writer.busy())

    def test_a_scene_with_thousands_of_baked_objects_keeps_a_short_command_line(self):
        """Windows refuses a command line past 32,767 characters: the arguments go in a file, whatever the scene."""
        baked = [("Object.%05d" % index, "Baked.%05d" % index) for index in range(5000)]
        self.writer.request("/p/a.glb", "/p/a.blend", "/p/a.glb", "gltf", {"export_apply": True}, baked=baked)
        self.assertEqual(self.wait(1), [("/p/a.glb", None)])
        call = self.calls()[0]
        self.assertEqual(call["spec"]["baked"], [list(pair) for pair in baked])
        self.assertLess(len(json.dumps(call["argv"][-1])), 1024)
        self.assertNotIn("Object.00001", " ".join(call["argv"]))

    def test_the_spec_file_is_gone_once_the_child_has_ended(self):
        self.writer.request("/p/a.glb", "/p/a.blend", "/p/a.glb", "gltf", {}, baked=[("Crate", "Crate Baked")])
        self.wait(1)
        self.assertFalse(Path(self.calls()[0]["argv"][-1]).exists())

    def test_a_spec_folder_windows_cant_remove_yet_never_fails_a_write_back_that_worked(self):
        """An antivirus scan can hold spec.json past the child's end: removing it raises PermissionError on Windows."""
        real, left = os.unlink, []
        from angelo_agent import private_roots
        before = set(Path(private_roots.scratch_root()).glob("angelo-engine-link-*"))

        def held(path, *args, **kwargs):
            if os.path.basename(str(path)) == "spec.json":   # rmtree removes by the folder's descriptor.
                left.append(path)
                raise PermissionError(13, "The process cannot access the file", str(path))
            return real(path, *args, **kwargs)
        with mock.patch.object(os, "unlink", held):
            self.writer.request("/p/a.glb", "/p/a.blend", "/p/a.glb", "gltf", {})
            done = self.wait(1)
        for folder in set(Path(private_roots.scratch_root()).glob("angelo-engine-link-*")) - before:
            for item in folder.iterdir():   # What Windows would remove once the scan lets go.
                real(item)
            folder.rmdir()
        self.assertTrue(left)
        self.assertEqual(done, [("/p/a.glb", None)])

if __name__ == "__main__":
    unittest.main()
