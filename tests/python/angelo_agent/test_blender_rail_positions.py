# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel's three places and its minimized button, in the real window (GUI; no draw of the rail forced).

The maintainer (2026-10-25): the panel may stand down the left of the 3D view or centred across its top or bottom,
the person decides; it is a scrollable container when the workers do not fit; it minimizes to a round button at the
top right of the view, like Angelo's own, with a robot and the worker count. Specification: the rail-positions
SPEC.md (lanes L1-L4). Every rule is geometry (``rail_positions_checks``), never a coordinate of one screen: the same
rules hold in any window the run gives, at any interface scale (read from the preferences), on macOS and Windows.

Cases (``CASES``), each in Director Mode with its chat (entered as Angelo starts: ``angelo_default_layout.open_default``
builds it) and in Manual with its chat and the sidebar open, with the panel LEFT, TOP and BOTTOM:

* 1, 3 and 16 workers at 1x and 2x: the drawn plan's orientation and panel; the panel inside the view and clear of
  the headers, toolbar, sidebar, navigation gizmo column, Angelo in the corner and the shelf's Media Studio and
  Generation Flows pills; tiles inside it, in rail order, never overlapping; every session shown or counted hidden
  on its side; arrows and "+N more" only where tiles are hidden, inside the panel and clickable, the amber mark on
  the side a hidden approval waits; the wheel and a trackpad swipe page it; each "+N more" pages its way and its
  count follows; a click on a worker's tile shows its live view (the panel then clears the live view's label),
  keeps it in view and changes no job; a switch from elsewhere brings a hidden worker into view; Main's tile brings
  the document back; the header's minimize control leaves no rail and a round button at the top right, clear of
  the gizmo and the headers, the Angelo button's size, counting the workers and marked only while one waits for
  approval; a click on it restores the panel where it was.
* 16 workers at 1.5x and 3x, and in a short view (the 3D view split top and bottom) at 1x and 3x: the panel cannot fit
  at the person's scale (the integrator's Windows T4 failure: a paged rail at 3x in a 502 px view, panel at y = -8);
  the panel, its arrows and "+N more" lines and the minimized button stay inside the view and clear of the rest.
* 16 workers at 1x with the Asset Shelf open (expanded to its rows): the panel clears it and its pills.

Then the place and the minimized state are the add-on's preferences: set, marked to save, saved and read back by a
second, background Angelo from the run's own isolated profile. The interface scale and the panel are put back.
Captures of every state in the QA folder (``<case>-<n>-<state>.png``) for a vision review; ``rail-positions.json``.

Model-free: stand-in workers are chats bound as worker sessions with no worker process, provider or model (as
test_blender_rail_crowd); no account or network. GUI ``--factory-startup --enable-event-simulate``, Angelo's default
startup, disposable profile.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-rail-positions'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'))
os.environ.setdefault('ANGELO_DISABLE_INTRO', '1')
os.environ.setdefault('ANGELO_DISABLE_HERO', '1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import product_env  # noqa: E402
product_env.clear('DISABLE_DEFAULT_CHAT')   # Director with its chat, as Angelo starts.
import qa_boot  # noqa: E402
import rail_positions_checks as geometry  # noqa: E402
import rail_positions_fixture as fx  # noqa: E402
import rail_positions_run as run  # noqa: E402
import rail_positions_stages as stages  # noqa: E402
from rail_positions_run import S, check, pause, plan, until  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False   # Saved once, on purpose, by ``stages.persistence``.

run.OUT = OUT
REPORT = OUT / 'rail-positions.json'
POSITIONS = ('LEFT', 'TOP', 'BOTTOM')
CROWD = 16
CASES = ((1, (1.0, 2.0), 'full'), (3, (1.0, 2.0), 'full'), (CROWD, (1.0, 2.0), 'full'), (CROWD, (1.5, 3.0), 'fit'),
         (CROWD, (1.0, 3.0), 'short'), (CROWD, (1.0,), 'shelf'))
LAYOUTS = ('director', 'manual')
CAP = 1100.0   # Seconds for the whole run (x the runner's timeout scale): under the manifest's timeout.
state, report = run.state, run.report


def scrolling(tag):
    """A fitted paging layout can already show every session: scrolling then keeps every card in place."""
    placed = plan()
    if any(placed.hidden):
        yield from stages.scrolling(tag)
        return
    before = placed.first, run.tiles()
    check(tag + '__all_sessions_already_visible', run.tiles() == run.order(), [run.tiles(), run.order()])
    x, y = run.to_window(placed.panel)
    for kind in ('WHEELDOWNMOUSE', 'WHEELUPMOUSE', 'TRACKPADPAN'):
        S.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))
        dx = min(8 * placed.scale, placed.panel[2] / 4) if kind == 'TRACKPADPAN' else 0
        S.window.event_simulate(type=kind, value='NOTHING' if kind == 'TRACKPADPAN' else 'PRESS',
                                x=int(x - dx), y=int(y))
        yield pause(0.2)
        check(tag + '__all_visible_scroll_keeps_every_session',
              plan().first == before[0] and run.tiles() == before[1] and not any(plan().hidden))
    run.geometry_of(tag, 'all-visible-scroll')


