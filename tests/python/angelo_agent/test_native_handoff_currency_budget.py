# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded newest-first currency preserves the eager owner's latest revisions."""
import suite_isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.context_ledger import own_commits
from angelo_agent.handoff_currency import Currency
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED, WORK_LIMIT


def commit(*pairs):
    return {"role": "activity", "status": "completed", "result": {"status": "committed",
            "live_revisions": {"status": "COMMITTED", "objects": [
                {"handle": handle, "object_revision": revision} for handle, revision in pairs]}}}


class Collision:
    def __init__(self, target):
        self.target = target
    def __hash__(self):
        return hash(self.target)
    def __eq__(self, other):
        raise AssertionError("untrusted key equality ran")


class CurrencyBudget(unittest.TestCase):
    def test_newest_first_matches_eager_including_duplicate_rows(self):
        messages = [commit(("a", "r1"), ("b", "r2")), {"role": "user", "text": "Next"},
                    commit(("a", "r3"), ("a", "r4")), commit(("c", "r5"))]
        currency, budget = Currency(), HandoffBudget(1000)
        for message in reversed(messages):
            self.assertIsNone(currency.observe_latest(message, budget))
        self.assertEqual(currency.committed, own_commits(messages))

    def test_ineligible_results_keep_existing_semantics(self):
        variants = [{"role": "system"}, {"role": "activity", "status": "failed"},
                    {"role": "activity", "status": "completed", "result": {"status": "preview"}},
                    {"role": "activity", "status": "completed", "result": {"status": "committed",
                     "live_revisions": {"status": "UNKNOWN"}}}]
        currency, budget = Currency(), HandoffBudget(1000)
        for message in variants:
            self.assertIsNone(currency.observe_latest(message, budget))
        self.assertEqual(currency.committed, own_commits(variants))

    def test_exhaustion_never_publishes_a_partial_message(self):
        currency, budget = Currency(), HandoffBudget(3)
        currency.committed["existing"] = "current"
        self.assertEqual(currency.observe_latest(commit(("a", "1"), ("b", "2")), budget), WORK_LIMIT)
        self.assertEqual(currency.committed, {"existing": "current"})
        self.assertTrue(budget.exhausted)

    def test_large_string_is_rejected_before_hashing_or_storing(self):
        currency, budget = Currency(), HandoffBudget(10)
        self.assertEqual(currency.observe_latest(commit(("x" * 1000000, "r")), budget), WORK_LIMIT)
        self.assertEqual(currency.committed, {})
        self.assertEqual(budget.chars_left, 0)

    def test_unknown_row_refuses_without_str_repr_or_partial_publication(self):
        class Unknown:
            def __str__(self):
                self.fail()
            def __repr__(self):
                self.fail()
            def fail(self):
                raise AssertionError("custom callback")
        currency, budget = Currency(), HandoffBudget(100)
        message = commit((Unknown(), "r"), ("valid", "r"))
        self.assertEqual(currency.observe_latest(message, budget), UNSUPPORTED)
        self.assertEqual(currency.committed, {})

    def test_dictionary_subclasses_are_not_executed(self):
        class Custom(dict):
            def get(self, *args):
                raise AssertionError("custom get")
        currency = Currency()
        self.assertEqual(currency.observe_latest(Custom(), HandoffBudget(100)), UNSUPPORTED)

    def test_empty_handles_and_revisions_preserve_the_eager_contract(self):
        message = commit(("", ""))
        currency, budget = Currency(), HandoffBudget(1000)
        self.assertIsNone(currency.observe_latest(message, budget))
        self.assertEqual(currency.committed, own_commits([message]))
        self.assertLess(budget.nodes_left, 1000)

    def test_current_observation_still_wins_over_history(self):
        class Table:
            def observed(self, handle, *, shown=False):
                return "observed" if handle == "a" else None
        currency = Currency(Table())
        self.assertIsNone(currency.observe_latest(commit(("a", "old"), ("b", "new")), HandoffBudget(1000)))
        self.assertEqual(currency.current("a"), "observed")
        self.assertEqual(currency.current("b"), "new")

    def test_message_key_collision_is_refused_before_lookup(self):
        self.assertEqual(Currency().observe_latest({Collision("role"): "activity"}, HandoffBudget(1000)), UNSUPPORTED)

    def test_result_key_collision_is_refused_before_lookup(self):
        message = commit()
        message["result"] = {Collision("status"): "committed"}
        self.assertEqual(Currency().observe_latest(message, HandoffBudget(1000)), UNSUPPORTED)

    def test_live_key_collision_is_refused_before_lookup(self):
        message = commit()
        message["result"]["live_revisions"] = {Collision("status"): "COMMITTED"}
        self.assertEqual(Currency().observe_latest(message, HandoffBudget(1000)), UNSUPPORTED)

    def test_row_key_collision_is_refused_before_lookup(self):
        message = commit()
        message["result"]["live_revisions"]["objects"] = [{Collision("handle"): "a", "object_revision": "r"}]
        self.assertEqual(Currency().observe_latest(message, HandoffBudget(1000)), UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
