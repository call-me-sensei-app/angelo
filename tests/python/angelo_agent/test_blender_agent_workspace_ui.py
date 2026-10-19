# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable, unbilled native workspace UI/approval/Undo factory fixture.

Run GUI Angelo with --factory-startup --enable-event-simulate and ANGELO_QA_DIR.
Provider discovery/authentication are disabled before enabling the addon. No
production document, native build, inference, network or paid API is used.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

import addon_utils
import blf
import bpy
import gpu

assert '--factory-startup' in sys.argv and not bpy.app.background
assert '--enable-event-simulate' in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-agent-workspace-ui'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import ui, console, chat_view, agent_workspace_ui, approval_review, native_programs, scene_queries, native_preview_images
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.tool_specs import TOOL_MAP
sys.path.insert(0, str(Path(__file__).resolve().parent))
from program_qa_helpers import spent_budget

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
ui.connection_available = lambda: True
ui.prepare_controller = lambda: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.edit.use_global_undo = True
bpy.context.preferences.filepaths.file_preview_type = 'NONE'
(OUT / 'runtime-temp').mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'runtime-temp')
report = {'checks': {}, 'success': False, 'paid_requests': 0, 'binary': bpy.app.binary_path,
          'host_modules': str(Path(ui.__file__).parent)}
state = {'phase': 'boot', 'started': time.monotonic(), 'responses': [], 'done': False,
         'ui_ticks': 0, 'heartbeats': 0, 'dialogs': {}}
window = controller = handler = None
original_pump = ui.pump
capture_request = None


def check(name, condition):
    report['checks'][name] = bool(condition)
    (OUT / 'workspace-progress.json').write_text(json.dumps(report, indent=2))
    assert condition, name


def context_parts(chat=True):
    area = next(a for a in window.screen.areas if console.is_chat(a)) if chat else next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {'window': window, 'area': area, 'region': region}


def redraw():
    parts = context_parts()
    with bpy.context.temp_override(**parts):
        parts['area'].tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    check('native_chat_draw_has_no_errors', chat_view.last_error is None)


def capture_canvas():
    request = capture_request
    if request is None or bpy.context.area.as_pointer() != request['area']:
        return
    width, height = bpy.context.region.width, bpy.context.region.height
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE')
    pixels.dimensions = width * height * 4
    request.update(width=width, height=height, pixels=[value / 255 for value in pixels])


capture_handler = bpy.types.SpaceConsole.draw_handler_add(capture_canvas, (), 'WINDOW', 'POST_PIXEL')


def capture(name, dialog=False):
    global capture_request
    if dialog:
        with bpy.context.temp_override(**context_parts()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            bpy.ops.screen.screenshot(filepath=str(OUT / name))
        return
    capture_request = {'area': context_parts()['area'].as_pointer()}
    redraw()
    request, capture_request = capture_request, None
    assert 'pixels' in request, 'The actual chat framebuffer was not captured'
    image = bpy.data.images.new('Disposable workspace UI capture', width=request['width'], height=request['height'], alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(request['pixels'])
        image.filepath_raw, image.file_format = str(OUT / name), 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)


def event(kind, value='PRESS', x=40, y=40):
    parts = context_parts()
    region = parts['region']
    data = SimpleNamespace(type=kind, value=value, mouse_x=region.x + x, mouse_y=region.y + y,
                           mouse_prev_y=region.y + y, ctrl=False, oskey=False, shift=False, alt=False, unicode='')
    with bpy.context.temp_override(**parts):
        return chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, data)


def approval_shown(call_id):
    """Whether the pending call's approval card is drawn with a real Approve hit.

    The chat timer prepares a program's review rows off-draw
    (``approval_review.prepare_canvas_reviews``), so the card can appear a tick
    after the call starts waiting for approval.
    """
    if not controller.pending[call_id].approval:
        return False
    redraw()
    hits = chat_view._views[context_parts()['area'].as_pointer()]['hits']
    return any(found == 'approve' and argument == call_id for _, found, argument in hits)


