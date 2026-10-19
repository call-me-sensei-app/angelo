# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a model reads on a heavy scene, without Blender: the heavy-scene payload trims.

* A and B: a lookup's cache and budget diagnostics and a script's bookkeeping
  stay out of the model's copy of a result (``model_results``), never out of
  the raw result the chat, journal and receipts keep;
* D: a scene the observation cannot list whole gets at most eight scene-order
  rows (``turn_context.MAX_FILL_OBJECTS``);
* the shorter continuation line (``plan_recovery.CONTINUED``).

doc/angelo/performance.rst, "Heavy-scene payloads", has the measurements.
"""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_turn_context import Clock, TurnContextFixture, Update, make_window, object_id
from test_native_scene_queries import FakeObject
from angelo_agent import turn_context
from angelo_agent.model_results import model_result, plan_results
from angelo_agent.plan_recovery import CONTINUED

# A scene search and a completed script as the heavy-castle run recorded them (values shortened).
SEARCH = {"scene": "Scene", "object_count": 1969, "matching_count_exact": True, "truncated": False,
          "observation_token": "obs:e:t", "next_cursor": None, "page": {"offset": 0, "limit": 10, "returned": 1, "total": 1},
          "objects": [{"object": "scn:%s:5" % ("a" * 32), "name": "Watchtower", "object_revision": "9e27222d"}],
          "document_epoch": "a" * 32, "read_only": True,
          "cache": {"exact_checks": 2, "fingerprint_bytes": 32741, "totals": {"exact_checks": 39}},
          "budgets": {"object_scan": 20000, "payload_bytes": 128000}, "query_ms": 4.884,
          "native_exec_ms": 4.933, "native_dispatch_wait_ms": 6.014}
SCRIPT = {"result": {"moved": 298}, "success": True, "output": "", "undoable": False, "main_data_undoable": False,
          "undo_scope": "MAIN_DATA", "image_pixels_undoable": False, "undo_available": True,
          "undo": "Script Undo history is unverified.", "verified": {"scene": "Scene", "object_count": 1969},
          "external_effects_undoable": False,
          "changes": {"added": [], "removed": [], "modified": [], "object_count_before": 1969,
                      "object_count_after": 1969, "truncated": True,
                      "scope": "Scene object/dependency-graph read-back; external effects are not tracked"},
          "after": {"scene_settings": {"handle": "o1", "engine": "BLENDER_EEVEE", "camera": {
              "handle": "o2", "location": [120.0, -170.0, 60.0], "direction": [-0.559, 0.793, -0.242]},
              "revision": "1b7ed0b6"}, "objects": []},
          "native_exec_ms": 178.278, "execution_mode": "trusted_unconfined", "confined": False,
          "consent": "chat_session", "stop_interruptible": False, "native_dispatch_wait_ms": 7.518}


def size(value):
    return len(json.dumps(value, separators=(",", ":")).encode())


class ModelResultTests(unittest.TestCase):
    def test_a_lookup_loses_its_cache_budgets_and_timing_but_nothing_it_found(self):
        raw = copy.deepcopy(SEARCH)
        shown = model_result("angelo_scene_search", raw)
        self.assertEqual(raw, SEARCH, "The raw result is never modified")
        for key in ("cache", "budgets", "query_ms", "native_exec_ms", "native_dispatch_wait_ms"):
            self.assertNotIn(key, shown)
        for key in ("objects", "truncated", "next_cursor", "observation_token", "page", "matching_count_exact"):
            self.assertEqual(shown[key], SEARCH[key])
        self.assertLess(size(shown), size(SEARCH) - 200)

    def test_a_script_keeps_its_undo_sentence_output_result_changes_and_handles(self):
        shown = model_result("angelo_execute_python", copy.deepcopy(SCRIPT))
        self.assertEqual(shown["undo"], "Script Undo history is unverified.")
        for key in ("result", "success", "output"):
            self.assertEqual(shown[key], SCRIPT[key])
        for key in ("undoable", "consent", "execution_mode", "confined", "stop_interruptible",
                    "native_exec_ms", "external_effects_undoable", "undo_available"):
            self.assertNotIn(key, shown)
        self.assertEqual(shown["changes"], {k: v for k, v in SCRIPT["changes"].items() if k != "scope"})
        # Codex reads it with no observation after it: the scene entry and count stay.
        self.assertEqual((shown["after"], shown["verified"]), (SCRIPT["after"], SCRIPT["verified"]))
        trimmed = model_result("angelo_execute_python", copy.deepcopy(SCRIPT), observation_follows=True)
        self.assertNotIn("after", trimmed, "An after with no objects says nothing the observation does not")
        self.assertNotIn("verified", trimmed, "The following observation states the scene and its count")
        rows = dict(SCRIPT, after={"scene_settings": {"handle": "s"}, "objects": [{"handle": "h", "object_revision": "r"}]})
        self.assertEqual(model_result("angelo_execute_python", rows, observation_follows=True)["after"],
                         {"objects": [{"handle": "h", "object_revision": "r"}]})
        self.assertGreaterEqual(size(SCRIPT) - size(trimmed), 600)  # 645 B of the recorded result

    def test_a_script_s_change_lists_are_capped_as_its_after_is(self):
        # Regression: a rebuild script listed about 400 objects in changes, a 12.2k-token result Codex cut. The
        # model's copy keeps the first rows of each list and counts the rest; the chat and journal keep the raw result.
        from angelo_agent.model_results import MAX_CHANGED
        rows = lambda n, kind: [{"name": "%s %d" % (kind, i), "type": "MESH", "vertices": 8} for i in range(n)]
        heavy = dict(SCRIPT, changes=dict(SCRIPT["changes"], added=rows(400, "Stone"), removed=rows(30, "Old"),
                                          modified=[{"name": "Gate", "fields": ["matrix"]}], object_count_after=2339))
        raw = copy.deepcopy(heavy)
        shown = model_result("angelo_execute_python", heavy)
        self.assertEqual(heavy, raw, "The raw result is never modified")
        changes = shown["changes"]
        self.assertEqual((len(changes["added"]), len(changes["removed"]), len(changes["modified"])), (24, 24, 1))
        self.assertEqual(changes["added"], raw["changes"]["added"][:24])
        self.assertEqual(changes["omitted"], {"added": 376, "removed": 6})
        self.assertEqual((changes["object_count_before"], changes["object_count_after"]), (1969, 2339))
        self.assertNotIn("scope", changes)
        self.assertLess(size(changes), size(raw["changes"]) / 8)
        self.assertNotIn("omitted", model_result("angelo_execute_python", copy.deepcopy(SCRIPT))["changes"])
        # The same bound as a script's after rows (script_after.MAX_OBJECTS).
        after_source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/script_after.py").read_text()
        self.assertIn("MAX_OBJECTS = %d\n" % MAX_CHANGED, after_source)
        # A failed script keeps every field but timing, its change lists whole.
        failed = dict(raw, success=False, error="boom")
        self.assertEqual(len(model_result("angelo_execute_python", failed)["changes"]["added"]), 400)

    def test_failures_keep_every_field_but_timing(self):
        failed = dict(SCRIPT, success=False, error="NameError on line 3", main_data_restored=True)
        shown = model_result("angelo_execute_python", failed)
        self.assertEqual(set(shown), set(failed) - {"native_exec_ms", "native_dispatch_wait_ms"})
        lookup = dict(SEARCH, error="stale")
        self.assertIn("cache", model_result("angelo_scene_search", lookup))

    def test_plan_results_apply_per_operation_and_leave_other_shapes(self):
        operations = [{"tool": "angelo_scene_search", "arguments": {}}, {"tool": "angelo_execute_python", "arguments": {}}]
        applied = {"success": True, "operations": [copy.deepcopy(SEARCH), copy.deepcopy(SCRIPT)]}
        shown = plan_results(operations, applied, observation_follows=True)
        self.assertNotIn("cache", shown["operations"][0])
        self.assertNotIn("consent", shown["operations"][1])
        self.assertEqual(applied["operations"][0], SEARCH)
        other = {"objects": []}
        self.assertIs(plan_results(operations, other), other, "Other shapes pass through")


class ContinuationFollowsTests(unittest.TestCase):
    """A script's scene entry and count leave the continuation only when a turn-context observation follows."""

    def prompt(self, observation, legacy=False):
        from unittest.mock import Mock, patch
        from claude_protocol_fixtures import host
        from angelo_agent.providers import ClaudeProvider
        context = host()
        context.request_observation = Mock(side_effect=lambda callback: callback(copy.deepcopy(observation)))
        provider = ClaudeProvider(context, {})
        script = {"tool": "angelo_execute_python", "arguments": {"description": "Probe", "code": "result = 1"}}
        provider._turn = Mock(side_effect=[{"reply": "", "operations": [script], "verify": True},
                                           {"reply": "Done.", "operations": [], "verify": False}])
        answers = iter([copy.deepcopy(SCRIPT), copy.deepcopy(observation)])
        provider._native_call = Mock(side_effect=lambda *args, **kwargs: next(answers))
        with patch("angelo_agent.change_feed.legacy_turn_context", return_value=legacy):
            self.assertEqual(provider.run(None, "Probe", observation={}), "completed")
        return json.loads(provider._turn.call_args.args[1].split("\n", 1)[1])

    def test_a_turn_context_observation_states_them(self):
        shown = self.prompt({"context_version": 2, "objects": []})["result"]["operations"][0]
        self.assertNotIn("after", shown)
        self.assertNotIn("verified", shown)

    def test_the_legacy_inspect_scene_does_not_so_they_stay(self):
        sent = self.prompt({"scene": "Scene", "object_count": 1969, "objects": []}, legacy=True)
        shown = sent["result"]["operations"][0]
        self.assertEqual((shown["after"], shown["verified"]), (SCRIPT["after"], SCRIPT["verified"]))
        self.assertNotIn("consent", shown)


