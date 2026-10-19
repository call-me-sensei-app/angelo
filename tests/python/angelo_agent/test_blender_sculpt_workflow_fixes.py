# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native landmark placement, reproducible silhouettes and real reference progress."""
import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
import gpu
import numpy as np
from mathutils import Matrix, Vector

assert '--factory-startup' in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-sculpt-workflow-fixes'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'), ANGELO_STATE_DIR=str(OUT / 'state'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1')
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.references import ReferenceStore
from angelo_agent import sculpt, sculpt_workflow as workflow

report = {'checks': {}, 'paid_requests': 0}


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def main():
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    (OUT / 'recovery').mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'recovery')
    bpy.context.preferences.edit.use_global_undo = True
    with bpy.context.temp_override(window=window, area=area, region=region):
        executor = BlenderExecutor(window)
        executor.observation_store = ReferenceStore(OUT, '46689796-edca-49ca-a964-a3a6fe116482')

        def reset():
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete(use_global=False)

        def inspect(obj):
            return executor.execute('angelo_inspect_sculpt', {'object': obj.name, 'probes': []})

        def prepare(obj):
            return executor.execute('angelo_prepare_sculpt', {'object': obj.name,
                                    'mesh_revision': inspect(obj)['mesh_revision'], 'add_levels': 0})

        def define(obj, name, position, radius=.1):
            return executor.execute('angelo_sculpt_region', {'object': obj.name,
                                    'mesh_revision': inspect(obj)['mesh_revision'], 'name': name,
                                    'position': position, 'radius': radius, 'face_set': 0})

        def view(obj, **extra):
            return executor.execute('angelo_sculpt_view', dict(object=obj.name, views=['FRONT'],
                                    resolution=384, **extra))

        reset()
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj.name = 'Coarse landmark regression'
        prepare(obj)
        accepted = define(obj, 'Corner', [1, -1, -1])
        check('vertex_aligned_landmark_is_accepted', accepted.get('success') and
              math.dist(accepted['region']['position'], [1, -1, -1]) < 1e-7)
        before = sculpt.snapshot(window, obj)
        before_metadata = obj[workflow.REGIONS_KEY]
        try:
            define(obj, 'Face center', [0, -1, 0])
        except sculpt.SculptPreflightError as error:
            report['coarse_landmark_rejection'] = str(error)
            check('coarse_face_center_rejected_with_measured_density_guidance',
                  '1.41421' in str(error) and '0.05' in str(error) and 'working resolution' in str(error))
        else:
            check('coarse_face_center_rejected_with_measured_density_guidance', False)
        check('rejected_landmark_preserves_metadata_and_geometry', obj[workflow.REGIONS_KEY] == before_metadata and
              sculpt.snapshot(window, obj)['revision'] == before['revision'])
        accepted = define(obj, 'Near corner', [1, -1, -.98])
        check('accepted_anchor_stays_inside_requested_region', accepted.get('success') and
              math.dist(accepted['region']['position'], [1, -1, -.98]) <= .05)

        valid_metadata = obj[workflow.REGIONS_KEY]
        valid = workflow.regions(obj)['Corner']
        bad_records = [None, [], 'legacy', {'vertex': 0, 'topology': before['topology_revision'],
                       'radius': .1, 'face_set': 0}, dict(valid, vertex=-1),
                       dict(valid, requested_world=[math.nan, 0, 0]), dict(valid, anchor_version=True),
                       dict(valid, accepted_world=[0, -1, 0])]
        rejected = []
        for record in bad_records:
            obj[workflow.REGIONS_KEY] = json.dumps({'Invalid': record})
            state = sculpt.snapshot(window, obj)
            try:
                workflow.resolve_region(obj, state, 'Invalid')
            except sculpt.SculptPreflightError:
                rejected.append(True)
            else:
                rejected.append(False)
            listed = workflow.list_regions(obj, state)
            rejected.append(len(listed) == 1 and listed[0].get('stale') is True)
        obj[workflow.REGIONS_KEY] = valid_metadata
        check('legacy_and_corrupt_regions_reject_without_native_mutation', all(rejected) and
              np.array_equal(before['positions'], sculpt.snapshot(window, obj)['positions']))

        # An unchanged reversed plane stays the same target-only silhouette when
        # the artist changes viewport culling and other rendering preferences.
        reset()
        mesh = bpy.data.meshes.new('Back-facing plane')
        mesh.from_pydata([(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)], [], [(0, 3, 2, 1)])
        mesh.update()
        obj = bpy.data.objects.new('Back-facing plane', mesh)
        window.scene.collection.objects.link(obj)
        window.view_layer.objects.active = obj
        obj.select_set(True)
        if mesh.polygons[0].normal.y < 0:
            mesh.flip_normals()
            mesh.update()
        window.view_layer.update()
        area.spaces.active.shading.show_backface_culling = False
        baseline = view(obj)
        camera = executor.observation_store.observation_camera(baseline['views'][0]['id'])
        check('back_facing_plane_has_visible_silhouette', int(camera['silhouette'].sum()) > 10000)
        area.spaces.active.shading.show_backface_culling = True
        reviewed = executor.execute('angelo_sculpt_review', {'object': obj.name,
                                    'baseline': baseline['views'][0]['id'], 'outline': [], 'landmarks': []})
        report['culling_review'] = reviewed['review']
        check('unchanged_backface_culling_does_not_report_shape_change',
              reviewed['mesh_revision'] == baseline['mesh_revision'] and reviewed['review']['baseline_iou'] == 1.0)
        check('artist_backface_preference_is_restored', area.spaces.active.shading.show_backface_culling)

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        neighbor = bpy.context.active_object
        neighbor.name = 'Selected neighbor'
        obj.select_set(False)
        bpy.ops.mesh.primitive_cube_add(location=(-3, 0, 0))
        hidden = bpy.context.active_object
        hidden.name = 'Hidden neighbor'
        hidden.hide_set(True)
        window.view_layer.objects.active = neighbor
        neighbor.select_set(True)
        space = area.spaces.active
        shading = space.shading
        shading.type = 'WIREFRAME'
        shading.light = 'MATCAP'
        shading.color_type = 'RANDOM'
        shading.single_color = (.13, .47, .9)
        shading.background_type = 'WORLD'
        shading.background_color = (.4, .2, .1)
        shading.show_cavity = shading.show_shadows = shading.show_xray = True
        shading.show_backface_culling = shading.show_object_outline = shading.show_specular_highlight = True
        shading.use_dof = True
        space.overlay.show_overlays = True
        space.region_3d.use_clip_planes = True
        obj.display_type = 'WIRE'
        obj.show_in_front = True
        window.view_layer.update()

        def artist_state():
            fields = ('type', 'light', 'color_type', 'background_type', 'show_cavity', 'show_shadows',
                      'show_backface_culling', 'show_xray', 'show_object_outline', 'show_specular_highlight', 'use_dof')
            result = {key: getattr(shading, key) for key in fields}
            result.update(single_color=tuple(shading.single_color), background_color=tuple(shading.background_color),
                          overlays=space.overlay.show_overlays, clipping=space.region_3d.use_clip_planes,
                          active=window.view_layer.objects.active.name, mode=bpy.context.mode,
                          view=tuple(value for row in space.region_3d.view_matrix for value in row),
                          objects={o.name: (o.hide_get(view_layer=window.view_layer),
                                            o.select_get(view_layer=window.view_layer), o.display_type,
                                            o.show_in_front) for o in window.view_layer.objects})
            return result

        rotation = Vector(camera['direction']).to_track_quat('Z', 'Y').to_matrix().to_4x4()
        view_matrix = (Matrix.Translation(Vector(camera['center']) + Vector(camera['direction']) *
                       camera['camera_distance']) @ rotation).inverted()
        projection = Matrix(((1 / camera['radius'], 0, 0, 0), (0, 1 / camera['radius'], 0, 0),
                             (0, 0, -2 / camera['clip_depth'], -1), (0, 0, 0, 1)))
        original = artist_state()
        offscreen = gpu.types.GPUOffScreen(384, 384)
        try:
            mask = workflow.silhouette(window, area, region, obj, offscreen, view_matrix, projection, 384)
        finally:
            offscreen.free()
        check('independent_silhouette_is_deterministic_with_artist_display_overrides',
              np.array_equal(mask, camera['silhouette']))
        check('independent_silhouette_restores_every_changed_state', artist_state() == original)

        class FailingOffscreen:
            def draw_view3d(self, *args, **kwargs):
                check('failure_injection_observes_normalized_silhouette_settings',
                      shading.type == 'SOLID' and shading.light == 'FLAT' and shading.color_type == 'SINGLE' and
                      not shading.show_backface_culling and not shading.show_xray and not obj.show_in_front and
                      not space.region_3d.use_clip_planes and not space.overlay.show_overlays and neighbor.hide_get())
                raise RuntimeError('Injected target-only GPU draw failure')
        try:
            workflow.silhouette(window, area, region, obj, FailingOffscreen(), view_matrix, projection, 384)
        except RuntimeError as error:
            check('silhouette_draw_failure_propagates', 'Injected' in str(error))
        else:
            check('silhouette_draw_failure_propagates', False)
        check('silhouette_failure_restores_artist_visibility_selection_and_preferences', artist_state() == original)
        space.region_3d.use_clip_planes = False

        # A real geometry edit should improve an explicitly supplied goal while
        # the baseline camera stays fixed. Constant-falloff Grab translates this
        # complete sphere, making both silhouette and landmark goals measurable.
        reset()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48)
        obj = bpy.context.active_object
        obj.name = 'Explicit reference progress'
        prepare(obj)
        defined = define(obj, 'Front landmark', [0, -1, 0], .2)
        check('dense_landmark_admitted_near_requested_surface', defined.get('success'))
        baseline = view(obj)
        camera = executor.observation_store.observation_camera(baseline['views'][0]['id'])
        delta = .1
        goal_center = [.5 + delta / (2 * camera['radius']), .5]
        goal = [[goal_center[0] + math.cos(i * math.tau / 128) / (2 * camera['radius']),
                 goal_center[1] + math.sin(i * math.tau / 128) / (2 * camera['radius'])] for i in range(128)]
        before = sculpt.snapshot(window, obj)['positions'].copy()
        bpy.ops.ed.undo_push(message='Reference progress baseline')
        edited = executor.execute('angelo_sculpt_batch', {'object': obj.name,
                                  'mesh_revision': inspect(obj)['mesh_revision'], 'brush': 'GRAB',
                                  'radius': 2.3, 'strength': 1, 'direction': 'ADD', 'symmetry': 'NONE',
                                  'normal': [0, -1, 0], 'front_faces_only': False, 'spacing': .2,
                                  'falloff': 'CONSTANT', 'automasking': [],
                                  'paths': [[{'position': [0, -1, 0], 'pressure': 1},
                                             {'position': [delta, -1, 0], 'pressure': 1}]]})
        check('reference_fixture_actually_changes_native_geometry', edited.get('success') and edited['changed_points'] > 0)
        reviewed = executor.execute('angelo_sculpt_review', {'object': obj.name,
                                    'baseline': baseline['views'][0]['id'], 'outline': goal,
                                    'landmarks': [{'region': 'Front landmark', 'pixel': goal_center}]})
        metrics = reviewed['review']
        report['reference_progress'] = metrics
        check('actual_geometry_improves_explicit_outline_goal', metrics['reference_iou_delta'] > .05 and
              metrics['reference_iou_after'] > .95)
        check('actual_geometry_improves_landmark_goal', metrics['landmarks'][0]['error_reduction_pixels'] > 10 and
              metrics['landmarks'][0]['error_pixels'] < 1)
        observed = executor.observation_store.observation_camera(reviewed['views'][0]['id'])
        check('geometry_comparison_preserves_exact_camera', all(camera[key] == observed[key]
              for key in ('center', 'radius', 'direction', 'camera_distance', 'clip_depth')))
        bpy.ops.ed.undo()
        obj = window.scene.objects['Explicit reference progress']
        check('reference_geometry_edit_has_one_undo', np.array_equal(before, sculpt.snapshot(window, obj)['positions']))

        # Persistent landmark admission still participates in real UI operator
        # Undo. Only local host execution occurs; no provider request is made.
        import addon_utils
        from angelo_agent import ui
        ui._authentication.start = lambda *args, **kwargs: None
        ui.refresh_connected_models = lambda *args, **kwargs: None
        addon_utils.enable('angelo_agent', default_set=False, persistent=False)
        try:
            controller = ui.get_controller()
            controller.busy = True
            old_metadata = obj[workflow.REGIONS_KEY]
            bpy.ops.ed.undo_push(message='Validated metadata approval baseline')
            answers = []
            args = {'object': obj.name, 'mesh_revision': inspect(obj)['mesh_revision'],
                    'name': 'Approved landmark', 'position': [0, -1, 0], 'radius': .2, 'face_set': 0}
            controller.request_tool('validated-region-undo', 'angelo_sculpt_region', args, answers.append)
            controller.pump_tools(executor, ui.document_key())
            call = next(iter(controller.pending.values()))
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=controller.state['id'])
            controller.busy = False
            check('validated_region_metadata_created_through_chat_approval', answers and answers[0].get('success') and
                  workflow.regions(obj)['Approved landmark']['anchor_version'] == workflow.REGION_ANCHOR_VERSION)
            bpy.ops.ed.undo()
            obj = window.scene.objects['Explicit reference progress']
            check('validated_region_metadata_restores_with_one_undo', obj[workflow.REGIONS_KEY] == old_metadata)
        finally:
            addon_utils.disable('angelo_agent', default_set=False)
        report['success'] = True


def run():
    try:
        main()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    (OUT / 'sculpt-workflow-fixes.json').write_text(json.dumps(report, indent=2))
    print('ANGELO_SCULPT_WORKFLOW_FIXES', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
