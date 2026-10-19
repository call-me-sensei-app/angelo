# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""world_configure: schema, cross-field rules, the supported World graph, checks and discovery.

Dependency-free: node graphs are small stand-ins. Real Worlds, staging,
environment images, commit and Undo are covered by test_blender_world.py.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_catalog import resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.action_schemas import CAPABILITIES  # noqa: E402
from angelo_agent.action_world_spec import WorldGraphError, graph_values, world_graph  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

SCENE = "scn:" + "a" * 32 + ":1"
IMAGE = "dat:" + "c" * 32 + ":IMAGE:42"


def op(**parameters):
    return {"kind": "world_configure", "target": SCENE, "parameters": parameters}


class Socket:
    def __init__(self, identifier, value=None):
        self.identifier, self.default_value, self.links = identifier, value, []

    @property
    def is_linked(self):
        return bool(self.links)


class Sockets(list):
    def __getitem__(self, key):
        return next(socket for socket in self if socket.identifier == key) if isinstance(key, str) else super().__getitem__(key)


class Node:
    def __init__(self, kind, name, inputs=(), outputs=()):
        self.type, self.name, self.is_active_output, self.image = kind, name, True, None
        self.inputs = Sockets(Socket(identifier, value) for identifier, value in inputs)
        self.outputs = Sockets(Socket(identifier) for identifier in outputs)


class Tree:
    def __init__(self):
        self.nodes, self.links = [], []

    def add(self, node):
        self.nodes.append(node)
        return node

    def link(self, source, output, destination, input_):
        link = SimpleNamespace(from_node=source, from_socket=source.outputs[output], to_node=destination,
                               to_socket=destination.inputs[input_])
        destination.inputs[input_].links.append(link)
        self.links.append(link)


def default_world(environment=False, mapping=True):
    tree = Tree()
    output = tree.add(Node("OUTPUT_WORLD", "World Output", [("Surface", None), ("Volume", None)]))
    background = tree.add(Node("BACKGROUND", "Background", [("Color", [.05, .05, .05, 1.]), ("Strength", 1.), ("Weight", 0.)],
                               ["Background"]))
    tree.link(background, "Background", output, "Surface")
    if environment:
        texture = tree.add(Node("TEX_ENVIRONMENT", "Env", [("Vector", None)], ["Color"]))
        tree.link(texture, "Color", background, "Color")
        if mapping:
            mapper = tree.add(Node("MAPPING", "Map", [("Vector", None), ("Location", None), ("Rotation", [0., 0., .5]),
                                                       ("Scale", None)], ["Vector"]))
            coordinates = tree.add(Node("TEX_COORD", "Coords", [], ["Generated", "Normal"]))
            tree.link(coordinates, "Generated", mapper, "Vector")
            tree.link(mapper, "Vector", texture, "Vector")
    return SimpleNamespace(node_tree=tree)