_coarse_find_circle = fx.find_circle


def find_circle(window):
    """The usual hit scan, then a pixel scan only in a thin native-uncovered strip a coarse grid can miss.

    Kept in this GUI suite: shared QA helpers are part of the frozen benchmark harness digest.
    """
    found, frame = _coarse_find_circle(window)
    if found is not None:
        return found, frame
    from angelo_agent import rail_minimized
    area, region = fx.window_region(window)
    coarse = max(2, int(round(5 * bpy.context.preferences.system.ui_scale)))
    left, bottom, right, top = geometry.clear_box(geometry.view_obstacles(area.regions, region),
                                                (region.width, region.height))
    if min(right - left, top - bottom) > 2 * coarse:
        return None, None
    for frame, (ox, oy) in (('window', (region.x, region.y)), ('region', (0, 0))):
        points = [(x, y) for x in range(max(0, int(left)), min(region.width, int(right) + 1))
                  for y in range(max(0, int(bottom)), min(region.height, int(top) + 1))
                  if rail_minimized.hit(window.as_pointer(), ox + x, oy + y)]
        if not points:
            continue
        x0, x1 = min(p[0] for p in points) - coarse, max(p[0] for p in points) + coarse
        y0, y1 = min(p[1] for p in points) - coarse, max(p[1] for p in points) + coarse
        # Reconstruct the whole disc, including any part under a native region: the geometry oracle must see it.
        fine = [(x, y) for x in range(max(0, x0), min(region.width, x1 + 1))
                for y in range(max(0, y0), min(region.height, y1 + 1))
                if rail_minimized.hit(window.as_pointer(), ox + x, oy + y)]
        return geometry.circle_from_hits(fine), frame
    return None, None


fx.find_circle = find_circle


def integer_disc_room(box):
    """Can any contained positive disc reach a native-uncovered integer pixel? A corner alone cannot."""
    left, bottom, right, top = box
    x, y = math.ceil(left), math.ceil(bottom)
    return x < right and y < top and (x > left or y > bottom or x + 1 < right or y + 1 < top)


def cramped(tag):
    """A cramped button must have an actual integer hotspot, or be absent only when no usable disc can fit."""
    from angelo_agent import rail_minimized
    area, region = fx.window_region(S.window)
    native = geometry.view_obstacles(area.regions, region)
    box = geometry.clear_box(native, (region.width, region.height))
    pointer = S.window.as_pointer()
    if integer_disc_room(box):
        yield from stages.cramped(tag)
        placed = rail_minimized._places.get(pointer)
        points = []
        if placed is not None:
            cx, cy, _radius = placed[1:]
            points = [(x, y) for x in {math.floor(cx), math.ceil(cx)} for y in {math.floor(cy), math.ceil(cy)}
                      if box[0] <= x - region.x < box[2] and box[1] <= y - region.y < box[3]
                      and rail_minimized.hit(pointer, x, y)]
        check(tag + '__the_button_has_an_uncovered_integer_hotspot', bool(points), {'box': box, 'placed': placed})
        if points and placed[3] < 1 and not state.get('subpixel_recovery_checked'):
            state['subpixel_recovery_checked'] = True
            yield from pixel_recovery(tag, points[0])
    else:
        check(tag + '__no_integer_room_leaves_no_disc', pointer not in rail_minimized._places, {'box': box})
        check(tag + '__no_integer_room_leaves_no_hover_or_press',
              pointer not in rail_minimized._hover and pointer not in rail_minimized._pressed)
        run.shot(tag, 7, 'no-integer-room')
        if not state.get('zero_pixel_recovery_checked'):
            state['zero_pixel_recovery_checked'] = True
            yield from pixel_recovery(tag)


