# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""visibility_set: schema, families, derived checks, the predicate and the commit journal.

Dependency-free, with RNA stand-ins. Staging, commit to the bound view layer,
Undo and staleness are covered by test_blender_visibility.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import action_visibility as visibility  # noqa: E402
from angelo_agent.action_catalog import resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.validation_spec import validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
SCENE = "scn:" + "a" * 32 + ":2"


def op(target=OBJ, **parameters):
    return {"kind": "visibility_set", "target": target, "parameters": parameters}


class Base:
    """An object stand-in with a view-layer hide state."""

    def __init__(self, name, hidden=False):
        self.name, self.hidden = name, hidden

    def hide_get(self, view_layer=None):
        return self.hidden

    def hide_set(self, value, view_layer=None):
        self.hidden = value


def layer(*names, objects=()):
    children = [SimpleNamespace(collection=SimpleNamespace(name=name, hide_viewport=False, hide_render=False,
                                                           hide_select=False),
                                exclude=False, hide_viewport=False, children=[]) for name in names]
    return SimpleNamespace(name="ViewLayer", layer_collection=SimpleNamespace(children=children),
                           objects={obj.name: obj for obj in objects})


class SchemaTests(unittest.TestCase):
    def test_deferred_and_narrowed(self):
        self.assertIn("visibility_set", DEFERRED_ACTIONS)
        self.assertIn("visibility", DEFERRED_CHECKS)
        self.assertEqual(resource_dependencies(op(hidden=True))["mutates"], ["object", "visibility"])
        self.assertEqual(resource_dependencies(op(SCENE, collection="Props", exclude=True))["mutates"], ["scene"])

    def test_refusals(self):
        for operation in (op(), op(hidden=None), op(hidden=True, hide_render=True), op(hide_render=True),
                          op(SCENE, collection="Props"), op(hidden="yes"), op(SCENE, collection="", exclude=True)):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)


class CheckTests(unittest.TestCase):
    def derived(self, *operations):
        return derive_checks([validate_operation(operation) for operation in operations])

    def test_derived_and_merged(self):
        self.assertEqual(self.derived(op(hidden=True)), [{"kind": "visibility", "target": OBJ, "hidden": True}])
        merged = self.derived(op(SCENE, collection="Props", exclude=True),
                              op(SCENE, collection="Props", hide_render=True, exclude=False),
                              op(SCENE, collection="Other", hidden=True))
        self.assertEqual(merged, [
            {"kind": "visibility", "target": SCENE, "collection": "Props", "exclude": False, "hide_render": True},
            {"kind": "visibility", "target": SCENE, "collection": "Other", "hidden": True}])
        for check in merged:
            self.assertEqual(validate_check(check), check)

    def test_check_shapes(self):
        for check in ({"kind": "visibility", "target": OBJ}, {"kind": "visibility", "target": OBJ, "exclude": True},
                      {"kind": "visibility", "target": SCENE, "collection": "Props"}):
            with self.subTest(check=check), self.assertRaises(ValueError):
                validate_check(check)

    def test_predicate_reads_intents_when_staged_and_the_view_layer_when_live(self):
        cube = Base("Cube", hidden=True)
        view_layer = layer("Props", objects=[cube])
        check = {"kind": "visibility", "target": OBJ, "hidden": True}
        self.assertEqual(visibility.evaluate(None, view_layer, cube, check), (True, {"hidden": True}))
        staged = {"objects": {OBJ: False}, "collections": {"Props": {"exclude": True}}}
        self.assertFalse(visibility.evaluate(staged, view_layer, cube, check)[0])
        props = {"kind": "visibility", "target": SCENE, "collection": "Props", "exclude": True, "hide_render": False}
        self.assertTrue(visibility.evaluate(staged, view_layer, None, props)[0])
        self.assertEqual(visibility.evaluate(None, view_layer, None, props)[1]["mismatched"], ["exclude"])
        missing = dict(props, collection="Missing")
        self.assertEqual(visibility.evaluate(None, view_layer, None, missing), (False, {"collection": "Missing",
                                                                                        "found": False}))
        outside = Base("Excluded")
        self.assertFalse(visibility.evaluate(None, view_layer, outside, check)[0])  # No Base in the view layer.


