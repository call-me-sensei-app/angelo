# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object-scoped observations, discovery pages and covered deltas; no Blender.

Capture counts come from the module's own exact_checks counter, so these tests
pin how much of the scene each query fingerprints, not only what it returns.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_scene_queries import FakeObject, queries, window
from angelo_agent.scene_spec import validate_scene


class FakeModifier:
    def __init__(self, name, kind):
        self.name, self.type = name, kind
        self.show_viewport = self.show_render = self.show_in_editmode = self.show_on_cage = True


class CoverageFixture(unittest.TestCase):
    def setUp(self):
        queries.invalidate()
        self.objects = [FakeObject(300 + index, "Prop %03d" % index, mesh=True) for index in range(40)]
        self.window = window(self.objects)
        self.handles = [queries.object_handle(obj) for obj in self.objects]

    def run_query(self, name, **arguments):
        before = queries._counters["exact_checks"]
        result = queries.execute(self.window, name, arguments)
        return result, queries._counters["exact_checks"] - before

    def detail(self, obj, **arguments):
        return self.run_query("angelo_scene_object", object=queries.object_handle(obj), **arguments)

    def search(self, **arguments):
        return self.run_query("angelo_scene_search", **arguments)

    def delta(self, token, **arguments):
        return self.run_query("angelo_scene_delta", observation_token=token, **arguments)

    def covered(self, token):
        return sorted(queries._observation(self.window, token)["objects"])


class ObjectScopedObservationTests(CoverageFixture):
    def test_detail_token_covers_only_the_capture_that_produced_its_revision(self):
        result, captures = self.detail(self.objects[3], sections=["TRANSFORM"])
        self.assertEqual(captures, 1)
        self.assertEqual(result["observation_coverage"], "OBJECTS")
        self.assertEqual(self.covered(result["observation_token"]), [self.handles[3]])
        stored = queries._observation(self.window, result["observation_token"])["objects"][self.handles[3]]
        self.assertEqual(stored["summary"]["object_revision"], result["object_revision"])

    def test_object_delta_compares_only_covered_objects_and_never_reports_additions(self):
        token = self.detail(self.objects[3], sections=["TRANSFORM"])[0]["observation_token"]
        self.objects[3].location[0] = 4.
        self.objects[3].matrix_world[0][3] = 4.
        self.objects[9].location[0] = 9.  # Outside the coverage.
        self.window.scene.objects.append(FakeObject(900, "Added after the baseline", mesh=True))
        delta, captures = self.delta(token)
        self.assertEqual(captures, 1)
        self.assertEqual([(c["change"], c["object"]["object"]) for c in delta["changes"]], [("MODIFIED", self.handles[3])])
        self.assertIn("transform", delta["changes"][0]["fields"])
        self.assertEqual((delta["observation_coverage"], delta["covered_count"]), ("OBJECTS", 1))
        self.assertEqual((delta["object_count_before"], delta["object_count_after"]), (40, 41))
        self.assertIn("COVERED_OBJECTS_ONLY", delta["scope"])
        self.assertTrue(delta["delta_exact"])
        self.assertEqual(self.covered(delta["observation_token"]), [self.handles[3]])

    def test_removing_a_covered_object_is_proven_not_merely_unseen(self):
        token = self.search(coverage="PAGE", limit=2)[0]["observation_token"]
        self.window.scene.objects.remove(self.objects[0])
        delta = self.delta(token)[0]
        self.assertEqual([(c["change"], c["object"]["object"]) for c in delta["changes"]], [("REMOVED", self.handles[0])])
        self.assertEqual(delta["unchanged_count"], 1)
        self.assertFalse(delta["truncated"])
        self.assertEqual(self.covered(delta["observation_token"]), [self.handles[1]])

    def test_unchanged_object_delta_is_empty_but_states_its_coverage(self):
        token = self.detail(self.objects[5], sections=["TRANSFORM"])[0]["observation_token"]
        delta = self.delta(token)[0]
        self.assertEqual(delta["changes"], [])
        self.assertEqual(delta["unchanged_count"], 1)
        self.assertEqual(delta["observation_coverage"], "OBJECTS")

    def test_replaced_covered_id_fails_the_query_instead_of_reporting_removal(self):
        token = self.detail(self.objects[3], sections=["TRANSFORM"])[0]["observation_token"]
        self.objects[3].pointer += 1  # Same session UID, different ID: Undo/load replacement.
        with self.assertRaisesRegex(ValueError, "replaced|expired|evicted"):
            self.delta(token)

    def test_object_tokens_expire_with_the_document_epoch(self):
        token = self.detail(self.objects[3], sections=["TRANSFORM"])[0]["observation_token"]
        queries.invalidate()
        with self.assertRaisesRegex(ValueError, "expired or evicted"):
            self.delta(token)

    def test_object_and_scene_baselines_never_evict_each_other(self):
        scene_token = self.search(limit=1)[0]["observation_token"]
        object_token = self.detail(self.objects[3], sections=["TRANSFORM"])[0]["observation_token"]
        for _ in range(queries.MAX_OBSERVATIONS + 4):
            self.detail(self.objects[4], sections=["TRANSFORM"])
        self.assertEqual(self.delta(scene_token)[0]["observation_coverage"], "SCENE")
        for _ in range(queries.MAX_OBSERVATIONS + 4):
            self.search(limit=1)
        self.assertEqual(self.delta(object_token)[0]["observation_coverage"], "OBJECTS")
        for _ in range(queries.MAX_OBJECT_OBSERVATIONS):
            self.detail(self.objects[4], sections=["TRANSFORM"])
        with self.assertRaisesRegex(ValueError, "evicted"):
            self.delta(object_token)

    def test_relationships_token_is_object_scoped(self):
        self.objects[1].parent = self.objects[0]
        result, captures = self.run_query("angelo_scene_relationships", object=self.handles[0], direction="USES")
        self.assertEqual(captures, 1)
        self.assertEqual(result["observation_coverage"], "OBJECTS")
        self.assertEqual(self.covered(result["observation_token"]), [self.handles[0]])
        self.assertTrue(any(edge["kind"] == "PARENT" for edge in result["relationships"]))


