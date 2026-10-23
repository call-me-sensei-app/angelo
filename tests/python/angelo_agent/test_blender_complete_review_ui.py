# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled native GUI evidence for complete, cooperative approval reviews.

Use a disposable GUI app/profile with --factory-startup --enable-event-simulate
--disable-autoexec. This fixture never focuses another window, starts a provider,
or stages its display-only large programs. Real chat draw handlers, GPU pixels,
BLF calls, locale preferences and the canvas preparation timer run on the main
thread. Measured timer slices are not promises about native font-call latency.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from collections import defaultdict
from types import SimpleNamespace

import addon_utils
import blf
import bpy
import gpu

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-complete-review-ui'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_INTRO='1',
                  ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'))
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import (approval_review, approvals, agent_workspace_ui, canvas_rtl, canvas_wrap,
                          chat_view, console, native_programs, program_checks, rtl_text, ui, ui_text)  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.program_context import ProgramBinding  # noqa: E402
from angelo_agent.program_material_obligations import GUARD  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

report = {'checks': {}, 'cases': {}, 'screenshots': [], 'paid_requests': 0, 'success': False,
          'binary': bpy.app.binary_path, 'version': bpy.app.version_string,
          'scope': 'real GUI/font/timer review; large programs are display-only and never execute',
          'timing_limit': '4 ms requested cooperative work; one font/shaping/control step may exceed it',
          'timing_trials_per_workload_and_text_width': 5,
          'baseline_oracle': 'previous whole-paragraph binary search with real BLF, ordinary cases only',
          'timing_instrumentation': 'Python wrappers observe draw, BLF, shaping and timer calls; snapshot timings include instrumentation'}
state = {'phase': 'boot', 'started': time.monotonic(), 'done': False, 'tag': None,
         'in_draw': False, 'draw_text': [], 'last_draw_text': [], 'cold_slices': defaultdict(list),
         'draws': defaultdict(list), 'font': {}, 'heavy_draw_calls': defaultdict(int), 'responses': []}
window = controller = handler = steps = None
capture_request = None
originals = []


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def scene_signature():
    return [{'name': obj.name, 'type': obj.type, 'location': list(obj.location), 'scale': list(obj.scale),
             'rotation': list(obj.rotation_euler),
             'coordinates': [list(vertex.co) for vertex in obj.data.vertices] if obj.type == 'MESH' else None}
            for obj in sorted(window.scene.objects, key=lambda obj: obj.name)]


def wrap_function(module, name, wrapper):
    original = getattr(module, name)
    originals.append((module, name, original))
    setattr(module, name, wrapper(original))


def expensive(label):
    def decorate(original):
        def counted(*args, **kwargs):
            if state['in_draw']:
                state['heavy_draw_calls'][label] += 1
            return original(*args, **kwargs)
        return counted
    return decorate


def measured_font(label):
    def decorate(original):
        def measured(*args, **kwargs):
            if state['tag'] is None:
                return original(*args, **kwargs)
            assert threading.current_thread() is threading.main_thread()
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                key = state['tag'] + '/' + label
                record = state['font'].setdefault(key, {'calls': 0, 'total_ms': 0., 'max_ms': 0., 'max_input_chars': 0})
                record['calls'] += 1
                record['total_ms'] += elapsed
                record['max_ms'] = max(record['max_ms'], elapsed)
                record['max_input_chars'] = max(record['max_input_chars'],
                                                max((len(arg) for arg in args if isinstance(arg, str)), default=0))
        return measured
    return decorate


def measured_draw(original):
    def drawn():
        is_chat = bpy.context.area is not None and console.is_chat(bpy.context.area)
        if not is_chat:
            return original()
        state['in_draw'], state['draw_text'] = True, []
        started = time.perf_counter()
        try:
            return original()
        finally:
            state['in_draw'] = False
            state['last_draw_text'] = state['draw_text']
            if state['tag']:
                state['draws'][state['tag']].append({'ms': (time.perf_counter() - started) * 1000,
                                                   'text_rows': len(state['draw_text'])})
    return drawn


def recorded_text(original):
    def draw_text(value, x, y, *args, **kwargs):
        if state['in_draw']:
            state['draw_text'].append({'value': value, 'y': y})
        return original(value, x, y, *args, **kwargs)
    return draw_text


def measured_advance(original):
    def advance(call, owner, width, size, font, mono, iterator, deadline):
        started = time.perf_counter()
        requested_ms = max(0., (deadline - time.monotonic()) * 1000)
        result = original(call, owner, width, size, font, mono, iterator, deadline)
        review = getattr(call, '_approval_review', None)
        layout = review.layouts.get(owner) if review else None
        if state['tag']:
            state['cold_slices'][state['tag']].append({
                'ms': (time.perf_counter() - started) * 1000, 'requested_ms': requested_ms,
                'pending': bool(result), 'published_summary_rows': len(layout.preview) if layout else 0,
                'published_detail_rows': len(layout.details) if layout else 0})
        if layout is not None and layout.iterator is not None:
            assert not layout.details, 'An unfinished detail prefix was advertised as complete'
        return result
    return advance


# Instrument before registration: Blender stores the actual callback object.
wrap_function(chat_view, 'draw', measured_draw)
wrap_function(chat_view, 'text', recorded_text)
wrap_function(approval_review, 'advance_review_layout', measured_advance)
for module, name in ((program_checks, 'required_checks'), (program_checks, 'checks_digest'),
                     (program_checks, 'resolve_obligations'), (approvals, '_review_obligations'),
                     (program_checks.FrozenObligations, 'checks'), (program_checks.FrozenObligations, 'facts'),
                     (program_checks.FrozenObligations, 'effects'), (program_checks.FrozenObligations, 'unknowns'),
                     (approval_review, 'prepare_review'), (approval_review, '_review_arguments'),
                     (approval_review, '_immutable'), (approvals, 'approval_details'),
                     (agent_workspace_ui, 'iter_control_lines'), (copy, 'deepcopy'), (hashlib, 'sha256')):
    wrap_function(module, name, expensive(module.__name__ + '.' + name))
