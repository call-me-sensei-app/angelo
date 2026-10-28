# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""node_edit and the node_graph check: schemas, every rejection, derived checks, revision, predicate and validity.

Dependency-free, with RNA stand-ins. Private staging, sharing, commit,
rollback, Undo/Redo, preview equality and evaluation bounds run in the
compiled app (test_blender_node_edit.py, test_blender_program_commit_effects.py).
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import node_edit_commit, node_edit_geometry, node_edit_spec, node_graph_check  # noqa: E402
from angelo_agent.action_catalog import affected_references, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.derived_checks import derive_checks, identity  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.validation_spec import CHECK_KINDS, validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
SCENE = "scn:" + "a" * 32 + ":3"


def node_edit(edits, target=OBJ, **tree):
    tree = tree or {"material": "Wood"}
    return {"kind": "node_edit", "target": target, "parameters": dict(tree, edits=edits)}


def code(callable_, *args):
    try:
        callable_(*args)
    except ValueError as error:
        return str(error)
    return None


class SchemaTests(unittest.TestCase):
    def test_canonical_keeps_json_types_and_names_one_tree(self):
        operation = validate_operation(node_edit([
            {"op": "add", "node": "Noise", "type": "ShaderNodeTexNoise", "inputs": {"Detail": 3}, "settings": {"label": "N"}},
            {"op": "set", "node": "Principled BSDF", "settings": {"mute": True}}]))
        add = operation["parameters"]["edits"][0]
        self.assertIsInstance(add["inputs"]["Detail"], int)  # RNA, not the schema, types it.
        self.assertEqual((add["image"], add["location"], add["parent"]), (None, None, None))
        self.assertEqual(operation["parameters"]["shared"], "REFUSE")
        self.assertEqual(resource_dependencies(operation)["mutates"], ["object", "node_trees"])
        self.assertEqual(affected_references(operation), (OBJ,))
        world = validate_operation(node_edit([{"op": "remove", "node": "Mix"}], target=SCENE, world=True))
        self.assertEqual(resource_dependencies(world)["mutates"], ["world_nodes"])
        self.assertIn("node_edit", DEFERRED_ACTIONS)
        self.assertIn("node_graph", DEFERRED_CHECKS)
        self.assertIn("node_graph", CHECK_KINDS)

    def test_rejections(self):
        cases = [
            (node_edit([{"op": "remove", "node": "A"}], material="M", modifier="G"), "TREE"),
            (node_edit([{"op": "remove", "node": "A"}], world=False), "TREE"),
            (node_edit([]), "items; 0 given"),
            (node_edit([{"op": "explode", "node": "A"}]), "unsupported value"),
            (node_edit([{"op": "remove", "node": "A", "type": "ShaderNodeMath"}]), "FIELDS"),
            (node_edit([{"op": "add", "node": "A"}]), "FIELDS"),
            (node_edit([{"op": "link", "from_node": None, "from_socket": "Out", "to_node": "B", "to_socket": "In"}]),
             "from_node requires"),
            (node_edit([{"op": "add", "node": "A", "type": "CompositorNodeBlur"}]), "NODE_TYPE"),
            (node_edit([{"op": "add", "node": "A", "type": "ShaderNodeMath", "settings": {"select": True}}]), "SETTING_DENIED"),
            (node_edit([{"op": "add", "node": "A", "type": "ShaderNodeMath", "settings": {"location_absolute": [0, 0]}}]),
             "SETTING_DENIED"),
            # A node's place stays editor state although struct paths are no longer screened as object transforms.
            (node_edit([{"op": "set", "node": "A", "settings": {"location": [0, 0]}}]), "SETTING_DENIED"),
            (node_edit([{"op": "set", "node": "A", "settings": {"name": "B"}}]), "names have their own"),
            (node_edit([{"op": "set", "node": "A", "settings": {"parent": None}}]), "ID_POINTER"),
            (node_edit([{"op": "set", "node": "A", "settings": {"image.filepath": "x"}}]), "FILE_PATH"),
            (node_edit([{"op": "set", "node": "A", "settings": {"script": 1}}]), "SCRIPT_OR_DRIVER"),
            (node_edit([{"op": "set", "node": "A", "settings": {"object": {"object": OBJ}}}]), "NO_POINTERS"),
            (node_edit([{"op": "set", "node": "A", "inputs": {"Vector": {"object": OBJ}}}]), "NO_POINTERS"),
            (node_edit([{"op": "set", "node": "A"}]), "EMPTY"),
            (node_edit([{"op": "parent", "nodes": ["F"], "frame": "F"}]), "PARENT"),
            (node_edit([{"op": "group", "nodes": ["A", "A"], "node": "G", "name": "G"}]), "DUPLICATE_NAMES"),
            (node_edit([{"op": "duplicate", "node": "A", "as": "A"}]), "NAME_TAKEN"),
            (node_edit([{"op": "add", "node": "A", "type": "ShaderNodeMath"}, {"op": "duplicate", "node": "B", "as": "A"}]),
             "NAME_TAKEN"),
            (node_edit([{"op": "interface", "action": "ADD", "name": "X", "in_out": "INPUT", "socket_type": "FLOAT"}]),
             "INTERFACE_NEEDS_GROUP"),
            (node_edit([{"op": "interface", "action": "ADD", "name": "X", "in_out": "INPUT"}], material="M", group="G"),
             "socket_type"),
            (node_edit([{"op": "interface", "action": "SET", "name": "X", "in_out": "INPUT"}], material="M", group="G"),
             "SET needs"),
            (node_edit([{"op": "interface", "action": "REMOVE", "name": "X", "in_out": "INPUT", "default": 1}],
                       material="M", group="G"), "REMOVE takes"),
            (node_edit([{"op": "link", "from_node": "A", "from_socket": "Out\n", "to_node": "B", "to_socket": "In"}]),
             "closed schema"),
            (node_edit([{"op": "link", "from_node": "A", "from_socket": "Out*", "to_node": "B", "to_socket": "In"}]),
             "SOCKET_NAME"),
        ]
        for operation, expected in cases:
            with self.subTest(expected=expected, operation=operation["parameters"]["edits"][:1]):
                self.assertIn(expected, code(validate_operation, operation) or "accepted")

    def test_each_step_is_canonical_for_its_own_op(self):
        # The advertised step is one flat object; canonical() narrows it to the op's closed variant, so a
        # step carries only its own fields and their defaults.
        steps = validate_operation(node_edit([{"op": "remove", "node": "A"}, {"op": "duplicate", "node": "A", "as": "B"},
                                              {"op": "unlink", "to_node": "B", "to_socket": "In"}]))["parameters"]["edits"]
        self.assertEqual(steps, [{"op": "remove", "node": "A", "reconnect": False},
                                 {"op": "duplicate", "node": "A", "as": "B", "keep_inputs": False, "offset": [40.0, -40.0]},
                                 {"op": "unlink", "to_node": "B", "to_socket": "In", "from_node": None, "from_socket": None}])
        self.assertIn("Fields by op", node_edit_spec.NODE_EDIT_SCHEMA["properties"]["edits"]["items"]["description"])

    def test_new_names_are_bounded_and_printable(self):
        long = "x" * 64
        self.assertIn("characters; 64 given", code(validate_operation, node_edit([{"op": "add", "node": long,
                                                                            "type": "ShaderNodeMath"}])))
        operation = validate_operation(node_edit([{"op": "add", "node": "Mix Shader.001", "type": "ShaderNodeMixShader"}]))
        self.assertEqual(operation["parameters"]["edits"][0]["node"], "Mix Shader.001")