class CommitTests(unittest.TestCase):
    def test_intents_apply_with_a_journal_that_restores_everything(self):
        cube = Base("Cube")
        view_layer = layer("Props", "Lights", objects=[cube])
        extras = {"visibility": {"objects": {OBJ: True},
                                 "collections": {"Props": {"hidden": True, "exclude": True, "hide_render": True}}}}
        journal = []
        visibility.apply_intents(extras, {OBJ: cube}, view_layer, journal, RuntimeError)
        props = view_layer.layer_collection.children[0]
        self.assertTrue(cube.hidden and props.exclude and props.hide_viewport and props.collection.hide_render)
        # Exclusion edits first, in program order (an object hidden in a collection keeps its Base: staging checked).
        self.assertEqual([name for _, name, _ in journal], ["exclude", "hidden", "hide_viewport", "hide_render"])
        for holder, name, value in reversed(journal):
            setattr(holder, name, value)
        self.assertFalse(cube.hidden or props.exclude or props.hide_viewport or props.collection.hide_render)

    def test_missing_targets_raise_the_given_error(self):
        view_layer = layer("Props")
        with self.assertRaisesRegex(KeyError, "NOT_IN_VIEW_LAYER"):
            visibility.apply_intents({"visibility": {"objects": {OBJ: True}}}, {OBJ: Base("Cube")}, view_layer, [],
                                     KeyError)
        with self.assertRaisesRegex(KeyError, "COLLECTION_NOT_FOUND"):
            visibility.apply_intents({"visibility": {"collections": {"Gone": {"exclude": True}}}}, {}, view_layer, [],
                                     KeyError)

    def test_revision_rows_cover_nested_collections(self):
        view_layer = layer("Props")
        child = layer("Inner").layer_collection.children[0]
        view_layer.layer_collection.children[0].children = [child]
        rows = visibility.layer_rows(SimpleNamespace(view_layers=[view_layer]))
        self.assertEqual([row[1:3] for row in rows], [[1, "Props"], [2, "Inner"]])
        self.assertIs(visibility.layer_collection(view_layer, "Inner"), child)


class Item:
    """A LayerCollection stand-in with Blender's exclusion update (layer_collection_flag_recursive_set), including
    the hidden LAYER_COLLECTION_PREVIOUSLY_EXCLUDED flag RNA does not expose."""

    def __init__(self, name, exclude, linked=False, previously=False):
        self.collection = SimpleNamespace(name=name, hide_viewport=False, hide_render=False, hide_select=False,
                                          library=object() if linked else None, session_uid=id(self))
        self._exclude, self.previously, self.hide_viewport, self.children = exclude, previously, False, []

    @property
    def exclude(self):
        return self._exclude

    @exclude.setter
    def exclude(self, value):  # RNA writes the flag, then its update recurses with restore_flag false at the top.
        self._exclude = bool(value)
        self._recurse(bool(value), top=True)

    def _recurse(self, value, top):
        if value:
            self.previously = False if top else self._exclude
            self._exclude = True
        elif not self.previously:
            self._exclude = False
        for child in self.children:
            child._recurse(value, top=False)


def tree(*rows):
    """A view layer whose LayerCollections follow (depth, name, exclude[, linked[, previously]]) rows."""
    root, stack = SimpleNamespace(children=[]), []
    for depth, name, exclude, *rest in rows:
        item = Item(name, exclude, bool(rest and rest[0]), bool(len(rest) > 1 and rest[1]))
        del stack[depth - 1:]
        (stack[-1].children if stack else root.children).append(item)
        stack.append(item)
    return SimpleNamespace(name="ViewLayer", layer_collection=root, objects={})


def known_rows(view_layer):
    """The rows the native read gives: the stand-in's hidden flag is known."""
    items, pending = [], [(item, 1) for item in view_layer.layer_collection.children]
    while pending:
        item, depth = pending.pop(0)
        items.append([depth, item.collection.name, item.exclude, item.collection.library is not None, item.previously])
        pending[:0] = [(child, depth + 1) for child in item.children]
    return items


class Layered(dict):
    """View-layer objects with a Base only while a collection holding them is included (Blender's rule)."""

    def __init__(self, view_layer, members):
        super().__init__()
        self.view_layer, self.members = view_layer, members

    def get(self, name, default=None):
        included = visibility.included(known_rows(self.view_layer))
        return self.members[name][0] if any(each in included for each in self.members[name][1]) else default


