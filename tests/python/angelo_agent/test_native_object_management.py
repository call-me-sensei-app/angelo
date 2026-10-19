# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object management kinds: schemas, dependencies, derived checks and their merging.

Dependency-free. Staging, commit, rollback and Undo of rename, duplicate,
delete, apply transforms, origin and collection edits are covered by
test_blender_object_management.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_catalog import affected_references, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.action_schemas import CREATE_KINDS  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
SOURCE = "scn:" + "a" * 32 + ":9"
REV = "b" * 64
KINDS = ("object_rename", "object_delete", "object_duplicate", "transform_apply", "origin_set", "collection_link")


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


class SchemaTests(unittest.TestCase):
    def test_kinds_are_deferred_and_duplicate_is_a_create_kind(self):
        for kind in KINDS:
            self.assertIn(kind, DEFERRED_ACTIONS)
        self.assertIn("object_duplicate", CREATE_KINDS)
        self.assertIn("object_exists", DEFERRED_CHECKS)
        self.assertIn("collection_membership", DEFERRED_CHECKS)

    def test_accepts_and_refuses(self):
        self.assertEqual(validate_operation(op("object_rename", name="Hero"))["parameters"], {"name": "Hero"})
        self.assertEqual(validate_operation(op("object_delete"))["parameters"], {})
        duplicate = validate_operation(op("object_duplicate", None, source=SOURCE, alias="twin"))
        self.assertEqual(duplicate["parameters"], {"source": SOURCE, "alias": "twin", "name": None, "linked": False,
                                                    "location": None})
        self.assertEqual(affected_references(duplicate), (SOURCE,))
        self.assertEqual(validate_operation(op("collection_link", collection="Props"))["parameters"]["mode"], "LINK")
        for operation in (op("object_rename", name=""), op("object_rename", name="x" * 65), op("object_delete", force=True),
                          op("object_duplicate", OBJ, source=SOURCE, alias="twin"),
                          op("object_duplicate", None, source="$twin", alias="twin"),
                          op("object_duplicate", None, source=SOURCE, alias="9bad"),
                          op("transform_apply"), op("origin_set", mode="POINT"),
                          op("origin_set", mode="MEDIAN", point=[0, 0, 0]), op("origin_set", mode="CURSOR"),
                          op("collection_link", collection="Props", mode="SWAP"), op("collection_link")):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)

    def test_program_binding(self):
        program = {"mode": "APPLY", "read_set": [], "write_set": [],
                   "operations": [op("object_duplicate", None, source=SOURCE, alias="twin"),
                                  op("object_rename", "$twin", name="Twin")]}
        with self.assertRaises(ValueError):
            validate_program(program)  # The source must be declared.
        program["read_set"] = [{"handle": SOURCE, "revision": REV}]
        self.assertEqual(len(validate_program(program)["operations"]), 2)
        with self.assertRaises(ValueError):
            validate_program(dict(program, operations=[op("object_delete")]))  # Deletes need the write set.
        self.assertEqual(resource_dependencies(op("transform_apply", scale=True))["mutates"], ["object", "mesh", "transforms"])


class DerivedCheckTests(unittest.TestCase):
    def derived(self, *operations):
        return derive_checks([validate_operation(operation) for operation in operations])

    def test_each_kind_states_its_result(self):
        self.assertEqual(self.derived(op("object_rename", name="Hero")),
                         [{"kind": "object_name", "target": OBJ, "expected": "Hero"}])
        self.assertEqual(self.derived(op("object_delete")), [{"kind": "object_exists", "target": OBJ, "expected": False}])
        duplicate = self.derived(op("object_duplicate", None, source=SOURCE, alias="twin", name="Twin", location=[1, 2, 3]))
        self.assertEqual([check["kind"] for check in duplicate], ["object_exists", "object_name", "transform"])
        self.assertEqual({check["target"] for check in duplicate}, {"$twin"})
        applied = self.derived(op("transform_apply", location=True, scale=True))
        self.assertEqual([(check["property"], check["expected"]) for check in applied],
                         [("location", [0., 0., 0.]), ("scale", [1., 1., 1.])])
        self.assertEqual(self.derived(op("origin_set")), [])
        for check in duplicate + applied:
            self.assertEqual(validate_check(check), check)

    def test_membership_merges_by_mode(self):
        merged = self.derived(op("collection_link", collection="A"), op("collection_link", collection="B"),
                              op("collection_link", collection="C", mode="MOVE"), op("collection_link", collection="D"),
                              op("collection_link", collection="D", mode="UNLINK"))
        self.assertEqual(merged, [{"kind": "collection_membership", "target": OBJ, "collections": ["C"],
                                   "absent": ["A", "B", "D"]}])
        self.assertEqual(validate_check(merged[0]), merged[0])
        with self.assertRaises(ValueError):
            validate_check({"kind": "collection_membership", "target": OBJ, "collections": ["A"], "absent": ["A"]})
        with self.assertRaises(ValueError):
            validate_check({"kind": "collection_membership", "target": OBJ})

    def test_later_rename_wins(self):
        self.assertEqual(self.derived(op("object_rename", name="A"), op("object_rename", name="B")),
                         [{"kind": "object_name", "target": OBJ, "expected": "B"}])


if __name__ == "__main__":
    unittest.main()
