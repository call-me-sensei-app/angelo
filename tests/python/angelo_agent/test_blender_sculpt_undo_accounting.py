# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real encoded-byte accounting, pruning, destination selection and exact Undo.

Run rebuilt Angelo graphically with --factory-startup --disable-autoexec
--python-exit-code 1 --log undo,undo.sculpt --log-level debug --python this_file.
Set ANGELO_QA_DIR and isolated config/accounts/state before launch.

The oversized Multires control deliberately has no eligible Undo predecessor;
its operator returns FINISHED but must not change the applied state. Positive
restoration uses base-mesh arrays and is bit-exact. Reading dirty CCG normals
averages seam coordinates AND masks, so post-Undo CCG reads do not promise exact
mask bits or a fixed absolute position tolerance after repeated full-surface
strokes. Existing Multires stress assertions are not changed by this fixture.
Completion timings include compression joins/initialization, not a throughput
benchmark or isolated compression cost. No provider or user document is used.
The native limit retains its boundary step and a protected memfile; this is
pruning validation, not a promise that resident memory stays below the quota.
"""

from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys
import tempfile
import time
import traceback

import bmesh
import bpy
import numpy as np
from mathutils import Vector

assert '--factory-startup' in sys.argv and not bpy.app.background
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-sculpt-undo-accounting')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_STATE_DIR=str(OUT / 'state'))
REPORT = {'checks': {}, 'measurements': {}, 'paid_requests': 0,
          'scope': 'Native encoded-byte accounting; oversized-budget negative control; bit-exact base-mesh Undo',
          'ccg_scope': 'Dirty normal reads average seam positions and masks; no exact CCG restoration claim'}
MIB = 1024 * 1024


def check(name, value):
    REPORT['checks'][name] = bool(value)
    assert value, name


@contextmanager
def native_output(path):
    """Capture installed C APIs/logs on both descriptors, restoring them even on failure."""
    sys.stdout.flush()
    sys.stderr.flush()
    libc = ctypes.CDLL("ucrtbase") if os.name == "nt" else ctypes.CDLL(None)   # Windows: the shared C runtime
    libc.fflush.argtypes = [ctypes.c_void_p]
    libc.fflush.restype = ctypes.c_int
    libc.fflush(None)
    saved = [os.dup(1), os.dup(2)]
    try:
        with path.open('w') as stream:
            os.dup2(stream.fileno(), 1)
            os.dup2(stream.fileno(), 2)
            try:
                yield
            finally:
                libc.fflush(None)
    finally:
        for descriptor, original in zip((1, 2), saved):
            os.dup2(original, descriptor)
            os.close(original)


def snapshot(obj, multires):
    mesh = obj.angelo_sculpt_mesh() if multires else obj.data
    try:
        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', positions)
        masks = np.zeros(len(mesh.vertices), dtype=np.float32)
        attribute = mesh.attributes.get('.sculpt_mask')
        if attribute:
            attribute.data.foreach_get('value', masks)
        return positions, masks
    finally:
        if multires:
            obj.to_mesh_clear()


def history(label):
    path = OUT / (label + '-undo-history.txt')
    with native_output(path):
        bpy.context.window_manager.print_undo_steps()
    entries = []
    for line in path.read_text().splitlines():
        match = re.match(r"^\[(.{4})\]\s+(\d+)\s+\{([^}]+)\}\s+type='([^']+)', name='(.*)'", line)
        if match:
            flags, index, pointer, kind, name = match.groups()
            entries.append({'index': int(index), 'pointer': pointer, 'type': kind, 'name': name,
                            'active': flags[0] == '*', 'applied': flags[1] == '#', 'skip': flags[3] == 'S'})
    assert entries, 'Native Undo history must be captured, not inferred'
    return entries


def visible_predecessors(entries):
    active = next(entry for entry in entries if entry['active'])
    return [entry for entry in entries if entry['index'] < active['index'] and not entry['skip']]


def program_count(entries):
    return sum(entry['type'] == 'Sculpt' and entry['name'] == 'Angelo Sculpt Program' for entry in entries)


def program(obj, label):
    hit, co, *_ = obj.angelo_sculpt_query([0., -1., .07], distance=3.)
    assert hit, 'The native fixture requires a live surface anchor'
    point = {'name': 'accounting sample', 'location': list(co), 'pressure': 1., 'size': 1.,
             'is_start': True, 'x_tilt': 0., 'y_tilt': 0., 'mouse': [0., 0.],
             'mouse_event': [0., 0.], 'time': 0.}
    common = {'radius': 5., 'surface_normal': [0., -1., 0.], 'symmetry': 'NONE',
              'front_faces_only': False, 'mode': 'NORMAL', 'falloff': 'SMOOTH',
              'automasking': [], 'stroke': [point]}
    steps = [dict(common, brush_type='MASK', strength=.01),
             dict(common, brush_type='DRAW', strength=.12)]
    path = OUT / (label + '-native.log')
    with native_output(path):
        started = time.perf_counter()
        result = bpy.ops.sculpt.angelo_sculpt_program('EXEC_DEFAULT', True,
                                                    program=json.dumps(steps, allow_nan=False))
        elapsed = (time.perf_counter() - started) * 1000
    assert result == {'FINISHED'}, result
    sizes = re.findall(r'Encoded sculpt undo storage: (\d+) bytes', path.read_text())
    assert len(sizes) == 1 and int(sizes[0]) > 0, 'Enable --log undo,undo.sculpt --log-level debug'
    return {'operator_completion_ms': elapsed, 'encoded_bytes': int(sizes[0]), 'log': str(path)}


def fixture(multires):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    if multires:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
        obj = bpy.context.object
        rng = random.Random(3819)
        for vertex in obj.data.vertices:
            vertex.co += Vector(tuple(rng.uniform(-.003, .003) for _ in range(3)))
        obj.data.update()
        modifier = obj.modifiers.new('Accounting Multires', 'MULTIRES')
        for _ in range(3):
            bpy.ops.object.multires_subdivide(modifier=modifier.name, mode='CATMULL_CLARK')
        modifier.sculpt_levels = modifier.levels = modifier.total_levels
    else:
        mesh = bpy.data.meshes.new('Accounting base grid')
        bm = bmesh.new()
        try:
            bmesh.ops.create_grid(bm, x_segments=256, y_segments=256, size=1)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        obj = bpy.data.objects.new('Accounting base grid', mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        values = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', values)
        values += np.random.default_rng(3819).uniform(-.003, .003, values.size).astype(np.float32)
        mesh.vertices.foreach_set('co', values)
        mesh.update()
    obj.name = 'Sculpt Undo Accounting Fixture'
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='SCULPT')
    bpy.ops.ed.undo_push(message='Sculpt accounting baseline')
    return obj


def case(multires):
    prefix = 'oversized' if multires else 'base'
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    recovery = OUT / 'recovery'
    recovery.mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(recovery)
    preferences = bpy.context.preferences.edit
    preferences.use_global_undo = True
    preferences.undo_steps = 64
    preferences.undo_memory_limit = 0
    with bpy.context.temp_override(window=window, area=area, region=region):
        obj = fixture(multires)
        initial, _ = snapshot(obj, multires)
        check(prefix + '_bounded_native_fixture', 50000 < initial.size // 3 < 250000)
        samples = [program(obj, prefix + '-unlimited-%02d' % index) for index in range(8)]
        unlimited = history(prefix + '-unlimited')
        check(prefix + '_unlimited_retains_eight_real_programs', program_count(unlimited) == 8)
        before, before_masks = snapshot(obj, multires)
        # The positive budget must retain a usable predecessor, not only a skipped memfile.
        limit_mib = 1 if multires else math.ceil(2.25 * max(row['encoded_bytes'] for row in samples) / MIB)
        preferences.undo_memory_limit = limit_mib
        limited_sample = program(obj, prefix + '-limited')
        limited = history(prefix + '-limited')
        applied, applied_masks = snapshot(obj, multires)
        measurement = {'vertices': initial.size // 3, 'memory_limit_mib': limit_mib,
                       'unlimited_programs': samples, 'limited_program': limited_sample,
                       'unlimited_history': unlimited, 'limited_history': limited,
                       'completion_median_ms': statistics.median(row['operator_completion_ms'] for row in samples),
                       'completion_max_ms': max(row['operator_completion_ms'] for row in samples)}
        REPORT['measurements'][prefix] = measurement
        retained_count = program_count(limited)
        retained_bytes = sum(row['encoded_bytes'] for row in (samples + [limited_sample])[-retained_count:])
        pruning_log = Path(limited_sample['log']).read_text()
        pruning_counts = re.findall(r'At step \d+: data_size_all=(\d+) >= memory_limit=(\d+)', pruning_log)
        measurement['retained_encoded_bytes'] = retained_bytes
        measurement['pruner_accounting'] = pruning_counts
        check(prefix + '_pruner_uses_exact_encoded_byte_sum',
              pruning_counts == [(str(retained_bytes), str(limit_mib * MIB))])
        check(prefix + '_actual_position_and_mask_changes',
              np.max(np.abs(applied - before)) > 1e-4 and not np.array_equal(applied_masks, before_masks))
        if multires:
            check('oversized_step_exceeds_budget', limited_sample['encoded_bytes'] > limit_mib * MIB)
            check('oversized_pruning_leaves_only_one_sculpt_program', program_count(limited) == 1)
            check('oversized_history_has_no_eligible_predecessor', not visible_predecessors(limited))
        else:
            check('base_budget_prunes_real_programs', 1 < program_count(limited) < program_count(unlimited))
            check('base_history_retains_visible_sculpt_predecessor',
                  any(entry['type'] == 'Sculpt' for entry in visible_predecessors(limited)))
        with native_output(OUT / (prefix + '-undo-native.log')):
            measurement['undo_operator_result'] = sorted(bpy.ops.ed.undo())
        after_undo = history(prefix + '-after-undo')
        measurement['after_undo_history'] = after_undo
        obj = window.scene.objects['Sculpt Undo Accounting Fixture']
        restored, restored_masks = snapshot(obj, multires)
        if multires:
            check('oversized_no_destination_leaves_positions_unchanged', np.array_equal(restored, applied))
            check('oversized_no_destination_leaves_masks_unchanged', np.array_equal(restored_masks, applied_masks))
            check('oversized_no_destination_leaves_history_unchanged', after_undo == limited)
            return
        check('base_one_undo_restores_positions_bit_exact', np.array_equal(restored, before))
        check('base_one_undo_restores_masks_bit_exact', np.array_equal(restored_masks, before_masks))
        with native_output(OUT / 'base-redo-native.log'):
            measurement['redo_operator_result'] = sorted(bpy.ops.ed.redo())
        measurement['after_redo_history'] = history('base-after-redo')
        obj = window.scene.objects['Sculpt Undo Accounting Fixture']
        redone, redone_masks = snapshot(obj, False)
        check('base_one_redo_restores_positions_bit_exact', np.array_equal(redone, applied))
        check('base_one_redo_restores_masks_bit_exact', np.array_equal(redone_masks, applied_masks))


def run():
    try:
        case(True)
        # Separate actual documents/Undo stacks; never mix negative-control history into the positive case.
        bpy.ops.wm.read_factory_settings(use_empty=False)
        case(False)
        REPORT['success'] = True
    except BaseException:
        REPORT.update(success=False, error=traceback.format_exc())
    (OUT / 'sculpt-undo-accounting.json').write_text(json.dumps(REPORT, indent=2))
    print('ANGELO_SCULPT_UNDO_ACCOUNTING', json.dumps(REPORT), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