class ExclusionTests(unittest.TestCase):
    """Blender's rule: exclusion is each layer collection's own flag; an edit recurses into
    the descendants, and a descendant excluded before an excluded ancestor stays excluded when that ancestor is
    included again (the hidden "previously excluded" flag)."""

    def test_simulation_matches_blenders_recursion_on_random_trees_and_edits(self):
        import random
        chooser = random.Random(20261015)
        for trial in range(400):
            rows, depth = [], 0
            for index in range(chooser.randint(1, 9)):
                depth = chooser.randint(1, min(depth + 1, 4)) if rows else 1
                rows.append((depth, "C%d" % index, chooser.random() < 0.4, False, chooser.random() < 0.3))
            view_layer = tree(*rows)
            order = [["C%d" % chooser.randrange(len(rows)), chooser.random() < 0.5] for _ in range(chooser.randint(1, 5))]
            start = known_rows(view_layer)
            for name, value in order:  # Commit's own rule: an unchanged flag is never written.
                item = visibility.layer_collection(view_layer, name)
                if bool(item.exclude) != bool(value):
                    item.exclude = value
            live = known_rows(view_layer)
            excluded, previously = visibility.simulate(start, order)
            with self.subTest(trial=trial, rows=rows, order=order):
                self.assertEqual(excluded, [row[2] for row in live])
                self.assertEqual(previously, [row[4] for row in live])

    def test_a_child_excluded_before_its_parent_stays_excluded(self):
        rows = [[1, "Parent", True, False, False], [2, "Child", True, False, True], [2, "Other", True, False, False]]
        self.assertEqual(visibility.included(rows, [["Parent", False]]), {"Scene Collection", "Parent", "Other"})

    def test_an_unknown_hidden_flag_counts_as_excluded(self):
        rows = [[1, "Parent", True, False, None], [2, "Child", True, False, None], [2, "Seen", False, False, None]]
        self.assertEqual(visibility.simulate(rows, [["Parent", False]])[0], [False, None, False])
        self.assertEqual(visibility.included(rows, [["Parent", False]]), {"Scene Collection", "Parent", "Seen"})
        extras = {visibility.LAYER_TREE: rows, "visibility": {"objects": {}, "collections": {"Parent": {"exclude": False}},
                                                             visibility.ORDER: [["Parent", False]]}}
        self.assertEqual(visibility.effective_collections(extras), {"Parent": {"exclude": False},
                                                                    "Child": {"exclude": None}})

    def test_an_unchanged_flag_is_never_written(self):
        rows = [[1, "Parent", True, False, False], [2, "Child", False, False, False]]  # Child included again.
        self.assertEqual(visibility.simulate(rows, [["Parent", True]]), ([True, False], [False, False]))

    def test_a_local_collection_is_never_stood_for_by_a_library_one(self):
        rows = [[1, "Props", False, True, False], [1, "Props", True, False, False], [1, "Rocks", False, True, False]]
        self.assertEqual(visibility.included(rows), {"Scene Collection", "Rocks"})
        self.assertEqual(visibility.included(rows, [["Props", False]]), {"Scene Collection", "Props", "Rocks"})

    def test_a_big_tree_is_still_known(self):
        rows = [(1, "C%d" % index, False) for index in range(visibility.MAX_LAYER_ROWS + 1)]
        self.assertEqual(len(visibility.layer_tree(tree(*rows))), visibility.MAX_LAYER_ROWS + 1)

    def test_has_base_reads_memberships_program_order_and_created_collections(self):
        rows = [[1, "Off", True, False, False], [1, "Home", False, False, False]]
        extras = {visibility.LAYER_TREE: rows, "created_collections": ["New"]}
        self.assertTrue(visibility.has_base(extras, ["Home"]))
        self.assertFalse(visibility.has_base(extras, ["Off"]))
        self.assertTrue(visibility.has_base(extras, ["New"]))
        extras["visibility"] = {"objects": {}, "collections": {}, visibility.ORDER: [["Off", False], ["Home", True]]}
        self.assertTrue(visibility.has_base(extras, ["Off"]))
        self.assertFalse(visibility.has_base(extras, ["Home"]))
        self.assertIsNone(visibility.has_base({}, ["Home"]))  # Not recorded: unknown.

    def test_commit_applies_exclusions_in_program_order_before_hiding(self):
        cube = Base("Cube")
        view_layer = tree((1, "Parent", True), (2, "Child", True))
        view_layer.objects = Layered(view_layer, {"Cube": (cube, ["Child"])})
        extras = {"visibility": {"objects": {OBJ: True}, "collections": {"Parent": {"exclude": False, "hide_render": True}},
                                 visibility.ORDER: [["Parent", False]]},
                  visibility.LAYER_TREE: visibility.layer_tree(view_layer)}
        journal = []
        visibility.apply_intents(extras, {OBJ: cube}, view_layer, journal, RuntimeError)
        parent = view_layer.layer_collection.children[0]
        self.assertTrue(cube.hidden and not parent.exclude and not parent.children[0].exclude)
        # Without the native write only the edited flag is journaled (as an earlier build did).
        self.assertEqual([name for _, name, _ in journal], ["exclude", "hidden", "hide_render"])

    def test_live_flags_that_differ_from_the_prediction_are_refused_correctably(self):
        from unittest.mock import patch
        from angelo_agent import view_layer_exclusion
        view_layer = tree((1, "Parent", True), (2, "Child", True))
        lying = lambda layer: [(1, "Parent", 1, False, 1), (2, "Child", 3, False, 2)]  # Child "excluded before".
        extras = {"visibility": {"objects": {}, "collections": {"Parent": {"exclude": False}},
                                 visibility.ORDER: [["Parent", False]]}}
        with patch.object(view_layer_exclusion, "native_rows", lying):
            extras[visibility.LAYER_TREE] = visibility.layer_tree(view_layer)
            with self.assertRaisesRegex(ConnectionError, "COMMIT_FIDELITY.*exclusions"):
                visibility.apply_intents(extras, {}, view_layer, [], KeyError, refused=ConnectionError)
        view_layer = tree((1, "Parent", True), (2, "Child", True))
        extras[visibility.LAYER_TREE] = visibility.layer_tree(view_layer)  # Unknown hidden flag: no claim on Child.
        visibility.apply_intents(extras, {}, view_layer, [], KeyError, refused=ConnectionError)


    def test_a_view_layer_changed_after_staging_is_stale_and_anything_else_an_internal_fault(self):
        cube = Base("Cube")
        view_layer = tree((1, "Home", False))
        view_layer.objects = Layered(view_layer, {"Cube": (cube, ["Home"])})
        extras = {"visibility": {"objects": {OBJ: True}}, visibility.LAYER_TREE: visibility.layer_tree(view_layer)}
        view_layer.layer_collection.children[0].exclude = True  # The artist, while the job waited.
        with self.assertRaisesRegex(ConnectionError, "NOT_IN_VIEW_LAYER.*changed"):
            visibility.apply_intents(extras, {OBJ: cube}, view_layer, [], KeyError, stale=ConnectionError)
        extras[visibility.LAYER_TREE] = visibility.layer_tree(view_layer)  # Unchanged since staging: a fault.
        with self.assertRaisesRegex(KeyError, "NOT_IN_VIEW_LAYER"):
            visibility.apply_intents(extras, {OBJ: cube}, view_layer, [], KeyError, stale=ConnectionError)
        with self.assertRaisesRegex(ConnectionError, "COLLECTION_NOT_FOUND"):
            visibility.apply_intents({"visibility": {"collections": {"Gone": {"exclude": True}}}}, {}, view_layer, [],
                                     KeyError, stale=ConnectionError)

    def test_staged_checks_read_the_exclusion_the_program_ends_with(self):
        rows = [[1, "Parent", False, False, False], [2, "Child", False, False, False]]
        extras = {visibility.LAYER_TREE: rows, "visibility": {"objects": {}, "collections": {
            "Child": {"exclude": False}, "Parent": {"exclude": True}},
            visibility.ORDER: [["Child", False], ["Parent", True]]}}
        self.assertEqual(visibility.effective_collections(extras),
                         {"Child": {"exclude": True}, "Parent": {"exclude": True}})

    def test_effective_collections_is_linear_in_the_rows(self):
        import time
        rows = [[1, "Off", False, False, False]] + [[1, "C%05d" % index, False, False, False] for index in range(20000)]
        extras = {visibility.LAYER_TREE: rows, "visibility": {"objects": {}, "collections": {"Off": {"exclude": True}},
                                                             visibility.ORDER: [["Off", True]]}}
        start = time.perf_counter()
        visibility.effective_collections(extras)
        visibility.has_base(extras, ["C19999"])
        self.assertLess(time.perf_counter() - start, 0.5)  # Quadratic, 20,000 rows took minutes.


if __name__ == "__main__":
    unittest.main()
