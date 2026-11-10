# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual module/class composition with native acquisition boundaries mocked; never Windows readiness."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

import host_test_policy
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_platform as platform_owner, fab_browser_connection as connection
from angelo_agent import fab_browser_process_windows as windows, fab_browser_identity as identity
from angelo_agent import fab_browser_profile_session_windows as profiles
from angelo_agent.fab_browser_process import BrowserProcess, BrowserUnavailable
from angelo_agent.fab_browser_manual_connection import ManualConnection
from angelo_agent.fab_browser_account import AccountObservation


class PlatformDispatchTests(unittest.TestCase):
    @contextmanager
    def windows_path(self):
        with patch.object(platform_owner, '_platform_name', return_value='win32'):
            yield

    def store(self, **methods):
        value = profiles.WindowsProfileStore.__new__(profiles.WindowsProfileStore)
        value.__dict__.update(methods)
        value.close = Mock()
        return value

    def lease(self):
        value = profiles.WindowsProfileLease.__new__(profiles.WindowsProfileLease)
        value.identity = (71, bytes.fromhex('12' * 16))
        value.generation = 'a' * 32
        value._row = dict(version=1, sid='fixture-sid', path='fixture-profile', volume=71,
                          file_id='12' * 16, token=value.generation, state='pending')
        value._browser = None
        value.verify = Mock()
        value.close = Mock()
        return value

    def browser(self, lease, *, manual=False):
        value = windows.WindowsBrowserProcess.__new__(windows.WindowsBrowserProcess)
        value._profile_lease = lease
        lease._browser = value
        value._manual_login = manual
        value._manual_auth_active = manual
        value._closed = False
        value._runtime_generation = object()
        value._runtime_epoch_token = 'b' * 32
        value._download_uncertain = False
        value.close = Mock()
        value._account_binding = Mock(return_value=(lease, lease.generation, value._runtime_epoch_token))
        return value

    def test_platform_routes_exact_existing_process_profile_and_file_types(self):
        with self.windows_path():
            self.assertIs(platform_owner.browser_type(), windows.WindowsBrowserProcess)
            self.assertIs(platform_owner.profile_types()[0], profiles.WindowsProfileStore)
            self.assertEqual(platform_owner.file_types()[1].__module__, 'angelo_agent.fab_browser_download_file_windows')
        with patch.object(platform_owner, '_platform_name', return_value='darwin'):
            self.assertIs(platform_owner.browser_type(), BrowserProcess)

    def test_unsupported_platform_refuses_without_selecting_a_native_owner(self):
        with patch.object(platform_owner, '_platform_name', return_value='unsupported'):
            for operation in (platform_owner.browser_type, platform_owner.profile_types, platform_owner.file_types):
                with self.assertRaises(BrowserUnavailable) as caught:
                    operation()
                self.assertEqual(caught.exception.code, 'PLATFORM_UNVERIFIED')

    def test_windows_binding_requires_fresh_native_proof_every_time(self):
        lease = self.lease(); browser = self.browser(lease)
        with self.windows_path():
            first = platform_owner.account_binding(browser)
            second = platform_owner.account_binding(browser)
        self.assertEqual(first, second)
        self.assertEqual(browser._account_binding.call_count, 2)
        self.assertGreaterEqual(lease.verify.call_count, 2)

    def test_windows_missing_or_foreign_native_binding_never_becomes_an_account(self):
        lease = self.lease(); browser = self.browser(lease)
        for result in ((object(), lease.generation, browser._runtime_epoch_token),
                       (lease, 'changed', browser._runtime_epoch_token),
                       (lease, lease.generation, 'changed'), True):
            with self.subTest(result_type=type(result).__name__), self.windows_path():
                browser._account_binding.return_value = result
                with self.assertRaises(BrowserUnavailable):
                    platform_owner.account_binding(browser)
        browser._account_binding.side_effect = BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
        with self.windows_path(), self.assertRaises(BrowserUnavailable):
            platform_owner.account_binding(browser)

    def test_manual_windows_owner_cannot_mint_account_binding(self):
        lease = self.lease(); browser = self.browser(lease, manual=True)
        with self.windows_path(), self.assertRaises(BrowserUnavailable) as caught:
            platform_owner.account_binding(browser)
        self.assertEqual(caught.exception.code, 'AUTH_REQUIRED')
        browser._account_binding.assert_not_called()

    def test_windows_witness_revalidates_actual_native_owner_instead_of_cached_epoch(self):
        lease = self.lease(); browser = self.browser(lease)
        owner = SimpleNamespace(_browser=browser, _user_auth_active=False)
        record = AccountObservation('opaque-account', lease.generation, browser._runtime_epoch_token)
        witness = identity._AccountWitness(owner, browser, lease, record)
        with self.windows_path():
            self.assertTrue(witness.valid_for_retention(owner))
            browser._account_binding.side_effect = BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
            with self.assertRaises(BrowserUnavailable):
                witness.valid_for_retention(owner)
        self.assertEqual(browser._account_binding.call_count, 2)

    def test_actual_common_connection_factory_uses_windows_constructor_and_initial_cdp_sequence(self):
        lease = self.lease(); store = self.store(resume=Mock(return_value=lease), create=Mock())
        lifetime = threading.Event(); calls = []; created = []
        def initialize(browser, root=None, **kwargs):
            self.assertIs(kwargs['profile_lease'], lease)
            self.assertIsNone(root)
            browser.__dict__.update(self.browser(lease).__dict__)
            lease._browser = browser
            created.append(browser)
        def call(browser, method, params=None, **kwargs):
            calls.append(method)
            return {'targetId':'target'} if method=='Target.createTarget' else {'sessionId':'session'} if method=='Target.attachToTarget' else {}
        with self.windows_path(), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(windows.WindowsBrowserProcess, '__init__', initialize), patch.object(windows.WindowsBrowserProcess, 'call', call):
            owner = connection.create_owned_connection(stop=lifetime, store=store)
        self.assertIs(type(owner._browser), windows.WindowsBrowserProcess)
        self.assertIs(owner._browser, created[0])
        self.assertIs(owner._stop, lifetime)
        self.assertEqual(calls, ['Browser.setDownloadBehavior','Target.createTarget','Target.attachToTarget','Page.navigate'])
        store.create.assert_not_called()

    def test_windows_strict_resume_missing_binding_never_falls_back_to_create_or_spawn(self):
        store = self.store(resume_expected=Mock(side_effect=profiles.ProfileUnavailable('PROFILE_MISSING')), create=Mock())
        with self.windows_path(), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(windows.WindowsBrowserProcess, '__init__') as native:
            with self.assertRaises(BrowserUnavailable):
                connection._resume_managed_expected(store, {'state':'retained'})
        store.create.assert_not_called(); native.assert_not_called()

    def test_partial_windows_constructor_owner_is_retained_when_cleanup_is_unknown(self):
        lease = self.lease(); store = self.store(resume=Mock(return_value=lease))
        exact = []
        def initialize(browser, *args, **kwargs):
            exact.append(browser)
            browser.close = Mock(side_effect=OSError('fixture uncertain native owner'))
            raise BrowserUnavailable('START_FAILED')
        with self.windows_path(), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(windows.WindowsBrowserProcess, '__init__', initialize):
            with self.assertRaises(BrowserUnavailable) as caught:
                connection.create_owned_connection(store=store)
        self.assertIs(caught.exception.owner.native, exact[0])
        self.assertIs(caught.exception.owner._profile_store, store)

    def test_windows_manual_transition_requires_new_physical_runtime_then_fresh_binding(self):
        lease = self.lease(); old = self.browser(lease, manual=True)
        old.close.side_effect = lambda **kwargs: lease._row.update(state='retained')
        current = self.lease(); new_browser = self.browser(current)
        store = self.store(_binding=Mock(return_value=None))
        new = SimpleNamespace(login_mode='managed', _browser=new_browser, close=Mock(), _profile_store=store)
        lifetime = threading.Event(); transient = threading.Event()
        with self.windows_path():
            owner = ManualConnection(old, store, lifetime)
            owner.acknowledge_user_login(completed=True)
            with patch.object(connection, '_resume_managed_expected', return_value=new) as resume:
                result = owner.finish_manual_login(stop=transient)
        self.assertIs(result, new)
        self.assertIs(resume.call_args.kwargs['stop'], lifetime)
        self.assertIs(resume.call_args.kwargs['startup_stop'], transient)
        self.assertEqual(resume.call_args.args[1]['state'], 'retained')
        old.close.assert_called_once_with(retain_profile=True)
        new_browser._account_binding.assert_called_once()

    def test_windows_native_revalidation_failure_retains_new_owner_for_cleanup(self):
        lease = self.lease(); old = self.browser(lease, manual=True)
        old.close.side_effect = lambda **kwargs: lease._row.update(state='retained')
        new_browser = self.browser(self.lease())
        new_browser._account_binding.side_effect = BrowserUnavailable('PHYSICAL_BINDING_UNPROVED')
        store = self.store(_binding=Mock(return_value=None))
        new = SimpleNamespace(login_mode='managed', _browser=new_browser, close=Mock(), _profile_store=store)
        with self.windows_path():
            owner = ManualConnection(old, store)
            owner.acknowledge_user_login(completed=True)
            with patch.object(connection, '_resume_managed_expected', return_value=new), self.assertRaises(BrowserUnavailable) as caught:
                owner.finish_manual_login()
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._replacement, new)

    def test_windows_manual_foreground_metadata_comes_only_from_exact_native_binding(self):
        from angelo_agent.fab_browser_windows_proof import _Binding
        lease = self.lease(); browser = self.browser(lease, manual=True)
        browser.begin_manual_auth = Mock(return_value=True)
        proof = _Binding(browser, object(), object(), lease, browser._runtime_generation,
                         browser._runtime_epoch_token, ('fixture-chrome',), 11, 12, 41, 123,
                         lease.identity, lease.generation, 'fixture-profile', 'fixture-chrome', 99, 1)
        browser._physical_proof = proof
        with self.windows_path():
            ready = platform_owner.begin_manual_auth(browser)
            self.assertEqual(ready, dict(native_window_id=99, owned_browser_pid=41, foreground_verified=True))
            browser._physical_proof = None
            with self.assertRaises(BrowserUnavailable):
                platform_owner.begin_manual_auth(browser)

    def test_windows_profile_constructor_failure_preserves_actual_partial_store_owner(self):
        exact = []
        def initialize(store, *args, **kwargs):
            exact.append(store)
            raise profiles.ProfileUnavailable('PROFILE_METADATA_CLOSE_UNPROVED', owner=store)
        with self.windows_path(), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(profiles.WindowsProfileStore, '__init__', initialize), patch.object(windows.WindowsBrowserProcess, '__init__') as native:
            with self.assertRaises(BrowserUnavailable) as caught:
                connection.create_owned_connection()
        self.assertEqual(caught.exception.code, 'PROFILE_UNAVAILABLE')
        self.assertIs(caught.exception.owner, exact[0])
        native.assert_not_called()

    def test_fresh_windows_manual_factory_never_creates_a_cdp_or_account_session(self):
        lease = self.lease()
        store = self.store(resume=Mock(side_effect=profiles.ProfileUnavailable('PROFILE_MISSING')),
                                create=Mock(return_value=lease))
        seen = []
        def initialize(browser, root=None, **kwargs):
            seen.append(kwargs)
            browser.__dict__.update(self.browser(lease, manual=True).__dict__)
            lease._browser = browser
        with self.windows_path(), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(windows.WindowsBrowserProcess, '__init__', initialize), patch.object(windows.WindowsBrowserProcess, 'call') as call:
            owner = connection.create_owned_connection(store=store, login_mode='manual')
        self.assertIs(type(owner), ManualConnection)
        self.assertEqual(owner.login_mode, 'manual')
        self.assertTrue(seen[0]['manual_login'])
        store.create.assert_called_once()
        call.assert_not_called()

    def test_common_strict_resume_uses_actual_windows_store_transaction_before_constructor(self):
        import test_native_fab_windows_profile_session as fixtures
        helper = fixtures.WindowsProfileSessionTests()
        helper.setUp()
        try:
            lease = helper.store.create()
            old = helper.browser(lease)
            helper.drain(old)
            lease.finish_drained(old, retain=True)
            expected = dict(lease._row)
            marker = object()
            with self.windows_path(), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(connection, '_open_profile_connection', return_value=marker) as spawn:
                self.assertIs(connection._resume_managed_expected(helper.store, expected), marker)
            acquired = spawn.call_args.args[0]
            self.assertIs(type(acquired), profiles.WindowsProfileLease)
            self.assertEqual(acquired.identity, lease.identity)
            self.assertEqual(acquired.generation, lease.generation)
            self.assertEqual(acquired._row['state'], 'pending')
            acquired.close()
        finally:
            helper.doCleanups()

    def test_common_strict_resume_actual_replacement_binding_cannot_spawn_or_create_fallback(self):
        import test_native_fab_windows_profile_session as fixtures
        helper = fixtures.WindowsProfileSessionTests()
        helper.setUp()
        try:
            lease = helper.store.create()
            old = helper.browser(lease)
            helper.drain(old)
            lease.finish_drained(old, retain=True)
            expected = dict(lease._row)
            replacement = dict(expected, token='c' * 32, path=str(helper.store.scratch / ('angelo-fab-browser-' + 'c' * 32)))
            helper.store._write(replacement)
            with self.windows_path(), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(connection, '_open_profile_connection') as spawn, patch.object(helper.store, 'create', wraps=helper.store.create) as create:
                with self.assertRaises(BrowserUnavailable) as caught:
                    connection._resume_managed_expected(helper.store, expected)
            self.assertEqual(caught.exception.code, 'PROFILE_UNAVAILABLE')
            self.assertEqual(helper.store._binding(), replacement)
            spawn.assert_not_called()
            create.assert_not_called()
        finally:
            helper.doCleanups()

    @unittest.skipIf(sys.platform != 'darwin', host_test_policy.environment_skip(
        'the macOS profile lease (fcntl); Windows runs test_native_fab_windows_*, Linux has no Fab browser'))
    def test_stop_projection_invokes_darwin_native_method_once_with_same_stop(self):
        from angelo_agent.fab_browser_profile import ProfileLease
        browser = BrowserProcess.__new__(BrowserProcess)
        lease = ProfileLease.__new__(ProfileLease)
        lease._browser = browser
        lease.validate = Mock()
        browser._profile_lease = lease
        ready = {'foreground_verified': True}
        browser.begin_manual_auth = Mock(return_value=ready)
        stop = threading.Event()
        with patch.object(platform_owner, '_platform_name', return_value='darwin'):
            self.assertIs(platform_owner.begin_manual_auth(browser, stop=stop), ready)
        browser.begin_manual_auth.assert_called_once_with(stop=stop)

    def test_stop_projection_invokes_windows_native_method_once_with_same_stop(self):
        from angelo_agent.fab_browser_windows_proof import _Binding
        lease = self.lease()
        browser = self.browser(lease, manual=True)
        browser.begin_manual_auth = Mock(return_value=True)
        browser._physical_proof = _Binding(browser, object(), object(), lease, browser._runtime_generation,
                                          browser._runtime_epoch_token, ('fixture-chrome',), 11, 12, 41, 123,
                                          lease.identity, lease.generation, 'fixture-profile', 'fixture-chrome', 99, 1)
        stop = threading.Event()
        with self.windows_path():
            ready = platform_owner.begin_manual_auth(browser, stop=stop)
        self.assertTrue(ready['foreground_verified'])
        browser.begin_manual_auth.assert_called_once_with(stop=stop)

    def test_stop_projection_refuses_before_and_during_lease_validation_without_native_invocation(self):
        for during in (False, True):
            with self.subTest(during=during):
                lease = self.lease()
                browser = self.browser(lease, manual=True)
                browser._manual_auth_active = False
                browser.begin_manual_auth = Mock()
                stop = threading.Event()
                if during:
                    lease.verify.side_effect = stop.set
                else:
                    stop.set()
                with self.windows_path(), self.assertRaises(BrowserUnavailable) as caught:
                    platform_owner.begin_manual_auth(browser, stop=stop)
                self.assertEqual(caught.exception.code, 'CANCELLED')
                self.assertFalse(browser._manual_auth_active)
                browser.begin_manual_auth.assert_not_called()