class DerivedCheckTests(unittest.TestCase):
    def derived(self, *operations):
        return derive_checks([validate_operation(operation) for operation in operations])

    def test_steps_state_nodes_absent_nodes_and_links(self):
        checks = self.derived(node_edit([
            {"op": "add", "node": "Noise", "type": "ShaderNodeTexNoise", "inputs": {"Scale": 4.5}, "parent": "Frame"},
            {"op": "link", "from_node": "Noise", "from_socket": "Fac", "to_node": "BSDF", "to_socket": "Roughness"},
            {"op": "set", "node": "BSDF", "settings": {"label": "Main"}},
            {"op": "remove", "node": "Old"},
            {"op": "swap", "node": "Mix", "type": "ShaderNodeMixShader"}]))
        self.assertEqual(len(checks), 1)
        check = validate_check(checks[0])
        self.assertEqual(check["material"], "Wood")
        names = {entry["name"]: entry for entry in check["nodes"]}
        self.assertEqual(names["Noise"], {"name": "Noise", "type": "ShaderNodeTexNoise", "inputs": {"Scale": 4.5},
                                          "parent": "Frame"})
        self.assertEqual(names["BSDF"]["settings"], {"label": "Main"})
        self.assertEqual(names["Mix"], {"name": "Mix", "type": "ShaderNodeMixShader"})
        self.assertEqual(check["absent"], [{"name": "Old"}])
        self.assertEqual(check["links"], [{"from_node": "Noise", "from_socket": "Fac", "to_node": "BSDF",
                                           "to_socket": "Roughness"}])

    def test_later_steps_drop_what_they_may_change(self):
        checks = self.derived(node_edit([
            {"op": "add", "node": "A", "type": "ShaderNodeMath"},
            {"op": "link", "from_node": "A", "from_socket": "Value", "to_node": "B", "to_socket": "Fac"},
            {"op": "group", "nodes": ["A"], "node": "G", "name": "Group"},
            {"op": "set", "node": "C", "inputs": {"Fac": 1}}, {"op": "remove", "node": "C"},
            {"op": "add", "node": "D", "type": "ShaderNodeMath"},
            {"op": "insert", "node": "D", "from_node": "X", "from_socket": "Y", "to_node": "Z", "to_socket": "W"}]))
        check = checks[0]
        self.assertEqual({entry["name"] for entry in check["nodes"]}, {"G", "D"})
        self.assertEqual({entry["name"] for entry in check["absent"]}, {"A", "C"})
        self.assertEqual(check["links"], [])  # The grouped link moved inside the group; D's sockets are the kernel's choice.

    def test_only_steps_without_stated_results_derive_nothing(self):
        self.assertEqual(self.derived(node_edit([{"op": "detach", "node": "A"}])), [])
        interface = node_edit([{"op": "interface", "action": "ADD", "name": "X", "in_out": "INPUT", "socket_type": "FLOAT"}],
                              material="M", group="G")
        self.assertEqual(self.derived(interface), [])

    def test_keeps_and_identity_follow_the_tree(self):
        first = node_edit([{"op": "add", "node": "A", "type": "ShaderNodeMath"}])
        other_tree = node_edit([{"op": "add", "node": "B", "type": "GeometryNodeTransform"}], modifier="GN")
        same_tree = node_edit([{"op": "add", "node": "C", "type": "ShaderNodeMath"}])
        self.assertEqual(len(self.derived(first, other_tree)), 2)
        self.assertEqual([entry["name"] for entry in self.derived(first, same_tree)[0]["nodes"]], ["C"])
        principled = {"kind": "material_principled", "target": OBJ, "parameters": {"name": "New"}}
        self.assertNotIn("node_graph", [check["kind"] for check in self.derived(first, principled)])
        self.assertNotEqual(identity(self.derived(first)[0]), identity(self.derived(other_tree)[0]))
        world = self.derived(node_edit([{"op": "add", "node": "RGB", "type": "ShaderNodeRGB"}], target=SCENE, world=True))
        self.assertTrue(world[0]["world"])


