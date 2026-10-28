# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nullable scalar representation preserves strict-plan values and property order."""

import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import codex_plan, strict_schema
from angelo_agent.schema_compaction import compact_nullable_scalars, compact_schema
from strict_plan_vectors import GATE_OUTPUTS, NAMED, union_vectors

try:
    import jsonschema
except ImportError:
    jsonschema = None


def property_orders(schema):
    if isinstance(schema, list):
        return [row for item in schema for row in property_orders(item)]
    if not isinstance(schema, dict):
        return []
    own = [list(schema["properties"])] if "properties" in schema else []
    return own + [row for key, value in schema.items() if key not in {"enum", "const", "default"}
                  for row in property_orders(value)]


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class NullableSchemaTests(unittest.TestCase):
    def test_scalar_values_and_constraints_have_identical_acceptance(self):
        samples = [None, False, True, -2, 0, 1, 1.5, 2, 3, "", "A", "B", "C", [], {}, [1]]
        scalars = [{"type": "string"}, {"type": "boolean"},
                   {"type": "number", "exclusiveMinimum": 0, "maximum": 2},
                   {"type": "integer", "minimum": 0, "exclusiveMaximum": 3},
                   {"type": "string", "enum": ["A", "B"]},
                   {"type": "integer", "enum": [0, 2], "minimum": 1},
                   {"type": "number", "const": 1, "maximum": 0},
                   {"type": "string", "const": "A"}]
        for scalar in scalars:
            with self.subTest(scalar=scalar):
                before = {"anyOf": [scalar, {"type": "null"}]}
                preserved = copy.deepcopy(before)
                after = compact_nullable_scalars(before)
                self.assertLess(len(json.dumps(after)), len(json.dumps(before)))
                old, new = (jsonschema.Draft202012Validator(value) for value in (before, after))
                self.assertEqual([old.is_valid(value) for value in samples],
                                 [new.is_valid(value) for value in samples])
                self.assertEqual(before, preserved)

    def test_other_unions_references_and_literal_values_are_unchanged(self):
        scalar = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        cases = [{"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
                 {"anyOf": [{"type": "object", "properties": {}}, {"type": "null"}]},
                 {"anyOf": [{"$ref": "#/$defs/a"}, {"type": "null"}], "$defs": {"a": {"type": "string"}}},
                 {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]},
                 {"anyOf": [{"type": "string", "const": "A", "enum": ["B"]}, {"type": "null"}]},
                 {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}], "not": {"type": "null"}},
                 {"enum": [scalar], "default": scalar}]
        for value in cases:
            with self.subTest(schema=value):
                self.assertEqual(compact_nullable_scalars(value), value)

    def test_actual_compacted_and_expanded_plan_corpus_is_equivalent(self):
        ordinary = codex_plan.ordinary_schema()
        before = compact_schema(strict_schema.strict_schema(ordinary),
                                min_shared_bytes=codex_plan.STRICT_SHARED_BYTES, preserve_order=True)
        after = codex_plan.output_schema()
        expanded = [strict_schema.inline_references(value) for value in (before, after)]
        self.assertEqual(property_orders(expanded[0]), property_orders(expanded[1]))
        self.assertEqual(list(before["$defs"]), list(after["$defs"]))
        # 37,040 B: the recipe and transform short forms (+1,707 B), mesh_bend's name (+12 B) and the web export
        # (+41 B; test_native_codex_plan's budget says why).
        self.assertLessEqual(len(json.dumps(after, separators=(",", ":"))), 37040)
        self.assertLess(len(json.dumps(after)), len(json.dumps(before)))
        validators = [jsonschema.Draft202012Validator(schema) for schema in (before, after, *expanded)]
        for schema in (before, after, *expanded):
            self.assertEqual(strict_schema.strict_problems(schema), [])
        corpus = union_vectors(ordinary)
        corpus += [(name, {"reply": "", "operations": [operation]}) for name, operation in NAMED.items()]
        for label, plan in corpus:
            with self.subTest(vector=label):
                written = strict_schema.encode(plan, ordinary)
                self.assertTrue(all(validator.is_valid(written) for validator in validators))
                # Existing plan semantics omit optional nulls on both paths.
                self.assertEqual(strict_schema.decode(written, ordinary), strict_schema.decode(plan, ordinary))
                malformed = dict(written, unexpected=True)
                self.assertFalse(any(validator.is_valid(malformed) for validator in validators))
        self.assertGreater(len(corpus), 100)

    def test_recorded_gate_plans_decode_without_changing_their_arguments(self):
        ordinary = codex_plan.ordinary_schema()
        before = compact_schema(strict_schema.strict_schema(ordinary),
                                min_shared_bytes=codex_plan.STRICT_SHARED_BYTES, preserve_order=True)
        old = jsonschema.Draft202012Validator(before)
        new = jsonschema.Draft202012Validator(codex_plan.output_schema())
        for model, request, text in GATE_OUTPUTS:
            with self.subTest(model=model, request=request):
                recorded = json.loads(text)
                self.assertEqual(old.is_valid(recorded), new.is_valid(recorded))
                decoded, problem = codex_plan.parse_plan(text)
                self.assertIsNone(problem)
                self.assertEqual(decoded, strict_schema.decode(recorded, ordinary))


if __name__ == "__main__":
    unittest.main()
