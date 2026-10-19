# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Existing-object staged effects, pointer settings, local rollback and real Undo.

Use the exact Angelo binary with --factory-startup (GUI, not --background).
No provider process, account, user project, builds or paid calls are used.
"""

import contextlib
import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert '--factory-startup' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-program-commit-effects'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_CONNECTIONS_DIR=str(OUT/'accounts'),
                  ANGELO_STATE_DIR=str(OUT/'state'))
sys.path.insert(0, str(ROOT/'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries
from angelo_agent import scene_settings  # noqa: E402
from angelo_agent import program_commit
from angelo_agent.action_catalog import OPERATION_SCHEMAS
from program_qa_helpers import args_for, FullStore, no_stages

STORAGE_MODE = os.environ.get('ANGELO_QA_STAGE_STORAGE', 'native')
assert STORAGE_MODE in {'native', 'file'}
report = {'binary': bpy.app.binary_path, 'storage': STORAGE_MODE, 'checks': {}, 'cases': {}, 'paid_requests': 0}
seen = set()


def check(name, value):
    report['checks'][name] = bool(value)
    assert value, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        assert not result.get('error'), result
        if result.get('final') or result.get('ready_to_commit'):
            job.qa_poll_count = _ + 1
            return result
    raise AssertionError('Job did not settle')


def vector(value):
    return [round(float(item), 6) for item in value]


def curves(action):
    if action is None:
        return []
    return [curve for layer in action.layers for strip in layer.strips
            for bag in getattr(strip, 'channelbags', []) for curve in bag.fcurves]


def graph(tree):
    """A node tree's nodes (name, type, frame, mute, label, hide, value inputs) and links, by name."""
    if tree is None:
        return None
    nodes = sorted([node.name, node.bl_idname, node.parent.name if node.parent else None, node.mute, node.label, node.hide,
                    [round(socket.default_value, 5) for socket in node.inputs if socket.type == 'VALUE' and not socket.is_linked]]
                   for node in tree.nodes)
    links = sorted([link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier]
                   for link in tree.links)
    return [nodes, links]


def snapshot(obj, references=None):
    if obj is None:
        return None
    references = references or {}
    ref = lambda value: references.get(value.as_pointer(), value.name) if value is not None else None
    value = {'type': obj.type, 'mode': obj.rotation_mode, 'location': vector(obj.location),
             'euler': vector(obj.rotation_euler), 'quaternion': vector(obj.rotation_quaternion),
             'axis_angle': vector(obj.rotation_axis_angle), 'scale': vector(obj.scale),
             'matrix': [vector(row) for row in obj.matrix_world], 'parent': ref(obj.parent),
             'parent_type': obj.parent_type, 'parent_bone': obj.parent_bone,
             'groups': [group.name for group in obj.vertex_groups],
             'modifiers': [{'name': mod.name, 'type': mod.type, 'object': ref(getattr(mod, 'object', None)),
                            'node_group': sorted(node.bl_idname for node in mod.node_group.nodes) if getattr(mod, 'node_group', None) else None,
                            'graph': graph(getattr(mod, 'node_group', None)),
                            'width': round(mod.width, 6) if hasattr(mod, 'width') else None,
                            'axes': list(mod.use_axis) if hasattr(mod, 'use_axis') else None}
                           for mod in obj.modifiers],
             'constraints': [{'name': con.name, 'type': con.type, 'target': ref(getattr(con, 'target', None)),
                              'influence': con.influence} for con in obj.constraints],
             'action': [[curve.data_path, curve.array_index,
                         [[*vector(key.co), key.interpolation] for key in curve.keyframe_points]]
                        for curve in curves(obj.animation_data.action if obj.animation_data else None)],
             'particles': [[psys.name, psys.seed, psys.settings.type, psys.settings.count,
                            round(psys.settings.hair_length, 6), psys.settings.render_type,
                            ref(psys.settings.instance_object)] for psys in getattr(obj, 'particle_systems', ())]}
    if obj.type == 'MESH':
        value.update(vertices=[vector(vertex.co) for vertex in obj.data.vertices],
                     faces=[[list(face.vertices), face.material_index, face.use_smooth] for face in obj.data.polygons],
                     uv={layer.name: [vector(corner.uv) for corner in layer.data] for layer in obj.data.uv_layers},
                     weights=[[[obj.vertex_groups[group.group].name, round(group.weight, 6)] for group in vertex.groups]
                              for vertex in obj.data.vertices],
                     materials=[None if slot.material is None else [slot.link, vector(slot.material.diffuse_color),
                                sorted(node.bl_idname for node in slot.material.node_tree.nodes),
                                graph(slot.material.node_tree)] for slot in obj.material_slots])
    if obj.type == 'ARMATURE':
        value['bones'] = [[bone.name, vector(bone.head_local), vector(bone.tail_local),
                           bone.parent.name if bone.parent else None, vector(bone.matrix_local.col[2]),
                           bone.use_deform, bone.bbone_segments] for bone in obj.data.bones]
        value['pose'] = [[bone.name, bone.rotation_mode, vector(bone.location), vector(bone.rotation_quaternion),
                          vector(bone.rotation_euler), vector(bone.scale), round(bone.ik_stretch, 6)]
                         for bone in obj.pose.bones]
    if obj.type in {'CAMERA', 'LIGHT'}:
        from angelo_agent.action_camera_light import object_settings
        settings = next(iter(object_settings(obj).values()))
        value['data_settings'] = {key: vector(item) if isinstance(item, list) else round(item, 6) if isinstance(item, float) else item
                                  for key, item in settings.items()}
    return value


class EffectCommit(bpy.types.Operator):
    bl_idname = 'angelo.commit_effects_qa'
    bl_label = 'Commit validated effects QA'
    bl_options = {'REGISTER', 'UNDO'}
    program_id: bpy.props.StringProperty()

    def execute(self, context):
        result = native.commit_job(self.program_id, context.window)
        report['last_result'] = result
        return {'FINISHED'} if result.get('success') else {'CANCELLED'}


@contextlib.contextmanager
def staged_snapshot(job, native_expected):
    """Read expected values without silently switching the native commit to a file.

    Production job_context intentionally materializes an immutable file copy.
    The native audit instead resumes and re-suspends the actual stage read-only,
    preserving the ownership route being tested. Run a separate process with
    test_blender_program_commit_file.py to exercise and compare the original path.
    """
    if not native_expected:
        with native.job_context(job.id) as stage:
            yield stage
        return
    assert job.stage.native_owner is not None, 'Native fixture unexpectedly used file fallback'
    job.stage = job.backend.resume_stage(job.stage)
    try:
        yield job.stage
    finally:
        job.stage = job.backend.suspend_stage(job.stage)


