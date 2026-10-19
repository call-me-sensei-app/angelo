# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Region admission and exact silhouette metrics, without a Blender process.

NumPy cases also run with Angelo's bundled standalone Python when the host
Python has no NumPy. Blender-facing imports are isolated to this test module.
"""
import copy
import importlib.util
import json
import math
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
try:
    import numpy as np
except ImportError:
    np = None


class Vector(list):
    def to_tuple(self):
        return tuple(self)


class Identity:
    def __matmul__(self, value):
        return Vector(value)

    def inverted(self):
        return self


class PreflightError(RuntimeError):
    partial_changes_possible = False


spec = importlib.util.spec_from_file_location(
    'angelo_agent._metrics_fixes_test_workflow', ROOT / 'scripts/addons_core/angelo_agent/sculpt_workflow.py')
workflow = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'bpy': SimpleNamespace(), 'numpy': np or SimpleNamespace(),
                             'mathutils': SimpleNamespace(Vector=Vector)}):
    spec.loader.exec_module(workflow)


def region_record():
    return {'anchor_version': 2, 'vertex': 3, 'topology': 'a' * 64, 'radius': .2, 'face_set': 0,
            'requested_world': [0, -1, 0], 'accepted_world': [.02, -1, 0]}


class RegionAnchorTests(unittest.TestCase):
    def setUp(self):
        self.state = {'topology_revision': 'a' * 64, 'vertices': 8}

    def test_validated_radius_includes_actual_anchor(self):
        record = region_record()
        self.assertEqual(workflow._region_controls(record, self.state), (3, .2, 0))
        record['accepted_world'] = [.1, -1, 0]
        workflow._region_controls(record, self.state)
        record['accepted_world'][0] = math.nextafter(.1, math.inf)
        with self.assertRaisesRegex(ValueError, 'footprint'):
            workflow._region_controls(record, self.state)

    def test_legacy_and_malformed_records_cannot_reach_native_index(self):
        bad_records = [None, [], 'old', 5, True, {},
                       {k: v for k, v in region_record().items() if k != 'anchor_version'}]
        for field, bad in [('anchor_version', True), ('vertex', True), ('vertex', -1), ('vertex', 8),
                           ('radius', math.nan), ('radius', True), ('radius', 10 ** 1000), ('face_set', False),
                           ('topology', 'b' * 64), ('requested_world', [0, -1]),
                           ('accepted_world', [0, math.inf, 0]), ('accepted_world', [0, False, 0]),
                           ('requested_world', 'abc'), ('accepted_world', [10 ** 1000, 0, 0])]:
            bad_records.append(dict(region_record(), **{field: bad}))
        native_vertex = Mock(side_effect=AssertionError('Invalid metadata reached native code'))
        obj = SimpleNamespace(get=lambda key, default=None: json.dumps({'Cheek': current}),
                              angelo_sculpt_vertex=native_vertex, matrix_world=Identity())
        with patch.dict(sys.modules, {'angelo_agent.sculpt': SimpleNamespace(SculptPreflightError=PreflightError)}):
            for current in bad_records:
                with self.subTest(record=current), self.assertRaises(PreflightError):
                    workflow.resolve_region(obj, self.state, 'Cheek')
        native_vertex.assert_not_called()

    def test_live_vertex_can_move_after_valid_admission(self):
        obj = SimpleNamespace(get=lambda key, default=None: json.dumps({'Cheek': region_record()}),
                              angelo_sculpt_vertex=lambda index: [.5, -1.2, 0], matrix_world=Identity())
        with patch.dict(sys.modules, {'angelo_agent.sculpt': SimpleNamespace(SculptPreflightError=PreflightError)}):
            result = workflow.resolve_region(obj, self.state, 'Cheek')
        self.assertEqual(result['position'], (.5, -1.2, 0))
        self.assertEqual(set(result), {'name', 'vertex', 'topology', 'radius', 'face_set', 'position'})

    def test_invalid_live_coordinates_fail_closed(self):
        obj = SimpleNamespace(get=lambda key, default=None: json.dumps({'Cheek': region_record()}),
                              angelo_sculpt_vertex=lambda index: [math.nan, 0, 0], matrix_world=Identity())
        with patch.dict(sys.modules, {'angelo_agent.sculpt': SimpleNamespace(SculptPreflightError=PreflightError)}):
            with self.assertRaisesRegex(PreflightError, 'finite'):
                workflow.resolve_region(obj, self.state, 'Cheek')

    def test_coarse_surface_rejects_distant_anchor_before_writing_metadata(self):
        class Object(dict):
            mode = 'SCULPT'
            matrix_world = Identity()
            angelo_sculpt_query = Mock(return_value=(True, [0, -1, 0], [0, -1, 0], 0, 3))
            angelo_sculpt_vertex = Mock(return_value=[1, -1, -1])
        obj = Object({workflow.REGIONS_KEY: json.dumps({'Existing': region_record()})})
        before = dict(obj)
        args = dict(name='Face center', position=[0, -1, 0], radius=.1, face_set=0)
        module = SimpleNamespace(SculptPreflightError=PreflightError, snapshot=Mock(), public_state=Mock(),
                                 change_report=Mock(), partial_edit=Mock())
        with patch.dict(sys.modules, {'angelo_agent.sculpt': module}):
            with self.assertRaisesRegex(PreflightError, '1.41421.*0.05.*working resolution') as error:
                workflow.define_region(None, obj, self.state, args)
        self.assertFalse(error.exception.partial_changes_possible)
        self.assertEqual(dict(obj), before)
        module.snapshot.assert_not_called()
        module.partial_edit.assert_not_called()


@unittest.skipIf(np is None, 'NumPy is available in the bundled Angelo Python runtime')
class ExactOutlineTests(unittest.TestCase):
    @staticmethod
    def oracle(points, size):
        # Independent, deliberately slow pixel-center ray parity reference.
        rows = []
        for row in range(size):
            y = (row + .5) / size
            pixels = []
            for column in range(size):
                x = (column + .5) / size
                inside = False
                if points:
                    ax, ay = points[-1]
                    for bx, by in points:
                        if (ay > y) != (by > y):
                            inside ^= x < (bx - ax) * (y - ay) / (by - ay) + ax
                        ax, ay = bx, by
                pixels.append(inside)
            rows.append(pixels)
        return np.array(rows, dtype=bool)

    def assert_exact(self, points, size):
        before = copy.deepcopy(points)
        actual = workflow.polygon_mask(points, size)
        np.testing.assert_array_equal(actual, self.oracle(points, size))
        self.assertEqual(points, before)
        self.assertEqual(actual.dtype, np.bool_)

    def test_horizontal_degenerate_and_repeated_vertices(self):
        for outline in ([], [[.2, .2]], [[.2, .2], [.8, .8]],
                        [[.1, .5], [.5, .5], [.9, .5]],
                        [[.2, .2], [.8, .2], [.8, .2], [.8, .8], [.2, .8], [.2, .2]]):
            with self.subTest(outline=outline):
                self.assert_exact(outline, 17)

    def test_pixel_center_boundary_and_nearly_horizontal_edges(self):
        a, b = 3.5 / 17, 12.5 / 17
        for outline in ([[a, a], [b, a], [b, b], [a, b]],
                        [[a, .5 - 1e-15], [b, .5 + 1e-15], [.5, .8]],
                        [[a, a], [b, b], [a, b]], [[0, 0], [1, 0], [1, 1], [0, 1]]):
            self.assert_exact(outline, 17)

    def test_self_intersections_and_outside_frame_are_even_odd(self):
        for outline in ([[.1, .1], [.9, .9], [.9, .1], [.1, .9]],
                        [[-.2, .2], [1.2, .2], [.5, 1.2]],
                        [[-.3, -.3], [-.2, -.1], [-.1, -.3]],
                        [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0],
                         [.3, .3], [.3, .7], [.7, .7], [.7, .3], [.3, .3], [0, 0]]):
            self.assert_exact(outline, 29)

    def test_randomized_polygons_match_reference_without_raster_rounding(self):
        rng = random.Random(472159)
        for _ in range(40):
            outline = [[rng.uniform(-.2, 1.2), rng.uniform(-.2, 1.2)] for _ in range(rng.randrange(3, 129))]
            self.assert_exact(outline, 19)

    def test_pixel_center_vertices_preserve_floating_point_crossing_boundaries(self):
        rng = random.Random(4242)
        for _ in range(40):
            outline = [[(rng.randrange(19) + .5) / 19, (rng.randrange(19) + .5) / 19]
                       for _ in range(rng.randrange(3, 30))]
            self.assert_exact(outline, 19)

    def test_full_size_128_edge_goal_matches_full_grid_formula(self):
        size = 768
        outline = [[.5 + .43 * math.cos(i * math.tau / 128),
                    .5 + .37 * math.sin(i * math.tau / 128)] for i in range(128)]
        y, x = np.mgrid[:size, :size]
        x, y = (x + .5) / size, (y + .5) / size
        expected = np.zeros((size, size), dtype=bool)
        ax, ay = outline[-1]
        for bx, by in outline:
            if ay != by:
                expected ^= ((ay > y) != (by > y)) & (x < (bx - ax) * (y - ay) / (by - ay) + ax)
            ax, ay = bx, by
        np.testing.assert_array_equal(workflow.polygon_mask(outline, size), expected)

    def test_invalid_inputs_cannot_allocate_unbounded_masks(self):
        for size in (0, 769, True, 12.5):
            with self.assertRaises(ValueError):
                workflow.polygon_mask([[0, 0], [1, 0], [0, 1]], size)
        for outline in ([[0, 0, 0]] * 3, [[0, math.nan]] * 3, [[0, 0]] * 129):
            with self.assertRaises(ValueError):
                workflow.polygon_mask(outline, 17)


if __name__ == '__main__':
    unittest.main()
