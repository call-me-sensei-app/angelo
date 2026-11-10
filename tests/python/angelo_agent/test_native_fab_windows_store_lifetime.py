# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Persistent-store roles/ownership with native boundaries modelled; no Windows readiness."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager
import ctypes
from pathlib import PureWindowsPath
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_fab_windows_profile_session as fixtures
from angelo_agent import fab_browser_windows_store_setup as setup

profile = fixtures.profile


class RootAPI:
    def __init__(self):
        self.calls, self.handles = [], {}
        self.next, self.pending, self.fail_close = 1, False, False
    def known(self):
        if self.pending:raise profile.ProfileUnavailable('PROFILE_NATIVE_OPERATION_UNPROVED')
    def current_sid(self):return 'fixture-sid'
    def require_local_drive(self, drive):self.calls.append(('local', drive))
    def acquire(self, path, output):
        output.value = self.next
        self.next += 1
        self.handles[output.value] = str(path)
    def open_directory_into(self, path, output):
        self.calls.append(('ancestor_read', str(path)))
        self.acquire(path, output)
    def ensure_persistent_directory_into(self, parent, name, sid, output):
        self.calls.append(('persistent', name))
        self.acquire(PureWindowsPath(self.handles[parent.value]) / name, output)
        return 'opened'
    def create_directory_into(self, parent, name, sid, output):
        self.calls.append(('removable', name))
        self.acquire(PureWindowsPath(self.handles[parent.value]) / name, output)
        return True
    def _open(self, parent, name, output, **options):
        self.calls.append(('relative', options))
        self.acquire(PureWindowsPath(self.handles[parent.value]) / name, output)
    def information(self, handle):return (7, handle.value.to_bytes(16, 'little'))
    def verify_acl(self, handle, sid):self.calls.append(('acl', handle.value))
    def final_path(self, handle):return self.handles[handle.value]
    def close(self, handle):
        self.known()
        self.calls.append(('close', handle.value))
        if self.fail_close:
            self.pending = True
            raise profile.ProfileUnavailable('PROFILE_HANDLE_CLOSE_UNPROVED')
        handle.value = None
    def cleanup(self):self.known()


class PersistentRootTests(unittest.TestCase):
    def root(self, *, persistent=False, create=True):
        api = RootAPI()
        with patch.object(profile, 'Path', PureWindowsPath):
            owner = profile._PinnedRoot('C:/app/scratch/fab-browser-windows', create=create,
                                        persistent=persistent, api=api)
        return owner, api

    def test_persistent_creation_uses_atomic_open_if_and_cannot_remove_directory(self):
        owner, api = self.root(persistent=True)
        self.assertIn(('persistent', 'fab-browser-windows'), api.calls)
        self.assertFalse(any(row[0] == 'removable' for row in api.calls))
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PERSISTENT_ROOT_NOT_REMOVABLE'):
            owner.remove(0)
        owner.release()

    def test_existing_persistent_root_requests_read_directory_without_delete(self):
        owner, api = self.root(persistent=True, create=False)
        self.assertIn(('relative', {'directory':True, 'delete':False, 'no_reparse':True}), api.calls)
        owner.release()

    def test_default_removable_role_keeps_original_creator(self):
        owner, api = self.root()
        self.assertIn(('removable', 'fab-browser-windows'), api.calls)
        self.assertFalse(any(row[0] == 'persistent' for row in api.calls))
        owner.release()

    def test_known_root_release_is_idempotent(self):
        owner, api = self.root(persistent=True)
        owner.release()
        calls = list(api.calls)
        owner.release()
        self.assertEqual(api.calls, calls)
        self.assertTrue(owner._released)

    def test_unknown_root_close_never_retries_numeric_handle_or_releases_ancestors(self):
        owner, api = self.root(persistent=True)
        api.fail_close = True
        with self.assertRaises(profile.ProfileUnavailable):owner.release()
        self.assertTrue(all(handle.value for handle in owner._handles))
        calls = list(api.calls)
        with self.assertRaises(profile.ProfileUnavailable):owner.release()
        self.assertEqual(api.calls, calls)
        self.assertFalse(owner._released)


