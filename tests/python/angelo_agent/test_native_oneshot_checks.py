# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The checks and image statistics the one-shot tests add, against stand-in data.

The check kinds run inside Blender; these tests pin their logic on the host
with the stand-ins of ``test_native_scenario_checks``. The compiled-app proof
is each test's reference job, which refuses to write a reference unless the
brief's checks fail on the factory scene and pass on the ground truth.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_checks as checks  # noqa: E402
import oneshot_images as images  # noqa: E402
import scenario_checks  # noqa: E402
import shader_fakes as fakes  # noqa: E402
import shader_graph  # noqa: E402
import surface_reading  # noqa: E402
from test_native_scenario_checks import FakeVector, mathutils  # noqa: E402

try:
    import numpy
except ImportError:   # The image tests need it, as the scoring tests do; CI installs it.
    numpy = None


def holder(name, *materials, **extra):
    data = NS(polygons=[1], materials=list(materials))
    values = dict(name=name, type="MESH", hide_render=False, data=data,
                  material_slots=[NS(material=m) for m in materials])
    values.update(extra)
    obj = NS(**values)
    obj.evaluated_get = lambda depsgraph: obj
    return obj


class Objects(list):
    """``scene.objects``: iterable, with ``get`` by name."""

    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


BROWN, DARK = (0.30, 0.17, 0.08, 1.0), (0.10, 0.05, 0.02, 1.0)


def noise(tree, output="Fac", name="Noise"):
    return tree.add("TEX_NOISE", name=name), output


def image(tree, output="Color", name="Picture"):
    """An Image texture: still unknown to the grader (an image is never loaded)."""
    return tree.add("TEX_IMAGE", name=name), output


def base(build):
    """A material whose Base Color ``build(tree)`` makes: it returns ``(node, output)``."""
    def wire(tree, bsdf):
        node, output = build(tree)
        tree.link(node, output, bsdf, "Base Color")
    return fakes.material("M", build=wire)


def read_base(material, source=None):
    return scenario_checks.material_facts(material, source)


def surface(material, check, mesh=None):
    obj = fakes.holder("Thing", material, mesh=mesh)
    return checks._check_surface(None, NS(objects=[obj]), None, dict(check, target={"name": "Thing"}))


