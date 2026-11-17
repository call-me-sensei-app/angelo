# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Click tickets come only from an aimed pointer press on a region Angelo registered (GUI; no provider or paid call).

doc/angelo/grant_operators.rst, "Authority in C". The Copy setup break-it re-review (2026-11-14, MUST-4) found that a
keymap item bound to Space, Return or a click, and a button any script drew, counted as the person's click. Here the
window manager's own input reaches ``_bpy._angelo_guard`` (``BPY_angelo_guard_note_input``), driven by simulated
events (this app was started with --enable-event-simulate; a shipped app has no such path):

* a press within the region's shield, or without a move into it, mints nothing;
* an aimed press mints one ticket, for that region's idname and digest only, taken once;
* Space and Return with the pointer on the region, and a keymap item bound to them, mint nothing;
* a press on two overlapping regions mints nothing;
* a region no longer drawn mints nothing.

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
SHIELD = 1.0
report = {'success': False, 'checks': {}}
state = {'phase': 'boot', 'started': time.monotonic(), 'window': None, 'drawn': 0.0}


def check(name, value):
    report['checks'][name] = bool(value)


def send(kind, value='NOTHING', at=INSIDE):
    state['window'].event_simulate(type=kind, value=value, x=at[0], y=at[1])


def draw(*regions):
    G.consent_regions(state['window'].as_pointer(), 'guard_tickets', tuple(regions))
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
            send('MOUSEMOVE', at=(10, 10))   # The pointer known, far from the region.
            draw(REGION)
            send('MOUSEMOVE', at=BELOW)
            send('MOUSEMOVE', at=INSIDE)     # Into it within its shield: no move that counts.
            send('LEFTMOUSE', 'PRESS')
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'within_shield'
            return .2
        if phase == 'within_shield':
            check('a_press_within_the_shield_mints_nothing', not G.take(IDNAME, DIGEST))
            state['phase'] = 'resting'
            return SHIELD + .2
        if phase == 'resting':
            send('LEFTMOUSE', 'PRESS')       # Resting inside since before its shield passed: never moved into it.
            send('LEFTMOUSE', 'RELEASE')
            state['phase'] = 'resting_checked'
            return .2
        if phase == 'resting_checked':
            check('a_pointer_resting_on_it_mints_nothing', not G.take(IDNAME, DIGEST))
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
            return finish()
    except Exception:  # noqa: BLE001 - the receipt says what broke.
        return finish(traceback.format_exc())
    return .1


bpy.app.timers.register(tick, first_interval=.5)
