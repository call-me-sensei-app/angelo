# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the executor does around a reviewed script (script_prepare), without Blender: a parent-inverse statement
read from a parent whose move Blender has not evaluated. The host call before it, where it goes (any block, a loop
body on one line), what it leaves alone, line numbers kept, and the staleness test. test_blender_script_parent_inverse.py
runs it on real Blender data, including the recorded car whose parts landed off their body.
"""

from pathlib import Path
import sys
import traceback
import unittest
from types import SimpleNamespace as NS

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import script_prepare as prepare  # noqa: E402
from angelo_agent.script_errors import SCRIPT_FILE  # noqa: E402

RECORDED = ("rig.location = (0, -1, 0)\n"
            "for p_ in parts: p_.parent=rig; p_.matrix_parent_inverse=rig.matrix_world.inverted()\n")


class Mat(numpy.ndarray):
    """A 4x4 matrix as mathutils reads it here: indexing, @ and inverted()."""

    def inverted(self):
        return numpy.linalg.inv(self).view(Mat)


def identity():
    return numpy.identity(4).view(Mat)


class FakeObject:
    def __init__(self, name, location=(0, 0, 0), world=None, parent=None, animated=False, constraints=()):
        self.name, self.parent, self.parent_type, self.constraints = name, parent, "OBJECT", list(constraints)
        self.matrix_basis = translation(*location)
        self.matrix_world = translation(*location) if world is None else world
        self.matrix_parent_inverse = identity()
        self.animation_data = NS(action=object(), drivers=[], nla_tracks=[]) if animated else None


def translation(x, y, z):
    matrix = identity()
    matrix[0][3], matrix[1][3], matrix[2][3] = x, y, z
    return matrix


class Layer:
    def __init__(self, objects=()):
        self.updates, self.objects = 0, objects

    def update(self):
        self.updates += 1
        for obj in self.objects:   # Blender evaluates: every world matrix becomes its basis composition.
            obj.matrix_world = prepare._basis_world(obj, 0) if prepare._basis_world(obj, 0) is not None else obj.matrix_world


def run(code, bpy, layer, **names):
    prepared = prepare.Prepared(bpy, layer)
    namespace = dict(names)
    exec(prepared.compiled(code, namespace), namespace)
    return prepared, namespace


class ParentInverseTests(unittest.TestCase):
    def test_the_host_call_precedes_each_parent_inverse_statement(self):
        prepared = prepare.Prepared(NS(data=NS(), types=NS(Object=FakeObject)), Layer())
        seen = []
        namespace = {}
        code = prepared.compiled(RECORDED, namespace)
        namespace[prepare.FRESH] = lambda parent, line: seen.append((parent.name, line))
        rig = FakeObject("Rig")
        namespace.update(rig=rig, parts=[NS(), NS()])
        exec(code, namespace)
        self.assertEqual(seen, [("Rig", 2), ("Rig", 2)])   # Before the statement, each time round the loop.

    def test_every_block_is_reached_and_other_statements_are_left_alone(self):
        tree = prepare._Precede().visit(prepare.ast.parse(
            "def make(c, p):\n    if c:\n        c.matrix_parent_inverse = p.matrix_world.inverted_safe()\n"
            "try:\n    x.matrix_parent_inverse = bpy.data.objects['Body'].matrix_world.inverted()\nexcept KeyError:\n    pass\n"
            "y.matrix_parent_inverse = make_parent().matrix_world.inverted()\n"   # A call: never read twice.
            "z.matrix_parent_inverse = p.matrix_world.inverted() @ other\n"       # Another shape: left as written.
            "w.matrix_world = p.matrix_world.inverted()\n"))
        text = prepare.ast.unparse(tree)
        self.assertEqual(text.count(prepare.FRESH), 2)
        self.assertIn("%s(p, 3)" % prepare.FRESH, text)
        self.assertIn("%s(bpy.data.objects['Body'], 5)" % prepare.FRESH, text)

    def test_a_script_without_the_statement_compiles_as_written_and_lines_are_kept(self):
        prepared = prepare.Prepared(NS(data=NS(), types=NS(Object=FakeObject)), Layer())
        namespace = {}
        plain = prepared.compiled("a = 1\nb = 2\n", namespace)
        self.assertNotIn(prepare.FRESH, namespace)
        self.assertEqual(plain.co_filename, SCRIPT_FILE)
        namespace = {"rig": FakeObject("Rig"), "parts": [NS()]}
        code = prepared.compiled(RECORDED + "raise ValueError('third line')\n", namespace)
        try:
            exec(code, namespace)
        except ValueError as error:
            lines = [number for frame, number in traceback.walk_tb(error.__traceback__)
                     if frame.f_code.co_filename == SCRIPT_FILE]
        self.assertEqual(lines, [3])

    def test_staleness_is_exact_for_plain_objects_and_assumed_otherwise(self):
        moved = FakeObject("Body", location=(0, -1, 0), world=identity())   # Location set, not evaluated.
        fresh = FakeObject("Body", location=(0, -1, 0))
        child = FakeObject("Child", location=(1, 0, 0), parent=fresh)
        child.matrix_world = translation(1, -1, 0)
        self.assertTrue(prepare._stale(moved))
        self.assertFalse(prepare._stale(fresh))
        self.assertFalse(prepare._stale(child))
        self.assertTrue(prepare._stale(FakeObject("Rig", animated=True)))           # Keys decide: evaluate.
        self.assertTrue(prepare._stale(FakeObject("Rig", constraints=[object()])))
        bone_child = FakeObject("Bone child", parent=fresh)
        bone_child.parent_type = "BONE"
        self.assertTrue(prepare._stale(bone_child))

    def test_the_scene_is_evaluated_only_for_a_stale_parent_and_the_lines_are_reported(self):
        rig = FakeObject("Rig", location=(0, -1, 0), world=identity())
        layer = Layer([rig])
        bpy = NS(data=NS(), types=NS(Object=FakeObject))
        parts = [NS(), NS()]
        prepared, _ = run(RECORDED, bpy, layer, rig=rig, parts=parts)
        self.assertEqual(layer.updates, 1)   # The first statement evaluates; the second finds the rig fresh.
        self.assertTrue(all(numpy.allclose(p.matrix_parent_inverse, translation(0, 1, 0)) for p in parts))
        self.assertEqual(prepared.after(RECORDED)["evaluated_before_parent_inverse"]["lines"], [2])
        layer = Layer([rig])
        prepared, _ = run(RECORDED, bpy, layer, rig=rig, parts=[NS()])   # Now fresh: nothing to do.
        self.assertEqual((layer.updates, prepared.after(RECORDED)), (0, {}))

    def test_a_failure_of_the_host_call_never_fails_the_script(self):
        layer = Layer()
        layer.update = lambda: (_ for _ in ()).throw(RuntimeError("no context"))
        rig = FakeObject("Rig", location=(0, -1, 0), world=identity())
        prepared, _ = run(RECORDED, NS(data=NS(), types=NS(Object=FakeObject)), layer, rig=rig, parts=[NS()])
        self.assertEqual(prepared.evaluated, [])


if __name__ == "__main__":
    unittest.main()
