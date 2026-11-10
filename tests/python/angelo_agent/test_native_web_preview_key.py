# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live web preview follows every scene setting its export reads, on a stand-in ``bpy``
(doc/angelo/web_export.rst, "Every change re-exports").

An edit of the scene's own settings (its colour management, whether the film shows the World, the engine, the
frame rate) updates only the scene, which ``scene_changes.content`` ignores, so the preview's key must hold
everything the export reads from the scene itself. Angelo 1.0.9 kept only the frame, camera, World and visible
names there, and a View Transform or Exposure change waited for an unrelated edit. The checks here derive the
edits from what the export reads (``web_look.settings``, ``web_world.settings``, ``web_facts.scene_settings``),
not from a list of cases; ``test_blender_web_preview_key`` holds the same contract against Blender's own RNA.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
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


class World:
    pass


def fake_bpy():
    bpy = types.ModuleType("bpy")
    classes = {"Scene": Scene, "Object": Object, "World": World}
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: classes.setdefault(name, type(name, (), {}))
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    bpy.app = SimpleNamespace(handlers=SimpleNamespace(persistent=lambda function: function,
                                                       depsgraph_update_post=[], exit_pre=[]),
                              timers=SimpleNamespace(register=lambda *a, **k: None, is_registered=lambda f: False),
                              background=True,
                              translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text,
                                                           locale="en_US"))
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[]))
    bpy.data = SimpleNamespace(filepath="")
    bpy.utils = SimpleNamespace(system_resource=lambda *a, **k: "")
    return bpy


BPY = fake_bpy()
gltf_scene_reads, scene_changes, web_facts, web_look, web_preview, web_world = import_against_stand_ins(
    BPY, ("gltf_scene_reads", "scene_changes", "web_facts", "web_look", "web_preview", "web_world"))


def stand_in_scene():
    scene = Scene()
    world = World()
    world.name, world.node_tree, world.color = "World", None, (0.05, 0.05, 0.05)
    scene.session_uid, scene.name, scene.is_nla_tweakmode, scene.angelo_physics_format = 7, "Scene", False, "OMI"
    scene.frame_current, scene.frame_subframe, scene.frame_start, scene.frame_end = 1, 0.0, 1, 250
    scene.world, scene.camera, scene.compositing_node_group = world, None, None
    scene.view_settings = SimpleNamespace(view_transform="AgX", look="None", exposure=0.0, gamma=1.0,
                                          use_curve_mapping=False, use_white_balance=False)
    scene.display_settings = SimpleNamespace(display_device="sRGB")
    scene.render = SimpleNamespace(film_transparent=False, engine="BLENDER_EEVEE", use_compositing=True,
                                   use_motion_blur=False, fps=24, fps_base=1.0, resolution_x=1920,
                                   resolution_y=1080, pixel_aspect_x=1.0, pixel_aspect_y=1.0)
    cube = Object()
    cube.name, cube.visible_get = "Cube", lambda view_layer=None: True
    return scene, SimpleNamespace(objects=[cube])


def update(datablock, geometry=False, transform=False, shading=False):
    return SimpleNamespace(id=datablock, is_updated_geometry=geometry, is_updated_transform=transform,
                           is_updated_shading=shading)


