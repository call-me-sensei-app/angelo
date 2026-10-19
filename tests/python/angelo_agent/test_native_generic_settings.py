# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic, RNA-driven settings: path rules, value checks, modifier/constraint/node types and their checks.

Dependency-free, with RNA stand-ins. The installed RNA definitions, staging,
commit, read-back and Undo are covered by test_blender_generic_settings.py and
test_blender_property_paths.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import generic_settings, node_generic, rna_rules  # noqa: E402
from angelo_agent.action_catalog import affected_references, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.rna_rules import PropertyPathError  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
OTHER = "scn:" + "a" * 32 + ":9"


def code(callable_, *args, **kwargs):
    try:
        callable_(*args, **kwargs)
    except PropertyPathError as error:
        return error.code
    except ValueError as error:
        return str(error).split(":")[0]
    return None


def prop(kind, identifier="value", **fields):
    defaults = {"type": kind, "identifier": identifier, "is_readonly": False, "is_runtime": False, "is_hidden": False,
                "subtype": "NONE", "array_length": 0, "array_dimensions": (0, 0, 0), "hard_min": -10, "hard_max": 10,
                "is_enum_flag": False, "length_max": 0, "is_never_none": False,
                "fixed_type": SimpleNamespace(identifier="Object")}
    defaults.update(fields)
    return SimpleNamespace(**defaults)


