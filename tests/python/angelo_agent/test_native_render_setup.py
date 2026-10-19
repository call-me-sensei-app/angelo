# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A physical sky for ``world_configure`` and the admitted Cycles settings: schemas, rules and the revision.

Dependency-free: node graphs and scenes are small stand-ins. The real World,
the installed Cycles definitions, staging, commit, Undo and recovery are
covered by test_blender_render_setup.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import rna_rules, scene_settings, world_sky  # noqa: E402
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.action_properties import PROPERTY_PATHS  # noqa: E402
from angelo_agent.action_world_spec import WorldGraphError, graph_values, world_graph  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402
from test_native_world import Node, default_world  # noqa: E402

SCENE = "scn:" + "a" * 32 + ":1"
IMAGE = "dat:" + "c" * 32 + ":IMAGE:42"
SKY_DEFAULTS = {"sky_type": "MULTIPLE_SCATTERING", "sun_elevation": 0.2618, "sun_rotation": 0.0, "sun_disc": True,
                "sun_size": 0.0095, "sun_intensity": 1.0, "altitude": 100.0, "air_density": 1.0, "aerosol_density": 1.0,
                "ozone_density": 1.0}


def op(**parameters):
    return {"kind": "world_configure", "target": SCENE, "parameters": parameters}


def sky_world(**settings):
    world = default_world()
    tree = world.node_tree
    node = tree.add(Node("TEX_SKY", "Angelo Sky", [("Vector", None)], ["Color"]))
    for name, value in dict(SKY_DEFAULTS, **settings).items():
        setattr(node, name, value)
    tree.link(node, "Color", tree.nodes[1], "Color")
    return world


