# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only closed contract, admission, budget and immutable-cache tests."""

import ast
from array import array
import importlib
import inspect
import json
import math
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.scene_spec import SCENE_TOOLS, SCENE_NAMES, SECTIONS, validate_scene


class FakeID:
    def __init__(self, uid, name):
        self.session_uid = uid
        self.name = name
        self.library = None
        self.is_editable = True
        self.pointer = uid * 10
        self.animation_data = None
        self.asset_data = None

    def as_pointer(self):
        return self.pointer

    def get(self, *_):
        raise AssertionError("Scene readers must not read custom properties")

    def keys(self):
        raise AssertionError("Scene readers must not enumerate custom properties")

    def __setitem__(self, *_):
        raise AssertionError("Scene readers must not mutate custom properties")


class FakeCollection(FakeID):
    pass


class FakeRows(list):
    def foreach_get(self, field, target):
        values = []
        for row in self:
            value = getattr(row, field)
            values.extend(value if isinstance(value, (tuple, list)) else [value])
        assert len(values) == len(target)
        for index, value in enumerate(values):
            target[index] = value

    def get(self, name):
        return next((row for row in self if row.name == name), None)


class ElementFlags:
    """Blender stores element select/hide in these BOOLEAN attributes; this view reads the fake elements."""
    def __init__(self, name, domain, elements, field):
        self.name, self.domain, self.data_type = name, domain, "BOOLEAN"
        self._elements, self._field = elements, field

    @property
    def data(self):
        return FakeRows(SimpleNamespace(value=bool(getattr(row, self._field))) for row in self._elements())


class FakeMesh(FakeID):
    def __init__(self, uid):
        super().__init__(uid, "Original mesh")
        self.vertices = FakeRows([SimpleNamespace(co=[0., 0., 0.], select=False, hide=False, index=0, groups=[]),
                                  SimpleNamespace(co=[1., 0., 0.], select=True, hide=False, index=1, groups=[])])
        self.edges = FakeRows([SimpleNamespace(vertices=[0, 1], select=True, hide=False)])
        self.polygons = FakeRows()
        self.loops = FakeRows()
        self.attributes = FakeRows(ElementFlags(name, domain, elements, field) for name, domain, elements, field in (
            (".select_vert", "POINT", lambda: self.vertices, "select"), (".hide_vert", "POINT", lambda: self.vertices, "hide"),
            (".select_edge", "EDGE", lambda: self.edges, "select"), (".hide_edge", "EDGE", lambda: self.edges, "hide"),
            (".select_poly", "FACE", lambda: self.polygons, "select"), (".hide_poly", "FACE", lambda: self.polygons, "hide")))
        self.uv_layers = FakeRows()
        self.shape_keys = None


class FakeObject(FakeID):
    def __init__(self, uid, name, mesh=False):
        super().__init__(uid, name)
        self.type = "MESH" if mesh else "EMPTY"
        self.data = FakeMesh(uid + 10000) if mesh else None
        self.mode = "OBJECT"
        self.location = [0., 0., 0.]
        self.rotation_mode = "XYZ"
        self.rotation_euler = [0., 0., 0.]
        self.rotation_quaternion = [1., 0., 0., 0.]
        self.scale = [1., 1., 1.]
        self.matrix_world = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]]
        self.matrix_parent_inverse = self.matrix_world
        self.delta_location = [0., 0., 0.]
        self.delta_rotation_euler = [0., 0., 0.]
        self.delta_rotation_quaternion = [1., 0., 0., 0.]
        self.delta_scale = [1., 1., 1.]
        self.lock_location = [False] * 3
        self.lock_rotation = [False] * 3
        self.lock_scale = [False] * 3
        self.parent = None
        self.parent_type = "OBJECT"
        self.parent_bone = ""
        self.users_collection = []
        self.modifiers = []
        self.material_slots = []
        self.vertex_groups = []
        self.constraints = []
        self.pose = None
        self.instance_collection = None
        self.hide_viewport = self.hide_render = self.hide_select = False
        self.selected = False
        self.visible = True

    def select_get(self, view_layer=None):
        return self.selected

    def visible_get(self, view_layer=None):
        return self.visible


