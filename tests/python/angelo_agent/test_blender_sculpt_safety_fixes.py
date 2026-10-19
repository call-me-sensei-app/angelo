# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable native GUI regressions for sculpt budget and view safety.

Requires --factory-startup. No provider, account, or user project is accessed.
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

assert '--factory-startup' in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-sculpt-safety-fixes'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_CONNECTIONS_DIR=str(OUT/'accounts'),
                  ANGELO_STATE_DIR=str(OUT/'state'))
sys.path.insert(0, str(ROOT/'scripts/addons_core'))
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.references import ReferenceStore
from angelo_agent.sculpt import SculptPreflightError

report = {'checks':{}, 'paid_requests':0}
window = bpy.context.window_manager.windows[0]
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
executor = BlenderExecutor(window)
executor.observation_store = ReferenceStore(OUT, '116f08ad-f9ee-4dc6-9e5d-c17541321f91')


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def reset():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in window.view_layer.objects:
        obj.hide_set(False)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)


def inspect(obj):
    return executor.execute('angelo_inspect_sculpt', {'object':obj.name, 'probes':[]})


def prepare(obj, add=0):
    return executor.execute('angelo_prepare_sculpt', {'object':obj.name,
        'mesh_revision':inspect(obj)['mesh_revision'], 'add_levels':add})


def view(obj, **extra):
    return executor.execute('angelo_sculpt_view', dict(object=obj.name, views=['FRONT'], resolution=384, **extra))


def camera(observation):
    return executor.observation_store.observation_camera(observation['views'][0]['id'])


def aim(obj, observation):
    return executor.execute('angelo_sculpt_target', {'object':obj.name,
        'mesh_revision':observation['mesh_revision'], 'view_reference':observation['views'][0]['id'],
        'pixels':[[.5,.5]]})['hits'][0]


def state():
    shading = area.spaces.active.shading
    return {'active':window.view_layer.objects.active.name if window.view_layer.objects.active else None,
            'mode':bpy.context.mode, 'overlays':area.spaces.active.overlay.show_overlays,
            'clipping':area.spaces.active.region_3d.use_clip_planes,
            'shading':{key:getattr(shading,key) for key in ('type','light','color_type','show_shadows',
                'show_cavity','show_xray','show_backface_culling','show_object_outline','show_specular_highlight','use_dof')},
            'objects':{o.name:(o.hide_get(),o.select_get(),o.display_type,o.show_in_front,o.hide_viewport,o.hide_render)
                       for o in window.view_layer.objects}}


def budgets():
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    modifier = obj.modifiers.new('Existing detail', 'MULTIRES')
    for _ in range(8):
        bpy.ops.object.multires_subdivide(modifier=modifier.name, mode='CATMULL_CLARK')
    modifier.levels = 8
    modifier.sculpt_levels = 1
    bpy.ops.object.mode_set(mode='SCULPT')
    before = inspect(obj)
    check('low_working_level_fixture', before['vertices'] == 96)
    artist_state = state()
    try:
        prepare(obj, 1)
    except SculptPreflightError as error:
        check('total_destination_budget_rejected', 'one-million-point' in str(error))
    else:
        check('total_destination_budget_rejected', False)
    check('budget_rejection_has_no_mutation', modifier.total_levels == 8 and modifier.levels == 8 and
          modifier.sculpt_levels == 1 and inspect(obj)['mesh_revision'] == before['mesh_revision'] and state() == artist_state)
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    check('valid_first_subdivision', prepare(obj, 1)['verified']['vertices'] == 96)
    check('valid_second_subdivision', prepare(obj, 1)['verified']['vertices'] == 216)
    modifier = obj.modifiers[0]
    modifier.sculpt_levels = 1
    window.view_layer.update()
    try:
        prepare(obj, 1)
    except SculptPreflightError as error:
        check('existing_higher_levels_not_duplicated', 'Higher Multires levels' in str(error))
    else:
        check('existing_higher_levels_not_duplicated', False)


