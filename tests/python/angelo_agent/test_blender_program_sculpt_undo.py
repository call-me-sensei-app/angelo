# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed commits preserve an artist's Sculpt mode, geometry and individual Undo steps.

Factory GUI only, no provider or rendered image. The actual Controller admits
and approves a ToolCall, BlenderExecutor stages its real ProgramJob, and the
production commit UI operator owns preparation and native Undo publication.
This mechanics fixture configures no provider or checkpoint factory; it does
not establish provider/authorization UI or pre-request checkpoint acceptance.
The checkpoint companion remains a separate blocker. Mesh strokes use the
compiled native brush operator.
"""

import array
import contextlib
import ctypes
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
import traceback
from collections import Counter
from types import SimpleNamespace

import bpy

assert '--factory-startup' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts/addons_core'), str(Path(__file__).resolve().parent)]
from angelo_agent import agent_workspace_ui, native_programs as native, program_undo, scene_queries, ui
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.controller import Controller, ToolCall, ToolDispatchBudget
from program_qa_helpers import args_for, move, no_stages

OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-program-sculpt-undo'))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {'checks': {}, 'paid_requests': 0, 'measurements': {}, 'cases': {},
          'fixture_scope': 'Actual Controller/ToolCall/BlenderExecutor/ProgramJob and production commit UI; no provider or checkpoint factory',
          'checkpoint_companion': 'Separate blocker; pre-request checkpoint capture is not exercised here'}
ACTOR, TARGET = 'Artist Sphere', 'Agent Target'


def check(name, value):
    REPORT['checks'][name] = bool(value)
    assert value, name


def history():
    """Read the actual native stack; never infer its shape from operator calls."""
    path = OUT / 'history.txt'
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
    result = []
    for line in path.read_text().splitlines():
        match = re.match(r"^\[(.{4})\]\s+(\d+)\s+\{[^}]+\}\s+type='([^']+)', name='(.*)'", line)
        if match:
            flags, index, kind, name = match.groups()
            result.append({'index': int(index), 'type': kind, 'name': name,
                           'active': flags[0] == '*', 'skip': flags[3] == 'S'})
    assert result
    return result


def active_index():
    return next(step['index'] for step in history() if step['active'])


def snapshot():
    obj = bpy.data.objects[ACTOR]
    if obj.use_dynamic_topology_sculpting:
        # Test-only native iteration observes the actual editable BMesh. It does not flush,
        # evaluate, assign element indices, create layers, or change the Undo history.
        observed = program_undo.native_bridge()._test_sculpt_surface()
        values = array.array('f', (v for co in observed['positions'] for v in co))
        topology = tuple(tuple(face) for face in observed['topology'])
        hidden, masks = tuple(observed['hidden']), tuple(observed['masks'])
    else:
        mesh = obj.angelo_sculpt_mesh() if obj.modifiers else obj.data
        try:
            values = array.array('f', [0.]) * (len(mesh.vertices) * 3)
            mesh.vertices.foreach_get('co', values)
            topology = tuple(tuple(poly.vertices) for poly in mesh.polygons)
            hidden = mesh.attributes.get('.hide_vert')
            masks = mesh.attributes.get('.sculpt_mask')
            hidden = tuple(bool(item.value) for item in hidden.data) if hidden else (False,) * len(mesh.vertices)
            masks = tuple(float(item.value) for item in masks.data) if masks else (0.,) * len(mesh.vertices)
        finally:
            if obj.modifiers:
                obj.to_mesh_clear()
    view = bpy.context.window.view_layer
    brush = bpy.context.scene.tool_settings.sculpt.brush
    return {'positions': values, 'mesh': hashlib.sha256(values.tobytes()).hexdigest(),
            'topology': topology,
            'hidden': hidden, 'masks': masks,
            'target_x': bpy.data.objects[TARGET].location.x, 'mode': obj.mode,
            'active': view.objects.active.name, 'selection': sorted(o.name for o in view.objects if o.select_get()),
            'brush': brush.name if brush else None, 'dyntopo': obj.use_dynamic_topology_sculpting}


def same_geometry(a, b, tolerance=0.):
    before, after = a['positions'], b['positions']
    # Incremental BMesh Undo may enumerate the same faces/cyclic first corners differently.
    # Preserve winding and vertex connectivity; never sort the vertices within a face.
    def oriented_faces(topology):
        return sorted(min(face[i:] + face[:i] for i in range(len(face)))
                      for face in map(tuple, topology))
    return (oriented_faces(a['topology']) == oriented_faces(b['topology']) and len(before) == len(after) and
            all(abs(x-y) <= tolerance for x, y in zip(before, after)))


def same_artist(a, b, tolerance=0.):
    return same_geometry(a, b, tolerance) and all(a[key] == b[key] for key in
        ('mode', 'active', 'selection', 'brush', 'dyntopo', 'hidden', 'masks'))


def compact(value):
    return {key: item for key, item in value.items() if key not in ('positions', 'topology', 'hidden', 'masks', 'grid_inventory')}


def fixture(multires=False, undo_steps=64, dense=False, setup=None, capture_snapshot=True):
    native.cleanup_all()
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=256 if dense else 32, ring_count=128 if dense else 16)
    obj = bpy.context.object
    obj.name = ACTOR
    target = bpy.data.objects.new(TARGET, None)
    bpy.context.scene.collection.objects.link(target)
    if setup is not None:
        setup(obj, target)  # Fixture construction only, before Multires and Sculpt entry.
    if multires:
        modifier = obj.modifiers.new('Artist Multires', 'MULTIRES')
        bpy.ops.object.multires_subdivide(modifier=modifier.name, mode='CATMULL_CLARK')
    bpy.context.view_layer.update()
    bpy.context.preferences.edit.undo_steps = undo_steps
    bpy.ops.ed.undo_push(message='Sculpt commit fixture')
    bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='SCULPT')
    return snapshot() if capture_snapshot else None


def stroke(position=(0., -1., .07), radius=1.):
    obj = bpy.data.objects[ACTOR]
    hit, co, *_ = obj.angelo_sculpt_query(list(position), distance=3.)
    assert hit
    point = {'name': 'fixture', 'location': list(co), 'pressure': 1., 'size': 1., 'is_start': True,
             'x_tilt': 0., 'y_tilt': 0., 'mouse': [0., 0.], 'mouse_event': [0., 0.], 'time': 0.}
    step = {'radius': radius, 'surface_normal': list(position), 'symmetry': 'NONE', 'front_faces_only': False,
            'mode': 'NORMAL', 'falloff': 'SMOOTH', 'automasking': [], 'stroke': [point],
            'brush_type': 'DRAW', 'strength': .12}
    assert bpy.ops.sculpt.angelo_sculpt_program('EXEC_DEFAULT', True, program=json.dumps([step])) == {'FINISHED'}
    return snapshot()


def commit(x=5., refuse=False, after_apply=None, *, arguments=None):
    scene_queries.invalidate()
    directory = tempfile.mkdtemp(prefix='commit-controller-', dir=OUT)
    controller = Controller(directory)
    session = controller.state['id']
    document = ui.document_key()
    controller.set_document(document)
    controller.busy = True
    controller.state['options']['approval'] = 'ASK'
    executor = BlenderExecutor(bpy.context.window)
    # Observation only: no controller lifecycle or native job methods are stubbed.
    host = SimpleNamespace(result=None, commits=0, responses=[])
    def delivered(result):
        host.result = result
        host.responses.append(result)
    job, original_commit = None, None
    try:
        controller.request_tool('program', 'angelo_scene_program',
            move(bpy.context.window, bpy.data.objects[TARGET], (x, 0, 0)) if arguments is None else arguments, delivered)
        call = controller.pending.get(session + '/program')
        assert isinstance(call, ToolCall), host.responses
        controller.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
        assert call.approval and not call.authorized and not host.responses, host.responses
        controller.approve_tool(call, executor, document)  # Fixture authorizes this exact reviewed program.
        for _ in range(400):
            controller.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
            job = controller.native_jobs.get(call.id)
            if host.responses or job is not None and job.ready_to_commit:
                break
        assert job is not None and job.ready_to_commit and not host.responses, host.responses
        assert job.invocation_id == call.id and job.owner == ('chat', session)
        original_commit = job.backend.commit
        def observed_commit(stage):
            host.commits += 1
            result = original_commit(stage)
            if after_apply is not None:
                after_apply()  # Actual backend transfer finished; native publication still follows.
            return result
        job.backend.commit = observed_commit
        if refuse:
            # Exercise production preapply refusal, before native begin can alter history.
            controller.state['options']['approval'] = 'READ_ONLY'
        ui._controllers[session] = controller
        started = time.perf_counter()
        try:
            outcome = bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True, call_id=call.id, session_id=session)
        except RuntimeError:
            if not (host.result or {}).get('error'):
                raise
            outcome = {'CANCELLED'}
        elapsed = (time.perf_counter() - started) * 1000
        assert len(host.responses) == 1 and call.id not in controller.pending, host.responses
        assert call.native_preparation is None and native.WRITER.owner is not job
    finally:
        if original_commit is not None:
            job.backend.commit = original_commit
        ui._controllers.pop(session, None)
        controller.busy = False
        controller.close()
    if not refuse and program_undo.native_bridge() is not None:
        assert outcome == {'FINISHED'} and host.result.get('undoable') is True, host.result
        if arguments is None:
            assert bpy.data.objects[TARGET].location.x == x
    return host, outcome, elapsed


class OracleInconclusive(RuntimeError):
    """An observation/mapping failure is never evidence of brush deformation."""


_EVIDENCE_BYTES = 0
_GRID_EXACT_FIELDS = ('object_uid', 'mesh_uid', 'scene_uid', 'view_layer', 'authority',
    'scene_original_session_uid', 'frame', 'ctime', 'canonical', 'viewport',
    'visibility_optimization', 'render_pipeline', 'editors_update', 'sync_writeback', 'debug_flags',
    'level', 'grid_size', 'grids_num', 'hidden_groups', 'hidden_group_size',
    'face_offsets', 'grid_to_face', 'positions', 'normals', 'masks', 'hidden',
    'dirty_coords', 'dirty_hidden', 'pending_normal_faces', 'session_needs_flush')


def record_phase(prefix, phase, value):
    """Persist complete bounded facts before assertions as deterministic gzip JSON."""
    if prefix is None:
        return
    global _EVIDENCE_BYTES
    def plain(item):
        if isinstance(item, bytes):
            return {'encoding': 'hex', 'bytes': len(item), 'sha256': hashlib.sha256(item).hexdigest(), 'data': item.hex()}
        if isinstance(item, array.array):
            return [plain(child) for child in item]
        if isinstance(item, dict):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [plain(child) for child in item]
        if isinstance(item, float) and not math.isfinite(item):
            return {'nonfinite': repr(item)}
        return item
    case = REPORT['cases'].setdefault(prefix, {'phases': []})
    path = OUT / (prefix + '-' + phase + '.json.gz')
    try:
        steps = history()
    except Exception as error:
        steps = {'observation_error_type': type(error).__name__}
    payload = {'format_version': 1, 'case': prefix, 'phase': phase, 'capture_complete': True,
               'mask_hide_scope': value.get('mask_hide_scope', 'Default/absent point masks and vertex/grid hide; coarse face hide is not observed') if isinstance(value, dict) else 'Unspecified',
               'observed': plain(value), 'history': steps}
    encoded = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode()
    stored = gzip.compress(encoded, compresslevel=1, mtime=0) if len(encoded) <= 32 * 1024 * 1024 else None
    if stored is None or _EVIDENCE_BYTES + len(stored) > 128 * 1024 * 1024:
        payload = {'capture_complete': False, 'case': prefix, 'phase': phase,
                   'required_uncompressed_bytes': len(encoded),
                   'required_stored_bytes': len(stored) if stored is not None else None,
                   'reason': 'Fixture evidence budget exceeded; comparison is inconclusive'}
        path = OUT / (prefix + '-' + phase + '-incomplete.json')
        path.write_text(json.dumps(payload))
        case.setdefault('phases', []).append({'phase': phase, 'path': str(path), 'capture_complete': False})
        raise OracleInconclusive(payload['reason'])
    path.write_bytes(stored)
    _EVIDENCE_BYTES += len(stored)
    REPORT['stored_observation_bytes'] = _EVIDENCE_BYTES
    case.setdefault('phases', []).append({'phase': phase, 'path': str(path), 'encoding': 'gzip-json',
        'uncompressed_bytes': len(encoded), 'uncompressed_sha256': hashlib.sha256(encoded).hexdigest(),
        'stored_bytes': len(stored), 'stored_sha256': hashlib.sha256(stored).hexdigest(), 'capture_complete': True})
    if isinstance(steps, dict):
        raise OracleInconclusive('The raw surface was captured but native history observation failed')


def _native_array(code, data, byte_order):
    values = array.array(code)
    if values.itemsize != 4 or len(data) % 4:
        raise OracleInconclusive('Unexpected native grid scalar layout')
    values.frombytes(data)
    if byte_order != sys.byteorder:
        values.byteswap()
    return values


def _artist_state(prefix=None, phase='observation'):
    """Read raw live state. Multires never enters the normalizing Mesh extractor."""
    actor = bpy.data.objects[ACTOR]
    multires = any(modifier.type == 'MULTIRES' and modifier.total_levels for modifier in actor.modifiers)
    extra = {}
    if multires:
        bridge = program_undo.native_bridge()
        inventory = bridge._test_sculpt_grid_surfaces()
        record_phase(prefix, phase + '-grid-inventory', inventory)
        repeat = bridge._test_sculpt_grid_surfaces()
        record_phase(prefix, phase + '-grid-repeat', repeat)
        if inventory != repeat:
            raise OracleInconclusive('The immutable grid observer changed raw state between consecutive reads')
        matches = [index for index, owner in enumerate(inventory['surfaces'])
                   if owner['object_uid'] == actor.session_uid and owner['mesh_uid'] == actor.data.session_uid
                   and owner['scene_uid'] == bpy.context.scene.session_uid
                   and owner['view_layer'] == bpy.context.view_layer.name
                   and owner['canonical'] and owner['viewport'] and owner['authority']]
        if (len(matches) != 1 or sum(owner['object_uid'] == actor.session_uid for owner in inventory['surfaces']) != 1):
            raise OracleInconclusive('The fixture did not find exactly one current canonical Sculpt grid authority')
        owner = inventory['surfaces'][matches[0]]
        side, count = owner['grid_size'], owner['grids_num']
        values = _native_array('f', owner['positions'], inventory['byte_order'])
        offsets = _native_array('i', owner['face_offsets'], inventory['byte_order'])
        grid_faces = _native_array('i', owner['grid_to_face'], inventory['byte_order'])
        samples = side * side * count
        if (side < 2 or len(values) != samples * 3 or len(owner['normals']) != samples * 12
                or len(grid_faces) != count or not offsets or offsets[0] != 0 or offsets[-1] != count
                or any(offsets[i] > offsets[i + 1] for i in range(len(offsets) - 1))
                or any(not 0 <= face < len(offsets) - 1 or not offsets[face] <= index < offsets[face + 1]
                       for index, face in enumerate(grid_faces))):
            raise OracleInconclusive('Raw grid dimensions or stable face mapping are inconsistent')
        masks = tuple(_native_array('f', owner['masks'], inventory['byte_order'])) if owner['masks'] else (0.,) * samples
        hidden = tuple(bool(value) for value in owner['hidden']) if owner['hidden'] else (False,) * samples
        topology = tuple((v, v + side, v + side + 1, v + 1)
                         for grid in range(count) for y in range(side - 1) for x in range(side - 1)
                         for v in (grid * side * side + y * side + x,))
        extra = {'surface_kind': 'grids', 'grid_inventory': inventory, 'grid_owner_index': matches[0]}
    elif actor.use_dynamic_topology_sculpting:
        raw = program_undo.native_bridge()._test_sculpt_surface()
        record_phase(prefix, phase + '-bmesh', raw)
        values = array.array('f', (value for coordinate in raw['positions'] for value in coordinate))
        topology, masks, hidden = tuple(map(tuple, raw['topology'])), tuple(raw['masks']), tuple(raw['hidden'])
        extra = {'surface_kind': 'dyntopo'}
    else:
        mesh = actor.data
        values = array.array('f', [0.]) * (len(mesh.vertices) * 3)
        mesh.vertices.foreach_get('co', values)
        topology = tuple(tuple(poly.vertices) for poly in mesh.polygons)
        mask, hide = mesh.attributes.get('.sculpt_mask'), mesh.attributes.get('.hide_vert')
        masks = tuple(item.value for item in mask.data) if mask else (0.,) * len(mesh.vertices)
        hidden = tuple(bool(item.value) for item in hide.data) if hide else (False,) * len(mesh.vertices)
        extra = {'surface_kind': 'mesh'}
    view, layer = bpy.context.space_data.region_3d, bpy.context.view_layer
    sculpt = bpy.context.scene.tool_settings.sculpt
    brush = sculpt.brush
    state = dict(extra, positions=values, topology=topology, masks=masks, hidden=hidden,
        mesh=hashlib.sha256(values.tobytes()).hexdigest(), vertex_count=len(values) // 3, face_count=len(topology),
        target_x=bpy.data.objects[TARGET].location.x, mode=actor.mode, active=layer.objects.active.name,
        selection=sorted(obj.name for obj in layer.objects if obj.select_get()), brush=brush.name if brush else None,
        dyntopo=actor.use_dynamic_topology_sculpting, world_matrix=tuple(tuple(row) for row in actor.matrix_world),
        local_transform=(tuple(actor.location), tuple(actor.rotation_euler), tuple(actor.scale)),
        view=(tuple(view.view_rotation), tuple(view.view_location), view.view_distance, view.view_perspective),
        brush_settings=tuple(getattr(brush, key, None) for key in ('strength', 'size', 'use_locked_size', 'unprojected_radius')),
        sculpt_settings=tuple(getattr(sculpt, key, None) for key in ('use_symmetry_x', 'use_symmetry_y', 'use_symmetry_z',
                                                                  'detail_type_method', 'detail_refine_method')),
        parent=actor.parent.name if actor.parent else None,
        materials=tuple((slot.link, slot.material.name, tuple(slot.material.diffuse_color), slot.material.use_nodes)
                        if slot.material else (slot.link, None) for slot in actor.material_slots),
        modifiers=tuple((modifier.name, modifier.type, modifier.show_viewport,
                         tuple(getattr(modifier, key, None) for key in ('levels', 'sculpt_levels', 'render_levels', 'total_levels')))
                        for modifier in actor.modifiers),
        face_hide_coverage='NOT_OBSERVED', mask_hide_scope='Default/absent point masks and vertex/grid hiding')
    state.update(nonzero_mask_samples=sum(value != 0. for value in masks), hidden_samples=sum(hidden))
    record_phase(prefix, phase, state)
    return state


def artist_state(prefix=None, phase='observation'):
    try:
        return _artist_state(prefix, phase)
    except Exception as error:
        try:
            record_phase(prefix, phase + '-observation-failed', {'exception_type': type(error).__name__})
        except Exception:
            pass  # Keep the original failure; budget failures already save an explicit incomplete receipt.
        raise


def _near(a, b, tolerance):
    return len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _surface_points(state, world=False, delta=(0., 0., 0.)):
    rows = [tuple(state['positions'][i:i + 3]) for i in range(0, len(state['positions']), 3)]
    if any(len(row) != 3 or any(not math.isfinite(value) for value in row) for row in rows):
        raise OracleInconclusive('Nonfinite or malformed raw surface coordinates')
    if world:
        matrix = state['world_matrix']
        rows = [tuple(sum(matrix[axis][i] * co[i] for i in range(3)) + matrix[axis][3]
                      for axis in range(3)) for co in rows]
    rows = [tuple(co[i] + delta[i] for i in range(3)) for co in rows]
    if any(any(not math.isfinite(value) for value in row) for row in rows):
        raise OracleInconclusive('Nonfinite transformed surface coordinates')
    return rows


def _oriented_faces(state, mapping):
    faces = []
    for face in state['topology']:
        if not face or any(index < 0 or index >= len(mapping) for index in face):
            raise OracleInconclusive('Invalid indexed surface topology')
        row = tuple(mapping[index] for index in face)
        faces.append(min(row[offset:] + row[:offset] for offset in range(len(row))))
    return Counter(faces)


def same_surface(before, after, *, world=False, delta=(0., 0., 0.), tolerance=2e-6):
    """One globally consistent vertex mapping; ambiguity never certifies a change."""
    kind = before['surface_kind']
    if kind != after['surface_kind']:
        raise OracleInconclusive('The surface representation changed during the fixture')
    a, b = _surface_points(before, world, delta), _surface_points(after, world)
    if len(a) != len(b) or len(before['topology']) != len(after['topology']):
        return False
    if any(len(state[key]) != len(points) for state, points in ((before, a), (after, b)) for key in ('hidden', 'masks')):
        raise OracleInconclusive('Surface attribute cardinality does not match its vertices')
    def match(i, j):
        return (_near(a[i], b[j], tolerance) and before['hidden'][i] == after['hidden'][j]
                and abs(before['masks'][i] - after['masks'][j]) <= tolerance)
    if kind == 'dyntopo':
        cell = lambda co: tuple(math.floor(value / tolerance) for value in co)
        buckets = {}
        for j, co in enumerate(b):
            buckets.setdefault(cell(co), []).append(j)
        mapping = []
        for i, co in enumerate(a):
            x, y, z = cell(co)
            candidates = [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                          for j in buckets.get((x + dx, y + dy, z + dz), ()) if match(i, j)]
            if len(candidates) > 1:
                raise OracleInconclusive('Dyntopo vertex correspondence is ambiguous within the fixture tolerance')
            if not candidates:
                return False
            mapping.append(candidates[0])
        if len(set(mapping)) != len(mapping):
            return False  # Every row had a unique candidate, but no bijection exists.
    else:
        mapping = list(range(len(a)))  # Mesh and CCG retain their explicit indexed layout.
        if not all(match(i, i) for i in mapping):
            return False
    if kind == 'grids':
        left = before['grid_inventory']['surfaces'][before['grid_owner_index']]
        right = after['grid_inventory']['surfaces'][after['grid_owner_index']]
        if (any(left[key] != right[key] for key in _GRID_EXACT_FIELDS)
                or before['grid_inventory']['main_flush_needed'] != after['grid_inventory']['main_flush_needed']):
            return False
    return _oriented_faces(before, mapping) == _oriented_faces(after, list(range(len(b))))


def surface_deformed(before, after, *, world=False, tolerance=2e-6):
    """Coordinate extents prove geometry changed; masks/topology mismatch alone cannot."""
    a, b = _surface_points(before, world), _surface_points(after, world)
    if not a or not b:
        raise OracleInconclusive('A brush deformation needs two nonempty observed surfaces')
    bounds = lambda rows: tuple(value for axis in range(3)
                                for value in (min(co[axis] for co in rows), max(co[axis] for co in rows)))
    return not _near(bounds(a), bounds(b), tolerance)


def same_artist_state(before, after):
    return (same_surface(before, after) and same_surface(before, after, world=True)
            and _near(tuple(value for row in before['world_matrix'] for value in row),
                      tuple(value for row in after['world_matrix'] for value in row), 1e-6)
            and all(before[key] == after[key] for key in ('mode', 'active', 'selection', 'brush', 'dyntopo',
                'local_transform', 'parent', 'materials', 'modifiers', 'target_x', 'view', 'brush_settings', 'sculpt_settings')))


def ordinary_artist_stroke(prefix=None, phase='artist-stroke'):
    """The stock artist brush path, including BMesh; no Angelo Dyntopo bypass."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector
    actor = bpy.data.objects[ACTOR]
    brush = bpy.context.scene.tool_settings.sculpt.brush
    assert brush is not None, 'The factory Sculpt fixture needs an active artist brush'
    position = actor.matrix_world @ Vector((0., -1., 0.))
    mouse = location_3d_to_region_2d(bpy.context.region, bpy.context.space_data.region_3d, position)
    assert mouse is not None
    sample = {'name': 'artist continuation', 'location': list(position), 'pressure': 1., 'size': 1.,
              'is_start': True, 'x_tilt': 0., 'y_tilt': 0., 'mouse': list(mouse), 'mouse_event': list(mouse), 'time': 0.}
    try:
        outcome = bpy.ops.sculpt.brush_stroke('EXEC_DEFAULT', True, stroke=[sample], mode='NORMAL')
    finally:
        state = artist_state(prefix, phase)
    assert outcome == {'FINISHED'}
    assert bpy.context.scene.tool_settings.sculpt.brush == brush
    return state


