# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which tool results the chat counts as scene changes (summary and popup)."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.chat_changes import change_counts, scene_change_report

# Shapes produced today: blender_tools.scene_diff, program_commit and sculpt.change_report.
SCENE_DIFF = {"added": [{"name": "Cube", "type": "MESH", "vertices": 8}], "removed": [{"name": "Old", "type": "MESH"}],
              "modified": [{"name": "Target", "fields": ["matrix"]}], "truncated": False, "scope": "read-back"}
SCULPT = {"added": [], "removed": [], "modified": [{"name": "Head", "fields": ["geometry"]}], "truncated": False}
# angelo_scene_delta: "changes" is a list of observation rows, not a mutation report.
DELTA_ROWS = [{"change": "MODIFIED", "object": {"object": "scn:1:7", "name": "Target"}, "fields": ["transform"]}]


def tool(result, reverted=False):
    return {"role": "tool", "result": result, "reverted": reverted}


class ChatChangeTests(unittest.TestCase):
    def test_scene_delta_rows_are_observations_not_changes(self):
        self.assertIsNone(scene_change_report({"changes": DELTA_ROWS}))
        # The chat summary previously indexed this list as a dict and failed on every redraw.
        self.assertEqual(change_counts([tool({"changes": DELTA_ROWS})]), (0, 0, 0))

    def test_mutation_reports_are_counted_once_per_object(self):
        messages = [tool({"changes": SCENE_DIFF}), tool({"changes": SCULPT}), tool({"changes": DELTA_ROWS}),
                    tool({"changes": SCENE_DIFF}, reverted=True)]
        self.assertEqual(change_counts(messages), (4, 1, 1))  # Cube, Old, Target, Head.

    def test_missing_or_malformed_changes_are_ignored(self):
        for result in (None, {}, {"changes": None}, {"changes": {}}, {"changes": {"added": [], "removed": []}},
                       {"changes": {"added": "x", "removed": [], "modified": []}}, "text result"):
            with self.subTest(result=result):
                self.assertIsNone(scene_change_report(result))
        self.assertEqual(change_counts([{"role": "assistant", "text": "hello"}, tool(None)]), (0, 0, 0))

    def test_chat_summary_uses_the_shared_definition(self):
        # chat_view draws with GPU modules; the popup's behaviour is exercised in
        # test_native_dispatch_fixes, which runs its actual draw code.
        source = (ROOT / "scripts/addons_core/angelo_agent/chat_view.py").read_text()
        self.assertIn("from .chat_changes import change_counts", source)
        self.assertNotIn("def change_counts", source)


if __name__ == "__main__":
    unittest.main()
