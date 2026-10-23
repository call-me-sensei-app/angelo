# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real native scissored approval/retained-preview controls and composer input.

GUI --factory-startup --enable-event-simulate only. All snapshots, preferences,
accounts and quit recovery are disposable; no providers/auth/paid calls/builds.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

import addon_utils
import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-chat-hit-testing'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
    ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import ui, console, chat_view, approval_review
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
from angelo_agent.chat_hit_testing import contains
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

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
original_pump = ui.pump
state = {'phase': 'boot', 'started': time.monotonic(), 'responses': [], 'dispatches': [], 'done': False}
report = {'checks': {}, 'paid_requests': 0, 'provider_started': False, 'binary': bpy.app.binary_path}
window = controller = handler = None
original_dispatch = chat_view.dispatch_action


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def parts():
    area = next(area for area in window.screen.areas if console.is_chat(area))
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return {'window': window, 'area': area, 'region': region}


def redraw(scroll=None):
    context = parts()
    if scroll is not None:
        chat_view._scroll[controller.state['id']] = scroll
    with bpy.context.temp_override(**context):
        context['area'].tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    check('native_renderer_has_no_errors', chat_view.last_error is None)
    return chat_view._views[context['area'].as_pointer()]


def event(kind, value, x, y):
    context = parts()
    region = context['region']
    data = SimpleNamespace(type=kind, value=value, mouse_x=region.x + x, mouse_y=region.y + y,
        mouse_prev_y=region.y + y, ctrl=False, oskey=False, shift=False, alt=False, unicode='')
    with bpy.context.temp_override(**context):
        return chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, data)


def review_details_published(call):
    """Whether the canvas would now draw this program's complete expanded Details.

    The chat timer prepares a program's review rows off-draw and publishes its
    Details only once complete (``approval_review.advance_review_layout``); the
    clipping fixture needs the full-height expanded card. Asked with the draw's
    own geometry: ``review_rows`` returns nothing for a layout prepared for other
    geometry or display labels (target labels can arrive after the first preparation).
    """
    context = parts()
    area, region = context['area'], context['region']
    preview, details = approval_review.review_rows(
        call, area.as_pointer(), max(40, region.width - 64), max(12, int(area.spaces.active.font_size * 0.8)),
        chat_view._font, chat_view._mono, chat_view.wrap, True)
    return bool(preview) and bool(details)


def click_hit(action):
    view = redraw(0)
    rect, _, _ = next(item for item in view['hits'] if item[1] == action)
    x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    event('LEFTMOUSE', 'PRESS', x, y)
    event('LEFTMOUSE', 'RELEASE', x, y)


def record_dispatch(context, target, action, value):
    state['dispatches'].append(action)
    return original_dispatch(context, target, action, value)


chat_view.dispatch_action = record_dispatch


def screenshot(name):
    with bpy.context.temp_override(**parts()):
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def clipping_checks(actions, prefix):
    view = redraw(0)
    original = {action: rect for rect, action, _ in view['hits'] if action in actions}
    check(prefix + '_all_real_controls_visible_at_bottom', set(original) == set(actions))
    center = original[actions[0]]
    x, y = center[0] + center[2] / 2, center[1] + center[3] / 2
    before = len(state['dispatches'])
    event('LEFTMOUSE', 'PRESS', x, y)
    view = redraw(100)
    # The redraw must really have hidden the pressed control before its release
    # is judged: a release on a control still drawn under the pointer is an
    # ordinary click, so a card too short to scroll would make the next check vacuous.
    check(prefix + '_all_hidden_action_hits_are_removed', not any(action in actions for _, action, _ in view['hits']))
    event('LEFTMOUSE', 'RELEASE', x, y)
    check(prefix + '_press_redraw_hidden_release_never_dispatches', len(state['dispatches']) == before)
    for action, rect in original.items():
        x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2 - 100
        check(prefix + '_' + action + '_old_hidden_pixels_overlap_composer', contains(view['composer'], x, y))
        event('LEFTMOUSE', 'PRESS', x, y)
        check(prefix + '_' + action + '_composer_gains_caret_not_action', handler.dragging and handler.pending_click is None)
        event('LEFTMOUSE', 'RELEASE', x, y)
    check(prefix + '_composer_clicks_never_dispatch_hidden_actions', len(state['dispatches']) == before)
    screenshot(prefix + '-fully-hidden-composer.png')
    view = redraw(40)
    partial = {action: rect for rect, action, _ in view['hits'] if action in actions}
    check(prefix + '_partial_visible_control_is_clipped', set(partial) == set(actions)
        and all(rect[1] == view['message_viewport'][1] and 0 < rect[3] < 28 for rect in partial.values()))
    if prefix == 'preview':
        rect = partial['expand']
        x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        event('LEFTMOUSE', 'PRESS', x, y)
        event('LEFTMOUSE', 'RELEASE', x, y)
        check('partial_visible_preview_details_really_dispatches', state['dispatches'][-1] == 'expand')
        redraw(0)
        click_hit('expand')
        view = redraw(40)
    screenshot(prefix + '-clipped-composer.png')
    redraw(0)


