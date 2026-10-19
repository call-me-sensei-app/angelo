# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable native UI-timer, Stop, expiry and recovery regressions.

Run with --factory-startup --enable-event-simulate and ANGELO_QA_DIR. Uses actual
Blender timers/operators and a private scratch scene, never provider inference,
account authentication, a user document, or performance benchmark assertions.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

import addon_utils
import bpy
import numpy as np

assert '--factory-startup' in sys.argv and not bpy.app.background
assert '--enable-event-simulate' in sys.argv, 'Allow a synthetic Escape to close only this test popup'
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-dispatch-fixes'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import ui
from angelo_agent.blender_tools import BlenderExecutor

# Prevent the addon bootstrap and pump from discovering real accounts/models.
ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None

report = {'checks': {}, 'success': False, 'paid_requests': 0,
          'binary': bpy.app.binary_path, 'host_modules': str(Path(ui.__file__).parent)}
state = {'phase': 'setup', 'heartbeat': 0, 'ui_ticks': 0, 'responses': [],
         'executions': [], 'done': False}
started = time.monotonic()
controller = None
original_execute = BlenderExecutor.execute
original_pump = ui.pump


def check(name, condition):
    report['checks'][name] = bool(condition)
    (OUT / 'progress.json').write_text(json.dumps(report, indent=2))
    assert condition, name


def view_context():
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return {'window': window, 'area': area, 'region': region}


def target():
    return bpy.data.objects['Dispatch regression sphere']


def positions():
    obj = target()
    points = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', points)
    return points


def arguments(pressure=0):
    obj = target()
    observed = original_execute(BlenderExecutor(view_context()['window']),
                                'angelo_inspect_sculpt', {'object': obj.name, 'probes': []})
    return {'object': obj.name, 'mesh_revision': observed['mesh_revision'], 'steps': [{
        'brush': 'DRAW', 'radius': .3, 'strength': .5, 'direction': 'ADD', 'symmetry': 'NONE',
        'normal': [0, -1, 0], 'front_faces_only': True, 'spacing': .2,
        'paths': [[{'position': [0, -1, 0], 'pressure': pressure}]],
    }]}


def begin(phase, approval='AUTO_BOUNDED'):
    assert not controller.pending and controller.tools.empty(), 'Prior phase must drain'
    state.update(phase=phase, responses=[], executions=[])
    controller.busy, controller.cancelled = True, False
    controller.status = 'Native dispatcher regression'
    controller.state['options']['approval'] = approval
    state['baseline'] = positions()


def enqueue(identity, args):
    def completed(result):
        state['responses'].append({'id': identity, 'result': result,
                                   'heartbeat': state['heartbeat'], 'ui_tick': state['ui_ticks']})
    controller.request_tool(identity, 'angelo_sculpt_program', args, completed)
    return controller.pending[controller.state['id'] + '/' + identity]


def instrumented_execute(self, name, args):
    result = original_execute(self, name, args)
    if name == 'angelo_sculpt_program':
        state['executions'].append({'heartbeat': state['heartbeat'], 'ui_tick': state['ui_ticks']})
        if state['phase'] == 'malformed':
            result['injected_nonfinite_readback'] = float('nan')
    return result