def review_details_published(call_id):
    """Whether the canvas would now draw this program's complete Details.

    Asked with the draw's own geometry: ``review_rows`` returns nothing for a
    layout prepared for other geometry or display labels (target labels can
    arrive after the first preparation), and partial Details are never published.
    """
    parts = context_parts()
    area, region = parts['area'], parts['region']
    preview, details = approval_review.review_rows(
        controller.pending[call_id], area.as_pointer(), max(40, region.width - 64),
        max(12, int(area.spaces.active.font_size * 0.8)), chat_view._font, chat_view._mono, chat_view.wrap, True)
    return bool(preview) and bool(details)


def click(action, value=None):
    redraw()
    area = context_parts()['area']
    hits = chat_view._views[area.as_pointer()]['hits']
    rect = next(rect for rect, found, argument in hits if found == action and (value is None or argument == value))
    x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    event('LEFTMOUSE', 'PRESS', x, y)
    event('LEFTMOUSE', 'RELEASE', x, y)


def close_dialog(confirm=True):
    window.event_simulate(type='RET' if confirm else 'ESC', value='PRESS')
    window.event_simulate(type='RET' if confirm else 'ESC', value='RELEASE')


def track_dialog(class_name):
    cls = next(cls for cls in (*agent_workspace_ui._classes, *ui.CLASSES) if cls.__name__ == class_name)
    original = cls.invoke
    def invoke(operator, context, event):
        state['dialogs'][class_name] = operator
        return original(operator, context, event)
    cls.invoke = invoke


def create_program(name='Workspace UI staged cube'):
    return {'mode': 'PREVIEW', 'read_set': [], 'write_set': [], 'operations': [
        {'kind': 'create_primitive', 'target': None, 'parameters': {'primitive': 'CUBE', 'name': name, 'alias': 'ui_cube'}},
        {'kind': 'material_principled', 'target': '$ui_cube', 'parameters': {
            'name': 'Workspace UI blue', 'inputs': {'Base Color': [.1, .2, .3, 1], 'Roughness': .123456789012345}}}],
        'expected_checks': [{'kind': 'object_name', 'target': '$ui_cube', 'expected': name}]}


def enqueue(arguments, identity, checkpoint=None, tool='angelo_scene_program'):
    controller.busy, controller.cancelled = True, False
    controller.state['options']['approval'] = 'ASK'
    controller._begin_metrics()
    controller.request_checkpoint = checkpoint['id'] if checkpoint else ui.capture_checkpoint(controller)['id']
    controller.request_tool(identity, tool, arguments, state['responses'].append)
    state['call_id'] = controller.state['id'] + '/' + identity
    return controller.pending[state['call_id']]


def provider_finished():
    controller.busy = False
    controller._finish_metrics('completed')
    controller.version += 1


def finish(error=None):
    if state['done']:
        return None
    state['done'] = True
    if state.get('spent_budget'):
        state.pop('spent_budget').stop()
    log = Path(os.environ.get('ANGELO_QA_LOG', str(OUT) + '.log'))
    if log.is_file():
        bad_log = any(marker in log.read_text(errors='replace') for marker in ('BLI_assert failed', 'Segmentation fault'))
        report['checks']['no_native_gpu_assertions'] = not bad_log
        if bad_log:
            error = error or 'Native BLI assertion/segmentation failure: read-back success is not sufficient'
    report.update(success=error is None, ui_ticks=state['ui_ticks'], heartbeat_callbacks=state['heartbeats'],
                  provider_started=any(c.provider is not None for c in ui._controllers.values()),
                  retained_stage_main_ids=[scene.name for scene in bpy.data.scenes if scene.name.startswith('.Angelo')])
    if error:
        report['error'] = error
    report['phase'] = state['phase']
    (OUT / 'agent-workspace-ui.json').write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_AGENT_WORKSPACE_UI', json.dumps(report, default=str), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def pump_tick():
    if state['done']:
        return None
    try:
        state['ui_ticks'] += 1
        original_pump()
        return .07  # Deliberately leave a real input/redraw turn per native quantum.
    except Exception:
        return finish(traceback.format_exc())


