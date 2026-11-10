# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Expired-token notice -> real welcome router -> settings operator -> native auth owner.

Only Blender's module boundary and the final process launch are substituted. The operator body is compiled
unchanged from ui.py because importing the entire registered UI requires Blender; actual registered UI/pixels
remain a separate GUI gate. No provider process, browser, account store or credential is accessed.
"""
import suite_isolation  # noqa: F401 First: no real provider credentials.

import ast
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from test_native_first_run import FakeUI
from test_native_welcome_card import fake_modules
import angelo_agent
from angelo_agent import authentication

ADDON = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent'


class SigninReconnectTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.modules = fake_modules()
        self.team = NS(welcome_row=lambda: {'label': 'Your team', 'action': 'welcome_team', 'value': ''},
                       team_only_options=lambda rows: rows, signing_in=lambda: False)
        self.modules['angelo_agent.team_account_ui'] = self.team
        self.patch = mock.patch.dict(sys.modules, self.modules)
        self.patch.start()
        self.team_patch = mock.patch.object(angelo_agent, 'team_account_ui', self.team, create=True)
        self.team_patch.start()
        self.previous = {n: sys.modules.pop(n, None) for n in
                         ('angelo_agent.chat_welcome', 'angelo_agent.quick_connect', 'angelo_agent.chat_notices')}
        self.welcome = importlib.import_module('angelo_agent.chat_welcome')
        self.notices = importlib.import_module('angelo_agent.chat_notices')
        self.ui = FakeUI(self.scratch.name, {'codex_subscription': 'Connected to ChatGPT'})
        self.ui.state_directory = lambda: self.scratch.name
        self.record = self.ui.store.add('OPENAI', 'SUBSCRIPTION', reuse_native=True)
        self.controller = self.ui.controller
        self.controller.selection_provider = 'codex_subscription'
        self.controller.selection_connection_id = self.record['id']
        self.controller.state['connection_id'] = self.record['id']
        self.controller.delegate_jobs = {}
        self.ui._authentication = authentication.NativeAuthentication()
        self.ui._authentication.status['codex_subscription'] = 'Connected to ChatGPT'
        self.reports = []
        tree = ast.parse((ADDON / 'ui.py').read_text())
        operator = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                        and n.name == 'ANGELO_OT_connection_signin')
        self.globals = dict(Operator=type('Operator', (), {'report': lambda obj, level, text: self.reports.append(text)}),
                            StringProperty=lambda **_: None, connections=self.ui.connections,
                            _controllers=self.ui._controllers, _authentication=self.ui._authentication,
                            connection_settings=lambda mode, record: {'connection_id': record['id'],
                                'reuse_codex_login': record['reuse_native'], 'auth_directory': self.scratch.name},
                            state_directory=lambda: self.scratch.name, preferences_ui=NS(redraw=mock.Mock()),
                            rpt_=lambda text: text, error_message=str)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[operator], type_ignores=[])),
                     str(ADDON / 'ui.py'), 'exec'), self.globals)
        self.op = self.globals['ANGELO_OT_connection_signin']()
        self.results = []
        def invoke(*args, connection_id):
            self.op.connection_id = connection_id
            result = self.op.execute(NS())
            self.results.append(result)
            return result
        self.modules['bpy'].ops = NS(angelo=NS(connection_signin=invoke,
                                             quick_connect_cancel=lambda **kw: self.welcome.quick_connect.cancel(self.ui, kw["mode"], token=kw["flow_token"]),
                                             quick_connect=mock.Mock(side_effect=AssertionError('must reconnect, not Use'))))
        self.fake_ui_module = self.ui
        self.ui_patch = mock.patch.dict(sys.modules, {'angelo_agent.ui': self.fake_ui_module})
        self.ui_patch.start()
        self.package_ui_patch = mock.patch.object(angelo_agent, 'ui', self.fake_ui_module, create=True)
        self.package_ui_patch.start()

    def tearDown(self):
        self.package_ui_patch.stop()
        self.ui_patch.stop()
        self.team_patch.stop()
        for name, previous in self.previous.items():
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous
        self.welcome.quick_connect.reset()
        self.patch.stop()
        self.scratch.cleanup()

    def action(self):
        with mock.patch.object(self.welcome.first_run, 'built_in', return_value=True):
            card = self.notices._card({'role': 'error', 'text': '401 Unauthorized: authentication token expired'},
                                      7, True, self.ui, self.controller, set())
        return next(row for row in card['actions'] if row[0] != 'welcome_team')

    def dispatch(self):
        action, value, label, style = self.action()
        self.welcome.dispatch(NS(), self.controller, {}, action, value)

    def test_stale_connected_notice_targets_saved_connection_not_mode(self):
        action, value, label, style = self.action()
        self.assertEqual((action, value, label), ('welcome_reconnect', self.record['id'], 'Sign in again'))
        self.assertNotEqual(value, 'codex_subscription')

    def test_old_notice_has_no_reconnect_action(self):
        card = self.notices._card({'role': 'error', 'text': '401 Unauthorized'}, 7, False,
                                  self.ui, self.controller, set())
        self.assertEqual(card['actions'], ())

    def test_real_auth_owner_is_called_despite_cached_ready(self):
        auth = self.ui._authentication
        with mock.patch.object(auth, 'start', wraps=auth.start) as start, \
             mock.patch.object(authentication, 'native_auth_command', return_value=(['never-launched', 'login'], {})), \
             mock.patch.object(authentication.threading, 'Thread'):
            self.dispatch()
        self.assertEqual(self.results, [{'FINISHED'}])
        self.assertEqual(start.call_args.kwargs, {'login': True, 'identity': 'codex_subscription'})
        self.assertEqual(auth.status['codex_subscription'], 'Complete sign-in in your browser…')
        self.assertFalse(self.ui.connection_ready(self.record))
        self.assertEqual(len(self.ui.store.records), 1)

    def test_private_profile_is_reconnected_even_with_other_native_profile(self):
        private = self.ui.store.add('OPENAI', 'SUBSCRIPTION', reuse_native=False)
        self.record = private
        self.controller.selection_connection_id = private['id']
        self.controller.state['connection_id'] = private['id']
        with mock.patch.object(self.ui._authentication, 'start') as start:
            self.dispatch()
        self.assertEqual(start.call_args.kwargs, {'login': True, 'identity': private['id']})
        self.assertEqual(start.call_args.args[1]['connection_id'], private['id'])
        self.assertFalse(start.call_args.args[1]['reuse_codex_login'])

    def test_same_connection_active_turn_refuses_without_login(self):
        self.controller.busy = True
        with mock.patch.object(self.ui._authentication, 'start') as start:
            self.dispatch()
        start.assert_not_called()
        self.assertEqual(self.results, [{'CANCELLED'}])
        self.assertIn('active turns', self.reports[0])

    def test_removed_connection_does_not_fall_back_to_native_profile(self):
        self.ui.store.remove(self.record['id'])
        with mock.patch.object(self.ui._authentication, 'start') as start:
            with self.assertRaises(StopIteration):
                self.action()
        start.assert_not_called()

    def test_managed_team_only_policy_does_not_offer_personal_reconnect(self):
        self.team.team_only_options = lambda rows: [r for r in rows if r['id'] == 'team']
        with self.assertRaises(StopIteration):
            self.action()

    def test_real_auth_command_and_success_event_require_status_recheck(self):
        auth = self.ui._authentication
        launched = []
        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        class FinishedProcess:
            returncode = 0
            def communicate(self, **kwargs):
                return ('browser output is deliberately not a status result', None)
            def poll(self):
                return self.returncode
        def launch(command, **kwargs):
            launched.append((command, kwargs))
            return FinishedProcess()
        with mock.patch.object(authentication, 'find_executable', return_value='/reviewed/codex'), \
             mock.patch.object(authentication, 'child_environment', return_value={}), \
             mock.patch.object(authentication, 'profile_environment', side_effect=lambda env, *args: env), \
             mock.patch.object(authentication, 'with_runtime_path', side_effect=lambda env, exe: env), \
             mock.patch.object(authentication, 'launch', side_effect=launch), \
             mock.patch.object(authentication.threading, 'Thread', ImmediateThread):
            self.dispatch()
        self.assertEqual(launched[0][0], ['/reviewed/codex', 'login'])
        self.assertTrue(launched[0][1]['keep_gui_programs'])
        self.assertEqual([(identity, done) for identity, done, _ in auth.pump()], [('codex_subscription', True)])
        self.assertEqual(auth.status['codex_subscription'], 'Sign-in complete. Refresh status to confirm.')
        self.assertFalse(self.ui.connection_ready(self.record))
        self.assertEqual(authentication.metadata_status('codex_subscription', 0, 'Logged in using ChatGPT'),
                         'Connected to ChatGPT')

    def test_real_command_private_profile_keeps_its_own_directory(self):
        private = self.ui.store.add('OPENAI', 'SUBSCRIPTION', reuse_native=False)
        with mock.patch.object(authentication, 'find_executable', return_value='/reviewed/codex'), \
             mock.patch.object(authentication, 'child_environment', return_value={'CODEX_SQLITE_HOME': '/foreign'}), \
             mock.patch.object(authentication, 'with_runtime_path', side_effect=lambda env, exe: env):
            command, env = authentication.native_auth_command('codex_subscription',
                {'connection_id': private['id'], 'reuse_codex_login': False}, self.scratch.name, login=True)
        self.assertEqual(command, ['/reviewed/codex', 'login'])
        self.assertTrue(Path(env['CODEX_HOME']).is_relative_to(self.scratch.name))
        self.assertIn(private['id'], env['CODEX_HOME'])
        self.assertNotIn('CODEX_SQLITE_HOME', env)

    def start_offline_reconnect(self):
        with mock.patch.object(authentication, 'native_auth_command', return_value=(['never-launched', 'login'], {})), \
             mock.patch.object(authentication.threading, 'Thread'):
            self.dispatch()

    def test_running_reconnect_has_visible_progress_and_cancel(self):
        self.start_offline_reconnect()
        self.welcome.quick_connect.pump(self.ui)
        self.assertIn('Finish signing in to ChatGPT', self.welcome.sign_in_body(self.ui, self.controller, 'old notice'))
        action, token, label, style = self.action()
        self.assertEqual((action, label), ('welcome_reconnect_cancel', 'Cancel'))
        self.assertEqual(token, self.welcome.quick_connect.reconnect_flow(self.controller)['token'])

    def test_failed_reconnect_has_visible_retry_without_another_auth_start(self):
        self.start_offline_reconnect()
        auth = self.ui._authentication
        auth.running.discard('codex_subscription')
        auth.status['codex_subscription'] = 'Sign-in did not complete. Retry or use the native Terminal command.'
        with mock.patch.object(auth, 'start') as start:
            self.welcome.quick_connect.pump(self.ui)
        start.assert_not_called()
        self.assertIn("Sign-in didn't finish", self.welcome.sign_in_body(self.ui, self.controller, 'old notice'))
        self.assertEqual(self.action()[:3], ('welcome_reconnect', self.record['id'], 'Connect ChatGPT'))

    def test_cancel_targets_only_captured_private_auth_identity(self):
        private = self.ui.store.add('OPENAI', 'SUBSCRIPTION', reuse_native=False)
        self.record = private
        self.controller.selection_connection_id = private['id']
        self.controller.state['connection_id'] = private['id']
        self.start_offline_reconnect()
        self.assertEqual(self.action()[0], 'welcome_reconnect_cancel')
        self.cancelled_attempt = self.welcome.quick_connect.reconnect_flow(self.controller)['reconnect_attempt']
        with mock.patch.object(self.ui._authentication, 'cancel') as cancel:
            self.dispatch()
        cancel.assert_called_once_with(private['id'], attempt=self.cancelled_attempt)
        self.assertEqual(len(self.ui.store.records), 2)
        self.assertIsNone(self.welcome.quick_connect.reconnect_flow(self.controller))

    def test_changed_selection_cannot_be_rebound_after_login(self):
        self.start_offline_reconnect()
        self.controller.selection_connection_id = 'another-profile'
        auth = self.ui._authentication
        auth.running.discard('codex_subscription')
        auth.status['codex_subscription'] = 'Connected to ChatGPT'
        self.welcome.quick_connect.pump(self.ui)
        self.assertEqual(self.ui.bound, [])
        self.assertIsNone(self.welcome.quick_connect.reconnect_flow(self.controller))

    def test_replaced_chat_controller_cannot_be_rebound(self):
        self.start_offline_reconnect()
        self.ui._controllers[self.controller.state['id']] = NS(state={'id': 'chat-1'})
        self.ui._authentication.running.clear()
        self.welcome.quick_connect.pump(self.ui)
        self.assertEqual(self.ui.bound, [])

    def test_changed_auth_profile_cannot_be_rebound(self):
        self.start_offline_reconnect()
        self.record['reuse_native'] = False
        self.ui._authentication.running.clear()
        self.ui._authentication.status[self.record['id']] = 'Connected to ChatGPT'
        self.welcome.quick_connect.pump(self.ui)
        self.assertEqual(self.ui.bound, [])

    def test_stale_notice_hit_cannot_start_another_selected_profile(self):
        action, value, label, style = self.action()
        self.controller.selection_connection_id = 'another-profile'
        with mock.patch.object(self.ui._authentication, 'start') as start:
            self.welcome.dispatch(NS(), self.controller, {}, action, value)
        start.assert_not_called()
        self.assertEqual(self.results, [])

    def test_native_profile_path_change_cannot_rebind(self):
        self.start_offline_reconnect()
        self.record['native_profile'] = '/a-different-profile'
        self.ui._authentication.running.clear()
        self.welcome.quick_connect.pump(self.ui)
        self.assertEqual(self.ui.bound, [])

    def test_completed_reconnect_binds_only_its_exact_existing_record_without_login_again(self):
        for reuse in (True, False):
            with self.subTest(reuse_native=reuse):
                record = self.record if reuse else self.ui.store.add('OPENAI', 'SUBSCRIPTION', reuse_native=False)
                self.record = record
                self.controller.selection_connection_id = record['id']
                self.controller.state['connection_id'] = record['id']
                self.start_offline_reconnect()
                identity = record['mode'] if record['reuse_native'] else record['id']
                self.ui._authentication.running.discard(identity)
                self.ui._authentication.status[identity] = 'Connected to ChatGPT'
                self.ui.catalog.entries[record['id']] = [{'id': 'ready-model'}]
                with mock.patch.object(self.ui._authentication, 'start') as start:
                    self.welcome.quick_connect.pump(self.ui)
                start.assert_not_called()
                self.assertEqual(self.ui.bound[-1], record['id'])
                self.assertIsNone(self.welcome.quick_connect.reconnect_flow(self.controller))

    def two_private_chats(self):
        first = self.ui.store.add('OPENAI', 'SUBSCRIPTION', reuse_native=False)
        second = self.ui.store.add('OPENAI', 'SUBSCRIPTION', reuse_native=False)
        self.record = first
        self.controller.selection_connection_id = first['id']
        self.controller.state['connection_id'] = first['id']
        other = NS(state={'id': 'chat-2', 'connection_id': second['id']}, busy=False, version=0,
                   selection_provider='codex_subscription', selection_connection_id=second['id'],
                   selection_models=[], delegate_jobs={})
        self.ui._controllers['chat-2'] = other
        self.start_offline_reconnect()
        with mock.patch.object(self.welcome.first_run, 'built_in', return_value=True):
            action = next(row for row in self.welcome.sign_in_items(self.ui, other) if row[1] == 'welcome_reconnect')
        with mock.patch.object(authentication, 'native_auth_command', return_value=(['never-launched', 'login'], {})), \
             mock.patch.object(authentication.threading, 'Thread'):
            self.welcome.dispatch(NS(), other, {}, action[1], action[2])
        return first, second, other

    def test_two_same_provider_private_watchers_do_not_overwrite_welcome_or_each_other(self):
        welcome = {'phase': 'failed', 'reason': 'existing welcome failure'}
        self.welcome.quick_connect._flows['codex_subscription'] = welcome
        first, second, other = self.two_private_chats()
        a = self.welcome.quick_connect.reconnect_flow(self.controller)
        b = self.welcome.quick_connect.reconnect_flow(other)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertNotEqual(a['token'], b['token'])
        self.assertEqual((a['record'], b['record']), (first['id'], second['id']))
        self.assertIs(self.welcome.quick_connect.flow('codex_subscription'), welcome)
        self.assertEqual(len(self.welcome.quick_connect._reconnects), 2)

    def test_interleaved_cancel_is_token_and_chat_owned_then_other_completion_is_exact(self):
        first, second, other = self.two_private_chats()
        a = self.welcome.quick_connect.reconnect_flow(self.controller)
        b = self.welcome.quick_connect.reconnect_flow(other)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        with mock.patch.object(self.ui._authentication, 'cancel') as cancel:
            self.welcome.dispatch(NS(), other, {}, 'welcome_reconnect_cancel', a['token'])
            cancel.assert_not_called()  # Another chat cannot use the first chat's drawn hit.
            self.welcome.dispatch(NS(), self.controller, {}, 'welcome_reconnect_cancel', a['token'])
            cancel.assert_called_once_with(first['id'], attempt=a['reconnect_attempt'])
            self.welcome.dispatch(NS(), self.controller, {}, 'welcome_reconnect_cancel', a['token'])
            self.assertEqual(cancel.call_count, 1)  # A stale duplicate click cannot target the other watcher.
        self.assertIs(self.welcome.quick_connect.reconnect_flow(other), b)
        self.ui._authentication.running.discard(second['id'])
        self.ui._authentication.status[second['id']] = 'Connected to ChatGPT'
        self.ui.catalog.entries[second['id']] = [{'id': 'ready'}]
        self.welcome.quick_connect.pump(self.ui)
        self.assertEqual(self.ui.bound, [second['id']])
        self.assertEqual(len(self.ui.store.records), 3)

    def test_superseded_cancel_token_cannot_cancel_same_chat_retry_or_other_chat(self):
        first, second, other = self.two_private_chats()
        self.assertIsNotNone(self.welcome.quick_connect.reconnect_flow(self.controller))
        self.assertIsNotNone(self.welcome.quick_connect.reconnect_flow(other))
        old = self.welcome.quick_connect.reconnect_flow(self.controller)['token']
        other_token = self.welcome.quick_connect.reconnect_flow(other)['token']
        self.ui._authentication.running.discard(first['id'])
        self.ui._authentication.status[first['id']] = 'Sign-in did not complete.'
        self.welcome.quick_connect.pump(self.ui)
        self.start_offline_reconnect()
        retry = self.welcome.quick_connect.reconnect_flow(self.controller)['token']
        self.assertNotEqual(old, retry)
        with mock.patch.object(self.ui._authentication, 'cancel') as cancel:
            self.welcome.dispatch(NS(), self.controller, {}, 'welcome_reconnect_cancel', old)
            cancel.assert_not_called()
        self.assertEqual(self.welcome.quick_connect.reconnect_flow(self.controller)['token'], retry)
        self.assertEqual(self.welcome.quick_connect.reconnect_flow(other)['token'], other_token)

    def other_chat_same_record(self):
        other = NS(state={'id': 'chat-2', 'connection_id': self.record['id']}, busy=False, version=0,
                   selection_provider='codex_subscription', selection_connection_id=self.record['id'],
                   selection_models=[], delegate_jobs={})
        self.ui._controllers['chat-2'] = other
        return other

    def dispatch_other(self, other):
        with mock.patch.object(authentication, 'native_auth_command', return_value=(['never-launched', 'login'], {})), \
             mock.patch.object(authentication.threading, 'Thread'):
            self.welcome.dispatch(NS(), other, {}, 'welcome_reconnect', self.record['id'])

    def complete_synthetic_failure(self, identity, attempt):
        self.ui._authentication.events.put((identity, 'Sign-in did not complete.', False, attempt, True))
        self.ui._authentication.pump()

    def test_shared_native_identity_refuses_overlapping_login_without_replacing_first_watcher(self):
        self.start_offline_reconnect()
        first = self.welcome.quick_connect.reconnect_flow(self.controller)
        other = self.other_chat_same_record()
        self.dispatch_other(other)
        self.assertEqual(self.results[-1], {'CANCELLED'})
        self.assertIsNone(self.welcome.quick_connect.reconnect_flow(other))
        self.assertIs(self.welcome.quick_connect.reconnect_flow(self.controller), first)

    def test_old_shared_identity_cancel_cannot_cancel_other_chat_new_attempt(self):
        self.start_offline_reconnect()
        first = self.welcome.quick_connect.reconnect_flow(self.controller)
        self.complete_synthetic_failure('codex_subscription', first['reconnect_attempt'])
        self.welcome.quick_connect.pump(self.ui)
        other = self.other_chat_same_record()
        self.dispatch_other(other)
        second = self.welcome.quick_connect.reconnect_flow(other)
        self.assertIsNotNone(second)
        self.assertNotEqual(first['reconnect_attempt'], second['reconnect_attempt'])
        with mock.patch.object(self.ui._authentication, 'cancel') as cancel:
            self.welcome.dispatch(NS(), self.controller, {}, 'welcome_reconnect_cancel', first['token'])
            cancel.assert_not_called()
        self.assertIs(self.welcome.quick_connect.reconnect_flow(other), second)
        self.assertIn('codex_subscription', self.ui._authentication.running)

    def start_created_welcome(self):
        self.ui.store.records.clear()
        self.ui._authentication.status['codex_subscription'] = 'Not connected'
        with mock.patch.object(authentication, 'native_auth_command', return_value=(['never-launched', 'login'], {})), \
             mock.patch.object(authentication.threading, 'Thread'):
            self.welcome.quick_connect.start(self.ui, self.controller, 'codex_subscription')
        item = self.welcome.quick_connect.flow('codex_subscription')
        self.assertTrue(item['created'])
        self.record = self.ui.store.records[item['record']]
        self.controller.selection_connection_id = self.record['id']
        self.controller.state['connection_id'] = self.record['id']
        return item

    def test_stale_welcome_cancel_cannot_delete_record_used_by_new_reconnect_attempt(self):
        old = self.start_created_welcome()
        self.complete_synthetic_failure(old['auth_identity'], old['auth_attempt'])
        other = self.other_chat_same_record()
        self.dispatch_other(other)
        current = self.welcome.quick_connect.reconnect_flow(other)
        self.assertIsNotNone(current)
        with mock.patch.object(self.ui._authentication, 'cancel') as cancel:
            self.welcome.dispatch(NS(), self.controller, {}, 'welcome_cancel', ('codex_subscription', old['token']))
            cancel.assert_not_called()
        self.assertIn(self.record['id'], self.ui.store.records)
        self.assertIs(self.welcome.quick_connect.reconnect_flow(other), current)

    def test_stale_welcome_cancel_cannot_pop_replacement_welcome_flow(self):
        old = self.start_created_welcome()
        other = self.other_chat_same_record()
        self.welcome.quick_connect.start(self.ui, other, 'codex_subscription')
        current = self.welcome.quick_connect.flow('codex_subscription')
        self.assertNotEqual(old['token'], current['token'])
        with mock.patch.object(self.ui._authentication, 'cancel') as cancel:
            self.welcome.dispatch(NS(), self.controller, {}, 'welcome_cancel', ('codex_subscription', old['token']))
            cancel.assert_not_called()
            self.welcome.dispatch(NS(), other, {}, 'welcome_cancel', ('codex_subscription', current['token']))
            cancel.assert_not_called()  # This replacement only waited on the original login; it owns no auth attempt.
        self.assertIn(self.record['id'], self.ui.store.records)

    def test_actual_own_failed_welcome_still_removes_only_its_new_record(self):
        item = self.start_created_welcome()
        self.complete_synthetic_failure(item['auth_identity'], item['auth_attempt'])
        self.welcome.quick_connect.pump(self.ui)
        self.assertNotIn(self.record['id'], self.ui.store.records)


if __name__ == '__main__':
    unittest.main()
