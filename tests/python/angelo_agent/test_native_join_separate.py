# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""object_join and mesh_separate: schemas, write sets, inference and derived checks.

Dependency-free. Staged geometry, materials, collections, commit and Undo are
covered by test_blender_join_separate.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_catalog import (affected_references, resource_dependencies, validate_operation,  # noqa: E402
                                         written_references)
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
SOURCE = "scn:" + "a" * 32 + ":9"
OTHER = "scn:" + "a" * 32 + ":11"
REV = "b" * 64


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


class SchemaTests(unittest.TestCase):
    def test_deferred_and_canonical(self):
        self.assertIn("object_join", DEFERRED_ACTIONS)
        self.assertIn("mesh_separate", DEFERRED_ACTIONS)
        joined = validate_operation(op("object_join", sources=[SOURCE, OTHER]))
        self.assertEqual(affected_references(joined), (OBJ, SOURCE, OTHER))
        self.assertEqual(written_references(joined), (OBJ, SOURCE, OTHER))
        self.assertEqual(resource_dependencies(joined)["mutates"], ["object", "mesh"])
        separated = validate_operation(op("mesh_separate"))
        self.assertEqual(separated["parameters"]["mode"], "LOOSE")
        self.assertEqual(resource_dependencies(separated)["creates"], ["object", "mesh"])
        selection = validate_operation(op("mesh_separate", mode="SELECTION", face_indices=[0, 1]))
        self.assertEqual(selection["parameters"]["index_scope"], "INITIAL")

    def test_refusals(self):
        for operation in (op("object_join", sources=[]), op("object_join", sources=[OBJ]),
                          op("object_join", sources=[SOURCE, SOURCE]),
                          op("mesh_separate", mode="SELECTION"), op("mesh_separate", face_indices=[0]),
                          op("mesh_separate", mode="MATERIAL", face_indices=[0]), op("mesh_separate", mode="BY_UV")):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)

    def test_sources_need_the_write_set(self):
        program = {"mode": "APPLY", "write_set": [{"handle": OBJ, "revision": REV}],
                   "read_set": [{"handle": SOURCE, "revision": REV}],
                   "operations": [op("object_join", sources=[SOURCE])]}
        with self.assertRaises(ValueError):
            validate_program(program)  # A joined source is deleted, so it is written.
        program.update(read_set=[], write_set=program["write_set"] + [{"handle": SOURCE, "revision": REV}])
        self.assertEqual(len(validate_program(program)["operations"]), 1)


class DerivedCheckTests(unittest.TestCase):
    def derived(self, *operations):
        return derive_checks([validate_operation(operation) for operation in operations])

    def test_join_states_sources_are_gone(self):
        self.assertEqual(self.derived(op("object_join", sources=[SOURCE, OTHER])),
                         [{"kind": "object_exists", "target": SOURCE, "expected": False},
                          {"kind": "object_exists", "target": OTHER, "expected": False}])

    def test_earlier_checks_on_a_removed_object_are_dropped(self):
        rename = op("object_rename", SOURCE, name="Doomed")
        self.assertEqual(self.derived(rename, op("object_join", sources=[SOURCE])),
                         [{"kind": "object_exists", "target": SOURCE, "expected": False}])
        self.assertEqual(self.derived(rename, op("object_delete", SOURCE)),
                         [{"kind": "object_exists", "target": SOURCE, "expected": False}])

    def test_join_keeps_target_transform_but_separate_derives_nothing(self):
        transform = op("transform", location=[1, 2, 3])
        kept = self.derived(transform, op("object_join", sources=[SOURCE]))
        self.assertIn("transform", [check["kind"] for check in kept])
        self.assertEqual(self.derived(op("mesh_separate")), [])


class InferenceTests(unittest.TestCase):
    def test_inferred_sources_go_to_the_write_set(self):
        from angelo_agent import identifiers

        class Table:
            def bound_revision(self, handle):
                return REV

        inferred = identifiers._infer_program({"mode": "APPLY", "operations": [op("object_join", sources=[SOURCE])]},
                                              Table())
        self.assertEqual({entry["handle"] for entry in inferred["write_set"]}, {OBJ, SOURCE})
        self.assertNotIn("read_set", inferred)


if __name__ == "__main__":
    unittest.main()
