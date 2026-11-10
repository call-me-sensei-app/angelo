# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Baked materials stay honest whatever the viewport hides, and on every UDIM tile, in the built app.

Run through ``run_native_suites.py --include test_blender_baked_export_hidden`` or an isolated Angelo with --background
--factory-startup --python-exit-code 1 --python <this file> -- <receipt.json>.

Needs the native reader (``_bpy._angelo_digest``, bpy_angelo_digest.cc): stock Blender has none. Each target is bound
as a ``BAKE_MAPS`` job binds it, then one edit at a time is made and ``export_baked.plan`` must call the binding
stale exactly when the edit changes what a render (and so a bake) reads. Hiding with the eye or disabling in
viewports changes nothing a render reads, so it never makes a binding stale; an edit made to what a hidden object
shows (an instanced object's geometry, a modifier's target) always does; render visibility (Disable in Renders,
Show Instancer for renders) does. Unsaved paint on any UDIM tile makes a binding stale, and so does a saved tile's file changing, in either tile
naming. Shapes here are generic
(boxes, a geometry-nodes scatter, a collection instance, a Cast modifier pulled by an empty); the answers come from
what each edit does. ``test_native_baked_digest_hidden.py`` holds the same rules against stand-ins.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import time
import unittest
from pathlib import Path

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_maps_fixtures as fx  # noqa: E402
from test_blender_baked_export import ensure_addon  # noqa: E402

RESULTS = {"cases": {}, "cost": {}}


def depsgraph():
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()
    return graph


def box(name, location, size=1.0):
    mesh = bpy.data.meshes.new(name)
    built = bmesh.new()
    bmesh.ops.create_cube(built, size=size)
    built.to_mesh(mesh)
    built.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


def nudge(obj, axis, by=0.3):
    """Move one vertex of ``obj``'s mesh, as an edit would (tagged for evaluation)."""
    obj.data.vertices[0].co[axis] += by
    obj.data.update()


def scatter_of(source, location):
    """A box whose geometry nodes put an instance of ``source`` (as an instance, not realized) on each vertex."""
    obj = box("Scatter", location)
    tree = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    inputs, output = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    points, info = tree.nodes.new("GeometryNodeMeshToPoints"), tree.nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = source
    info.inputs["As Instance"].default_value = True
    place = tree.nodes.new("GeometryNodeInstanceOnPoints")
    tree.links.new(inputs.outputs[0], points.inputs[0])
    tree.links.new(points.outputs[0], place.inputs["Points"])
    tree.links.new(info.outputs["Geometry"], place.inputs["Instance"])
    tree.links.new(place.outputs[0], output.inputs[0])
    obj.modifiers.new("Scatter", "NODES").node_group = tree
    return obj


def bind(obj, sources=()):
    from angelo_agent import export_baked
    baked = bpy.data.materials.get("Baked Stand-in") or bpy.data.materials.new("Baked Stand-in")
    full, own = export_baked.digests(obj, depsgraph(), sources, None, True)
    export_baked.bind(obj, baked, full, "GLTF", sources, None, True, own)


def stale(obj):
    from angelo_agent import export_baked
    return export_baked.plan([obj], depsgraph())["stale"] == [obj.name]