def changed(value):
    """Another value of the same kind (what an edit of that setting would leave)."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + " Edited"
    if value is None:
        return "Edited"
    if isinstance(value, tuple):
        return tuple(changed(item) for item in value)
    return World()   # An ID: another one.


# Where each value that a ``settings`` function returns is set on the scene (its RNA path).
LOOK_PATHS = {"view_transform": "view_settings.view_transform", "look": "view_settings.look",
              "exposure": "view_settings.exposure", "gamma": "view_settings.gamma",
              "curves": "view_settings.use_curve_mapping", "white_balance": "view_settings.use_white_balance",
              "display": "display_settings.display_device"}
WORLD_PATHS = {"world": "world", "background": "render.film_transparent", "engine": "render.engine",
               "frame": "frame_current"}
EFFECT_PATHS = {"compositor": "compositing_node_group", "use_compositing": "render.use_compositing",
                "motion_blur": "render.use_motion_blur"}


def every_path():
    paths = list(LOOK_PATHS.values()) + list(WORLD_PATHS.values()) + list(EFFECT_PATHS.values())
    reads = gltf_scene_reads
    paths += list(reads.SCENE_FIELDS + reads.ANIMATION_SCENE_FIELDS)
    paths += ["render." + name for name in reads.LIGHT_FIELDS + reads.CAMERA_FIELDS + reads.ANIMATION_RENDER_FIELDS]
    return paths


def write(scene, path, value):
    *owners, name = path.split(".")
    target = scene
    for owner in owners:
        target = getattr(target, owner)
    setattr(target, name, value)


def read(scene, path):
    *owners, name = path.split(".")
    target = scene
    for owner in owners:
        target = getattr(target, owner)
    return getattr(target, name)


class PreviewKeyTests(unittest.TestCase):
    def setUp(self):
        self.scene, self.layer = stand_in_scene()
        window = SimpleNamespace(scene=self.scene, view_layer=self.layer)
        for module in (scene_changes, web_preview):
            patcher = mock.patch.object(module, "bpy", BPY)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(web_preview, "_window", return_value=window)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(web_preview._state, server=object(), exporting=False, dirty_at=None, scene=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settle()

    def notify(self, *updates):
        """Blender's depsgraph_update_post with ``updates``; True when the preview scheduled a re-export."""
        web_preview._state["dirty_at"] = None
        web_preview._on_depsgraph(self.scene, SimpleNamespace(updates=list(updates) or [update(self.scene)]))
        return web_preview._state["dirty_at"] is not None

    def settle(self):
        """The preview's own key after an export (the handler's first look records it)."""
        self.notify()
        web_preview._state["dirty_at"] = None

    def test_every_scene_setting_the_export_reads_schedules_an_export(self):
        # The edit of a scene setting arrives as an update of the scene alone, with no content flag.
        for path in every_path():
            with self.subTest(setting=path):
                write(self.scene, path, changed(read(self.scene, path)))
                self.assertTrue(self.notify(), path)
                self.assertFalse(self.notify(), path + " again: one export per edit, never a loop")

    def test_the_look_the_report_named_schedules_an_export(self):
        # The external 1.0.9 report: View Transform and Exposure (and the rest of Colour Management).
        for path, value in (("view_settings.view_transform", "Filmic"), ("view_settings.exposure", 1.5),
                            ("view_settings.look", "AgX - Punchy"), ("display_settings.display_device", "Display P3"),
                            ("view_settings.gamma", 1.2), ("render.film_transparent", True)):
            with self.subTest(setting=path):
                write(self.scene, path, value)
                self.assertTrue(self.notify(), path)

    def test_a_world_edit_schedules_an_export(self):
        # The World's colour is the World's own update (any data but the scene counts).
        self.scene.world.color = (0.8, 0.2, 0.1)
        self.assertTrue(self.notify(update(self.scene.world)))

    def test_a_selection_or_an_unread_setting_does_not(self):
        # A selection click updates the scene and the object with no content flag; a setting the export never
        # reads (the render size percentage) changes no key.
        self.assertFalse(self.notify(update(self.scene), update(self.layer.objects[0])))
        self.scene.render.resolution_percentage = 50
        self.assertFalse(self.notify())

    def test_every_value_the_look_and_world_read_is_in_the_key(self):
        # Correct by construction: each value ``settings`` returns, changed alone, changes the preview key.
        key = web_preview.scene_key(self.scene, self.layer)
        for module, paths in ((web_look, LOOK_PATHS), (web_world, WORLD_PATHS)):
            self.assertEqual(set(module.settings(self.scene)), set(paths), module.__name__)
        self.assertEqual(set(web_facts._effect_settings(self.scene)), set(EFFECT_PATHS))
        for path in every_path():
            with self.subTest(setting=path):
                before = read(self.scene, path)
                write(self.scene, path, changed(before))
                self.assertNotEqual(web_preview.scene_key(self.scene, self.layer), key, path)
                write(self.scene, path, before)
                self.assertEqual(web_preview.scene_key(self.scene, self.layer), key, path)

    def test_the_look_and_the_world_read_the_scene_only_through_settings(self):
        # A field added to ``facts`` or ``plan`` without ``settings`` would escape the key: the scene they are
        # handed here answers nothing, so either reading it directly fails.
        look = {"view_transform": "AgX", "look": None, "exposure": 0.5, "gamma": 1.0, "curves": False,
                "white_balance": False, "display": "sRGB"}
        mapping = {"tone_mapping": "AgXToneMapping", "output": "SRGBColorSpace", "difference": 0.0}
        with mock.patch.object(web_look, "settings", return_value=look), \
                mock.patch.object(web_look, "_config_path", return_value="config.ocio"), \
                mock.patch.object(web_look.web_tone, "measure", return_value=mapping), \
                mock.patch.object(web_look.web_tone, "web_display", return_value="sRGB", create=True):
            facts = web_look.facts(object())
        self.assertEqual({name: facts[name] for name in look}, look)
        world = SimpleNamespace(name="World", node_tree=None, color=(0.1, 0.2, 0.3))
        read_world = {"world": world, "background": True, "engine": "CYCLES", "frame": (1, 0.0)}
        with mock.patch.object(web_world, "settings", return_value=read_world):
            plan = web_world.plan(object())
        self.assertEqual((plan["kind"], plan["background"]), ("COLOR", True))
        with mock.patch.object(web_world, "settings", return_value=dict(read_world, world=None)):
            self.assertEqual(web_world.plan(object())["kind"], "NONE")

    def test_an_export_remembers_the_same_key(self):
        # ``refresh`` stores the key the handler compares with: an unrelated scene update after it schedules nothing.
        scene, layer = self.scene, self.layer
        window = SimpleNamespace(scene=scene, view_layer=layer)
        server = mock.Mock()

        def export(window, scene, view_layer, path, request, require_environment):
            Path(path).write_bytes(b"glTF")
            return {"settings": {}, "findings": []}
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(web_preview._state, server=server, folder=temporary, request={}), \
                mock.patch.object(web_preview.web_export, "export", side_effect=export), \
                mock.patch.object(web_preview, "_exported_materials", return_value=[]), \
                mock.patch.object(web_preview, "_shader_state", return_value={}), \
                mock.patch.object(web_preview.web_environment, "landed", return_value=0):
            scene.view_settings.exposure = 2.0
            web_preview.refresh(window)
            self.assertEqual(web_preview._state["scene"], web_preview.scene_key(scene, layer))
            self.assertFalse(self.notify())


if __name__ == "__main__":
    unittest.main()
