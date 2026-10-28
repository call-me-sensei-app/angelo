# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stages of one test_blender_rail_positions case (1.0.6), each a generator the suite's scenario runs: it yields
waits (``rail_positions_run.until`` / ``pause``) and checks what the product drew by itself. Real events only
(``Window.event_simulate``, ``qa_input.press``); a fixture's own change (an approval added, a preference set from
code) tags a redraw, as the change would in the product. Runs inside the app only (imports ``bpy``)."""

import json
import os
import subprocess

import bpy

import qa_input
import rail_positions_checks as geometry
import rail_positions_fixture as fx
import rail_positions_run as run
from rail_positions_run import S, check, pause, plan, tiles, until


def scrolling(tag):
    """The wheel over the panel pages it toward the hidden tiles and back; a trackpad swipe along it pages it."""
    placed = plan()
    horizontal = getattr(placed, 'orientation', None) == 'HORIZONTAL'
    x, y = run.to_window(placed.tiles[len(placed.tiles) // 2].rect)
    later = placed.hidden[1] > 0   # Toward the side tiles are hidden on first (the case before may have paged on).
    wheel = {1: 'WHEELDOWNMOUSE', -1: 'WHEELUPMOUSE'}
    for toward in ((1, -1) if later else (-1, 1)):
        before = plan().first
        S.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))
        S.window.event_simulate(type=wheel[toward], value='PRESS', x=int(x), y=int(y))
        ok = yield until(lambda: plan() is not None and (plan().first - before) * toward > 0)
        check('%s__the_wheel_pages_%s' % (tag, 'on' if toward > 0 else 'back'), ok, [before, plan().first])
        run.geometry_of(tag, 'wheel')
    run.shot(tag, 2, 'wheel')
    toward = 1 if plan().hidden[1] > 0 else -1   # Then hidden on both sides, so a swipe either way pages it.
    S.window.event_simulate(type=wheel[toward], value='PRESS', x=int(x), y=int(y))
    yield pause(0.4)
    before, step = plan().first, 48 * placed.scale
    step *= 1 if plan().hidden[1] > 0 else -1   # Toward the side that still hides tiles (a short crowd ends soon).
    # From the panel's centre as it stands now: a page changes its length, and a swipe that starts off it is the
    # view's (Windows T4: the last page's column ended above the old point).
    x, y = run.to_window(plan().panel)
    for _ in range(10):   # Fingers along the strip (down a column): ten short swipes from one point over it.
        S.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))
        S.window.event_simulate(type='TRACKPADPAN', value='NOTHING', x=int(x - step if horizontal else x),
                                y=int(y if horizontal else y + step))
    S.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))
    ok = yield until(lambda: plan() is not None and plan().first != before)
    check(tag + '__a_trackpad_swipe_pages', ok, [before, plan().first, 'horizontal' if horizontal else 'vertical'])
    run.geometry_of(tag, 'swipe')
    run.shot(tag, 3, 'swipe')


def more_lines(tag):
    """Each "+N more" line (``Layout.more``) pages its way when clicked and its count follows."""
    for side, toward, name in ((1, 1, 'after'), (0, -1, 'before')):
        placed = plan()
        rects = geometry.more_rects(placed)
        dropped = rects is not None and not any(rects) and placed.arrows is not None   # Lines gone: arrows page.
        if rects is None or placed.hidden[side] == 0 or (rects[side] is None and not dropped):
            check('%s__more_%s_is_there_to_click' % (tag, name), placed.hidden[side] == 0, [rects, placed.hidden])
            continue
        before, hidden = placed.first, placed.hidden[side]
        if dropped:
            qa_input.press(S.window, run.at(lambda placed: placed.arrows[side] if placed.arrows else None))
        else:
            qa_input.press(S.window, run.at(lambda placed: (geometry.more_rects(placed) or (None, None))[side]))
        ok = yield until(lambda: plan() is not None and (plan().first - before) * toward > 0)
        check('%s__more_%s_pages_its_way' % (tag, name), ok, [before, plan().first])
        check('%s__more_%s_count_follows' % (tag, name), plan().hidden[side] < hidden, [hidden, plan().hidden])
        run.geometry_of(tag, 'more')
    run.shot(tag, 4, 'more')


def approval(tag):
    """A worker waits for approval: one hidden when the panel pages (the arrows and "+N more" on its side carry the
    amber mark: ``Layout.attention``), else one in view. Returns ``[session, call id]``."""
    before, after = geometry.hidden_sides(run.order(), tiles())
    shown_workers = [s for s in tiles() if s in S.workers]
    session = (after or before or shown_workers[-1:] or S.workers[-1:])[0]
    call = fx.ask_approval(session)
    mark = S.probe.mark()
    fx.redraw(S.window)
    ok = yield until(lambda: run.drawn_since(mark) is not None)
    check(tag + '__drawn_with_an_approval', ok)
    yield pause(0.3)
    run.geometry_of(tag, 'approval')
    run.shot(tag, 5, 'approval-hidden' if session in after + before else 'approval-shown')
    return [session, call]


def tile_click(tag, waiting_session):
    """A click on a worker's tile: that worker is the big view (its live view), its tile active and in view, and no
    job changed. A switch from elsewhere brings a hidden worker into view; Main's tile brings the document back."""
    from angelo_agent import live_view_draw, parallel_sessions, session_rail_input
    lead = S.lead.state['id']
    rects = geometry.more_rects(plan())
    if not any(t.session in S.workers for t in plan().tiles) and rects and rects[1] is not None:
        # A narrow view's strip shows Main alone: its "+N more" line pages a worker into view, as a person would.
        qa_input.press(S.window, run.at(lambda placed: (geometry.more_rects(placed) or (None, None))[1]))
        yield until(lambda: any(t.session in S.workers for t in plan().tiles))
    workers = [t for t in reversed(plan().tiles) if t.session in S.workers]
    target = next((t for t in workers if t.session != waiting_session), workers[0] if workers else None)
    if target is None:
        check(tag + '__a_worker_tile_to_click', False, tiles())
        return
    before = fx.job_state(S.window, S.lead, run.order())
    qa_input.press(S.window, run.at(run.tile_rect(target.session), target.rect))
    ok = yield until(lambda: parallel_sessions._active() == target.session and live_view_draw.shown == target.session
                     and live_view_draw.holding(S.window, fx.window_region(S.window)[0]))
    check(tag + '__a_tile_click_shows_that_worker', ok, [parallel_sessions._active(), live_view_draw.shown])
    yield pause(0.4)
    shown = {t.session: t for t in plan().tiles}
    check(tag + '__the_clicked_worker_stays_in_view_and_active', target.session in shown
          and shown[target.session].active, list(shown))
    check(tag + '__a_tile_click_changes_no_job', fx.job_state(S.window, S.lead, run.order()) == before, before)
    run.geometry_of(tag, 'live', live=target.session)
    run.shot(tag, 6, 'live')
    hidden = [s for s in S.workers if s not in tiles()]
    if hidden:
        session_rail_input.switch(hidden[-1], S.window)   # From the chat's sessions menu, say.
        ok = yield until(lambda: hidden[-1] in tiles())
        check(tag + '__a_switch_brings_a_hidden_worker_into_view', ok, tiles())
        run.geometry_of(tag, 'switched', live=hidden[-1])
    if lead in tiles():
        qa_input.press(S.window, run.at(run.tile_rect(lead)))
    else:
        session_rail_input.switch(lead, S.window)
    ok = yield until(lambda: parallel_sessions._active() == lead and live_view_draw.shown is None)
    check(tag + '__mains_tile_brings_the_document_back', ok, parallel_sessions._active())