class Hidden(unittest.TestCase):
    def setUp(self):
        fx.scene_setup()
        ensure_addon()
        import _bpy
        self.assertTrue(hasattr(_bpy, "_angelo_digest"), "this build has the native reader")
        self.target = box("Target", (0.0, 0.0, 0.0))

    def check(self, name, edit, expected, undo=None, sources=()):
        bind(self.target, sources)
        edit()
        found = stale(self.target)
        RESULTS["cases"][name] = {"stale": found, "expected": expected}
        self.assertEqual(found, expected, name)
        if undo is not None:
            undo()

    def test_instancers_hidden_in_the_viewport(self):
        rock = box("Rock", (40.0, 40.0, 40.0))   # Out of reach itself: reached only through its instances.
        scatter = scatter_of(rock, (2.0, 0.0, 0.0))
        props = bpy.data.collections.new("Props")   # Its only collection, so disabling that disables it.
        bpy.context.scene.collection.children.link(props)
        bpy.context.scene.collection.objects.unlink(scatter)
        props.objects.link(scatter)
        ways = (("with the eye", lambda: scatter.hide_set(True), lambda: scatter.hide_set(False)),
                ("disabled in viewports", lambda: setattr(scatter, "hide_viewport", True),
                 lambda: setattr(scatter, "hide_viewport", False)),
                ("in a collection disabled in viewports", lambda: setattr(props, "hide_viewport", True),
                 lambda: setattr(props, "hide_viewport", False)))
        for way, hide, unhide in ways:
            self.check("hiding the scatter " + way, hide, False, unhide)
            hide()
            self.check("an instanced object's vertex while it is hidden " + way, lambda: nudge(rock, 0), True)
            self.check("showing it again after " + way, unhide, False)
        self.check("disabling it in renders", lambda: setattr(scatter, "hide_render", True), True,
                   lambda: setattr(scatter, "hide_render", False))

    def test_collection_instance_hidden_in_the_viewport(self):
        part = box("Part", (0.0, 0.0, 30.0))
        kit = bpy.data.collections.new("Kit")
        bpy.context.scene.collection.children.link(kit)
        bpy.context.scene.collection.objects.unlink(part)
        kit.objects.link(part)
        bpy.context.view_layer.layer_collection.children["Kit"].exclude = True
        kit.instance_offset = (0.0, 0.0, 30.0)
        empty = bpy.data.objects.new("KitInstance", None)
        empty.instance_type, empty.instance_collection = "COLLECTION", kit
        empty.location = (0.0, 2.0, 0.0)
        bpy.context.scene.collection.objects.link(empty)
        self.check("hiding the instance with the eye", lambda: empty.hide_set(True), False)
        self.check("the instanced part's vertex while hidden", lambda: nudge(part, 2), True)
        empty.hide_set(False)
        self.check("disabling the instance in viewports", lambda: setattr(empty, "hide_viewport", True), False)
        self.check("the instanced part's vertex while disabled", lambda: nudge(part, 2), True)

    def test_modifier_target_of_a_disabled_neighbour(self):
        near = box("Near", (0.0, 2.0, 0.0))
        pull = bpy.data.objects.new("Pull", None)
        bpy.context.scene.collection.objects.link(pull)
        cast = near.modifiers.new("Cast", "CAST")
        cast.object, cast.factor = pull, 1.0
        self.check("disabling the neighbour in viewports", lambda: setattr(near, "hide_viewport", True), False)
        self.check("moving its Cast target while disabled", lambda: setattr(pull, "location", (3.0, 0.0, 0.0)), True)
        self.check("enabling it again", lambda: setattr(near, "hide_viewport", False), False)

    def test_source_disabled_in_viewports(self):
        high = box("High", (0.0, 0.0, 0.0), size=1.1)
        pull = bpy.data.objects.new("PullHigh", None)
        bpy.context.scene.collection.objects.link(pull)
        cast = high.modifiers.new("Cast", "CAST")
        cast.object, cast.factor = pull, 0.5
        self.check("disabling the source in viewports", lambda: setattr(high, "hide_viewport", True), False,
                   sources=[high])
        self.check("moving its Cast target while disabled", lambda: setattr(pull, "location", (0.0, 0.0, 2.0)),
                   True, sources=[high])

    def test_cost_of_a_disabled_neighbour(self):
        near = box("Heavy", (0.0, 2.0, 0.0))
        near.modifiers.new("Subdivision", "SUBSURF").levels = 5   # About 6,000 faces of its own evaluation.
        near.hide_viewport = True
        bind(self.target)
        from angelo_agent import export_baked
        times = []
        for _ in range(5):
            start = time.perf_counter()
            export_baked.plan([self.target], depsgraph())
            times.append(time.perf_counter() - start)
        times.sort()
        RESULTS["cost"]["plan_with_disabled_neighbour_ms"] = {"median": round(times[2] * 1000, 2),
                                                             "max": round(times[-1] * 1000, 2)}
        self.assertFalse(stale(self.target))

    def test_show_instancer_follows_the_render(self):
        rock = box("Rock", (40.0, 40.0, 40.0))
        scatter = scatter_of(rock, (2.0, 0.0, 0.0))
        self.check("Show Instancer for the viewport off",
                   lambda: setattr(scatter, "show_instancer_for_viewport", False), False)
        self.check("Show Instancer for renders off",
                   lambda: setattr(scatter, "show_instancer_for_render", False), True)


