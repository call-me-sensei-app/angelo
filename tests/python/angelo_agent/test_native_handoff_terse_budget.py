# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Budgeted terse history preserves existing receipt wording and currency."""
import suite_isolation  # noqa: F401
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.claude_outcomes import terse_receipt as previous_terse
from angelo_agent.handoff_currency import Currency
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED, WORK_LIMIT
from angelo_agent.handoff_receipts import terse_receipt
from angelo_agent.identifiers import IdentifierTable
from test_native_handoff_receipts_budget import committed, passed


class TerseBudget(unittest.TestCase):
    def test_standard_program_preview_recipe_and_accept_match_existing(self):
        values = [committed(), committed(status="preview", preview_id="preview", delta={"added": [
            {"name": "Panel", "type": "MESH"}]}), committed(changes={"modified": [{"name": "Light"}]}),
            committed(world={"color": [0.2, 0.3, 0.4]}, motion={"frames": [1, 4]})]
        for tool in ("angelo_scene_program", "angelo_apply_recipe", "angelo_accept_preview"):
            for value in values:
                projected = terse_receipt(tool, value, budget=HandoffBudget(100000))
                self.assertIsNone(projected.reason)
                self.assertEqual(projected.value, previous_terse(tool, value))

    def test_check_target_labels_and_value_suppression_match_existing(self):
        value = committed(live_revisions={"status": "COMMITTED", "objects": [
            {"handle": "a", "name": "Panel"}, {"handle": "b", "name": "Light"}]}, checks=[
                passed(checks=[passed(kind="dimensions", target="a", observed={"dimensions": [1, 2, 3],
                       "scope": "OBJECT", "space": "WORLD", "passed": True, "note": "Edge\nvalue"})]),
                passed(checks=[passed(kind="object_name", target="b", observed={"name": "Key light"})])])
        projected = terse_receipt("angelo_scene_program", value, budget=HandoffBudget(100000))
        self.assertIsNone(projected.reason)
        self.assertEqual(projected.value, previous_terse("angelo_scene_program", value))

    def test_extra_passing_checks_and_changed_row_counts_match_existing(self):
        value = committed(checks=[passed(checks=[passed(kind="checked", observed={"value": i}) for i in range(40)])],
                          changes={"added": [{"name": "Panel %d" % i, "type": "MESH"} for i in range(12)]})
        projected = terse_receipt("angelo_scene_program", value, budget=HandoffBudget(100000))
        self.assertIsNone(projected.reason)
        self.assertEqual(projected.value, previous_terse("angelo_scene_program", value))

    def test_current_revision_filter_preserves_changed_since_and_omitted_handles(self):
        value = committed(live_revisions={"status": "COMMITTED", "objects": [
            {"handle": "a", "name": "Panel", "object_revision": "old"},
            {"handle": "b", "name": "Light", "revision_omitted": {"reason": "BUDGET"}}]})
        currency = Currency()
        currency.committed["a"] = "new"
        projected = terse_receipt("angelo_scene_program", value, budget=HandoffBudget(100000), currency=currency)
        self.assertIsNone(projected.reason)
        self.assertEqual(projected.value, previous_terse("angelo_scene_program", value, currency=currency))

    def test_identifier_numbering_matches_post_receipt_compaction(self):
        handle, revision = "scn:" + "a" * 32 + ":1", "b" * 64
        value = committed(live_revisions={"status": "COMMITTED", "objects": [
            {"handle": handle, "name": "Panel", "object_revision": revision}]})
        old, new = IdentifierTable(), IdentifierTable()
        expected = old.compact(previous_terse("angelo_scene_program", value))
        projected = terse_receipt("angelo_scene_program", value, budget=HandoffBudget(100000), identifiers=new)
        self.assertIsNone(projected.reason)
        self.assertEqual(projected.value, expected)
        self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_partial_evidence_never_gets_a_verified_line(self):
        projected = terse_receipt("angelo_scene_program", committed(verification={"status": "partial"}),
                                 budget=HandoffBudget(10000))
        self.assertIsNone(projected.value)

    def test_large_name_refuses_whole_candidate_without_modifying_evidence(self):
        value = committed(changes={"added": [{"name": "x" * 1000000, "type": "MESH"}]})
        before = copy.deepcopy(value)
        projected = terse_receipt("angelo_scene_program", value, budget=HandoffBudget(1000))
        self.assertEqual(projected.reason, WORK_LIMIT)
        self.assertIsNone(projected.value)
        self.assertEqual(value, before)

    def test_custom_observed_key_cannot_run_equality_or_formatting(self):
        class Collision:
            def __hash__(self):
                return hash("scope")
            def __eq__(self, other):
                raise AssertionError("custom equality")
            def __str__(self):
                raise AssertionError("custom formatting")
        value = committed(checks=[passed(checks=[passed(kind="checked", observed={Collision(): "value"})])])
        projected = terse_receipt("angelo_scene_program", value, budget=HandoffBudget(10000))
        self.assertEqual(projected.reason, UNSUPPORTED)
        self.assertIsNone(projected.value)


if __name__ == "__main__":
    unittest.main()