def minimized(tag, waits, by_click):
    """The header's minimize control (or the preference, ``by_click`` False): no rail, the round button at the top
    right with the worker count, marked while a worker waits for approval (``waits``: ``[session, call]``, cleared
    here to see the mark go); a click on it (or the preference) brings the panel back where it was."""
    from angelo_agent import parallel_setting
    position, mark = parallel_setting.rail_position(), S.probe.mark()
    if by_click:
        if getattr(plan(), 'minimize', None) is None:
            check(tag + '__header_has_a_minimize_control', False, 'Layout has no minimize rect')
            return
        qa_input.press(S.window, run.at(lambda placed: getattr(placed, 'minimize', None)))
    else:
        parallel_setting.set_rail_minimized(True)
        fx.redraw(S.window)
    ok = yield until(lambda: parallel_setting.rail_minimized() and any(f.circle for f in S.probe.since(mark)))
    check(tag + '__minimizes_to_the_round_button', ok, [parallel_setting.rail_minimized(), S.probe.circle_error])
    if not ok:
        return
    yield pause(0.4)
    first = next(f.n for f in S.probe.since(mark) if f.circle)   # From the first frame with the button on: an idle
    frames = S.probe.since(first - 1)                           # view draws no more, and need not.
    check(tag + '__no_rail_drawn_while_minimized', frames and not any(f.plan is not None or f.tiles for f in frames),
          [(f.n, f.plan is not None, f.tiles) for f in frames[-6:]])
    yield from circle(tag, waits, 7, exact=by_click)
    if waits[0] is not None:
        fx.clear_approval(*waits)
        waits[:] = [None, None]
        mark = S.probe.mark()
        fx.redraw(S.window)
        ok = yield until(lambda: any(f.circle and not f.circle.get('waiting') for f in S.probe.since(mark)))
        check(tag + '__the_mark_goes_with_the_approval', ok)
        yield from circle(tag, waits, 8, exact=by_click)
    mark = S.probe.mark()
    found, frame = fx.find_circle(S.window)
    if by_click and found is not None:
        _area, region = fx.window_region(S.window)
        qa_input.press(S.window, region.x + found[0], region.y + found[1])
    else:
        check(tag + '__the_button_can_be_clicked', not by_click, 'rail_minimized.hit is True nowhere')
        parallel_setting.set_rail_minimized(False)
        fx.redraw(S.window)
    ok = yield until(lambda: not parallel_setting.rail_minimized() and run.drawn_since(mark) is not None)
    check(tag + '__the_button_restores_the_panel', ok, [parallel_setting.rail_minimized(), frame])
    check(tag + '__restored_where_it_was', parallel_setting.rail_position() == position, parallel_setting.rail_position())
    if ok:
        run.geometry_of(tag, 'restored')
        run.shot(tag, 9, 'restored')


