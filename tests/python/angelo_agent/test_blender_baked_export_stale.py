# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When a baked material goes stale, in real Blender (doc/angelo/long_jobs.rst, "Baked materials at export").

Run through ``run_native_suites.py --include test_blender_baked_export_stale`` or an isolated Angelo with --background
--factory-startup --python-exit-code 1 --python <this file> -- <receipt.json> [scene.blend|scene.glb ...].

Each target is bound as a ``BAKE_MAPS`` job binds it (the digest of what its bake read, taken as the job takes it),
then one edit at a time is made and ``export_baked.plan`` must call the binding stale exactly when the edit changes
something a bake reads. The ``battery`` is generic: it applies to any mesh with a material, and each edit's expected
answer comes from what the edit does, never from a scene's names. Edits that change what a bake reads: a vertex, a
UV, face material assignments, flat or smooth shading, a node value, a slot's material, an image's pixels (painted,
unsaved), a modifier's render setting, geometry arriving, moving or leaving within AO reach, and a neighbour
disabled in renders. Edits that don't: selection, renaming, a neighbour hidden only in the viewport (a bake's AO
still sees it), an object moving far outside AO reach, and moving the target alone where nothing is in reach, no
node reads its placement and no modifier depends on it (an armature left behind deforms it). Then placement-reading
nodes, high-to-low sources and cage, and a real high-to-low bake that leaves the digest as it was.

