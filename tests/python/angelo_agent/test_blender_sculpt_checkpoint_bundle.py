# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full Sculpt checkpoint capture, production Restore and continued ordinary editing.

One factory GUI process per ANGELO_SCULPT_CHECKPOINT_VARIANT: shared (default),
dyntopo, corrupt, post-failure. No provider; no manual checkpoint factory or
snapshot installation. Actual native proof requires the integrated build.
"""
import array
import contextlib
import ctypes
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import traceback

import addon_utils
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Quaternion, Vector

assert '--factory-startup' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts/addons_core'), str(Path(__file__).resolve().parent)]
OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'),
                  ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_KEYCHAIN='1')


def require_private_paths(environment, OUT):
    for key in ('ANGELO_STATE_DIR', 'ANGELO_CONNECTIONS_DIR', 'BLENDER_USER_RESOURCES',
                'BLENDER_USER_CONFIG', 'BLENDER_USER_DATAFILES', 'BLENDER_USER_SCRIPTS',
                'BLENDER_USER_EXTENSIONS'):
        assert Path(environment[key]).resolve().is_relative_to(OUT), key + ' must be inside QA'


require_private_paths(os.environ, OUT)
from angelo_agent import ui, program_undo, checkpoint_capture
from qa_waits import IdleWait

# The existing isolated GUI pattern disables authentication/model discovery,
# not checkpoint capture, its native owner, or Restore's safety gate.
ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
(OUT / 'runtime-temp').mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'runtime-temp')
VARIANT = os.environ.get('ANGELO_SCULPT_CHECKPOINT_VARIANT', 'shared')
assert VARIANT in ('shared', 'dyntopo', 'corrupt', 'post-failure')
LABELS = ('A',) if VARIANT == 'dyntopo' else ('A', 'B')
SAME_OBJECT = False
KEY_AUTHORED = VARIANT != 'dyntopo'
ACTORS = {'A': 'Bundle Artist A', 'B': 'Bundle Artist B'}
SCENES = {'A': 'Bundle Scene A', 'B': 'Bundle Scene B'}
PAYLOADS = ('positions', 'normals', 'masks', 'face_offsets', 'grid_to_face',
            'pending_normal_faces', 'hidden')
BOOL_FIELDS = ('canonical', 'viewport', 'active', 'visibility_optimization',
               'render_pipeline', 'editors_update', 'pending_updates', 'authority',
               'dirty_coords', 'dirty_hidden', 'session_needs_flush')
INT_FIELDS = ('object_uid', 'mesh_uid', 'scene_uid', 'scene_original_session_uid',
              'graph_address', 'ccg_address', 'sync_writeback', 'debug_flags',
              'level', 'grid_size', 'grids_num', 'hidden_groups', 'hidden_group_size')
FIELDS = set(PAYLOADS + BOOL_FIELDS + INT_FIELDS + ('frame', 'ctime', 'view_layer'))
WINDOWS = {}
BRIDGE = program_undo.native_bridge()
REPORT = {'success': False, 'variant': VARIANT, 'checks': {}, 'observations': {},
          'comparisons': {}, 'actions': [], 'records': {}, 'paid_requests': 0,
          'fixture_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'binary': bpy.app.binary_path,
          'coverage': {'shape_keys_authored': KEY_AUTHORED, 'coarse_mask_authored': True,
                       'fine_hidden_authored': False, 'generic_bmesh_customdata_observed': False},
          'limitations': [
              'Grid observer v1 has an explicitly partial graph/modifier profile.',
              'Dyntopo observer proves coordinates, oriented topology, masks and hidden bits; '
              'generic CustomData and BMLog structure require the separate native codec tests.',
              'Fresh usable BMLog is tested by continued ordinary brush Undo/Redo, not introspected.',
              'TODO: lossless ordinary Sculpt Redo is deferred. Recovered continuation must match '
              'a same-scene ordinary reference byte-for-byte; checkpoint restoration stays exact.',
              'The strict Dyntopo reference uses native fixed-Y Draw. Newly generated vertices '
              'may correspond by an exact bijection; original vertex slots and every payload '
              'bit/connectivity remain fixed. No geometric tolerance is used.',
              'AREA Draw was not bitwise repeatable in a zero-checkpoint ordinary control. '
              'This is a measurement limitation, not the Redo TODO or a restriction on brushes.',
              'Normal Save/external MDisps and zero-runtime coverage belongs to separate fixtures.']}
STATE = {'phase': 'boot', 'started': time.monotonic(), 'done': False}
IDLE = IdleWait(10.0)  # Busy-job seconds allowed per wait, x the runner's --timeout-scale.
REPORT['idle_wait'] = {'base_s': IDLE.base, 'timeout_scale': IDLE.scale, 'budget_s': IDLE.budget}
controller = None

def check(name, value):
    REPORT['checks'][name] = bool(value)
    assert value, name

def action(name):
    REPORT['actions'].append({'name': name, 'time_unix': time.time()})

def refresh_windows():
    for window in bpy.context.window_manager.windows:
        for label in LABELS:
            if window.scene.name == SCENES[label]:
                WINDOWS[label] = window

def context(label):
    refresh_windows()
    window = WINDOWS[label]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=window, area=area, region=region)

def actor(label):
    return bpy.data.objects[ACTORS['A' if SAME_OBJECT else label]]

def parse_history(text):
    """Require the complete native stack, not equal empty parser results."""
    lines = text.splitlines()
    header = re.fullmatch(r"Undo (\d+) Steps \(\*: active, #=applied, M=memfile-active, S=skip\)",
                          lines[0]) if lines else None
    assert header is not None, 'Missing native Undo stack header'
    count = int(header.group(1))
    assert count > 0 and len(lines) == count + 1, 'Empty or incomplete native Undo stack'
    result = []
    for index, line in enumerate(lines[1:]):
        match = re.fullmatch(r"\[([ *][ #][ M][ S])\]\s+(\d+)\s+\{([^{}\s]+)\} type='([^']+)', name='(.*)'", line)
        assert match is not None, 'Unrecognized native Undo step: ' + line
        flags, printed_index, address, kind, name = match.groups()
        assert int(printed_index) == index, 'Native Undo indexes are not a complete sequential stack'
        result.append({'flags': flags, 'index': index, 'address': address,
                       'type': kind, 'name': name, 'active': flags[0] == '*',
                       'skip': flags[3] == 'S'})
    assert sum(row['active'] for row in result) == 1, 'Native Undo stack must have exactly one active step'
    return result


def history():
    """Printed stack addresses/flags are read-only and included in no-op equality."""
    path = OUT / 'history-latest.txt'
    sys.stdout.flush()
    libc = ctypes.CDLL('ucrtbase') if os.name == 'nt' else ctypes.CDLL(None)
    libc.fflush(None)
    saved = os.dup(1)
    try:
        with path.open('w') as output:
            os.dup2(output.fileno(), 1)
            bpy.context.window_manager.print_undo_steps()
            libc.fflush(None)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    text = path.read_text()
    REPORT.setdefault('history_dumps', []).append(text)
    return parse_history(text)

def unpack(data, typecode, byte_order):
    values = array.array(typecode)
    assert values.itemsize == 4
    values.frombytes(data)
    if sys.byteorder != byte_order:
        values.byteswap()
    return values

def validate_inventory(value):
    assert type(value) is dict and value.get('format_version') == 1, 'Missing/unknown grid observer schema'
    assert value.get('byte_order') in ('little', 'big')
    assert value.get('profile_complete') is False, 'Fixture expects explicitly partial profile v1'
    assert type(value.get('main_flush_needed')) is bool
    assert type(value.get('surfaces')) is list and len(value['surfaces']) <= 32
    identities = set()
    for surface in value['surfaces']:
        assert type(surface) is dict and FIELDS <= surface.keys(), 'Incomplete surface observation'
        assert all(type(surface[key]) is bool for key in BOOL_FIELDS)
        assert all(type(surface[key]) is int for key in INT_FIELDS)
        assert all(type(surface[key]) is bytes for key in PAYLOADS), 'Observer must return owned bytes'
        assert isinstance(surface['view_layer'], str)
        assert all(math.isfinite(surface[key]) for key in ('frame', 'ctime'))
        assert all(surface[key] > 0 for key in ('object_uid', 'mesh_uid', 'scene_uid',
                                               'graph_address', 'ccg_address', 'level', 'grid_size'))
        area = surface['grid_size'] ** 2
        samples = surface['grids_num'] * area
        assert 0 < samples <= 262144, 'Unexpectedly large or empty fixture surface'
        assert len(surface['positions']) == samples * 12
        assert len(surface['normals']) in (0, samples * 12)
        assert len(surface['masks']) in (0, samples * 4)
        assert len(surface['grid_to_face']) == surface['grids_num'] * 4
        assert surface['hidden_groups'] in (0, surface['grids_num'])
        assert surface['hidden_group_size'] >= 0
        if surface['hidden_groups']:
            assert surface['hidden_group_size'] == area
        assert len(surface['hidden']) == surface['hidden_groups'] * surface['hidden_group_size']
        assert all(bit in (0, 1) for bit in surface['hidden'])
        for key in ('positions', 'normals', 'masks'):
            assert all(math.isfinite(v) for v in unpack(surface[key], 'f', value['byte_order']))
        offsets = unpack(surface['face_offsets'], 'i', value['byte_order'])
        mapping = unpack(surface['grid_to_face'], 'i', value['byte_order'])
        pending = unpack(surface['pending_normal_faces'], 'i', value['byte_order'])
        assert len(offsets) >= 2 and offsets[0] == 0 and offsets[-1] == surface['grids_num']
        assert all(x <= y for x, y in zip(offsets, offsets[1:]))
        assert all(0 <= v < len(offsets) - 1 for v in (*mapping, *pending))
        identity = tuple(surface[k] for k in ('object_uid', 'scene_uid', 'view_layer',
                                             'graph_address', 'ccg_address'))
        assert identity not in identities, 'Duplicate inventory binding'
        identities.add(identity)
    # Inventory ordering is not the contract; no data or facts are discarded.
    value['surfaces'].sort(key=lambda s: tuple(s[k] for k in
        ('object_uid', 'scene_uid', 'view_layer', 'graph_address', 'ccg_address')))
    return value

def owned_bytes(value, stem):
    """Persist all owned bytes in one file per observation, with exact ranges."""
    path = OUT / (stem + '.bin')
    with path.open('wb') as output:
        def save(item):
            if type(item) is bytes:
                offset = output.tell()
                output.write(item)
                return {'path': str(path), 'offset': offset, 'bytes': len(item),
                        'sha256': hashlib.sha256(item).hexdigest()}
            if isinstance(item, dict):
                return {key: save(child) for key, child in item.items()}
            if isinstance(item, (tuple, list)):
                return [save(child) for child in item]
            return item
        return save(value)

def original_shape_keys(mesh):
    key = mesh.shape_keys
    if key is None:
        return None
    blocks = []
    for block in key.key_blocks:
        coordinates = array.array('f', [0.]) * (len(block.data) * 3)
        block.data.foreach_get('co', coordinates)
        blocks.append({'name': block.name, 'coordinates': coordinates.tobytes(),
                       'value': block.value, 'frame': block.frame,
                       'relative_key': block.relative_key.name, 'mute': block.mute,
                       'interpolation': block.interpolation,
                       'vertex_group': block.vertex_group,
                       'slider_min': block.slider_min, 'slider_max': block.slider_max})
    return {'uid': key.session_uid, 'name': key.name, 'use_relative': key.use_relative,
            'eval_time': key.eval_time, 'blocks': blocks}

def artist_state():
    result = {}
    for label in LABELS:
        with context(label):
            obj = actor(label)
            sculpt = bpy.context.scene.tool_settings.sculpt
            brush = sculpt.brush if sculpt else None
            result[label] = {
                'object_uid': obj.session_uid, 'mesh_uid': obj.data.session_uid,
                'scene_uid': bpy.context.scene.session_uid,
                'view_layer': bpy.context.view_layer.name, 'mode': obj.mode,
                'dyntopo': obj.use_dynamic_topology_sculpting,
                'active_shape_key_index': obj.active_shape_key_index,
                'show_only_shape_key': obj.show_only_shape_key,
                'use_shape_key_edit_mode': obj.use_shape_key_edit_mode,
                'active': bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
                'selection': sorted(o.name for o in bpy.context.view_layer.objects if o.select_get()),
                'brush': brush.name if brush else None,
                'brush_settings': {key: getattr(brush, key) for key in (
                    'sculpt_brush_type', 'direction', 'size', 'strength', 'use_pressure_size',
                    'use_pressure_strength', 'falloff_shape', 'use_frontface', 'use_locked_size',
                    'sculpt_plane')}
                    if brush else None,
                'unified_paint': {key: getattr(sculpt.unified_paint_settings, key) for key in (
                    'use_unified_size', 'use_unified_strength', 'size', 'strength')}
                    if sculpt else None,
                'mesh_symmetry': tuple(getattr(obj.data, 'use_mirror_' + axis) for axis in 'xyz'),
                'location': tuple(obj.location), 'rotation': tuple(obj.rotation_euler),
                'scale': tuple(obj.scale),
                'modifiers': [(m.name, m.type, m.levels, m.sculpt_levels, m.render_levels)
                              for m in obj.modifiers if m.type == 'MULTIRES'],
            }
    return result

def surfaces_for(value, label):
    binding = value['artists'][label]
    return [s for s in value['inventory']['surfaces'] if all(
        s[k] == binding[k] for k in ('object_uid', 'mesh_uid', 'scene_uid', 'view_layer'))]

def surface_for(value, label):
    surfaces = surfaces_for(value, label)
    assert len(surfaces) == 1, f'{label}: expected one materialized graph surface, got {len(surfaces)}'
    return surfaces[0]

def stroke(label, x, *, name):
    action(label + ': ordinary brush stroke; immediate snapshot follows')
    with context(label):
        view = bpy.context.space_data.region_3d
        view.view_rotation = Quaternion((1, 0, 0), math.pi / 2)
        view.view_location = (0, 0, 0)
        view.view_distance = 4.
        view.view_perspective = 'ORTHO'
        view.update()
        assert bpy.context.tool_settings.sculpt.brush is not None, label + ': missing ordinary brush'
        location = (x, -math.sqrt(1 - x*x), 0.)
        mouse = location_3d_to_region_2d(bpy.context.region, view, Vector(location))
        assert mouse is not None
        point = {'name': 'graph grid fixture', 'location': location, 'pressure': 1.,
                 'size': 1., 'is_start': True, 'x_tilt': 0., 'y_tilt': 0.,
                 'mouse': tuple(mouse), 'mouse_event': tuple(mouse), 'time': 0.}
        outcome = bpy.ops.sculpt.brush_stroke('EXEC_DEFAULT', True, stroke=[point], mode='NORMAL')
        check(name + '_ordinary_stroke_finished', outcome == {'FINISHED'})
    return observe(name + '-immediate')

def bind_window_scenes():
    action('Bind window B after creation has returned through the ordinary event loop')
    if len(LABELS) == 2:
        WINDOWS['B'].scene = bpy.data.scenes[SCENES['B']]
    for label in LABELS:
        with context(label):
            obj = actor(label)
            for candidate in bpy.context.view_layer.objects:
                candidate.select_set(candidate == obj)
            bpy.context.view_layer.objects.active = obj
            bpy.context.scene.render.threads_mode = 'FIXED'
            bpy.context.scene.render.threads = 2
            bpy.context.view_layer.update()  # Explicit setup action, before preservation baseline.
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

def setup_coarse_mask(mesh):
    """Setup-only RNA read: Attribute.data may make shared storage mutable."""
    mask = mesh.attributes.get('.sculpt_mask')
    assert mask is not None and mask.domain == 'POINT' and mask.data_type == 'FLOAT'
    assert len(mask.data) == len(mesh.vertices)
    values = array.array('f', [0.]) * len(mesh.vertices)
    mask.data.foreach_get('value', values)
    assert any(value > 0. for value in values) and any(value == 0. for value in values)
    return values.tobytes()


def enter_sculpt(label, *, leave_first=False):
    action(label + ': ordinary mode transition after screen/Scene refresh')
    with context(label):
        obj = actor(label)
        # The normal screen callback must expose this member before mode_set triggers tool setup.
        # No active_object/sculpt_object injection or private context cache manipulation is allowed.
        check(label + ('_exit_' if leave_first else '_enter_') + 'normal_context_ready',
              hasattr(bpy.context, 'sculpt_object') and bpy.context.view_layer.objects.active == obj)
        if leave_first:
            if obj.mode != 'OBJECT':
                assert bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT') == {'FINISHED'}
            return
        assert obj.mode == 'OBJECT'
        assert bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='SCULPT') == {'FINISHED'}
        sculpt = bpy.context.scene.tool_settings.sculpt
        assert sculpt is not None
        unified = sculpt.unified_paint_settings
        unified.use_unified_size = unified.use_unified_strength = True
        unified.size, unified.strength = 90, .5
        if VARIANT != 'dyntopo':
            # Object-mode subdivision and Draw strokes do not create grid paint masks.
            # A normal mask operation materializes the authored coarse mask before baseline.
            before_mask = setup_coarse_mask(obj.data)
            action(label + ': ordinary mask smoothing materializes authored coarse mask')
            outcome = bpy.ops.sculpt.mask_filter('EXEC_DEFAULT', True, filter_type='SMOOTH',
                                                iterations=1, auto_iteration_count=False)
            check(label + '_ordinary_mask_filter_finished', outcome == {'FINISHED'})
            after_mask = setup_coarse_mask(obj.data)
            REPORT.setdefault('setup_masks', {})[label] = owned_bytes(
                {'before': before_mask, 'after': after_mask}, label + '-coarse-mask-setup')
            check(label + '_authored_coarse_mask_preserved_during_grid_setup', before_mask == after_mask)

def setup_refresh():
    action('Final ordinary setup evaluation/redraw; then return to event loop for idle jobs')
    for label in LABELS:
        with context(label):
            bpy.context.view_layer.update()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def setup_objects():
    check('one_initial_factory_window', len(bpy.context.window_manager.windows) == 1)
    WINDOWS['A'] = bpy.context.window_manager.windows[0]
    WINDOWS['A'].scene.name = SCENES['A']
    with context('A'):
        bpy.context.preferences.edit.use_global_undo = True
        bpy.context.preferences.edit.undo_steps = 64
        bpy.context.preferences.edit.undo_memory_limit = 0
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
        obj = bpy.context.object
        obj.name = ACTORS['A']
        mesh = obj.data
        mesh.use_mirror_x = mesh.use_mirror_y = mesh.use_mirror_z = False
        mask = mesh.attributes.new('.sculpt_mask', 'FLOAT', 'POINT')
        for vertex, value in zip(mesh.vertices, mask.data):
            value.value = .25 if vertex.co.x > .2 else 0.
        if KEY_AUTHORED:
            obj.shape_key_add(name='Basis')
            key = obj.shape_key_add(name='Authored Artist Key')
            for index, point in enumerate(key.data):
                point.co.z += .01 * (index % 3)
            key.value = .35
            modifier = obj.modifiers.new('Bundle Multires', 'MULTIRES')
            for _ in range(2):
                check('subdivision_' + str(_), bpy.ops.object.multires_subdivide(
                    modifier=modifier.name, mode='CATMULL_CLARK') == {'FINISHED'})
            modifier.sculpt_levels = modifier.levels = 2
        else:
            hidden = mesh.attributes.new('.hide_vert', 'BOOLEAN', 'POINT')
            for vertex, value in zip(mesh.vertices, hidden.data):
                value.value = vertex.co.y > .8
        if len(LABELS) == 2:
            other = obj.copy()
            other.data, other.name = mesh, ACTORS['B']
            bpy.data.scenes.new(SCENES['B']).collection.objects.link(other)
            check('second_window_created', bpy.ops.wm.window_new_main() == {'FINISHED'})
            WINDOWS['B'] = next(w for w in bpy.context.window_manager.windows if w != WINDOWS['A'])
        bpy.ops.ed.undo_push(message='Full Sculpt checkpoint fixture')


def jobs_idle():
    """True once no native job runs; the observers refuse to read while one does."""
    try:
        BRIDGE._test_original_sculpt_storage()
        BRIDGE._test_sculpt_grid_surfaces()
    except RuntimeError as error:
        if 'idle jobs' in str(error):
            return False
        raise
    return True


def observe(name):
    """Observe existing native state; never flush, synchronize, or evaluate it."""
    storage = BRIDGE._test_original_sculpt_storage()
    inventory = BRIDGE._test_sculpt_grid_surfaces()
    raw = {'original_sculpt_storage': storage, 'inventory': inventory}
    if VARIANT == 'dyntopo':
        with context('A'):
            raw['bmesh'] = BRIDGE._test_sculpt_surface()
    # Save every raw field before validation, normalization, or comparison.
    REPORT['observations'][name + '-raw'] = owned_bytes(raw, name + '-raw')
    validate_inventory(inventory)
    artists, meshes = artist_state(), {}
    for label in LABELS:
        mesh = actor(label).data
        meshes[label] = {'positions': mesh.angelo_sculpt_positions,
                         'topology': mesh.angelo_sculpt_topology,
                         'weights': mesh.angelo_vertex_group_weights,
                         'shape_keys': original_shape_keys(mesh)}
        assert all(type(meshes[label][key]) is bytes for key in ('positions', 'topology', 'weights'))
    result = {**raw, 'artists': artists, 'coarse_meshes': meshes, 'history': history(),
              'filepath': bpy.data.filepath, 'window_count': len(bpy.context.window_manager.windows),
              'preferences': {'file_preview_type': bpy.context.preferences.filepaths.file_preview_type,
                              'is_dirty': bpy.context.preferences.is_dirty}}
    REPORT['observations'][name] = owned_bytes(result, name)
    if VARIANT != 'dyntopo':
        check(name + '_complete_current_grid_bindings', len(inventory['surfaces']) == len(LABELS) and
              all(surface_for(result, label)['canonical'] and
                  surface_for(result, label)['scene_original_session_uid'] == artists[label]['scene_uid']
                  for label in LABELS))
    return result


def equivalent_after_load(value):
    """Only rebind documented IDs/addresses; all payload bits remain authority."""
    artists, grids, storage, meshes = {}, {}, {}, {}
    rebound_ids = {'object_uid', 'mesh_uid', 'scene_uid'}
    grid_transient = rebound_ids | {'scene_original_session_uid', 'graph_address',
                                    'ccg_address', 'active', 'pending_updates'}
    for label in LABELS:
        artist = value['artists'][label]
        artists[label] = {k: v for k, v in artist.items() if k not in rebound_ids}
        storage[label] = value['original_sculpt_storage'][artist['mesh_uid']]
        mesh = dict(value['coarse_meshes'][label])
        if mesh['shape_keys'] is not None:
            mesh['shape_keys'] = {k: v for k, v in mesh['shape_keys'].items() if k != 'uid'}
        meshes[label] = mesh
        if VARIANT != 'dyntopo':
            surface = surface_for(value, label)
            grids[label] = {k: v for k, v in surface.items() if k not in grid_transient}
    result = {'artists': artists, 'surfaces': grids, 'original_storage': storage,
              'coarse_meshes': meshes, 'window_count': value['window_count'],
              'main_flush_needed': value['inventory']['main_flush_needed']}
    if VARIANT == 'dyntopo':
        bm = value['bmesh']
        # Face visitation and cyclic first corner are not persistent topology.
        # Vertex ordering, winding, connectivity, and all float bits stay exact.
        faces = [tuple(face) for face in bm['topology']]
        result['bmesh'] = {'positions': array.array('f', (x for co in bm['positions'] for x in co)).tobytes(),
                           'masks': array.array('f', bm['masks']).tobytes(),
                           'hidden': bytes(bm['hidden']),
                           'topology': sorted(min(f[i:] + f[:i] for i in range(len(f))) for f in faces)}
    return result


def exact_capture(name, before, after):
    changed = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
    REPORT['comparisons'][name] = {'changed_fields': sorted(changed), 'same_main_exact': not changed}
    check(name + '_all_source_bytes_context_history_unchanged', not changed)


def restored_equal(name, before, after):
    if VARIANT == 'dyntopo':
        check(name + '_authored_and_restored_dyntopo_flush_required',
              before['inventory']['main_flush_needed'] is True and after['inventory']['main_flush_needed'] is True)
    left, right = equivalent_after_load(before), equivalent_after_load(after)
    changed = [key for key in left.keys() | right.keys() if left.get(key) != right.get(key)]
    REPORT['comparisons'][name] = {'changed_fields': sorted(changed), 'exact_payloads': not changed,
        'before_after_main_flush': [v['inventory']['main_flush_needed'] for v in (before, after)],
        'transient_owner_observations': [
            [{key: row[key] for key in ('object_uid', 'mesh_uid', 'scene_uid', 'graph_address',
                                      'ccg_address', 'active', 'pending_updates')}
             for row in v['inventory']['surfaces']] for v in (before, after)]}
    check(name + '_all_semantic_owners_and_payload_bits', not changed)


def dyntopo_new_vertex_bijection(left, right, baseline_vertices):
    """Match independently generated vertices exactly; never rebind the original vertices."""
    assert type(baseline_vertices) is int and baseline_vertices > 0, 'invalid baseline size'
    count = len(left['positions'])
    assert count == len(right['positions']) and count > baseline_vertices, 'vertex count/refinement differs'

    def float_bits(values, size):
        assert len(values) == size and all(type(x) in (int, float) and math.isfinite(x)
                                          for x in values), 'invalid finite float payload'
        return array.array('f', values).tobytes()

    positions = [[float_bits(co, 3) for co in side['positions']] for side in (left, right)]
    # Even exact spatial matching cannot change the meaning of an existing vertex slot.
    assert positions[0][:baseline_vertices] == positions[1][:baseline_vertices], 'original vertex changed'
    for side in positions:
        assert len(set(side)) == count, 'ambiguous duplicate position'
    right_new = {key: index for index, key in enumerate(positions[1]) if index >= baseline_vertices}
    assert set(positions[0][baseline_vertices:]) == set(right_new), 'new vertex position changed'
    mapping = list(range(baseline_vertices)) + [right_new[key] for key in positions[0][baseline_vertices:]]
    assert len(set(mapping)) == count, 'incomplete vertex correspondence'
    masks = [float_bits(side['masks'], count) for side in (left, right)]
    assert all(masks[0][4*i:4*i+4] == masks[1][4*j:4*j+4]
               for i, j in enumerate(mapping)), 'mask bits changed'
    assert all(len(side['hidden']) == count and all(type(x) is bool for x in side['hidden'])
               for side in (left, right)), 'invalid hidden payload'
    assert all(left['hidden'][i] == right['hidden'][j] for i, j in enumerate(mapping)), 'hidden flag changed'

    def faces(side, remap):
        result = []
        for face in side['topology']:
            assert len(face) >= 3 and all(type(v) is int and 0 <= v < count for v in face), 'invalid face vertex'
            assert len(set(face)) == len(face), 'repeated face vertex'
            ring = tuple(remap[v] for v in face)
            result.append(min(ring[i:] + ring[:i] for i in range(len(ring))))
        return sorted(result)

    inverse = {right_index: left_index for left_index, right_index in enumerate(mapping)}
    assert faces(left, list(range(count))) == faces(right, inverse), 'oriented connectivity/multiplicity changed'
    return mapping


def continued_stroke_equal(name, before, after, baseline):
    """Only independent Dyntopo stroke/Redo comparisons may rebind new vertex slots."""
    if VARIANT != 'dyntopo':
        return restored_equal(name, before, after)
    check(name + '_authored_and_restored_dyntopo_flush_required',
          before['inventory']['main_flush_needed'] is True and after['inventory']['main_flush_needed'] is True)
    left, right = equivalent_after_load(before), equivalent_after_load(after)
    changed = sorted(key for key in left.keys() | right.keys()
                     if key != 'bmesh' and left.get(key) != right.get(key))
    correspondence = {'baseline_vertices': len(baseline['bmesh']['positions']),
                      'original_vertex_slots_fixed': True, 'tolerance': 0,
                      'raw_snapshots_retained': True, 'left_to_right': None}
    try:
        correspondence['left_to_right'] = dyntopo_new_vertex_bijection(
            before['bmesh'], after['bmesh'], correspondence['baseline_vertices'])
    except AssertionError as error:
        correspondence['error'] = str(error)
        changed.append('bmesh')
    REPORT['comparisons'][name] = {'changed_fields': sorted(changed), 'exact_payloads': not changed,
                                  'new_vertex_correspondence': correspondence}
    check(name + '_all_semantic_owners_and_payload_bits', not changed)


def geometry(value, label):
    if VARIANT == 'dyntopo':
        return equivalent_after_load(value)['bmesh']
    row = surface_for(value, label)
    return {key: row[key] for key in ('positions', 'masks', 'hidden', 'face_offsets', 'grid_to_face')}


def record_pair(record, name, *, validation='VALIDATED'):
    actual = controller.checkpoints.load(record['id'])
    REPORT['records'][name] = actual
    check(name + '_full_runtime_pair', actual['version'] == 4 and actual['state'] == 'READY'
          and actual['capture_validation'] == validation and actual['sculpt']['owner_count'] == len(LABELS))
    folder = controller.checkpoints.folder(actual['id'])
    check(name + '_closed_runtime_name', actual['sculpt']['filename'] == 'sculpt.runtime')
    components = {}
    for filename, size, digest in (
            ('scene.blend', actual['scene_bytes'], actual['scene_hash']),
            ('sculpt.runtime', actual['sculpt']['bytes'], actual['sculpt']['hash'])):
        path = folder / filename
        data = path.read_bytes()
        components[filename] = {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                'blake2b256': hashlib.blake2b(data, digest_size=32).hexdigest()}
        check(name + '_' + filename + '_actual_hash', len(data) == size and components[filename]['blake2b256'] == digest)
    REPORT.setdefault('components', {})[name] = components
    return actual


def capture(name):
    before = observe(name + '-before')
    record = ui.capture_checkpoint(controller, purpose='request')
    after = observe(name + '-after')
    exact_capture(name, before, after)
    return record_pair(record, name), before


def restore(identity, name, *, expected='complete'):
    before = observe(name + '-before')
    notices = len(controller.state['messages'])
    with context('A'):
        outcome = bpy.ops.angelo.restore_checkpoint('EXEC_DEFAULT', checkpoint_id=identity)
    REPORT.setdefault('restores', {})[name] = {'outcome': sorted(outcome)}
    check(name + '_production_operator_finished', outcome == {'FINISHED'})
    notice = next(item for item in reversed(controller.state['messages'][notices:])
                  if item.get('restore_source_checkpoint') == identity)
    REPORT['restores'][name]['notice'] = dict(notice)
    check(name + '_truthful_complete_notice', notice['restore_state'] == expected and notice['scene_restored'] is True)
    saved = controller.store.load()
    check(name + '_durable_safety_link', any(item.get('restore_checkpoint') == notice['restore_checkpoint']
          for item in saved['messages']))
    safety = record_pair(controller.checkpoints.load(notice['restore_checkpoint']), name + '-safety')
    check(name + '_protected_full_safety', safety['protected'] is True and safety['purpose'] == 'safety')
    after = observe(name + '-after')
    return after, notice['restore_checkpoint'], before


def continuation_history(name, states):
    """Verify real Undo traversal while retaining the complete native prefix in receipts."""
    baseline = states['baseline']['history']
    active = next(row['index'] for row in baseline if row['active'])
    check(name + '_baseline_at_history_tip', active == len(baseline) - 1)
    identity = lambda row: tuple(row[key] for key in ('index', 'address', 'type', 'name', 'skip'))
    prefix = [identity(row) for row in baseline]
    expected_active = {'stroke': len(baseline), 'undo': active, 'redo': len(baseline)}
    for phase in ('stroke', 'undo', 'redo'):
        rows = states[phase]['history']
        check(name + '_' + phase + '_one_sculpt_history_step', len(rows) == len(baseline) + 1 and
              [identity(row) for row in rows[:-1]] == prefix and
              rows[-1]['type'] == 'Sculpt' and rows[-1]['name'] == 'Draw Brush' and not rows[-1]['skip'])
        check(name + '_' + phase + '_history_active_position',
              [row['index'] for row in rows if row['active']] == [expected_active[phase]])
        check(name + '_' + phase + '_history_applied_state',
              (rows[-1]['flags'][1] == '#') == (phase != 'undo'))
    check(name + '_same_stroke_history_owner',
          len({states[phase]['history'][-1]['address'] for phase in ('stroke', 'undo', 'redo')}) == 1)


def ordinary_redo_todo(name, label, stroked, redone):
    """Record lossless-Redo evidence without treating an ordinary reference as a checkpoint."""
    left, right = equivalent_after_load(stroked), equivalent_after_load(redone)
    changed = sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key))
    REPORT.setdefault('deferred_todos', {})[name + '-' + label] = {
        'guarantee': 'bit-for-bit lossless ordinary Sculpt Redo',
        'status': 'deferred by user for v1; not certified by this fixture',
        'changed_semantic_sections': changed,
        'stroke_equals_redo_geometry': geometry(stroked, label) == geometry(redone, label),
        'stroke_equals_redo_original_storage': left['original_storage'] == right['original_storage'],
        'stroke_observation': name + '-' + label + '-stroke-immediate',
        'redo_observation': name + '-' + label + '-redo',
        'raw_bytes_retained': True,
        'acceptance_rule': 'recovered phases must equal the same-scene ordinary reference exactly',
        'tolerance': 0,
    }


def continuation(name, reference=None, complete_baseline=None):
    states = {}
    for label in LABELS:
        baseline = observe(name + '-' + label + '-baseline')
        # Select the Undo target before invoking the brush or observing Undo's result.
        complete_target = complete_baseline is not None and label == LABELS[0]
        undo_expected = reference[label]['undo'] if reference is not None else None
        undo_target = None
        if complete_target:
            rows = baseline['history']
            check(name + '_' + label + '_complete_undo_target_before_edit', len(rows) == 1 and
                  rows[0]['index'] == 0 and rows[0]['type'] == 'Global Undo' and
                  rows[0]['name'] == 'Checkpoint Restored' and rows[0]['active'] and not rows[0]['skip'])
            undo_target = {key: rows[0][key] for key in ('index', 'address', 'type', 'name', 'skip')}
            undo_expected = complete_baseline
            restored_equal(name + '_' + label + '_complete_baseline_before_edit', complete_baseline, baseline)
        if reference is not None:
            restored_equal(name + '_' + label + '_matches_ordinary_baseline',
                           reference[label]['baseline'], baseline)
        stroked = stroke(label, -.12 if label == 'A' else .12, name=name + '-' + label + '-stroke')
        check(name + '_' + label + '_continued_stroke_changes_geometry', geometry(baseline, label) != geometry(stroked, label))
        if VARIANT == 'dyntopo':
            check(name + '_' + label + '_continued_stroke_refines_topology',
                  len(stroked['bmesh']['positions']) > len(baseline['bmesh']['positions']) and
                  len(stroked['bmesh']['topology']) > len(baseline['bmesh']['topology']))
        if reference is not None:
            continued_stroke_equal(name + '_' + label + '_matches_ordinary_stroke',
                                   reference[label]['stroke'], stroked, baseline)
        with context(label):
            check(name + '_' + label + '_ordinary_undo', bpy.ops.ed.undo() == {'FINISHED'})
        undone = observe(name + '-' + label + '-undo')
        check(name + '_' + label + '_exact_undo_geometry', geometry(undone, label) == geometry(baseline, label))
        if complete_target:
            active = [row for row in undone['history'] if row['active']]
            check(name + '_' + label + '_complete_undo_target_after_edit', len(active) == 1 and
                  {key: active[0][key] for key in undo_target} == undo_target)
            restored_equal(name + '_' + label + '_undo_restores_complete_baseline', undo_expected, undone)
        elif reference is not None:
            restored_equal(name + '_' + label + '_matches_ordinary_undo', undo_expected, undone)
        REPORT['comparisons'][name + '-' + label + '-undo-target'] = {
            'semantics': 'complete_checkpoint_baseline' if complete_target else 'ordinary_sculpt_delta',
            'expected_state': 'capture-before' if complete_target else 'ordinary-' + label + '-undo',
            'native_complete_target': undo_target,
            'all_semantic_fields_compared': reference is not None,
            'tolerance': 0}
        with context(label):
            check(name + '_' + label + '_ordinary_redo', bpy.ops.ed.redo() == {'FINISHED'})
        redone = observe(name + '-' + label + '-redo')
        if reference is not None:
            continued_stroke_equal(name + '_' + label + '_matches_ordinary_redo',
                                   reference[label]['redo'], redone, baseline)
        ordinary_redo_todo(name, label, stroked, redone)
        for other in LABELS:
            if other != label:
                check(name + '_' + label + '_keeps_' + other + '_geometry', all(
                    geometry(value, other) == geometry(baseline, other) for value in (stroked, undone, redone)))
        baseline_artists = equivalent_after_load(baseline)['artists']
        check(name + '_' + label + '_artist_context_preserved', all(
            equivalent_after_load(value)['artists'] == baseline_artists for value in (stroked, undone, redone)))
        states[label] = {'baseline': baseline, 'stroke': stroked, 'undo': undone, 'redo': redone}
        continuation_history(name + '_' + label, states[label])
        REPORT['comparisons'][name + '-' + label + '-continuation'] = {
            'strict_geometry_keys': list(geometry(redone, label)),
            'all_other_raw_fields_retained': True, 'tolerance': 0,
            'reference': None if reference is None else 'ordinary-' + label,
            'matched_semantic_sections': None if reference is None else sorted(equivalent_after_load(redone)),
            'ordinary_history_prefix': (baseline if reference is None else reference[label]['baseline'])['history'],
            'current_history_prefix': baseline['history'],
            'history_policy': 'exact identities within each roundtrip; explicit checkpoint rebase between runs'}
    return states


def record_ids():
    return {path.name for path in controller.checkpoints.directory.iterdir()
            if path.is_dir() and (path / 'checkpoint.json').is_file()} if controller.checkpoints.directory.exists() else set()


def corruption_case(record):
    # A refusal is meaningful only after this exact pair has restored successfully
    # through the same production operator in this fresh variant.
    baseline = observe('corruption-valid-before')
    valid, _safety, _before = restore(record['id'], 'corruption-valid-control')
    restored_equal('corruption_valid_restore', baseline, valid)
    before = observe('corruption-before')
    identifiers, messages = record_ids(), list(controller.state['messages'])
    path = controller.checkpoints.folder(record['id']) / 'sculpt.runtime'
    original = path.read_bytes()
    # Owned test checkpoint only: a same-size payload corruption must fail its hash.
    altered = bytearray(original)
    altered[len(altered) // 2] ^= 1
    path.write_bytes(altered)
    REPORT['corruption'] = {'original_sha256': hashlib.sha256(original).hexdigest(),
                            'corrupted_sha256': hashlib.sha256(altered).hexdigest(), 'bytes': len(altered)}
    try:
        reported_error = None
        with context('A'):
            try:
                outcome = bpy.ops.angelo.restore_checkpoint('EXEC_DEFAULT', checkpoint_id=record['id'])
            except RuntimeError as error:
                reported_error = str(error).strip()
                REPORT['corruption']['operator_error'] = reported_error
                outcome = {'CANCELLED'}
        check('corrupt_restore_refused', outcome == {'CANCELLED'})
        expected = 'The recovery component hash does not match the checkpoint'
        check('corrupt_restore_specific_component_hash_failure', reported_error in (expected, 'Error: ' + expected))
        check('corrupt_restore_before_safety_reservation', record_ids() == identifiers)
        check('corrupt_restore_no_success_or_safety_notice', controller.state['messages'] == messages)
        exact_capture('corrupt_restore_no_replacement', before, observe('corruption-after'))
    finally:
        path.write_bytes(original)
    check('corrupt_fixture_restored_only_owned_file', path.read_bytes() == original)


def post_failure_case():
    before = observe('post-failure-before')
    identifiers = record_ids()
    calls, stderr, expected_tracebacks = [], io.StringIO(), []
    marker = 'EXPECTED_SCULPT_CHECKPOINT_SAVE_POST_FAILURE'
    def fail_post(*_args):
        calls.append('save_post')
        try:
            raise RuntimeError(marker)
        except RuntimeError as error:
            # Format the exact traceback that native PyErr_PrintEx will emit.
            # No unrelated captured output is allowed around this one failure.
            expected_tracebacks.append(''.join(traceback.format_exception(error)))
            raise
    handler_index = len(bpy.app.handlers.save_post)
    bpy.app.handlers.save_post.append(fail_post)
    error = None
    try:
        with contextlib.redirect_stderr(stderr):
            try:
                ui.capture_checkpoint(controller, purpose='request')
            except BaseException as caught:
                error = caught
    finally:
        bpy.app.handlers.save_post.remove(fail_post)
        (OUT / 'expected-save-post-stderr.txt').write_text(stderr.getvalue())
    REPORT['post_failure'] = {'callback_count': len(calls), 'error_type': type(error).__name__ if error else None,
                              'error': str(error) if error else None, 'stderr': str(OUT / 'expected-save-post-stderr.txt')}
    check('post_handler_called_once_and_restored', calls == ['save_post'] and fail_post not in bpy.app.handlers.save_post)
    # Like an ordinary save, a SAVE_POST handler that raises is printed, never a capture failure.
    check('post_handler_error_does_not_fail_the_capture', error is None)
    check('post_failure_one_expected_traceback', len(expected_tracebacks) == 1)
    expected_stderr = f'Error in bpy.app.handlers.save_post[{handler_index}]:\n' + expected_tracebacks[0]
    (OUT / 'expected-save-post-stderr-contract.txt').write_text(expected_stderr)
    check('post_failure_only_expected_handler_stderr', stderr.getvalue() == expected_stderr)
    created = record_ids() - identifiers
    check('post_failure_one_actual_record', len(created) == 1)
    record = record_pair({'id': next(iter(created))}, 'post-failure', validation='VALIDATED')
    check('post_failure_pair_available_after_native_close', controller.checkpoints.availability(record['id'])['available'])
    check('post_failure_no_active_capture', ui._capturing_checkpoint is False)
    exact_capture('post_failure_source_preserved', before, observe('post-failure-after'))


def finish(error=None):
    STATE['done'] = True
    elapsed = time.monotonic() - STATE['started']
    REPORT.update(elapsed_seconds=elapsed, deadline_seconds=180, deadline_exceeded=elapsed > 180)
    if elapsed > 180 and error is None:
        error = 'Full Sculpt checkpoint fixture deadline exceeded before completion'
    REPORT.update(success=error is None and all(REPORT['checks'].values()), error=error,
                  phase=STATE['phase'], provider_started=any(c.provider is not None for c in ui._controllers.values()))
    if REPORT['provider_started']:
        REPORT.update(success=False, error='Unexpected provider start')
    (OUT / 'sculpt-checkpoint-bundle.json').write_text(json.dumps(REPORT, indent=2))
    print('ANGELO_SCULPT_CHECKPOINT_BUNDLE', json.dumps({'success': REPORT['success'],
          'phase': STATE['phase'], 'variant': VARIANT, 'checks': len(REPORT['checks'])}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def tick():
    global controller
    try:
        if STATE['done']:
            return None
        if time.monotonic() - STATE['started'] > 180:
            raise RuntimeError('Full Sculpt checkpoint fixture deadline exceeded')
        phase = STATE['phase']
        if phase == 'boot':
            check('native_observers_available', BRIDGE is not None and all(callable(getattr(BRIDGE, key, None))
                  for key in ('_test_original_sculpt_storage', '_test_sculpt_grid_surfaces', '_test_sculpt_surface')))
            setup_objects()
            STATE['phase'] = 'bind'
        elif phase == 'bind':
            bind_window_scenes()
            STATE['phase'] = 'enter-a'
        elif phase == 'enter-a':
            enter_sculpt('A')
            STATE['phase'] = 'exit-b' if len(LABELS) == 2 else 'dyntopo'
        elif phase == 'exit-b':
            enter_sculpt('B', leave_first=True)
            STATE['phase'] = 'enter-b'
        elif phase == 'enter-b':
            enter_sculpt('B')
            STATE['phase'] = 'refresh'
        elif phase == 'dyntopo':
            with context('A'):
                check('dyntopo_enabled', bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT', True) == {'FINISHED'})
                check('editable_dyntopo_mode', actor('A').use_dynamic_topology_sculpting)
            STATE['phase'] = 'refresh'
        elif phase == 'refresh':
            setup_refresh()
            STATE['phase'] = 'idle'
        elif phase == 'idle':
            if not IDLE.ready(jobs_idle):
                return .1
            controller = ui.get_controller()
            controller.refresh_models = lambda *args, **kwargs: None
            check('production_checkpoint_factory_installed', callable(controller.checkpoint_factory))
            STATE['phase'] = 'exercise'
        elif phase == 'exercise':
            # A job can start once control returns to the event loop: observe in the tick that saw idle.
            if not IDLE.ready(jobs_idle):
                return .1
            if VARIANT == 'dyntopo':
                # Explicit controlled input before all baselines; AREA brushes stay supported.
                with context('A'):
                    brush = bpy.context.scene.tool_settings.sculpt.brush
                    previous_plane = brush.sculpt_plane
                    brush.sculpt_plane = 'Y'
                    check('dyntopo_reference_uses_native_fixed_y', brush.sculpt_plane == 'Y')
                    REPORT['dyntopo_reference'] = {'plane_before': previous_plane, 'plane': 'Y',
                        'new_vertex_identity': 'unique exact float32 bijection; original slots fixed',
                        'area_repeatability': 'not certified; separate ordinary AREA failures retained',
                        'tolerance': 0}
            initial = observe('initial')
            for label in LABELS:
                changed = stroke(label, -.3 if label == 'A' else .3, name='initial-' + label)
                check(label + '_authored_stroke_changes_surface', geometry(initial, label) != geometry(changed, label))
            if len(LABELS) == 2:
                check('two_distinct_objects_one_shared_mesh', actor('A') != actor('B') and actor('A').data == actor('B').data)
                check('two_distinct_authored_surfaces', geometry(changed, 'A') != geometry(changed, 'B'))
                check('exact_two_canonical_surfaces', len(changed['inventory']['surfaces']) == 2 and
                      all(surface_for(changed, label)['canonical'] and surface_for(changed, label)['authority'] for label in LABELS))
                check('shared_coarse_mask_materialized_in_both_surfaces', all(any(unpack(
                    surface_for(changed, label)['masks'], 'f', changed['inventory']['byte_order'])) for label in LABELS))
            else:
                check('dyntopo_authored_mask_nonempty', any(changed['bmesh']['masks']))
                check('dyntopo_authored_hidden_nonempty', any(changed['bmesh']['hidden']))
                REPORT['coverage']['fine_hidden_authored'] = True
            repeat = observe('repeat')
            exact_capture('observer_no_effect', changed, repeat)
            if VARIANT == 'post-failure':
                post_failure_case()
                return finish()
            record, captured = capture('capture')
            if VARIANT == 'corrupt':
                corruption_case(record)
                return finish()
            check('ordinary_reference_before_any_restore', not REPORT.get('restores'))
            reference = continuation('ordinary')
            exact_capture('ordinary_reference_starts_at_captured_source',
                          captured, reference[LABELS[0]]['baseline'])
            for label in LABELS:
                changed = stroke(label, -.55 if label == 'A' else .55, name='later-' + label)
                check(label + '_later_stroke_differs_from_capture', geometry(captured, label) != geometry(changed, label))
            later = observe('later-both')
            restored, safety, pre_restore = restore(record['id'], 'restore')
            exact_capture('restore_entry_equals_later', later, pre_restore)
            restored_equal('restore_captured_pair', captured, restored)
            undone, _new_safety, _before = restore(safety, 'undo-restore')
            restored_equal('undo_restore_later_pair', later, undone)
            # Restore the captured pair again so continuation starts from cold install.
            cold, _safety, _before = restore(record['id'], 'restore-for-continuation')
            restored_equal('second_cold_restore', captured, cold)
            check('cold_history_rebased_to_complete_checkpoint', len(cold['history']) == 1 and
                  cold['history'][0]['type'] == 'Global Undo' and
                  cold['history'][0]['name'] == 'Checkpoint Restored' and
                  cold['history'][0]['active'] and not cold['history'][0]['skip'])
            continuation('cold', reference, captured)
            return finish()
        return .15
    except BaseException:
        return finish(traceback.format_exc())


bpy.app.timers.register(tick, first_interval=.2, persistent=True)
