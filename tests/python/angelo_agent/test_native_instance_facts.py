# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Instance facts (``object_bounds.InstanceFacts``): what an object's instances show, stated by every size reader.

An object's own box leaves its instances out, so a Geometry Nodes instancer read 0 and a curve leaving only instances
its control points padded by their radius (bounds audit and REVIEW-BOUNDS-2 M1, 2026-10-25). These pin the plumbing
over a stand-in for the native pass (``_bpy._angelo_live.instance_bounds``): the facts, when the pass runs, and what
each reader states. test_blender_object_bounds.py checks the real geometry in Blender.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import object_bounds, program_model_result, script_kit_support, turn_context  # noqa: E402

GRID = ((12.4, -0.6, -0.05), (13.6, 0.6, 0.05), False)   # Nine cubes, no geometry of the instancer's own.


def native(found):
    """A stand-in ``_bpy`` whose ``_angelo_live.instance_bounds`` returns ``found`` and counts its calls."""
    calls = []
    module = ModuleType("_bpy")
    def instance_bounds(depsgraph, objects, limit):   # As the native pass: only the objects asked about.
        uids = [o.session_uid for o in objects]
        calls.append((depsgraph, uids, limit))
        return {uid: value for uid, value in found.items() if uid in uids} if found is not None else None
    module._angelo_live = SimpleNamespace(instance_bounds=instance_bounds)
    return patch.dict(sys.modules, {"_bpy": module}), calls


def thing(uid, kind="MESH", modifiers=(), instance_type="NONE", evaluated=True, dimensions=(1.0, 2.0, 3.0)):
    obj = SimpleNamespace(session_uid=uid, type=kind, instance_type=instance_type, dimensions=dimensions,
                          modifiers=[SimpleNamespace(type=t, name=t) for t in modifiers], scale=(1.0, 1.0, 1.0),
                          rotation_mode="XYZ", rotation_euler=(0.0, 0.0, 0.0))
    obj.evaluated_get = lambda depsgraph: SimpleNamespace(is_evaluated=evaluated)
    return obj


