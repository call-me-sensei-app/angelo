# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sculpt checkpoint review regressions in one factory GUI process (WITH_GTESTS build).

The same Multires object is in Sculpt Mode in two Scenes and two windows, so two
graphs own grids of one object. Checked, in order:

1. Capture, later edits, Restore and Undo restore are exact; the restored document
   is marked modified (its private BASE file lacks the installed Sculpt state).
2. A Restore whose install fails after replacing the document reloads the exact
   pre-Restore document (``_test_fail_next_checkpoint_install``) and says so.
3. A typed commit right after a Restore adds exactly one visible Undo step, and one
   Undo returns exactly to the state before the commit.
4. After an ordinary property edit pushed as an untagged MemFile step, a typed commit keeps
   its exact boundary as a visible step here, because grids shown by two graphs do not
   decode bit for bit from that MemFile; one Undo returns exactly to before the commit.
   (With grids in one graph and no Dyntopo session the boundary is hidden: one step.)
5. With no Undo steps the commit is refused honestly: nothing changed, no partial changes.
6. Add-on handlers that raise (save_post, load_post) never fail a capture, Restore or
   Undo restore; their errors are printed, as for File > Save and File > Open.
7. A ``scene.blend`` from another capture, with its record declarations rewritten, and a
   record downgraded to an unsealed version 3 full copy are both refused and leave the
   document unchanged.
8. With the grids in one graph: a commit after Undo onto an ordinary MemFile keeps its exact
   boundary visible and Undo is exact; a commit on the latest ordinary MemFile adds one step.
9. A load handler that edits the sculpted mesh once is refused and the exact pre-Restore state
   reloads. One that edits on every load leaves Restore and Undo restore not applied: the exact
   pre-Restore state reloads with add-on load handlers suspended, the notice names the add-on
   and says to disable it, and nothing is lost; without the handler both then complete exactly.

