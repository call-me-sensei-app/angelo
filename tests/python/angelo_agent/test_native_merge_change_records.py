# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The merge reports what it added as every tool does, and the model's copy stays as small as before.

Evidence: angelo_workers_merge listed bare names, and the chat's change summary and Changes popup (which read
{"name", "type"} records) raised on every draw of a lead whose transcript held a merge (pa-p1's repro, 2026-10-25).
Pinned without Blender: the producer's own line builds records, the chat's reader counts them, and the model's copy
names them (no larger than the names it read before: the latency budget). test_blender_parallel_merge checks the same
on a real merge.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import chat_changes  # noqa: E402
from angelo_agent.model_results import MERGE_TOOL, model_result  # noqa: E402

RESULT = {"success": True, "scene_changed": True, "merged": [{"worker": 1, "objects": 2, "placements": 1}],
          "changes": {"added": [{"name": "Seat", "type": "MESH"}, {"name": "Lamp", "type": "LIGHT"}],
                      "removed": [], "modified": [], "truncated": False, "scope": "Parts merged"}}


class MergeChangeRecordTests(unittest.TestCase):
    def test_the_merge_builds_name_and_type_records(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/worker_merge.py").read_text()
        extends = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == "extend"
                   and isinstance(node.func.value, ast.Name) and node.func.value.id == "added"]
        self.assertTrue(extends)
        for call in extends:   # Every row added to the report is a {"name", "type"} dict.
            row = call.args[0].elt if isinstance(call.args[0], ast.GeneratorExp) else call.args[0]
            self.assertIsInstance(row, ast.Dict)
            self.assertEqual({key.value for key in row.keys}, {"name", "type"})

    def test_the_chat_counts_them_and_the_model_reads_names(self):
        self.assertEqual(chat_changes.change_counts([{"result": RESULT}]), (2, 2, 0))
        copy = model_result(MERGE_TOOL, RESULT)
        self.assertEqual(copy["changes"]["added"], ["Seat", "Lamp"])
        self.assertEqual(RESULT["changes"]["added"][0], {"name": "Seat", "type": "MESH"})   # The result unchanged.


if __name__ == "__main__":
    unittest.main()
