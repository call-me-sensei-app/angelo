# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The session rail and live views in the real window, driven by real events (``event_simulate``).

Nothing here forces a draw (break-it P1-8 P1: a suite that redrew before every check hid a rail that never redrew
itself). A probe after Angelo's own POST_PIXEL records each real draw of the view, and the timed checks pass only when
the product drew the change by itself within a second while the lead sits idle: each worker's first part and a later
edit on its tile, an edit in the live view, an approval, a step title with its counts, the clock's seconds, Done and
Merged. The connect card is dismissed first, by keyboard as a person would, and the launcher's blink is off: both would
redraw the view for the rail.

Two real workers (``Angelo --background`` children) build unrelated parts through chats bound to them, with typed
programs only and no provider. Checked in the window, with screenshots of every state:

* the rail shows one tile per session (the main one first), each with a picture drawn by Angelo, its name and its
  status; the main tile is active;
* a click on a worker's tile switches the chat and shows that worker's part full screen in the 3D view, while the
  window's scene stays the document; orbiting moves the live view; an edit key over it is held back and says so;
  Home frames the part; a click names the object under the pointer;
* a worker waiting for approval shows it on its tile;
* the 3D view beneath holds its input natively while the live view shows: a modal operator that passes every event
  on (as a render or bake does), typing into the focused chat with a corner card shown, an Option character and a
  lost middle-button release never reach the document (break-it P1-3 H1, H2, M2);
* the lead's capture shows its own document, never the live view or the rail (P1-3 M1); Home frames the whole part;
* the main tile brings back the document and the view it was left with, and releases the hold;
* a saved file holds no library and only the document's scenes.

A failed run exits 1, after writing its report and stopping its workers. With ``ANGELO_WORKER_CHAT_HOST`` unset or ``1``
(the "hosted" variant) each chat runs in its worker and the stand-in model plays its calls (``hosted_rail_turns``); the
lead's side is what the window's own timer pumps. Its timed checks start when the lead's mirror applied the change.

GUI ``--factory-startup --enable-event-simulate`` only, disposable profile, no provider or paid call; unforced."""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import _bpy
import addon_utils
import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-session-rail'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
import qa_input  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
from angelo_agent import (chat_launcher, connect_hero, live_view, live_view_draw, parallel_sessions,  # noqa: E402
                          rail_pictures, session_rail, session_rail_draw, worker_routing, worker_sessions)
from parallel_workers_qa import op, paint, primitive, program  # noqa: E402
import composer_hint_qa  # noqa: E402
from hosted_worker_chat import connect, hosted  # noqa: E402

HOSTED = hosted()   # Design A′: each worker chat's turns run in its worker (the "hosted" variant).
if HOSTED:
    from hosted_rail_turns import RailTurns  # noqa: E402
    turns = RailTurns(OUT / 'stand-in')   # On PATH before any worker starts; the model its turns run on.

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.edit.use_global_undo = True
state = {'phase': 'boot', 'started': time.monotonic(), 'at': time.monotonic(), 'done': False}
report = {'checks': {}, 'paid_requests': 0, 'binary': bpy.app.binary_path, 'timings': {}, 'shots': [],
          'variant': 'hosted' if HOSTED else 'lanes'}
chats, answers = {}, {}
window = document = None


def check(name, condition, detail=None):
    report['checks'][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault('details', {})[name] = detail
    assert condition, name


def view_area():
    return live_view_draw.target(window)


def view_region():
    return next(r for r in view_area().regions if r.type == 'WINDOW')


def screenshot(name):
    """The window as it draws now, rail and live view included: Angelo's offscreen read (``live_view.window_pixels``,
    outside the lead's hidden capture). A window behind others is not presented on macOS, so Blender's screenshot read
    a stale frame on Mac 1. The probe ignores this draw: it is the suite's, never the product's."""
    from angelo_agent import view_editor
    state['capturing'] = True
    try:
        width, height, pixels = live_view.window_pixels(window)
    finally:
        state['capturing'] = False
    (OUT / name).write_bytes(view_editor.encode_png(pixels, width, height, bottom_up=True))
    report['shots'].append(name)


frames = []   # Each real draw of the rail's view: at, tiles {session: (kind, status)}, pictures {session: at}, live.


def probe():
    """POST_PIXEL after Angelo's own: when the window really drew the rail's view, and what the rail showed. Nothing in
    this suite forces a draw (break-it P1-8 P1: forced redraws before every check hid a rail that never redrew), so
    every frame here is one the product asked for. Only names that exist before the fix: the suite can show the bug."""
    if window is None or state.get('capturing') or bpy.context.window != window or bpy.context.area != view_area() \
            or bpy.context.region != view_region():
        return
    placed = session_rail_draw.last.get(window.as_pointer())
    here = placed and placed[0] == view_area().as_pointer()
    tiles = {t.session: (t.kind, t.status) for t in placed[1].tiles} if here else {}
    live = getattr(_bpy, '_angelo_live', None)
    text_end = getattr(live, 'text_info_end', None)   # The build's: where its text info ended.
    from angelo_agent import asset_shelf_add
    region = bpy.context.region
    shelf = asset_shelf_add.places.get(bpy.context.area.as_pointer())
    pills = [rect[1] + rect[3] - region.y for _action, rect in shelf[1]] if shelf and shelf[0] == window.as_pointer() else []
    bottom = max([session_rail.bottom_inset(bpy.context.area.regions, region)] + pills)
    frames.append({'at': time.monotonic(), 'tiles': tiles,
                   'pictures': {session: rail_pictures._drawn.get(session, 0.0) for session in tiles},
                   'live': {(key[0], key[-1]) for _offscreen, key in live_view_draw._offscreens.values()},
                   'top': placed[1].panel[1] + placed[1].panel[3] if here and tiles else None,
                   'rail_scale': placed[1].scale if here else None, 'session_count': len(parallel_sessions.sessions()),
                   'bottom': bottom, 'pill_tops': pills,
                   'headers': session_rail.top_inset(bpy.context.area.regions, region),
                   'text_end': text_end(bpy.context.region) if text_end else None,
                   'height': bpy.context.region.height, 'ui_scale': bpy.context.preferences.system.ui_scale})
    del frames[:-4000]


def first_frame(after, test):
    """The first drawn frame after ``after`` that satisfies ``test(frame)``: its time, or None."""
    return next((frame['at'] for frame in frames if frame['at'] > after and test(frame)), None)


def on_screen_within_a_second(name, since, test, phase_wait=3.0):
    """None while waiting (up to ``phase_wait`` after ``since``, the event itself); else checks that a frame showed it
    within a second of ``since`` and records the time. A miss reports what happened around the event."""
    from angelo_agent import input_policy
    waits = state.setdefault('waits', {}).setdefault(name, [])
    waits.append((round(time.monotonic() - since, 3), input_policy.canvas_blocking(window)))
    seen = first_frame(since, test)
    if seen is None and time.monotonic() - since < phase_wait:
        return None
    report['timings'][name + '_s'] = round(seen - since, 3) if seen is not None else None
    if seen is None or seen - since > 1.0:
        try:
            from angelo_agent import rail_redraw
            tagged = [round(at - since, 3) for at, _w in rail_redraw.tags if since - 1.0 <= at <= since + 3.0]
        except ImportError:   # The add-on before the fix has no rail_redraw.
            tagged = None
        report.setdefault('details', {})[name + '_around'] = {
            'frames': [(round(f['at'] - since, 3), sorted(set(kind for kind, _s in f['tiles'].values())))
                       for f in frames if since - 1.0 <= f['at'] <= since + 3.0],
            'tags': tagged, 'blocking': [w for w in waits if w[1]][:20]}
    check(name + '_drawn_by_itself', seen is not None, {'since': since, 'last_frame': frames[-1]['at'] if frames
                                                        else None})
    check(name + '_within_a_second', seen - since <= 1.0, seen - since)
    return seen


bpy.types.SpaceView3D.draw_handler_add(probe, (), 'WINDOW', 'POST_PIXEL')   # After the rail's and the live view's.
# The launcher's blink redraws the whole view every few seconds: it would draw a rail that never asks to be drawn.
chat_launcher.BLINK_EVERY = float('inf')


def plan():
    placed = session_rail_draw.last.get(window.as_pointer())
    return placed[1] if placed else None


# Where the rail starts (break-it P1-13 U1/U2, P1-14 V1/V2): each case changes the text info Blender draws at the
# view's top-left. ``lines``: how far down it reaches, in Blender's text lines (its own spacing: a name or object
# line 1, Statistics a 0.6 gap and 5 rows for a selected mesh in Object Mode, Performance a gap and 3 rows, the grid
# unit 1), so the native value is checked against the view, not against itself. How the rail answers: 'follows' (right
# below the text, or as low as its tiles allow), 'steady' (the text shrank, its settings did not: the rail stays) or
# 'held' (the text grew during a press on a tile: the rail stays until the press ends, P1-15 L2). A press, not a hover:
# a simulated move carries no motion of its own, so it never reaches the rail's hover (P1-16 H1). Events are queued in
# one tick and state changed in a later one: a frame drawn in between would show the change without them.
TEXT_CASES = (('as_launched', 2, 'follows'), ('statistics_during_a_press', 7.6, 'held'),
              ('released_over_the_view', 7.6, 'follows'), ('performance', 11.6, 'follows'),
              ('orthographic_axis_view', 12.6, 'follows'), ('left_the_axis_view', 11.6, 'steady'),
              ('no_names', 0, 'follows'), ('larger_interface', 2, 'follows'))


def text_view(case):
    """Sets one case's view and preferences (cumulative, then no text, then a larger interface) and redraws the view:
    a setting the suite changes, so this phase checks the layout in the frame drawn, never the product's timing."""
    overlay, preferences, view = view_area().spaces.active.overlay, bpy.context.preferences.view, view_region().data
    if case == 'as_launched':   # Director Mode hides the view's text info and grid (with its unit line); these cases
        state['overlays'] = (overlay.show_text, overlay.show_floor)   # are about that text, so a person's shows it.
        overlay.show_text = overlay.show_floor = True
    elif case == 'statistics_during_a_press':   # A worker's tile: a release off it must switch nothing (P1-17 L2).
        state['selected'] = sorted(o.name for o in bpy.context.view_layer.objects if o.select_get())
        state['pressed_session'] = chats['room'].state['id']
        send('LEFTMOUSE', 'PRESS', *tile_point(state['pressed_session']))
    elif case == 'held_statistics':
        overlay.show_stats = True
    elif case == 'released_over_the_view':   # Off its tile: no switch, and the press ends (P1-15 M1).
        send('LEFTMOUSE', 'RELEASE', *view_point(0.6, 0.5))
    elif case == 'performance':
        overlay.show_performance = True
    elif case == 'orthographic_axis_view':   # Its grid unit line ("Meters").
        state['user_view'] = (view.view_rotation.copy(), view.view_perspective)
        view.view_rotation = (1.0, 0.0, 0.0, 0.0)
        view.is_orthographic_side_view = True
        view.view_perspective = 'ORTHO'
    elif case == 'left_the_axis_view':   # Still orthographic, no longer an axis view: no grid unit line.
        view.is_orthographic_side_view = False
    elif case == 'no_names':
        state['names'] = (preferences.show_view_name, preferences.show_object_info)
        overlay.show_stats = overlay.show_performance = False
        view.view_rotation, view.view_perspective = state['user_view']
        preferences.show_view_name = preferences.show_object_info = False
    elif case == 'larger_interface':
        preferences.show_view_name, preferences.show_object_info = state['names']
        state['ui_scale'] = preferences.ui_scale
        preferences.ui_scale = preferences.ui_scale * 1.5
    elif case == 'restore':
        preferences.ui_scale = state['ui_scale']
        overlay.show_text, overlay.show_floor = state['overlays']
        check('interface_scale_restored', preferences.ui_scale == state['ui_scale'], preferences.ui_scale)
    view_region().tag_redraw()


