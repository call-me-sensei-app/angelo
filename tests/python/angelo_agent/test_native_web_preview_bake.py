# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A page (re)loaded while the World's picture is being made gets the lighting the open page shows
(doc/angelo/web_export.rst, "Waiting for it" and "The extras").

The live preview never waits for the World's picture: it exports at once with the World marked pending. The open page
keeps the World it shows, but a page loaded then (a browser reload, a second tab, a preview started on such a World)
had nothing to keep, so a scene lit only by its World showed black until the bake ended (1.0.9, a Sky World reloaded
mid-bake). Every export made during the bake now carries the World the page shows, marked pending, for every form of
World; before the preview has shown one, ``{"type": "pending"}`` and the page lights the scene with the studio.

Dependency-free: ``web_preview.refresh`` and ``web_export.export`` run for real against a stand-in bpy, with the
scene's reading and the glTF exporter stood in for, and the page reads the file over HTTP from a fresh connection, as
a reloaded browser does.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import http.client
import importlib
import json
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import web_budget, web_glb, web_look  # noqa: E402
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402

LOOK = {"view_transform": "AgX", "look": None, "exposure": 0.0, "gamma": 1.0, "display": "sRGB",
        "tone_mapping": "AgXToneMapping", "output": "srgb", "difference": 0.0}
REQUEST = {"meshes": "NONE", "textures": "WEBP", "merge": True, "selection_only": False}


