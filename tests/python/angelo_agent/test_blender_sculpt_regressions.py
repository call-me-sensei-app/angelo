# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproductions of the five sculpt audit findings. Native isolated GUI only.

Never starts a model, authenticates an account, or opens a user's project.
Provider-image ordering regressions live in test_native_sculpt.py.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np
from mathutils import Vector

assert '--factory-startup' in sys.argv, 'Use an isolated factory-startup instance'
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-sculpt-regressions'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ['ANGELO_CONNECTIONS_DIR'] = str(OUT / 'accounts')
os.environ['ANGELO_STATE_DIR'] = str(OUT / 'state')
os.environ['ANGELO_DISABLE_DEFAULT_CHAT'] = '1'
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))

from angelo_agent import sculpt
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.references import ReferenceStore

report = {'checks': {}, 'paid_requests': 0}
window = bpy.context.window_manager.windows[0]
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
executor = BlenderExecutor(window)
executor.observation_store = ReferenceStore(OUT, '86a778a9-dfcf-436a-a7b9-732878d935f6')


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def reset():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)


def sphere(name='Regression sphere'):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def inspect(obj):
    return executor.execute('angelo_inspect_sculpt', {'object':obj.name, 'probes':[]})


def prepare(obj, add=0):
    state = inspect(obj)
    return executor.execute('angelo_prepare_sculpt', {'object':obj.name,
                            'mesh_revision':state['mesh_revision'], 'add_levels':add})


def brush_args(obj, state, scale=1):
    return {'object':obj.name, 'mesh_revision':state['mesh_revision'], 'brush':'DRAW',
            'radius':.3*scale, 'strength':.7, 'direction':'ADD', 'symmetry':'NONE',
            'normal':[1,0,0], 'front_faces_only':True, 'spacing':.2,
            'paths':[[{'position':[scale,0,0], 'pressure':1}]]}


def transform_regressions():
    for label, local, parent_scale in (
            ('uniform_negative',(-1,-1,-1),None),
            ('reflected_x',(-1,1,1),None), ('reflected_y',(1,-1,1),None),
            ('reflected_z',(1,1,-1),None),
            ('inherited_reflection',(1,1,1),(-1,1,1)),
            ('compensated_negative',(-1,-1,-1),(-1,-1,-1)),
            ('compensated_nonuniform',(.5,2,1),(2,.5,1))):
        reset()
        obj = sphere(label)
        obj.scale = local
        if parent_scale:
            parent = bpy.data.objects.new('Regression parent', None)
            window.scene.collection.objects.link(parent)
            parent.scale = parent_scale
            obj.parent = parent
        window.view_layer.update()
        before = sculpt.snapshot(window, obj)
        matrix = obj.matrix_world.copy()
        state = inspect(obj)
        check(label+'_blocker_visible', bool(state['edit_blockers']))
        try:
            prepare(obj)
        except sculpt.SculptPreflightError as error:
            check(label+'_host_rejected_before_edit', not error.partial_changes_possible and 'scale' in str(error))
        else:
            check(label+'_host_rejected_before_edit', False)
        check(label+'_transforms_and_mesh_preserved', obj.mode=='OBJECT' and matrix==obj.matrix_world and
              np.array_equal(before['positions'], sculpt.snapshot(window,obj)['positions']))
        # Direct native operator calls and Repeat must not bypass this guard.
        bpy.ops.object.mode_set(mode='SCULPT')
        try:
            bpy.ops.sculpt.angelo_sculpt_pass('EXEC_DEFAULT', True,
                stroke=[{'name':'0', 'location':(1,0,0), 'pressure':1, 'is_start':True,
                         'mouse':(0,0), 'mouse_event':(0,0), 'x_tilt':0, 'y_tilt':0, 'time':0, 'size':0}],
                brush_type='DRAW', radius=.3, strength=.7, symmetry='NONE',
                surface_normal=(1,0,0), front_faces_only=True)
        except RuntimeError as error:
            check(label+'_native_guard', 'scale' in str(error))
        else:
            check(label+'_native_guard', False)
    report['positive_scale_strokes'] = []
    for scale, rotation in ((.5,0), (1,.55), (2,1.2)):
        reset()
        obj = sphere()
        obj.scale = (scale,)*3
        obj.rotation_euler = (.2, rotation, .35)
        window.view_layer.update()
        state = prepare(obj)['verified']
        before = sculpt.snapshot(window,obj)['world']
        applied = executor.execute('angelo_sculpt_batch', brush_args(obj,state,scale))
        after = sculpt.snapshot(window,obj)['world']
        shifts = after-before
        changed = np.linalg.norm(shifts,axis=1)>1e-6
        radial = before/np.maximum(np.linalg.norm(before,axis=1)[:,None],1e-12)
        outward = np.sum(shifts[changed]*radial[changed],axis=1)
        report['positive_scale_strokes'].append({'scale':scale, 'mean_outward':float(outward.mean())})
        check(f'positive_uniform_rotated_{scale}_add_is_outward', applied['success'] and changed.any() and outward.mean()>0)