def active_fixture(mode, setup=None, *, prefix=None):
    from mathutils import Quaternion
    fixture(multires=mode == 'multires', setup=setup, capture_snapshot=False)
    artist_state(prefix, 'after-sculpt-entry')
    if mode == 'dyntopo':
        try:
            outcome = bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT', True)
        finally:
            artist_state(prefix, 'after-dyntopo-entry')
        assert outcome == {'FINISHED'}
    view = bpy.context.space_data.region_3d
    view.view_rotation = Quaternion((1, 0, 0), math.pi / 2)
    view.view_location, view.view_distance, view.view_perspective = (0, 0, 0), 4., 'ORTHO'
    view.update()
    before = artist_state(prefix, 'before-seed-stroke')
    after = ordinary_artist_stroke(prefix, 'after-seed-stroke')
    check(mode + '_active_fixture_has_real_artist_stroke', surface_deformed(before, after))
    return after


def observed_history_step(prefix, phase, *, redo=False):
    try:
        if redo:
            bpy.ops.ed.redo()
        else:
            bpy.ops.ed.undo()
    finally:
        state = artist_state(prefix, phase)
    return state


def brush_continuation(prefix, before):
    after = ordinary_artist_stroke(prefix, 'continued-brush')
    check(prefix + '_continued_real_brush', surface_deformed(before, after))
    undone = observed_history_step(prefix, 'continued-brush-undo')
    check(prefix + '_continued_brush_one_undo', same_artist_state(before, undone))
    redone = observed_history_step(prefix, 'continued-brush-redo', redo=True)
    check(prefix + '_continued_brush_one_redo', same_artist_state(after, redone))