wrap_function(blf, 'dimensions', measured_font('blf.dimensions'))
for name in ('to_visual', 'to_logical', 'is_visual'):
    wrap_function(rtl_text, name, measured_font('rtl_text.' + name))

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
ui.connection_available = lambda: True
ui.prepare_controller = lambda: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
bpy.context.preferences.filepaths.file_preview_type = 'NONE'
(OUT / 'runtime-temp').mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / 'runtime-temp')


def parts():
    area = next(area for area in window.screen.areas if console.is_chat(area))
    return {'window': window, 'area': area, 'region': next(region for region in area.regions if region.type == 'WINDOW')}


def geometry():
    context = parts()
    area, region = context['area'], context['region']
    return area.as_pointer(), max(40, region.width - 64), max(12, int(area.spaces.active.font_size * .8))


def redraw():
    with bpy.context.temp_override(**parts()):
        bpy.context.area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    check('actual_canvas_draw_has_no_error', chat_view.last_error is None)


def capture_canvas():
    request = capture_request
    if request is None or bpy.context.area.as_pointer() != request['area']:
        return
    width, height = bpy.context.region.width, bpy.context.region.height
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE')
    pixels.dimensions = width * height * 4
    request.update(width=width, height=height, pixels=[value / 255 for value in pixels])


capture_handler = bpy.types.SpaceConsole.draw_handler_add(capture_canvas, (), 'WINDOW', 'POST_PIXEL')


def capture(name):
    global capture_request
    capture_request = {'area': parts()['area'].as_pointer()}
    redraw()
    request, capture_request = capture_request, None
    assert 'pixels' in request, 'No actual native chat framebuffer was captured'
    image = bpy.data.images.new('Disposable complete review capture', width=request['width'], height=request['height'], alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(request['pixels'])
        path = OUT / name
        image.filepath_raw, image.file_format = str(path), 'PNG'
        image.save()
        report['screenshots'].append(str(path))
    finally:
        bpy.data.images.remove(image)


def event(kind, value='PRESS', x=40, y=40):
    """Direct handler probe for bounded scroll geometry, not native event delivery."""
    context = parts()
    region = context['region']
    data = SimpleNamespace(type=kind, value=value, mouse_x=region.x + x, mouse_y=region.y + y,
                           mouse_prev_y=region.y + y, ctrl=False, oskey=False, shift=False, alt=False, unicode='')
    with bpy.context.temp_override(**context):
        return chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, data)


def native_event(kind, value, x, y):
    region = parts()['region']
    window.event_simulate(type=kind, value=value, x=int(region.x + x), y=int(region.y + y))


def hit(action, call):
    view = chat_view._views[parts()['area'].as_pointer()]
    rect = next(rect for rect, found, argument in view['hits'] if found == action and argument == call.id)
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def operation(kind, target=None, **parameters):
    return {'kind': kind, 'target': target, 'parameters': parameters}


def ordinary():
    return validate_program({'mode': 'APPLY', 'operations': [operation(
        'create_primitive', primitive='CUBE', name='Review ordinary cube', alias='cube')], 'expected_checks': []})


def largest_material():
    handles = ['scn:' + 'a' * 32 + ':' + str(index + 1) for index in range(63)]
    return validate_program({'mode': 'APPLY',
        'write_set': [{'handle': handle, 'revision': 'b' * 64} for handle in handles],
        'operations': [operation('material_principled', handles[index],
                                name='Review material %02d' % index if index < 31 else 'LAST_REQUIRED_MATERIAL',
                                also=handles[32:]) for index in range(32)],
        'expected_checks': [{'kind': 'object_count', 'minimum': 0, 'maximum': 100 + index} for index in range(32)]})


def text_program(value, *, large=False):
    handle = 'scn:' + 'a' * 32 + ':1'
    edits = [{'op': 'set', 'node': 'Node %02d' % index,
              'settings': {'setting_%02d' % setting: value for setting in range(31 if large else 1)}}
             for index in range(32 if large else 1)]
    if large:
        edits[-1]['settings']['setting_30'] = 'LAST_REQUIRED_VALUE' + 'x' * (1024 - len('LAST_REQUIRED_VALUE'))
    return validate_program({'mode': 'APPLY', 'write_set': [{'handle': handle, 'revision': 'b' * 64}],
        'operations': [operation('node_edit', handle, material='Review Material', edits=edits)], 'expected_checks': []})


def fixture_facts(arguments):
    """Fixture-owned slot identities, never observations of the artist's scene.

    The 63-owner material workload starts with empty slots. The node workload
    explicitly starts with a separate Review Material on each referenced owner;
    names do not establish sharing. Native staging remains disabled throughout.
    """
    handles = list(dict.fromkeys(entry['handle'] for field in ('read_set', 'write_set')
                                for entry in arguments.get(field, [])))
    node_owners = {operation['target'] for operation in arguments['operations']
                   if operation['kind'] == 'node_edit' and operation['parameters'].get('material') == 'Review Material'}
    owners, resources = [], {}
    for index, target in enumerate(handles):
        slots = []
        if target in node_owners:
            token = 'live:%d' % (100 + index)
            resources[token] = {'uid': 100 + index, 'name': 'Review Material', 'principled': 'Principled BSDF',
                                'groups': {}, 'sharing': {target: {'others': 0, 'data_shared': False,
                                'outside': False, 'users_digest': 'complete-review-fixture'}}}
            slots = [{'slot': 0, 'resource': token, 'name': 'Review Material', 'link': 'OBJECT'}]
        owners.append({'target': target, 'slot_count': len(slots), 'slots': slots})
    return {'owners': owners, 'resources': resources}