def text_case():
    """True while the text-info cases run: each sets its view, waits for frames drawn after it, and checks the rail's
    top against where Blender said its own text ended in that same frame (``_bpy._angelo_live.text_info_end``), and
    that value against the lines the case shows."""
    from angelo_agent import chat_view
    index = state.setdefault('text_case', 0)
    if index >= len(TEXT_CASES):
        return False
    case, lines, answer = TEXT_CASES[index]
    if 'text_since' not in state:
        text_view(case)
        state['text_since'] = time.monotonic()
        return True
    since = state['text_since']
    if answer == 'held' and 'held_since' not in state:   # Statistics go on only once the press is seen set.
        pressing = session_rail_draw.pressed.get(window.as_pointer()) == ('switch', state['pressed_session'])
        if not (pressing and any(f['at'] > since for f in frames)):
            if time.monotonic() - since < 3.0:
                return True
            check('a_press_on_a_tile_is_held_' + case, False, session_rail_draw.pressed.get(window.as_pointer()))
        text_view('held_statistics')
        state['held_since'] = state['text_since'] = time.monotonic()
        return True
    seen = [f for f in frames if f['at'] > since and f['top'] is not None and f['text_end'] is not None]
    if len(seen) < 3 and time.monotonic() - since < 3.0:
        return True
    frame = seen[-1] if seen else {'top': None, 'text_end': None, 'height': None, 'ui_scale': None, 'tiles': {}}
    headers, bottom = frame.get('headers'), frame.get('bottom')
    found = {k: frame.get(k) for k in ('top', 'text_end', 'height', 'ui_scale', 'rail_scale', 'session_count', 'pill_tops')}
    found.update(headers=headers, bottom=bottom, pressed=session_rail_draw.pressed.get(window.as_pointer()))
    report.setdefault('text_info', {})[case] = found
    check('rail_drawn_with_text_info_' + case, seen, found)
    line = bpy.context.preferences.ui_styles[0].widget.points * frame['ui_scale'] * 1.6   # Blender's text line.
    reach = frame['height'] - headers - frame['text_end']
    found['lines'] = round(reach / line, 2)
    check('blenders_text_reaches_its_lines_' + case, abs(reach - lines * line) <= 0.5 * line, dict(found, line=line))
    before = report['text_info'][TEXT_CASES[index - 1][0]] if index else None
    if answer == 'steady':
        check('rail_holds_still_when_only_the_text_shrinks_' + case,
              frame['text_end'] > before['text_end'] and frame['top'] == before['top'], [before, found])
    elif answer == 'held':
        check('rail_holds_still_during_a_press_' + case, found['pressed'] is not None
              and frame['text_end'] < before['text_end'] and frame['top'] == before['top'], [before, found])
    else:   # As low as the text needs, or as its tiles allow in a short view (V5): the product's own bound.
        scale = frame['ui_scale']
        inset = session_rail.fit_below_text(headers, frame['height'] - frame['text_end'], frame['session_count'],
                                            frame['height'], scale, bottom)
        expected = frame['height'] - inset - session_rail.MARGIN * frame['rail_scale']
        check('rail_starts_right_below_it_' + case, abs(frame['top'] - expected) <= 1.0, dict(found, expected=expected))
        check('rail_starts_below_the_views_text_' + case, frame['top'] <= frame['text_end']
              or inset < frame['height'] - frame['text_end'], found)
    if case == 'released_over_the_view':
        selected = sorted(o.name for o in bpy.context.view_layer.objects if o.select_get())
        check('a_release_off_its_tile_ends_the_press_and_switches_nothing', found['pressed'] is None
              and ui._sessions.active == report['lead'] and live_view_draw.shown != state['pressed_session']
              and selected == state['selected'], [found, ui._sessions.active, live_view_draw.shown, selected])
    if case == 'orthographic_axis_view':
        screenshot('1c-rail-below-text-info.png')
    state.pop('held_since', None)
    del state['text_since']
    state['text_case'] = index + 1
    if state['text_case'] == len(TEXT_CASES):
        text_view('restore')
    return True


