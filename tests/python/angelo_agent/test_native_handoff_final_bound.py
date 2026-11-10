# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One central monotonic budget holds final work above an admission fence."""
import suite_isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_json import HandoffBudget, WORK_LIMIT, encode_json


class FinalBound(unittest.TestCase):
    def test_reserving_never_spends_restores_or_transfers_remaining_credit(self):
        budget = HandoffBudget(20)
        self.assertTrue(budget.reserve_final(5, 7))
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars), (20, 20))
        self.assertEqual((budget.nodes_left, budget.chars_left), (15, 13))
        self.assertEqual(budget.final_bound, (5, 7))

    def test_admission_cannot_spend_held_final_credit(self):
        budget = HandoffBudget(20)
        budget.reserve_final(5, 7)
        for _ in range(15):
            self.assertTrue(budget.visit())
        self.assertFalse(budget.visit())
        self.assertFalse(budget.scan(14))
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars), (5, 7))

    def test_phase_transition_changes_only_the_fence_not_remaining_counters(self):
        budget = HandoffBudget(20)
        budget.reserve_final(5, 7)
        budget.scan(14)
        before = budget.remaining_nodes, budget.remaining_chars
        budget.begin_final_projection()
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars), before)
        self.assertTrue(budget.visit())
        self.assertTrue(budget.scan(2))
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars), (before[0] - 1, before[1] - 2))

    def test_bound_is_monotonic_and_failure_does_not_replace_it(self):
        budget = HandoffBudget(20)
        self.assertTrue(budget.reserve_final(2, 3))
        self.assertTrue(budget.reserve_final(4, 5))
        before = budget.remaining_nodes, budget.remaining_chars, budget.final_bound
        self.assertFalse(budget.reserve_final(15, 1))
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars, budget.final_bound), before)

    def test_rejected_candidate_keeps_its_actual_inspection_cost(self):
        budget = HandoffBudget(20)
        budget.visit()
        budget.visit()
        budget.scan(3)
        self.assertFalse(budget.reserve_final(20, 20))
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars), (18, 17))

    def test_invalid_bounds_are_not_credits(self):
        for nodes, characters in ((True, 1), (1, True), (-1, 1), (1, -1), (0, 0), (2, 0), ("1", 1)):
            with self.subTest(nodes=nodes, characters=characters), self.assertRaises(ValueError):
                HandoffBudget(20).reserve_final(nodes, characters)

    def test_final_transition_is_one_way_and_no_new_reservations_follow(self):
        budget = HandoffBudget(20)
        budget.begin_final_projection()
        with self.assertRaises(ValueError):
            budget.begin_final_projection()
        with self.assertRaises(ValueError):
            budget.reserve_final(1, 1)

    def test_encoder_work_refusal_cannot_consume_held_final_headroom(self):
        budget = HandoffBudget(200)
        budget.reserve_final(100, 100)
        refused = encode_json("x" * 101, budget=budget)
        self.assertEqual(refused.reason, WORK_LIMIT)
        self.assertIsNone(refused.text)
        self.assertEqual(budget.remaining_chars, 100)
        before = budget.remaining_nodes, budget.remaining_chars
        budget.begin_final_projection()
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars), before)
        self.assertEqual(encode_json("ok", budget=budget).text, '"ok"')

    def test_filtered_tail_cannot_starve_an_already_reserved_recent_entry(self):
        budget = HandoffBudget(1000)
        # Exact graph: one dict, two keys/two values; 19 input characters plus visit headroom.
        self.assertTrue(budget.reserve_final(5, 20))
        examined = 0
        for _ in range(100000):
            if not budget.visit() or not budget.scan(len("role")):
                break
            examined += 1
        self.assertLess(examined, 100000)
        before = budget.remaining_nodes, budget.remaining_chars
        budget.begin_final_projection()
        self.assertEqual((budget.remaining_nodes, budget.remaining_chars), before)
        encoded = encode_json({"role": "user", "text": "current"}, budget=budget)
        self.assertIsNone(encoded.reason)
        self.assertEqual(json.loads(encoded.text)["text"], "current")


if __name__ == "__main__":
    unittest.main()