Baselines are observed after a settling view-layer update: an ordinary save flush
can move the editing authority and leave the other graph's grids pending.
No provider, account or paid call.
"""
import array
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

import addon_utils
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Quaternion, Vector

assert '--factory-startup' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts/addons_core'), str(Path(__file__).resolve().parent)]
OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_KEYCHAIN='1')
# Checkpoints and the profile's seal key are created here: never in a real profile.
for _key in ('ANGELO_STATE_DIR', 'ANGELO_CONNECTIONS_DIR', 'BLENDER_USER_RESOURCES', 'BLENDER_USER_CONFIG',
             'BLENDER_USER_DATAFILES', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_EXTENSIONS'):
    assert Path(os.environ[_key]).resolve().is_relative_to(OUT), _key + ' must be inside QA'
from angelo_agent import agent_workspace_ui, native_programs, program_undo, scene_queries, ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from program_qa_helpers import move  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
(OUT / 'runtime-temp').mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'runtime-temp')

LABELS = ('A', 'B')
SCENES = {'A': 'Review Scene A', 'B': 'Review Scene B'}
SHARED, TARGET = 'Review Shared', 'Review Agent Target'
BRIDGE = program_undo.native_bridge()
SCRIPT = Path(__file__).name  # How a load handler defined in this script is named (its file, not English prose).
REPORT = {'success': False, 'phase': 'boot', 'checks': {}, 'compare': {}, 'facts': {}, 'paid_requests': 0,
          'binary': bpy.app.binary_path}
STATE = {'phase': 'boot', 'started': time.monotonic()}
WINDOWS = {}
controller = None


def check(name, value):
    REPORT['checks'][name] = bool(value)
    assert value, name


def context(label):
    for window in bpy.context.window_manager.windows:
        if window.scene.name == SCENES[label]:
            WINDOWS[label] = window
    window = WINDOWS[label]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=window, area=area, region=region)


def idle():
    try:
        BRIDGE._test_original_sculpt_storage()
        BRIDGE._test_sculpt_grid_surfaces()
        return True
    except RuntimeError as error:
        if 'idle jobs' in str(error):
            return False
        raise


def settle():
    for label in LABELS:
        with context(label):
            bpy.context.view_layer.update()


def observe():
    """Exact grids of both graphs, coarse Mesh data and Sculpt storage (no evaluation)."""
    storage = BRIDGE._test_original_sculpt_storage()
    inventory = BRIDGE._test_sculpt_grid_surfaces()
    obj = bpy.data.objects[SHARED]
    mesh = obj.data
    digest = lambda value: hashlib.sha256(value).hexdigest() if isinstance(value, bytes) else value
    grids = sorted(({key: digest(value) for key, value in row.items() if key not in {
        'object_uid', 'mesh_uid', 'scene_uid', 'scene_original_session_uid', 'graph_address', 'ccg_address',
        'active', 'pending_updates'}} for row in inventory['surfaces'] if row['object_uid'] == obj.session_uid),
        key=lambda row: json.dumps(row, sort_keys=True))
    record = storage.get(mesh.session_uid)
    return {'grids': grids, 'grid_count': len(grids), 'mode': obj.mode,
            'positions': hashlib.sha256(mesh.angelo_sculpt_positions).hexdigest(),
            'topology': hashlib.sha256(mesh.angelo_sculpt_topology).hexdigest(),
            'storage': None if record is None else hashlib.sha256(json.dumps(
                record, sort_keys=True, default=lambda b: digest(b) if isinstance(b, bytes) else str(b)).encode()).hexdigest()}


def compare(name, expected, actual):
    differs = sorted(key for key in set(expected) | set(actual) if expected.get(key) != actual.get(key))
    REPORT['compare'][name] = differs
    if 'grids' in differs:  # Which fields of which surface, for the report.
        REPORT['facts'][name + '_grid_fields'] = [
            sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
            for before, after in zip(expected['grids'], actual['grids'])]
    check(name, not differs)


def stroke(label, x):
    with context(label):
        obj = bpy.data.objects[SHARED]
        bpy.context.view_layer.objects.active = obj
        view = bpy.context.space_data.region_3d
        view.view_rotation = Quaternion((1, 0, 0), math.pi / 2)
        view.view_location, view.view_distance, view.view_perspective = (0, 0, 0), 4., 'ORTHO'
        view.update()
        location = (x, -math.sqrt(max(0., 1 - x * x)), 0.)
        mouse = location_3d_to_region_2d(bpy.context.region, view, Vector(location))
        point = {'name': 'review', 'location': location, 'pressure': 1., 'size': 1., 'is_start': True,
                 'x_tilt': 0., 'y_tilt': 0., 'mouse': tuple(mouse), 'mouse_event': tuple(mouse), 'time': 0.}
        assert bpy.ops.sculpt.brush_stroke('EXEC_DEFAULT', True, stroke=[point], mode='NORMAL') == {'FINISHED'}


def setup():
    WINDOWS['A'] = bpy.context.window_manager.windows[0]
    WINDOWS['A'].scene.name = SCENES['A']
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.edit.undo_steps = 64
    scene_a = WINDOWS['A'].scene
    for obj in [o for o in scene_a.objects if o.type == 'MESH']:
        bpy.data.objects.remove(obj)
    import bmesh
    mesh = bpy.data.meshes.new(SHARED + ' Mesh')
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(SHARED, mesh)
    scene_a.collection.objects.link(obj)
    scene_b = bpy.data.scenes.new(SCENES['B'])
    scene_b.collection.objects.link(obj)  # The same Object in a second Scene.
    scene_a.collection.objects.link(bpy.data.objects.new(TARGET, None))
    obj.modifiers.new('Review Multires', 'MULTIRES')
    assert bpy.ops.wm.window_new_main() == {'FINISHED'}
    WINDOWS['B'] = next(w for w in bpy.context.window_manager.windows if w != WINDOWS['A'])
    WINDOWS['B'].scene = scene_b


def enter():
    for label in LABELS:
        with context(label):
            obj = bpy.data.objects[SHARED]
            for other in bpy.context.view_layer.objects:
                other.select_set(other == obj)
            bpy.context.view_layer.objects.active = obj
            if label == 'A':
                for _ in range(2):
                    assert bpy.ops.object.multires_subdivide(modifier='Review Multires', mode='CATMULL_CLARK') == {'FINISHED'}
                bpy.ops.ed.undo_push(message='Review fixture')
            if obj.mode == 'SCULPT':  # Leave and enter from window B so its graph owns a session too.
                assert bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT') == {'FINISHED'}
            assert bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='SCULPT') == {'FINISHED'}
            unified = bpy.context.scene.tool_settings.sculpt.unified_paint_settings
            unified.use_unified_size = unified.use_unified_strength = True
            unified.size, unified.strength = 90, .5


def restore(identity, name):
    count = len(controller.state['messages'])
    with context('A'):
        try:
            outcome = sorted(bpy.ops.angelo.restore_checkpoint('EXEC_DEFAULT', checkpoint_id=identity))
        except RuntimeError as error:
            outcome = {'raised': str(error)[:400]}
    notice = next((m for m in reversed(controller.state['messages'][count:])
                   if m.get('restore_source_checkpoint') == identity), {})
    REPORT['facts'][name] = {'outcome': outcome, 'state': notice.get('restore_state'),
                             'reloaded': notice.get('scene_reloaded'), 'text': (notice.get('text') or '')[:300],
                             'reason': (notice.get('restore_reason') or '')[:300]}
    return notice


def capture(text):
    record = ui.capture_checkpoint(controller, purpose='request')
    controller.state['messages'].append({'role': 'user', 'text': text, 'checkpoint': record['id'],
                                         'checkpoint_scope': 'before_request'})
    return controller.checkpoints.load(record['id'])


def history_rows():
    """Blender's own Undo history print (flags: active, applied, MemFile, skip)."""
    import ctypes
    import re
    rows = []
    path = OUT / 'history.txt'
    libc = ctypes.CDLL(None)
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        with path.open('w') as output:
            os.dup2(output.fileno(), 1)
            bpy.context.window_manager.print_undo_steps()
            libc.fflush(None)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    for line in path.read_text().splitlines()[1:]:
        found = re.fullmatch(r"\[([ *][ #][ M][ S])\]\s+(\d+)\s+\{([^{}\s]+)\} type='([^']+)', name='(.*)'", line)
        if found:
            rows.append({'flags': found.group(1), 'name': found.group(5)})
    return rows


