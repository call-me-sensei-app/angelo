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
the shipped plugin installs into a project and replaces an older copy.
"""

import os
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


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    handlers = SimpleNamespace(persistent=lambda function: function, depsgraph_update_post=[], load_post=[],
                               save_post=[], exit_pre=[])
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
with mock.patch.dict(sys.modules, {"bpy": BPY}):
    from angelo_agent import engine_link, engine_link_assets as assets, godot_link_assets  # noqa: E402
# patch.dict restores sys.modules on leaving; an already imported package keeps the attributes, so drop the ones bound
# to the stand-in bpy too: no later test reuses them.
# The adapters hold no bpy: keep the modules imported above, so the link's adapter lookup returns these same ones.
sys.modules.setdefault("angelo_agent.engine_link_assets", assets)
sys.modules.setdefault("angelo_agent.godot_link_assets", godot_link_assets)
for _name in ("engine_link", "engine_link_import", "scene_changes"):
    _package = sys.modules.get("angelo_agent")
    if _package is not None and getattr(getattr(_package, _name, None), "bpy", None) is BPY:
        delattr(_package, _name)


class FakeServer:
    def __init__(self):
        self.sent, self.items = [], []
        self.info = {1: {"project": ""}, 2: {"project": ""}}

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
        self.requests, self.done, self.running = [], [], False

    def request(self, key, blend, target, exporter, options):
        self.requests.append((key, blend, target, exporter, options))

    def busy(self):
        return self.running

    def finished(self):
        items, self.done = self.done, []
        return items


class LinkStateTests(unittest.TestCase):
    def setUp(self):
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
                                 writeback=self.writeback)
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
        for _ in range(2):
            self.writeback.done.append((asset.path, None))
            with mock.patch.object(assets, "write_stamp"):
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
        self.writeback.done.append((asset.path, None))
        with mock.patch.object(assets, "write_stamp") as stamp:
            engine_link._written()
            stamp.assert_called_once_with(asset)
        self.assertEqual(self.server.last()["type"], "saved")
        BPY.data.filepath = str(self.project / "elsewhere.blend")
        engine_link._save_post(BPY.data.filepath)
        self.assertEqual(self.server.last()["reason"], "saved_elsewhere")

    def test_a_failed_write_back_says_so_and_keeps_the_link(self):
        asset = self.link()
        self.writeback.done.append((asset.path, "RuntimeError: disk full"))
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
                                              "files": ["res://crate.blend"], "scene_version": 0})

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

        adapters = {"godot": godot_link_assets, "standin": StandIn}
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
        self.server.sent.clear()
        self.writeback.done.append((asset.path, None))
        with mock.patch.object(assets, "write_stamp"):
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

        def write(window, path, options):
            Path(path).write_bytes(b"glb")
        with mock.patch.object(assets, "export", side_effect=write), \
                mock.patch.object(engine_link, "_followed", return_value=(None, None)), \
                mock.patch.object(engine_link.scene_changes, "scene_key", return_value=None):
            self.assertEqual(engine_link.publish(None), 3)
        self.assertEqual([client for client, header in self.server.sent if header["type"] == "scene"], [2])

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
