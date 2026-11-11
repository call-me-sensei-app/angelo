# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine live link's requests, sessions and saves on a stand-in ``bpy`` (doc/angelo/engine_link.rst), with
Godot's adapter and a stand-in second engine.

Covered: an engine with no adapter is refused; a close ends the link by the asset's name, without resolving it; requests are routed to the asking editor's engine adapter, and its
exporter writes the asset back; live scenes go only to editors that show them, of that engine and project, while
saves reach every editor of the project; a bad request is refused with its reason and opens nothing; a good one waits for the person (never opens by
itself) and a newer one replaces it; Not now, an expired request and a disconnected editor withdraw it; an editor
asking for the linked asset joins at once; Godot's close ends the link; saving the linked .glb writes it back and
tells Godot, while a copy (a checkpoint) does not and Save As elsewhere ends the link; opening another file ends it;
the shipped plugin installs into a project and replaces an older copy; a glTF save sends the current baked materials
and says which objects are stale, while FBX sends none.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import addon_stand_in_import as stand_ins

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    handlers = SimpleNamespace(persistent=lambda function: function, depsgraph_update_post=[], load_post=[],
                               save_post=[], exit_pre=[], undo_post=[], redo_post=[])
    timers = SimpleNamespace(registered=set())
    timers.register = lambda function, **kwargs: timers.registered.add(function)
    timers.unregister = timers.registered.discard
    timers.is_registered = lambda function: function in timers.registered
    bpy.app = SimpleNamespace(handlers=handlers, timers=timers, background=True, binary_path="/Apps/Angelo",
                              translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text,
                                                           locale="en_US"))
    bpy.data = SimpleNamespace(filepath="", is_dirty=False)
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[]))
    return bpy


BPY = fake_bpy()
_BEFORE = stand_ins.snapshot()
with mock.patch.dict(sys.modules, {"bpy": BPY}):
    from angelo_agent import engine_link, engine_link_assets as assets, engine_link_facts, godot_link_assets  # noqa
    _IMPORTED = stand_ins.imported_since(_BEFORE)
# Each module imported above is the same object again for later suites, or unreachable if it holds the stand-in bpy
# (addon_stand_in_import.settle: two engine_link_facts made test_native_engine_link_save_report fail after this suite).
stand_ins.settle(_IMPORTED, BPY)


class FakeServer:
    def __init__(self):
        self.sent, self.items = [], []
        self.info = {1: {"project": ""}, 2: {"project": ""}}


    def lagging(self, client):   # LinkServer: a client that lost delta scenes (none here).
        return False

    def lagging_any(self):
        return False
    def send(self, client, header, payload=b""):
        self.sent.append((client, header))
        return True

    def broadcast(self, header, payload=b"", only=None):
        targets = [client for client, info in self.info.items() if only is None or only(info)]
        for client in targets:
            self.sent.append((client, header))
        return len(targets)

    def clients(self):
        return dict(self.info)

    def info_of(self, client):
        return dict(self.info.get(client, {}))

    def drain(self):
        items, self.items = self.items, []
        return items

    def last(self, client=None):
        return [header for target, header in self.sent if client is None or target == client][-1]


class FakeWriteBack:
    def __init__(self):
        self.requests, self.done, self.running, self.baked, self.saved = [], [], False, [], []

    def request(self, key, blend, target, exporter, options, baked=(), stamp=None, saved=None):
        self.requests.append((key, blend, target, exporter, options))
        self.saved.append(saved)
        self.baked.append(list(baked))

    def busy(self):
        return self.running

    def finished(self):
        items, self.done = self.done, []
        return items


