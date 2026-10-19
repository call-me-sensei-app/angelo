# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Normal artist Save and protected Sculpt checkpoints with external MDisps.

Factory GUI only, private QA paths, actual production capture/Restore/full safety.
The original external file is relocated only inside owned QA and restored in finally.
No provider, manual snapshot, inherited native run or shared-surface Save guarantee.
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
VARIANT = 'external'
LABELS = ('A',)
SAME_OBJECT = KEY_AUTHORED = False
ACTORS = {'A': 'External Artist'}
SCENES = {'A': 'External Scene'}
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
REPORT = {'success': False, 'checks': {}, 'observations': {}, 'comparisons': {},
          'actions': [], 'records': {}, 'events': {}, 'paid_requests': 0,
          'fixture_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'binary': bpy.app.binary_path,
          'coverage': {'external_mdisps_resident': False, 'runtime_owners': 1},
          'limitations': ['One resident external Multires owner; distinct shared surfaces are a separate fixture.',
                          'Ordinary Save is permitted to flush Sculpt and change external bytes.',
                          'Grid observer v1 has a partial graph/modifier profile; no native pass from host tests.']}
ARTIST_FILE = OUT / 'artist.blend'
EXTERNAL_FILE = OUT / 'external-displacements.btx'
HELD_EXTERNAL = OUT / 'relocated' / 'external-displacements.btx'
CD_FLAG_EXTERNAL = 1 << 3  # DNA_customdata_types.h
CD_FLAG_IN_MEMORY = 1 << 4
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

def setup_refresh():
    action('Final ordinary setup evaluation/redraw; then return to event loop for idle jobs')
    for label in LABELS:
        with context(label):
            bpy.context.view_layer.update()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

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


def modifier():
    return actor('A').modifiers['External Multires']


def file_bytes(path):
    return path.read_bytes() if path.is_file() else None