def execute_case(label, operations, *, target='Cube', writes=(), reads=(), checks=(), intended=None,
                 file_fallback=False):
    window = bpy.context.window_manager.windows[0]
    names = [obj.name for obj in writes]
    before = snapshot(window.scene.objects.get(target))
    fingerprints = [native._fingerprint(obj) for obj in [*writes, *reads]]
    bpy.ops.ed.undo_push(message='Commit effect baseline ' + label)
    value = args_for(window, operations, writes=writes, reads=reads, checks=checks, store=FullStore())
    job = native.begin(window, 'angelo_scene_program', value, store=FullStore())
    intent = settle(job)
    check(label + '_ready_without_live_effects', intent.get('ready_to_commit') and no_stages() and
          [native._fingerprint(obj) for obj in [*writes, *reads]] == fingerprints)
    native_expected = STORAGE_MODE == 'native' and not file_fallback
    check(label + '_uses_expected_storage', (job.stage.native_owner is not None) == native_expected)
    with staged_snapshot(job, native_expected) as stage:
        mapping = {obj.as_pointer(): stage.originals[handle].name for handle, obj in stage.objects.items()}
        obj = next((obj for handle, obj in stage.objects.items() if stage.originals[handle].name == target),
                   stage.scene.objects.get(target))
        expected = snapshot(obj, mapping)
    native_commit = job.stage.native_owner is not None
    check(label + '_commits_through_requested_storage', native_commit == native_expected)
    result = bpy.ops.angelo.commit_effects_qa('EXEC_DEFAULT', True, program_id=job.id)
    actual = snapshot(window.scene.objects.get(target))
    report['cases'][label] = {'expected': expected, 'actual': actual, 'result': job.result.get('status'),
                              'native_commit': native_commit, 'poll_count': job.qa_poll_count}
    check(label + '_commits_staged_effects', 'FINISHED' in result and job.result['success'] and actual == expected and no_stages())
    if intended:
        check(label + '_requested_live_effect', intended(window.scene.objects[target]))
    bpy.ops.ed.undo()
    queries.invalidate()
    window = bpy.context.window_manager.windows[0]
    check(label + '_one_undo_restores_original', snapshot(window.scene.objects.get(target)) == before and no_stages())
    bpy.ops.ed.redo()
    queries.invalidate()
    window = bpy.context.window_manager.windows[0]
    check(label + '_one_redo_restores_all_effects', snapshot(window.scene.objects.get(target)) == expected and no_stages())
    bpy.ops.ed.undo()
    queries.invalidate()
    seen.update(operation['kind'] for operation in operations)


def cube():
    return bpy.context.window_manager.windows[0].scene.objects['Cube']


def handle(obj):
    return queries.object_handle(obj)


def op(kind, target, parameters):
    return {'kind': kind, 'target': target, 'parameters': parameters}


def rotations():
    for mode in ('QUATERNION', 'AXIS_ANGLE', 'ZYX'):
        source = cube()
        source.rotation_mode = mode
        source.rotation_quaternion = (1, 0, 0, 0)
        source.rotation_axis_angle = (0, 0, 1, 0)
        source.rotation_euler = (0, 0, 0)
        bpy.context.window_manager.windows[0].view_layer.update()
        h = handle(source)
        operations = [op('keyframe_insert', h, {'property':'rotation_euler', 'value':[0,0,math.pi/2], 'frame':1})]
        checks = [{'kind':'transform', 'target':h, 'property':'rotation_euler', 'expected':[0,0,math.pi/2], 'tolerance':.00001}]
        execute_case('euler_key_from_' + mode, operations, writes=[source], checks=checks,
                     intended=lambda obj: obj.rotation_mode == 'XYZ' and abs(obj.matrix_world[0][0]) < .00001)
    for property_name, value in (('location', [2,3,4]), ('scale', [1.5,2,2.5])):
        source = cube()
        source.rotation_mode = 'QUATERNION'
        execute_case('key_' + property_name, [op('keyframe_insert', handle(source), {'property':property_name, 'value':value, 'frame':1})],
                     writes=[source], intended=lambda obj: obj.rotation_mode == 'QUATERNION')


def mesh_and_material_effects():
    for kind, params in (
        ('transform', {'location':[1,2,3], 'rotation':[.1,.2,.3], 'scale':[1.2,1.3,1.4]}),
        ('mesh_extrude', {'face_indices':[0], 'translation':[0,0,.2]}),
        ('mesh_inset', {'face_indices':[0], 'thickness':.1}),
        ('mesh_bevel', {'edge_indices':[0], 'offset':.03}),
        ('mesh_merge_by_distance', {'vertex_indices':[0,1], 'distance':.001}),
        ('mesh_recalculate_normals', {}),
        ('uv_project', {'uv_map':'Commit projected UV'}),
        ('uv_unwrap', {'uv_map':'Commit unwrapped UV'}),
        ('material_principled', {'name':'Commit material', 'inputs':{'Base Color':[.2,.3,.4,1], 'Roughness':.23}}),
        ('shader_nodes', {'name':'Commit shader', 'nodes':[{'id':'shader','type':'BSDF_PRINCIPLED'}, {'id':'out','type':'OUTPUT_MATERIAL'}],
                          'links':[{'from_node':'shader','from_socket':'BSDF','to_node':'out','to_socket':'Surface'}]})):
        source = cube()
        if kind == 'mesh_merge_by_distance':
            source.data.vertices[1].co = source.data.vertices[0].co
            source.data.update()
        execute_case(kind, [op(kind, handle(source), params)], writes=[source])
    # The bend cuts even slices along X, then lifts the middle by rise; the ends stay where they were.
    execute_case('mesh_bend', [op('mesh_bend', handle(cube()), {'rise': .2, 'axis': 'X', 'segments': 4})],
                 writes=[cube()], intended=lambda obj: bent(obj, .2))


def bent(obj, rise):
    top = [v.co for v in obj.data.vertices if v.co.z > 0]
    middle = max(co.z for co in top if abs(co.x) < 1e-4)
    ends = [co.z for co in top if abs(abs(co.x) - 1) < 1e-4]
    return abs(middle - (1 + rise)) < 1e-4 and ends and all(abs(z - 1) < 1e-4 for z in ends)


def pointer_effects():
    window = bpy.context.window_manager.windows[0]
    source = cube()
    operand = source.copy(); operand.data = source.data.copy(); operand.name = 'Commit operand'
    window.scene.collection.objects.link(operand)
    operand.location = (.5, 0, 0)
    execute_case('boolean_add', [op('modifier_add', handle(source), {'type':'BOOLEAN', 'name':'Commit Boolean',
                 'settings':{'operand':handle(operand), 'operation':'UNION'}})], writes=[source], reads=[operand],
                 intended=lambda obj: obj.modifiers['Commit Boolean'].object == bpy.context.window_manager.windows[0].scene.objects['Commit operand'])
    source, operand = cube(), bpy.context.window_manager.windows[0].scene.objects['Commit operand']
    modifier = source.modifiers.new('Commit existing Boolean', 'BOOLEAN'); modifier.object = operand
    execute_case('boolean_configure', [op('modifier_configure', handle(source), {'type':'BOOLEAN', 'modifier':modifier.name,
                 'settings':{'operand':handle(operand), 'operation':'INTERSECT'}})], writes=[source], reads=[operand])
    source, operand = cube(), bpy.context.window_manager.windows[0].scene.objects['Commit operand']
    source.modifiers.remove(source.modifiers['Commit existing Boolean'])
    execute_case('constraint_pointer', [op('constraint_add', handle(source), {'type':'COPY_LOCATION', 'name':'Commit Copy',
                 'target':handle(operand), 'influence':.5})], writes=[source], reads=[operand],
                 intended=lambda obj: obj.constraints['Commit Copy'].target == bpy.context.window_manager.windows[0].scene.objects['Commit operand'])
    source, operand = cube(), bpy.context.window_manager.windows[0].scene.objects['Commit operand']
    execute_case('parent_pointer', [op('parent_set', handle(source), {'parent':handle(operand), 'keep_transform':True})],
                 writes=[source], reads=[operand], intended=lambda obj: obj.parent == bpy.context.window_manager.windows[0].scene.objects['Commit operand'])
    source = cube()
    graph = {'name':'Commit Geometry', 'nodes':[{'id':'input','type':'GROUP_INPUT'},
             {'id':'move','type':'TRANSFORM_GEOMETRY','inputs':{'Translation':[.2,.3,.4]}}, {'id':'output','type':'GROUP_OUTPUT'}],
             'links':[{'from_node':'input','from_socket':'Geometry','to_node':'move','to_socket':'Geometry'},
                      {'from_node':'move','from_socket':'Geometry','to_node':'output','to_socket':'Geometry'}]}
    execute_case('geometry_pointer', [op('geometry_nodes', handle(source), graph)], writes=[source],
                 intended=lambda obj: obj.modifiers['Commit Geometry'].node_group is not None)


