# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The file Blender reads for an image's frame, tile or view, named as Blender names it, in the built app.

Run through ``run_native_suites.py --include test_blender_image_read_files`` or an isolated Angelo with --background
--factory-startup --python-exit-code 1 --python <this file> -- <receipt.json>.

* ``blend_file_paths.frame_file_path`` names the same file as Blender's ``Image.filepath_from_user`` for every path
  shape and frame here, and ``frame_number`` the same frame as Blender's own image user at each scene frame.
* A baked material reading an image sequence goes stale when the file of the frame it reads is saved again and the
  image reloaded, and not when another frame's file is; the same for each view's file of a multi-view image.
* ``angelo_blend_file`` lists a tile set that starts at 1002 (no tile 1001) as present, as Blender's own missing
  file check does (1.0.10 audit, P3-2 and P3-4). ``test_native_image_read_files.py`` and
  ``test_native_baked_digest_frames.py`` hold the same rules against stand-ins.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_maps_fixtures as fx  # noqa: E402
from test_blender_baked_export import ensure_addon  # noqa: E402

RESULTS = {"cases": {}}
SIZE = 8


def depsgraph():
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()
    return graph


def box(name):
    mesh = bpy.data.meshes.new(name)
    built = bmesh.new()
    bmesh.ops.create_cube(built, size=1.0)
    built.to_mesh(mesh)
    built.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def save_png(path, color):
    """A small PNG of one colour at ``path``, with a later time than any before it (as a later save would have)."""
    image = bpy.data.images.new("Writer", SIZE, SIZE)
    image.generated_color = color
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)
    later = time.time_ns() + 2 * 10 ** 9
    os.utime(path, ns=(later, later))


def textured(obj, image):
    material = bpy.data.materials.new("Read")
    if material.node_tree is None:
        material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material.node_tree.links.new(node.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs[0])
    obj.data.materials.append(material)
    return material, node


def bind(obj):
    from angelo_agent import export_baked
    baked = bpy.data.materials.get("Baked Stand-in") or bpy.data.materials.new("Baked Stand-in")
    full, own = export_baked.digests(obj, depsgraph(), (), None, True)
    export_baked.bind(obj, baked, full, "GLTF", (), None, True, own)


def stale(obj):
    from angelo_agent import export_baked
    return export_baked.plan([obj], depsgraph())["stale"] == [obj.name]


class Names(unittest.TestCase):
    def setUp(self):
        fx.scene_setup()
        ensure_addon()

    def test_frame_file_path_names_what_blender_names(self):
        from angelo_agent import blend_file_paths as paths
        folder = tempfile.mkdtemp()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "names.blend"))   # So // has a folder.
        save_png(os.path.join(folder, "seq.0001.png"), (0.1, 0.2, 0.3, 1.0))
        image = bpy.data.images.load(os.path.join(folder, "seq.0001.png"))
        image.source = "SEQUENCE"
        _material, node = textured(box("Names"), image)
        user = node.image_user
        shapes = ["seq.0001.png", "shot2_take3_0007.exr", "render-007-final.png", "a1.b2", "seq_01", ".0001",
                  "a..png", "plain.png", "seq.####.png", "seq.99999999999.png", "seq.2147483648.png", "séq.01.png"]
        compared = 0
        for shape in shapes:
            for raw in (os.path.join(folder, "v12", shape), "//v12/" + shape):
                image.filepath = raw
                for frame in (0, 1, 7, 12, 123456, -3):
                    user.frame_current = frame
                    ours = bpy.path.abspath(paths.frame_file_path(raw, frame))
                    theirs = image.filepath_from_user(image_user=user)
                    self.assertEqual(os.path.normpath(ours), os.path.normpath(theirs), (raw, frame))
                    compared += 1
        RESULTS["cases"]["frame file names compared with filepath_from_user"] = compared

    def test_frame_number_reads_the_frame_blenders_image_user_reads(self):
        from angelo_agent import blend_file_paths as paths
        folder = tempfile.mkdtemp()
        for frame in range(1, 6):
            save_png(os.path.join(folder, "seq.%04d.png" % frame), (0.1, 0.2, 0.3, 1.0))
        image = bpy.data.images.load(os.path.join(folder, "seq.0001.png"))
        image.source = "SEQUENCE"
        material, node = textured(box("Frames"), image)
        compared = 0
        for duration, start, offset, cyclic in ((5, 1, 0, False), (3, 4, 0, True), (4, 2, 7, False), (2, 1, -1, True),
                                                (0, 1, 3, False)):
            user = node.image_user
            user.frame_duration, user.frame_start, user.frame_offset = duration, start, offset
            user.use_cyclic, user.use_auto_refresh = cyclic, True
            for scene_frame in (-2, 0, 1, 3, 4, 6, 11):
                bpy.context.scene.frame_set(scene_frame)
                evaluated = material.evaluated_get(depsgraph()).node_tree.nodes[node.name].image_user
                expected = paths.frame_number(scene_frame, duration, start, offset, cyclic)
                self.assertEqual(expected, evaluated.frame_current, (duration, start, offset, cyclic, scene_frame))
                compared += 1
        RESULTS["cases"]["frame numbers compared with Blender's image user"] = compared


