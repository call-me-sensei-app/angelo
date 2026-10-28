# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker's script approval in Auto, shown in the lead's window and answered there, in Director Mode (design A′).

Evidence: 78e17c6e1a1's Town benchmark of record (2026-10-28). Every worker script card was refused "This card is no
longer current": the tester answers a worker's card from the lead while the lead's chat is shown, as the lanes allowed,
and A′ sent an Approve only for a card a chat area had drawn. Each worker then waited until its approval expired or
its model gave up, and the lead waited on it (up to 636 s). The other GUI suites approve through a seam that records
the drawing by hand (``hosted_worker_chat.draw``), so none of them saw it.

One hosted worker, played by the stand-in model (``hosted_rail_turns``), asks to run a script while the lead is in
Auto with scripts not trusted, so the card is the person's. Checked in the real window, with screenshots:

* **Shown in the lead:** the worker's rail tile shows the approval, drawn by the product within a second of the
  card reaching the lead's mirror, while the lead's own chat stays in the chat area;
* **answered from the lead:** Angelo's approve operator, called as the benchmark's tester calls it (its first call
  opens the script's review, the second approves: ``eval_live_agent_session.answer_approvals``) on a card no chat
  area drew, runs the script in the worker within ``PROMPT_S``;
* **answered by a person:** real clicks on the worker's tile, then on its card's Approve twice (review, then run),
  run the next script in the worker within ``PROMPT_S``, and the main tile brings the lead back.

A failed run exits 1 after writing its report and stopping its worker. GUI ``--factory-startup
--enable-event-simulate`` only, disposable profile, no provider or paid call. Only with ``ANGELO_WORKER_CHAT_HOST``
unset or ``1`` (the hosted variant); the lanes variant records ``skipped``."""

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
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-worker-approval-lead'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
from angelo_agent import (chat_launcher, chat_view, connect_hero, console, live_view, live_view_draw,  # noqa: E402
                          parallel_sessions, session_rail, session_rail_draw, worker_consent_lead, worker_routing,
                          worker_sessions)
from hosted_worker_chat import connect, hosted  # noqa: E402
from hosted_rail_turns import RailTurns  # noqa: E402

PROMPT_S = 10.0   # From the Approve to the script's result in the lead; the benchmark's workers waited minutes.
HOSTED = hosted()
turns = RailTurns(OUT / 'stand-in') if HOSTED else None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.edit.use_global_undo = True
chat_launcher.BLINK_EVERY = float('inf')   # Its blink would redraw the view for the rail.
state = {'phase': 'boot', 'started': time.monotonic(), 'at': time.monotonic(), 'done': False}
report = {'checks': {}, 'paid_requests': 0, 'binary': bpy.app.binary_path, 'timings': {}, 'shots': [],
          'variant': 'hosted' if HOSTED else 'lanes', 'addon': worker_consent_lead.__file__}   # Which code ran.
answers = []
window = worker = None
frames = []   # Each real draw of the rail's view: when, and each tile's kind.


def check(name, condition, detail=None):
    report['checks'][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault('details', {})[name] = detail
    assert condition, name


def advance(phase):
    state['phase'], state['at'] = phase, time.monotonic()


def waited(seconds):
    return time.monotonic() - state['at'] >= seconds


def view_area():
    return live_view_draw.target(window)


def view_region():
    return next(r for r in view_area().regions if r.type == 'WINDOW')


def chat_area():
    return next(a for a in window.screen.areas if console.is_chat(a))


def probe():
    """POST_PIXEL after Angelo's own: what the rail showed in a frame the product drew (this suite forces none)."""
    if window is None or state.get('capturing') or bpy.context.window != window or bpy.context.area != view_area():
        return
    placed = session_rail_draw.last.get(window.as_pointer())
    tiles = {t.session: t.kind for t in placed[1].tiles} if placed and placed[0] == view_area().as_pointer() else {}
    frames.append({'at': time.monotonic(), 'tiles': tiles})
    del frames[:-2000]


bpy.types.SpaceView3D.draw_handler_add(probe, (), 'WINDOW', 'POST_PIXEL')


def screenshot(name):
    """The window as drawn now, rail included (``live_view.window_pixels``: a window behind others is not presented)."""
    from angelo_agent import view_editor
    state['capturing'] = True
    try:
        width, height, pixels = live_view.window_pixels(window)
    finally:
        state['capturing'] = False
    (OUT / name).write_bytes(view_editor.encode_png(pixels, width, height, bottom_up=True))
    report['shots'].append(name)


def send(kind, value, x, y):
    window.event_simulate(type=kind, value=value, x=x, y=y)


def click(x, y):
    send('MOUSEMOVE', 'NOTHING', x, y)
    send('LEFTMOUSE', 'PRESS', x, y)
    send('LEFTMOUSE', 'RELEASE', x, y)


def tile_point(session):
    placed = session_rail_draw.last.get(window.as_pointer())
    tile = next(t for t in placed[1].tiles if t.session == session)
    region = view_region()
    x, y, w, h = tile.rect
    return int(region.x + x + w / 2), int(region.y + y + h / 2)


def approve_point(call_id):
    """The window point of the Approve the chat area drew for ``call_id`` (its hit), or None."""
    area = chat_area()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    for rect, action, value in chat_view._views.get(area.as_pointer(), {}).get('hits', []):
        if action == 'approve' and value == call_id:
            return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    return None


def card():
    """The worker's card waiting for the person now, or None."""
    return next((c for c in worker.pending.values() if getattr(c, 'approval', None)
                 and not getattr(c, 'executing', False) and getattr(c, 'needs_person', True)), None)


def script(name):
    return {'description': 'an empty named %s' % name,
            'code': "import bpy\nob = bpy.data.objects.new(%r, None)\nbpy.context.scene.collection.objects.link(ob)\n"
                    "result = {'made': ob.name}" % name}


def ask(name):
    def answered(result):
        state['answered_at'] = time.monotonic()
        answers.append(result)
    state.pop('answered_at', None)
    answers.clear()
    turns.request('part', 'angelo_execute_python', script(name), answered)


def tile_shows_approval_within_a_second(since):
    """None while it may still come; else checks the product drew the tile's approval within a second of ``since``."""
    seen = next((f['at'] for f in frames if f['at'] > since
                 and f['tiles'].get(worker.state['id']) == session_rail.APPROVAL), None)
    if seen is None and time.monotonic() - since < 3.0:
        return None
    report['timings'].setdefault('tile_approval_drawn_s', []).append(None if seen is None else round(seen - since, 3))
    check('the_approval_is_drawn_on_the_workers_tile', seen is not None and seen - since <= 1.0,
          None if seen is None else seen - since)
    return seen


def ran_promptly(name, label):
    """None while waiting; else checks the script ran in the worker within ``PROMPT_S`` of the Approve."""
    if not answers and time.monotonic() - state['approved_at'] < PROMPT_S + 20:
        return None
    result = answers[-1] if answers else {}
    seconds = state['answered_at'] - state['approved_at'] if answers else None
    report['timings'][label + '_approve_to_result_s'] = None if seconds is None else round(seconds, 3)
    check(label + '_script_ran_in_the_worker', result.get('success') is True and (result.get('result') or {}).get(
        'made') == name, result)
    check(label + '_ran_promptly', seconds is not None and seconds <= PROMPT_S, seconds)
    check(label + '_never_ran_in_the_lead', name not in bpy.data.objects)
    return True


def tick():
    global window, worker
    try:
        if time.monotonic() - state['started'] > 300:
            raise RuntimeError('Worker approval fixture timed out in ' + state['phase'])
        phase = state['phase']
        if HOSTED:
            turns.tick()
        if phase == 'boot':
            if not HOSTED:   # The lanes approve a worker's card from the lead by design (CardBindingTests).
                report['skipped'] = 'ANGELO_WORKER_CHAT_HOST=0: lanes have no mirror; this suite is about a hosted chat'
                return finish()
            if not qa_boot.started():
                return .1
            window = bpy.context.window_manager.windows[0]
            if connect_hero.is_shown():   # No account: dismissed by keyboard, as a person would.
                if not state.get('hero_dismissed'):
                    x, y = int(window.width / 2), int(window.height / 2)
                    for key in ('TAB', 'RET'):
                        send(key, 'PRESS', x, y)
                        send(key, 'RELEASE', x, y)
                    state['hero_dismissed'] = True
                return .2 if not waited(10) else finish('the connect card never went away')
            connect_hero._dismissed = True
            from angelo_agent import director   # Director Mode, the default view a person works in.
            if not director.is_director(window):
                if 'director_asked' not in state:
                    director.request_mode(window, 'DIRECTOR')
                    state['director_asked'] = time.monotonic()
                return .2 if time.monotonic() - state['director_asked'] < 20 else finish('Director Mode never opened')
            if not any(console.is_chat(a) for a in window.screen.areas):   # The chat docked, as a person keeps it.
                if 'chat_asked' not in state:
                    from angelo_agent import chat_toggle
                    chat_toggle.show(window)
                    state['chat_asked'] = time.monotonic()
                return .2 if time.monotonic() - state['chat_asked'] < 15 else finish('the chat never docked')
            report['layout'] = 'director'
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'main.blend'), check_existing=False)
            lead = ui.get_controller()
            report['lead'] = lead.state['id']
            lead.state['messages'].append({'role': 'user', 'text': 'Build a shelf.'})   # Workers start in its turn.
            connect(lead)   # Its worker's chat inherits its connection and its Auto.
            lead.state['options'] = dict(lead.state.get('options') or {}, approval='AUTO_BOUNDED')
            check('scripts_are_not_trusted', not lead.trusted_scripts and not lead.always_allow_scripts)
            worker = worker_sessions._new_chat(lead)
            worker_routing.start(worker, OUT / 'parts' / 'shelf' / 'part.blend')
            turns.add('part', worker)
            parallel_sessions.names[worker.state['id']] = 'Shelf'
            ask('Lead Approved')
            advance('lead-card')
            return .2
        if phase == 'lead-card':   # Case A: the lead's chat shown; the card on the worker's tile only.
            call = card()
            if call is None:
                return .1 if not waited(60) else finish('the worker never asked to run its script')
            if tile_shows_approval_within_a_second(turns.card_at('part')) is None:
                return .05
            session = worker.state['id']
            check('the_leads_chat_stays_in_the_chat_area', ui._sessions.active == report['lead'],
                  ui._sessions.active)
            check('no_chat_area_drew_the_workers_card', call.id not in worker_consent_lead._shown.get(session, {}))
            check('the_card_asks_the_person_in_auto', getattr(call, 'needs_person', None) is True
                  and call.name == 'angelo_execute_python')
            screenshot('1-lead-approval-on-the-tile.png')
            state['approved_at'] = time.monotonic()
            with bpy.context.temp_override(window=window):   # As eval_live_agent_session.answer_approvals does.
                first = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=session)
                second = (bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=session)
                          if 'CANCELLED' in first and call.id in worker.reviewed_calls else first)
            report['operator_results'] = [sorted(first), sorted(second)]
            check('the_first_approve_opens_the_scripts_review', first == {'CANCELLED'}
                  and call.id in worker.reviewed_calls, sorted(first))
            check('the_second_approve_is_sent', second == {'FINISHED'}, sorted(second))
            advance('lead-ran')
            return .05
        if phase == 'lead-ran':
            if ran_promptly('Lead Approved', 'lead') is None:
                return .1
            screenshot('2-lead-approved-ran.png')
            ask('Person Approved')
            advance('person-card')
            return .1
        if phase == 'person-card':   # Case B: a person clicks the tile, then the card's Approve twice.
            call = card()
            if call is None:
                return .1 if not waited(60) else finish('the worker never asked to run its second script')
            if tile_shows_approval_within_a_second(turns.card_at('part')) is None:
                return .05
            state['call'] = call.id
            click(*tile_point(worker.state['id']))
            advance('person-shown')
            return .3
        if phase == 'person-shown':
            point = approve_point(state['call'])
            if ui._sessions.active != worker.state['id'] or point is None:
                return .1 if not waited(10) else finish('the worker card never showed after its tile was clicked')
            check('a_click_on_the_tile_shows_the_workers_card', True)
            screenshot('3-person-card-in-the-chat.png')
            state['approved_at'] = time.monotonic()
            click(*point)   # Its exact-code review opens.
            advance('person-review')
            return .3
        if phase == 'person-review':
            point = approve_point(state['call'])
            if state['call'] not in worker.reviewed_calls or point is None:
                return .1 if not waited(10) else finish('the script review never opened')
            screenshot('4-person-script-review.png')
            click(*point)
            advance('person-ran')
            return .05
        if phase == 'person-ran':
            if ran_promptly('Person Approved', 'person') is None:
                return .1
            click(*tile_point(report['lead']))
            advance('back')
            return .3
        if phase == 'back':
            if ui._sessions.active != report['lead'] and not waited(10):
                return .2
            check('the_main_tile_brings_the_lead_back', ui._sessions.active == report['lead'])
            screenshot('5-back-on-the-lead.png')
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
    (OUT / 'worker-approval-lead.json').write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_WORKER_APPROVAL_LEAD', json.dumps({'success': report['success'], 'phase': state['phase']}),
          flush=True)
    if not report['success']:
        os._exit(1)   # Report written and the worker stopped above.
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=.5)
