# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link's live export follows every scene setting its glTF exporter reads, on a stand-in ``bpy``
(doc/angelo/engine_link.rst, "Live scenes"; ``gltf_scene_reads``).

An edit of the scene's own settings (its name, the render size a camera's aspect comes from, the engine its lights
convert for, the custom properties Godot asks for as extras, the active collection Godot can export alone) updates
only the scene, which ``scene_changes.content`` ignores; 1.0.10's link kept only the frame, camera, World and visible
names in its key, so the engine's editor waited for an unrelated edit. Each adapter's own live options decide the
edits (Godot with its defaults and with the .import settings that make it read more, Unity, Unreal, the web), never
a list of cases, and what those options never read (the frame rate of a live export without animation) schedules
nothing. ``test_blender_engine_link_key`` holds the same contract with Blender's own exporter.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy  # noqa: F401  # Before the stand-in block, which drops what is first imported inside it.

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from addon_stand_in_import import import_against_stand_ins  # noqa: E402


class Scene:
    pass


class Object:
    pass


def fake_bpy():
    bpy = types.ModuleType("bpy")
    classes = {"Scene": Scene, "Object": Object}
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: classes.setdefault(name, type(name, (), {}))
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    handlers = SimpleNamespace(persistent=lambda function: function, depsgraph_update_post=[], load_post=[],
                               save_post=[], exit_pre=[], undo_post=[], redo_post=[])
    timers = SimpleNamespace(register=lambda *a, **k: None, unregister=lambda f: None, is_registered=lambda f: False)
    bpy.app = SimpleNamespace(handlers=handlers, timers=timers, background=True, binary_path="/Apps/Angelo",
                              translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text,
                                                           locale="en_US"))
    bpy.data = SimpleNamespace(filepath="", is_dirty=False)
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[]))
    bpy.utils = SimpleNamespace(system_resource=lambda *a, **k: "")
    return bpy


BPY = fake_bpy()
ADAPTERS = ("godot_link_assets", "unity_link_assets", "unreal_link_assets", "web_link_assets")
(engine_link, assets, gltf_scene_reads, scene_changes, *ADAPTER_MODULES) = import_against_stand_ins(
    BPY, ("engine_link", "engine_link_assets", "gltf_scene_reads", "scene_changes") + ADAPTERS)
BY_ENGINE = {module.ENGINE: module for module in ADAPTER_MODULES}
# (engine, the asset's import params): each adapter, and Godot with the .import settings that make it read more.
LINKS = (("godot", {}), ("godot", {"blender/nodes/active_collection_only": True, "blender/nodes/visible": 2}),
         ("unity", {}), ("unreal", {}), ("web", {}))


def stand_in_scene():
    scene = Scene()
    scene.session_uid, scene.name, scene.is_nla_tweakmode, scene.angelo_physics_format = 7, "Scene", False, "OMI"
    scene.frame_current, scene.frame_start, scene.frame_end = 1, 1, 250
    scene.world, scene.camera, scene.custom = None, None, {"level": 1}
    scene.render = SimpleNamespace(engine="BLENDER_EEVEE", fps=24, fps_base=1.0, resolution_x=1920,
                                   resolution_y=1080, pixel_aspect_x=1.0, pixel_aspect_y=1.0)
    objects = []
    for name in ("Cube", "Lamp"):
        obj = Object()
        obj.name, obj.hide_render, obj.visible_get = name, False, (lambda view_layer=None: True)
        objects.append(obj)
    collections = (SimpleNamespace(collection="Props"), SimpleNamespace(collection="Lights"))
    layer = SimpleNamespace(objects=objects, active_layer_collection=collections[0], collections=collections)
    return scene, layer


def update(datablock, geometry=False, transform=False, shading=False):
    return SimpleNamespace(id=datablock, is_updated_geometry=geometry, is_updated_transform=transform,
                           is_updated_shading=shading)