def active_target_admission_cases():
    """Baseline unsupported active-target requests stay unchanged; no positive-edit claim."""
    from angelo_agent.scene_read_values import SceneQueryError
    operations = (
        ('transform', {'location': [.25, 0., 0.]}),
        ('material_principled', {'name': 'Active Sculpt Material', 'inputs': {'Base Color': [.2, .4, .6, 1.], 'Roughness': .23}}),
        ('mesh_subdivide', {'face_indices': [0], 'cuts': 1}),
        ('modifier_add', {'type': 'DISPLACE', 'name': 'Active Sculpt Displace', 'settings': {'strength': .1, 'mid_level': 0.}}),
    )
    REPORT['active_target_positive_coverage'] = 'BLOCKED_PREEXISTING_SCULPT_REVISION_AND_OBJECT_MODE_ADMISSION'
    REPORT['coverage_kind'] = 'Unchanged-refusal controls only; these are not positive active-target edits'
    for mode in ('mesh', 'multires', 'dyntopo'):
        for kind, parameters in operations:
            prefix = mode + '_active_' + kind
            REPORT['cases'][prefix] = {'coverage': 'pre-existing refusal', 'phases': [],
                                      'mask_hide_scope': 'Default/absent point masks and vertex/grid hiding; coarse face hide uncovered'}
            observation = {}
            def observe_object_mode(actor, _target):
                bpy.context.view_layer.update()
                scene_queries.invalidate()
                observation.update(handle=scene_queries.object_handle(actor),
                                   revision=scene_queries.object_revision(bpy.context.window, actor))
            active_fixture(mode, observe_object_mode, prefix=prefix)
            redo_state = ordinary_artist_stroke(prefix, 'prior-artist-redo-state')
            before = observed_history_step(prefix, 'before-admission')
            steps = history()
            scene_queries.invalidate()
            try:
                scene_queries.object_revision(bpy.context.window, bpy.data.objects[ACTOR])
            except SceneQueryError as error:
                artist_state(prefix, 'after-revision-observation')
                check(prefix + '_current_exact_revision_unavailable',
                      'live_sculpt_surface_requires_exact_sculpt_observation' in str(error))
            else:
                artist_state(prefix, 'after-revision-observation')
                check(prefix + '_current_exact_revision_unavailable', False)
            arguments = {'mode': 'APPLY', 'read_set': [], 'write_set': [observation],
                         'operations': [{'kind': kind, 'target': observation['handle'], 'parameters': parameters}],
                         'expected_checks': []}
            controller = Controller(tempfile.mkdtemp(prefix='active-refusal-', dir=OUT))
            controller.set_document(ui.document_key())
            controller.busy = True
            controller.state['options']['approval'] = 'ASK'
            answers = []
            try:
                controller.request_tool('active', 'angelo_scene_program', arguments, answers.append)
                call = controller.pending.get(controller.state['id'] + '/active')
                assert isinstance(call, ToolCall), answers
                try:
                    controller.pump_tools(BlenderExecutor(bpy.context.window), controller.document,
                                          budget=ToolDispatchBudget(seconds=60))
                finally:
                    REPORT['cases'][prefix]['admission_results'] = answers
                    after_refusal = artist_state(prefix, 'after-admission')
                check(prefix + '_baseline_mode_refusal', len(answers) == 1
                      and answers[0].get('error_code') == 'UNSUPPORTED'
                      and 'Switch all declared program inputs to Object Mode before staging' in answers[0].get('error', ''))
                check(prefix + '_no_approval_stage_or_mutation', not call.approved and not call.authorized
                      and not call.mutation_started and not controller.native_jobs and native.WRITER.owner is None)
            finally:
                controller.busy = False
                controller.close()
            check(prefix + '_artist_and_history_unchanged', same_artist_state(before, after_refusal) and history() == steps)
            redone = observed_history_step(prefix, 'prior-artist-redone', redo=True)
            check(prefix + '_prior_artist_redo_preserved', same_artist_state(redo_state, redone))
            brush_continuation(prefix, redone)
            REPORT['cases'][prefix].update({'result': answers[0],
                'vertex_count': before['vertex_count'], 'face_count': before['face_count'],
                'observation_scope': 'Legitimate earlier Object-Mode revision; no exact current Sculpt program revision exists'})


