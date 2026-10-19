# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Constraint types, create_object and the UV kinds: schemas, rules and derived checks.

Dependency-free. Real staging and commits are covered by
test_blender_constraints.py, test_blender_object_management.py and
test_blender_uv_tools.py.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_checks import required_checks
from angelo_agent.action_catalog import affected_references, validate_operation  # noqa: E402
from angelo_agent.action_schemas import CONSTRAINT_SETTINGS, CREATE_KINDS, TARGETLESS_CONSTRAINTS  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.scene_read_fields import KNOWN_CONSTRAINTS  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
OTHER = "scn:" + "a" * 32 + ":8"


def constraint(kind, target=None, **settings):
    return {"kind": "constraint_add", "target": OBJ, "parameters": {"type": kind, "target": target, "settings": settings}}


class ConstraintTests(unittest.TestCase):
    def test_every_type_has_an_exact_revision(self):
        self.assertTrue(set(CONSTRAINT_SETTINGS) <= KNOWN_CONSTRAINTS)

    def test_targets_and_limits(self):
        for kind in ("DAMPED_TRACK", "COPY_TRANSFORMS", "CHILD_OF"):
            self.assertEqual(affected_references(constraint(kind, OTHER)), (OBJ, OTHER))
            with self.assertRaises(ValueError):
                validate_operation(constraint(kind))
        for kind in TARGETLESS_CONSTRAINTS:
            validate_operation(constraint(kind))
            with self.assertRaises(ValueError):
                validate_operation(constraint(kind, OTHER))
        with self.assertRaises(ValueError):
            validate_operation(constraint("LIMIT_ROTATION", use_limit=[True, False, False], minimum=[1, 0, 0], maximum=[-1, 0, 0]))
        # Any other type is generic: its settings go by RNA path, its objects by {"object": handle}.
        generic = validate_operation(constraint("FOLLOW_PATH", OTHER))
        self.assertEqual(affected_references(generic), (OBJ, OTHER))
        self.assertEqual(affected_references(validate_operation({"kind": "constraint_add", "target": OBJ, "parameters": {
            "type": "SHRINKWRAP", "settings": {"target": {"object": OTHER}, "distance": 1}}})), (OBJ, OTHER))
        for parameters in ({"type": "PYTHON"}, {"type": "FOLLOW_PATH", "settings": {"target": {"object": OBJ}}},
                           {"type": "FOLLOW_PATH", "settings": {"script": 1}}):
            with self.assertRaises(ValueError):
                validate_operation({"kind": "constraint_add", "target": OBJ, "parameters": parameters})
        self.assertTrue(validate_operation(constraint("CHILD_OF", OTHER))["parameters"]["settings"]["set_inverse"])