def finish(error=None):
    state['done'] = True
    report.update(success=error is None, error=error, phase=state['phase'], dispatches=state['dispatches'],
        provider_started=any(item.provider is not None for item in ui._controllers.values()),
        retained_stage_main_ids=[scene.name for scene in bpy.data.scenes if scene.name.startswith('.Angelo')])
    log = Path(os.environ.get('ANGELO_QA_LOG', str(OUT) + '.log'))
    if log.is_file() and any(marker in log.read_text(errors='replace') for marker in ('BLI_assert failed', 'Segmentation fault')):
        report.update(success=False, error='Native assertion/segmentation failure')
    (OUT / 'chat-hit-testing.json').write_text(json.dumps(report, indent=2))
    print('ANGELO_CHAT_HIT_TESTING', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def pump():
    if state['done']:
        return None
    original_pump()
    return .1


def tick():
    global window, controller, handler
    try:
        if time.monotonic() - state['started'] > 100:
            raise RuntimeError('Native hit-testing fixture timed out')
        if state['phase'] == 'boot':
            if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
                return .1
            window = bpy.context.window_manager.windows[0]
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'factory.blend'), check_existing=False)
            bpy.ops.angelo.open_chat(side='RIGHT')
            state['phase'] = 'layout'
            return .5
        if not qa_boot.started():
            return .05
        context = parts()
        if state['phase'] == 'layout':
            controller = ui.get_controller()
            controller.refresh_models = lambda *args, **kwargs: None
            controller.state['messages'] = []
            controller.state['options']['approval'] = 'ASK'
            controller.request_checkpoint = ui.capture_checkpoint(controller)['id']
            controller.busy = True
            arguments = {'mode': 'PREVIEW', 'read_set': [], 'write_set': [],
                'operations': [{'kind': 'create_primitive', 'target': None, 'parameters': {
                    'primitive': 'CUBE', 'name': 'Hit fixture cube ' + str(index), 'alias': 'cube_' + str(index)}} for index in range(32)],
                'expected_checks': [{'kind': 'object_name', 'target': '$cube_' + str(index),
                    'expected': 'Hit fixture cube ' + str(index)} for index in range(32)]}
            controller.request_tool('clipped-program', 'angelo_scene_program', arguments, state['responses'].append)
            with bpy.context.temp_override(**context):
                console.render(bpy.context, force=True)
                context['area'].spaces.active.history[-1].body = 'A hidden button must not discard my stage.'
            handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window.as_pointer(),
                                           focused_area=context['area'].as_pointer(), session=controller.state['id'],
                                           selected_all=False, anchor=None, dragging=False, pending_click=None)
            bpy.app.timers.unregister(original_pump)
            bpy.app.timers.register(pump, first_interval=.1, persistent=True)
            state['phase'] = 'approval'
        elif state['phase'] == 'approval' and next(iter(controller.pending.values())).approval:
            call = next(iter(controller.pending.values()))
            redraw()['expanded'].add(('approval', call.id))
            state['phase'] = 'approval_details'
        elif state['phase'] == 'approval_details' and review_details_published(next(iter(controller.pending.values()))):
            call = next(iter(controller.pending.values()))
            clipping_checks(('approve', 'decline', 'review'), 'approval')
            check('clipped_approval_still_pending_and_document_unchanged', call.approval and not any(obj.name.startswith('Hit fixture') for obj in bpy.data.objects))
            click_hit('approve')
            state['phase'] = 'preview'
        elif state['phase'] == 'preview' and state['responses']:
            result = state['responses'][-1]
            check('real_native_job_returns_retained_long_preview', result.get('status') == 'preview' and len(result['delta']['added']) == 32)
            controller.busy = False
            controller.pump_events()
            # The card's plain summary is short by design: open its exact Details so the card is taller than the
            # chat and scrolling really hides the controls (a card too short to scroll makes the checks vacuous).
            redraw()['expanded'].add(('native_preview', controller.native_previews()[0]['preview_id']))
            clipping_checks(('accept_native_preview', 'discard_native_preview', 'expand'), 'preview')
            check('composer_input_preserves_actual_retained_stage', bool(controller.native_previews()))
            view = redraw(0)
            rect = next(rect for rect, action, _ in view['hits'] if action == 'discard_native_preview')
            x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
            before = len(state['dispatches'])
            event('LEFTMOUSE', 'PRESS', x, y)
            event('WHEELUPMOUSE', 'PRESS', x, y)
            event('LEFTMOUSE', 'RELEASE', x, y)
            check('wheel_during_press_cancels_pending_native_action', len(state['dispatches']) == before and bool(controller.native_previews()))
            click_hit('discard_native_preview')
            check('visible_discard_control_really_frees_stage', not controller.native_previews())
            check('no_live_geometry_or_provider_was_created', not any(obj.name.startswith('Hit fixture') for obj in bpy.data.objects) and controller.provider is None)
            screenshot('visible-discard-completed.png')
            return finish()
        return .05
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(tick, first_interval=.3, persistent=True)