class CheckSchemaTests(unittest.TestCase):
    def test_check_rejections(self):
        base = {"kind": "node_graph", "target": OBJ, "material": "Wood", "nodes": [{"type": "ShaderNodeMath"}]}
        self.assertEqual(validate_check(base)["absent"], [])
        cases = [(dict(base, modifier="GN"), "exactly one tree"), (dict(base, material=None), "supported closed"),
                 ({"kind": "node_graph", "target": OBJ, "material": "Wood"}, "needs nodes"),
                 (dict(base, nodes=[{}]), "supported closed"), (dict(base, nodes=[{"label": "x"}]), "supported closed"),
                 (dict(base, nodes=[{"name": "A", "settings": {"node_tree": 1}}]), "ID_POINTER"),
                 (dict(base, links=[{"from_socket": "A", "to_node": "B", "to_socket": "C"}]), "each end node"),
                 (dict(base, unlinked=[{"socket": "Fac"}]), "names its node"), (dict(base, world=False), "exactly one"),
                 (dict(base, nodes=[{"type": "Bad Type"}]), "supported closed")]
        for check, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, code(validate_check, check) or "accepted")


def socket(identifier, kind="VALUE", value=0.0, linked=False, name=None, enabled=True, hide=False):
    return SimpleNamespace(identifier=identifier, name=name or identifier, type=kind, default_value=value,
                           is_linked=linked, enabled=enabled, hide=hide, links=[])


