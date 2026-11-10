# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The proposed lazy composer must retain real routing's chronological owners.

These protect against the earlier inactive newest-first draft regression.
They are not an old-routing failure claim: the production owner is correct here.
"""
import suite_isolation  # noqa: F401
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_compose import compose
from angelo_agent.identifiers import IdentifierTable
from angelo_agent.routing import HANDOFF_HEADER, handoff
from test_native_handoff_receipts_budget import committed


H1, H2 = "scn:" + "a" * 32 + ":1", "scn:" + "b" * 32 + ":2"
R1, R2 = "c" * 64, "d" * 64


def activity(tool, result, call_id):
    return {"role": "activity", "status": "completed", "tool": tool, "result": result, "call_id": call_id}


def entries(text):
    return [json.loads(line) for line in text.split(HANDOFF_HEADER, 1)[1].splitlines()]


class ChronologicalOwners(unittest.TestCase):
    def parity(self, messages):
        old, new = IdentifierTable("native"), IdentifierTable("native")
        expected = handoff(messages, identifiers=old)
        proposed = compose(messages, 0, 24000, header=HANDOFF_HEADER, identifiers=new)
        self.assertEqual(proposed, expected)
        self.assertEqual(new.to_dict("native"), old.to_dict("native"))

    def test_oldest_first_numbering_is_preserved_for_unseen_historical_handles(self):
        messages = []
        for index, (handle, revision) in enumerate(((H1, R1), (H2, R2))):
            messages.extend([{"role": "user", "text": "Make the next object"},
                activity("angelo_scene_program", committed(live_revisions={"status": "COMMITTED", "objects": [
                    {"handle": handle, "name": "Object %d" % index, "object_revision": revision}]}), str(index)),
                {"role": "system", "host_summary": True, "text": "Host summary"}])
        reference = entries(handoff(messages, identifiers=IdentifierTable())[0])
        results = [row for row in reference if row.get("role") == "tool_result"]
        self.assertEqual([row["live"][0]["handle"] for row in results], ["o1", "o2"])
        self.parity(messages)

    def test_later_read_uses_the_same_final_latest_commit_currency_map(self):
        messages = [activity("angelo_execute_python", {"status": "committed", "live_revisions": {
            "status": "COMMITTED", "objects": [{"handle": H1, "object_revision": R1}]}}, "commit"),
            activity("angelo_scene_object", {"after": {"objects": [
                {"handle": H1, "object_revision": R2, "state": "read-back"}]}}, "read")]
        reference = entries(handoff(messages, identifiers=IdentifierTable())[0])
        read = next(row for row in reference if row.get("tool") == "angelo_scene_object")
        self.assertIn("changed_since", json.loads(read["result"]))
        self.parity(messages)


if __name__ == "__main__":
    unittest.main()
