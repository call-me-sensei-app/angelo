# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual product transition ownership fixtures; no native process, browser, account or profile access."""
import suite_isolation  # noqa: F401
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_connection as connection
from angelo_agent.fab_browser_manual_connection import ManualConnection
import host_test_policy  # noqa: E402
if sys.platform != 'darwin':   # The macOS profile store (fcntl, Chrome's macOS bundle); Windows has its own.
    raise unittest.SkipTest(host_test_policy.environment_skip(
        'the macOS Fab profile store; Windows runs test_native_fab_windows_*, Linux has no Fab browser'))
from angelo_agent import fab_browser_profile as profile
import test_native_fab_browser_profile as profile_fixtures
import test_native_fab_browser_connection as connection_fixtures
from angelo_agent import fab_host_jobs as jobs
from angelo_agent.fab_browser_process import BrowserProcess, BrowserUnavailable


class ManualTransitionTests(unittest.TestCase):
    def fixture(self):
        lifetime = threading.Event()
        lease = SimpleNamespace(identity=(10, 20), _row={'token': 'private-token'}, validate=Mock())
        old = BrowserProcess.__new__(BrowserProcess)
        old._manual_login = True
        old._manual_auth_active = True
        old._profile_lease = lease
        old._marker = 'old-runtime'
        old._pid = 41
        old._closed = False
        old.close = Mock(side_effect=lambda **kwargs: setattr(old, '_pid', None))
        store = SimpleNamespace(_binding=Mock(return_value=None))
        owner = ManualConnection(old, store, lifetime)
        current = SimpleNamespace(identity=(10, 20), _row={'token': 'private-token'}, validate=Mock())
        native = SimpleNamespace(_manual_login=False, _closed=False, _pid=51,
                                 _profile_lease=current, _marker='new-runtime')
        new = SimpleNamespace(login_mode='managed', _browser=native, close=Mock())
        return owner, old, new, lifetime

    def test_done_alone_is_not_connected_and_transition_returns_new_physical_runtime(self):
        owner, old, new, lifetime = self.fixture()
        reply = owner.acknowledge_user_login(completed=True)
        self.assertFalse(reply['account_verified'])
        with patch.object(connection, '_resume_managed_expected', return_value=new) as factory:
            self.assertIs(owner.finish_manual_login(), new)
        old.close.assert_called_once_with(retain_profile=True)
        self.assertIs(factory.call_args.kwargs['stop'], lifetime)
        self.assertIsNone(factory.call_args.kwargs['startup_stop'])
        self.assertIsNone(owner._replacement)
        self.assertTrue(owner._closed)

    def test_no_acknowledgement_refuses_without_close_or_spawn(self):
        owner, old, new, lifetime = self.fixture()
        with patch.object(connection, '_resume_managed_expected') as factory:
            with self.assertRaisesRegex(BrowserUnavailable, 'MANUAL_COMPLETION_REQUIRED') as caught:
                owner.finish_manual_login()
        self.assertIs(caught.exception.owner, owner)
        old.close.assert_not_called()
        factory.assert_not_called()

    def test_cancel_after_graceful_drain_never_spawns_and_retains_exact_owner(self):
        owner, old, new, lifetime = self.fixture()
        owner.acknowledge_user_login(completed=True)
        old.close.side_effect = lambda **kwargs: lifetime.set()
        with patch.object(connection, '_resume_managed_expected') as factory:
            with self.assertRaisesRegex(BrowserUnavailable, 'CANCELLED') as caught:
                owner.finish_manual_login()
        factory.assert_not_called()
        self.assertIs(caught.exception.owner, owner)

    def test_changed_profile_or_reused_runtime_refuses_and_holds_new_owner(self):
        for field, value in (('profile', (10, 21)), ('runtime', 'old-runtime')):
            with self.subTest(field=field):
                owner, old, new, lifetime = self.fixture()
                owner.acknowledge_user_login(completed=True)
                if field == 'profile':
                    new._browser._profile_lease.identity = value
                else:
                    new._browser._marker = value
                with patch.object(connection, '_resume_managed_expected', return_value=new):
                    with self.assertRaisesRegex(BrowserUnavailable, 'MANUAL_TRANSITION_UNPROVED') as caught:
                        owner.finish_manual_login()
                self.assertIs(caught.exception.owner, owner)
                self.assertIs(owner._replacement, new)

    def test_returned_new_owner_published_before_its_metadata_failure(self):
        owner, old, new, lifetime = self.fixture()
        owner.acknowledge_user_login(completed=True)
        new._browser._profile_lease.validate.side_effect = RuntimeError('fixture failure')
        with patch.object(connection, '_resume_managed_expected', return_value=new):
            with self.assertRaises(BrowserUnavailable) as caught:
                owner.finish_manual_login()
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._replacement, new)

    def test_partial_factory_owner_is_retained_on_transition_failure(self):
        owner, old, new, lifetime = self.fixture()
        owner.acknowledge_user_login(completed=True)
        failure = BrowserUnavailable('START_FAILED')
        failure.owner = new
        with patch.object(connection, '_resume_managed_expected', side_effect=failure):
            with self.assertRaisesRegex(BrowserUnavailable, 'START_FAILED') as caught:
                owner.finish_manual_login()
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._replacement, new)

    def test_cleanup_ignores_cancel_and_closes_partial_before_old_owner(self):
        owner, old, new, lifetime = self.fixture()
        owner._replacement = new
        order = []
        new.close.side_effect = lambda: order.append('new')
        old.close.side_effect = lambda **kwargs: order.append('old')
        lifetime.set()
        owner.close_normal_shutdown()
        self.assertEqual(order, ['new', 'old'])
        old.close.assert_called_once_with()

    def test_partial_cleanup_failure_keeps_exact_owner_for_retry(self):
        owner, old, new, lifetime = self.fixture()
        owner._replacement = new
        new.close.side_effect = RuntimeError('fixture close failure')
        with self.assertRaises(BrowserUnavailable) as caught:
            owner.close()
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._replacement, new)
        old.close.assert_not_called()

    def test_no_manual_rpc_or_account_authority_and_shutdown_never_retains_from_done(self):
        owner, old, new, lifetime = self.fixture()
        owner.acknowledge_user_login(completed=True)
        for operation in (owner.current_account, owner.public_search, owner.listing, owner.open_selected):
            with self.assertRaisesRegex(BrowserUnavailable, 'AUTH_REQUIRED'):
                operation()
        self.assertIsNone(owner.service_idle())
        owner.close_normal_shutdown()
        old.close.assert_called_once_with()

    def test_cancelled_post_drain_retained_profile_is_reclaimed_by_exact_store_lease(self):
        owner, old, new, lifetime = self.fixture()
        old._pid = None
        reclaim = SimpleNamespace(close=Mock())
        retained = dict(version=1, uid=17, path='/private/tmp/fixture-retained-profile',
                        device=10, inode=20, token='a' * 32, state='retained')
        old._profile_lease._row = dict(retained)
        owner._store._binding.return_value = dict(retained)
        owner._store.resume_expected = Mock(return_value=reclaim)
        owner.close()
        owner._store.resume_expected.assert_called_once_with(dict(old._profile_lease._row))
        reclaim.close.assert_called_once_with()
        self.assertIsNone(owner._replacement)

    def test_different_retained_profile_is_never_reclaimed_or_deleted(self):
        owner, old, new, lifetime = self.fixture()
        owner._store._binding.return_value = {'device': 10, 'inode': 99, 'token': 'other', 'state': 'retained'}
        owner._store.resume_expected = Mock()
        with self.assertRaisesRegex(BrowserUnavailable, 'MANUAL_TRANSITION_CLEANUP_UNPROVED') as caught:
            owner.close()
        owner._store.resume_expected.assert_not_called()
        self.assertIs(caught.exception.owner, owner)

    def test_factory_requested_manual_is_fresh_only_and_retained_profile_stays_managed(self):
        from angelo_agent.fab_browser_profile import ProfileUnavailable
        for fresh in (True, False):
            with self.subTest(fresh=fresh):
                lease = SimpleNamespace()
                store = SimpleNamespace(resume=Mock(return_value=lease), create=Mock(return_value=lease))
                if fresh:
                    store.resume.side_effect = ProfileUnavailable('PROFILE_MISSING')
                marker = object()
                def init(browser, root=None, **kwargs):
                    browser._manual_login = kwargs.get('manual_login', False)
                    browser._closed = False
                with patch.object(connection, 'chrome_program', return_value='/Applications/Fixture.app/Contents/MacOS/Chrome'), patch.object(BrowserProcess, '__init__', init), patch.object(connection, 'Connection', return_value=marker) as managed:
                    value = connection.create_owned_connection(store=store, login_mode='manual')
                if fresh:
                    self.assertIsInstance(value, ManualConnection)
                    self.assertEqual(value.login_mode, 'manual')
                    managed.assert_not_called()
                else:
                    self.assertIs(value, marker)
                    store.create.assert_not_called()
                    managed.assert_called_once()

    def test_actual_store_replacement_race_never_claims_or_deletes_other_profile(self):
        helper = profile_fixtures.FabProfileTests()
        with helper.store() as store:
            first = store.create()
            old = helper.process(first)
            old.close(retain_profile=True)
            expected = dict(first._row)
            with store._transaction():
                (store.directory / profile.BINDING).unlink()
            second = store.create()
            helper.process(second).close(retain_profile=True)
            replacement = dict(second._row)
            with store._transaction():
                store._write(expected)
            old._manual_login = True
            owner = ManualConnection(old, store)
            original = store.resume_expected
            def race(row):
                with store._transaction():
                    store._write(replacement)
                return original(row)
            try:
                with patch.object(store, 'resume_expected', side_effect=race):
                    with self.assertRaisesRegex(BrowserUnavailable, 'MANUAL_TRANSITION_CLEANUP_UNPROVED'):
                        owner.close()
                self.assertTrue(first.path.is_dir())
                self.assertTrue(second.path.is_dir())
                self.assertEqual(store._binding(), replacement)
            finally:
                store.resume_expected(replacement).close()
                with store._transaction():
                    store._write(expected)
                store.resume_expected(expected).close()

    def test_actual_missing_expected_binding_refuses_before_any_managed_spawn_or_create(self):
        helper = profile_fixtures.FabProfileTests()
        with helper.store() as store:
            lease = store.create()
            helper.process(lease).close(retain_profile=True)
            expected = dict(lease._row)
            with store._transaction():
                (store.directory / profile.BINDING).unlink()
            try:
                with patch.object(connection, 'chrome_program', return_value=profile_fixtures.PROGRAM), patch.object(connection, '_open_profile_connection') as spawn, patch.object(store, 'create', wraps=store.create) as create:
                    with self.assertRaisesRegex(BrowserUnavailable, 'PROFILE_UNAVAILABLE'):
                        connection._resume_managed_expected(store, expected)
                spawn.assert_not_called()
                create.assert_not_called()
                self.assertTrue(lease.path.is_dir())
            finally:
                with store._transaction():
                    store._write(expected)
                store.resume_expected(expected).close()

    def test_actual_expected_store_resume_holds_same_profile_and_refuses_changed_binding(self):
        helper = profile_fixtures.FabProfileTests()
        with helper.store() as store:
            lease = store.create()
            helper.process(lease).close(retain_profile=True)
            expected = dict(lease._row)
            changed = dict(expected, token='b' * 32)
            with self.assertRaises(profile.ProfileUnavailable) as caught:
                store.resume_expected(changed)
            self.assertEqual(caught.exception.code, 'PROFILE_METADATA_CHANGED')
            reopened = store.resume_expected(expected)
            self.assertEqual(reopened.identity, lease.identity)
            reopened.close()

    def test_actual_finite_job_stop_expires_without_poisoning_new_connection_lifetime(self):
        clock = [0.0]
        lifetime = threading.Event()
        job_cancel = threading.Event()
        class Browser(connection_fixtures.Browser):
            def call(self, method, params=None, **kwargs):
                stop = kwargs.get('stop')
                if stop is not None and stop.is_set():
                    raise BrowserUnavailable('CANCELLED')
                return super().call(method, params, **kwargs)
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
            transient = jobs.Stop(job_cancel, lifetime, seconds=120)
            client = connection.Connection(browser_factory=Browser, stop=lifetime, startup_stop=transient)
            try:
                self.assertIs(client._stop, lifetime)
                self.assertIsNone(client._startup_stop)
                clock[0] = 3600
                self.assertTrue(transient.is_set())
                self.assertEqual(client.state()['state'], 'site_visible')
                job_cancel.set()
                self.assertEqual(client.state()['state'], 'site_visible')
                lifetime.set()
                with self.assertRaisesRegex(BrowserUnavailable, 'CANCELLED'):
                    client.state()
            finally:
                client.close()

    def test_actual_transient_startup_cancellation_refuses_before_browser_creation(self):
        lifetime = threading.Event()
        cancel = threading.Event()
        cancel.set()
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: 0.0)):
            transient = jobs.Stop(cancel, lifetime, seconds=120)
            with patch.object(connection_fixtures, 'Browser') as factory:
                with self.assertRaisesRegex(BrowserUnavailable, 'CANCELLED'):
                    connection.Connection(browser_factory=factory, stop=lifetime, startup_stop=transient)
            factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