class Node(SimpleNamespace):
    def __init__(self, name, kind, inputs=(), outputs=(), parent=None, **settings):
        super().__init__(name=name, bl_idname=kind, inputs=list(inputs), outputs=list(outputs), parent=parent,
                         label=settings.pop("label", ""), **settings)
        for item in self.inputs + self.outputs:
            item.node = self

    def as_pointer(self):
        return id(self)


def link(source, output, target, input_name, valid=True, muted=False):
    return SimpleNamespace(from_node=source, from_socket=next(s for s in source.outputs if s.identifier == output),
                           to_node=target, to_socket=next(s for s in target.inputs if s.identifier == input_name),
                           is_valid=valid, is_muted=muted)


def tree(nodes, links, name="Tree"):
    return SimpleNamespace(name=name, nodes=nodes, links=links, interface=SimpleNamespace(items_tree=[]),
                           as_pointer=lambda: id(nodes))


class PredicateTests(unittest.TestCase):
    def setUp(self):
        self.noise = Node("Noise", "ShaderNodeTexNoise", [socket("Scale", value=5.0)], [socket("Fac")])
        self.bsdf = Node("BSDF", "ShaderNodeBsdfPrincipled", [socket("Roughness", linked=True), socket("Metallic")],
                         [socket("BSDF", "SHADER")])
        self.tree = tree([self.noise, self.bsdf], [link(self.noise, "Fac", self.bsdf, "Roughness")])
        material = SimpleNamespace(name="Wood", node_tree=self.tree)
        self.owner = SimpleNamespace(material_slots=[SimpleNamespace(material=material)], modifiers={})

    def evaluate(self, **fields):
        check = validate_check(dict({"kind": "node_graph", "target": OBJ, "material": "Wood"}, **fields))
        return node_graph_check.evaluate(self.owner, check)

    def test_nodes_links_absent_and_unlinked(self):
        passed, observed = self.evaluate(nodes=[{"type": "ShaderNodeTexNoise", "inputs": {"Scale": 5.0000001}}],
                                         links=[{"from_type": "ShaderNodeTexNoise", "from_socket": "Fac",
                                                 "to_node": "BSDF", "to_socket": "Roughness"}],
                                         absent=[{"name": "Old"}], unlinked=[{"node": "BSDF", "socket": "Metallic"}])
        self.assertTrue(passed, observed)
        self.assertEqual((observed["nodes"], observed["links"]), (2, 1))

    def test_each_mismatch_fails(self):
        cases = [{"nodes": [{"name": "Noise", "inputs": {"Scale": 6}}]}, {"nodes": [{"name": "Noise", "parent": "Frame"}]},
                 {"absent": [{"type": "ShaderNodeTexNoise"}]}, {"unlinked": [{"node": "BSDF", "socket": "Roughness"}]},
                 {"links": [{"from_node": "Noise", "from_socket": "Fac", "to_node": "BSDF", "to_socket": "Metallic"}]},
                 {"nodes": [{"name": "Missing"}]}]
        for fields in cases:
            with self.subTest(fields=fields):
                passed, observed = self.evaluate(**fields)
                self.assertFalse(passed)
                self.assertTrue(observed["mismatched"])

    def test_invalid_or_muted_links_do_not_count_and_missing_trees_fail(self):
        self.tree.links[0].is_muted = True
        self.assertFalse(self.evaluate(links=[{"from_node": "Noise", "from_socket": "Fac", "to_node": "BSDF",
                                               "to_socket": "Roughness"}])[0])
        passed, observed = node_graph_check.evaluate(self.owner, validate_check(
            {"kind": "node_graph", "target": OBJ, "material": "Other", "nodes": [{"name": "Noise"}]}))
        self.assertEqual((passed, observed["reason"]), (False, "tree_not_found"))