class DiscoverySearchTests(CoverageFixture):
    def setUp(self):
        super().setUp()
        for index, obj in enumerate(self.objects):
            obj.selected = index % 3 == 0
            obj.visible = index % 4 != 0
            if index % 5 == 0:
                obj.modifiers = [FakeModifier("Bevel", "BEVEL")]
        self.objects[7].type = "EMPTY"

    def test_page_and_scene_coverage_apply_identical_filters(self):
        for arguments in ({}, {"query": "prop 01"}, {"types": ["EMPTY"]}, {"selected": True}, {"visible": False},
                          {"modifier": "bevel"}, {"selected": True, "visible": True, "modifier": "BEVEL"}):
            with self.subTest(arguments=arguments):
                scene = self.search(limit=100, **arguments)[0]
                page = self.search(coverage="PAGE", limit=100, **arguments)[0]
                self.assertEqual([o["object"] for o in page["objects"]], [o["object"] for o in scene["objects"]])
                self.assertEqual(page["page"]["total"], scene["page"]["total"])
                self.assertTrue(page["matching_count_exact"])

    def test_discovery_fingerprints_only_the_returned_page(self):
        scene, scene_captures = self.search(limit=5)
        page, page_captures = self.search(coverage="PAGE", limit=5)
        self.assertEqual(scene_captures, 40)
        self.assertEqual(page_captures, 5)
        self.assertEqual((scene["observation_coverage"], page["observation_coverage"]), ("SCENE", "OBJECTS"))
        self.assertEqual(self.covered(page["observation_token"]), sorted(o["object"] for o in page["objects"]))
        self.assertTrue(all(o["object_revision"] for o in page["objects"]))
        self.assertEqual((page["scanned_count"], page["object_count"]), (40, 40))

    def test_each_discovery_page_admits_a_token_for_its_own_rows(self):
        first = self.search(coverage="PAGE", limit=15)[0]
        second = self.search(coverage="PAGE", limit=15, cursor=first["next_cursor"])[0]
        third = self.search(coverage="PAGE", limit=15, cursor=second["next_cursor"])[0]
        pages = [[o["object"] for o in result["objects"]] for result in (first, second, third)]
        self.assertEqual(sum(pages, []), self.handles)
        self.assertIsNone(third["next_cursor"])
        for result, handles in zip((first, second, third), pages):
            self.assertEqual(self.covered(result["observation_token"]), sorted(handles))

    def test_discovery_cursor_tracks_the_match_list_not_unrelated_edits(self):
        first = self.search(coverage="PAGE", query="prop 01", limit=4)[0]
        self.objects[30].name = "Renamed but still not a match"
        self.objects[11].location[0] = 3.  # A matching object's state is captured fresh per page.
        second = self.search(coverage="PAGE", query="prop 01", limit=4, cursor=first["next_cursor"])[0]
        self.assertEqual(second["page"]["offset"], 4)
        self.objects[30].name = "Prop 01 joins the matches"
        with self.assertRaisesRegex(ValueError, "Scene changed during pagination"):
            self.search(coverage="PAGE", query="prop 01", limit=4, cursor=first["next_cursor"])

    def test_cursors_are_bound_to_their_coverage(self):
        scene = self.search(limit=10)[0]
        with self.assertRaisesRegex(ValueError, "another scope or query"):
            self.search(coverage="PAGE", limit=10, cursor=scene["next_cursor"])

    def test_scene_coverage_still_reports_additions_scene_wide(self):
        token = self.search(limit=1)[0]["observation_token"]
        self.window.scene.objects.append(FakeObject(901, "Added after the baseline", mesh=True))
        delta = self.delta(token)[0]
        self.assertEqual(delta["observation_coverage"], "SCENE")
        added = [c["object"]["name"] for c in delta["changes"] if c["change"] == "ADDED"]
        self.assertEqual(added, ["Added after the baseline"])