class InstanceFactTests(unittest.TestCase):
    def test_the_facts_state_the_world_box_and_no_own_geometry(self):
        stand_in, _calls = native({7: GRID, 8: ((0, 0, 0), (1, 1, 1), True), 9: (None, False)})
        with stand_in:
            facts = object_bounds.instance_facts("graph", [thing(7), thing(8), thing(9)])
        self.assertEqual(facts[7], {"bounds": [[12.4, -0.6, -0.05], [13.6, 0.6, 0.05]], "own_geometry": False})
        self.assertNotIn("own_geometry", facts[8])   # Stated only when false.
        self.assertEqual(facts[9], dict(object_bounds.TOO_MANY, own_geometry=False))   # Past the limit: that object.

    def test_an_instance_drawing_hair_is_unknown_never_makes_none(self):
        """REVIEW-INSTANCES-LATENCY L-r2: strands are no dupli; a hair patch's instance read as making none."""
        stand_in, _calls = native({7: (None, True, "hair")})
        with stand_in:
            facts = object_bounds.instance_facts("graph", [thing(7)])
        self.assertEqual(facts[7], dict(object_bounds.HAIR, own_geometry=True))

    def test_no_facts_from_a_depsgraph_the_native_pass_refuses(self):
        stand_in, _calls = native(None)   # Not fully evaluated since its last change.
        with stand_in:
            self.assertIsNone(object_bounds.instance_facts("graph", [thing(7)]))
        self.assertIs(object_bounds.instance_facts("graph", [thing(7)]), False)   # No native reader at all.

    def test_the_pass_runs_once_and_only_for_an_object_that_can_make_instances(self):
        stand_in, calls = native({7: GRID})
        with stand_in:
            facts = object_bounds.InstanceFacts("graph")
            self.assertIsNone(facts.of(thing(7)))   # A plain mesh never asks.
            self.assertEqual(calls, [])
            self.assertIn("bounds", facts.of(thing(7, modifiers=["NODES"])))
            self.assertIsNone(facts.of(thing(8, modifiers=["NODES"])))   # Its nodes make none.
            self.assertEqual(facts.of(thing(7, kind="EMPTY", instance_type="COLLECTION"))["own_geometry"], False)
            # Each object asked about expanded alone, once (the EMPTY is uid 7 again: remembered, not re-read).
            self.assertEqual(calls, [("graph", [7], object_bounds.LIMIT), ("graph", [8], object_bounds.LIMIT)])
        self.assertTrue(object_bounds.makes_instances(thing(1, modifiers=["PARTICLE_SYSTEM"])))
        self.assertTrue(object_bounds.makes_instances(thing(1, instance_type="VERTS")))
        self.assertIsNone(object_bounds.InstanceFacts(None).of(thing(7, modifiers=["NODES"])))

    def test_unreadable_facts_state_no_size_never_the_data_box(self):
        """REVIEW-INSTANCES M1: a depsgraph not evaluated since its change, or past LIMIT: the reader states it does
        not know, and no own size (a curve's would be its control points padded by their radius)."""
        stand_in, _calls = native(None)
        with stand_in:
            facts = object_bounds.InstanceFacts("graph")
            entry = {}
            turn_context._shape(thing(7, kind="CURVE", modifiers=["NODES"], dimensions=(4.0, 2.0, 2.0)), entry, facts)
            found = []
            with self.assertRaises(ValueError):   # Never a box short of what it shows (REVIEW-INSTANCES-4 M-A).
                script_kit_support.add_instances(thing(7, modifiers=["NODES"]), facts, found)
        self.assertNotIn("dimensions", entry)
        self.assertIn("unknown", entry["instances"])
        self.assertEqual(found, [])
        row = program_model_result._geometry({"name": "Row", "bounds": [[-1, -1, -1], [3, 1, 1]],
                                              "instances": dict(object_bounds.NOT_EVALUATED)})
        self.assertNotIn("bounds", row)

    def test_past_a_limit_a_reader_states_unknown_and_keeps_only_an_own_size_it_knows(self):
        """REVIEW-INSTANCES-3 L-A: an object past the limit keeps its own size when the pass saw its own geometry;
        another object is read alone, so a big scatter never makes it unknown."""
        stand_in, _calls = native({7: (None, True), 8: (None, False), 9: GRID})
        with stand_in:
            facts = object_bounds.InstanceFacts("graph")
            terrain, row, unreached = {}, {}, {}
            turn_context._shape(thing(7, modifiers=["NODES"]), terrain, facts)
            turn_context._shape(thing(8, kind="CURVE", modifiers=["NODES"], dimensions=(4.0, 2.0, 2.0)), row, facts)
            turn_context._shape(thing(9, modifiers=["NODES"]), unreached, facts)   # Read alone: its fact stands.
            finished = {}
            turn_context._shape(thing(10, modifiers=["NODES"]), finished, facts)   # Its nodes make none.
        self.assertEqual(terrain["dimensions"], [1.0, 2.0, 3.0])   # Its own geometry: its size stands.
        self.assertIn("unknown", terrain["instances"])
        self.assertNotIn("dimensions", row)
        self.assertIn("bounds", unreached["instances"])
        self.assertEqual(finished, {"dimensions": [1.0, 2.0, 3.0], "modifiers": {"NODES": "NODES"}})

    def test_kit_bounds_reads_with_no_limit_an_object(self):
        stand_in, calls = native({7: GRID})
        with stand_in:
            script_kit_support.add_instances(thing(7, modifiers=["NODES"]), script_kit_support.instance_facts("g"), [])
        self.assertEqual(calls, [("g", [7], 0)])   # No limit: a script asked for this box.

    def test_no_reader_in_the_build_says_so(self):
        entry = {}
        turn_context._shape(thing(7, modifiers=["NODES"]), entry, object_bounds.InstanceFacts("graph"))
        self.assertEqual(entry["instances"], object_bounds.UNAVAILABLE)
        self.assertNotIn("dimensions", entry)

    def test_a_fact_is_kept_for_the_evaluation_it_was_read_from(self):
        """The coordinator's latency rule: a read of the same instancer after nothing changed pays nothing; a new
        evaluation (an edit, a frame change, Undo, Redo, a load, Restore) gives a new update count, so nothing kept
        from an earlier one is served, and a depsgraph not evaluated since its change has none and is never served."""
        stand_in, calls = native({7: GRID})
        obj = thing(7, modifiers=["NODES"])
        counts = [41]
        with stand_in, patch.dict(object_bounds._cache, clear=True), \
                patch.object(object_bounds, "_update_count", lambda depsgraph: counts[0]):
            first = object_bounds.InstanceFacts("graph").of(obj)
            again = object_bounds.InstanceFacts("graph").of(obj)   # A later read pass, nothing evaluated since.
            self.assertIs(again, first)
            self.assertEqual([c[1] for c in calls], [[7]])
            counts[0] = 42   # Evaluated again: a new count.
            object_bounds.InstanceFacts("graph").of(obj)
            self.assertEqual([c[1] for c in calls], [[7], [7]])
            counts[0] = None   # Tagged, not evaluated yet: nothing kept is served, nothing is kept.
            object_bounds.InstanceFacts("graph").of(obj)
            self.assertEqual([c[1] for c in calls], [[7], [7], [7]])
            self.assertEqual(len(object_bounds._cache), 2)

    def test_a_kept_fact_is_never_served_from_a_depsgraph_not_evaluated_since_its_change(self):
        stand_in, _calls = native({7: GRID})
        obj = thing(7, modifiers=["NODES"])
        with stand_in, patch.dict(object_bounds._cache, clear=True), \
                patch.object(object_bounds, "_update_count", lambda depsgraph: 41):
            object_bounds.InstanceFacts("graph").of(obj)
        dirty, _calls = native(None)   # An edit tagged it; nothing evaluated yet.
        with dirty, patch.object(object_bounds, "_update_count", lambda depsgraph: None):
            self.assertEqual(object_bounds.InstanceFacts("graph").of(obj), object_bounds.NOT_EVALUATED)
        object_bounds._cache.clear()

    def test_the_cache_watches_no_handler(self):
        """REVIEW-INSTANCES-LATENCY M2: a script that clears a handler list can never leave a kept fact stale."""
        init = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/__init__.py").read_text()
        self.assertNotIn("object_bounds.register()", init)
        source = Path(object_bounds.__file__).read_text(encoding="utf-8")
        self.assertNotIn("bpy.app.handlers", source)
        self.assertIn("_angelo_live.update_count(depsgraph)", source)

    def test_a_curve_no_depsgraph_evaluates_has_no_known_box(self):
        self.assertFalse(object_bounds.own_box_known(thing(1, kind="CURVE", evaluated=False), "graph"))
        self.assertTrue(object_bounds.own_box_known(thing(1, kind="CURVE"), "graph"))
        self.assertTrue(object_bounds.own_box_known(thing(1, kind="MESH", evaluated=False), "graph"))
        self.assertIsNone(object_bounds.world_bounds(thing(1, kind="FONT", evaluated=False), depsgraph="graph"))

    def test_per_turn_facts_state_instances_and_never_a_box_the_object_does_not_have(self):
        stand_in, _calls = native({7: GRID, 9: ((0, 0, 0), (2, 2, 2), True)})
        with stand_in:
            facts = object_bounds.InstanceFacts("graph")
            only, both, hidden, plain = {}, {}, {}, {}
            turn_context._shape(thing(7, kind="CURVE", modifiers=["NODES"], dimensions=(4.0, 2.0, 2.0)), only, facts)
            turn_context._shape(thing(9, modifiers=["NODES"]), both, facts)
            turn_context._shape(thing(3, kind="CURVE", evaluated=False), hidden, facts)
            turn_context._shape(thing(4), plain, facts)
        self.assertEqual(only["instances"]["own_geometry"], False)
        self.assertNotIn("dimensions", only)   # Its padded control points, never stated.
        self.assertEqual(both["dimensions"], [1.0, 2.0, 3.0])
        self.assertEqual(both["instances"]["bounds"], [[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]])
        self.assertNotIn("dimensions", hidden)
        self.assertEqual(plain, {"dimensions": [1.0, 2.0, 3.0]})

    def test_a_program_row_of_an_object_showing_only_instances_drops_its_own_box(self):
        fact = {"bounds": [[0, 0, 0], [1, 1, 1]], "own_geometry": False}
        row = program_model_result._geometry({"name": "Row", "bounds": [[-1, -1, -1], [3, 1, 1]], "instances": fact})
        self.assertNotIn("bounds", row)
        self.assertEqual(row["instances"], fact)
        kept = program_model_result._geometry({"name": "Both", "bounds": [[0, 0, 0], [1, 1, 1]],
                                               "instances": {"bounds": [[0, 0, 0], [2, 2, 2]]}})
        self.assertEqual(kept["bounds"], [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])

    def test_kit_bounds_counts_instances_and_skips_a_box_the_object_does_not_have(self):
        stand_in, _calls = native({7: GRID})
        with stand_in:
            facts, found = script_kit_support.instance_facts("graph"), []
            self.assertFalse(script_kit_support.add_instances(thing(7, modifiers=["NODES"]), facts, found))
            self.assertTrue(script_kit_support.add_instances(thing(5), facts, found))
        self.assertEqual(found, [((12.4, -0.6, -0.05), (13.6, 0.6, 0.05))])   # Exact, never rounded.

    def test_every_reader_reads_the_one_source(self):
        here = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        for module, text in (("script_after.py", "instances.of(obj)"), ("program_verification.py", "instances.of(obj)"),
                             ("scene_query_results.py", "instance_fact(obj"), ("view_capture.py", "InstanceFacts"),
                             ("turn_context.py", "object_bounds.InstanceFacts"), ("script_kit.py", "add_instances")):
            self.assertIn(text, (here / module).read_text(encoding="utf-8"), module)


if __name__ == "__main__":
    unittest.main()
