# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The other size readers state what an instancer shows (``object_bounds.InstanceFacts``), never its base data.

The coordinator, 2026-10-25, under the maintainer's rule (found before publish, fixed in the release): a team
library import row, the scene object read's GEOMETRY section, a validation "dimensions" check and the chosen tools'
fit-to-selection box each read a Geometry Nodes instancer as its base data (0, or a curve's control points padded by
their radius). These pin each reader over a stand-in for the native pass; test_native_instance_facts.py pins the
facts and test_blender_object_bounds.py the real geometry.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import scene_query_results, validation_checks  # noqa: E402
from test_native_instance_facts import GRID, native, thing  # noqa: E402

BOTH = ((0.0, 0.0, 0.0), (2.0, 2.0, 2.0), True)   # Instances beside geometry of its own.


def blender_modules():
    """Stand-ins for the modules a reader imports at load: ``bpy`` and ``mathutils.Vector``."""
    bpy = types.ModuleType("bpy")
    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = tuple
    handles = types.ModuleType("angelo_agent.scene_queries")
    handles.object_handle = lambda obj: "handle-%d" % obj.session_uid
    return {"bpy": bpy, "mathutils": mathutils, "angelo_agent.scene_queries": handles}


class Moved:
    """A world matrix that moves a point by ``offset``."""
    def __init__(self, offset):
        self.offset = offset

    def __matmul__(self, point):
        return tuple(a + b for a, b in zip(point, self.offset))


def placed(uid, offset=(0.0, 0.0, 0.0), **kwargs):
    obj = thing(uid, **kwargs)
    obj.name, obj.matrix_world = "Object %d" % uid, Moved(offset)
    obj.bound_box = [(x, y, z) for x in (0.0, 1.0) for y in (0.0, 2.0) for z in (0.0, 3.0)]
    return obj


class SceneObjectReadTests(unittest.TestCase):
    def test_the_geometry_section_is_named_base_data_only_when_the_object_shows_none_of_its_own(self):
        window = SimpleNamespace(view_layer=SimpleNamespace(depsgraph="graph"))
        stand_in, _calls = native({7: GRID, 9: BOTH})
        with stand_in:
            only = scene_query_results.instance_detail(window, thing(7, modifiers=["NODES"]))
            both = scene_query_results.instance_detail(window, thing(9, modifiers=["NODES"]))
            plain = scene_query_results.instance_detail(window, thing(4))
        self.assertEqual(only["geometry_note"], scene_query_results.GEOMETRY_NOTE)
        self.assertEqual(only["instances"]["bounds"], [[12.4, -0.6, -0.05], [13.6, 0.6, 0.05]])
        self.assertNotIn("geometry_note", both)   # Its GEOMETRY section is what it shows of its own.
        self.assertEqual(plain, {})
        unread, _calls = native(None)
        with unread:
            fact = scene_query_results.instance_detail(window, thing(9, modifiers=["NODES"]))
        self.assertNotIn("geometry_note", fact)   # Unread: nothing said about its base data (REVIEW-OVERLAP-A-2 L5).


