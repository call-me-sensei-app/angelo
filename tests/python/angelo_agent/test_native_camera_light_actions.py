# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure camera/light catalog, aim math, check schemas and predicate contracts.

The companion test_blender_camera_light_programs.py runs the real kernels,
staging, commit, Undo and revision checks inside the Angelo binary.
"""

import copy
import json
import math
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.action_catalog import CAPABILITIES, resource_dependencies, validate_operation
from angelo_agent.action_camera_light_spec import (
    CAMERA_RNA, LIGHT_RNA, CAMERA_FIELDS, LIGHT_FIELDS, LIGHT_TYPE_DEFAULTS, aim_error, look_at_euler,
)
from angelo_agent.program_spec import validate_program
from angelo_agent.scene_validation import VerificationContext, verify
from angelo_agent.validation_spec import CHECK_KINDS, check_references, validate_checks

HANDLE = 'scn:' + 'a' * 32 + ':7'


def op(kind, parameters=None, target=None):
    if target is None and kind.endswith('_configure'):
        target = HANDLE
    return {'kind': kind, 'target': target, 'parameters': parameters or {}}


def euler_matrix(angles):
    """Blender XYZ Euler: R = Rz(c) @ Ry(b) @ Rx(a), as row-major rows."""
    a, b, c = angles
    rx = [[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]
    ry = [[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]]
    rz = [[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]]
    multiply = lambda m, n: [[sum(m[i][k] * n[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return multiply(rz, multiply(ry, rx))


def world(rotation, origin):
    return [row + [origin[index]] for index, row in enumerate(rotation)] + [[0, 0, 0, 1]]


class CameraLightCatalogTests(unittest.TestCase):
    def test_create_fills_explicit_type_defaults_idempotently(self):
        camera = validate_operation(op('camera_create', {'alias': 'shot'}))
        self.assertEqual(camera['parameters']['settings'], {
            'projection': 'PERSP', 'sensor_fit': 'AUTO', 'sensor_width': 36., 'sensor_height': 24.,
            'clip_start': .1, 'clip_end': 1000., 'lens': 50.})
        self.assertNotIn('look_at', camera['parameters'])
        self.assertEqual(validate_operation(camera), camera)
        ortho = validate_operation(op('camera_create', {'settings': {'projection': 'ORTHO'}}))['parameters']['settings']
        self.assertEqual((ortho['ortho_scale'], 'lens' in ortho), (6., False))
        for light_type, defaults in LIGHT_TYPE_DEFAULTS.items():
            settings = validate_operation(op('light_create', {'settings': {'light_type': light_type}}))['parameters']['settings']
            with self.subTest(light_type=light_type):
                self.assertEqual({key: settings[key] for key in defaults}, defaults)
                self.assertEqual((settings['color'], settings['use_shadow']), ([1., 1., 1.], True))
        area = validate_operation(op('light_create', {'settings': {'light_type': 'AREA', 'shape': 'RECTANGLE', 'size': 2}}))
        self.assertEqual(area['parameters']['settings']['size_y'], 1.)
        source = op('light_create', {'settings': {'light_type': 'SPOT'}})
        canonical = validate_operation(source)
        canonical['parameters']['settings']['color'][0] = .5
        self.assertEqual(source['parameters']['settings'], {'light_type': 'SPOT'})
        self.assertEqual(validate_operation(source)['parameters']['settings']['color'], [1., 1., 1.])

    def test_configure_keeps_only_supplied_settings(self):
        value = validate_operation(op('light_configure', {'settings': {'energy': 250, 'color': [1, .5, .25]}}))
        self.assertEqual(value['parameters'], {'settings': {'energy': 250., 'color': [1., .5, .25]}})
        self.assertEqual(validate_operation(value), value)
        aimed = validate_operation(op('camera_configure', {'look_at': [0, 0, 0]}))
        self.assertEqual(aimed['parameters'], {'look_at': [0., 0., 0.]})
        # Without a type in the request, type-specific settings are checked by
        # the kernel against the staged object's actual type.
        validate_operation(op('light_configure', {'settings': {'spot_size': 1}}))
        validate_operation(op('camera_configure', {'settings': {'lens': 85}}))

    def test_bounds_types_and_unknown_fields_fail_closed(self):
        bad = [('camera_create', {'settings': {'lens': .5}}), ('camera_create', {'settings': {'lens': 5001}}),
               ('camera_create', {'settings': {'lens': math.nan}}), ('camera_create', {'settings': {'lens': True}}),
               ('camera_create', {'settings': {'sensor_width': 0}}), ('camera_create', {'settings': {'clip_start': 0}}),
               ('camera_create', {'settings': {'projection': 'PANO'}}), ('camera_create', {'settings': {'shift_x': .1}}),
               ('camera_create', {'settings': {'lens_unit': 'FOV'}}), ('camera_create', {'scale': [1, 1, 1]}),
               ('light_create', {'settings': {'energy': -1}}), ('light_create', {'settings': {'energy': 10 ** 7}}),
               ('light_create', {'settings': {'color': [1, 1, 1.5]}}), ('light_create', {'settings': {'color': [1, 1]}}),
               ('light_create', {'settings': {'light_type': 'MESH'}}), ('light_create', {'settings': {'use_nodes': True}}),
               ('light_create', {'settings': {'light_type': 'SPOT', 'spot_size': 0}}),
               ('light_create', {'settings': {'light_type': 'SUN', 'angle': 4}}),
               ('light_configure', {'settings': {'use_shadow': 1}}), ('light_configure', {'data_path': 'energy'}),
               ('light_configure', {'settings': {'energy': 1}, 'location': [0, 0, 0]}),
               ('camera_configure', {'look_at': [0, 0, 10 ** 6]})]
        for kind, parameters in bad:
            with self.subTest(kind=kind, parameters=parameters), self.assertRaises(ValueError):
                validate_operation(op(kind, parameters))
        with self.assertRaises(ValueError):
            validate_operation(op('camera_configure', {'settings': {'lens': 50}}, 'Artist Camera'))  # Names are not handles.

    def test_cross_field_rules(self):
        bad = [('camera_create', {'settings': {'projection': 'ORTHO', 'lens': 35}}),
               ('camera_create', {'settings': {'ortho_scale': 4}}),
               ('camera_create', {'settings': {'clip_start': 10, 'clip_end': 10}}),
               ('camera_configure', {'settings': {'clip_start': 5, 'clip_end': 1}}),
               ('camera_configure', {'settings': {'projection': 'PERSP', 'ortho_scale': 2}}),
               ('light_create', {'settings': {'spot_size': 1}}),
               ('light_create', {'settings': {'light_type': 'SUN', 'radius': 1}}),
               ('light_create', {'settings': {'light_type': 'AREA', 'angle': .1}}),
               ('light_create', {'settings': {'light_type': 'SPOT', 'shape': 'DISK'}}),
               ('light_create', {'settings': {'light_type': 'AREA', 'size_y': 2}}),
               ('light_configure', {'settings': {'light_type': 'POINT', 'spot_blend': .5}}),
               ('light_configure', {'settings': {'shape': 'DISK', 'size_y': 1}}),
               ('camera_create', {'rotation': [1, 0, 0], 'look_at': [0, 0, -1]}),
               ('camera_create', {'location': [1, 2, 3], 'look_at': [1, 2, 3.00001]}),
               ('camera_configure', {}), ('light_configure', {'settings': {}}), ('camera_create', {'settings': {}})]
        for kind, parameters in bad:
            with self.subTest(kind=kind, parameters=parameters), self.assertRaises(ValueError):
                validate_operation(op(kind, parameters))

    def test_written_families_drive_commit_transfer(self):
        self.assertEqual(resource_dependencies(op('camera_create'))['creates'], ['object', 'camera'])
        self.assertEqual(resource_dependencies(op('light_create'))['creates'], ['object', 'light'])
        # A look_at-only edit transfers transforms; it must not replace shared data.
        self.assertEqual(resource_dependencies(op('camera_configure', {'look_at': [0, 0, 0]}))['mutates'], ['object', 'transforms'])
        self.assertEqual(resource_dependencies(op('light_configure', {'settings': {'energy': 5}}))['mutates'], ['object', 'light'])
        both = resource_dependencies(op('camera_configure', {'settings': {'lens': 35}, 'look_at': [0, 0, 0]}))
        self.assertEqual(both['mutates'], ['object', 'transforms', 'camera'])
        self.assertEqual(resource_dependencies(op('light_configure', {'settings': {'energy': 5}}))['references'], [HANDLE])

    def test_programs_declare_configure_targets_and_aliases(self):
        program = {'mode': 'APPLY', 'read_set': [], 'write_set': [], 'operations': [
            op('light_create', {'alias': 'key'}), op('light_configure', {'settings': {'energy': 40}}, '$key')]}
        self.assertEqual(validate_program(program)['operations'][1]['target'], '$key')
        with self.assertRaises(ValueError):
            validate_program(dict(program, operations=[op('light_configure', {'settings': {'energy': 40}})]))
        written = dict(program, write_set=[{'handle': HANDLE, 'revision': 'r'}],
                       operations=[op('camera_configure', {'look_at': [0, 0, 0]})])
        self.assertEqual(validate_program(written)['operations'][0]['target'], HANDLE)

    def test_capabilities_document_defaults_policy_and_deferrals(self):
        summary = CAPABILITIES['camera_light']
        self.assertEqual(summary['light_create_defaults']['by_type']['SUN']['energy'], 1.)
        self.assertIn('shared_object_data', summary)
        for deferred in ('custom_world_node_graphs', 'panoramic_or_custom_cameras', 'custom_light_node_graphs'):
            self.assertIn(deferred, CAPABILITIES['unsupported'])
        # The active scene camera is a scene setting written by property_set (scene handle binding).
        self.assertNotIn('scene_camera', CAPABILITIES['unsupported'])
        self.assertIn('scene_binding', CAPABILITIES['property_set'])
        self.assertEqual(set(CAMERA_FIELDS), set(CAMERA_RNA))
        self.assertEqual(set(LIGHT_FIELDS), set(LIGHT_RNA))

    def test_settings_objects_are_shared_schema_definitions(self):
        from angelo_agent.schema_compaction import compact_schema
        from angelo_agent.tool_specs import TOOL_MAP
        compact = json.dumps(compact_schema(TOOL_MAP['angelo_scene_program']['inputSchema']))
        # Create, configure and the settings checks reuse one $def per family.
        self.assertEqual(compact.count('"ortho_scale"'), 1)
        self.assertEqual(compact.count('"spot_blend"'), 1)


class AimMathTests(unittest.TestCase):
    def assert_aims(self, origin, point):
        angles = look_at_euler(origin, point)
        rotation = euler_matrix(angles)
        self.assertAlmostEqual(angles[1], 0)
        self.assertLess(aim_error(world(rotation, origin), point), 1e-9)
        self.assertAlmostEqual(rotation[2][0], 0)  # Right axis stays horizontal: no roll.
        self.assertGreaterEqual(rotation[2][1], -1e-12)  # Local +Y never points below the horizon.

    def test_look_at_reproduces_blender_default_camera(self):
        degrees = [math.degrees(value) for value in look_at_euler([7.3589, -6.9258, 4.9583], [0, 0, 0])]
        self.assertAlmostEqual(degrees[0], 63.86, places=1)
        self.assertAlmostEqual(degrees[2], 46.74, places=1)

    def test_random_aims_are_exact_and_roll_free(self):
        generator = random.Random(1306)
        for _ in range(500):
            origin = [generator.uniform(-50, 50) for _ in range(3)]
            point = [generator.uniform(-50, 50) for _ in range(3)]
            with self.subTest(origin=origin, point=point):
                self.assert_aims(origin, point)

    def test_vertical_aims_are_deterministic(self):
        self.assertEqual(look_at_euler([0, 0, 5], [0, 0, 0]), [0., 0., 0.])
        self.assertEqual(look_at_euler([1, 1, 0], [1, 1, 3]), [math.pi, 0., 0.])
        self.assert_aims([0, 0, 5], [0, 0, 0])
        self.assert_aims([0, 0, 0], [0, 0, 5])
        with self.assertRaises(ValueError):
            look_at_euler([0, 0, 0], [0, 0, .00001])

    def test_aim_error_is_angular_and_fails_closed(self):
        identity = world([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0])
        self.assertEqual(aim_error(identity, [0, 0, -3]), 0)
        self.assertAlmostEqual(aim_error(identity, [1, 0, 0]), math.pi / 2)
        self.assertAlmostEqual(aim_error(identity, [0, 0, 3]), math.pi)
        self.assertAlmostEqual(aim_error(identity, [0, 1e-7, -1]), 1e-7, places=12)
        for matrix, point in ((identity, [0, 0, 0]), (world([[1, 0, 0], [0, 1, 0], [0, 0, 0]], [0, 0, 0]), [0, 0, -1]),
                              (world([[1, 0, 0], [0, 1, 0], [0, 0, math.inf]], [0, 0, 0]), [0, 0, -1])):
            with self.assertRaises(ValueError):
                aim_error(matrix, point)


class Data(NS):
    """RNA-like datablock: subtype properties exist only when set."""


def camera(**overrides):
    data = Data(type='PERSP', lens=50., ortho_scale=6., sensor_fit='AUTO', sensor_width=36., sensor_height=24.,
                clip_start=.1, clip_end=1000., animation_data=None)
    for key, value in overrides.items():
        setattr(data, key, value)
    return NS(type='CAMERA', name='Shot', data=data, matrix_world=world([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 5]))


def light(light_type='SPOT', **overrides):
    data = Data(type=light_type, energy=10., color=[1., 1., 1.], use_shadow=True, shadow_soft_size=0., animation_data=None)
    if light_type == 'SPOT':
        data.spot_size, data.spot_blend = math.pi / 4, .15
    for key, value in overrides.items():
        setattr(data, key, value)
    return NS(type='LIGHT', name='Key', data=data, matrix_world=world([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 5]))


class CameraLightCheckTests(unittest.TestCase):
    def run_check(self, obj, check):
        return verify(VerificationContext({'$t': obj}, [obj]), [dict(check, target='$t')])['checks'][0]

    def test_check_schemas_are_closed_and_referenced(self):
        self.assertTrue({'camera_settings', 'light_settings', 'aim'} <= CHECK_KINDS)
        canonical = validate_checks([{'kind': 'camera_settings', 'target': '$t', 'values': {'lens': 35}, 'tolerance': .001},
                                     {'kind': 'aim', 'target': '$t', 'point': [0, 0, 0], 'tolerance': .001}])
        self.assertEqual(check_references(canonical[1]), ('$t',))
        # tolerance is optional in every check kind: each number's float32 read-back precision, or the host's aim one.
        omitted = validate_checks([{'kind': 'light_settings', 'target': '$t', 'values': {'energy': 1000}},
                                   {'kind': 'aim', 'target': '$t', 'point': [0, 0, 0]}])
        self.assertEqual([check.get('tolerance') for check in omitted], [None, 0.0001])
        bad = [{'kind': 'camera_settings', 'target': '$t', 'values': {}, 'tolerance': .001},
               {'kind': 'camera_settings', 'target': '$t', 'values': {'shift_x': 0}, 'tolerance': .001},
               {'kind': 'light_settings', 'target': '$t', 'values': {'energy': -1}, 'tolerance': .001},
               {'kind': 'aim', 'target': '$t', 'point': [0, 0, 0], 'tolerance': 4},
               {'kind': 'aim', 'target': '$t', 'point': [0, 0], 'tolerance': .1}]
        for check in bad:
            with self.subTest(check=check), self.assertRaises(ValueError):
                validate_checks([check])

    def test_settings_pass_fail_and_not_applicable(self):
        passed = self.run_check(camera(lens=35.), {'kind': 'camera_settings', 'values': {'projection': 'PERSP', 'lens': 35}, 'tolerance': 1e-5})
        self.assertEqual(passed['status'], 'pass')
        failed = self.run_check(camera(), {'kind': 'camera_settings', 'values': {'lens': 35}, 'tolerance': 1e-5})
        self.assertEqual((failed['status'], failed['observed']['values']['lens']), ('fail', 50.))
        colored = self.run_check(light(color=[1., .5, .25]), {'kind': 'light_settings', 'values': {'color': [1, .5, .25], 'use_shadow': True}, 'tolerance': 1e-6})
        self.assertEqual(colored['status'], 'pass')
        point = self.run_check(light('POINT'), {'kind': 'light_settings', 'values': {'spot_size': 1}, 'tolerance': .01})
        self.assertEqual((point['status'], point['observed']['not_applicable']), ('fail', ['spot_size']))
        wrong_type = self.run_check(camera(), {'kind': 'light_settings', 'values': {'energy': 10}, 'tolerance': .01})
        self.assertEqual(wrong_type['status'], 'fail')

    def test_animated_data_and_point_aims_are_inconclusive(self):
        animated = camera(animation_data=NS(action=object(), drivers=[], nla_tracks=[]))
        result = self.run_check(animated, {'kind': 'camera_settings', 'values': {'lens': 50}, 'tolerance': .01})
        self.assertEqual(result['status'], 'inconclusive')
        result = self.run_check(light('POINT'), {'kind': 'aim', 'point': [0, 0, 0], 'tolerance': .01})
        self.assertEqual(result['status'], 'inconclusive')
        result = self.run_check(camera(), {'kind': 'aim', 'point': [0, 0, 5], 'tolerance': .01})
        self.assertEqual(result['status'], 'inconclusive')  # Point at the origin: no direction.
        mesh = NS(type='MESH', name='Cube', data=None, matrix_world=world([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0]))
        self.assertEqual(self.run_check(mesh, {'kind': 'aim', 'point': [0, 0, -1], 'tolerance': .01})['status'], 'inconclusive')

    def test_aim_pass_and_fail(self):
        aimed = self.run_check(light('SPOT'), {'kind': 'aim', 'point': [0, 0, 0], 'tolerance': 1e-6})
        self.assertEqual((aimed['status'], aimed['observed']['angle_error']), ('pass', 0))
        sideways = self.run_check(camera(), {'kind': 'aim', 'point': [5, 0, 5], 'tolerance': .1})
        self.assertEqual(sideways['status'], 'fail')
        self.assertAlmostEqual(sideways['observed']['angle_error'], math.pi / 2)


class TaskMatrixTests(unittest.TestCase):
    def test_camera_light_tasks_validate_with_a_negative_control(self):
        from eval_agent_tasks import fixtures
        tasks = fixtures()
        self.assertEqual(len({task['id'] for task in tasks}), len(tasks))
        lighting = [task for task in tasks if {'camera', 'light'} & set(task['categories'])]
        self.assertGreaterEqual(len(lighting), 4)
        self.assertTrue(any(task['expected_check_status'] != 'pass' for task in lighting))
        for task in lighting:
            self.assertTrue(task['program']['expected_checks'])
            self.assertEqual(copy.deepcopy(task['program']), validate_program(task['program']))


if __name__ == '__main__':
    unittest.main()
