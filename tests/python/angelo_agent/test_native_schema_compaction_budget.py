# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lossless static sharing: generated names, ordered fragments and cache bounds."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.schema_compaction import _compact_serialized, compact_schema
from test_native_schema_compaction import expand_generated


class CompactionBudgetTests(unittest.TestCase):
    def test_surviving_names_are_dense_and_references_resolve(self):
        nodes = [{"type": "string", "description": "repeated schema " + str(index) + "x" * 100,
                  "minLength": index} for index in range(54)]
        schema = {"type": "object", "properties": {"field" + str(index): node
                  for index, node in enumerate(nodes + copy.deepcopy(nodes))}}
        compact = compact_schema(schema, preserve_order=True)
        self.assertEqual(list(compact["$defs"]), list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["aa", "ab"])
        self.assertEqual(json.dumps(expand_generated(compact)), json.dumps(schema))
        self.assertLess(len(json.dumps(compact)), len(json.dumps(schema)))

    def test_equal_constraints_with_different_property_order_remain_distinct(self):
        first = {"type": "object", "properties": {"later": {"type": "string"}, "earlier": {"type": "number"}},
                 "required": ["later", "earlier"], "additionalProperties": False}
        second = copy.deepcopy(first)
        second["properties"] = dict(reversed(list(second["properties"].items())))
        schema = {"type": "object", "properties": {"one": first, "two": second, "three": first, "four": second}}
        compact = compact_schema(schema, 32, preserve_order=True)
        self.assertEqual(json.dumps(expand_generated(compact)), json.dumps(schema))
        self.assertNotEqual(compact["properties"]["one"], compact["properties"]["two"])
        self.assertEqual(compact["properties"]["one"], compact["properties"]["three"])
        self.assertEqual(expand_generated(compact_schema(schema, 32)), schema)

    def test_ordered_literal_reference_names_are_data(self):
        literal = {"$ref": "#/$defs/s0", "$defs": {"a": {"$ref": "#/$defs/a"}}, "s0": "a"}
        node = {"type": "string", "description": "a" * 150, "default": literal}
        schema = {"properties": {"z": node, "a": node}, "examples": [literal], "required": ["z", "a"]}
        compact = compact_schema(schema, preserve_order=True)
        self.assertEqual(json.dumps(expand_generated(compact)), json.dumps(schema))
        self.assertEqual(compact["$defs"]["a"]["default"], literal)
        self.assertEqual(compact["examples"], [literal])

    def test_cache_is_bounded_and_order_mode_has_its_own_entry(self):
        schema = {"properties": {"z": {"type": "string"}, "a": {"type": "number"}}}
        sorted_result = compact_schema(schema)
        ordered_result = compact_schema(schema, preserve_order=True)
        self.assertEqual(list(sorted_result["properties"]), ["a", "z"])
        self.assertEqual(list(ordered_result["properties"]), ["z", "a"])
        for index in range(70):
            compact_schema({"type": "string", "description": "bounded-cache-" + str(index)})
        self.assertEqual(_compact_serialized.cache_info().maxsize, 64)
        self.assertLessEqual(_compact_serialized.cache_info().currsize, 64)


if __name__ == "__main__":
    unittest.main()