def binding(arguments):
    # Only the host's resolver creates the private bundle. None of these facts
    # are public arguments or permission to stage a synthetic native target.
    bundle = program_checks.resolve_obligations(arguments, fixture_facts(arguments))
    return ProgramBinding(0, 'display-only-fixture', (1, 0), 'display-only-view', (), None,
                          bundle.checks_digest, bundle)


def mount(arguments, label):
    controller.cancel_tools('GUI fixture replaces display-only request', persist=False)
    controller.busy, controller.cancelled = True, False
    controller.state['messages'] = []
    controller.state['options']['approval'] = 'ASK'
    controller.request_tool(label, 'angelo_scene_program', arguments, state['responses'].append, enqueue=False)
    call = controller.pending[controller.state['id'] + '/' + label]
    call.expires = time.monotonic() + 1200
    started = time.perf_counter()
    call.precondition = approval_review.prepare_review(call, binding(call.arguments))
    prepared_ms = (time.perf_counter() - started) * 1000
    call.approval = True
    controller.version += 1
    view = chat_view._views.setdefault(parts()['area'].as_pointer(), {})
    view['expanded'] = set()
    chat_view._scroll[controller.state['id']] = 0
    before = dict(state['heavy_draw_calls'])
    redraw()
    check(label + '_first_draw_does_not_build_review', before == dict(state['heavy_draw_calls']))
    return call, prepared_ms


def layout_for(call):
    review = getattr(call, '_approval_review', None)
    return review.layouts.get(parts()['area'].as_pointer()) if review else None


def wait_layout(call):
    started = time.monotonic()
    while True:
        layout = layout_for(call)
        owner, width, size = geometry()
        rows = approval_review.review_rows(call, owner, width, size, chat_view._font, chat_view._mono, chat_view.wrap, True)
        # A layout for older display labels still draws its kept rows: wait for the current one.
        if (layout is not None and layout.iterator is None and rows[0] and rows[1]
                and layout.labels_source is getattr(call, 'target_labels', None)):
            check(state['tag'] + '_summary_and_all_details_published', bool(layout.preview) and bool(layout.details))
            return layout
        assert time.monotonic() - started < 180, 'One bounded review layout did not finish'
        yield .01


def old_wrap(value, width, size, font):
    """Pre-change whole-paragraph binary search, using actual native BLF metrics.

    Only ordinary cases use this intentionally expensive baseline. Long-control
    tests separately compare whole and incremental current-engine assembly.
    """
    from angelo_agent import text_layout
    rtl = canvas_rtl.has_rtl(value)
    output, offset = [], 0
    for paragraph in value.split('\n'):
        remaining = rtl_text.to_logical(paragraph) if rtl and rtl_text.is_visual(paragraph) else paragraph
        if not remaining:
            output.append(('', offset))
        while remaining:
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                candidate = rtl_text.to_visual(remaining[:middle]) if rtl else remaining[:middle]
                blf.size(font, size)
                if blf.dimensions(font, candidate)[0] <= width:
                    low = middle
                else:
                    high = middle - 1
            cut = low if rtl else text_layout.line_break(remaining, low)
            if rtl and cut < len(remaining):
                space = remaining.rfind(' ', 0, cut + 1)
                if space > 0:
                    cut = space + 1
            output.append((rtl_text.to_visual(remaining[:cut].rstrip()) if rtl else remaining[:cut], offset))
            offset += cut
            remaining = remaining[cut:]
        offset += 1
    return tuple(output)


def full_controls(call, layout, *, parity=False, baseline=False):
    args = approval_review.review_arguments(call)
    assert_bound_display(call, args)
    message = approvals.approval_message(call.name, args, target_labels=getattr(call, 'target_labels', None))
    text = approvals.approval_details(call.name, args, target_labels=getattr(call, 'target_labels', None))
    generated = '\n'.join(approval_review._detail_lines(call.name, args, getattr(call, 'target_labels', None), message))
    check(state['tag'] + '_all_control_bytes_match_existing_formatter', generated == text)
    if any(GUARD in row for row in call.precondition.obligations.checks()):
        labels = [agent_workspace_ui.readable_label(key) for key in ('Material identity', 'Selected slot')]  # In the UI language.
        check(state['tag'] + '_semantic_guard_labels_visible', all(label in text for label in labels))
    owner, width, size = geometry()
    rows = approval_review.review_rows(call, owner, width, size, chat_view._font, chat_view._mono, chat_view.wrap, True)
    check(state['tag'] + '_published_rows_are_same_immutable_layout', rows[0] is layout.preview and rows[1] is layout.details)
    if parity:
        tag = state['tag']
        state['tag'] = tag + '_parity_oracle'
        expected = old_wrap(text, width, size - 1, chat_view._mono) if baseline else tuple(
            canvas_wrap.iter_wrap(text, width, size - 1, chat_view._mono))
        state['tag'] = tag
        check(tag + ('_native_font_baseline_wrap_parity' if baseline else '_whole_vs_incremental_visual_assembly'),
              [row[0].rstrip() for row in expected] == [row[0].rstrip() for row in layout.details])
    elif not canvas_rtl.has_rtl(text):
        cursor = 0
        for value, offset in layout.details:
            assert text[offset:offset + len(value)] == value, 'A prepared row changed a control'
            assert not text[cursor:offset].strip('\n'), 'A control was skipped between rows'
            cursor = offset + len(value)
        check(state['tag'] + '_every_control_character_covered', not text[cursor:].strip('\n'))
    return text


