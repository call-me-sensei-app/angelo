# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native regressions for stroke originals, mixed Undo, tilt and finite input.

Run in a disposable graphical --factory-startup instance. No provider calls.
The tests compare actual live base/CCG arrays and exercise the native bpy API,
independently of host validation and result summaries.
"""
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

import bpy
from mathutils import Quaternion, Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d
import numpy as np

assert '--factory-startup' in sys.argv
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-sculpt-engine-fixes'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_DISABLE_DEFAULT_CHAT='1')
report = {'checks': {}, 'measurements': {}, 'paid_requests': 0}
_templates = {}


def check(name, value):
    report['checks'][name] = bool(value)
    (OUT / 'progress.json').write_text(json.dumps(report, indent=2))
    assert value, name


def snapshot(obj):
    live = any(m.type == 'MULTIRES' for m in obj.modifiers)
    mesh = obj.angelo_sculpt_mesh() if live else obj.data
    try:
        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', positions)
        masks = np.zeros(len(mesh.vertices), dtype=np.float32)
        attribute = mesh.attributes.get('.sculpt_mask')
        if attribute:
            attribute.data.foreach_get('value', masks)
        return positions.reshape(-1, 3), masks
    finally:
        if live:
            obj.to_mesh_clear()


def fixture(multires=False, hidden=False):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    if multires not in _templates:
        # Both variants have more than one PBVH leaf, exercising parallel
        # cross-node Pose-weight smoothing instead of only a single leaf.
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16 if multires else 96,
                                           ring_count=8 if multires else 48)
        obj = bpy.context.object
        _templates[multires] = obj.data.copy()
    else:
        # BMesh-to-Mesh primitive construction can reorder otherwise identical
        # faces between allocations. Stable base face order is essential when
        # comparing flattened CCG arrays from independent fixtures.
        obj = bpy.data.objects.new('Angelo Engine Regression', _templates[multires].copy())
        bpy.context.scene.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.context.view_layer.update()
    obj.name = 'Angelo Engine Regression'
    if hidden:
        attribute = obj.data.attributes.new('.hide_poly', 'BOOLEAN', 'FACE')
        attribute.data.foreach_set('value', np.array([p.center.z > .4 for p in obj.data.polygons],
                                                     dtype=np.bool_))
        obj.data.update()
    if multires:
        modifier = obj.modifiers.new('Engine Multires', 'MULTIRES')
        for _ in range(2):
            bpy.ops.object.multires_subdivide(modifier=modifier.name, mode='CATMULL_CLARK')
        modifier.sculpt_levels = modifier.levels = modifier.total_levels
    bpy.ops.object.mode_set(mode='SCULPT')
    bpy.ops.ed.undo_push(message='Engine regression baseline')
    return obj


def point(location, start=True, tilt=(0, 0)):
    return {'name': 'sample', 'location': list(location), 'pressure': 1.0,
            'size': 1.0, 'is_start': start, 'x_tilt': float(tilt[0]),
            'y_tilt': float(tilt[1]), 'mouse': (0, 0), 'mouse_event': (0, 0), 'time': 0.0}


def action(brush='DRAW', x=-.45, move=.07, symmetry='NONE', tilt=(0, 0)):
    # Avoid duplicated CCG seam vertices for the comparison fixture: Pose's
    # native topology seed is a nearest vertex, which is ambiguous on a seam.
    location = [x, -math.sqrt(1 - x * x - .071 ** 2), .071]
    stroke = [point(location, tilt=tilt)]
    if brush in ('GRAB', 'ELASTIC_DEFORM', 'POSE'):
        stroke.append(point([x, location[1] - move, location[2] + (.025 if move else 0)], False, tilt))
    elif brush in ('CLAY_STRIPS', 'FLATTEN'):
        stroke.append(point([x + .07, location[1], location[2] + .03], False, tilt))
    return {'brush_type': brush, 'radius': .28, 'strength': .3,
            'surface_normal': [0.0, -1.0, 0.0], 'symmetry': symmetry,
            'front_faces_only': True, 'mode': 'NORMAL', 'falloff': 'SMOOTH',
            'automasking': [], 'stroke': stroke}


def project(obj, payload):
    payload = copy.deepcopy(payload)
    anchored = payload['brush_type'] in ('GRAB', 'ELASTIC_DEFORM', 'POSE')
    for sample in payload['stroke']:
        if not anchored or sample['is_start']:
            hit, co, *_ = obj.angelo_sculpt_query(sample['location'],
                                                 distance=payload['radius'] * .5)
            assert hit, 'Fixture stroke is off surface'
            sample['location'] = list(co)
    return payload


def native_pass(obj, payload):
    payload = project(obj, payload)
    payload['automasking'] = set(payload['automasking'])
    result = bpy.ops.sculpt.angelo_sculpt_pass('EXEC_DEFAULT', True, **payload)
    assert result == {'FINISHED'}, result


def native_program(payloads):
    return bpy.ops.sculpt.angelo_sculpt_program('EXEC_DEFAULT', True,
                                               program=json.dumps(payloads, allow_nan=False))


def restored_object():
    return bpy.context.window.scene.objects['Angelo Engine Regression']


def geometry_equal(actual, expected, multires):
    # Reading CCG normals stitches duplicated seams; floating-point averaging
    # can move a restored seam by a few ulps. Base geometry remains bit-exact.
    return (np.allclose(actual, expected, atol=2e-7, rtol=0) if multires
            else np.array_equal(actual, expected))


def main():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    (OUT / 'recovery').mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'recovery')
    bpy.context.preferences.edit.use_global_undo = True
    with bpy.context.temp_override(window=window, area=area, region=region):
        for multires in (False, True):
            surface = 'MULTIRES' if multires else 'BASE'
            if os.environ.get('ANGELO_ENGINE_SURFACE', surface) != surface:
                continue
            for brush in ('GRAB', 'ELASTIC_DEFORM', 'POSE'):
                for move in (0.0, .07):
                    label = f'{surface}_{brush}_{move}'
                    steps = [action(), action(brush, .45, move)]
                    obj = fixture(multires)
                    # A zero-motion anchored stroke must duplicate the actual
                    # mesh point, not an ideal sphere point slightly off it.
                    steps[1] = project(obj, steps[1])
                    if move == 0:
                        steps[1]['stroke'][1]['location'] = steps[1]['stroke'][0]['location'][:]
                    before, _ = snapshot(obj)
                    native_pass(obj, steps[0])
                    drawn, _ = snapshot(obj)
                    native_pass(obj, steps[1])
                    expected, _ = snapshot(obj)
                    bpy.ops.ed.undo()
                    bpy.ops.ed.undo()
                    separate_undo, _ = snapshot(restored_object())
                    report['measurements'][label + '_separate_undo_difference'] = float(
                        np.max(np.abs(separate_undo - before)))
                    obj = fixture(multires)
                    baseline, baseline_mask = snapshot(obj)
                    report['measurements'][label + '_fixture_difference'] = float(np.max(np.abs(before - baseline)))
                    np.savez(OUT / (label + '.npz'), before=before, baseline=baseline,
                             drawn=drawn, expected=expected)
                    check(label + '_fixture_equivalent',
                          np.allclose(before, baseline, atol=2e-7, rtol=0))
                    native_program(steps)
                    actual, actual_mask = snapshot(obj)
                    peak = float(np.max(np.abs(actual - expected)))
                    report['measurements'][label] = {'max_difference_from_separate': peak}
                    check(label + '_matches_separate_strokes', peak < 3e-6)
                    if move == 0:
                        check(label + '_zero_move_keeps_prior_draw',
                              np.allclose(actual, drawn, atol=3e-6, rtol=0))
                    check(label + '_retains_a_real_edit', np.max(np.abs(actual - baseline)) > 1e-4)
                    bpy.ops.ed.undo()
                    obj = restored_object()
                    restored, restored_mask = snapshot(obj)
                    report['measurements'][label + '_undo_difference'] = {
                        'positions': float(np.max(np.abs(restored - baseline))),
                        'masks': float(np.max(np.abs(restored_mask - baseline_mask)))}
                    np.savez(OUT / (label + '-undo.npz'), baseline=baseline, actual=actual,
                             restored=restored, baseline_mask=baseline_mask, restored_mask=restored_mask)
                    check(label + '_one_undo_restores_program', geometry_equal(restored, baseline, multires)
                          and np.array_equal(restored_mask, baseline_mask))
                    bpy.ops.ed.redo()
                    obj = restored_object()
                    redone, redone_mask = snapshot(obj)
                    check(label + '_one_redo_restores_program', geometry_equal(redone, actual, multires)
                          and np.array_equal(redone_mask, actual_mask))

            # Local X symmetry still samples the current stroke's original coordinates.
            obj = fixture(multires)
            baseline, _ = snapshot(obj)
            steps = [action(symmetry='X'), action('GRAB', 0, .05, symmetry='X')]
            for step in steps:
                native_pass(obj, step)
            expected, _ = snapshot(obj)
            obj = fixture(multires)
            native_program(steps)
            actual, _ = snapshot(obj)
            check(surface + '_symmetric_program_matches_separate',
                  np.allclose(actual, expected, atol=3e-6, rtol=0))
            bpy.ops.ed.undo()
            check(surface + '_symmetric_program_one_undo',
                  geometry_equal(snapshot(restored_object())[0], baseline, multires))

            # Repeated separated runs are an independent control for parallel
            # blur races: identical topology/seed must produce identical Pose.
            for hidden in (False, True) if multires else (False,):
                results = []
                elapsed = []
                samples = []
                for trial in range(6):
                    obj = fixture(multires, hidden=hidden)
                    baseline, _ = snapshot(obj)
                    steps = [action(), action('POSE', .45, .07)]
                    started = time.perf_counter()
                    native_pass(obj, steps[0])
                    snapshot(obj)
                    sample = obj.angelo_sculpt_query(steps[1]['stroke'][0]['location'], distance=.2)
                    samples.append(sample)
                    native_pass(obj, steps[1])
                    after, _ = snapshot(obj)
                    elapsed.append((time.perf_counter() - started) * 1000)
                    results.append(after)
                    if hidden:
                        check(f'{surface}_hidden_pose_{trial}_protects_hidden_geometry',
                              np.allclose(after[baseline[:, 2] > .55], baseline[baseline[:, 2] > .55],
                                          atol=2e-7, rtol=0))
                label = f'{surface}_hidden_{hidden}_pose_repeats'
                peak = max(float(np.max(np.abs(result - results[0]))) for result in results[1:])
                report['measurements'][label] = {'peak_difference': peak,
                                                'two_strokes_ms': elapsed}
                check(label + '_identical_seed', all(sample == samples[0] for sample in samples[1:]))
                check(label + '_deterministic', peak < 3e-6)
                obj = fixture(multires, hidden=hidden)
                baseline, _ = snapshot(obj)
                native_program(steps)
                check(label + '_program_matches_separate',
                      np.allclose(snapshot(obj)[0], results[0], atol=3e-6, rtol=0))
                bpy.ops.ed.undo()
                check(label + '_one_program_undo',
                      geometry_equal(snapshot(restored_object())[0], baseline, multires))

            if multires:
                # Query and exported observations must canonicalize the same
                # CCG seam before choosing a nearest vertex/topology seed.
                obj = fixture(True)
                for phase in ('initial', 'after_draw'):
                    if phase == 'after_draw':
                        native_pass(obj, action())
                    for x in (-.45, 0, .45):
                        position = [x, -math.sqrt(1 - x * x), 0]
                        first_hit = obj.angelo_sculpt_query(position, distance=.2)
                        snapshot(obj)
                        second_hit = obj.angelo_sculpt_query(position, distance=.2)
                        check(f'MULTIRES_{phase}_{x}_query_snapshot_query_stable',
                              first_hit == second_hit)
                for brush in ('GRAB', 'ELASTIC_DEFORM', 'POSE'):
                    obj = fixture(True)
                    steps = [action(), action(brush, .45, .07)]
                    for step in steps:
                        for sample in step['stroke']:
                            sample['location'][2] -= .071
                        x = step['stroke'][0]['location'][0]
                        correction = -math.sqrt(1 - x * x) - step['stroke'][0]['location'][1]
                        for sample in step['stroke']:
                            sample['location'][1] += correction
                    steps = [project(obj, step) for step in steps]
                    for step in steps:
                        native_pass(obj, step)
                        snapshot(obj)
                    expected, _ = snapshot(obj)
                    obj = fixture(True)
                    native_program(steps)
                    actual, _ = snapshot(obj)
                    peak = float(np.max(np.abs(actual - expected)))
                    report['measurements'][f'MULTIRES_{brush}_seam_program_difference'] = peak
                    check(f'MULTIRES_{brush}_seam_program_matches_separate', peak < 3e-6)

            # Mask/position stores are independent, regardless of brush order.
            for order in (('MASK', 'DRAW'), ('DRAW', 'MASK'), ('MASK', 'DRAW', 'MASK'),
                          ('DRAW', 'MASK', 'GRAB'), ('MASK', 'MASK')):
                label = surface + '_' + '_'.join(order)
                steps = [action(brush, -.35 if index == 0 else .35,
                                0 if brush == 'GRAB' else .07)
                         for index, brush in enumerate(order)]
                obj = fixture(multires)
                for step in steps:
                    native_pass(obj, step)
                expected, expected_masks = snapshot(obj)
                obj = fixture(multires)
                baseline, baseline_masks = snapshot(obj)
                native_program(steps)
                actual, actual_masks = snapshot(obj)
                check(label + '_matches_positions', np.allclose(actual, expected, atol=3e-6, rtol=0))
                check(label + '_matches_masks', np.allclose(actual_masks, expected_masks, atol=3e-6, rtol=0)
                      and np.max(actual_masks) > .01)
                bpy.ops.ed.undo()
                obj = restored_object()
                restored, restored_masks = snapshot(obj)
                check(label + '_one_undo_restores_program', geometry_equal(restored, baseline, multires)
                      and np.array_equal(restored_masks, baseline_masks))
                bpy.ops.ed.redo()
                redone, redone_masks = snapshot(restored_object())
                check(label + '_one_redo_restores_program', geometry_equal(redone, actual, multires)
                      and np.array_equal(redone_masks, actual_masks))

            # A second path gets a fresh original even inside a single native pass.
            for brush in ('ELASTIC_DEFORM', 'POSE'):
                obj = fixture(multires)
                baseline, baseline_masks = snapshot(obj)
                first, second = action(brush), action(brush, .45, 0)
                native_pass(obj, first)
                expected, _ = snapshot(obj)
                second = project(obj, second)
                second['stroke'][1]['location'] = second['stroke'][0]['location'][:]
                obj = fixture(multires)
                multi_path = project(obj, first)
                multi_path['stroke'].extend(second['stroke'])
                multi_path['automasking'] = set()
                bpy.ops.sculpt.angelo_sculpt_pass('EXEC_DEFAULT', True, **multi_path)
                check(f'{surface}_{brush}_second_path_keeps_first',
                      np.allclose(snapshot(obj)[0], expected, atol=3e-6, rtol=0))
                bpy.ops.ed.undo()
                check(f'{surface}_{brush}_multi_path_one_undo',
                      geometry_equal(snapshot(restored_object())[0], baseline, multires))

            # A later path failure must preserve already-applied mask/geometry and one Undo.
            obj = fixture(multires)
            baseline, baseline_masks = snapshot(obj)
            bad = action()
            bad['stroke'][0]['location'] = [100.0, 100.0, 100.0]
            try:
                native_program([action('MASK', -.35), action('DRAW', .35), bad])
            except RuntimeError as error:
                check(surface + '_late_failure_reported', 'partial changes' in str(error))
            else:
                check(surface + '_late_failure_reported', False)
            partial, partial_masks = snapshot(obj)
            check(surface + '_late_failure_retains_changes',
                  not np.array_equal(partial, baseline) and np.max(partial_masks) > .01)
            bpy.ops.ed.undo()
            restored, restored_masks = snapshot(restored_object())
            report['measurements'][surface + '_late_failure_undo'] = {
                'positions_equal': geometry_equal(restored, baseline, multires),
                'masks_equal': bool(np.array_equal(restored_masks, baseline_masks)),
                'position_max_difference': float(np.max(np.abs(restored - baseline))),
                'mask_max_difference': float(np.max(np.abs(restored_masks - baseline_masks))),
            }
            check(surface + '_late_failure_one_undo_restores_program', geometry_equal(restored, baseline, multires)
                  and np.array_equal(restored_masks, baseline_masks))

            # Native API rejects malformed properties before touching Undo or the artist brush.
            for field, value in (('strength', float('nan')), ('radius', float('nan')),
                                 ('surface_normal', [0, float('nan'), 0])):
                obj = fixture(multires)
                baseline, _ = snapshot(obj)
                native_pass(obj, action())
                before, _ = snapshot(obj)
                old_brush = bpy.context.tool_settings.sculpt.brush
                invalid = project(obj, action(x=.45))
                invalid['automasking'] = set()
                invalid[field] = value
                try:
                    bpy.ops.sculpt.angelo_sculpt_pass('EXEC_DEFAULT', True, **invalid)
                except RuntimeError:
                    rejected = True
                else:
                    rejected = False
                check(f'{surface}_{field}_nan_rejected', rejected
                      and geometry_equal(snapshot(obj)[0], before, multires)
                      and bpy.context.tool_settings.sculpt.brush == old_brush)
                bpy.ops.ed.undo()
                check(f'{surface}_{field}_invalid_does_not_add_undo',
                      geometry_equal(snapshot(restored_object())[0], baseline, multires))

            obj = fixture(multires)
            baseline, _ = snapshot(obj)
            native_pass(obj, action())
            before, _ = snapshot(obj)
            invalid = action(x=.45)
            invalid['surface_normal'] = [0.0, 0.0, 0.0]
            try:
                native_program([action(), invalid])
            except RuntimeError:
                rejected = True
            else:
                rejected = False
            check(surface + '_program_preflights_all_passes', rejected
                  and geometry_equal(snapshot(obj)[0], before, multires))
            bpy.ops.ed.undo()
            check(surface + '_invalid_program_does_not_add_undo',
                  geometry_equal(snapshot(restored_object())[0], baseline, multires))

            # Identical tilt controls have identical results for unrelated viewport rolls.
            rv3d = area.spaces.active.region_3d
            initial_rotation = rv3d.view_rotation.copy()
            for brush in ('DRAW', 'CLAY_STRIPS', 'FLATTEN'):
                outputs = []
                for roll in (0, math.pi / 2):
                    obj = fixture(multires)
                    rv3d.view_rotation = initial_rotation @ Quaternion((0, 0, 1), roll)
                    rv3d.update()
                    before_rotation = rv3d.view_rotation.copy()
                    payload = action(brush, -.1, tilt=(.8, -.3))
                    payload['radius'] = .38
                    native_pass(obj, payload)
                    outputs.append(snapshot(obj)[0])
                    check(f'{surface}_{brush}_{roll}_artist_view_preserved',
                          before_rotation == rv3d.view_rotation)
                peak = float(np.max(np.abs(outputs[0] - outputs[1])))
                report['measurements'][f'{surface}_{brush}_tilt_view_difference'] = peak
                check(f'{surface}_{brush}_tilt_independent_of_viewport', peak < 3e-6)
            rv3d.view_rotation = initial_rotation
            rv3d.update()

            # Native nearest-hit pruning must retain the front-most actual surface.
            obj = fixture(multires)
            for z in (-.3, 0, .3):
                hit, co, *_ = obj.angelo_sculpt_query([0, -3, z], direction=[0, 1, 0], distance=10)
                check(f'{surface}_ray_front_{z}', hit and co[1] < -.8)
                hit, *_ = obj.angelo_sculpt_query([0, -3, z], direction=[0, 1, 0], distance=1)
                check(f'{surface}_ray_max_distance_{z}', not hit)

            # The ordinary artist operator keeps its original view-driven path,
            # active brush and independent Undo after a synthetic program.
            rv3d.view_rotation = Quaternion((1, 0, 0), math.pi / 2)
            rv3d.view_location = (0, 0, 0)
            rv3d.view_distance = 4
            rv3d.view_perspective = 'ORTHO'
            rv3d.update()
            obj = fixture(multires)
            brush = bpy.context.tool_settings.sculpt.brush
            check(surface + '_artist_brush_available', brush is not None)
            native_program([action(), action('GRAB', .45, 0)])
            bpy.ops.ed.undo()
            obj = restored_object()
            baseline, _ = snapshot(obj)
            mouse = location_3d_to_region_2d(region, rv3d, Vector((0, -1, 0)))
            sample = point((0, -1, 0))
            sample['mouse'] = sample['mouse_event'] = list(mouse)
            bpy.ops.sculpt.brush_stroke('EXEC_DEFAULT', True, stroke=[sample], mode='NORMAL')
            artist_result, _ = snapshot(obj)
            check(surface + '_ordinary_artist_stroke_still_deforms',
                  np.max(np.abs(artist_result - baseline)) > 1e-6
                  and bpy.context.tool_settings.sculpt.brush == brush)
            bpy.ops.ed.undo()
            check(surface + '_ordinary_artist_stroke_one_undo',
                  geometry_equal(snapshot(restored_object())[0], baseline, multires))
            rv3d.view_rotation = initial_rotation
            rv3d.update()
        report['success'] = True


def run():
    try:
        main()
    except BaseException:
        report.update(success=False, error=traceback.format_exc())
    (OUT / 'sculpt-engine-fixes.json').write_text(json.dumps(report, indent=2))
    print('ANGELO_SCULPT_ENGINE_FIXES', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
