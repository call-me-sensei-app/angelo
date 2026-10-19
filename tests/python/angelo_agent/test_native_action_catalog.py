# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure catalog contracts: closed schemas, exact defaults and work bounds."""

import copy
import ast
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.action_catalog import (
    ACTION_SCHEMAS, CAPABILITIES, action_capabilities, affected_references,
    catalog, estimated_work, resource_dependencies, validate_operation,
)


def action(kind, parameters=None, target='doc:object:abc'):
    return {'kind': kind, 'target': target, 'parameters': parameters or {}}


def geometry_chain(levels):
    nodes = [{'id': 'input', 'type': 'GROUP_INPUT'}, {'id': 'output', 'type': 'GROUP_OUTPUT'}]
    links, previous = [], 'input'
    for index, level in enumerate(levels):
        identifier = 'sub' + str(index)
        nodes.append({'id': identifier, 'type': 'SUBDIVIDE_MESH', 'inputs': {'Level': level}})
        links.append({'from_node': previous, 'from_socket': 'Geometry' if previous == 'input' else 'Mesh',
                      'to_node': identifier, 'to_socket': 'Mesh'})
        previous = identifier
    links.append({'from_node': previous, 'from_socket': 'Geometry' if previous == 'input' else 'Mesh',
                  'to_node': 'output', 'to_socket': 'Geometry'})
    return action('geometry_nodes', {'nodes': nodes, 'links': links})