def assert_bound_display(call, displayed):
    """Compare every ordered predicate without treating its private token as UI."""
    bundle = call.precondition.obligations
    bundle.assert_integrity(call.arguments)
    original, shown = bundle.checks(), displayed['expected_checks']
    check(state['tag'] + '_public_arguments_unchanged_in_display',
          {key: value for key, value in displayed.items() if key in call.arguments and key != 'expected_checks'} ==
          {key: value for key, value in call.arguments.items() if key != 'expected_checks'})
    check(state['tag'] + '_complete_ordered_public_obligations',
          [{key: value for key, value in row.items() if key != GUARD} for row in original] ==
          [{key: value for key, value in row.items() if key != 'material_identity'} for row in shown])
    facts, effects = bundle.facts(), bundle.effects()
    selected, semantic = {}, True
    for actual, visible in zip(original, shown):
        guard = actual.get(GUARD)
        if guard is None:
            continue
        identity = visible.get('material_identity', {})
        peer = selected.setdefault(guard['resource'], {'target': actual['target'], 'slot': guard['slot']})
        semantic &= identity.get('selected_slot') == guard['slot'] and any(
            field in identity for field in ('original_material', 'created_by', 'copied_by', 'isolated_by'))
        if 'original_material' in identity:
            semantic &= any(slot['resource'] == guard['resource'] and identity['original_material'] ==
                            {'target': owner['target'], 'slot': slot['slot']}
                            for owner in facts.get('owners', []) for slot in owner['slots'])
        for label, mode in (('created_by', 'create'), ('copied_by', 'copy'), ('isolated_by', 'isolate')):
            if label in identity:
                semantic &= any(effect['resource'] == guard['resource'] and effect['mode'] == mode and
                                identity[label] == {'step': effect['operation'] + 1,
                                                    'target': effect['target'], 'slot': effect['slot']}
                                for effect in effects)
        if peer != {'target': actual['target'], 'slot': guard['slot']}:
            semantic &= identity.get('same_material_as') == peer
    check(state['tag'] + '_every_private_guard_has_semantic_identity', semantic)
    check(state['tag'] + '_review_digest_matches_exact_bound_bundle', call._approval_review.checks_digest == bundle.checks_digest)
    check(state['tag'] + '_complete_ordered_material_effects',
          [{key: row[key] for key in ('step', 'target', 'slot', 'action')} for row in displayed.get('material_effects', [])] ==
          [{'step': row['operation'] + 1, 'target': row['target'], 'slot': row['slot'], 'action': row['mode']} for row in effects])
    tokens = set(facts.get('resources', {})) | {row['resource'] for row in effects}
    text = encoded(displayed)
    check(state['tag'] + '_private_resource_tokens_not_exposed', GUARD not in text and not any(token in text for token in tokens))
    check(state['tag'] + '_all_unverified_reasons_and_affected_controls', displayed.get('unverified_effects', []) == [
        {'step': row['operation'] + 1, 'root': row['root'], 'reason': row['reason'].replace('_', ' '), 'affected': row['affected']}
        for row in bundle.unknowns()])


def warm_draws(call, expanded, repetitions=5):
    view = chat_view._views[parts()['area'].as_pointer()]
    view['expanded'] = {('approval', call.id)} if expanded else set()
    before = dict(state['heavy_draw_calls'])
    start = len(state['draws'][state['tag']])
    for _ in range(repetitions):
        redraw()
    samples = state['draws'][state['tag']][start:]
    suffix = '_expanded' if expanded else '_collapsed'
    check(state['tag'] + suffix + '_real_draws_measured', len(samples) >= repetitions)
    check(state['tag'] + suffix + '_no_derivation_hash_copy_or_full_format', before == dict(state['heavy_draw_calls']))
    limit = int(parts()['region'].height / (geometry()[2] + 8)) + 120
    check(state['tag'] + suffix + '_visible_rows_only', all(row['text_rows'] <= limit for row in samples))
    return samples


def scroll_to_last(call, marker, screenshot):
    layout = layout_for(call)
    index = next(index for index in range(len(layout.details) - 1, -1, -1) if marker in layout.details[index][0])
    view = chat_view._views[parts()['area'].as_pointer()]
    view['expanded'] = {('approval', call.id)}
    redraw()
    _, bottom, _, visible = view['message_viewport']
    line_height = geometry()[2] + 8
    wanted = bottom + view['content_height'] - 52 - len(layout.preview) * line_height - 14 - index * line_height
    desired = max(0, min(view['max_scroll'], wanted - (bottom + visible / 2)))
    chat_view._scroll[controller.state['id']] = min(view['max_scroll'], desired + 66)
    event('WHEELDOWNMOUSE', x=parts()['region'].width / 2, y=bottom + visible / 2)
    redraw()
    check(state['tag'] + '_wheel_reaches_last_mandatory_control',
          any(marker in row['value'] and bottom <= row['y'] <= bottom + visible for row in state['last_draw_text']))
    capture(screenshot)


def resize_chat(desired):
    context = parts()
    area = context['area']
    previous = area.width
    with bpy.context.temp_override(**context):
        result = bpy.ops.screen.area_move('EXEC_DEFAULT', x=area.x, y=area.y + area.height // 2,
                                         delta=area.width - desired)
    check(state['tag'] + '_native_area_resize_executed', 'FINISHED' in result)
    return previous


def record_case(label, call, prepared_ms, elapsed, collapsed, expanded):
    layout = layout_for(call)
    slices = state['cold_slices'][label]
    report['cases'][label] = {'snapshot_ms': round(prepared_ms, 3), 'preparation_wall_ms': round(elapsed * 1000, 3),
        'timer_slices': slices, 'max_timer_slice_ms': round(max((row['ms'] for row in slices), default=0), 3),
        'summary_rows': len(layout.preview), 'detail_rows': len(layout.details),
        'required_checks': len(call._approval_review.arguments['expected_checks']),
        'prepared_json_bytes': len(call._approval_review.encoded.encode('utf-8')),
        'collapsed_draws': collapsed, 'expanded_draws': expanded,
        'font_and_shaping_calls': {key.split('/', 1)[1]: value for key, value in state['font'].items() if key.startswith(label + '/')}}
    (OUT / 'complete-review-ui-progress.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print('COMPLETE_REVIEW_UI_CASE ' + json.dumps({'case': label, 'detail_rows': len(layout.details),
          'timer_slices': len(slices), 'max_slice_ms': report['cases'][label]['max_timer_slice_ms']}), flush=True)