class SurfaceTests(unittest.TestCase):
    """What a material shows, read from its graph over its surface (``surface_reading``)."""

    def test_colour_is_followed_through_ramps_mixes_and_groups(self):
        self.assertEqual(read_base(fakes.material("M", base=BROWN))["base_color"], [0.30, 0.17, 0.08])

        def mixed(factor, blend="MIX", second=DARK):
            def build(tree):
                node = fakes.colour_mix(tree, blend=blend)
                node.inputs["A_Color"].default_value, node.inputs["B_Color"].default_value = BROWN, second
                node.inputs["Factor_Float"].default_value = factor
                return node, "Result_Color"
            return base(build)
        self.assertEqual(read_base(mixed(0.25))["base_color"], [0.25, 0.14, 0.065])
        self.assertEqual(read_base(mixed(1.0, "MULTIPLY", (0.5, 0.5, 0.5, 1)))["base_color"], [0.15, 0.085, 0.04])

        def adjusted(tree):
            node = tree.add("HUE_SAT")
            node.inputs["Color"].default_value = BROWN
            return node, "Color"
        self.assertEqual(read_base(base(adjusted))["base_color"], [0.3, 0.17, 0.08])

        def group(tree):   # A node group: its output, evaluated inside it.
            inner = fakes.Tree()
            colour = inner.add("RGB")
            colour.outputs["Color"].default_value = BROWN
            inner.link(colour, "Color", inner.add("GROUP_OUTPUT", is_active_output=True), "Socket_0")
            return tree.add("GROUP", node_tree=inner), "Socket_0"
        self.assertEqual(read_base(base(group))["base_color"], [0.3, 0.17, 0.08])

    def test_a_ramp_or_mix_after_an_unreadable_texture_shows_what_it_can(self):
        def ramp(*stops):
            def build(tree):
                source, output = image(tree, "Alpha")
                node = tree.add("VALTORGB", color_ramp=fakes.Ramp(*stops))
                tree.link(source, output, node, "Fac")
                return node, "Color"
            return base(build)
        # Every colour the ramp can show is warm: a pass whatever the noise does (no stop average).
        self.assertTrue(surface(ramp((0.0, BROWN), (1.0, DARK)), {"color": ["warm"]})[0])
        self.assertFalse(surface(ramp((0.0, BROWN), (1.0, DARK)), {"color": ["green"]})[0])
        self.assertFalse(checks._surface_problems(read_base(ramp((0.0, BROWN), (1.0, DARK))),
                                                  {"color": ["green"]})[1])   # A measured fail, not undecided.
        # Brown to green: the image decides how much is green, which the grader cannot read.
        passed, details = surface(ramp((0.3, BROWN), (0.7, (0.05, 0.3, 0.05, 1.0))), {"color": ["green"]})
        self.assertFalse(passed)
        self.assertTrue(details["inconclusive"])

        def by_noise(tree):
            source, output = image(tree, "Alpha")
            node = fakes.colour_mix(tree)
            node.inputs["A_Color"].default_value, node.inputs["B_Color"].default_value = BROWN, DARK
            tree.link(source, output, node, "Factor_Float")
            return node, "Result_Color"
        self.assertTrue(surface(base(by_noise), {"color": ["warm"]})[0])   # Between two browns: brown.

    def test_a_raw_texture_or_a_runaway_graph_is_unknown(self):
        passed, details = surface(base(lambda tree: image(tree, "Color")), {"color": ["green"]})
        self.assertEqual((passed, details["inconclusive"]), (False, True))

        def deep(tree):
            node = tree.add("RGB")
            for _ in range(shader_graph.MAX_DEPTH + 1):
                outer = tree.add("GAMMA")
                tree.link(node, "Color", outer, "Color")
                node = outer
            return node, "Color"
        self.assertIsNone(read_base(base(deep))["base_color"])   # A bounded walk, never an endless one.

    def test_roughness_through_value_and_map_range_nodes(self):
        self.assertEqual(read_base(fakes.material("M", roughness=0.7))["roughness"], 0.7)

        def value(tree, bsdf):
            node = tree.add("VALUE")
            node.outputs["Value"].default_value = 0.35
            tree.link(node, "Value", bsdf, "Roughness")
        self.assertEqual(read_base(fakes.material("M", build=value))["roughness"], 0.35)

        def spread(tree, bsdf):   # An unreadable value mapped onto 0.6..0.9 (clamped): somewhere in that range.
            source, output = image(tree, "Alpha")
            node = tree.add("MAP_RANGE", data_type="FLOAT", interpolation_type="LINEAR", clamp=True)
            node.inputs["To Min"].default_value, node.inputs["To Max"].default_value = 0.6, 0.9
            tree.link(source, output, node, "Value")
            tree.link(node, "Result", bsdf, "Roughness")
        rough = fakes.material("M", build=spread)
        self.assertAlmostEqual(read_base(rough)["roughness"], 0.75)
        self.assertTrue(surface(rough, {"roughness": [0.5, 1.0]})[0])
        self.assertTrue(surface(rough, {"roughness": [0.7, 1.0]})[1]["inconclusive"])
        self.assertFalse(surface(rough, {"roughness": [0.0, 0.2]})[0])

    def test_a_colour_attribute_is_read_on_every_corner(self):
        # "Green leaves" from a Color Attribute (dt-ab-tree-lean trial 2): every corner's colour is green.
        def attribute(tree):
            return tree.add("VERTEX_COLOR", layer_name="leaf_tint"), "Color"
        leaves = base(attribute)
        green = [(0.03 + 0.01 * (i % 3), 0.13, 0.006) for i in range(8)]
        for kind in ("FLOAT_COLOR", "BYTE_COLOR"):
            mesh = fakes.Mesh([(0, 1.0), (0, 1.0)], [fakes.colour_attribute("leaf_tint", green, kind=kind)])
            passed, details = surface(leaves, {"color": ["green"]}, mesh)
            self.assertTrue(passed, details)
        # Without the mesh (the grader cannot see the data) it is undecided, never passed.
        self.assertTrue(surface(leaves, {"color": ["green"]})[1]["inconclusive"])
        missing = fakes.Mesh([(0, 1.0)], [fakes.colour_attribute("other", green[:4])])
        self.assertTrue(surface(leaves, {"color": ["green"]}, missing)[1]["inconclusive"])

    def test_an_attribute_times_a_colour_and_the_share_of_the_surface(self):
        # dt-ab-tree-clean-mcp trial 2: (0.24, 0.52, 0.10) multiplied by a face attribute from 0.65 to 1.43.
        def tinted(tree):
            node = fakes.colour_mix(tree, blend="MULTIPLY")
            node.inputs["Factor_Float"].default_value = 1.0
            node.inputs["A_Color"].default_value = (0.24, 0.52, 0.10, 1.0)
            tree.link(tree.add("ATTRIBUTE", attribute_name="leaf_tint", attribute_type="GEOMETRY"), "Fac", node,
                      "B_Color")
            return node, "Result_Color"
        mesh = fakes.Mesh([(0, 1.0)] * 3, [fakes.float_attribute("leaf_tint", [0.65, 1.0, 1.43])])
        self.assertTrue(surface(base(tinted), {"color": ["green"]}, mesh)[0])
        # A colour attribute that is green on a third of the area and brown on the rest is not "green".
        def attribute(tree):
            return tree.add("ATTRIBUTE", attribute_name="tint", attribute_type="GEOMETRY"), "Color"
        colours = [(0.05, 0.3, 0.05)] * 4 + [(0.3, 0.17, 0.08)] * 4
        split = fakes.Mesh([(0, 1.0), (0, 2.0)], [fakes.colour_attribute("tint", colours)])
        self.assertFalse(surface(base(attribute), {"color": ["green"]}, split)[0])
        self.assertTrue(surface(base(attribute), {"color": ["warm"]}, split)[0])
        per_face = fakes.Mesh([(0, 1.0), (0, 2.0)], [fakes.colour_attribute("tint", [(0.05, 0.3, 0.05),
                                                                                     (0.3, 0.17, 0.08)], "FACE")])
        material = base(attribute)
        reading = surface_reading.read(material, material.node_tree.nodes[1], "Base Color",
                                       surface_reading.SurfaceSource(per_face, None, [0]))
        green = sum(weight for value, weight in reading if scenario_checks.colour_family(value) == "green")
        self.assertAlmostEqual(green, 1 / 3, places=2)   # Points spread by area: a third on the small face.

    def test_only_the_faces_that_show_the_material_count(self):
        def attribute(tree):
            return tree.add("ATTRIBUTE", attribute_name="tint", attribute_type="GEOMETRY"), "Color"
        colours = [(0.05, 0.3, 0.05)] * 4 + [(0.3, 0.17, 0.08)] * 4
        mesh = fakes.Mesh([(0, 1.0), (1, 9.0)], [fakes.colour_attribute("tint", colours)])
        material = base(attribute)
        green_faces = surface_reading.SurfaceSource(mesh, None, [0])
        self.assertTrue(surface_reading.decide(surface_reading.read(material, material.node_tree.nodes[1],
                                                                    "Base Color", green_faces),
                                               lambda rgb: scenario_checks.colour_family(rgb) == "green"))

    def test_noise_voronoi_brick_and_checker_are_computed_where_the_surface_is(self):
        mesh = fakes.Mesh([(0, 1.0), (0, 1.0)])

        def ramp(texture, output, *stops, **settings):
            def build(tree):
                source = tree.add(texture, name="Texture", **settings)
                coords = tree.add("TEX_COORD")
                tree.link(coords, "Object", source, "Vector")
                node = tree.add("VALTORGB", color_ramp=fakes.Ramp(*stops))
                tree.link(source, output, node, "Fac")
                return node, "Color"
            return base(build)
        green = (0.05, 0.3, 0.05, 1.0)
        # A Noise texture's Fac stays near 0.5: brown below 0.1 and green above shows green; a ramp that turns
        # green only above 0.9 shows brown. Both are decided, never left to the noise.
        self.assertTrue(surface(ramp("TEX_NOISE", "Fac", (0.1, BROWN), (0.1001, green)), {"color": ["green"]},
                                mesh)[0])
        passed, details = surface(ramp("TEX_NOISE", "Fac", (0.9, BROWN), (0.9001, green)), {"color": ["green"]}, mesh)
        self.assertEqual((passed, "inconclusive" in details), (False, False))
        # A Voronoi F1 cell colour is random per cell: its red channel spreads over 0..1.
        flat = ramp("TEX_VORONOI", "Color", (0.0, green), (1.0, green))
        self.assertTrue(surface(flat, {"color": ["green"]}, mesh)[0])

        def bricks(tree):   # The cottage's roof: terracotta tiles, a thin dark mortar.
            node = tree.add("TEX_BRICK", name="Tiles")
            node.inputs["Color1"].default_value, node.inputs["Color2"].default_value = (0.55, 0.2, 0.08, 1), (
                0.42, 0.14, 0.06, 1)
            node.inputs["Mortar"].default_value, node.inputs["Mortar Size"].default_value = (0.15, 0.15, 0.15, 1), 0.01
            node.inputs["Scale"].default_value = 1.0
            tree.link(tree.add("TEX_COORD"), "Object", node, "Vector")
            return node, "Color"
        passed, details = surface(base(bricks), {"color": ["warm"]}, mesh)
        self.assertTrue(passed, details)

        def checker(tree):   # Half warm, half green squares: half the surface is green, so "green" holds.
            node = tree.add("TEX_CHECKER", name="Squares")
            node.inputs["Color1"].default_value, node.inputs["Color2"].default_value = BROWN, green
            node.inputs["Scale"].default_value = 8.0
            tree.link(tree.add("TEX_COORD"), "Object", node, "Vector")
            return node, "Color"
        reading = surface_reading.read(base(checker), base(checker).node_tree.nodes[1], "Base Color",
                                       surface_reading.SurfaceSource(mesh, None, [0]))
        share = sum(weight for value, weight in reading if scenario_checks.colour_family(value) == "green")
        self.assertAlmostEqual(share, 0.5, delta=0.05)

    def test_a_texture_inside_a_node_group_is_computed_as_outside_it(self):
        mesh = fakes.Mesh([(0, 1.0), (0, 1.0)])
        green = (0.05, 0.3, 0.05, 1.0)

        def squares(tree, coordinate):
            node = tree.add("TEX_CHECKER", name="Squares")
            node.inputs["Color1"].default_value, node.inputs["Color2"].default_value = BROWN, green
            node.inputs["Scale"].default_value = 8.0
            if coordinate:
                tree.link(tree.add("TEX_COORD"), coordinate, node, "Vector")
            return node, "Color"

        def grouped(coordinate):
            def build(tree):
                inner = fakes.Tree()
                node, output = squares(inner, coordinate)
                inner.link(node, output, inner.add("GROUP_OUTPUT", is_active_output=True), "Socket_0")
                return tree.add("GROUP", node_tree=inner), "Socket_0"
            return base(build)

        def values(material):
            reading = surface_reading.read(material, material.node_tree.nodes[1], "Base Color",
                                           surface_reading.SurfaceSource(mesh, None, [0]))
            return [value for value, _ in reading]
        for coordinate in ("Object", None):   # None: the unlinked Vector, which reads the Generated coordinate.
            outside, inside = values(base(lambda tree: squares(tree, coordinate))), values(grouped(coordinate))
            self.assertEqual(inside, outside, coordinate)
            self.assertTrue(all(isinstance(value, tuple) for value in inside), "computed, never unknown")
            share = sum(scenario_checks.colour_family(value) == "green" for value in inside) / len(inside)
            self.assertTrue(0.3 < share < 0.7, (coordinate, share))

    def test_surface_check_never_passes_an_unread_colour_or_a_default_material(self):
        default = fakes.material("Material", base=(0.8, 0.8, 0.8, 1), roughness=0.5)
        rule = {"color": ["green", "warm"]}
        passed, details = surface(default, rule)
        self.assertEqual((passed, "inconclusive" in details), (False, False))
        scene = NS(objects=[fakes.holder("Ground")])
        self.assertEqual(checks._check_surface(None, scene, None, {"target": {"name": "Ground"}, **rule}),
                         (False, {"materials": "no material on a matching object", "objects_without": ["Ground"]}))

    def test_surface_emission_value_and_any_material_of_the_object(self):
        glass = fakes.material("Glow", base=(0.9, 0.8, 0.6, 1), roughness=0.2, emission=(1.0, 0.6, 0.25, 1),
                               strength=5.0)
        iron = fakes.material("Iron", base=(0.02, 0.02, 0.02, 1))
        scene = NS(objects=[fakes.holder("Lantern", iron, glass)])
        passed, details = checks._check_surface(None, scene, None, {"target": {"name": "Lantern"}, "emission_min": 2.0,
                                                                    "emission_color": ["warm"]})
        self.assertTrue(passed and details["material"] == "Glow")
        self.assertTrue(checks._check_surface(None, scene, None, {"target": {"name": "Lantern"}, "value": [0, 0.1]})[0])
        self.assertFalse(checks._check_surface(None, scene, None, {"target": {"name": "Lantern"},
                                                                   "transmission_min": 0.5})[0])

    def test_surface_metal_saturation_and_every_built_object(self):
        copper = fakes.material("Brushed Copper", base=(0.955, 0.637, 0.538, 1), roughness=0.35, metallic=1.0)
        rule = {"target": {"name": "Cube"}, "color": ["warm"], "saturation": [0.2, 1.0], "metallic": [0.8, 1.0],
                "roughness": [0.15, 0.65]}
        scene = NS(objects=[holder("Cube", copper)])
        passed, details = checks._check_surface(None, scene, None, rule)
        self.assertTrue(passed, details)
        self.assertEqual(details["metallic"], 1.0)
        copper.node_tree.nodes[1].inputs["Metallic"].default_value = 0.0
        problems = checks._check_surface(None, scene, None, rule)[1]["materials"][0]["problems"]
        self.assertEqual(problems, ["metallic 0.0"])
        # A mini's "in a grey stone material": every built object, and a chosen grey, not the default 0.8.
        stone = fakes.material("Grey Stone", base=(0.42, 0.42, 0.41, 1), roughness=0.85)
        default = fakes.material("Material", base=(0.8, 0.8, 0.8, 1), roughness=0.5)
        grey = {"target": {"built": True}, "all": True, "saturation": [0.0, 0.25], "value": [0.05, 0.7],
                "roughness": [0.35, 1.0]}
        parts = [holder("Deck", stone, location=(0, 0, 0.1), dimensions=(4, 1.2, 0.15)),
                 holder("Post", stone, location=(1.9, 0.5, 0.6), dimensions=(0.1, 0.1, 1))]
        self.assertTrue(checks._check_surface(None, self.scene(*parts), None, grey)[0])
        bare = holder("Rail", location=(0, 0.5, 1), dimensions=(4, 0.1, 0.1))
        passed, details = checks._check_surface(None, self.scene(*parts, bare), None, grey)
        self.assertEqual((passed, details["objects_without"]), (False, ["Rail"]))
        plain = holder("Rail", default, location=(0, 0.5, 1), dimensions=(4, 0.1, 0.1))
        self.assertFalse(checks._check_surface(None, self.scene(*parts, plain), None, grey)[0])
        self.assertTrue(checks._check_surface(None, self.scene(*parts, plain), None, dict(grey, all=False))[0])

    def scene(self, *objects):
        return NS(objects=list(objects))