class Work:
    def __init__(self):
        self.items = 0

    def take(self, items=0, elements=0):
        self.items += items


class RevisionTests(unittest.TestCase):
    def owner(self, frame=None, name="Noise", interface=()):
        noise = Node(name, "ShaderNodeTexNoise", parent=frame)
        graph = tree([noise], [])
        graph.interface.items_tree = list(interface)
        material = SimpleNamespace(name="Wood", node_tree=graph)
        return SimpleNamespace(material_slots=[SimpleNamespace(material=material)], modifiers=[])

    def test_frames_names_and_interface_join_the_revision(self):
        # Node settings by RNA path (label, colour, mute...) need the installed RNA: test_blender_node_edit.
        unsupported = []
        socket_item = SimpleNamespace(item_type="SOCKET", name="Fac", identifier="Socket_0", in_out="INPUT",
                                      socket_type="NodeSocketFloat", default_value=0.5)
        revisions = {node_graph_check.trees_revision(owner, Work(), unsupported) for owner in (
            self.owner(), self.owner(frame=SimpleNamespace(name="Frame")), self.owner(name="Other"),
            self.owner(interface=[socket_item]))}
        self.assertEqual(len(revisions), 4)
        self.assertEqual(unsupported, [])
        self.assertIsNone(node_graph_check.trees_revision(SimpleNamespace(material_slots=[], modifiers=[]), Work(), []))

    def test_cycles_and_depth_are_limitations(self):
        group = SimpleNamespace(name="G", nodes=[], links=[], interface=SimpleNamespace(items_tree=[]))
        node = Node("Group", "ShaderNodeGroup", node_tree=group)
        group.nodes.append(node)  # The group contains itself.
        group.as_pointer = lambda: 99
        unsupported = []
        node_graph_check._tree_state(group, Work(), unsupported, 0, frozenset())
        self.assertIn("node_group_cycle_or_depth", unsupported)


class ValidityTests(unittest.TestCase):
    def state(self, nodes, links):
        from angelo_agent import node_edit_ops
        state = node_edit_ops.EditState.__new__(node_edit_ops.EditState)
        state.tree, state.family, state.outputs, state.surfaces = tree(nodes, links), "SHADER", [], []
        return state

    def test_cycles_and_invalid_links_are_refused(self):
        a = Node("A", "ShaderNodeMath", [socket("Value")], [socket("Value")])
        b = Node("B", "ShaderNodeMath", [socket("Value")], [socket("Value")])
        self.state([a, b], [link(a, "Value", b, "Value")]).validate()
        cyclic = self.state([a, b], [link(a, "Value", b, "Value"), link(b, "Value", a, "Value")])
        self.assertIn("CYCLE", code(cyclic.validate))
        self.assertIn("INVALID_LINK", code(self.state([a, b], [link(a, "Value", b, "Value", valid=False)]).validate))

    def test_outputs_and_surfaces_are_kept(self):
        output = Node("Material Output", "ShaderNodeOutputMaterial", [socket("Surface", "SHADER", linked=False)])
        state = self.state([output], [])
        state.outputs, state.surfaces = ["Material Output", "Second"], []
        self.assertIn("OUTPUT_NODES", code(state.validate))
        state.outputs, state.surfaces = ["Material Output"], ["Material Output"]
        self.assertIn("OUTPUT_DISCONNECTED", code(state.validate))


