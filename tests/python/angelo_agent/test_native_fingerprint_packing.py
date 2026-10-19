# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact numeric/byte packing and bounded ID-property admission without bpy."""

from array import array
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.program_fingerprint_packing import packed_attribute_values, packed_bytes_value
from angelo_agent.program_dependencies import _custom_properties
from angelo_agent.program_jobs import ProgramError


def prop(name, kind, shape=()):
    return SimpleNamespace(identifier=name, type=kind, is_array=bool(shape),
                           array_dimensions=tuple(shape) + (0,) * (3 - len(shape)),
                           array_length=math.prod(shape) if shape else 0, is_readonly=False)


class NumericData:
    def __init__(self, properties, values, count):
        self.first = SimpleNamespace(bl_rna=SimpleNamespace(identifier='TestNumericValue', properties=properties))
        self.values, self.count, self.reads = values, count, []

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        assert index == 0
        return self.first

    def foreach_get(self, name, output):
        self.reads.append(name)
        output[:] = array(output.typecode, self.values[name])


def attribute(name='Unused', kind='FLOAT', domain='POINT'):
    return SimpleNamespace(name=name, data_type=kind, domain=domain)


class OversizedArray:
    def __len__(self):
        return 1000000000

    def __iter__(self):
        raise AssertionError('Oversized array was iterated before admission')

    def to_list(self):
        raise AssertionError('Oversized array was materialized before admission')


class OversizedGroup:
    def __len__(self):
        return 1000000000

    def keys(self):
        raise AssertionError('Oversized group was expanded before admission')

    def to_dict(self):
        raise AssertionError('Oversized group was materialized before admission')