def indirect_sculpt_cases():
    """Admitted Object-Mode owners produce actual indirect effects on the Sculpt user."""
    owner_name = 'Indirect Material Owner'
    wanted_color = (.15, .35, .65, 1.)
    REPORT['coverage_kind'] = 'Admitted indirect Sculpt effects; direct active-target admission is unchanged'
    for mode in ('mesh', 'multires', 'dyntopo'):
        for effect in ('parent_transform', 'shared_material_color'):
            prefix = mode + '_indirect_' + effect
            REPORT['cases'][prefix] = {'coverage': 'admitted indirect effect', 'phases': [],
                                      'mask_hide_scope': 'Default/absent point masks and vertex/grid hiding; coarse face hide uncovered'}
            def setup(actor, parent):
                if effect == 'parent_transform':
                    actor.parent = parent
                else:
                    mesh = bpy.data.meshes.new('Indirect Owner Mesh')
                    mesh.from_pydata([(3., 0., 0.), (3., 1., 0.), (3., 0., 1.)], [], [(0, 1, 2)])
                    owner = bpy.data.objects.new(owner_name, mesh)
                    bpy.context.scene.collection.objects.link(owner)
                    material = bpy.data.materials.new('Shared Sculpt Diffuse Color')
                    material.diffuse_color = (.7, .6, .5, 1.)
                    material.use_nodes = False
                    actor.data.materials.append(material)
                    owner.data.materials.append(material)
            before = active_fixture(mode, setup, prefix=prefix)
            steps = history()
            if effect == 'parent_transform':
                owner = bpy.data.objects[TARGET]
                operation = {'kind': 'transform', 'target': scene_queries.object_handle(owner),
                             'parameters': {'location': [.25, 0., 0.]}}
            else:
                owner = bpy.data.objects[owner_name]
                # The Sculpt actor shares the material: an edit for all its users.
                operation = {'kind': 'property_set', 'target': scene_queries.object_handle(owner),
                             'parameters': {'values': {'active_material.diffuse_color': list(wanted_color)},
                                            'shared': 'ALL_USERS'}}
            check(prefix + '_actual_owner_is_object_mode', owner.mode == 'OBJECT' and bpy.data.objects[ACTOR].mode == 'SCULPT')
            scene_queries.invalidate()
            try:
                host, outcome, elapsed = commit(arguments=args_for(bpy.context.window, [operation], writes=[owner]))
                REPORT['cases'][prefix]['commit_result'] = host.result
            finally:
                after = artist_state(prefix, 'after-commit')
            check(prefix + '_real_controller_commit', outcome == {'FINISHED'} and host.commits == 1
                  and host.result.get('undoable') is True)
            check(prefix + '_artist_local_surface_and_context', same_surface(before, after)
                  and all(before[key] == after[key] for key in ('mode', 'active', 'selection', 'brush', 'dyntopo',
                      'local_transform', 'parent', 'modifiers', 'view', 'brush_settings', 'sculpt_settings')))
            if effect == 'parent_transform':
                expected_matrix = tuple(tuple(value + (.25 if row == 0 and column == 3 else 0.)
                                              for column, value in enumerate(values))
                                        for row, values in enumerate(before['world_matrix']))
                check(prefix + '_visible_world_translation', same_surface(before, after, world=True, delta=(.25, 0., 0.))
                      and surface_deformed(before, after, world=True) and before['materials'] == after['materials']
                      and _near(tuple(value for row in expected_matrix for value in row),
                                tuple(value for row in after['world_matrix'] for value in row), 1e-6))
            else:
                actor, owner = bpy.data.objects[ACTOR], bpy.data.objects[owner_name]
                check(prefix + '_actual_shared_diffuse_color', actor.active_material == owner.active_material
                      and _near(tuple(actor.active_material.diffuse_color), wanted_color, 1e-6)
                      and not actor.active_material.use_nodes and before['materials'] != after['materials']
                      and same_surface(before, after, world=True))
            check(prefix + '_single_visible_commit_step', sum(not step['skip'] for step in history()) ==
                  sum(not step['skip'] for step in steps) + 1)
            undone = observed_history_step(prefix, 'commit-undo')
            check(prefix + '_one_commit_undo', same_artist_state(before, undone))
            if effect == 'shared_material_color':
                check(prefix + '_undo_preserves_actual_shared_material',
                      bpy.data.objects[ACTOR].active_material == bpy.data.objects[owner_name].active_material)
            redone = observed_history_step(prefix, 'commit-redo', redo=True)
            check(prefix + '_one_commit_redo', same_artist_state(after, redone))
            if effect == 'shared_material_color':
                check(prefix + '_redo_preserves_actual_shared_material',
                      bpy.data.objects[ACTOR].active_material == bpy.data.objects[owner_name].active_material)
            brush_continuation(prefix, after)
            REPORT['cases'][prefix].update({'commit_ms': elapsed,
                'before': compact(before), 'after': compact(after),
                'material_oracle': 'Actual shared Material.diffuse_color and pointer equality; no Principled or rendered-pixel claim'})


