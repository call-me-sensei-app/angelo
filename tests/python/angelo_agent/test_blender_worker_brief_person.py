# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker's brief, as the person sees it: the part, then their own messages, in Director Mode (design A′).

Evidence: in 13d18cae0f9's Town benchmark of record (2026-10-28) every worker read only its lead's paraphrase of the
person's request; one lead's brief asked for details the person never asked for, and that worker's grade lost them
(``worker_request``). Each worker's first message now quotes the person's own messages after its part.

The lead, in Director Mode with the chat docked, holds two messages of the person's and a reply of its own. Its
``angelo_workers_start`` runs through Angelo's executor as an approved call runs and starts one real hosted worker,
whose chat the stand-in model plays (``hosted_rail_turns``: no provider or paid call). Checked in the real window,
with screenshots:

* the worker's tile appears on the rail while the lead's chat stays in the chat area;
* the worker's first message is its part, then both of the person's messages, never the lead's reply;
* a click on the worker's tile shows that message in the chat area (the product's own drawing), and the main tile
  brings the lead back.

GUI ``--factory-startup --enable-event-simulate`` only, disposable profile. Only with ``ANGELO_WORKER_CHAT_HOST`` unset
or ``1`` (the hosted variant); the lanes variant records ``skipped``."""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-worker-brief-person'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_ENABLE_PARALLEL_AGENTS='1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
from angelo_agent import (chat_launcher, connect_hero, console, live_view, live_view_draw,  # noqa: E402
                          session_rail_draw, worker_request, worker_routing, worker_sessions)
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from hosted_worker_chat import connect, hosted, install_stand_in  # noqa: E402

REQUEST = "A tea set on a wooden table: a kettle and a mug. Build it."
REPLY = "Glazed or plain mug?"   # The lead's: never in a worker's brief.
ANSWER = "Glazed, cobalt blue."
PART = {"name": "Kettle", "brief": "A stovetop kettle, 22 cm tall, brushed steel, black handle."}
HOSTED = hosted()
if HOSTED:
    install_stand_in(OUT / 'stand-in')
bpy.context.preferences.view.show_splash = False
chat_launcher.BLINK_EVERY = float('inf')
state = {'phase': 'boot', 'started': time.monotonic(), 'at': time.monotonic(), 'done': False}
report = {'checks': {}, 'paid_requests': 0, 'binary': bpy.app.binary_path, 'shots': [],
          'variant': 'hosted' if HOSTED else 'lanes', 'addon': worker_request.__file__}   # Which code ran.
window = lead = None


def check(name, condition, detail=None):
    report['checks'][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault('details', {})[name] = detail
    assert condition, name


def advance(phase):
    state['phase'], state['at'] = phase, time.monotonic()


def waited(seconds):
    return time.monotonic() - state['at'] >= seconds


def view_region():
    return next(r for r in live_view_draw.target(window).regions if r.type == 'WINDOW')


def screenshot(name):
    """The window as drawn now, rail included."""
    from angelo_agent import view_editor
    width, height, pixels = live_view.window_pixels(window)
    (OUT / name).write_bytes(view_editor.encode_png(pixels, width, height, bottom_up=True))
    report['shots'].append(name)


def click(x, y):
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window.event_simulate(type=kind, value=value, x=x, y=y)


def tile_point(session):
    """The window point of ``session``'s tile as the product last drew it, or None."""
    placed = session_rail_draw.last.get(window.as_pointer())
    tile = next((t for t in placed[1].tiles if t.session == session), None) if placed else None
    if tile is None:
        return None
    region = view_region()
    x, y, w, h = tile.rect
    return int(region.x + x + w / 2), int(region.y + y + h / 2)


def worker_chat():
    sessions = worker_sessions.workers_of(lead.state['id'])
    return ui._controllers.get(sessions[0]) if sessions else None


def first_message(chat):
    rows = [row for row in (chat.state.get('messages') or ()) if row.get('role') == 'user']
    return rows[0].get('text') or '' if rows else ''


