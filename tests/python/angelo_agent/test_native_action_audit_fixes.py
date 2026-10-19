# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure precision, effective-basis, UV and planned-topology contracts."""

import copy
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.action_catalog import validate_operation
from angelo_agent.action_recipes import expand_recipe
from angelo_agent.action_topology import topology_digest, MAX_CORNERS
from angelo_agent.scene_validation import VerificationContext, observe, verify
from test_native_scene_validation import cube


def rig(bones):
    return {'kind': 'armature_create', 'target': None, 'parameters': {'bones': bones}}


class ActionAuditContracts(unittest.TestCase):
    def test_float32_collapse_and_native_lengthening_are_rejected(self):
        for head, tail in (([10000, 0, 0], [10000.0001, 0, 0]),
                           ([0, 0, 0], [0, 0, .000001])):
            with self.subTest(head=head), self.assertRaisesRegex(ValueError, 'float32'):
                validate_operation(rig([{'name': 'root', 'head': head, 'tail': tail}]))

    def test_representable_bones_are_canonical_and_idempotent(self):
        original = rig([{'name': 'root', 'head': [10000, 0, 0], 'tail': [10000.001, .1, 0]}])
        canonical = validate_operation(original)
        bone = canonical['parameters']['bones'][0]
        self.assertEqual(bone['tail'][0], 10000.0009765625)
        self.assertNotEqual(bone['tail'][1], .1)
        self.assertEqual(validate_operation(canonical), canonical)
        self.assertEqual(original['parameters']['bones'][0]['tail'], [10000.001, .1, 0])

    def test_connected_bones_cannot_silently_snap_endpoints(self):
        bones = [{'name': 'root', 'head': [0, 0, 0], 'tail': [0, 0, 1]},
                 {'name': 'tip', 'head': [.000001, 0, 1], 'tail': [0, 0, 2],
                  'parent': 'root', 'connected': True}]
        with self.assertRaisesRegex(ValueError, 'exactly'):
            validate_operation(rig(bones))
        bones[1]['head'] = [0, 0, 1]
        self.assertEqual(len(validate_operation(rig(bones))['parameters']['bones']), 2)

    def test_fit_uses_effective_basis_not_inactive_euler_values(self):
        obj = cube()
        angle = math.pi / 4
        obj.matrix_world = [[math.cos(angle), -math.sin(angle), 0, 0],
                            [math.sin(angle), math.cos(angle), 0, 0],
                            [0, 0, 1, 0], [0, 0, 0, 1]]
        observation = observe(VerificationContext({'$cube': obj}), '$cube')
        params = {'dimensions': [4, 1, 3], 'minimum_scale': .001,
                  'maximum_scale': 100, 'maximum_scale_ratio': 100}
        self.assertEqual(observation['transforms']['rotation_euler'], [0, 0, 0])
        with self.assertRaisesRegex(ValueError, 'axis-aligned'):
            expand_recipe('fit-dimensions', '$cube', params, observation)
        del observation['effective_linear_transform']
        with self.assertRaisesRegex(ValueError, 'effective world basis'):
            expand_recipe('fit-dimensions', '$cube', params, observation)

    def test_fit_retains_axis_aligned_delta_scale_support(self):
        obj = cube()
        obj.matrix_world[0][0], obj.matrix_world[1][1] = 2, .5
        observation = observe(VerificationContext({'$cube': obj}), '$cube')
        expanded = expand_recipe('fit-dimensions', '$cube', {
            'dimensions': [8, 2, 3], 'minimum_scale': .001,
            'maximum_scale': 100, 'maximum_scale_ratio': 100}, observation)
        self.assertEqual(expanded['operations'][0]['parameters']['scale'], [2, 2, 1.5])

    def test_live_edit_uv_is_inconclusive_even_with_empty_original_rna(self):
        obj = cube()
        obj.mode = 'EDIT'
        obj.data.uv_layers.append(NS(name='UVMap', uv=[]))
        result = verify(VerificationContext({'$cube': obj}), [{'kind': 'uv_layer', 'target': '$cube',
            'name': 'UVMap', 'require_nonempty': True, 'require_finite': True}])
        self.assertEqual(result['status'], 'inconclusive')
        self.assertIn('Edit-mode', result['checks'][0]['reason'])
        self.assertFalse(result['passed'])

    def test_explicit_clean_faces_need_a_bound_lineage_plan(self):
        params = {'vertex_indices': [0, 1], 'face_indices': [1], 'distance': .0001}
        with self.assertRaisesRegex(ValueError, 'lineage'):
            expand_recipe('clean-mesh', '$cube', params)
        observation = {'target': '$cube', 'status': 'pass', 'clean_mesh_face_plan': {
            'face_indices': [0], 'topology_digest': 'a' * 64}}
        expanded = expand_recipe('clean-mesh', '$cube', params, observation)
        normals = expanded['operations'][1]['parameters']
        self.assertEqual((normals['face_indices'], normals['index_scope'], normals['topology_step']), ([0], 'CURRENT', 1))
        self.assertEqual(normals['topology_digest'], 'a' * 64)
        self.assertEqual(params['face_indices'], [1])
        observation['clean_mesh_face_plan']['face_indices'] = []
        with self.assertRaises(ValueError):
            expand_recipe('clean-mesh', '$cube', params, observation)

    def test_topology_digest_requires_explicit_current_faces(self):
        base = {'kind': 'mesh_recalculate_normals', 'target': '$cube',
                'parameters': {'topology_digest': 'a' * 64, 'face_indices': [0]}}
        with self.assertRaisesRegex(ValueError, 'CURRENT'):
            validate_operation(base)
        base['parameters'].update(index_scope='CURRENT', topology_step=1)
        self.assertEqual(validate_operation(base)['parameters']['topology_digest'], 'a' * 64)
        for bad in ('a' * 63, '../scene', True):
            changed = copy.deepcopy(base)
            changed['parameters']['topology_digest'] = bad
            with self.assertRaises(ValueError):
                validate_operation(changed)

    def test_topology_binding_includes_coordinate_and_index_order(self):
        mesh = cube().data
        original = topology_digest(mesh)
        self.assertEqual(topology_digest(mesh), original)
        mesh.vertices[0].co[0] += .1
        self.assertNotEqual(topology_digest(mesh), original)
        mesh.vertices[0].co[0] -= .1
        mesh.polygons.reverse()
        self.assertNotEqual(topology_digest(mesh), original)

    def test_topology_binding_rejects_large_corners_before_iteration(self):
        class Huge:
            def __len__(self):
                return MAX_CORNERS + 1

            def __iter__(self):
                raise AssertionError('Oversized data must not be read')
        mesh = cube().data
        mesh.loops = Huge()
        with self.assertRaisesRegex(ValueError, 'bounded'):
            topology_digest(mesh)


if __name__ == '__main__':
    unittest.main()