def creation_and_weights():
    execute_case('create_primitive', [op('create_primitive', None, {'primitive':'CUBE', 'name':'Commit new cube', 'alias':'created'})], target='Commit new cube')
    execute_case('armature_create', [op('armature_create', None, {'name':'Commit new rig', 'alias':'created',
                 'bones':[{'name':'Bone','head':[0,0,0],'tail':[0,0,1]}]})], target='Commit new rig')
    bpy.ops.object.armature_add()
    rig = bpy.context.object; rig.name = 'Commit existing rig'
    source = cube()
    execute_case('armature_bind_existing', [op('armature_bind', handle(source), {'armature':handle(rig), 'name':'Commit skin',
                 'groups':[{'bone':rig.data.bones[0].name, 'weights':[{'vertex':i,'weight':.5} for i in range(8)]}]})],
                 writes=[source], reads=[rig], intended=lambda obj: obj.modifiers['Commit skin'].object == bpy.context.window_manager.windows[0].scene.objects['Commit existing rig'])


def camera_light_effects():
    execute_case('camera_create', [op('camera_create', None, {'name':'Commit new camera', 'alias':'created', 'location':[4,-4,3],
                 'look_at':[0,0,0], 'settings':{'lens':35}})], target='Commit new camera')
    execute_case('light_create', [op('light_create', None, {'name':'Commit new light', 'alias':'created',
                 'settings':{'light_type':'AREA', 'energy':300}})], target='Commit new light')
    window = bpy.context.window_manager.windows[0]
    camera = window.scene.objects['Camera']
    execute_case('camera_configure', [op('camera_configure', handle(camera), {'settings':{'lens':70, 'clip_end':250}, 'look_at':[0,0,0]})],
                 target='Camera', writes=[camera], intended=lambda obj: abs(obj.data.lens - 70) < .00001)
    light = bpy.context.window_manager.windows[0].scene.objects['Light']
    execute_case('light_configure', [op('light_configure', handle(light), {'settings':{'light_type':'SPOT', 'spot_size':.7, 'energy':500},
                 'look_at':[0,0,0]})], target='Light', writes=[light], intended=lambda obj: obj.data.type == 'SPOT')
    light = bpy.context.window_manager.windows[0].scene.objects['Light']
    execute_case('light_repeated_subtype_configure', [
                 op('light_configure', handle(light), {'settings':{'light_type':'SPOT','spot_size':.4}}),
                 op('light_configure', handle(light), {'settings':{'light_type':'AREA','shape':'DISK','size':2}}),
                 op('light_configure', handle(light), {'settings':{'energy':350}})],
                 target='Light', writes=[light], intended=lambda obj: obj.data.type == 'AREA' and obj.data.energy == 350)
    execute_case('created_light_subtype_configure', [
                 op('light_create', None, {'name':'Commit retyped light','alias':'retyped','settings':{'light_type':'POINT'}}),
                 op('light_configure', '$retyped', {'settings':{'light_type':'SPOT','spot_size':.4}}),
                 op('light_configure', '$retyped', {'settings':{'light_type':'AREA','shape':'DISK','size':2}}),
                 op('light_configure', '$retyped', {'settings':{'energy':350}})],
                 target='Commit retyped light', intended=lambda obj: obj.data.type == 'AREA' and obj.data.energy == 350)


def next_turn_with_existing_node_pointer():
    window, source = bpy.context.window_manager.windows[0], cube()
    graph = {'name':'Commit retained Geometry', 'nodes':[{'id':'input','type':'GROUP_INPUT'},
             {'id':'move','type':'TRANSFORM_GEOMETRY','inputs':{'Translation':[.2,.3,.4]}}, {'id':'output','type':'GROUP_OUTPUT'}],
             'links':[{'from_node':'input','from_socket':'Geometry','to_node':'move','to_socket':'Geometry'},
                      {'from_node':'move','from_socket':'Geometry','to_node':'output','to_socket':'Geometry'}]}
    job = native.begin(window, 'angelo_scene_program', args_for(window, [op('geometry_nodes', handle(source), graph)],
                       writes=[source], store=FullStore()), store=FullStore())
    settle(job)
    check('first_geometry_program_commits_existing_target', 'FINISHED' in bpy.ops.angelo.commit_effects_qa('EXEC_DEFAULT', True, program_id=job.id))
    group = source.modifiers['Commit retained Geometry'].node_group
    second = native.begin(window, 'angelo_scene_program', args_for(window, [op('transform', handle(source), {'location':[2,3,4]})],
                          writes=[source], store=FullStore()), store=FullStore())
    settle(second)
    check('next_turn_pointer_journal_is_readable', 'FINISHED' in bpy.ops.angelo.commit_effects_qa('EXEC_DEFAULT', True, program_id=second.id) and
          second.result['success'] and vector(source.location) == [2,3,4] and source.modifiers['Commit retained Geometry'].node_group == group and no_stages())
    evaluated = source.evaluated_get(window.view_layer.depsgraph)
    mesh = evaluated.to_mesh()
    try:
        check('committed_geometry_group_has_actual_effect', len(mesh.vertices) == len(source.data.vertices) and
              all(abs(mesh.vertices[0].co[i] - source.data.vertices[0].co[i] - [.2,.3,.4][i]) < .00001 for i in range(3)))
    finally:
        evaluated.to_mesh_clear()


