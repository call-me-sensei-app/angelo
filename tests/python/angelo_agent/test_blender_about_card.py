# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""About Angelo: the Angelo menu opens Angelo's own About card, drawn and clickable, in English and Arabic.

Checks the app menu routes to the card, the card's text (product and display version, "Based on Blender",
no branch), every link it offers (recorded, never opened), Close, and captures it in English, Arabic and at
a large interface scale. Local only.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-about-card-qa'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_LIBRARY_DIR=str(OUT / 'library'))
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
product_env.clear('DISABLE_DEFAULT_CHAT')
import angelo_identity  # noqa: E402
from angelo_agent import ui, director, card_host, update_card, about_card  # noqa: E402
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

ui._authentication.start = lambda *a, **k: None
ui.refresh_connected_models = lambda *a, **k: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / 'about-card.json'
opened = []
update_card.open_link = opened.append   # Links are recorded, never opened in a browser.
started = time.monotonic()
state = {'phase': 'startup', 'checks': {}, 'success': False, 'screenshots': [],
         'fixture': 'Real Angelo menu, card host and About card; links recorded, not opened.'}
STEPS = [('en', 'en_US', 1.0), ('ar', 'ar_EG', 1.0), ('en-large', 'en_US', 1.6)]


class Recorder:
    def __init__(self):
        self.items = []

    def operator(self, idname, text="", **kwargs):
        self.items.append(idname)
        return type("Props", (), {})()

    def __getattr__(self, name):
        return lambda *a, **k: None


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state['screenshots'].append(name)


def texts(plan):
    return [op[1] for op in plan.ops if op[0] in {"text", "mono"}]


def actions(plan):
    return [action for _, action, _ in plan.hits]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        print(state['traceback'], flush=True)
    else:
        state['phase'] = 'complete'
        state['success'] = all(state['checks'].values())
    view = bpy.context.preferences.view
    view.language, view.ui_scale = 'en_US', 1.0
    REPORT.write_text(json.dumps(state, indent=2))
    print('ANGELO_ABOUT_CARD_QA', json.dumps(state), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 90, 'About card QA timed out in phase ' + state['phase']
        if state['phase'] == 'startup':
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            menu = Recorder()
            bpy.types.TOPBAR_MT_blender.draw(type("Menu", (), {"layout": menu})(), bpy.context)
            state['checks']['angelo_menu_opens_angelos_about'] = 'angelo.show_about' in menu.items \
                and 'wm.splash_about' not in menu.items
            state['step'] = 0
            state['phase'] = 'open'
            return .2
        if state['phase'] == 'open':
            key, locale, scale = STEPS[state['step']]
            view = bpy.context.preferences.view
            view.use_translate_interface = True
            view.language, view.ui_scale = locale, scale
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.show_about()
            state['phase'] = 'shown'
            return .8
        if state['phase'] == 'shown':
            key = STEPS[state['step']][0]
            state['checks'][key + '_card_is_showing'] = card_host.is_active('about')
            plan = card_host._active.plan
            shown = texts(plan)
            state[key + '_texts'] = shown
            product = '%s %s' % (angelo_identity.PRODUCT_NAME, angelo_identity.APP_VERSION_DISPLAY)
            state['checks'][key + '_names_product_and_display_version'] = product in shown
            state['checks'][key + '_never_shows_a_branch'] = not any('Branch' in t or 'integration/' in t for t in shown)
            # The header is Angelo's brand art (the welcome banner), drawn under the product name.
            state['checks'][key + '_brand_banner_drawn'] = about_card.banner_ready() and any(
                op[0] == 'paint' for op in plan.ops)
            if key == 'en':
                state['checks']['en_says_based_on_blender'] = any(t.startswith('Based on Blender 5.2') for t in shown)
                state['checks']['offers_every_link_and_close'] = {
                    'whatsnew', 'credits', 'license', 'site', 'donate', 'blender', 'fund', 'close'} <= set(actions(plan))
                for action in ('credits', 'license', 'site', 'donate', 'blender', 'fund'):
                    card_host._active.card.act(action, None, card_host.Host(card_host._active))
                state['checks']['links_come_from_the_url_table'] = opened == [angelo_identity.url(k) for k in (
                    'credits', 'license', 'site', 'donate', 'blender_website', 'blender_fund')]
            x0, y0, x1, y1 = plan.card
            state[key + '_card_rect'] = [x0, y0, x1, y1]
            capture('about-card-%s.png' % key)
            card_host._active.card.act('close', None, card_host.Host(card_host._active))
            state['phase'] = 'closed'
            return .4
        if state['phase'] == 'closed':
            key = STEPS[state['step']][0]
            state['checks'][key + '_close_closes'] = not card_host.is_active('about')
            state['step'] += 1
            if state['step'] < len(STEPS):
                state['phase'] = 'open'
                return .3
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
