# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real compiled sculpt engine / undo / offscreen pixels, no inference or billing."""
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

assert '--factory-startup' in sys.argv, 'Use an isolated factory-startup instance'
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-native-sculpt-qa'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ['ANGELO_CONNECTIONS_DIR'] = str(OUT / 'accounts')
os.environ['ANGELO_STATE_DIR'] = str(OUT / 'state')
os.environ['ANGELO_DISABLE_DEFAULT_CHAT'] = '1'
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.references import ReferenceStore

report = {'checks': {}, 'timings': {}, 'paid_requests': 0}


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def main():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        (OUT / 'recovery').mkdir(exist_ok=True)
        bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'recovery')
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64)
        bpy.ops.object.shade_smooth()
        obj = bpy.context.active_object
        obj.name = 'Sculpt QA Sphere'
        bpy.ops.ed.undo_push(message='Sculpt QA initial scene')
        executor = BlenderExecutor(window)
        executor.observation_store = ReferenceStore(OUT, '92447c82-6f6e-464b-8497-1d65b1bf1cb2')
        initial = executor.execute('angelo_inspect_sculpt', {'object': obj.name, 'probes': [[.3, -.95, .2]]})
        check('surface_probe', initial['probes'][0]['distance'] < .1)
        check('evaluated_mesh_revision', len(initial['mesh_revision']) == 64)
        prepared = executor.execute('angelo_prepare_sculpt', {'object': obj.name, 'mesh_revision': initial['mesh_revision'], 'add_levels': 0})
        check('native_sculpt_mode', prepared['success'] and obj.mode == 'SCULPT')
        initial = prepared['verified']
        bpy.ops.ed.undo_push(message='Sculpt QA prepared mode')
        old_brush = bpy.context.tool_settings.sculpt.brush
        old_strength = old_brush.strength if old_brush else None
        old_symmetry = (obj.data.use_mirror_x, obj.data.use_mirror_y, obj.data.use_mirror_z)
        before = np.empty(len(obj.data.vertices)*3, dtype=np.float32)
        obj.data.vertices.foreach_get('co', before)
        args = {'object': obj.name, 'mesh_revision': initial['mesh_revision'], 'brush': 'DRAW', 'radius': .3,
                'strength': .5, 'direction': 'ADD', 'symmetry': 'X', 'normal': [0, -1, 0], 'front_faces_only': True, 'spacing': .2,
                'paths': [[{'position': [.3, -.932, .2], 'pressure': .3}, {'position': [.3, -.932, -.15], 'pressure': .8}],
                          [{'position': [.45, -.865, .1], 'pressure': .8}]]}
        result = executor.execute('angelo_sculpt_batch', args)
        report['draw_result'] = result
        actual = np.empty_like(before)
        obj.data.vertices.foreach_get('co', actual)
        report['actual_data_max_delta'] = float(np.max(np.abs(actual-before)))
        report['timings']['draw_pass'] = {k: result[k] for k in ('native_exec_ms', 'native_brush_ms', 'dabs')}
        check('real_native_displacement', result['changed_points'] > 20 and result['max_displacement_world'] > .001)
        check('batched_multi_path', result['paths'] == 2 and result['dabs'] > 2)
        check('artist_brush_restored', bpy.context.tool_settings.sculpt.brush == old_brush and (not old_brush or old_brush.strength == old_strength))
        check('artist_symmetry_restored', old_symmetry == (obj.data.use_mirror_x, obj.data.use_mirror_y, obj.data.use_mirror_z))
        check('no_modal_cursor_grab', not any('sculpt' in getattr(op, 'bl_idname', '').lower() for op in window.modal_operators))
        after = np.empty_like(before)
        obj.data.vertices.foreach_get('co', after)
        deltas = (after - before).reshape(-1, 3)
        check('back_surface_protected', float(np.abs(deltas[before.reshape(-1, 3)[:,1] > .4]).max()) < 1e-7)
        old_args = dict(args)
        try:
            executor.execute('angelo_sculpt_batch', old_args)
        except RuntimeError as error:
            check('stale_surface_rejected', 'surface changed' in str(error))
        else:
            check('stale_surface_rejected', False)
        observation = executor.execute('angelo_sculpt_view', {'object': obj.name, 'views': ['FRONT', 'THREE_QUARTER'], 'resolution': 512})
        report['views'] = observation['views']
        report['timings']['views'] = observation['capture_ms']
        check('fresh_native_pixels', len(observation['views']) == 2 and all(Path(v['path']).stat().st_size > 4000 for v in observation['views']))
        aim = executor.execute('angelo_sculpt_target', {'object':obj.name, 'mesh_revision':observation['mesh_revision'],
                               'view_reference':observation['views'][0]['id'], 'pixels':[[.5,.5],[.65,.45],[0,0]]})
        report['image_targets'] = aim
        camera = executor.observation_store.observation_camera(observation['views'][0]['id'])
        report['native_depth_debug'] = {'min':float(camera['depths'].min()), 'max':float(camera['depths'].max()),
                                       'center':float(camera['depths'][256,256]), 'clip_depth':camera['clip_depth']}
        check('image_space_raycast_to_3d', aim['hits'][0]['hit'] and aim['hits'][0]['position'][1] < -.9 and
              aim['hits'][1]['hit'] and not aim['hits'][2]['hit'])
        bpy.ops.ed.undo()
        obj = window.scene.objects['Sculpt QA Sphere']
        restored = np.empty_like(before)
        obj.data.vertices.foreach_get('co', restored)
        check('one_undo_restores_entire_pass', np.array_equal(before, restored))
        executor = BlenderExecutor(window)
        current = executor.execute('angelo_inspect_sculpt', {'object': obj.name, 'probes': []})
        args.update(mesh_revision=current['mesh_revision'], brush='GRAB', symmetry='NONE', paths=[[
            {'position': [0, -1, 0], 'pressure': 1}, {'position': [0, -1.2, .1], 'pressure': 1}]])
        grabbed = executor.execute('angelo_sculpt_batch', args)
        check('grab_true_3d_delta', grabbed['max_displacement_world'] > .04 and grabbed['dabs'] == 2)
        report['timings']['grab_pass'] = grabbed['native_exec_ms']
        # Full protection must prevent all displacement, not just a UI mask overlay.
        bpy.ops.object.mode_set(mode='OBJECT')
        mask = obj.data.attributes.get('.sculpt_mask') or obj.data.attributes.new('.sculpt_mask', 'FLOAT', 'POINT')
        mask.data.foreach_set('value', np.ones(len(obj.data.vertices), dtype=np.float32))
        obj.data.update()
        bpy.ops.object.mode_set(mode='SCULPT')
        current = executor.execute('angelo_inspect_sculpt', {'object': obj.name, 'probes': []})
        args.update(mesh_revision=current['mesh_revision'], brush='INFLATE', paths=[[{'position': [0, -1.1, 0], 'pressure': 1}]])
        protected = executor.execute('angelo_sculpt_batch', args)
        check('mask_protects_geometry', protected['changed_points'] == 0 and 'warning' in protected)
        # Non-destructive detail preparation and Multires sculpt read-back.
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, location=(3, 0, 0))
        multi = bpy.context.active_object
        multi.name = 'Multires QA'
        state = executor.execute('angelo_inspect_sculpt', {'object': multi.name, 'probes': []})
        base_vertices = len(multi.data.vertices)
        prepared = executor.execute('angelo_prepare_sculpt', {'object': multi.name, 'mesh_revision': state['mesh_revision'], 'add_levels': 1})
        check('non_destructive_multires', len(multi.data.vertices) == base_vertices and prepared['verified']['vertices'] > base_vertices)
        args.update(object=multi.name, mesh_revision=prepared['verified']['mesh_revision'], brush='DRAW', normal=[0,-1,0],
                    paths=[[{'position': [3, -1, 0], 'pressure': 1}]])
        detailed = executor.execute('angelo_sculpt_batch', args)
        check('multires_native_sculpt', detailed['changed_points'] > 0 and detailed['verified']['mesh_revision'] != prepared['verified']['mesh_revision'])
        report['timings']['multires_pass'] = detailed['native_exec_ms']
        executor.observation_store = ReferenceStore(OUT, '92447c82-6f6e-464b-8497-1d65b1bf1cb2')
        from angelo_agent import sculpt
        from angelo_agent.sculpt import snapshot
        grid_before_view = snapshot(window, multi)
        multi_views = executor.execute('angelo_sculpt_view', {'object':multi.name, 'views':['FRONT','THREE_QUARTER'], 'resolution':512})
        grid_after_view = snapshot(window, multi)
        report['multires_views'] = multi_views['views']
        report['view_grid_debug'] = {'before':grid_before_view['revision'], 'returned':multi_views['mesh_revision'], 'after':grid_after_view['revision'],
                                    'max_coordinate_delta':float(np.max(np.abs(grid_before_view['positions']-grid_after_view['positions']))),
                                    'mask_before':grid_before_view['mask_mean'],'mask_after':grid_after_view['mask_mean']}
        multi_aim = executor.execute('angelo_sculpt_target', {'object':multi.name, 'mesh_revision':multi_views['mesh_revision'],
                                    'view_reference':multi_views['views'][0]['id'], 'pixels':[[.5,.5]]})
        report['multires_aim'] = multi_aim
        camera = executor.observation_store.observation_camera(multi_views['views'][0]['id'])
        from mathutils import Vector
        origin = Vector(camera['center']) + Vector(camera['direction'])*camera['camera_distance']
        origin += (Vector(camera['right'])+Vector(camera['up']))*(camera['radius']/512)
        inv = multi.matrix_world.inverted()
        hit = sculpt.surface(window,multi,grid_after_view).ray_cast(inv@origin,(inv.to_3x3()@-Vector(camera['direction'])).normalized())
        report['multires_depth_debug'] = {'pixel_depth':float(camera['depths'][256,256]),
            'target_depth':(origin-multi.matrix_world@hit[0]).dot(Vector(camera['direction']))/camera['clip_depth'] if hit[0] else None,
            'position':list(multi.matrix_world@hit[0]) if hit[0] else None,
            'clip_depth':camera['clip_depth'], 'min':float(camera['depths'].min()), 'max':float(camera['depths'].max())}
        check('multires_view_and_raycast_stay_live', multi_aim['hits'][0]['hit'] and
              multi_aim['hits'][0]['position'][1] < -.9 and multi_views['mesh_revision'] == grid_after_view['revision'] and
              np.allclose(grid_before_view['positions'],grid_after_view['positions'],rtol=0,atol=1e-6))
        check('multires_pixels_show_committed_sculpt_not_old_modifier_mesh',
              abs(report['multires_depth_debug']['target_depth']-report['multires_depth_debug']['pixel_depth']) < .0002)
        report['multires_views'] = multi_views['views']
        # All exposed brushes must deform real geometry, not just return FINISHED.
        for brush in ('CLAY','INFLATE','SMOOTH','CREASE','PINCH','FLATTEN'):
            state = executor.execute('angelo_inspect_sculpt', {'object':multi.name,'probes':[[3,-1,0]]})
            args.update(mesh_revision=state['mesh_revision'], brush=brush, strength=.4,
                        paths=[[{'position':state['probes'][0]['position'],'pressure':1}]])
            if brush in {'PINCH','FLATTEN'}:
                start = state['probes'][0]['position']
                args['paths'][0].append({'position':[start[0]+.15, start[1], start[2]], 'pressure':1})
            applied = executor.execute('angelo_sculpt_batch', args)
            check('native_brush_' + brush.lower(), applied['changed_points'] > 0)
        state = executor.execute('angelo_inspect_sculpt', {'object':multi.name,'probes':[[3,-1,0]]})
        args.update(mesh_revision=state['mesh_revision'], brush='MASK', strength=1,
                    paths=[[{'position':state['probes'][0]['position'],'pressure':1}]])
        masked = executor.execute('angelo_sculpt_batch', args)
        check('native_mask_brush', masked['verified']['mask_mean'] > state['mask_mean'])
        # Restore without erasing user sculpt masks automatically in real use.
        bpy.ops.ed.undo()
        multi = window.scene.objects['Multires QA']

        # Exercise the actual chat approval operator, nested sculpt Undo and the
        # same per-request checkpoint UI contract. No model/account is started.
        import addon_utils
        from angelo_agent import ui
        ui._authentication.start = lambda *a, **kw: None
        ui.refresh_connected_models = lambda *a, **kw: None
        addon_utils.enable('angelo_agent', default_set=False, persistent=False)
        controller = ui.get_controller()
        checkpoint = ui.capture_checkpoint(controller)
        controller.request_checkpoint = checkpoint['id']
        controller.state['messages'].append({'role':'user','text':'Local sculpt acceptance fixture','checkpoint':checkpoint['id']})
        controller.busy = True
        state = executor.execute('angelo_inspect_sculpt', {'object':multi.name,'probes':[[3,-1,0]]})
        args.update(mesh_revision=state['mesh_revision'], brush='DRAW', strength=.3,
                    paths=[[{'position':state['probes'][0]['position'],'pressure':1}]])
        before_approval = state['mesh_revision']
        approval_geometry = snapshot(window, multi)
        responses = []
        controller.request_tool('native-sculpt-ui', 'angelo_sculpt_batch', args, responses.append)
        controller.pump_tools(executor, ui.document_key())
        check('chat_approval_no_early_edit', not responses and len(controller.pending) == 1 and
              executor.execute('angelo_inspect_sculpt', {'object':multi.name,'probes':[]})['mesh_revision'] == before_approval)
        call = next(iter(controller.pending.values()))
        approved = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=controller.state['id'])
        check('chat_approval_runs_native_sculpt', 'FINISHED' in approved and responses[0]['success'] and responses[0]['changed_points'] > 0)
        controller.busy = False
        bpy.ops.ed.undo()
        multi = window.scene.objects['Multires QA']
        restored = executor.execute('angelo_inspect_sculpt', {'object':multi.name,'probes':[]})
        restored_geometry = snapshot(window, multi)
        report['approval_undo_difference'] = {
            'max_position_delta':float(np.max(np.abs(approval_geometry['positions']-restored_geometry['positions']))),
            'topology_unchanged':approval_geometry['topology_revision'] == restored_geometry['topology_revision'],
            'mask_mean_before':approval_geometry['mask_mean'], 'mask_mean_after':restored_geometry['mask_mean'],
            'same_exact_revision':restored['mesh_revision'] == before_approval,
            'changed_public_fields':{key:[state.get(key),restored.get(key)] for key in state if state.get(key) != restored.get(key)}}
        # An opaque exact-content token is not an Undo fidelity metric: native
        # Multires seam averaging can change a few float32 ULPs after Undo.
        # The engine-fix suite measures the same residual for separate native
        # Undo controls. Keep runtime hashes exact; test the restored surface
        # and its material metadata directly instead of requiring token reuse.
        stable_fields = ('object','vertices','faces','mask_mean','masked_points','fully_masked_points',
                         'hidden_faces','multires','mode','dyntopo','modifiers')
        check('chat_approval_one_undo',
              approval_geometry['topology_revision'] == restored_geometry['topology_revision'] and
              np.allclose(approval_geometry['world'],restored_geometry['world'],rtol=0,atol=2e-7) and
              all(state.get(key) == restored.get(key) for key in stable_fields))
        # Make a NEW real edit after the Undo test. Snapshot restoration must
        # prove restoration of changed live geometry, not just modifier levels.
        controller.busy = True
        args.update(mesh_revision=restored['mesh_revision'])
        before_checkpoint_restore = snapshot(window,multi)['positions'].copy()
        responses = []
        controller.request_tool('native-sculpt-snapshot', 'angelo_sculpt_batch', args, responses.append)
        controller.pump_tools(executor,ui.document_key())
        call = next(iter(controller.pending.values()))
        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=controller.state['id'])
        controller.busy = False
        check('snapshot_fixture_has_changed_geometry', responses[0]['success'] and
              not np.array_equal(before_checkpoint_restore,snapshot(window,multi)['positions']))
        restored_checkpoint = bpy.ops.angelo.restore_checkpoint(checkpoint_id=checkpoint['id'])
        restored_multi = window.scene.objects['Multires QA']
        check('request_snapshot_restores_sculpt', 'FINISHED' in restored_checkpoint and
              restored_multi.modifiers[0].total_levels == 1 and
              np.allclose(before_checkpoint_restore,snapshot(window,restored_multi)['positions'],rtol=0,atol=1e-6))
        addon_utils.disable('angelo_agent', default_set=False)
        report['success'] = True


def run():
    try:
        main()
    except Exception:
        report['success'] = False
        report['error'] = traceback.format_exc()
    (OUT / 'sculpt-qa.json').write_text(json.dumps(report, indent=2))
    print('ANGELO_SCULPT_QA', json.dumps(report, default=str), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