class PathRuleTests(unittest.TestCase):
    def test_deny_list_codes(self):
        cases = {"render.filepath": "FILE_PATH", "output_path": "FILE_PATH", "drivers.x": "SCRIPT_OR_DRIVER",
                 "text": "SCRIPT_OR_DRIVER", "data.texture.intensity": "ID_POINTER", "data": "ID_POINTER",
                 "world": "ID_POINTER", "location": "NOT_ALLOWED", "tool_settings.use_snap": "NOT_ALLOWED",
                 "animation_data.action": "NOT_ALLOWED", "bl_rna.x": "NOT_ALLOWED", "_private": "NOT_ALLOWED",
                 "a.b.c.d.e.f": "NOT_ALLOWED", "x[0]": "CUSTOM_PROPERTY_OR_INDEX", "data.name": "NOT_ALLOWED"}
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(code(rna_rules.screen_path, path), expected)
        for path in ("data.use_path", "data.path_duration", "data.dof.aperture_ratio", "active_material.roughness", "world.mist_settings.start",
                     "settings.quality", "display.show_shadows"):
            self.assertEqual(rna_rules.screen_path(path), path)

    def test_owners_and_families(self):
        self.assertEqual((rna_rules.owner_of("render.fps"), rna_rules.family("render.fps")), ("SCENE", None))
        self.assertEqual(rna_rules.family("world.mist_settings.start"), "world")
        self.assertEqual(rna_rules.family("data.lens"), "data")
        self.assertEqual(rna_rules.family("active_material.metallic"), "material")
        self.assertEqual(rna_rules.owner_of("display.show_shadows"), "OBJECT")  # Object, never the scene's display.

    def test_leaf_and_value_rules(self):
        self.assertEqual(rna_rules.leaf_problem(None, prop("INT", is_runtime=True), "x")[0], "RUNTIME_PROPERTY")
        self.assertIsNone(rna_rules.leaf_problem(None, prop("INT", is_runtime=True), "cycles.samples"))
        self.assertEqual(rna_rules.leaf_problem(None, prop("STRING", subtype="DIR_PATH"), "x")[0], "FILE_PATH")
        self.assertEqual(rna_rules.leaf_problem(None, prop("FLOAT", array_dimensions=(4, 4, 0)), "x")[0], "NOT_A_SETTING")
        self.assertEqual(rna_rules.leaf_problem(None, prop("COLLECTION"), "x")[0], "NOT_A_SETTING")
        self.assertEqual(rna_rules.leaf_problem(None, prop("BOOLEAN", is_readonly=True), "x")[0], "RNA_READONLY")
        material = prop("POINTER", fixed_type=SimpleNamespace(identifier="Material"))
        self.assertEqual(rna_rules.leaf_problem(None, material, "x")[0], "ID_POINTER")
        cases = [(prop("INT"), 1.5, "RNA_TYPE_MISMATCH"), (prop("INT"), 11, "RNA_RANGE"), (prop("BOOLEAN"), 1, "RNA_TYPE_MISMATCH"),
                 (prop("FLOAT", array_length=3), [1, 2], "RNA_ARRAY_LENGTH"), (prop("FLOAT"), [1], "RNA_ARRAY_LENGTH"),
                 (prop("ENUM", is_enum_flag=True), "X", "RNA_TYPE_MISMATCH"), (prop("STRING", length_max=3), "abc", "STRING_TOO_LONG"),
                 (prop("POINTER"), "plain", "RNA_TYPE_MISMATCH"), (prop("POINTER", is_never_none=True), None, "RNA_TYPE_MISMATCH")]
        for definition, value, expected in cases:
            with self.subTest(value=value, expected=expected):
                self.assertEqual(code(rna_rules.check_value, definition, "x", value), expected)
        for definition, value in ((prop("FLOAT", array_length=3), [1, 2.5, 3]), (prop("ENUM", is_enum_flag=True), ["X", "Y"]),
                                  (prop("POINTER"), {"object": OTHER}), (prop("POINTER"), None), (prop("FLOAT"), 2)):
            rna_rules.check_value(definition, "x", value)

    def test_writable_groups_list_what_resolve_admits_for_a_whole_owner(self):
        """The scene settings revision walks these groups (it once covered only the common paths)."""
        def struct(identifier, *properties, base=None):
            return SimpleNamespace(identifier=identifier, properties=list(properties), base=base)

        id_type = struct("ID")
        deep = struct("Deep", prop("INT", "level"))
        nested = struct("Nested", prop("FLOAT", "amount"), prop("POINTER", "deeper", fixed_type=deep))
        render = struct("RenderSettings", prop("INT", "resolution_x"), prop("FLOAT", "border_min_x"),
                        prop("STRING", "filepath", subtype="FILE_PATH"), prop("BOOLEAN", "is_movie", is_readonly=True),
                        prop("INT", "addon_setting", is_runtime=True), prop("COLLECTION", "views"),
                        prop("POINTER", "image", fixed_type=struct("Image", base=id_type)),
                        prop("POINTER", "tool_settings", fixed_type=struct("ToolSettings", prop("INT", "x"))),
                        prop("POINTER", "nested", fixed_type=nested))
        scene = struct("Scene", prop("POINTER", "render", fixed_type=render), prop("BOOLEAN", "use_audio"),
                       prop("POINTER", "camera"), prop("BOOLEAN", "use_fake_user"),
                       prop("POINTER", "world", fixed_type=struct("World", prop("FLOAT", "mist"), base=id_type)))
        groups = rna_rules.writable_groups(scene, rna_rules.SCENE_ROOTS - {"world"}, frozenset({"render.resolution_x"}))
        paths = {".".join(segments + (leaf,)) for segments, _struct, leaves in groups for leaf in leaves}
        self.assertEqual(paths, {"use_audio", "camera", "render.border_min_x", "render.nested.amount",
                                 "render.nested.deeper.level"})
        self.assertEqual({segments: name for segments, name, _ in groups},
                         {(): "Scene", ("render",): "RenderSettings", ("render", "nested"): "Nested",
                          ("render", "nested", "deeper"): "Deep"})
        # Five segments at most, as resolve: a struct at the fifth segment holds no admissible leaf.
        chain = struct("Leafy", prop("INT", "value"))
        for name in ("d", "c", "b", "a"):
            chain = struct(name.upper(), prop("POINTER", name, fixed_type=chain))
        too_deep = struct("Scene", prop("POINTER", "render", fixed_type=chain))
        self.assertEqual(rna_rules.writable_groups(too_deep, {"render"}), [])

    def test_read_back_comparison(self):
        self.assertTrue(rna_rules.same(2, 2.0000001))
        self.assertTrue(rna_rules.same(["B", "A"], ["A", "B"]))
        self.assertFalse(rna_rules.same(True, 1))
        self.assertTrue(rna_rules.same({"object": OTHER}, {"object": OTHER}))


