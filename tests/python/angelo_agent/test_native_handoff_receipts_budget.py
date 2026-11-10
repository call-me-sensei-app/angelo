# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Historical check inspection is bounded and never turns incomplete evidence positive."""
import suite_isolation  # noqa: F401
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.claude_outcomes import _positive, compact_receipt as previous_receipt
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED, WORK_LIMIT
from angelo_agent.handoff_receipts import Reader, compact_receipt
from angelo_agent.program_completion import positive_rows


def passed(**extra):
    return {"passed": True, "status": "pass", **extra}


def committed(**extra):
    return {"success": True, "final": True, "status": "committed",
            "checks": [passed(checks=[passed(kind="dimensions", observed={"dimensions": [1, 2, 3]})])], **extra}


class ReceiptBudget(unittest.TestCase):
    def test_predicate_parity_on_supported_evidence(self):
        cases = [[], [{}], [passed()], [passed(checks=[passed()])], [passed(passed=False)],
                 [passed(status="inconclusive")], [passed(checks=[])], [passed(checks=None)],
                 [passed(), passed(checks=[passed(checks=[passed()])])]]
        for rows in cases:
            with self.subTest(rows=rows):
                result = Reader(HandoffBudget(1000)).positive_rows(rows)
                self.assertIsNone(result.reason)
                self.assertEqual(result.positive, positive_rows(rows))

    def test_outer_program_checks_require_inner_positive_rows(self):
        for rows in ([passed()], [passed(checks=[])], [passed(checks=[passed()])],
                     [passed(checks=[passed(passed=False)])]):
            result = Reader(HandoffBudget(1000)).positive_rows(rows, require_children=True)
            self.assertEqual(result.positive, bool(rows) and all(_positive(row) for row in rows))

    def test_cycle_is_not_positive(self):
        row = passed()
        row["checks"] = [row]
        result = Reader(HandoffBudget(100)).positive_rows([row])
        self.assertFalse(result.positive)

    def test_shared_acyclic_predicates_can_pass_twice(self):
        shared = passed()
        rows = [passed(checks=[shared]), passed(checks=[shared])]
        self.assertTrue(Reader(HandoffBudget(1000)).positive_rows(rows).positive)

    def test_deep_tree_stops_on_shared_work_budget(self):
        rows = [passed()]
        for _ in range(10000):
            rows = [passed(checks=rows)]
        budget = HandoffBudget(30)
        # Earlier structural work can consume nodes without string scans.
        # Use the same ledger; the reader's root/row/lookup visits then reach
        # the node boundary before key admission can spend character credit.
        for _ in range(budget.limit - 3):
            self.assertTrue(budget.visit())
        self.assertEqual(budget.chars_left, budget.limit)
        result = Reader(budget).positive_rows(rows)
        self.assertFalse(result.positive)
        self.assertEqual(result.reason, WORK_LIMIT)
        self.assertEqual(budget.nodes_left, 0)
        self.assertEqual(budget.chars_left, budget.limit)

    def test_deep_key_admission_can_exhaust_characters_before_nodes_without_reuse(self):
        rows = [passed()]
        for _ in range(10000):
            rows = [passed(checks=rows)]
        budget = HandoffBudget(30)
        result = Reader(budget).positive_rows(rows)
        self.assertFalse(result.positive)
        self.assertEqual(result.reason, WORK_LIMIT)
        self.assertEqual(budget.chars_left, 0)
        self.assertGreater(budget.nodes_left, 0)
        self.assertTrue(budget.exhausted)
        remaining = budget.nodes_left, budget.chars_left
        repeated = Reader(budget).positive_rows([passed()])
        self.assertFalse(repeated.positive)
        self.assertEqual(repeated.reason, WORK_LIMIT)
        self.assertEqual((budget.nodes_left, budget.chars_left), remaining)

    def test_large_flat_tail_is_not_preloaded(self):
        row = passed()
        rows = [row] * 100000
        result = Reader(HandoffBudget(20)).positive_rows(rows)
        self.assertFalse(result.positive)
        self.assertEqual(result.reason, WORK_LIMIT)

    def test_custom_field_values_do_not_execute_equality_or_truth(self):
        class Custom:
            def __bool__(self):
                raise AssertionError("custom truth")
            def __eq__(self, other):
                raise AssertionError("custom equality")
        result = Reader(HandoffBudget(100)).positive_rows([passed(status=Custom())])
        self.assertFalse(result.positive)
        self.assertEqual(result.reason, UNSUPPORTED)

    def test_custom_rows_do_not_execute_get(self):
        class Custom(dict):
            def get(self, *args):
                raise AssertionError("custom get")
        result = Reader(HandoffBudget(100)).positive_rows([Custom()])
        self.assertFalse(result.positive)

    def test_exact_dict_custom_key_cannot_run_equality_during_predicate_lookup(self):
        class Collision:
            def __init__(self, target):
                self.target = target
            def __hash__(self):
                return hash(self.target)
            def __eq__(self, other):
                raise AssertionError("custom equality")
        for target in ("passed", "status", "checks"):
            row = {key: value for key, value in passed().items() if key != target}
            row[Collision(target)] = True
            result = Reader(HandoffBudget(1000)).positive_rows([row])
            self.assertEqual(result.reason, UNSUPPORTED)
            self.assertFalse(result.positive)

    def test_exhausted_owner_cannot_reset_by_starting_another_check(self):
        budget = HandoffBudget(3)
        reader = Reader(budget)
        first = reader.positive_rows([passed()])
        second = reader.positive_rows([passed()])
        self.assertEqual((first.reason, second.reason), (WORK_LIMIT, WORK_LIMIT))
        self.assertFalse(first.positive or second.positive)

    def test_selected_status_characters_use_the_same_credit(self):
        budget = HandoffBudget(3)
        result = Reader(budget).positive_rows([passed()])
        self.assertFalse(result.positive)
        self.assertEqual(result.reason, WORK_LIMIT)

    def test_compact_receipt_matches_original_for_supported_families(self):
        values = [committed(), committed(status="preview", preview_id="preview-id", delta={"added": [{
            "name": "Panel", "type": "MESH"}]}), committed(live_revisions={"status": "COMMITTED", "objects": [
            {"handle": "object", "name": "Panel", "object_revision": "revision"}]}),
            committed(changes={"added": [{"name": "Panel", "type": "MESH"}],
                               "modified": [{"name": "Light", "fields": ["energy"]}],
                               "removed": [{"name": "Old", "type": "MESH"}]}),
            committed(views=[{"id": "view", "name": "Overview"}]),
            committed(world={"color": [0.2, 0.3, 0.4]}, motion={"frames": [1, 5]}, unverified_effects=["effect"])]
        for tool in ("angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"):
            for value in values:
                before = copy.deepcopy(value)
                result = compact_receipt(tool, value, budget=HandoffBudget(100000))
                self.assertIsNone(result.reason)
                self.assertEqual(result.value, previous_receipt(tool, value))
                self.assertEqual(value, before)

    def test_large_row_excerpts_preserve_existing_counts_and_truncation(self):
        value = committed(live_revisions={"status": "COMMITTED", "objects": [None] + [
            {"handle": "h%d" % i, "object_revision": "r%d" % i} for i in range(40)]},
            changes={"modified": [{"name": "Object", "fields": ["location"]}] * 40},
            unchecked_dependents=[{"name": "Follower"}] * 40)
        result = compact_receipt("angelo_scene_program", value, budget=HandoffBudget(100000))
        self.assertIsNone(result.reason)
        self.assertEqual(result.value, previous_receipt("angelo_scene_program", value))

    def test_shared_edits_and_operation_fact_notes_keep_original_order(self):
        value = committed(shared_edits=[{"material": "Coating", "count": 12,
            "users": [None] + ["user%d" % i for i in range(12)], "holders": ["Unused mesh"]}] * 6,
            operation_results=[{"also_slots_only": {"note": note, "objects": ["Panel"]}}
                               for note in ("First", "First", "Different")])
        result = compact_receipt("angelo_scene_program", value, budget=HandoffBudget(100000))
        self.assertIsNone(result.reason)
        self.assertEqual(result.value, previous_receipt("angelo_scene_program", value))

    def test_nonpositive_or_incomplete_results_never_gain_receipts(self):
        variants = [{"success": False}, {"final": False}, {"status": "failed"}, {"checks": []},
                    {"verification": None}, {"verification": {"status": "partial", "unknowns": ["unknown"]}},
                    {"cleanup_pending": True}, {"journal_durable": False}]
        for change in variants:
            value = committed(**change)
            result = compact_receipt("angelo_scene_program", value, budget=HandoffBudget(100000))
            self.assertIsNone(result.reason)
            self.assertIsNone(result.value)
            self.assertIsNone(previous_receipt("angelo_scene_program", value))

    def test_budget_cutoff_never_returns_partial_receipt(self):
        value = committed(operation_results=[{}] * 100000)
        result = compact_receipt("angelo_scene_program", value, budget=HandoffBudget(100))
        self.assertEqual(result.reason, WORK_LIMIT)
        self.assertIsNone(result.value)

    def test_unknown_nested_fact_refuses_without_custom_formatting(self):
        class Custom:
            def __str__(self):
                raise AssertionError("custom formatting")
            def __repr__(self):
                raise AssertionError("custom repr")
        value = committed(world={"unknown": Custom()})
        result = compact_receipt("angelo_scene_program", value, budget=HandoffBudget(10000))
        self.assertEqual(result.reason, UNSUPPORTED)
        self.assertIsNone(result.value)


if __name__ == "__main__":
    unittest.main()