def workload_trials():
    factories = (('ordinary', ordinary), ('material_1088', largest_material),
                 ('near_budget', lambda: text_program('x' * 1024, large=True)))
    for name, factory in factories:
        for trial in range(5):
            label = name + '_%d' % trial
            state['tag'] = state['phase'] = label
            call, prepared_ms = mount(factory(), label)
            check(label + '_complete_required_predicates_in_snapshot',
                  len(call._approval_review.arguments['expected_checks']) == {'ordinary': 3, 'material_1088': 1088, 'near_budget': 1}[name])
            started = time.monotonic()
            layout = yield from wait_layout(call)
            elapsed = time.monotonic() - started
            full_controls(call, layout, parity=name == 'ordinary', baseline=name == 'ordinary')
            collapsed, expanded = warm_draws(call, False), warm_draws(call, True)
            if name != 'ordinary':
                check(label + '_preparation_yielded_before_all_details', any(row['pending'] for row in state['cold_slices'][label]))
            if trial == 0 and name != 'ordinary':
                marker = 'LAST_REQUIRED_MATERIAL' if name == 'material_1088' else 'LAST_REQUIRED_VALUE'
                scroll_to_last(call, marker, name + '-last-required-control.png')
            record_case(label, call, prepared_ms, elapsed, collapsed, expanded)
            if name == 'material_1088' and trial == 0:
                yield from reflow_existing(call)
                state['tag'] = label
            controller.cancel_tools('Completed display-only timing trial', persist=False)
            check(label + '_cancel_drops_snapshot', getattr(call, '_approval_review', None) is None)
            yield .01


TEXT_CASES = (
    ('english_word', 'en_US', 'x' * 1024),
    ('cjk', 'ja_JP', ('日本語の設定と検証値' * 128)[:1024]),
    ('arabic', 'ar_EG', ('إعدادات التحقق الكاملة ' * 80)[:1024]),
    ('mixed_combining', 'en_US', ('Value e\u0301 A\u0308 日本語 العربية 123 [x] ' * 80)[:1024]),
    ('numeric_presentation', 'ar_EG', ('1 ' * 500) + '\ufe8f'),
    ('combining_presentation', 'ar_EG', 'a' + '\u0301' * 1022 + '\ufe8f'),
)


def reflow_existing(call):
    """Resize and switch language on the same complete 1,088-check snapshot."""
    state['tag'] = state['phase'] = 'existing_1088_reflow'
    review, old_layout = call._approval_review, layout_for(call)
    width_before = parts()['area'].width
    resize_chat(max(280, width_before - 120))
    yield .08
    layout = yield from wait_layout(call)
    check('actual_resize_rebuilds_same_request_layout', layout is not old_layout and layout.key[0] != old_layout.key[0])
    full_controls(call, layout)
    old_layout = layout
    bpy.context.preferences.view.language = 'ja_JP'
    yield .15
    layout = yield from wait_layout(call)
    check('actual_locale_change_rebuilds_same_request_layout', layout is not old_layout and ui_text.language() == 'ja')
    full_controls(call, layout)
    check('reflow_preserves_complete_snapshot_and_digest', call._approval_review is review
          and len(review.arguments['expected_checks']) == 1088)
    bpy.context.preferences.view.language = 'en_US'
    resize_chat(width_before)
    yield .1
    yield from wait_layout(call)


def drawn_text():
    return [row['value'] for row in state['last_draw_text']]


def preparing_and_late_label_cards():
    """Opened Details say they are being prepared; late display labels never hide a drawn card."""
    state['tag'] = state['phase'] = 'preparing_details_note'
    call, _ = mount(largest_material(), state['tag'])
    owner, width, size = geometry()
    chat_view._views[owner]['expanded'].add(('approval', call.id))
    deadline = time.monotonic() + 60
    layout = None
    # Publish only the summary within this step, so no timer tick can complete the Details first.
    while layout is None or not layout.preview:
        assert time.monotonic() < deadline, 'The 1,088-check summary was not published'
        approval_review.advance_review_layout(call, owner, width, size, chat_view._font, chat_view._mono,
                                              chat_view.iter_wrap, time.monotonic() + .002)
        layout = layout_for(call)
    check('preparing_fixture_has_summary_without_details', layout.iterator is not None and not layout.details)
    redraw()
    check('opened_details_being_prepared_say_so', 'Preparing details…' in drawn_text() and 'Hide details' not in drawn_text())
    check('preparing_card_keeps_its_real_approve_hit', bool(hit('approve', call)))
    capture('approval-preparing-details.png')
    layout = yield from wait_layout(call)
    redraw()
    check('published_details_replace_the_preparing_note', 'Hide details' in drawn_text() and 'Preparing details…' not in drawn_text())
    state['tag'] = state['phase'] = 'late_labels_keep_card'
    old_preview, old_details, token = layout.preview, layout.details, call._approval_review.token
    call.target_labels = {call.arguments['write_set'][0]['handle']: 'Late label fixture'}
    redraw()
    rows = approval_review.review_rows(call, owner, width, size, chat_view._font, chat_view._mono, chat_view.wrap, True)
    check('late_labels_keep_the_drawn_card', rows[0] is old_preview and rows[1] is old_details)
    check('late_labels_keep_the_real_approve_hit', bool(hit('approve', call)))
    check('kept_card_still_carries_the_current_review_token',
          chat_view._views[owner].get('approval_review') == (call.id, token) and call._approval_review.token is token)
    layout = yield from wait_layout(call)
    shown = '\n'.join(row for row, _ in layout.preview + layout.details)
    check('late_labels_replace_the_card_when_ready', layout.preview is not old_preview and 'Late label fixture' in shown)
    controller.cancel_tools('Completed preparing and late-label fixture', persist=False)
    check('late_label_fixture_never_authorized', not call.authorized and not controller.native_jobs)
    yield .01


