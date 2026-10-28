# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scene signature's world matrices (scene_signature): read for the whole scene in one foreach_get, column by
column as Blender stores them, and compared as the rounded lists were (equal to 6 decimals). A typed edit's targets
read one by one into the same layout; a collection that cannot be bulk-read falls back to that. The real layout is
checked on Blender data by the concurrent-edit fixture's read-backs and benchmark_blender_agent_execution."""

from array import array
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import scene_signature as signature  # noqa: E402


def rows(x=0.0, y=0.0, z=0.0, scale=1.0):
    return [[scale, 0.0, 0.0, x], [0.0, scale, 0.0, y], [0.0, 0.0, scale, z], [0.0, 0.0, 0.0, 1.0]]


class Obj:
    _uid = 0

    def __init__(self, name, matrix):
        Obj._uid += 1
        self.session_uid, self.name, self.type, self.data, self.matrix_world = Obj._uid, name, "EMPTY", None, matrix
        self.material_slots, self.modifiers, self.animation_data, self.parent, self.constraints = [], [], None, None, []


class Objects(list):
    """``scene.objects``: iteration, len and (optionally) foreach_get of matrix_world, column-major per object."""
    reads = 0

    def __init__(self, items, bulk=True):
        super().__init__(items)
        self.bulk = bulk

    def foreach_get(self, name, buffer):
        if not self.bulk:
            raise TypeError("foreach_get not supported")
        assert name == "matrix_world" and len(buffer) == 16 * len(self)
        Objects.reads += 1
        for index, obj in enumerate(self):
            buffer[index * 16:index * 16 + 16] = array("f", [obj.matrix_world[r][c] for c in range(4) for r in range(4)])


def matrix(values):
    return signature.Matrix(array("f", values).tobytes())


class MatrixTests(unittest.TestCase):
    def test_equal_exactly_when_every_value_is_equal_to_six_decimals(self):
        base = [1.0, 0.0, 0.0, 0.0] * 4
        self.assertEqual(matrix(base), matrix(base))
        self.assertEqual(matrix(base), matrix([1.0000001] + base[1:]))   # Below the 6th decimal: the same.
        self.assertEqual(matrix([0.0] + base[1:]), matrix([-0.0] + base[1:]))   # Different bytes, equal values.
        self.assertNotEqual(matrix(base), matrix([1.001] + base[1:]))
        self.assertNotEqual(matrix(base), [round(v, 6) for v in base])   # Never equal to another kind of value.
        self.assertEqual(matrix(base).rounded(), [round(float(v), 6) for v in array("f", base)])

    def test_bulk_and_single_reads_share_one_layout(self):
        objects = [Obj("A", rows(1.5, -2.0, 3.25)), Obj("B", rows(scale=2.0))]
        objects[1].matrix_world[0][1] = 0.5   # Not symmetric: a transposed read would differ.
        bulk = signature._matrices(Objects(objects), 2)
        self.assertEqual(bulk, [signature._one_matrix(obj) for obj in objects])
        self.assertIsNone(signature._matrices(Objects(objects, bulk=False), 2))


class SignatureTests(unittest.TestCase):
    def scene(self, bulk=True):
        self.objects = [Obj("Cube.%03d" % i, rows(i, 0, 0)) for i in range(4)]
        return NS(objects=Objects(self.objects, bulk))

    def test_one_bulk_read_and_the_diff_names_what_moved(self):
        for bulk in (True, False):
            scene = self.scene(bulk)
            Objects.reads = 0
            before = signature.scene_signature(scene)
            self.assertEqual(Objects.reads, 1 if bulk else 0)
            self.objects[1].matrix_world = rows(1.0000001, 0, 0)   # Float noise: unchanged, as before.
            self.objects[2].matrix_world = rows(2.5, 0, 0)
            diff = signature.scene_diff(before, signature.scene_signature(scene))
            self.assertEqual(diff["modified"], [{"name": "Cube.002", "fields": ["matrix"]}])
            self.assertEqual(signature.scene_signature(scene), signature.scene_signature(scene))

    def test_targets_are_read_one_by_one_in_the_same_layout(self):
        scene = self.scene()
        Objects.reads = 0
        targeted = signature.scene_signature(scene, [self.objects[3]])
        whole = signature.scene_signature(scene)
        self.assertEqual(Objects.reads, 1)
        uid = str(self.objects[3].session_uid)
        self.assertEqual(targeted["objects"][uid]["matrix"].raw, whole["objects"][uid]["matrix"].raw)
        self.assertTrue(targeted["targeted"])


if __name__ == "__main__":
    unittest.main()