class FingerprintPackingTests(unittest.TestCase):
    def test_every_numeric_field_is_fresh_tagged_and_accounted(self):
        data = NumericData([prop('value', 'FLOAT'), prop('vector', 'INT', (2,))],
                           {'value': [.1, .2], 'vector': [1, 2, 3, 4]}, 2)
        budget = [100]
        first = packed_attribute_values(attribute(), data, budget=budget, skip_names=set())
        self.assertEqual(data.reads, ['value', 'vector'])
        self.assertEqual(budget[0], 90)  # 2 * (child + scalar + array + two leaves).
        fields = first['packed_numeric_attribute_v1']
        self.assertEqual([field['shape'] for field in fields], [[], [2]])
        data.values['vector'][-1] = 5
        changed = packed_attribute_values(attribute(), data, budget=[100], skip_names=set())
        self.assertNotEqual(first, changed)
        self.assertEqual(data.reads, ['value', 'vector'] * 2)
        self.assertNotEqual(first, packed_attribute_values(attribute(name='Renamed'), data, budget=[100], skip_names=set()))
        self.assertNotEqual(changed, packed_attribute_values(attribute(domain='EDGE'), data, budget=[100], skip_names=set()))

    def test_budget_fails_before_numeric_buffer_allocation(self):
        data = NumericData([prop('vector', 'FLOAT', (3,))], {}, 1000000000)
        with patch('angelo_agent.program_fingerprint_packing.array', side_effect=AssertionError('Allocated')):
            with self.assertRaises(ProgramError):
                packed_attribute_values(attribute(), data, budget=[100], skip_names=set())
        self.assertEqual(data.reads, [])

    def test_matrix_budget_preserves_nested_array_work_and_depth(self):
        data = NumericData([prop('value', 'FLOAT', (4, 4))], {'value': [0] * 16}, 1)
        budget = [22]
        self.assertIsNotNone(packed_attribute_values(attribute(), data, budget=budget, skip_names=set()))
        self.assertEqual(budget, [0])
        self.assertIsNone(packed_attribute_values(attribute(), data, budget=[100], skip_names=set(), child_depth=14))

    def test_unsupported_fields_and_access_keep_generic_fallback(self):
        for kind in ('STRING', 'POINTER', 'COLLECTION', 'ENUM'):
            data = NumericData([prop('value', kind)], {}, 4)
            budget = [100]
            self.assertIsNone(packed_attribute_values(attribute(), data, budget=budget, skip_names=set()))
            self.assertEqual(budget, [100])
            self.assertEqual(data.reads, [])
        data = NumericData([prop('value', 'FLOAT')], {}, 1)
        data.foreach_get = lambda *_args: (_ for _ in ()).throw(TypeError('Unsupported storage'))
        budget = [10]
        self.assertIsNone(packed_attribute_values(attribute(), data, budget=budget, skip_names=set()))
        self.assertEqual(budget, [10])

    def test_nonfinite_and_negative_zero_are_not_hidden(self):
        data = NumericData([prop('value', 'FLOAT')], {'value': [0.]}, 1)
        zero = packed_attribute_values(attribute(), data, budget=[10], skip_names=set())
        data.values['value'] = [-0.]
        self.assertNotEqual(zero, packed_attribute_values(attribute(), data, budget=[10], skip_names=set()))
        for bad in (math.nan, math.inf, -math.inf):
            data.values['value'] = [bad]
            with self.assertRaises(ProgramError):
                packed_attribute_values(attribute(), data, budget=[10], skip_names=set())

    def test_byte_buffers_keep_all_bytes_and_legacy_budget(self):
        budget = [4]
        first = packed_bytes_value(b'\x00\x00\x00\xff', budget=budget)
        self.assertEqual(budget, [0])
        self.assertNotEqual(first, packed_bytes_value(b'\x00\x00\x01\xff', budget=[4]))
        self.assertNotEqual(first, packed_bytes_value(b'\x00\x00\x00\xff\x00', budget=[5]))
        with self.assertRaises(ProgramError):
            packed_bytes_value(b'12345', budget=[4])

    def test_two_slot_budget_charges_hashed_bytes_to_the_packed_slot(self):
        data = NumericData([prop('value', 'FLOAT'), prop('vector', 'FLOAT', (3,))],
                           {'value': [1., 2.], 'vector': [0.] * 6}, 2)
        budget = [10, 1000]
        packed_attribute_values(attribute(), data, budget=budget, skip_names=set())
        self.assertEqual(budget, [10, 1000 - 2 * (4 + 12)])  # Items untouched; 32 bytes hashed.
        with self.assertRaises(ProgramError):
            packed_attribute_values(attribute(), data, budget=[10, 31], skip_names=set())
        self.assertEqual(data.reads, ['value', 'vector'])  # The refusal allocated nothing.
        budget = [3, 8]
        packed_bytes_value(b'12345678', budget=budget)
        self.assertEqual(budget, [3, 0])
        with self.assertRaises(ProgramError):
            packed_bytes_value(b'123456789', budget=[1000, 8])

    def test_fingerprint_budget_separates_recursive_items_from_packed_bytes(self):
        from angelo_agent.program_dependencies import (MAX_FINGERPRINT_ITEMS, MAX_FINGERPRINT_PACKED_BYTES,
                                                        fingerprint_budget)
        first, second = fingerprint_budget(), fingerprint_budget()
        self.assertEqual(first, [MAX_FINGERPRINT_ITEMS, MAX_FINGERPRINT_PACKED_BYTES])
        self.assertIsNot(first, second)

    def test_custom_property_arrays_and_groups_admitted_before_materialization(self):
        for value in (OversizedArray(), OversizedGroup()):
            with self.assertRaises(ProgramError):
                _custom_properties({'source': value}, budget=[100])
        self.assertEqual(_custom_properties({'a': [1, 2.0], 'group': {'value': 'abc'}}),
                         {'a': [1, 2.0], 'group': {'value': 'abc'}})
        with self.assertRaises(ProgramError):
            _custom_properties({'a': 'x' * 100}, budget=[10])
        nested = 1
        for _ in range(18):
            nested = {'a': nested}
        with self.assertRaises(ProgramError):
            _custom_properties({'source': nested})
        with self.assertRaises(ProgramError):
            _custom_properties({'source': math.nan})


if __name__ == '__main__':
    unittest.main()