def cramped(tag):
    """No readable panel fits this view (``session_rail_strip.readable``): its round button stands for it. Right only
    where the view's clear room cannot hold the least panel (``rail_positions_checks.room_holds``); the button is
    then checked as a minimized one is, never larger than the Angelo button."""
    from angelo_agent import chat_view, live_view_label, parallel_setting
    _area, region = fx.window_region(S.window)
    found, readings = fx.obstacles(S.window)
    holds = geometry.room_holds(found, (region.width, region.height), run.least_pixels(),
                                parallel_setting.rail_position(), live_view_label.room(chat_view.ui_scale()))
    check(tag + '__no_room_the_round_button_stands_in', not holds, dict(readings, least=run.least_pixels()))
    yield from circle(tag, [None, None], 7, exact=False)


def circle(tag, waits, number, exact=True):
    """The button as it was drawn (count, mark) and as its own hit test finds it (place, size: the Angelo button's
    where the view has room, ``exact``; never larger where it has not)."""
    from angelo_agent import chat_launcher
    drawn = next((f.circle for f in reversed(S.probe.frames) if f.circle), {}) or {}
    waiting = waits[0] is not None
    check(tag + '__the_button_counts_the_workers', drawn.get('count') == len(S.workers),
          {'drawn': drawn.get('count'), 'workers': len(S.workers), 'sessions': len(run.order())})
    check(tag + '__the_button_is_marked_%s' % ('while_one_waits' if waiting else 'only_while_one_waits'),
          bool(drawn.get('waiting')) == waiting, drawn.get('waiting'))
    found, frame = fx.find_circle(S.window)
    check(tag + '__the_button_has_a_hit', found is not None and frame == 'window', frame)
    if found is not None:
        _area, region = fx.window_region(S.window)
        obstacles, readings = fx.obstacles(S.window)
        scale = drawn.get('scale') or bpy.context.preferences.system.ui_scale
        run.rules(tag, geometry.circle_rules(found, (region.width, region.height), obstacles, scale,
                                             chat_launcher.DISC, exact), 'minimized')
        run.gizmo(tag, readings, 'minimized')
        run.report['readings'].setdefault(tag, {})['circle_%d' % number] = dict(readings, circle=found)
    run.shot(tag, number, 'minimized-approval' if waiting else 'minimized')
    yield pause(0)