def pixel_recovery(tag, point=None):
    """Real integer input, viewport expansion and guaranteed restoration, including failed probes."""
    from angelo_agent import rail_minimized, parallel_sessions
    pointer = S.window.as_pointer()
    active = parallel_sessions._active()
    if S.window.screen.show_fullscreen:
        raise RuntimeError('pixel probe requires a normal screen, not Focus Mode or a maximized area')
    original_screen = S.window.screen.as_pointer()
    def layout():
        return tuple((a.as_pointer(), a.type, a.x, a.y, a.width, a.height) for a in S.window.screen.areas)
    original_layout, press_sent = layout(), False
    try:
        if point is not None:
            x, y = point
            # Simulated MOUSEMOVE has no delta: intermediate motion goes through the real input path.
            S.window.event_simulate(type='INBETWEEN_MOUSEMOVE', value='NOTHING', x=x, y=y)
            ok = yield until(lambda: pointer in rail_minimized._hover)
            check(tag + '__integer_mouse_pixel_hovers_the_button', ok, point)
            S.window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
            press_sent = True
            ok = yield until(lambda: pointer in rail_minimized._pressed)
            check(tag + '__integer_mouse_pixel_presses_the_button', ok, point)
        area, region = fx.window_region(S.window)
        with bpy.context.temp_override(window=S.window, area=area, region=region):
            if bpy.ops.screen.screen_full_area(use_hide_panels=True) != {'FINISHED'}:
                raise RuntimeError('pixel probe could not enter Focus Mode')
        ok = yield until(lambda: plan() is not None and bool(plan().tiles) and pointer not in rail_minimized._cramped, 8)
        check(tag + '__a_larger_view_restores_the_panel', ok)
        check(tag + '__the_returned_panel_has_no_circle_hover_or_press',
              pointer not in rail_minimized._hover and pointer not in rail_minimized._pressed)
        if point is not None:
            S.window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=point[0], y=point[1])
            press_sent = False
            yield pause(0.2)
            check(tag + '__a_stale_circle_release_switches_no_session', parallel_sessions._active() == active)
        run.shot(tag, 10, 'pixel-recovered')
    finally:
        had_error, cleanup_errors = sys.exc_info()[0] is not None, []
        try:
            if point is not None and press_sent:
                S.window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=point[0], y=point[1])
        except Exception:
            cleanup_errors.append(traceback.format_exc())
        try:
            if S.window.screen.show_fullscreen:
                area, region = fx.window_region(S.window)   # The native area changed: resolve it afresh.
                with bpy.context.temp_override(window=S.window, area=area, region=region):
                    if bpy.ops.screen.screen_full_area(use_hide_panels=True) != {'FINISHED'}:
                        raise RuntimeError('pixel probe could not leave Focus Mode')
            if S.window.screen.show_fullscreen or S.window.screen.as_pointer() != original_screen:
                raise RuntimeError('pixel probe did not restore its original normal screen')
            fx.redraw(S.window)
        except Exception:
            cleanup_errors.append(traceback.format_exc())
        if cleanup_errors:
            check(tag + '__pixel_probe_cleanup_succeeded', False, cleanup_errors)
            if not had_error:
                raise RuntimeError('pixel probe cleanup failed: ' + '\n'.join(cleanup_errors))
    ok = yield until(lambda: layout() == original_layout, 8)
    check(tag + '__the_exact_original_layout_returns', ok)
    if not ok:
        raise RuntimeError('pixel probe did not restore its original area layout')
    ok = yield until(lambda: pointer in rail_minimized._cramped, 8)
    check(tag + '__the_original_cramped_layout_returns', ok)