class CreateObjectTests(unittest.TestCase):
    def operation(self, **parameters):
        return {"kind": "create_object", "target": None, "parameters": parameters}

    def test_types_defaults_and_checks(self):
        self.assertIn("create_object", CREATE_KINDS)
        text = validate_operation(self.operation(type="TEXT", alias="title"))
        self.assertEqual((text["parameters"]["name"], text["parameters"]["body"]), ("Angelo Text", "Text"))
        checks = derive_checks([text])
        self.assertEqual(checks[0], {"kind": "object_type", "target": "$title", "expected": "FONT"})
        self.assertFalse(any(check["kind"] == "object_name" for check in checks))
        named = derive_checks([validate_operation(self.operation(type="CURVE", alias="ring", name="Ring", shape="CIRCLE"))])
        self.assertIn({"kind": "object_name", "target": "$ring", "expected": "Ring"}, named)
        for check in checks + named:
            self.assertEqual(validate_check(check), check)
        # Every create_object type derives its checks (a lattice plan used to fail normalization with a KeyError).
        from angelo_agent.action_schemas import PARAMETER_SCHEMAS
        for variant in PARAMETER_SCHEMAS["create_object"]["oneOf"]:
            kind = variant["properties"]["type"]["const"]
            with self.subTest(type=kind):
                derived = derive_checks([validate_operation(self.operation(type=kind, alias="made"))])
                self.assertEqual(derived[0]["kind"], "object_type")
                self.assertFalse(any(check["kind"] == "object_name" for check in derived))
        from angelo_agent.tool_specs import validate_call
        program = validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [], "operations": [
            self.operation(type="LATTICE", alias="cage")]})
        self.assertIn({"kind": "object_type", "target": "$cage", "expected": "LATTICE"}, required_checks(program))
        for parameters in ({"type": "METABALL"}, {"type": "TEXT", "body": ""}, {"type": "CURVE", "shape": "NURBS"},
                           {"type": "EMPTY", "display": "IMAGE"}, {"type": "EMPTY", "body": "x"}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation(self.operation(**parameters))


class MeshToolTests(unittest.TestCase):
    def test_subdivide_and_delete(self):
        from angelo_agent.action_catalog import estimated_work
        from angelo_agent.action_schemas import TOPOLOGY_KINDS
        self.assertTrue({"mesh_subdivide", "mesh_delete"} <= TOPOLOGY_KINDS)
        subdivide = {"kind": "mesh_subdivide", "target": OBJ, "parameters": {"face_indices": [0], "cuts": 2}}
        self.assertEqual(estimated_work(subdivide, {"vertices": 8, "edges": 12, "faces": 6})["faces"], 6 + 54)
        for parameters in ({}, {"face_indices": [0], "cuts": 7}, {"face_indices": [0], "smoothness": 2}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation(dict(subdivide, parameters=parameters))
        delete = validate_operation({"kind": "mesh_delete", "target": OBJ, "parameters": {"face_selector": {"material_index": 1}}})
        self.assertEqual(delete["parameters"]["mode"], "FACES")
        self.assertEqual(derive_checks([delete]), [])
        with self.assertRaises(ValueError):
            validate_operation({"kind": "mesh_delete", "target": OBJ, "parameters": {"face_indices": [0], "mode": "VERTS"}})


class BendTests(unittest.TestCase):
    def test_bend_schema_rules_work_and_lookup(self):
        from angelo_agent.action_catalog import catalog, estimated_work
        from angelo_agent.action_schemas import BEND_NOTE, TOPOLOGY_KINDS
        from angelo_agent.plan_schema import (CLAUDE_CORE_ACTIONS, CLAUDE_DEFERRED_ACTIONS, CODEX_REFERENCE_ACTIONS,
                                              PLAN_CORE_ACTIONS, core_operation, deferred_references)
        from angelo_agent.tool_specs import native_plan_schema
        bend = validate_operation({"kind": "mesh_bend", "target": OBJ, "parameters": {"rise": .3}})
        self.assertEqual(bend["parameters"], {"rise": .3, "axis": None, "up": "Z", "segments": 16})
        self.assertIn("mesh_bend", TOPOLOGY_KINDS)
        # A cube's 12 edges are each split at most once by each of the 15 parallel cuts.
        self.assertEqual(estimated_work(bend, {"vertices": 8, "edges": 12, "faces": 6})["vertices"], 8 + 180)
        self.assertEqual(estimated_work(dict(bend, parameters={"rise": .3, "segments": 1}),
                                        {"vertices": 8, "edges": 12, "faces": 6})["faces"], 6)
        self.assertEqual(derive_checks([bend]), [])
        for parameters in ({}, {"rise": 0}, {"rise": .3, "axis": "Z"}, {"rise": .3, "axis": "Y", "up": "Y"},
                           {"rise": .3, "segments": 0}, {"rise": .3, "segments": 65}, {"rise": 2000},
                           {"rise": .3, "up": "W"}, {"rise": .3, "face_indices": [0]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation({"kind": "mesh_bend", "target": OBJ, "parameters": parameters})
        self.assertEqual(catalog("mesh_bend")["note"], BEND_NOTE)
        # Codex states it (its plan cannot end with a script). Claude's plan states it too, with the note as its
        # description: Opus never looked the named kind up and wrote the arch as a script in its plan instead.
        self.assertIn("mesh_bend", CODEX_REFERENCE_ACTIONS)
        self.assertIn("mesh_bend", CLAUDE_CORE_ACTIONS)
        self.assertNotIn("mesh_bend", CLAUDE_DEFERRED_ACTIONS + PLAN_CORE_ACTIONS)
        self.assertEqual(core_operation("mesh_bend")["properties"]["parameters"]["description"], BEND_NOTE)
        self.assertIn(json.dumps(BEND_NOTE), json.dumps(native_plan_schema()))
        plan = [{"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [bend]}}]
        self.assertEqual(deferred_references(plan, CLAUDE_CORE_ACTIONS), {}, "no lookup before a bend")


class UvTests(unittest.TestCase):
    def test_smart_project_and_pack(self):
        smart = validate_operation({"kind": "uv_smart_project", "target": OBJ, "parameters": {"uv_map": "Atlas"}})
        self.assertEqual(derive_checks([smart]), [{"kind": "uv_layer", "target": OBJ, "name": "Atlas",
                                                   "require_nonempty": True, "require_finite": True}])
        pack = validate_operation({"kind": "uv_pack", "target": OBJ, "parameters": {"margin": .01}})
        self.assertEqual(pack["parameters"]["shape_method"], "CONCAVE")
        mesh_edit = validate_operation({"kind": "mesh_inset", "target": OBJ, "parameters": {"face_indices": [0]}})
        self.assertEqual(derive_checks([smart, mesh_edit]), [])
        for parameters in ({"angle_limit": 0}, {"island_margin": 2}, {"face_indices": [0, 0]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation({"kind": "uv_smart_project", "target": OBJ, "parameters": parameters})


if __name__ == "__main__":
    unittest.main()