def commit(window, x):
    """One approved typed program moving the agent target, through the real native commit."""
    scene_queries.invalidate()
    chat = Controller(tempfile.mkdtemp(prefix='commit-', dir=str(OUT)))
    session, document = chat.state['id'], ui.document_key()
    chat.set_document(document)
    chat.busy = True
    chat.state['options']['approval'] = 'ASK'
    executor, responses = BlenderExecutor(window), []
    try:
        chat.request_tool('program', 'angelo_scene_program', move(window, bpy.data.objects[TARGET], (x, 0, 0)),
                          responses.append)
        call = chat.pending.get(session + '/program')
        chat.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
        chat.approve_tool(call, executor, document)
        for _ in range(400):
            chat.pump_tools(executor, document, budget=ToolDispatchBudget(seconds=60))
            job = chat.native_jobs.get(call.id)
            if responses or job is not None and job.ready_to_commit:
                break
        ui._controllers[session] = chat
        try:
            outcome = bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True, call_id=call.id, session_id=session)
        except RuntimeError as error:  # A refusal reported as an ERROR; its result is still delivered.
            outcome = {'raised': str(error)[:300]}
    finally:
        ui._controllers.pop(session, None)
        chat.busy = False
        chat.close()
    return (sorted(outcome) if isinstance(outcome, set) else outcome), responses[-1] if responses else {}