class Tiles(unittest.TestCase):
    SIZE = 4096
    TILES = (1001, 1002, 1003, 1004)

    def setUp(self):
        fx.scene_setup()
        ensure_addon()
        self.target = box("Target", (0.0, 0.0, 0.0))
        self.image = bpy.data.images.new("Paint", self.SIZE, self.SIZE, tiled=True)
        with bpy.context.temp_override(edit_image=self.image):
            bpy.ops.image.tile_add(number=1002, count=len(self.TILES) - 1, fill=True, width=self.SIZE,
                                   height=self.SIZE, generated_type="UV_GRID")
        material = bpy.data.materials.new("Painted")
        if material.node_tree is None:
            material.use_nodes = True
        tree = material.node_tree
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = self.image
        tree.links.new(node.outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"])
        self.target.data.materials.append(material)

    def paint(self, index):
        """An unsaved edit of one tile's pixels: its grid blurred by a scale down and back (``Image.scale`` marks the
        tile's buffer edited, as painting does)."""
        self.image.scale(self.SIZE // 2, self.SIZE // 2, tile_index=index)
        self.image.scale(self.SIZE, self.SIZE, tile_index=index)

    def test_unsaved_paint_on_every_tile(self):
        self.paint(0)
        self.assertTrue(self.image.is_dirty)
        for index in range(1, len(self.TILES)):
            bind(self.target)
            self.paint(index)
            found = stale(self.target)
            RESULTS["cases"]["painting tile %d" % self.TILES[index]] = {"stale": found, "expected": True}
            self.assertTrue(found, self.TILES[index])
        bind(self.target)
        self.assertFalse(stale(self.target), "nothing changed")

    def test_cost_of_every_tile_unsaved(self):
        from angelo_agent import export_baked_images
        for index in range(len(self.TILES)):
            self.paint(index)
        times = []
        for _ in range(5):
            start = time.perf_counter()
            export_baked_images.image_digest(self.image, export_baked_images.image_key(self.image, None, 1))
            times.append(time.perf_counter() - start)
        times.sort()
        RESULTS["cost"]["image_digest_ms"] = {"tiles": len(self.TILES), "size": self.SIZE,
                                              "median": round(times[2] * 1000, 2),
                                              "max": round(times[-1] * 1000, 2)}


class TileFiles(unittest.TestCase):
    """A saved tile's file changing on disk makes a binding stale, in both of Blender's tile namings: ``<UDIM>``
    (``paint.1002.png``) and ``<UVTILE>`` (``paint.u2_v1.png``)."""
    SIZE = 64
    TILES = (1001, 1002, 1011)

    def tiled(self, folder, token):
        fx.scene_setup()
        ensure_addon()
        target = box("Target", (0.0, 0.0, 0.0))
        image = bpy.data.images.new("Saved", self.SIZE, self.SIZE, tiled=True)
        with bpy.context.temp_override(edit_image=image):
            for number in self.TILES[1:]:
                bpy.ops.image.tile_add(number=number, count=1, fill=True, width=self.SIZE, height=self.SIZE,
                                       generated_type="UV_GRID")
        image.filepath_raw = os.path.join(folder, "saved.%s.png" % token)
        image.file_format = "PNG"
        image.save()
        material = bpy.data.materials.new("Saved")
        if material.node_tree is None:
            material.use_nodes = True
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        material.node_tree.links.new(node.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs[0])
        target.data.materials.append(material)
        return target, image

    def test_a_tile_file_saved_again_with_other_pixels(self):
        import tempfile
        for token, name in (("<UDIM>", "{}"), ("<UVTILE>", "u{u}_v{v}")):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as folder:
                target, image = self.tiled(folder, token)
                files = {number: os.path.join(folder, "saved.%s.png" % name.format(
                    number, u=(number - 1001) % 10 + 1, v=(number - 1001) // 10 + 1)) for number in self.TILES}
                self.assertTrue(all(os.path.isfile(path) for path in files.values()), files)
                bind(target)
                image.reload()
                self.assertFalse(stale(target), "%s: reloading unchanged files" % token)
                for number in self.TILES[1:]:   # Another tile's pixels written over a later tile, then reloaded.
                    bind(target)
                    Path(files[number]).write_bytes(Path(files[1001]).read_bytes())
                    os.utime(files[number], ns=(time.time_ns(), time.time_ns() + 10 ** 9))
                    image.reload()
                    found = stale(target)
                    RESULTS["cases"]["%s tile %d file changed" % (token, number)] = {"stale": found,
                                                                                     "expected": True}
                    self.assertTrue(found, "%s tile %d" % (token, number))
                bpy.data.images.remove(image)

if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "baked-export-hidden.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(case) for case in (Hidden, Tiles, TileFiles)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "numbers": RESULTS, "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("Baked-material digest of hidden objects or tiles failed; see the receipt")