class FakeObjects(list):
    active = None


def window(objects, uid=1):
    scene = FakeID(uid, "Disposable scene")
    scene.objects = FakeObjects(objects)
    scene.frame_current = 1
    layer = FakeID(uid + 500, "View layer")
    layer.objects = scene.objects
    return SimpleNamespace(scene=scene, view_layer=layer)


fake_bpy = ModuleType("bpy")
fake_bpy.types = SimpleNamespace(Object=FakeObject, Collection=FakeCollection)
# Whole-scene passes index membership from bpy.data, as Object.users_collection
# does; these default fakes link no objects to any collection.
fake_bpy.data = SimpleNamespace(collections=[], scenes=[])
with patch.dict(sys.modules, {"bpy": fake_bpy}):
    queries = importlib.import_module("angelo_agent.scene_queries")


class SceneContractTests(unittest.TestCase):
    def test_public_api_and_cohesive_module_size_and_imports(self):
        expected = {"execute": ["window", "name", "args"], "invalidate": ["window", "depsgraph"],
                    "resolve_object": ["window", "handle"], "object_revision": ["window", "obj"],
                    "object_handle": ["obj"]}
        for name, parameters in expected.items():
            self.assertEqual(list(inspect.signature(getattr(queries, name)).parameters), parameters)
        directory = Path(queries.__file__).parent
        modules = ("scene_queries", "scene_spec", "scene_read_values", "scene_read_fields",
                   "scene_metadata", "scene_geometry", "scene_catalog_graphs", "scene_fingerprints", "scene_relationships",
                   "scene_query_results", "scene_coverage", "scene_stores", "scene_shared_reads")
        for name in modules:
            with self.subTest(module=name):
                source = (directory/(name+".py")).read_text()
                self.assertLessEqual(len(source.splitlines()), 500)
                self.assertIn("SPDX-License-Identifier:", source)
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertTrue(all(alias.name != "*" for alias in node.names))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, {"globals", "locals", "eval", "exec"})

    def test_closed_readonly_toolset(self):
        self.assertEqual(len(SCENE_NAMES), 5)
        for tool in SCENE_TOOLS:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])
            self.assertFalse(tool["annotations"]["openWorldHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            with self.assertRaises(ValueError):
                validate_scene(tool["name"], {"code": "bpy.ops.object.delete()"})

    def test_payload_abuse_is_validation_not_internal_type_error(self):
        for bad in (None, [], "scene", 1, True):
            with self.subTest(args=bad), self.assertRaises(ValueError):
                validate_scene("angelo_scene_search", bad)
        for name in (None, [], {}, "angelo_execute_python"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_scene(name, {})
        for field, bad in (("query", []), ("query", "a\x00b"), ("query", "x"*129),
                           ("selected", 1), ("visible", "true"), ("limit", True), ("limit", 101), ("limit", 0),
                           ("types", [{}]), ("types", [["MESH"]]), ("types", ["MESH", "MESH"]),
                           ("collection", None), ("material", ""), ("modifier", {"path": "secret"}),
                           ("cursor", 4), ("cursor", "page:"+"a"*100000)):
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                validate_scene("angelo_scene_search", {field: bad})

    def test_ids_not_names_paths_or_rna_and_no_nested_aliases(self):
        for bad in ("Cube", "../scene.blend", "bpy.data.objects['Cube']", "scn:"+"a"*32+":0", [], None):
            with self.subTest(handle=bad), self.assertRaises(ValueError):
                validate_scene("angelo_scene_object", {"object": bad})
        source = {"types": ["MESH"], "query": "Artist"}
        checked = validate_scene("angelo_scene_search", source)
        checked["types"].append("EMPTY")
        self.assertEqual(source["types"], ["MESH"])
        self.assertEqual(validate_scene("angelo_scene_object", {"object": "scn:"+"a"*32+":1"})["sections"], list(SECTIONS))


class SceneObservationTests(unittest.TestCase):
    def setUp(self):
        queries.invalidate()
        self.obj = FakeObject(101, "Artist mesh", mesh=True)
        self.other = FakeObject(102, "Target")
        self.window = window([self.obj, self.other])

    def search(self, **kwargs):
        return queries.execute(self.window, "angelo_scene_search", kwargs)

    def delta(self, token, **kwargs):
        return queries.execute(self.window, "angelo_scene_delta", dict(observation_token=token, **kwargs))

    def test_rename_delete_and_object_replacement(self):
        handle = queries.object_handle(self.obj)
        self.obj.name = "Renamed by artist"
        self.assertIs(queries.resolve_object(self.window, handle), self.obj)
        self.assertEqual(queries.object_handle(self.obj), handle)
        self.obj.pointer += 1
        with self.assertRaisesRegex(ValueError, "expired"):
            queries.resolve_object(self.window, handle)
        fresh = queries.object_handle(self.obj)
        self.window.scene.objects.remove(self.obj)
        with self.assertRaisesRegex(ValueError, "deleted"):
            queries.resolve_object(self.window, fresh)

    def test_unknown_edits_invalidate_cache_but_keep_delta_baselines(self):
        before = self.search()
        old = queries.object_revision(self.window, self.obj)
        self.obj.data.vertices[0].co[0] = .25  # No update/handler call whatsoever.
        self.assertNotEqual(queries.object_revision(self.window, self.obj), old)
        queries.invalidate(window=self.window)
        delta = self.delta(before["observation_token"])
        row = next(c for c in delta["changes"] if c["object"]["object"] == queries.object_handle(self.obj))
        self.assertIn("geometry", row["fields"])
        self.assertTrue(delta["delta_exact"])
        self.assertEqual(delta["unchanged_count"], 1)

    def test_transform_selection_mask_and_topology_affect_revision(self):
        revision = queries.object_revision(self.window, self.obj)
        self.obj.location[0] = 3
        moved = queries.object_revision(self.window, self.obj)
        self.assertNotEqual(revision, moved)
        self.obj.selected = True
        selected = queries.object_revision(self.window, self.obj)
        self.assertNotEqual(moved, selected)
        attribute = SimpleNamespace(name=".sculpt_mask", domain="POINT", data_type="FLOAT",
                                    data=FakeRows([SimpleNamespace(value=.1), SimpleNamespace(value=.5)]))
        self.obj.data.attributes.append(attribute)
        masked = queries.object_revision(self.window, self.obj)
        self.assertNotEqual(masked, selected)
        attribute.data[0].value = .2
        self.assertNotEqual(queries.object_revision(self.window, self.obj), masked)
        self.obj.data.edges[0].vertices = [1, 0]
        self.assertNotEqual(queries.object_revision(self.window, self.obj), masked)

    def test_immutable_cache_reuse_and_depsgraph_invalidation(self):
        args = {"object": queries.object_handle(self.obj), "sections": ["MESH", "TRANSFORM"]}
        first = queries.execute(self.window, "angelo_scene_object", args)
        first["sections"]["transform"]["location"][0] = 999
        repeated = queries.execute(self.window, "angelo_scene_object", args)
        self.assertGreater(repeated["cache"]["cache_hits"], 0)
        self.assertEqual(repeated["sections"]["transform"]["location"], [0., 0., 0.])
        self.assertTrue(all(type(value) is str for value in queries._cache.values()))
        self.assertTrue(all(type(value) is bytes for value in queries._observations.values()))
        count = len(queries._cache)
        queries.invalidate(depsgraph=SimpleNamespace(updates=[SimpleNamespace(id=self.obj.data)]))
        self.assertLess(len(queries._cache), count)
        self.assertGreater(queries._counters["depsgraph_updates"], 0)

    def test_pagination_is_stable_and_changed_query_or_scene_rejected(self):
        self.window.scene.objects.extend(FakeObject(200+i, f"Many{i:02}") for i in range(11))
        result = self.search(query="Many", limit=3)
        all_rows = list(result["objects"])
        token = result["observation_token"]
        first_cursor = result["next_cursor"]
        while result["next_cursor"]:
            result = self.search(query="Many", limit=3, cursor=result["next_cursor"])
            self.assertEqual(result["observation_token"], token)
            all_rows.extend(result["objects"])
        self.assertEqual(len(all_rows), 11)
        self.assertEqual(len({o["object"] for o in all_rows}), 11)
        with self.assertRaisesRegex(ValueError, "query"):
            self.search(query="Other", limit=3, cursor=first_cursor)
        self.obj.name = "Changed during pagination"
        with self.assertRaisesRegex(ValueError, "changed during pagination"):
            self.search(query="Many", limit=3, cursor=first_cursor)

    def test_cross_document_scene_layer_and_epoch_token_rejection(self):
        token = self.search()["observation_token"]
        other_window = window([self.obj], uid=77)
        with self.assertRaisesRegex(ValueError, "another document"):
            queries.execute(other_window, "angelo_scene_delta", {"observation_token": token})
        self.window.view_layer.pointer += 2
        with self.assertRaisesRegex(ValueError, "view layer"):
            self.delta(token)
        self.window.view_layer.pointer -= 2
        handle = queries.object_handle(self.obj)
        queries.invalidate()
        with self.assertRaisesRegex(ValueError, "expired"):
            queries.resolve_object(self.window, handle)
        with self.assertRaisesRegex(ValueError, "unadmitted"):
            self.delta(token)

    def test_delta_add_remove_rename_and_empty_are_exact(self):
        before = self.search()
        self.assertEqual(self.delta(before["observation_token"])["changes"], [])
        old = queries.object_handle(self.obj)
        self.obj.name = "Renamed"
        self.window.scene.objects.remove(self.other)
        self.window.scene.objects.append(FakeObject(300, "New"))
        result = self.delta(before["observation_token"])
        self.assertEqual({c["change"] for c in result["changes"]}, {"ADDED", "REMOVED", "MODIFIED"})
        renamed = next(c for c in result["changes"] if c["change"] == "MODIFIED")
        self.assertEqual(renamed["object"]["object"], old)
        self.assertEqual(renamed["fields"], ["name"])

    def test_delta_pagination_token_is_pinned_and_eviction_fails_closed(self):
        baseline = self.search()["observation_token"]
        self.window.scene.objects.extend(FakeObject(400+i, f"New{i}") for i in range(9))
        result = self.delta(baseline, limit=2)
        admitted = result["observation_token"]
        rows = []
        while True:
            rows.extend(result["changes"])
            if not result["next_cursor"]:
                break
            result = self.delta(baseline, limit=2, cursor=result["next_cursor"])
            self.assertEqual(result["observation_token"], admitted)
        self.assertEqual(len(rows), 9)
        self.assertEqual({r["change"] for r in rows}, {"ADDED"})
        with patch.object(queries, "MAX_OBSERVATIONS", 2):
            for _ in range(3):
                self.search()
            with self.assertRaisesRegex(ValueError, "evicted"):
                self.delta(baseline)

    def test_asset_metadata_truncation_cannot_authorize_and_payload_is_hard_bounded(self):
        self.obj.asset_data = SimpleNamespace(catalog_id="0"*36, description="x"*1000, author="Artist", tags=[])
        result = self.search()
        asset = next(r["asset"] for r in result["objects"] if r["asset"])
        self.assertTrue(asset["description_truncated"])
        with self.assertRaisesRegex(ValueError, "bounded_asset_metadata"):
            queries.object_revision(self.window, self.obj)
        with patch.object(queries, "MAX_PAYLOAD_BYTES", 900):
            limited = self.search()
            self.assertLessEqual(len(queries._dump(limited).encode()), 900)
            self.assertTrue(limited["truncated"])
            self.assertIsNone(limited["next_cursor"])

    def test_uses_and_mutable_dependency_revision(self):
        self.obj.parent = self.other
        revision = queries.object_revision(self.window, self.obj)
        self.other.location[0] = 7
        self.other.matrix_world[0][3] = 7
        self.assertNotEqual(queries.object_revision(self.window, self.obj), revision)
        relationship = queries.execute(self.window, "angelo_scene_relationships", {"object": queries.object_handle(self.other), "direction": "USES"})
        self.assertTrue(any(e["kind"] == "PARENT" and e["from"]["object"] == queries.object_handle(self.obj) for e in relationship["relationships"]))

    def test_overbudget_or_unsupported_never_claims_unchanged_authority(self):
        before = self.search()
        with patch.object(queries, "MAX_ELEMENTS", 1):
            with self.assertRaisesRegex(ValueError, "work_budget"):
                queries.object_revision(self.window, self.obj)
            after = self.delta(before["observation_token"])
            self.assertFalse(after["delta_exact"])
            self.assertGreater(after["uncertain_count"], 0)
        self.obj.mode = "SCULPT"
        with self.assertRaisesRegex(ValueError, "sculpt"):
            queries.object_revision(self.window, self.obj)
        unsupported = self.search()
        self.assertFalse(unsupported["snapshot_exact"])
        self.assertTrue(any(c["change"] == "UNCERTAIN" for c in self.delta(unsupported["observation_token"])["changes"]))

    def test_selected_component_and_payload_bounds_are_explicit(self):
        result = queries.execute(self.window, "angelo_scene_object", {"object": queries.object_handle(self.obj), "sections": ["MESH"], "limit": 1})
        selected = result["sections"]["mesh"]["selection"]
        self.assertEqual(selected["vertices"]["count"], 1)
        self.assertEqual(selected["vertices"]["indices"], [1])
        self.assertLessEqual(len(json.dumps(result).encode()), queries.MAX_PAYLOAD_BYTES)
        with patch.object(queries, "MAX_OBJECTS", 1):
            truncated = self.search()
            self.assertTrue(truncated["truncated"])
            self.assertFalse(truncated["matching_count_exact"])


class ForbiddenMembership:
    """users_collection stand-in proving a whole-scene pass used its index."""
    def __iter__(self):
        raise AssertionError("Whole-scene passes must use the per-pass membership index")
    __len__ = __iter__


class CollectionMembershipTests(unittest.TestCase):
    def setUp(self):
        queries.invalidate()
        self.first, self.second = FakeObject(201, "First"), FakeObject(202, "Second")
        self.props, self.extra = FakeCollection(301, "Props"), FakeCollection(302, "Extra")
        self.master = FakeCollection(303, "Scene Collection")
        self.props.objects, self.extra.objects, self.master.objects = [self.first, self.second], [self.first], [self.second]
        # Object.users_collection order: Main collections, then scene master collections.
        self.expected = {201: [self.props, self.extra], 202: [self.props, self.master]}
        self.data = SimpleNamespace(collections=[self.props, self.extra], scenes=[SimpleNamespace(collection=self.master)])
        for obj in (self.first, self.second):
            obj.users_collection = list(self.expected[obj.session_uid])
        self.window = window([self.first, self.second])

    def search(self, **kwargs):
        # scene_fingerprints imported bpy while fake_bpy was installed.
        with patch.object(fake_bpy, "data", self.data):
            return queries.execute(self.window, "angelo_scene_search", kwargs)

    def test_index_matches_users_collection_order_and_is_all_or_nothing(self):
        build, wanted = queries.collection_memberships, {201, 202}
        self.props.objects.append(FakeObject(299, "Other scene"))  # Not scanned: neither recorded nor counted.
        with patch.object(fake_bpy, "data", self.data):
            self.assertEqual(build(wanted, time.monotonic() + 60, 4), self.expected)
            self.assertIsNone(build(wanted, time.monotonic() + 60, 3))  # Four wanted links exist.
            self.assertIsNone(build(wanted, time.monotonic() - 1, 4))  # Deadline already passed.
            clock = iter([0.0, 2.0])  # Expires after the first collection: no partial index.
            # Patch only scene_fingerprints' clock, never the process-wide one other threads read.
            with patch.dict(build.__globals__, {"time": SimpleNamespace(monotonic=lambda: next(clock))}):
                self.assertIsNone(build(wanted, 1.0, 4))
        self.assertEqual(queries.membership_reader(None)(self.first), (self.props, self.extra))

    def test_index_is_bounded_by_the_pass_budget(self):
        budget, calls = queries._read_budget(), []

        def recorded(wanted, deadline, max_links):
            calls.append((wanted, deadline, max_links))
            return None  # Unavailable index: the pass must still finish via per-object reads.
        with patch.object(queries, "_read_budget", lambda deadline=None: budget), \
                patch.object(queries, "collection_memberships", recorded):
            self.assertEqual(len(self.search()["objects"]), 2)
        self.assertEqual(calls, [({201, 202}, budget.deadline, queries.MAX_MEMBERSHIP_LINKS)])

    def test_search_reads_membership_once_per_pass_and_filters_with_it(self):
        for obj in (self.first, self.second):
            obj.users_collection = ForbiddenMembership()
        rows = {row["object"]: row for row in self.search()["objects"]}
        first = rows[queries.object_handle(self.first)]
        self.assertEqual((first["collections"], first["collection_count"]), (["Props", "Extra"], 2))
        filtered = self.search(collection="Extra")["objects"]
        self.assertEqual([row["object"] for row in filtered], [queries.object_handle(self.first)])

    def test_unavailable_index_falls_back_to_identical_per_object_reads(self):
        indexed = self.search(collection="Props")["objects"]
        fallbacks = queries._counters["membership_fallbacks"]
        with patch.object(queries, "MAX_MEMBERSHIP_LINKS", 1):
            fallback = self.search(collection="Props")["objects"]
        self.assertEqual(len(indexed), 2)
        self.assertEqual(indexed, fallback)
        self.assertEqual(queries._counters["membership_fallbacks"], fallbacks + 1)

    def test_delta_reports_collection_moves_through_the_index(self):
        for obj in (self.first, self.second):
            obj.users_collection = ForbiddenMembership()
        baseline = self.search()["observation_token"]
        self.extra.objects.remove(self.first)
        self.master.objects.append(self.first)
        queries.invalidate(window=self.window)
        with patch.object(fake_bpy, "data", self.data):
            delta = queries.execute(self.window, "angelo_scene_delta", {"observation_token": baseline})
        moved = {row["object"]["object"]: row for row in delta["changes"]}[queries.object_handle(self.first)]
        self.assertEqual(moved["change"], "MODIFIED")
        self.assertIn("collections", moved["fields"])
        self.assertEqual(delta["unchanged_count"], 1)


class PackedValueHelperTests(unittest.TestCase):
    def test_c_level_scans_match_scalar_definitions_on_edges(self):
        from angelo_agent.scene_read_values import all_finite, count_nonzero
        largest = array("f", [3.4028234663852886e38])[0]
        for values in ([], [0.0, -1.5], [largest] * 100000, [-largest, largest], [math.inf],
                       [1.0, -math.inf], [math.inf, -math.inf], [math.nan], [0.0, math.nan, 1.0]):
            packed = array("f", values)
            with self.subTest(values=values[:3], count=len(values)):
                self.assertEqual(all_finite(packed), all(math.isfinite(v) for v in packed))
        for flags in (array("b"), array("b", [0, 1, 0, 1, 1]), array("b", [0] * 10), [True, False, True], []):
            with self.subTest(flags=list(flags)):
                self.assertEqual(count_nonzero(flags), sum(bool(v) for v in flags))


if __name__ == "__main__":
    unittest.main()