def paging_cleanup():
    """Detach only this fixture's model-free crowd; retain the two real workers and the lead's state."""
    import rail_positions_fixture as fixture
    step = state.get('paging') or {}
    if 'base' not in step:
        return
    preferences = bpy.context.preferences.view
    preferences.ui_scale = step['base']
    fixture.unbind(window, step.get('added', []), 0)
    if ui._sessions.active != step['active']:
        ui.show_session(step['active'])


def paging_case():
    """Run the paging stages and restore the original sessions even when an assertion fails."""
    try:
        return paging_step()
    except Exception:
        try:
            paging_cleanup()
        except Exception:
            report['paging_cleanup_error'] = traceback.format_exc()
        raise


def paging_snapshot():
    """The rendered paging facts at this stage, copied before any event or capture can replace the layout."""
    placed = plan()
    return {'panel': tuple(placed.panel), 'scale': placed.scale, 'first': placed.first,
            'shown': [tile.session for tile in placed.tiles], 'hidden': tuple(placed.hidden),
            'more': tuple(tuple(rect) if rect is not None else None for rect in placed.more)}


def paging_window(step, observed, first, stage):
    """Require the exact Main-pinned contiguous window and hidden counts at the requested unit offset."""
    workers = step['order'][1:]
    count = len(observed['shown']) - 1
    expected = [step['order'][0]] + workers[first:first + count]
    check('paging_' + stage + '_has_exact_offset_main_order_and_hidden_counts',
          count > 0 and observed['first'] == first and observed['shown'] == expected
          and observed['hidden'] == (first, len(workers) - first - count),
          {'observed': observed, 'expected_window': expected, 'expected_first': first})


def paging_step():
    """Overflow the rail at its original scale and require forward content progress in at most two unit notches.

    The first upper more-row can replace one worker slot, leaving the tail unchanged after a correct first step.
    Only that recorded transition permits one more down notch; it must reveal a new tail. A reverse wheel restores
    the first-step window before the original up-arrow check restores the initial window. Every other anomaly fails.
    """
    import rail_positions_fixture as fixture
    step = state.setdefault('paging', {'stage': 'crowd', 'added': []})
    preferences = bpy.context.preferences.view
    stage = step['stage']
    if 'since' not in step:
        receipt = {'stage': stage, 'before': paging_snapshot(), 'events': []}
        report.setdefault('paging_stages', []).append(receipt)
        step['receipt'] = receipt
        if stage == 'crowd':
            step.update(base=preferences.ui_scale, active=ui._sessions.active,
                        original=list(parallel_sessions.sessions()))
            scale = bpy.context.preferences.system.ui_scale
            capacity = int(view_region().height / (session_rail.TEXT_BLOCK * scale)) + 2
            wanted = max(len(step['original']) + 1, capacity)
            fixture.bind(ui._controllers[report['lead']], step['added'], wanted - len(step['original']))
            step['order'] = list(parallel_sessions.sessions())
        elif stage in ('wheel', 'wheel_tail', 'wheel_return'):
            x, y = tile_point(plan().tiles[-1].session)
            # Literal wheel types: a sender's variable type reads as a held button (test_native_qa_input).
            if stage == 'wheel_return':
                kind = 'WHEELUPMOUSE'
                send('WHEELUPMOUSE', 'PRESS', x, y)
            else:
                kind = 'WHEELDOWNMOUSE'
                send('WHEELDOWNMOUSE', 'PRESS', x, y)
            receipt['events'].append({'type': kind, 'value': 'PRESS', 'x': x, 'y': y})
        elif stage == 'arrow':
            ax, ay, aw, ah = plan().arrows[0]
            x, y = int(view_region().x + ax + aw / 2), int(view_region().y + ay + ah / 2)
            for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
                send(kind, value, x, y)
                receipt['events'].append({'type': kind, 'value': value, 'x': x, 'y': y})
        else:
            paging_cleanup()
        view_region().tag_redraw()
        step['since'] = time.monotonic()
        return True
    drawn_since = [f for f in frames if f['at'] > step['since'] and f['top'] is not None]
    if len(drawn_since) < 3 and time.monotonic() - step['since'] < 3.0:
        return True
    del step['since']
    observed = paging_snapshot()
    step['receipt']['after'] = observed
    screenshot('1d-rail-' + stage.replace('_', '-') + '-after.png')
    placed, height, sessions = plan(), view_region().height, len(parallel_sessions.sessions())
    shown = observed['shown']
    if stage == 'crowd':
        report['timings'].update(paging_ui_scale=preferences.ui_scale, paging_session_count=sessions)
        check('rail_pages_when_its_tiles_do_not_fit', placed.paged and any(placed.hidden),
              [placed.panel, height, preferences.ui_scale, sessions, placed.hidden])
        check('a_paged_rail_stays_inside_the_view', placed.panel[1] >= 0 and placed.panel[1] + placed.panel[3]
              <= height, [placed.panel, height])
        check('a_paged_rail_keeps_the_main_tile_when_two_fit_and_counts_the_hidden', (shown[0] == report['lead']
              or len(shown) == 1) and placed.hidden == (0, sessions - len(shown)), [shown, placed.hidden, sessions])
        step['shown'], step['initial'] = shown, observed
        step['stage'] = 'wheel'
    elif stage == 'wheel':
        paging_window(step, observed, 1, stage)
        before = step['receipt']['before']
        check('paging_window_is_stable_before_the_first_wheel', before == step['initial'],
              [before, step['initial']])
        step['first_window'] = observed
        if shown[-1] in step['shown']:
            old_panel, new_panel = before['panel'], observed['panel']
            stable_span = (before['scale'] == observed['scale'] and old_panel[0] == new_panel[0]
                           and old_panel[2] == new_panel[2]
                           and abs(old_panel[1] + old_panel[3] - new_panel[1] - new_panel[3]) <= 1e-6)
            upper = observed['more'][0]
            check('paging_extra_notch_requires_only_a_new_upper_more_row_and_one_lost_slot',
                  before['more'][0] is None and upper is not None and upper[2] > 0 and upper[3] > 0
                  and before['more'][1] is not None and observed['more'][1] is not None
                  and len(shown) == len(before['shown']) - 1 and stable_span,
                  {'before': before, 'after': observed})
            step['stage'] = 'wheel_tail'
        else:
            check('the_wheel_over_a_paged_rail_shows_the_next_worker',
                  observed['first'] == 1 and shown[-1] not in step['shown'], observed)
            report['timings']['paging_tail_progress_notches'] = 1
            step['stage'] = 'arrow'
    elif stage == 'wheel_tail':
        paging_window(step, observed, 2, stage)
        check('the_wheel_over_a_paged_rail_shows_the_next_worker', shown[-1] not in step['shown'],
              [shown, step['shown'], observed['first']])
        report['timings']['paging_tail_progress_notches'] = 2
        step['stage'] = 'wheel_return'
    elif stage == 'wheel_return':
        paging_window(step, observed, 1, stage)
        check('a_reverse_wheel_restores_the_first_scrolled_window', observed == step['first_window'],
              [observed, step['first_window']])
        step['stage'] = 'arrow'
    elif stage == 'arrow':
        check('its_up_arrow_shows_the_first_workers_again', placed.first == 0 and shown == step['shown'], shown)
        step['stage'] = 'restore'
    else:
        check('paging_restores_the_original_sessions', parallel_sessions.sessions() == step['original'],
              [parallel_sessions.sessions(), step['original']])
        check('every_tile_back_when_there_is_room', not placed.paged and len(shown) == sessions, [shown, sessions])
        return False
    return True


