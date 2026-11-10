# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded primitive handoff JSON: differential bytes and resource refusal."""
import suite_isolation  # noqa: F401
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import handoff_json as H


class HandoffJson(unittest.TestCase):
    def test_supported_values_match_existing_json_bytes(self):
        values = [None, True, False, 0, -73, 2 ** 100, 1.5, -0.0, 1e-100, "", "café 日本語",
                  'quote" slash\\ line\n\b\f\r\t\x00', [], {}, [1, "x", None],
                  {"role": "user", "text": "A\nB", "nested": [{"ok": True}, []]}]
        for compact in (False, True):
            for value in values:
                with self.subTest(compact=compact, value=value):
                    expected = json.dumps(value, ensure_ascii=False, allow_nan=False,
                                          separators=(",", ":") if compact else None)
                    result = H.encode_json(value, budget=H.HandoffBudget(max(1, len(expected))), compact=compact)
                    self.assertEqual(result.text, expected)
                    self.assertIsNone(result.reason)

    def test_surrogates_are_escaped_for_transport(self):
        result = H.encode_json("\ud800x\udfff", budget=H.HandoffBudget(40))
        self.assertEqual(result.text, '"\\ud800x\\udfff"')
        result.text.encode("utf-8")

    def test_exact_size_and_one_character_short(self):
        value = {"text": "quote\""}
        expected = json.dumps(value, ensure_ascii=False)
        self.assertEqual(H.encode_json(value, budget=H.HandoffBudget(len(expected))).text, expected)
        refused = H.encode_json(value, budget=H.HandoffBudget(len(expected)), max_chars=len(expected) - 1)
        self.assertIsNone(refused.text)
        self.assertEqual(refused.reason, H.OUTPUT_LIMIT)

    def test_large_string_lower_bound_does_not_scan_or_copy_its_tail(self):
        budget = H.HandoffBudget(20)
        result = H.encode_json("x" * 1000000, budget=budget)
        self.assertEqual(result.reason, H.OUTPUT_LIMIT)
        self.assertEqual(budget.chars_left, 20)
        self.assertEqual(budget.nodes_left, 19)

    def test_deep_input_uses_a_stack_and_stops_at_node_credit(self):
        value = 0
        for _ in range(2000):
            value = [value]
        budget = H.HandoffBudget(80)
        result = H.encode_json(value, budget=budget)
        self.assertIsNone(result.text)
        self.assertIn(result.reason, (H.WORK_LIMIT, H.OUTPUT_LIMIT))
        self.assertGreaterEqual(budget.nodes_left, 0)

    def test_cycles_refuse_but_repeated_noncyclic_aliases_encode(self):
        cycle = []
        cycle.append(cycle)
        self.assertEqual(H.encode_json(cycle, budget=H.HandoffBudget(100)).reason, H.CYCLE)
        shared = [1, 2]
        self.assertEqual(H.encode_json([shared, shared], budget=H.HandoffBudget(100)).text,
                         json.dumps([shared, shared]))

    def test_unknown_and_subclass_values_never_call_custom_protocols(self):
        class Hostile:
            def __repr__(self):
                raise AssertionError("repr called")
            def __str__(self):
                raise AssertionError("str called")
            def __iter__(self):
                raise AssertionError("iterator called")
        class HostileList(list):
            def __iter__(self):
                raise AssertionError("subclass iterator called")
        class HostileMeta(type):
            def __eq__(cls, other):
                raise AssertionError("custom type equality called")
        class HostileType(metaclass=HostileMeta):
            pass
        for value in (Hostile(), HostileList([1]), HostileType(), (1, 2), {1: "not a JSON string key"}):
            result = H.encode_json(value, budget=H.HandoffBudget(100))
            self.assertIsNone(result.text)
            self.assertEqual(result.reason, H.UNSUPPORTED)

    def test_invalid_numbers_refuse(self):
        for value in (math.nan, math.inf, -math.inf):
            self.assertEqual(H.encode_json(value, budget=H.HandoffBudget(100)).reason, H.INVALID)
        huge = 1 << 1000000
        result = H.encode_json(huge, budget=H.HandoffBudget(30))
        self.assertIsNone(result.text)
        self.assertEqual(result.reason, H.OUTPUT_LIMIT)

    def test_work_cannot_reset_after_failed_encoding(self):
        budget = H.HandoffBudget(12)
        for _ in range(12):
            H.encode_json("x" * 100, budget=budget)
        self.assertEqual(budget.nodes_left, 0)
        self.assertEqual(H.encode_json(0, budget=budget).reason, H.WORK_LIMIT)

    def test_char_credit_is_spent_even_when_escaping_exceeds_output(self):
        budget = H.HandoffBudget(8)
        result = H.encode_json("\x00" * 4, budget=budget)
        self.assertEqual(result.reason, H.OUTPUT_LIMIT)
        self.assertLess(budget.chars_left, 8)

    def test_all_output_counts_header_and_stub_and_pop_does_not_refund_work(self):
        budget = H.HandoffBudget(24)
        output = H.HandoffOutput(budget)
        self.assertTrue(output.append("Header\n"))
        encoded = H.encode_json({"x": 1}, budget=budget)
        self.assertTrue(output.append(encoded.text + "\n"))
        before = budget.nodes_left, budget.chars_left
        output.pop()
        self.assertEqual((budget.nodes_left, budget.chars_left), before)
        self.assertTrue(output.append('{"omitted": 2}'))
        self.assertEqual(len(output.render()), output.used)
        self.assertLessEqual(output.used, budget.limit)
        self.assertFalse(output.append("x" * 24))
        with self.assertRaises(ValueError):
            H.HandoffOutput(budget)

    def test_empty_and_invalid_budget_refuse_explicitly(self):
        for limit in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                H.HandoffBudget(limit)
        self.assertEqual(H.encode_json({}, budget=H.HandoffBudget(1)).reason, H.OUTPUT_LIMIT)
        self.assertEqual(H.encode_json(0, budget=H.HandoffBudget(1)).text, "0")

    def test_budget_has_no_public_reset_or_negative_credit(self):
        budget = H.HandoffBudget(10)
        for count in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                budget.scan(count)
        for field in ("limit", "nodes_left", "chars_left"):
            with self.assertRaises(AttributeError):
                setattr(budget, field, 100)
        self.assertFalse(budget.scan(11))
        self.assertEqual(budget.chars_left, 0)


if __name__ == "__main__":
    unittest.main()
