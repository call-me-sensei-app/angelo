# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent neutral graph shape and proven primitive final-pass credit."""
import suite_isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_bound import identifier_json_bound, neutral_graph
from angelo_agent.handoff_json import CYCLE, HandoffBudget, UNSUPPORTED, encode_json
from angelo_agent import handoff_bound, handoff_json
from angelo_agent.handoff_projection import compact
from angelo_agent.identifiers import IdentifierTable


H = "scn:" + "a" * 32 + ":1"
R = "b" * 64


class NeutralBound(unittest.TestCase):
    def test_independent_shape_includes_quotes_escapes_and_separators(self):
        value = {"name": 'quote" slash\\\n\t\x00 café 日本語', "rows": [None, True, False, -27]}
        for short in (False, True):
            projected = neutral_graph(value, budget=HandoffBudget(10000), compact_json=short)
            self.assertIsNone(projected.reason)
            expected = json.dumps(value, ensure_ascii=False, separators=(",", ":") if short else None)
            self.assertEqual(projected.shape.json_chars, len(expected))

    def test_builtin_float_bound_is_independent_and_covers_extremes(self):
        for value in (0.0, -0.0, sys.float_info.max, sys.float_info.min, 5e-324, 1.2345678901234567):
            projected = neutral_graph(value, budget=HandoffBudget(1000))
            self.assertGreaterEqual(projected.shape.json_chars, len(json.dumps(value)))
            self.assertGreaterEqual(projected.shape.number_chars, len(json.dumps(value)))

    def test_final_copy_and_tracked_encoding_fit_held_credit_after_admission_drains(self):
        values = [None, [], {}, {"handle": H, "object_revision": R, "name": "Panel"},
                  {H: {"target": H, "revision": R}, "o1": "literal collision"},
                  [{"a": ["café", 1.25, False]}, {"object": H, "source_revision": R}]]
        for value in values:
            budget = HandoffBudget(100000)
            neutral = neutral_graph(value, budget=budget)
            self.assertIsNone(neutral.reason)
            bound = identifier_json_bound(neutral.shape)
            self.assertTrue(budget.reserve_final(bound.nodes, bound.characters))
            budget.scan(budget.chars_left + 1)
            before = budget.remaining_nodes, budget.remaining_chars
            budget.begin_final_projection()
            self.assertEqual((budget.remaining_nodes, budget.remaining_chars), before)
            table, old = IdentifierTable(), IdentifierTable()
            projected = compact(neutral.value, budget=budget, identifiers=table)
            self.assertIsNone(projected.reason)
            self.assertEqual(projected.value, old.compact(value))
            encoded = encode_json(projected.value, budget=budget, track_members=True)
            self.assertIsNone(encoded.reason)
            self.assertLessEqual(before[0] - budget.remaining_nodes, bound.nodes)
            self.assertLessEqual(before[1] - budget.remaining_chars, bound.characters)
            self.assertGreaterEqual(budget.remaining_chars, 1)
            self.assertLessEqual(len(encoded.text), bound.json_chars)

    def test_neutral_copy_preserves_identity_numbering_for_the_later_chronological_owner(self):
        budget = HandoffBudget(10000)
        table = IdentifierTable()
        neutral = neutral_graph({"handle": H, "revision": R}, budget=budget)
        self.assertEqual(table.to_dict("native")["handles"], [])
        self.assertEqual(neutral.value["handle"], H)
        self.assertIsNotNone(neutral.shape)
        # Shape is independent of a tight admission room; a later encoder may
        # prepay its conservative upper rather than need that refinement.
        tiny = neutral_graph(99, budget=HandoffBudget(2))
        roomy = HandoffBudget(100)
        before = roomy.remaining_chars
        encoded = encode_json(99, budget=roomy)
        self.assertIsNone(encoded.reason)
        self.assertGreaterEqual(tiny.shape.number_chars, before - roomy.remaining_chars)

    def test_cycle_and_custom_keys_return_no_shape_certificate(self):
        value = []
        value.append(value)
        self.assertEqual(neutral_graph(value, budget=HandoffBudget(100)).reason, CYCLE)
        class Collision:
            def __hash__(self):
                return hash("handle")
            def __eq__(self, other):
                raise AssertionError("custom equality")
        projected = neutral_graph({Collision(): H}, budget=HandoffBudget(1000))
        self.assertEqual(projected.reason, UNSUPPORTED)
        self.assertIsNone(projected.shape)

    def test_size_bound_cannot_be_supplied_as_a_saved_json_claim(self):
        with self.assertRaises(ValueError):
            identifier_json_bound({"values": 1, "json_chars": 1})

    def test_shape_refuses_1000_with_three_characters_before_decimal_conversion(self):
        budget = HandoffBudget(100)
        budget.scan(97)
        with patch.object(handoff_bound, "str", side_effect=AssertionError("decimal conversion before preflight"),
                          create=True):
            projected = neutral_graph(1000, budget=budget)
        self.assertIsNone(projected.value)
        self.assertIsNone(projected.shape)
        self.assertEqual(projected.reason, handoff_json.WORK_LIMIT)

    def test_encoder_refuses_1000_with_three_characters_before_decimal_conversion(self):
        budget = HandoffBudget(100)
        budget.scan(97)
        with patch.object(handoff_json, "str", side_effect=AssertionError("decimal conversion before preflight"),
                          create=True):
            encoded = encode_json(1000, budget=budget)
        self.assertIsNone(encoded.text)
        self.assertEqual(encoded.reason, handoff_json.WORK_LIMIT)
        for value in (-73, 99, 1000, 2 ** 100):
            expected = str(value)
            with self.subTest(value=value):
                fitted = encode_json(value, budget=HandoffBudget(len(expected)))
                self.assertEqual(fitted.text, expected)
                self.assertIsNone(fitted.reason)


if __name__ == "__main__":
    unittest.main()