class Stale(unittest.TestCase):
    def setUp(self):
        fx.scene_setup()
        ensure_addon()
        self.folder = tempfile.mkdtemp()

    def check(self, name, obj, image, change, expected):
        bind(obj)
        change()
        image.reload()
        found = stale(obj)
        RESULTS["cases"][name] = {"stale": found, "expected": expected}
        self.assertEqual(found, expected, name)

    def test_a_sequence_frames_file_saved_again_and_reloaded(self):
        for frame in (1, 2, 3):
            save_png(os.path.join(self.folder, "seq.%04d.png" % frame), (0.1, 0.2, 0.3, 1.0))
        image = bpy.data.images.load(os.path.join(self.folder, "seq.0001.png"))
        image.source = "SEQUENCE"
        obj = box("Sequence")
        _material, node = textured(obj, image)
        node.image_user.frame_duration, node.image_user.frame_start, node.image_user.use_auto_refresh = 3, 1, True
        bpy.context.scene.frame_set(2)
        self.check("sequence: frame 2's file changed at frame 2", obj, image,
                   lambda: save_png(os.path.join(self.folder, "seq.0002.png"), (0.8, 0.1, 0.3, 1.0)), True)
        self.check("sequence: frame 3's file changed at frame 2", obj, image,
                   lambda: save_png(os.path.join(self.folder, "seq.0003.png"), (0.8, 0.1, 0.3, 1.0)), False)
        node.image_user.frame_offset = 1   # Now frame 2 reads file 3.
        self.check("sequence: offset 1, frame 3's file changed at frame 2", obj, image,
                   lambda: save_png(os.path.join(self.folder, "seq.0003.png"), (0.2, 0.9, 0.3, 1.0)), True)

    def test_each_views_file_of_a_multi_view_image(self):
        scene = bpy.context.scene
        scene.render.use_multiview, scene.render.views_format = True, "STEREO_3D"
        for suffix in ("_L", "_R"):
            save_png(os.path.join(self.folder, "shot%s.png" % suffix), (0.1, 0.2, 0.3, 1.0))
        image = bpy.data.images.load(os.path.join(self.folder, "shot_L.png"))
        image.views_format = "INDIVIDUAL"
        self.assertEqual(tuple(image.size), (SIZE, SIZE))   # Loaded: turning views on now sets them up.
        image.use_multiview = True
        RESULTS["cases"]["multi-view image is_multiview"] = image.is_multiview
        self.assertTrue(image.is_multiview, "Blender reads both views' files")
        obj = box("Views")
        textured(obj, image)
        self.check("multi-view: the right view's file changed", obj, image,
                   lambda: save_png(os.path.join(self.folder, "shot_R.png"), (0.8, 0.1, 0.3, 1.0)), True)
        bind(obj)
        RESULTS["cases"]["multi-view: unchanged"] = {"stale": stale(obj), "expected": False}
        self.assertFalse(stale(obj), "nothing changed")
        # Stereoscopy turned off since: Blender keeps the views it set up, which Python cannot list (unknown).
        scene.render.use_multiview = False
        self.assertTrue(image.is_multiview)
        found = stale(obj)
        RESULTS["cases"]["multi-view: views set up for another scene setting"] = {"stale": found, "expected": True}
        self.assertTrue(found)

    @unittest.skipUnless(os.name == "posix", "Blender respells a Windows path only on macOS and Linux")
    def test_a_sequence_path_saved_on_windows(self):
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(self.folder, "scene.blend"))
        os.makedirs(os.path.join(self.folder, "textures"))
        for frame in (1, 2):
            save_png(os.path.join(self.folder, "textures", "seq.%04d.png" % frame), (0.1, 0.2, 0.3, 1.0))
        image = bpy.data.images.load(os.path.join(self.folder, "textures", "seq.0001.png"))
        image.source = "SEQUENCE"
        image.filepath = "//textures\\seq.0001.png"   # As a .blend saved on Windows stores it.
        obj = box("Windows")
        _material, node = textured(obj, image)
        node.image_user.frame_duration, node.image_user.use_auto_refresh = 2, True
        bpy.context.scene.frame_set(2)
        node.image_user.frame_current = 2
        self.assertTrue(os.path.isfile(image.filepath_from_user(image_user=node.image_user)), "Blender reads it")
        self.check("windows path: frame 2's file changed", obj, image,
                   lambda: save_png(os.path.join(self.folder, "textures", "seq.0002.png"), (0.8, 0.1, 0.3, 1.0)),
                   True)


