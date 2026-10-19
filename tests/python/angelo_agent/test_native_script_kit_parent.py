# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""kit.parent keeps where things are (script_kit_parent), without Blender: the order that makes it so.

The recorded miss (doc/angelo/performance.rst, "Colours a request states"): a script set a car body's location and
then parented the world-placed parts with ``matrix_parent_inverse = body.matrix_world.inverted()``; the matrix had not
been evaluated, so the parts moved by the body's offset. Pinned here: the view layer is updated before any matrix is
read, each child's inverse comes from the parent's matrix read after that update, the child is put back at the world
matrix it had, and an object Blender does not evaluate, or a parenting loop, is refused before anything changes.
test_blender_script_kit.py runs the same scenario on real objects in stock Blender.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent.script_kit_parent import parent_keep_world  # noqa: E402


class Inverse(str):
    def copy(self):
        return Inverse(self)


class Matrix:
    def __init__(self, label, log, singular=False):
        self.label, self.log, self.singular = label, log, singular

    def copy(self):
        return Matrix(self.label, self.log)

    def inverted(self):
        self.log.append("inverse of %s" % self.label)
        if self.singular:
            raise ValueError("Matrix.inverted(): matrix does not have an inverse")
        return Inverse("inverse(%s)" % self.label)

    def identity(self):
        self.log.append("identity %s" % self.label)


class Evaluated:
    def as_pointer(self):
        return id(self)


class Obj:
    def __init__(self, name, log, scene, evaluated=True):
        self.name, self.log, self.users_scene, self._evaluated = name, log, [scene], evaluated
        self.parent, self.children_recursive, self.singular = None, [], False
        self.matrix_parent_inverse = Matrix("%s inverse" % name, log)
        self._world = "stale" if name == "Car Body" else name

    def as_pointer(self):
        return id(self)

    def evaluated_get(self, _depsgraph):
        return Evaluated() if self._evaluated else self

    @property
    def matrix_world(self):
        self.log.append("read %s matrix_world (%s)" % (self.name, self._world))
        return Matrix("%s@%s" % (self.name, self._world), self.log, self.singular)

    @matrix_world.setter
    def matrix_world(self, value):
        self.log.append("set %s matrix_world to %s" % (self.name, value.label))


class Context:
    def __init__(self, log, objects):
        self.log, self.objects = log, objects
        self.view_layer = self

    def update(self):
        self.log.append("view layer update")
        for obj in self.objects:
            obj._world = "fresh"

    def evaluated_depsgraph_get(self):
        return "depsgraph"


class Bpy:
    def __init__(self, context):
        self.context = context


class ParentTests(unittest.TestCase):
    def setUp(self):
        self.log, self.scene = [], "Scene"
        self.body = Obj("Car Body", self.log, self.scene)
        self.wheel = Obj("Car Wheel FL", self.log, self.scene)
        self.bpy = Bpy(Context(self.log, [self.body, self.wheel]))

    def test_the_inverse_comes_from_the_fresh_parent_matrix_and_the_child_stays_put(self):
        parent_keep_world(self.bpy, self.scene, [self.wheel], self.body)
        self.assertEqual(self.log[0], "view layer update")   # Before any matrix is read.
        self.assertNotIn("stale", " ".join(self.log))
        self.assertIn("inverse of Car Body@fresh", self.log)
        self.assertIn("set Car Wheel FL matrix_world to Car Wheel FL@fresh", self.log)
        self.assertEqual((self.wheel.parent, self.wheel.matrix_parent_inverse), (self.body, "inverse(Car Body@fresh)"))
        self.assertEqual(self.log.count("inverse of Car Body@fresh"), 1)

    def test_an_object_blender_does_not_evaluate_or_a_loop_is_refused_before_any_change(self):
        hidden = Obj("Hidden", self.log, self.scene, evaluated=False)
        with self.assertRaisesRegex(ValueError, "not evaluated"):
            parent_keep_world(self.bpy, self.scene, [hidden], self.body)
        self.body.children_recursive = [self.wheel]
        with self.assertRaisesRegex(ValueError, "its own child or itself"):
            parent_keep_world(self.bpy, self.scene, [self.body], self.wheel)
        with self.assertRaisesRegex(ValueError, "its own child or itself"):
            parent_keep_world(self.bpy, self.scene, [self.body], self.body)
        self.assertEqual((self.wheel.parent, hidden.parent), (None, None))

    def test_a_parent_without_an_inverse_is_refused_before_any_child_changes(self):
        # Parent review P-N1: the inverse was taken inside the loop, so the first child was parented before it raised.
        cabin = Obj("Car Cabin", self.log, self.scene)
        self.bpy.context.objects.append(cabin)
        self.body.singular = True
        with self.assertRaisesRegex(ValueError, "no inverse"):
            parent_keep_world(self.bpy, self.scene, [self.wheel, cabin], self.body)
        self.assertEqual((self.wheel.parent, cabin.parent), (None, None))
        self.assertEqual(self.log.count("inverse of Car Body@fresh"), 1)   # Once, before the loop.

    def test_a_parent_of_none_unparents_in_place(self):
        # Parent review P-N3: kit.parent(child, None) raised "None is not an object".
        self.wheel.parent = self.body
        parent_keep_world(self.bpy, self.scene, [self.wheel], None)
        self.assertIsNone(self.wheel.parent)
        self.assertIn("identity Car Wheel FL inverse", self.log)
        self.assertIn("set Car Wheel FL matrix_world to Car Wheel FL@fresh", self.log)


if __name__ == "__main__":
    unittest.main()