class DimensionsCheckTests(unittest.TestCase):
    def check(self, obj, expected=(1.0, 2.0, 3.0)):
        reader = SimpleNamespace(context={"depsgraph": "graph", "targets": {"it": obj}},
                                 dimensions=lambda target, space: list(target.dimensions))
        return validation_checks.evaluate_check(reader, {"kind": "dimensions", "target": "it", "space": "WORLD",
                                                         "expected": list(expected), "tolerance": 0.01})

    def test_an_object_showing_only_instances_is_inconclusive_by_name_with_what_its_instances_show(self):
        """Fail closed, never a failed assertion (validation_spec; REVIEW-OVERLAP-A-2 M2)."""
        stand_in, _calls = native({7: GRID, 8: (None, False)})
        with stand_in:
            with self.assertRaises(validation_checks.Inconclusive) as found:
                self.check(placed(7, modifiers=["NODES"]))   # Its base data matches; it shows something else.
            with self.assertRaises(validation_checks.Inconclusive) as many:
                self.check(placed(8, modifiers=["NODES"]))   # Past the limit, no geometry of its own.
        self.assertIn("Object 7 shows only instances", str(found.exception))
        self.assertIn("[[12.4, -0.6, -0.05], [13.6, 0.6, 0.05]]", str(found.exception))
        self.assertIn("Object 8 shows only instances", str(many.exception))
        unread, _calls = native(None)   # Not evaluated since its change: no claim about what it shows.
        with unread:
            with self.assertRaises(validation_checks.Inconclusive) as why:
                self.check(placed(9, modifiers=["NODES"]))
        self.assertIn("could not be read", str(why.exception))
        self.assertNotIn("shows only instances", str(why.exception))

    def test_its_own_dimensions_are_checked_beside_the_instances_it_states(self):
        stand_in, _calls = native({9: BOTH})
        with stand_in:
            passed, details = self.check(placed(9, modifiers=["NODES"]))
            plain_passed, plain = self.check(placed(4))
        self.assertTrue(passed)
        self.assertEqual(details["instances"]["bounds"], [[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]])
        self.assertTrue(plain_passed)
        self.assertNotIn("instances", plain)


class ReadersLoadedWithBlenderTests(unittest.TestCase):
    """The team library's import rows and the chosen tools' fit box: modules that import ``bpy`` at load."""

    def setUp(self):
        modules = mock.patch.dict(sys.modules, blender_modules())
        modules.start()
        self.addCleanup(modules.stop)
        for name in ("team_library_import", "chosen_tools_host"):
            sys.modules.pop("angelo_agent." + name, None)
            self.addCleanup(sys.modules.pop, "angelo_agent." + name, None)
        from angelo_agent import chosen_tools_host, object_bounds, team_library_import
        self.library, self.tools, self.bounds = team_library_import, chosen_tools_host, object_bounds

    def test_an_import_row_states_instances_and_no_dimensions_the_object_does_not_show(self):
        stand_in, _calls = native({7: GRID, 9: BOTH})
        with stand_in:
            facts = self.bounds.InstanceFacts("graph")
            only = self.library._object_row(placed(7, modifiers=["NODES"]), facts)
            both = self.library._object_row(placed(9, modifiers=["NODES"]), facts)
            plain = self.library._object_row(placed(4), facts)
        self.assertNotIn("dimensions", only)
        self.assertEqual(only["instances"]["own_geometry"], False)
        self.assertEqual(both["dimensions"], [1.0, 2.0, 3.0])
        self.assertIn("instances", both)
        self.assertEqual(plain, {"name": "Object 4", "type": "MESH", "handle": "handle-4", "dimensions": [1.0, 2.0, 3.0]})

    def test_a_fit_box_is_what_the_target_shows(self):
        layer = SimpleNamespace(depsgraph="graph")
        stand_in, _calls = native({7: GRID, 9: BOTH})
        with stand_in:
            only = self.tools._fit_box(placed(7, offset=(5.0, 0.0, 0.0), modifiers=["NODES"]), layer)
            both = self.tools._fit_box(placed(9, offset=(-1.0, 0.0, 0.0), modifiers=["NODES"]), layer)
            plain = self.tools._fit_box(placed(4, offset=(1.0, 1.0, 1.0)), layer)
        self.assertEqual(only, ([12.4, -0.6, -0.05], [13.6, 0.6, 0.05]))   # Never the instancer's own point.
        self.assertEqual(both, ([-1.0, 0.0, 0.0], [2.0, 2.0, 3.0]))        # Its own box and its instances'.
        self.assertEqual(plain, ([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]))

    def test_a_fit_box_of_an_unreadable_target_is_none_never_its_base_data(self):
        stand_in, _calls = native({7: (None, False)})   # Past the limit, no geometry of its own.
        with stand_in:
            self.assertIsNone(self.tools._fit_box(placed(7, modifiers=["NODES"]), SimpleNamespace(depsgraph="graph")))


if __name__ == "__main__":
    unittest.main()
