# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A finished task's changes and cost in ``angelo_status``'s result (``external_result``), without Blender.

Contract: doc/angelo/external_mcp.rst, "Status".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import external_result as outcome  # noqa: E402
from angelo_agent import external_status  # noqa: E402

ROWS = [{"role": "user", "request_kind": "external_task", "text": "one"},
        {"role": "activity", "tool": "angelo_create", "status": "completed", "result": {"ok": True}},
        {"role": "system", "text": "Verified: added Cube (2 m) at the origin.", "host_summary": True},
        {"role": "system", "text": "A snapshot was saved."},
        {"role": "assistant", "text": "Made the cube."},
        {"role": "user", "request_kind": "external_task", "text": "two"},
        {"role": "system", "text": "Verified: moved Cube up 1 m.", "host_summary": True},
        {"role": "assistant", "text": "Moved it."}]


class ChangesTests(unittest.TestCase):
    def test_the_verified_account_is_the_tasks_own_and_never_the_next_ones(self):
        self.assertEqual(outcome.changes(ROWS, 0), {"changes": ["Verified: added Cube (2 m) at the origin."],
                                                    "verified": True})
        self.assertEqual(outcome.changes(ROWS, 5)["changes"], ["Verified: moved Cube up 1 m."])

    def test_a_task_without_a_certified_account_says_where_to_read_instead(self):
        value = outcome.changes([{"role": "user", "request_kind": "external_task"},
                                 {"role": "assistant", "text": "Looked."}], 0)
        self.assertEqual((value["changes"], value["verified"], value["changes_note"]), ([], False, outcome.UNVERIFIED))

    def test_a_long_account_is_cut_and_says_so(self):
        rows = [{"role": "user", "request_kind": "external_task"}] + [
            {"role": "system", "text": "x" * 3000, "host_summary": True}] * 3
        value = outcome.changes(rows, 0)
        self.assertEqual(sum(map(len, value["changes"])), outcome.MAX_CHANGES_TEXT)
        self.assertTrue(value["changes_truncated"])


class CostTests(unittest.TestCase):
    def test_a_task_costs_up_to_the_next_tasks_send(self):
        zero = {"input": 0, "output": 0, "requests": 0, "usd": 0.0, "models": [], "live": False}
        tasks = {"c:0": zero, "c:5": dict(zero, input=100, usd=0.01)}
        now = dict(zero, input=250, usd=0.03)
        first = outcome.completed(external_status.result(ROWS, 0), ROWS, 0, tasks, "c:0", now)
        self.assertEqual((first["cost"]["input"], first["cost"]["usd"]), (100, 0.01))
        self.assertEqual(first["summary"], "Made the cube.")
        self.assertTrue(first["verified"])
        last = outcome.completed(external_status.result(ROWS, 5), ROWS, 5, tasks, "c:5", now)
        self.assertEqual((last["cost"]["input"], last["cost"]["usd"]), (150, 0.02))
        self.assertIsNone(outcome.cost(tasks, "c:9", now))


if __name__ == "__main__":
    unittest.main()