def edits(options):
    """Every scene setting the exporter reads with ``options`` (``gltf_scene_reads``), as (name, edit) pairs, and the
    ones it never reads with them."""
    reads, on = gltf_scene_reads, lambda name: options.get(name, gltf_scene_reads.DEFAULTS[name])
    read, unread = [], []
    groups = ((True, (), reads.SCENE_FIELDS), (on("export_lights"), reads.LIGHT_FIELDS, ()),
              (on("export_cameras"), reads.CAMERA_FIELDS, ()),
              (on("export_animations"), reads.ANIMATION_RENDER_FIELDS, reads.ANIMATION_SCENE_FIELDS))
    for used, render_fields, scene_fields in groups:
        target = read if used else unread
        target += [("render." + name, lambda scene, layer, name=name: bump(scene.render, name))
                   for name in render_fields]
        target += [(name, lambda scene, layer, name=name: bump(scene, name)) for name in scene_fields]
    (read if on("export_extras") else unread).append(
        ("extras", lambda scene, layer: scene.custom.update(level=scene.custom["level"] + 1)))
    (read if on("use_active_collection") else unread).append(
        ("active_collection", lambda scene, layer: setattr(layer, "active_layer_collection", layer.collections[1])))
    return read, unread


def bump(owner, name):
    value = getattr(owner, name)
    setattr(owner, name, (not value) if isinstance(value, bool) else value + 1 if isinstance(value, (int, float))
            else value + "2")


class EngineLinkKeyTests(unittest.TestCase):
    def link(self, engine, params):
        scene, layer = stand_in_scene()
        self.scene, self.layer = scene, layer
        patches = (mock.patch.object(scene_changes, "bpy", BPY), mock.patch.object(engine_link, "bpy", BPY),
                   mock.patch.object(engine_link, "_window", return_value=None),
                   mock.patch.object(engine_link, "_followed", return_value=(scene, layer)),
                   mock.patch.object(assets, "adapter", BY_ENGINE.get),
                   mock.patch.object(gltf_scene_reads, "extras", lambda scene: dict(scene.custom)),
                   mock.patch.dict(engine_link._state, session={"asset": SimpleNamespace(engine=engine),
                                                                "params": params},
                                   exporting=False, dirty_at=None, scene=None))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.notify()   # The first update records the key, as the link's first export does.
        return BY_ENGINE[engine].live_options(params)

    def notify(self, *updates):
        """Blender's depsgraph_update_post with ``updates``; True when the link scheduled a live export."""
        engine_link._state["dirty_at"] = None
        with mock.patch.object(engine_link.web_link_live, "note", lambda depsgraph: None):
            engine_link._on_depsgraph(self.scene, SimpleNamespace(updates=list(updates) or [update(self.scene)]))
        return engine_link._state["dirty_at"] is not None

    def test_every_setting_the_live_export_reads_schedules_one(self):
        for engine, params in LINKS:
            with self.subTest(engine=engine, params=params):
                options = self.link(engine, params)
                read, _unread = edits(options)
                self.assertTrue(read)
                for name, edit in read:
                    edit(self.scene, self.layer)
                    self.assertTrue(self.notify(), "%s %s" % (engine, name))
                    self.assertFalse(self.notify(), "%s %s again: one export per edit" % (engine, name))

    def test_what_the_live_export_never_reads_schedules_nothing(self):
        for engine, params in LINKS:
            with self.subTest(engine=engine, params=params):
                options = self.link(engine, params)
                _read, unread = edits(options)
                self.assertIn("render.fps", [name for name, _edit in unread])   # A live export has no animation.
                for name, edit in unread:
                    edit(self.scene, self.layer)
                    self.assertFalse(self.notify(), "%s %s" % (engine, name))
                self.assertFalse(self.notify(update(self.scene), update(self.layer.objects[0])), "a selection")

    def test_the_export_remembers_the_key_the_handler_compares(self):
        # ``engine_link_live.publish`` stores the link's key: an unrelated update after it schedules nothing.
        self.link("godot", {})
        bump(self.scene.render, "resolution_x")
        engine_link._state["scene"] = engine_link.scene_key(self.scene, self.layer)
        self.assertFalse(self.notify())
        source = (ROOT / "scripts/addons_core/angelo_agent/engine_link_live.py").read_text(encoding="utf-8")
        self.assertIn('state["scene"] = engine_link.scene_key(', source)

    def test_without_a_link_the_key_is_the_shared_one(self):
        scene, layer = stand_in_scene()
        with mock.patch.object(scene_changes, "bpy", BPY), mock.patch.dict(engine_link._state, session=None):
            self.assertEqual(engine_link.scene_key(scene, layer), scene_changes.scene_key(scene, layer))


if __name__ == "__main__":
    unittest.main()