def finish(error=None):
    if state['done']:
        return None
    state['done'] = True
    if error:
        report['error'] = error
        report['success'] = False
    else:
        report['success'] = True
    report['heartbeat_callbacks'] = state['heartbeat']
    report['ui_timer_callbacks'] = state['ui_ticks']
    report['provider_started'] = any(c.provider is not None for c in ui._controllers.values())
    BlenderExecutor.execute = original_execute
    if bpy.app.timers.is_registered(pump_tick):
        bpy.app.timers.unregister(pump_tick)
    (OUT / 'dispatch-fixes.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    print('ANGELO_DISPATCH_FIXES', json.dumps(report, allow_nan=False), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def pump_tick():
    if state['done']:
        return None
    try:
        state['ui_ticks'] += 1
        before = len(state['executions'])
        interval = original_pump()
        executed = len(state['executions']) - before
        if executed > 1:
            raise AssertionError(f'One UI timer executed {executed} native programs')
        if controller.has_dispatchable_tools:
            check('queued_work_uses_short_followup_timer', interval == .001)
        return interval
    except Exception:
        return finish(traceback.format_exc())


def heartbeat():
    if state['done']:
        return None
    state['heartbeat'] += 1
    try:
        if time.monotonic() - started > 120:
            raise RuntimeError('Disposable native dispatcher fixture timed out')
        phase = state['phase']
        if phase == 'all_programs' and len(state['responses']) == 32:
            responses = state['responses']
            check('32_actual_programs_complete', len(state['executions']) == 32 and
                  all(item['result'].get('success') for item in responses))
            check('each_program_has_a_separate_ui_timer', len({item['ui_tick'] for item in responses}) == 32)
            check('heartbeat_runs_while_program_queue_is_active', responses[-1]['heartbeat'] > responses[0]['heartbeat'])
            check('zero_pressure_geometry_is_unchanged', np.array_equal(state['baseline'], positions()))
            report['completed_program_heartbeats'] = [item['heartbeat'] for item in responses]
            report['completed_program_ticks'] = [item['ui_tick'] for item in responses]
            begin('stop_programs')
            args = arguments()
            for index in range(32):
                enqueue('stop-' + str(index), args)
        elif phase == 'stop_programs' and len(state['executions']) >= 3:
            completed = len(state['executions'])
            check('stop_has_remaining_queued_programs', completed < 32)
            with bpy.context.temp_override(**view_context()):
                result = bpy.ops.angelo.stop('EXEC_DEFAULT')
            check('actual_stop_operator_finishes', 'FINISHED' in result)
            check('stop_cancels_remaining_queued_programs', len(state['responses']) == 32 and
                  sum(item['result'].get('success') is True for item in state['responses']) == completed and
                  sum('cancelled' in item['result'].get('error', '') for item in state['responses']) == 32 - completed)
            check('stop_has_an_event_loop_turn_between_programs', state['heartbeat'] > state['executions'][-1]['heartbeat'])
            check('stop_preserves_completed_zero_pressure_geometry', np.array_equal(state['baseline'], positions()))
            report['stop_completed_programs'] = len(state['executions'])
            state['phase'] = 'drain_stop'
        elif phase == 'drain_stop' and controller.tools.empty():
            check('stop_prevents_later_native_dispatch', len(state['executions']) == report['stop_completed_programs'])
            begin('expired_queue')
            call = enqueue('expired-queue', arguments(pressure=1))
            call.expires = time.monotonic() - 1
        elif phase == 'expired_queue' and state['responses'] and controller.tools.empty():
            result = state['responses'][0]['result']
            check('expired_queue_retains_expired_result', result.get('expired') is True and 'expired' in result['error'])
            check('expired_queue_never_executes_native_code', not state['executions'])
            check('expired_queue_cannot_mutate_geometry', np.array_equal(state['baseline'], positions()))
            begin('expired_approval', approval='ASK')
            state['approval'] = enqueue('expired-approval', arguments(pressure=1))
        elif phase == 'expired_approval' and state['approval'].approval:
            call = state['approval']
            call.expires = time.monotonic() - 1
            with bpy.context.temp_override(**view_context()):
                result = bpy.ops.angelo.approve('EXEC_DEFAULT', True,
                    call_id=call.id, session_id=controller.state['id'])
            check('expired_manual_approval_is_cancelled', 'CANCELLED' in result)
            check('expired_manual_approval_retains_expired_result', state['responses'][0]['result'].get('expired') is True)
            check('expired_manual_approval_cannot_mutate', not state['executions'] and
                  np.array_equal(state['baseline'], positions()))
            begin('malformed', approval='ASK')
            checkpoint = ui.capture_checkpoint(controller)
            controller.request_checkpoint = checkpoint['id']
            state['checkpoint'] = checkpoint
            state['approval'] = enqueue('malformed-readback', arguments(pressure=1))
        elif phase == 'malformed' and state['approval'].approval:
            call = state['approval']
            with bpy.context.temp_override(**view_context()):
                result = bpy.ops.angelo.approve('EXEC_DEFAULT', True,
                    call_id=call.id, session_id=controller.state['id'])
            check('malformed_native_mutation_preserves_undo_dispatch', 'FINISHED' in result and len(state['executions']) == 1)
            check('malformed_readback_fixture_really_changed_geometry', not np.array_equal(state['baseline'], positions()))
            result = state['responses'][0]['result']
            report['malformed_result'] = result
            check('malformed_result_keeps_request_checkpoint', result.get('request_checkpoint') == state['checkpoint']['id'])
            check('malformed_result_is_explicitly_uncertain', result.get('serialization_failed') is True and
                  result.get('partial_changes_possible') is True and result['changes']['uncertain'])
            check('malformed_result_keeps_measured_changes', any(record['name'] == target().name and
                  'geometry' in record['fields'] for record in result['changes']['modified']))
            check('malformed_result_keeps_recovery_guidance', 'snapshot' in result['recovery'] and
                  'Do not automatically retry' in result['recovery'])
            controller.pump_events()
            from angelo_agent.sessions import read_transcript  # The saved base and every appended record.
            saved = read_transcript(controller.store.path)
            check('malformed_recovery_is_durable_in_session_ledger', saved['results'][call.id]['result'] == result)
            check('malformed_result_is_in_actual_changes_history', any(
                m.get('call_id') == call.id and m.get('result', {}).get('changes') for m in controller.state['messages']))
            state['phase'] = 'changes_popup'
            with bpy.context.temp_override(**view_context()):
                outcome = bpy.ops.angelo.changes('INVOKE_DEFAULT')
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                # Popups pause normal timer processing until they are dismissed.
                # This event belongs to this disposable native window only.
                view_context()['window'].event_simulate(type='ESC', value='PRESS')
                view_context()['window'].event_simulate(type='ESC', value='RELEASE')
            check('actual_changes_popup_opens', 'CANCELLED' not in outcome)
        elif phase == 'changes_popup':
            report['changes_ui_validation'] = ('Native Changes operator invoked with the durable uncertain result in history. '
                'Exact warning/journal text is covered by test_native_dispatch_fixes.py; no pixel/visual assertion is made.')
            check('no_provider_or_authentication_started', not any(c.provider for c in ui._controllers.values()) and
                  not ui._authentication.processes)
            return finish()
        return .01
    except Exception:
        return finish(traceback.format_exc())


def setup():
    global controller
    try:
        (OUT / 'recovery').mkdir(exist_ok=True)
        bpy.context.preferences.use_preferences_save = False
        bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'recovery')
        bpy.context.preferences.edit.use_global_undo = True
        bpy.context.preferences.view.show_splash = False
        with bpy.context.temp_override(**view_context()):
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete(use_global=False)
            bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64)
            bpy.context.object.name = 'Dispatch regression sphere'
            executor = BlenderExecutor(view_context()['window'])
            before = executor.execute('angelo_inspect_sculpt', {'object': target().name, 'probes': []})
            prepared = executor.execute('angelo_prepare_sculpt', {'object': target().name,
                'mesh_revision': before['mesh_revision'], 'add_levels': 0})
            check('native_sculpt_fixture_prepared', prepared['success'] and target().mode == 'SCULPT')
            bpy.ops.ed.undo_push(message='Native dispatcher fixture baseline')
        addon_utils.enable('angelo_agent', default_set=False, persistent=False)
        controller = ui.get_controller()
        # Exercise the real UI pump, while observing each scheduled invocation.
        if bpy.app.timers.is_registered(ui.pump):
            bpy.app.timers.unregister(ui.pump)
        BlenderExecutor.execute = instrumented_execute
        begin('all_programs')
        args = arguments()
        for index in range(32):
            enqueue('program-' + str(index), args)
        bpy.app.timers.register(heartbeat, first_interval=0.0)
        bpy.app.timers.register(pump_tick, first_interval=.001)
    except Exception:
        return finish(traceback.format_exc())
    return None


bpy.app.timers.register(setup, first_interval=1)
