# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Director hides the status bar, so its reports and running work show in Director's top bar.

A real operator error (Accept on a preview that no longer exists) is raised through the event system,
exactly as a click or shortcut would, and must appear in the top bar's report banner; the agent's
background-job item (stand-in job state, real drawing path) must appear beside it. Captured in English
and Arabic. Local only: no provider, network or paid work.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-director-reports-qa'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_LIBRARY_DIR=str(OUT / 'library'))
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
product_env.clear('DISABLE_DEFAULT_CHAT')
from angelo_agent import ui, director, job_ui  # noqa: E402

ui._authentication.start = lambda *a, **k: None
ui.refresh_connected_models = lambda *a, **k: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / 'director-reports.json'
KEY = 'F19'   # A key nothing else binds: the QA shortcut runs the real Accept operator through the event system.
started = time.monotonic()
state = {'phase': 'startup', 'checks': {}, 'success': False, 'screenshots': [],
         'fixture': 'Real Director layout and a real operator error raised by a key event; stand-in job state.'}
drawn = []


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state['screenshots'].append(name)


def error_pixels(name):
    """Reddish pixels in the top bar strip of a capture: the error banner's icon background."""
    image = bpy.data.images.load(str(OUT / name))
    try:
        width, height = image.size
        strip = int(28 * bpy.context.preferences.system.pixel_size * bpy.context.preferences.view.ui_scale)
        pixels = list(image.pixels[(height - strip) * width * 4:])
    finally:
        bpy.data.images.remove(image)
    return sum(1 for i in range(0, len(pixels), 4)
               if pixels[i] > 0.55 and pixels[i + 1] < 0.35 and pixels[i + 2] < 0.35)


def press():
    target = window()
    x, y = target.width // 3, target.height // 2   # Over the scene preview, not the chat.
    target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    target.event_simulate(type=KEY, value='PRESS', x=x, y=y)
    target.event_simulate(type=KEY, value='RELEASE', x=x, y=y)


def fake_job_item(manager, now):
    drawn.append(now)
    from angelo_agent.ui_text import iface_
    return (iface_("Rendering frames") + " · 40%", 'RENDER_ANIMATION', "cancel", "qa-job")


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        print(state['traceback'], flush=True)
    else:
        state['phase'] = 'complete'
        state['success'] = all(state['checks'].values())
    REPORT.write_text(json.dumps(state, indent=2))
    print('ANGELO_DIRECTOR_REPORTS_QA', json.dumps(state), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 90, 'Director reports QA timed out in phase ' + state['phase']
        if state['phase'] == 'startup':
            if not director.is_director(window()) or director._timer is not None:
                return .2
            capture('director-before-report.png')
            state['red_pixels_before'] = error_pixels('director-before-report.png')
            state['checks']['status_bar_hidden_in_director'] = not window().screen.show_statusbar
            keymap = bpy.context.window_manager.keyconfigs.addon.keymaps.new(name='Window', space_type='EMPTY')
            keymap.keymap_items.new('angelo.accept_native_preview', KEY, 'PRESS')
            job_ui.status_item = fake_job_item
            job_ui.redraw()
            state['phase'] = 'en'
            return .5
        if state['phase'] in {'en', 'ar'}:
            press()
            state['phase'] += '-shown'
            return .6   # The banner animates in, then holds for several seconds.
        if state['phase'] in {'en-shown', 'ar-shown'}:
            locale = state['phase'][:2]
            state['checks'][locale + '_job_item_drawn_in_director_top_bar'] = bool(drawn)
            drawn.clear()
            name = 'director-report-%s.png' % locale
            capture(name)
            state['red_pixels_' + locale] = red = error_pixels(name)
            state['checks'][locale + '_error_banner_shown_in_top_bar'] = red > 40 + 2 * state['red_pixels_before']
            if locale == 'en':
                view = bpy.context.preferences.view
                view.use_translate_interface = True
                view.language = 'ar_EG'
                state['phase'] = 'ar'
                return 1.0
            bpy.context.preferences.view.language = 'en_US'
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
