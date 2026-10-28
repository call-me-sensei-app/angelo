# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``whole`` check target: a box measures the whole thing a brief names, however it was built.

Host-only: fake objects and a fake ``mathutils`` (patched in per test) stand in for Blender. One target kind in
``scenario_checks`` admits the object of the exact name (or a Blender copy), the parts named with it as a word prefix,
and everything parented under any of those; a near name that only shares letters ('Parked Hatchbacks Sign') is not
part of it. The cases: one mesh, a body with named parts, an Empty with differently named children, a lone roof slab
(which must fail a box that bounds the bottom), and the near-name decoy.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scenario_checks as checks  # noqa: E402


class _Vector(tuple):
    def __new__(cls, values):
        return super().__new__(cls, (float(v) for v in values))


class _Shift:
    """A translation-only world matrix: ``matrix @ Vector(corner)``."""

    def __init__(self, offset):
        self.offset = offset

    def __matmul__(self, point):
        return _Vector(p + o for p, o in zip(point, self.offset))


class _Object:
    def __init__(self, name, low=None, high=None, parent=None, kind="MESH", at=(0.0, 0.0, 0.0)):
        self.name, self.parent, self.type, self.at = name, parent, kind, at
        self.original = self
        corners = [] if low is None else [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1])
                                          for z in (low[2], high[2])]
        self.bound_box = corners


class _Instance:
    def __init__(self, obj):
        self.object, self.is_instance, self.parent = obj, False, None
        self.matrix_world = _Shift(obj.at)


class _Scene:
    def __init__(self, objects):
        self.objects = objects


class _Depsgraph:
    def __init__(self, objects):
        self.object_instances = [_Instance(obj) for obj in objects]


def _hatchback_box(target):
    """The town centre's hatchback check: 4 x 1.75 m at (-8, 2.4), on the road (z = 0) and 1.5 m tall."""
    return {"kind": "bbox", "target": target, "min": [-10.0, 1.525, 0.0], "max": [-6.0, 3.275, 1.5], "tolerance": 0.3,
            "label": "the hatchback"}


def _grade(objects, check):
    fake = types.ModuleType("mathutils")
    fake.Vector = _Vector
    with mock.patch.dict(sys.modules, {"mathutils": fake}):
        return checks._check_bbox(None, _Scene(objects), _Depsgraph(objects), check)


BODY = ((-10.0, 1.525, 0.25), (-6.0, 3.275, 1.0))      # The lower body slab alone.
CABIN = ((-9.2, 1.6, 1.0), (-6.8, 3.2, 1.5))
WHEEL = ((-9.6, 1.5, 0.0), (-8.9, 1.7, 0.65))


class WholeTargetNameTests(unittest.TestCase):
    def test_the_exact_name_its_copies_and_its_word_prefixed_parts(self):
        whole = {"whole": "Parked Hatchback"}
        for name in ("Parked Hatchback", "Parked Hatchback.001", "Parked Hatchback Cabin", "Parked Hatchback Wheel FL",
                     "Parked Hatchback_Door"):
            self.assertTrue(checks.matches(name, whole), name)

    def test_a_near_name_decoy_is_not_part_of_it(self):
        whole = {"whole": "Parked Hatchback"}
        for name in ("Parked Hatchbacks Sign", "Parked Hatch", "Parked Van", "Old Parked Hatchback"):
            self.assertFalse(checks.matches(name, whole), name)

    def test_children_count_through_their_parents(self):
        root = _Object("Bus Shelter", kind="EMPTY")
        roof = _Object("Roof", parent=root)
        bolt = _Object("Bolt", parent=roof)
        stranger = _Object("Bench")
        target = {"whole": "Bus Shelter"}
        self.assertTrue(checks.object_matches(roof, target))
        self.assertTrue(checks.object_matches(bolt, target))
        self.assertFalse(checks.object_matches(stranger, target))
        # Other target kinds stay by-name only: a differently named child is not the thing.
        self.assertFalse(checks.object_matches(roof, {"name": "Bus Shelter"}))
        self.assertFalse(checks.object_matches(roof, {"prefix": "Bus Shelter", "word": True}))

    def test_except_and_any_compose(self):
        root = _Object("Room Floor")
        board = _Object("Board 3", parent=root)
        self.assertTrue(checks.object_matches(board, {"any": [{"whole": "Room Floor"}]}))
        furniture = {"prefix": "", "except": [{"whole": "Room Floor"}]}
        self.assertFalse(checks.matches("Room Floor Boards", furniture))
        self.assertTrue(checks.matches("Room Sofa", furniture))

    def test_it_says_what_it_looked_for(self):
        self.assertIn("parented under", checks.looked_for({"whole": "Phone Box"}))


class WholeTargetBoxTests(unittest.TestCase):
    def test_one_mesh_passes(self):
        objects = [_Object("Parked Hatchback", (-10.0, 1.525, 0.0), (-6.0, 3.275, 1.5))]
        passed, details = _grade(objects, _hatchback_box({"whole": "Parked Hatchback"}))
        self.assertTrue(passed, details)

    def test_a_body_with_word_prefixed_parts_is_measured_whole(self):
        objects = [_Object("Parked Hatchback", *BODY), _Object("Parked Hatchback Cabin", *CABIN),
                   _Object("Parked Hatchback Wheel", *WHEEL)]
        self.assertFalse(_grade(objects, _hatchback_box({"name": "Parked Hatchback"}))[0])   # The old exact name.
        passed, details = _grade(objects, _hatchback_box({"whole": "Parked Hatchback"}))
        self.assertTrue(passed, details)

    def test_an_empty_with_children_is_measured_by_its_children(self):
        root = _Object("Parked Hatchback", kind="EMPTY")
        objects = [root, _Object("Body", *BODY, parent=root), _Object("Cabin", *CABIN, parent=root),
                   _Object("Wheel", *WHEEL, parent=root)]
        self.assertEqual(_grade(objects, _hatchback_box({"name": "Parked Hatchback"}))[1],
                         {"reason": "no matching geometry"})
        passed, details = _grade(objects, _hatchback_box({"whole": "Parked Hatchback"}))
        self.assertTrue(passed, details)

    def test_a_lone_roof_slab_fails_once_the_bottom_is_bounded(self):
        objects = [_Object("Parked Hatchback", (-10.0, 1.525, 1.4), (-6.0, 3.275, 1.5))]
        passed, details = _grade(objects, _hatchback_box({"whole": "Parked Hatchback"}))
        self.assertFalse(passed)
        self.assertIn("min.z", details["off"])

    def test_a_near_name_decoy_is_left_out_of_the_box(self):
        objects = [_Object("Parked Hatchback", (-10.0, 1.525, 0.0), (-6.0, 3.275, 1.5)),
                   _Object("Parked Hatchbacks Sign", (-8.2, 4.0, 0.0), (-7.8, 4.1, 3.0))]
        passed, details = _grade(objects, _hatchback_box({"whole": "Parked Hatchback"}))
        self.assertTrue(passed, details)


if __name__ == "__main__":
    unittest.main()