def shade_stack_and_parent_hydration():
    source = cube()
    shade = {'name':'Commit smooth shading', 'nodes':[{'id':'input','type':'GROUP_INPUT'},
             {'id':'smooth','type':'SET_SHADE_SMOOTH'}, {'id':'output','type':'GROUP_OUTPUT'}],
             'links':[{'from_node':'input','from_socket':'Geometry','to_node':'smooth','to_socket':'Geometry'},
                      {'from_node':'smooth','from_socket':'Geometry','to_node':'output','to_socket':'Geometry'}]}
    execute_case('bevel_and_shade_existing_stack', [op('modifier_add', handle(source), {'type':'BEVEL','name':'Commit bevel','settings':{'width':.02}}),
                 op('geometry_nodes', handle(source), shade)], writes=[source])
    for parenting in ('OBJECT', 'BONE'):
        window, source = bpy.context.window_manager.windows[0], cube()
        parent = window.scene.objects['Commit operand' if parenting == 'OBJECT' else 'Commit existing rig']
        parent.location, parent.rotation_euler, parent.scale = (5,-2,1), (.2,.3,.4), (2.1,.6,1.4)
        source.parent = None
        source.rotation_mode = 'XYZ'
        source.location, source.rotation_euler, source.scale = (1.2,.3,.4), (.1,.2,.3), (1.2,.8,1)
        window.view_layer.update()
        goal = source.matrix_world.copy()
        source.parent, source.parent_type = parent, parenting
        source.parent_bone = parent.data.bones[0].name if parenting == 'BONE' else ''
        source.matrix_parent_inverse = parent.matrix_world.inverted()
        source.matrix_world = goal
        window.view_layer.update()
        before, inverse = source.matrix_world.copy(), source.matrix_parent_inverse.copy()
        args = args_for(window, [op('parent_set', handle(source), {'parent':None,'keep_transform':True})],
                        writes=[source], reads=[parent], store=FullStore())
        job = native.begin(window, 'angelo_scene_program', args, store=FullStore())
        # Inspect the serialized preparation result before applying the action. A normal fused
        # poll may also execute it on a fast machine (and reject bone keep-world unparenting).
        # A zero-work clock makes this check independent of machine speed; normal fused execution
        # is still exercised by execute_case below.
        job.backend.fuse_phases = False
        job.backend.operation_quantum_clock = lambda: 0.0
        job.poll()
        check(parenting + '_preparation_phase_only', job.phase == 'OPERATIONS' and not job.closed)
        with native.job_context(job.id) as stage:
            staged = stage.objects[handle(source)]
            close = lambda a,b: max(abs(a[i][j]-b[i][j]) for i in range(4) for j in range(4)) < .00001
            check(parenting + '_copy_hydration_preserves_parent_inverse_world', staged.parent != parent and
                  staged.parent_type == parenting and staged.parent_bone == source.parent_bone and
                  close(staged.matrix_world, before) and close(staged.matrix_parent_inverse, inverse))
        job.cancel()
        if parenting == 'OBJECT':
            execute_case(parenting + '_unparent_preserves_world', [op('parent_set', handle(source), {'parent':None,'keep_transform':True})],
                         writes=[source], reads=[parent], intended=lambda obj: obj.parent is None and close(obj.matrix_world, before))
        else:
            # The action owner intentionally rejects keep-world bone/vertex
            # reparenting. Copy/hydration must still preserve these inputs for
            # supported operations, not silently turn them into Object parents.
            execute_case('BONE_parented_local_transform', [op('transform', handle(source), {'location':[1,.5,.3]})],
                         writes=[source], reads=[parent], intended=lambda obj: obj.parent_type == 'BONE')
        source = cube()
        source.parent = None


def custom_case(label, operations, applied, restored, *, writes=(), reads=(), scene=False, file_fallback=False):
    """Commit, Undo and Redo for kinds whose effect is not on the target object's own state."""
    window = bpy.context.window_manager.windows[0]
    bpy.ops.ed.undo_push(message='Commit effect baseline ' + label)
    value = args_for(window, operations, writes=writes, reads=reads, store=FullStore())
    if scene:
        settings = scene_settings.execute(window, {})
        value['write_set'].append({'handle': settings['handle'], 'revision': settings['revision']})
    job = native.begin(window, 'angelo_scene_program', value, store=FullStore())
    settle(job)
    native_expected = STORAGE_MODE == 'native' and not file_fallback
    check(label + '_uses_expected_storage', (job.stage.native_owner is not None) == native_expected)
    result = bpy.ops.angelo.commit_effects_qa('EXEC_DEFAULT', True, program_id=job.id)
    check(label + '_commits_staged_effects', 'FINISHED' in result and job.result['success'] and applied() and no_stages())
    bpy.ops.ed.undo()
    queries.invalidate()
    check(label + '_one_undo_restores_original', restored() and no_stages())
    bpy.ops.ed.redo()
    queries.invalidate()
    check(label + '_one_redo_restores_all_effects', applied() and no_stages())
    bpy.ops.ed.undo()
    queries.invalidate()
    seen.update(operation['kind'] for operation in operations)