def tick():
    """Runs the scenario: a generator yielding ``until``/``pause`` waits; never blocks the app's loop."""
    try:
        now = time.monotonic()
        if now - state['started'] > CAP * run.SCALE:
            return finish('cap reached in %s' % state['phase'])
        if state['run'] is None:
            state['run'] = scenario()
        value, pending = None, state['pending']
        if pending is not None:
            kind, condition, deadline = pending
            if kind == 'pause':
                if now < deadline:
                    return .05
                value = True
            else:
                try:
                    value = bool(condition())
                except Exception:   # noqa: BLE001 - a wait whose reading fails waits on, then fails its check.
                    value = False
                    report['readings']['last_wait_error'] = [state['phase'], traceback.format_exc(limit=3)]
                if not value and now < deadline:
                    return .05
            state['pending'] = None
        kind, condition, seconds = state['run'].send(value)
        state['pending'] = (kind, condition, now + seconds)
        return .02
    except StopIteration:
        return finish()
    except Exception:   # noqa: BLE001 - recorded, the run fails.
        return finish(traceback.format_exc())


def scenario():
    from angelo_agent import connect_hero, director, parallel_access
    ok = yield until(lambda: qa_boot.started() and ui.main_window() is not None
                     and director.is_director(ui.main_window()) and fx.chat_area(ui.main_window()) is not None, 90)
    if not ok:
        raise RuntimeError('Angelo never settled in Director with its chat: %s' % qa_boot.waiting_on())
    S.window = ui.main_window()
    report['window'] = [S.window.width, S.window.height]
    connect_hero._dismissed = True   # No account here: the view shows the rail, not the showcase.
    check('parallel_edit_is_on_here', parallel_access.enabled(), 'needs this machine above the bar')
    if not parallel_access.enabled():
        return
    S.lead, S.base_scale = ui.get_controller(), bpy.context.preferences.view.ui_scale
    S.probe = fx.Probe(S.window)
    report['readings']['circle_module'] = S.probe.circle_error or 'present'
    for layout in LAYOUTS:
        yield from enter(layout)
        for count, scales, kind in CASES:
            if len(S.workers) > count:
                fx.unbind(S.window, S.workers, count)
            elif len(S.workers) < count:
                fx.bind(S.lead, S.workers, count - len(S.workers))
            split = yield from arrange(layout, kind, True)
            for scale in scales:
                yield from set_scale(scale)
                for position in POSITIONS:
                    yield from run_case(layout, scale, position, kind)
            yield from arrange(layout, kind, False, split)
    yield from set_scale(S.base_scale)
    yield from stages.persistence()


def arrange(layout, kind, before, split=None):
    """The shelf open, or the view split short, for those cases; put back after them."""
    if kind == 'shelf':
        report['readings'].setdefault('shelf', {})[layout] = fx.shelf(S.window, before) or ('open' if before else 'closed')
    elif kind == 'short' and before:
        split, error = fx.split_view(S.window)
        check(layout + '__the_view_splits_short', split is not None, error)
    elif kind == 'short' and split is not None:
        check(layout + '__the_split_view_joins_again', fx.join_view(S.window, split))
    yield pause(0.6 if kind in ('shelf', 'short') else 0)
    return split


def enter(layout):
    from angelo_agent import director
    state['phase'] = 'enter ' + layout
    want = layout == 'director'
    yield until(lambda: qa_boot.started(), 40)   # Never a mode switch while the start-up still moves the layout.
    if director.is_director(S.window) != want:
        director.request_mode(S.window, 'DIRECTOR' if want else 'MANUAL')
    ok = yield until(lambda: qa_boot.started() and director.is_director(S.window) == want
                     and fx.chat_area(S.window) is not None and fx.window_region(S.window)[1] is not None, 40)
    check(layout + '__entered_with_its_chat', ok, qa_boot.waiting_on())
    if not want:
        area, _region = fx.window_region(S.window)
        area.spaces.active.show_region_ui = True   # The sidebar open: the panel and the button clear it too.
    yield pause(1.0)