class CopyAndSizeTests(unittest.TestCase):
    def test_a_copy_that_differs_is_removed_and_refused(self):
        from angelo_agent import node_copy

        class Nodes(list):
            def new(self, kind):
                node = Node("copy", kind, [socket("Fac")])  # The rebuild lacks the source's second socket.
                node.bl_rna, node.select = SimpleNamespace(properties=[]), True
                node.keys = lambda: []
                self.append(node)
                return node

            def remove(self, node):
                list.remove(self, node)

        source = Node("Capture", "GeometryNodeCaptureAttribute", [socket("Fac"), socket("Value")])
        source.bl_rna, source.keys = SimpleNamespace(properties=[]), lambda: []
        target = SimpleNamespace(nodes=Nodes())
        self.assertIn("NODE_COPY_INEXACT", code(node_copy.copy_node, source, target))
        self.assertEqual(list(target.nodes), [])

    def test_tree_size_is_bounded(self):
        from angelo_agent import node_edit_ops
        nodes = [Node("n%d" % index, "ShaderNodeMath") for index in range(node_edit_spec.MAX_TREE_NODES + 1)]
        state = node_edit_ops.EditState.__new__(node_edit_ops.EditState)
        state.tree, state.family, state.outputs, state.surfaces = tree(nodes, []), "SHADER", [], []
        self.assertIn("TREE_SIZE", code(state.validate))

    def test_fresh_clone_markers_win_over_copied_ones(self):
        block = {node_edit_spec.EDITED: 1, node_edit_spec.SOURCE: 2}
        self.assertFalse(node_edit_spec.is_edited(block))
        self.assertTrue(node_edit_spec.is_edited({node_edit_spec.EDITED: 1}))


class ObservationTests(unittest.TestCase):
    def test_world_graph_lists_names_types_and_links_bounded(self):
        from angelo_agent.scene_settings import world_graph_names
        gain = Node("Gain", "ShaderNodeValue", outputs=[socket("Value")])
        background = Node("Background", "ShaderNodeBackground", [socket("Strength")])
        world = SimpleNamespace(node_tree=tree([gain, background], [link(gain, "Value", background, "Strength")]))
        self.assertEqual(world_graph_names(world), {"nodes": [["Gain", "ShaderNodeValue"],
                                                              ["Background", "ShaderNodeBackground"]],
                                                    "links": [["Gain", "Value", "Background", "Strength"]],
                                                    "truncated": False})
        self.assertTrue(world_graph_names(world, limit=1)["truncated"])
        self.assertIsNone(world_graph_names(None))


