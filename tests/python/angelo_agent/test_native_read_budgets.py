# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read budgets charge C-speed copies by bytes and element reads by elements; no bpy."""

from array import array
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.scene_geometry import GeometryReader
from angelo_agent.scene_read_values import BudgetExceeded, ReadBudget


class Rows(list):
    def foreach_get(self, field, target):
        values = [getattr(row, field) for row in self]
        target[:] = array(target.typecode, values)


class Attributes(dict):
    def get(self, name):
        return dict.get(self, name)


class Charges:
    def __init__(self):
        self.calls = []

    def take(self, elements=0, items=0, packed=0):
        self.calls.append((elements, items, packed))


def flags(name, values, domain="POINT"):
    return SimpleNamespace(name=name, data_type="BOOLEAN", domain=domain,
                           data=Rows(SimpleNamespace(value=value) for value in values))


class ReadBudgetTests(unittest.TestCase):
    def test_packed_bytes_and_elements_are_independent_bounds(self):
        budget = ReadBudget(10, 10, 5, packed=100)
        budget.take(packed=60)
        budget.take(elements=10)
        with self.assertRaises(BudgetExceeded):
            budget.take(packed=41)
        with self.assertRaises(BudgetExceeded):
            budget.take(elements=1)
        self.assertEqual((budget.elements, budget.packed), (0, 40))

    def test_a_budget_without_a_packed_allowance_refuses_packed_reads(self):
        with self.assertRaises(BudgetExceeded):
            ReadBudget(10, 10, 5).take(packed=1)


class MeshArrayChargeTests(unittest.TestCase):
    def setUp(self):
        self.reader = GeometryReader(None, {"fingerprint_bytes": 0}, 2048)
        self.vertices = Rows(SimpleNamespace(select=index == 1, hide=False) for index in range(3))

    def test_attribute_copy_is_charged_bytes_before_the_copy(self):
        mesh = SimpleNamespace(attributes=Attributes({".select_vert": flags(".select_vert", [False, True, False])}))
        charges = Charges()
        values = self.reader.read_mesh_array(mesh, "POINT", self.vertices, "select", "b", 1, charges)
        self.assertEqual(list(values), [0, 1, 0])
        self.assertEqual(charges.calls, [(0, 0, 3)])

    def test_absent_flag_default_is_charged_bytes(self):
        charges = Charges()
        values = self.reader.read_mesh_array(SimpleNamespace(attributes=Attributes()), "POINT", self.vertices,
                                             "hide", "b", 1, charges)
        self.assertEqual(list(values), [0, 0, 0])
        self.assertEqual(charges.calls, [(0, 0, 3)])

    def test_element_getter_fallback_is_charged_per_element(self):
        mesh = SimpleNamespace(attributes=Attributes({".select_vert": flags(".select_vert", [True], "EDGE")}))
        charges = Charges()
        values = self.reader.read_mesh_array(mesh, "POINT", self.vertices, "select", "b", 1, charges)
        self.assertEqual(list(values), [0, 1, 0])  # Wrong domain: the exact element read.
        self.assertEqual(charges.calls, [(3, 0, 0)])


if __name__ == "__main__":
    unittest.main()
