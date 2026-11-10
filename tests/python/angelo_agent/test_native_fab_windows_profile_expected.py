# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Atomic expected-row reopening; fake native I/O, no Windows persistence acceptance."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager
import json
from pathlib import Path
from unittest import mock
import unittest

import test_native_fab_windows_profile_session as fixtures

if Path(fixtures.__file__).resolve() != Path(__file__).with_name('test_native_fab_windows_profile_session.py').resolve():
    raise RuntimeError('Expected the frozen sibling native model fixture')

profile = fixtures.profile
if Path(profile.__file__).resolve() != (Path(__file__).resolve().parents[3] /
        'scripts/addons_core/angelo_agent/fab_browser_profile_session_windows.py'):
    raise RuntimeError('Expected the composed runtime Windows profile module')


class WindowsExpectedProfileTests(unittest.TestCase):
    # Reuse only the accepted native model/setup, never inherit or replay its25 methods.
    setUp = fixtures.WindowsProfileSessionTests.setUp
    store_new = fixtures.WindowsProfileSessionTests.store_new
    browser = fixtures.WindowsProfileSessionTests.browser
    drain = fixtures.WindowsProfileSessionTests.drain

    def retained(self):
        lease = self.store.create()
        browser = self.browser(lease)
        self.drain(browser)
        lease.finish_drained(browser, retain=True)
        return lease, dict(lease._row)

    def test_matching_row_reopens_same_full_identity_and_holds_lifetime_lock(self):
        old, expected = self.retained()
        store = self.store_new()
        opened = store.resume_expected(expected)
        self.assertEqual((opened.path, opened.identity, opened.generation),
                         (old.path, old.identity, old.generation))
        self.assertEqual(opened._row['state'], 'pending')
        self.assertEqual(expected['state'], 'retained')
        self.assertIn(str(opened.path) + '/' + profile.PROFILE_LOCK, self.model.locks)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_RECOVERY_REQUIRED'):
            self.store_new().resume()

    def test_replacement_retained_binding_is_refused_before_any_lease_initialization(self):
        _, expected = self.retained()
        # Actual model-owned replacement, with a different token/root/fullidentity.
        remover = self.store_new().resume()
        remover.cancel_pending()
        replacement = self.store_new().create()
        browser = self.browser(replacement)
        self.drain(browser)
        replacement.finish_drained(browser, retain=True)
        store = self.store_new()
        before = dict(store._binding())
        with mock.patch.object(profile.WindowsProfileLease, '_initialize') as initialize:
            with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_CHANGED'):
                store.resume_expected(expected)
        initialize.assert_not_called()
        self.assertEqual(store._binding(), before)
        self.assertIsNone(store._partial)
        self.assertIn(str(replacement.path), self.model.nodes)

    def test_replacement_after_transaction_acquisition_is_checked_inside_that_lock(self):
        _, expected = self.retained()
        store = self.store_new()
        transaction = store._transaction
        changed = dict(expected, volume=expected['volume'] + 1)
        @contextmanager
        def replace_under_lock():
            with transaction():
                self.assertIn(str(self.store.binding_directory) + '/connections.lock', self.model.locks)
                store._write(changed)
                yield
        with mock.patch.object(store, '_transaction', replace_under_lock), \
                mock.patch.object(profile.WindowsProfileLease, '_initialize') as initialize:
            with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_CHANGED'):
                store.resume_expected(expected)
        initialize.assert_not_called()
        self.assertEqual(store._binding(), changed)

    def test_expected_row_is_detached_before_entering_transaction(self):
        old, expected = self.retained()
        store = self.store_new()
        transaction = store._transaction
        @contextmanager
        def mutate_callers_dict():
            with transaction():
                expected['token'] = ('1' if old.generation[0] != '1' else '2') + old.generation[1:]
                yield
        with mock.patch.object(store, '_transaction', mutate_callers_dict):
            opened = store.resume_expected(expected)
        self.assertEqual(opened.generation, old.generation)
        self.assertNotEqual(opened.generation, expected['token'])
        self.assertEqual(opened.identity, old.identity)

    def test_malformed_expected_rows_refuse_before_metadata_transaction(self):
        _, expected = self.retained()
        class DictSubclass(dict):
            pass
        invalid = [None, DictSubclass(expected), dict(expected, extra=0),
                   dict(expected, version=True), dict(expected, volume=False),
                   dict(expected, volume=2**64), dict(expected, file_id='0' * 31),
                   dict(expected, token='G' * 32), dict(expected, state='pending'),
                   dict(expected, sid=1), dict(expected, path=object())]
        store = self.store_new()
        with mock.patch.object(store, '_transaction') as transaction:
            for row in invalid:
                with self.subTest(row_type=type(row).__name__), \
                        self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_METADATA_SHAPE'):
                    store.resume_expected(row)
        transaction.assert_not_called()
        self.assertIsNone(store._partial)

    def test_missing_binding_does_not_create_or_open_a_substitute(self):
        _, expected = self.retained()
        self.store_new().resume().cancel_pending()
        store = self.store_new()
        with mock.patch.object(profile.WindowsProfileLease, '_initialize') as initialize:
            with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_MISSING'):
                store.resume_expected(expected)
        initialize.assert_not_called()
        self.assertIsNone(store._binding())
        self.assertIsNone(store._partial)

    def test_native_identity_change_after_row_match_preserves_exact_partial_owner(self):
        old, expected = self.retained()
        node = self.model.nodes[str(old.path)]
        node['identity'] = (old.identity[0], bytes([old.identity[1][0] ^ 128]) + old.identity[1][1:])
        store = self.store_new()
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_CHANGED') as caught:
            store.resume_expected(expected)
        partial = store._partial
        self.assertIs(caught.exception.owner, partial)
        self.assertIsNotNone(partial._root)
        self.assertFalse(partial._released)
        self.assertTrue(partial._root._handles[0].value)
        self.assertEqual(store._binding(), expected)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_LEASE_STILL_OWNED'):
            store.close()

    def test_pending_publish_failure_keeps_lifetime_lock_and_partial_lease(self):
        _, expected = self.retained()
        store = self.store_new()
        self.model.fail_write = True
        with self.assertRaises(profile.ProfileUnavailable) as caught:
            store.resume_expected(expected)
        partial = store._partial
        self.assertIs(caught.exception.owner, partial)
        self.assertFalse(partial._released)
        self.assertIn(str(partial.path) + '/' + profile.PROFILE_LOCK, self.model.locks)
        self.assertEqual(store._binding(), expected)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_LEASE_STILL_OWNED'):
            store.close()

    def test_metadata_lock_close_failure_does_not_lose_successfully_opened_lease(self):
        _, expected = self.retained()
        store = self.store_new()
        transaction = store._transaction
        @contextmanager
        def fail_transaction_close():
            with transaction():
                yield
                self.model.fail_close_key = str(self.store.binding_directory) + '/connections.lock'
        with mock.patch.object(store, '_transaction', fail_transaction_close):
            with self.assertRaises(profile.ProfileUnavailable) as caught:
                store.resume_expected(expected)
        partial = store._partial
        self.assertIs(caught.exception.owner, partial)
        self.assertFalse(partial._released)
        self.assertTrue(store._uncertain)
        self.assertIn(str(partial.path) + '/' + profile.PROFILE_LOCK, self.model.locks)
        row = json.loads(self.model.nodes[str(self.store.binding_directory) + '/' + profile.BINDING]['data'])
        self.assertEqual(row['state'], 'pending')
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_METADATA_CLOSE_UNPROVED'):
            store.close()


if __name__ == '__main__':
    unittest.main()