class SceneCheckTests(unittest.TestCase):
    def hit(self, name, parent=None):
        obj = NS(original=NS(name=name), parent=NS(original=NS(name=parent)) if parent else None)
        return NS(ray_cast=lambda depsgraph, origin, direction, distance: (True, FakeVector((0, -2.75, 1)), None, 0,
                                                                            obj, None))

    def test_visible_asks_for_the_first_thing_a_ray_meets(self):
        rule = {"target": {"name": "Cottage Door"}, "origin": [0, -8, 1], "direction": [0, 1, 0]}
        with mathutils():
            self.assertTrue(checks._check_visible(None, self.hit("Cottage Door"), None, rule)[0])
            # A differently named child is another part (the brief's naming rule), not the door.
            self.assertFalse(checks._check_visible(None, self.hit("Door Handle", parent="Cottage Door"), None, rule)[0])
            passed, details = checks._check_visible(None, self.hit("Cottage Walls"), None, rule)
            self.assertEqual((passed, details["first_object"]), (False, "Cottage Walls"))
            nothing = NS(ray_cast=lambda *args, **kwargs: (False, None, None, 0, None, None))
            self.assertEqual(checks._check_visible(None, nothing, None, rule), (False, {"hit": False}))

    def test_detail_counts_own_vertices_and_instances(self):
        def instance(name, vertices, is_instance=False, parent=None):
            return NS(object=NS(type="MESH", data=NS(vertices=[0] * vertices, polygons=[]), original=NS(name=name)),
                      is_instance=is_instance, parent=NS(original=NS(name=parent)) if parent else None)
        graph = NS(object_instances=[instance("Tree Foliage", 600)] + [
            instance("Leaf", 12, True, "Tree Foliage") for _ in range(900)])
        rule = {"target": {"name": "Tree Foliage"}, "elements": [1500, float("inf")]}
        passed, details = checks._check_detail(None, None, graph, rule)
        self.assertTrue(passed)
        self.assertEqual((details["vertices"], details["instances"], details["elements"]), (600, 900, 1500))
        ball = NS(object_instances=[instance("Tree Foliage", 482)])
        self.assertFalse(checks._check_detail(None, None, ball, rule)[0])

    def hygiene(self, objects, orphans=None, session=None, **rule):
        """``orphans``: what a save would keep unused (the default count); ``session``: the zero-user data."""
        scene = NS(objects=Objects(objects))
        rule = dict({"absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
                     "max_unused": 12}, **rule)
        with mock.patch("scenario_hygiene.saved_orphans", return_value=orphans or {}), \
                mock.patch("scenario_hygiene.orphans", return_value=session or {}):
            return checks._check_hygiene(None, scene, None, rule)

    def test_unused_data_is_counted_as_a_save_writes_it(self):
        stone = fakes.material("Stone")
        tidy = [holder("Ground", stone)]
        dropped = {"materials": ["Try %d" % n for n in range(30)]}   # Zero-user data: a save drops it.
        passed, details = self.hygiene(tidy, orphans={}, session=dropped)
        self.assertEqual((passed, details["unused"], details["unused_in_session"], details["unused_counted"]),
                         (True, 0, 30, "in a save"))
        passed, details = self.hygiene(tidy, orphans={}, session=dropped, unused_counted="session")
        self.assertEqual((passed, details["unused"], details["unused_counted"]), (False, 30, "in the session"))

    def test_saved_orphans_are_what_only_dropped_data_uses(self):
        import scenario_hygiene
        class Block:   # Hashable, as Blender's data-blocks are.
            def __init__(self, name, users, fake=False):
                self.name, self.users, self.use_fake_user = name, users, fake
        block = Block
        deleted_mesh = block("Spare Mesh", 0)                  # A deleted object's mesh: the save drops it.
        kept_mesh = block("Wall Mesh", 1)
        orphaned, used, kept, lonely = block("Spare", 1), block("Stone", 2), block("Kept", 1, True), block("Lone", 0)
        users = {orphaned: {deleted_mesh}, used: {deleted_mesh, kept_mesh}}
        data = NS(user_map=lambda subset: {key: users.get(key, set()) for key in subset},
                  **{name: [] for name in scenario_hygiene.ORPHAN_COLLECTIONS})
        data.materials, data.meshes = [orphaned, used, kept, lonely], [deleted_mesh, kept_mesh]
        self.assertEqual(scenario_hygiene.saved_orphans(NS(data=data)), {"materials": ["Spare"]})

    def test_hygiene_wants_a_tidy_file(self):
        stone = fakes.material("Stone")
        tidy = [holder("Ground", stone), holder("Footbridge Deck", stone), NS(name="Sun", type="LIGHT", hide_render=False)]
        passed, details = self.hygiene(tidy, {"materials": ["Dots Stroke"], "meshes": ["Cube"], "cameras": ["Camera"]})
        self.assertTrue(passed, details)
        self.assertEqual(details["unused"], 2)                    # The factory file's own orphan is not counted.
        passed, details = self.hygiene(tidy + [holder("Cube", stone)])
        self.assertEqual(details["problems"], ["default objects left", "default-named objects"])
        passed, details = self.hygiene(tidy + [holder("Sphere.003", stone)])
        self.assertEqual(details["problems"], ["default-named objects"])
        passed, details = self.hygiene(tidy + [holder("Stone 01")])
        self.assertEqual((details["problems"], details["meshes_without_material"]),
                         (["meshes without a material"], ["Stone 01"]))
        self.assertTrue(self.hygiene(tidy + [holder("Guide", hide_render=True)])[0])   # Not in the render: not judged.
        passed, details = self.hygiene(tidy, {"meshes": ["Try %d" % n for n in range(13)]})
        self.assertEqual(details["problems"], ["unused data-blocks"])
        self.assertTrue(self.hygiene(tidy, {"meshes": ["Try %d" % n for n in range(13)]}, max_unused=20)[0])

    def test_a_material_set_by_modifiers_counts(self):
        stone = fakes.material("Stone")
        scattered = holder("Grass")
        scattered.evaluated_get = lambda depsgraph: NS(material_slots=[], data=NS(materials=[stone]))
        self.assertEqual([m.name for m in checks._evaluated_materials(scattered, None)], ["Stone"])
        self.assertTrue(self.hygiene([scattered])[0])

    def test_kinds_are_registered_with_the_scenario_checks(self):
        for kind, function in checks.CHECKS.items():
            self.assertIs(scenario_checks.CHECKS[kind], function)
        result = scenario_checks.evaluate(NS(context=NS(evaluated_depsgraph_get=lambda: None)), NS(objects=[]),
                                          [{"kind": "surface", "target": {"name": "X"}, "label": "x"}])
        self.assertEqual((result["success"], result["results"][0]["passed"]), (False, False))