def density_regressions():
    reset()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=8)
    obj = bpy.context.active_object
    check('dense_triangle_fixture', len(obj.data.vertices)==163842 and len(obj.data.polygons)==327680)
    vertices, faces = len(obj.data.vertices), len(obj.data.polygons)
    result = prepare(obj,0)
    check('dense_existing_mesh_enters_sculpt_without_new_levels', result['success'] and obj.mode=='SCULPT' and
          result['verified']['vertices']==vertices and not len(obj.modifiers) and len(obj.data.polygons)==faces)
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        prepare(obj,1)
    except sculpt.SculptPreflightError as error:
        check('dense_subdivision_budget_still_rejected_before_changes', 'budget' in str(error) and
              obj.mode=='OBJECT' and not len(obj.modifiers) and len(obj.data.vertices)==vertices)
    else:
        check('dense_subdivision_budget_still_rejected_before_changes', False)


def visibility_regressions():
    reset()
    obj = sphere('Visible target')
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32,ring_count=16,radius=.6,location=(0,-3,0))
    foreground = bpy.context.active_object
    foreground.name = 'Foreground occluder'
    area.spaces.active.shading.show_xray = True
    state_before = (bpy.context.mode, window.view_layer.objects.active.session_uid,
                    [(o.session_uid,o.select_get(),o.hide_get(),o.hide_viewport) for o in window.scene.objects],
                    area.spaces.active.region_3d.view_matrix.copy(), area.spaces.active.shading.show_xray,
                    area.spaces.active.overlay.show_overlays)
    view = executor.execute('angelo_sculpt_view', {'object':obj.name,'views':['FRONT'],'resolution':384})
    result = executor.execute('angelo_sculpt_target', {'object':obj.name,'mesh_revision':view['mesh_revision'],
                              'view_reference':view['views'][0]['id'],'pixels':[[.5,.5],[.82,.5],[0,0]]})
    report['occlusion_hits'] = result['hits']
    camera = executor.observation_store.observation_camera(view['views'][0]['id'])
    report['native_depth'] = {'min':float(camera['depths'].min()), 'max':float(camera['depths'].max()),
                             'center':float(camera['depths'][192,192]), 'clip_depth':camera['clip_depth']}
    check('foreground_pixels_are_not_hidden_target_hits', not result['hits'][0]['hit'] and result['hits'][0]['reason']=='occluded' and
          'position' not in result['hits'][0])
    check('unoccluded_pixels_still_hit_target', result['hits'][1]['hit'] and not result['hits'][2]['hit'])
    scene_hit = window.scene.ray_cast(window.view_layer.depsgraph,Vector((0,-5,0)),Vector((0,1,0)))
    check('native_depth_agrees_with_actual_foreground_object', scene_hit[0] and scene_hit[4].original==foreground)
    state_after = (bpy.context.mode, window.view_layer.objects.active.session_uid,
                   [(o.session_uid,o.select_get(),o.hide_get(),o.hide_viewport) for o in window.scene.objects],
                   area.spaces.active.region_3d.view_matrix.copy(), area.spaces.active.shading.show_xray,
                   area.spaces.active.overlay.show_overlays)
    check('capture_and_target_preserve_user_view_and_visibility', state_before==state_after)
    # The depth belongs to the admitted image, not a later view of the scene.
    foreground.hide_set(True)
    hidden_view = executor.execute('angelo_sculpt_view', {'object':obj.name,'views':['FRONT'],'resolution':384})
    clear = executor.execute('angelo_sculpt_target', {'object':obj.name,'mesh_revision':hidden_view['mesh_revision'],
                             'view_reference':hidden_view['views'][0]['id'],'pixels':[[.5,.5]]})
    check('hidden_neighbor_does_not_occlude_native_pixels', clear['hits'][0]['hit'] and clear['hits'][0]['position'][1]<-.9)
    camera = executor.observation_store.observation_camera(view['views'][0]['id'])
    check('depth_is_runtime_only_not_tool_json', 'depths' in camera and 'depths' not in json.dumps(view))
    before_images = len(bpy.data.images)
    with patch.object(executor.observation_store,'new_observation',side_effect=OSError('Injected disk reservation failure')):
        try:
            executor.execute('angelo_sculpt_view', {'object':obj.name,'views':['FRONT'],'resolution':384})
        except OSError:
            pass
    check('failed_image_reservation_does_not_leak_image_ids', len(bpy.data.images)==before_images)


