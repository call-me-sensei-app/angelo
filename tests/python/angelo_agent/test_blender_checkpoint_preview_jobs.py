# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checkpoint Restore while icon and material preview jobs run (GUI, one factory process).

A preview job thread that ends while Python holds the GIL used to make any join of it wait
forever (a file read kills every job; a second preview request kills the first), and a preview
written into Main between the safety capture and the replacement made Restore refuse. Ten Sculpt (Dyntopo, version 4) and ten Object Mode
(version 3) Restores each start object and material preview jobs in the very tick that Restores,
without waiting for idle jobs. Every one must finish (the runner's timeout is the hang watchdog)
and complete exactly. No provider, account or paid call.
"""
import json
import os
from pathlib import Path
import sys
import time
import traceback

import addon_utils
import bpy

assert '--factory-startup' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts/addons_core'), str(Path(__file__).resolve().parent)]
OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_KEYCHAIN='1')
for _key in ('ANGELO_STATE_DIR', 'ANGELO_CONNECTIONS_DIR', 'BLENDER_USER_RESOURCES', 'BLENDER_USER_CONFIG',
             'BLENDER_USER_DATAFILES', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_EXTENSIONS'):
    assert Path(os.environ[_key]).resolve().is_relative_to(OUT), _key + ' must be inside QA'
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
(OUT / 'runtime-temp').mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'runtime-temp')

RUNS = 10
SCULPT, MARKER = 'Preview Sculpt', 'Preview Marker'
REPORT = {'success': False, 'phase': 'boot', 'checks': {}, 'runs': [], 'paid_requests': 0,
          'binary': bpy.app.binary_path}
STATE = {'phase': 'boot', 'started': time.monotonic(), 'run': 0}
controller = None


def check(name, value):
    REPORT['checks'][name] = bool(value)
    assert value, name


def view3d():
    """The first window's 3D Viewport and its main region, or None while the window is still being set up: on a slow
    start (Mac 1 under load, 2026-10-24 22:46) the first tick found no 3D Viewport yet and the suite failed at boot."""
    windows = bpy.context.window_manager.windows
    if not windows:
        return None
    window = windows[0]
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    return (window, area, region) if region else None


def context():
    window, area, region = view3d()
    return bpy.context.temp_override(window=window, area=area, region=region)


def setup():
    scene = bpy.context.window_manager.windows[0].scene
    for obj in [o for o in scene.objects if o.type == 'MESH']:
        bpy.data.objects.remove(obj)
    import bmesh
    mesh = bpy.data.meshes.new(SCULPT)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(SCULPT, mesh)
    obj.data.materials.append(bpy.data.materials.new('Preview Material'))
    scene.collection.objects.link(obj)
    scene.collection.objects.link(bpy.data.objects.new(MARKER, None))
    with context():
        bpy.context.view_layer.update()
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.ed.undo_push(message='Preview fixture')
        assert bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='SCULPT') == {'FINISHED'}
        assert bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT', True) == {'FINISHED'}


def start_previews():
    """Icon preview jobs for the object and its material: the everyday trigger is a material preview.

    The object's preview is asked for twice: the second request kills the first's running job from
    Python, the same join under the GIL as a file read's.
    """
    obj = bpy.data.objects[SCULPT]
    obj.asset_generate_preview()
    bpy.data.materials['Preview Material'].asset_generate_preview()
    running = bpy.app.is_job_running('RENDER_PREVIEW')
    obj.asset_generate_preview()
    return running


def one_restore(kind):
    """Capture, change the marker, start preview jobs and Restore in this same tick (no idle wait)."""
    record = ui.capture_checkpoint(controller, purpose='request')
    controller.state['messages'].append({'role': 'user', 'text': kind, 'checkpoint': record['id'],
                                         'checkpoint_scope': 'before_request'})
    marker = bpy.data.objects[MARKER]
    before = tuple(marker.location)
    marker.location.x += 1.
    running = start_previews()
    count = len(controller.state['messages'])
    started = time.monotonic()
    with context():
        try:
            outcome = sorted(bpy.ops.angelo.restore_checkpoint('EXEC_DEFAULT', checkpoint_id=record['id']))
        except RuntimeError as error:
            outcome = {'raised': str(error)[:300]}
    notice = next((m for m in reversed(controller.state['messages'][count:])
                   if m.get('restore_source_checkpoint') == record['id']), {})
    row = {'kind': kind, 'version': record.get('version'), 'preview_job_running': running, 'outcome': outcome,
           'state': notice.get('restore_state'), 'seconds': round(time.monotonic() - started, 3),
           'marker_restored': tuple(bpy.data.objects[MARKER].location) == before,
           'text': (notice.get('text') or '')[:200]}
    REPORT['runs'].append(row)
    check('%s_%d_restore_completes' % (kind, len(REPORT['runs'])), row['state'] == 'complete' and row['marker_restored'])
    return row


def tick():
    global controller
    try:
        assert time.monotonic() - STATE['started'] < 400, 'Preview-job Restore fixture timed out'
        phase = STATE['phase']
        REPORT['phase'] = phase
        if phase == 'boot':
            if view3d() is None:   # Still setting up its window; the time limit above bounds the wait.
                return .2
            setup()
            controller = ui.get_controller()
            controller.refresh_models = lambda *args, **kwargs: None
            STATE['phase'] = 'sculpt'
        elif phase == 'sculpt':
            row = one_restore('sculpt')
            if STATE['run'] == 0:
                check('sculpt_checkpoint_is_version_4', row['version'] == 4)
            STATE['run'] += 1
            if STATE['run'] == RUNS:
                with context():
                    assert bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode='OBJECT') == {'FINISHED'}
                STATE['run'], STATE['phase'] = 0, 'object'
        elif phase == 'object':
            row = one_restore('object')
            if STATE['run'] == 0:
                check('object_mode_checkpoint_is_version_3', row['version'] == 3)
            STATE['run'] += 1
            if STATE['run'] == RUNS:
                check('preview_jobs_overlapped_some_restores', any(r['preview_job_running'] for r in REPORT['runs']))
                REPORT['success'] = all(REPORT['checks'].values())
                REPORT['phase'] = 'complete'
                return finish()
        return .2
    except BaseException:
        return finish(traceback.format_exc())


def finish(error=None):
    if error:
        REPORT['error'] = error
        REPORT['success'] = False
    (OUT / 'checkpoint-preview-jobs.json').write_text(json.dumps(REPORT, indent=1, default=str))
    print('ANGELO_CHECKPOINT_PREVIEW_JOBS', json.dumps({'success': REPORT['success'], 'phase': REPORT['phase'],
          'runs': len(REPORT['runs'])}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=.3, persistent=True)