class StoreLifetimeTests(unittest.TestCase):
    setUp = fixtures.WindowsProfileSessionTests.setUp
    store_new = fixtures.WindowsProfileSessionTests.store_new
    browser = fixtures.WindowsProfileSessionTests.browser
    drain = fixtures.WindowsProfileSessionTests.drain

    def test_store_adopts_the_exact_setup_roots_without_reopening_them(self):
        roots = self.store._setup
        self.assertIs(self.store._metadata, roots._metadata)
        self.assertIs(self.store._scratch, roots._scratch)
        self.assertIs(roots._store_owner, self.store)
        self.assertEqual(self.store.directory, self.directory)
        self.assertEqual(self.store.binding_directory, self.directory / setup.PRIVATE_LEAF)
        self.assertEqual(self.store.scratch, self.scratch / setup.PRIVATE_LEAF)

    def test_global_lock_precedes_private_lock_and_registry_stays_at_global_directory(self):
        self.model.events.clear()
        lease = self.store.create()
        events = self.model.events
        global_index = next(i for i, row in enumerate(events) if row[0] == 'global_enter')
        private_index = next(i for i, row in enumerate(events) if row[0] == 'private_lock')
        exit_index = next(i for i, row in enumerate(events) if row[0] == 'global_exit')
        self.assertLess(global_index, private_index)
        self.assertLess(private_index, exit_index)
        self.assertTrue(events[private_index][2])
        self.assertTrue(events[private_index][1].startswith(str(self.store.binding_directory)))
        self.assertIn(str(lease.path), self.remembered)

    def test_known_store_close_releases_all_persistent_roots_once(self):
        self.store.close()
        roots = self.store._setup
        self.assertTrue(roots._released)
        self.assertTrue(self.store._closed)
        count = len(self.model.closed)
        self.store.close()
        self.assertEqual(len(self.model.closed), count)
        self.assertIn(str(self.store.binding_directory), self.model.nodes)
        self.assertIn(str(self.store.scratch), self.model.nodes)

    def test_active_profile_blocks_store_release_until_exact_lease_finishes(self):
        lease = self.store.create()
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'LEASE_STILL_OWNED'):
            self.store.close()
        self.assertFalse(self.store._setup._released)
        lease.cancel_pending()
        self.store.close()
        self.assertTrue(self.store._closed)

    def test_unknown_store_release_latches_whole_graph_and_cannot_retry(self):
        roots = self.store._setup
        self.model.fail_close_key = str(self.store.scratch)
        with self.assertRaises(profile.ProfileUnavailable) as caught:self.store.close()
        self.assertIs(caught.exception.owner, self.store)
        self.assertTrue(self.store._close_failed)
        count = len(self.model.closed)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'STORE_CLOSE_UNPROVED'):self.store.close()
        self.assertEqual(len(self.model.closed), count)
        self.assertTrue(all(parent._handles[0].value for parent in roots._parents))

    def test_unadopted_partial_setup_owner_can_release_known_roots(self):
        self.store.close()
        seen = []
        def partial(roots, directory, scratch):
            seen.append(roots)
            self.setup_builder(roots, directory, scratch)
            roots._scratch.release()
            roots._scratch = None
            roots._complete = False
            raise profile.ProfileUnavailable('PROFILE_SETUP_PARTIAL', owner=roots)
        with patch.object(setup._SetupRoots, '__init__', partial):
            with self.assertRaises(profile.ProfileUnavailable) as caught:self.store_new()
        owner = caught.exception.owner
        roots = seen[0]
        self.assertIs(owner._setup, roots)
        self.assertFalse(owner._ready)
        owner.close()
        self.assertTrue(roots._released)
        self.assertTrue(owner._closed)

    def test_other_store_cannot_adopt_or_release_a_previously_claimed_setup(self):
        roots = self.store._setup
        with patch.object(setup._SetupRoots, '__new__', return_value=roots):
            with self.assertRaisesRegex(profile.ProfileUnavailable, 'STORE_OWNER_REQUIRED') as caught:
                self.store_new()
        caught.exception.owner.close()
        self.assertIs(roots._store_owner, self.store)
        self.assertFalse(roots._released)
        self.store._current()

    def test_setup_graph_is_published_before_resourceful_initialization_or_lost_return(self):
        owner = profile.WindowsProfileStore.__new__(profile.WindowsProfileStore)
        def lost(roots, directory, scratch):
            self.assertIs(owner._setup, roots)
            self.setup_builder(roots, directory, scratch)
            raise KeyboardInterrupt('fixture setup lost return')
        with patch.object(setup._SetupRoots, '__init__', lost):
            with self.assertRaises(KeyboardInterrupt) as caught:
                profile.WindowsProfileStore.__init__(owner, self.directory, self.scratch,
                                                     _root_factory=fixtures.root_factory(self.model))
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._setup._store_owner, owner)
        owner.close()
        self.assertTrue(owner._closed)

    def test_interruption_before_setup_initialization_preserves_primary_and_empty_owner(self):
        owner = profile.WindowsProfileStore.__new__(profile.WindowsProfileStore)
        primary = KeyboardInterrupt('fixture before setup fields initialize')
        with patch.object(setup._SetupRoots, '__init__', side_effect=primary):
            with self.assertRaises(KeyboardInterrupt) as caught:
                profile.WindowsProfileStore.__init__(owner, self.directory, self.scratch)
        self.assertIs(caught.exception, primary)
        self.assertIs(primary.owner, owner)
        self.assertIs(owner._setup._store_owner, owner)
        owner.close()
        self.assertTrue(owner._closed)

    def test_global_cleanup_failure_preserves_primary_cancellation_and_store_graph(self):
        original = fixtures.connections.metadata_lock.side_effect
        @contextmanager
        def bad_exit(directory):
            with original(directory):
                try:yield
                finally:raise OSError('fixture unknown global lock close')
        primary = KeyboardInterrupt('fixture cancellation')
        with patch.object(fixtures.connections, 'metadata_lock', side_effect=bad_exit):
            with self.assertRaises(KeyboardInterrupt) as caught:
                with self.store._transaction():raise primary
        self.assertIs(caught.exception, primary)
        self.assertIs(primary.owner, self.store)
        self.assertIsInstance(primary.cleanup_error, OSError)
        self.assertIsNotNone(self.store._global_context)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'METADATA_CLOSE_UNPROVED'):self.store.close()

    def legacy_refusal(self):
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'LEGACY_LAYOUT_REQUIRES_RECOVERY') as caught:
            self.store_new()
        owner = caught.exception.owner
        self.assertFalse(owner._ready)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'STORE_SETUP_UNAVAILABLE'):owner.create()
        self.assertFalse(any(key.startswith(str(owner.scratch / profile.private_roots.FAB_BROWSER_PREFIX))
                             for key in self.model.nodes))
        owner.close()

    def test_legacy_global_binding_is_preserved_and_prevents_fresh_profile(self):
        self.store.close()
        key = str(self.directory) + '/' + profile.BINDING   # The model's own key form (fixtures: parent + '/' + name).
        self.model.node(key)['data'] = b'legacy bytes, never interpreted as account proof'
        self.legacy_refusal()
        self.assertEqual(self.model.nodes[key]['data'], b'legacy bytes, never interpreted as account proof')

    def test_legacy_direct_scratch_registry_record_requires_recovery(self):
        self.store.close()
        legacy = str(self.scratch / (profile.private_roots.FAB_BROWSER_PREFIX + 'old'))
        with patch.object(profile.private_roots, 'retained_profiles', return_value=[legacy]):
            self.legacy_refusal()

    def test_legacy_direct_scratch_native_entry_is_not_deleted_or_replaced(self):
        self.store.close()
        key = str(self.scratch) + '/' + profile.private_roots.FAB_BROWSER_PREFIX + 'unregistered'   # The model's key form.
        self.model.node(key)
        self.legacy_refusal()
        self.assertIn(key, self.model.nodes)

    def test_current_download_landing_after_startup_never_blocks_profile_finish(self):
        lease = self.store.create()
        browser = self.browser(lease)
        key = str(self.scratch / (profile.private_roots.FAB_BROWSER_PREFIX + 'current-landing'))
        self.model.node(key)
        self.drain(browser)
        lease.finish_drained(browser, retain=True)
        self.assertEqual(lease._row['state'], 'retained')
        self.assertIn(key, self.model.nodes)


if __name__ == '__main__':unittest.main()
