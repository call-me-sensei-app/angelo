# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual routing facade regressions prepared before activating the lazy owner.

The existing routing implementation is expected to fail these behavioral
assertions. No helper API is missing on that baseline; this is not runtime proof.
"""
import suite_isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.routing import HANDOFF_HEADER, handoff
from test_native_handoff_receipts_budget import committed


def records(text):
    return [json.loads(line) for line in text.split(HANDOFF_HEADER, 1)[1].splitlines()]


class RoutingBudget(unittest.TestCase):
    def test_final_character_limit_includes_the_existing_header(self):
        message = {"role": "user", "text": "x" * 500}
        limit = len(json.dumps(message, ensure_ascii=False))
        text, _ids = handoff([message], limit=limit)
        self.assertLessEqual(len(text), limit)
        self.assertTrue(text)
        self.assertTrue(records(text))

    def test_unexamined_filtered_tail_is_reported_as_raw_records(self):
        text, ids = handoff([{"role": "thinking"}] * 100000, limit=512)
        self.assertTrue(text, "A bounded inspection must report its unexamined raw tail")
        report = next(row for row in records(text) if row.get("role") == "handoff_omission")
        self.assertGreater(report["unexamined_transcript_records"], 0)
        self.assertEqual(ids, frozenset())
        self.assertLessEqual(len(text), 512)

    def test_middle_oversized_receipt_does_not_hide_an_earlier_small_entry(self):
        value = committed(live_revisions={"status": "COMMITTED", "objects": [
            {"handle": "object", "object_revision": "revision", "name": "\x01" * 350}]})
        messages = [{"role": "user", "text": "older-small"},
            {"role": "activity", "status": "completed", "tool": "angelo_scene_program", "result": value,
             "call_id": "middle-call"},
            {"role": "system", "host_summary": True, "text": "Host summary"},
            {"role": "user", "text": "\x02" * 500}]
        text, ids = handoff(messages, limit=5000)
        found = records(text)
        self.assertIn("older-small", [row.get("text") for row in found])
        self.assertNotIn("middle-call", ids)
        self.assertLessEqual(len(text), 5000)


if __name__ == "__main__":
    unittest.main()