def native_coverage_effects():
    """Every kind added for native coverage, through the same commit/Undo/Redo harness."""
    window = bpy.context.window_manager.windows[0]
    source = cube()
    source.parent = None
    for constraint in list(source.constraints):
        source.constraints.remove(constraint)
    source.modifiers.clear()
    source.animation_data_clear()
    source.rotation_mode = 'XYZ'
    source.location, source.rotation_euler, source.scale = (0, 0, 0), (0, 0, 0), (1, 1, 1)
    window.view_layer.update()
    execute_case('property_set_object', [op('property_set', handle(cube()), {'values': {'hide_render': True, 'pass_index': 4}})],
                 writes=[source], intended=lambda obj: obj.hide_render and obj.pass_index == 4)
    source = cube()
    execute_case('modifier_stack_edits', [
        op('modifier_add', handle(cube()), {'type': 'ARRAY', 'name': 'Commit array', 'settings': {'count': 3}}),
        op('modifier_add', handle(cube()), {'type': 'WEIGHTED_NORMAL', 'name': 'Commit weighted'}),
        op('modifier_move', handle(cube()), {'modifier': 'Commit weighted', 'index': 0}),
        op('modifier_remove', handle(cube()), {'modifier': 'Commit array'})], writes=[source],
        intended=lambda obj: [mod.name for mod in obj.modifiers] == ['Commit weighted'])
    source = cube()
    execute_case('modifier_apply', [op('modifier_add', handle(cube()), {'type': 'SUBSURF', 'name': 'Commit sub', 'settings': {'levels': 1}}),
                                    op('modifier_apply', handle(cube()), {'modifier': 'Commit sub'})], writes=[source],
                 intended=lambda obj: len(obj.data.vertices) == 26 and not obj.modifiers)
    source = cube()
    execute_case('transform_and_origin', [op('transform', handle(cube()), {'scale': [2, 1, 1]}),
                                          op('transform_apply', handle(cube()), {'scale': True}),
                                          op('origin_set', handle(cube()), {'mode': 'POINT', 'point': [0, 0, 1]})], writes=[source],
                 intended=lambda obj: vector(obj.scale) == [1, 1, 1] and vector(obj.location) == [0, 0, 1])
    source = cube()
    execute_case('collection_link', [op('collection_link', handle(cube()), {'collection': 'Commit collection', 'create': True, 'mode': 'MOVE'})],
                 writes=[source], intended=lambda obj: [c.name for c in obj.users_collection] == ['Commit collection'])
    source = cube()
    execute_case('key_edits', [op('keyframe_insert', handle(cube()), {'property': 'location', 'value': [0, 0, 0], 'frame': 1}),
                               op('keyframe_insert', handle(cube()), {'property': 'location', 'value': [1, 0, 0], 'frame': 10}),
                               op('keyframe_configure', handle(cube()), {'property': 'location', 'frames': [1, 10], 'interpolation': 'LINEAR'}),
                               op('keyframe_delete', handle(cube()), {'property': 'location', 'frame': 10})], writes=[source],
                 intended=lambda obj: len(curves(obj.animation_data.action)[0].keyframe_points) == 1)
    source = cube()
    execute_case('property_keyframe', [op('property_keyframe', handle(cube()), {'values': {'pass_index': 2}, 'frame': 3})],
                 writes=[source], intended=lambda obj: obj.pass_index == 2 and obj.animation_data.action is not None)
    source = cube()
    execute_case('shape_keys', [op('shape_key_add', handle(cube()), {'name': 'Commit smile', 'value': .2}),
                                op('shape_key_value', handle(cube()), {'name': 'Commit smile', 'value': .5})], writes=[source],
                 # Keys are owned through their mesh; the removable-Main scan excludes them.
                 file_fallback=True, intended=lambda obj: abs(obj.data.shape_keys.key_blocks['Commit smile'].value - .5) < 1e-6)
    source = cube()
    execute_case('nla_push_down', [op('keyframe_insert', handle(cube()), {'property': 'location', 'value': [0, 0, 0], 'frame': 1}),
                                   op('nla_push_down', handle(cube()), {'track': 'Commit track'})], writes=[source],
                 intended=lambda obj: obj.animation_data.action is None and obj.animation_data.nla_tracks[0].name == 'Commit track')
    source = cube()
    execute_case('uv_tools', [op('uv_smart_project', handle(cube()), {'uv_map': 'Commit smart UV'}),
                              op('uv_pack', handle(cube()), {'uv_map': 'Commit smart UV', 'margin': .02})], writes=[source],
                 intended=lambda obj: 'Commit smart UV' in obj.data.uv_layers)
    source = cube()
    execute_case('mesh_tools', [op('mesh_subdivide', handle(cube()), {'face_indices': [0], 'cuts': 1}),
                                op('mesh_delete', handle(cube()), {'face_selector': {'normal': [0, 0, 1], 'normal_angle': .1}})],
                 writes=[source], intended=lambda obj: len(obj.data.polygons) < 9)
    source = cube()
    execute_case('operator_run', [op('operator_run', handle(cube()), {'operator': 'mesh.subdivide'}),
                                  op('operator_run', handle(cube()), {'operator': 'object.vertex_group_add'}),
                                  op('operator_run', handle(cube()), {'operator': 'object.vertex_group_assign'}),
                                  op('operator_run', handle(cube()), {'operator': 'object.shade_smooth'}),
                                  op('operator_run', handle(cube()), {'operator': 'object.material_slot_add'})],
                 writes=[source], intended=lambda obj: len(obj.data.vertices) == 26
                 and [group.name for group in obj.vertex_groups] == ['Group'] and obj.active_material_index == len(obj.material_slots) - 1)
    source = cube()
    execute_case('object_duplicate', [op('object_duplicate', None, {'source': handle(cube()), 'alias': 'twin', 'name': 'Commit twin',
                                                                    'location': [3, 0, 0]})],
                 target='Commit twin', reads=[source], intended=lambda obj: obj.data != cube().data)
    execute_case('create_object', [op('create_object', None, {'type': 'TEXT', 'name': 'Commit text', 'alias': 'text',
                                                              'body': 'Commit'})], target='Commit text',
                 file_fallback=True, intended=lambda obj: obj.data.body == 'Commit')  # Curve/text IDs stay file-backed.
    scene_objects = lambda: bpy.context.window_manager.windows[0].scene.objects
    # A new child of a new parent keeps the place it was staged at: commit keeps the parent inverse.
    custom_case('new_child_keeps_its_place', [
        op('create_primitive', None, {'primitive': 'CUBE', 'name': 'Commit lamp', 'alias': 'lamp', 'location': [4, 1, 0]}),
        op('create_primitive', None, {'primitive': 'CUBE', 'name': 'Commit lantern', 'alias': 'lantern', 'location': [4, 1, 2]}),
        op('parent_set', '$lantern', {'parent': '$lamp', 'keep_transform': True})],
        lambda: scene_objects()['Commit lantern'].parent == scene_objects()['Commit lamp']
        and vector(scene_objects()['Commit lantern'].matrix_world.translation) == [4, 1, 2],
        lambda: 'Commit lantern' not in scene_objects() and 'Commit lamp' not in scene_objects())
    source = cube()
    custom_case('material_assign', [
        op('material_principled', handle(cube()), {'name': 'Commit shared', 'inputs': {'Base Color': [.1, .2, .3, 1]}}),
        op('create_primitive', None, {'primitive': 'CUBE', 'name': 'Commit user', 'alias': 'user', 'location': [0, 4, 0]}),
        op('material_assign', '$user', {'material': 'Commit shared', 'source': handle(cube())})],
        lambda: scene_objects()['Commit user'].data.materials[0].name == 'Commit shared'
        and scene_objects()['Commit user'].data.materials[0] in list(scene_objects()['Cube'].data.materials),
        lambda: 'Commit user' not in scene_objects() and 'Commit shared' not in bpy.data.materials, writes=[source])
    posts = lambda: [scene_objects().get('Commit post %d' % index) for index in range(1, 9)]
    custom_case('batch', [
        op('create_primitive', None, {'primitive': 'CUBE', 'name': 'Commit post 1', 'alias': 'post', 'location': [0, 5, 0]}),
        op('batch', None, {'sources': ['$post'], 'array': [{'step': [2, 0, 0], 'count': 4}], 'mirror': 'Y'})],
        lambda: all(posts()) and len({post.data for post in posts()}) == 1 and vector(posts()[7].location) == [6, -5, 0],
        lambda: not any(posts()))
    source = cube()
    custom_case('object_rename', [op('object_rename', handle(cube()), {'name': 'Commit renamed'})],
                lambda: 'Commit renamed' in window.scene.objects and 'Cube' not in window.scene.objects,
                lambda: 'Cube' in bpy.context.window_manager.windows[0].scene.objects, writes=[source])
    light = window.scene.objects['Light']
    custom_case('object_delete', [op('object_delete', handle(light), {})],
                lambda: 'Light' not in bpy.data.objects, lambda: 'Light' in bpy.context.window_manager.windows[0].scene.objects,
                writes=[light])
    source, operand = cube(), window.scene.objects['Commit operand']
    live = lambda: bpy.context.window_manager.windows[0].scene.objects
    custom_case('join_and_separate', [op('object_join', handle(source), {'sources': [handle(operand)]}),
                                      op('mesh_separate', handle(source), {'mode': 'LOOSE'})],
                lambda: 'Commit operand' not in bpy.data.objects and len(live()['Cube'].data.vertices) == 8
                and len(live()['Cube.001'].data.vertices) == 8,
                lambda: 'Commit operand' in live() and 'Cube.001' not in live(), writes=[source, operand])
    path_data = bpy.data.curves.new('Commit path', 'CURVE')
    path_data.splines.new('POLY').points.add(1)
    path = bpy.data.objects.new('Commit path', path_data)
    window.scene.collection.objects.link(path)
    queries.invalidate()
    points = lambda: len(live()['Commit path'].data.splines[0].points)
    custom_case('curves', [op('curve_points', handle(path), {'points': [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
                                                             'spline_type': 'POLY'}),
                           op('object_convert', None, {'source': handle(path), 'alias': 'wire', 'name': 'Commit wire'})],
                lambda: 'Commit wire' in live() and live()['Commit wire'].type == 'MESH' and points() == 3,
                lambda: 'Commit wire' not in live() and points() == 2, writes=[path], file_fallback=True)
    scene_handle = scene_settings.execute(window, {})['handle']
    source = cube()
    layer = lambda: bpy.context.window_manager.windows[0].view_layer.layer_collection.children['Collection']
    custom_case('visibility', [op('visibility_set', handle(source), {'hidden': True}),
                               op('visibility_set', scene_handle, {'collection': 'Collection', 'hide_render': True,
                                                                   'hidden': True})],
                lambda: live()['Cube'].hide_get() and layer().hide_viewport and layer().collection.hide_render,
                lambda: not live()['Cube'].hide_get() and not layer().hide_viewport, writes=[source], scene=True)
    scene_handle = scene_settings.execute(window, {})['handle']  # Undo gave the scene a new handle.
    custom_case('scene_settings_and_world', [
        op('property_set', scene_handle, {'values': {'render.resolution_x': 1234, 'frame_end': 77}}),
        op('world_configure', scene_handle, {'strength': 2.5})],
        lambda: window.scene.render.resolution_x == 1234 and window.scene.frame_end == 77
        and abs(window.scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value - 2.5) < 1e-6,
        lambda: bpy.context.window_manager.windows[0].scene.render.resolution_x != 1234, scene=True,
        file_fallback=True)  # Worlds keep their existing file-backed resource lifetime.
    source = cube()
    execute_case('particle_system', [
        op('modifier_add', handle(cube()), {'type': 'PARTICLE_SYSTEM', 'name': 'Commit hair'}),
        op('particle_set', handle(cube()), {'modifier': 'Commit hair', 'settings': {'type': 'HAIR', 'count': 50,
                                                                                   'hair_length': .3}})],
        writes=[source], file_fallback=True,  # Simulation caches are deliberately excluded from native detachment.
        intended=lambda obj: obj.particle_systems['Commit hair'].settings.count == 50)
    cube().modifiers.new('Commit existing hair', 'PARTICLE_SYSTEM')
    operand = window.scene.objects['Commit operand']
    execute_case('particle_set_existing', [op('particle_set', handle(cube()), {
        'modifier': 'Commit existing hair', 'settings': {'count': 40, 'render_type': 'OBJECT',
                                                         'instance_object': {'object': handle(operand)}}}),
        op('modifier_configure', handle(cube()), {'type': 'PARTICLE_SYSTEM', 'modifier': 'Commit existing hair',
                                                  'settings': {'particle_system.seed': 3}})],
        writes=[cube()], reads=[operand], file_fallback=True,
        intended=lambda obj: obj.particle_systems[0].settings.instance_object == window.scene.objects['Commit operand'])
    cube().modifiers.remove(cube().modifiers['Commit existing hair'])
    queries.invalidate()
    live_limit = cube().constraints.new('LIMIT_LOCATION')
    live_limit.name = 'Commit live limit'
    queries.invalidate()
    execute_case('constraint_configure', [op('constraint_configure', handle(cube()), {
        'constraint': 'Commit live limit', 'influence': .25, 'settings': {'use_min_x': True, 'min_x': -1}})],
        writes=[cube()], intended=lambda obj: abs(obj.constraints['Commit live limit'].influence - .25) < 1e-6
        and obj.constraints['Commit live limit'].use_min_x)
    # operator_run's constraints family beside the typed kinds: the stack commits by name, before typed appends.
    execute_case('operator_run_constraints', [
        op('constraint_add', handle(cube()), {'type': 'LIMIT_ROTATION', 'name': 'Commit added limit'}),
        op('operator_run', handle(cube()), {'operator': 'constraint.copy', 'properties': {'constraint': 'Commit live limit'}}),
        op('operator_run', handle(cube()), {'operator': 'constraint.move_to_index',
                                            'properties': {'constraint': 'Commit live limit.001', 'index': 0}}),
        op('operator_run', handle(cube()), {'operator': 'constraint.delete', 'properties': {'constraint': 'Commit added limit'}})],
        writes=[cube()], intended=lambda obj: [item.name for item in obj.constraints] == ['Commit live limit.001',
                                                                                          'Commit live limit'])
    execute_case('operator_run_transforms', [
        op('operator_run', handle(cube()), {'operator': 'transform.translate', 'properties': {'value': [0, 0, 1]}}),
        op('operator_run', handle(cube()), {'operator': 'transform.rotate', 'select': 'INDICES', 'face_indices': [0],
                                            'properties': {'value': .5, 'orient_axis': 'Z', 'center_override': [1, 0, 0]}})],
        writes=[cube()], intended=lambda obj: abs(min(vertex.co.z for vertex in obj.data.vertices)) < 1e-5)
    cube().constraints.remove(cube().constraints['Commit live limit'])
    queries.invalidate()
    node_edit_effects()
    bpy.ops.object.armature_add()
    rig = bpy.context.object
    rig.name = 'Commit bone rig'
    queries.invalidate()
    execute_case('bone_set', [
        op('bone_set', handle(rig), {'bone': 'Bone', 'tail': [0, .5, 1.5], 'roll': .2,
                                     'settings': {'use_deform': False, 'bbone_segments': 3}}),
        op('bone_set', handle(rig), {'bone': 'Bone', 'rotation_mode': 'XYZ', 'rotation': [.3, 0, 0],
                                     'location': [0, .1, 0], 'pose_settings': {'ik_stretch': .25}})],
        target='Commit bone rig', writes=[rig],
        intended=lambda obj: vector(obj.data.bones['Bone'].tail_local) == [0, .5, 1.5] and not obj.data.bones['Bone'].use_deform
        and obj.pose.bones['Bone'].rotation_mode == 'XYZ' and abs(obj.pose.bones['Bone'].rotation_euler[0] - .3) < 1e-6)


def node_edit_effects():
    """node_edit on a material tree, a Geometry Nodes catalog graph and the World: staged equals committed."""
    window = bpy.context.window_manager.windows[0]
    cube().data.materials.append(bpy.data.materials.new('Commit wood'))
    queries.invalidate()
    execute_case('node_edit_material', [op('node_edit', handle(cube()), {'material': 'Commit wood', 'edits': [
        {'op': 'add', 'node': 'Commit noise', 'type': 'ShaderNodeTexNoise', 'inputs': {'Scale': 3}},
        {'op': 'link', 'from_node': 'Commit noise', 'from_socket': 'Fac', 'to_node': 'Principled BSDF', 'to_socket': 'Roughness'},
        {'op': 'add', 'node': 'Commit frame', 'type': 'NodeFrame'},
        {'op': 'parent', 'nodes': ['Commit noise'], 'frame': 'Commit frame'},
        {'op': 'set', 'node': 'Principled BSDF', 'settings': {'label': 'Commit', 'hide': True}, 'inputs': {'Metallic': .5}}]})],
        writes=[cube()], intended=lambda obj: obj.material_slots[-1].material.name == 'Commit wood'
        and obj.material_slots[-1].material.node_tree.nodes['Commit noise'].parent is not None)
    cube().data.materials.pop()
    queries.invalidate()
    execute_case('node_edit_geometry', [
        op('geometry_nodes', handle(cube()), {'name': 'Commit scatter', 'nodes': [
            {'id': 'inp', 'type': 'NodeGroupInput'},
            {'id': 'scatter', 'type': 'GeometryNodeDistributePointsOnFaces', 'inputs': {'Density': 3}},
            {'id': 'out', 'type': 'NodeGroupOutput'}], 'links': [
            {'from_node': 'inp', 'from_socket': 'Geometry', 'to_node': 'scatter', 'to_socket': 'Mesh'},
            {'from_node': 'scatter', 'from_socket': 'Points', 'to_node': 'out', 'to_socket': 'Geometry'}]}),
        op('node_edit', handle(cube()), {'modifier': 'Commit scatter', 'edits': [
            {'op': 'set', 'node': 'scatter', 'inputs': {'Density': 2}},
            {'op': 'add', 'node': 'lift', 'type': 'GeometryNodeSetPosition'},
            {'op': 'insert', 'node': 'lift', 'from_node': 'scatter', 'from_socket': 'Points', 'to_node': 'out',
             'to_socket': 'Geometry'}]})],
        writes=[cube()], intended=lambda obj: 'lift' in obj.modifiers['Commit scatter'].node_group.nodes)
    scene_handle = scene_settings.execute(window, {})['handle']
    world = lambda: bpy.context.window_manager.windows[0].scene.world.node_tree
    custom_case('node_edit_world', [op('node_edit', scene_handle, {'world': True, 'edits': [
        {'op': 'add', 'node': 'Commit gain', 'type': 'ShaderNodeValue'},
        {'op': 'set', 'node': 'Commit gain', 'outputs': {'Value': 1.5}},
        {'op': 'link', 'from_node': 'Commit gain', 'from_socket': 'Value', 'to_node': 'Background', 'to_socket': 'Strength'}]})],
        lambda: 'Commit gain' in world().nodes and world().nodes['Background'].inputs['Strength'].is_linked,
        lambda: 'Commit gain' not in world().nodes, scene=True, file_fallback=True)


def silent_rotation_mode_mismatch_rolls_back():
    window, source = bpy.context.window_manager.windows[0], cube()
    source.rotation_mode = 'QUATERNION'
    before = native._fingerprint(source)
    value = args_for(window, [op('keyframe_insert', handle(source), {'property':'rotation_euler','value':[0,0,1.2],'frame':1})],
                     writes=[source], store=FullStore())
    job = native.begin(window, 'angelo_scene_program', value, store=FullStore()); settle(job)
    setter, calls = program_commit._set_transforms, []
    def omit_mode_once(obj, values):
        calls.append(1)
        if len(calls) == 1:
            for name, value in values.items():
                if name != 'rotation_mode':
                    setattr(obj, name, value)
        else:
            setter(obj, values)
    with patch.object(program_commit, '_set_transforms', side_effect=omit_mode_once):
        result = job.commit()
    check('silent_current_mode_mismatch_is_not_false_success', not result['success'] and
          'does not match' in result['error'] and not result['partial_changes_possible'] and
          native._fingerprint(source) == before and no_stages())


def rollback_after_transform_and_pointer():
    window = bpy.context.window_manager.windows[0]
    source, operand = cube(), window.scene.objects['Commit operand']
    source.rotation_mode = 'QUATERNION'
    modifier = source.modifiers.new('Commit rollback Boolean', 'BOOLEAN'); modifier.object = operand
    before = native._fingerprint(source)
    values = args_for(window, [op('keyframe_insert', handle(source), {'property':'rotation_euler','value':[0,0,1.2],'frame':1}),
                     op('modifier_configure', handle(source), {'type':'BOOLEAN','modifier':modifier.name,
                        'settings':{'operand':handle(operand),'operation':'UNION'}})], writes=[source], reads=[operand], store=FullStore())
    job = native.begin(window, 'angelo_scene_program', values, store=FullStore()); settle(job)
    original_setter = native._set_settings
    calls = []
    def fail_after_settings(target, settings, mapping):
        original_setter(target, settings, mapping)
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('injected after current mode and pointer transfer')
    with patch.object(native, '_set_settings', side_effect=fail_after_settings):
        result = job.commit()
    check('rotation_pointer_failure_rolls_back_local_journal', not result['success'] and not result['partial_changes_possible'] and
          source.rotation_mode == 'QUATERNION' and native._fingerprint(source) == before and no_stages())


def animation_editing_effects():
    """The animation editing kinds (phase 5): keys, F-Curve modifiers, key copy, Actions, NLA and markers."""
    window = bpy.context.window_manager.windows[0]
    live = lambda: bpy.context.window_manager.windows[0].scene.objects
    source, camera = cube(), window.scene.objects['Camera']
    source.animation_data_clear()
    camera.animation_data_clear()
    for frame, x in ((1, 0), (10, 2), (20, 1)):
        source.location = camera.location = (x, 0, 0)
        source.keyframe_insert('location', frame=frame)
        camera.keyframe_insert('location', frame=frame)
    window.scene.frame_set(1)
    queries.invalidate()
    execute_case('key_editing', [op('keyframe_configure', handle(cube()), {'interpolation': 'LINEAR'}),
                                 op('keyframe_edit', handle(cube()), {'operation': 'OFFSET', 'property': 'location',
                                                                      'frame_range': [10, 20], 'offset': 5}),
                                 op('keyframe_edit', handle(cube()), {'operation': 'INSERT', 'property': 'location',
                                                                      'frames': [3]}),
                                 op('fcurve_modifier', handle(cube()), {'property': 'location', 'index': 0,
                                                                        'type': 'CYCLES', 'name': 'Commit loop'})],
                 writes=[source], intended=lambda obj: len(curves(obj.animation_data.action)[0].keyframe_points) == 4
                 and curves(obj.animation_data.action)[0].modifiers[0].name == 'Commit loop')
    source = cube()
    execute_case('keyframe_copy', [op('keyframe_copy', handle(cube()), {'source': handle(window.scene.objects['Camera']),
                                                                        'property': 'location', 'offset': 30})],
                 writes=[source], reads=[window.scene.objects['Camera']],
                 intended=lambda obj: len(curves(obj.animation_data.action)[0].keyframe_points) == 6)
    original = cube().animation_data.action.name
    custom_case('action_set', [op('action_set', handle(cube()), {'mode': 'NEW', 'name': 'Commit idle',
                                                                 'stash_previous': True})],
                lambda: live()['Cube'].animation_data.action.name == 'Commit idle'
                and live()['Cube'].animation_data.nla_tracks[0].strips[0].action.name == original,
                lambda: live()['Cube'].animation_data.action.name == original
                and not len(live()['Cube'].animation_data.nla_tracks), writes=[cube()])
    action = 'dat:%s:ACTION:%d' % (queries.document_epoch(), cube().animation_data.action.session_uid)
    custom_case('nla_edit', [op('nla_edit', handle(cube()), {'operation': 'TRACK_ADD', 'track': 'Commit base'}),
                             op('nla_edit', handle(cube()), {'operation': 'STRIP_ADD', 'track': 'Commit base',
                                                             'action': action, 'frame': 40, 'settings': {'repeat': 2.0}})],
                lambda: live()['Cube'].animation_data.nla_tracks['Commit base'].strips[0].action
                == live()['Cube'].animation_data.action,
                lambda: not len(live()['Cube'].animation_data.nla_tracks), writes=[cube()])
    scene_handle = scene_settings.execute(window, {})['handle']
    markers = lambda: [(m.name, m.frame, m.camera.name if m.camera else None)
                       for m in bpy.context.window_manager.windows[0].scene.timeline_markers]
    custom_case('marker_edit', [op('marker_edit', scene_handle, {'operation': 'ADD', 'name': 'Commit marker', 'frame': 7,
                                                                 'camera': handle(window.scene.objects['Camera'])})],
                lambda: markers() == [('Commit marker', 7, 'Camera')], lambda: markers() == [],
                reads=[window.scene.objects['Camera']], scene=True)


def merged_track_effects():
    """Phase-5 tracks in one program each: keys, a material tree and an operator on one object; markers and the World."""
    window = bpy.context.window_manager.windows[0]
    cube().data.materials.append(bpy.data.materials.new('Commit merged'))
    queries.invalidate()
    execute_case('keys_and_nodes', [
        op('keyframe_edit', handle(cube()), {'operation': 'OFFSET', 'property': 'location', 'offset': 2}),
        op('node_edit', handle(cube()), {'material': 'Commit merged', 'edits': [
            {'op': 'set', 'node': 'Principled BSDF', 'inputs': {'Roughness': .25}}]})],
        writes=[cube()], intended=lambda obj: curves(obj.animation_data.action)[0].keyframe_points[0].co[0] == 3
        and abs(obj.material_slots[-1].material.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value
                - .25) < 1e-6)
    # operator_run swaps the mesh data the material slot is linked to; node_edit's tree follows the swapped data.
    execute_case('nodes_and_operator', [
        op('operator_run', handle(cube()), {'operator': 'transform.translate', 'properties': {'value': [0, 0, 1]}}),
        op('node_edit', handle(cube()), {'material': 'Commit merged', 'edits': [
            {'op': 'set', 'node': 'Principled BSDF', 'inputs': {'Metallic': .75}}]})],
        writes=[cube()], intended=lambda obj: abs(min(vertex.co.z for vertex in obj.data.vertices)) < 1e-5
        and abs(obj.material_slots[-1].material.node_tree.nodes['Principled BSDF'].inputs['Metallic'].default_value
                - .75) < 1e-6)
    cube().data.materials.pop()
    queries.invalidate()
    execute_case('keys_and_operator', [
        op('keyframe_edit', handle(cube()), {'operation': 'OFFSET', 'property': 'location', 'offset': 2}),
        op('operator_run', handle(cube()), {'operator': 'transform.translate', 'properties': {'value': [0, 0, 1]}})],
        writes=[cube()], intended=lambda obj: curves(obj.animation_data.action)[0].keyframe_points[0].co[0] == 3
        and abs(min(vertex.co.z for vertex in obj.data.vertices)) < 1e-5)
    scene_handle = scene_settings.execute(window, {})['handle']
    scene = lambda: bpy.context.window_manager.windows[0].scene
    custom_case('markers_and_world', [
        op('marker_edit', scene_handle, {'operation': 'ADD', 'name': 'Commit merged marker', 'frame': 9}),
        op('node_edit', scene_handle, {'world': True, 'edits': [{'op': 'add', 'node': 'Commit merged gain',
                                                                  'type': 'ShaderNodeValue'}]})],
        lambda: [m.name for m in scene().timeline_markers] == ['Commit merged marker']
        and 'Commit merged gain' in scene().world.node_tree.nodes,
        lambda: not len(scene().timeline_markers) and 'Commit merged gain' not in scene().world.node_tree.nodes,
        scene=True, file_fallback=True)


def curve_moves_never_roll_back():
    """Regression: with the window's active depsgraph, moving a curve, lattice or text after an Undo,
    or while disabled or hidden, rolled back on stale cached bounds. Each commits and is checked live."""
    window = bpy.context.window_manager.windows[0]
    outcomes = {}
    for kind in ('poly', 'bezier', 'lattice', 'text'):
        for state in ('after_undo', 'disabled', 'hidden'):
            if kind == 'lattice':
                data = bpy.data.lattices.new('QA Fidelity ' + kind)
            elif kind == 'text':
                data = bpy.data.curves.new('QA Fidelity ' + kind, 'FONT')
                data.body = 'Hi'
            else:
                data = bpy.data.curves.new('QA Fidelity ' + kind, 'CURVE')
                data.dimensions = '3D'
                spline = data.splines.new('BEZIER' if kind == 'bezier' else 'POLY')
                points = spline.bezier_points if kind == 'bezier' else spline.points
                points.add(2)
                for index, point in enumerate(points):
                    point.co = (index, 0, 0.5 * index) if kind == 'bezier' else (index, 0, 0.5 * index, 1)
            obj = bpy.data.objects.new('QA Fidelity T', data)
            window.scene.collection.objects.link(obj)
            if state == 'disabled':
                obj.hide_viewport = True
            elif state == 'hidden':
                obj.hide_set(True)
            bpy.context.evaluated_depsgraph_get()
            bpy.ops.ed.undo_push(message='QA fidelity baseline')
            if state == 'after_undo':
                obj.location.x += 1
                bpy.ops.ed.undo_push(message='QA fidelity plain move')
                bpy.ops.ed.undo()
                obj = window.scene.objects['QA Fidelity T']
            queries.invalidate()
            value = args_for(window, [op('transform', handle(obj), {'location': [2, 0, 0]})], writes=[obj])
            job = native.begin(window, 'angelo_scene_program', value)
            if settle(job).get('ready_to_commit'):
                bpy.ops.angelo.commit_effects_qa('EXEC_DEFAULT', True, program_id=job.id)
            obj = window.scene.objects['QA Fidelity T']
            outcomes[kind + '_' + state] = job.result.get('status') == 'committed' and abs(obj.location.x - 2) < 1e-6
            report.setdefault('fidelity_errors', {})[kind + '_' + state] = job.result.get('error')
            window.scene.collection.objects.unlink(obj)
            bpy.data.objects.remove(obj)
            queries.invalidate()
    report['cases']['curve_moves'] = outcomes
    check('curve_lattice_and_text_moves_commit_with_the_active_depsgraph', all(outcomes.values()))


def run():
    try:
        native.register(); bpy.utils.register_class(EffectCommit)
        bpy.context.preferences.edit.use_global_undo = True
        bpy.context.preferences.filepaths.file_preview_type = 'NONE'
        # Separate preparation, operation, validation and read-back yields so
        # each feature must survive multiple ownership transfers before commit.
        with patch.object(native.BlenderProgramBackend, 'memory_staging', STORAGE_MODE == 'native'), \
                patch.object(native.BlenderProgramBackend, 'fuse_phases', False):
            rotations(); mesh_and_material_effects(); pointer_effects(); creation_and_weights(); camera_light_effects()
            next_turn_with_existing_node_pointer(); shade_stack_and_parent_hydration()
            silent_rotation_mode_mismatch_rolls_back(); rollback_after_transform_and_pointer()
            native_coverage_effects()
            animation_editing_effects()
            merged_track_effects()
            curve_moves_never_roll_back()
        check('every_current_closed_kind_has_committed_state_coverage', seen == set(OPERATION_SCHEMAS))
        report['success'] = True
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
        (OUT/'results.json').write_text(json.dumps(report, indent=2))
        print('ANGELO_COMMIT_EFFECTS', json.dumps(report), flush=True)
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
