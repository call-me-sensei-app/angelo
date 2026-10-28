# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How an object is hidden, stated beside every reader's facts (``visibility_facts``; the maintainer's ruling of
2026-10-25 on an instancer in a collection hidden by its eye: it exists, so the agent is told how it is hidden).

These pin the rule over stand-ins for a view layer's collection tree: one fact per kind of hiding, each only when it
takes effect as Blender decides it (``layer.cc`` ``layer_collection_sync``: an eye, a Disable in Viewports and a
Disable in Renders hold for the collections inside, an object shows through any collection free of one, exclusion is
each layer collection's own), nothing for a shown object, and that every reader states them through the one helper.
The held-out cases in Blender check the real flags.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import object_bounds, scene_query_results, visibility_facts  # noqa: E402

ADDON = ROOT / "scripts/addons_core/angelo_agent"


class Collection(SimpleNamespace):
    def __init__(self, name, hide_viewport=False, hide_render=False, library=None):
        super().__init__(name=name, hide_viewport=hide_viewport, hide_render=hide_render, library=library)

    def as_pointer(self):
        return id(self)


def layer(collection, *children, exclude=False, eye=False):
    return SimpleNamespace(collection=collection, children=list(children), exclude=exclude, hide_viewport=eye)


class Thing(SimpleNamespace):
    def __init__(self, collections, eye=False, hide_viewport=False, hide_render=False):
        super().__init__(users_collection=list(collections), hide_viewport=hide_viewport, hide_render=hide_render,
                         _eye=eye, name="Thing")

    def hide_get(self, view_layer=None):
        return self._eye


def view_layer(*children):
    master = Collection("Scene Collection")
    return SimpleNamespace(layer_collection=layer(master, *children)), master


class Rule(unittest.TestCase):
    def test_a_shown_object_states_nothing(self):
        props = Collection("Props")
        found, master = view_layer(layer(props))
        facts = visibility_facts.Pass(found)
        self.assertIsNone(facts.of(Thing([props])))
        self.assertIsNone(facts.of(Thing([master])))
        self.assertEqual(facts.entry(Thing([props])), {})

    def test_each_of_its_own_settings_is_its_own_fact(self):
        found, master = view_layer()
        facts = visibility_facts.Pass(found)
        self.assertEqual(facts.of(Thing([master], eye=True)), {"hidden": True})
        self.assertEqual(facts.of(Thing([master], hide_viewport=True)), {"hide_viewport": True})
        self.assertEqual(facts.of(Thing([master], hide_render=True)), {"hide_render": True})
        self.assertEqual(facts.of(Thing([master], eye=True, hide_render=True)), {"hidden": True, "hide_render": True})

    def test_a_collection_setting_names_the_collection_that_carries_it_on_every_path(self):
        parent, child = Collection("Room"), Collection("Lamps")
        for key, tree in (("collection_hidden", layer(parent, layer(child), eye=True)),
                          ("collection_hide_viewport", layer(Collection("Room", hide_viewport=True), layer(child))),
                          ("collection_hide_render", layer(Collection("Room", hide_render=True), layer(child)))):
            found, _master = view_layer(tree)
            self.assertEqual(visibility_facts.Pass(found).of(Thing([child])), {key: ["Room"]}, key)

    def test_an_object_shows_through_any_collection_free_of_a_setting(self):
        hidden, shown = Collection("Hidden"), Collection("Shown")
        found, _master = view_layer(layer(hidden, eye=True), layer(shown))
        self.assertIsNone(visibility_facts.Pass(found).of(Thing([hidden, shown])))
        self.assertEqual(visibility_facts.Pass(found).of(Thing([hidden])), {"collection_hidden": ["Hidden"]})

    def test_exclusion_is_each_collections_own_and_stated_only_when_every_one_is_excluded(self):
        parent, child, other = Collection("Set"), Collection("Props"), Collection("Other")
        found, _master = view_layer(layer(parent, layer(child), exclude=True), layer(other, exclude=True))
        facts = visibility_facts.Pass(found)
        self.assertIsNone(facts.of(Thing([child])))   # Its own layer collection is included.
        self.assertEqual(facts.of(Thing([parent, other])), {"collection_exclude": ["Set", "Other"]})
        self.assertIsNone(facts.of(Thing([parent, child])))

    def test_kinds_are_never_folded_and_each_holds_on_its_own(self):
        both = Collection("Both", hide_render=True)
        found, _master = view_layer(layer(both, eye=True))
        self.assertEqual(visibility_facts.Pass(found).of(Thing([both], eye=True)),
                         {"hidden": True, "collection_hidden": ["Both"], "collection_hide_render": ["Both"]})

    def test_a_path_hidden_by_an_eye_and_another_disabled_in_viewports_hide_it(self):
        """The viewport shows an object through a path free of both (REVIEW-HIDDEN-FLAGS-2 M1)."""
        eyed, monitored, free = Collection("Eyed"), Collection("Off", hide_viewport=True), Collection("Free")
        found, _master = view_layer(layer(eyed, eye=True), layer(monitored), layer(free, eye=False))
        facts = visibility_facts.Pass(found)
        self.assertEqual(facts.of(Thing([eyed, monitored])),
                         {"collection_hidden": ["Eyed"], "collection_hide_viewport": ["Off"]})
        self.assertIsNone(facts.of(Thing([eyed, monitored, free])), "shown through the free one")
        nested = Collection("Inner", hide_render=True)
        found, _master = view_layer(layer(Collection("Outer", hide_viewport=True), layer(nested)),
                                    layer(free, eye=True))
        self.assertEqual(visibility_facts.Pass(found).of(Thing([nested, free])),
                         {"collection_hidden": ["Free"], "collection_hide_viewport": ["Outer"]},
                         "renders through the free path: no render fact")

    def test_names_are_bounded(self):
        names = [Collection("C%d" % index) for index in range(7)]
        found, _master = view_layer(*(layer(c, eye=True) for c in names))
        facts = visibility_facts.Pass(found).of(Thing(names))
        self.assertEqual(facts, {"collection_hidden": ["C0", "C1", "C2", "C3", "+3 more"]})

    def test_a_stand_in_is_never_read_as_hidden_and_an_unreadable_tree_states_no_collection_fact(self):
        self.assertIsNone(visibility_facts.Pass(MagicMock()).of(MagicMock()))
        self.assertIsNone(visibility_facts.Pass(None).of(Thing([])))

    def test_the_tree_is_walked_once_per_pass(self):
        props = Collection("Props")
        found, _master = view_layer(layer(props, eye=True))
        facts = visibility_facts.Pass(found)
        facts.of(Thing([props]))
        found.layer_collection = None   # A second walk would raise and state nothing.
        self.assertEqual(facts.of(Thing([props])), {"collection_hidden": ["Props"]})

    def test_a_shown_object_reads_no_collection_unless_one_is_left_out_of_renders(self):
        class Shown(Thing):
            def visible_get(self, view_layer=None):
                return True

            @property
            def users_collection(self):
                raise AssertionError("its collections were read")

            @users_collection.setter
            def users_collection(self, value):
                pass
        props = Collection("Props")
        found, master = view_layer(layer(props))
        master.children_recursive = [props]
        self.assertIsNone(visibility_facts.Pass(found).of(Shown([props])))
        self.assertEqual(visibility_facts.Pass(found).of(Shown([props], hide_render=True)), {"hide_render": True})
        props.hide_render = True   # Now a collection setting can apply to a shown object: its collections are read.
        with self.assertRaises(AssertionError):
            visibility_facts.Pass(found).of(Shown([props]))

    def test_a_programs_edits_and_memberships_are_the_state_its_commit_gives(self):
        props, lamps = Collection("Props"), Collection("Lamps")
        found, _master = view_layer(layer(props), layer(lamps, eye=True))
        edits = {"Props": {"hidden": True}, "Lamps": {"hidden": False, "hide_render": True}}
        facts = visibility_facts.Pass(found, edits)
        self.assertEqual(facts.of(Thing([]), collections=["Props"], hidden=False), {"collection_hidden": ["Props"]})
        self.assertEqual(facts.of(Thing([]), collections=["Lamps"], hidden=True),
                         {"hidden": True, "collection_hide_render": ["Lamps"]})
        self.assertIsNone(facts.of(Thing([]), collections=["Scene Collection"], hidden=False))


class Readers(unittest.TestCase):
    def test_a_scene_detail_states_how_it_is_hidden_beside_its_instances(self):
        props = Collection("Props")
        found, _master = view_layer(layer(props, eye=True))
        found.depsgraph = None
        window = SimpleNamespace(view_layer=found)
        self.assertEqual(scene_query_results.instance_detail(window, Thing([props])),
                         {"visibility": {"collection_hidden": ["Props"]}})
        self.assertEqual(scene_query_results.instance_detail(window, Thing([_master])), {})

    def test_a_search_page_row_gains_the_facts_only_for_a_hidden_object(self):
        props = Collection("Props")
        found, master = view_layer(layer(props, eye=True))
        hidden, shown = Thing([props]), Thing([master])
        hidden.name, shown.name = "Hidden", "Shown"
        hidden.session_uid, shown.session_uid = 1, 2
        objects = [hidden, shown]
        window = SimpleNamespace(view_layer=found, scene=SimpleNamespace(objects=objects))
        rows = [{"object": "scn:test:1", "name": "Hidden"}, {"object": "scn:test:2", "name": "Shown"}]
        shown_rows = scene_query_results.hidden_rows(window, rows, lambda obj: "scn:test:%d" % obj.session_uid)
        self.assertEqual(shown_rows[0]["visibility"], {"collection_hidden": ["Props"]})
        self.assertNotIn("visibility", shown_rows[1])
        self.assertNotIn("visibility", rows[0])   # The snapshot's own rows are never changed.

    def test_a_row_whose_name_was_shortened_is_found_by_its_handle(self):
        props = Collection("Props")
        found, _master = view_layer(layer(props, eye=True))
        long = Thing([props])
        long.name = "L" * 200
        long.session_uid = 3

        class Objects(dict):   # As ``Scene.objects``: looked up by name, iterated as objects.
            def __iter__(self):
                return iter(self.values())
        window = SimpleNamespace(view_layer=found, scene=SimpleNamespace(objects=Objects({long.name: long})))
        rows = [{"object": "scn:test:3", "name": long.name[:128]}]
        shown_rows = scene_query_results.hidden_rows(window, rows, lambda obj: "scn:test:%d" % obj.session_uid)
        self.assertEqual(shown_rows[0]["visibility"], {"collection_hidden": ["Props"]})

    def test_every_reader_states_them_through_the_one_helper(self):
        """The per-turn facts, the scene reads, a script's after rows, a program's geometry rows and kit.bounds."""
        readers = {"turn_context.py": "Pass", "scene_query_results.py": "Pass", "script_after.py": "Pass",
                   "program_verification.py": "staged", "script_kit_support.py": "Pass"}
        for name, helper in readers.items():
            tree = ast.parse((ADDON / name).read_text(encoding="utf-8"))
            used = {node.attr if isinstance(node, ast.Attribute) else node.id for node in ast.walk(tree)
                    if isinstance(node, (ast.Attribute, ast.Name))}
            self.assertIn(helper, used, name)
        self.assertIn("support.no_bounds(", (ADDON / "script_kit.py").read_text(encoding="utf-8"))

    def test_facts_only(self):
        """No instruction to the model: keys and collection names, never a sentence."""
        found, master = view_layer()
        facts = visibility_facts.Pass(found).of(Thing([master], eye=True, hide_viewport=True, hide_render=True))
        self.assertTrue(all(value is True for value in facts.values()))


class HiddenInstancer(unittest.TestCase):
    """An instancer the native pass does not expand because it is hidden is never stated as making none, so never
    sized by its data's box (REVIEW-HIDDEN-FLAGS-2 M2)."""

    def thing(self, shown=False, evaluated=True, instances=True, mesh=True, instance_type="NONE", modifiers=()):
        geometry = SimpleNamespace(instances_pointcloud=lambda: object() if instances else None,
                                   mesh=SimpleNamespace(vertices=[0] * (3 if mesh else 0)))
        evaluated_object = SimpleNamespace(is_evaluated=evaluated, evaluated_geometry=lambda: geometry)
        return SimpleNamespace(visible_get=lambda view_layer=None: shown, evaluated_get=lambda depsgraph:
                               evaluated_object, instance_type=instance_type,
                               modifiers=[SimpleNamespace(type=kind) for kind in modifiers])

    def fact(self, obj):
        return object_bounds._hidden_fact(obj, SimpleNamespace(view_layer=None))

    def test_shown_the_native_pass_decides(self):
        self.assertIsNone(self.fact(self.thing(shown=True)))

    def test_hidden_and_evaluated_its_geometry_decides(self):
        self.assertIsNone(self.fact(self.thing(instances=False)), "it makes none")
        self.assertEqual(self.fact(self.thing()), dict(object_bounds.HIDDEN, own_geometry=True))
        self.assertEqual(self.fact(self.thing(mesh=False)), dict(object_bounds.HIDDEN, own_geometry=False),
                         "a mesh object's set always holds a mesh: an empty one is none of its own")
        self.assertFalse(object_bounds.states_own_box(self.fact(self.thing(mesh=False))))

    def test_not_evaluated_or_an_instancing_setting_is_unknown_without_its_own_size(self):
        for obj in (self.thing(evaluated=False), self.thing(instance_type="COLLECTION"),
                    self.thing(modifiers=("PARTICLE_SYSTEM",))):
            self.assertEqual(self.fact(obj), object_bounds.HIDDEN)
            self.assertFalse(object_bounds.states_own_box(self.fact(obj)))


class RenderDisabledCheckTests(unittest.TestCase):
    """``_disabled_in_renders``: one bulk read of every collection's Disable in Renders comes before any tree walk
    (``children_recursive`` rebuilt the whole tree on every single-object read: deep_collections ratchet, 1.0.6)."""

    def fake_bpy(self, flags):
        prop_collection = type("bpy_prop_collection", (), {})

        class Collections(prop_collection):
            def __len__(self):
                return len(flags)

            def foreach_get(self, name, buffer):
                assert name == "hide_render"
                buffer[:] = type(buffer)("b", flags)
        return SimpleNamespace(types=SimpleNamespace(bpy_prop_collection=prop_collection),
                               data=SimpleNamespace(collections=Collections()))

    class Master:
        def __init__(self, children):
            self._children = children

        @property
        def children_recursive(self):
            if self._children is None:
                raise AssertionError("the collection tree was walked")
            return self._children

    def check(self, flags, children):
        from unittest import mock
        with mock.patch.dict(sys.modules, {"bpy": self.fake_bpy(flags)}):
            return visibility_facts._disabled_in_renders(self.Master(children))

    def test_none_set_anywhere_needs_no_walk(self):
        self.assertFalse(self.check([0, 0, 0], None))

    def test_a_set_flag_lets_the_scenes_own_tree_decide(self):
        elsewhere = SimpleNamespace(hide_render=False)   # The set collection belongs to another scene.
        self.assertFalse(self.check([0, 1, 0], [elsewhere]))
        self.assertTrue(self.check([0, 1, 0], [elsewhere, SimpleNamespace(hide_render=True)]))

    def test_a_stand_in_without_blender_collections_walks(self):
        from unittest import mock
        with mock.patch.dict(sys.modules, {"bpy": MagicMock()}):
            self.assertTrue(visibility_facts._disabled_in_renders(self.Master([SimpleNamespace(hide_render=True)])))


if __name__ == "__main__":
    unittest.main()