def locale_width_matrix():
    check('international_build_available', bool(bpy.app.build_options.international))
    bpy.context.preferences.view.use_translate_interface = True
    original_font_size = parts()['area'].spaces.active.font_size
    for text_name, locale, value in TEXT_CASES:
        bpy.context.preferences.view.language = locale
        yield .15
        check(text_name + '_actual_locale_changed', ui_text.language() == locale.split('_')[0])
        for width_name, desired in (('small', 340), ('large', min(840, int(window.width * .7)))):
            label = text_name + '_' + width_name
            state['tag'] = state['phase'] = label
            resize_chat(desired)
            yield .05
            ordinary_call, _ = mount(ordinary(), 'ordinary_' + label)
            ordinary_layout = yield from wait_layout(ordinary_call)
            full_controls(ordinary_call, ordinary_layout, parity=True, baseline=True)
            warm_draws(ordinary_call, False)
            warm_draws(ordinary_call, True)
            # The submitted array has up to 32,768 characters. A presentation
            # bound may use ordered Item lines; every scalar must still appear.
            program = text_program([value] * 32)
            if text_name in {'numeric_presentation', 'combining_presentation'}:
                target = program['write_set'][0]['handle']
                program = validate_program({'mode': 'APPLY', 'write_set': program['write_set'],
                    'operations': [operation('property_set', target, values={'v': [value] * 32})], 'expected_checks': []})
            call, prepared_ms = mount(program, label)
            started = time.monotonic()
            layout = yield from wait_layout(call)
            elapsed = time.monotonic() - started
            text = full_controls(call, layout, parity=True)
            check(label + '_all_32_long_scalar_values_preserved', text.count(value) >= 32)
            collapsed, expanded = warm_draws(call, False), warm_draws(call, True)
            record_case(label, call, prepared_ms, elapsed, collapsed, expanded)
            report['cases'][label].update(locale=ui_text.language(), region_width=parts()['region'].width,
                                         submitted_scalar_characters=len(value) * 32,
                                         maximum_control_line_chars=max(map(len, text.split('\n'))))
            old_layout = layout
            parts()['area'].spaces.active.font_size = original_font_size + 4
            owner, width, size = geometry()
            check(label + '_font_change_hides_old_layout', approval_review.review_rows(
                call, owner, width, size, chat_view._font, chat_view._mono, chat_view.wrap, True) == ((), ()))
            layout = yield from wait_layout(call)
            check(label + '_font_change_rebuilds_layout', layout is not old_layout)
            full_controls(call, layout, parity=True)
            parts()['area'].spaces.active.font_size = original_font_size
            yield from wait_layout(call)
            if width_name == 'large':
                capture(text_name + '-large-details.png')
            controller.cancel_tools('Completed locale/width display fixture', persist=False)
            yield .01
            for trial in range(1, 5):
                repeated = label + '_trial_%d' % trial
                state['tag'] = state['phase'] = repeated
                repeat_call, prepared_ms = mount(program, repeated)
                started = time.monotonic()
                repeat_layout = yield from wait_layout(repeat_call)
                elapsed = time.monotonic() - started
                repeated_text = full_controls(repeat_call, repeat_layout)
                check(repeated + '_all_long_values_retained', repeated_text.count(value) >= 32)
                collapsed, expanded = warm_draws(repeat_call, False), warm_draws(repeat_call, True)
                record_case(repeated, repeat_call, prepared_ms, elapsed, collapsed, expanded)
                report['cases'][repeated].update(locale=ui_text.language(), region_width=parts()['region'].width,
                                                 submitted_scalar_characters=len(value) * 32)
                controller.cancel_tools('Completed repeated native font measurement', persist=False)
                yield .01
    bpy.context.preferences.view.language = 'en_US'
    yield .1