def boot():
    """Director Mode with the chat docked, as a person works; True once ready."""
    global window
    if not qa_boot.started():   # Nothing docks or switches mode before the window is ready (test_native_qa_boot).
        return False
    window = bpy.context.window_manager.windows[0]
    if connect_hero.is_shown():
        if not state.get('hero_dismissed'):
            x, y = int(window.width / 2), int(window.height / 2)
            for key in ('TAB', 'RET'):
                window.event_simulate(type=key, value='PRESS', x=x, y=y)
                window.event_simulate(type=key, value='RELEASE', x=x, y=y)
            state['hero_dismissed'] = True
        return False
    connect_hero._dismissed = True
    from angelo_agent import director
    if not director.is_director(window):
        if 'director_asked' not in state:
            director.request_mode(window, 'DIRECTOR')
            state['director_asked'] = time.monotonic()
        return False
    if not any(console.is_chat(a) for a in window.screen.areas):
        if 'chat_asked' not in state:
            from angelo_agent import chat_toggle
            chat_toggle.show(window)
            state['chat_asked'] = time.monotonic()
        return False
    return True


def tick():
    global lead
    try:
        if time.monotonic() - state['started'] > 240:
            raise RuntimeError('Worker brief fixture timed out in ' + state['phase'])
        phase = state['phase']
        if phase == 'boot':
            if not HOSTED:
                report['skipped'] = 'ANGELO_WORKER_CHAT_HOST=0: this suite is about a hosted worker chat in the lead'
                return finish()
            if not boot():
                return .2 if not waited(40) else finish('Director Mode with the chat docked never opened')
            report['layout'] = 'director'
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'main.blend'), check_existing=False)
            lead = ui.get_controller()
            report['lead'] = lead.state['id']
            connect(lead)
            lead.state['options'] = dict(lead.state.get('options') or {}, approval='AUTO_BOUNDED')
            lead.state['messages'].extend([{'role': 'user', 'text': REQUEST}, {'role': 'assistant', 'text': REPLY},
                                           {'role': 'user', 'text': ANSWER}])
            executor = BlenderExecutor(window)
            executor.studio_context = lead
            arguments = {'parts': [PART]}
            executor.precondition('angelo_workers_start', arguments)
            report['start'] = executor.execute('angelo_workers_start', arguments)
            check('the_start_answers', report['start'].get('success') is True, report['start'])
            advance('briefed')
            return .2
        if phase == 'briefed':
            chat = worker_chat()
            text = first_message(chat) if chat is not None else ''
            if not text or tile_point(chat.state['id']) is None:
                return .2 if not waited(90) else finish('the worker never got its brief or its tile')
            report['brief'] = text
            quoted = worker_request.quoted(REQUEST) + worker_request.quoted(ANSWER)
            check('its_first_message_is_its_part_then_the_persons_own_messages',
                  text.startswith('Part: ' + PART['name']) and worker_request.HEADER + quoted in text
                  and text.index(PART['brief']) < text.index(worker_request.HEADER), text)
            check('the_leads_reply_is_never_quoted', REPLY not in text, text)
            check('the_leads_chat_stays_in_the_chat_area', ui._sessions.active == report['lead'], ui._sessions.active)
            screenshot('1-worker-tile-on-the-rail.png')
            click(*tile_point(chat.state['id']))
            advance('shown')
            return .5
        if phase == 'shown':
            chat = worker_chat()
            if ui._sessions.active != chat.state['id']:
                return .2 if not waited(10) else finish('the worker chat never showed after its tile was clicked')
            if not waited(1.5):   # Its first frame presented before the capture.
                return .2
            check('a_click_on_the_tile_shows_the_workers_chat', True)
            screenshot('2-worker-brief-in-the-chat.png')
            click(*tile_point(report['lead']))
            advance('back')
            return .5
        if phase == 'back':
            if ui._sessions.active != report['lead'] and not waited(10):
                return .2
            check('the_main_tile_brings_the_lead_back', ui._sessions.active == report['lead'])
            screenshot('3-back-on-the-lead.png')
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
    (OUT / 'worker-brief-person.json').write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_WORKER_BRIEF_PERSON', json.dumps({'success': report['success'], 'phase': state['phase']}),
          flush=True)
    if not report['success']:
        os._exit(1)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=.5)
