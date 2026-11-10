# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned neutral receipt filtering retains the original currency and proven final-work bound."""
import suite_isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_bound import neutral_graph
from angelo_agent.handoff_currency import CHANGED, Currency, receipt_rows
from angelo_agent.handoff_currency_view import INCOMPLETE_CURRENCY, plan_receipt, project_receipt
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED, encode_json
from angelo_agent.handoff_projection import compact
from angelo_agent.identifiers import IdentifierTable


H = "scn:" + "a" * 32 + ":1"
OLD, NEW = "b" * 64, "c" * 64


def reference(root, currency):
    kept, changed = receipt_rows(root.get("live", []), currency)
    output = {key: value for key, value in root.items() if key not in ("live", CHANGED, "live_truncated")}
    if kept:
        output["live"] = kept
    if changed:
        output[CHANGED] = changed
    if root.get("live_truncated"):
        output["live_truncated"] = root["live_truncated"]
    return output


class CurrencyView(unittest.TestCase):
    def check(self, root, currency):
        budget = HandoffBudget(100000)
        neutral = neutral_graph(root, budget=budget)
        plan = plan_receipt(neutral, budget=budget)
        self.assertTrue(budget.reserve_final(plan.final_work.nodes, plan.final_work.characters))
        budget.scan(budget.chars_left + 1)
        before = budget.remaining_nodes, budget.remaining_chars
        budget.begin_final_projection()
        projected = project_receipt(plan, currency, budget=budget)
        self.assertIsNone(projected.reason)
        expected = reference(root, currency)
        self.assertEqual(json.dumps(projected.value), json.dumps(expected))
        shown = compact(projected.value, budget=budget, identifiers=IdentifierTable())
        self.assertIsNone(shown.reason)
        encoded = encode_json(shown.value, budget=budget, track_members=True)
        self.assertIsNone(encoded.reason)
        self.assertLessEqual(before[0] - budget.remaining_nodes, plan.final_work.nodes)
        self.assertLessEqual(before[1] - budget.remaining_chars, plan.final_work.characters)
        self.assertGreaterEqual(budget.remaining_chars, 1)
        self.assertLessEqual(len(encoded.text), plan.final_work.json_chars)

    def test_latest_commit_filters_stale_rows_and_keeps_missing_revisions(self):
        currency = Currency()
        currency.committed[H] = NEW
        self.check({"role": "tool_result", "verified": "checks", "live": [
            {"handle": H, "name": "Panel", "object_revision": OLD},
            {"handle": "other", "revision_omitted": {"reason": "BUDGET"}}], "live_truncated": True}, currency)

    def test_current_observation_wins_and_already_observed_rows_do_not_repeat(self):
        table = IdentifierTable()
        table.short_handle(H)
        table.observe({"objects": [{"handle": H, "object_revision": NEW}]}, latest=True)
        currency = Currency(table)
        currency.committed[H] = OLD
        self.check({"role": "tool_result", "live": [
            {"handle": H, "object_revision": NEW}, {"handle": H, "object_revision": OLD}]}, currency)

    def test_unchanged_live_rows_and_duplicate_changed_handles_match(self):
        currency = Currency()
        currency.committed[H] = NEW
        self.check({"role": "tool_result", "live": [
            {"handle": H, "object_revision": NEW}, {"handle": H, "object_revision": OLD},
            {"handle": H, "object_revision": OLD}]}, currency)

    def test_empty_receipt_and_absent_revision_still_fit_final_headroom(self):
        self.check({"role": "tool_result"}, Currency())
        self.check({"role": "tool_result", "live": [{"handle": H}]}, Currency())

    def test_raw_saved_claim_cannot_supply_owned_neutral_preimage(self):
        with self.assertRaises(ValueError):
            plan_receipt({"shape": "ready", "live": []}, budget=HandoffBudget(1000))

    def test_custom_currency_table_never_runs_a_callback(self):
        class Custom:
            def observed(self, *args, **kwargs):
                raise AssertionError("custom callback")
        budget = HandoffBudget(1000)
        plan = plan_receipt(neutral_graph({"live": []}, budget=budget), budget=budget)
        self.assertEqual(project_receipt(plan, Currency(Custom()), budget=budget).reason, UNSUPPORTED)

    def test_incomplete_unknown_currency_refuses_the_entire_receipt(self):
        budget = HandoffBudget(10000)
        plan = plan_receipt(neutral_graph({"live": [
            {"handle": "other"}, {"handle": H, "object_revision": OLD}]}, budget=budget), budget=budget)
        projected = project_receipt(plan, Currency(), budget=budget, history_complete=False)
        self.assertEqual(projected.reason, INCOMPLETE_CURRENCY)
        self.assertIsNone(projected.value)

    def test_incomplete_history_can_use_known_current_and_missing_revisions(self):
        budget = HandoffBudget(10000)
        root = {"live": [{"handle": H, "object_revision": OLD}, {"handle": "other"}]}
        plan = plan_receipt(neutral_graph(root, budget=budget), budget=budget)
        currency = Currency()
        currency.committed[H] = NEW
        projected = project_receipt(plan, currency, budget=budget, history_complete=False)
        self.assertIsNone(projected.reason)
        self.assertEqual(projected.value, reference(root, currency))


if __name__ == "__main__":
    unittest.main()