class SkySchemaTests(unittest.TestCase):
    def test_sky_is_a_closed_group_and_keeps_what_it_does_not_state(self):
        stated = validate_operation(op(sky={"sun_elevation": 0.0873, "sun_disc": False}, strength=1))["parameters"]
        self.assertEqual(stated, {"strength": 1.0, "sky": {"sun_elevation": 0.0873, "sun_disc": False}},
                         "No default is filled in: a member left out keeps the node's value")
        self.assertEqual(validate_operation(op(sky={}))["parameters"], {"sky": {}})
        self.assertEqual(validate_operation(op(sky="NONE", color=[0, 0, 0]))["parameters"]["sky"], "NONE")
        everything = {"type": "SINGLE_SCATTERING", "sun_elevation": 1.5, "sun_rotation": -3, "sun_disc": True,
                      "sun_size": 0.5, "sun_intensity": 2, "altitude": 3000, "air_density": 2, "aerosol_density": 0,
                      "ozone_density": 4}
        self.assertEqual(set(validate_operation(op(sky=everything))["parameters"]["sky"]), set(everything))
        self.assertEqual(set(everything), {"type", *world_sky.SKY_SETTINGS})

    def test_refusals(self):
        for refused in ({"sun_elevation": 1.6}, {"sun_rotation": 7}, {"sun_size": 2}, {"sun_intensity": -1},
                        {"altitude": 200000}, {"air_density": 1001}, {"type": "PREETHAM"}, {"turbidity": 3},
                        {"sun_disc": 1}, {"file": "sky.exr"}, {"sun_elevation": "5deg"}):
            with self.subTest(sky=refused), self.assertRaises(ValueError):
                validate_operation(op(sky=refused))
        for parameters in ({"sky": "OFF"}, {"sky": None}, {"sky": True}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation(op(**parameters))

    def test_one_source_feeds_the_background(self):
        # A colour beside a sky is kept under its link and reported unused (action_world.COLOR_UNUSED); it used to be
        # refused (COLOR_HIDDEN), which cost models a round.
        self.assertEqual(validate_operation(op(sky={}, color=[1, 1, 1]))["parameters"]["color"], [1., 1., 1.])
        with self.assertRaises(WorldGraphError) as refused:
            validate_operation(op(sky={}, environment=IMAGE))
        self.assertEqual(refused.exception.code, "ONE_BACKGROUND")
        validate_operation(op(sky={}, environment="NONE"))
        validate_operation(op(sky="NONE", environment=IMAGE))

    def test_the_derived_check_states_the_sky(self):
        (check,) = derive_checks([validate_operation(op(sky={"sun_elevation": 0.0873}, strength=0.8))])
        self.assertEqual(check, {"kind": "world_settings", "target": SCENE,
                                 "values": {"strength": 0.8, "sky": {"sun_elevation": 0.0873}}})
        validate_check(check)
        with self.assertRaises(ValueError):
            validate_check(dict(check, values={"sky": {"sun_elevation": 9}}))


class SkyGraphTests(unittest.TestCase):
    def test_a_sky_feeding_the_background_is_the_supported_graph(self):
        graph = world_graph(sky_world(sun_elevation=0.0873))
        self.assertEqual(graph["sky"].name, "Angelo Sky")
        self.assertIsNone(graph["environment"])
        values = graph_values(graph)
        self.assertEqual(values["sky"]["sun_elevation"], 0.0873)
        self.assertEqual(values["sky"]["type"], "MULTIPLE_SCATTERING")
        self.assertNotIn("image", values)
        self.assertIsNone(world_graph(default_world())["sky"])
        self.assertNotIn("sky", graph_values(world_graph(default_world())))

    def test_anything_else_around_a_sky_is_a_custom_graph(self):
        world = sky_world()
        tree = world.node_tree
        coordinates = tree.add(Node("TEX_COORD", "Coords", [], ["Generated"]))
        tree.link(coordinates, "Generated", tree.nodes[2], "Vector")
        with self.assertRaises(WorldGraphError):
            world_graph(world)
        stray = sky_world()
        stray.node_tree.add(Node("TEX_SKY", "Second Sky", [("Vector", None)], ["Color"]))
        with self.assertRaises(WorldGraphError):
            world_graph(stray)

    def test_matching_reads_only_the_stated_members(self):
        observed = world_sky.values(world_graph(sky_world(sun_elevation=0.5, sun_disc=False))["sky"])
        self.assertTrue(world_sky.matches({"sun_elevation": 0.5}, observed))
        self.assertTrue(world_sky.matches({"sun_elevation": 0.5000001, "sun_disc": False, "type": "MULTIPLE_SCATTERING"},
                                          observed))
        self.assertFalse(world_sky.matches({"sun_elevation": 0.4}, observed))
        self.assertFalse(world_sky.matches({"sun_disc": True}, observed))
        self.assertFalse(world_sky.matches("NONE", observed))
        self.assertTrue(world_sky.matches("NONE", "NONE"))
        self.assertFalse(world_sky.matches({"sun_elevation": 0.5}, "NONE"))


class CyclesSettingsTests(unittest.TestCase):
    def test_admitted_settings_are_named_one_by_one(self):
        self.assertEqual(len(rna_rules.CYCLES_PLAIN), len(set(rna_rules.CYCLES_PLAIN)))
        self.assertEqual(len(rna_rules.PROVEN_RUNTIME), 88)
        self.assertEqual(rna_rules.CYCLES_PASS_UPDATE, ("device", "use_denoising", "use_guiding"))
        self.assertTrue(all(path.startswith("cycles.") and path.count(".") == 1 for path in rna_rules.PROVEN_RUNTIME))
        for wanted in ("cycles.samples", "cycles.use_denoising", "cycles.device", "cycles.volume_bounces",
                       "cycles.dicing_rate", "cycles.time_limit", "cycles.denoising_prefilter"):
            self.assertIn(wanted, rna_rules.PROVEN_RUNTIME)

    def test_settings_with_unaudited_python_stay_out(self):
        for refused in ("preview_pause", "shading_system", "sampling_pattern", "denoiser", "preview_denoiser",
                        "dicing_camera", "bake_type", "debug_use_spatial_splits", "debug_bvh_type"):
            self.assertNotIn("cycles." + refused, rna_rules.PROVEN_RUNTIME)
        runtime = NS(is_runtime=True, type="BOOLEAN", is_readonly=False, identifier="shading_system")
        self.assertEqual(rna_rules.leaf_problem(None, runtime, "cycles.shading_system")[0], "RUNTIME_PROPERTY")
        admitted = NS(is_runtime=True, type="BOOLEAN", is_readonly=False, identifier="use_guiding", array_dimensions=None)
        self.assertIsNone(rna_rules.leaf_problem(None, admitted, "cycles.use_guiding"))
        # Only a scene's own render settings: the same names under a material, a light or the World are other paths.
        for path in ("active_material.cycles.emission_sampling", "data.cycles.is_portal", "world.cycles.max_bounces"):
            self.assertEqual(rna_rules.leaf_problem(None, runtime, path)[0], "RUNTIME_PROPERTY")

    def test_the_revision_covers_admitted_settings_the_report_leaves_out(self):
        def scene(**values):
            properties = {name: NS(type="INT") for name in values}
            return NS(cycles=NS(bl_rna=NS(properties=properties), **values), bl_rna=NS(properties={"cycles": NS(type="POINTER")}))

        first = scene_settings.proven_values(scene(volume_bounces=3, dicing_rate=1.0, samples=64))
        self.assertEqual(first, {"cycles.dicing_rate": 1.0, "cycles.volume_bounces": 3},
                         "Common paths are already in the report and the digest")
        self.assertIn("cycles.samples", PROPERTY_PATHS)
        self.assertNotEqual(first, scene_settings.proven_values(scene(volume_bounces=4, dicing_rate=1.0, samples=64)))
        self.assertEqual(scene_settings.proven_values(NS(bl_rna=NS(properties={}))), {}, "A build without Cycles")
        self.assertEqual(scene_settings.REVISION_VERSION, 6)


if __name__ == "__main__":
    unittest.main()