class LinkStateTests(unittest.TestCase):
    def setUp(self):
        from save_identity_stand_in import stand_in_for_saves
        stand_in_for_saves(self)   # Blender's save names the file it wrote (``_bpy._angelo_saved``): stood in.
        self.project = Path(tempfile.mkdtemp()).resolve()
        (self.project / "project.godot").write_text("config_version=5\n")
        for name in ("crate.glb", "crate.blend", "other.glb"):
            (self.project / name).write_bytes(b"x")
        self.server = FakeServer()
        for info in self.server.info.values():
            info["project"] = str(self.project)
        self.events = []
        saved = dict(engine_link._state)
        self.addCleanup(lambda: (engine_link._state.clear(), engine_link._state.update(saved)))
        self.writeback = FakeWriteBack()
        engine_link._state.update(server=self.server, pending=None, session=None, opening=None, dirty_at=None,
                                 writeback=self.writeback, facts=engine_link_facts.new())
        hooks = dict(engine_link.hooks)
        self.addCleanup(lambda: engine_link.hooks.update(hooks))
        engine_link.hooks.update(request=lambda request: self.events.append(("request", request["asset"].ident)),
                                withdraw=lambda: self.events.append(("withdraw",)), changed=lambda: None)
        BPY.data.filepath = ""

    def open(self, client=1, asset="res://crate.glb", **extra):
        engine_link._handle(client, dict({"type": "open", "id": "7", "project": str(self.project), "asset": asset,
                                         "project_name": "Game", "node": "Crate"}, **extra))

    def link(self, asset="res://crate.glb"):
        resolved = godot_link_assets.resolve({"project": str(self.project), "asset": asset})
        engine_link._state["session"] = {"asset": resolved, "params": {}}
        return resolved

    def test_a_bad_request_is_refused_and_opens_nothing(self):
        self.open(asset="res://../x.glb")
        self.assertEqual((self.server.last()["type"], self.server.last()["code"]), ("refused", "outside"))
        self.assertIsNone(engine_link.pending())
        self.assertEqual(self.events, [])

    def test_a_request_waits_for_the_person_and_a_newer_one_replaces_it(self):
        self.open()
        self.assertEqual(self.server.last(1)["type"], "pending")
        self.assertEqual(engine_link.pending()["project_name"], "Game")
        self.assertEqual(self.events, [("request", "res://crate.glb")])
        self.open(client=2, asset="res://other.glb")
        self.assertEqual(self.server.last(1)["code"], "superseded")
        self.assertEqual(engine_link.pending()["asset"].ident, "res://other.glb")
        engine_link.decline()
        self.assertEqual(self.server.last(2)["code"], "declined")
        self.assertIsNone(engine_link.pending())
        self.assertEqual(self.events[-1], ("withdraw",))

    def test_expiry_and_disconnection_withdraw_the_request(self):
        self.open()
        engine_link._state["pending"]["at"] = time.monotonic() - engine_link.REQUEST_SECONDS - 1
        engine_link._tick()
        self.assertIsNone(engine_link.pending())
        self.open(client=2)
        engine_link._handle(1, {"type": "disconnected"})
        self.assertIsNotNone(engine_link.pending(), "another editor's leaving keeps it")
        engine_link._handle(2, {"type": "disconnected"})
        self.assertIsNone(engine_link.pending())

    def test_an_editor_asking_for_the_linked_asset_joins_and_close_ends_it(self):
        self.link()
        self.open(client=2)
        self.assertEqual(self.server.last(2)["type"], "opened")
        self.assertEqual(engine_link._state["dirty_at"], 0.0, "it gets the scene on the next tick")
        self.assertIsNone(engine_link.pending())
        engine_link._handle(2, {"type": "close", "id": "8", "project": str(self.project), "asset": "res://crate.glb"})
        self.assertIsNone(engine_link.session())
        self.assertIn({"type": "ended", "asset": "res://crate.glb", "reason": "engine"},
                      [header for _client, header in self.server.sent])

    def test_each_saved_names_the_live_scene_its_write_holds(self):
        """Saves made while a write-back runs: the running one keeps its scene, a newer one replaces the queued one
        (WriteBack.request), and each ``saved`` says which live scene it holds, so a live editor keeps a newer one."""
        asset = self.link()
        BPY.data.filepath = asset.editable
        saved = []
        for version, running in ((3, False), (5, True), (7, True)):
            engine_link._state["version"] = version
            self.writeback.running = running
            engine_link._save_post(asset.editable)
        engine_link._state["version"] = 9   # Edits after the last save.
        # The first runs; the third replaced the queued second (WriteBack.request): each ends with its own key.
        for key in (self.writeback.requests[0][0], self.writeback.requests[2][0]):
            self.writeback.done.append((key, None))
            with mock.patch.object(assets, "written_stamp"):
                engine_link._written()
            saved.append(self.server.last()["scene_version"])
        self.assertEqual(saved, [3, 7])

    def test_saving_a_linked_glb_writes_it_back_and_a_copy_does_not(self):
        asset = self.link()
        BPY.data.filepath = asset.editable
        engine_link._save_post("/tmp/checkpoint-copy.blend")
        self.assertEqual(self.writeback.requests, [], "a copy (a checkpoint, Save Copy) writes nothing")
        engine_link._save_post(asset.editable)
        key, blend, target, exporter, options = self.writeback.requests[0]
        self.assertEqual((blend, target), (asset.editable, asset.path), "from the saved file, in the background")
        self.assertEqual(exporter, "gltf")
        self.assertTrue(options["export_animations"], "the asset keeps its animations")
        self.assertFalse(any(header["type"] == "saved" for _client, header in self.server.sent), "not yet written")
        self.writeback.done.append((key, None))
        with mock.patch.object(assets, "written_stamp") as stamp:
            engine_link._written()
            stamp.assert_called_once_with(asset, key[1], key[2])   # The child's digest of this save, never a new one.
        self.assertEqual(self.server.last()["type"], "saved")
        BPY.data.filepath = str(self.project / "elsewhere.blend")
        engine_link._save_post(BPY.data.filepath)
        self.assertEqual(self.server.last()["reason"], "saved_elsewhere")

    def test_a_write_back_s_stamp_names_the_bytes_written_not_a_later_write(self):
        """Stat before read (release-1010 audit, re-check 2): the session once digested the asset again when the
        write-back ended, so a version written to it after the child's replace (the engine, another program) was
        stamped as Angelo's, and the next link opened the older working .blend as current for it."""
        asset = self.link()
        BPY.data.filepath = asset.editable
        Path(asset.editable).parent.mkdir(parents=True, exist_ok=True)
        Path(asset.editable).write_bytes(b"BLENDER working")
        engine_link._save_post(asset.editable)
        key = self.writeback.requests[0][0]
        # What the child does: write the asset, then stamp the digest of those bytes for this save and link.
        Path(asset.path).write_bytes(b"glb written by the write-back")
        written = assets.digest(asset.path)
        Path(assets.stamp_path(asset)).write_text(json.dumps({"sha256": written, "asset": asset.ident,
                                                             "save": key[1], "link": key[2], "writer": assets.WRITER}))
        Path(asset.path).write_bytes(b"glb saved by the engine after the replace")
        self.writeback.done.append((key, None))
        engine_link._written()
        self.assertEqual(assets.read_stamp(asset), written)
        self.assertFalse(assets.working_is_current(asset), "the engine's version is imported at the next link")
        self.assertEqual(engine_link._state["facts"]["save"]["sha256"], written, "the preflight names the save's bytes")
        self.assertEqual(self.server.last()["type"], "saved")

    def test_a_write_back_whose_stamp_names_another_save_records_no_digest(self):
        asset = self.link()
        BPY.data.filepath = asset.editable
        engine_link._save_post(asset.editable)
        key = self.writeback.requests[0][0]
        Path(assets.stamp_path(asset)).parent.mkdir(parents=True, exist_ok=True)
        theirs = {"sha256": "b" * 64, "asset": asset.ident, "save": key[1], "link": key[2], "writer": "another Angelo"}
        for stamp in ({"sha256": "b" * 64, "asset": asset.ident, "save": key[1] + 1, "link": key[2],
                       "writer": assets.WRITER},   # A newer one.
                      theirs,   # The same numbers, from a write-back child of an Angelo that quit while it ran.
                      {"sha256": "b" * 64, "asset": asset.ident}):   # An older one: this child could not write its own.
            with self.subTest(stamp=stamp):
                Path(assets.stamp_path(asset)).write_text(json.dumps(stamp))
                self.assertIsNone(assets.written_stamp(asset, key[1], key[2]))
        self.writeback.done.append((key, None))
        engine_link._written()
        self.assertNotIn("sha256", engine_link._state["facts"]["save"])
        self.assertEqual(json.loads(Path(assets.stamp_path(asset)).read_text())["sha256"], "b" * 64, "left as it is")

    def test_a_write_back_that_read_a_later_file_is_left_to_the_newer_save_or_said(self):
        """The child refuses a .blend that is not the save that asked (``engine_link_writeback.SUPERSEDED``): with a
        newer save's write-back on its way that one reports, else the editors hear that nothing was written."""
        from angelo_agent import engine_link_writeback
        asset = self.link()
        BPY.data.filepath = asset.editable
        Path(asset.editable).parent.mkdir(parents=True, exist_ok=True)
        Path(asset.editable).write_bytes(b"BLENDER as saved")
        engine_link._save_post(asset.editable)
        info = os.stat(asset.editable)
        saved = self.writeback.saved[-1]   # The save's stat: the write-back settles it before its child reads.
        self.assertEqual([saved.st_dev, saved.st_ino, saved.st_size, saved.st_mtime_ns],
                         [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])
        self.writeback.running = True   # A newer save's write-back runs.
        self.writeback.done.append((self.writeback.requests[-1][0], engine_link_writeback.SUPERSEDED))
        engine_link._written()
        self.assertFalse(any(header.get("code") == "write_failed" for _client, header in self.server.sent))
        self.writeback.running = False  # Nothing follows (another program wrote the file): said.
        engine_link._save_post(asset.editable)
        self.writeback.done.append((self.writeback.requests[-1][0], engine_link_writeback.SUPERSEDED))
        engine_link._written()
        self.assertEqual(self.server.last()["code"], "write_failed")
        self.assertIn("changed after Angelo saved it", self.server.last()["message"])

    def test_a_save_that_named_no_file_writes_nothing_and_says_why(self):
        """Release-1010 break-it (M6): a save that could not identify the file it wrote ran a child that always refused
        it as superseded, and the editors heard that the file changed. Now no child runs, and they hear why."""
        from angelo_agent import document_file_identity
        asset = self.link()
        BPY.data.filepath = asset.editable
        Path(asset.editable).parent.mkdir(parents=True, exist_ok=True)
        Path(asset.editable).write_bytes(b"BLENDER as saved")
        requests = len(self.writeback.requests)
        with mock.patch.object(document_file_identity, "written", lambda _path: None):
            engine_link._save_post(asset.editable)
        self.assertEqual(len(self.writeback.requests), requests, "no write-back child")
        self.assertEqual(self.server.last()["code"], "write_failed")
        self.assertIn("couldn't tell that the saved file is the one its save wrote", self.server.last()["message"])

    def test_a_preflight_names_the_blend_as_saved_not_a_later_write(self):
        """Stat before read (release-1010 re-check 2, L4): the saved .blend's identity was taken when the preflight was
        requested, after the write-back ended, so a version written to the working .blend meanwhile was checked and
        reported as the save."""
        engine_preflight_link = engine_link.engine_link_saves.engine_preflight_link   # The one the link calls.
        from angelo_agent import export_snapshot
        asset = self.link()
        BPY.data.filepath = asset.editable
        Path(asset.editable).parent.mkdir(parents=True, exist_ok=True)
        Path(asset.editable).write_bytes(b"BLENDER as the person saved it")
        saved = export_snapshot.identity(asset.editable)
        engine_link._save_post(asset.editable)
        key = self.writeback.requests[0][0]
        Path(asset.path).write_bytes(b"glb written by the write-back")
        temporary = Path(asset.editable + ".other")
        temporary.write_bytes(b"BLENDER written by another program, later")
        os.replace(temporary, asset.editable)
        specs = []
        runner = SimpleNamespace(request=lambda blend, spec: specs.append(spec))
        self.writeback.done.append((key, None))
        owner = threading.get_ident()
        settle, waited = export_snapshot.change_clock.settle, []

        def settle_original(witness, *args, **kwargs):
            if witness.st_ino == saved[1]:
                self.assertNotEqual(threading.get_ident(), owner, "the UI never waits out the save's coarse step")
                waited.append(witness)
            return settle(witness, *args, **kwargs)

        with mock.patch.object(BPY.app, "binary_path", sys.executable), \
                mock.patch.object(engine_preflight_link, "_runner", runner), \
                mock.patch.object(export_snapshot.change_clock, "settle", settle_original):
            engine_link._written()
            self.assertEqual(len(specs), 1, engine_link._state.get("error"))
            self.assertEqual(specs[0]["blend_witness"].st_ino, saved[1], "the save's witness, never the later path")
            self.assertEqual(waited, [], "request kept the raw saved witness")
            with mock.patch.object(engine_preflight_link.subprocess, "run",
                                   return_value=SimpleNamespace(returncode=0, stderr=b"")):
                worker = threading.Thread(target=engine_preflight_link.Runner(sys.executable)._run,
                                          args=(asset.editable, specs[0]))
                worker.start()
                worker.join(10)
                self.assertFalse(worker.is_alive())
        self.assertEqual(len(waited), 1, "the worker settled exactly the original save witness")
        self.assertEqual(specs[0]["blend_identity"], saved)
        self.assertNotEqual(specs[0]["blend_identity"], export_snapshot.identity(asset.editable))
        shutil.rmtree(specs[0]["folder"], ignore_errors=True)

    def test_an_imported_working_file_is_stamped_with_the_bytes_the_import_read(self):
        """The import's stamp is taken before the import reads the asset: a write during the import makes the next
        link import again, never open this working .blend as current for the newer file."""
        with mock.patch.dict(sys.modules, {"bpy": BPY}):
            from angelo_agent import engine_link_import
        asset = godot_link_assets.resolve({"project": str(self.project), "asset": "res://crate.glb"})
        Path(asset.path).write_bytes(b"glb as the import reads it")
        read = assets.digest(asset.path)

        def import_file(path, kind):
            self.assertEqual(Path(path).read_bytes(), b"glb as the import reads it")
            Path(path).write_bytes(b"glb the engine saved during the import")
            return {"FINISHED"}

        def save_as_mainfile(filepath, **_options):
            Path(filepath).write_bytes(b"BLENDER working")
            return {"FINISHED"}
        stand_in = SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(
            read_homefile=lambda **_options: {"FINISHED"}, save_as_mainfile=save_as_mainfile)))
        with mock.patch.object(engine_link_import, "bpy", stand_in), \
                mock.patch.object(engine_link_import, "import_file", import_file), \
                mock.patch("angelo_agent.gltf_checks.check_file", lambda *_args, **_kwargs: None):
            self.assertEqual(engine_link_import.working_file(asset, lambda _on: None), {"FINISHED"})
        self.assertEqual(assets.read_stamp(asset), read)
        self.assertFalse(assets.working_is_current(asset))

    def test_a_glb_save_sends_current_baked_materials_and_says_which_are_stale(self):
        asset = self.link()
        BPY.data.filepath = asset.editable
        material = SimpleNamespace(name="Crate Baked", library=None)
        crate = SimpleNamespace(name="Crate", session_uid=7, library=SimpleNamespace(filepath="//props.blend"))
        plan = {"substitutes": {7: material}, "used": [("Crate", "Crate Baked")], "stale": ["Barrel", "Rock", "Vase"],
                "nearby": ["Rock"], "packing": [("Hull", "UNITY")], "updated": ["Vase"]}
        scene = SimpleNamespace(objects=[crate, SimpleNamespace(name="Crate", session_uid=8, library=None)])
        with mock.patch.object(assets, "baked_plan", return_value=plan) as planned, \
                mock.patch.dict(sys.modules, {"bpy": SimpleNamespace(context=SimpleNamespace(scene=scene))}):
            engine_link._save_post(asset.editable)
        planned.assert_called_once_with(None)
        # By name and library: a linked Crate is the planned one, never the local Crate of the same name.
        self.assertEqual(self.writeback.baked, [[["Crate", "Crate Baked", "//props.blend", None]]])
        said = {header["code"]: header["message"] for _client, header in self.server.sent if header["type"] == "status"}
        self.assertEqual(set(said), {"baked_stale", "baked_stale_nearby", "baked_packing", "baked_stale_update"})
        self.assertIn("Vase", said["baked_stale_update"])   # Bound before this update: said once, as its doing.
        self.assertIn("before this Angelo update", said["baked_stale_update"])
        self.assertNotIn("Vase", said["baked_stale"])
        self.assertIn("Barrel", said["baked_stale"])
        self.assertNotIn("Rock", said["baked_stale"])
        self.assertIn("Rock", said["baked_stale_nearby"])
        self.assertIn("ambient occlusion", said["baked_stale_nearby"])
        self.assertIn("Hull", said["baked_packing"])
        with mock.patch.object(assets, "baked_plan", side_effect=RuntimeError("no depsgraph")):
            engine_link._save_post(asset.editable)   # A plan that fails still writes the asset, own materials.
        self.assertEqual((len(self.writeback.requests), self.writeback.baked[-1]), (2, []))

    def test_a_failed_write_back_says_so_and_keeps_the_link(self):
        asset = self.link()
        BPY.data.filepath = asset.editable
        engine_link._save_post(asset.editable)
        self.writeback.done.append((self.writeback.requests[-1][0], "RuntimeError: disk full"))
        engine_link._written()
        self.assertEqual(self.server.last()["code"], "write_failed")
        self.assertIsNotNone(engine_link.session())

    def test_live_exports_never_change_the_frame(self):
        options = godot_link_assets.live_options({})
        self.assertEqual((options["export_animations"], options["export_current_frame"]), (False, True))

    def test_a_card_answers_only_its_own_request(self):
        self.open()
        first = engine_link.pending()
        self.open(client=2, asset="res://other.glb")
        self.assertIsNone(engine_link.accept(None, first), "a newer request is never opened by the older card")
        self.assertIsNotNone(engine_link.pending())

    def test_saving_a_linked_blend_only_tells_godot(self):
        asset = self.link("res://crate.blend")
        with mock.patch.object(assets, "export") as export:
            engine_link._save_post(asset.path)
            export.assert_not_called()
        self.assertEqual(self.server.last(), {"type": "saved", "asset": "res://crate.blend",
                                              "files": ["res://crate.blend"], "scene_version": 0, "save": 1})

    def test_opening_another_file_ends_the_link(self):
        asset = self.link()
        BPY.data.filepath = asset.editable
        engine_link._load_post()
        self.assertIsNotNone(engine_link.session(), "reloading the same file keeps it")
        BPY.data.filepath = str(self.project / "unrelated.blend")
        engine_link._load_post()
        self.assertIsNone(engine_link.session())
        self.assertEqual(self.server.last()["reason"], "other_file")

    def test_the_indicator_counts_only_the_linked_projects_editors(self):
        self.assertEqual(engine_link.editors(), 0, "nothing linked")
        self.link()
        self.assertEqual(engine_link.editors(), 2)
        self.server.info[2]["project"] = str(self.project.parent)
        self.assertEqual(engine_link.editors(), 1)
        self.server.info.clear()   # Godot closed: Angelo's status says it isn't connected.
        self.assertEqual(engine_link.editors(), 0)

    def test_other_projects_editors_never_get_the_asset(self):
        self.server.info[2]["project"] = str(self.project.parent)
        self.link()
        engine_link._end("angelo")
        self.assertEqual([client for client, header in self.server.sent if header["type"] == "ended"], [1])

    def test_the_main_thread_matches_an_editors_project_without_reading_a_folder(self):
        """``serves`` runs on the main thread for every acknowledgement, close and broadcast; ``os.path.realpath``
        there held one tick 5 to 10 s on a stalled drive (Mac 2, 1.0.9). The editor's project was resolved when it
        authenticated (``engine_link_server.project_key``)."""
        self.link()
        with mock.patch.object(assets.os.path, "realpath", side_effect=AssertionError("a folder read, main thread")):
            self.assertEqual(engine_link.editors(), 2)
            engine_link._handle(1, {"type": "status", "asset": "res://crate.glb", "code": "previewed", "version": 1})
            self.assertIn(1, engine_link._state["facts"]["editors"])   # Its acknowledgement was kept.
            engine_link._handle(2, {"type": "close", "id": "8", "asset": "res://crate.glb"})
        self.assertIsNone(engine_link.session())
        self.assertEqual(sorted(client for client, header in self.server.sent if header["type"] == "ended"), [1, 2])


