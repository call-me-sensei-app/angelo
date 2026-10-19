# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Review & Accept card's words (review_text): plain, complete and exact, and display only."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import review_text  # noqa: E402
from angelo_agent.agent_workspace_ui import preview_lines  # noqa: E402


def descriptor(**changes):
    return {"state": "PREVIEW", "preview_id": "p", "stage_id": "s", "preview_token": "t",
            "summary": "1 object(s) added; 0 object(s) modified; 0 object(s) deleted. No live edits until Accept/commit.",
            "arguments": {"mode": "PREVIEW", "operations": [{"kind": "create_primitive", "target": None,
                          "parameters": {"name": "Cube", "size": 2.000000000001}}]},
            "changes": {"added": [{"name": "Workspace UI staged cube", "type": "MESH", "vertices": 8}],
                        "removed": [], "modified": [{"handle": "scn:abc", "name": "Lamp", "fields": ["light"],
                                                     "properties": ["data.energy"]}],
                        "object_count_before": 3, "object_count_after": 4, "truncated": False,
                        "scope": "Exact bound staged-object delta; unchanged live objects are not rewritten",
                        **changes},
            "checks": [{"kind": "object_name", "status": "pass"}, {"kind": "dimensions", "status": "fail"},
                       {"name": "Transform", "passed": None}]}


class ReviewTextTests(unittest.TestCase):
    def test_reads_for_an_artist_without_the_engineering_text(self):
        rows = review_text.sections(descriptor())
        text = "\n".join(value for _, value in rows)
        for fact in ("Added · 1", "Workspace UI staged cube · Mesh · 8 vertices", "Changed · 1",
                     "Lamp · light · settings data.energy", "Objects in the scene: 3 → 4", "Checks · 3",
                     "Object name · Passed", "Dimensions · Failed", "Transform · Not verified"):
            self.assertIn(fact, text)
        for jargon in ("Summary:", "object(s)", "Staged changes", "Item 1:", "Type: MESH", "Truncated", "Scope",
                       "Accept/commit", "scn:"):
            self.assertNotIn(jargon, text)
        self.assertEqual(("heading", "Added · 1"), rows[0])

    def test_caveats_appear_only_when_they_apply(self):
        self.assertNotIn("not listed", "\n".join(v for _, v in review_text.sections(descriptor())))
        rows = review_text.sections(descriptor(truncated=True, uncertain=True))
        text = "\n".join(value for _, value in rows)
        self.assertIn("Some changes are not listed here", text)
        self.assertIn("could not be read back", text)

    def test_exact_settings_keep_every_value_and_the_operator_lines_are_unchanged(self):
        exact = "\n".join(review_text.exact_settings(descriptor()))
        self.assertIn("2.000000000001", exact)
        self.assertIn("create_primitive", exact)
        # The operator's own review lines (stored on the operator, checked by the suites) are the exact text.
        self.assertIn("Summary:", "\n".join(preview_lines(descriptor(), exact=True)))

    def test_an_empty_result_says_so(self):
        rows = review_text.sections({"changes": {}, "checks": []})
        self.assertEqual([value for _, value in rows],
                         ["Nothing is added, changed or removed.", "No checks were run on this preview."])


if __name__ == "__main__":
    unittest.main()