class SchemaTests(unittest.TestCase):
    def test_accepts_closed_parameters(self):
        self.assertEqual(validate_operation(op(color=[.1, .2, .3], strength=2))["parameters"],
                         {"color": [.1, .2, .3], "strength": 2.})
        self.assertEqual(validate_operation(op(environment=IMAGE, rotation=1))["parameters"]["environment"], IMAGE)
        self.assertEqual(validate_operation(op(environment="NONE", color=[0, 0, 0]))["parameters"]["environment"], "NONE")
        self.assertEqual(resource_dependencies(op(strength=1))["mutates"], ["world"])

    def test_rejections(self):
        # A colour beside an environment is admitted and reported unused (action_world.COLOR_UNUSED).
        validate_operation(op(color=[1, 1, 1], environment=IMAGE))
        for parameters in ({},
                           {"environment": "/tmp/sky.exr"}, {"environment": "dat:" + "c" * 32 + ":MESH:4"},
                           {"strength": -1}, {"strength": 1001}, {"color": [2, 0, 0]}, {"rotation": 7},
                           {"image_path": "sky.hdr"}):
            with self.subTest(parameters=parameters):
                with self.assertRaises(ValueError):
                    validate_operation(op(**parameters))
        # The host fills in a null target where it observed the scene (scene_binding); here nothing did.
        with self.assertRaisesRegex(ValueError, "world_configure edits the bound scene: pass the scene handle as target"):
            validate_operation({"kind": "world_configure", "target": None, "parameters": {"strength": 1}})

    def test_a_rotation_beside_a_removed_environment_is_dropped(self):
        # Regression: a model that fills every field sent a rotation with environment NONE; the whole plan came back.
        operation = validate_operation(op(color=[.48, .32, .2], strength=.35, environment="NONE", rotation=0))
        self.assertEqual(operation["parameters"], {"color": [.48, .32, .2], "strength": .35, "environment": "NONE"})
        self.assertEqual(validate_operation(op(environment="NONE", rotation=1.5))["parameters"], {"environment": "NONE"})
        self.assertEqual(derive_checks([operation]), [{"kind": "world_settings", "target": SCENE,
                                                       "values": operation["parameters"]}])
        self.assertEqual(validate_operation(op(strength=2, rotation=1))["parameters"], {"strength": 2., "rotation": 1.},
                         "Kept without an environment field: the staged World decides whether there is one to turn")


class GraphTests(unittest.TestCase):
    def test_supported_graphs(self):
        self.assertIsNone(world_graph(default_world())["environment"])
        graph = world_graph(default_world(environment=True))
        self.assertEqual(graph["mapping"].name, "Map")
        self.assertEqual(graph_values(graph)["rotation"], .5)
        self.assertIsNone(world_graph(default_world(environment=True, mapping=False))["mapping"])

    def test_custom_graphs_are_refused(self):
        def refused(world):
            with self.assertRaises(WorldGraphError) as caught:
                world_graph(world)
            self.assertEqual(caught.exception.code, "CUSTOM_WORLD_GRAPH")

        refused(SimpleNamespace(node_tree=None))
        world = default_world()
        world.node_tree.add(Node("RGB", "Stray", [], ["Color"]))
        refused(world)
        world = default_world()
        rgb = world.node_tree.add(Node("RGB", "Colour", [], ["Color"]))
        background = world.node_tree.nodes[1]
        background.inputs["Color"].links.clear()
        world.node_tree.link(rgb, "Color", background, "Color")
        refused(world)
        world = default_world()
        value = world.node_tree.add(Node("VALUE", "Strength", [], ["Value"]))
        world.node_tree.link(value, "Value", world.node_tree.nodes[1], "Strength")
        refused(world)
        world = default_world()
        world.node_tree.nodes[0].is_active_output = False
        refused(world)


class CheckTests(unittest.TestCase):
    def test_world_check_is_derived_merged_and_strict(self):
        operations = [validate_operation(op(strength=2)), validate_operation(op(environment=IMAGE, rotation=1))]
        derived = derive_checks(operations)
        self.assertEqual(derived, [{"kind": "world_settings", "target": SCENE,
                                    "values": {"strength": 2., "environment": IMAGE, "rotation": 1.}}])
        self.assertEqual(validate_check(derived[0])["values"]["environment"], IMAGE)
        for values in ({}, {"environment": "sky.exr"}, {"strength": "high"}):
            with self.assertRaises(ValueError):
                validate_check({"kind": "world_settings", "target": SCENE, "values": values})

    def test_discovery_and_deferral(self):
        self.assertIn("world_configure", DEFERRED_ACTIONS)
        self.assertIn("world_settings", DEFERRED_CHECKS)
        self.assertIn("custom_world_node_graphs", CAPABILITIES["unsupported"])
        self.assertNotIn("world_settings", CAPABILITIES["unsupported"])
        self.assertIn("no file paths", CAPABILITIES["world"]["images"])


if __name__ == "__main__":
    unittest.main()
