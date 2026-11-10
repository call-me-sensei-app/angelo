# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A builtin dictionary is admitted before lookup, including its key types."""
import suite_isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED, WORK_LIMIT, admit_mapping


class MappingBudget(unittest.TestCase):
    def test_string_keys_are_admitted_with_node_and_character_credit(self):
        budget = HandoffBudget(100)
        self.assertIsNone(admit_mapping({"role": "user", "text": "hello"}, budget))
        self.assertEqual((budget.nodes_left, budget.chars_left), (98, 92))

    def test_colliding_custom_key_never_runs_equality(self):
        class Collision:
            def __hash__(self):
                return hash("role")
            def __eq__(self, other):
                raise AssertionError("custom equality")
        self.assertEqual(admit_mapping({Collision(): "user"}, HandoffBudget(100)), UNSUPPORTED)

    def test_custom_mapping_is_not_iterated(self):
        class Custom(dict):
            def __iter__(self):
                raise AssertionError("custom iteration")
        self.assertEqual(admit_mapping(Custom(), HandoffBudget(100)), UNSUPPORTED)

    def test_unexamined_key_tail_stops_at_node_credit(self):
        value = {str(index): None for index in range(100000)}
        budget = HandoffBudget(3)
        self.assertEqual(admit_mapping(value, budget), WORK_LIMIT)
        self.assertEqual(budget.nodes_left, 0)

    def test_large_key_refuses_from_length_before_lookup(self):
        budget = HandoffBudget(10)
        self.assertEqual(admit_mapping({"x" * 1000000: None}, budget), WORK_LIMIT)
        self.assertEqual(budget.chars_left, 0)

    def test_no_admission_is_reused_after_dictionary_changes(self):
        value, budget = {"role": "user"}, HandoffBudget(100)
        self.assertIsNone(admit_mapping(value, budget))
        value[1] = "not a string key"
        self.assertEqual(admit_mapping(value, budget), UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
