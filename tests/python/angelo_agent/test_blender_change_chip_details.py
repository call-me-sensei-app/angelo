# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual rendered chip/native mouse events, popup screenshots and Escape in an isolated app.

No product callback is injected, replaced or wrapped. success is STRUCTURAL ONLY:
the saved popup image must separately be reviewed for all 39 names and legibility.
No acknowledgement file can convert a timer completion into independent pixel proof.
The factory scene is fixture-owned; opening/dismissing the popup must not mutate it or chat.
"""
import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import threading
import time
import traceback

import addon_utils
import bpy
import qa_boot
import qa_input

assert '--factory-startup' in __import__('sys').argv
assert not bpy.app.background
OUT = Path(os.environ['ANGELO_QA_DIR'])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / 'change-chip-details.json'
STARTED = time.monotonic()
state = dict(success=False, phase='boot', checks={}, screenshots=[], errors=[],
             unexpected_process_requests=0, unexpected_network_requests=0, blocked_process_requests=0,
             denied_startup_status_requests={mode: 0 for mode in ("codex_subscription", "claude_subscription", "codex_api")},
             structural_only=True, actual_pixels_accepted=False, pixel_review_required=True,
             scope='Actual rendered chip mouse events; popup contents require independent saved-image review')
# Resolve CLI lookup to owned inert files before registration; no CLI is executed.
BIN = OUT / 'offline-bin'
BIN.mkdir(exist_ok=False)
for name in ('codex', 'claude'):
    path = BIN / name
    path.write_text('#!/bin/sh\nexit 1\n')
    path.chmod(0o700)
os.environ['PATH'] = str(BIN) + os.pathsep + os.environ.get('PATH', '')
os.environ.update(ANGELO_AGENT_RUNTIME='off', ANGELO_DISABLE_KEYCHAIN='1',
                  ANGELO_DISABLE_DEFAULT_CHAT='1')


# ui.register asks these three read-only questions even with the agent runtime off.
# They are DENIED like every other process request; only their closed attribution differs.
_STATUS_ARGV = {
    (str(BIN / 'codex'), 'login', 'status'): 'codex_subscription',
    (str(BIN / 'claude'), 'auth', 'status'): 'claude_subscription',
    (str(BIN / 'codex'), '-c', 'cli_auth_credentials_store="keyring"', 'login', 'status'): 'codex_api',
}
_STATUS_PROFILES = {mode: {key: os.environ[key] for key in ('HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR')}
                    for mode in state['denied_startup_status_requests']}
_STATUS_PROFILES['codex_api']['CODEX_HOME'] = str(Path(os.environ['ANGELO_CONNECTIONS_DIR']) / 'codex-profile' / 'codex_api')
_STATUS_FILES = {str(path): path.lstat() for path in (BIN / 'codex', BIN / 'claude')}
_PROCESS_GUARD_LOCK = threading.Lock()


def forbidden_process(*args, **kwargs):
    command = args[1] if len(args) == 2 and type(args[1]) is list else ()
    mode = _STATUS_ARGV.get(tuple(command)) if all(type(part) is str for part in command) else None
    env = kwargs.get('env')
    known = (mode is not None and type(env) is dict
             and threading.current_thread().name == 'AngeloNativeAuthentication'
             and set(kwargs) == {'env', 'cwd', 'stdin', 'stdout', 'stderr', 'text', 'encoding', 'errors'}
             and kwargs['cwd'] == os.environ['ANGELO_STATE_DIR']
             and kwargs['stdin'] == subprocess.DEVNULL and kwargs['stdout'] == subprocess.PIPE
             and kwargs['stderr'] == subprocess.STDOUT and kwargs['text'] is True
             and kwargs['encoding'] == 'utf-8' and kwargs['errors'] == 'replace'
             and all(env.get(key) == value for key, value in _STATUS_PROFILES[mode].items()))
    if known:
        try:
            current, created = Path(command[0]).lstat(), _STATUS_FILES[command[0]]
            known = stat.S_ISREG(current.st_mode) and all(getattr(current, field) == getattr(created, field)
                for field in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
        except OSError:
            known = False
    with _PROCESS_GUARD_LOCK:
        state['blocked_process_requests'] += 1
        if known and state['denied_startup_status_requests'][mode] == 0:
            state['denied_startup_status_requests'][mode] += 1
        else:
            state['unexpected_process_requests'] += 1
    # There is no successful/forwarding path, including the three recognized startup questions.
    raise RuntimeError('External processes are forbidden in the offline chip fixture')


def status_denials_reconcile():
    with _PROCESS_GUARD_LOCK:
        return (state['blocked_process_requests'] == sum(state['denied_startup_status_requests'].values()) <= 3
                and all(count in (0, 1) for count in state['denied_startup_status_requests'].values()))


def forbidden_network(*args, **kwargs):
    state['unexpected_network_requests'] += 1
    raise RuntimeError('Network is forbidden in the offline chip fixture')


# Only final OS boundaries are denied. Production registration, dispatch and draw stay untouched.
# Preserve the real base class for ChildProcess; deny every constructor before CPython reaches OS spawn.
subprocess.Popen.__init__ = forbidden_process
socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
controller = None
area_pointer = None
scene_before = None
messages_before = None
phase_since = STARTED


def save():
    REPORT.write_text(json.dumps(state, indent=2) + '\n')


def check(name, value):
    state['checks'][name] = value is True
    assert value is True, name


def window():
    return bpy.context.window_manager.windows[0]


def area():
    return next(a for a in window().screen.areas if a.as_pointer() == area_pointer)


def scene_stamp():
    return {'filepath': bpy.data.filepath, 'dirty': bpy.data.is_dirty,
            'objects': sorted((o.as_pointer(), o.name, o.data.as_pointer() if o.data else 0,
                               tuple(tuple(row) for row in o.matrix_world), o.hide_viewport, o.hide_render)
                              for o in bpy.data.objects),
            'data_counts': {name: len(getattr(bpy.data, name))
                            for name in ('meshes', 'materials', 'images', 'cameras', 'lights', 'collections')},
            'frame': bpy.context.scene.frame_current}


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    assert path.is_file() and path.stat().st_size > 0
    state['screenshots'].append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})


def changes_position():
    from angelo_agent import chat_view
    region = next(r for r in area().regions if r.type == 'WINDOW')
    current = chat_view._views.get(area_pointer, {})
    rect = next(h[0] for h in current.get('hits', []) if h[1] == 'changes')
    assert rect[2] > 0 and rect[3] > 0
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)


def advance(phase):
    global phase_since
    state['phase'] = phase
    phase_since = time.monotonic()
    save()


def tick():
    global controller, area_pointer, scene_before, messages_before
    try:
        assert time.monotonic() - STARTED < 105, 'Bounded chip fixture expired'
        phase = state['phase']
        if phase == 'boot':
            bpy.context.preferences.use_preferences_save = False
            bpy.context.preferences.view.show_splash = False
            addon_utils.enable('angelo_agent', default_set=False, persistent=False)
            advance('prepare')
            return .2
        from angelo_agent import ui, chat_view, console
        if phase == 'prepare':
            if not qa_boot.started():
                return .1
            check('registered_product_execute_present', callable(ui.ANGELO_OT_changes.__dict__.get('execute')))
            check('startup_status_constructors_denied', status_denials_reconcile())
            check('no_startup_process_or_network', state['unexpected_process_requests'] == 0 and
                  state['unexpected_network_requests'] == 0)
            bpy.context.preferences.view.language = 'en_US'
            controller = ui.get_controller()
            from angelo_agent.chat_settings import normalize_models
            controller.models = normalize_models([{'model': 'offline-chip-model',
                                                   'displayName': 'Offline chip model', 'isDefault': True}])
            added = []
            for i in range(37):
                mesh = bpy.data.meshes.new('Chip fixture mesh %02d' % i)
                mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
                obj = bpy.data.objects.new('Added object %02d' % i, mesh)
                bpy.context.scene.collection.objects.link(obj)
                added.append({'name': obj.name, 'type': obj.type})
            removed = []
            for i in range(2):
                obj = bpy.data.objects.new('Removed object %02d' % i, None)
                bpy.context.scene.collection.objects.link(obj)
                removed.append({'name': obj.name, 'type': obj.type})
                bpy.data.objects.remove(obj, do_unlink=True)
            controller.state['messages'][:] = [{'role': 'activity', 'name': 'angelo_scene_program',
                'status': 'completed', 'text': 'Read-backs from isolated fixture-owned scene changes',
                'call_id': 'change-chip-disclosure-fixture',
                'result': {'changes': {'added': added, 'removed': removed, 'modified': [], 'truncated': False}}}]
            controller.version += 1
            state['expected_added_names'] = [r['name'] for r in added]
            state['expected_removed_names'] = [r['name'] for r in removed]
            target = max(window().screen.areas, key=lambda a: a.width * a.height)
            target.type = 'CONSOLE'
            target.spaces.active.language = console.language_id
            area_pointer = target.as_pointer()
            chat_view.ensure_input(window(), target)
            target.tag_redraw()
            advance('wait_hit')
        elif phase == 'wait_hit':
            assert time.monotonic() - phase_since < 10, 'Actual changes hit did not appear'
            view = chat_view._views.get(area_pointer, {})
            hit = next((h for h in view.get('hits', []) if h[1] == 'changes'), None)
            if hit is None:
                area().tag_redraw()
                return .05
            rect = hit[0]
            check('actual_rendered_changes_hit', rect[2] > 0 and rect[3] > 0)
            capture('chip-1-rendered-hit.png')
            scene_before = scene_stamp()
            messages_before = json.dumps(controller.state['messages'], sort_keys=True)
            state['source_ui'] = str(Path(ui.__file__).resolve())
            state['chip_rect'] = list(rect)
            state['presses_before'] = len(qa_input.sent)
            # The helper activates on one pass and recomputes the actual hit before a later press.
            qa_input.press(window(), changes_position)
            advance('capture_popup')
            return .8
        elif phase == 'capture_popup':
            check('native_mouse_sequence_sent_to_changes_hit', qa_input.pending() == 0 and
                  len(qa_input.sent) == state['presses_before'] + 1)
            state['actual_press'] = list(qa_input.sent[-1])
            capture('chip-2-popup-for-independent-review.png')
            check('popup_frame_differs_from_chip_frame', state['screenshots'][0]['sha256'] != state['screenshots'][1]['sha256'])
            check('opening_keeps_scene_and_chat', scene_stamp() == scene_before and
                  json.dumps(controller.state['messages'], sort_keys=True) == messages_before)
            window().event_simulate(type='ESC', value='PRESS')
            window().event_simulate(type='ESC', value='RELEASE')
            check('native_escape_sent', True)
            advance('after_escape')
            return .5
        elif phase == 'after_escape':
            capture('chip-3-after-escape.png')
            check('escape_keeps_scene_and_chat', scene_stamp() == scene_before and
                  json.dumps(controller.state['messages'], sort_keys=True) == messages_before)
            check('no_provider_or_auth_process', not any(c.provider for c in ui._controllers.values()) and
                  not ui._authentication.processes and not ui._authentication.running)
            check('no_auth_worker_thread', not any(t.name == 'AngeloNativeAuthentication' and t.is_alive()
                                                  for t in threading.enumerate()))
            check('all_process_constructors_denied', status_denials_reconcile())
            check('zero_external_process_or_network', state['unexpected_process_requests'] == 0 and
                  state['unexpected_network_requests'] == 0)
            check('three_actual_screenshots', len(state['screenshots']) == 3)
            state['phase'] = 'complete'
            state['success'] = all(value is True for value in state['checks'].values())
            save()
            bpy.ops.wm.quit_blender()
            return None
        save()
        return .1
    except Exception:
        state['errors'].append({'phase': state['phase'], 'traceback': traceback.format_exc()})
        try:
            capture('chip-failure.png')
        except Exception as error:
            state['capture_error'] = type(error).__name__
        save()
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(qa_boot.gated(qa_input.after_clicks(tick)), first_interval=.5)
