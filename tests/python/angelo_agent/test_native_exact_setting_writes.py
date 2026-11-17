# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A commit or a restore writes a setting unless the target already holds that exact value.

``program_transfers._set_settings`` and ``modifier_state._put`` skip a write the target already holds, because
rewriting a held value can clamp it (a fresh Build modifier's seed, a cloth's goal spring). Python's ``==`` is not
"already holds", though: it calls -0.0 equal to 0.0. Blender writes -0.0 where it computes a value, for example the
inverse of an identity matrix, which is full of -0.0. So when an equality-based skip met a staged -0.0 over the
target's 0.0, the commit kept the target's zero instead of the staged one. test_blender_kept_settings found that on a
Child Of constraint's inverse (2026-11-15). These tests hold every kind of setting value to that rule: exact floats
(the sign of zero, NaN), arrays and matrices element by element, and plain values as before.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import modifier_state, program_transfers  # noqa: E402

NEG_IDENTITY = ((1.0, -0.0, 0.0, -0.0), (-0.0, 1.0, -0.0, 0.0), (0.0, -0.0, 1.0, -0.0), (-0.0, 0.0, -0.0, 1.0))
IDENTITY = tuple(tuple(1.0 if row == column else 0.0 for column in range(4)) for row in range(4))


def signs(value):
    """Every float in ``value`` (nested), with the sign of its zeros made visible."""
    if isinstance(value, (tuple, list)):
        return [signs(item) for item in value]
    return repr(value)


class Recording:
    """A stand-in RNA struct that records each write."""

    def __init__(self, **values):
        object.__setattr__(self, "writes", [])
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        self.writes.append(name)
        object.__setattr__(self, name, value)


class HeldExactlyTests(unittest.TestCase):
    def test_the_sign_of_zero_is_part_of_the_value(self):
        self.assertFalse(program_transfers.held_exactly(0.0, -0.0))
        self.assertFalse(program_transfers.held_exactly(-0.0, 0.0))
        self.assertTrue(program_transfers.held_exactly(-0.0, -0.0))

    def test_nan_holds_nan(self):
        self.assertTrue(program_transfers.held_exactly(math.nan, math.nan))
        self.assertFalse(program_transfers.held_exactly(0.0, math.nan))

    def test_arrays_and_matrices_compare_element_by_element(self):
        self.assertFalse(program_transfers.held_exactly(IDENTITY, NEG_IDENTITY))
        self.assertTrue(program_transfers.held_exactly(NEG_IDENTITY, tuple(tuple(row) for row in NEG_IDENTITY)))
        self.assertFalse(program_transfers.held_exactly((1.0, 0.0), (1.0, -0.0)))
        self.assertFalse(program_transfers.held_exactly((1.0, 2.0), (1.0, 2.0, 3.0)))
        self.assertTrue(program_transfers.held_exactly([1, 2], (1, 2)))   # An RNA array against its staged tuple.

    def test_plain_values_compare_as_before(self):
        for current, expected, same in ((3, 3, True), (3, 4, False), (True, True, True), (True, False, False),
                                        ("A", "A", True), ("A", "B", False), (None, None, True),
                                        ({"X"}, {"X"}, True), ({"X"}, {"X", "Y"}, False), (1.0, 1, True)):
            with self.subTest(current=current, expected=expected):
                self.assertIs(program_transfers.held_exactly(current, expected), same)


class SetSettingsTests(unittest.TestCase):
    def test_a_staged_negative_zero_is_written(self):
        target = Recording(offset=0.0, inverse_matrix=IDENTITY)
        program_transfers._set_settings(target, {"offset": -0.0, "inverse_matrix": NEG_IDENTITY}, {})
        self.assertEqual(target.writes, ["offset", "inverse_matrix"])
        self.assertEqual(signs(target.offset), "-0.0")
        self.assertEqual(signs(target.inverse_matrix), signs(NEG_IDENTITY))

    def test_a_value_held_exactly_is_still_not_rewritten(self):
        target = Recording(seed=0, offset=-0.0, use_x=True, inverse_matrix=NEG_IDENTITY)
        program_transfers._set_settings(target, {"seed": 0, "offset": -0.0, "use_x": True,
                                                 "inverse_matrix": NEG_IDENTITY}, {})
        self.assertEqual(target.writes, [])


class ModifierStoreTests(unittest.TestCase):
    def test_a_restored_store_takes_its_negative_zero(self):
        store = Recording(value=0.0, vector=(0.0, 1.0))
        modifier_state._put(store, {"value": -0.0, "vector": (-0.0, 1.0)}, {}, "QA")
        self.assertEqual(store.writes, ["value", "vector"])
        self.assertEqual(signs(store.value), "-0.0")
        self.assertEqual(signs(store.vector), ["-0.0", "1.0"])

    def test_a_store_held_exactly_is_not_rewritten(self):
        store = Recording(value=-0.0, vector=(-0.0, 1.0), seed=0)
        modifier_state._put(store, {"value": -0.0, "vector": (-0.0, 1.0), "seed": 0}, {}, "QA")
        self.assertEqual(store.writes, [])


if __name__ == "__main__":
    unittest.main()
