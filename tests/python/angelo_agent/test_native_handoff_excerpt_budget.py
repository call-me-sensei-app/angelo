# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded full-result text keeps the original partial/fact-aware byte layout."""
import suite_isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_currency import Currency
from angelo_agent.handoff_excerpt import history_result_text
from angelo_agent.handoff_json import HandoffBudget, OUTPUT_LIMIT, UNSUPPORTED, WORK_LIMIT, encode_json
from angelo_agent.identifiers import IdentifierTable
from angelo_agent.program_completion import history_result_text as previous


class ExcerptBudget(unittest.TestCase):
    def parity(self, value, *, short=False, currency=None):
        old, new = (IdentifierTable(), IdentifierTable()) if short else (None, None)
        expected = previous(value, old, currency)
        result = history_result_text(value, budget=HandoffBudget(1000000), identifiers=new, currency=currency)
        self.assertIsNone(result.reason)
        self.assertEqual(result.value, expected)
        if short:
            self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_small_results_and_primitive_values_match(self):
        for value in ({"status": "failed", "error": "No object"}, {"items": [1, True, None]}, "text", None, 5):
            self.parity(value)

    def test_partial_verification_order_and_first_unknown_match(self):
        self.parity({"before": True, "verification": {"unknowns": [{"extra": 1, "reason": "unknown",
            "root": "object", "operation": 2, "affected": ["object"]}], "status": "partial"}, "after": []})

    def test_facts_after_long_readback_keep_original_tail(self):
        for short in (False, True):
            self.parity({"readback": "x" * 6000, "world": {"color": [0.2, 0.3, 0.4]},
                         "motion": {"frames": [1, 4]}, "operation_results": [
                             {"also_slots_only": {"note": "Only slots", "objects": ["Panel"]}}]}, short=short)

    def test_oversized_facts_are_named_with_same_marker(self):
        self.parity({"readback": "x" * 6000, "world": {"long": "w" * 4000},
                     "unobservable_checks": ["No measurement"]})

    def test_fact_before_cut_and_removed_worker_token_match(self):
        self.parity({"world": {"energy": 2}, "readback": "x" * 6000, "_worker_finishes": "private"})

    def test_large_partial_verification_adds_existing_excerpt_marker(self):
        self.parity({"status": "committed", "verification": {"status": "partial", "unknowns": [
            {"operation": i, "reason": "Not measured " + "x" * 80} for i in range(60)]},
            "unverified_effects": ["effect"]})

    def test_currency_removes_stale_rows_without_quadratic_identity_search(self):
        currency = Currency()
        currency.committed["a"] = "new"
        stale = {"handle": "a", "object_revision": "old"}
        self.parity({"live_revisions": {"objects": [stale, stale, {"handle": "b", "object_revision": "kept"}]},
                     "after": {"objects": [stale]}}, currency=currency)

    def test_member_offsets_match_actual_json_field_boundaries(self):
        value = {"one": [1, {"nested": "café"}], "two": {"key": "日本語"}}
        result = encode_json(value, budget=HandoffBudget(1000), track_members=True)
        self.assertIsNone(result.reason)
        for key, end in result.member_ends.items():
            prefix = result.text[:end]
            parsed = json.loads(prefix + "}")
            self.assertEqual(parsed[key], value[key])

    def test_work_or_output_refusal_never_returns_a_partial_candidate(self):
        for value in ({"readback": "x" * 1000000}, {"number": 1 << 1000000}):
            result = history_result_text(value, budget=HandoffBudget(100))
            self.assertIsNone(result.value)
            self.assertIn(result.reason, (WORK_LIMIT, OUTPUT_LIMIT))

    def test_custom_colliding_key_is_refused_before_history_lookup(self):
        class Collision:
            def __hash__(self):
                return hash("verification")
            def __eq__(self, other):
                raise AssertionError("custom equality")
        result = history_result_text({Collision(): {}}, budget=HandoffBudget(1000))
        self.assertEqual(result.reason, UNSUPPORTED)
        self.assertIsNone(result.value)


if __name__ == "__main__":
    unittest.main()
