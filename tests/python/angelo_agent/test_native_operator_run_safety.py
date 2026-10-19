# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""operator_run safety rules without Blender: bounds, admission refusals, pose and reindex guards.

The kernel module is imported with a stand-in ``bpy`` only inside each test
(``patch.dict(sys.modules)`` removes it again). The same rules are exercised
on real operators by test_blender_operator_run_safety.py, and the scene
override across mode switches by test_blender_operator_run_windows.py (GUI).
"""

import ast
import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import action_operator_spec as spec  # noqa: E402
from angelo_agent.action_catalog import resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.action_schema_types import MAX_EVALUATED_BONES  # noqa: E402
from angelo_agent.operator_state import reindexed_dependents, untransferred_pose_changes  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"


class BoundTests(unittest.TestCase):
    def test_armature_operators_are_bounded_in_bones_before_they_run(self):
        subdivide, duplicate, extrude = (spec.ALLOWED["armature." + name] for name in ("subdivide", "duplicate", "extrude"))
        self.assertEqual(spec.bone_estimate(subdivide, 32, {"number_cuts": 31}), 1024)  # Exact: one bone per cut.
        self.assertGreater(spec.bone_estimate(subdivide, 32, {"number_cuts": 32}), MAX_EVALUATED_BONES)
        self.assertGreater(spec.bone_estimate(subdivide, 1000, {"number_cuts": 32}), MAX_EVALUATED_BONES)
        self.assertEqual(spec.bone_estimate(duplicate, 512, {}), 1024)
        self.assertEqual(spec.bone_estimate(extrude, 256, {}), 1024)  # A forked extrude adds two bones per bone.
        self.assertEqual(spec.bone_estimate(spec.ALLOWED["armature.bone_primitive_add"], 0, {}), 2)
        self.assertEqual(spec.bone_estimate(spec.ALLOWED["pose.loc_clear"], 1024, {}), 1024)

    def test_superlinear_operators_have_work_bounds(self):
        self.assertTrue(set(spec.WORK_BOUNDS) <= set(spec.ALLOWED))
        self.assertIn("mesh.edge_face_add", spec.WORK_BOUNDS)
        # A whole 20,000-vertex wire tree (39,999 elements, all selected) exceeds edge_face_add's bound; making a
        # face from four selected vertices of the same mesh does not, nor does an operator without a bound.
        self.assertFalse(spec.within_work_bound("mesh.edge_face_add", 39999, 39999))
        self.assertTrue(spec.within_work_bound("mesh.edge_face_add", 39999, 4))
        self.assertTrue(spec.within_work_bound("mesh.subdivide", 400000, 400000))

    def test_armature_operators_commit_the_pose_family(self):
        operation = validate_operation({"kind": "operator_run", "target": OBJ, "parameters": {"operator": "armature.duplicate"}})
        self.assertEqual(resource_dependencies(operation)["mutates"], ["object", "data", "pose"])


def _kernel():
    """action_operators imported against the stand-in ``bpy`` the caller patched into sys.modules."""
    return importlib.import_module("angelo_agent.action_operators")


class AdmissionRuleTests(unittest.TestCase):
    """The installed-RNA rules (PYTHON_OPERATOR ... FILE_PROPERTY) and the ID accounting after a run."""

    def setUp(self):
        package = sys.modules["angelo_agent"]
        before = set(vars(package))  # Submodules imported against the stand-in leave the package again.
        self.addCleanup(lambda: [delattr(package, name) for name in set(vars(package)) - before])

    def admission_error(self, options=("REGISTER", "UNDO"), properties=(), python=False):
        prop = lambda identifier, kind, subtype="NONE": SimpleNamespace(identifier=identifier, type=kind, subtype=subtype)
        rna = SimpleNamespace(properties=[prop("rna_type", "POINTER"), *(prop(*item) for item in properties)])
        call = SimpleNamespace(get_rna_type=lambda: rna, idname=lambda: "MESH_OT_angelo_qa", bl_options=set(options))
        bpy = SimpleNamespace(ops=SimpleNamespace(mesh=SimpleNamespace(angelo_qa=call)), context=SimpleNamespace(),
                              data=SimpleNamespace(), types=SimpleNamespace(Operator=SimpleNamespace(
                                  bl_rna_get_subclass_py=lambda name: object() if python else None)))
        with patch.dict(sys.modules, {"bpy": bpy, "mathutils": SimpleNamespace(Vector=tuple), "bmesh": SimpleNamespace()}), \
                patch.dict(spec.ALLOWED, {"mesh.angelo_qa": (1, {})}):
            kernel = _kernel()
            try:
                kernel.admission("mesh.angelo_qa")
            except spec.OperatorError as error:
                return error.code
        return None

    def test_runtime_rules_refuse_even_listed_operators(self):
        self.assertIsNone(self.admission_error())
        self.assertEqual(self.admission_error(python=True), "PYTHON_OPERATOR")
        self.assertEqual(self.admission_error(options=("REGISTER", "UNDO", "BLOCKING")), "INTERACTIVE")
        self.assertEqual(self.admission_error(options=("REGISTER", "UNDO", "DEPENDS_ON_CURSOR")), "INTERACTIVE")
        self.assertEqual(self.admission_error(options=("REGISTER",)), "NO_UNDO")
        self.assertEqual(self.admission_error(properties=[("selection", "COLLECTION")]), "DATA_PROPERTY")
        self.assertEqual(self.admission_error(properties=[("object", "POINTER")]), "DATA_PROPERTY")
        self.assertEqual(self.admission_error(properties=[("filepath", "STRING", "FILE_PATH")]), "FILE_PROPERTY")
        self.assertEqual(self.admission_error(properties=[("directory", "STRING")]), "FILE_PROPERTY")

    def test_id_accounting_refuses_freed_and_new_datablocks(self):
        class Block:
            def __init__(self, pointer):
                self.as_pointer = lambda: pointer

        block = Block
        data = SimpleNamespace(materials=[block(3)], shape_keys=[block(4)])
        bpy = SimpleNamespace(data=data, ops=SimpleNamespace(), types=SimpleNamespace(), context=SimpleNamespace())
        with patch.dict(sys.modules, {"bpy": bpy, "mathutils": SimpleNamespace(Vector=tuple), "bmesh": SimpleNamespace()}):
            kernel = _kernel()
            tracked = []
            context = SimpleNamespace(owned_ids={block(1)}, created_ids=[], track_id=tracked.append)
            before = {("meshes", 1): 11, ("meshes", 2): 12}
            self.assertEqual(kernel._account_ids(context, before, {("meshes", 1): 11}).code, "FREED_DATA")
            self.assertEqual(kernel._account_ids(context, before, {**before, ("materials", 3): 13}).code, "NEW_DATABLOCKS")
            self.assertIsNone(kernel._account_ids(context, before, {**before, ("shape_keys", 4): 14}))
            self.assertEqual(len(tracked), 2)  # Every new ID is owned by the stage, refused or not.

    def test_mode_switches_never_use_object_mode_set(self):
        # object.mode_set writes a pending autosave of the whole file, the private stage included.
        for name in ("action_operators.py", "action_operator_modes.py", "action_edit_context.py"):
            tree = ast.parse((ADDON / name).read_text())
            calls = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            with self.subTest(module=name):
                self.assertNotIn("mode_set", calls)
                if name == "action_operators.py":
                    continue  # It switches modes only through action_operator_modes.mode_override.
                self.assertIn("switch_mode", {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
                              | {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names})


class StateGuardTests(unittest.TestCase):
    def test_pose_extras_compare_with_what_the_live_pose_keeps(self):
        before = {"Arm": "constrained", "Hand": "plain"}
        empty = untransferred_pose_changes({}, {"New": None})  # A new bone is compared with no extras at all.
        self.assertEqual(empty, ["New"])
        self.assertEqual(untransferred_pose_changes(before, dict(before)), [])
        self.assertEqual(untransferred_pose_changes(before, {"Arm": "constrained"}), [])  # Removed bones: not compared.
        self.assertEqual(untransferred_pose_changes(before, {**before, "Arm.001": "constrained"}), ["Arm.001"])
        self.assertEqual(untransferred_pose_changes(before, {**before, "Arm": "target cleared"}), ["Arm"])

    def test_reindexed_dependents(self):
        curve = lambda *counts: SimpleNamespace(splines=[SimpleNamespace(bezier_points=[0] * count, points=[]) for count in counts])
        child = SimpleNamespace(name="Tag", parent_type="VERTEX", parent_bone="")
        live = SimpleNamespace(type="CURVE", data=curve(2), children=[child], modifiers=[])
        self.assertIsNone(reindexed_dependents(live, SimpleNamespace(data=curve(2))))
        self.assertIn("vertex-parented", reindexed_dependents(live, SimpleNamespace(data=curve(3))))
        live.children = []
        self.assertIsNone(reindexed_dependents(live, SimpleNamespace(data=curve(3))))
        live.modifiers = [SimpleNamespace(type="HOOK", vertex_indices=[1])]
        self.assertIn("Hook", reindexed_dependents(live, SimpleNamespace(data=curve(3))))
        live.modifiers = [SimpleNamespace(type="HOOK", vertex_indices=[])]  # Bound by a vertex group only.
        self.assertIsNone(reindexed_dependents(live, SimpleNamespace(data=curve(3))))
        bones = lambda *names: SimpleNamespace(bones={name: SimpleNamespace(name=name) for name in names}.values())
        rig_child = SimpleNamespace(name="Prop", parent_type="BONE", parent_bone="Hand")
        rig = SimpleNamespace(type="ARMATURE", data=SimpleNamespace(bones={"Arm": 0, "Hand": 0}), children=[rig_child])
        self.assertIsNone(reindexed_dependents(rig, SimpleNamespace(data=bones("Arm", "Hand"))))
        self.assertIn("Prop", reindexed_dependents(rig, SimpleNamespace(data=bones("Arm"))))


if __name__ == "__main__":
    unittest.main()
