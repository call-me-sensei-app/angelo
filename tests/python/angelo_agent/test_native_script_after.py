# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A completed script's read-back: what it changed by handle and revision, bounded, and observed like a commit.

Host fakes only. The real revisions and the program that follows a script are in test_blender_world.py.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_ledger, scene_settings, script_after, turn_context  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402
from test_native_scene_queries import queries  # noqa: E402
from test_native_turn_context import Clock, TurnContextFixture  # noqa: E402
from angelo_agent.revision_budget import READ_BACK_SECONDS  # noqa: E402

SETTINGS = "7" * 64


def result_for(added=(), modified=(), **fields):
    return dict({"success": True, "result": None, "changes": {
        "added": [{"name": name, "type": "MESH", "vertices": 8} for name in added],
        "modified": [{"name": name, "fields": ["matrix"]} for name in modified], "removed": []}}, **fields)


class AttachTests(TurnContextFixture):
    def attach(self, result, clock=None):
        with patch.object(scene_settings, "settings_revision", return_value=SETTINGS):
            return script_after.attach(self.window, result, clock=clock or Clock(0.))

    def test_added_and_modified_objects_come_back_with_handle_and_revision(self):
        after = self.attach(result_for(added=["Prop 03"], modified=["Prop 07", "Prop 03"]))["after"]
        self.assertEqual([row["name"] for row in after["objects"]], ["Prop 03", "Prop 07"], "Each object once")
        for row, obj in zip(after["objects"], (self.objects[3], self.objects[7])):
            self.assertEqual(row, {"name": obj.name, "type": "MESH", "handle": queries.object_handle(obj),
                                   "object_revision": queries.object_revision(self.window, obj)})
        self.assertEqual(after["scene_settings"]["revision"], SETTINGS)
        self.assertNotIn("omitted", after)

    def test_rows_are_bounded_and_the_rest_is_counted(self):
        names = [obj.name for obj in self.objects[:script_after.MAX_OBJECTS + 5]]
        after = self.attach(result_for(modified=names))["after"]
        self.assertEqual(len(after["objects"]), script_after.MAX_OBJECTS)
        self.assertEqual(after["omitted"], 5)

    def test_the_read_back_outlasts_the_turn_contexts_budget_and_keeps_the_scene_revision(self):
        """Regression: two ~90,000-vertex meshes spent the 25 ms turn-context budget, so the ground's and the
        scene settings' revisions were left out and the next program (a scene setting) was refused."""
        with patch("angelo_agent.revision_budget.SMALL_REVISION_VERTICES", 0):  # Every fake mesh counts as large.
            after = self.attach(result_for(modified=["Prop 03", "Prop 04", "Prop 05"]),
                                clock=Clock(turn_context.REVISION_BUDGET_SECONDS))["after"]
        self.assertTrue(all("object_revision" in row for row in after["objects"]), after["objects"])
        self.assertEqual(after["scene_settings"]["revision"], SETTINGS)
        table = IdentifierTable()
        table.compact({"after": after})  # As delivered: numbered first, then observed.
        context_ledger.observe_committed(table, {"after": after})
        expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [
            {"kind": "property_set", "parameters": {"values": {"render.resolution_x": 800}}}]}, table)

    def test_an_object_past_the_read_back_budget_states_why_and_the_lookup_that_returns_it(self):
        with patch("angelo_agent.revision_budget.SMALL_REVISION_VERTICES", 0):
            after = self.attach(result_for(modified=["Prop 03"]), clock=Clock(READ_BACK_SECONDS))["after"]
        row = after["objects"][0]
        self.assertNotIn("object_revision", row)
        self.assertEqual(row["revision_omitted"], {"reason": "BUDGET", "detail": "cooperative_read_back_budget"})
        self.assertEqual(after["scene_settings"]["revision"], SETTINGS, "Small: never left out")
        table = IdentifierTable()
        short = table.compact(row["handle"])
        context_ledger.observe_committed(table, {"after": after})
        with self.assertRaisesRegex(ValueError, r"%s has no revision in your latest observation \(the host's read "
                                    r"time.*angelo_scene_object \{\"object\":\"%s\"\} returns" % (short, short)):
            expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [
                {"kind": "transform", "target": short, "parameters": {"location": [0, 0, 1]}}]}, table)

    def test_a_failed_script_or_a_result_without_the_read_back_gets_nothing(self):
        for result in (result_for(added=["Prop 03"], success=False, error="boom"),
                       result_for(added=["Prop 03"], error="rolled back"), {"success": True}, {"success": True, "changes": []}):
            self.assertNotIn("after", self.attach(dict(result)))

    def test_an_object_that_is_gone_again_is_skipped(self):
        after = self.attach(result_for(added=["Never Existed", "Prop 03"]))["after"]
        self.assertEqual([row["name"] for row in after["objects"]], ["Prop 03"])

    def test_the_read_back_is_observed_like_a_commit(self):
        table = IdentifierTable()
        result = self.attach(result_for(added=["Prop 03"]))
        shown = table.compact(result)
        context_ledger.observe_committed(table, result)
        row = result["after"]["objects"][0]
        self.assertEqual(table.observed(row["handle"]), row["object_revision"])
        self.assertEqual(table.observed(result["after"]["scene_settings"]["handle"]), SETTINGS)
        self.assertRegex(shown["after"]["objects"][0]["handle"], r"^o\d+$")
        # The scene's own edits bind the script's result, without a target and without a lookup.
        resolved = expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [
            {"kind": "property_set", "parameters": {"values": {"frame_end": 90}}}]}, table)
        self.assertEqual(resolved["write_set"], [{"handle": result["after"]["scene_settings"]["handle"], "revision": SETTINGS}])


if __name__ == "__main__":
    unittest.main()