def sequence(multires=False, no_stroke=False):
    name = 'multires' if multires else 'entered' if no_stroke else 'mesh'
    tolerance = 2e-6 if multires else 0.
    initial = fixture(multires)
    previous = initial if no_stroke else stroke()
    before = previous if no_stroke else stroke()
    steps = history()
    host, result, elapsed = commit()
    after = snapshot()
    check(name+'_actual_commit', result == {'FINISHED'} and host.result.get('undoable') is True and after['target_x'] == 5.)
    check(name+'_artist_unchanged', same_artist(before, after, tolerance))
    check(name+'_one_added_visible_step', sum(not s['skip'] for s in history()) ==
          sum(not s['skip'] for s in steps)+1 and not history()[-1]['skip'])
    bpy.ops.ed.undo()
    undone = snapshot()
    check(name+'_one_undo', undone['target_x'] == 0. and same_artist(before, undone, tolerance))
    bpy.ops.ed.redo()
    check(name+'_one_redo', snapshot()['target_x'] == 5. and same_artist(after, snapshot(), tolerance))
    if not no_stroke:
        following = stroke()
        check(name+'_can_keep_sculpting', not same_geometry(following, after, tolerance))
        bpy.ops.ed.undo()
        check(name+'_undo_next_stroke', same_artist(after, snapshot(), tolerance) and snapshot()['target_x'] == 5.)
        bpy.ops.ed.undo()
        check(name+'_undo_commit_between_strokes', snapshot()['target_x'] == 0. and same_artist(before, snapshot(), tolerance))
        bpy.ops.ed.undo()
        check(name+'_previous_stroke_is_next_step', same_artist(previous, snapshot(), tolerance) and snapshot()['target_x'] == 0.)
        bpy.ops.ed.redo()
        check(name+'_redo_previous_stroke', same_artist(before, snapshot(), tolerance))
        bpy.ops.ed.redo()
        check(name+'_redo_commit_again', snapshot()['target_x'] == 5. and same_artist(after, snapshot(), tolerance))
    REPORT['cases'][name] = {'before': compact(before), 'after': compact(after), 'undo': compact(undone), 'commit_ms': elapsed}


def history_cases():
    fixture()
    first = stroke()
    first_index = active_index()
    commit(5.)
    second = stroke()
    commit(9.)
    final_index = active_index()
    bpy.ops.ed.undo_history(item=first_index)
    check('history_jump_across_two_commits', snapshot()['target_x'] == 0. and same_artist(first, snapshot()))
    bpy.ops.ed.undo_history(item=final_index)
    check('history_redo_across_two_commits', snapshot()['target_x'] == 9. and same_artist(second, snapshot()))
    bpy.ops.ed.undo()
    before = history()
    host, result, _ = commit(17., refuse=True)
    check('refused_commit_keeps_redo_history', result == {'CANCELLED'} and history() == before and host.commits == 0)
    bpy.ops.ed.redo()
    check('previous_commit_still_redoes_after_refusal', snapshot()['target_x'] == 9. and same_artist(second, snapshot()))
    fixture(undo_steps=4, dense=True)
    stroke()
    for position in ((1., 0., 0.), (-1., 0., 0.), (0., 1., 0.), (0., 0., 1.), (0., 0., -1.), (0., -1., 0.)):
        before = stroke(position, .2)
    commit()
    bpy.ops.ed.undo()
    check('pruned_history_keeps_sculpt_baseline', snapshot()['target_x'] == 0. and same_artist(before, snapshot()))
    bpy.ops.ed.redo()
    check('pruned_history_redoes_commit', snapshot()['target_x'] == 5. and same_artist(before, snapshot()))


