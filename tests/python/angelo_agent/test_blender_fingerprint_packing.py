# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Factory-startup exact coverage, COW stability, budgets and real stale gates."""

from array import array
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix

assert '--factory-startup' in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-fingerprint-packing-qa'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_STATE_DIR=str(OUT / 'state'))
from angelo_agent import native_programs as native, scene_queries as queries
from angelo_agent.program_dependencies import _fingerprint, _custom_properties, _RNA_SKIP
from angelo_agent.program_fingerprint_packing import packed_attribute_values
from angelo_agent.program_jobs import ProgramError
from angelo_agent.action_context import StagedActionContext, _mesh
from program_qa_helpers import FullStore, args_for

checks = []


def check(name, condition=True):
    assert condition, name
    checks.append(name)


def cube():
    mesh = bpy.data.meshes.new('Exact Coverage Mesh')
    bm = bmesh.new()
    try:
        bmesh.ops.create_cube(bm, size=2)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    obj = bpy.data.objects.new('Exact Coverage Object', mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def different(obj, name, mutation):
    before = _fingerprint(obj)
    mutation()
    check(name, _fingerprint(obj) != before)


def set_field(mesh, name, index, field, value):
    # Never retain value RNA pointers across fingerprint reads/materialization.
    setattr(mesh.attributes[name].data[index], field, value)


def numeric_value(prop, changed=False):
    if prop.type == 'BOOLEAN':
        return changed
    if prop.subtype == 'MATRIX':
        return Matrix.Translation((.25, .5, .75)) if changed else Matrix.Identity(4)
    scalar = (.625 if changed else .25) if prop.type == 'FLOAT' else (8 if changed else 7)
    return [scalar] * prop.array_length if prop.is_array else scalar


def settle(job):
    for _ in range(200):
        result = job.poll()
        if job.closed or job.ready_to_commit:
            return result
    raise AssertionError('Native program did not settle')


def stale(obj, name, mutation):
    args = move_args(obj, (4, 5, 6))
    job = native.begin(bpy.context.window, 'angelo_scene_program', args, store=FullStore)
    result = settle(job)
    assert job.ready_to_commit, result
    location = tuple(obj.location)
    mutation()
    result = job.commit()
    check(name, not result['success'] and job.closed and tuple(obj.location) == location and
          not result.get('partial_changes_possible'))
    native.cleanup_all()


def move_args(obj, location):
    return args_for(bpy.context.window, [{'kind': 'transform', 'target': queries.object_handle(obj),
                                         'parameters': {'location': list(location)}}], writes=[obj], store=FullStore)


def main():
    obj = cube(); mesh = obj.data
    check('unchanged_fresh_fingerprint_stable', _fingerprint(obj) == _fingerprint(obj))
    enum_types = [item.identifier for item in bpy.types.Attribute.bl_rna.properties['data_type'].enum_items]
    numeric_kinds = [kind for kind in enum_types if kind != 'STRING']
    for kind in numeric_kinds:
        name = 'Unused_' + kind
        attr = mesh.attributes.new(name, kind, 'POINT')
        properties = [prop for prop in attr.data[0].bl_rna.properties if prop.identifier not in _RNA_SKIP]
        assert properties and all(prop.type in {'FLOAT', 'INT', 'BOOLEAN'} for prop in properties), kind
        for prop in properties:
            field = prop.identifier
            set_field(mesh, name, 0, field, numeric_value(prop))
            different(obj, kind + '_' + field + '_first',
                      lambda n=name, f=field, p=prop: set_field(mesh, n, 0, f, numeric_value(p, True)))
            different(obj, kind + '_' + field + '_last_unused',
                      lambda n=name, f=field, p=prop: set_field(mesh, n, len(mesh.vertices) - 1, f, numeric_value(p, True)))
        packed = packed_attribute_values(mesh.attributes[name], mesh.attributes[name].data,
                                          budget=[2000000], skip_names=_RNA_SKIP)
        check(kind + '_all_exposed_numeric_fields_packed', packed is not None and
              {field['name'] for field in packed['packed_numeric_attribute_v1']} == {prop.identifier for prop in properties})
    string_name = 'Unused_String'
    mesh.attributes.new(string_name, 'STRING', 'POINT')
    set_field(mesh, string_name, 0, 'value', b'alpha')
    check('string_attribute_retains_exact_fallback', packed_attribute_values(mesh.attributes[string_name],
          mesh.attributes[string_name].data, budget=[2000000], skip_names=_RNA_SKIP) is None)
    different(obj, 'string_attribute_unused_last', lambda: set_field(mesh, string_name, 7, 'value', b'beta'))
    unused = 'Unused_FLOAT'
    set_field(mesh, unused, 0, 'value', 0.)
    different(obj, 'negative_zero_attribute_bits', lambda: set_field(mesh, unused, 0, 'value', -0.))
    for index, bad in enumerate((math.nan, math.inf, -math.inf)):
        set_field(mesh, unused, 0, 'value', bad)
        try:
            _fingerprint(obj)
        except ProgramError:
            check('nonfinite_attribute_' + str(index))
        else:
            raise AssertionError('Nonfinite packed attribute admitted')
    set_field(mesh, unused, 0, 'value', .5)
    for domain in ('EDGE', 'FACE', 'CORNER'):
        name = 'Unused_domain_' + domain
        attr = mesh.attributes.new(name, 'FLOAT', domain)
        last = len(attr.data) - 1
        different(obj, 'unused_' + domain + '_last', lambda n=name, i=last: set_field(mesh, n, i, 'value', .75))
    different(obj, 'unused_attribute_rename', lambda: setattr(mesh.attributes['Unused_domain_EDGE'], 'name', 'RenamedEdge'))

    uv = mesh.uv_layers.new(name='Exact UV')
    different(obj, 'uv_values', lambda: setattr(mesh.uv_layers['Exact UV'].data[-1], 'uv', (.25, .75)))
    different(obj, 'uv_pin', lambda: setattr(mesh.uv_layers['Exact UV'].data[-1], 'pin_uv', True))
    # Current 5.2 exposes UV/pin views, not deprecated MeshUVLoop.select flags.
    # Hidden CORNER boolean storage must still be included with no name filter.
    for name in ('.uv_vertex_selection_probe', '.uv_edge_selection_probe'):
        attr = mesh.attributes.new(name, 'BOOLEAN', 'CORNER')
        last = len(attr.data) - 1
        different(obj, name, lambda n=name, i=last: set_field(mesh, n, i, 'value', True))
    uv2 = mesh.uv_layers.new(name='Exact UV Two')
    different(obj, 'uv_active_render', lambda: setattr(mesh.uv_layers['Exact UV Two'], 'active_render', True))
    for collection_name, fields in [('vertices', ('select', 'hide')), ('edges', ('select', 'hide', 'use_seam', 'use_edge_sharp')),
                                    ('polygons', ('select', 'hide', 'use_smooth', 'material_index'))]:
        for field in fields:
            different(obj, collection_name + '_' + field,
                      lambda c=collection_name, f=field: setattr(getattr(mesh, c)[-1], f, 1 if f == 'material_index' else True))
    positions = array('f', [0]) * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get('co', positions)
    positions[-1] += .125
    different(obj, 'raw_rna_coordinate_without_graph_update', lambda: mesh.vertices.foreach_set('co', positions))
    positions[0] = 0.
    mesh.vertices.foreach_set('co', positions)
    different(obj, 'negative_zero_coordinate_bits', lambda: setattr(mesh.vertices[0].co, 'x', -0.))
    group = obj.vertex_groups.new(name='Exact weights')
    group.add([0], .25, 'REPLACE')
    different(obj, 'vertex_weight', lambda: group.add([0], .75, 'REPLACE'))
    different(obj, 'unused_vertex_weight', lambda: group.add([7], .5, 'REPLACE'))
    different(obj, 'vertex_group_settings', lambda: setattr(group, 'lock_weight', True))
    obj['values'] = [1., 2., 3.]
    obj['nested'] = {'child': {'values': [1, 2], 'text': 'first'}}
    different(obj, 'custom_property_array', lambda: obj['values'].__setitem__(2, 4.))
    different(obj, 'custom_property_nested', lambda: obj['nested']['child'].__setitem__('text', 'second'))
    mesh['data_property'] = .25
    different(obj, 'mesh_custom_property', lambda: mesh.__setitem__('data_property', .75))
    check('native_custom_array_budget_fails_closed', fails(lambda: _custom_properties(obj, budget=[3])))
    material = bpy.data.materials.new('Exact material')
    mesh.materials.append(material)
    different(obj, 'material_values', lambda: setattr(material, 'diffuse_color', (.1, .2, .3, 1)))
    principled = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    different(obj, 'material_node_input', lambda: setattr(principled.inputs['Roughness'], 'default_value', .2))
    different(obj, 'material_object_slot_link', lambda: setattr(obj.material_slots[0], 'link', 'OBJECT'))
    replacement = bpy.data.materials.new('Exact object-linked material')
    different(obj, 'material_object_slot_pointer', lambda: setattr(obj.material_slots[0], 'material', replacement))
    different(obj, 'object_material_dependency_content', lambda: setattr(replacement, 'diffuse_color', (.3, .2, .1, 1)))
    obj.shape_key_add(name='Basis')
    unused_key = obj.shape_key_add(name='UnusedShape')
    different(obj, 'unused_shape_key_coordinates', lambda: setattr(unused_key.data[-1].co, 'z', 2.))
    different(obj, 'shape_key_setting', lambda: setattr(unused_key, 'slider_max', 2.))
    before_copy = _fingerprint(obj)
    duplicate = mesh.copy()
    check('mesh_copy_cow_does_not_change_source_digest', _fingerprint(obj) == before_copy)
    bpy.data.meshes.remove(duplicate)
    check('mesh_copy_cleanup_does_not_change_source_digest', _fingerprint(obj) == before_copy)

    validation = cube()
    context = StagedActionContext(bpy.context.scene, bpy.context.scene.collection, objects={'validation': validation})
    mesh2 = validation.data
    for bad in (math.nan, math.inf, -math.inf, 100001., -100001.):
        mesh2.vertices[-1].co.z = bad
        check('fresh_coordinate_guard_' + repr(bad), fails(lambda: _mesh(context, validation), ValueError))
    mesh2.vertices[-1].co.z = -100000.
    check('coordinate_limit_inclusive', _mesh(context, validation) == mesh2)
    mesh2.vertices[-1].co.z = 0.
    mesh2.vertices[0].co.x = -0.
    check('coordinate_negative_zero_valid', _mesh(context, validation) == mesh2)
    # An oversized fake count is enough to prove no numeric allocation occurs.
    huge = SimpleNamespace(vertices=range(100001), edges=(), polygons=(), is_editmode=False, shape_keys=None)
    fake_context = SimpleNamespace(assert_staged=lambda _obj: None, require_private=lambda _data: huge)
    with patch('angelo_agent.action_context.np.empty', side_effect=AssertionError('Allocated before guard')):
        check('coordinate_count_guard_before_allocation', fails(lambda: _mesh(fake_context, SimpleNamespace(type='MESH', data=huge)), ValueError))

    source = cube()
    source.data.attributes.new('UnusedStale', 'FLOAT', 'POINT')
    stale(source, 'public_program_rejects_unused_attribute_edit', lambda: set_field(source.data, 'UnusedStale', 7, 'value', .5))
    stale(source, 'public_program_rejects_raw_coordinate_edit', lambda: setattr(source.data.vertices[7].co, 'x', 2.))
    source['array'] = [1, 2, 3]
    stale(source, 'public_program_rejects_custom_array_edit', lambda: source['array'].__setitem__(2, 4))
    baseline = _fingerprint(source)
    job = native.begin(bpy.context.window, 'angelo_scene_program', move_args(source, (2, 3, 4)), store=FullStore)
    result = settle(job)
    assert job.ready_to_commit and _fingerprint(source) == baseline, result
    result = job.commit()
    check('unchanged_public_program_commits_after_private_cow', result['success'] and tuple(source.location) == (2, 3, 4))
    native.cleanup_all()
    report = {'count': len(checks), 'checks': checks, 'binary_path': bpy.app.binary_path,
              'version': bpy.app.version_string, 'numeric_attribute_types': numeric_kinds,
              'paid_requests': 0, 'success': True}
    (OUT / 'results.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    print('ANGELO_FINGERPRINT_PACKING_OK', json.dumps(report), flush=True)


def fails(function, expected=ProgramError):
    try:
        function()
    except expected:
        return True
    return False


if __name__ == '__main__':
    main()