class TypeRuleTests(unittest.TestCase):
    def test_modifier_types(self):
        for kind in ("NODES", "MESH_CACHE", "OCEAN", "PARTICLE_INSTANCE", "FLUID"):
            self.assertIn("MODIFIER_TYPE_DENIED", code(generic_settings.check_type, kind, {"BEVEL"}))
        self.assertIsNotNone(code(generic_settings.check_type, "BEVEL", {"BEVEL"}))
        self.assertEqual(generic_settings.bound_factor("LATTICE", None), 1)
        self.assertEqual(generic_settings.bound_factor("MULTIRES", SimpleNamespace(levels=2, sculpt_levels=1)), 16)
        self.assertIn("outside the bounded", code(generic_settings.bound_factor, "UNKNOWN_TYPE", None))

    def test_node_types(self):
        for kind in ("ShaderNodeScript", "GeometryNodeGroup", "GeometryNodeRepeatInput", "GeometryNodeImportOBJ",
                     "GeometryNodeObjectInfo", "GeometryNodeVolumeCube"):
            self.assertIn("NODE_TYPE_DENIED", code(node_generic.check_type, kind, "GEOMETRY"))
        self.assertIsNotNone(code(node_generic.check_type, "GeometryNodeMeshGrid", "SHADER"))
        node_generic.check_type("ShaderNodeTexVoronoi", "SHADER")
        node_generic.check_type("FunctionNodeRandomValue", "GEOMETRY")

    def test_socket_compatibility(self):
        socket = lambda kind: SimpleNamespace(type=kind)
        self.assertTrue(node_generic.compatible(socket("VALUE"), socket("RGBA")))
        self.assertTrue(node_generic.compatible(socket("RGBA"), socket("SHADER")))
        self.assertFalse(node_generic.compatible(socket("SHADER"), socket("RGBA")))
        self.assertFalse(node_generic.compatible(socket("GEOMETRY"), socket("VALUE")))
        self.assertTrue(node_generic.compatible(socket("GEOMETRY"), socket("GEOMETRY")))

    def test_tree_bound_needs_a_rule_and_literal_amplifiers(self):
        def node(name, inputs=(), outputs=("GEOMETRY",)):
            sockets = [SimpleNamespace(identifier=key, name=key, enabled=True, is_linked=linked, default_value=value)
                       for key, value, linked in inputs]
            made = type("Node", (), {})()  # Hashable, like a bpy node.
            made.bl_idname, made.inputs, made.outputs = name, sockets, [SimpleNamespace(type=kind) for kind in outputs]
            return made

        def tree(nodes, links=()):
            made = [SimpleNamespace(from_node=a, to_node=b, from_socket=SimpleNamespace(type="GEOMETRY"),
                                    to_socket=SimpleNamespace(type="GEOMETRY")) for a, b in links]
            return SimpleNamespace(nodes=nodes, links=made)

        grid = node("GeometryNodeMeshGrid", [("Vertices X", 10, False), ("Vertices Y", 20, False)])
        subdivide = node("GeometryNodeSubdivideMesh", [("Level", 2, False)])
        output = node("NodeGroupOutput", outputs=())
        self.assertEqual(node_generic.tree_bound(tree([grid, subdivide, output], [(grid, subdivide), (subdivide, output)]), 8), 3200)
        linked = node("GeometryNodeMeshGrid", [("Vertices X", 10, True), ("Vertices Y", 2, False)])
        self.assertIn("AMPLIFIER_INPUT_LINKED", code(node_generic.tree_bound, tree([linked]), 0))
        self.assertIn("GEOMETRY_NODE_UNBOUNDED", code(node_generic.tree_bound, tree([node("GeometryNodeMystery")]), 0))
        self.assertEqual(node_generic.tree_bound(tree([node("FunctionNodeRandomValue", outputs=("VALUE",))]), 5), 0)


