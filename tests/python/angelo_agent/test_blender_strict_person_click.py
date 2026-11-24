# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person turns Strict script protection off with a real click, on each of its two controls, and every reader of
the setting agrees afterwards (``strict_protection``; doc/angelo/script_confinement.rst, "Strict script protection").

``test_blender_strict_script_protection`` proves Off in C's harness scope; this suite takes the path a person's press
takes instead: a real move onto the control, the press once it armed, C's own ticket or grant scope for that press,
the preference's update and C's sync. Each case starts from On set by Angelo's own code (narrowing), then:

* its switch on the AI Providers page (``providers_page_host``: the grant scope of the press, ``page.toggle``);
* its own checkbox (an add-on preference's button: the ``prop.strict_script_protection`` ticket the press mints);
* that checkbox once more, with the window brought forward while its popup is open (``activation``): the operating
  system's activation, run through the window manager's own path (``_angelo_jobs.window_activate_simulate``), then
  the person's usual move and press. Windows brings the main window forward when Preferences closes, up to a second
  later, so a person who opens the popup at once gets that order (build2 on 336a00f, 2026-11-23: the press there
  minted nothing and showed only a hint);
* that checkbox once a menu covered it (``covered``): armed by the person's move, a menu opened over it,
  a click on the menu's item, then the person's next click at the same spot without moving finds it unarmed (no
  grant, a hint: they have not seen it since the menu covered it), and their usual move and press grants (the
  review of d719ab2's covered-control SHOULD; the maintainer's "Fix in 2.1.0").

After each press: the preference, C's flag (``_bpy._angelo_guard``), the confinement's reader (``strict_protection``),
the saved choice in the state folder, the page's switch and CPython's own ``os.stat`` (no Strict wrapper once off)
all say off, and no "not changed" refusal was told. Angelo's own code without a press still can't turn it off.
The build3 in-app run on 1f95324 (1/30, ``os_stat_native_when_off``) came from that suite's child sharing the parent's
state folder, where On was saved; this suite is the person's path itself.

A person whose window was left in the background makes one click to bring it forward first, which never grants
(grant_operators.rst): ``qa_input.forward`` makes that click on a blank header spot when C's focus record says the
next press would be it, before the popup opens. It is not what the Windows failure of 2026-11-23 was: there it never
fired (brought_forward False on cea106b), and the checkbox case passed only because its popup opened after Windows'
activation had landed; the ``activation`` case is that order, on every platform.

GUI ``--factory-startup --enable-event-simulate`` only, a disposable profile, preferences never saved. No provider,
account or network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-strict-person-click'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
import qa_boot  # noqa: E402
import qa_hit  # noqa: E402
import qa_input  # noqa: E402
import strict_click_probe as probe  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
from angelo_agent import providers_page_host as page, strict_protection  # noqa: E402

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False   # Never written: a disposable profile, and nothing kept.
NAME = strict_protection.NAME
LABEL = "Strict script protection"   # Its checkbox's label (``StrictProtectionPreferences``).
ARM = 0.6   # Past C's ARM_SECONDS (0.3) with room.
NATIVE = sys.modules["nt" if os.name == "nt" else "posix"]
REPORT = OUT / 'strict-person-click.json'
state = {'phase': 'boot', 'started': time.monotonic(), 'waiting': None, 'done': False, 'case': 'page'}
report = {'checks': {}, 'binary': bpy.app.binary_path, 'details': {}}


def check(name, condition, detail=None):
    report['checks'][name] = bool(condition)
    if not condition and detail is not None:
        report['details'][name] = str(detail)[:1500]


def guard():
    import _bpy
    return _bpy._angelo_guard


def saved():
    path = Path(os.environ['ANGELO_STATE_DIR']) / strict_protection.FILE
    return json.loads(path.read_text()).get('on') if path.is_file() else None


def readers():
    """Every reader of the setting: preference, C, the confinement's reader, the saved choice, os.stat."""
    return {'preference': ui.preferences().strict_script_protection, 'c': guard().strict_script_protection(),
            'reader': strict_protection.enabled(), 'saved': saved(),
            'os_stat_wrapped': getattr(os.stat, '__self__', None) is not NATIVE}


def refusals():
    from _bpy import _angelo_jobs
    said = ("Strict script protection was not changed", "Only your own click")
    return [message for _kind, message in _angelo_jobs.window_reports() if any(s in message for s in said)]


def prefs_window():
    target = next((w for w in bpy.context.window_manager.windows
                   if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)
    if target is None:
        return None, None, None
    area = next(a for a in target.screen.areas if a.type == 'PREFERENCES')
    return target, area, next(r for r in area.regions if r.type == 'WINDOW')


def page_switch():
    """The rect of Strict's switch on the AI Providers page, in region pixels; None while not drawn."""
    _, _, region = prefs_window()
    found = page.plan_for(region) if region else None
    return next((r for r, action, value in (found.hits if found else ()) if action == 'toggle' and value == NAME), None)


def inside(inner, outer):
    (x, y, w, h), (ox, oy, ow, oh) = inner, outer
    return ox <= x and oy <= y and x + w <= ox + ow and y + h <= oy + oh


def page_switch_drawn():
    """Whether the page draws Strict's switch on: the switch op drawn inside its hit (the hit is the switch padded on
    every side, for the press: providers_page_layout._switch_row), never one at exactly the hit's rect. None when not
    found."""
    _, _, region = prefs_window()
    found, rect = (page.plan_for(region) if region else None), page_switch()
    return next((op[2] for op in (found.ops if found else ()) if op[0] == 'switch' and inside(op[1], rect)),
                None) if rect is not None else None


def page_switch_seen():
    """What the drawn-switch check looked at, for a failed check's details: the hit and every switch op drawn."""
    _, _, region = prefs_window()
    found = page.plan_for(region) if region else None
    return {'hit': page_switch(), 'switches': [(tuple(op[1]), op[2]) for op in (found.ops if found else ())
                                               if op[0] == 'switch']}


def scroll_into_view(rect, target, area, region, margin=24):
    x, y, w, h = rect
    if margin <= y and y + h <= region.height - margin:
        return True
    state['scrolls'] = state.get('scrolls', 0) + 1
    assert state['scrolls'] < 80, "Strict's switch never came into view: %r" % (rect,)
    scroll = bpy.ops.view2d.scroll_down if y < margin else bpy.ops.view2d.scroll_up
    with bpy.context.temp_override(window=target, area=area, region=region):
        for _ in range(2):
            scroll()
    return False


def checkbox(window):
    """The checkbox's centre in ``window`` (Blender's own button in the popup Angelo's code opened), or None."""
    from _bpy import _angelo_jobs
    found = next(((x, y, w, h) for pointer, _block, label, _idname, x, y, w, h in _angelo_jobs.popup_buttons()
                  if pointer == window.as_pointer() and w > 0 and LABEL in label), None)
    return (int(found[0] + min(found[2], found[3]) / 2), int(found[1] + found[3] / 2)) if found else None


def checkbox_seen(window):
    """What the checkbox press looked at, for a failed check's details (Windows build2 on 1e19738: the press on the
    popup's checkbox changed nothing and told no refusal, 2/2): the popup's buttons and their bounds, the checkbox
    and the point pressed, the window's size, place and scale, C's focus record of it (``window_state``: epoch,
    returning, whether the last press only brought it forward), the hints a press before arming showed, the readers."""
    from _bpy import _angelo_jobs
    from angelo_agent import approval_arming, grant_hints
    try:
        rows = [row for row in _angelo_jobs.popup_buttons() if row[0] == window.as_pointer()]
    except Exception as error:   # noqa: BLE001 - a detail only.
        rows, error_text = [], repr(error)
    else:
        error_text = None
    blocks = {}
    for _pointer, block, _label, _idname, x, y, w, h in rows:
        x0, y0, x1, y1 = blocks.get(block, (x, y, x + w, y + h))
        blocks[block] = (min(x0, x), min(y0, y), max(x1, x + w), max(y1, y + h))
    found = next(((x, y, w, h) for _p, _b, label, _i, x, y, w, h in rows if w > 0 and LABEL in label), None)
    system, view = bpy.context.preferences.system, bpy.context.preferences.view
    return {'popup_open': found is not None, 'checkbox_rect': found, 'checkbox_point': checkbox(window),
            'popup_bounds': blocks, 'buttons': [(label, idname, (x, y, w, h)) for _p, _b, label, idname, x, y, w, h
                                                in rows][:12], 'popup_buttons_error': error_text,
            'window': {'size': (window.width, window.height), 'place': (window.x, window.y),
                       'pixel_size': system.pixel_size, 'ui_scale': view.ui_scale, 'dpi': system.dpi},
            'focus': approval_arming.window_state(window.as_pointer()), 'os_cursor': probe.os_cursor(),
            'os_foreground': probe.os_foreground(),
            'pressed': list(qa_input.sent[-3:]), 'pressed_ago': [round(time.monotonic() - t, 2)
                                                                 for t in qa_input.sent_at[-3:]],
            'press_lost': list(qa_input.lost),
            # In region pixels of the region holding the hint's centre (grant_hints._shown), never window pixels.
            'hints_in_region_pixels': {str(k): v for k, v in grant_hints._shown.items()},
            'guard': _guarded(lambda: _angelo_jobs.window_guard(window.as_pointer())),
            'checkbox_guard': _guarded(lambda: [row for row in _angelo_jobs.popup_button_guards()
                                                 if row['window'] == window.as_pointer() and LABEL in row['text']]),
            'refusals': refusals(), 'readers': readers()}


def _guarded(read):
    """A detail C's arming reads (window_guard, popup_button_guards), or why there is none."""
    try:
        return read()
    except Exception as error:   # noqa: BLE001 - a detail only.
        return repr(error)


def screenshot(window, name):
    """The window as drawn now, beside the report (``name``.png); its path, or why there is none."""
    path = OUT / (name + '.png')
    try:
        with bpy.context.temp_override(window=window):
            bpy.ops.screen.screenshot(filepath=str(path))
        return str(path)
    except Exception as error:   # noqa: BLE001 - a detail only.
        return repr(error)


def note_seen(when, window):
    state.setdefault('seen', {})[when] = checkbox_seen(window)


def checkbox_timeout():
    """The checkbox press changed nothing: every snapshot (after the Preferences window closed, before the press,
    after it, now) and the screenshots go in the report's details; the timeout's message says the gist."""
    window, case = ui.main_window(), state['case']
    note_seen('timeout', window)
    report['details'][case + '_check'] = dict(state['seen'], before_press_png=state.get('before_press_png'),
                                              timeout_png=screenshot(window, case + '-timeout'))
    return {when: {key: seen[key] for key in ('focus', 'guard', 'checkbox_guard', 'popup_open', 'checkbox_point',
                                             'pressed', 'hints_in_region_pixels')}
            for when, seen in state['seen'].items()}


def open_checkbox():
    window = ui.main_window()
    area = max(window.screen.areas, key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.context.window_manager.popup_menu(lambda menu, _c: menu.layout.prop(ui.preferences(), NAME), title="Strict")


def wait(condition, limit=30, seen=None):
    """``seen``: what the condition looked at, put in the timeout's message."""
    if condition():
        state['waiting'] = None
        return True
    state['waiting'] = state['waiting'] or time.monotonic()
    assert time.monotonic() - state['waiting'] < limit * qa_input_scale(), 'Timed out waiting in %s%s' % (
        state['phase'], ': %r' % (seen(),) if seen else '')
    return False


def qa_input_scale():
    import qa_waits
    return qa_waits.timeout_scale()


def turn_on(case):
    """Angelo's own code turns it on (narrowing), and no press turns it off."""
    ui.preferences().strict_script_protection = True
    on = readers()
    check(case + '_starts_on', on['preference'] is True and on['c'] is True and on['reader'] is True
          and on['saved'] is True, on)
    ui.preferences().strict_script_protection = False   # No press: refused, On back.
    kept = readers()
    check(case + '_angelo_without_a_press_keeps_it_on', kept['preference'] is True and kept['c'] is True, kept)


def all_off(case):
    off = readers()
    check(case + '_the_persons_press_turns_every_reader_off', off == {
        'preference': False, 'c': False, 'reader': False, 'saved': False, 'os_stat_wrapped': False}, off)
    check(case + '_tells_no_refusal', len(refusals()) == state['refusals'], refusals())


def finish(error=None):
    if state['done']:
        return None
    state['done'] = True
    report.update(phase=state['phase'], error=error,
                  success=error is None and bool(report['checks']) and all(report['checks'].values()))
    REPORT.write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_STRICT_PERSON_CLICK', json.dumps({'success': report['success'], 'phase': state['phase'],
                                                    'checks': report['checks']}), flush=True)
    try:   # The suite's own covering-menu operator goes with it.
        bpy.utils.unregister_class(probe.ANGELO_OT_suite_cover_noop)
    except RuntimeError:
        pass
    if not report['success']:
        os._exit(1)   # The report is written: a failed suite exits 1.
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.5)
    return None


