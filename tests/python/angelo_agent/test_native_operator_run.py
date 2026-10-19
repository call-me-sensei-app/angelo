# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""operator_run: schema, allow-list, pure refusals, families, derived checks and object-level state.

Dependency-free. The kernel (installed-RNA admission, modes, selection, the
operator itself, ID tracking), commit, Undo and stale revisions are covered by
test_blender_operator_run.py in the compiled app.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import action_operator_spec as spec  # noqa: E402
from angelo_agent.action_catalog import (affected_references, resource_dependencies, validate_operation,  # noqa: E402
                                         written_references)
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.operator_state import commit_object_state, revision_state  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"


def op(target=OBJ, **parameters):
    return {"kind": "operator_run", "target": target, "parameters": parameters}


class OperatorRunSchemaTests(unittest.TestCase):
    def test_canonical_defaults_and_exact_values(self):
        self.assertIn("operator_run", DEFERRED_ACTIONS)
        canonical = validate_operation(op(operator="mesh.subdivide", properties={"number_cuts": 2, "smoothness": 1}))
        self.assertEqual(canonical["parameters"], {
            "operator": "mesh.subdivide", "properties": {"number_cuts": 2, "smoothness": 1}, "select": None,
            "select_mode": None, "vertex_indices": [], "edge_indices": [], "face_indices": [], "index_scope": "INITIAL",
            "topology_step": 0})
        # Integers stay integers for INT properties; the schema alone would make them floats.
        self.assertIs(type(canonical["parameters"]["properties"]["number_cuts"]), int)
        self.assertEqual(validate_operation(canonical), canonical)
        indexed = validate_operation(op(operator="mesh.poke", select="INDICES", face_indices=[0, 3],
                                        index_scope="CURRENT", topology_step=2))
        self.assertEqual(indexed["parameters"]["face_indices"], [0, 3])

    def test_pure_refusals_name_their_reason(self):
        cases = {
            "NOT_ALLOWED": op(operator="object.delete"), "NEW_OBJECTS": op(operator="mesh.separate"),
            "FILE_WIDE_RENAME": op(operator="armature.flip_names"), "SUPERLINEAR": op(operator="mesh.intersect"),
            "CLIPBOARD": op(operator="uv.paste"), "CURSOR_PICK": op(operator="mesh.select_linked_pick"),
            "OTHER_OBJECTS": op(operator="object.vertex_group_copy_to_selected"),
            "ASSET_FILE": op(operator="object.shade_auto_smooth"),
            "BONE_CONSTRAINTS": op(operator="pose.ik_add"), "REST_AND_CHILDREN": op(operator="pose.armature_apply"),
            "COVERED": op(operator="object.particle_system_add"),
            "AMPLIFIER_BOUND": op(operator="mesh.subdivide", properties={"number_cuts": 33}),
            "FILE_OR_SCRIPT": op(operator="mesh.subdivide", properties={"filepath": "/tmp/x"}),
            "SELECTION": op(operator="mesh.poke", select="INDICES"),
        }
        for code, operation in cases.items():
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "operator_run " + code):
                validate_operation(operation)
        self.assertRaisesRegex(ValueError, "NOT_ALLOWED", validate_operation, op(operator="pose.select_all"))

    def test_schema_and_selection_refusals(self):
        for operation in (op(operator="mesh.subdivide", properties={"Number": 1}),
                          op(operator="mesh.subdivide", properties={"number_cuts": None}),
                          op(operator="mesh.subdivide", properties={"number_cuts": {"object": OBJ}}),
                          op(operator="bpy.ops.mesh.subdivide"), op(operator="sculpt.mask_filter"),
                          op(operator="mesh.poke", face_indices=[0]),
                          op(operator="mesh.poke", select="INDICES", face_indices=[0], edge_indices=[1]),
                          op(operator="mesh.poke", select="INDICES", face_indices=[0], select_mode="VERT"),
                          op(operator="curve.subdivide", select="INDICES", vertex_indices=[0]),
                          op(operator="mesh.poke", select="INDICES", face_indices=[0], topology_step=1),
                          op(operator="mesh.subdivide", properties={str(i): 1 for i in range(33)})):
            with self.subTest(operation=operation["parameters"]), self.assertRaises(ValueError):
                validate_operation(operation)

    def test_families_and_references(self):
        smooth = op(operator="object.shade_smooth")
        self.assertEqual(resource_dependencies(smooth)["mutates"], ["object", "data"])
        # A force-field toggle never swaps (and so never de-instances) the data.
        self.assertEqual(resource_dependencies(op(operator="object.forcefield_toggle"))["mutates"], ["object"])
        # Pose operators commit through the pose family, never the armature data.
        self.assertEqual(resource_dependencies(op(operator="pose.rot_clear"))["mutates"], ["object", "pose"])
        self.assertEqual(affected_references(smooth), (OBJ,))
        self.assertEqual(written_references(smooth), (OBJ,))

    def test_derived_checks_drop_what_an_operator_could_falsify(self):
        operations = [validate_operation(op(operator="mesh.subdivide"))]
        self.assertEqual(derive_checks(operations), [])
        before = [validate_operation({"kind": "property_set", "target": OBJ, "parameters": {"values": {"pass_index": 3}}}),
                  validate_operation({"kind": "shape_key_add", "target": OBJ, "parameters": {"name": "Smile"}}),
                  validate_operation({"kind": "object_rename", "target": OBJ, "parameters": {"name": "Kept"}})]
        kinds = [check["kind"] for check in derive_checks(before + operations)]
        self.assertEqual(kinds, ["object_name"])