class FirstTile(unittest.TestCase):
    def test_a_tile_set_without_1001_is_present(self):
        from angelo_agent import blend_file
        from angelo_agent.read_spec import validate_read
        fx.scene_setup()
        ensure_addon()
        folder = tempfile.mkdtemp()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "scene.blend"))
        os.makedirs(os.path.join(folder, "textures"))
        answers = {}
        for token, name in (("<UDIM>", "{n}"), ("<UVTILE>", "u{u}_v{v}")):
            image = bpy.data.images.new("Bricks " + token, SIZE, SIZE, tiled=True)
            with bpy.context.temp_override(edit_image=image):
                bpy.ops.image.tile_add(number=1002, count=4, fill=True, width=SIZE, height=SIZE,
                                       generated_type="UV_GRID")
            image.tiles.remove(image.tiles[0])   # Tile 1001.
            self.assertEqual([tile.number for tile in image.tiles], [1002, 1003, 1004, 1005])
            image.filepath_raw, image.file_format = "//textures/bricks.%s.png" % token, "PNG"
            image.save()
            image.use_fake_user = True
            files = [os.path.join(folder, "textures", "bricks.%s.png" % name.format(
                n=number, u=(number - 1001) % 10 + 1, v=(number - 1001) // 10 + 1)) for number in range(1001, 1006)]
            self.assertEqual([os.path.isfile(path) for path in files], [False, True, True, True, True], files)
        bpy.ops.wm.save_mainfile()
        result = blend_file.read(bpy, validate_read("angelo_blend_file", {}), SimpleNamespace(root=folder))
        answers = result["external_files"]
        RESULTS["cases"]["tile set from 1002: external_files"] = answers
        self.assertEqual(sorted((row["name"], row["status"]) for row in answers["items"]),
                         [("Bricks <UDIM>", "present"), ("Bricks <UVTILE>", "present")], answers)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "image-read-files.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(case) for case in (Names, Stale, FirstTile)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "numbers": RESULTS, "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("Image file names or their staleness failed; see the receipt")