def tick():
    global controller
    try:
        assert time.monotonic() - STATE['started'] < 420, 'Sculpt checkpoint review fixture timed out'
        phase = STATE['phase']
        REPORT['phase'] = phase
        if phase not in ('boot', 'enter') and not idle():
            return .1
        if phase == 'boot':
            setup()
            STATE['phase'] = 'enter'
        elif phase == 'enter':
            enter()
            STATE['phase'] = 'author'
        elif phase == 'author':
            stroke('A', -.3)
            stroke('B', .3)
            stroke('B', .4)
            STATE['phase'] = 'capture'
        elif phase == 'capture':
            controller = ui.get_controller()
            controller.refresh_models = lambda *args, **kwargs: None
            settle()
            STATE['O'] = observe()
            check('same_object_has_two_graph_owners', STATE['O']['grid_count'] == 2)
            record = capture('review capture')
            check('two_graph_capture_is_validated', record.get('capture_validation') == 'VALIDATED'
                  and record.get('sculpt', {}).get('owner_count') == 1)
            check('two_graph_capture_is_sealed', type(record.get('component_seal')) is str)
            STATE['C'] = record['id']
            compare('capture_is_inert', STATE['O'], observe())
            stroke('A', -.55)
            stroke('B', .55)
            STATE['phase'] = 'later'
        elif phase == 'later':
            settle()
            STATE['L'] = observe()
            check('later_edits_differ', STATE['L'] != STATE['O'])
            notice = restore(STATE['C'], 'restore')
            check('two_graph_restore_completes', notice.get('restore_state') == 'complete')
            STATE['safety'] = notice.get('restore_checkpoint')
            STATE['phase'] = 'restored'
        elif phase == 'restored':
            compare('restore_equals_capture', STATE['O'], observe())
            check('restored_document_is_modified', bpy.data.is_dirty)
            notice = restore(STATE['safety'], 'undo_restore')
            check('two_graph_undo_restore_completes', notice.get('restore_state') == 'complete')
            STATE['phase'] = 'undone'
        elif phase == 'undone':
            settle()
            compare('undo_restore_equals_pre_restore', STATE['L'], observe())
            STATE['P'] = observe()
            BRIDGE._test_fail_next_checkpoint_install()
            notice = restore(STATE['C'], 'faulted_restore')
            check('late_install_failure_reloads_safety', notice.get('restore_state') == 'not_applied'
                  and notice.get('scene_reloaded') is True and 'Test fault' in (notice.get('restore_reason') or ''))
            STATE['phase'] = 'reloaded'
        elif phase == 'reloaded':
            settle()
            compare('reload_equals_pre_restore', STATE['P'], observe())
            check('reloaded_document_is_modified', bpy.data.is_dirty)
            notice = restore(STATE['C'], 'restore_after_reload')
            check('restore_works_after_reload', notice.get('restore_state') == 'complete')
            STATE['phase'] = 'commit'
        elif phase == 'commit':
            settle()
            STATE['before_commit'] = observe()
            before = history_rows()
            native_programs.register()
            if not hasattr(bpy.types, 'ANGELO_OT_commit_native_job'):
                for cls in agent_workspace_ui.workspace_operators():
                    if getattr(cls, 'bl_idname', '') == 'angelo.commit_native_job':
                        bpy.utils.register_class(cls)
            with context('A'):
                outcome, result = commit(bpy.context.window, 5.)
            check('commit_after_restore_finishes', outcome == ['FINISHED'] and result.get('status') == 'committed')
            after = history_rows()
            added = [row for row in after[len(before):] if 'S' not in row['flags']]
            REPORT['facts']['commit_history'] = after[-4:]
            check('commit_adds_one_visible_undo_step', len(added) == 1)
            with context('A'):
                check('one_undo_after_commit', bpy.ops.ed.undo() == {'FINISHED'})
            STATE['phase'] = 'commit-undone'
        elif phase == 'commit-undone':
            compare('one_undo_returns_to_before_commit', STATE['before_commit'], observe())
            check('target_back_at_origin', bpy.data.objects[TARGET].location.x == 0.)
            with context('A'):
                no_empty_step = not bpy.ops.ed.undo.poll() or history_rows()[-1]['name'] != 'Before Validated Native Program'
            check('no_empty_before_step', no_empty_step and not any(
                row['name'] == 'Before Validated Native Program' for row in history_rows()))
            STATE['phase'] = 'memfile-edit'
        elif phase == 'memfile-edit':
            settle()
            with context('A'):
                target = bpy.data.objects[TARGET]
                target.hide_render = not target.hide_render  # An ordinary property edit while Sculpting.
                bpy.ops.ed.undo_push(message='Review property edit')
            STATE['phase'] = 'memfile-commit'  # Through the event loop, as an artist's edit is.
        elif phase == 'memfile-commit':
            settle()
            STATE['before_memfile_commit'] = observe()
            before = history_rows()
            check('untagged_memfile_step_is_active', before[-1]['name'] == 'Review property edit'
                  and before[-1]['flags'][0] == '*' and 'M' in before[-1]['flags'])
            with context('A'):
                outcome, result = commit(bpy.context.window, 6.)
            check('memfile_commit_finishes', outcome == ['FINISHED'] and result.get('status') == 'committed')
            after = history_rows()
            REPORT['facts']['memfile_commit_history'] = after[-4:]
            check('two_graph_memfile_commit_keeps_its_exact_boundary_visible',
                  [row['name'] for row in after[len(before):] if 'S' not in row['flags']]
                  == ['Before Validated Native Program', 'Commit Validated Native Program'])
            with context('A'):
                check('one_undo_after_memfile_commit', bpy.ops.ed.undo() == {'FINISHED'})
            STATE['phase'] = 'memfile-undone'
        elif phase == 'memfile-undone':
            settle()  # Decoding a MemFile rebuilds both graphs' grids; observe them settled, like every baseline.
            compare('memfile_undo_returns_to_before_commit', STATE['before_memfile_commit'], observe())
            check('memfile_target_back_at_origin', bpy.data.objects[TARGET].location.x == 0.)
            edit = bpy.context.preferences.edit
            steps, edit.undo_steps = edit.undo_steps, 0
            try:
                with context('A'):
                    outcome, result = commit(bpy.context.window, 7.)
            finally:
                edit.undo_steps = steps
            REPORT['facts']['no_undo_steps_commit'] = result
            check('no_undo_steps_refusal_is_honest', result.get('error_code') == 'UNSUPPORTED'
                  and result.get('scene_changed') is False and not result.get('partial_changes_possible'))
            check('no_undo_steps_leaves_target', bpy.data.objects[TARGET].location.x == 0.)
            STATE['phase'] = 'handlers'
        elif phase == 'handlers':
            def raising(*_args):
                raise RuntimeError('EXPECTED_REVIEW_HANDLER_ERROR')
            STATE['handler'] = handler = bpy.app.handlers.persistent(raising)
            for handlers in (bpy.app.handlers.save_post, bpy.app.handlers.load_post):
                handlers.append(handler)
            settle()
            STATE['HC'] = observe()
            with contextlib.redirect_stderr(STATE.setdefault('handler_stderr', io.StringIO())):
                record = capture('review handler capture')
            check('raising_save_handler_never_fails_capture', record.get('capture_validation') == 'VALIDATED')
            STATE['handler_capture'] = record['id']
            stroke('A', .2)
            STATE['phase'] = 'handlers-restore'
        elif phase == 'handlers-restore':
            settle()
            STATE['H'] = observe()
            with contextlib.redirect_stderr(STATE['handler_stderr']):
                notice = restore(STATE['handler_capture'], 'handler_restore')
            check('raising_load_handler_never_fails_restore', notice.get('restore_state') == 'complete')
            STATE['handler_safety'] = notice.get('restore_checkpoint')
            STATE['phase'] = 'handlers-undo'
        elif phase == 'handlers-undo':
            settle()
            compare('handler_restore_equals_capture', STATE['HC'], observe())
            with contextlib.redirect_stderr(STATE['handler_stderr']):
                notice = restore(STATE['handler_safety'], 'handler_undo_restore')
            check('raising_load_handler_never_fails_undo_restore', notice.get('restore_state') == 'complete')
            STATE['phase'] = 'handlers-done'
        elif phase == 'handlers-done':
            settle()
            compare('handler_undo_restore_equals_pre_restore', STATE['H'], observe())
            for handlers in (bpy.app.handlers.save_post, bpy.app.handlers.load_post):
                while STATE['handler'] in handlers:
                    handlers.remove(STATE['handler'])
            printed = STATE['handler_stderr'].getvalue()
            (OUT / 'expected-handler-stderr.txt').write_text(printed)
            check('handler_errors_are_printed', printed.count('EXPECTED_REVIEW_HANDLER_ERROR') >= 3)
            STATE['phase'] = 'tamper'
        elif phase == 'tamper':
            first = capture('review first')
            stroke('A', .1)
            STATE['phase'] = 'tamper-second'
        elif phase == 'tamper-second':
            second = capture('review second')
            settle()
            STATE['T'] = observe()
            records = [controller.checkpoints.load(m['checkpoint']) for m in controller.state['messages']
                       if m.get('text') in ('review first', 'review second')]
            first, second = records[-2], records[-1]
            folder_first = controller.checkpoints.folder(first['id'])
            folder_second = controller.checkpoints.folder(second['id'])
            data = (folder_first / 'scene.blend').read_bytes()
            with open(folder_second / 'scene.blend', 'r+b') as handle:  # Keep the inode; only the bytes change.
                handle.write(data)
                handle.truncate(len(data))
            metadata = folder_second / 'checkpoint.json'
            record = json.loads(metadata.read_text())
            record.update(scene_bytes=len(data), scene_hash=hashlib.blake2b(data, digest_size=32).hexdigest())
            metadata.write_text(json.dumps(record))
            notice = restore(second['id'], 'tampered_restore')
            outcome = REPORT['facts']['tampered_restore']['outcome']
            check('swapped_base_is_refused', isinstance(outcome, dict)
                  and 'not bound to its own capture' in outcome.get('raised', ''))
            metadata = folder_first / 'checkpoint.json'  # Downgrade to an unsealed version 3 full copy.
            record = json.loads(metadata.read_text())
            record['version'] = 3
            for key in ('sculpt', 'capture_outputs', 'capture_validation', 'component_seal', 'scene_hash'):
                record.pop(key, None)
            metadata.write_text(json.dumps(record))
            (folder_first / 'sculpt.runtime').unlink()
            restore(first['id'], 'downgraded_restore')
            outcome = REPORT['facts']['downgraded_restore']['outcome']
            check('downgraded_unsealed_record_is_refused', isinstance(outcome, dict)
                  and 'not bound to its own capture' in outcome.get('raised', ''))
            STATE['phase'] = 'tamper-check'
        elif phase == 'tamper-check':
            settle()
            compare('refused_restore_leaves_document_unchanged', STATE['T'], observe())
            # From here on one graph owns the grids: the shared object leaves Scene B.
            bpy.data.scenes[SCENES['B']].collection.objects.unlink(bpy.data.objects[SHARED])
            STATE['phase'] = 'redo-edit'
        elif phase == 'redo-edit':
            settle()
            check('one_graph_owns_the_grids', observe()['grid_count'] == 1)
            with context('A'):
                target = bpy.data.objects[TARGET]
                target.hide_render = not target.hide_render
                bpy.ops.ed.undo_push(message='Review redo edit')
            stroke('A', .15)
            STATE['phase'] = 'redo-undo'
        elif phase == 'redo-undo':
            with context('A'):
                check('stroke_undone_onto_the_ordinary_memfile', bpy.ops.ed.undo() == {'FINISHED'})
            STATE['phase'] = 'redo-commit'
        elif phase == 'redo-commit':
            settle()
            before = history_rows()
            active = next(index for index, row in enumerate(before) if row['flags'][0] == '*')
            check('active_ordinary_memfile_has_redo_steps', before[active]['name'] == 'Review redo edit'
                  and 'M' in before[active]['flags'] and active < len(before) - 1)
            STATE['before_redo_commit'] = observe()
            with context('A'):
                outcome, result = commit(bpy.context.window, 8.)
            check('redo_commit_finishes', outcome == ['FINISHED'] and result.get('status') == 'committed')
            # Decoding a MemFile reached by Undo is not the live state Undo left: keep the exact boundary.
            check('memfile_with_redo_keeps_its_exact_boundary_visible',
                  [row['name'] for row in history_rows()[active + 1:] if 'S' not in row['flags']]
                  == ['Before Validated Native Program', 'Commit Validated Native Program'])
            with context('A'):
                check('one_undo_after_redo_commit', bpy.ops.ed.undo() == {'FINISHED'})
            STATE['phase'] = 'redo-undone'
        elif phase == 'redo-undone':
            settle()
            compare('redo_commit_undo_returns_exactly', STATE['before_redo_commit'], observe())
            check('redo_target_back_at_origin', bpy.data.objects[TARGET].location.x == 0.)
            with context('A'):
                target = bpy.data.objects[TARGET]
                target.hide_render = not target.hide_render
                bpy.ops.ed.undo_push(message='Review latest edit')
            STATE['phase'] = 'latest-commit'
        elif phase == 'latest-commit':
            settle()
            before = history_rows()
            check('latest_ordinary_memfile_is_active', before[-1]['name'] == 'Review latest edit'
                  and before[-1]['flags'][0] == '*')
            STATE['before_latest_commit'] = observe()
            with context('A'):
                outcome, result = commit(bpy.context.window, 9.)
            check('latest_memfile_one_graph_commit_adds_one_visible_step', outcome == ['FINISHED'] and
                  [row['name'] for row in history_rows()[len(before):] if 'S' not in row['flags']]
                  == ['Commit Validated Native Program'])
            with context('A'):
                check('one_undo_after_latest_commit', bpy.ops.ed.undo() == {'FINISHED'})
            STATE['phase'] = 'latest-undone'
        elif phase == 'latest-undone':
            settle()
            compare('latest_commit_undo_returns_exactly', STATE['before_latest_commit'], observe())
            STATE['v0'] = bpy.data.objects[SHARED].data.vertices[0].co.x
            STATE['H2'] = capture('review handler checkpoint')['id']
            stroke('A', -.2)
            STATE['phase'] = 'once-restore'
        elif phase == 'once-restore':
            settle()
            STATE['P2'] = observe()
            calls = STATE['handler_calls'] = []
            def once(*_args):  # An add-on that edits the sculpted mesh on its first load only.
                calls.append(1)
                if len(calls) == 1:
                    bpy.data.objects[SHARED].data.vertices[0].co.x += .25
            STATE['once'] = bpy.app.handlers.persistent(once)
            bpy.app.handlers.load_post.append(STATE['once'])
            notice = restore(STATE['H2'], 'one_time_handler_restore')
            check('one_time_handler_edit_is_refused_then_reloaded', notice.get('restore_state') == 'not_applied'
                  and notice.get('scene_reloaded') is True and 'Review Shared' in (notice.get('restore_reason') or ''))
            STATE['phase'] = 'once-reloaded'
        elif phase == 'once-reloaded':
            settle()
            compare('one_time_handler_reload_is_exact', STATE['P2'], observe())
            bpy.app.handlers.load_post.remove(STATE['once'])
            def every(*_args):  # An add-on that edits the sculpted mesh on every load.
                bpy.data.objects[SHARED].data.vertices[0].co.x += .25
            STATE['every'] = bpy.app.handlers.persistent(every)
            bpy.app.handlers.load_post.append(STATE['every'])
            notice = restore(STATE['H2'], 'every_load_handler_restore')
            text = notice.get('text') or ''
            # Never presented as the recorded state: the pre-Restore state is kept exactly instead.
            check('every_load_handler_restore_is_not_applied_and_loses_nothing',
                  notice.get('restore_state') == 'not_applied' and notice.get('scene_reloaded') is True
                  and notice.get('load_handlers_suspended') == [SCRIPT] and 'nothing was lost' in text
                  and 'Restore again' in text)
            check('suspended_handler_is_back', STATE['every'] in bpy.app.handlers.load_post)
            STATE['phase'] = 'every-reloaded'
        elif phase == 'every-reloaded':
            settle()
            compare('every_load_handler_reload_is_exact', STATE['P2'], observe())
            check('every_load_reload_stays_in_sculpt', bpy.data.objects[SHARED].mode == 'SCULPT')
            stroke('A', .1)
            with context('A'):
                check('every_load_reload_has_working_undo', bpy.ops.ed.undo() == {'FINISHED'})
            bpy.app.handlers.load_post.remove(STATE['every'])
            notice = restore(STATE['H2'], 'restore_before_every_load_undo')
            check('restore_without_the_handler_completes', notice.get('restore_state') == 'complete')
            STATE['undo_safety'] = notice.get('restore_checkpoint')
            STATE['phase'] = 'every-undo'
        elif phase == 'every-undo':
            settle()
            STATE['R2'] = observe()
            bpy.app.handlers.load_post.append(STATE['every'])
            notice = restore(STATE['undo_safety'], 'every_load_handler_undo_restore')
            check('every_load_handler_undo_restore_loses_nothing', notice.get('restore_state') == 'not_applied'
                  and notice.get('scene_reloaded') is True and notice.get('load_handlers_suspended') == [SCRIPT])
            STATE['phase'] = 'every-undo-reloaded'
        elif phase == 'every-undo-reloaded':
            settle()
            compare('every_load_undo_restore_keeps_the_restored_state', STATE['R2'], observe())
            bpy.app.handlers.load_post.remove(STATE['every'])
            notice = restore(STATE['undo_safety'], 'undo_restore_without_the_handler')
            check('undo_restore_without_the_handler_completes', notice.get('restore_state') == 'complete')
            STATE['phase'] = 'every-done'
        elif phase == 'every-done':
            settle()
            compare('undo_restore_returns_the_pre_restore_work', STATE['P2'], observe())
            REPORT['success'] = all(REPORT['checks'].values())
            REPORT['phase'] = 'complete'
            return finish()
        return .15
    except BaseException:
        return finish(traceback.format_exc())


def finish(error=None):
    if error:
        REPORT['error'] = error
        REPORT['success'] = False
    (OUT / 'sculpt-checkpoint-review.json').write_text(json.dumps(REPORT, indent=1, default=str))
    print('ANGELO_SCULPT_CHECKPOINT_REVIEW', json.dumps({'success': REPORT['success'], 'phase': REPORT['phase'],
          'checks': len(REPORT['checks'])}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=.3, persistent=True)