def stale_manual_approval_cases():
    state['tag'] = state['phase'] = 'native_approval_positive_control'
    call, _ = mount(ordinary(), state['tag'])
    yield from wait_layout(call)
    redraw()
    x, y = hit('approve', call)
    native_event('MOUSEMOVE', 'NOTHING', x, y)
    native_event('LEFTMOUSE', 'PRESS', x, y)
    yield .08
    check('native_event_queue_reaches_actual_approval_handler',
          chat_view._views[parts()['area'].as_pointer()].get('pressed_approval_review') == (call.id, call._approval_review.token))
    native_event('LEFTMOUSE', 'RELEASE', x, y)
    yield .08
    check('native_approval_positive_control_authorizes_without_staging', call.authorized and not controller.native_jobs)
    controller.cancel_tools('Discard native event positive control before execution', persist=False)
    for change in ('arguments', 'program_arguments', 'binding', 'document', 'in_place', 'cancel'):
        label = 'stale_' + change
        state['tag'] = state['phase'] = label
        call, _ = mount(ordinary(), label)
        yield from wait_layout(call)
        redraw()
        x, y = hit('approve', call)
        native_event('MOUSEMOVE', 'NOTHING', x, y)
        native_event('LEFTMOUSE', 'PRESS', x, y)
        yield .08
        old_review = call._approval_review
        check(label + '_native_press_captured_review_token',
              chat_view._views[parts()['area'].as_pointer()].get('pressed_approval_review') == (call.id, old_review.token))
        if change == 'arguments':
            call.arguments = copy.deepcopy(call.arguments)
        elif change == 'program_arguments':
            call.program_arguments = copy.deepcopy(call.arguments)
        elif change == 'binding':
            call.precondition = call.precondition._replace(generation=1)
        elif change == 'document':
            call.document = ('different-native-document',)
        elif change == 'in_place':
            call.arguments['operations'][0]['parameters']['name'] = 'Changed after mouse press'
        else:
            controller.cancel_tools('Cancelled between actual mouse press and release', persist=False)
        # No redraw: these are the old card's real hit coordinates and token.
        native_event('LEFTMOUSE', 'RELEASE', x, y)
        yield .08
        check(label + '_old_hit_cannot_authorize', not call.authorized and not call.approved and not controller.native_jobs)
        check(label + '_artist_scene_unchanged', scene_signature() == report['scene_baseline'])
        check(label + '_old_snapshot_unavailable', approval_review._current_review(call) is None)
        if change != 'cancel':
            controller.cancel_tools('Finish stale manual-hit fixture', persist=False)
        check(label + '_snapshot_released_after_request', getattr(call, '_approval_review', None) is None)
        check(label + '_old_snapshot_not_retained_by_view', all(value is not old_review for value in
                                                               chat_view._views[parts()['area'].as_pointer()].values()))
        yield .01
    state['tag'] = state['phase'] = 'explicit_reprepare'
    call, _ = mount(ordinary(), state['tag'])
    yield from wait_layout(call)
    old = call._approval_review
    call.arguments['operations'][0]['parameters']['name'] = 'Fresh explicit review'
    call.precondition = approval_review.prepare_review(call, binding(call.arguments))
    check('explicit_update_gets_new_review_token', call._approval_review.token != old.token)
    layout = yield from wait_layout(call)
    check('explicit_update_shows_new_controls', 'Fresh explicit review' in full_controls(call, layout, parity=True))
    controller.cancel_tools('Completed explicit update fixture', persist=False)


def partial_bound_program():
    arguments = text_program(.2)
    arguments['operations'][0]['parameters']['edits'] = [
        {'op': 'set', 'node': 'Math', 'inputs': {'Value': 1}},
        {'op': 'set', 'node': 'Other', 'inputs': {'Scale': 2}},
        {'op': 'set', 'node': 'Math', 'settings': {'operation': 'MULTIPLY'}}]
    return validate_program(arguments)


def bound_obligation_cases():
    """Partial read-back and stale bound controls remain visible and fail closed."""
    state['tag'] = state['phase'] = 'bound_partial_verification'
    call, _ = mount(partial_bound_program(), state['tag'])
    layout = yield from wait_layout(call)
    bundle = call.precondition.obligations
    text = full_controls(call, layout)
    displayed = approval_review.review_arguments(call)
    check('partial_bundle_retains_known_checks_and_unproved_obligations',
          bool(bundle.checks()) and bool(bundle.unknowns()) and bool(displayed.get('unverified_effects')))
    check('partial_verification_warning_and_exact_reason_visible',
          'Verification is partial.' in text and 'Unverified effects' in text and 'opaque node settings' in text)
    check('partial_affected_value_and_surviving_other_node_visible',
          displayed['unverified_effects'][0]['affected'][0]['nodes'] == [{'name': 'Math', 'inputs': {'Value': 1}}]
          and any(node.get('name') == 'Other' and node.get('inputs') == {'Scale': 2}
                  for row in displayed['expected_checks'] for node in row.get('nodes', [])))
    warm_draws(call, False)
    warm_draws(call, True)
    scroll_to_last(call, 'opaque node settings', 'bound-partial-verification-reason.png')
    controller.cancel_tools('Completed bound partial review fixture', persist=False)

    state['tag'] = state['phase'] = 'stale_bound_controls'
    call, _ = mount(text_program(.2), state['tag'])
    yield from wait_layout(call)
    original_bundle = call.precondition.obligations
    original_encoded = original_bundle.encoded
    redraw()
    x, y = hit('approve', call)
    native_event('MOUSEMOVE', 'NOTHING', x, y)
    native_event('LEFTMOUSE', 'PRESS', x, y)
    yield .08
    check('bound_native_press_captures_frozen_review_token',
          chat_view._views[parts()['area'].as_pointer()].get('pressed_approval_review') == (call.id, call._approval_review.token))
    call.arguments['operations'][0]['parameters']['edits'][0]['settings']['setting_00'] = .8
    native_event('LEFTMOUSE', 'RELEASE', x, y)
    yield .08
    check('changed_bound_controls_cannot_use_old_manual_approval', not call.authorized and not call.approved and not controller.native_jobs)
    check('stale_bound_snapshot_unavailable', approval_review._current_review(call) is None)
    check('bound_bundle_is_immutable_after_request_edit', original_bundle.encoded == original_encoded)
    refused = False
    try:
        approval_review.prepare_review(call, call.precondition)
    except ValueError:
        refused = True
    check('stale_private_bundle_cannot_prepare_new_controls', refused and call._approval_review is None)
    check('bound_stale_review_never_changes_artist_scene', scene_signature() == report['scene_baseline'])
    controller.cancel_tools('Completed stale bound review fixture', persist=False)
    yield .01


def independent_approval_path():
    state['tag'] = state['phase'] = 'approval_without_canvas'
    call, _ = mount(ordinary(), state['tag'])
    approval_review.clear_review(call)
    call.automatic = True
    approved = controller.approve_tool(call, BlenderExecutor(window), ui.document_key())
    check('configured_approval_does_not_depend_on_visible_layout', approved and call.authorized)
    check('display_fixture_never_starts_native_staging', not controller.native_jobs and not native_programs.runtime_is_staging())
    controller.cancel_tools('Discard authorized queue before any execution', persist=False)
    yield .01