class ObservationStoreTests(unittest.TestCase):
    def test_oversized_baseline_is_rejected_without_evicting_others(self):
        from angelo_agent.scene_stores import ObservationStore
        store = ObservationStore()
        store.admit("a", "SCENE", b"x" * 10, 4, 30)
        store.admit("b", "SCENE", b"x" * 10, 4, 30)
        with self.assertRaisesRegex(ValueError, "too large"):
            store.admit("c", "SCENE", b"x" * 31, 4, 30)
        self.assertEqual((store.get("a"), store.get("b"), store.get("c")), (b"x" * 10, b"x" * 10, None))
        self.assertEqual(store.admit("d", "SCENE", b"x" * 25, 4, 30), 2)
        self.assertEqual(len(store), 1)
        self.assertEqual(store.admit("e", "OBJECTS", b"y", 1, 30), 0)
        self.assertEqual(store.admit("f", "OBJECTS", b"y", 1, 30), 1)
        self.assertEqual((store.get("d"), store.get("e"), store.get("f")), (b"x" * 25, None, b"y"))


class CoverageContractTests(unittest.TestCase):
    def test_search_coverage_is_closed_and_defaults_to_scene(self):
        self.assertEqual(validate_scene("angelo_scene_search", {})["coverage"], "SCENE")
        self.assertEqual(validate_scene("angelo_scene_search", {"coverage": "PAGE"})["coverage"], "PAGE")
        for value in ("OBJECTS", "page", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_scene("angelo_scene_search", {"coverage": value})
        for name in ("angelo_scene_object", "angelo_scene_relationships", "angelo_scene_delta"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_scene(name, {"object": "scn:" + "a" * 32 + ":1", "observation_token": "obs:" + "a" * 32 + ":" + "b" * 32,
                                      "coverage": "PAGE"})


if __name__ == "__main__":
    unittest.main()
