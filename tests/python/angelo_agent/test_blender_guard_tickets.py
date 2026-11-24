# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Click tickets come only from an aimed pointer press on a region Angelo registered (GUI; no provider or paid call).

doc/angelo/grant_operators.rst, "Authority in C". The Copy setup break-it re-review (2026-11-14, MUST-4) found that a
keymap item bound to Space, Return or a click, and a button any script drew, counted as the person's click. Here the
window manager's own input reaches ``_bpy._angelo_guard`` (``BPY_angelo_guard_note_input``), driven by simulated
events (this app was started with --enable-event-simulate; a shipped app has no such path):

* a pointer resting where the region appeared (never moved into it) mints nothing, within its shield or after it,
  and C's own window record shows no focus change or window-forward click meanwhile (S1: presses alone never grant
  a control that appeared under a resting pointer);
* a move into it during its shield aims, so a press within the shield mints nothing and one after it mints, once
  (2.0.1, eeb1f7307f1: "one deliberate click approves"; gtest AMoveOntoItDuringItsShieldThenOnePressAfterItMints);
* an aimed press mints one ticket, for that region's idname and digest only, taken once;
* Space and Return with the pointer on the region, and a keymap item bound to them, mint nothing;
* a press on two overlapping regions mints nothing;
* a region no longer drawn mints nothing;
* the press that brings the window forward (a press while it is in the background, as Windows delivers one) aims
  at the control under a resting pointer and mints nothing; the person's next press there, a shield later, mints.
  The activation's own 0.5 s window (GHOST's activation, which no simulated event makes) is pinned by the gtests
  TheClickThatBringsTheWindowForwardAimsSoTheNextPressMints and ALatePressIsNotTheWindowForwardClick.

Writes ``guard-tickets.json``; exits non-zero on any failure.
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

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-guard-tickets'))
OUT.mkdir(parents=True, exist_ok=True)
G = _bpy._angelo_guard
IDNAME, DIGEST = 'angelo.guard_ticket_test', 'd1'
REGION = (100, 100, 80, 24, IDNAME, DIGEST, True)
INSIDE, BELOW = (140, 112), (140, 90)
MOVED_DIGEST = 'd3'   # A second control, above the first: moved into during its shield.
MOVED_REGION = (100, 140, 80, 24, IDNAME, MOVED_DIGEST, True)
INSIDE_MOVED, BETWEEN = (140, 152), (140, 132)
SHIELD = 1.0
report = {'success': False, 'checks': {}}
state = {'phase': 'boot', 'started': time.monotonic(), 'window': None, 'drawn': 0.0, 'offset': (0, 0)}


def check(name, value):
    report['checks'][name] = bool(value)


def send(kind, value='NOTHING', at=INSIDE):
    dx, dy = state['offset']
    state['window'].event_simulate(type=kind, value=value, x=at[0] + dx, y=at[1] + dy)


def window_state():
    """C's own record of the window (focus epoch, returning, whether the last press brought it forward)."""
    return tuple(G.window_state(state['window'].as_pointer()))


