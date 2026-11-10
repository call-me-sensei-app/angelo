# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Differential bounded model copies over the actual old policy and recorded-shape fixture."""
import suite_isolation  # noqa: F401
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.handoff_json import HandoffBudget, UNSUPPORTED, WORK_LIMIT
from angelo_agent.handoff_model import model_result
from angelo_agent.model_results import model_result as previous
from test_native_program_model_result import committed


class ModelBudget(unittest.TestCase):
    def parity(self, tool, value, **options):
        before = copy.deepcopy(value)
        result = model_result(tool, value, budget=HandoffBudget(1000000), **options)
        self.assertIsNone(result.reason)
        self.assertEqual(result.value, previous(tool, value, **options))
        self.assertEqual(value, before)

    def test_recorded_shape_program_geometry_checks_and_operations_match(self):
        self.parity("angelo_scene_program", committed())

    def test_preview_and_ambiguous_created_names_keep_original_identity_policy(self):
        value = committed()
        value["status"] = "preview"
        self.parity("angelo_scene_program", value)
        value = committed()
        value["live_revisions"]["objects"].append(dict(value["live_revisions"]["objects"][0], handle="another"))
        self.parity("angelo_scene_program", value)

    def test_script_changes_and_following_observation_match(self):
        value = {"success": True, "native_exec_ms": 3, "consent": "approved", "undoable": True,
                 "changes": {"scope": "scene", "added": [{"name": "Object"}] * 40},
                 "after": {"scene_settings": {"name": "Scene"}}, "verified": {"object_count": 40}}
        self.parity("angelo_execute_python", value)
        self.parity("angelo_execute_python", value, observation_follows=True)

    def test_failed_scripts_and_queries_keep_failure_facts(self):
        for tool in ("angelo_execute_python", "angelo_scene_object"):
            self.parity(tool, {"success": False, "error": "failure", "cache": {"hit": True},
                               "budgets": {"limit": 4}, "native_dispatch_wait_ms": 3, "undoable": False})

    def test_query_and_worker_merge_copies_match(self):
        self.parity("angelo_scene_object", {"success": True, "cache": {}, "budgets": {}, "query_ms": 4,
                                             "object": {"name": "Panel", "vertices": 8}})
        self.parity("angelo_workers_merge", {"changes": {"added": [{"name": "Panel", "type": "MESH"}]}})

    def test_normalized_nested_results_are_iterative_and_equal(self):
        value = {"normalized": True, "_worker_finishes": "private", "results": [
            {"success": True, "cache": {}, "query_ms": 4}, {"normalized": True, "results": [{"success": False}]}]}
        self.parity("angelo_scene_object", value)

    def test_geometry_showing_only_instances_matches(self):
        value = committed()
        value["delta"]["geometry"][0]["instances"] = {"own_geometry": False, "bounds": [[0, 0, 0], [2, 2, 2]]}
        self.parity("angelo_scene_program", value)

    def test_deep_normalized_graph_refuses_without_recursive_copy(self):
        value = {"success": True}
        for _ in range(10000):
            value = {"normalized": True, "results": [value]}
        result = model_result("angelo_scene_object", value, budget=HandoffBudget(100))
        self.assertEqual(result.reason, WORK_LIMIT)
        self.assertIsNone(result.value)

    def test_large_unprojected_payload_refuses_the_whole_value(self):
        value = {"success": False, "result": "x" * 1000000}
        result = model_result("angelo_execute_python", value, budget=HandoffBudget(100))
        self.assertEqual(result.reason, WORK_LIMIT)
        self.assertIsNone(result.value)

    def test_custom_colliding_key_cannot_execute_model_copy_lookups(self):
        class Collision:
            def __hash__(self):
                return hash("success")
            def __eq__(self, other):
                raise AssertionError("custom equality")
        result = model_result("angelo_scene_program", {Collision(): True}, budget=HandoffBudget(1000))
        self.assertEqual(result.reason, UNSUPPORTED)
        self.assertIsNone(result.value)

    def test_custom_nested_values_cannot_execute_truth_during_projection(self):
        class Custom:
            def __bool__(self):
                raise AssertionError("custom truth")
        cases = [("angelo_execute_python", {"success": True, "after": {"objects": Custom()}},
                  {"observation_follows": True}),
                 ("angelo_scene_program", {"success": True, "status": "committed",
                  "live_revisions": Custom(), "operation_results": []}, {})]
        for tool, value, options in cases:
            result = model_result(tool, value, budget=HandoffBudget(1000), **options)
            self.assertEqual(result.reason, UNSUPPORTED)
            self.assertIsNone(result.value)


if __name__ == "__main__":
    unittest.main()