class EngineRoutingTests(unittest.TestCase):
    """Requests from several engines on one listener (each named its engine when it authenticated)."""

    open = LinkStateTests.open

    def setUp(self):
        LinkStateTests.setUp(self)
        (self.project / "crate.fbx").write_bytes(b"x")
        self.calls = []
        test = self

        class StandIn:   # A second engine's adapter: admits any file it is asked for, writes .fbx back.
            ENGINE, DISPLAY, CAPABILITIES = "standin", "Stand-in Engine", ("durable",)

            @staticmethod
            def resolve(request):
                test.calls.append(("resolve", request["asset"]))
                path = test.project / request["source"]
                return assets.Asset("standin", str(test.project), request["asset"], str(path), path.suffix,
                                    request["source"])

            @staticmethod
            def import_params(asset):
                return {"scale": 2}

            @staticmethod
            def live_options(params):
                return {}

            @staticmethod
            def durable(asset, params):
                return "fbx", {"params": params}

        adapters = self.adapters = {"godot": godot_link_assets, "standin": StandIn}
        patcher = mock.patch.object(assets, "adapter", side_effect=adapters.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server.info[2]["engine"] = "standin"

    def test_an_engine_without_an_adapter_is_refused(self):
        self.server.info[2]["engine"] = "elsewhere"
        self.open(client=2)
        self.assertEqual((self.server.last(2)["type"], self.server.last(2)["code"]), ("refused", "engine"))
        self.assertIn("elsewhere", self.server.last(2)["message"])
        self.assertIsNone(engine_link.pending())

    def test_the_asking_editors_engine_resolves_the_request(self):
        self.open(client=2, asset="/Game/Crate", source="crate.fbx")
        self.assertEqual(self.calls, [("resolve", "/Game/Crate")])
        request = engine_link.pending()
        self.assertEqual((request["asset"].engine, request["live"]), ("standin", False), "its adapter's default")
        self.assertEqual(self.server.last(2), {"type": "pending", "id": "7", "asset": "/Game/Crate"})
        self.open(client=1)   # Godot's editor, same listener: Godot's adapter.
        self.assertEqual((engine_link.pending()["asset"].engine, engine_link.pending()["live"]), ("godot", True))

    def test_close_ends_the_link_by_name_without_resolving_the_file_again(self):
        engine_link._state["session"] = {"asset": self.standin(), "params": {}}
        engine_link._handle(1, {"type": "close", "id": "8", "asset": "/Game/Crate"})   # Godot's editor: not its asset.
        self.assertIsNotNone(engine_link.session())
        engine_link._handle(2, {"type": "close", "id": "9", "asset": "/Game/Crate"})   # No source, no project.
        self.assertIsNone(engine_link.session())
        self.assertEqual(self.calls, [], "nothing is resolved for a close")
        self.assertIn({"type": "closed", "id": "9", "asset": "/Game/Crate"}, [h for c, h in self.server.sent if c == 2])

    def test_an_editor_that_says_what_it_shows_is_believed(self):
        self.server.info[1]["capabilities"] = ["durable"]
        self.open(client=1)
        self.assertFalse(engine_link.pending()["live"])

    def test_saving_uses_the_engines_exporter_and_tells_only_its_editors(self):
        engine_link._state["opening"] = {"asset": self.standin(), "client": 2, "id": "7"}
        engine_link._begin(None)
        asset = engine_link.session()
        self.assertEqual(engine_link._state["session"]["params"], {"scale": 2})
        self.assertEqual((engine_link.editors(), engine_link.editors(live=True)), (1, 0), "its editor saves only")
        BPY.data.filepath = asset.editable
        engine_link._save_post(asset.editable)
        self.assertEqual(self.writeback.requests[0][2:], (asset.path, "fbx", {"params": {"scale": 2}}))
        self.assertEqual(self.writeback.baked, [[]], "FBX binding is the engines' (no baked material is assigned)")
        self.server.sent.clear()
        self.writeback.done.append((self.writeback.requests[0][0], None))
        with mock.patch.object(assets, "written_stamp"):
            engine_link._written()
        self.assertEqual([(client, header["type"]) for client, header in self.server.sent], [(2, "saved")],
                         "Godot's editor of the same folder is another engine's")

    def test_live_scenes_go_only_to_editors_that_show_them(self):
        engine_link._state["session"] = {"asset": self.standin(), "params": {}}
        engine_link._state["folder"] = str(self.project)
        with mock.patch.object(assets, "export") as export:
            self.assertEqual(engine_link.publish(None), 0)
            export.assert_not_called()   # No editor of the stand-in shows live scenes: nothing is exported.
        self.server.info[2]["capabilities"] = ["durable", "live"]

        def write(window, path, options, export_id=assets.EXPORT_ID):
            Path(path).write_bytes(b"glb")
        with mock.patch.object(assets, "export", side_effect=write), \
                mock.patch.object(engine_link, "_followed", return_value=(None, None)), \
                mock.patch.object(engine_link.scene_changes, "scene_key", return_value=None):
            self.assertEqual(engine_link.publish(None), 3)
        self.assertEqual([client for client, header in self.server.sent if header["type"] == "scene"], [2])

    def test_a_web_pages_live_scene_is_exported_with_node_identity(self):
        """The web adapter makes its own frames (web_link_live): the first one is whole, exported under its
        ``LIVE_EXPORT_ID`` (the glTF hooks that key every node), and says so."""
        from angelo_agent import web_link_assets, web_link_live
        (self.project / "package.json").write_text("{}", encoding="utf-8")
        (self.project / "a.glb").write_bytes(b"x")
        asset = assets.Asset("web", str(self.project), "a.glb", str(self.project / "a.glb"), ".glb", "a.glb")
        engine_link._state["session"] = {"asset": asset, "params": {}, "number": 7}
        engine_link._state["folder"] = str(self.project)
        self.server.info[2] = {"engine": "web", "project": str(self.project),
                               "project_key": os.path.normcase(str(self.project)),   # As the server keys it.
                               "capabilities": ["durable", "live", "capture"]}
        used = []

        def write(window, path, options, export_id=assets.EXPORT_ID):   # What the exporter and its hooks write.
            used.append(export_id)
            web_link_live._export.update(filtered=True, marked=True, objects={5}, owners={"5": 5})
            node = {"name": "Chair", "extras": {web_link_live.KEY: "5", web_link_live.ROLE: web_link_live.CONTENT}}
            Path(path).write_bytes(web_link_live.write_glb({"asset": {"version": "2.0"}, "nodes": [node]}, b""))
        with mock.patch.object(assets, "export", side_effect=write), \
                mock.patch.dict(self.adapters, {"web": web_link_assets}), \
                mock.patch.object(web_link_live, "image_changes", lambda scene=None: set()), \
                mock.patch.object(engine_link, "_followed", return_value=(None, None)), \
                mock.patch.object(engine_link.scene_changes, "scene_key", return_value=None):
            self.assertGreater(engine_link.publish(None), 0, engine_link._state["error"])
        self.assertEqual(used, [web_link_assets.LIVE_EXPORT_ID])
        [header] = [header for client, header in self.server.sent if header["type"] == "scene"]
        self.assertEqual((header["kind"], header["asset"]), ("full", "a.glb"))

    def test_a_web_link_asks_when_to_publish_only_while_nothing_is_pending(self):
        """``web_link_live.due``: pixels written with no depsgraph update, or a due still-scene check, start a
        publish (break-it round 8, M1; round 9, M1), and only while no edit waits."""
        web = assets.Asset("web", str(self.project), "a.glb", str(self.project / "a.glb"), ".glb", "a.glb")
        self.server.info[2] = {"engine": "web", "project": str(self.project),
                               "project_key": os.path.normcase(str(self.project)),   # As the server keys it.
                               "capabilities": ["durable", "live"]}
        for asset, due, pending, expected in ((web, 7.0, None, 7.0), (web, 0.0, None, 0.0), (web, None, None, None),
                                              (web, 7.0, -1.0, -1.0), (self.standin(), 7.0, None, None)):
            with self.subTest(engine=asset.engine, due=due, pending=pending):
                engine_link._state.update(session={"asset": asset, "params": {}}, dirty_at=pending)
                with mock.patch.object(engine_link.web_link_live, "due", return_value=due) as asked, \
                        mock.patch.object(engine_link, "_window", return_value=None), \
                        mock.patch.object(engine_link, "_followed", return_value=(None, None)), \
                        mock.patch.object(engine_link.cadence, "due", return_value=False):
                    engine_link._tick()
                self.assertEqual(engine_link._state["dirty_at"], expected)
                self.assertEqual(asked.called, asset is web and pending is None)

    def standin(self):
        return assets.Asset("standin", str(self.project), "/Game/Crate", str(self.project / "crate.fbx"), ".fbx",
                            "crate.fbx")


class InstallTests(unittest.TestCase):
    def test_the_plugin_installs_and_replaces_an_older_copy(self):
        project = Path(tempfile.mkdtemp())
        (project / "project.godot").write_text("config_version=5\n")
        old = project / "addons/angelo_link"
        old.mkdir(parents=True)
        (old / "stale.gd").write_text("old")
        target = Path(assets.install_plugin(godot_link_assets, str(project)))
        self.assertEqual(sorted(path.name for path in target.iterdir()),
                         sorted(path.name for path in godot_link_assets.PLUGIN_SOURCE.iterdir()
                                if path.suffix != ".import"))
        self.assertFalse((target / "stale.gd").exists())
        self.assertEqual([path.name for path in (project / "addons").iterdir()], ["angelo_link"], "no staging left")
        with self.assertRaises(assets.AssetError):
            assets.install_plugin(godot_link_assets, str(project / "addons"))


if __name__ == "__main__":
    unittest.main()