def draw(*regions):
    """Register ``regions`` as a surface's draw does, then redraw the window: C counts a region's shield and arm from
    the first frame with it on screen (``note_presented``, wm_draw), and a region registered from a timer with nothing
    to redraw never reaches the screen, so it never arms."""
    window, (dx, dy) = state['window'].as_pointer(), state['offset']
    regions = tuple((x + dx, y + dy, *rest) for x, y, *rest in regions)
    # In the region Blender routes their points to (the review of f6b01a9..da9ba66, SHOULD 1): the layout sits in
    # the window's largest main region (``offset``), so every region shares it.
    owners = {_bpy._angelo_jobs.pointer_region(window, x + w // 2, y + h // 2) for x, y, w, h, *_ in regions or
              ((REGION[0] + dx, REGION[1] + dy, REGION[2], REGION[3]),)}
    if len(owners) != 1 or None in owners:
        raise RuntimeError('the regions are not all in one Blender region: %r' % (owners,))
    G.consent_regions(window, 'guard_tickets', regions, owners.pop())
    for area in state['window'].screen.areas:
        area.tag_redraw()
    state['drawn'] = time.monotonic()


def finish(error=None):
    report.update(success=error is None and bool(report['checks']) and all(report['checks'].values()),
                  error=error, phase=state['phase'])
    (OUT / 'guard-tickets.json').write_text(json.dumps(report, indent=2))
    print('ANGELO_GUARD_TICKETS', json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def tick():
    try:
        if time.monotonic() - state['started'] > 60:
            raise RuntimeError('guard tickets fixture timed out')
        phase = state['phase']
        if phase == 'boot':
            state['window'] = bpy.context.window_manager.windows[0]
            main = max((r for a in state['window'].screen.areas for r in a.regions if r.type == 'WINDOW'),
                       key=lambda r: r.width * r.height)   # The layout (x 100..200, y 90..164) sits inside it.
            state['offset'] = (main.x, main.y)
            send('MOUSEMOVE', at=(10, 10))   # The pointer known, far from the region.
            send('MOUSEMOVE', at=INSIDE)     # Resting where the region is about to appear.
            draw(REGION)                     # It appears under the resting pointer.
            state['rest_state'] = window_state()
            send('LEFTMOUSE', 'PRESS')       # Within its shield, never moved into it.
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'resting_within_shield'
            return .2
        if phase == 'resting_within_shield':
            check('a_resting_press_within_the_shield_mints_nothing', not G.take(IDNAME, DIGEST))
            state['phase'] = 'resting'
            return SHIELD + .2
        if phase == 'resting':
            send('LEFTMOUSE', 'PRESS')       # Its shield long passed, the pointer never moved into it.
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'resting_checked'
            return .2
        if phase == 'resting_checked':
            check('a_pointer_resting_on_it_mints_nothing', not G.take(IDNAME, DIGEST))
            rested = window_state()          # Same focus epoch, nothing returning, no window-forward click.
            report.setdefault('details', {})['window_state_while_resting'] = [state['rest_state'], rested]
            check('nothing_brought_the_window_forward_while_it_rested',
                  rested[0] == state['rest_state'][0] and not rested[1] and not rested[2])
            draw(REGION, MOVED_REGION)       # The first stays where it was; the second appears above it.
            send('MOUSEMOVE', at=BETWEEN)
            send('MOUSEMOVE', at=INSIDE_MOVED)   # Into the second within its shield: an aim.
            send('LEFTMOUSE', 'PRESS', at=INSIDE_MOVED)
            send('LEFTMOUSE', 'RELEASE', at=INSIDE_MOVED)
            state['phase'] = 'moved_within_shield'
            return .2
        if phase == 'moved_within_shield':
            check('a_press_within_the_shield_mints_nothing', not G.take(IDNAME, MOVED_DIGEST))
            state['phase'] = 'moved_after_shield'
            return SHIELD + .2
        if phase == 'moved_after_shield':
            send('LEFTMOUSE', 'PRESS', at=INSIDE_MOVED)   # No move since: the move in during its shield aims.
            send('LEFTMOUSE', 'RELEASE', at=INSIDE_MOVED)
            state['phase'] = 'moved_checked'
            return .2
        if phase == 'moved_checked':
            check('a_move_in_during_its_shield_then_a_press_after_it_mints', G.take(IDNAME, MOVED_DIGEST))
            check('that_ticket_is_taken_once', not G.take(IDNAME, MOVED_DIGEST))
            send('MOUSEMOVE', at=BELOW)
            send('MOUSEMOVE', at=INSIDE)     # Aimed: out, then into it, its shield long passed.
            send('LEFTMOUSE', 'PRESS')
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'aimed'
            return .2
        if phase == 'aimed':
            check('another_digest_takes_nothing', not G.take(IDNAME, 'other'))
            check('another_idname_takes_nothing', not G.take('angelo.external_app_connect', DIGEST))
            check('an_aimed_press_mints_one_ticket', G.take(IDNAME, DIGEST))
            check('a_ticket_is_taken_once', not G.take(IDNAME, DIGEST))
            keymap = bpy.context.window_manager.keyconfigs.addon.keymaps.new('Window')
            state['keymap_items'] = [keymap.keymap_items.new('wm.context_toggle', key, 'PRESS')
                                     for key in ('SPACE', 'RET')]
            for key in ('SPACE', 'RET', 'NUMPAD_ENTER'):
                send(key, 'PRESS')
                send(key, 'RELEASE')
            state['phase'] = 'keys'
            return .3
        if phase == 'keys':
            check('keys_and_keymap_items_mint_nothing', not G.take(IDNAME, DIGEST))
            keymap = bpy.context.window_manager.keyconfigs.addon.keymaps['Window']
            for item in state.pop('keymap_items'):
                keymap.keymap_items.remove(item)
            draw(REGION, (120, 104, 80, 24, 'angelo.other_grant', 'd2', True))   # Overlapping it.
            state['phase'] = 'overlap_wait'
            return SHIELD + .2
        if phase == 'overlap_wait':
            send('MOUSEMOVE', at=BELOW)
            send('MOUSEMOVE', at=(150, 112))
            send('LEFTMOUSE', 'PRESS')
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'overlap'
            return .2
        if phase == 'overlap':
            check('a_press_on_two_regions_mints_nothing',
                  not G.take(IDNAME, DIGEST) and not G.take('angelo.other_grant', 'd2'))
            draw()                           # Nothing drawn now.
            send('MOUSEMOVE', at=BELOW)
            send('MOUSEMOVE', at=INSIDE)
            send('LEFTMOUSE', 'PRESS')
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'gone'
            return .2
        if phase == 'gone':
            check('a_region_no_longer_drawn_mints_nothing', not G.take(IDNAME, DIGEST))
            draw(REGION)                     # Again under the resting pointer (it moved in while nothing was drawn).
            state['phase'] = 'background'
            return SHIELD + .2
        if phase == 'background':
            send('WINDOW_DEACTIVATE')        # The person went to another app.
            send('LEFTMOUSE', 'PRESS')       # Their click on the card brings the window forward.
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'forward_checked'
            return .2
        if phase == 'forward_checked':
            forward = window_state()
            report.setdefault('details', {})['window_state_after_the_window_forward_click'] = forward
            check('the_window_forward_click_was_noted_as_one', forward[2])
            check('the_window_forward_click_mints_nothing', not G.take(IDNAME, DIGEST))
            state['phase'] = 'after_forward'
            return SHIELD + .2
        if phase == 'after_forward':
            send('LEFTMOUSE', 'PRESS')       # The person's next press, no move: the forward click aimed.
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'after_forward_checked'
            return .2
        if phase == 'after_forward_checked':
            check('the_next_press_after_the_window_forward_click_mints', G.take(IDNAME, DIGEST))
            return finish()
    except Exception:  # noqa: BLE001 - the receipt says what broke.
        return finish(traceback.format_exc())
    return .1


bpy.app.timers.register(tick, first_interval=.5)