class PixelTests(unittest.TestCase):
    PICTURE = {"pixels": 2_073_600, "mean": 0.42, "spread": 0.18, "dark_share": 0.03,
               "thumbnail": [0.2 + 0.001 * index for index in range(576)]}
    CUBE = [0.26] * 576

    def passed(self, stats, baselines=None):
        baselines = {"default scene": self.CUBE} if baselines is None else baselines
        return [entry["passed"] for entry in checks.pixel_checks(stats, baselines, "still")]

    def test_a_real_picture_passes(self):
        self.assertEqual(self.passed(self.PICTURE), [True, True, True])
        labels = [entry["label"] for entry in checks.pixel_checks(self.PICTURE, {"default scene": self.CUBE}, "still")]
        self.assertEqual(labels, ["still: not empty", "still: not black", "still: not the default scene"])

    def test_empty_black_and_default_scene_stills_fail(self):
        flat = dict(self.PICTURE, spread=0.004)
        black = dict(self.PICTURE, mean=0.006, spread=0.02, dark_share=0.995)
        night = dict(self.PICTURE, mean=0.05, dark_share=0.99)
        default = dict(self.PICTURE, thumbnail=[0.262] * 576)
        self.assertEqual(self.passed(flat), [False, True, True])
        self.assertEqual(self.passed(black), [True, False, True])
        self.assertEqual(self.passed(night), [True, False, True])
        self.assertEqual(self.passed(default), [True, True, False])
        self.assertEqual(self.passed(default, {"default scene": self.CUBE, "from the brief's camera": [0.9] * 576}),
                         [True, True, False])                     # Like either render of the factory scene.

    def test_nothing_is_assumed_about_a_still_that_could_not_be_read(self):
        for stats in (None, {}, {"error": "ValueError: no pixels"}, {"pixels": 0}):
            self.assertEqual(self.passed(stats), [False, False, False])
        self.assertEqual(self.passed(self.PICTURE, {}), [True, True, False])            # No baseline: not proven.
        self.assertEqual(self.passed(self.PICTURE, {"default scene": None}), [True, True, False])
        self.assertIsNone(checks.difference([0.1], [0.1, 0.2]))
        self.assertAlmostEqual(checks.difference([0.0, 1.0], [0.5, 0.5]), 0.5)

    @unittest.skipIf(numpy is None, "NumPy is not installed")
    def test_statistics_of_pixel_arrays(self):
        black = numpy.zeros((90, 160, 3))
        facts = images.statistics(black)
        self.assertEqual((facts["pixels"], facts["size"], facts["mean"], facts["spread"], facts["dark_share"]),
                         (14400, [160, 90], 0.0, 0.0, 1.0))
        self.assertEqual(len(facts["thumbnail"]), images.THUMBNAIL[0] * images.THUMBNAIL[1])
        half = numpy.zeros((90, 160, 3))
        half[:, 80:] = 1.0
        facts = images.statistics(half)
        self.assertEqual((facts["mean"], facts["spread"], facts["dark_share"]), (0.5, 0.5, 0.5))
        self.assertEqual((facts["thumbnail"][0], facts["thumbnail"][images.THUMBNAIL[0] - 1]), (0.0, 1.0))
        self.assertEqual(self.passed(facts, {"default scene": images.statistics(black)["thumbnail"]}), [True, True, True])
        self.assertEqual(self.passed(images.statistics(black), {"default scene": facts["thumbnail"]}), [False, False, True])
        self.assertEqual(images.statistics(numpy.full((4, 4, 3), 0.5))["thumbnail"], [0.5] * 16)   # Smaller than the grid.

    @unittest.skipIf(numpy is None, "NumPy is not installed")
    def test_crops_resizing_and_the_display_curve(self):
        picture = numpy.arange(6 * 8 * 3, dtype=float).reshape(6, 8, 3) / 143.0
        part = images.cropped(picture, (2, 1, 6, 4))
        self.assertEqual(part.shape, (3, 4, 3))
        self.assertTrue(numpy.array_equal(part, picture[1:4, 2:6]))
        enlarged = images.cropped(picture, (2, 1, 6, 4), 3)
        self.assertEqual(enlarged.shape, (9, 12, 3))
        self.assertAlmostEqual(float(enlarged.mean()), float(part.mean()), places=2)
        with self.assertRaises(ValueError):
            images.cropped(picture, (4, 0, 12, 4))
        self.assertEqual(images.resized(numpy.full((5, 7, 3), 0.25), 14, 10).shape, (10, 14, 3))
        linear = numpy.array([[[0.0, 0.18, 1.0]]])
        self.assertEqual([round(float(v), 3) for v in images.to_display(linear)[0, 0]], [0.0, 0.461, 1.0])


if __name__ == "__main__":
    unittest.main()