def tile_point(session):
    tile = next(t for t in plan().tiles if t.session == session)
    region = view_region()
    x, y, w, h = tile.rect
    return int(region.x + x + w / 2), int(region.y + y + h / 2)


def view_point(dx=0.5, dy=0.5):
    region = view_region()
    return int(region.x + region.width * dx), int(region.y + region.height * dy)


def send(kind, value, x, y, **mods):
    window.event_simulate(type=kind, value=value, x=x, y=y, **mods)


def click(x, y):
    send('MOUSEMOVE', 'NOTHING', x, y)
    send('LEFTMOUSE', 'PRESS', x, y)
    send('LEFTMOUSE', 'RELEASE', x, y)


class QA_OT_pass_through(bpy.types.Operator):
    """A modal operator that passes every event on, as Blender's render and bake operators do while their job runs."""
    bl_idname, bl_label = "qa.pass_through", "QA pass-through modal"
    running = [False]

    def invoke(self, context, event):
        self.running[0] = True
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return {'PASS_THROUGH'} if self.running[0] else {'FINISHED', 'PASS_THROUGH'}


bpy.utils.register_class(QA_OT_pass_through)


def document_state():
    return (tuple(document.objects['Cube'].location), tuple(document.objects['Cube'].scale),
            sorted(o.name for o in document.objects), sorted(o.name for o in document.objects if o.select_get()),
            [op.bl_idname for op in window.modal_operators if op.bl_idname.lower().startswith('transform')])


def edit_keys(x, y):
    send('MOUSEMOVE', 'NOTHING', x, y)
    for key in ('G', 'X', 'DEL', 'I', 'S'):
        send(key, 'PRESS', x, y)
        send(key, 'RELEASE', x, y)
    for button in ('LEFTMOUSE', 'RIGHTMOUSE'):
        send(button, 'PRESS', x, y)
        send(button, 'RELEASE', x, y)


def chat_area():
    from angelo_agent import console
    return next(a for a in window.screen.areas if console.is_chat(a))


def request(name, tool, arguments, reply=None):
    answers[name] = []

    def answered(result):
        state[name + '_answered_at'] = time.monotonic()
        answers[name].append(result)
    if HOSTED:   # The model's call in a turn of its own, which then keeps working (``reply``: its closing message).
        return turns.request(name, tool, arguments, answered, reply=reply)
    state['requests'] = state.get('requests', 0) + 1   # Each request its own id: a reused one is refused.
    chats[name].request_tool('qa-%s-%d' % (name, state['requests']), tool, arguments, answered)


def view_redraw_ms(iterations=20):
    """One redraw of the rail's 3D view region (the document beneath, the live view or rail over it), ms."""
    with bpy.context.temp_override(window=window, area=view_area(), region=view_region()):
        started = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW', iterations=iterations)
    return round((time.perf_counter() - started) * 1000 / iterations, 3)


def room():
    return program([primitive("PLANE", "Floor", "floor", size=6),
                    primitive("CUBE", "Table Top", "top", size=1, location=[0, 0, 0.74], scale=[1.4, 0.8, 0.06]),
                    primitive("CUBE", "Back Wall", "wall", size=1, location=[0, 3, 1.4], scale=[6, 0.1, 2.8]),
                    paint("floor", "Oak Floor", [0.42, 0.28, 0.16, 1]), paint("wall", "Plaster", [0.86, 0.84, 0.8, 1])])


def lamp():
    return program([primitive("CYLINDER", "Lamp Base", "base", radius=0.3, depth=0.05, location=[0, 0, 0.025]),
                    primitive("CYLINDER", "Lamp Pole", "pole", radius=0.03, depth=1.4, location=[0, 0, 0.75]),
                    primitive("CONE", "Lamp Shade", "shade", radius=0.35, depth=0.4, location=[0, 0, 1.5]),
                    paint("shade", "Linen", [0.9, 0.85, 0.7, 1])])


def reopen(path):
    script = OUT / 'check_file.py'
    script.write_text("import bpy, json\nprint('FILE', json.dumps({'libraries': [l.filepath for l in bpy.data.libraries],"
                      " 'scenes': [s.name for s in bpy.data.scenes]}))\n")
    out = subprocess.run([bpy.app.binary_path, '-b', str(path), '--factory-startup', '--disable-autoexec',
                          '--python', str(script)], capture_output=True, text=True, timeout=120)
    rows = [json.loads(line[5:]) for line in out.stdout.splitlines() if line.startswith('FILE ')]
    return rows[0] if rows else {'error': out.stdout[-1500:]}


def advance(phase):
    state['phase'], state['at'] = phase, time.monotonic()


def waited(seconds):
    return time.monotonic() - state['at'] >= seconds


