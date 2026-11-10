# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual normal-quit ownership models. No native app, signal, browser, network or account interaction."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import signal
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_manual_close as close, fab_browser_ownership as ownership
from angelo_agent.fab_browser_process import BrowserUnavailable
import host_test_policy  # noqa: E402
if sys.platform != 'darwin':   # The macOS profile store (fcntl, Chrome's macOS bundle); Windows has its own.
    raise unittest.SkipTest(host_test_policy.environment_skip(
        'the macOS Fab profile store; Windows runs test_native_fab_windows_*, Linux has no Fab browser'))
import test_native_fab_browser_profile as profile_fixtures


class ManualCloseTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        helper = profile_fixtures.FabProfileTests()
        with helper.store() as store:
            lease = store.create()
            browser = helper.process(lease)
            browser._manual_login = True
            browser._closed = True
            browser._pid = 41
            browser._leader_identity = (41, 'original-start', profile_fixtures.PROGRAM)
            browser._manual_retention_revoked = False
            browser._manual_cancel_signal_used = False
            browser._manual_helper_drained = False
            browser._manual_graceful_drained = False
            browser._manual_singletons = None
            owner = ownership.ProcessOwner(41, 'a' * 32, profile_fixtures.PROGRAM, str(lease.path))
            owner.known[41] = browser._leader_identity
            browser._owner = owner
            row = dict(pid=41, parent=40, group=41, start='original-start',
                       program=profile_fixtures.PROGRAM, zombie=False)
            child = dict(pid=42, parent=41, group=41, start='helper-start',
                         program='/Applications/Fixture.app/Contents/Helpers/Renderer', zombie=False)
            state = {41: row, 42: child}
            raw = profile_fixtures.PROGRAM + ' --user-data-dir=' + str(lease.path) + ' --new-window https://www.fab.com/'
            marker = lease.path / '.angelo-private-owner'
            original_marker = marker.read_bytes()
            try:
                with patch.object(ownership, 'snapshot', side_effect=lambda deadline: dict(state)), patch.object(ownership, '_run', return_value=raw):
                    yield browser, lease, owner, state, raw
            finally:
                if lease.path.exists():
                    marker.write_bytes(original_marker)
                browser._manual_login = False
                browser._pid = None
                if not lease._released:
                    lease._browser = browser
                browser.close()
                if store._binding() is not None:
                    current = store.resume_expected(dict(lease._row))
                    helper.process(current).close()

    def test_normal_request_only_then_two_complete_empty_observations_without_any_signal(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            terminate = Mock(side_effect=lambda pid: (state.clear(), True)[1])
            with patch.object(close, '_application_api', return_value=nullcontext((Mock(), terminate))), patch.object(close.os, 'kill') as kill:
                close.drain_manual(browser, retain=True)
            terminate.assert_called_once_with(41)
            kill.assert_not_called()
            self.assertTrue(browser._manual_helper_drained)
            self.assertEqual(owner.known[42], (42, 'helper-start', '/Applications/Fixture.app/Contents/Helpers/Renderer'))

    def test_already_exited_leader_after_request_does_not_invent_a_signal_or_skip_drain(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            terminate = Mock(side_effect=lambda pid: (state.clear(), False)[1])
            with patch.object(close, '_application_api', return_value=nullcontext((Mock(), terminate))), patch.object(close.os, 'kill') as kill:
                close.drain_manual(browser, retain=True)
            self.assertTrue(browser._manual_helper_drained)
            self.assertFalse(browser._manual_graceful_drained)  # Only the caller's actual waitpid normal0 may set it.
            kill.assert_not_called()

    def test_normal_request_refusal_never_signal_falls_back_for_retention(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            with patch.object(close, '_application_api', return_value=nullcontext((Mock(), Mock(return_value=False)))), patch.object(close.os, 'kill') as kill:
                with self.assertRaises(BrowserUnavailable) as caught:
                    close.drain_manual(browser, retain=True)
            self.assertEqual(caught.exception.reason, 'MANUAL_NORMAL_QUIT_REFUSED')
            self.assertFalse(browser._manual_helper_drained)
            kill.assert_not_called()

    def test_cancel_fallback_signals_only_revalidated_original_leader_then_requires_full_drain(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            with patch.object(close, '_application_api', return_value=nullcontext((Mock(), Mock(return_value=False)))), patch.object(close.os, 'kill', side_effect=lambda pid, sig: state.clear()) as kill:
                close.drain_manual(browser, retain=False)
            kill.assert_called_once_with(41, signal.SIGTERM)
            self.assertTrue(browser._manual_helper_drained)

    def test_request_success_does_not_replace_helper_drain_proof(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            with patch.object(close, '_application_api', return_value=nullcontext((Mock(), Mock(return_value=True)))), patch.object(owner, '_observe', side_effect=ownership.OwnershipUnavailable('Browser processes did not drain')), patch.object(close.os, 'kill') as kill:
                with self.assertRaises(ownership.OwnershipUnavailable):
                    close.drain_manual(browser, retain=True)
            self.assertFalse(browser._manual_helper_drained)
            kill.assert_not_called()

    def test_reused_live_leader_refuses_before_native_request_or_signal(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            state[41] = dict(state[41], start='different-start')
            with patch.object(close, '_application_api') as native, patch.object(close.os, 'kill') as kill:
                with self.assertRaises(ownership.OwnershipUnavailable):
                    close.drain_manual(browser, retain=False)
            native.assert_not_called()
            kill.assert_not_called()

    def test_duplicate_or_changed_typed_profile_argument_never_requests_or_signals(self):
        for suffix in (' --user-data-dir=/other-profile', 'changed'):
            with self.subTest(suffix=suffix), self.fixture() as (browser, lease, owner, state, raw):
                altered = raw + suffix if suffix.startswith(' ') else raw.replace(str(lease.path), '/other-profile')
                with patch.object(ownership, '_run', return_value=altered), patch.object(close, '_application_api') as native, patch.object(close.os, 'kill') as kill:
                    with self.assertRaises(BrowserUnavailable) as caught:
                        close.drain_manual(browser, retain=False)
                self.assertEqual(caught.exception.reason, 'MANUAL_PROFILE_ARGUMENT_UNPROVED')
                native.assert_not_called()
                kill.assert_not_called()

    def test_changed_private_owner_token_refuses_before_request_or_signal(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            (lease.path / '.angelo-private-owner').write_text('b' * 32)
            with patch.object(close, '_application_api') as native, patch.object(close.os, 'kill') as kill:
                with self.assertRaises(BrowserUnavailable):
                    close.drain_manual(browser, retain=False)
            native.assert_not_called()
            kill.assert_not_called()

    def test_normal_zero_and_full_drain_required_before_stale_links_can_retire(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            browser._manual_singletons = {}
            browser._pid = None
            with self.assertRaises(BrowserUnavailable):
                close.retire_singletons(browser)
            browser._manual_graceful_drained = True
            with self.assertRaises(BrowserUnavailable):
                close.retire_singletons(browser)

    def test_exact_owned_stale_symlinks_retire_without_touching_external_targets(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            target = lease.path.parent / 'unrelated-target'
            target.write_bytes(b'known-nonsecret-fixture')
            (lease.path / 'SingletonLock').symlink_to('fixture-host-41')
            (lease.path / 'SingletonSocket').symlink_to(target)
            browser._manual_singletons = close._singletons(lease)
            browser._manual_graceful_drained = browser._manual_helper_drained = True
            browser._pid = None
            close.retire_singletons(browser)
            self.assertFalse(os.path.lexists(lease.path / 'SingletonLock'))
            self.assertFalse(os.path.lexists(lease.path / 'SingletonSocket'))
            self.assertEqual(target.read_bytes(), b'known-nonsecret-fixture')
            target.unlink()

    def test_replaced_singleton_is_not_unlinked_and_blocks_retention(self):
        with self.fixture() as (browser, lease, owner, state, raw):
            link = lease.path / 'SingletonLock'
            link.symlink_to('fixture-host-41')
            browser._manual_singletons = close._singletons(lease)
            link.unlink()
            link.symlink_to('other-host-99')
            browser._manual_graceful_drained = browser._manual_helper_drained = True
            browser._pid = None
            with self.assertRaises(BrowserUnavailable) as caught:
                close.retire_singletons(browser)
            self.assertEqual(caught.exception.reason, 'MANUAL_SINGLETON_CHANGED')
            self.assertEqual(os.readlink(link), 'other-host-99')
            self.assertEqual(lease.store._binding()['state'], 'pending')
            with patch.object(close.os, 'waitpid') as wait:
                with self.assertRaises(BrowserUnavailable) as repeated:
                    browser.close(retain_profile=True)
            self.assertIs(repeated.exception.owner, browser)
            self.assertEqual(repeated.exception.reason, 'MANUAL_SINGLETON_CHANGED')
            self.assertEqual(lease.store._binding()['state'], 'pending')
            wait.assert_not_called()

    def test_cancel_failure_can_never_retry_into_retention_even_with_later_normal_zero(self):
        for signal_fallback in (True, False):
            with self.subTest(signal_fallback=signal_fallback), self.fixture() as (browser, lease, owner, state, raw):
                terminate = Mock(return_value=not signal_fallback)
                def kill(pid, sig):
                    self.assertTrue(browser._manual_cancel_signal_used)
                    state.clear()
                # Skip the optional two-second cooperative observation in the no-signal model.
                def leader(current, deadline):
                    return state.get(41) if signal_fallback else None
                with patch.object(close, '_application_api', return_value=nullcontext((Mock(), terminate))), patch.object(close, '_leader', side_effect=leader), patch.object(close.os, 'kill', side_effect=kill) as signal_call, patch.object(owner, '_observe', side_effect=ownership.OwnershipUnavailable('Browser processes did not drain')):
                    with self.assertRaises(BrowserUnavailable):
                        browser.close(retain_profile=False)
                self.assertTrue(browser._manual_retention_revoked)
                self.assertEqual(browser._manual_cancel_signal_used, signal_fallback)
                self.assertEqual(signal_call.call_count, int(signal_fallback))
                for remaining_pid in (41, None):
                    browser._pid = remaining_pid
                    with patch.object(close.os, 'waitpid', return_value=(41, 0)) as wait:
                        with self.assertRaises(BrowserUnavailable) as caught:
                            browser.close(retain_profile=True)
                    self.assertIs(caught.exception.owner, browser)
                    self.assertEqual(caught.exception.reason, 'MANUAL_CANCEL_SIGNAL_USED' if signal_fallback else 'MANUAL_RETENTION_REVOKED')
                    self.assertEqual(lease.store._binding()['state'], 'pending')
                    wait.assert_not_called()


if __name__ == '__main__':
    unittest.main()
