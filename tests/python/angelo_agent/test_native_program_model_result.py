# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A finished program's model copy: every handle, revision, check and failure, one short row per object.

A full result can outgrow what a runtime keeps: a 62-object batch's was 13,319 tokens and Codex cut it, and about
a quarter of recorded program results were over 16 KB. Replayed through ``program_model_result.compact`` with the
invariants below, a set of recorded results went from 1,738,199 B to 1,003,093 B, the largest from 56,958 B to
23,404 B, and none stayed over 40 KB. These pin the shape on a small result of the recorded form. Host only; no
Blender.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import model_results, program_model_result  # noqa: E402

NOTE = "Post-commit handles and exact object revisions from the production revision check. ..."


def committed():
    """The recorded shape (a bridge one-shot's program), two objects created and one written, short handles."""
    return {
        "partial_changes_possible": False, "mutation_started": True, "success": True, "final": True,
        "status": "committed", "program_id": "e353f725-057d-4f34-ba11-a58ad7e083df", "undoable": True,
        "native_exec_ms": 41.2,
        "verified": {"object_count": 5, "write_count": 1, "added_count": 2, "exact_preconditions": True},
        "live_revisions": {"status": "COMMITTED", "document_epoch": "e584f02edda84a9293bd0d38c7ade425",
                           "truncated": False, "note": NOTE, "objects": [
                               {"handle": "o17", "name": "Railing Post", "type": "MESH", "role": "CREATED",
                                "alias": "$post", "object_revision": "c11da816"},
                               {"handle": "o18", "name": "Sun", "type": "LIGHT", "role": "CREATED",
                                "object_revision": "9ca92568"},
                               {"handle": "o5", "name": "Valley", "type": "MESH", "role": "WRITTEN",
                                "object_revision": "b5cc8539"}]},
        "changes": {"added": [{"name": "Railing Post", "type": "MESH"}, {"name": "Sun", "type": "LIGHT"}],
                    "removed": [{"name": "Cube", "type": "MESH"}],
                    "modified": [{"name": "Valley", "fields": ["materials"]}],
                    "truncated": False, "scope": "Validated native staged transaction"},
        "delta": {"added": [{"name": "Railing Post", "type": "MESH", "vertices": 8}],
                  "removed": [{"handle": "o3", "name": "Cube", "type": "MESH"}], "modified": [],
                  "object_count_before": 4, "object_count_after": 5, "truncated": False,
                  "scope": "Exact bound staged-object delta; unchanged live objects are not rewritten",
                  "summary": "2 object(s) added; 1 object(s) modified; 1 object(s) deleted.",
                  "geometry": [
                      {"name": "Valley", "type": "MESH", "handle": "o5",
                       "bounds": [[-60.0, -40.0, -8.0], [60.0, 40.0, 4.996613502502441]],
                       "matrix": [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0],
                       "mesh_counts": {"vertices": 5281, "edges": 10400, "faces": 5121}},
                      {"name": "Railing Post", "type": "MESH",
                       "bounds": [[-12.050000190734863, -2.450000047683716, 1.0],
                                  [-11.949999809265137, -2.3500001430511475, 2.0]],
                       "matrix": [0.05, 0, 0, -12.0, 0, 0.05, 0, -2.4, 0, 0, 0.5, 1.5, 0, 0, 0, 1.0],
                       "mesh_counts": {"vertices": 8, "edges": 12, "faces": 6}},
                      {"name": "Sun", "type": "LIGHT", "bounds": [[0.0, 0.0, 9.0], [0.0, 0.0, 9.0]],
                       "matrix": [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 9.0, 0, 0, 0, 1.0], "light_type": "SUN",
                       "energy": 3.0},
                      {"name": "Pier", "type": "MESH", "handle": "o9", "bounds": [[1.0, 1.0, 0.0], [2.0, 2.0, 3.0]],
                       "matrix": [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0],
                       "mesh_counts": {"vertices": 8, "edges": 12, "faces": 6}}],
                  "geometry_scope": "Bounded summaries only; native staged pixels are returned in views",
                  "details": []},
        "checks": [
            {"success": True, "passed": True, "status": "pass", "truncated": False, "read_only": True,
             "scope": "closed_base_data_predicates", "kind": "transform", "message": "transform",
             "checks": [{"index": 0, "kind": "transform", "target": "$post", "status": "pass", "passed": True,
                         "observed": {"location": [-12.0, -2.4000000953674316, 1.5], "scope": "local_RNA"}}]},
            {"success": True, "passed": False, "status": "fail", "truncated": False, "kind": "object_name",
             "checks": [{"index": 1, "kind": "object_name", "target": "$post", "status": "fail", "passed": False,
                         "observed": {"name": "Railing Post.001"}}]}],
        "operation_results": [
            {"kind": "create_primitive", "target": None, "object": "Railing Post", "object_type": "MESH",
             "vertices": 8, "edges": 12, "faces": 6, "created": True, "alias": "$post"},
            {"kind": "material_principled", "target": "o5", "object": "Valley", "object_type": "MESH",
             "vertices": 5281, "edges": 10400, "faces": 5121, "material": "Valley Grass", "slot": 1,
             "assigned_faces": 5121, "topology_step": 0}],
        "orphan_data": {"count": 1, "names": ["Cube"], "note": "..."},
    }


