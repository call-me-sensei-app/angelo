# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real native notice hits/registered operators; final auth process and browser boundaries are intercepted.

No real CLI, credential, browser login, provider request or inference. The team account owns a fresh loopback
listener only; no callback/code is delivered and its transport refuses every request. Pixel acceptance is
separate from this fixture's structural report and requires inspecting the saved native screenshots.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: F401 First: the actual native process gets a fresh isolated profile.

import json
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path

import bpy
from angelo_agent import authentication, product_env
from angelo_agent.chat_settings import normalize_models
import qa_auth
import qa_boot
import qa_hit

OFFLINE_MODELS = normalize_models([{'model': 'offline-fixture-model', 'displayName': 'Offline fixture model',
                                   'isDefault': True}])
product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-signin-reconnect'))
OUT.mkdir(parents=True, exist_ok=True)
OFFLINE_BIN = OUT / 'offline-bin'
OFFLINE_BIN.mkdir(exist_ok=False)
for name in ('codex', 'claude'):
    executable = OFFLINE_BIN / name
    executable.write_text('#!/bin/sh\nexit 1\n')
    executable.chmod(0o700)
os.environ['PATH'] = str(OFFLINE_BIN) + os.pathsep + os.environ.get('PATH', '')
REPORT = OUT / 'signin-reconnect.json'
STARTED = time.monotonic()
state = dict(success=False, phase='boot', checks={}, screenshots=[], auth_calls=[], browser_callbacks=[],
             intercept_violations=[], startup_status={},
             network_requests=0, unexpected_process_requests=0, blocked_process_attempts=[], actual_pixels_accepted=False, account_login_acceptance=False,
             scope='Actual registered GUI/canvas click, isolated synthetic status, final auth/browser intercepted')
pending = []
owned_team = None
controller = None
record = None
expected_profile = None
owned_connections = None
auth_threads = []
OriginalThread = threading.Thread


class RecordedThread(OriginalThread):
    def __init__(self, *args, **kwargs):
        # Not super(): threading.Timer (any Thread subclass) calls ``Thread.__init__(self)``, which is this method once
        # threading.Thread is replaced, on an object that is no RecordedThread.
        OriginalThread.__init__(self, *args, **kwargs)
        if self.name == 'AngeloNativeAuthentication':
            auth_threads.append(self)


threading.Thread = RecordedThread  # Real targets and scheduling; retain thread objects for final joins.


def forbidden_process(*args, **kwargs):
    state['unexpected_process_requests'] += 1
    if len(state['blocked_process_attempts']) < 8:
        command = args[0] if args else kwargs.get('args', ())
        executable = command[0] if isinstance(command, (list, tuple)) and command else ''
        name = Path(str(executable)).name
        kind = ('system-shell:' + name if name in {'sh', 'bash', 'zsh'} else
                'fixture-cli' if Path(str(executable)).parent == OFFLINE_BIN else 'other-executable')
        state['blocked_process_attempts'].append(dict(executable_class=kind, callers=[
            dict(module=Path(frame.filename).name, function=frame.name, line=frame.lineno)
            for frame in traceback.extract_stack(limit=7)[:-1]]))
    raise RuntimeError('A non-auth subprocess was requested in an offline GUI fixture')


# macOS child_process.popen/cli_runtime/protocol resolve subprocess.Popen per call. Auth is intercepted below.
# This also blocks login-shell discovery or a missed model/app-server launch rather than letting it escape.
subprocess.Popen = forbidden_process


def violation(text):
    """A broken expectation in code Angelo calls (its auth worker, inside its own ``except``): recorded, so the suite
    fails on it at its next tick with this text, then raised, so the call goes no further. An ``assert`` here became
    a "Native runtime unavailable" status and the row silently never appeared (test_native_suite_owned_status)."""
    state['intercept_violations'].append(text)
    raise RuntimeError(text)


# The start-up status checks answer this late on purpose: the boot must wait for their completion signal
# (qa_auth.startup_settled), never a tick count (the force-late-workers lesson).
LATE_STARTUP_SECONDS = 1.0