def visibility_cases():
    fixture(multires=True)
    obj = bpy.data.objects[ACTOR]
    bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT')
    for _ in range(2):
        bpy.ops.object.multires_subdivide(modifier='Artist Multires', mode='CATMULL_CLARK')
    bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='SCULPT')
    stroke()
    bpy.ops.view3d.view_axis(type='FRONT')
    region = bpy.context.region
    bpy.context.space_data.region_3d.view_distance = 5.
    bpy.context.space_data.region_3d.view_location = (0., 0., 0.)
    bpy.ops.paint.hide_show('EXEC_DEFAULT', True, action='HIDE', area='Inside',
        xmin=region.width//2+15, xmax=region.width//2+70,
        ymin=region.height//2-50, ymax=region.height//2+50)
    before = snapshot()
    grid_area = len(before['hidden']) // len(obj.data.loops)
    mixed = sum(0 < sum(before['hidden'][i:i+grid_area]) < grid_area
                for i in range(0, len(before['hidden']), grid_area))
    check('visibility_fixture_has_partial_grids', mixed > 0)
    commit()
    check('commit_keeps_fine_grid_hiding', snapshot()['hidden'] == before['hidden'])
    bpy.ops.ed.undo()
    check('undo_keeps_fine_grid_hiding', snapshot()['hidden'] == before['hidden'])
    bpy.ops.ed.redo()
    check('redo_keeps_fine_grid_hiding', snapshot()['hidden'] == before['hidden'])
    updates = []
    def retag_once(scene, depsgraph):
        actor = bpy.data.objects[ACTOR]
        if not updates:
            first_eval_x = actor.evaluated_get(depsgraph).matrix_world.translation.x
            actor.location.x += .125
            updates.append((first_eval_x, actor.location.x))
            actor.update_tag(refresh={'DATA'})
    bpy.app.handlers.depsgraph_update_post.append(retag_once)
    try:
        bpy.ops.ed.undo()
        bpy.context.view_layer.update()
        # The second native pass deliberately omits callbacks. Prove it ran by observing the
        # newly evaluated transform assigned only in the first pass's post-update callback.
        evaluated_x = bpy.data.objects[ACTOR].evaluated_get(
            bpy.context.evaluated_depsgraph_get()).matrix_world.translation.x
        check('post_callback_change_was_evaluated', len(updates) == 1 and
              updates[0][0] != updates[0][1] and abs(evaluated_x-updates[0][1]) < 1e-6)
        check('two_dependency_passes_keep_fine_hiding',
              snapshot()['hidden'] == before['hidden'])
        REPORT['cases']['two_dependency_passes'] = {'first_eval_x': updates[0][0],
            'callback_x': updates[0][1], 'final_eval_x': evaluated_x}
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(retag_once)
    # Real artist visibility edits must supersede the retained snapshot payload.
    bpy.ops.paint.hide_show_all('EXEC_DEFAULT', True, action='SHOW')
    shown = snapshot()
    bpy.data.objects[ACTOR].update_tag(refresh={'DATA'})
    bpy.context.view_layer.update()
    check('artist_show_supersedes_snapshot_hiding', not any(snapshot()['hidden']) and
          snapshot()['hidden'] == shown['hidden'])


def dyntopo_cases():
    fixture()
    bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT', True)
    bpy.ops.sculpt.symmetrize('EXEC_DEFAULT', True)
    before = snapshot()
    check('dyntopo_fixture_is_live', before['dyntopo'])
    commit()
    after = snapshot()
    bpy.ops.ed.undo()
    undone = snapshot()
    REPORT['cases']['dyntopo_first_undo'] = {'before': before, 'after': after, 'undo': undone}
    check('dyntopo_one_undo_retains_mode_and_geometry', undone['target_x'] == 0. and
          same_artist(before, undone))
    bpy.ops.ed.redo()
    check('dyntopo_one_redo_retains_mode_and_geometry', snapshot()['target_x'] == 5. and
          same_artist(after, snapshot()))
    bpy.ops.sculpt.symmetrize('EXEC_DEFAULT', True)
    continued = snapshot()
    check('dyntopo_can_continue_after_redo', continued['dyntopo'])
    bpy.ops.ed.undo()
    check('dyntopo_next_stroke_undo', same_artist(after, snapshot()))
    bpy.ops.ed.undo()
    check('dyntopo_prior_commit_undo', snapshot()['target_x'] == 0. and same_artist(before, snapshot()))
    bpy.ops.ed.undo()
    check('dyntopo_prior_artist_step_still_works', snapshot()['dyntopo'])
    bpy.ops.ed.redo()
    prior_redone = snapshot()
    REPORT['cases']['dyntopo_prior_artist_redo'] = prior_redone
    check('dyntopo_prior_artist_step_redoes', same_artist(before, prior_redone))
    bpy.ops.ed.redo()
    check('dyntopo_commit_redoes_again', snapshot()['target_x'] == 5. and same_artist(after, snapshot()))



def raw_mesh_state():
    """Observe stored Mesh data directly; never flush or evaluate its live BMesh."""
    mesh = bpy.data.objects[ACTOR].data
    fields = {'FLOAT': 'value', 'INT': 'value', 'INT8': 'value', 'BOOLEAN': 'value',
              'STRING': 'value', 'INT32_2D': 'value', 'QUATERNION': 'value',
              'FLOAT_VECTOR': 'vector', 'FLOAT2': 'vector', 'FLOAT4': 'vector',
              'FLOAT_COLOR': 'color', 'BYTE_COLOR': 'color'}
    attributes = {}
    items = 0
    for attribute in mesh.attributes:
        field = fields.get(attribute.data_type)
        if field is None:
            raise OracleInconclusive('Unsupported raw fixture attribute: ' + attribute.data_type)
        values = []
        items += len(attribute.data)
        if items > 65536:
            raise OracleInconclusive('Raw fixture attribute inventory exceeds its bound')
        for item in attribute.data:
            value = getattr(item, field)
            values.append(value if isinstance(value, (bool, int, float, str, bytes)) else tuple(value))
        attributes[attribute.name] = (attribute.data_type, attribute.domain, tuple(values))
    properties = {}
    for key in mesh.keys():
        value = mesh[key]
        if not isinstance(value, (bool, int, float, str, bytes)):
            raise OracleInconclusive('Unsupported raw fixture custom property: ' + key)
        properties[key] = value
    return {'positions': tuple(tuple(vertex.co) for vertex in mesh.vertices),
            'edges': tuple(tuple(edge.vertices) for edge in mesh.edges),
            'topology': tuple(tuple(face.vertices) for face in mesh.polygons),
            'corners': tuple((loop.vertex_index, loop.edge_index) for loop in mesh.loops),
            'face_metadata': tuple((face.material_index, face.use_smooth) for face in mesh.polygons),
            'attributes': attributes, 'attribute_order': tuple(attribute.name for attribute in mesh.attributes),
            'active_attribute_index': mesh.attributes.active_index,
            'active_color_name': mesh.color_attributes.active_color_name,
            'default_color_name': mesh.color_attributes.default_color_name,
            'name': mesh.name, 'custom_properties': properties,
            'materials': tuple(material.name if material else None for material in mesh.materials),
            'mask_hide_scope': 'Exact saved raw attribute inventory/values, including nonzero point masks and point hiding'}


def raw_metadata_setup(actor, _target):
    """Seed discriminating stored paint data before entering Sculpt or creating history."""
    mesh = actor.data
    mask = mesh.attributes.new('.sculpt_mask', 'FLOAT', 'POINT')
    hidden = mesh.attributes.new('.hide_vert', 'BOOLEAN', 'POINT')
    for index, (mask_value, hidden_value) in enumerate(zip(mask.data, hidden.data)):
        mask_value.value = .25 if index % 7 == 0 else 0.
        hidden_value.value = index % 17 == 0
    mesh['angelo_fixture_raw_metadata'] = 'manual-main-before-tagged-snapshot'


def untagged_memfile_case():
    """Cross back from a tagged snapshot into an older manual Main boundary."""
    for untracked_edit in (False, True):
        prefix = 'untagged_untracked' if untracked_edit else 'untagged_equal_main'
        fixture(setup=raw_metadata_setup)
        bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT', True)
        bpy.ops.sculpt.symmetrize('EXEC_DEFAULT', True)
        bpy.data.objects[TARGET].location.x = 1.
        bpy.ops.ed.undo_push(message='Artist manual Main checkpoint')
        saved_raw = raw_mesh_state()
        raw_boundary = (saved_raw['positions'], saved_raw['topology'])
        record_phase(prefix, 'saved-manual-main-raw', saved_raw)
        check(prefix+'_saved_raw_has_nonzero_masks_and_hiding',
              any(saved_raw['attributes']['.sculpt_mask'][2]) and
              any(saved_raw['attributes']['.hide_vert'][2]))
        steps = history()
        check(prefix+'_is_real_main_history', steps[-1]['type'] == 'Global Undo')
        if untracked_edit:
            bpy.data.objects[TARGET].location.x = 2.
            # Establish a current live transform before the staged program binds its revision.
            # This evaluates the intentional RNA edit without adding or replacing Undo history.
            bpy.context.view_layer.update()
        before = snapshot()
        check(prefix+'_saved_raw_differs_from_live_bmesh',
              tuple(value for coordinate in saved_raw['positions'] for value in coordinate) !=
              tuple(before['positions']) or saved_raw['topology'] != before['topology'])
        record_phase(prefix, 'tagged-before-live', before)
        commit()
        after = snapshot()
        record_phase(prefix, 'tagged-after-live', after)
        check(prefix+'_retains_old_boundary_and_visible_before',
              sum(not item['skip'] for item in history()) == sum(not item['skip'] for item in steps)+2 and
              history()[steps[-1]['index']]['name'] == 'Artist manual Main checkpoint')
        bpy.ops.ed.undo()
        check(prefix+'_one_undo_keeps_actual_live_state', snapshot()['target_x'] == before['target_x'] and
              same_artist(before, snapshot()))
        bpy.ops.ed.undo()
        restored_raw = raw_mesh_state()
        raw_restored = (restored_raw['positions'], restored_raw['topology'])
        record_phase(prefix, 'older-manual-main-raw', restored_raw)
        historical_artist = snapshot()
        REPORT['cases'][prefix + '_older_main'] = {
            'saved_raw_mesh': raw_boundary, 'restored_raw_mesh': raw_restored,
            'target_x': bpy.data.objects[TARGET].location.x, 'history': history(),
            'actual_artist': historical_artist, 'saved_raw_state': saved_raw,
            'restored_raw_state': restored_raw}
        # This transition LEAVES a tagged Angelo snapshot. Its no-flush contract protects
        # the manual MemFile's exact stored Mesh in both readers. Pure untagged stock
        # decoding is tested separately below; it has a different live-BMesh oracle.
        check(prefix+'_older_main_is_still_reachable', bpy.data.objects[TARGET].location.x == 1. and
              raw_restored == raw_boundary)
        check(prefix+'_older_main_raw_metadata_and_masks_are_exact', restored_raw == saved_raw)
        check(prefix+'_older_main_has_no_unpublished_live_bmesh', not historical_artist['dyntopo'])
        check(prefix+'_older_main_retains_artist_context', all(
              historical_artist[key] == before[key] for key in
              ('mode', 'active', 'selection', 'brush')))
        bpy.ops.ed.redo()
        redone_before = snapshot()
        record_phase(prefix, 'exact-before-redo-live', redone_before)
        check(prefix+'_exact_before_still_redoes', redone_before['target_x'] == before['target_x'] and
              same_artist(before, redone_before))
        bpy.ops.ed.redo()
        redone_after = snapshot()
        record_phase(prefix, 'commit-redo-live', redone_after)
        check(prefix+'_commit_still_redoes', redone_after['target_x'] == 5. and same_artist(after, redone_after))


def stock_untagged_memfile_case():
    """Neither side has an Angelo adjunct: retain each ordinary reader's exact oracle."""
    preference = bpy.context.preferences.experimental
    old_legacy = preference.use_undo_legacy
    old_developer_ui = bpy.context.preferences.view.show_developer_ui
    try:
        bpy.context.preferences.view.show_developer_ui = True
        for full_reread in (False, True):
            preference.use_undo_legacy = full_reread
            prefix = 'stock_untagged_full' if full_reread else 'stock_untagged_reused'
            fixture()
            bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT', True)
            bpy.ops.sculpt.symmetrize('EXEC_DEFAULT', True)
            bpy.data.objects[TARGET].location.x = 1.
            bpy.ops.ed.undo_push(message='Stock manual Main checkpoint')
            saved_raw = raw_mesh_state()
            before = snapshot()
            record_phase(prefix, 'saved-raw', saved_raw)
            record_phase(prefix, 'before-live', before)
            bpy.data.objects[TARGET].location.x = 2.
            bpy.ops.ed.undo_push(message='Stock manual Main successor')
            check(prefix+'_has_only_ordinary_manual_main_boundaries',
                  [(row['type'], row['name']) for row in history()[-2:]] ==
                  [('Global Undo', 'Stock manual Main checkpoint'),
                   ('Global Undo', 'Stock manual Main successor')])
            bpy.ops.ed.undo()
            restored_raw = raw_mesh_state()
            historical_artist = snapshot()
            record_phase(prefix, 'undo-raw', restored_raw)
            record_phase(prefix, 'undo-artist', historical_artist)
            expected = (restored_raw == saved_raw if full_reread else
                        same_artist({**before, 'dyntopo': False}, historical_artist))
            check(prefix+'_ordinary_untagged_decode_is_unchanged',
                  historical_artist['target_x'] == 1. and expected)
            bpy.ops.ed.redo()
            redone = snapshot()
            record_phase(prefix, 'redo-artist', redone)
            check(prefix+'_ordinary_untagged_redo_is_reachable', redone['target_x'] == 2.)
    finally:
        preference.use_undo_legacy = old_legacy
        bpy.context.preferences.view.show_developer_ui = old_developer_ui


def capsule_cases():
    fixture()
    stroke()
    commit()
    bpy.ops.ed.undo()
    steps = history()
    bridge = program_undo.native_bridge()
    scope = bridge.begin()
    try:
        try:
            bridge.begin()
        except RuntimeError:
            check('nested_scope_refuses', True)
        else:
            check('nested_scope_refuses', False)
    finally:
        check('abandoned_capture_has_no_publication', bridge.finish(scope, False) == {'undo_published': False})
    check('abandoned_capture_preserves_redo_history', history() == steps)
    try:
        bridge.finish(scope, False)
    except RuntimeError:
        check('consumed_capsule_rejected', True)
    else:
        check('consumed_capsule_rejected', False)
    try:
        bridge.finish(object(), False)
    except (RuntimeError, ValueError, TypeError):
        check('foreign_capsule_rejected', True)
    else:
        check('foreign_capsule_rejected', False)
    bpy.ops.ed.redo()
    check('abandoned_capture_keeps_usable_redo', snapshot()['target_x'] == 5.)


def cross_window_case():
    """An exact hidden baseline must not put another window's object into Sculpt."""
    fixture()
    artist_window = bpy.context.window
    other_window = None

    def window_context(window):
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        return bpy.context.temp_override(window=window, area=area, region=region)

    try:
        other_scene = bpy.data.scenes.new('Unrelated artist scene')
        mesh = bpy.data.meshes.new('Unrelated artist mesh')
        mesh.from_pydata([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)], [], [(0, 1, 2)])
        unrelated = bpy.data.objects.new('Unrelated Object Mode object', mesh)
        other_scene.collection.objects.link(unrelated)
        bpy.ops.wm.window_new_main()
        other_window = next(w for w in bpy.context.window_manager.windows if w != artist_window)
        other_window.scene = other_scene
        with window_context(other_window):
            bpy.context.view_layer.objects.active = unrelated
            unrelated.select_set(True)
            bpy.context.view_layer.update()
            check('other_window_starts_in_object_mode', bpy.context.view_layer.objects.active.mode == 'OBJECT')

        with window_context(artist_window):
            previous = stroke()
            before = stroke()
            original_index = active_index()
            commit()
            committed = snapshot()
            check('cross_window_commit_has_hidden_exact_before', history()[-2]['skip'])

        with window_context(other_window):
            bpy.ops.ed.undo()
            other_active = bpy.context.view_layer.objects.active
            REPORT['cases']['cross_window_undo'] = {'active': other_active.name,
                'mode': other_active.mode, 'scene': bpy.context.scene.name,
                'target_x': bpy.data.objects[TARGET].location.x, 'history': history()}
            check('undo_from_other_window_keeps_object_mode', other_active.mode == 'OBJECT' and
                  other_active.name == 'Unrelated Object Mode object')
            check('undo_from_other_window_returns_original_history', active_index() == original_index)
        with window_context(artist_window):
            check('other_window_undo_preserves_artist', snapshot()['target_x'] == 0. and
                  same_artist(before, snapshot()))

        with window_context(other_window):
            bpy.ops.ed.redo()
            check('redo_from_other_window_keeps_object_mode', bpy.context.view_layer.objects.active.mode == 'OBJECT')
        with window_context(artist_window):
            check('other_window_redo_preserves_artist', snapshot()['target_x'] == 5. and
                  same_artist(committed, snapshot()))
            bpy.ops.ed.undo_history(item=original_index-1)
            check('deeper_history_still_decodes_prior_stroke', snapshot()['target_x'] == 0. and
                  same_artist(previous, snapshot()))
            bpy.ops.ed.redo()
            check('deeper_history_redoes_stroke', same_artist(before, snapshot()))
            bpy.ops.ed.redo()
            check('deeper_history_redoes_commit', snapshot()['target_x'] == 5. and
                  same_artist(committed, snapshot()))
    finally:
        if other_window is not None:
            with window_context(other_window):
                bpy.ops.wm.window_close()