class NativeActionCatalogTests(unittest.TestCase):
    def test_public_catalog_facade_keeps_single_schema_owner(self):
        from angelo_agent import action_catalog, action_schema_types, action_schemas
        for name in ('ACTION_SCHEMAS', 'OPERATION_SCHEMAS', 'PARAMETER_SCHEMAS', 'CAPABILITIES',
                     'MODIFIER_SETTINGS', 'SHADER_NODE_TYPES', 'GEOMETRY_NODE_TYPES'):
            self.assertIs(getattr(action_catalog, name), getattr(action_schemas, name))
        self.assertIs(action_catalog.number, action_schema_types.number)
        self.assertIs(action_catalog.HANDLE, action_schema_types.HANDLE)

    def test_action_modules_are_bounded_and_use_explicit_imports(self):
        addon = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent'
        names = ('action_catalog', 'action_schema_types', 'action_schemas', 'action_kernels',
                 'action_context', 'action_budget', 'action_node_graphs', 'action_nodes',
                 'action_modifiers', 'action_edit_context', 'action_mesh', 'action_object',
                 'action_camera_light_spec', 'action_camera_light')
        pure = {'action_catalog', 'action_schema_types', 'action_schemas', 'action_camera_light_spec', 'action_properties', 'action_world_spec'}
        for name in names:
            source = (addon / (name + '.py')).read_text()
            with self.subTest(module=name):
                self.assertLessEqual(len(source.splitlines()), 500)
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn('*', [alias.name for alias in node.names])
                        if name in pure:
                            self.assertNotIn(node.module, {'bpy', 'bmesh', 'mathutils'})
                    elif isinstance(node, ast.Import) and name in pure:
                        self.assertFalse({'bpy', 'bmesh', 'mathutils'} & {alias.name for alias in node.names})

    def test_all_actions_have_strict_schema_and_bounded_discovery(self):
        self.assertEqual(set(ACTION_SCHEMAS), set(CAPABILITIES['kinds']))
        discovery = action_capabilities()
        encoded = json.dumps(discovery, allow_nan=False)
        # The complete catalog is internal; discovery returns the bounded index. 160,000: the typed torus
        # took it past 150,000. 161,000: the scene camera's {"object": handle} form, in every schema that
        # holds property values, took it 159,668 -> 160,150 (measured). 170,000: every typed object pointer
        # also takes that form (action_schema_types.POINTER), written out in full here: 160,150 -> 167,850
        # (measured).
        self.assertLess(len(encoded), 170000)
        from angelo_agent.action_catalog import action_index
        index = action_index()
        self.assertLess(len(json.dumps(index, allow_nan=False)), 16000)
        self.assertEqual([entry['kind'] for entry in index['actions']], list(ACTION_SCHEMAS))
        # 54, then six animation kinds and node_edit; then material_assign and batch; then mesh_bend.
        self.assertEqual(len(discovery["actions"]), 64)
        discovery['summary']['kinds'].clear()
        self.assertEqual(len(CAPABILITIES["kinds"]), 64)
        for kind in ACTION_SCHEMAS:
            detail = catalog(kind)
            detail['parameters'].clear()
            self.assertTrue(ACTION_SCHEMAS[kind])

    def test_creation_defaults_are_explicit_independent_and_idempotent(self):
        source = action('create_primitive', {'primitive': 'CUBE', 'alias': 'body'}, None)
        canonical = validate_operation(source)
        self.assertEqual(canonical['parameters']['size'], 2)
        self.assertEqual(canonical['parameters']['rotation'], [0, 0, 0])
        self.assertEqual(canonical['parameters']['scale'], [1, 1, 1])
        self.assertEqual(validate_operation(canonical), canonical)
        canonical['parameters']['location'][0] = 77
        self.assertNotIn('location', source['parameters'])
        self.assertEqual(affected_references(source), ())
        self.assertEqual(resource_dependencies(source)['creates'], ['object', 'mesh'])

    def test_unknown_fields_kinds_and_name_lookup_targets_are_rejected(self):
        inputs = [action('execute_python', {'code': 'print(1)'}),
                  action('transform', {'location': [0, 0, 0], 'data_path': 'name'}),
                  action('create_primitive', {'primitive': 'CUBE', 'radius': 1}, None),
                  action('transform', {'location': [0, 0, 0]}, 'Artist Cube'),
                  action('transform', {'location': [0, 0, 0]}, '../path'),
                  action('create_primitive', {'primitive': 'CUBE'}, 'doc:object:abc'),
                  dict(action('transform', {'location': [0, 0, 0]}), code='exec(1)')]
        for value in inputs:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_operation(value)

    def test_finite_numeric_limits_booleans_and_singular_scales(self):
        for bad in (math.nan, math.inf, -math.inf, True, 10 ** 1000, -1, 1001):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_operation(action('create_primitive', {'primitive': 'CUBE', 'size': bad}, None))
        for bad in ([0, 1, 1], [1, math.nan, 1], [1, True, 1], [1, 1, 1001]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_operation(action('transform', {'scale': bad}))
        for value in (action('transform'), action('mesh_extrude', {'face_indices': [0], 'translation': [0, 0, 0]}),
                      action('keyframe_insert', {'property': 'rotation_euler', 'value': [0, 100001, 0]}),
                      action('modifier_add', {'type': 'MIRROR', 'settings': {'axes': [False] * 3}})):
            with self.assertRaises(ValueError):
                validate_operation(value)
        # A key takes the value its schema states: three turns of a wheel are 6 pi (it once stopped at 2 pi).
        spin = validate_operation(action('keyframe_insert', {'property': 'rotation_euler', 'value': [0, 6 * math.pi, 0]}))
        self.assertEqual(spin['parameters']['value'][1], 6 * math.pi)

    def test_index_scope_defaults_and_rule_selector_normalization(self):
        value = validate_operation(action('mesh_extrude', {'face_indices': [0], 'translation': [0, 0, 1]}))
        self.assertEqual(value['parameters']['index_scope'], 'INITIAL')
        self.assertEqual(value['parameters']['topology_step'], 0)
        with self.assertRaises(ValueError):
            validate_operation(action('mesh_bevel', {'edge_indices': [0], 'index_scope': 'INITIAL', 'topology_step': 1}))
        selector = validate_operation(action('mesh_inset', {'face_selector': {'normal': [0, 0, .5]}}))
        self.assertEqual(selector['parameters']['face_selector']['normal'], [0, 0, 1])
        for parameters in ({'face_indices': [0, 0]}, {'face_indices': [True]}, {'face_indices': list(range(4097))},
                           {'face_indices': [0], 'face_selector': {'normal': [0, 0, 1]}},
                           {'face_selector': {'normal': [0, 0, 0]}}, {'face_selector': {'area_min': 2, 'area_max': 1}}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation(action('mesh_inset', parameters))

    def test_resource_dependencies_include_all_staged_operands(self):
        examples = [
            (action('modifier_add', {'type': 'BOOLEAN', 'settings': {'operand': '$cutter'}}), '$cutter'),
            (action('parent_set', {'parent': 'doc:object:parent'}), 'doc:object:parent'),
            (action('constraint_add', {'type': 'COPY_LOCATION', 'target': '$leader'}), '$leader'),
            (action('armature_bind', {'armature': '$rig', 'groups': [
                {'bone': 'Root', 'weights': [{'vertex': 0, 'weight': 1}]}]}), '$rig'),
        ]
        for operation, operand in examples:
            with self.subTest(operation=operation):
                self.assertEqual(affected_references(operation), ('doc:object:abc', operand))
        self.assertTrue(resource_dependencies(action('keyframe_insert', {'property': 'location', 'value': [0, 0, 0]}))['private_action'])
        self.assertTrue(resource_dependencies(action('material_principled'))['private_mesh'])

    def test_modifier_and_constraint_whitelists_have_no_rna_escape(self):
        for value in (action('modifier_add', {'type': 'SKIN', 'settings': {'bad path': 1}}),
                      action('modifier_add', {'type': 'skin'}), action('modifier_add', {'type': 'ARRAY', 'settings': {'fit_type': 'FIT_CURVE'}}),
                      action('modifier_add', {'type': 'ARRAY', 'settings': {'count': 65}}),
                      action('modifier_add', {'type': 'SHRINKWRAP', 'settings': {}}),
                      action('modifier_add', {'type': 'DISPLACE', 'settings': {'texture': 'Clouds'}}),
                      action('modifier_add', {'type': 'SUBSURF', 'settings': {'levels': 4}}),
                      action('modifier_configure', {'type': 'BEVEL', 'modifier': 'Bevel', 'settings': {'path': 'width'}}),
                      action('modifier_add', {'type': 'BOOLEAN', 'settings': {'operand': 'doc:object:abc'}}),
                      action('constraint_add', {'type': 'PYTHON'}),
                      action('constraint_add', {'type': 'TRACK_TO', 'target': '$a', 'settings': {'track_axis': 'TRACK_Z', 'up_axis': 'UP_Z'}})):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_operation(value)

    def test_nodes_are_closed_acyclic_and_have_typed_link_endpoints(self):
        value = validate_operation(geometry_chain([1]))
        self.assertEqual(value['parameters']['nodes'][2]['inputs']['Level'], 1)
        for mutate in [lambda p: p['nodes'].append({'id': 'evil', 'type': 'SCRIPT'}),
                       lambda p: p['links'][0].update(to_node='missing'),
                       lambda p: p['links'][0].update(from_socket='python'),
                       lambda p: p['links'].append({'from_node': 'sub0', 'from_socket': 'Mesh', 'to_node': 'sub0', 'to_socket': 'Mesh'}),
                       lambda p: p['nodes'].append({'id': 'output', 'type': 'GROUP_OUTPUT'})]:
            bad = geometry_chain([1]); mutate(bad['parameters'])
            with self.assertRaises(ValueError):
                validate_operation(bad)

    def test_work_preflight_reports_large_acyclic_expansion(self):
        work = estimated_work(geometry_chain([2, 2, 2, 2]), {'vertices': 8, 'edges': 12, 'faces': 6})
        self.assertGreater(work['evaluated_vertices'], 500000)
        cube = estimated_work(action('create_primitive', {'primitive': 'CUBE'}, None))
        self.assertEqual((cube['vertices'], cube['edges'], cube['faces']), (8, 12, 6))
        with self.assertRaises(ValueError):
            estimated_work(action('transform', {'location': [0, 0, 0]}), {'vertices': True})

    def test_bones_and_explicit_weights_are_bounded_acyclic_and_normalized(self):
        params = {'bones': [{'name': 'Root', 'head': [0, 0, 0], 'tail': [0, 0, 1]},
                            {'name': 'Tip', 'head': [0, 0, 1], 'tail': [0, 0, 2], 'parent': 'Root', 'connected': True}]}
        canonical = validate_operation(action('armature_create', params, None))
        self.assertEqual(canonical['parameters']['bones'][0]['roll'], 0)
        for mutation in (lambda p: p['bones'][0].update(tail=[0, 0, 0]),
                         lambda p: p['bones'][0].update(parent='Tip'),
                         lambda p: p['bones'][1].update(head=[1, 0, 1]),
                         lambda p: p['bones'][1].update(name='Root')):
            bad = copy.deepcopy(params); mutation(bad)
            with self.assertRaises(ValueError):
                validate_operation(action('armature_create', bad, None))
        with self.assertRaises(ValueError):
            validate_operation(action('armature_bind', {'armature': '$rig', 'groups': [
                {'bone': 'Root', 'weights': [{'vertex': 0, 'weight': .7}]},
                {'bone': 'Tip', 'weights': [{'vertex': 0, 'weight': .7}]}]}))


if __name__ == '__main__':
    unittest.main()
