# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the actual GUI fixture's exact correspondence without importing Blender."""
import array
import ast
import copy
import math
from pathlib import Path
import struct
import unittest


SOURCE = Path(__file__).with_name('test_blender_sculpt_checkpoint_bundle.py')
FUNCTIONS = {'dyntopo_new_vertex_bijection', 'continued_stroke_equal',
             'equivalent_after_load', 'restored_equal', 'check'}


class DyntopoStrokeCorrespondence(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name in FUNCTIONS]
        self.assertEqual({node.name for node in selected}, FUNCTIONS)
        self.ns = {'array': array, 'math': math, 'VARIANT': 'dyntopo', 'LABELS': ('A',),
                   'REPORT': {'checks': {}, 'comparisons': {}}}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        self.left = {'positions': [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.],
                                  [.25, .25, .5], [.5, .25, .75], [.25, .5, 1.]],
                     'masks': [0., .25, .5, .75, 1., .125],
                     'hidden': [False, True, False, False, True, False],
                     'topology': [[0, 3, 1], [1, 3, 4], [1, 4, 2], [2, 4, 5],
                                  [2, 5, 0], [0, 5, 3], [3, 5, 4]]}
        self.mapping = [0, 1, 2, 5, 3, 4]
        self.right = self.permute(self.left, self.mapping)

    @staticmethod
    def permute(value, mapping):
        result = copy.deepcopy(value)
        for key in ('positions', 'masks', 'hidden'):
            for left, right in enumerate(mapping):
                result[key][right] = copy.deepcopy(value[key][left])
        result['topology'] = [[mapping[v] for v in face[1:] + face[:1]]
                              for face in reversed(value['topology'])]
        return result

    @staticmethod
    def next_float(value):
        bits = struct.unpack('<I', struct.pack('<f', value))[0]
        return struct.unpack('<f', struct.pack('<I', bits + 1))[0]

    def compare(self):
        return self.ns['dyntopo_new_vertex_bijection'](self.left, self.right, 3)

    def test_exact_new_vertex_permutation_preserves_all_values_and_oriented_faces(self):
        before = copy.deepcopy((self.left, self.right))
        self.assertEqual(self.compare(), self.mapping)
        self.assertEqual((self.left, self.right), before)

    def test_identity_mapping_is_valid(self):
        self.right = copy.deepcopy(self.left)
        self.assertEqual(self.compare(), list(range(6)))

    def test_one_float32_step_new_coordinate_rejected(self):
        self.right['positions'][3][0] = self.next_float(self.right['positions'][3][0])
        with self.assertRaisesRegex(AssertionError, 'new vertex position changed'):
            self.compare()

    def test_original_vertex_coordinate_change_rejected(self):
        self.right['positions'][1][0] = self.next_float(1.)
        with self.assertRaisesRegex(AssertionError, 'original vertex changed'):
            self.compare()

    def test_signed_zero_change_is_not_a_tolerance(self):
        self.right['positions'][0][0] = -0.0
        with self.assertRaisesRegex(AssertionError, 'original vertex changed'):
            self.compare()

    def test_original_vertex_renumbering_with_consistent_topology_rejected(self):
        self.right = self.permute(self.left, [1, 0, 2, 3, 4, 5])
        with self.assertRaisesRegex(AssertionError, 'original vertex changed'):
            self.compare()

    def test_one_float32_step_mask_rejected(self):
        self.right['masks'][3] = self.next_float(self.right['masks'][3])
        with self.assertRaisesRegex(AssertionError, 'mask bits changed'):
            self.compare()

    def test_hidden_flag_change_rejected(self):
        self.right['hidden'][3] = not self.right['hidden'][3]
        with self.assertRaisesRegex(AssertionError, 'hidden flag changed'):
            self.compare()

    def test_reversed_winding_rejected(self):
        self.right['topology'][0].reverse()
        with self.assertRaisesRegex(AssertionError, 'oriented connectivity/multiplicity changed'):
            self.compare()

    def test_missing_face_rejected(self):
        self.right['topology'].pop()
        with self.assertRaisesRegex(AssertionError, 'oriented connectivity/multiplicity changed'):
            self.compare()

    def test_duplicate_face_rejected(self):
        self.right['topology'].append(list(self.right['topology'][0]))
        with self.assertRaisesRegex(AssertionError, 'oriented connectivity/multiplicity changed'):
            self.compare()

    def test_changed_connectivity_with_identical_point_cloud_rejected(self):
        self.right['topology'][0] = [0, 1, 2]
        with self.assertRaisesRegex(AssertionError, 'oriented connectivity/multiplicity changed'):
            self.compare()

    def test_ambiguous_new_coordinate_rejected(self):
        self.right['positions'][4] = list(self.right['positions'][3])
        with self.assertRaisesRegex(AssertionError, 'ambiguous duplicate position'):
            self.compare()

    def test_missing_vertex_rejected(self):
        self.right['positions'].pop()
        with self.assertRaisesRegex(AssertionError, 'vertex count/refinement differs'):
            self.compare()

    def test_out_of_range_face_index_rejected(self):
        self.right['topology'][0][0] = 6
        with self.assertRaisesRegex(AssertionError, 'invalid face vertex'):
            self.compare()

    def test_truncated_mask_payload_rejected(self):
        self.right['masks'].pop()
        with self.assertRaisesRegex(AssertionError, 'invalid finite float payload'):
            self.compare()

    @staticmethod
    def observation(bmesh):
        return {'bmesh': copy.deepcopy(bmesh), 'artists': {'A': {'mesh_uid': 7,
                'object_uid': 8, 'scene_uid': 9, 'brush_settings': {'sculpt_plane': 'Y'}}},
                'original_sculpt_storage': {7: {'mdisps': b'original storage'}},
                'coarse_meshes': {'A': {'positions': b'original coarse', 'shape_keys': None}},
                'inventory': {'main_flush_needed': True, 'surfaces': []}, 'window_count': 1}

    def stroke_check(self, left=None, right=None):
        left = self.observation(self.left) if left is None else left
        right = self.observation(self.right) if right is None else right
        self.ns['continued_stroke_equal']('matched', left, right,
                                          {'bmesh': {'positions': [None] * 3}})

    def test_actual_stroke_wrapper_records_complete_bijection(self):
        self.stroke_check()
        result = self.ns['REPORT']['comparisons']['matched']
        self.assertTrue(result['exact_payloads'])
        self.assertEqual(result['new_vertex_correspondence']['left_to_right'], self.mapping)

    def test_stroke_wrapper_still_rejects_coarse_storage_change(self):
        right = self.observation(self.right)
        right['coarse_meshes']['A']['positions'] = b'corrupted coarse'
        with self.assertRaisesRegex(AssertionError, 'all_semantic_owners_and_payload_bits'):
            self.stroke_check(right=right)

    def test_stroke_wrapper_still_rejects_original_displacement_change(self):
        right = self.observation(self.right)
        right['original_sculpt_storage'][7]['mdisps'] = b'corrupted displacement'
        with self.assertRaisesRegex(AssertionError, 'all_semantic_owners_and_payload_bits'):
            self.stroke_check(right=right)

    def test_stroke_wrapper_still_rejects_brush_plane_change(self):
        right = self.observation(self.right)
        right['artists']['A']['brush_settings']['sculpt_plane'] = 'AREA'
        with self.assertRaisesRegex(AssertionError, 'all_semantic_owners_and_payload_bits'):
            self.stroke_check(right=right)

    def test_complete_baseline_does_not_allow_even_valid_new_vertex_permutation(self):
        with self.assertRaisesRegex(AssertionError, 'all_semantic_owners_and_payload_bits'):
            self.ns['restored_equal']('baseline', self.observation(self.left), self.observation(self.right))

    def test_bad_geometry_is_recorded_as_failure_in_actual_stroke_wrapper(self):
        self.right['hidden'][3] = not self.right['hidden'][3]
        with self.assertRaisesRegex(AssertionError, 'all_semantic_owners_and_payload_bits'):
            self.stroke_check()
        result = self.ns['REPORT']['comparisons']['matched']
        self.assertEqual(result['changed_fields'], ['bmesh'])
        self.assertEqual(result['new_vertex_correspondence']['error'], 'hidden flag changed')


if __name__ == '__main__':
    unittest.main()