def tick():
    try:
        assert time.monotonic() - state['started'] < 200, 'Strict person click suite timed out in ' + state['phase']
        phase = state['phase']
        if phase == 'boot':
            if not qa_boot.started() or ui.main_window() is None:
                return .2
            first = readers()
            check('off_by_default', not any(first[key] for key in ('preference', 'c', 'reader', 'os_stat_wrapped')),
                  first)
            turn_on('page')
            with bpy.context.temp_override(window=ui.main_window()):
                bpy.ops.angelo.settings()
            state['phase'] = 'page'
            return .5
        if phase == 'page':
            target, area, region = prefs_window()
            if not wait(lambda: page_switch() is not None and prefs_window()[0].as_pointer() in page._modals):
                return .2
            if not scroll_into_view(page_switch(), target, area, region):
                return .3
            state['shown'], state['phase'] = time.monotonic(), 'page_press'
            return .1
        if phase == 'page_press':
            target, _area, region = prefs_window()
            if time.monotonic() - state['shown'] < ARM:
                return .1
            state['refusals'] = len(refusals())   # Angelo's own refused Off above told its refusal by now.
            check('page_switch_drawn_on_before_the_press', page_switch_drawn() is True,
                  dict(page_switch_seen(), drawn=page_switch_drawn()))
            qa_input.press(target, lambda: qa_hit.point(qa_hit.PIXELS, region, page_switch()))
            state['phase'] = 'page_check'
            return .3
        if phase == 'page_check':
            if not wait(lambda: ui.preferences().strict_script_protection is False, 8):
                return .2
            all_off('page')
            state['phase'] = 'page_drawn'
            return .2
        if phase == 'page_drawn':
            if not wait(lambda: page_switch_drawn() is False, 8, page_switch_seen):
                return .2
            check('page_switch_draws_off', True)
            target, _area, _region = prefs_window()
            with bpy.context.temp_override(window=target):
                bpy.ops.wm.window_close()
            note_seen('after_close', ui.main_window())
            state['case'] = 'checkbox'
            turn_on('checkbox')
            # A person's click to bring the window forward, when C says the next press is that (reported, so a run
            # shows it). Not the Windows failure's fix: there it never fired; the activation case is that order.
            report['brought_forward'] = qa_input.forward(ui.main_window())
            state['phase'] = 'forward'
            return .3
        if phase == 'forward':
            from angelo_agent import approval_arming
            window = ui.main_window()
            if not wait(lambda: not approval_arming.window_state(window.as_pointer())[1], 8,
                        lambda: checkbox_seen(window)):
                return .1
            open_checkbox()
            state['phase'], state['shown'] = 'checkbox', None
            return .3
        if phase == 'checkbox':
            window = ui.main_window()
            if not wait(lambda: checkbox(window) is not None):
                return .1
            state['shown'] = state['shown'] or time.monotonic()
            if time.monotonic() - state['shown'] < ARM:
                return .1
            if state['case'] == 'activation' and not state.get('activated'):
                # The operating system brings the window forward while the popup is open (Windows, when
                # Preferences closes): the window manager's own path, then the person's usual move and press.
                from _bpy import _angelo_jobs
                note_seen('before_activation', window)
                _angelo_jobs.window_activate_simulate(window.as_pointer())
                state['activated'] = True
                return .3   # Its synthetic move handled, as any event before the person's next one.
            state['refusals'] = len(refusals())
            note_seen('before_press', window)
            state['before_press_png'] = screenshot(window, state['case'] + '-before-press')
            qa_input.press(window, lambda: checkbox(window))   # A person's move onto it, then the press.
            for at in (.15, .4):   # Failure details: its arming between the arrival and the press.
                bpy.app.timers.register(lambda at=at, w=window: note_seen('arrival+%.2f' % at, w), first_interval=at)
            state['phase'] = 'checkbox_check'
            return .3
        if phase == 'checkbox_check':
            if 'after_press' not in state.get('seen', {}):
                note_seen('after_press', ui.main_window())
            if not wait(lambda: ui.preferences().strict_script_protection is False, 8, checkbox_timeout):
                return .2
            all_off(state['case'])
            for value in ('PRESS', 'RELEASE'):
                ui.main_window().event_simulate(type='ESC', value=value)
            if state['case'] == 'checkbox':
                state['phase'], state['case'], state['seen'] = 'activation', 'activation', {}
                return .5
            if state['case'] == 'activation':
                state['phase'], state['case'], state['seen'] = 'covered', 'covered', {}
                return .5
            return finish()
        if phase == 'covered':
            window = ui.main_window()
            if not wait(lambda: checkbox(window) is None, 8):   # The last case's popup closed.
                return .2
            turn_on('covered')
            # The popup away from the window's edges (a menu near an edge is clamped, never over the point).
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=window.width // 2, y=window.height // 2)
            open_checkbox()
            state['phase'], state['shown'], state['arriving'] = 'covered_arm', None, False
            return .3
        if phase == 'covered_arm':
            window = ui.main_window()
            if not wait(lambda: checkbox(window) is not None):
                return .1
            state['shown'] = state['shown'] or time.monotonic()
            if time.monotonic() - state['shown'] < ARM:
                return .1
            x, y = state['point'] = checkbox(window)
            if not state['arriving']:   # The person's hand arrives next to it, then moves onto it: armed.
                window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x - 1, y=y)
                state['arriving'] = True
                return qa_input.DELAY
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
            state['phase'] = 'covered_menu'
            return .3
        if phase == 'covered_menu':
            window = ui.main_window()
            check('covered_armed_before_the_menu', probe.armed_checkbox(window, LABEL) is True, checkbox_seen(window))
            probe.open_cover(window)   # A menu opens over it, under the pointer.
            state['phase'] = 'covered_first_click'
            return .3
        if phase == 'covered_first_click':
            window = ui.main_window()
            if not wait(lambda: probe.cover_item(window) is not None, 8):
                return .1
            x, y = state['point']
            if probe.cover_item(window, (x, y)) is None:
                return finish('no row of the covering menu is over the checkbox: a row %r, checkbox point %r'
                              % (probe.cover_item(window), state['point']))
            state['refusals'] = len(refusals())
            note_seen('covered_before_first_click', window)
            for value in ('PRESS', 'RELEASE'):   # The menu's item: the menu closes.
                window.event_simulate(type='LEFTMOUSE', value=value, x=x, y=y)
            state['phase'] = 'covered_second_click'
            return .45   # Past the double-click time, with no move between.
        if phase == 'covered_second_click':
            window = ui.main_window()
            if not wait(lambda: probe.cover_item(window) is None, 8):
                return .1
            x, y = state['point']
            note_seen('covered_before_second_click', window)
            for value in ('PRESS', 'RELEASE'):   # The person's next click, where they were, without moving.
                window.event_simulate(type='LEFTMOUSE', value=value, x=x, y=y)
            state['phase'], state['second_click'] = 'covered_refused', time.monotonic()
            return .3
        if phase == 'covered_refused':
            from angelo_agent import grant_hints
            window = ui.main_window()
            if time.monotonic() - state['second_click'] < 1.0:
                return .1
            note_seen('covered_after_second_click', window)
            kept = readers()
            check('covered_second_click_grants_nothing', kept['preference'] is True and kept['c'] is True, kept)
            check('covered_second_click_tells_no_refusal', len(refusals()) == state['refusals'], refusals())
            check('covered_second_click_shows_the_hint', bool(grant_hints.showing()), grant_hints._shown)
            if any(value is False for value in report['checks'].values()):
                report['details']['covered'] = state['seen']
            if checkbox(window) is None:
                open_checkbox()   # The person opens it again for their own press.
            state['phase'], state['shown'] = 'checkbox', None
            return .3
        if phase == 'activation':
            if not wait(lambda: checkbox(ui.main_window()) is None, 8):   # The last case's popup closed.
                return .2
            turn_on('activation')
            open_checkbox()
            state['phase'], state['shown'] = 'checkbox', None
            return .3
        return .2
    except Exception:
        return finish(traceback.format_exc())


# Never a phase while a click it sent is still on its way (qa_input.after_clicks).
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=1.0)
