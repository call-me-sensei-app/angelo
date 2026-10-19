# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Production-route action audit regressions in isolated factory-startup RNA.

Uses normal object_revision admission and an UNDO operator for commit, never
FullStore/fingerprint overrides. No providers, credentials or project imports.
"""

import copy
import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import bmesh
from mathutils import Euler, Matrix

assert bpy.app.background and '--factory-startup' in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-action-audit-fixes'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_STATE_DIR=str(OUT / 'state'))
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import native_programs as native
from angelo_agent.action_catalog import validate_operation
from angelo_agent.action_kernels import StagedActionContext, apply_operation, assert_evaluation_budget
from angelo_agent.action_node_graphs import node_metadata, restore_node_metadata
from angelo_agent.action_recipe_selection import plan_clean_mesh_faces
from angelo_agent.action_recipes import execute as expand_public
from angelo_agent.action_topology import topology_digest
from angelo_agent.scene_queries import object_handle, object_revision
from angelo_agent.scene_validation import VerificationContext, execute as verify_public, verify

report = {'binary': bpy.app.binary_path, 'version': bpy.app.version_string,
          'paid_requests': 0, 'checks': {}, 'cases': {}, 'failures': {}}
window = bpy.context.window


class ActionAuditCommit(bpy.types.Operator):
    bl_idname = 'angelo.action_audit_commit'
    bl_label = 'Action audit native commit'
    bl_options = {'REGISTER', 'UNDO'}
    program_id: bpy.props.StringProperty()

    def execute(self, context):
        report['last_commit'] = native.commit_job(self.program_id, window=context.window)
        return {'FINISHED'} if report['last_commit'].get('success') else {'CANCELLED'}


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def fresh_cube(name):
    base = window.scene.objects['Cube']
    obj = base.copy()
    obj.data = base.data.copy()
    obj.name = name
    obj.animation_data_clear()
    obj.modifiers.clear()
    window.scene.collection.objects.link(obj)
    return obj


def program(objects, writes, operations, checks):
    window.view_layer.update()
    rows = [{'handle': object_handle(obj), 'revision': object_revision(window, obj)} for obj in objects]
    by_object = {obj.as_pointer(): row for obj, row in zip(objects, rows)}
    return {'mode': 'APPLY', 'read_set': rows, 'write_set': [by_object[obj.as_pointer()] for obj in writes],
            'operations': operations, 'expected_checks': checks}


def finish(arguments, capture=None):
    job = native.begin(window, 'angelo_scene_program', arguments)
    for index in range(100):
        outcome = job.poll()
        if index == 0 and capture and not outcome.get('final'):
            with native.job_context(job.id) as stage:
                target = stage.objects[capture]
                report['unparent_staged_matrices'] = {'world': [list(row) for row in target.matrix_world],
                    'decomposed': [list(row) for row in Matrix.LocRotScale(*target.matrix_world.decompose())]}
        if outcome.get('final'):
            return outcome
        if outcome.get('ready_to_commit'):
            bpy.ops.angelo.action_audit_commit('EXEC_DEFAULT', True, program_id=job.id)
            return report['last_commit']
    job.cancel()
    raise AssertionError('Native action program did not settle')


def recipe(obj, name, parameters):
    window.view_layer.update()
    return expand_public(window, 'angelo_expand_recipe', {'recipe': name, 'target': object_handle(obj),
        'revision': object_revision(window, obj), 'parameters': parameters})


def live_verify(obj, checks):
    window.view_layer.update()
    return verify_public(window, 'angelo_scene_verify', {
        'read_set': [{'handle': object_handle(obj), 'revision': object_revision(window, obj)}], 'checks': checks})


def socket_graph():
    obj = fresh_cube('Audit Shade Recipe')
    expanded = recipe(obj, 'bevel-and-shade', {'width': .1, 'segments': 1})
    result = finish(expanded['program'])
    report['shade_program_result'] = result
    check('shade_recipe_public_commit', result.get('success') and len(obj.modifiers) == 2)
    check('shade_recipe_live_readback', live_verify(obj, expanded['expected_checks'])['passed'])
    # Metadata survives .blend staging suspend/resume and still audits actual
    # native sockets. Changing a link must not gain approval from provenance.
    scene = bpy.data.scenes.new('Audit graph stage')
    collection = bpy.data.collections.new('Audit graph copies')
    scene.collection.children.link(collection)
    context = StagedActionContext(scene, collection)
    apply_operation(context, {'kind': 'create_primitive', 'target': None,
                              'parameters': {'primitive': 'CUBE', 'alias': 'mesh'}})
    graph = copy.deepcopy(expanded['operations'][1])
    graph['target'] = '$mesh'
    apply_operation(context, graph)
    staged = context.resolve('$mesh')
    metadata = node_metadata(context)
    context.node_graphs.clear()
    restore_node_metadata(context, metadata)
    check('shade_provenance_resolves_mesh_display_labels', assert_evaluation_budget(context, staged) > 0)
    tree = staged.modifiers[0].node_group
    tree.links.remove(tree.links[0])
    try:
        restore_node_metadata(context, metadata)
    except ValueError:
        check('changed_graph_provenance_rejected', True)
    else:
        raise AssertionError('Changed graph was accepted')
    # Dispose only this test-owned stage, including embedded tree references.
    private_mesh = staged.data
    bpy.data.objects.remove(staged, do_unlink=True)
    bpy.data.meshes.remove(private_mesh)
    bpy.data.node_groups.remove(tree)
    bpy.data.scenes.remove(scene)
    bpy.data.collections.remove(collection)
    return {'program': result}


def material_slots():
    results = []
    for kind in ('material_principled', 'shader_nodes'):
        obj = fresh_cube('Audit Material ' + kind)
        old = bpy.data.materials.new('Audit Old Override ' + kind)
        obj.material_slots[0].link = 'OBJECT'
        obj.material_slots[0].material = old
        handle, name = object_handle(obj), 'Audit Assigned ' + kind
        params = {'name': name, 'slot': 0}
        if kind == 'material_principled':
            params['inputs'] = {'Base Color': [1, 0, 0, 1]}
        else:
            params.update(nodes=[{'id': 'shader', 'type': 'BSDF_PRINCIPLED'}, {'id': 'output', 'type': 'OUTPUT_MATERIAL'}],
                          links=[{'from_node': 'shader', 'from_socket': 'BSDF', 'to_node': 'output', 'to_socket': 'Surface'}])
        checks = [{'kind': 'material_assignment', 'target': handle, 'material': name, 'all_faces': True}]
        result = finish(program([obj], [obj], [{'kind': kind, 'target': handle, 'parameters': params}], checks))
        check(kind + '_object_linked_commit', result.get('success'))
        check(kind + '_effective_slot_and_link', obj.material_slots[0].material.name == name and obj.material_slots[0].link == 'OBJECT')
        check(kind + '_live_face_assignment', live_verify(obj, checks)['passed'])
        results.append(result)
    return results


def parenting():
    child, parent, leader = [fresh_cube('Audit Parent ' + name) for name in ('Child', 'Parent', 'Leader')]
    child.location, parent.location, leader.location = (1, 0, 0), (5, 0, 0), (2, 0, 0)
    constraint = child.constraints.new('COPY_LOCATION')
    constraint.target, constraint.use_offset = leader, True
    window.view_layer.update()
    before, source_revision = child.matrix_world.copy(), object_revision(window, child)
    ch, pa = object_handle(child), object_handle(parent)
    action = {'kind': 'parent_set', 'target': ch, 'parameters': {'parent': pa, 'keep_transform': True}}
    result = finish(program([child, parent, leader], [child], [action], [{'kind': 'parent', 'target': ch, 'expected': pa}]))
    check('constrained_parent_rejected_before_commit', not result.get('success') and 'active owner constraints' in result.get('error', ''))
    check('constrained_parent_live_world_unchanged', child.parent is None and child.matrix_world == before and object_revision(window, child) == source_revision)
    check('constrained_parent_flags_unchanged', not constraint.mute and constraint.influence == 1 and constraint.use_offset)
    constraint.mute = True
    child.rotation_mode = 'QUATERNION'
    child.rotation_quaternion = Euler((.1, .2, .3)).to_quaternion()
    child.delta_location, child.delta_scale = (.2, .3, .4), (1.2, .8, 1)
    parent.rotation_euler, parent.scale = (.3, .1, .2), (2, 3, 1)
    window.view_layer.update()
    before, basis = child.matrix_world.copy(), child.matrix_basis.copy()
    result = finish(program([child, parent, leader], [child], [action], [{'kind': 'parent', 'target': ch, 'expected': pa}]))
    check('muted_constraint_parent_public_commit', result.get('success') and child.parent == parent)
    check('parent_keeps_basis_rotation_mode_and_deltas', child.rotation_mode == 'QUATERNION' and child.matrix_basis == basis and constraint.mute)
    check('parent_keeps_world_transform', all(abs(before[i][j] - child.matrix_world[i][j]) < 1e-5 for i in range(4) for j in range(4)))
    report['unparent_source_matrices'] = {'world': [list(row) for row in child.matrix_world],
        'decomposed': [list(row) for row in Matrix.LocRotScale(*child.matrix_world.decompose())]}
    action['parameters']['parent'] = None
    result = finish(program([child, parent, leader], [child], [action], [{'kind': 'parent', 'target': ch, 'expected': None}]), capture=ch)
    report['unparent_program_result'] = result
    check('unparent_keeps_quaternion_and_delta_world', result.get('success') and child.parent is None and
          all(abs(before[i][j] - child.matrix_world[i][j]) < 1e-5 for i in range(4) for j in range(4)))
    child.parent, child.matrix_parent_inverse = parent, Matrix.Identity(4)
    window.view_layer.update()
    sheared_world, basis = child.matrix_world.copy(), child.matrix_basis.copy()
    action['parameters']['parent'] = object_handle(leader)
    result = finish(program([child, parent, leader], [child], [action], [{'kind': 'parent', 'target': ch, 'expected': object_handle(leader)}]))
    check('reparent_retains_existing_shear_and_basis', result.get('success') and child.matrix_basis == basis and
          all(abs(sheared_world[i][j] - child.matrix_world[i][j]) < 1e-5 for i in range(4) for j in range(4)))
    action['parameters']['parent'] = None
    result = finish(program([child, leader], [child], [action], [{'kind': 'parent', 'target': ch, 'expected': None}]))
    check('unrepresentable_sheared_unparent_rejected_without_live_mutation', not result.get('success') and
          'sheared' in result.get('error', '') and child.parent == leader and
          all(abs(sheared_world[i][j] - child.matrix_world[i][j]) < 1e-5 for i in range(4) for j in range(4)))
    return result


def hidden_modifier():
    obj = fresh_cube('Audit Hidden Modifier')
    mod = obj.modifiers.new('Audit Hidden Bevel', 'BEVEL')
    mod.show_viewport, mod.show_render, mod.show_in_editmode, mod.show_on_cage = False, False, False, True
    before = (mod.show_viewport, mod.show_render, mod.show_in_editmode, mod.show_on_cage)
    handle = object_handle(obj)
    checks = [{'kind': 'modifier', 'target': handle, 'name': mod.name, 'type': 'BEVEL',
               'values': {'width': .2, 'show_viewport': False, 'show_render': False}, 'tolerance': .00001}]
    result = finish(program([obj], [obj], [{'kind': 'modifier_configure', 'target': handle,
        'parameters': {'modifier': mod.name, 'type': 'BEVEL', 'settings': {'width': .2, 'segments': 1}}}], checks))
    check('hidden_modifier_public_commit', result.get('success'))
    check('configure_preserves_all_enable_flags', (mod.show_viewport, mod.show_render, mod.show_in_editmode, mod.show_on_cage) == before)
    check('hidden_modifier_live_readback', live_verify(obj, checks)['passed'])
    return result


def clean_mesh():
    obj = fresh_cube('Audit Clean Faces')
    mesh = bpy.data.meshes.new('Audit clean source mesh')
    mesh.from_pydata([(0, 0, 0), (.00001, 0, 0), (0, 1, 0), (2, 0, 0), (3, 0, 0), (2, 1, 0)],
                     [], [(0, 1, 2), (3, 4, 5)])
    mesh.update()
    obj.data = mesh
    original = topology_digest(mesh)
    baseline_mesh_count = len(bpy.data.meshes)
    expanded = recipe(obj, 'clean-mesh', {'vertex_indices': [0, 1], 'face_indices': [1], 'distance': .0001})
    normals = expanded['operations'][1]['parameters']
    check('clean_plan_read_only_and_no_leaked_mesh', topology_digest(mesh) == original and len(bpy.data.meshes) == baseline_mesh_count)
    check('clean_plan_remaps_surviving_face', normals['face_indices'] == [0] and normals['index_scope'] == 'CURRENT' and normals['topology_step'] == 1)
    bad = copy.deepcopy(expanded['program'])
    bad['operations'][1]['parameters']['topology_digest'] = '0' * 64
    result = finish(bad)
    check('wrong_topology_digest_fails_without_live_changes', not result.get('success') and 'topology selection' in result.get('error', '') and topology_digest(obj.data) == original)
    try:
        recipe(obj, 'clean-mesh', {'vertex_indices': [0, 1], 'face_indices': [0], 'distance': .0001})
    except ValueError as error:
        check('removed_explicit_face_rejected_before_approval', 'selected face' in str(error) and topology_digest(obj.data) == original)
    else:
        raise AssertionError('A removed face was rebound as all faces')
    result = finish(expanded['program'])
    check('clean_explicit_faces_public_commit', result.get('success') and len(obj.data.polygons) == 1)
    check('clean_exact_merged_topology_binding', topology_digest(obj.data) == normals['topology_digest'])
    check('clean_live_readback', live_verify(obj, expanded['expected_checks'])['passed'])
    return result


def fit_dimensions():
    params = {'dimensions': [4, 1, 3], 'minimum_scale': .001, 'maximum_scale': 100, 'maximum_scale_ratio': 100}
    for mode in ('QUATERNION', 'DELTA_EULER', 'DELTA_QUATERNION'):
        obj = fresh_cube('Audit Fit ' + mode)
        if mode == 'QUATERNION':
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = Euler((0, 0, math.pi / 4)).to_quaternion()
        elif mode == 'DELTA_EULER':
            obj.delta_rotation_euler = (0, 0, math.pi / 4)
        else:
            obj.rotation_mode = 'QUATERNION'
            obj.delta_rotation_quaternion = Euler((0, 0, math.pi / 4)).to_quaternion()
        window.view_layer.update()
        before = object_revision(window, obj)
        try:
            recipe(obj, 'fit-dimensions', params)
        except ValueError as error:
            check(mode + '_effective_rotation_rejected', 'axis-aligned' in str(error) and object_revision(window, obj) == before)
        else:
            raise AssertionError('Effective rotation was accepted as axis aligned')
    obj = fresh_cube('Audit Fit Delta Scale')
    obj.rotation_mode = 'QUATERNION'
    obj.delta_scale = (2, .5, 1)
    expanded = recipe(obj, 'fit-dimensions', params)
    result = finish(expanded['program'])
    check('axis_aligned_quaternion_delta_scale_commit', result.get('success'))
    check('fit_actual_dimensions_live_readback', live_verify(obj, expanded['expected_checks'])['passed'])
    return result


def edit_uv():
    obj = fresh_cube('Audit Edit UV')
    window.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        layer = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                loop[layer].uv = (.2, .3)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        handle, revision = object_handle(obj), object_revision(window, obj)
        result = verify_public(window, 'angelo_scene_verify', {'read_set': [{'handle': handle, 'revision': revision}],
            'checks': [{'kind': 'uv_layer', 'target': handle, 'name': layer.name, 'require_nonempty': True, 'require_finite': True}]})
        check('live_edit_uv_inconclusive_not_failed_or_passed', result['status'] == 'inconclusive' and not result['passed'])
        check('live_edit_uv_verify_preserves_mode', obj.mode == 'EDIT' and object_revision(window, obj) == revision)
        return result
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def bone_precision():
    before = len(window.scene.objects), len(bpy.data.armatures)
    bad = {'kind': 'armature_create', 'target': None, 'parameters': {'bones': [
        {'name': 'collapsed', 'head': [10000, 0, 0], 'tail': [10000.0001, 0, 0]}]}}
    try:
        finish({'mode': 'APPLY', 'read_set': [], 'write_set': [], 'operations': [bad], 'expected_checks': []})
    except ValueError as error:
        check('float32_collapsed_bone_rejected_before_allocation', 'float32' in str(error) and before == (len(window.scene.objects), len(bpy.data.armatures)))
    else:
        raise AssertionError('Collapsed float32 bone was accepted')
    good = {'kind': 'armature_create', 'target': None, 'parameters': {'name': 'Audit Float32 Rig', 'alias': 'rig', 'bones': [
        {'name': 'root', 'head': [10000, 0, 0], 'tail': [10000.001, 0, 0]}]}}
    canonical = validate_operation(good)
    result = finish({'mode': 'APPLY', 'read_set': [], 'write_set': [], 'operations': [good],
        'expected_checks': [{'kind': 'rig', 'target': '$rig', 'minimum_bones': 1, 'maximum_bones': 1, 'require_finite': True}]})
    check('representable_bone_public_commit', result.get('success'))
    obj = window.scene.objects['Audit Float32 Rig']
    bone = obj.data.bones['root']
    endpoints = result['operation_results'][0]['bone_endpoints'][0]
    check('bone_exact_endpoint_readback', endpoints['head'] == list(bone.head_local) == canonical['parameters']['bones'][0]['head'] and
          endpoints['tail'] == list(bone.tail_local) == canonical['parameters']['bones'][0]['tail'])
    check('bone_did_not_fallback_to_z_direction', bone.tail_local.x > bone.head_local.x and bone.tail_local.z == bone.head_local.z)
    angled = {'kind': 'armature_create', 'target': None, 'parameters': {'name': 'Audit Angled Rig', 'alias': 'angled', 'bones': [
        {'name': 'root', 'head': [.1, .2, .3], 'tail': [.5, 1.2, 2.3]},
        {'name': 'tip', 'head': [.5, 1.2, 2.3], 'tail': [-.3, 4, 2.1], 'parent': 'root', 'connected': True}]}}
    angled_result = finish({'mode': 'APPLY', 'read_set': [], 'write_set': [], 'operations': [angled],
        'expected_checks': [{'kind': 'rig', 'target': '$angled', 'minimum_bones': 2, 'maximum_bones': 2, 'require_finite': True}]})
    report['angled_bone_result'] = angled_result
    check('angled_connected_bones_public_commit', angled_result.get('success'))
    return result


def clean_mesh_planning_diagnostic():
    """Serial bounded timing, not an optimization or a UI latency guarantee."""
    obj = fresh_cube('Audit 16k Planning Diagnostic')
    mesh = bpy.data.meshes.new('Audit 16k Planning Mesh')
    width = 128
    coordinates = [(float(x), float(y), 0) for y in range(width) for x in range(width)]
    faces = [(y * width + x, y * width + x + 1, (y + 1) * width + x + 1, (y + 1) * width + x)
             for y in range(width - 1) for x in range(width - 1)]
    coordinates[1] = (.00001, 0, 0)
    mesh.from_pydata(coordinates, [], faces)
    mesh.update()
    obj.data = mesh
    parameters = {'vertex_indices': list(range(4096)), 'face_indices': list(range(5000, 5064)), 'distance': .0001}
    baseline, meshes_before = topology_digest(mesh), len(bpy.data.meshes)
    digest_ms, planning_ms, plans = [], [], []
    for _ in range(3):
        start = time.perf_counter()
        topology_digest(mesh)
        digest_ms.append(round((time.perf_counter() - start) * 1000, 3))
        start = time.perf_counter()
        plans.append(plan_clean_mesh_faces(obj, parameters))
        planning_ms.append(round((time.perf_counter() - start) * 1000, 3))
    check('16k_face_planning_is_repeatable_read_only_and_cleans_temporary_ids',
          plans[0] == plans[1] == plans[2] and topology_digest(mesh) == baseline and len(bpy.data.meshes) == meshes_before)
    result = {'vertices': len(mesh.vertices), 'edges': len(mesh.edges), 'faces': len(mesh.polygons),
              'corners': len(mesh.loops), 'selected_vertices': 4096, 'selected_faces': 64,
              'runs_serial': True, 'timing_scope': 'planner_only_including_two_topology_digests_not_admission_or_commit',
              'digest_ms': digest_ms, 'planning_ms': planning_ms,
              'concurrent_native_jobs': os.environ.get('ANGELO_QA_CONCURRENT_NATIVE_JOBS', 'unverified'),
              'diagnostic_only': os.environ.get('ANGELO_QA_CONCURRENT_NATIVE_JOBS', 'unverified') != '0'}
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    return result


bpy.context.preferences.filepaths.file_preview_type = 'NONE'
bpy.utils.register_class(ActionAuditCommit)
try:
    for name, callback in (('socket_graph', socket_graph), ('effective_material_slots', material_slots),
                           ('parenting', parenting), ('modifier_flags', hidden_modifier), ('clean_mesh_selection', clean_mesh),
                           ('effective_fit_dimensions', fit_dimensions), ('edit_uv', edit_uv), ('bone_precision', bone_precision),
                           ('clean_mesh_planning_diagnostic', clean_mesh_planning_diagnostic)):
        try:
            report['cases'][name] = callback()
        except Exception:
            report['failures'][name] = traceback.format_exc()
finally:
    bpy.utils.unregister_class(ActionAuditCommit)
    report['all_passed'] = not report['failures'] and all(report['checks'].values())
    (OUT / 'action-audit-fixes.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    print('ACTION_AUDIT_FIXES ' + json.dumps({'all_passed': report['all_passed'], 'checks': report['checks'], 'failures': report['failures']}))
assert report['all_passed'], report['failures']