def heartbeat():
    global window, controller, handler
    if state['done']:
        return None
    state['heartbeats'] += 1
    try:
        if time.monotonic() - state['started'] > 120:
            raise RuntimeError('Disposable native workspace UI fixture timed out')
        phase = state['phase']
        if phase == 'boot':
            check('public_native_program_is_registered', 'angelo_scene_program' in TOOL_MAP)
            window = bpy.context.window_manager.windows[0]
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'factory-workspace.blend'), check_existing=False)
            state['original_file_hash'] = hashlib.sha256((OUT / 'factory-workspace.blend').read_bytes()).hexdigest()
            bpy.ops.angelo.open_chat(side='RIGHT')
            state['phase'] = 'layout'
            return .5
        if ui._layout_timer is not None:
            return .05
        area = context_parts()['area']
        source = chat_view._views.setdefault(area.as_pointer(), {})
        if phase == 'layout':
            controller = ui.get_controller()
            state['original_image_names'] = {item.name for item in bpy.data.images}
            controller.refresh_models = lambda *args, **kwargs: None
            controller.state['messages'] = [{'role': 'user', 'text': 'Preview a native cube without changing the document.'}]
            with bpy.context.temp_override(**context_parts()):
                console.render(bpy.context, force=True)
                area.spaces.active.history[-1].body = 'Composer draft must survive native reviews.'
            handler = SimpleNamespace(window_pointer=window.as_pointer(), focused_area=area.as_pointer(), session=controller.state['id'],
                                      selected_all=False, anchor=None, dragging=False, pending_click=None)
            for name in ('ANGELO_OT_accept_native_preview', 'ANGELO_OT_project_brief', 'ANGELO_OT_performance_summary', 'ANGELO_OT_restore_checkpoint'):
                track_dialog(name)
            if bpy.app.timers.is_registered(original_pump):
                bpy.app.timers.unregister(original_pump)
            bpy.app.timers.register(pump_tick, first_interval=.07, persistent=True)
            enqueue(create_program(), 'preview-create')
            baseline = controller.checkpoints.folder(controller.request_checkpoint) / 'scene.blend'
            with bpy.data.libraries.load(str(baseline), link=False) as (baseline_data, unused):
                state['original_ids'] = {key: set(getattr(baseline_data, key))
                    for key in ('scenes', 'objects', 'meshes', 'materials', 'images')}
            state['phase'] = 'approval'
        elif phase == 'approval' and approval_shown(state['call_id']):
            capture('native-program-approval.png')
            click('approve', state['call_id'])
            check('approval_starts_stage_not_live_edit', bpy.data.objects.get('Workspace UI staged cube') is None)
            # One unit per poll keeps in-progress staging observable; later phases run fused.
            state['spent_budget'] = spent_budget()
            state['spent_budget'].start()
            state['phase'] = 'staging'
        elif phase == 'staging' and controller.native_jobs and next(iter(controller.native_jobs.values())).stage is not None:
            capture('native-job-progress-stop.png')
            check('native_progress_has_a_real_stop_control', any(action == 'stop_native_job' for _, action, _ in source['hits']))
            job = next(iter(controller.native_jobs.values()))
            check('suspended_native_job_has_no_live_staging_main_ids', not native_programs.runtime_is_staging())
            point = ui.capture_checkpoint(controller)
            with bpy.data.libraries.load(str(controller.checkpoints.folder(point['id']) / 'scene.blend'), link=False) as (source_data, unused):
                clean = all(set(getattr(source_data, key)) == names for key, names in state['original_ids'].items())
            check('snapshot_between_yields_contains_no_staged_scene_object_material_ids', clean)
            with native_programs.job_context(job.id):
                check('hydrated_native_job_exposes_real_main_stage_for_guard_test', native_programs.runtime_is_staging())
                try:
                    ui.capture_checkpoint(controller)
                    raise AssertionError('A request snapshot included hydrated staging Main IDs')
                except RuntimeError as error:
                    check('snapshot_with_actual_hydrated_stage_fails_closed_actionably', 'Finish the current native program or press Stop' in str(error))
            check('native_inspection_context_cleans_temporary_main_ids', not native_programs.runtime_is_staging())
            state.pop('spent_budget').stop()
            state['phase'] = 'await_preview'
        elif phase == 'await_preview' and state['responses']:
            result = state['responses'][-1]
            check('preview_returns_an_actual_retained_stage', result.get('status') == 'preview' and bool(result.get('preview_id')))
            check('preview_is_not_a_live_scene_change', result.get('scene_changed') is False and bpy.data.objects.get('Workspace UI staged cube') is None)
            provider_finished()
            state['preview_id'] = result['preview_id']
            descriptor = controller.inspect_native_preview(state['preview_id'])
            check('native_preview_pixels_prewarmed_in_main_thread_pump', native_preview_images.prepared(descriptor, controller))
            check('native_preview_decode_leaves_no_image_main_ids', {item.name for item in bpy.data.images} == state['original_image_names'])
            capture('native-staged-preview-accept-discard.png')
            check('native_preview_draw_leaves_no_image_main_ids', {item.name for item in bpy.data.images} == state['original_image_names'])
            check('retained_preview_has_real_accept_discard_hits', {'accept_native_preview', 'discard_native_preview'} <= {action for _, action, _ in source['hits']})
            click('accept_native_preview')
            state['phase'] = 'unchecked_review'
            return .35
        elif phase == 'unchecked_review':
            operator = state['dialogs']['ANGELO_OT_accept_native_preview']
            check('accept_review_starts_unchecked', not operator.review_confirmed)
            text = '\n'.join(line.body for line in operator.review_lines)
            check('accept_review_contains_exact_material_controls_checks', 'Roughness: 0.123456789012345' in text and 'Expected checks' in text)
            capture('exact-native-preview-review.png', dialog=True)
            from angelo_agent import card_host
            check('review_is_an_angelo_card_not_a_stock_dialog', card_host.is_active('native_review'))
            view = bpy.context.preferences.view
            if bpy.app.build_options.international and 'ar_EG' in {item.identifier for item in view.bl_rna.properties['language'].enum_items}:
                view.language, view.use_translate_interface = 'ar_EG', True
                state['phase'] = 'unchecked_review_arabic'
                return .4
            close_dialog()
            state['phase'] = 'unchecked_closed'
            return .35
        elif phase == 'unchecked_review_arabic':
            capture('exact-native-preview-review-arabic.png', dialog=True)
            bpy.context.preferences.view.language = 'en_US'
            close_dialog()
            state['phase'] = 'unchecked_closed'
            return .35
        elif phase == 'unchecked_closed':
            check('unchecked_confirmation_never_commits', not controller.busy and bpy.data.objects.get('Workspace UI staged cube') is None)
            click('accept_native_preview')
            state['phase'] = 'confirmed_review'
            return .3
        elif phase == 'confirmed_review':
            state['dialogs']['ANGELO_OT_accept_native_preview'].review_confirmed = True
            close_dialog()
            state['phase'] = 'await_commit'
        elif phase == 'await_commit' and not controller.busy and bpy.data.objects.get('Workspace UI staged cube') is None:
            errors = [entry.get('result', {}).get('error') for identity, entry in controller.state['results'].items() if '/ui-preview/' in identity]
            if any(errors):
                raise AssertionError('Actual preview acceptance failed: ' + str(errors[-1]))
        elif phase == 'await_commit' and not controller.busy and bpy.data.objects.get('Workspace UI staged cube') is not None:
            check('explicit_accept_commits_real_staged_geometry', True)
            controller.pump_events()
            messages = controller.state['messages']
            intent = next(index for index, message in enumerate(messages) if message.get('request_kind') == 'native_preview_accept')
            accepted = next(index for index, message in enumerate(messages) if message.get('tool') == 'angelo_accept_preview' and message.get('result', {}).get('scene_changed') is True)
            check('human_accept_intent_precedes_actual_committed_activity', intent < accepted)
            capture('native-preview-committed.png')
            with bpy.context.temp_override(**context_parts(chat=False)):
                check('one_native_undo_operator_finishes', 'FINISHED' in bpy.ops.ed.undo())
            check('one_undo_reverts_entire_accepted_program', bpy.data.objects.get('Workspace UI staged cube') is None)
            check('undo_marks_scene_evidence_stale', controller.state['task_memory']['stale'] and 'Undo' in controller.state['task_memory']['stale_reason'])
            with bpy.context.temp_override(**context_parts()):
                bpy.ops.angelo.project_brief('INVOKE_DEFAULT')
            state['phase'] = 'brief'
            return .3
        elif phase == 'brief':
            operator = state['dialogs']['ANGELO_OT_project_brief']
            operator.objective, operator.style = 'Build a blue studio prop', 'Clean stylized'
            operator.constraints = 'Keep the original user scene intact'
            operator.planned_steps = 'Review before accepting'
            close_dialog()
            state['phase'] = 'brief_saved'
            return .3
        elif phase == 'brief_saved':
            check('project_brief_editor_saves_real_structured_notes', controller.state['task_memory']['brief']['objective'] == 'Build a blue studio prop')
            check('brief_does_not_change_authorization', controller.state['options']['approval'] == 'ASK')
            check('native_reviews_preserve_composer_draft', area.spaces.active.history[-1].body == 'Composer draft must survive native reviews.')
            with bpy.context.temp_override(**context_parts()):
                bpy.ops.angelo.performance_summary('INVOKE_DEFAULT')
            state['phase'] = 'metrics'
            return .3
        elif phase == 'metrics':
            text = '\n'.join(line.body for line in state['dialogs']['ANGELO_OT_performance_summary'].review_lines)
            check('performance_viewer_reads_real_local_measurements', 'Measured native execution' in text and 'Native commits' in text)
            capture('native-performance-summary.png', dialog=True)
            close_dialog()
            state['phase'] = 'metrics_closed'
            return .3
        elif phase == 'metrics_closed':
            state['responses'] = []
            enqueue(create_program('Workspace UI discard cube'), 'preview-discard')
            state['phase'] = 'discard_approval'
        elif phase == 'discard_approval' and approval_shown(state['call_id']):
            click('approve', state['call_id'])
            state['phase'] = 'discard_preview'
        elif phase == 'discard_preview' and state['responses']:
            provider_finished()
            identity = state['responses'][-1]['preview_id']
            click('discard_native_preview', (controller.state['id'], identity))
            check('discard_control_frees_real_stage_without_live_mutation', not controller.native_previews() and bpy.data.objects.get('Workspace UI discard cube') is None)
            state['responses'] = []
            enqueue(create_program('Workspace UI stopped cube'), 'preview-stop')
            state['phase'] = 'stop_approval'
        elif phase == 'stop_approval' and approval_shown(state['call_id']):
            click('approve', state['call_id'])
            state['spent_budget'] = spent_budget()
            state['spent_budget'].start()
            state['phase'] = 'stop_active'
        elif phase == 'stop_active' and controller.native_jobs:
            click('stop_native_job')
            state.pop('spent_budget').stop()
            check('stop_control_cancels_actual_native_stage', not controller.pending and not controller.native_jobs and state['responses'][-1].get('cancelled'))
            check('stop_never_applies_staged_geometry', bpy.data.objects.get('Workspace UI stopped cube') is None)
            target = bpy.data.objects['Cube']
            handle, revision = scene_queries.object_handle(target), scene_queries.object_revision(window, target)
            arguments = {'mode': 'PREVIEW', 'read_set': [{'handle': handle, 'revision': revision}],
                         'write_set': [{'handle': handle, 'revision': revision}], 'operations': [
                             {'kind': 'transform', 'target': handle, 'parameters': {'location': [2, 0, 0]}}],
                         'expected_checks': [{'kind': 'transform', 'target': handle, 'property': 'location', 'expected': [2, 0, 0], 'tolerance': .000001}]}
            state['responses'] = []
            enqueue(arguments, 'preview-stale')
            state['phase'] = 'stale_approval'
        elif phase == 'stale_approval' and approval_shown(state['call_id']):
            click('approve', state['call_id'])
            state['phase'] = 'stale_preview'
        elif phase == 'stale_preview' and state['responses']:
            check('stale_fixture_produced_a_preview', state['responses'][-1].get('status') == 'preview')
            provider_finished()
            click('accept_native_preview')
            state['phase'] = 'stale_review'
            return .3
        elif phase == 'stale_review':
            bpy.data.objects['Cube'].location.x = .25
            window.view_layer.update()
            state['dialogs']['ANGELO_OT_accept_native_preview'].review_confirmed = True
            close_dialog()
            state['phase'] = 'stale_rejected'
            return .4
        elif phase == 'stale_rejected':
            check('fresh_revision_rejects_stale_accept_without_overwrite', abs(bpy.data.objects['Cube'].location.x - .25) < 1e-7 and not controller.busy)
            check('factory_fixture_never_started_a_provider', all(c.provider is None for c in ui._controllers.values()))
            capture('stale-preview-rejected.png')
            controller._turn_checkpoint_policy = {'capture_mode': 'EVERY_REQUEST'}
            state['every_x'] = bpy.data.objects['Cube'].location.x
            point = ui.capture_checkpoint(controller)
            controller.state['messages'].append({'role': 'user', 'text': 'Every Request restore-boundary fixture',
                'checkpoint': point['id'], 'checkpoint_scope': 'before_request'})
            controller.version += 1
            bpy.data.objects['Cube'].location.x += 1
            window.view_layer.update()
            with bpy.context.temp_override(**context_parts()):
                bpy.ops.angelo.restore_checkpoint('INVOKE_DEFAULT', checkpoint_id=point['id'])
            state['phase'] = 'every_restore_review'
            return .3
        elif phase == 'every_restore_review':
            review = state['dialogs']['ANGELO_OT_restore_checkpoint']._snapshot_review
            check('every_request_restore_review_states_actual_pre_request_boundary', review['scope'] == 'before_request' and 'before this request' in review['question'])
            capture('every-request-restore-review.png', dialog=True)
            close_dialog()
            state['phase'] = 'every_restored'
            return .5
        elif phase == 'every_restored':
            check('every_request_restore_reverts_manual_edit_after_request', abs(bpy.data.objects['Cube'].location.x - state['every_x']) < 1e-7)
            check('native_restore_rebinds_readable_chat_fonts', blf.dimensions(chat_view._font, 'Composer draft stays visible')[0] > 0)
            controller._turn_checkpoint_policy = {'capture_mode': 'BEFORE_FIRST_EDIT'}
            request = {'role': 'user', 'text': 'Deferred native-staging restore-boundary fixture', 'checkpoint_scope': 'before_first_approved_edit'}
            controller.state['messages'].append(request)
            bpy.data.objects['Cube'].location.x += 1
            window.view_layer.update()
            state['deferred_x'] = bpy.data.objects['Cube'].location.x
            point = ui.capture_checkpoint(controller)
            request.update(checkpoint=point['id'], checkpoint_scope='before_native_staging')
            controller.version += 1
            state['deferred_checkpoint'] = point['id']
            target = bpy.data.objects['Cube']
            handle, revision = scene_queries.object_handle(target), scene_queries.object_revision(window, target)
            location = [state['deferred_x'] + 1, 0, 0]
            arguments = {'mode': 'APPLY', 'read_set': [{'handle': handle, 'revision': revision}],
                'write_set': [{'handle': handle, 'revision': revision}], 'operations': [
                    {'kind': 'transform', 'target': handle, 'parameters': {'location': location}}],
                'expected_checks': [{'kind': 'transform', 'target': handle, 'property': 'location', 'expected': location, 'tolerance': .000001}]}
            state['responses'] = []
            enqueue(arguments, 'deferred-restore-edit', checkpoint=point)
            state['phase'] = 'deferred_restore_approval'
        elif phase == 'deferred_restore_approval' and approval_shown(state['call_id']):
            call = controller.pending[state['call_id']]
            check('approval_preserves_full_id_with_friendly_display_name', call.target_labels.get(call.arguments['write_set'][0]['handle']) == 'Cube')
            click('approve', state['call_id'])
            state['phase'] = 'deferred_restore_applied'
        elif phase == 'deferred_restore_applied' and state['responses']:
            check('deferred_restore_fixture_applies_real_approved_native_edit', state['responses'][-1].get('scene_changed') is True)
            provider_finished()
            with bpy.context.temp_override(**context_parts()):
                bpy.ops.angelo.restore_checkpoint('INVOKE_DEFAULT', checkpoint_id=state['deferred_checkpoint'])
            state['phase'] = 'deferred_restore_review'
            return .3
        elif phase == 'deferred_restore_review':
            review = state['dialogs']['ANGELO_OT_restore_checkpoint']._snapshot_review
            check('deferred_restore_review_does_not_promise_before_message', review['scope'] == 'before_native_staging' and 'before this request' not in review['question'])
            check('deferred_restore_review_explains_manual_edits_are_included', 'includes edits you made' in review['note'])
            capture('deferred-native-staging-restore-review.png', dialog=True)
            close_dialog()
            state['phase'] = 'deferred_restored'
            return .5
        elif phase == 'deferred_restored':
            check('deferred_restore_retains_manual_edit_after_request_before_staging', abs(bpy.data.objects['Cube'].location.x - state['deferred_x']) < 1e-7)
            check('repeated_native_restore_preserves_readable_chat_fonts', blf.dimensions(chat_view._font, 'Composer draft stays visible')[0] > 0)
            notices = [message['text'] for message in controller.state['messages'] if message['role'] == 'system' and message['text'].startswith('Restored the document')]
            check('deferred_restore_success_notice_uses_actual_staging_boundary', bool(notices) and 'before Angelo started working on' in notices[-1] and 'before that request' not in notices[-1])
            check('repeated_restore_keeps_original_user_project_unchanged',
                hashlib.sha256((OUT / 'factory-workspace.blend').read_bytes()).hexdigest() == state['original_file_hash'])
            capture('deferred-restore-preserved-manual-edit.png')
            target = bpy.data.objects['Cube']
            revision = scene_queries.object_revision(window, target)
            state['recipe_before'] = (revision, len(target.material_slots))
            state['responses'] = []
            enqueue({'recipe': 'material-setup', 'target': scene_queries.object_handle(target), 'revision': revision,
                     'parameters': {'name': 'Workspace UI recipe red', 'base_color': [.8, .1, .1, 1], 'roughness': .3125},
                     'mode': 'PREVIEW'}, 'recipe-preview', tool='angelo_apply_recipe')
            state['phase'] = 'recipe_approval'
        elif phase == 'recipe_approval' and approval_shown(state['call_id']):
            call = controller.pending[state['call_id']]
            target = bpy.data.objects['Cube']
            check('recipe_expanded_on_host_before_approval', call.program_arguments is not None
                  and call.program_arguments['mode'] == 'PREVIEW')
            check('recipe_nothing_staged_before_approval', not controller.native_jobs and not native_programs.runtime_is_staging()
                  and (scene_queries.object_revision(window, target), len(target.material_slots)) == state['recipe_before'])
            click('review', state['call_id'])  # Details: the approval card's full review.
            state['phase'] = 'recipe_details'
        elif phase == 'recipe_details' and review_details_published(state['call_id']):
            # The card draws the rows its review prepared; record exactly those the draw received.
            drawn, original_rows = [], approval_review.review_rows

            def recording_rows(*args, **kwargs):
                rows = original_rows(*args, **kwargs)
                drawn.append(''.join(text for part in rows for text, _ in part))  # Wrapped rows rejoined.
                return rows
            approval_review.review_rows = recording_rows
            try:
                redraw()
            finally:
                approval_review.review_rows = original_rows
            controls = ['material-setup recipe · Preview 1 change', 'Kind: material_principled',
                        'Name: Workspace UI recipe red', 'Roughness: 0.3125', 'Kind: material_assignment',
                        'Kind: material_values', 'Kind: finite_coordinates', state['recipe_before'][0],
                        'Recipe inputs, defaults included', 'Metallic: 0']
            check('recipe_approval_card_draws_every_expanded_control', any(all(text in value for text in controls)
                                                                           for value in drawn if isinstance(value, str)))
            capture('recipe-approval-details.png')
            click('approve', state['call_id'])
            state['phase'] = 'recipe_preview'
        elif phase == 'recipe_preview' and state['responses']:
            result = state['responses'][-1]
            target = bpy.data.objects['Cube']
            check('approved_recipe_stages_the_reviewed_preview', result.get('status') == 'preview'
                  and result.get('live_revisions', {}).get('status') == 'UNCHANGED_UNTIL_ACCEPT'
                  and (scene_queries.object_revision(window, target), len(target.material_slots)) == state['recipe_before'])
            provider_finished()
            click('discard_native_preview', (controller.state['id'], result['preview_id']))
            check('recipe_preview_discard_leaves_scene_unchanged', not controller.native_previews()
                  and len(target.material_slots) == state['recipe_before'][1])
            return finish()
        return .04
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(heartbeat, first_interval=.3, persistent=True)