def depth_and_display():
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    for vertex in obj.data.vertices:
        vertex.co.y *= 10
    obj.data.update()
    window.view_layer.update()
    observation = view(obj)
    cam, hit = camera(observation), aim(obj, observation)
    check('camera_outside_deep_target', cam['camera_distance'] > 10)
    check('deep_target_visible_and_hittable', cam['silhouette'].sum() > 10000 and hit['hit'] and abs(hit['position'][1]+10) < .0001)
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    obj = bpy.context.active_object
    obj.show_in_front = True
    original = state()
    observation = view(obj)
    check('in_front_visible_surface_hittable', aim(obj, observation)['hit'])
    check('in_front_restored', state() == original)
    bpy.ops.mesh.primitive_cube_add(size=.8, location=(0,-2,0))
    blocker = bpy.context.active_object
    blocker.show_in_front = True
    window.view_layer.update()
    original = state()
    observation = view(obj)
    check('in_front_does_not_punch_through_blocker', not aim(obj, observation)['hit'])
    check('neighbor_override_restored', state() == original)
    blocker.location.y = 2
    window.view_layer.update()
    observation = view(obj)
    check('in_front_neighbor_behind_does_not_occlude', aim(obj, observation)['hit'])
    saved_camera = observation['views'][0]['id']
    blocker.location.y = 200
    window.view_layer.update()
    background = view(obj, framing_reference=saved_camera)
    check('fixed_camera_ignores_new_distant_background', aim(obj, background)['hit'])
    blocker.location.y = -20
    window.view_layer.update()
    try:
        view(obj, framing_reference=saved_camera)
    except RuntimeError as error:
        check('saved_camera_cannot_ignore_new_foreground', 'saved camera depth range' in str(error))
    else:
        check('saved_camera_cannot_ignore_new_foreground', False)
    observation = view(obj)
    check('camera_includes_distant_foreground_blocker', camera(observation)['camera_distance'] > 20 and not aim(obj, observation)['hit'])
    shading = area.spaces.active.shading
    shading.show_backface_culling = True
    shading.show_xray = True
    shading.show_object_outline = True
    shading.show_specular_highlight = True
    area.spaces.active.region_3d.use_clip_planes = True
    original = state()
    view(obj)
    check('all_artist_display_state_restored', state() == original)
    before_files = set(executor.observation_store.directory.glob('*.png'))
    with patch('angelo_agent.sculpt_workflow.silhouette', side_effect=RuntimeError('injected capture failure')):
        try:
            view(obj)
        except RuntimeError as error:
            check('capture_failure_reported', 'injected capture failure' in str(error))
        else:
            check('capture_failure_reported', False)
    check('failure_restores_artist_state', state() == original)
    check('failure_discards_incomplete_observation', before_files == set(executor.observation_store.directory.glob('*.png')))
    area.spaces.active.region_3d.use_clip_planes = False


def collection_instance():
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    obj = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=.8, location=(0,-2,0))
    source = bpy.context.active_object
    collection = bpy.data.collections.new('Unlinked source collection')
    collection.objects.link(source)
    for owner in list(source.users_collection):
        if owner != collection:
            owner.objects.unlink(source)
    source.show_in_front = True
    instance = bpy.data.objects.new('Collection instance', None)
    instance.instance_type = 'COLLECTION'
    instance.instance_collection = collection
    window.scene.collection.objects.link(instance)
    window.view_layer.objects.active = obj
    window.view_layer.update()
    check('instance_source_outside_view_layer_fixture', source.name not in window.view_layer.objects)
    observation = view(obj)
    check('in_front_collection_source_occludes_at_real_depth', not aim(obj, observation)['hit'])
    check('collection_source_override_restored', source.show_in_front)
    source.location.y = 2
    window.view_layer.update()
    observation = view(obj)
    check('in_front_collection_source_behind_does_not_occlude', aim(obj, observation)['hit'])
    check('collection_source_still_restored', source.show_in_front)


def closeup():
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    obj = bpy.context.active_object
    vertices = [list(v.co) for v in obj.data.vertices]
    faces = [list(p.vertices) for p in obj.data.polygons]
    n = len(vertices)
    vertices.extend([(99,0,0),(100,0,0),(100,0,1)])
    faces.append([n,n+1,n+2])
    mesh = bpy.data.meshes.new('Far lateral detail')
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj.data = mesh
    prepare(obj)
    executor.execute('angelo_sculpt_region', {'object':obj.name, 'mesh_revision':inspect(obj)['mesh_revision'],
        'name':'Closeup', 'position':[0,-1,0], 'radius':.1, 'face_set':0})
    observation = view(obj, region='Closeup')
    check('unoccluded_closeup_hittable', aim(obj, observation)['hit'])
    bpy.ops.object.mode_set(mode='OBJECT')
    block = bpy.data.meshes.new('Foreground')
    block.from_pydata([(-.2,-1.05,-.2),(.2,-1.05,-.2),(.2,-1.05,.2),(-.2,-1.05,.2)], [], [(0,1,2,3)])
    block.update()
    blocker = bpy.data.objects.new('Five centimeter occluder', block)
    window.scene.collection.objects.link(blocker)
    bpy.ops.object.mode_set(mode='SCULPT')
    window.view_layer.update()
    observation = view(obj, region='Closeup')
    hit = aim(obj, observation)
    check('closeup_rejects_hidden_surface', not hit['hit'] and hit.get('reason') == 'occluded')
    check('crop_depth_not_inflated_by_lateral_extent', camera(observation)['clip_depth'] < 10)
    baseline = observation['views'][0]['id']
    fixed = view(obj, framing_reference=baseline)
    check('comparison_camera_remains_fixed', all(camera(observation)[key] == camera(fixed)[key]
        for key in ('center','radius','direction','camera_distance','clip_depth')))


def far_origin_precision():
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=(100000,0,0))
    obj = bpy.context.active_object
    window.view_layer.update()
    hit = aim(obj, view(obj))
    check('large_world_coordinates_fail_closed_when_pixel_precision_is_lost',
          not hit['hit'] and hit.get('reason') == 'insufficient_depth_precision')


def run():
    try:
        (OUT/'recovery').mkdir(exist_ok=True)
        bpy.context.preferences.filepaths.temporary_directory = str(OUT/'recovery')
        with bpy.context.temp_override(window=window, area=area, region=region):
            budgets()
            depth_and_display()
            collection_instance()
            closeup()
            far_origin_precision()
        report['completed'] = True
    except Exception:
        report.update(completed=False, error=traceback.format_exc())
    (OUT/'results.json').write_text(json.dumps(report, indent=2))
    print('ANGELO_SCULPT_SAFETY_FIXES', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