def measure():
    for name in ('object_small', 'sculpt_small', 'sculpt_dense'):
        values = {'commit_ms': [], 'undo_ms': [], 'redo_ms': []}
        for _ in range(5):
            fixture(dense=name == 'sculpt_dense')
            if name == 'object_small':
                bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT')
            else:
                stroke()
            _, _, elapsed = commit()
            values['commit_ms'].append(elapsed)
            for action in ('undo', 'redo'):
                started = time.perf_counter()
                getattr(bpy.ops.ed, action)()
                values[action+'_ms'].append((time.perf_counter()-started)*1000)
        REPORT['measurements'][name] = {
            key: {'samples': runs, 'median': statistics.median(runs)} for key, runs in values.items()}



def recovery_failure_case(*, full_reread=False, second_object=False):
    fixture()
    if second_object:
        bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT')
        first = bpy.data.objects[ACTOR]
        second = bpy.data.objects.new('Z Second Artist', first.data.copy())
        bpy.context.scene.collection.objects.link(second)
        second.location.x = 3.
        bpy.context.view_layer.objects.active = second
        second.select_set(True)
        first.select_set(False)
        bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='SCULPT')
        bpy.context.view_layer.objects.active = first
        second.select_set(False)
        first.select_set(True)
        bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='SCULPT')
        check('two_objects_really_in_sculpt', first.mode == second.mode == 'SCULPT')
    bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT', True)
    bpy.ops.sculpt.symmetrize('EXEC_DEFAULT', True)
    bridge = program_undo.native_bridge()
    state_before_observation = history()
    surface = bridge._test_sculpt_surface()
    check('live_surface_observation_is_stable', surface == bridge._test_sculpt_surface() and
          state_before_observation == history())
    bpy.context.scene['recovery_fixture_token'] = 'known-valid-checkpoint'
    checkpoint = str(OUT / 'known-valid-checkpoint.blend')
    bpy.ops.wm.save_as_mainfile(filepath=checkpoint, copy=True)
    # Encode a Mesh-only difference in the post snapshot while retaining the Object identity.
    # An untracked edit after both snapshots cannot force reread: Blender legitimately reuses
    # identical serialized ID chunks. This hook forwards through the real backend commit,
    # after the real typed transfer and before production native Undo publication.
    def mark_mesh_after_apply():
        actor = bpy.data.objects[ACTOR]
        actor.data['after_boundary_mesh_marker'] = 731
        actor.data.update_tag()
    commit(after_apply=mark_mesh_after_apply)
    saved_history = history()
    boundary = saved_history[-2]
    check('fault_targets_exact_hidden_boundary', boundary['skip'] and boundary['type'] == 'Global Undo')
    # The property disappearing proves Mesh reread even when its address is retained in place.
    actor = bpy.data.objects[ACTOR]
    object_pointer = actor.as_pointer()
    check('post_snapshot_contains_mesh_marker', actor.data.get('after_boundary_mesh_marker') == 731)
    bpy.context.preferences.view.show_developer_ui = True
    bpy.context.preferences.experimental.use_undo_legacy = full_reread
    called = []
    def post_handler(_scene):
        called.append('unexpected-success-handler')
    bpy.app.handlers.undo_post.append(post_handler)
    fault = bridge._test_arm_restore_failure(boundary['index'], 1 if second_object else 0)
    try:
        try:
            status = bpy.ops.ed.undo()
        except RuntimeError as error:
            status = {'CANCELLED'}
            REPORT['cases']['injected_error'] = str(error)
    finally:
        receipt = bridge._test_disarm_restore_failure(fault)
        bpy.app.handlers.undo_post.remove(post_handler)
    REPORT['cases']['fault_receipt'] = receipt
    check('actual_validator_fault_consumed', receipt['consumed'] and
          receipt['step_index'] == boundary['index'])
    check('failed_undo_is_cancelled', status == {'CANCELLED'})
    check('failed_undo_has_no_success_callback', not called)
    check('history_marks_installed_hidden_boundary', active_index() == boundary['index'])
    actor = bpy.data.objects[ACTOR]
    check('mesh_was_actually_reread', 'after_boundary_mesh_marker' not in actor.data)
    check('requested_object_reuse_path_was_exercised',
          (actor.as_pointer() != object_pointer) if full_reread else
          (actor.as_pointer() == object_pointer))
    check('failure_keeps_history_links', [(v['index'],v['name'],v['skip']) for v in history()] ==
          [(v['index'],v['name'],v['skip']) for v in saved_history])
    check('undo_redo_history_repeat_are_blocked', not bpy.ops.ed.undo.poll() and
          not bpy.ops.ed.redo.poll() and not bpy.ops.ed.undo_history.poll() and
          not bpy.ops.screen.redo_last.poll())
    check('unsafe_sculpt_operators_are_blocked', not bpy.ops.sculpt.brush_stroke.poll() and
          not bpy.ops.sculpt.angelo_sculpt_program.poll() and not bpy.ops.sculpt.symmetrize.poll() and
          not bpy.ops.paint.hide_show.poll() and not bpy.ops.transform.translate.poll() and
          not bpy.ops.object.voxel_remesh.poll())
    def meshes():
        return {obj.name: (tuple(tuple(v.co) for v in obj.data.vertices),
                          tuple(tuple(poly.vertices) for poly in obj.data.polygons))
                for obj in bpy.data.objects if obj.type == 'MESH'}
    installed = meshes()
    artist_mode = bpy.data.objects[ACTOR].mode
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            obj.update_tag(refresh={'DATA'})
    bpy.context.view_layer.update()
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    bpy.context.view_layer.update()
    check('failed_restore_redraw_evaluation_is_safe', meshes() == installed and
          bpy.data.objects[ACTOR].mode == artist_mode == 'SCULPT')
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'partial-document.blend'), copy=True)
    check('save_does_not_flush_stale_bmesh', meshes() == installed)
    bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT')
    check('explicit_mode_exit_is_safe', bpy.data.objects[ACTOR].mode == 'OBJECT' and meshes() == installed)
    bpy.context.preferences.edit.use_global_undo = False
    bpy.context.preferences.edit.undo_steps = 0
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    bpy.ops.ed.undo_push(message='Quarantine must survive new history')
    check('reset_history_does_not_clear_main_quarantine', not bpy.ops.ed.undo.poll())
    try:
        scope = bridge.begin()
    except RuntimeError:
        scope = None
    if scope is not None:
        bridge.finish(scope, False)
    check('typed_commit_admission_remains_blocked', scope is None)
    bpy.ops.wm.open_mainfile(filepath=checkpoint, load_ui=False)
    check('known_valid_document_was_loaded', bpy.context.scene.get('recovery_fixture_token') ==
          'known-valid-checkpoint')
    bpy.context.preferences.experimental.use_undo_legacy = False
    actor = bpy.data.objects[ACTOR]
    REPORT['cases']['loaded_checkpoint_context'] = {
        'active': bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        'actor_mode': actor.mode}
    bpy.context.view_layer.objects.active = actor
    actor.select_set(True)
    # File loading replaces context-owned state. Resolve the loaded window's owners explicitly
    # instead of inheriting the pre-load operator override's cached active-object lookup.
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region, scene=window.scene,
                                   view_layer=window.view_layer, active_object=actor, object=actor):
        if actor.mode != 'OBJECT':
            bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT')
        bpy.ops.ed.undo_push(message='Recovered document baseline')
        commit()
        bpy.ops.ed.undo()
        check('known_valid_load_restores_edit_and_undo', bpy.data.objects[TARGET].location.x == 0.)