class AllowListTests(unittest.TestCase):
    def test_allow_list_is_closed_and_consistent(self):
        self.assertFalse(set(spec.ALLOWED) & set(spec.DENIED))
        for name, (factor, amplifiers) in spec.ALLOWED.items():
            with self.subTest(name=name):
                self.assertIn(spec.module_of(name), set(spec.MODULE_TARGETS) | set(spec.OBJECT_MODULES)
                              | set(spec.POSE_MODULES) | {"object"})
                self.assertRegex(name, spec.OPERATOR_PATTERN)
                self.assertTrue(factor == "quadratic" or factor in (1, 2, 3, 4, 16))  # 3: curve-type conversions.
                self.assertTrue(set(amplifiers) <= set(spec.CAPS))
                self.assertTrue(spec.target_types(name))
        self.assertEqual(sorted(name for name in spec.ALLOWED if name.startswith("pose.")), [
            "pose.loc_clear", "pose.quaternions_flip", "pose.rot_clear", "pose.rotation_mode_set", "pose.scale_clear",
            "pose.transforms_clear", "pose.user_transforms_clear", "pose.visual_transform_apply"])
        self.assertEqual(spec.OBJECT_TARGETS["object.forcefield_toggle"][-1], "ARMATURE")
        self.assertEqual(spec.OBJECT_TARGETS["object.vertex_group_add"], ("MESH",))
        self.assertEqual(spec.target_types("grease_pencil.layer_add"), ("GREASEPENCIL",))
        self.assertIn("grease_pencil.layer_duplicate_object", spec.DENIED)

    def test_growth_estimates(self):
        cube = {"elements": 26, "edges": 12}
        self.assertEqual(spec.estimate(spec.ALLOWED["mesh.subdivide"], cube, {"number_cuts": 2}), 90 * 4 * 9)
        self.assertEqual(spec.estimate(spec.ALLOWED["mesh.primitive_ico_sphere_add"], cube, {"subdivisions": 7}),
                         90 * 4 * 4 ** 7)
        self.assertEqual(spec.estimate(spec.ALLOWED["mesh.fill_grid"], cube, {}), 90 * 4 + 7 ** 2)
        self.assertEqual(spec.estimate(spec.ALLOWED["mesh.select_all"], cube, {}), 90)

    def test_spec_is_pure(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/action_operator_spec.py").read_text()
        modules = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}
        modules |= {alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import) for alias in node.names}
        self.assertFalse(modules & {"bpy", "bmesh", "mathutils"})
        for name in ("action_operator_spec.py", "action_operators.py", "operator_state.py"):
            self.assertLessEqual(len((ROOT / "scripts/addons_core/angelo_agent" / name).read_text().splitlines()), 500)


class GreasePencilTests(unittest.TestCase):
    def test_create_object_and_layer_operators(self):
        created = validate_operation({"kind": "create_object", "target": None,
                                      "parameters": {"type": "GREASEPENCIL", "alias": "sketch"}})
        self.assertEqual((created["parameters"]["name"], created["parameters"]["layer"]), ("Angelo Grease Pencil", "Layer"))
        self.assertEqual(derive_checks([created])[0], {"kind": "object_type", "target": "$sketch", "expected": "GREASEPENCIL"})
        layer = validate_operation(op("$sketch", operator="grease_pencil.layer_add", properties={"new_layer_name": "Ink"}))
        self.assertEqual(resource_dependencies(layer)["mutates"], ["object", "data"])
        for operation in (op(operator="grease_pencil.layer_duplicate_object"), op(operator="grease_pencil.separate"),
                          op(operator="grease_pencil.layer_add", select="ALL")):
            with self.subTest(operation=operation["parameters"]["operator"]):
                if operation["parameters"].get("select"):
                    validate_operation(operation)  # Refused by the kernel: Object-mode operators keep the selection.
                else:
                    self.assertRaises(ValueError, validate_operation, operation)


class ObjectStateTests(unittest.TestCase):
    def test_revision_state_reads_stand_ins(self):
        mesh = SimpleNamespace(type="MESH", data=SimpleNamespace(), active_material_index=1,
                               vertex_groups=SimpleNamespace(active_index=2))
        self.assertEqual(revision_state(mesh), [1, 2])
        self.assertEqual(revision_state(SimpleNamespace(type="EMPTY", data=None)), [None, None])
        bone = SimpleNamespace(name="Root", collections=[SimpleNamespace(name="Deform")], select_head=True, select_tail=False)
        bones = [bone]
        armature = SimpleNamespace(type="ARMATURE", mode="OBJECT", data=SimpleNamespace(bones=bones, collections_all=[]))
        self.assertEqual(revision_state(armature)[2:], [[["Root", ["Deform"], True, False]], [], None])

    def test_commit_object_state_journals_and_copies(self):
        names = ("active_material_index", "active_shape_key_index", "show_only_shape_key", "use_shape_key_edit_mode")
        live = SimpleNamespace(**dict.fromkeys(names, 0), field=None)
        staged = SimpleNamespace(active_material_index=2, active_shape_key_index=1, show_only_shape_key=False,
                                 use_shape_key_edit_mode=0, field=None)
        journal = []
        commit_object_state(live, staged, journal, RuntimeError)
        self.assertEqual((live.active_material_index, live.active_shape_key_index), (2, 1))
        self.assertEqual(journal, [(live, "active_material_index", 0), (live, "active_shape_key_index", 0)])
        staged.field = SimpleNamespace(type="FORCE")
        with self.assertRaisesRegex(RuntimeError, "no force-field settings"):
            commit_object_state(live, staged, [], RuntimeError)


if __name__ == "__main__":
    unittest.main()