def set_scale(value):
    """The person's interface scale (Preferences ▸ Interface ▸ Resolution Scale); sizes are read back from the drawn
    plan and the regions, never assumed (the panel may draw smaller where even its least does not fit)."""
    state['phase'] = 'scale %g' % value
    mark = S.probe.mark()
    bpy.context.preferences.view.ui_scale = value
    fx.redraw(S.window)
    ok = yield until(lambda: run.drawn_since(mark) is not None or any(f.circle for f in S.probe.since(mark)), 8)
    check('scale_%s_drawn' % ('%g' % value).replace('.', '_'), ok)   # The panel, or its round button (cramped).
    yield pause(0.8)   # Regions settle over a few draws after a scale change.


def run_case(layout, scale, position, kind):
    tag = '%s-%sx-%dw-%s%s' % (layout, ('%g' % scale).replace('.', '_'), len(S.workers), position.lower(),
                               '' if kind in ('full', 'fit') else '-' + kind)
    state['phase'] = tag
    report['cases'].append(tag)
    waits = [None, None]
    try:
        shown = yield from show(tag, position)
        if shown == 'cramped':
            yield from cramped(tag)
            return
        if not shown:
            return
        placed = run.geometry_of(tag, 'placed')
        run.shot(tag, 1, 'placed')
        if len(S.workers) == CROWD:
            check(tag + '__the_crowd_pages_or_fits', placed.paged or len(placed.tiles) == len(run.order()),
                  [len(placed.tiles), len(run.order()), placed.panel])   # A tall view at 1x holds 17 compact tiles.
        if kind == 'shelf':
            return
        if kind in ('fit', 'short'):
            yield from stages.minimized(tag, waits, by_click=False)
            return
        if placed.paged:
            yield from scrolling(tag)
            yield from stages.more_lines(tag)
        waits[:] = yield from stages.approval(tag)
        yield from stages.tile_click(tag, waits[0])
        yield from stages.minimized(tag, waits, by_click=True)
    except Exception:   # noqa: BLE001 - one case's failure never hides the next case.
        check(tag + '__ran_without_raising', False, traceback.format_exc())
    finally:
        if waits[0] is not None:
            fx.clear_approval(*waits)
        reset()


def reset():
    from angelo_agent import parallel_setting, session_rail_input
    parallel_setting.set_rail_minimized(False)
    if S.lead.state['id'] != getattr(ui._sessions, 'active', None):
        session_rail_input.switch(S.lead.state['id'], S.window)
    fx.redraw(S.window)


def show(tag, position):
    """The panel in ``position`` (the preference, as Preferences sets it), drawn there by the product."""
    from angelo_agent import parallel_setting
    reset()
    mark = S.probe.mark()
    parallel_setting.set_rail_position(position)
    check(tag + '__position_is_stored', parallel_setting.rail_position() == position, parallel_setting.rail_position())
    fx.redraw(S.window)   # Set from code here, not from Preferences: the view is drawn again as after a change there.
    wanted = geometry.EXPECTED[position]
    cramped = lambda: any(f.circle and f.circle.get('cramped') for f in S.probe.since(mark))   # noqa: E731
    ok = yield until(lambda: cramped() or run.drawn_since(
        mark, lambda p: getattr(p, 'orientation', None) == wanted) is not None)
    if ok and cramped():   # No readable panel fits this view: its round button stands for it (stages.cramped).
        return 'cramped'
    check(tag + '__drawn_in_its_place', ok, getattr(plan(), 'orientation', 'Layout has no orientation'))
    yield pause(0.3)   # The text inset and hover settle (session_rail.steady_inset).
    return plan() is not None


def finish(error=None):
    if state['done']:
        return None
    state['done'] = True
    try:
        if S.base_scale is not None:
            bpy.context.preferences.view.ui_scale = S.base_scale
        if S.probe is not None:
            S.probe.release()
    except Exception:   # noqa: BLE001 - the report is written whatever happens here.
        report['readings']['restore_error'] = traceback.format_exc()
    report.update(error=error, phase='complete' if error is None else state['phase'],
                  success=error is None and bool(report['checks']) and all(report['checks'].values()),
                  failed=sorted(name for name, passed in report['checks'].items() if not passed),
                  elapsed_seconds=round(time.monotonic() - state['started'], 1))
    REPORT.write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_RAIL_POSITIONS', json.dumps({'success': report['success'], 'phase': report['phase'],
                                               'failed': len(report['failed'])}), flush=True)
    if not report['success']:
        os._exit(1)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=.5)
