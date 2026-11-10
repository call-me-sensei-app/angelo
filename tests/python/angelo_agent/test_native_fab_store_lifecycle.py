# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Terminal composite ownership ordering; native acquisition is modeled, never Windows acceptance."""
import suite_isolation  # noqa: F401
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_platform as platform_owner
from angelo_agent.fab_browser_connection import Connection
from angelo_agent.fab_browser_process import BrowserUnavailable
from angelo_agent.fab_browser_profile_session_windows import WindowsProfileStore


class StoreLifecycleTests(unittest.TestCase):
    def owner(self, *, native=True):
        events = []
        store = WindowsProfileStore.__new__(WindowsProfileStore)
        store.close = Mock(side_effect=lambda: events.append('store'))
        browser = SimpleNamespace(close=Mock(side_effect=lambda **kwargs: events.append('native')))
        file = SimpleNamespace(close=Mock(side_effect=lambda: events.append('file')))
        landing = SimpleNamespace(remove=Mock(side_effect=lambda: events.append('landing')))
        owner = Connection.__new__(Connection)
        owner._lock = threading.RLock()
        owner._browser = browser if native else None
        owner._profile_store = store
        owner._download_cleanup = (landing, file)
        owner._download_landing = landing
        owner._download_tracker = object()
        owner._selected_transfer = None
        return owner, browser, store, file, landing, events

    def test_terminal_native_file_landing_store_order_and_known_field_release(self):
        owner, browser, store, file, landing, events = self.owner()
        with patch.object(platform_owner, '_platform_name', return_value='win32'):
            owner.close()
        self.assertEqual(events, ['native', 'file', 'landing', 'store'])
        self.assertIsNone(owner._browser)
        self.assertIsNone(owner._profile_store)
        self.assertIsNone(owner._download_cleanup)
        self.assertTrue(store._fab_common_closed)

    def test_unknown_store_close_preserves_entire_graph_and_never_retries_native_close(self):
        owner, browser, store, file, landing, events = self.owner()
        def unknown():
            events.append('store')
            raise OSError('fixture native close succeeded then lost return')
        store.close.side_effect = unknown
        with patch.object(platform_owner, '_platform_name', return_value='win32'):
            for _ in range(2):
                with self.assertRaises(BrowserUnavailable) as caught:
                    owner.close()
                self.assertIs(caught.exception.owner, owner)
            alias = SimpleNamespace(_profile_store=store)
            with self.assertRaises(BrowserUnavailable):
                platform_owner.close_profile_store(alias)
        self.assertIs(owner._browser, browser)
        self.assertIs(owner._profile_store, store)
        self.assertEqual(owner._download_cleanup, (landing, file))
        self.assertTrue(store._fab_common_close_uncertain)
        browser.close.assert_called_once()
        file.close.assert_called_once()
        landing.remove.assert_called_once()
        store.close.assert_called_once()

    def test_unknown_landing_cleanup_retains_store_without_attempting_its_close(self):
        owner, browser, store, file, landing, events = self.owner()
        landing.remove.side_effect = OSError('fixture landing identity changed')
        with patch.object(platform_owner, '_platform_name', return_value='win32'), self.assertRaises(BrowserUnavailable) as caught:
            owner.close()
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._browser, browser)
        self.assertIs(owner._profile_store, store)
        self.assertEqual(owner._download_cleanup, (landing, file))
        store.close.assert_not_called()
        self.assertFalse(getattr(store, '_fab_common_close_uncertain', False))

    def test_unknown_native_drain_never_closes_files_landing_or_store(self):
        owner, browser, store, file, landing, events = self.owner()
        browser.close.side_effect = BrowserUnavailable('DRAIN_UNPROVED')
        with patch.object(platform_owner, '_platform_name', return_value='win32'), self.assertRaises(BrowserUnavailable) as caught:
            owner.close()
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._browser, browser)
        self.assertIs(owner._profile_store, store)
        file.close.assert_not_called()
        landing.remove.assert_not_called()
        store.close.assert_not_called()

    def test_store_still_finishes_when_prior_terminal_path_has_no_browser(self):
        owner, browser, store, file, landing, events = self.owner(native=False)
        with patch.object(platform_owner, '_platform_name', return_value='win32'):
            owner.close()
        self.assertEqual(events, ['file', 'landing', 'store'])
        self.assertIsNone(owner._profile_store)
        browser.close.assert_not_called()

    def test_partial_factory_graph_keeps_native_and_store_after_unknown_store_close(self):
        owner, browser, store, file, landing, events = self.owner()
        store.close.side_effect = OSError('fixture Store close return unknown')
        with patch.object(platform_owner, '_platform_name', return_value='win32'):
            partial = platform_owner._FactoryCleanupOwner(store, browser)
            for _ in range(2):
                with self.assertRaises(BrowserUnavailable) as caught:
                    partial.close()
                self.assertIs(caught.exception.owner, partial)
        self.assertIs(partial.native, browser)
        self.assertIs(partial._profile_store, store)
        self.assertTrue(partial.native_finished)
        browser.close.assert_called_once()
        store.close.assert_called_once()

    def test_selected_failure_closes_store_only_after_native_and_landing_and_keeps_graph_on_unknown(self):
        import test_native_fab_browser_selected as fixtures
        from angelo_agent import fab_browser_selected as selected
        helper = fixtures.SelectedTests()
        for unknown in (False, True):
            with self.subTest(unknown=unknown), helper.fixture(cancel=True) as (owner, account, page, events, stop, click):
                store = WindowsProfileStore.__new__(WindowsProfileStore)
                def close():
                    events.append(('store-closed', None))
                    if unknown:
                        raise OSError('fixture Store close return unknown')
                store.close = Mock(side_effect=close)
                owner._profile_store = store
                browser = owner._browser
                # The existing transfer fixture models file APIs; use its selected factories without changing
                # platform dispatch globally until terminal Store validation runs.
                original_close = platform_owner.close_profile_store
                def finish(current):
                    with patch.object(platform_owner, '_platform_name', return_value='win32'):
                        original_close(current)
                with patch.object(platform_owner, 'close_profile_store', side_effect=finish):
                    with self.assertRaises(BrowserUnavailable):
                        with selected.open_selected(
                                owner, fixtures.APPROVED_ITEM, fixtures.CHOICE, account, stop, 256 * 1024 * 1024):
                            pass
                labels = [entry[0] for entry in events]
                self.assertLess(labels.index('browser-drained'), labels.index('landing-removed'))
                self.assertLess(labels.index('landing-removed'), labels.index('store-closed'))
                if unknown:
                    self.assertIs(owner._browser, browser)
                    self.assertIs(owner._profile_store, store)
                    with patch.object(platform_owner, '_platform_name', return_value='win32'), self.assertRaises(BrowserUnavailable):
                        Connection.close(owner)
                else:
                    self.assertIsNone(owner._browser)
                    self.assertIsNone(owner._profile_store)
                store.close.assert_called_once()

    def test_manual_transfer_moves_exact_store_to_new_managed_owner_then_closes_once(self):
        import test_native_fab_platform_dispatch as fixtures
        from angelo_agent import fab_browser_connection as connection
        from angelo_agent.fab_browser_manual_connection import ManualConnection
        helper = fixtures.PlatformDispatchTests()
        lease = helper.lease()
        old = helper.browser(lease, manual=True)
        old.close.side_effect = lambda **kwargs: lease._row.update(state='retained')
        new_browser = helper.browser(helper.lease())
        store = helper.store(_binding=Mock(return_value=None))
        managed = Connection.__new__(Connection)
        managed._lock = threading.RLock()
        managed._browser = new_browser
        managed._profile_store = store
        managed._download_cleanup = None
        with patch.object(platform_owner, '_platform_name', return_value='win32'):
            manual = ManualConnection(old, store, threading.Event())
            manual.acknowledge_user_login(completed=True)
            with patch.object(connection, '_resume_managed_expected', return_value=managed):
                self.assertIs(manual.finish_manual_login(), managed)
            self.assertIsNone(manual._profile_store)
            self.assertIs(managed._profile_store, store)
            managed.close()
            manual.close()  # No query/reopen through already-closed native Store roots.
        store.close.assert_called_once()
        store._binding.assert_not_called()

    def test_transferred_manual_alias_refuses_unknown_managed_store_before_any_native_or_metadata_operation(self):
        import test_native_fab_platform_dispatch as fixtures
        from angelo_agent import fab_browser_connection as connection
        from angelo_agent.fab_browser_manual_connection import ManualConnection
        helper = fixtures.PlatformDispatchTests()
        lease = helper.lease(); old = helper.browser(lease, manual=True)
        old.close.side_effect = lambda **kwargs: lease._row.update(state='retained')
        new_browser = helper.browser(helper.lease())
        store = helper.store(_binding=Mock(return_value=None))
        store.close.side_effect = OSError('fixture unknown native Store close')
        managed = Connection.__new__(Connection)
        managed._lock = threading.RLock()
        managed._browser = new_browser
        managed._profile_store = store
        managed._download_cleanup = None
        with patch.object(platform_owner, '_platform_name', return_value='win32'):
            manual = ManualConnection(old, store, threading.Event())
            manual.acknowledge_user_login(completed=True)
            with patch.object(connection, '_resume_managed_expected', return_value=managed):
                self.assertIs(manual.finish_manual_login(), managed)
            self.assertIsNone(manual._profile_store)
            with self.assertRaises(BrowserUnavailable):
                managed.close()
            with self.assertRaises(BrowserUnavailable) as alias:
                manual.close()
        self.assertIs(alias.exception.owner, manual)
        self.assertIs(manual._store, store)
        self.assertIs(managed._profile_store, store)
        self.assertIs(managed._browser, new_browser)
        old.close.assert_called_once_with(retain_profile=True)
        new_browser.close.assert_called_once()
        store._binding.assert_not_called()
        store.close.assert_called_once()

    def test_actual_native_store_current_root_fault_retains_both_roots_on_every_prelease_path(self):
        import test_native_fab_windows_profile_session as fixtures
        from angelo_agent import fab_browser_connection as connection, fab_browser_process_windows as windows
        from angelo_agent.fab_browser_profile_metadata_windows import ProfileUnavailable
        for operation in ('resume', 'create', 'expected'):
            with self.subTest(operation=operation):
                helper = fixtures.WindowsProfileSessionTests()
                helper.setUp()
                try:
                    store = helper.store
                    metadata, scratch = store._metadata, store._scratch
                    fault = ProfileUnavailable('PROFILE_SID_CHANGED', owner=metadata)
                    expected = dict(version=1, sid=metadata._sid,
                                    path=str(helper.store.scratch / ('angelo-fab-browser-' + 'a' * 32)),
                                    volume=1, file_id='12' * 16, token='a' * 32, state='retained')
                    with patch.object(platform_owner, '_platform_name', return_value='win32'), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(metadata, 'verify', side_effect=fault), patch.object(windows.WindowsBrowserProcess, '__init__') as native:
                        with self.assertRaises(BrowserUnavailable) as caught:
                            if operation == 'expected':
                                connection._resume_managed_expected(store, expected)
                            else:
                                connection.create_owned_connection(store=store, resume=operation == 'resume')
                    self.assertIs(caught.exception.owner, store)
                    self.assertIs(caught.exception.native_partial_owner, store._setup)
                    self.assertIs(caught.exception.native_partial_owner._metadata, metadata)
                    self.assertIs(caught.exception.native_partial_owner._scratch, scratch)
                    self.assertIs(caught.exception.original_error, fault)
                    self.assertIs(store._metadata, metadata)
                    self.assertIs(store._scratch, scratch)
                    self.assertIs(store._fab_common_prelease_error, caught.exception)
                    native.assert_not_called()
                finally:
                    helper.doCleanups()

    def test_prelease_constructor_leaf_failure_keeps_prepublished_actual_store_graph(self):
        from angelo_agent import fab_browser_connection as connection, fab_browser_process_windows as windows
        from angelo_agent.fab_browser_profile_metadata_windows import ProfileUnavailable
        actual = []
        def initialize(store, *args, **kwargs):
            store._metadata, store._scratch = object(), object()
            actual.append(store)
            raise ProfileUnavailable('PROFILE_SID_CHANGED', owner=store._metadata)
        with patch.object(platform_owner, '_platform_name', return_value='win32'), patch.object(windows, 'chrome_program', return_value='fixture-chrome.exe'), patch.object(WindowsProfileStore, '__init__', initialize), patch.object(windows.WindowsBrowserProcess, '__init__') as native:
            with self.assertRaises(BrowserUnavailable) as caught:
                connection.create_owned_connection()
        self.assertIs(caught.exception.owner, actual[0])
        self.assertIs(caught.exception.native_partial_owner, actual[0]._metadata)
        self.assertIsNotNone(actual[0]._scratch)
        self.assertIs(actual[0]._fab_common_prelease_error, caught.exception)
        native.assert_not_called()
