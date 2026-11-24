# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A consent control under the 3D view's open N panel takes none of the person's input there (GUI; the review of
f6b01a9..da9ba66, SHOULD 1; the maintainer: "Fix properly in 2.1.0").

doc/angelo/grant_operators.rst, "Authority in C". A sidebar, toolbar, transparent header, HUD or asset shelf drawn over
the region a card is drawn in took no part in the grant guard: a move over the open N panel aimed at the card's control
beneath, and a press there minted its ticket. Here a control is registered in the 3D view's main region as a card's
draw registers it (``consent_regions`` with that region), and the window manager's own input (simulated events, this
app was started with --enable-event-simulate) goes through the real routing (``wm_event_pointer_region``) to C:

* the person aims at the control, then opens the N panel over it without moving: a press there is the sidebar's and
  mints nothing; they hide it, still without moving: the control it covered starts again (a press mints nothing), and
  their move onto it and press mint;
* with the N panel open, moves and a press on its panels mint nothing for the control beneath, and under its
  transparent part (below its panels; Blender gives the 3D view the input) an aimed press mints.

Writes ``guard-overlap.json``; exits non-zero on any failure.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
from pathlib import Path
import tempfile
import time
import traceback

import bpy
import _bpy

assert '--enable-event-simulate' in sys.argv and not bpy.app.background
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-guard-overlap'))
OUT.mkdir(parents=True, exist_ok=True)
G, J = _bpy._angelo_guard, _bpy._angelo_jobs
IDNAME = 'angelo.guard_overlap_test'
SHIELD = 1.0
report = {'success': False, 'checks': {}, 'details': {}}
state = {'phase': 'boot', 'started': time.monotonic()}


def check(name, value, detail=None):
    report['checks'][name] = bool(value)
    if detail is not None and not value:
        report['details'][name] = detail


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    """The window's largest 3D view (the default startup's), with its main region and its sidebar."""
    areas = [a for a in window().screen.areas if a.type == 'VIEW_3D']
    if not areas:
        raise RuntimeError('no 3D view in the startup layout')
    area = max(areas, key=lambda a: a.width * a.height)
    regions = {r.type: r for r in area.regions}
    return area, regions['WINDOW'], regions.get('UI')


def send(kind, at, value='NOTHING'):
    window().event_simulate(type=kind, value=value, x=int(at[0]), y=int(at[1]))


def routed(at):
    return J.pointer_region(window().as_pointer(), int(at[0]), int(at[1]))


def register(controls):
    """``controls``: (name, (x, y)) centred 40 x 20 boxes in window pixels, drawn in the 3D view's main region."""
    area, main, _ui = view3d()
    G.consent_regions(window().as_pointer(), 'guard_overlap',
                      tuple((int(x) - 20, int(y) - 10, 40, 20, IDNAME, name, True) for name, (x, y) in controls),
                      main.as_pointer())
    area.tag_redraw()


def aim(at):
    send('MOUSEMOVE', (at[0], at[1] - 30))
    send('MOUSEMOVE', at)


def press(at):
    send('LEFTMOUSE', at, 'PRESS')
    send('LEFTMOUSE', at, 'RELEASE')


def finish(error=None):
    report.update(success=error is None and bool(report['checks']) and all(report['checks'].values()),
                  error=error, phase=state['phase'])
    (OUT / 'guard-overlap.json').write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_GUARD_OVERLAP', json.dumps({'success': report['success'], 'phase': state['phase'],
          'failed': [k for k, v in report['checks'].items() if not v]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def sidebar(shown):
    area, _main, _ui = view3d()
    area.spaces.active.show_region_ui = shown   # The person's N, the pointer at rest.
    area.tag_redraw()


def tick():
    try:
        if time.monotonic() - state['started'] > 90:
            raise RuntimeError('guard overlap fixture timed out in ' + state['phase'])
        phase = state['phase']
        if phase == 'boot':
            bpy.context.preferences.system.use_region_overlap = True
            sidebar(True)   # Opened once to find where its panels are.
            state['phase'] = 'measure'
            return .5
        if phase == 'measure':
            area, main, ui = view3d()
            if ui is None or ui.width < 100:
                raise RuntimeError('the N panel did not open: %r' % (ui and (ui.width, ui.height),))
            x = ui.x + ui.width // 2
            state['under_panels'] = (x, ui.y + ui.height - 60)   # On its first panel.
            state['transparent'] = (x, ui.y + 40)                # Below its panels.
            ids = {'main': main.as_pointer(), 'ui': ui.as_pointer()}
            routing = {name: routed(state[name]) for name in ('under_panels', 'transparent')}
            report['details'].update(regions=ids, routing=routing)
            check('blender_gives_the_sidebar_the_input_over_its_panels', routing['under_panels'] == ids['ui'], routing)
            check('blender_gives_the_3d_view_the_input_below_them', routing['transparent'] == ids['main'], routing)
            sidebar(False)
            register([('under', state['under_panels']), ('through', state['transparent'])])
            send('MOUSEMOVE', (main.x + 40, main.y + 40))   # The pointer known, away from both.
            state['phase'] = 'armed'
            return SHIELD + .3
        if phase == 'armed':   # The N panel closed: the person aims at the control and rests on it.
            aim(state['under_panels'])
            sidebar(True)      # Then opens the N panel over it, without moving.
            state['phase'] = 'opened'
            return .5
        if phase == 'opened':
            press(state['under_panels'])
            check('a_press_on_the_open_n_panel_mints_nothing_for_the_control_beneath', not G.take(IDNAME, 'under'))
            sidebar(False)     # And hides it again, still without moving.
            state['phase'] = 'hidden'
            return .5
        if phase == 'hidden':
            _area, main, _ui = view3d()
            routing = routed(state['under_panels'])
            check('hidden_blender_gives_the_3d_view_the_input', routing == main.as_pointer(), routing)
            press(state['under_panels'])
            check('the_n_panel_hidden_what_it_covered_starts_again', not G.take(IDNAME, 'under'))
            state['phase'] = 'rearm'
            return SHIELD + .3
        if phase == 'rearm':
            aim(state['under_panels'])
            state['phase'] = 'repress'
            return .4
        if phase == 'repress':
            press(state['under_panels'])
            check('the_persons_move_and_press_after_it_hides_mint', G.take(IDNAME, 'under'))
            sidebar(True)
            state['phase'] = 'through'
            return .5
        if phase == 'through':   # The N panel open: its panels take the hand, its transparent part passes it on.
            aim(state['under_panels'])
            press(state['under_panels'])
            check('moves_and_a_press_on_its_panels_mint_nothing', not G.take(IDNAME, 'under'))
            aim(state['transparent'])
            state['phase'] = 'through_press'
            return SHIELD + .3
        if phase == 'through_press':
            press(state['transparent'])
            check('under_its_transparent_part_an_aimed_press_mints', G.take(IDNAME, 'through'))
            return finish()
    except Exception:  # noqa: BLE001 - the receipt says what broke.
        return finish(traceback.format_exc())
    return .1


bpy.app.timers.register(tick, first_interval=1.0)