``DEV`` scenes were used while writing the digest (the audit's two-material cube among them); ``HELD_OUT`` scenes
(other shapes, materials, modifiers and instancing) and every scene given on the command line never were.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import bpy
import numpy
from mathutils import Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_maps_fixtures as fx  # noqa: E402
from test_blender_baked_export import ensure_addon, procedural, shape  # noqa: E402

RESULTS = {"DEV": {}, "HELD_OUT": {}, "scenes": {}}
SCENES = []   # Scene files from the command line (held out).
PLACEMENT_NODES = {"ShaderNodeNewGeometry", "ShaderNodeObjectInfo", "ShaderNodeVectorTransform",
                   "ShaderNodeCameraData", "ShaderNodeTexPointDensity", "ShaderNodeParticleInfo",
                   "ShaderNodeWireframe", "ShaderNodeAttribute"}


def depsgraph():
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()
    return graph


def bind(obj, sources=(), cage=None):
    """Bind as a packed-preset job does (AO baked) to a stand-in baked material."""
    from angelo_agent import export_baked
    baked = bpy.data.materials.get("Baked Stand-in") or bpy.data.materials.new("Baked Stand-in")
    full, own = export_baked.digests(obj, depsgraph(), sources, cage, True)
    export_baked.bind(obj, baked, full, "GLTF", sources, cage, True, own)


def stale(obj):
    from angelo_agent import export_baked
    return export_baked.plan([obj], depsgraph())["stale"] == [obj.name]


def named_nearby(obj):
    """Whether the export names something near the object as the reason it is stale."""
    from angelo_agent import export_baked
    return export_baked.plan([obj], depsgraph())["nearby"] == [obj.name]


def trees_of(obj):
    found, todo = [], [slot.material.node_tree for slot in obj.material_slots if slot.material and
                       slot.material.node_tree]
    while todo:
        tree = todo.pop()
        if tree in found:
            continue
        found.append(tree)
        todo += [node.node_tree for node in tree.nodes if getattr(node, "node_tree", None)]
    return found


def reads_placement(obj):
    """Whether a node in the object's materials reads where the object is or what it is (the oracle's own check)."""
    for tree in trees_of(obj):
        for node in tree.nodes:
            if node.bl_idname in PLACEMENT_NODES and not (node.bl_idname == "ShaderNodeAttribute" and
                                                         node.attribute_type == "GEOMETRY"):
                return True
            if node.bl_idname == "ShaderNodeTexCoord" and (node.object is not None or any(
                    out.is_linked for out in node.outputs if out.name in {"Camera", "Window", "Reflection"})):
                return True
            for prop in node.bl_rna.properties:
                if prop.type == "ENUM" and not prop.is_readonly and "WORLD" in str(getattr(node, prop.identifier, "")):
                    return True
    return False


def extent():
    """A distance beyond every object and the AO reach: moving something this far puts it out of reach."""
    corners = [obj.matrix_world @ Vector(corner) for obj in bpy.context.scene.objects for corner in obj.bound_box]
    size = max((max(abs(value) for value in corner) for corner in corners), default=1.0)
    world = bpy.context.scene.world
    return 10.0 * (size + (world.light_settings.distance if world else 10.0)) + 100.0


def image_of(obj):
    for tree in trees_of(obj):
        for node in tree.nodes:
            image = getattr(node, "image", None)
            if node.bl_idname == "ShaderNodeTexImage" and image is not None and len(image.pixels) and image.has_data:
                return image   # (Reading its length loads it.)
    return None


def local_surface(obj):
    """The object's evaluated vertex positions in its own space, exactly as a bake reads them (the oracle's own
    reading; an armature left behind changes them, at a distance by float noise alone)."""
    evaluated = obj.evaluated_get(depsgraph()).data
    values = numpy.empty(len(evaluated.vertices) * 3, dtype=numpy.float32)
    evaluated.vertices.foreach_get("co", values)
    return values.tobytes()


def faces_with_two_materials(mesh):
    indices = [polygon.material_index for polygon in mesh.polygons]
    for index, value in enumerate(indices):
        if value != indices[0]:
            return 0, index
    return None


def battery(obj):
    """Each applicable edit made on its own: ``{edit: (expected stale, found stale)}``; rebound after each."""
    mesh, out = obj.data, {}
    far = extent()

    def check(name, edit, expected, near=False):
        """``near``: a stale binding's reason is something near the object (its baked AO saw it), not the object."""
        bind(obj)
        if stale(obj):
            out[name + " (not current before the edit)"] = (False, True)
            return
        before = local_surface(obj)
        edit()
        mesh.update()
        if callable(expected):   # Decided by what the edit did to the object's own evaluated surface.
            expected = expected(before, local_surface(obj))
        if callable(near):
            near = near(before, local_surface(obj))
        out[name] = (expected, stale(obj))
        if expected:
            out[name + " (named as nearby)"] = (near, named_nearby(obj))

    placed = reads_placement(obj)
    check("nothing", lambda: None, False)
    check("selection", lambda: mesh.vertices.foreach_set("select", [not v.select for v in mesh.vertices]), False)
    check("rename", lambda: (setattr(obj, "name", obj.name + " Renamed"),
                             [setattr(slot.material, "name", slot.material.name + " Renamed")
                              for slot in obj.material_slots if slot.material]), placed)
    # With shape keys the evaluated mesh is made from the keys: the reference key is the one to edit.
    vertices = mesh.shape_keys.reference_key.data if mesh.shape_keys else mesh.vertices
    check("vertex", lambda: setattr(vertices[0], "co", vertices[0].co + Vector((0.01, 0.02, 0.03))), True)
    if mesh.uv_layers.active is not None:
        check("uv", lambda: setattr(mesh.uv_layers.active.data[0], "uv",
                                    mesh.uv_layers.active.data[0].uv + Vector((0.01, 0.02))), True)
    pair = faces_with_two_materials(mesh)
    if pair is not None:
        def swap():
            first, second = (mesh.polygons[index] for index in pair)
            first.material_index, second.material_index = second.material_index, first.material_index
        check("face materials swapped", swap, True)
    check("shading", lambda: setattr(mesh.polygons[0], "use_smooth", not mesh.polygons[0].use_smooth), True)
    tree = trees_of(obj)[0]
    socket = next((s for node in tree.nodes for s in node.inputs if not s.is_linked and
                   isinstance(getattr(s, "default_value", None), float)), None)
    if socket is not None:
        check("node value", lambda: setattr(socket, "default_value", socket.default_value + 0.25), True)
    material = obj.material_slots[0].material
    # A copy that differs in a value; a plain copy shades the same.
    changed = any(not s.is_linked and isinstance(getattr(s, "default_value", None), float)
                  for node in material.node_tree.nodes for s in node.inputs)

    def other_material():
        copy = material.copy()
        socket_ = next((s for node in copy.node_tree.nodes for s in node.inputs if not s.is_linked and
                        isinstance(getattr(s, "default_value", None), float)), None)
        if socket_ is not None:
            socket_.default_value += 0.5
        obj.material_slots[0].material = copy
    check("slot material", other_material, changed)
    image = image_of(obj)
    if image is not None:
        def paint():
            first = list(image.pixels[:4])
            image.pixels[0] = 1.0 - first[0] if first[0] != 0.5 else 0.25
        check("image painted", paint, True)
    subsurf = next((modifier for modifier in obj.modifiers if modifier.type == "SUBSURF"), None)
    if subsurf is not None:
        check("render subdivision", lambda: setattr(subsurf, "render_levels", subsurf.render_levels + 1), True)
    # AO reach: geometry arriving next to the target, moving there, leaving and moving far away.
    low = min((obj.matrix_world @ Vector(corner) for corner in obj.bound_box), key=lambda point: point.z)
    neighbour_mesh = bpy.data.meshes.new("Neighbour")
    neighbour_mesh.from_pydata([(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)], [], [(0, 1, 2, 3)])
    neighbour = bpy.data.objects.new("Neighbour", neighbour_mesh)
    neighbour.location = low - Vector((0.0, 0.0, 0.05))

    def arrive():
        bpy.context.scene.collection.objects.link(neighbour)
    check("geometry arrives in reach", arrive, True, near=True)
    check("neighbour moves in reach", lambda: setattr(neighbour, "location", neighbour.location + Vector((0.1, 0, 0))),
          True, near=True)
    # A bake's AO sees what a render shows: hiding in the viewport changes nothing, disabling in renders does.
    check("neighbour hidden in the viewport", lambda: setattr(neighbour, "hide_viewport", True), False)
    check("neighbour disabled in renders", lambda: setattr(neighbour, "hide_render", True), True, near=True)
    neighbour.hide_viewport = neighbour.hide_render = False
    check("neighbour leaves reach", lambda: setattr(neighbour, "location", Vector((far, far, far))), True, near=True)
    check("far object moves", lambda: setattr(neighbour, "location", Vector((far * 2, far, far))), False)
    # Moving next to it changes only what is near, unless the move changes the object's own reading too.
    check("target moves next to it", lambda: setattr(obj, "location", neighbour.location + Vector((0, 0, 0.3))), True,
          near=lambda before, after: not placed and before == after)
    bpy.data.objects.remove(neighbour)
    # A move alone changes what a bake reads when a node reads the placement, or when a modifier depends on where the
    # object is (an armature or hook left behind deforms it differently).
    moved = lambda before, after: placed or before != after  # noqa: E731
    check("target moves far, alone", lambda: setattr(obj, "location", Vector((-far, -far, -far))), moved)
    check("target moves again, alone", lambda: setattr(obj, "location", obj.location + Vector((5.0, 0, 0))), moved)
    return out


def assert_battery(test, obj, record):
    found = battery(obj)
    record[obj.name] = {name: list(pair) for name, pair in found.items()}
    wrong = {name: pair for name, pair in found.items() if pair[0] != pair[1]}
    test.assertEqual(wrong, {}, obj.name)


def two_materials(name, kind, variants, modifier=None):
    """A shape whose faces alternate between two procedural materials."""
    obj = shape(name, kind, procedural(name + " A", variants[0]), modifier)
    obj.data.materials.append(procedural(name + " B", variants[1]))
    for polygon in obj.data.polygons:
        polygon.material_index = polygon.index % 2
    return obj


def image_material(name):
    """An image texture material (a generated image, so it holds unsaved pixels) with a colour attribute mix."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    image = bpy.data.images.new(name + " Image", 32, 32)
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    attribute = nodes.new("ShaderNodeVertexColor")
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    links.new(texture.outputs["Color"], mix.inputs["A"])
    links.new(attribute.outputs["Color"], mix.inputs["B"])
    links.new(mix.outputs["Result"], nodes["Principled BSDF"].inputs["Base Color"])
    return material


class Scenes(unittest.TestCase):
    group = "DEV"

    def setUp(self):
        fx.scene_setup()
        ensure_addon()
        self.record = RESULTS[self.group].setdefault(self.id().rsplit(".", 1)[-1], {})

    def test_a_two_material_cube(self):   # The audit's case: swapping two faces' materials.
        assert_battery(self, two_materials("Crate", "cube", ("noise", "voronoi")), self.record)

    def test_a_geometry_position_material_reads_its_placement(self):
        obj = shape("Marker", "sphere", procedural("Marker Paint", "noise"))
        nodes, links = obj.data.materials[0].node_tree.nodes, obj.data.materials[0].node_tree.links
        geometry = nodes.new("ShaderNodeNewGeometry")
        links.new(geometry.outputs["Position"], next(n for n in nodes if n.bl_idname == "ShaderNodeTexNoise")
                  .inputs["Vector"])
        assert_battery(self, obj, self.record)


class HeldOutScenes(Scenes):
    group = "HELD_OUT"

    def test_a_two_material_cube(self):
        assert_battery(self, two_materials("Column", "cylinder", ("wave", "noise"), "SUBSURF"), self.record)

    def test_a_geometry_position_material_reads_its_placement(self):
        obj = shape("Totem", "cylinder", procedural("Totem Paint", "wave"), "BEVEL")
        nodes, links = obj.data.materials[0].node_tree.nodes, obj.data.materials[0].node_tree.links
        transform = nodes.new("ShaderNodeVectorTransform")
        transform.convert_from, transform.convert_to = "OBJECT", "WORLD"
        coordinates = nodes.new("ShaderNodeTexCoord")
        links.new(coordinates.outputs["Object"], transform.inputs["Vector"])
        links.new(transform.outputs["Vector"], next(n for n in nodes if n.bl_idname == "ShaderNodeTexWave")
                  .inputs["Vector"])
        assert_battery(self, obj, self.record)

    def test_an_image_and_colour_attribute_material(self):
        obj = shape("Tile", "cube", image_material("Tile Paint"), "BEVEL")
        layer = obj.data.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
        obj.data.color_attributes.active_color = layer
        assert_battery(self, obj, self.record)
        bind(obj)
        layer.data[0].color = (0.1, 0.9, 0.2, 1.0)
        self.assertTrue(stale(obj), "a colour attribute edit")

    def test_an_instance_scatter_in_reach(self):
        """Geometry-nodes instances near the target are AO neighbours: moving the scatter is a change."""
        obj = shape("Rock", "sphere", procedural("Rock Paint", "voronoi"))
        ground = shape("Ground", "cube", procedural("Ground Paint", "noise"))
        ground.scale = (4.0, 4.0, 0.1)
        ground.location.z = -0.8
        modifier = ground.modifiers.new("Scatter", "NODES")
        group = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
        group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        nodes, links = group.nodes, group.links
        inputs, outputs = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
        points = nodes.new("GeometryNodeDistributePointsOnFaces")
        points.inputs["Density"].default_value = 2.0
        cone = nodes.new("GeometryNodeMeshCone")
        instance = nodes.new("GeometryNodeInstanceOnPoints")
        join = nodes.new("GeometryNodeJoinGeometry")
        links.new(inputs.outputs[0], points.inputs["Mesh"])
        links.new(points.outputs["Points"], instance.inputs["Points"])
        links.new(cone.outputs["Mesh"], instance.inputs["Instance"])
        links.new(inputs.outputs[0], join.inputs[0])
        links.new(instance.outputs["Instances"], join.inputs[0])
        links.new(join.outputs[0], outputs.inputs[0])
        modifier.node_group = group
        bind(obj)
        self.assertFalse(stale(obj))
        points.inputs["Seed"].default_value = 7
        self.record["scatter_reseeded"] = stale(obj)
        self.assertTrue(self.record["scatter_reseeded"], "the instances moved within reach")

    def test_high_to_low_sources_and_cage(self):
        """A bake from sources reads the sources' shading and placement, and the cage; not the target's material."""
        low = shape("Low", "cube", procedural("Low Paint", "noise"))
        high = shape("High", "sphere", procedural("High Paint", "voronoi"), "SUBSURF")
        cage = shape("Cage", "cube", procedural("Cage Paint", "noise"))
        cage.scale = (1.2, 1.2, 1.2)
        edits = {
            "the target's own material": (lambda: setattr(low.data.materials[0].node_tree.nodes["Principled BSDF"]
                                                          .inputs["Roughness"], "default_value", 0.123), False),
            "a source's material": (lambda: setattr(high.data.materials[0].node_tree.nodes["Principled BSDF"]
                                                    .inputs["Roughness"], "default_value", 0.321), True),
            "a source moved": (lambda: setattr(high, "location", high.location + Vector((0.05, 0, 0))), True),
            "the cage scaled": (lambda: setattr(cage, "scale", (1.3, 1.3, 1.3)), True),
            "a source's vertex": (lambda: setattr(high.data.vertices[3], "co", high.data.vertices[3].co * 1.1), True),
            "a source deleted": (lambda: bpy.data.objects.remove(high), True),
        }
        found = {}
        for name, (edit, expected) in edits.items():
            bind(low, [high], cage)
            self.assertFalse(stale(low), "current before " + name)
            edit()
            found[name] = [expected, stale(low)]
        self.record["edits"] = found
        self.assertEqual({name: pair for name, pair in found.items() if pair[0] != pair[1]}, {})

    def test_a_real_high_to_low_bake_leaves_the_digest_as_it_was(self):
        from angelo_agent import export_baked
        low = shape("Plate", "cube", procedural("Plate Paint", "noise"))
        high = shape("Relief", "cube", procedural("Relief Paint", "wave"), "BEVEL")
        high.scale = (0.95, 0.95, 0.95)
        before = export_baked.digest(low, depsgraph(), [high], None, True)
        folder = Path(tempfile.mkdtemp(prefix="baked-stale-"))
        request = {"resolution": [fx.N, fx.N], "margin": 2, "packing": "GLTF", "format": "PNG", "output": "maps",
                   "material": True}
        (plan, _entries, _facts, _sums), = fx.bake([low], request, folder, sources=[high])
        self.assertIn("AO", plan.channels)
        self.record["digest_kept"] = before == export_baked.digest(low, depsgraph(), [high], None, True)
        self.assertTrue(self.record["digest_kept"])


class GivenScenes(unittest.TestCase):
    """Every scene file named on the command line (held out): the battery on up to six meshes of each."""

    def test_given_scenes(self):
        if not SCENES:
            self.skipTest("[environment] no scene files given")
        for path in SCENES:
            if path.suffix.lower() == ".blend":
                bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
            else:
                fx.scene_setup()
                bpy.ops.import_scene.gltf(filepath=str(path))
            ensure_addon()
            record = RESULTS["scenes"].setdefault(path.name, {})
            candidates = sorted((obj for obj in bpy.context.scene.objects if obj.type == "MESH" and
                                 obj.library is None and obj.data.library is None and obj.material_slots and
                                 all(slot.material and slot.material.node_tree for slot in obj.material_slots) and
                                 obj.visible_get()), key=lambda obj: (-len(obj.material_slots), obj.name))
            for obj in candidates[:6]:
                with self.subTest(scene=path.name, object=obj.name):
                    assert_battery(self, obj, record)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "baked-export-stale.json"
    SCENES += [Path(item) for item in arguments[1:]]
    receipt.parent.mkdir(parents=True, exist_ok=True)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(case) for case in (Scenes, HeldOutScenes, GivenScenes)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "skipped": [str(test) for test, _reason in result.skipped],
                                   "numbers": RESULTS, "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("Baked-material staleness failed; see the receipt and process log")