class CatalogTests(unittest.TestCase):
    def test_generic_modifier_and_constraint_operations(self):
        self.assertIn("constraint_configure", DEFERRED_ACTIONS)
        self.assertTrue({"modifier_values", "constraint_values"} <= set(DEFERRED_CHECKS))
        lattice = validate_operation({"kind": "modifier_add", "target": OBJ, "parameters": {
            "type": "LATTICE", "settings": {"object": {"object": OTHER}, "strength": 1}}})
        self.assertEqual(lattice["parameters"]["settings"]["strength"], 1)
        self.assertIsInstance(lattice["parameters"]["settings"]["strength"], int)  # RNA, not the schema, types it.
        self.assertEqual(affected_references(lattice), (OBJ, OTHER))
        self.assertEqual(resource_dependencies(lattice)["mutates"], ["object", "modifiers"])
        for parameters in ({"type": "LATTICE", "settings": {"object": {"object": OBJ}}},
                           {"type": "LATTICE", "settings": {"filepath": "x"}}, {"type": "lattice"}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation({"kind": "modifier_add", "target": OBJ, "parameters": parameters})
        configured = validate_operation({"kind": "constraint_configure", "target": OBJ, "parameters": {
            "constraint": "Track", "influence": .5, "settings": {"target": {"object": OTHER}}}})
        self.assertEqual(affected_references(configured), (OBJ, OTHER))
        with self.assertRaises(ValueError):
            validate_operation({"kind": "constraint_configure", "target": OBJ, "parameters": {"constraint": "Track"}})

    def test_generic_checks_are_derived_and_valid(self):
        derived = derive_checks([validate_operation(operation) for operation in (
            {"kind": "modifier_add", "target": OBJ, "parameters": {"type": "WAVE", "name": "Ripple", "settings": {"height": 2}}},
            {"kind": "constraint_configure", "target": OBJ, "parameters": {"constraint": "Track", "influence": .5}})])
        self.assertEqual([check["kind"] for check in derived], ["modifier_values", "constraint_values"])
        self.assertEqual(derived[0]["values"], {"height": 2})
        self.assertEqual(derived[1]["values"], {"influence": .5})
        for check in derived:
            self.assertEqual(validate_check(check), check)

    def test_generic_nodes(self):
        material = validate_operation({"kind": "shader_nodes", "target": OBJ, "parameters": {"name": "Voronoi", "nodes": [
            {"id": "tex", "type": "ShaderNodeTexVoronoi", "inputs": {"Scale": 4}, "settings": {"feature": "F2"}},
            {"id": "bsdf", "type": "BSDF_PRINCIPLED"}, {"id": "out", "type": "OUTPUT_MATERIAL"}], "links": [
            {"from_node": "tex", "from_socket": "Color", "to_node": "bsdf", "to_socket": "Base Color"},
            {"from_node": "bsdf", "from_socket": "BSDF", "to_node": "out", "to_socket": "Surface"}]}})
        self.assertIsInstance(material["parameters"]["nodes"][0]["inputs"]["Scale"], int)
        for node in ({"id": "s", "type": "ShaderNodeScript"}, {"id": "s", "type": "GeometryNodeMeshGrid"},
                     {"id": "s", "type": "ShaderNodeTexNoise", "settings": {"filepath": "x"}}):
            with self.subTest(node=node), self.assertRaises(ValueError):
                validate_operation({"kind": "shader_nodes", "target": OBJ, "parameters": {"nodes": [
                    node, {"id": "out", "type": "OUTPUT_MATERIAL"}], "links": [
                    {"from_node": "s", "from_socket": "Color", "to_node": "out", "to_socket": "Surface"}]}})


if __name__ == "__main__":
    unittest.main()