class InterceptedProcess:
    def __init__(self, command, env):
        self.returncode = None
        self.command = list(command)
        self.gate = threading.Event()
        self.login = 'login' in command and 'status' not in command
        self.output = 'Logged in using ChatGPT'
        home = env.get('CODEX_HOME', '')
        private = bool(expected_profile is not None and Path(home).resolve() == expected_profile)
        if self.login:
            if not private:
                violation('A notice must reconnect only its private fixture profile')
            if owned_connections is None or not Path(home).resolve().is_relative_to(owned_connections):
                violation('A reconnect must keep its profile inside the suite-owned connections folder')
            pending.append(self)
        else:
            # Startup status checks remain signed out; fixture-owned completion rechecks are released explicitly.
            if private:
                pending.append(self)
            else:
                late = threading.Timer(LATE_STARTUP_SECONDS, self.finish, (1,))
                late.daemon = True
                late.start()
        state['auth_calls'].append(dict(kind='login' if self.login else 'status', private_profile=private,
                                       intercepted=True, connection_id=record['id'] if private else None))

    def communicate(self, input=None, timeout=None):
        if input is not None:
            violation('No API key belongs in this GUI fixture')
        if not self.gate.wait(min(timeout or 20, 20)):
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.output, None

    def poll(self):
        return self.returncode

    def finish(self, code):
        self.returncode = code
        self.gate.set()

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


def intercepted_launch(command, **kwargs):
    # Runs on the product's worker, whose ``except Exception`` turns any error here into "Native runtime unavailable":
    # every failure is recorded (the tick fails on the record) before the worker sees it.
    try:
        return _intercepted_launch(command, **kwargs)
    except BaseException as error:
        if not state['intercept_violations'] or state['intercept_violations'][-1] != str(error):
            state['intercept_violations'].append('%s: %s' % (type(error).__name__, str(error)[:300]))
        raise


def _intercepted_launch(command, **kwargs):
    if Path(command[0]).resolve() not in {(OFFLINE_BIN / name).resolve() for name in ('codex', 'claude')}:
        violation('Auth must resolve only fixture-owned executables')
    if 'login' not in command and 'auth' not in command:
        violation('Unexpected auth command')
    if '--with-api-key' in command:
        violation('No API key belongs in this GUI fixture')
    # Only a browser sign-in keeps the windowed programs it starts; a status check does not (46804fbd49c).
    sign_in = 'login' in command and 'status' not in command
    if kwargs.get('keep_gui_programs') is not authentication.browser_sign_in(sign_in, False):
        violation('keep_gui_programs must be %s for a %s' % (authentication.browser_sign_in(sign_in, False),
                                                            'browser sign-in' if sign_in else 'status check'))
    return InterceptedProcess(command, kwargs['env'])


authentication.launch = intercepted_launch


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def hits():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {}).get('hits', [])


def has(action, value=None):
    return any(a == action and (value is None or v == value) for _, a, v in hits())


def click(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in hits() if a == action and (value is None or v == value))
    from qa_input import chat_point   # The chat's points as window pixels (its scale, right to left mirrored).
    x, y = chat_point(region, rect)
    assert rect[2] > 0 and rect[3] > 0
    import qa_input
    if action in qa_input.GRANT_ACTIONS:   # It reaches a grant: only a person's click (move, then press).
        return qa_input.press(window(), x, y)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state['screenshots'].append(str(path))


def check(name, condition):
    state['checks'][name] = bool(condition)
    assert condition, name


def wait(condition, seconds=10):
    if condition():
        state.pop('waiting', None)
        return True
    state.setdefault('waiting', time.monotonic())
    assert time.monotonic() - state['waiting'] < seconds, 'Timed out in ' + state['phase']
    return False


def save():
    REPORT.write_text(json.dumps(state, indent=2) + '\n')


def failure_context():
    from angelo_agent import chat_notices, chat_view, notice_kinds, ui
    area, region = chat()
    canvas = chat_view._views.get(area.as_pointer(), {})
    messages = controller.state['messages'] if controller is not None else []
    latest = messages[-1] if messages else None
    actions = chat_notices._card(latest, 0, True, ui, controller, set())['actions'] if latest and controller else ()
    return dict(editor=area.type, language=area.spaces.active.language, width=region.width, height=region.height,
                controller_is_current=controller is not None and ui.get_controller() is controller,
                selection_is_record=bool(controller and record and controller.selection_connection_id == record['id']),
                selection_provider_matches=bool(controller and record and controller.selection_provider == record['mode']),
                next_route_present=bool(controller and controller.state.get('next_route')),
                message_count=len(messages), message_roles=[m.get('role') for m in messages[-8:]],
                latest_notice_kind=notice_kinds.kind(latest) if latest else None,
                computed_notice_actions=[a[0] for a in actions[:8]],
                drawn_hit_actions=sorted({a for _, a, _ in hits()})[:40],
                drawn_reconnect_is_record=bool(record and has('welcome_reconnect', record['id'])),
                canvas_session_matches=bool(controller and canvas.get('session') == controller.state['id']),
                draw_count=chat_view.draw_count, draw_error=str(chat_view.last_error or '')[:600])