def partial_change_regressions():
    import addon_utils
    from angelo_agent import ui
    # Suppress discovery workers: this is a native approval/Undo fixture only.
    ui._authentication.start = lambda *a, **kw: None
    ui.refresh_connected_models = lambda *a, **kw: None
    addon_utils.enable('angelo_agent', default_set=False, persistent=False)
    controller = ui.get_controller()

    def begin_case_request(identity):
        # Each injected failure is a separate human request. Undo or resetting
        # the fixture alone cannot renew the previous failed request's authority.
        check(identity+'_no_previous_pending_call', not controller.pending and not controller.busy)
        window.view_layer.update()
        checkpoint = ui.capture_checkpoint(controller)
        generation = controller._request_generation
        controller._begin_metrics(new_request=True)
        controller.request_checkpoint = checkpoint['id']
        report.setdefault('request_checkpoints', {})[identity] = checkpoint['id']
        check(identity+'_fresh_request_generation', controller._request_generation == generation + 1)
        return checkpoint

    def approve(name, args, identity):
        responses = []
        controller.busy = True
        controller.request_tool(identity,name,args,responses.append)
        controller.pump_tools(executor,ui.document_key())
        check(identity+'_pending_approval', len(controller.pending)==1 and not responses)
        call = next(iter(controller.pending.values()))
        outcome = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=controller.state['id'])
        controller.busy = False
        check(identity+'_approval_finishes_for_undo', 'FINISHED' in outcome and len(responses)==1)
        return responses[0]

    reset()
    obj = sphere('Preparation failure target')
    before = inspect(obj)
    bpy.ops.ed.undo_push(message='Before failing preparation')
    checkpoint = begin_case_request('partial-prep')
    original = sculpt.snapshot
    def fail_prepared(win,ob,*a,**kw):
        if ob.mode=='SCULPT':
            raise RuntimeError('Injected post-preparation read-back failure')
        return original(win,ob,*a,**kw)
    with patch.object(sculpt,'snapshot',fail_prepared):
        result = approve('angelo_prepare_sculpt', {'object':obj.name,'mesh_revision':before['mesh_revision'],'add_levels':1}, 'partial-prep')
    report['preparation_error'] = result
    check('preparation_error_reports_actual_partial_state', result['partial_changes_possible'] and result['changes']['uncertain'] and
          result['current_state']['mode']=='SCULPT' and result['current_state']['multires'][0]['total_levels']==1 and
          result['request_checkpoint']==checkpoint['id'] and 'readback_error' in result and 'verified' not in result)
    check('preparation_error_does_not_automatically_retry', len(obj.modifiers)==1 and obj.modifiers[0].total_levels==1)
    bpy.ops.ed.undo()
    obj = window.scene.objects['Preparation failure target']
    check('failed_preparation_remains_undoable', obj.mode=='OBJECT' and not len(obj.modifiers))
    # The failure still fences this same request after a verified Undo. Preserve
    # that negative before opening the next independent native fixture case.
    blocked = []
    controller.busy = True
    controller.request_tool('partial-prep-same-request', 'angelo_prepare_sculpt',
        {'object':obj.name, 'mesh_revision':before['mesh_revision'], 'add_levels':1}, blocked.append)
    controller.pump_tools(executor, ui.document_key())
    controller.busy = False
    check('same_request_recovery_gate_preserved', len(blocked)==1 and
          blocked[0].get('error_code')=='RECOVERY_REQUIRED' and not controller.pending)

    reset()
    obj = sphere('Brush failure target')
    before = prepare(obj)['verified']
    bpy.ops.ed.undo_push(message='Before failing brush read-back')
    baseline = original(window,obj)['positions']
    brush_checkpoint = begin_case_request('partial-brush')
    check('independent_brush_checkpoint_is_distinct', brush_checkpoint['id']!=checkpoint['id'])
    def fail_changed(win,ob,*a,**kw):
        result = original(win,ob,*a,**kw)
        if result['revision']!=before['mesh_revision']:
            raise RuntimeError('Injected post-brush read-back failure')
        return result
    with patch.object(sculpt,'snapshot',fail_changed):
        result = approve('angelo_sculpt_batch',brush_args(obj,before),'partial-brush')
    report['brush_error'] = result
    check('brush_error_uses_fresh_request_checkpoint', result.get('request_checkpoint')==brush_checkpoint['id'])
    changed = original(window,obj)['positions']
    check('brush_error_reports_partial_geometry', result['partial_changes_possible'] and result['mutation_stage']=='Verify native sculpt brush' and
          result['changes']['modified'][0]['fields']==['geometry'] and not np.array_equal(baseline,changed))
    bpy.ops.ed.undo()
    obj = window.scene.objects['Brush failure target']
    check('failed_brush_readback_one_undo_restores_geometry', np.array_equal(baseline,original(window,obj)['positions']))
    addon_utils.disable('angelo_agent', default_set=False)


def run():
    try:
        with bpy.context.temp_override(window=window,area=area,region=region):
            (OUT/'recovery').mkdir(exist_ok=True)
            bpy.context.preferences.filepaths.temporary_directory = str(OUT/'recovery')
            bpy.context.preferences.edit.use_global_undo = True
            bpy.ops.ed.undo_push(message='Regression initial state')
            transform_regressions()
            density_regressions()
            visibility_regressions()
            partial_change_regressions()
            report['success'] = True
    except Exception:
        report['success'] = False
        report['error'] = traceback.format_exc()
    (OUT/'sculpt-regressions.json').write_text(json.dumps(report,indent=2))
    print('ANGELO_SCULPT_REGRESSIONS',json.dumps(report),flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run,first_interval=1)