CHILD = ("import bpy, json; a = bpy.context.preferences.addons.get('angelo_agent'); p = getattr(a, 'preferences', None);"
         " print('ANGELO_RAIL_PREFS ' + json.dumps({'position': getattr(p, 'rail_position', None),"
         " 'minimized': getattr(p, 'rail_minimized', None), 'enabled': a is not None}), flush=True)")


def persistence():
    """The place and the minimized state are the add-on's preferences, kept as Workers at once is: stored there, the
    preferences marked to save, saved, and read back by a second, background Angelo from the same isolated profile."""
    from angelo_agent import parallel_setting
    run.state['phase'] = 'persistence'
    addon = bpy.context.preferences.addons.get('angelo_agent')
    preferences = getattr(addon, 'preferences', None)
    check('the_panel_settings_are_add_on_preferences', hasattr(preferences, 'rail_position')
          and hasattr(preferences, 'rail_minimized'), addon is not None)
    bpy.context.preferences.is_dirty = False
    parallel_setting.set_rail_position('BOTTOM')
    parallel_setting.set_rail_minimized(True)
    stored = (getattr(preferences, 'rail_position', None), getattr(preferences, 'rail_minimized', None))
    check('place_and_minimized_are_stored_on_the_preferences', stored == ('BOTTOM', True), stored)
    check('a_change_marks_the_preferences_to_save', bpy.context.preferences.is_dirty)
    if not os.environ.get('BLENDER_USER_CONFIG'):
        run.report['readings']['persistence'] = 'save and read skipped: no isolated BLENDER_USER_CONFIG (a hand run)'
    else:
        with bpy.context.temp_override(window=S.window):
            bpy.ops.wm.save_userpref()
        log = run.OUT / 'preferences-read-back.log'
        with open(log, 'w', encoding='utf-8') as stream:
            child = subprocess.Popen([bpy.app.binary_path, '--background', '--disable-autoexec', '--python-expr',
                                      CHILD], stdout=stream, stderr=subprocess.STDOUT, env=dict(os.environ))
        done = yield until(lambda: child.poll() is not None, 120)
        if not done:
            child.kill()
        line = next((text for text in log.read_text(encoding='utf-8', errors='replace').splitlines()
                     if text.startswith('ANGELO_RAIL_PREFS ')), None)
        read = json.loads(line.split(' ', 1)[1]) if line else None
        check('place_and_minimized_survive_a_save_and_a_new_launch', read is not None and read.get('position') ==
              'BOTTOM' and read.get('minimized') is True, read or 'no ANGELO_RAIL_PREFS line in ' + log.name)
    parallel_setting.set_rail_minimized(False)
    parallel_setting.set_rail_position('LEFT')
