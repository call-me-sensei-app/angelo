# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed browser connection fixtures; never authentication, entitlement or live transfer acceptance."""
import suite_isolation  # noqa: F401

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.fab_browser_connection import Connection, normalize_links
from angelo_agent.fab_browser_process import BrowserProcess, BrowserUnavailable

UID = '05d847c0-7ab9-435c-8ca6-d18216b01c17'
ROW = dict(href='https://www.fab.com/listings/' + UID, title='Public listing')


class Browser:
    def __init__(self, root):
        self.calls, self.closed = [], False
        self.value = dict(state='site_visible')
    def call(self, method, params=None, **kw):
        self.calls.append((method, params, kw))
        if method == 'Target.createTarget': return dict(targetId='target')
        if method == 'Target.attachToTarget': return dict(sessionId='session')
        if method == 'Runtime.evaluate': return dict(result=dict(value=self.value))
        return {}
    def close(self): self.closed = True


class FabBrowserConnectionTests(unittest.TestCase):
    def connection(self):
        connection = Connection('/not-read-by-fake', browser_factory=Browser)
        self.addCleanup(connection.close)
        return connection, connection._browser

    def test_visible_page_never_claims_verified_account(self):
        connection, browser = self.connection()
        self.assertEqual(connection.state(), dict(state='site_visible', account_verified=False, downloads_enabled=False))
        self.assertEqual(browser.calls[0], ('Browser.setDownloadBehavior', {'behavior': 'deny'}, {}))

    def test_initial_target_is_attached_before_explicit_fab_navigation(self):
        connection, browser = self.connection()
        self.assertEqual([call[0] for call in browser.calls[:4]],
                         ['Browser.setDownloadBehavior', 'Target.createTarget', 'Target.attachToTarget', 'Page.navigate'])
        self.assertEqual(browser.calls[1][1], {'url': 'about:blank'})
        self.assertEqual(browser.calls[3], ('Page.navigate', {'url': 'https://www.fab.com/'}, {'session': 'session'}))

    def test_initial_navigation_failure_closes_and_refuses(self):
        browser = Browser(None)
        original = browser.call
        def call(method, *args, **kwargs):
            if method == 'Page.navigate':return {'errorText': 'private browser error must not escape'}
            return original(method, *args, **kwargs)
        with patch.object(browser, 'call', side_effect=call):
            with self.assertRaises(BrowserUnavailable) as caught:
                Connection(browser_factory=lambda root: browser)
        self.assertEqual(caught.exception.code, 'NAVIGATION_FAILED')
        self.assertNotIn('private browser', str(caught.exception))
        self.assertTrue(browser.closed)

    def test_login_is_user_handoff_not_token_exchange(self):
        connection, browser = self.connection()
        browser.value = dict(state='user_sign_in_pending')
        self.assertEqual(connection.begin_login()['state'], 'user_sign_in_pending')
        self.assertFalse(connection.begin_login()['account_verified'])
        self.assertFalse(any(method.startswith(('Network.', 'Storage.')) for method, _, _ in browser.calls))

    def test_search_is_encoded_and_rows_stay_unverified(self):
        connection, browser = self.connection()
        query = "rock & material'; alert(1)//"
        connection.start_search(query)
        navigations = [params['url'] for method, params, _ in browser.calls if method == 'Page.navigate']
        self.assertEqual(navigations, ['https://www.fab.com/',
                                      'https://www.fab.com/search?q=rock%20%26%20material%27%3B%20alert%281%29%2F%2F'])
        browser.value = dict(state='public_metadata', links=[ROW])
        result = connection.public_results()
        self.assertEqual(len(result['items']), 1)
        self.assertFalse(result['items'][0]['import_ready'])
        self.assertEqual(result['items'][0]['access'], 'unverified')
        self.assertNotIn('variants', result['items'][0])

    def test_new_connection_has_no_search_or_entitlement(self):
        connection, _browser = self.connection()
        with self.assertRaisesRegex(BrowserUnavailable, 'SEARCH_REQUIRED'):
            connection.public_results()
        self.assertFalse(hasattr(connection, 'authorize'))
        self.assertFalse(hasattr(connection, 'stream'))

    def test_link_admission_refuses_credentials_hosts_and_invalid_ids(self):
        for href in ('https://www.fab.com.evil.test/listings/' + UID,
                     'https://user:password@www.fab.com/listings/' + UID,
                     ROW['href'] + '?token=private', ROW['href'] + '/extra', '\n' + ROW['href']):
            with self.subTest(href=href), self.assertRaises(BrowserUnavailable):
                normalize_links([dict(ROW, href=href)], 1)
        self.assertEqual(len(normalize_links([ROW, ROW], 2)), 1)

    def test_corrupt_or_unknown_page_reply_refuses(self):
        connection, browser = self.connection()
        browser.value = {'state': 'authenticated', 'token': 'not-an-accepted-account-proof'}
        with self.assertRaisesRegex(BrowserUnavailable, 'SITE_CHANGED'):
            connection.state()

    def test_disconnect_revokes_public_calls(self):
        connection, browser = self.connection()
        connection.close()
        self.assertTrue(browser.closed)
        with self.assertRaisesRegex(BrowserUnavailable, 'CLOSED'):
            connection.state()

    def test_failed_process_drain_retains_profile_and_unreaped_identity(self):
        with tempfile.TemporaryDirectory() as root:
            process = BrowserProcess.__new__(BrowserProcess)
            import threading
            process._lock = threading.RLock()
            process._pid, process._read, process._write = 12345, None, None
            process._closed = False
            process.profile = Path(root)
            info = process.profile.stat()
            process._profile_identity = info.st_dev, info.st_ino
            from types import SimpleNamespace
            process._owner = SimpleNamespace(drain=lambda: (_ for _ in ()).throw(RuntimeError('unproved')))
            with patch('os.killpg', create=True) as unused, \
                    patch('os.kill') as kill, patch('os.waitpid') as reap:
                with self.assertRaisesRegex(BrowserUnavailable, 'DRAIN_UNPROVED'):
                    process.close()
            kill.assert_not_called()
            reap.assert_not_called()
            self.assertEqual(process._pid, 12345)
            self.assertTrue(process.profile.is_dir())


if __name__ == '__main__':
    unittest.main()
