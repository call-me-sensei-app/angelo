# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real NativeAuthentication ownership; only threads' scheduling and final process launch are substituted."""
import suite_isolation  # noqa: F401 First: no real account, executable or credential.

import contextlib
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import angelo_agent
import tempfile
import unittest
from unittest import mock

from angelo_agent import authentication


class FinishedProcess:
    def __init__(self, code=0, output='Logged in using ChatGPT'):
        self.returncode, self.output, self.inputs = code, output, []
    def communicate(self, **kwargs):
        self.inputs.append(kwargs.get('input'))
        return self.output, None
    def poll(self):
        return self.returncode
    def terminate(self):
        self.returncode = -15
    def kill(self):
        self.returncode = -9


class AuthenticationAttemptTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
    def tearDown(self):
        self.scratch.cleanup()

    @contextlib.contextmanager
    def rig(self, process=None):
        queued = []
        class DeferredThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                queued.append(self.target)
        auth = authentication.NativeAuthentication()
        with mock.patch.object(authentication, 'native_auth_command', return_value=(['never-launched', 'login'], {})), \
             mock.patch.object(authentication.threading, 'Thread', DeferredThread), \
             mock.patch.object(authentication, 'launch', return_value=process or FinishedProcess()) as launch:
            yield auth, queued, launch

    def pump(self, auth):
        changes = auth.pump()
        for change in changes:
            self.assertIsInstance(change, authentication.AuthChange)
            self.assertEqual(change.attempt, auth.attempt(change.identity))
        return [(change.identity, change.logged_in) for change in changes]

    def start(self, auth, **kwargs):
        return auth.start('codex_subscription', {}, self.scratch.name, identity='shared', **kwargs)

    def test_cancel_before_thread_launch_never_creates_a_process(self):
        with self.rig() as (auth, queued, launch):
            attempt = self.start(auth, login=True)
            self.assertTrue(auth.cancel('shared', attempt=attempt))
            queued[0]()
            launch.assert_not_called()
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertNotIn('shared', auth.running)
            self.assertIn('cancelled', auth.status['shared'])

    def test_close_before_thread_launch_never_creates_a_process(self):
        with self.rig() as (auth, queued, launch):
            self.start(auth, login=True)
            auth.close()
            queued[0]()
            launch.assert_not_called()
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertNotIn('shared', auth.running)

    def test_canceled_status_check_cannot_restore_ready_metadata(self):
        with self.rig() as (auth, queued, launch):
            attempt = self.start(auth)
            auth.cancel('shared', attempt=attempt)
            queued[0]()
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertNotEqual(auth.status['shared'], 'Connected to ChatGPT')
            launch.assert_not_called()

    def test_completion_recheck_retains_logical_attempt(self):
        with self.rig() as (auth, queued, launch):
            attempt = self.start(auth, login=True)
            queued[0]()
            self.assertEqual(self.pump(auth), [('shared', True)])
            self.assertEqual(self.start(auth, continuation=attempt), attempt)
            queued[1]()
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertEqual(auth.status['shared'], 'Connected to ChatGPT')
            self.assertEqual(auth.attempt('shared'), attempt)

    def test_cancel_queued_completion_recheck_blocks_only_that_attempt(self):
        with self.rig() as (auth, queued, launch):
            attempt = self.start(auth, login=True)
            queued[0]()
            self.pump(auth)
            self.start(auth, continuation=attempt)
            auth.cancel('shared', attempt=attempt)
            queued[1]()
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertIn('cancelled', auth.status['shared'])
            with self.assertRaises(RuntimeError):
                self.start(auth, continuation=attempt)

    def test_old_cancel_cannot_cancel_new_login_with_same_identity(self):
        with self.rig(FinishedProcess(code=1)) as (auth, queued, launch):
            old = self.start(auth, login=True)
            queued[0]()
            self.pump(auth)
            new = self.start(auth, login=True)
            self.assertNotEqual(old, new)
            self.assertFalse(auth.cancel('shared', attempt=old))
            queued[1]()
            self.assertEqual(launch.call_count, 2)
            self.assertNotIn('cancelled', auth.status['shared'])
            self.assertEqual(auth.attempt('shared'), new)

    def test_late_old_result_does_not_change_new_running_status(self):
        with self.rig(FinishedProcess(code=1)) as (auth, queued, launch):
            old = self.start(auth, login=True)
            queued[0]()
            self.pump(auth)
            new = self.start(auth, login=True)
            auth.events.put(('shared', 'Connected to ChatGPT', True, old, True))
            self.assertEqual(self.pump(auth), [])
            self.assertIn('shared', auth.running)
            self.assertEqual(auth.status['shared'], 'Complete sign-in in your browser…')
            self.assertEqual(auth.attempt('shared'), new)

    def test_old_finally_does_not_remove_newer_process_or_publish_old_result(self):
        with self.rig() as (auth, queued, launch):
            old = self.start(auth, login=True)
            newer = FinishedProcess()
            process = launch.return_value
            def poll():
                auth.processes['shared'] = newer
                auth._attempts['shared'] = 'newer-attempt'
                return 0
            process.poll = poll
            queued[0]()
            self.assertIs(auth.processes['shared'], newer)
            self.assertEqual(self.pump(auth), [])
            self.assertNotEqual(auth.attempt('shared'), old)

    def test_thread_start_failure_releases_running_without_launch(self):
        with self.rig() as (auth, queued, launch):
            with mock.patch.object(authentication.threading, 'Thread', side_effect=RuntimeError('thread refused')):
                self.start(auth, login=True)
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertNotIn('shared', auth.running)
            self.assertIn("couldn't start", auth.status['shared'])
            launch.assert_not_called()

    def test_cancel_after_launch_drains_before_publishing_no_success(self):
        with self.rig() as (auth, queued, launch):
            attempt = self.start(auth, login=True)
            process = launch.return_value
            process.returncode = None
            def communicate(**kwargs):
                auth.cancel('shared', attempt=attempt)
                return 'private provider output', None
            process.communicate = communicate
            queued[0]()
            self.assertEqual(process.poll(), -15)
            self.assertNotIn('shared', auth.processes)
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertIn('cancelled', auth.status['shared'])

    def test_unproved_drain_retains_owner_and_blocks_replacement(self):
        with self.rig() as (auth, queued, launch):
            self.start(auth, login=True)
            process = launch.return_value
            process.returncode = None
            process.communicate = mock.Mock(side_effect=OSError('unavailable pipe'))
            process.terminate = mock.Mock(side_effect=OSError('cannot terminate'))
            queued[0]()
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertIs(auth.processes['shared'], process)
            self.assertIn('shared', auth.running)
            self.assertIn('could not stop', auth.status['shared'])
            with self.assertRaises(RuntimeError):
                self.start(auth, login=True)

    def test_api_key_input_stays_only_on_first_process_stdin(self):
        with self.rig() as (auth, queued, launch):
            auth.start('codex_api', {}, self.scratch.name, login=True, identity='api', api_key='synthetic-test-key')
            queued[0]()
            self.assertEqual(launch.return_value.inputs, ['synthetic-test-key\n'])
            changes = self.pump(auth)
            self.assertNotIn('synthetic-test-key', repr(changes) + repr(auth.status))

    def ui_auth_block(self, auth, *, runtime_callback=lambda: None, save_callback=lambda: None,
                      invalidate_callback=lambda: None, retire_callback=lambda: None):
        """Execute the exact ui.pump authentication statements; no rewritten completion algorithm."""
        source = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent/ui.py'
        pump = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'pump')
        index = next(i for i, n in enumerate(pump.body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'changes' for t in n.targets)
                     and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                     and n.value.func.attr == 'pump' and isinstance(n.value.func.value, ast.Name)
                     and n.value.func.value.id == '_authentication')
        block = ast.Module(body=pump.body[index:index+2], type_ignores=[])
        first = NS(state={'provider': 'codex_subscription', 'connection_id': 'shared', 'native_id': 'first-old'},
                   provider=None, connection_error=True, save=mock.Mock(side_effect=save_callback),
                   invalidate_runtime=mock.Mock(side_effect=lambda _: invalidate_callback()))
        second = NS(state={'provider': 'codex_subscription', 'connection_id': 'shared', 'native_id': 'second-old'},
                    provider=None, connection_error=True, save=mock.Mock(), invalidate_runtime=mock.Mock())
        catalog = NS(invalidate=mock.Mock())
        scope = dict(__package__='angelo_agent', _authentication=auth,
            connections=lambda: NS(records={'shared': {'id': 'shared'}}), _controllers={'first': first, 'second': second},
            native_suggestions=NS(usable=lambda status: False), catalogs=lambda: catalog,
            runtime_bindings=NS(end_connection=lambda _: runtime_callback(), end_modes=lambda _: runtime_callback()),
            connection_settings=lambda mode, record: {}, state_directory=lambda: self.scratch.name,
            console=NS(redraw_chats=mock.Mock()), preferences_ui=NS(redraw=mock.Mock()), threading=authentication.threading)
        native = NS(retire_mode=mock.Mock(), retire_connection=mock.Mock(side_effect=lambda _: retire_callback()),
                    retire_login=mock.Mock(side_effect=lambda _: retire_callback()), login_file=lambda _: '')
        def run():
            with mock.patch.object(angelo_agent, 'codex_runtime', native):
                exec(compile(ast.fix_missing_locations(block), str(source), 'exec'), scope)
        return run, first, second, catalog

    def test_actual_ui_runtime_callback_cannot_borrow_new_attempt_for_recheck(self):
        with self.rig() as (auth, queued, launch):
            old = self.start(auth, login=True)
            queued[0]()
            newer = []
            def replace():
                newer.append(self.start(auth, login=True))
                queued[1]()
                auth.pump()
                self.start(auth, continuation=newer[0])
                queued[2]()
                auth.pump()
            run, first, second, catalog = self.ui_auth_block(auth, runtime_callback=replace)
            run()
            self.assertNotEqual(newer[0], old)
            self.assertEqual(auth.attempt('shared'), newer[0])
            self.assertEqual(auth.status['shared'], 'Connected to ChatGPT')
            self.assertEqual(len(queued), 3, 'The old event must not schedule a recheck of the new login')
            first.invalidate_runtime.assert_not_called()
            self.assertEqual(first.state['native_id'], 'first-old')

    def test_actual_ui_save_callback_cannot_recheck_or_clear_newer_state(self):
        with self.rig() as (auth, queued, launch):
            old = self.start(auth, login=True)
            queued[0]()
            newer = []
            def replace():
                newer.append(self.start(auth, login=True))
                first.state['native_id'] = 'newer-native'
            run, first, second, catalog = self.ui_auth_block(auth, save_callback=replace)
            run()
            self.assertNotEqual(newer[0], old)
            self.assertEqual(first.state['native_id'], 'newer-native')
            second.invalidate_runtime.assert_not_called()
            self.assertEqual(second.state['native_id'], 'second-old')
            self.assertEqual(len(queued), 2)

    def test_actual_ui_invalidate_callback_cannot_clear_replacement_state(self):
        with self.rig() as (auth, queued, launch):
            self.start(auth, login=True)
            queued[0]()
            def replace():
                self.start(auth, login=True)
                first.state['native_id'] = 'newer-native'
            run, first, second, catalog = self.ui_auth_block(auth, invalidate_callback=replace)
            run()
            self.assertEqual(first.state['native_id'], 'newer-native')
            first.save.assert_not_called()
            second.invalidate_runtime.assert_not_called()
            self.assertEqual(len(queued), 2)

    def test_actual_ui_ignores_immutable_old_change_before_callbacks(self):
        with self.rig() as (auth, queued, launch):
            self.start(auth, login=True)
            queued[0]()
            change = auth.pump()[0]
            newer = self.start(auth, login=True)
            run, first, second, catalog = self.ui_auth_block(auth)
            with mock.patch.object(auth, 'pump', return_value=[change]):
                run()
            catalog.invalidate.assert_not_called()
            first.invalidate_runtime.assert_not_called()
            self.assertEqual(auth.attempt('shared'), newer)
            self.assertEqual(len(queued), 2)

    def test_actual_ui_normal_completion_rechecks_only_event_attempt(self):
        with self.rig() as (auth, queued, launch):
            attempt = self.start(auth, login=True)
            queued[0]()
            run, first, second, catalog = self.ui_auth_block(auth)
            run()
            self.assertEqual(auth.attempt('shared'), attempt)
            self.assertEqual(len(queued), 2)
            self.assertIsNone(first.state['native_id'])
            self.assertIsNone(second.state['native_id'])
            queued[1]()
            self.assertEqual(self.pump(auth), [('shared', False)])
            self.assertEqual(auth.status['shared'], 'Connected to ChatGPT')

    def test_actual_ui_expired_status_retire_callback_cannot_end_new_runtime(self):
        with self.rig(FinishedProcess(code=1)) as (auth, queued, launch):
            old = self.start(auth)
            queued[0]()
            newer = []
            def replace():
                newer.append(self.start(auth, login=True))
            ended = mock.Mock()
            run, first, second, catalog = self.ui_auth_block(auth, runtime_callback=ended, retire_callback=replace)
            run()
            self.assertNotEqual(newer[0], old)
            ended.assert_not_called()
            self.assertEqual(auth.attempt('shared'), newer[0])
            self.assertEqual(auth.status['shared'], 'Complete sign-in in your browser…')


if __name__ == '__main__':
    unittest.main()