def run():
    classes = []
    try:
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.context.preferences.edit.use_global_undo = True
            bpy.context.preferences.edit.undo_memory_limit = 0
            native.register()
            classes = [c for c in agent_workspace_ui.workspace_operators()
                       if getattr(c, 'bl_idname', '') == 'angelo.commit_native_job']
            for cls in classes:
                bpy.utils.register_class(cls)
            if program_undo.native_bridge() is None:
                fixture()
                before, steps = snapshot(), history()
                host, result, _ = commit()
                check('old_binary_registers_and_refuses_without_mutation', result == {'CANCELLED'} and
                      host.commits == 0 and host.result.get('error_code') == 'RECOVERY_REQUIRED' and
                      snapshot() == before and history() == steps)
                raise AssertionError('Rebuild Angelo with Sculpt-compatible commit Undo to run the positive fixtures')
            variant = os.environ.get('SCULPT_PROBE_VARIANT', '')
            if variant.startswith('active-target-admission'):
                active_target_admission_cases()
            elif variant.startswith('indirect-sculpt'):
                indirect_sculpt_cases()
            elif variant.startswith('failure-'):
                recovery_failure_case(full_reread='full' in variant, second_object='second' in variant)
            elif variant.startswith('cross-window'):
                cross_window_case()
            elif variant.startswith('mesh-only'):
                sequence(no_stroke=True)
                sequence()
                history_cases()
                capsule_cases()
            elif variant.startswith('untagged-only'):
                if 'full' in variant:
                    bpy.context.preferences.view.show_developer_ui = True
                    bpy.context.preferences.experimental.use_undo_legacy = True
                untagged_memfile_case()
                stock_untagged_memfile_case()
            elif variant.startswith('dyntopo-only'):
                dyntopo_cases()
                untagged_memfile_case()
                stock_untagged_memfile_case()
            else:
                if variant.startswith('full-success'):
                    bpy.context.preferences.view.show_developer_ui = True
                    bpy.context.preferences.experimental.use_undo_legacy = True
                sequence(no_stroke=True)
                sequence()
                sequence(multires=True)
                history_cases()
                visibility_cases()
                dyntopo_cases()
                untagged_memfile_case()
                stock_untagged_memfile_case()
                capsule_cases()
                if os.environ.get('ANGELO_SCULPT_SKIP_TIMING') != '1':
                    measure()
            check('no_stage_residue', no_stages())
        REPORT['success'] = True
    except Exception:
        REPORT['error'] = traceback.format_exc()
    finally:
        native.cleanup_all()
        for cls in reversed(classes):
            with contextlib.suppress(Exception):
                bpy.utils.unregister_class(cls)
    (OUT/'program-sculpt-undo.json').write_text(json.dumps(REPORT, indent=2, default=str))
    print('PROGRAM_SCULPT_UNDO_DONE', REPORT.get('success', False), flush=True)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=1.)