class CompactTests(unittest.TestCase):
    def test_every_handle_revision_and_check_survives_and_each_object_has_one_row(self):
        raw = committed()
        before = copy.deepcopy(raw)
        shown = model_results.model_result("angelo_scene_program", raw)
        self.assertEqual(raw, before, "The raw result is never modified")
        live = shown["live_revisions"]
        self.assertNotIn("note", live)
        self.assertEqual([(row["handle"], row["object_revision"], row.get("alias")) for row in live["objects"]],
                         [("o17", "c11da816", "$post"), ("o18", "9ca92568", None), ("o5", "b5cc8539", None)])
        post, sun, valley = live["objects"]
        self.assertEqual(post["bounds"], [[-12.05, -2.45, 1.0], [-11.95, -2.35, 2.0]])
        self.assertEqual((post["vertices"], post["faces"]), (8, 6))
        self.assertEqual((sun["light_type"], sun["energy"]), ("SUN", 3.0))
        self.assertEqual(valley["bounds"], [[-60.0, -40.0, -8.0], [60.0, 40.0, 4.997]])
        # A geometry row without a live row (an input only read) stays, without its matrix.
        self.assertEqual(shown["delta"]["geometry"], [{"name": "Pier", "type": "MESH", "handle": "o9",
                                                        "bounds": [[1.0, 1.0, 0.0], [2.0, 2.0, 3.0]],
                                                        "vertices": 8, "faces": 6}])
        self.assertFalse({"added", "removed", "modified", "scope", "geometry_scope"} & set(shown["delta"]))
        self.assertEqual(shown["changes"], {"removed": [{"name": "Cube", "type": "MESH"}],
                                            "modified": [{"name": "Valley", "fields": ["materials"]}],
                                            "truncated": False})
        passed, failed = shown["checks"]
        self.assertEqual(passed, {"index": 0, "kind": "transform", "target": "$post", "passed": True,
                                  "observed": {"location": [-12.0, -2.4, 1.5], "scope": "local_RNA"}})
        self.assertEqual(failed, raw["checks"][1], "A check that did not pass is kept whole")
        self.assertEqual(shown["operation_results"], [
            {"kind": "create_primitive", "object": "Railing Post", "alias": "$post"},
            {"kind": "material_principled", "target": "o5", "object": "Valley", "material": "Valley Grass",
             "slot": 1, "assigned_faces": 5121, "topology_step": 0}])
        self.assertEqual(shown["orphan_data"], raw["orphan_data"])
        self.assertEqual(shown["detail"], program_model_result.DETAIL)
        self.assertNotIn("native_exec_ms", shown)
        size = lambda value: len(json.dumps(value, separators=(",", ":")))  # noqa: E731
        self.assertLess(size(shown), size(raw) * 0.7)

    def test_a_rename_never_moves_one_objects_geometry_to_another(self):
        """Regression: an input renamed to another input's old name. Its geometry row keeps its logical
        (pre-rename) name, so a match by name gave the new object's handle the old object's size."""
        raw = committed()
        raw["live_revisions"]["objects"] = [
            {"handle": "o5", "name": "Valley_old", "type": "MESH", "role": "WRITTEN", "object_revision": "11111111"},
            {"handle": "o6", "name": "Valley", "type": "MESH", "role": "WRITTEN", "object_revision": "22222222"}]
        raw["delta"]["geometry"] = [
            {"name": "Valley", "type": "MESH", "handle": "o5", "bounds": [[0, 0, 0], [1, 1, 1]],
             "mesh_counts": {"vertices": 8, "edges": 12, "faces": 6}},
            {"name": "Valley_new", "type": "MESH", "handle": "o6", "bounds": [[6, 0, 0], [14, 1, 1]],
             "mesh_counts": {"vertices": 400, "edges": 760, "faces": 361}}]
        live = model_results.model_result("angelo_scene_program", raw)["live_revisions"]["objects"]
        self.assertEqual([(row["name"], row["bounds"], row["vertices"]) for row in live],
                         [("Valley_old", [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], 8),
                          ("Valley", [[6.0, 0.0, 0.0], [14.0, 1.0, 1.0]], 400)])

    def test_a_created_name_shared_by_two_rows_merges_nothing(self):
        raw = committed()
        raw["live_revisions"]["objects"].append(dict(raw["live_revisions"]["objects"][0], handle="o30"))
        live = model_results.model_result("angelo_scene_program", raw)["live_revisions"]["objects"]
        self.assertTrue(all("bounds" not in row for row in live if row["name"] == "Railing Post"))

    def test_a_preview_keeps_its_delta_lists_declared_rows_and_note(self):
        raw = committed()
        raw["status"] = "preview"
        del raw["changes"]
        raw["live_revisions"] = {"status": "UNCHANGED_UNTIL_ACCEPT", "note": "The live scene is unchanged ...",
                                 "objects": [{"handle": "o5", "object_revision": "b5cc8539", "role": "DECLARED"}]}
        shown = model_results.model_result("angelo_scene_program", raw)
        self.assertEqual(shown["live_revisions"], raw["live_revisions"], "A preview's inputs are not changed yet")
        self.assertEqual(shown["delta"]["added"], raw["delta"]["added"])
        self.assertEqual(len(shown["delta"]["geometry"]), 4)
        self.assertTrue(all("matrix" not in row for row in shown["delta"]["geometry"]))
        self.assertEqual(shown["detail"], program_model_result.PREVIEW_DETAIL)

    def test_a_mesh_without_vertices_has_no_bounds_and_observed_numbers_keep_their_precision(self):
        raw = committed()
        raw["delta"]["geometry"][3]["mesh_counts"] = {"vertices": 0, "edges": 0, "faces": 0}
        raw["delta"]["geometry"][3]["bounds"] = [[1.0, 1.0, 0.0], [1.0, 1.0, 0.0]]  # Blender's point box.
        raw["checks"][0]["checks"][0]["observed"]["location"] = [0.000123456789, 12345.678912345, 1.5]
        shown = model_results.model_result("angelo_scene_program", raw)
        self.assertNotIn("bounds", shown["delta"]["geometry"][0])
        raw["delta"]["geometry"][3]["bounds"] = [[-10.0, -8.0, -1.0], [10.0, 8.0, -1.0]]  # Geometry Nodes made it.
        shown = model_results.model_result("angelo_scene_program", raw)
        self.assertEqual(shown["delta"]["geometry"][0]["bounds"], [[-10.0, -8.0, -1.0], [10.0, 8.0, -1.0]])
        self.assertEqual(shown["checks"][0]["observed"]["location"], [0.0001234568, 12345.68, 1.5])

    def test_a_failure_and_other_tools_are_left_as_they_are(self):
        failed = dict(committed(), success=False, status="failed", error="Rule-based face selector ...",
                      error_code="LIMIT_EXCEEDED")
        shown = model_results.model_result("angelo_scene_program", failed)
        self.assertEqual(shown, {key: value for key, value in failed.items() if key != "native_exec_ms"})
        other = committed()
        self.assertIs(program_model_result.compact("angelo_scene_object", other), other)


if __name__ == "__main__":
    unittest.main()
