# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged refusal authority: fixable input, native invariants, and bounded work.

Production functions run against small stand-ins; these tests do not validate
Blender's operators, isolation, Undo, or native shape-key clamping.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ADDON = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent'
sys.path.insert(0, str(ADDON.parent))
from angelo_agent.refusal_codes import RefusalError, refusal_code  # noqa: E402


def load_kernel(name, stubs):
    spec = importlib.util.spec_from_file_location('angelo_agent._test_' + name, ADDON / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class ShapeKey:
    name = 'Shape'
    slider_min = 0.
    slider_max = 1.

    def __init__(self, ignore=False):
        self.ignore, self.writes, self._value = ignore, 0, 0.

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self.writes += 1
        if not self.ignore:
            self._value = value


class ShapeKeyRefusalTests(unittest.TestCase):
    def setUp(self):
        context = NS(_mesh=lambda *args: None, _readback=lambda *args, **kwargs: kwargs)
        self.kernel = load_kernel('action_animation', {'bpy': NS(), 'angelo_agent.action_context': context})

    def apply(self, block, value, *, add=False):
        key = NS(key_blocks={'Shape': block} if not add else {}, reference_key=object(), animation_data=None)
        mesh = NS(vertices=[None] * 3, shape_keys=key)
        obj = NS(shape_key_add=lambda **kwargs: block)
        params = {'name': 'Shape', 'value': value, 'frame': None, 'slider_min': 0., 'slider_max': 1.}
        with patch.object(self.kernel, '_shape_keys', return_value=(mesh, key)):
            operation = {'parameters': params}
            return (self.kernel.add_shape_key if add else self.kernel.set_shape_key)(None, obj, operation)

    def test_out_of_range_add_and_set_are_correctable_before_value_assignment(self):
        for add in (False, True):
            with self.subTest(add=add):
                block = ShapeKey()
                with self.assertRaises(RefusalError) as raised:
                    self.apply(block, 2., add=add)
                self.assertEqual(refusal_code(raised.exception), 'VALIDATION')
                self.assertEqual(block.writes, 0)

    def test_in_range_ignored_write_is_an_internal_fault(self):
        for add in (False, True):
            with self.subTest(add=add):
                block = ShapeKey(ignore=True)
                with self.assertRaises(RefusalError) as raised:
                    self.apply(block, .5, add=add)
                self.assertEqual(refusal_code(raised.exception), 'INTERNAL_ERROR')
                self.assertEqual(block.writes, 1)

    def test_in_range_set_still_returns_the_written_value(self):
        block = ShapeKey()
        self.assertEqual(self.apply(block, .5)['value'], .5)
        self.assertEqual(block.writes, 1)


class StageGuardRefusalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = load_kernel('action_context', {
            'bpy': NS(), 'numpy': NS(), 'mathutils': NS(Vector=tuple),
        })

    def test_unowned_data_never_becomes_an_input_correction(self):
        block = type('Block', (), {'library': None})()
        with self.assertRaises(RefusalError) as raised:
            self.kernel.StagedActionContext.require_private(NS(owned_ids=set()), block)
        self.assertEqual(refusal_code(raised.exception), 'INTERNAL_ERROR')

    def test_selected_index_outside_the_mesh_is_correctable(self):
        with self.assertRaises(RefusalError) as raised:
            self.kernel._selected([object()], [1])
        self.assertEqual(refusal_code(raised.exception), 'VALIDATION')

    def test_topology_generation_mismatch_requires_fresh_observation(self):
        obj = object()
        operation = {'kind': 'mesh_extrude', 'parameters': {
            'face_indices': [0], 'index_scope': 'CURRENT', 'topology_step': 1}}
        with self.assertRaises(RefusalError) as raised:
            self.kernel._guard_index_scope(NS(topology_versions={obj: 2}), obj, operation)
        self.assertEqual(refusal_code(raised.exception), 'STALE_REVISION')

    def test_existing_subdivision_limit_is_a_bounded_input_refusal(self):
        mesh = Mock(return_value=NS(loops=[None] * 24))
        context = NS(_mesh=mesh,
                     _counts=lambda mesh: {'vertices': 8, 'edges': 12, 'faces': 6},
                     _require_subdivision=lambda levels: None)
        kernel = load_kernel('action_budget', {
            'angelo_agent.action_context': context,
            'angelo_agent.action_node_graphs': NS(_node_parameters=lambda *args: {}),
        })
        obj = type('Object', (), {'modifiers': [NS(type='SUBSURF', levels=4, render_levels=4)]})()
        with self.assertRaises(RefusalError) as raised:
            kernel._modifier_bound(None, obj)
        self.assertEqual(refusal_code(raised.exception), 'LIMIT_EXCEEDED')
        mesh.assert_called_once_with(None, obj, readonly=True)


class InversionRefusalTests(unittest.TestCase):
    def setUp(self):
        self.kernel = load_kernel('action_object', {
            'bpy': NS(), 'mathutils': NS(Euler=tuple, Vector=tuple, Matrix=NS(Identity=lambda size: None)),
            'angelo_agent.action_context': NS(_mesh=lambda *args: None, _readback=lambda *args, **kwargs: kwargs,
                                              _set_initial_transform=lambda *args: None),
            'angelo_agent.action_budget': NS(_update_stage=lambda context: None),
            'angelo_agent.action_edit_context': NS(_edit_override=lambda *args: None),
        })

    def refusal(self, matrix, *, constraint):
        class Constraints(list):
            def get(self, name):
                return None

            def new(self, kind):
                return NS()

        obj = NS(parent=None, constraints=Constraints(), matrix_world=NS(copy=lambda: None))
        context = NS(resolve=lambda reference: NS(matrix_world=matrix))
        with patch.object(self.kernel, '_dependency_reaches', return_value=False):
            with self.assertRaises(RefusalError) as raised:
                if constraint:
                    params = {'target': '$parent', 'name': 'Child', 'type': 'CHILD_OF', 'influence': 1.,
                              'owner_space': 'WORLD', 'target_space': 'WORLD', 'settings': {
                                  'use_location': [True] * 3, 'use_rotation': [True] * 3,
                                  'use_scale': [True] * 3, 'set_inverse': True}}
                    self.kernel._constraint(context, obj, {'parameters': params})
                else:
                    self.kernel._parent(context, obj, {'parameters': {'parent': '$parent', 'keep_transform': True}})
        return raised.exception

    def test_singular_input_is_correctable_in_both_inverse_paths(self):
        def fail():
            raise ValueError('native inverse failed')

        for constraint in (False, True):
            with self.subTest(constraint=constraint):
                error = self.refusal(NS(inverted=fail, determinant=lambda: 0.), constraint=constraint)
                self.assertEqual(refusal_code(error), 'VALIDATION')
                self.assertIsInstance(error.__cause__, ValueError)

    def test_inversion_failure_without_exact_finite_zero_stays_unknown(self):
        def fail():
            raise ValueError('singular matrix')

        def missing():
            raise AttributeError('diagnostic unavailable')

        matrices = [NS(inverted=fail), NS(inverted=fail, determinant=missing)]
        matrices += [NS(inverted=fail, determinant=lambda value=value: value)
                     for value in (1., 1e-300, float('nan'), float('inf'), False, '0')]
        for constraint in (False, True):
            for matrix in matrices:
                with self.subTest(constraint=constraint, matrix=matrix):
                    self.assertEqual(refusal_code(self.refusal(matrix, constraint=constraint)), 'UNKNOWN')

    def test_existing_typed_error_is_not_reclassified_by_a_zero_determinant(self):
        def fail():
            raise RefusalError('native fault', code='INTERNAL_ERROR')

        for constraint in (False, True):
            matrix = NS(inverted=fail, determinant=lambda: 0.)
            self.assertEqual(refusal_code(self.refusal(matrix, constraint=constraint)), 'INTERNAL_ERROR')


if __name__ == '__main__':
    unittest.main()
