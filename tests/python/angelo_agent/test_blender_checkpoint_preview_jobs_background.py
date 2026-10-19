# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Python-started file reads while preview jobs run (BACKGROUND, one factory process).

A file read started from Python holds the GIL and joins every running job; a preview job thread
that needs the GIL to end used to make that join wait forever. Ten Restores (the Restore
operator, version 3) and ten plain ``open_mainfile`` calls each request object and material
previews first and must finish (the runner's timeout is the hang watchdog). In background mode
Blender queues preview jobs but starts them only from the event loop, so here the reads kill
queued, not running, jobs; ``preview_job_running`` records which. The running-job case is
``test_blender_checkpoint_preview_jobs`` (GUI). No provider or paid call.
"""
import json
import os
from pathlib import Path
import sys
import time

import addon_utils
import bpy

assert '--factory-startup' in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_KEYCHAIN='1')
from angelo_agent import ui  # noqa: E402

RUNS = 10
REPORT = {'success': False, 'checks': {}, 'runs': [], 'paid_requests': 0, 'binary': bpy.app.binary_path}


def check(name, value):
    REPORT['checks'][name] = bool(value)
    assert value, name


def start_previews():
    for obj in bpy.data.objects:
        obj.asset_generate_preview()
    for material in bpy.data.materials:
        material.asset_generate_preview()
    return bpy.app.is_job_running('RENDER_PREVIEW')


def main():
    ui._authentication.start = lambda *args, **kwargs: None
    addon_utils.enable('angelo_agent', default_set=False, persistent=False)
    bpy.ops.mesh.primitive_uv_sphere_add()
    bpy.context.active_object.data.materials.append(bpy.data.materials.new('Preview Material'))
    document = OUT / 'preview-document.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(document))
    controller = ui.get_controller()
    for index in range(RUNS):
        record = ui.capture_checkpoint(controller)
        controller.state['messages'].append({'role': 'user', 'text': 'preview %d' % index, 'checkpoint': record['id']})
        bpy.context.active_object.location.x += 1.
        running = start_previews()
        started = time.monotonic()
        outcome = sorted(bpy.ops.angelo.restore_checkpoint(checkpoint_id=record['id']))
        notice = next((m for m in reversed(controller.state['messages'])
                       if m.get('restore_source_checkpoint') == record['id']), {})
        REPORT['runs'].append({'kind': 'restore', 'preview_job_running': running, 'outcome': outcome,
                               'state': notice.get('restore_state'), 'seconds': round(time.monotonic() - started, 3)})
        check('restore_%d_completes' % index, outcome == ['FINISHED'] and notice.get('restore_state') == 'complete')
        controller = ui.get_controller()
    for index in range(RUNS):
        running = start_previews()
        started = time.monotonic()
        outcome = sorted(bpy.ops.wm.open_mainfile(filepath=str(document)))
        REPORT['runs'].append({'kind': 'open_mainfile', 'preview_job_running': running, 'outcome': outcome,
                               'seconds': round(time.monotonic() - started, 3)})
        check('open_%d_finishes' % index, outcome == ['FINISHED'])
    REPORT['success'] = all(REPORT['checks'].values())


try:
    main()
finally:
    (OUT / 'checkpoint-preview-jobs-background.json').write_text(json.dumps(REPORT, indent=1, default=str))
    print('ANGELO_CHECKPOINT_PREVIEW_JOBS_BACKGROUND', json.dumps({'success': REPORT['success'],
          'runs': len(REPORT['runs'])}), flush=True)