def after_file_load():
    global window, controller, handler
    state['tag'] = state['phase'] = 'real_file_load'
    path = OUT / 'factory-review.blend'
    baseline = hashlib.sha256(path.read_bytes()).hexdigest()
    old_call, _ = mount(ordinary(), 'pending_when_document_loads')
    yield from wait_layout(old_call)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    yield .4
    window = bpy.context.window_manager.windows[0]
    while not qa_boot.started():   # A loaded file may get Manual's tabs built: never dock while the layout moves.
        yield .1
    if not any(console.is_chat(area) for area in window.screen.areas):
        bpy.ops.angelo.open_chat(side='RIGHT')
        yield .4
    check('actual_file_load_discards_pending_review_snapshot', getattr(old_call, '_approval_review', None) is None)
    with bpy.context.temp_override(**parts()):
        check('fresh_chat_after_load_created', 'FINISHED' in bpy.ops.angelo.new_chat())
    controller = ui.get_controller()
    controller.refresh_models = lambda *args, **kwargs: None
    if bpy.app.timers.is_registered(ui.pump):
        bpy.app.timers.unregister(ui.pump)
    handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window.as_pointer(),
                                   focused_area=parts()['area'].as_pointer(), session=controller.state['id'],
                                   selected_all=False, anchor=None, dragging=False, pending_click=None)
    check('file_load_retains_real_canvas_preparation_timer', bpy.app.timers.is_registered(chat_view.animate_popovers))
    call, _ = mount(ordinary(), 'after_real_file_load')
    layout = yield from wait_layout(call)
    full_controls(call, layout, parity=True)
    redraw()
    check('new_approval_after_load_has_real_manual_hit', bool(hit('approve', call)))
    check('file_load_rebinds_readable_native_font', blf.dimensions(chat_view._font, 'Approval review')[0] > 0)
    check('fixture_file_unchanged_after_load', hashlib.sha256(path.read_bytes()).hexdigest() == baseline)
    capture('approval-after-real-file-load.png')
    controller.cancel_tools('Completed actual file-load review fixture', persist=False)


def suite():
    yield from workload_trials()
    yield from preparing_and_late_label_cards()
    yield from locale_width_matrix()
    yield from stale_manual_approval_cases()
    yield from bound_obligation_cases()
    yield from independent_approval_path()
    yield from after_file_load()


def finish(error=None):
    if state['done']:
        return None
    state['done'], state['tag'] = True, None
    if controller is not None:
        controller.cancel_tools('Complete review GUI fixture ended', persist=False)
    report.update(phase=state['phase'], heavy_draw_calls=dict(state['heavy_draw_calls']),
                  provider_started=any(item.provider is not None for item in ui._controllers.values()))
    report['checks']['no_provider_started'] = not report['provider_started']
    report['checks']['no_staged_native_scene'] = not native_programs.runtime_is_staging() and not any(
        scene.get('_angelo_program_stage') for scene in bpy.data.scenes)
    if window is not None and 'scene_baseline' in report:
        report['checks']['factory_scene_content_unchanged'] = scene_signature() == report['scene_baseline']
    if error:
        report['error'] = error
    modules = [approval_review, approvals, agent_workspace_ui, chat_view, canvas_wrap, canvas_rtl, rtl_text, ui, program_checks]
    if controller is not None:
        modules.append(sys.modules[type(controller).__module__])
    report['source_sha256'] = {str(Path(module.__file__).resolve()): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                               for module in modules}
    report['fixture_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['success'] = not error and all(report['checks'].values())
    (OUT / 'complete-review-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print('ANGELO_COMPLETE_REVIEW_UI ' + json.dumps({'success': report['success'], 'phase': report['phase'],
          'checks': report['checks'], 'error': error}), flush=True)
    bpy.types.SpaceConsole.draw_handler_remove(capture_handler, 'WINDOW')
    for module, name, original in reversed(originals):
        setattr(module, name, original)
    bpy.ops.wm.quit_blender()
    return None


def heartbeat():
    global window, controller, handler, steps
    if state['done']:
        return None
    try:
        assert time.monotonic() - state['started'] < 900, 'Disposable complete-review GUI fixture timed out'
        if state['phase'] == 'boot':
            if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
                return .1
            window = bpy.context.window_manager.windows[0]
            report['scene_baseline'] = scene_signature()
            bpy.ops.angelo.open_chat(side='RIGHT')
            state['phase'] = 'layout'
            return .4
        if not qa_boot.started():
            return .05
        if state['phase'] == 'layout':
            controller = ui.get_controller()
            controller.refresh_models = lambda *args, **kwargs: None
            # No native/provider pump is needed: all large requests are solely
            # review inputs. The real canvas preparation timer stays enabled.
            if bpy.app.timers.is_registered(ui.pump):
                bpy.app.timers.unregister(ui.pump)
            with bpy.context.temp_override(**parts()):
                console.render(bpy.context, force=True)
                bpy.context.area.spaces.active.history[-1].body = 'Review fixture: no execution'
            handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window.as_pointer(),
                                           focused_area=parts()['area'].as_pointer(), session=controller.state['id'],
                                           selected_all=False, anchor=None, dragging=False, pending_click=None)
            bpy.context.preferences.view.use_translate_interface = True
            bpy.context.preferences.view.language = 'en_US'
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'factory-review.blend'), check_existing=False)
            with bpy.context.temp_override(**parts()):
                check('fresh_chat_bound_to_saved_fixture', 'FINISHED' in bpy.ops.angelo.new_chat())
            controller = ui.get_controller()
            controller.refresh_models = lambda *args, **kwargs: None
            handler.session = controller.state['id']
            steps = suite()
        try:
            interval = next(steps)
        except StopIteration:
            state['phase'] = 'complete'
            return finish()
        return interval
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(heartbeat, first_interval=.3, persistent=True)