class ContinuedNoteTests(unittest.TestCase):
    def test_the_line_keeps_its_prefix_and_is_shorter(self):
        self.assertTrue(CONTINUED.startswith("Host confirms these native operations completed."))
        self.assertEqual(len(CONTINUED.encode()), 289)  # 447 B, then 280, then "in reply" (doc/angelo/performance.rst).


class FillRowTests(TurnContextFixture):
    """The fixture scene has 40 objects: more than the 32 an observation lists."""

    def test_a_large_scene_gets_eight_scene_order_rows_after_the_relevant_ones(self):
        names = [entry["name"] for entry in self.prepare().observation["objects"]]
        self.assertEqual(names[:5], ["Prop 39", "Prop 34", "Prop 35", "Prop 36", "Prop 37"])
        self.assertEqual(names[5:], ["Prop %02d" % i for i in range(turn_context.MAX_FILL_OBJECTS)])

    def test_mentioned_and_changed_rows_are_never_cut_by_the_fill_cap(self):
        turn_context.prepare(self.window, "chat-a", clock=Clock(0.)).commit()
        self.depsgraph(Update(self.objects[20], transform=True))
        observation = turn_context.prepare(self.window, "chat-a", clock=Clock(0.), message="Move 'Prop 30'").observation
        names = [entry["name"] for entry in observation["objects"]]
        self.assertEqual(names[:7], ["Prop 39", "Prop 30", "Prop 34", "Prop 35", "Prop 36", "Prop 37", "Prop 20"])
        self.assertEqual(len(names), 7 + turn_context.MAX_FILL_OBJECTS)

    def test_a_scene_the_list_can_hold_is_still_listed_whole(self):
        objects = [object_id(FakeObject(900 + i, "Small %02d" % i, mesh=True)) for i in range(turn_context.MAX_OBJECTS)]
        self.window = make_window(objects, objects[0])
        observation = self.prepare().observation
        self.assertEqual(len(observation["objects"]), turn_context.MAX_OBJECTS)
        self.assertFalse(observation["truncated"])


if __name__ == "__main__":
    unittest.main()
