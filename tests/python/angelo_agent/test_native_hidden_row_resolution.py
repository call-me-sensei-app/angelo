# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Search visibility resolves page identity once without per-row scene-name walks."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import scene_query_results
from test_native_visibility_facts import Collection, Thing, layer, view_layer


def handle(obj):
    return "scn:%s:%d" % (getattr(obj, "epoch", "test"), obj.session_uid)


def thing(uid, name="Same", **kwargs):
    obj = Thing(kwargs.pop("collections", []), **kwargs)
    obj.session_uid, obj.name = uid, name
    return obj


def row(obj, **kwargs):
    return dict(object=handle(obj), name=obj.name[:128], **kwargs)


class Objects:
    def __init__(self, objects):
        self.objects, self.iterations, self.visited = objects, 0, []

    def get(self, name):
        raise AssertionError("Per-row Scene.objects.get rebuilds the collection iterator")

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("More than one scene traversal")
        for obj in self.objects:
            self.visited.append(obj.session_uid)
            yield obj


class ResolutionTests(unittest.TestCase):
    def observe(self, objects, rows, found=None, resolve=handle):
        self.objects, self.handles = Objects(objects), []
        window = SimpleNamespace(scene=SimpleNamespace(objects=self.objects), view_layer=found)

        def observed(obj):
            self.handles.append(obj.session_uid)
            return resolve(obj)
        return scene_query_results.hidden_rows(window, rows, observed)

    def test_page_order_and_snapshot_rows_survive_one_scene_pass(self):
        objects = [thing(uid, eye=True) for uid in (8, 3, 7, 5, 11)]
        rows = [row(objects[3]), row(objects[1]), row(objects[0])]
        result = self.observe(objects, rows)
        self.assertEqual([entry["object"] for entry in result], [entry["object"] for entry in rows])
        self.assertTrue(all(entry["visibility"] == {"hidden": True} for entry in result))
        self.assertTrue(all("visibility" not in entry for entry in rows))
        self.assertEqual(self.objects.iterations, 1)
        self.assertEqual(self.objects.visited, [8, 3, 7, 5])
        self.assertEqual(self.handles, [8, 3, 5])

    def test_one_row_stops_immediately_without_handles_for_other_objects(self):
        objects = [thing(uid) for uid in range(1, 20)]
        entry = row(objects[0])
        result = self.observe(objects, [entry])
        self.assertIs(result[0], entry)
        self.assertEqual(self.objects.visited, [1])
        self.assertEqual(self.handles, [1])

    def test_empty_page_never_traverses_scene(self):
        self.assertEqual(self.observe([thing(1)], []), [])
        self.assertEqual(self.objects.iterations, 0)
        self.assertEqual(self.handles, [])

    def test_page_sizes_do_not_create_handles_for_unreturned_objects(self):
        objects = [thing(uid) for uid in range(180, 0, -1)]
        for size in (1, 7, 31, 100):
            with self.subTest(size=size):
                wanted = objects[-size:]
                rows = [row(obj) for obj in reversed(wanted)]
                self.assertEqual(self.observe(objects, rows), rows)
                self.assertEqual(self.objects.iterations, 1)
                self.assertEqual(set(self.handles), {obj.session_uid for obj in wanted})
                self.assertEqual(len(self.handles), size)

    def test_renamed_and_shortened_names_do_not_control_identity(self):
        obj = thing(7, "L" * 200, eye=True)
        entry = row(obj)
        obj.name = "Renamed"
        result = self.observe([obj], [entry])
        self.assertEqual(result[0]["name"], "L" * 128)
        self.assertEqual(result[0]["visibility"], {"hidden": True})

    def test_same_names_in_different_libraries_use_their_distinct_handles(self):
        first, second = thing(1, hide_render=True), thing(2, eye=True)
        first.library, second.library = "a.blend", "b.blend"
        result = self.observe([first, second], [row(second), row(first)])
        self.assertEqual([r["visibility"] for r in result], [{"hidden": True}, {"hide_render": True}])

    def test_missing_object_never_binds_a_same_named_replacement(self):
        old, replacement = thing(1), thing(2, eye=True)
        entry = row(old)
        result = self.observe([replacement], [entry])
        self.assertEqual(result, [entry])
        self.assertEqual(self.handles, [])

    def test_stale_epoch_with_same_uid_has_no_visibility_from_the_new_object(self):
        obj = thing(1, eye=True)
        entry = row(obj)
        obj.epoch = "new-document"
        self.assertEqual(self.observe([obj], [entry]), [entry])
        self.assertEqual(self.handles, [1])

    def test_current_and_stale_rows_sharing_a_uid_only_bind_the_exact_handle(self):
        obj = thing(1, eye=True)
        old = row(obj)
        obj.epoch = "new-document"
        current = row(obj)
        result = self.observe([obj], [old, current])
        self.assertIs(result[0], old)
        self.assertEqual(result[1]["visibility"], {"hidden": True})
        self.assertEqual(self.handles, [1])

    def test_a_missing_uid_requires_only_one_pass_and_no_unrelated_handles(self):
        objects = [thing(uid) for uid in range(1, 20)]
        rows = [row(objects[0]), row(thing(99))]
        self.assertEqual(self.observe(objects, rows), rows)
        self.assertEqual(self.objects.iterations, 1)
        self.assertEqual(self.objects.visited, list(range(1, 20)))
        self.assertEqual(self.handles, [1])

    def test_identity_revalidation_error_is_not_swallowed(self):
        obj = thing(1)
        def replaced(_obj):
            raise RuntimeError("ID replacement")
        with self.assertRaisesRegex(RuntimeError, "ID replacement"):
            self.observe([obj], [row(obj)], resolve=replaced)

    def test_repeated_row_is_resolved_once_and_keeps_order(self):
        obj = thing(1, eye=True)
        result = self.observe([obj], [row(obj), row(obj)])
        self.assertEqual(len(result), 2)
        self.assertEqual(self.handles, [1])
        self.assertEqual(result[0], result[1])

    def test_restricted_collection_facts_and_unrestricted_alternate_path_are_preserved(self):
        eye, monitor, free = Collection("Eye"), Collection("Monitor", hide_viewport=True), Collection("Free")
        found, _master = view_layer(layer(eye, eye=True), layer(monitor), layer(free))
        restricted = thing(1, collections=[eye, monitor])
        shown = thing(2, collections=[eye, monitor, free])
        result = self.observe([restricted, shown], [row(restricted), row(shown)], found)
        self.assertEqual(result[0]["visibility"], {"collection_hidden": ["Eye"],
                                                   "collection_hide_viewport": ["Monitor"]})
        self.assertNotIn("visibility", result[1])

    def test_visibility_is_fresh_on_each_query_not_cached(self):
        obj = thing(1)
        entry = row(obj)
        self.assertNotIn("visibility", self.observe([obj], [entry])[0])
        obj._eye = True
        self.assertEqual(self.observe([obj], [entry])[0]["visibility"], {"hidden": True})


if __name__ == "__main__":
    unittest.main()