class GeometryContractTests(unittest.TestCase):
    def test_literal_inputs_and_socket_keys(self):
        self.assertEqual(node_edit_geometry._literal(3), 3)
        self.assertEqual(node_edit_geometry._literal((1, 2, 3)), [1.0, 2.0, 3.0])
        for value in ("", "a\nb", SimpleNamespace(users=1), [[1, 2], [3, 4]], None, [True]):
            with self.subTest(value=value):
                self.assertIsNone(node_edit_geometry._literal(value))
        group_input = Node("in", "NodeGroupInput", outputs=[socket("Socket_0", "GEOMETRY", name="Geometry")])
        math = Node("m", "ShaderNodeMath", [socket("Value_001", name="Value")])
        self.assertEqual(node_edit_geometry._key(group_input.outputs[0]), "Geometry")
        self.assertEqual(node_edit_geometry._key(math.inputs[0]), "Value_001")

    def test_provenance_states_unlinked_literals_and_links(self):
        source = Node("in", "NodeGroupInput", outputs=[socket("Socket_0", "GEOMETRY", name="Geometry")])
        scatter = Node("scatter", "GeometryNodeDistributePointsOnFaces",
                       [socket("Mesh", "GEOMETRY", linked=True), socket("Density", value=5.0),
                        SimpleNamespace(identifier="Selection", name="Selection", type="BOOLEAN", default_value=True,
                                        is_linked=False, hide_value=True)],
                       [socket("Points", "GEOMETRY")])
        out = Node("out", "NodeGroupOutput", [socket("Socket_1", "GEOMETRY", linked=True, name="Geometry")])
        graph = tree([source, scatter, out], [link(source, "Socket_0", scatter, "Mesh"),
                                              link(scatter, "Points", out, "Socket_1")])
        parameters = node_edit_geometry.provenance(graph, "Scatter")
        self.assertEqual(parameters["nodes"][1]["inputs"], {"Density": 5.0})  # Linked and implicit-field inputs stay out.
        self.assertEqual(parameters["links"][1], {"from_node": "scatter", "from_socket": "Points", "to_node": "out",
                                                  "to_socket": "Geometry"})
        validate_operation({"kind": "geometry_nodes", "target": "metadata", "parameters": parameters})

    def test_a_stack_over_its_bound_is_refused_as_budget_with_the_cause_policy(self):
        """A policy code (the stack's LIMIT_EXCEEDED) is never node_edit's domain code: the refusal stays BUDGET."""
        class Group(SimpleNamespace):  # A node group: hashable, with ID properties.
            __hash__ = object.__hash__

            def __setitem__(self, key, value):
                self.stored = {key: value}

        source = Node("in", "NodeGroupInput", outputs=[socket("Socket_0", "GEOMETRY", name="Geometry")])
        scatter = Node("scatter", "GeometryNodeDistributePointsOnFaces",
                       [socket("Mesh", "GEOMETRY", linked=True), socket("Density", value=1e9)],
                       [socket("Points", "GEOMETRY")])
        out = Node("out", "NodeGroupOutput", [socket("Socket_1", "GEOMETRY", linked=True, name="Geometry")])
        graph = Group(name="Scatter", nodes=[source, scatter, out], interface=SimpleNamespace(items_tree=[]),
                      links=[link(source, "Socket_0", scatter, "Mesh"), link(scatter, "Points", out, "Socket_1")])
        causes = [
            (RefusalError("Modifier stack exceeds the conservative evaluated topology budget", code="LIMIT_EXCEEDED"),
             "LIMIT_EXCEEDED"),
            (RefusalError("Existing modifier topology is outside the bounded native capability: X", code="UNSUPPORTED"),
             "UNSUPPORTED"),
            (ValueError("an unclassified failure"), "UNKNOWN"),
        ]
        for cause, policy in causes:
            def bound(context, obj, cause=cause):
                raise cause
            stubs = {"angelo_agent.action_budget": SimpleNamespace(_modifier_bound=bound),
                     "angelo_agent.action_node_graphs": SimpleNamespace(_audit_geometry_tree=lambda tree, params: None)}
            with self.subTest(policy=policy), patch.dict(sys.modules, stubs):
                with self.assertRaises(node_edit_spec.NodeEditError) as raised:
                    node_edit_geometry.refresh(SimpleNamespace(node_graphs={}), object(), graph)
                self.assertEqual((raised.exception.code, raised.exception.refusal_code), ("BUDGET", policy))
                self.assertEqual(str(raised.exception), "node_edit BUDGET: " + str(cause))


class SharingTests(unittest.TestCase):
    def test_other_users_exclude_the_target_and_the_stage_and_count_fake_users(self):
        class User:
            def __init__(self, name):
                self.name = name

            def as_pointer(self):
                return id(self)

        target, data, stage_copy, other = (User(name) for name in ("Cube", "Mesh", "__angelo_copy", "Sphere"))
        block = SimpleNamespace(use_fake_user=False)
        users = {target, data, stage_copy, other}
        count, who = node_edit_commit.other_users(block, [target, data], {stage_copy.as_pointer()}, users)
        self.assertEqual((count, who), (1, "Sphere"))
        block.use_fake_user = True
        self.assertEqual(node_edit_commit.other_users(block, [target, data, other], set(), {target, data, other}),
                         (1, "fake user"))
        self.assertEqual(node_edit_commit.other_users(SimpleNamespace(use_fake_user=False), [target], set(), {target},
                                                      extra=1)[0], 1)


if __name__ == "__main__":
    unittest.main()
