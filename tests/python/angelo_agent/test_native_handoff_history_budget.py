# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded raw history and exact host-summary adjacency, without a provider."""
import suite_isolation  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_history import History, HostSummaries
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED, WORK_LIMIT
from angelo_agent.routing import _certified_rows


def activity(tool="angelo_scene_program", **extra):
    return {"role": "activity", "tool": tool, **extra}


class HistoryBudget(unittest.TestCase):
    def test_huge_beyond_end_start_is_clamped_before_cursor_subtraction(self):
        messages = [{"role": "user"}]
        history = History(messages, 1 << 1000000, HandoffBudget(10))
        self.assertEqual(history.start, len(messages))
        self.assertEqual(history.unexamined, 0)
        self.assertIsNone(history.take())

    def test_lazy_groups_match_original_adjacency(self):
        messages = [{"role": "user"}, activity(), {"role": "thinking"},
                    activity("angelo_accept_preview"), {"role": "system", "notice": True},
                    {"role": "system", "host_summary": True}, {"role": "assistant"},
                    activity(notice=True), {"role": "system", "host_summary": True}, activity()]
        expected_rows, expected_accepts, expected_groups = _certified_rows(messages)
        history, groups, rows, accepts = History(messages, 0, HandoffBudget(1000)), HostSummaries(), set(), set()
        observed_groups = {}
        while (taken := history.take()) is not None:
            index, message = taken
            result = groups.observe(index, message, history.budget)
            self.assertIsNone(result.reason)
            if result.certified:
                rows.add(index)
            if result.accepted:
                accepts.add(index)
            if result.closed is not None:
                observed_groups[result.closed.summary] = result.closed.rows
        if (last := groups.end(reached_origin=True)) is not None:
            observed_groups[last.summary] = last.rows
        self.assertEqual((rows, accepts, observed_groups), (expected_rows, expected_accepts, expected_groups))

    def test_filtered_raw_records_still_consume_work(self):
        messages = [{"role": "thinking"}] * 100000
        history = History(messages, 0, HandoffBudget(3))
        self.assertEqual([history.take()[0] for _ in range(3)], [99999, 99998, 99997])
        self.assertIsNone(history.take())
        self.assertEqual(history.reason, WORK_LIMIT)
        self.assertEqual(history.unexamined, 99997)

    def test_start_does_not_copy_or_visit_earlier_records(self):
        messages = [{"role": "user"}] * 10
        history = History(messages, 9, HandoffBudget(10))
        self.assertIs(history.messages, messages)
        self.assertEqual(history.take()[0], 9)
        self.assertIsNone(history.take())
        self.assertEqual(history.budget.nodes_left, 9)
        self.assertEqual(history.unexamined, 0)

    def test_start_inside_group_never_claims_group_complete(self):
        groups, budget = HostSummaries(), HandoffBudget(100)
        groups.observe(2, {"role": "system", "host_summary": True}, budget)
        self.assertTrue(groups.observe(1, activity(), budget).certified)
        self.assertIsNone(groups.end(reached_origin=False))

    def test_work_cutoff_keeps_group_incomplete(self):
        groups, budget = HostSummaries(), HandoffBudget(100)
        groups.observe(2, {"role": "system", "host_summary": True}, budget)
        while budget.visit():
            pass
        self.assertEqual(groups.observe(1, activity(), budget).reason, WORK_LIMIT)
        self.assertIsNone(groups.end(reached_origin=True))

    def test_exact_dict_custom_key_cannot_run_equality_during_role_lookup(self):
        class Collision:
            def __hash__(self):
                return hash("role")
            def __eq__(self, other):
                raise AssertionError("custom equality")
        result = HostSummaries().observe(0, {Collision(): "activity"}, HandoffBudget(100))
        self.assertEqual(result.reason, UNSUPPORTED)

    def test_notice_summary_does_not_certify_activity(self):
        groups, budget = HostSummaries(), HandoffBudget(100)
        groups.observe(1, {"role": "system", "notice": True, "host_summary": True}, budget)
        self.assertFalse(groups.observe(0, activity(), budget).certified)

    def test_withdrawn_boundary_still_ends_certification(self):
        groups, budget = HostSummaries(), HandoffBudget(100)
        groups.observe(2, {"role": "system", "host_summary": True}, budget)
        groups.observe(1, {"role": "user", "withdrawn": True}, budget)
        self.assertFalse(groups.observe(0, activity(), budget).certified)

    def test_unknown_record_does_not_call_custom_get(self):
        class Custom(dict):
            def get(self, *args):
                raise AssertionError("custom get")
        history = History([Custom()], 0, HandoffBudget(10))
        self.assertEqual(history.take(), (0, None))
        self.assertEqual(history.reason, UNSUPPORTED)
        self.assertIsNone(history.take())

    def test_large_tool_name_refuses_before_conversion_and_group_publication(self):
        groups, budget = HostSummaries(), HandoffBudget(100)
        groups.observe(1, {"role": "system", "host_summary": True}, budget)
        result = groups.observe(0, activity("x" * 1000000), budget)
        self.assertEqual(result.reason, WORK_LIMIT)
        self.assertFalse(result.certified)
        self.assertIsNone(groups.end(reached_origin=True))


if __name__ == "__main__":
    unittest.main()
