# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Workers at once on the AI Providers page, with real clicks (``parallel_setting``, 1.0.6).

On a machine that runs parallel edit (unforced, as a person's Angelo): the Parallel edit section offers Suggested (the
machine's count, chosen in a new profile) and 1, 2, 4, 8, 16; a click on 4 sets the preference and admission's count
to 4 and draws 4 as chosen; 16 is allowed above the suggestion and the row says what it may cost; Suggested goes back
to the machine's count. The lead's start tool keeps the count it launched with (the maintainer: launch-time is fine).
Below the bar the section offers no choice. Screenshots of each state. The preferences are never saved.

GUI ``--factory-startup --enable-event-simulate`` only, disposable profile, no provider, account or network.
"""

import hashlib
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
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-parallel-setting'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1')
os.environ.pop('ANGELO_ENABLE_PARALLEL_AGENTS', None)   # A person's Angelo: the machine decides, never forced.
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
import qa_input  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
from angelo_agent import parallel_access, parallel_capability, providers_page_model, tool_specs  # noqa: E402
from angelo_agent import providers_page_host as page  # noqa: E402

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False   # Never written: a disposable profile, and nothing kept.
REPORT = OUT / 'parallel-setting.json'
state = {'phase': 'boot', 'started': time.monotonic(), 'waiting': None, 'done': False}
report = {'checks': {}, 'binary': bpy.app.binary_path, 'shots': [], 'observed': {}}
CLICKS = ((4, 'p2-workers-at-once-4.png'), (16, 'p3-workers-at-once-16.png'), (0, 'p4-workers-at-once-suggested.png'))


def check(name, condition, detail=None):
    report['checks'][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault('details', {})[name] = detail


def prefs_window():
    target = next((w for w in bpy.context.window_manager.windows
                   if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)
    if target is None:
        return None, None, None
    area = next(a for a in target.screen.areas if a.type == 'PREFERENCES')
    return target, area, next(r for r in area.regions if r.type == 'WINDOW')


def plan():
    _, _, region = prefs_window()
    return page.plan_for(region) if region else None


def choices():
    """{value: (x, y, w, h)} of the drawn Workers at once buttons, in region pixels."""
    found = plan()
    return {value[1]: rect for rect, action, value in (found.hits if found else ()) if action == 'pref'}


def styles():
    found = plan()
    return {op[2]: op[3] for op in (found.ops if found else ()) if op[0] == 'pbutton'}


def texts():
    found = plan()
    return ' '.join(' '.join(op[1] for op in (found.ops if found else ()) if op[0] == 'text').split())


def visible(rect, region, margin=24):
    """Wholly inside the page with room around it (the row grows when it warns, moving the buttons down)."""
    x, y, w, h = rect
    return margin <= y and y + h <= region.height - margin and 0 <= x and x + w <= region.width


def scroll_into_view(rect, target, area, region):
    """Scroll the page a few steps toward ``rect``; True when it is already in view (y is upward)."""
    if visible(rect, region):
        return True
    state['scrolls'] = state.get('scrolls', 0) + 1
    assert state['scrolls'] < 60, 'Workers at once never came into view: %r' % (rect,)
    scroll = bpy.ops.view2d.scroll_down if rect[1] < 24 else bpy.ops.view2d.scroll_up
    with bpy.context.temp_override(window=target, area=area, region=region):
        for _ in range(2):
            scroll()
    return False


def shot(name, target):
    """Capture until two shots in a row match: the page has settled and the capture is not an older frame."""
    path = OUT / name
    with bpy.context.temp_override(window=target):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    previous, count = state.get('shot', (None, 0)) if state.get('shot_name') == name else (None, 0)
    state['shot'], state['shot_name'] = (digest, count + 1), name
    if digest == previous or count + 1 >= 15:
        report['shots'].append(name)
        return True
    return False


def wait(condition, limit=30):
    if condition():
        state['waiting'] = None
        return True
    state['waiting'] = state['waiting'] or time.monotonic()
    assert time.monotonic() - state['waiting'] < limit, 'Timed out waiting in ' + state['phase']
    return False


def finish(error=None):
    if state['done']:
        return None
    state['done'] = True
    report.update(phase=state['phase'], error=error,
                  success=error is None and bool(report['checks']) and all(report['checks'].values()))
    REPORT.write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_PARALLEL_SETTING', json.dumps({'success': report['success'], 'phase': state['phase']}), flush=True)
    if not report['success']:
        os._exit(1)   # The report is written: a failed suite exits 1.
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.5)
    return None


def tick():
    try:
        assert time.monotonic() - state['started'] < 200, 'Parallel setting suite timed out in ' + state['phase']
        phase = state['phase']
        prefs = ui.preferences()
        if phase == 'boot':
            if not qa_boot.started() or ui.main_window() is None:
                return .2
            machine = parallel_capability.current()
            start = tool_specs.TOOL_MAP.get('angelo_workers_start')
            report['observed'].update(enabled=parallel_access.enabled(), suggested=machine.workers,
                                      missing=list(machine.missing), start_tool=start and start['description'])
            check('a_new_profile_follows_the_machine', prefs.parallel_workers == 0 and parallel_access.chosen() == 0)
            if parallel_access.enabled():
                state['launch'] = start['description']
                check('the_start_tool_says_the_launch_count',
                      'At most %d workers run at once' % machine.workers in start['description'])
            with bpy.context.temp_override(window=ui.main_window()):
                bpy.ops.angelo.settings()
            state['phase'] = 'page'
            return .5
        target, area, region = prefs_window()
        if phase == 'page':
            if not wait(lambda: plan() is not None and providers_page_model.ART['done']
                        and target.as_pointer() in page._modals):
                return .2
            if not parallel_access.enabled():   # Below the bar: the section says what it needs, with no choice.
                check('below_the_bar_offers_no_choice', not choices() and 'Parallel edit needs' in texts())
                shot('p1-parallel-edit-below-the-bar.png', target)
                return finish()
            rect = choices().get(0)
            check('the_section_offers_suggested_and_numbers', sorted(choices()) == [0, 1, 2, 4, 8, 16], choices())
            if rect is not None and not scroll_into_view(rect, target, area, region):
                return .3
            state['phase'] = 'suggested'
            return .3
        if phase == 'suggested':
            if not shot('p1-workers-at-once-suggested.png', target):
                return .3
            suggested = report['observed']['suggested']
            label = 'Suggested (%d)' % suggested
            check('suggested_names_the_machines_count_and_is_chosen', styles().get(label) == 'primary', styles())
            check('the_row_says_the_count_in_force',
                  'Up to %d worker agents at once on this computer.' % suggested in texts(), texts())
            state['phase'], state['click'] = 'click', 0
            return .1
        if phase == 'click':
            value, name = CLICKS[state['click']]
            if not state.get('sent'):
                if not scroll_into_view(choices()[value], target, area, region):   # A warning moved it down.
                    return .3
                qa_input.press(target, lambda: (region.x + choices()[value][0] + choices()[value][2] / 2,
                                                region.y + choices()[value][1] + choices()[value][3] / 2))
                state['sent'] = True
                return .3
            if not wait(lambda: prefs.parallel_workers == value):
                return .2
            if not scroll_into_view(choices()[value], target, area, region):   # The whole row in the picture.
                return .3
            if not shot(name, target):
                return .3
            suggested = report['observed']['suggested']
            limit = value or suggested
            label = str(value) if value else 'Suggested (%d)' % suggested
            check('a_click_on_%d_sets_admissions_count' % value, parallel_access.worker_limit() == limit)
            labels = ['Suggested (%d)' % suggested] + [str(n) for n in (1, 2, 4, 8, 16)]
            drawn = [styles().get(name) for name in labels]   # The choice's own buttons, not the page's others.
            check('a_click_on_%d_draws_it_chosen' % value,
                  styles().get(label) == 'primary' and drawn.count('primary') == 1, drawn)
            check('the_row_says_%d_at_once' % limit, 'Up to %d worker agents' % limit in texts(), texts())
            warned = 'More than this computer suggests' in texts()
            check('more_than_suggested_says_what_it_costs_%d' % value, warned == (value > suggested), texts())
            check('the_start_tool_keeps_its_launch_count_%d' % value,
                  tool_specs.TOOL_MAP['angelo_workers_start']['description'] == state['launch'])
            state['click'], state['sent'] = state['click'] + 1, False
            if state['click'] == len(CLICKS):
                return finish()
            return .2
        return .2
    except Exception:
        return finish(traceback.format_exc())


bpy.app.timers.register(tick, first_interval=1.0)