def tick():
    global window, document
    try:
        if time.monotonic() - state['started'] > 300:
            raise RuntimeError('Session rail fixture timed out in ' + state['phase'])
        phase = state['phase']
        if HOSTED:
            turns.tick()
        if phase == 'boot':
            if not qa_boot.started():
                return .1
            window = bpy.context.window_manager.windows[0]
            document = window.scene
            if connect_hero.is_shown():   # No account in this suite: the person explores the empty scene first.
                if not state.get('hero_dismissed'):
                    x, y = view_point()
                    for key in ('TAB', 'RET'):
                        send(key, 'PRESS', x, y)
                        send(key, 'RELEASE', x, y)
                    state['hero_dismissed'] = True
                return .2 if not waited(10) else finish('the connect card never went away')
            connect_hero._dismissed = True   # Explored: it stays away this session, as after the person's choice.
            from angelo_agent import director   # Director Mode, the default view a person works in.
            if not director.is_director(window):
                if 'director_asked' not in state:
                    director.request_mode(window, 'DIRECTOR')
                    state['director_asked'] = time.monotonic()
                return .2 if time.monotonic() - state['director_asked'] < 20 else finish('Director Mode never opened')
            report['layout'] = 'director'
            from angelo_agent import console
            if not any(console.is_chat(a) for a in window.screen.areas):   # The chat open, as a person keeps it to
                if 'chat_asked' not in state:                            # type in (this run starts it hidden).
                    from angelo_agent import chat_toggle
                    chat_toggle.show(window)
                    state['chat_asked'] = time.monotonic()
                return .2 if time.monotonic() - state['chat_asked'] < 15 else finish('the chat never docked')
            from angelo_agent import parallel_access, parallel_capability as cap   # A capable machine, unforced.
            check('this_machine_runs_parallel_edit', 'ANGELO_ENABLE_PARALLEL_AGENTS' not in os.environ and parallel_access.enabled() and cap.current().workers >= 2, cap.current())
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'main.blend'), check_existing=False)
            lead = ui.get_controller()
            report['lead'] = lead.state['id']
            # The person's request, as in the product, where workers start only in the lead's turn: a lead holding
            # work keeps its own scene while another is shown (document_work); without it the lead followed the shown
            # scene quietly and its tile drew that scene (this suite's lead-away phase, 2026-10-25).
            lead.state['messages'].append({'role': 'user', 'text': 'Build a room and a lamp.'})
            if HOSTED:   # Its workers' chats inherit its connection and its Auto, as a lead's do.
                connect(lead)
                lead.state['options'] = dict(lead.state.get('options') or {}, approval='AUTO_BOUNDED')
            for name in ('room', 'lamp'):
                chat = chats[name] = worker_sessions._new_chat(lead)
                started = time.perf_counter()
                if HOSTED:
                    worker_routing.start(chat, OUT / 'parts' / name / 'part.blend')
                    turns.add(name, chat)
                else:
                    chat.state['options'] = dict(chat.state.get('options') or {}, approval='AUTO_BOUNDED')
                    worker_routing.start(chat, OUT / 'parts' / name / 'part.blend')
                    chat.state['messages'].append({'role': 'user', 'text': 'Build the %s.' % name})   # Its brief, as
                    # the product sends it once the chat is bound (worker_sessions).
                    state[name + '_fence'] = chat.open_external_read()   # What this suite's requests bind to; it
                    # keeps the chat busy (its tile Working) until closed, as a model's turn would.
                report['timings'][name + '_start_s'] = round(time.perf_counter() - started, 3)
                parallel_sessions.names[chat.state['id']] = name.title()
            request('room', 'angelo_scene_program', room())
            request('lamp', 'angelo_scene_program', lamp())
            advance('building')
            return .2
        if phase == 'building':
            if not all(answers[name] for name in chats):
                return .2
            for name in chats:
                result = answers[name][0]
                check(name + '_built_in_its_worker', result.get('status') == 'committed', result)
            advance('first-parts')
            return .05
        if phase == 'first-parts':   # The lead idle, nothing forced: each worker's first part on its tile.
            for name in chats:
                session, since = chats[name].state['id'], state[name + '_answered_at']
                if on_screen_within_a_second(name + '_first_part_on_its_tile', since,
                                             lambda f, s=session, t=since: f['pictures'].get(s, 0.0) > t) is None:
                    return .05
            advance('pictures')
            return .2
        if phase == 'pictures':
            ready = all(rail_pictures.texture(s) is not None for s in parallel_sessions.sessions())
            if not ready and not waited(20):
                return .25
            check('every_tile_has_a_picture', ready, {s: rail_pictures.texture(s) is not None
                                                     for s in parallel_sessions.sessions()})
            check('rail_has_one_tile_per_session_lead_first', plan() is not None and [t.session for t in plan().tiles]
                  == [report['lead']] + [chats[n].state['id'] for n in ('room', 'lamp')])
            check('lead_tile_is_active', plan().tiles[0].active)
            covered = session_rail.top_inset(view_area().regions, view_region())
            check('rail_starts_below_the_headers_over_the_view', plan().panel[1] + plan().panel[3]
                  <= view_region().height - covered, [plan().panel, view_region().height, covered])
            check('worker_tiles_say_which_worker_and_what_it_builds',
                  [(t.role, t.title) for t in plan().tiles[1:]] == [('Worker 1', 'Room'), ('Worker 2', 'Lamp')],
                  [(t.role, t.title) for t in plan().tiles])
            check('window_shows_the_document', window.scene == document)
            report['timings']['picture_draw_ms'] = [round(s * 1000, 3) for s in rail_pictures.draw_seconds]
            screenshot('1-rail-main.png')
            region = view_region()
            state['main_view'] = tuple(region.data.view_location)
            # The lead's thumbnail stays visible while the window shows another scene (the maintainer, 2026-10-25):
            # it keeps its last picture, is never drawn from another scene, and is drawn again once its scene is back.
            state['lead_drawn'], state['away_scene'] = rail_pictures._drawn.get(report['lead'], 0.0), \
                bpy.data.scenes.new("Rail Away Probe")
            window.scene = state['away_scene']
            parallel_sessions.lead_changed()   # Out of date while away: still never drawn from the shown scene.
            advance('lead-away')
            return .1
        if phase == 'lead-away':
            if not waited(1.5):
                return .1
            lead_id = report['lead']
            check('lead_tile_keeps_its_picture_while_another_scene_shows', rail_pictures.texture(lead_id) is not None
                  and rail_pictures._drawn.get(lead_id, 0.0) == state['lead_drawn'],
                  {'drawn': rail_pictures._drawn.get(lead_id), 'before': state['lead_drawn']})
            screenshot('1a-lead-tile-while-away.png')
            # The composer says why the lead waits and how back, as whole as the chat allows (no hint is ever cut
            # mid-sentence), then the widest hint any shipped language has, at 1x and at 2x (half the logical width:
            # a narrower chat). Whether a hint fits whole follows the drawn width in layout units, which a display's
            # pixel size halves or doubles: judged from it on every platform (composer_hint_qa.as_the_chat_allows;
            # release-106's Windows T4 run, 2026-10-25, where the 2x chat is 163 units wide).
            from angelo_agent import chat_composer, document_scene
            whole, seen = composer_hint_qa.as_the_chat_allows(chat_area(), document_scene.notice(ui.get_controller())
                                                              or '')
            check('the_away_hint_is_drawn_as_whole_as_the_chat_allows',
                  whole and seen['hint'].startswith(document_scene.AWAY.split('{')[0]), seen)
            report.setdefault('hint_regimes', {})['away'] = seen['regime']
            state['long_hint'] = composer_hint_qa.longest(composer_hint_qa.drawn(chat_area()).get('font_size', 12))
            state['hint'] = chat_composer.hint
            chat_composer.hint = lambda controller, text=state['long_hint']: text
            chat_area().tag_redraw()   # A new hint, as any change of the composer's text redraws it.
            advance('hint-1x')
            return .1
        if phase in ('hint-1x', 'hint-2x'):   # The chat's scale includes the display's pixel size (chat_view.ui_scale).
            scale = state.setdefault('hint_scale', None) and 2 * state['hint_scale']
            whole, seen = composer_hint_qa.as_the_chat_allows(chat_area(), state['long_hint'])
            if not (whole and (scale is None or seen['scale'] == scale)) and not waited(3.0):
                return .1
            check('the_longest_hint_is_drawn_as_whole_as_the_chat_allows_at_%s' % phase[-2:],
                  whole and scale in (None, seen['scale']), dict(seen, expected_scale=scale))
            report.setdefault('hint_regimes', {})[phase] = seen['regime']   # whole, sentences or prefix: evidence.
            screenshot('1a-longest-hint-%s.png' % phase[-2:])
            if phase == 'hint-1x':
                state['hint_scale'] = seen['scale']
                bpy.context.preferences.view.ui_scale = 2.0
                advance('hint-2x')
                return .1
            bpy.context.preferences.view.ui_scale = 1.0
            from angelo_agent import chat_composer
            chat_composer.hint = state.pop('hint')
            window.scene = document
            state['back_at'] = time.monotonic()
            parallel_sessions.lead_changed()
            advance('lead-back')
            return .1
        if phase == 'lead-back':
            lead_id = report['lead']
            drawn = rail_pictures._drawn.get(lead_id, 0.0) > state['back_at']
            if not drawn and not waited(2.0):
                return .1
            check('lead_tile_is_drawn_again_once_its_scene_is_shown', drawn and window.scene == document)
            bpy.data.scenes.remove(state.pop('away_scene'))
            advance('text-info')
            return .05
        if phase == 'text-info':
            if text_case():
                return .05
            advance('paging')
            return .05
        if phase == 'paging':
            if paging_case():
                return .05
            request('lamp', 'angelo_scene_program', program([primitive("CUBE", "Lamp Foot", "foot", size=0.2,
                                                                       location=[0.4, 0, 0.1])]))
            advance('rail-follows')
            return .05
        if phase == 'rail-follows':
            if not answers['lamp']:
                return .05 if not waited(20) else finish('the lamp edit never answered')
            check('worker_edit_committed', answers['lamp'][0].get('status') == 'committed', answers['lamp'])
            lamp_id, since = chats['lamp'].state['id'], state['lamp_answered_at']
            if on_screen_within_a_second('an_edit_on_its_tile', since,
                                         lambda f: f['pictures'].get(lamp_id, 0.0) > since) is None:
                return .05
            screenshot('1b-rail-after-worker-edit.png')
            click(*tile_point(chats['room'].state['id']))
            advance('to-room')
            return .3
        if phase == 'to-room':
            room_id = chats['room'].state['id']
            if (ui._sessions.active != room_id or live_view_draw.shown != room_id) and not waited(10):
                return .2
            check('click_switches_the_chat', ui._sessions.active == room_id)
            check('click_shows_the_worker_live', live_view_draw.shown == room_id)
            check('live_view_drew_the_part', any(redrawn for _s, redrawn in live_view_draw.draws))
            check('window_still_shows_the_document', window.scene == document)
            check('worker_tile_is_active', next(t for t in plan().tiles if t.session == room_id).active)
            check('live_view_holds_the_view_beneath', live_view.held(window, view_area()))
            screenshot('2-live-room.png')
            state['revision_before'] = live_view.revision(room_id)   # A worker edit while its part shows.
            request('room', 'angelo_scene_program', program([primitive("CUBE", "Corner Box", "box", size=0.2,
                                                                       location=[2.6, -2.6, 0.1])]))
            advance('live-follows')
            return .05
        if phase == 'live-follows':
            room_id = chats['room'].state['id']
            if not answers['room']:
                return .05 if not waited(20) else finish('the room edit never answered')
            since, before = state['room_answered_at'], state['revision_before']
            if on_screen_within_a_second('an_edit_in_the_live_view', since,
                                         lambda f: any(s == room_id and r != before for s, r in f['live'])) is None:
                return .05
            screenshot('2b-live-room-after-worker-edit.png')
            state['view_before_orbit'] = tuple(live_view_draw.view_of(room_id).rotation)
            state['artist_view'] = (tuple(view_region().data.view_rotation), tuple(view_region().data.view_location))
            state['draws_before_orbit'] = sum(1 for _s, redrawn in live_view_draw.draws if redrawn)
            x, y = view_point()
            qa_input.send(window, qa_input.drag_path((x, y), (x + 144, y + 48), button='MIDDLEMOUSE'))   # An orbit.
            advance('orbited')
            return .4
        if phase == 'orbited':
            room_id = chats['room'].state['id']
            rotated = tuple(live_view_draw.view_of(room_id).rotation) != state['view_before_orbit']
            check('orbit_moves_the_live_view', rotated)
            check('orbit_never_moves_the_artists_own_view', (tuple(view_region().data.view_rotation),
                                                             tuple(view_region().data.view_location))
                  == state['artist_view'])
            check('live_view_redrawn_after_orbit',
                  sum(1 for _s, redrawn in live_view_draw.draws if redrawn) > state['draws_before_orbit'])
            screenshot('3-live-room-orbited.png')
            state['cube_location'] = tuple(document.objects['Cube'].location)
            state['objects'] = sorted(o.name for o in document.objects)
            state['selected'] = sorted(o.name for o in document.objects if o.select_get())
            x, y = view_point(0.6, 0.4)
            send('MOUSEMOVE', 'NOTHING', x, y)
            for key in ('G', 'X', 'DEL', 'I', 'E', 'K'):   # Edit keys a live view must never pass on.
                send(key, 'PRESS', x, y)
                send(key, 'RELEASE', x, y)
            send('RIGHTMOUSE', 'PRESS', x, y)
            send('RIGHTMOUSE', 'RELEASE', x, y)
            advance('held')
            return .3
        if phase == 'held':
            check('edit_keys_are_held_and_said', live_view_draw.notice_until > time.monotonic())
            check('no_tool_started_behind_the_live_view', not [op.bl_idname for op in window.modal_operators
                                                             if op.bl_idname.startswith(('TRANSFORM', 'transform'))])
            check('document_untouched', tuple(document.objects['Cube'].location) == state['cube_location']
                  and sorted(o.name for o in document.objects) == state['objects'])
            check('lead_selection_untouched', sorted(o.name for o in document.objects if o.select_get())
                  == state['selected'])
            screenshot('4-live-read-only-notice.png')
            state['document'] = document_state()
            with bpy.context.temp_override(window=window):
                bpy.ops.qa.pass_through('INVOKE_DEFAULT')   # A modal in front that hands every event on (H1).
            edit_keys(*view_point(0.6, 0.4))
            advance('passive')
            return .3
        if phase == 'passive':
            check('a_passing_modal_never_opens_the_view_beneath', document_state() == state['document'],
                  [document_state(), state['document']])
            QA_OT_pass_through.running[0] = False
            send('MOUSEMOVE', 'NOTHING', *view_point(0.55, 0.5))   # Its next event ends it, as a person's would.
            from angelo_agent import card_host, chat_hint
            card_host.request(chat_hint.ChatHint("manual", lambda: "manual", lambda: ()))   # A corner card (H2).
            # Shown now, as about_card shows one: this automation run (ANGELO_DISABLE_DEFAULT_CHAT) turns off the
            # notices' own pump (app_notices.automation_off), and this check is about typing with a card up.
            card_host.pump(lambda: True)
            advance('card')
            return .3
        if phase == 'card':
            from angelo_agent import card_host, chat_view
            if not card_host.shown_on(view_area().as_pointer()) and not waited(8):
                return .2
            check('a_corner_card_shows_over_the_live_view', card_host.shown_on(view_area().as_pointer()))
            chat_view._views.setdefault(chat_area().as_pointer(), {})["focus_request"] = True
            x, y = view_point(0.5, 0.45)
            send('MOUSEMOVE', 'NOTHING', x, y)
            for key, text, mods in (('A', 'a', {}), ('X', 'x', {}), ('S', 'ß', {'alt': True}),
                                    ('D', '∂', {'alt': True})):
                window.event_simulate(type=key, value='PRESS', x=x, y=y, unicode=text, **mods)
                window.event_simulate(type=key, value='RELEASE', x=x, y=y, **mods)
            advance('typed')
            return .3
        if phase == 'typed':
            from angelo_agent import card_host
            draft = chat_area().spaces.active.history[-1].body if chat_area().spaces.active.history else ''
            check('typing_over_the_live_view_with_a_card_reaches_the_chat', draft.endswith('ax'), draft)
            check('typing_and_option_keys_never_reach_the_document', document_state() == state['document'],
                  [document_state(), state['document']])
            screenshot('5-live-typing-with-card.png')
            card_host.close()
            from angelo_agent import view_editor
            before = len(live_view_draw.draws)
            rows = view_editor.screenshot(window, OUT / 'unused.png')
            check('lead_capture_draws_no_live_view', len(live_view_draw.draws) == before)
            (OUT / '6-lead-capture.png').write_bytes(view_editor.encode_png(rows.tobytes(), rows.shape[1],
                                                                            rows.shape[0], bottom_up=True))
            report['shots'].append('6-lead-capture.png')
            # Its pixels: the 3D view's box is the document, never the live view the window shows (P1-4 M3).
            import imbuf
            import numpy
            screenshot('6-front-buffer.png')
            front = imbuf.load(str(OUT / '6-front-buffer.png'))
            with front.with_buffer() as view:
                shown = numpy.array(view, dtype=numpy.uint8)
            front.free()
            height, width = rows.shape[:2]
            bottom, top, left, right = view_editor._box(view_area(), view_editor._scale(window, width), height, width)
            box = rows[bottom:top, left:right, :3].astype(float)
            differs = float(numpy.abs(box - shown[bottom:top, left:right, :3].astype(float)).mean()) \
                if shown.shape[:2] == rows.shape[:2] else None
            check('lead_capture_shows_the_document_not_the_live_view', differs is not None and differs > 4.0,
                  differs)
            check('lead_capture_has_a_drawn_top_bar', float(rows[-12:, :, :3].std()) > 1.0)
            x, y = view_point()
            room_id = chats['room'].state['id']
            state['view_before_lost'] = tuple(live_view_draw.view_of(room_id).rotation)
            send('MOUSEMOVE', 'NOTHING', x, y)
            send('MIDDLEMOUSE', 'PRESS', x, y)   # Its release never comes (another handler took it, M2).
            send('LEFTMOUSE', 'PRESS', x, y)
            send('LEFTMOUSE', 'RELEASE', x, y)
            for step in range(1, 6):
                send('MOUSEMOVE', 'NOTHING', x + 30 * step, y + 10 * step)
            advance('lost-release')
            return .3
        if phase == 'lost-release':
            room_id = chats['room'].state['id']
            check('a_lost_release_never_leaves_a_drag', not live_view_draw.navigation.drag.mode
                  and tuple(live_view_draw.view_of(room_id).rotation) == state['view_before_lost'])
            # A lapsed lease gives the artist their document back whole: a click there selects again (P1-4 M2).
            state['renew'] = live_view_draw.renew
            live_view_draw.renew = lambda window: None
            live_view.hold(window, None, 0)
            click(*view_point(0.92, 0.12))   # Empty space in the artist's own view: deselects.
            advance('lapsed')
            return .3
        if phase == 'lapsed':
            check('a_lapsed_hold_gives_the_document_its_input_back',
                  not any(o.select_get() for o in document.objects), [o.name for o in document.objects
                                                                      if o.select_get()])
            live_view_draw.renew = state.pop('renew')
            for name in state['selected']:
                document.objects[name].select_set(True)
            live_view_draw.renew(window)
            x, y = view_point()
            send('MOUSEMOVE', 'NOTHING', x, y)
            for _ in range(2):   # Maximized and back: the hold moves with the area and never reads a freed one (M1).
                send('SPACE', 'PRESS', x, y, ctrl=True)
                send('SPACE', 'RELEASE', x, y, ctrl=True)
            advance('maximized-back')
            return 1.0
        if phase == 'maximized-back':
            check('the_hold_follows_the_area_after_maximize_and_back', live_view.held(window, view_area()))
            edit_keys(*view_point(0.6, 0.4))
            advance('after-maximize')
            return .3
        if phase == 'after-maximize':
            check('edits_still_held_after_maximize_and_back', document_state() == state['document'],
                  [document_state(), state['document']])
            x, y = view_point()
            send('MOUSEMOVE', 'NOTHING', x, y)
            send('HOME', 'PRESS', x, y)
            send('HOME', 'RELEASE', x, y)
            advance('framed')
            return .3
        if phase == 'framed':
            low, high = live_view.bounds(chats['room'].state['id'])
            from mathutils import Vector
            centre = (Vector(low) + Vector(high)) / 2
            location = Vector(live_view_draw.view_of(chats['room'].state['id']).location)
            check('home_frames_the_part', (location - centre).length < 1e-3, [tuple(location), tuple(centre)])
            view, projection = live_view_draw.matrices(chats['room'].state['id'], view_area().spaces.active,
                                                       view_region())
            corners = [projection @ view @ Vector((a[0], b[1], c[2], 1.0)) for a in (low, high) for b in (low, high)
                       for c in (low, high)]
            ndc = [max(abs(p.x / p.w), abs(p.y / p.w)) for p in corners]
            check('home_shows_the_whole_part', max(ndc) <= 1.0, ndc)
            x, y = view_point()
            click(x, y)
            advance('inspected')
            return .3
        if phase == 'inspected':
            found = live_view_draw.inspected
            check('a_click_names_the_object_under_the_pointer', found is not None and found[1] in {
                'Floor', 'Table Top', 'Back Wall'}, found)
            check('a_click_never_selects_behind_the_live_view', sorted(o.name for o in document.objects
                                                                      if o.select_get()) == state['selected'])
            screenshot('7-live-inspect.png')
            if HOSTED:   # Sent to its worker, which changes it before the next message it reads.
                chats['lamp'].set_options(approval='ASK')
            else:
                chats['lamp'].state['options'] = dict(chats['lamp'].state['options'], approval='ASK')
            # Timed from the request: the earliest the approval can exist. The suite notices a pending call only when it
            # polls, after the rail may already have drawn it (Mac 1: drawn 0.1 s before the suite saw it).
            state['asked_at'] = time.monotonic()
            request('lamp', 'angelo_scene_program', program([primitive("UV_SPHERE", "Bulb", "bulb", radius=0.1,
                                                                       location=[0, 0, 1.4])]))
            advance('approval')
            return .3
        if phase == 'approval':
            lamp_id = chats['lamp'].state['id']
            if not any(getattr(call, 'approval', None) for call in chats['lamp'].pending.values()):
                return .05 if not waited(20) else finish('the lamp edit never asked for approval')
            if HOSTED:   # The earliest it exists here: when the lead's mirror applied the worker's card.
                state['asked_at'] = turns.card_at('lamp')
            if on_screen_within_a_second('an_approval_on_its_tile', state['asked_at'], lambda f: f['tiles'].get(
                    lamp_id, ('',))[0] == session_rail.APPROVAL) is None:
                return .05
            screenshot('8-rail-approval.png')
            click(*tile_point(report['lead']))
            advance('back')
            return .3
        if phase == 'back':
            if (ui._sessions.active != report['lead'] or live_view_draw.shown is not None) and not waited(10):
                return .2
            check('main_tile_brings_back_the_document', ui._sessions.active == report['lead']
                  and live_view_draw.shown is None)
            check('main_tile_releases_the_hold', not live_view.held(window, view_area()))
            check('main_view_is_where_it_was_left', all(abs(a - b) < 1e-4 for a, b in
                                                        zip(view_region().data.view_location, state['main_view'])))
            screenshot('9-rail-back-on-main.png')
            # The lead idle on its main tile, nothing forced: a worker's step text, clock, Done and Merged on its tile.
            request('room', 'angelo_workers_progress', {'steps': ['Floor', 'Walls', 'Table'], 'done': 1})
            advance('step-follows')
            return .05
        room_id = chats['room'].state['id'] if 'room' in chats else None
        room_says = (lambda f: f['tiles'].get(room_id, ('', ''))[1])
        if phase == 'step-follows':
            if not answers['room']:
                return .05 if not waited(20) else finish('the progress report never answered')
            if on_screen_within_a_second('a_step_on_its_tile', state['room_answered_at'],
                                         lambda f: 'Walls' in room_says(f) and '1 of 3 steps' in room_says(f)) is None:
                return .05
            built = 'Built the room: floor, wall, table.'
            if not HOSTED:
                chats['room'].state['messages'].append({'role': 'assistant', 'text': built})
            request('room', 'angelo_workers_progress', {'steps': ['Floor', 'Walls', 'Table'], 'done': 3}, reply=built)
            advance('done-follows')
            return .05
        if phase == 'done-follows':   # Its reply and every step reported done: Done, waiting for merge.
            if not answers['room']:
                return .05 if not waited(20) else finish('the last progress report never answered')
            if 'turn_ended_at' not in state:   # Its turn ends (break-it P1-10 R1: the open read kept it Working).
                if HOSTED and 'turn_ending' not in state:
                    check('last_report_shown', answers['room'][0].get('success') is True, answers['room'])
                    state['turn_ending'] = turns.end_turn('room')   # Its model says it is done; the worker forwards.
                if HOSTED:
                    if turns.idle_at('room') is None:   # From when the lead's mirror shows the turn ended.
                        return .05 if not waited(20) else finish('the room turn never ended')
                    check('room_turn_ended', state['turn_ending'])
                    state['turn_ended_at'] = turns.idle_at('room')
                else:
                    check('last_report_shown', answers['room'][0].get('success') is True, answers['room'])
                    check('room_turn_ended', chats['room'].close_external_read(state.pop('room_fence')))
                    state['turn_ended_at'] = time.monotonic()
            if on_screen_within_a_second('done_on_its_tile', state['turn_ended_at'], lambda f: f['tiles'].get(
                    room_id, ('',))[0] == session_rail.DONE) is None:
                return .05
            # A turn running, as a model's would (no request is made meanwhile): the tile shows its clock.
            room_chat = chats['room']
            if HOSTED:   # A turn of its model's, sent as any; its clock starts when its mirror begins it.
                if 'clock_asked' not in state:
                    state['clock_asked'] = turns.hold_turn('room') or True
                    return .05
                if not room_chat.busy or getattr(room_chat, 'turn_started', None) is None:
                    return .05 if not waited(20) else finish('the room turn never started')
            else:
                room_chat.busy = True
                room_chat.turn_started = (getattr(room_chat, '_request_generation', None), time.monotonic())
            state['turn_at'] = room_chat.turn_started[1]
            advance('clock-follows')
            return .05
        if phase == 'clock-follows':
            for second in (1, 2):
                if on_screen_within_a_second('clock_second_%d_on_its_tile' % second, state['turn_at'] + second,
                                             lambda f, n=second: room_says(f).endswith(' %ds' % n)) is None:
                    return .05
            room_chat = chats['room']
            if HOSTED:
                turns.end_turn('room')
            else:
                room_chat.busy, room_chat.turn_started = False, None
            parallel_sessions.merged(room_id)   # As the lead's merge marks it.
            state['merged_at'] = time.monotonic()
            advance('merged-follows')
            return .05
        if phase == 'merged-follows':
            if on_screen_within_a_second('merged_on_its_tile', state['merged_at'], lambda f: f['tiles'].get(
                    room_id, ('',))[0] == session_rail.MERGED) is None:
                return .05
            screenshot('10-rail-done-merged.png')
            # What each rail redraw costs: one redraw of the artist's view (the rail has no region of its own).
            report['timings']['view_redraw_ms'] = {'document': view_redraw_ms()}
            mesh = document.objects['Cube'].data
            extra = [bpy.data.objects.new('QA Load %04d' % index, mesh) for index in range(2000)]
            for index, item in enumerate(extra):
                item.location = (index % 50 * 0.3 - 7.5, index // 50 * 0.3 - 6.0, -2.0)
                document.collection.objects.link(item)
            view_redraw_ms(2)   # Its first draws build the new objects' batches.
            report['timings']['view_redraw_ms']['document_plus_2000'] = view_redraw_ms()
            bpy.data.batch_remove(extra)
            from angelo_agent import rail_redraw
            report['timings']['rail_redraw_rate_per_s'] = rail_redraw.RATE
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'main.blend'), check_existing=False)
            saved = reopen(OUT / 'main.blend')
            check('saved_file_has_no_library', saved.get('libraries') == [], saved)
            check('saved_file_has_only_the_documents_scenes', saved.get('scenes') == [s.name for s in bpy.data.scenes], saved)
            check('rail_and_pictures_never_failed', session_rail_draw.last_error is None
                  and rail_pictures.last_error is None, [session_rail_draw.last_error, rail_pictures.last_error])
            report['timings']['live_draw_ms'] = [round(s * 1000, 3) for s, redrawn in live_view_draw.draws if redrawn]
            report['timings']['live_copy_ms'] = [round(s * 1000, 3) for s, redrawn in live_view_draw.draws
                                                 if not redrawn][-50:]
            report['timings']['reload_ms'] = [round(s * 1000, 3) for s in parallel_sessions.refresh_seconds]
            return finish()
        return .1
    except Exception:
        return finish(traceback.format_exc())


def finish(error=None):
    if state['done']:
        return None
    state['done'] = True
    report.update(success=error is None, error=error, phase=state['phase'])
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault('cleanup_error', traceback.format_exc())
    (OUT / 'session-rail.json').write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_SESSION_RAIL', json.dumps({'success': report['success'], 'phase': state['phase']}), flush=True)
    if not report['success']:
        os._exit(1)   # Report written and workers stopped above.
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=.5)