def finish(error=None):
    if error is not None:
        try:
            state['failure_context_before_capture'] = failure_context()
        except Exception as diagnostic_error:
            state['failure_context_before_error'] = type(diagnostic_error).__name__ + ': ' + str(diagnostic_error)[:300]
        try:
            capture('signin-failure-' + state['phase'] + '.png')
        except Exception as diagnostic_error:
            state['failure_capture_error'] = type(diagnostic_error).__name__ + ': ' + str(diagnostic_error)[:300]
        try:
            state['failure_context_after_capture'] = failure_context()
        except Exception as diagnostic_error:
            state['failure_context_after_error'] = type(diagnostic_error).__name__ + ': ' + str(diagnostic_error)[:300]
    for process in pending:
        if process.poll() is None:
            process.terminate()
    from angelo_agent import ui
    ui._authentication.close()
    deadline = time.monotonic() + 5
    for thread in auth_threads:
        if thread.ident is not None:
            thread.join(max(0, deadline - time.monotonic()))
    state['auth_worker_threads'] = [dict(name=t.name, ident=t.ident, alive=t.is_alive()) for t in auth_threads]
    state['checks']['auth_worker_threads_recorded_and_joined'] = bool(auth_threads) and all(not t.is_alive() for t in auth_threads)
    state['checks']['no_non_auth_process_launch'] = state['unexpected_process_requests'] == 0
    state['checks']['no_intercept_violation'] = not state['intercept_violations']
    ui.catalogs().close()
    if owned_team is not None:
        owned_team.cancel_sign_in()
        owned_team.close()
        state['team_worker_drained'] = owned_team.join(2)
    if error is not None:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase='complete', success=all(state['checks'].values()) and state.get('team_worker_drained') is True)
    state['seconds'] = time.monotonic() - STARTED
    state['owned_auth_processes_are_intercepted'] = True
    save()
    print('ANGELO_SIGNIN_RECONNECT', json.dumps({'success': state['success'], 'phase': state['phase']}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    global controller, record, owned_team, expected_profile, owned_connections
    try:
        assert time.monotonic() - STARTED < 120, 'Bounded GUI fixture expired'
        assert not state['intercept_violations'], 'Intercept violation: ' + '; '.join(state['intercept_violations'])
        from angelo_agent import ui, director, chat_welcome, quick_connect, console, team_account_ui
        from angelo_agent.team_account import TeamAccount
        from angelo_agent.team_view import SIGNED_OUT, SIGNING_IN, OFFLINE
        phase = state['phase']
        if phase == 'boot':
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            # Every start-up check's result applied (held late on purpose: LATE_STARTUP_SECONDS), so none can replace
            # the status this suite sets below.
            if not wait(lambda: qa_auth.startup_settled(ui._authentication), 20):
                return .2
            assert state['unexpected_process_requests'] == 0, 'Blocked startup process; closed caller evidence retained'
            startup = [c for c in state['auth_calls'] if c['kind'] == 'status' and not c['private_profile']]
            # Every start-up check started and ran a fixture CLI, never a host one: codex_api's too, whose private profile
            # C refused while the checks ran before install_app (ProfileOutside; test_native_startup_checks_order).
            with ui._authentication.lock:
                started = [mode for mode in qa_auth.STARTUP_MODES if mode in ui._authentication.modes]
            state['startup_not_started'] = {mode: qa_auth.status_class(ui._authentication.status.get(mode))
                                            for mode in qa_auth.STARTUP_MODES if mode not in started}
            check('startup_checks_ran_the_fixture_clis', started == list(qa_auth.STARTUP_MODES)
                  and len(startup) == len(started))
            bpy.context.preferences.view.language = 'en_US'
            # No Preferences codex_path: a script's write of an executable path is refused (grant_guard.setting,
            # f50e61c4669; only the person's click sets it). The CLI is found as a person's is, on PATH, where
            # OFFLINE_BIN is first, and intercepted_launch refuses any other executable.
            controller = ui.get_controller()
            assert not controller.busy and not controller.pending
            record = ui.connections().add('OPENAI', 'SUBSCRIPTION', name='Isolated reconnect fixture', reuse_native=False)
            models = OFFLINE_MODELS
            controller.state.update(provider=record['mode'], connection_id=record['id'], native_id=None)
            controller._next_binding = None
            controller.models = models
            controller.settings = ui.connection_settings(record['mode'], record=record)
            from angelo_agent.storage import native_profile_directory
            assert controller.settings['reuse_codex_login'] is False
            assert controller.settings['connection_id'] == record['id'] and record.get('native_profile') is None
            expected_profile = native_profile_directory(controller.settings['auth_directory'], 'codex-profile',
                                                        controller.settings['connection_id']).resolve()
            owned_connections = Path(os.environ['ANGELO_CONNECTIONS_DIR']).resolve()
            assert owned_connections == OUT.resolve().parent / 'connections'
            assert Path(ui.connection_directory()).resolve() == owned_connections
            assert Path(controller.settings['auth_directory']).resolve() == owned_connections
            assert expected_profile == owned_connections / 'codex-profile' / record['id']
            controller.connection_error = False
            ui.catalogs().seed(record['id'], models)
            ui._authentication.status[record['id']] = 'Connected to ChatGPT'  # The stale cache that exposed the bug.
            # The row "Sign in again" needs the mode's CLI found (first_run.connect_options): the suite owns that
            # status, a signed-out CLI's, whatever the machine's own discovery or the start-up check said (recorded).
            qa_auth.own_status(ui._authentication, {'codex_subscription': 'Not connected'}, state['startup_status'])
            controller.state['messages'][:] = [{'role': 'user', 'text': 'Synthetic expired sign-in request'},
                                               {'role': 'error', 'text': '401 Unauthorized: token expired'}]
            controller.version += 1
            old_team = team_account_ui._account
            if old_team is not None:
                old_team.close()
                assert old_team.join(2)
            def refuse_network(*args, **kwargs):
                state['network_requests'] += 1
                raise AssertionError('No team HTTP request is allowed')
            owned_team = TeamAccount('https://example.invalid', transport=refuse_network, sign_in_timeout=15)
            team_account_ui._account = owned_team
            def browser_boundary(url):
                state['browser_callbacks'].append({'intercepted': True})  # Never persist PKCE state/query/URLs.
                return True
            team_account_ui.open_in_browser = browser_boundary
            console.redraw_chats(ui._controllers.values())
            state['phase'] = 'initial_notice'
            return .3
        if phase == 'initial_notice':
            if not wait(lambda: has('welcome_reconnect', record['id']) and has('welcome_team')):
                return .2
            check('stale_status_is_connected', ui.connection_ready(record))
            check('notice_has_exact_profile_sign_in_again', has('welcome_reconnect', record['id']))
            check('notice_has_your_team', has('welcome_team'))
            capture('signin-1-expired-notice.png')
            click('welcome_reconnect', record['id'])
            state['phase'] = 'progress'
            return .3
        if phase == 'progress':
            flow = quick_connect.reconnect_flow(controller)
            if not wait(lambda: flow is not None and flow['phase'] == 'signing_in' and len(pending) >= 1
                        and has('welcome_reconnect_cancel', flow['token'])):
                return .2
            check('registered_signin_starts_private_profile_login', pending[-1].login)
            check('login_attempt_is_exact_owner', flow['reconnect_attempt'] == ui._authentication.attempt(record['id']))
            check('progress_body_visible', 'Finish signing in to ChatGPT' in chat_welcome.sign_in_body(ui, controller, ''))
            check('cancel_uses_exact_flow_token', has('welcome_reconnect_cancel', flow['token']))
            capture('signin-2-visible-progress.png')
            state['first_token'] = flow['token']
            state['first_attempt'] = flow['reconnect_attempt']
            click('welcome_reconnect_cancel', flow['token'])
            state['phase'] = 'cancelled'
            return .3
        if phase == 'cancelled':
            if not wait(lambda: record['id'] not in ui._authentication.running and quick_connect.reconnect_flow(controller) is None):
                return .2
            check('cancelled_auth_cannot_be_ready', not ui.connection_ready(record))
            check('cancel_preserves_saved_connection', record['id'] in ui.connections().records)
            if not wait(lambda: has('welcome_reconnect', record['id'])):
                return .2
            click('welcome_reconnect', record['id'])
            state['phase'] = 'force_failure'
            return .3
        if phase == 'force_failure':
            flow = quick_connect.reconnect_flow(controller)
            if not wait(lambda: flow is not None and flow['phase'] == 'signing_in' and len(pending) >= 2):
                return .2
            check('retry_owns_new_attempt', flow['reconnect_attempt'] != state['first_attempt'])
            pending[-1].finish(1)
            state['phase'] = 'failure_visible'
            return .3
        if phase == 'failure_visible':
            flow = quick_connect.reconnect_flow(controller)
            if not wait(lambda: flow is not None and flow['phase'] == 'failed' and has('welcome_reconnect', record['id'])):
                return .2
            check('failure_reason_visible', "Sign-in didn't finish" in chat_welcome.sign_in_body(ui, controller, ''))
            check('failure_does_not_create_or_switch_connection', controller.selection_connection_id == record['id'])
            check('failed_request_not_resent', len(controller.state['messages']) == 2 and not controller.busy)
            capture('signin-3-actionable-failure.png')
            click('welcome_reconnect', record['id'])
            state['phase'] = 'successful_login'
            return .3
        if phase == 'successful_login':
            flow = quick_connect.reconnect_flow(controller)
            if not wait(lambda: flow is not None and flow['phase'] == 'signing_in' and len(pending) >= 3):
                return .2
            state['successful_attempt'] = flow['reconnect_attempt']
            pending[-1].finish(0)
            state['phase'] = 'status_recheck'
            return .2
        if phase == 'status_recheck':
            if not wait(lambda: len(pending) >= 4 and not pending[-1].login):
                return .1
            check('completion_recheck_retains_attempt', ui._authentication.attempt(record['id']) == state['successful_attempt'])
            ui.catalogs().seed(record['id'], OFFLINE_MODELS)
            pending[-1].finish(0)
            state['phase'] = 'completed'
            return .3
        if phase == 'completed':
            if not wait(lambda: record['id'] not in ui._authentication.running and quick_connect.reconnect_flow(controller) is None):
                return .2
            check('completion_is_ready_on_same_profile', ui.connection_ready(record) and controller.selection_connection_id == record['id'])
            check('completion_does_not_resend_failed_request', len(controller.state['messages']) == 2 and not controller.busy)
            check('all_auth_launches_intercepted', all(call['intercepted'] for call in state['auth_calls']))
            capture('signin-4-ready-same-profile.png')
            if not wait(lambda: has('welcome_team')):
                return .2
            click('welcome_team')
            state['phase'] = 'team_browser'
            return .3
        if phase == 'team_browser':
            if not wait(lambda: owned_team.view().status == SIGNING_IN and len(state['browser_callbacks']) == 1):
                return .2
            check('your_team_actual_operator_starts_owned_listener', owned_team._listener is not None)
            check('your_team_browser_boundary_intercepted', state['browser_callbacks'][0]['intercepted'])
            check('your_team_makes_no_console_request', state['network_requests'] == 0)
            capture('signin-5-team-browser-intercepted.png')
            bpy.ops.angelo.team_cancel_sign_in('EXEC_DEFAULT')  # Drain only this fixture-owned listener, no browser was opened.
            state['phase'] = 'team_cancelled'
            return .3
        if phase == 'team_cancelled':
            if not wait(lambda: owned_team.view().status == SIGNED_OUT and not owned_team._pending):
                return .2
            check('team_listener_cancelled', owned_team._listener is None)
            with owned_team._lock:
                owned_team._last['refresh'] = owned_team._monotonic()  # A just-failed refresh waits RETRY_EVERY.
                owned_team._update(status=OFFLINE, message='Synthetic offline status')
            state['phase'] = 'team_settings_click'
            return .3
        if phase == 'team_settings_click':
            if not wait(lambda: has('welcome_team') and has('welcome_reconnect', record['id'])):
                return .2
            assert owned_team.view().status == OFFLINE, 'Settings click must use the intended cached offline state'
            state['before_preferences'] = [w.as_pointer() for w in bpy.context.window_manager.windows]
            click('welcome_team')
            state['phase'] = 'team_settings_open'
            return .3
        if phase == 'team_settings_open':
            windows = [w for w in bpy.context.window_manager.windows if w.as_pointer() not in state['before_preferences']]
            if not wait(lambda: any(a.type == 'PREFERENCES' for w in windows for a in w.screen.areas)):
                return .2
            assert len(state['browser_callbacks']) == 1, 'Offline settings must not start another team sign-in'
            check('your_team_offline_opens_actual_preferences', bool(windows))
            check('no_real_account_or_provider_request', state['network_requests'] == 0)
            check('original_failed_request_still_not_resent', len(controller.state['messages']) == 2 and not controller.busy)
            with bpy.context.temp_override(window=windows[0]):
                path = OUT / 'signin-6-team-settings.png'
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
                bpy.ops.screen.screenshot(filepath=str(path))
                state['screenshots'].append(str(path))
                bpy.ops.wm.window_close()
            finish()
            return None
        return .2
    except Exception as error:
        finish(error)
        return None


import qa_input  # noqa: E402  (a click on its way holds the tick: qa_input.after_clicks)
bpy.app.timers.register(qa_boot.gated(qa_input.after_clicks(tick)), first_interval=.5)
