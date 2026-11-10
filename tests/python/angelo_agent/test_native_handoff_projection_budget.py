# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete bounded copies preserve the actual identifier owner's output/order."""
import suite_isolation  # noqa: F401
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_json import CYCLE, INVALID, UNSUPPORTED, WORK_LIMIT, HandoffBudget
from angelo_agent.handoff_projection import compact
from angelo_agent.identifiers import IdentifierTable

HANDLE = "scn:" + "a" * 32 + ":1"
OTHER = "scn:" + "b" * 32 + ":2"
REVISION = "c" * 64


class ProjectionBudget(unittest.TestCase):
    def test_values_and_numbering_match_existing_compaction(self):
        values = [None, True, 4, 2.5, "plain text", HANDLE, {HANDLE: OTHER},
                  {"revision": REVISION, "handle": HANDLE},
                  {"target": OTHER, "object_revision": REVISION, "nested": [HANDLE, {"object": HANDLE,
                   "source_revision": REVISION}]}, {"text": "free text " + HANDLE}]
        old, new = IdentifierTable("native"), IdentifierTable("native")
        for value in values:
            expected = old.compact(value)
            projected = compact(value, budget=HandoffBudget(10000), identifiers=new)
            self.assertIsNone(projected.reason)
            self.assertEqual(projected.value, expected)
            self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_plain_copy_preserves_order_and_does_not_reuse_containers(self):
        value = {"z": [1, {"b": "café", "a": "日本語"}], "a": True}
        projected = compact(value, budget=HandoffBudget(1000))
        self.assertEqual(projected.value, value)
        self.assertEqual(list(projected.value), list(value))
        self.assertIsNot(projected.value, value)
        self.assertIsNot(projected.value["z"], value["z"])

    def test_large_string_refuses_before_regex_or_table_updates(self):
        table, budget = IdentifierTable(), HandoffBudget(10)
        projected = compact("x" * 1000000, budget=budget, identifiers=table)
        self.assertEqual(projected.reason, WORK_LIMIT)
        self.assertIsNone(projected.value)
        self.assertEqual(table.to_dict("native")["handles"], [])

    def test_deep_values_are_iterative_and_stop_without_partial_value(self):
        value = 1
        for _ in range(10000):
            value = [value]
        budget = HandoffBudget(20)
        projected = compact(value, budget=budget)
        self.assertEqual(projected.reason, WORK_LIMIT)
        self.assertIsNone(projected.value)
        self.assertEqual(budget.nodes_left, 0)

    def test_cycle_refuses_but_shared_acyclic_children_remain_supported(self):
        value = []
        value.append(value)
        self.assertEqual(compact(value, budget=HandoffBudget(100)).reason, CYCLE)
        shared = {"a": "b"}
        projected = compact([shared, shared], budget=HandoffBudget(100))
        self.assertIsNone(projected.reason)
        self.assertEqual(projected.value, [shared, shared])

    def test_custom_values_and_tables_never_get_called(self):
        class Custom(dict):
            def items(self):
                raise AssertionError("custom iterator")
        class Table:
            def short_handle(self, value):
                raise AssertionError("custom table")
        self.assertEqual(compact(Custom(), budget=HandoffBudget(100)).reason, UNSUPPORTED)
        self.assertEqual(compact(HANDLE, budget=HandoffBudget(100), identifiers=Table()).reason, UNSUPPORTED)
        self.assertEqual(compact((1, 2), budget=HandoffBudget(100)).reason, UNSUPPORTED)

    def test_nonfinite_numbers_never_become_supported_json(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            self.assertEqual(compact(value, budget=HandoffBudget(100)).reason, INVALID)

    def test_ambiguous_revision_prefix_preserves_owner_behavior(self):
        old, new = IdentifierTable(), IdentifierTable()
        for revision in (REVISION, REVISION[:16] + "d" * 48):
            value = {"handle": HANDLE, "revision": revision}
            expected = old.compact(value)
            projected = compact(value, budget=HandoffBudget(1000), identifiers=new)
            self.assertEqual(projected.value, expected)
        self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_source_is_not_modified_on_refusal(self):
        value = {"handle": HANDLE, "nested": ["x" * 100]}
        before = copy.deepcopy(value)
        self.assertEqual(compact(value, budget=HandoffBudget(10)).reason, WORK_LIMIT)
        self.assertEqual(value, before)


if __name__ == "__main__":
    unittest.main()