def empty_glb():
    text = json.dumps({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": []}]}).encode("utf-8")
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def environment_of(data):
    """The file's ``angelo_look.environment`` as a page reads it, its bufferView replaced by that view's bytes."""
    document, binary = web_budget.read_glb(data)
    environment = document["scenes"][document.get("scene", 0)]["extras"]["angelo_look"]["environment"]
    if environment and isinstance(environment.get("bufferView"), int):
        view = document["bufferViews"][environment["bufferView"]]
        start = view.get("byteOffset", 0)
        environment = dict(environment, bufferView=bytes(binary[start:start + view["byteLength"]]))
    return environment


def stand_in_bpy():
    bpy = mock.MagicMock(name="bpy")
    bpy.app.handlers.persistent = lambda function: function
    return {"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types, "bpy.app": bpy.app,
            "bpy.app.handlers": bpy.app.handlers, "bpy.app.translations": bpy.app.translations, "_bpy": ModuleType("_bpy"),
            "mathutils": mock.MagicMock(name="mathutils"), "gpu": mock.MagicMock(name="gpu")}


def import_preview():
    """``angelo_agent.web_preview`` imported against a stand-in bpy, and the undo that leaves sys.modules and the
    add-on package as they were (another test's import never sees these modules)."""
    package = importlib.import_module("angelo_agent")
    before = dict(vars(package))
    with mock.patch.dict(sys.modules, stand_in_bpy()):
        for name in [name for name in sys.modules if name.startswith("angelo_agent.")]:
            del sys.modules[name]   # Restored on leaving: every module imported here runs against the stand-in.
        preview = importlib.import_module("angelo_agent.web_preview")

    def undo():
        for name in [name for name in vars(package) if name not in before]:
            delattr(package, name)
        for name, value in before.items():
            setattr(package, name, value)
    return preview, undo


def job(status, data=b""):
    return SimpleNamespace(status=status, data=data, width=8 if data else 0, height=4 if data else 0, error="",
                           ms=1.0)


class ReloadDuringBakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preview, undo = import_preview()
        cls.addClassCleanup(undo)
        cls.export = cls.preview.web_export

    def setUp(self):
        self.server = PreviewServer()
        self.addCleanup(self.server.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state = dict(self.preview._state, server=self.server, folder=folder.name, request=dict(REQUEST), shown={},
                     strings={}, exports=0)
        self.world, self.job = {"kind": "NONE", "name": None, "background": True}, job("ready")
        preview, export = self.preview, self.export
        for patch in (
                mock.patch.dict(preview._state, state),
                mock.patch.object(export, "_inspect", lambda *args: (
                    dict(REQUEST, baked_materials=False), [], {"world": dict(self.world), "look": LOOK}, [],
                    {"used": [], "stale": [], "packing": []})),
                mock.patch.object(export, "_write", self.write),
                mock.patch.object(export.web_environment, "ensure", lambda *args, **keywords: self.job),
                mock.patch.object(export.web_look, "_config_path", lambda: ""),
                mock.patch.object(preview, "_exported_materials", lambda window: []),
                mock.patch.object(preview, "_shader_state", lambda: {}),
                mock.patch.object(preview.scene_changes, "scene_key", lambda *args: None),
                mock.patch.object(preview, "scene_key", lambda *args: None)):   # The scene's reading is stood in.
            patch.start()
            self.addCleanup(patch.stop)
        self.window = SimpleNamespace(scene=SimpleNamespace(), view_layer=SimpleNamespace())

    @staticmethod
    def write(window, scene, view_layer, path, settings, baked, extras=None):
        """The glTF exporter's part: an empty scene with the extras, their bytes stored as bufferViews."""
        with open(path, "wb") as handle:
            handle.write(web_glb.add_scene_extras(empty_glb(), extras))
        return {"objects": 0, "groups": 0}

    def get(self, name):
        """``name`` from the server over a new connection: a page loading now (a reload, another tab)."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        connection.request("GET", "/%s/%s" % (self.server.token, name),
                           headers={"Host": "127.0.0.1:%d" % self.server.port})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        return body

    def export_now(self, world, status, data=b""):
        self.world, self.job = world, job(status, data)
        report = self.preview.refresh(self.window)
        self.assertNotEqual(report.get("status"), "failed", report)
        return report

    def page(self):
        """(the environment a freshly loaded page gets, the World's state its report column shows)."""
        report = json.loads(self.get("report.json"))
        return environment_of(self.get("scene.glb")), report["settings"]["environment"]

    def test_a_page_loaded_during_a_bake_gets_the_last_picture_for_every_picture_world(self):
        for kind in ("BAKE", "IMAGE"):   # A Sky (or any baked World) and an HDRI.
            with self.subTest(kind=kind):
                self.preview._state["shown"] = {}
                world = {"kind": kind, "name": "World", "background": True, "intensity": 1.0, "key": kind}
                self.export_now(world, "ready", b"#?RADIANCE first picture")
                self.export_now(dict(world, key=kind + " edited"), "pending")   # An edit: a new picture is made.
                environment, state = self.page()
                self.assertIsNotNone(environment, "a page loaded during the bake would light the scene with nothing")
                self.assertEqual({key: environment.get(key) for key in ("type", "bufferView", "pending")},
                                 {"type": "equirect", "bufferView": b"#?RADIANCE first picture", "pending": True})
                self.assertEqual(state["status"], "pending")   # The column says it is being made.
                self.export_now(dict(world, key=kind + " edited"), "pending")   # Another change meanwhile.
                self.assertEqual(self.page()[0]["bufferView"], b"#?RADIANCE first picture")
                self.export_now(dict(world, key=kind + " edited"), "ready", b"#?RADIANCE second picture")
                environment = self.page()[0]
                self.assertEqual((environment["bufferView"], "pending" in environment),
                                 (b"#?RADIANCE second picture", False))

    def test_a_colour_world_shown_before_stands_in_for_the_picture(self):
        self.export_now({"kind": "COLOR", "name": "World", "background": True, "color": [0.2, 0.4, 0.8],
                         "intensity": 1.5}, "ready")
        self.export_now({"kind": "BAKE", "name": "World", "background": True, "intensity": 1.0, "key": "sky"},
                        "pending")   # The person turned the colour into a Sky.
        self.assertEqual(self.page()[0], {"type": "color", "color": [0.2, 0.4, 0.8], "intensity": 1.5,
                                          "pending": True})

    def test_before_any_world_is_shown_the_page_is_told_it_is_pending(self):
        sky = {"kind": "BAKE", "name": "World", "background": True, "intensity": 1.0, "key": "sky"}
        self.export_now(sky, "pending")   # The preview started on a Sky: its first picture.
        self.assertEqual(self.page()[0], {"type": "pending"})
        self.export_now({"kind": "NONE", "name": None, "background": True}, "ready")
        self.export_now(sky, "pending")   # After a World with no light: nothing to stand in.
        self.assertEqual(self.page()[0], {"type": "pending"})
        self.export_now(sky, "failed")    # A picture that couldn't be made: none, with a finding.
        self.export_now(sky, "pending")
        self.assertEqual(self.page()[0], {"type": "pending"})

    def test_a_stopped_preview_forgets_what_its_page_showed(self):
        self.export_now({"kind": "COLOR", "name": "World", "background": True, "color": [1, 1, 1], "intensity": 1},
                        "ready")
        self.assertTrue(self.preview._state["shown"])
        with mock.patch.object(self.preview.bpy.app.timers, "is_registered", lambda function: False):
            self.assertTrue(self.preview.stop())
        self.assertEqual(self.preview._state["shown"], {})

    def test_an_explicit_export_never_carries_a_stand_in(self):
        """Only the live preview's ``shown`` stands in; an explicit export written after its wait has no page."""
        extras = web_look.extras(LOOK, {"kind": "BAKE", "status": "pending"}, None)["angelo_look"]
        self.assertEqual(extras["environment"], {"type": "pending"})


class PageTests(unittest.TestCase):
    """The page's side (``web_preview/look.js``), read as text: a browser check is in the document's validation."""

    def setUp(self):
        self.look = (ADDON / "web_preview/look.js").read_text(encoding="utf-8")

    def method(self, name):
        found = re.search(r"\n  (?:async )?%s\([^)]*\) \{\n(.*?)\n  \}\n" % name, self.look, re.S)
        self.assertIsNotNone(found, name)
        return found.group(1)

    def test_a_stand_in_is_applied_as_any_world(self):
        apply = self.method("apply")
        self.assertIn('environment.pending === true', apply)
        # Only a bare {"type": "pending"} keeps the World shown now; a stand-in reaches the code that lights with it.
        self.assertRegex(apply, r'if \(environment && environment\.type === "pending"\) \{ this\.update\(\); return; \}')
        self.assertNotRegex(apply, r'environment\.pending\)? \{ this\.update\(\); return; \}')

    def test_nothing_to_light_with_while_waiting_is_the_studio_never_nothing(self):
        lighting = self.method("lighting")
        self.assertIn('!this.world && this.waiting ? "studio"', lighting)
        self.assertIn("const light = this.lighting();", self.method("update"))
        self.assertIn("light: this.lighting(), pending: this.waiting", self.method("report"))


if __name__ == "__main__":
    unittest.main()