def file_stamp(path):
    if not path.is_file():
        return None
    stat = path.stat()
    return {'device': stat.st_dev, 'inode': stat.st_ino, 'bytes': stat.st_size,
            'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns}


def digest(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


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
    storage = BRIDGE._test_original_sculpt_storage()
    inventory = BRIDGE._test_sculpt_grid_surfaces()
    raw = {'original_sculpt_storage': storage, 'inventory': inventory}
    REPORT['observations'][name + '-raw'] = owned_bytes(raw, name + '-raw')
    validate_inventory(inventory)
    obj, mesh = actor('A'), actor('A').data
    value = {**raw, 'artists': artist_state(), 'coarse_meshes': {'A': {
        'positions': mesh.angelo_sculpt_positions, 'topology': mesh.angelo_sculpt_topology,
        'weights': mesh.angelo_vertex_group_weights, 'shape_keys': original_shape_keys(mesh)}},
        'history': history(), 'filepath': bpy.data.filepath,
        'window_count': len(bpy.context.window_manager.windows),
        'external': {'filepath': modifier().filepath, 'is_external': modifier().is_external,
                     'disk': file_bytes(EXTERNAL_FILE), 'stamp': file_stamp(EXTERNAL_FILE)},
        'artist_file': file_bytes(ARTIST_FILE),
        'preferences': {'file_preview_type': bpy.context.preferences.filepaths.file_preview_type,
                        'is_dirty': bpy.context.preferences.is_dirty}}
    REPORT['observations'][name] = owned_bytes(value, name)
    check(name + '_exact_original_storage_owner', set(storage) == {mesh.session_uid})
    check(name + '_one_current_canonical_owner', len(inventory['surfaces']) == 1 and
          surface_for(value, 'A')['canonical'] and surface_for(value, 'A')['authority'] and
          surface_for(value, 'A')['scene_original_session_uid'] == value['artists']['A']['scene_uid'])
    check(name + '_live_sculpt_owner', obj.mode == 'SCULPT')
    return value


def original_storage(value):
    return value['original_sculpt_storage'][value['artists']['A']['mesh_uid']]


def displacement_payload(value):
    layer = original_storage(value)['mdisps']
    return tuple((row['count'], row['level'], row['positions'], row['hidden']) for row in layer['values'])


def resident_external(value):
    layer = original_storage(value)['mdisps']
    return (value['external']['is_external'] is True and value['external']['filepath'].startswith('//')
            and original_storage(value)['external_path'] == value['external']['filepath']
            and layer is not None and layer['data_present'] is True
            and layer['flag'] & CD_FLAG_EXTERNAL and layer['flag'] & CD_FLAG_IN_MEMORY
            and bool(layer['values']) and all(row['count'] > 0 and type(row['positions']) is bytes
                                            and len(row['positions']) == row['count'] * 12 for row in layer['values'])
            and type(value['external']['disk']) is bytes and len(value['external']['disk']) > 0)


def inline_expected(value):
    """Apply only C's documented private BASE external metadata transformation."""
    import copy
    expected = copy.deepcopy(value)
    for storage in expected['original_sculpt_storage'].values():
        storage['external_path'] = None
        for key in ('mdisps', 'grid_paint_mask'):
            if storage[key] is not None:
                storage[key]['flag'] &= ~CD_FLAG_EXTERNAL
    return expected


def with_save_events(name, operation):
    events = []
    def record(kind, logical_path):
        events.append({'kind': kind, 'logical_path': logical_path,
                       'artist_filepath': bpy.data.filepath,
                       'relative_external_path': modifier().filepath,
                       'external_sha256': digest(file_bytes(EXTERNAL_FILE))})
    def pre(logical_path):
        record('PRE', logical_path)
    def post(logical_path):
        record('POST', logical_path)
    def failed(logical_path):
        record('FAIL', logical_path)
    installed = ((bpy.app.handlers.save_pre, pre), (bpy.app.handlers.save_post, post),
                 (bpy.app.handlers.save_post_fail, failed))
    for handlers, callback in installed:
        handlers.append(callback)
    try:
        return operation()
    finally:
        for handlers, callback in installed:
            handlers.remove(callback)
        REPORT['events'][name] = events
        check(name + '_owned_handlers_removed', all(callback not in handlers for handlers, callback in installed))


def event_order(name, logical_path, before, after, *, external_changed):
    events = REPORT['events'][name]
    check(name + '_exact_pre_post_order', [row['kind'] for row in events] == ['PRE', 'POST'])
    check(name + '_actual_callback_path', all(row['logical_path'] == logical_path for row in events))
    check(name + '_callback_artist_filepath', all(row['artist_filepath'] == str(ARTIST_FILE) for row in events))
    check(name + '_callback_relative_external_path', all(
        row['relative_external_path'] == before['external']['filepath'] for row in events))
    check(name + '_pre_reads_old_external_bytes', events[0]['external_sha256'] == digest(before['external']['disk']))
    check(name + '_post_reads_actual_final_external_bytes', events[1]['external_sha256'] == digest(after['external']['disk']))
    check(name + '_expected_external_write_behavior',
          (events[0]['external_sha256'] != events[1]['external_sha256']) is external_changed)


def setup_objects():
    check('one_factory_window', len(bpy.context.window_manager.windows) == 1)
    check('owned_output_names_unused', not ARTIST_FILE.exists() and not EXTERNAL_FILE.exists()
          and not HELD_EXTERNAL.exists())
    WINDOWS['A'] = bpy.context.window_manager.windows[0]
    WINDOWS['A'].scene.name = SCENES['A']
    with context('A'):
        bpy.context.preferences.edit.use_global_undo = True
        bpy.context.preferences.edit.undo_steps = 64
        bpy.context.preferences.edit.undo_memory_limit = 0
        bpy.context.preferences.filepaths.file_preview_type = 'NONE'
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
        obj = bpy.context.object
        obj.name = ACTORS['A']
        obj.data.use_mirror_x = obj.data.use_mirror_y = obj.data.use_mirror_z = False
        multires = obj.modifiers.new('External Multires', 'MULTIRES')
        for level in range(2):
            check('subdivision_' + str(level), bpy.ops.object.multires_subdivide(
                modifier=multires.name, mode='CATMULL_CLARK') == {'FINISHED'})
        multires.levels = multires.sculpt_levels = 2
        check('initial_artist_file_saved', bpy.ops.wm.save_as_mainfile(
            filepath=str(ARTIST_FILE), check_existing=False) == {'FINISHED'})
        check('actual_external_save_operator', bpy.ops.object.multires_external_save(
            'EXEC_DEFAULT', modifier=multires.name, filepath=str(EXTERNAL_FILE), relative_path=True) == {'FINISHED'})
        check('external_file_created_in_owned_qa', EXTERNAL_FILE.is_file() and EXTERNAL_FILE.stat().st_size > 0)
        check('external_path_is_relative_to_artist_project', multires.is_external and
              multires.filepath == '//external-displacements.btx' and bpy.data.filepath == str(ARTIST_FILE))
        bpy.ops.ed.undo_push(message='External Multires checkpoint fixture')


def exercise():
    initial = observe('materialized-baseline')
    check('resident_external_baseline_complete', resident_external(initial))
    REPORT['coverage']['external_mdisps_resident'] = True
    authored = stroke('A', -.3, name='ordinary-save-stroke')
    check('ordinary_stroke_authored_live_change', geometry(initial, 'A') != geometry(authored, 'A'))
    before_save = observe('ordinary-save-before')
    with context('A'):
        outcome = with_save_events('ordinary-save', lambda: bpy.ops.wm.save_as_mainfile(
            filepath=str(ARTIST_FILE), check_existing=False))
    check('ordinary_save_finished', outcome == {'FINISHED'})
    after_save = observe('ordinary-save-after')
    event_order('ordinary-save', str(ARTIST_FILE), before_save, after_save, external_changed=True)
    check('ordinary_save_flushed_new_displacements', displacement_payload(before_save) != displacement_payload(after_save))
    check('ordinary_save_retains_relative_external_storage', resident_external(after_save) and
          after_save['external']['filepath'] == before_save['external']['filepath'])
    REPORT['comparisons']['ordinary-save'] = {
        'semantics': 'Ordinary Save flushes Sculpt and writes external data; no no-change promise',
        'surface_changed_fields': [key for key in surface_for(before_save, 'A')
                                   if surface_for(before_save, 'A')[key] != surface_for(after_save, 'A')[key]],
        'external_sha256_before_after': [digest(value['external']['disk']) for value in (before_save, after_save)]}

    before_stroke = observe('checkpoint-stroke-before')
    authored = stroke('A', .3, name='checkpoint-stroke')
    check('checkpoint_stroke_authored_live_change', geometry(before_stroke, 'A') != geometry(authored, 'A'))
    captured = observe('checkpoint-before')
    check('checkpoint_external_resident_precondition', resident_external(captured))
    record = with_save_events('checkpoint', lambda: ui.capture_checkpoint(controller, purpose='request'))
    after_capture = observe('checkpoint-after')
    exact_capture('checkpoint_preserves_artist_and_external_bytes', captured, after_capture)
    record = record_pair(record, 'checkpoint')
    event_order('checkpoint', str(controller.checkpoints.folder(record['id']) / 'scene.blend'),
                captured, after_capture, external_changed=False)

    # Only owned QA data is moved. Full production Restore still must create a
    # complete safety pair from resident data; failure is not bypassed or retried.
    HELD_EXTERNAL.parent.mkdir(exist_ok=True)
    check('relocation_target_unused', not HELD_EXTERNAL.exists())
    original = EXTERNAL_FILE.read_bytes()
    EXTERNAL_FILE.rename(HELD_EXTERNAL)
    try:
        check('external_unavailable_at_original_path', not EXTERNAL_FILE.exists() and HELD_EXTERNAL.read_bytes() == original)
        restored, safety, _before = restore(record['id'], 'self-contained-restore')
        restored_equal('self_contained_exact_inline_base_and_runtime', inline_expected(captured), restored)
        check('restored_base_is_inline', original_storage(restored)['external_path'] is None and
              not original_storage(restored)['mdisps']['flag'] & CD_FLAG_EXTERNAL and
              restored['external']['is_external'] is False and restored['external']['filepath'] == '')
        check('restore_neither_recreates_nor_changes_external_file', not EXTERNAL_FILE.exists() and HELD_EXTERNAL.read_bytes() == original)
        check('restore_keeps_artist_project_disk_unchanged', restored['artist_file'] == captured['artist_file'])
        check('restore_uses_working_copy', restored['filepath'] != str(ARTIST_FILE))
        continued = stroke('A', -.5, name='self-contained-continuation')
        check('restored_inline_owner_still_sculpts_without_external_file', geometry(continued, 'A') != geometry(restored, 'A')
              and not EXTERNAL_FILE.exists() and HELD_EXTERNAL.read_bytes() == original)
        REPORT['self_contained_safety_id'] = safety
    finally:
        # Preserve any unexpected created bytes as evidence before restoring our
        # own external file. Such creation already fails the assertion above.
        if EXTERNAL_FILE.exists():
            unexpected = OUT / 'unexpected-external-during-restore.btx'
            assert not unexpected.exists()
            EXTERNAL_FILE.rename(unexpected)
            REPORT['unexpected_external_file'] = str(unexpected)
        HELD_EXTERNAL.rename(EXTERNAL_FILE)
    check('owned_external_file_restored_exactly', EXTERNAL_FILE.read_bytes() == original)


def finish(error=None):
    STATE['done'] = True
    elapsed = time.monotonic() - STATE['started']
    REPORT.update(elapsed_seconds=elapsed, deadline_seconds=180, deadline_exceeded=elapsed > 180)
    if elapsed > 180 and error is None:
        error = 'External Sculpt checkpoint fixture deadline exceeded before completion'
    REPORT.update(success=error is None and all(REPORT['checks'].values()), error=error,
                  phase='complete' if error is None else STATE['phase'],
                  provider_started=any(target.provider is not None for target in ui._controllers.values()))
    if REPORT['provider_started']:
        REPORT.update(success=False, error='Unexpected provider start')
    (OUT / 'sculpt-checkpoint-external.json').write_text(json.dumps(REPORT, indent=2))
    print('ANGELO_SCULPT_CHECKPOINT_EXTERNAL', json.dumps({'success': REPORT['success'],
          'phase': REPORT['phase'], 'checks': len(REPORT['checks'])}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def tick():
    global controller
    try:
        if STATE['done']:
            return None
        if time.monotonic() - STATE['started'] > 180:
            raise RuntimeError('External Sculpt checkpoint fixture deadline exceeded')
        phase = STATE['phase']
        if phase == 'boot':
            check('native_observers_available', BRIDGE is not None and all(callable(getattr(BRIDGE, key, None))
                  for key in ('_test_original_sculpt_storage', '_test_sculpt_grid_surfaces')))
            setup_objects()
            STATE['phase'] = 'bind'
        elif phase == 'bind':
            bind_window_scenes()
            STATE['phase'] = 'enter'
        elif phase == 'enter':
            enter_sculpt('A')
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
            exercise()
            return finish()
        return .15
    except BaseException:
        return finish(traceback.format_exc())


bpy.app.timers.register(tick, first_interval=.2, persistent=True)
