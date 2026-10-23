# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual scene recovery + injected file/transcript faults + real Undo Restore UI.

GUI --factory-startup --enable-event-simulate. Private state/workspace/quit files,
no provider/auth/paid calls, builds or user projects. Run serially, not as timing.
"""

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
import uuid
from unittest.mock import patch

import addon_utils
import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-checkpoint-restore-ui'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'), ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import ui, console, chat_view, checkpoints, agent_workspace_ui, memory_project_ui
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
from angelo_agent.controller import Controller
from angelo_agent.files import FileWorkspace
from angelo_agent.task_memory import read_memory, update_memory
from angelo_agent.memory_evidence import scope_token
from angelo_agent.memory_projects import MAX_PROJECT_BYTES
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
ui.connection_available = lambda: True
ui.prepare_controller = lambda: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.file_preview_type = 'NONE'
(OUT / 'runtime-temp').mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'runtime-temp')
report = {'checks': {}, 'cases': [], 'paid_requests': 0, 'binary': bpy.app.binary_path}
state = {'phase': 'boot', 'started': time.monotonic(), 'case_index': 0, 'done': False}
CASES = ('pre_notice', 'ordinary', 'file_partial', 'other_0_save', 'other_1_save', 'active_final_save')
window = controller = handler = None
others = []
original_pump = ui.pump


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def parts():
    area = next(area for area in window.screen.areas if console.is_chat(area))
    return {'window': window, 'area': area, 'region': next(region for region in area.regions if region.type == 'WINDOW')}


def redraw():
    with bpy.context.temp_override(**parts()):
        parts()['area'].tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    check('native_chat_renderer_has_no_errors', chat_view.last_error is None)


def screenshot(name):
    redraw()
    with bpy.context.temp_override(**parts()):
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def click_restore(identity):
    redraw()
    view = chat_view._views[parts()['area'].as_pointer()]
    rect = next(rect for rect, action, value in view['hits'] if action == 'restore' and value == identity)
    x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    region = parts()['region']
    for value in ('PRESS', 'RELEASE'):
        event = SimpleNamespace(type='LEFTMOUSE', value=value, mouse_x=region.x + x, mouse_y=region.y + y,
            mouse_prev_y=region.y + y, ctrl=False, oskey=False, shift=False, alt=False, unicode='')
        with bpy.context.temp_override(**parts()):
            chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, event)


def latest_safety():
    return next(message for message in reversed(controller.state['messages']) if message.get('restore_checkpoint'))


def verify_rebound(prefix):
    for index, target in enumerate([controller, *others]):
        check(prefix + '_chat_' + str(index) + '_native_document_and_runtime_revoked',
            target.document == ui.document_key() and not target.document_invalid and target.state['native_id'] is None
            and not target.state['runtime_sessions'] and target.request_checkpoint is None)
        check(prefix + '_chat_' + str(index) + '_scene_evidence_stale', target.state['task_memory']['stale'])
        check(prefix + '_chat_' + str(index) + '_live_same_project_brief_preserved',
            read_memory(target.state, target.document)['brief']['objective'] == state['goals'][target.state['id']])
    check(prefix + '_original_project_hash_unchanged', hashlib.sha256((OUT / 'original.blend').read_bytes()).hexdigest() == state['original_hash'])


def run_case(name):
    bpy.data.objects['Cube'].location.x = 0
    window.view_layer.update()
    folder = OUT / 'workspace'
    folder.mkdir(exist_ok=True)
    for letter in ('a', 'b'):
        (folder / (letter + '.txt')).write_text('before ' + name)
    point = ui.capture_checkpoint(controller)
    controller.state['messages'].append({'role': 'user', 'text': 'Recovery fixture ' + name,
        'checkpoint': point['id'], 'checkpoint_scope': 'before_request'})
    for letter in ('a', 'b'):
        controller.files.execute('angelo_write_file', {'path': letter + '.txt', 'content': 'after ' + name}, controller.checkpoints, point['id'])
    bpy.data.objects['Cube'].location.x = 6 + state['case_index']
    window.view_layer.update()
    before_x = bpy.data.objects['Cube'].location.x
    for target in [controller, *others]:
        target.state['native_id'] = 'old-native-' + target.state['id']
        target.state['runtime_sessions'] = {'codex_subscription': {'native_id': target.state['native_id'], 'synced_length': 0, 'toolset_version': 10}}
        target.request_checkpoint = point['id']
        target.state['task_memory']['stale'] = False
        target.save()
    original_replace = checkpoints._replace_file
    replacements = []
    def partial_replace(*args, **kwargs):
        if replacements:
            raise OSError('Injected disk-full restoring second scoped file')
        original_replace(*args, **kwargs)
        replacements.append(True)
    target = others[int(name[6])] if name.startswith('other_') else controller
    original_save = target.save
    def failing_save():
        if name == 'pre_notice' or ui._restoring_snapshot:
            raise OSError('Injected transcript failure at ' + name)
        return original_save()
    result, exception = None, None
    with ExitStack() as stack:
        if name == 'file_partial':
            stack.enter_context(patch.object(checkpoints, '_replace_file', side_effect=partial_replace))
        elif name.endswith('save') or name == 'pre_notice':
            stack.enter_context(patch.object(target, 'save', side_effect=failing_save))
        try:
            with bpy.context.temp_override(**parts()):
                result = bpy.ops.angelo.restore_checkpoint(checkpoint_id=point['id'])
        except RuntimeError as error:
            exception = str(error)
    notice = latest_safety()
    from angelo_agent.sessions import read_transcript  # The saved base and every appended record.
    saved = read_transcript(controller.store.path)
    durable_link = any(message.get('restore_checkpoint') == notice['restore_checkpoint'] for message in saved['messages'])
    if name == 'pre_notice':
        check('pre_notice_failure_has_no_scene_effects', abs(bpy.data.objects['Cube'].location.x - before_x) < 1e-7)
        check('pre_notice_failure_has_no_file_effects', all((folder / (letter + '.txt')).read_text() == 'after ' + name for letter in ('a', 'b')))
        check('pre_notice_failure_never_claims_scene_opened', notice['restore_state'] == 'not_applied' and exception is not None)
        report['cases'].append({'name': name, 'exception': exception, 'scene_opened': False})
        return
    check(name + '_returns_finished_after_actual_scene_open', result == {'FINISHED'} and exception is None)
    check(name + '_real_snapshot_scene_replaced', abs(bpy.data.objects['Cube'].location.x) < 1e-7)
    verify_rebound(name)
    check(name + '_durable_safety_reference_remains_available', durable_link and controller.checkpoints.availability(notice['restore_checkpoint'])['available'])
    if name == 'ordinary':
        check('ordinary_restore_is_complete', notice['restore_state'] == 'complete')
    else:
        check(name + '_truthful_partial_notice', notice['restore_state'] == 'partial' and 'safety snapshot' in notice['text'])
    if name == 'file_partial':
        check('partial_file_failure_reports_actual_one_completed_target', notice['restored_files'] == 1 and replacements == [True])
        check('partial_file_failure_has_real_mixed_file_effects', (folder / 'a.txt').read_text() == 'before ' + name and (folder / 'b.txt').read_text() == 'after ' + name)
    else:
        check(name + '_scoped_files_restored_even_if_transcript_save_fails', all((folder / (letter + '.txt')).read_text() == 'before ' + name for letter in ('a', 'b')))
    if name == 'active_final_save':
        durable_notice = next(message for message in reversed(saved['messages']) if message.get('restore_checkpoint') == notice['restore_checkpoint'])
        check('postopen_active_save_failure_preserves_earlier_pending_safety_notice', durable_notice['restore_state'] == 'pending')
        restarted = controller.store.load()
        check('restart_after_undurable_recovery_revokes_old_native_resume', restarted['native_id'] is None and not restarted['runtime_sessions'])
        check('restart_after_undurable_recovery_preserves_saved_safety_link', any(message.get('restore_checkpoint') == notice['restore_checkpoint'] for message in restarted['messages']))
    check(name + '_safety_link_has_real_visible_hit', any(action == 'restore' and value == notice['restore_checkpoint']
        for _, action, value in (redraw() or chat_view._views[parts()['area'].as_pointer()])['hits']))
    screenshot(name + '-recovery-safety-link.png')
    report['cases'].append({'name': name, 'result': sorted(result), 'scene_opened': True, 'safety': notice['restore_checkpoint'],
        'restore_state': notice['restore_state'], 'restored_files': notice.get('restored_files'), 'durable_link': durable_link})
    state['safety'], state['expected_undo_x'], state['expected_files'] = notice['restore_checkpoint'], before_x, 'after ' + name
    click_restore(state['safety'])
    state['phase'] = 'undo_dialog'


def finish(error=None):
    state['done'] = True
    report.update(success=error is None, error=error, phase=state['phase'],
        provider_started=any(target.provider is not None for target in ui._controllers.values()))
    log = Path(os.environ.get('ANGELO_QA_LOG', str(OUT) + '.log'))
    if log.is_file() and any(marker in log.read_text(errors='replace') for marker in ('BLI_assert failed', 'Segmentation fault')):
        report.update(success=False, error='Native assertion/segmentation failure')
    (OUT / 'checkpoint-restore-ui.json').write_text(json.dumps(report, indent=2))
    print('ANGELO_CHECKPOINT_RESTORE_UI', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def pump():
    return None if state['done'] else .1  # Fault checks run without unrelated background persistence.


def tick():
    global window, controller, handler, others
    try:
        if time.monotonic() - state['started'] > 120:
            raise RuntimeError('Native recovery fixture timed out')
        if state['phase'] == 'boot':
            if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
                return .1
            window = bpy.context.window_manager.windows[0]
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'original.blend'), check_existing=False)
            state['original_hash'] = hashlib.sha256((OUT / 'original.blend').read_bytes()).hexdigest()
            bpy.ops.angelo.open_chat(side='RIGHT')
            state['phase'] = 'layout'
            return .5
        if not qa_boot.started():
            return .05
        if state['phase'] == 'layout':
            controller = ui.get_controller()
            controller.refresh_models = lambda *args, **kwargs: None
            controller.files = FileWorkspace(OUT / 'workspace')
            for _ in range(2):
                target = Controller(ui.state_directory(), session_id=str(uuid.uuid4()))
                target.set_document(ui.document_key())
                target.refresh_models = lambda *args, **kwargs: None
                ui._controllers[target.state['id']] = target
                others.append(target)
            state['goals'] = {}
            for index, target in enumerate([controller, *others]):
                goal = 'Preserve this public project goal for chat ' + str(index)
                target.state = update_memory(target.state, {'expected_revision': target.state['task_memory']['revision'],
                    'patch': {'objective': goal}}, target.document, provenance={'kind': 'approved_update', 'id': 'ui:factory-goal'})
                target.save()
                state['goals'][target.state['id']] = goal
            with bpy.context.temp_override(**parts()):
                console.render(bpy.context, force=True)
                parts()['area'].spaces.active.history[-1].body = 'Recovery keeps the native composer.'
            handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window.as_pointer(),
                                           focused_area=parts()['area'].as_pointer(), session=controller.state['id'],
                                           selected_all=False, anchor=None, dragging=False, pending_click=None)
            original_invoke = ui.ANGELO_OT_restore_checkpoint.invoke
            def invoke(operator, context, event):
                state['dialog'] = operator
                return original_invoke(operator, context, event)
            ui.ANGELO_OT_restore_checkpoint.invoke = invoke
            if bpy.app.timers.is_registered(original_pump):
                bpy.app.timers.unregister(original_pump)
            bpy.app.timers.register(pump, first_interval=.1, persistent=True)
            state['phase'] = 'cases'
        elif state['phase'] == 'cases':
            if state['case_index'] == len(CASES):
                data = bpy.data.meshes.new('Factory continuity budget fixture')
                data.use_fake_user = True
                data.vertices.add(6_000_000)  # 72 MiB positions; unlinked, never drawn/evaluated.
                bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'large-project.blend'), check_existing=False, compress=False)
                check('large_project_is_really_over_automatic_continuity_budget', (OUT / 'large-project.blend').stat().st_size > MAX_PROJECT_BYTES)
                controller.state['task_memory']['scope'].update(document=scope_token(('legacy-launch', 99)), project=None, project_revision=None)
                controller.state['options']['approval'] = 'READ_ONLY'
                check('large_mismatched_brief_is_not_automatically_rebound', not memory_project_ui.bind_current(ui, controller, persist=False)
                    and read_memory(controller.state, controller.document)['scope_mismatch'])
                state['before_transfer_x'] = bpy.data.objects['Cube'].location.x
                cls = next(cls for cls in agent_workspace_ui._classes if cls.__name__ == 'ANGELO_OT_use_project_brief')
                original = cls.invoke
                def invoke(operator, context, event):
                    state['brief_dialog'] = operator
                    return original(operator, context, event)
                cls.invoke = invoke
                with bpy.context.temp_override(**parts()):
                    bpy.ops.angelo.project_brief('INVOKE_DEFAULT')
                state['phase'] = 'brief_unchecked'
                return .3
            name = CASES[state['case_index']]
            run_case(name)
            if state['phase'] == 'cases':
                state['case_index'] += 1
            return .3
        elif state['phase'] == 'undo_dialog':
            check(CASES[state['case_index']] + '_real_undo_restore_dialog_reviews_safety_scope', state['dialog']._snapshot_review['scope'] == 'before_restore')
            window.event_simulate(type='RET', value='PRESS')
            window.event_simulate(type='RET', value='RELEASE')
            state['phase'] = 'undo_done'
            return .4
        elif state['phase'] == 'undo_done':
            name = CASES[state['case_index']]
            check(name + '_real_safety_restore_recovers_pre_restore_scene', abs(bpy.data.objects['Cube'].location.x - state['expected_undo_x']) < 1e-7)
            check(name + '_real_safety_restore_recovers_pre_restore_files', all((OUT / 'workspace' / (letter + '.txt')).read_text() == state['expected_files'] for letter in ('a', 'b')))
            verify_rebound(name + '_undo')
            state['case_index'] += 1
            state['phase'] = 'cases'
        elif state['phase'] == 'brief_unchecked':
            operator = state['brief_dialog']
            check('unverified_project_brief_uses_real_unchecked_human_review', not operator.review_confirmed)
            text = '\n'.join(line.body for line in operator.review_lines)
            check('human_brief_review_contains_exact_notes_and_no_authority_warning', state['goals'][controller.state['id']] in text
                and 'grants NO scene edits' in text and 'remain stale' in text)
            screenshot('large-project-public-brief-review.png')
            window.event_simulate(type='RET', value='PRESS')
            window.event_simulate(type='RET', value='RELEASE')
            state['phase'] = 'brief_reopen'
            return .3
        elif state['phase'] == 'brief_reopen':
            check('unchecked_brief_confirmation_never_rebinds', read_memory(controller.state, controller.document)['scope_mismatch'])
            with bpy.context.temp_override(**parts()):
                bpy.ops.angelo.project_brief('INVOKE_DEFAULT')
            state['phase'] = 'brief_confirm'
            return .3
        elif state['phase'] == 'brief_confirm':
            state['brief_dialog'].review_confirmed = True
            window.event_simulate(type='RET', value='PRESS')
            window.event_simulate(type='RET', value='RELEASE')
            state['phase'] = 'brief_done'
            return .4
        elif state['phase'] == 'brief_done':
            memory = read_memory(controller.state, controller.document)
            check('explicit_human_large_project_brief_confirmation_rebinds_exact_notes', not memory['scope_mismatch']
                and memory['brief']['objective'] == state['goals'][controller.state['id']])
            check('explicit_brief_confirmation_marks_all_scene_evidence_stale', memory['stale'] and memory['scope']['project'] is None)
            check('brief_confirmation_does_not_change_scene_or_authorization', abs(bpy.data.objects['Cube'].location.x - state['before_transfer_x']) < 1e-7
                and controller.state['options']['approval'] == 'READ_ONLY' and not controller.busy)
            check('brief_confirmation_has_actual_human_notes_only_intent', controller.state['messages'][-1].get('request_kind') == 'task_memory_rebind')
            screenshot('large-project-explicit-brief-confirmed.png')
            # Dispose only the fixture's unlinked size-budget asset, after all
            # assertions. The real saved large .blend remains as replay evidence.
            bpy.data.meshes.remove(bpy.data.meshes['Factory continuity budget fixture'])
            return finish()
        return .05
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(tick, first_interval=.3, persistent=True)
