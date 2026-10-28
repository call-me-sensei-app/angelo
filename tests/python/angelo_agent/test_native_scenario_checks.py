# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scenario benchmarks' closed checks, hygiene report and Undo probe, against stand-in Blender data.

``scenario_checks`` and ``scenario_hygiene`` run inside Blender (Angelo and
stock Blender); these tests pin their logic on the host with small stand-ins
for the scene, depsgraph and ``mathutils``. The compiled-app proof is the
reference job's self-check (every turn's checks fail before it and pass after
it), which ``scenario_references.py`` runs in stock Blender.
"""

import contextlib
import math
import os
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scenario_checks as checks  # noqa: E402
import scenario_hygiene as hygiene  # noqa: E402
import shader_fakes as fakes  # noqa: E402


class FakeVector:
    """The slice of ``mathutils.Vector`` the scenario modules use."""

    def __init__(self, values):
        self.v = [float(value) for value in values]

    x = property(lambda self: self.v[0])
    y = property(lambda self: self.v[1])
    z = property(lambda self: self.v[2])

    def __getitem__(self, index):
        return self.v[index]

    def __iter__(self):
        return iter(self.v)

    def __len__(self):
        return len(self.v)

    def __add__(self, other):
        return FakeVector(a + b for a, b in zip(self.v, other))

    def __sub__(self, other):
        return FakeVector(a - b for a, b in zip(self.v, other))

    def __mul__(self, scale):
        return FakeVector(a * scale for a in self.v)

    def __neg__(self):
        return self * -1.0

    def dot(self, other):
        return sum(a * b for a, b in zip(self.v, other))

    def cross(self, other):
        a, b = self.v, list(other)
        return FakeVector((a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]))

    @property
    def length(self):
        return math.sqrt(self.dot(self))

    def normalized(self):
        return self * (1.0 / self.length)

    def copy(self):
        return FakeVector(self.v)


class Offset:
    """A translation-only world matrix: ``matrix @ point`` and ``translation``."""

    def __init__(self, offset=(0.0, 0.0, 0.0), z_axis=(0.0, 0.0, 1.0)):
        self.translation, self.z_axis = FakeVector(offset), FakeVector(z_axis)

    def __matmul__(self, point):
        return FakeVector(point) + self.translation

    def to_3x3(self):
        return NS(col=[FakeVector((1, 0, 0)), FakeVector((0, 1, 0)), self.z_axis])


FAKE_MATHUTILS = types.ModuleType("mathutils")
FAKE_MATHUTILS.Vector = FakeVector


def mathutils():
    return mock.patch.dict(sys.modules, {"mathutils": FAKE_MATHUTILS})


def node(kind, inputs=None, outputs=None, **extra):
    return NS(type=kind, inputs=inputs or {}, outputs=outputs or [], **extra)


def socket(value=None, link=None):
    return NS(is_linked=link is not None, default_value=value, links=[NS(from_node=link)] if link else [])


class MatchingTests(unittest.TestCase):
    def test_names_prefixes_and_blender_copies(self):
        self.assertTrue(checks.matches("Valley", {"name": "Valley"}))
        self.assertTrue(checks.matches("Valley.001", {"name": "Valley"}))
        self.assertFalse(checks.matches("Valley Floor", {"name": "Valley"}))
        self.assertFalse(checks.matches("Valley.01", {"name": "Valley"}))
        self.assertTrue(checks.matches("Lamp Posts", {"prefix": "Lamp"}))
        self.assertFalse(checks.matches("Street Lamp", {"prefix": "Lamp"}))
        self.assertEqual(checks.target_text({"prefix": "Rock"}), "Rock*")

    def test_colour_families(self):
        self.assertEqual(checks.colour_family((0.55, 0.03, 0.02)), "warm")      # Car paint red.
        self.assertEqual(checks.colour_family((1.0, 0.42, 0.16)), "warm")       # Golden-hour sun.
        self.assertEqual(checks.colour_family((0.012, 0.06, 0.065)), "blue")    # Teal water.
        self.assertEqual(checks.colour_family((0.1, 0.4, 0.1)), "green")
        self.assertEqual(checks.colour_family((0.4, 0.4, 0.42)), "grey")
        self.assertEqual(checks.colour_family((0.0, 0.0, 0.0)), "grey")
        self.assertEqual(checks.colour_family((0.5, 0.1, 0.6)), "other")


class MaterialTests(unittest.TestCase):
    """The ``material`` check reads what the node graph computes (``surface_reading``), on stand-in trees."""

    def test_principled_plain_values(self):
        facts = checks.material_facts(fakes.material("Paint", base=(0.55, 0.03, 0.02, 1), roughness=0.25))
        self.assertEqual(facts["base_color"], [0.55, 0.03, 0.02])
        self.assertEqual(facts["roughness"], 0.25)
        self.assertEqual(facts["inconclusive"], [])

    def test_colour_through_rgb_and_ramp_nodes(self):
        def rgb(tree, bsdf):
            node = tree.add("RGB")
            node.outputs["Color"].default_value = (0.1, 0.2, 0.3, 1)
            tree.link(node, "Color", bsdf, "Base Color")
        self.assertEqual(checks.material_facts(fakes.material("A", build=rgb))["base_color"], [0.1, 0.2, 0.3])

        def ramp(tree, bsdf):   # An unlinked factor (0.5) reads the ramp there, not the average of its stops.
            node = tree.add("VALTORGB", color_ramp=fakes.Ramp((0.0, (0.0, 0.2, 0.0, 1)), (0.25, (0.2, 0.4, 0.0, 1))))
            tree.link(node, "Color", bsdf, "Base Color")
        self.assertEqual(checks.material_facts(fakes.material("B", build=ramp))["base_color"], [0.2, 0.4, 0.0])

    def test_texture_driven_colour_is_inconclusive_never_a_pass(self):
        def noise(tree, bsdf):   # An image texture: the grader never loads one.
            tree.link(tree.add("TEX_IMAGE", name="Noise"), "Color", bsdf, "Base Color")
        facts = checks.material_facts(fakes.material("Tex", build=noise))
        self.assertIsNone(facts["base_color"])
        self.assertIn("base color reads TEX_IMAGE 'Noise'", facts["inconclusive"])
        scene = NS(objects=[fakes.holder("Car", fakes.material("Tex", build=noise))])
        passed, details = checks._check_material(None, scene, None, {"target": {"prefix": "Car"}, "color": ["warm"]})
        self.assertFalse(passed)
        self.assertTrue(details["inconclusive"])
        self.assertIn("inconclusive: base colour", details["materials"][0]["problems"])
        self.assertTrue(checks.is_inconclusive(details))

    def test_emission_from_principled_or_an_emission_node(self):
        lantern = fakes.material("Glow", emission=(1.0, 0.55, 0.22, 1), strength=4.0)
        facts = checks.material_facts(lantern)
        self.assertAlmostEqual(facts["emission"], 4.0)
        self.assertEqual(checks.colour_family(facts["emission_color"]), "warm")
        node_glow = fakes.emission_material("Node", (1, 0.6, 0.2, 1), 3.0)
        self.assertAlmostEqual(checks.material_facts(node_glow)["emission"], 3.0)

    def test_material_without_shader_nodes_uses_viewport_values(self):
        flat = NS(name="Flat", use_nodes=True, node_tree=fakes.Tree(), diffuse_color=(0.1, 0.2, 0.9, 1), roughness=0.05)
        facts = checks.material_facts(flat)
        self.assertEqual(facts["base_color"], [0.1, 0.2, 0.9])
        self.assertEqual(facts["roughness"], 0.05)

    def test_any_matching_material_may_satisfy_every_predicate(self):
        water = fakes.material("Water", base=(0.012, 0.06, 0.065, 1), roughness=0.04)
        mud = fakes.material("Mud", base=(0.3, 0.2, 0.1, 1), roughness=0.9)
        scene = NS(objects=[fakes.holder("River", mud, water)])
        rule = {"target": {"name": "River"}, "color": ["blue", "green"], "roughness_max": 0.15}
        passed, details = checks._check_material(None, scene, None, rule)
        self.assertTrue(passed)
        self.assertEqual(details["material"], "Water")
        rule["roughness_max"] = 0.01
        passed, details = checks._check_material(None, scene, None, rule)
        self.assertFalse(passed)
        self.assertNotIn("inconclusive", details)   # Measured, not undecided.

    def test_an_unconnected_bsdf_is_not_what_the_material_shows(self):
        def stray(tree, bsdf):
            other = tree.add("BSDF_PRINCIPLED", name="Unused")
            other.inputs["Base Color"].default_value = (0.0, 0.0, 1.0, 1)
        shown = fakes.material("Red", base=(0.6, 0.02, 0.02, 1), build=stray)
        self.assertEqual(checks.colour_family(checks.material_facts(shown)["base_color"]), "warm")


class SceneValueTests(unittest.TestCase):
    def scene(self, **render):
        values = dict(resolution_x=1920, resolution_y=1080, engine="BLENDER_EEVEE", fps=24, use_motion_blur=True)
        values.update(render)
        return NS(render=NS(**values), frame_start=1, frame_end=120, camera=NS(name="Deck Camera"),
                  eevee=NS(taa_render_samples=128))

    def test_scene_values_pointers_and_floats(self):
        rule = {"values": {"render.resolution_x": 1920, "render.engine": "BLENDER_EEVEE", "camera": "Deck Camera",
                           "frame_end": 120, "render.fps": 24}}
        passed, details = checks._check_scene(None, self.scene(), None, rule)
        self.assertTrue(passed, details)
        self.assertEqual(details["values"]["camera"], "Deck Camera")
        passed, details = checks._check_scene(None, self.scene(resolution_x=960), None, rule)
        self.assertFalse(passed)
        self.assertEqual(details["problems"], ["render.resolution_x"])

    def test_a_missing_property_fails_instead_of_raising(self):
        passed, details = checks._check_scene(None, self.scene(), None, {"values": {"render.nothing": 1}})
        self.assertFalse(passed)
        self.assertIsNone(details["values"]["render.nothing"])

    # The world check (a Sky texture the output uses, the colours the sky shows) is pinned against linked
    # stand-in graphs in test_native_world_background.py.


class GeometryCheckTests(unittest.TestCase):
    def test_bbox_tolerance_and_size(self):
        rule = {"target": {"name": "Bridge Deck"}, "min": [-12, -2.5, 0.4], "max": [12, 2.5, 1.0], "tolerance": 0.3,
                "size": [[23, 25], None, None]}
        with mock.patch.object(checks, "evaluated_bounds", return_value=([-12.2, -2.5, 0.4], [12.1, 2.5, 1.0])):
            passed, details = checks._check_bbox(None, None, None, rule)
        self.assertTrue(passed, details)
        with mock.patch.object(checks, "evaluated_bounds", return_value=([-12.5, -2.5, 0.4], [12, 2.5, 1.0])):
            passed, details = checks._check_bbox(None, None, None, rule)
        self.assertEqual(details["off"], ["min.x"])
        with mock.patch.object(checks, "evaluated_bounds", return_value=None):
            self.assertEqual(checks._check_bbox(None, None, None, rule), (False, {"reason": "no matching geometry"}))

    def test_probe_expectations(self):
        hit = {"hit": True, "object": "Railing Rails", "location": [-3.0, -2.4, 1.97], "distance": 7.6}
        rule = {"origin": [-3, -10, 1.97], "direction": [0, 1, 0], "target": {"prefix": "Railing"}, "axis": "y",
                "range": [-2.55, -2.25]}
        with mock.patch.object(checks, "probe", return_value=dict(hit)):
            self.assertTrue(checks._check_probe(None, None, None, rule)[0])
            self.assertFalse(checks._check_probe(None, None, None, dict(rule, range=[0, 1]))[0])
            self.assertFalse(checks._check_probe(None, None, None, dict(rule, expect="miss"))[0])
        with mock.patch.object(checks, "probe", return_value={"hit": False, "passed_through": "Valley"}):
            self.assertTrue(checks._check_probe(None, None, None, dict(rule, expect="miss"))[0])
            self.assertFalse(checks._check_probe(None, None, None, rule)[0])

    def test_probe_passes_through_other_geometry_to_the_target(self):
        """A lamp behind a railing is still found: the ray continues past every non-matching hit."""
        hits = [(2.0, "Railing Posts"), (5.0, "Lamp Posts")]

        def ray_cast(depsgraph, start, direction, distance):
            for y, name in hits:
                if y > start.y + 1e-9 and y - start.y <= distance:
                    obj = NS(original=NS(name=name), parent=None)
                    return True, FakeVector((0.0, y, 0.0)), None, 0, obj, None
            return False, None, None, 0, None, None
        with mathutils():
            result = checks.probe(NS(ray_cast=ray_cast), None, (0, 0, 0), (0, 1, 0), 50.0, {"prefix": "Lamp"})
            self.assertTrue(result["hit"])
            self.assertEqual(result["object"], "Lamp Posts")
            self.assertAlmostEqual(result["distance"], 5.0, places=3)
            missed = checks.probe(NS(ray_cast=ray_cast), None, (0, 0, 0), (0, 1, 0), 50.0, {"prefix": "Car"})
            named = checks.probe(NS(ray_cast=ray_cast), None, (0, 0, 0), (0, 1, 0), 50.0, {"name": "Lamp"})
        self.assertEqual(missed, {"hit": False, "looked_for": "an object named Car*", "passed_through": "Railing Posts"})
        self.assertEqual(named["looked_for"], "an object named 'Lamp' or a Blender copy of it ('Lamp.001')")
        self.assertEqual(named["passed_through"], "Railing Posts")   # 'Lamp Posts' is another name, not a copy.

    def depsgraph(self, *instances):
        return NS(object_instances=list(instances))

    def mesh_instance(self, name, pieces, offset=(0, 0, 0)):
        vertices, edges = [], []
        for piece in pieces:
            start = len(vertices)
            vertices += [NS(index=start + i, co=FakeVector(p)) for i, p in enumerate(piece)]
            edges += [NS(vertices=(start + i, start + i + 1)) for i in range(len(piece) - 1)]
        mesh = NS(vertices=vertices, edges=edges, as_pointer=lambda: id(vertices))
        obj = NS(type="MESH", data=mesh, original=NS(name=name))
        return NS(object=obj, is_instance=False, parent=None, matrix_world=Offset(offset))

    def test_parts_count_connected_pieces_in_a_region(self):
        rock = [(0, 0, 0), (0.5, 0, 0), (0.5, 0.5, 0.3)]
        far_rock = [(30, 0, 0), (30.6, 0, 0)]
        instance = self.mesh_instance("Rock Field", [rock, rock, far_rock], offset=(-6, 0, -3))
        rule = {"target": {"prefix": "Rock"}, "region_min": [-8.6, -20, -5], "region_max": [-4, 20, 1.5],
                "count": [2, 2], "size": [0.4, 2.2]}
        passed, details = checks._check_parts(None, None, self.depsgraph(instance), rule)
        self.assertTrue(passed, details)
        self.assertEqual(details["pieces_in_region"], 2)   # Two identical pieces; the far one is outside.

    def test_motion_samples_frames_and_restores_the_current_frame(self):
        scene = NS(frame_current=37)
        scene.frame_set = lambda frame: setattr(scene, "frame_current", frame)
        bpy = NS(context=NS(evaluated_depsgraph_get=lambda: None))
        car_x = {1: -10.5, 60: 0.1, 120: 10.4}

        def bounds(bpy_module, graph, target):
            x = car_x[scene.frame_current]
            return [x - 2, -1.9, 1.0], [x + 2, -0.1, 2.7]
        rule = {"target": {"prefix": "Car"}, "tolerance": 0.7, "z_tolerance": 0.2,
                "frames": {1: {"centre": [-10.5, -1.0, None], "min_z": 1.0}, 60: {"centre": [0.0, -1.0, None]},
                           120: {"centre": [10.5, -1.0, None], "min_z": 1.0}}}
        with mock.patch.object(checks, "evaluated_bounds", side_effect=bounds):
            passed, details = checks._check_motion(bpy, scene, None, rule)
            self.assertTrue(passed, details)
            self.assertEqual(scene.frame_current, 37)
            car_x[60] = 3.0
            passed, details = checks._check_motion(bpy, scene, None, rule)
        self.assertFalse(passed)
        self.assertEqual(details["problems"], ["frame 60 centre.x"])
        self.assertEqual(scene.frame_current, 37)

    def test_light_and_camera_placement(self):
        tilt = math.radians(5.0)
        sun = NS(name="Sun", type="LIGHT", data=NS(type="SUN", energy=7.0, color=(1.0, 0.42, 0.16)),
                 matrix_world=Offset((25, -17, 2.6), z_axis=(math.cos(tilt), 0.0, math.sin(tilt))))
        scene = NS(objects=[sun])
        rule = {"target": {"name": "Sun"}, "type": "SUN", "elevation_deg": [4, 18], "color": ["warm"],
                "energy": [1, 12]}
        with mathutils():
            passed, facts = checks._check_light(None, scene, None, rule)
            self.assertTrue(passed, facts)
            self.assertAlmostEqual(facts["elevation_deg"], 5.0, places=2)
            sun.name = "Key Light"
            self.assertFalse(checks._check_light(None, scene, None, rule)[0])
            camera = NS(name="Deck Camera", type="CAMERA", data=NS(lens=24.0, type="PERSP"),
                        matrix_world=Offset((-11, 1.4, 2.6), z_axis=(-1.0, 0.0, 0.0)))
            scene = NS(objects=[camera], camera=camera)
            rule = {"target": {"name": "Deck Camera"}, "active": True, "location": [-11, 1.4, 2.6], "radius": 0.5,
                    "look_at": [11, 1.4, 2.6], "min_dot": 0.98, "lens": [23, 25]}
            passed, facts = checks._check_camera(None, scene, None, rule)
            self.assertTrue(passed, facts)
            scene.camera = None
            passed, facts = checks._check_camera(None, scene, None, rule)
        self.assertEqual(facts["problems"], ["not the scene camera"])


def output_node(**linked):
    """A Material/World Output node whose named inputs are linked."""
    return node("OUTPUT_MATERIAL", {name: NS(is_linked=name in linked) for name in ("Surface", "Volume", "Displacement")},
                is_active_output=True)


class LookDevelopmentCheckTests(unittest.TestCase):
    """The kinds the island scenario adds: what a build must have, never how it looks."""

    def instance(self, name, parent=None, kind="MESH", vertices=0, faces=0, slots=()):
        obj = NS(type=kind, original=NS(name=name), data=NS(vertices=[0] * vertices, polygons=[0] * faces),
                 material_slots=[NS(material=m) for m in slots])
        return NS(object=obj, is_instance=parent is not None,
                  parent=NS(original=NS(name=parent)) if parent else None, matrix_world=Offset())

    def test_modifier_types_that_render(self):
        grass = NS(name="Grass", modifiers=[NS(type="NODES", show_render=True)])
        hidden = NS(name="Grass.001", modifiers=[NS(type="NODES", show_render=False)])
        rock = NS(name="Island", modifiers=[NS(type="SUBSURF", show_render=True), NS(type="DISPLACE", show_render=True)])
        scene = NS(objects=[grass, hidden, rock, NS(name="Light")])   # A light has no modifiers at all.
        passed, details = checks._check_modifier(None, scene, None, {"target": {"name": "Grass"}, "type": "NODES"})
        self.assertTrue(passed)
        self.assertEqual(details, {"objects": {"Grass": ["NODES"]}, "count": 1})
        self.assertTrue(checks._check_modifier(None, scene, None, {"target": {"name": "Island"},
                                                                    "type": ["DISPLACE", "NODES"]})[0])
        self.assertFalse(checks._check_modifier(None, scene, None, {"target": {"name": "Island"}, "type": "NODES"})[0])
        self.assertFalse(checks._check_modifier(None, scene, None, {"target": {"name": "Light"}, "type": "NODES"})[0])

    def test_true_displacement_needs_a_link_and_a_displacing_method(self):
        def rock(method, linked=True):
            tree = NS(nodes=[output_node(Surface=1, **({"Displacement": 1} if linked else {}))])
            return NS(name="Island", material_slots=[NS(material=NS(name="Sandstone", use_nodes=True, node_tree=tree,
                                                                    displacement_method=method))])
        rule = {"target": {"name": "Island"}}
        self.assertTrue(checks._check_displacement(None, NS(objects=[rock("BOTH")]), None, rule)[0])
        self.assertTrue(checks._check_displacement(None, NS(objects=[rock("DISPLACEMENT")]), None, rule)[0])
        passed, details = checks._check_displacement(None, NS(objects=[rock("BUMP")]), None, rule)   # Bump only.
        self.assertFalse(passed)
        self.assertEqual(details["materials"][0]["method"], "BUMP")
        self.assertFalse(checks._check_displacement(None, NS(objects=[rock("BOTH", linked=False)]), None, rule)[0])
        self.assertTrue(checks._check_displacement(None, NS(objects=[rock("BUMP")]), None,
                                                   dict(rule, methods=("BUMP",)))[0])
        bare = NS(name="Island", material_slots=[])
        self.assertEqual(checks._check_displacement(None, NS(objects=[bare]), None, rule),
                         (False, {"materials": "no material on a matching object"}))
        # A material without an output node is simply not displaced.
        plain = NS(name="Plain", use_nodes=True, node_tree=NS(nodes=[node("BSDF_PRINCIPLED")]))
        self.assertFalse(checks.displacement_facts(plain)["linked"])

    def test_volume_by_object_material_or_evaluated_geometry(self):
        fog = NS(name="Fog", use_nodes=True, node_tree=NS(nodes=[output_node(Volume=1)]))
        stone = NS(name="Stone", use_nodes=True, node_tree=NS(nodes=[output_node(Surface=1)]))
        mist = NS(name="Mist", type="MESH", material_slots=[NS(material=fog)])
        island = NS(name="Island", type="MESH", material_slots=[NS(material=stone)])
        vdb = NS(name="Cloud Deck", type="VOLUME", material_slots=[])
        nodes_cloud = NS(name="Cloud Wisp 1", type="MESH", material_slots=[])   # Its Geometry Nodes make a volume.
        scene = NS(objects=[mist, island, vdb, nodes_cloud])
        graph = NS(object_instances=[self.instance("Cloud Wisp 1", kind="VOLUME"),
                                     self.instance("Mist", slots=[fog]), self.instance("Island", slots=[stone])])
        self.assertTrue(checks._check_volume(None, scene, graph, {"target": {"name": "Mist"}})[0])
        self.assertFalse(checks._check_volume(None, scene, graph, {"target": {"name": "Island"}})[0])
        passed, details = checks._check_volume(None, scene, graph, {"target": {"prefix": "Cloud"}, "count": [2, 9]})
        self.assertTrue(passed, details)
        self.assertEqual(details["volumes"], {"Cloud Deck": "volume object", "Cloud Wisp 1": "evaluated volume"})
        self.assertFalse(checks._check_volume(None, scene, graph, {"target": {"prefix": "Cloud"}, "count": [3, 9]})[0])
        world = NS(name="Sky", node_tree=NS(nodes=[node("OUTPUT_WORLD", {"Surface": NS(is_linked=True),
                                                                         "Volume": NS(is_linked=True)})]))
        self.assertTrue(checks._check_volume(None, NS(world=world), None, {"world": True})[0])
        self.assertFalse(checks._check_volume(None, NS(world=None), None, {"world": True})[0])

    def test_instances_count_what_the_named_object_scatters(self):
        graph = NS(object_instances=[self.instance("Grass")] + [self.instance("Blade", parent="Grass")] * 40
                   + [self.instance("Needle", parent="Pine 1")] * 7)
        passed, details = checks._check_instances(None, None, graph, {"target": {"name": "Grass"}, "count": [30, 1e9]})
        self.assertTrue(passed)
        self.assertEqual((details["instances"], details["from"], details["capped"]), (40, ["Grass"], False))
        self.assertFalse(checks._check_instances(None, None, graph, {"target": {"name": "Grass"}, "count": [41, 1e9]})[0])
        self.assertTrue(checks._check_instances(None, None, graph, {"target": {"prefix": "Pine"}, "count": [7, 7]})[0])
        with mock.patch.object(checks, "MAX_COUNTED_INSTANCES", 10):
            self.assertTrue(checks._check_instances(None, None, graph, {"target": {"name": "Grass"}})[1]["capped"])

    def test_geometry_counts_the_objects_own_evaluated_mesh(self):
        graph = NS(object_instances=[self.instance("Island", vertices=30000, faces=29000),
                                     self.instance("Island", parent="Scatter", vertices=900000),   # Not its own mesh.
                                     self.instance("Pond", vertices=32, faces=1)])
        rule = {"target": {"name": "Island"}, "vertices": [20000, 1e8]}
        passed, details = checks._check_geometry(None, None, graph, rule)
        self.assertTrue(passed)
        self.assertEqual((details["vertices"], details["faces"]), (30000, 29000))
        self.assertFalse(checks._check_geometry(None, None, graph, dict(rule, vertices=[40000, 1e8]))[0])
        self.assertEqual(checks._check_geometry(None, None, graph, {"target": {"name": "Tree"}})[1]["problems"],
                         ["no matching mesh"])

    def camera(self, **dof):
        values = dict(use_dof=True, aperture_fstop=2.8, focus_distance=134.0, focus_object=None)
        values.update(dof)
        camera = NS(name="Hero Camera", type="CAMERA", data=NS(lens=50.0, type="PERSP", sensor_width=36.0,
                                                              dof=NS(**values)),
                    matrix_world=Offset((-12, -148, 20), z_axis=(0.0, -1.0, 0.0)))
        return NS(objects=[camera], camera=camera)

    def test_camera_depth_of_field_and_sensor(self):
        rule = {"target": {"name": "Hero Camera"}, "active": True, "dof": True, "fstop": [1.2, 5.6],
                "focus_distance": [100, 175], "sensor": [35, 37]}
        with mathutils():
            passed, facts = checks._check_camera(None, self.camera(), None, rule)
            self.assertTrue(passed, facts)
            self.assertEqual((facts["dof"], facts["fstop"], facts["focus_distance"]), (True, 2.8, 134.0))
            self.assertEqual(checks._check_camera(None, self.camera(use_dof=False), None, rule)[1]["problems"],
                             ["depth of field"])
            self.assertEqual(checks._check_camera(None, self.camera(aperture_fstop=11.0), None, rule)[1]["problems"],
                             ["f-stop"])
            target = NS(name="Focus", matrix_world=Offset((-12, -8, 20)))   # 140 m in front of the camera.
            passed, facts = checks._check_camera(None, self.camera(focus_object=target, focus_distance=5.0), None, rule)
            self.assertTrue(passed, facts)
            self.assertEqual((facts["focus_object"], facts["focus_distance"]), ("Focus", 140.0))
            self.assertEqual(checks._check_camera(None, self.camera(focus_distance=10.0), None, rule)[1]["problems"],
                             ["focus distance"])
            # A check that does not ask about depth of field never reads it (the bridge's camera has none here).
            plain = self.camera()
            del plain.objects[0].data.dof
            self.assertTrue(checks._check_camera(None, plain, None, {"target": {"name": "Hero Camera"},
                                                                    "lens": [48, 52]})[0])


def png(width, height):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


def jpeg(width, height):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x00" * 9
    return b"\xff\xd8" + app0 + frame


def exr(width, height):
    return (b"\x76\x2f\x31\x01\x02\x00\x00\x00" + b"channels\x00chlist\x00\x01\x00\x00\x00\x00"
            + b"dataWindow\x00box2i\x00" + struct.pack("<i", 16) + struct.pack("<iiii", 0, 0, width - 1, height - 1))


class OutputTests(unittest.TestCase):
    """Files a turn must have produced: found by name in the driver's folders, sized from the header."""

    def test_image_sizes_from_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, data, size in (("a.png", png(3840, 2160), (3840, 2160)), ("b.jpg", jpeg(1920, 1080), (1920, 1080)),
                                     ("c.exr", exr(2048, 858), (2048, 858)), ("d.png", b"not an image", None)):
                path = Path(folder, name)
                path.write_bytes(data)
                with self.subTest(file=name):
                    self.assertEqual(checks.image_size(path) and tuple(checks.image_size(path)), size)

    def test_outputs_are_found_by_name_but_never_in_hidden_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "renders").mkdir()
            (root / ".angelo-job-j1").mkdir()
            (root / ".angelo-job-j1" / "island-final.png").write_bytes(png(3840, 2160))   # A job's staging.
            wanted = [{"name": "island-final.png", "min_size": [3840, 2160], "label": "the 4K render was written"}]
            missing = checks.evaluate_outputs(wanted, [root])[0]
            self.assertFalse(missing["passed"])
            self.assertEqual({key: value for key, value in missing["details"].items() if key != "final"},
                             {"files": "no file named island-final.png", "folders": 1})
            self.assertEqual(missing["details"]["final"]["source"], "requested path")
            (root / "renders" / "Island-Final.PNG").write_bytes(png(1920, 1080))
            small = checks.evaluate_outputs(wanted, [root])[0]
            self.assertFalse(small["passed"])
            self.assertEqual(small["details"]["files"][0]["problems"], ["size"])
            (root / "renders" / "Island-Final.PNG").write_bytes(png(3840, 2160))
            found = checks.evaluate_outputs(wanted, [root])[0]
            self.assertTrue(found["passed"])
            self.assertEqual((found["kind"], found["label"], found["details"]["size"]),
                             ("output", "the 4K render was written", [3840, 2160]))
            self.assertFalse(checks.evaluate_outputs([{"name": "island-final.png", "min_bytes": 10 ** 6}], [root])[0]
                             ["passed"])
            self.assertFalse(checks.evaluate_outputs(wanted, [])[0]["passed"])   # No folder is known: never a pass.
            deep = root / "a" / "b" / "c" / "d" / "e"
            deep.mkdir(parents=True)
            (deep / "too-deep.png").write_bytes(png(8, 8))
            self.assertEqual(checks.find_outputs("too-deep.png", [root]), [])

    def test_the_render_judged_is_the_one_the_turn_names_as_final(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wanted = [{"name": "island-final.png", "min_size": [3840, 2160], "label": "the 4K render was written"}]
            (root / "island-final.png").write_bytes(png(1920, 1080))     # The first render, too small.
            (root / "island-final-j4.png").write_bytes(png(3840, 2160))  # The re-render: the name was taken.
            os.utime(root / "island-final.png", (1000.0, 1000.0))
            os.utime(root / "island-final-j4.png", (2000.0, 2000.0))
            before = checks.evaluate_outputs(wanted, [root])[0]
            self.assertFalse(before["passed"], "nothing names the re-render: the requested name is judged")
            self.assertEqual(before["details"]["final"]["source"], "requested path")
            reply = "Rendered at 3840 x 2160: `island-final-j4.png` (island-final.png was kept)."
            named = checks.evaluate_outputs(wanted, [root], reply)[0]
            self.assertTrue(named["passed"])
            self.assertEqual((named["details"]["file"], named["details"]["final"]["source"]),
                             ("island-final-j4.png", "reply"))
            receipts = [{"kind": "RENDER_STILL", "status": "done", "output": "island-final.png",
                         "wrote": "island-final-j4.png"}]
            receipt = checks.evaluate_outputs(wanted, [root], "Done.", receipts)[0]
            self.assertEqual((receipt["passed"], receipt["details"]["final"]["source"]), (True, "job receipt"))

    def test_outputs_count_with_the_turns_checks(self):
        scene_result = {"success": True, "passed": 1, "total": 1, "results": [{"index": 0, "kind": "camera",
                                                                               "label": "dof", "passed": True}]}
        self.assertIs(checks.with_outputs(scene_result, None, []), scene_result)
        with tempfile.TemporaryDirectory() as folder:
            wanted = [{"name": "island-final.png", "min_size": [3840, 2160]}]
            failed = checks.with_outputs(scene_result, wanted, [folder])
            self.assertEqual((failed["success"], failed["passed"], failed["total"]), (False, 1, 2))
            self.assertEqual(failed["results"][1]["index"], 1)
            Path(folder, "island-final.png").write_bytes(png(3840, 2160))
            done = checks.with_outputs(scene_result, wanted, [folder])
            self.assertEqual((done["success"], done["passed"], done["total"]), (True, 2, 2))
            broken = dict(scene_result, success=False)
            self.assertFalse(checks.with_outputs(broken, wanted, [folder])["success"])   # The file never hides a failed check.


class EvaluateTests(unittest.TestCase):
    def test_a_broken_check_fails_alone_and_never_raises(self):
        bpy = NS(context=NS(evaluated_depsgraph_get=lambda: None))
        scene = NS(objects=[NS(name="Valley", type="MESH")])
        result = checks.evaluate(bpy, scene, [{"kind": "object", "target": {"name": "Valley"}, "label": "valley"},
                                              {"kind": "no-such-kind", "label": "unknown"},
                                              {"kind": "bbox", "target": {"name": "Valley"}, "label": "bounds"}])
        self.assertFalse(result["success"])
        self.assertEqual([entry["passed"] for entry in result["results"]], [True, False, False])
        self.assertIn("KeyError", result["results"][1]["details"]["error"])
        self.assertEqual((result["passed"], result["total"]), (1, 3))

    def test_an_empty_turn_never_passes(self):
        bpy = NS(context=NS(evaluated_depsgraph_get=lambda: None))
        self.assertFalse(checks.evaluate(bpy, NS(objects=[]), [])["success"])

    def test_object_exists_type_and_absence(self):
        scene = NS(objects=[NS(name="Cube.001", type="MESH"), NS(name="Valley", type="MESH")])
        self.assertTrue(checks._check_object(None, scene, None, {"target": {"name": "Valley"}, "type": "MESH"})[0])
        self.assertFalse(checks._check_object(None, scene, None, {"target": {"name": "Valley"}, "type": "CURVE"})[0])
        self.assertFalse(checks._check_object(None, scene, None, {"target": {"name": "Cube"}, "exists": False})[0])
        self.assertTrue(checks._check_object(None, scene, None, {"target": {"name": "Camera"}, "exists": False})[0])


class FakeData:
    def __init__(self, **collections):
        for name in hygiene.ORPHAN_COLLECTIONS:
            setattr(self, name, collections.get(name, []))


class Objects(list):
    """A ``bpy_prop_collection`` of objects: a list with ``get(name)``."""

    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


def block(name, users=1, fake=False):
    return NS(name=name, users=users, use_fake_user=fake)


def scene_object(name, parent=None, scale=(1, 1, 1), slots=("Stone",), kind="MESH"):
    return NS(name=name, parent=parent, scale=scale, type=kind,
              material_slots=[NS(material=NS(name=s) if s else None) for s in slots])


class HygieneTests(unittest.TestCase):
    def test_targets_come_from_every_check(self):
        turns = [{"checks": [{"kind": "object", "target": {"name": "Valley"}},
                             {"kind": "light", "target": {"name": "Sun"}}, {"kind": "scene", "values": {}}]},
                 {"checks": [{"kind": "bbox", "target": {"name": "Valley"}},
                             {"kind": "armature", "target": {"name": "Rig"}, "skins": {"prefix": "Body"}}]}]
        self.assertEqual(hygiene.check_targets(turns), [{"name": "Valley"}, {"name": "Sun"}, {"name": "Rig"},
                                                        {"prefix": "Body"}])

    def test_report_counts_leftovers_and_keeps_orphans_apart(self):
        car = scene_object("Car")
        objects = Objects([scene_object("Valley"), car, scene_object("Car Wheel FL", parent=car),
                           scene_object("Cube.001"), scene_object("Helper", scale=(1, -1, 1), slots=(None,))])
        scene = NS(objects=objects, collection=NS(objects=objects))
        materials = [block("Stone"), block("Stone.001"), block("Dots Stroke", users=0)]
        data = FakeData(materials=materials, meshes=[block("Cube", users=0), block("Kept", users=0, fake=True)])
        data.collections = []
        bpy = NS(data=data, context=NS(mode="OBJECT"))
        result = hygiene.report(bpy, scene, [{"name": "Valley"}, {"prefix": "Car"}], ("Cube", "Camera", "Light"),
                                baseline={"materials": ["Dots Stroke"]})
        self.assertEqual(result["unexpected_objects"], ["Cube.001", "Helper"])
        self.assertEqual(result["default_named"], ["Cube.001"])
        self.assertEqual(result["negative_scale"], ["Helper"])
        self.assertEqual(result["empty_material_slots"], ["Helper"])
        self.assertEqual(result["duplicate_materials"], ["Stone.001"])
        self.assertEqual(result["new_orphans"], {"meshes": ["Cube"]})   # The factory file's own orphan is the baseline.
        # Unexpected (2) + duplicate material + empty slot + negative scale; orphans are reported, not counted.
        self.assertEqual(result["issues"], 5)
        bpy.context.mode = "EDIT_MESH"
        self.assertEqual(hygiene.report(bpy, scene, [{"name": "Valley"}, {"prefix": "Car"}])["issues"], 6)

    def undo_bpy(self, states):
        """A stand-in whose Undo steps walk back through ``states`` (digests, newest last)."""
        position = {"index": len(states) - 1}

        def undo():
            position["index"] -= 1
        poll = lambda: position["index"] > 0
        bpy = NS(ops=NS(ed=NS(undo=mock.Mock(side_effect=undo, poll=poll))),
                 context=NS(temp_override=lambda **kw: contextlib.nullcontext()))
        return bpy, (lambda bpy_module, scene: states[position["index"]])

    def test_undo_probe_with_one_step_per_turn(self):
        digests = ["start", "t1", "t2", "t3"]
        bpy, digest = self.undo_bpy(digests)
        with mock.patch.object(hygiene, "state_digest", side_effect=digest):
            result = hygiene.undo_probe(bpy, NS(scene=None), digests)
        self.assertEqual(result["landed"], [2, 1, 0])
        self.assertEqual(result["stopped"], "reached the start")
        self.assertTrue(result["first_undo_returns_previous_turn"])
        self.assertEqual(result["turn_boundaries_reachable"], 3)
        self.assertEqual(result["steps_per_turn"], {"3-3": 1, "2-2": 1, "1-1": 1})

    def test_undo_probe_with_several_steps_and_no_undo_history(self):
        digests = ["start", "t1", "t2"]
        states = ["start", "a", "b", "t1", "c", "t2"]   # Turn 1 took three steps, turn 2 two.
        bpy, digest = self.undo_bpy(states)
        with mock.patch.object(hygiene, "state_digest", side_effect=digest):
            result = hygiene.undo_probe(bpy, NS(scene=None), digests)
        self.assertEqual(result["landed"], [None, 1, None, None, 0])
        self.assertEqual(result["steps_per_turn"], {"2-2": 2, "1-1": 3})
        self.assertFalse(result["first_undo_returns_previous_turn"])
        bpy, digest = self.undo_bpy(["only"])
        with mock.patch.object(hygiene, "state_digest", side_effect=digest):
            result = hygiene.undo_probe(bpy, NS(scene=None), ["start", "t1"])
        self.assertEqual((result["undo_steps"], result["stopped"], result["reaches_start"]),
                         (0, "nothing left to undo", False))

    def preferences(self):
        filepaths = NS(file_preview_type="AUTO")
        props = {"is_dirty": NS(is_readonly=False)}
        return NS(filepaths=filepaths, is_dirty=False, bl_rna=NS(properties=props))

    def test_save_copy_has_no_thumbnail_and_restores_preferences(self):
        prefs, seen = self.preferences(), {}

        def save(**kwargs):
            seen.update(kwargs, preview=prefs.filepaths.file_preview_type)
            prefs.is_dirty = True
            return {"FINISHED"}
        bpy = NS(context=NS(preferences=prefs, temp_override=lambda **kw: contextlib.nullcontext()),
                 ops=NS(wm=NS(save_as_mainfile=save)))
        self.assertEqual(hygiene.save_copy(bpy, NS(), "/tmp/x.blend"), "/tmp/x.blend")
        self.assertEqual((seen["preview"], seen["copy"]), ("NONE", True))
        self.assertEqual((prefs.filepaths.file_preview_type, prefs.is_dirty), ("AUTO", False))

    def test_save_copy_failure_raises_and_still_restores(self):
        prefs = self.preferences()
        bpy = NS(context=NS(preferences=prefs, temp_override=lambda **kw: contextlib.nullcontext()),
                 ops=NS(wm=NS(save_as_mainfile=lambda **kw: {"CANCELLED"})))
        with self.assertRaises(RuntimeError):
            hygiene.save_copy(bpy, NS(), "/tmp/x.blend")
        self.assertEqual(prefs.filepaths.file_preview_type, "AUTO")

    def test_finish_keeps_what_was_measured_before_a_failure(self):
        with mock.patch.object(hygiene, "report", return_value={"issues": 0}), \
                mock.patch.object(hygiene, "state_digest", return_value="final"), \
                mock.patch.object(hygiene, "save_copy", side_effect=RuntimeError("disk full")), \
                mock.patch.object(hygiene, "undo_probe") as probe:
            result = hygiene.finish(None, NS(scene=None), [], ["start"], "/tmp/x.blend")
        self.assertEqual(result["hygiene"], {"issues": 0})
        self.assertEqual(result["final_digest"], "final")
        self.assertIn("disk full", result["error"])
        probe.assert_not_called()   # Nothing changes the scene after a failed save.


if __name__ == "__main__":
    unittest.main()
