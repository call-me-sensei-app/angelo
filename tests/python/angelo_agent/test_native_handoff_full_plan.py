# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whole full-result plans preserve old bytes and fund final work before admission ends."""
import suite_isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_currency import Currency
from angelo_agent.handoff_currency_view import INCOMPLETE_CURRENCY
from angelo_agent.handoff_full import FullPlan, prepare_full, render_full
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED
from angelo_agent.identifiers import IdentifierTable
from angelo_agent.program_completion import history_result_text as previous

H1, H2 = "scn:" + "a" * 32 + ":1", "scn:" + "b" * 32 + ":2"
R1, R2 = "c" * 64, "d" * 64


class FullResultPlan(unittest.TestCase):
    def parity(self, result, *, short=False, changed=False):
        old, new = (IdentifierTable(), IdentifierTable()) if short else (None, None)
        old_currency, currency = (Currency(old), Currency(new)) if short else (None, None)
        if changed:
            old_currency.committed[H1] = currency.committed[H1] = R2
        expected = previous(result, old, old_currency)
        budget = HandoffBudget(1000000)
        plan = prepare_full(result, budget=budget, compact_json=short)
        self.assertIs(type(plan), FullPlan)
        if short:
            self.assertEqual(new.to_dict("native"), IdentifierTable().to_dict("native"))
        self.assertTrue(budget.reserve_final(plan.final_work.nodes, plan.final_work.characters))
        # Spend all unreserved character work: final projection has no access
        # to a reset budget or credit belonging to a rejected older record.
        if budget.chars_left:
            budget.scan(budget.chars_left)
        budget.begin_final_projection()
        before = budget.remaining_nodes, budget.remaining_chars
        projected = render_full(plan, budget=budget, identifiers=new, currency=currency)
        self.assertIsNone(projected.reason)
        self.assertEqual(projected.value, expected)
        self.assertLessEqual(before[0] - budget.remaining_nodes, plan.final_work.nodes)
        self.assertLessEqual(before[1] - budget.remaining_chars, plan.final_work.characters)
        if short:
            self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_small_primitive_and_dictionary_results(self):
        for result in (None, "text", 99, {"items": [1, False, "café", "\n"]}):
            for short in (False, True):
                self.parity(result, short=short)

    def test_currency_is_final_and_both_sections_keep_current_rows(self):
        self.parity({"live_revisions": {"objects": [{"handle": H1, "object_revision": R1}]},
                     "after": {"objects": [{"handle": H2, "object_revision": R2}]}}, short=True, changed=True)

    def test_existing_changed_since_merge_order_is_preserved(self):
        self.parity({"changed_since": [H2], "after": {"objects": [
            {"handle": H1, "object_revision": R1}]}}, short=True, changed=True)

    def test_long_late_facts_and_repeated_operation_note(self):
        result = {"readback": "x" * 6000, "world": {"object": H1}, "motion": {"frames": [1, 4]},
                  "operation_results": [{"also_slots_only": {"note": "Same", "object": H2}}
                                        for _ in range(3)]}
        for short in (False, True):
            self.parity(result, short=short)

    def test_oversized_fact_is_named_without_unfunded_reencoding(self):
        self.parity({"readback": "x" * 6000, "world": {"long": "w" * 4000},
                     "unobservable_checks": ["Unknown"]}, short=True)

    def test_fact_tail_does_not_retouch_identifier_lru(self):
        self.parity({"readback": "x" * 6000, "world": {"object": H1},
                     "last": {"object": H2}}, short=True)

    def test_malformed_operation_rows_and_nonlist_scalar_are_still_funded(self):
        for value in ([None, "ignored", False] * 200, "ignored" * 200, 1 << 1000):
            self.parity({"readback": "x" * 6000, "operation_results": value}, short=True)

    def test_operation_note_deduplication_uses_already_shortened_text(self):
        self.parity({"readback": "x" * 6000, "operation_results": [
            {"also_slots_only": {"note": H1, "object": H1}},
            {"also_slots_only": {"note": "o1", "object": H2}}]}, short=True)

    def test_partial_order_and_existing_excerpt_value_are_both_bounded(self):
        for existing in ("", "old" * 2000):
            self.parity({"before": "x" * 6000, "verification": {
                "status": "partial", "excerpt": existing, "unknowns": [{"reason": "Unknown", "operation": 1}]},
                "unverified_effects": ["Unmeasured"]}, short=True)

    def test_currency_and_partial_verification_order_match(self):
        self.parity({"after": {"objects": [{"handle": H1, "object_revision": R1}]},
                     "verification": {"unknowns": [{"reason": "Unknown"}], "status": "partial"}},
                    short=True, changed=True)

    def test_custom_colliding_key_cannot_cross_admission(self):
        class Collision:
            def __hash__(self):
                return hash("verification")
            def __eq__(self, other):
                raise AssertionError("custom key lookup")
        projected = prepare_full({Collision(): {}}, budget=HandoffBudget(1000), compact_json=True)
        self.assertEqual(projected.reason, UNSUPPORTED)

    def test_incomplete_window_refuses_unknown_revision_but_keeps_known_current(self):
        result = {"after": {"objects": [{"handle": H1, "object_revision": R2}]}}
        for known in (False, True):
            budget = HandoffBudget(100000)
            plan = prepare_full(result, budget=budget, compact_json=True)
            table, currency = IdentifierTable(), Currency()
            if known:
                currency.committed[H1] = R1
            self.assertTrue(budget.reserve_final(plan.final_work.nodes, plan.final_work.characters))
            budget.begin_final_projection()
            projected = render_full(plan, budget=budget, identifiers=table,
                                    currency=currency, history_complete=False)
            if known:
                expected = previous(result, IdentifierTable(), currency)
                self.assertIsNone(projected.reason)
                self.assertEqual(projected.value, expected)
            else:
                self.assertEqual(projected.reason, INCOMPLETE_CURRENCY)
                self.assertIsNone(projected.value)
                self.assertEqual(table.to_dict("native"), IdentifierTable().to_dict("native"))


if __name__ == "__main__":
    unittest.main()
