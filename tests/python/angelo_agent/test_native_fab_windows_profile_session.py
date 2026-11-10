# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure ownership/state fixtures; no Windows ABI, Chrome or persistence acceptance."""
import suite_isolation  # noqa: F401
import ctypes
from contextlib import contextmanager
import json
from pathlib import Path, PureWindowsPath
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_profile_session_windows as profile
from angelo_agent import connections, fab_browser_windows_store_setup as setup
from angelo_agent.fab_browser_process_windows import WindowsBrowserProcess
from angelo_agent.fab_browser_profile_windows import _native_structures


class Model:
    def __init__(self):
        self.nodes, self.handles, self.locks, self.closed, self.events = {}, {}, {}, set(), []
        self.next = 1
        self.sid = 'S-1-5-21-100'
        self.fail_write = self.fail_close = self.pending = self.busy = False
        self.fail_close_key = None
        self.fail_retained_commit_close = False
        self.fail_acl = self.reparse = False
        self.global_locked = False

    def node(self, key):
        return self.nodes.setdefault(key, dict(identity=(7, len(self.nodes).to_bytes(16, 'little')),
                                              data=b'', position=0))

    def opened(self, key, output):
        output.value = self.next
        self.next += 1
        self.handles[output.value] = key


class FakeAPI:
    def __init__(self, model):
        self.m = model
        self._session_pending = None

    def known(self):
        if self.m.pending or self._session_pending is not None:
            raise profile.ProfileUnavailable('PROFILE_NATIVE_OPERATION_UNPROVED', owner=self)

    def file_into(self, parent, name, sid, output, *, create=False, **kwargs):
        self.known()
        key = self.m.handles[parent.value] + '/' + name
        if create and key in self.m.nodes or not create and key not in self.m.nodes:
            return False
        self.m.node(key)
        self.m.nodes[key]['position'] = 0
        self.m.opened(key, output)
        self.m.events.append(('open', name, create, kwargs))
        return True

    def file_identity(self, handle, sid):
        self.known()
        if sid != self.m.sid or self.m.fail_acl:
            raise profile.ProfileUnavailable('DACL_NOT_PRIVATE')
        return self.m.nodes[self.m.handles[handle.value]]['identity']

    def file_information(self, handle):
        node = self.m.nodes[self.m.handles[handle.value]]
        return node['identity'], len(node['data'])

    def lock_first_byte(self, handle):
        self.known()
        key = self.m.handles[handle.value]
        if key in self.m.locks:
            raise profile.ProfileUnavailable('PROFILE_BUSY')
        self.m.locks[key] = handle.value
        self.m.events.append(('private_lock', key, self.m.global_locked))

    def read(self, handle, maximum):
        self.known()
        node = self.m.nodes[self.m.handles[handle.value]]
        start = node['position']
        node['position'] += maximum
        return node['data'][start:start + maximum]

    def rewind(self, handle):
        self.known()
        self.m.nodes[self.m.handles[handle.value]]['position'] = 0

    def write(self, handle, payload):
        self.known()
        if self.m.fail_write:
            raise profile.ProfileUnavailable('PROFILE_METADATA_WRITE_UNPROVED')
        self.m.nodes[self.m.handles[handle.value]]['data'] = payload

    def replace(self, handle, parent, name):
        self.known()
        old = self.m.handles[handle.value]
        key = self.m.handles[parent.value] + '/' + name
        self.m.nodes[key] = self.m.nodes.pop(old)
        self.m.handles[handle.value] = key
        if self.m.fail_retained_commit_close and json.loads(self.m.nodes[key]['data'])['state'] == 'retained':
            self.m.fail_close_key = key

    def close(self, handle):
        if handle.value is None:
            return
        self.known()
        if self.m.fail_close or self.m.handles[handle.value] == self.m.fail_close_key:
            self.m.pending = True  # Model a permanently unknown close, never numeric retry.
            raise profile.ProfileUnavailable('PROFILE_HANDLE_CLOSE_UNPROVED')
        assert handle.value not in self.m.closed
        key = self.m.handles[handle.value]
        if self.m.locks.get(key) == handle.value:
            self.m.locks.pop(key)
        self.m.closed.add(handle.value)
        handle.value = None

    def mark_delete(self, handle):
        self.m.nodes.pop(self.m.handles[handle.value])

    def observed_busy(self, path):
        return self.m.busy

    def entries(self, handle, deadline):
        prefix = self.m.handles[handle.value] + '/'
        return iter(key[len(prefix):] for key in tuple(self.m.nodes)
                    if key.startswith(prefix) and '/' not in key[len(prefix):])


def root_factory(model):
    class Root:
        def __init__(self, path, *, create, persistent=False):
            self.path = Path(path)
            self._persistent, self._released = persistent, False
            self._api, self._sid = FakeAPI(model), model.sid
            self._handles = [ctypes.c_void_p()]
            key = str(path)
            if create and key in model.nodes:
                raise profile.ProfileUnavailable('PROFILE_COLLISION', owner=self)
            if not create and key not in model.nodes:
                raise profile.ProfileUnavailable('PROFILE_ROOT_MISSING', owner=self)
            self.identity = model.node(key)['identity']
            model.opened(key, self._handles[0])

        def verify(self):
            if self._released:
                raise profile.ProfileUnavailable('PROFILE_ROOT_CLOSED')
            self._api.known()
            if self._sid != model.sid or model.fail_acl or model.reparse:
                raise profile.ProfileUnavailable('PROFILE_CHANGED')
            if self.identity != model.nodes[str(self.path)]['identity']:
                raise profile.ProfileUnavailable('PROFILE_CHANGED')
            return True

        def release(self):
            if self._released:
                return
            for handle in self._handles:
                self._api.close(handle)
            self._released = True

        def remove(self, deadline):
            self.verify()
            for key in list(model.nodes):
                if key == str(self.path) or key.startswith(str(self.path) + '/'):
                    model.nodes.pop(key)
            self.release()
    return Root


class WindowsProfileSessionTests(unittest.TestCase):
    def setUp(self):
        self.model = Model()
        self.directory, self.scratch = Path('/private-fixture/connections'), Path('/private-fixture/scratch')
        self.model.node(str(self.directory))
        self.model.node(str(self.scratch))
        self.remembered = {'/other-private-profile'}
        def remember(directory, records, *, locked):
            self.assertTrue(locked)
            self.assertEqual(Path(directory), self.directory)
            self.assertTrue(self.model.global_locked)
            self.remembered.update(r['native_profile'] for r in records)
            return True
        @contextmanager
        def global_lock(directory, name='connections.lock'):
            self.assertEqual(Path(directory), self.directory)
            if self.model.global_locked:
                raise profile.ProfileUnavailable('PROFILE_BUSY')
            self.model.global_locked = True
            self.model.events.append(('global_enter', str(directory)))
            try:
                yield
            finally:
                self.model.global_locked = False
                self.model.events.append(('global_exit', str(directory)))
        def prepare(value, directory, scratch):
            root = root_factory(self.model)
            value.directory = Path(directory)
            value.binding_directory = value.directory / setup.PRIVATE_LEAF
            value.scratch = Path(scratch) / setup.PRIVATE_LEAF
            value._parents = [root(directory, create=False), root(scratch, create=False)]
            value._metadata = root(value.binding_directory,
                                   create=str(value.binding_directory) not in self.model.nodes, persistent=True)
            value._scratch = root(value.scratch, create=str(value.scratch) not in self.model.nodes, persistent=True)
            value._complete, value._released = True, False
            value._store_owner = None
            value._legacy_uncertain = False
            value._legacy_handles = [None]
        self.setup_builder = prepare
        self.patches = [patch.object(profile.private_roots, 'remember_profiles', side_effect=remember),
                        patch.object(profile.private_roots, 'retained_profiles', return_value=[]),
                        patch.object(profile.private_roots, 'declare'),
                        patch.object(connections, 'metadata_lock', side_effect=global_lock),
                        patch.object(setup._SetupRoots, '__init__', prepare)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.store = self.store_new()

    def store_new(self):
        return profile.WindowsProfileStore(self.directory, self.scratch, _root_factory=root_factory(self.model))

    def browser(self, lease):
        browser = WindowsBrowserProcess.__new__(WindowsBrowserProcess)
        browser._profile_lease = lease
        browser._closed, browser._drained = False, False
        browser._pipes, browser._process = object(), profile.ChildProcess.__new__(profile.ChildProcess)
        lease.claim(browser)
        return browser

    def drain(self, browser):
        browser._closed, browser._drained, browser._pipes = True, True, None

    def test_create_drain_retain_resume_keeps_native_profile_but_new_browser_owner(self):
        lease = self.store.create()
        browser = self.browser(lease)
        self.drain(browser)
        lease.finish_drained(browser, retain=True)
        reopened = self.store_new().resume()
        self.assertEqual((reopened.path, reopened.identity, reopened.generation),
                         (lease.path, lease.identity, lease.generation))
        fresh = self.browser(reopened)
        self.assertIsNot(fresh, browser)
        self.assertFalse(hasattr(reopened, 'account_verified'))
        self.assertEqual(reopened._row['state'], 'pending')

    def test_two_product_claims_refuse_while_lifetime_byte_lock_is_held(self):
        lease = self.store.create()
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_EXISTS'):
            self.store_new().create()
        browser = self.browser(lease)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_BUSY'):
            lease.claim(browser)

    def test_pending_and_cleanup_required_crashes_never_resume(self):
        lease = self.store.create()
        for state in ('pending', 'cleanup_required'):
            self.store._write(dict(lease._row, state=state))
            with self.subTest(state=state), self.assertRaisesRegex(profile.ProfileUnavailable, 'RECOVERY_REQUIRED'):
                self.store_new().resume()

    def test_sid_acl_reparse_and_full_128bit_identity_changes_refuse(self):
        lease = self.store.create()
        for field in ('sid', 'fail_acl', 'reparse', 'identity'):
            old_sid = self.model.sid
            node = self.model.nodes[str(lease.path)]
            old_identity = node['identity']
            if field == 'sid':
                self.model.sid = 'S-1-5-21-other'
            elif field == 'identity':
                node['identity'] = old_identity[0], b'\x80' + old_identity[1][1:]
            else:
                setattr(self.model, field, True)
            with self.subTest(field=field), self.assertRaises(profile.ProfileUnavailable):
                lease.verify()
            self.model.sid, node['identity'] = old_sid, old_identity
            self.model.fail_acl = self.model.reparse = False

    def test_marker_tamper_and_same_prefix_foreign_binding_refuse(self):
        lease = self.store.create()
        marker = self.model.nodes[str(lease.path) + '/' + profile.OWNER_MARKER]
        marker['data'] = b'0' * 32
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'MARKER_CHANGED'):
            lease.verify()
        self.store._write(dict(lease._row, path=str(lease.path) + '-foreign'))
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'METADATA_SHAPE'):
            self.store._binding()

    def test_claim_requires_exact_browser_and_mutual_private_binding(self):
        lease = self.store.create()
        with self.assertRaises(profile.ProfileUnavailable):
            lease.claim(SimpleNamespace(_profile_lease=lease))
        browser = WindowsBrowserProcess.__new__(WindowsBrowserProcess)
        browser._profile_lease = object()
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'BROWSER_BINDING_UNPROVED'):
            lease.claim(browser)

    def test_observed_external_busy_refuses_without_signalling_or_reuse(self):
        lease = self.store.create()
        self.model.busy = True
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_IN_USE'):
            self.browser(lease)
        self.assertIsNone(lease._browser)

    def test_finish_requires_same_browser_actual_closed_pipe_and_drain_facts(self):
        lease = self.store.create()
        browser = self.browser(lease)
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'DRAIN_UNPROVED'):
            lease.finish_drained(browser, retain=True)
        self.drain(browser)
        browser._pipes = object()
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'DRAIN_UNPROVED'):
            lease.finish_drained(browser, retain=True)
        self.assertFalse(lease._released)
        browser._pipes, browser._process = None, object()
        with self.assertRaisesRegex(profile.ProfileUnavailable, 'DRAIN_UNPROVED'):
            lease.finish_drained(browser, retain=True)

    def test_cancel_purges_only_exact_pre_spawn_root_and_preserves_registry_union(self):
        lease = self.store.create()
        lease.cancel_pending()
        self.assertTrue(lease._released)
        self.assertNotIn(str(lease.path), self.model.nodes)
        self.assertIsNone(self.store._binding())
        self.assertIn('/other-private-profile', self.remembered)

    def test_failed_metadata_publish_keeps_exact_profile_and_partial_owner(self):
        self.model.fail_write = True
        with self.assertRaises(profile.ProfileUnavailable) as caught:
            self.store.create()
        lease = self.store._partial
        self.assertIs(caught.exception.owner, lease)
        self.assertIn(str(lease.path), self.model.nodes)
        self.assertFalse(lease._released)

    def test_failed_retention_never_releases_lifetime_lock(self):
        lease = self.store.create()
        browser = self.browser(lease)
        self.drain(browser)
        self.model.fail_write = True
        with self.assertRaises(profile.ProfileUnavailable):
            lease.finish_drained(browser, retain=True)
        self.assertFalse(lease._released)
        self.assertTrue(self.model.locks)

    def test_unknown_close_is_permanent_and_does_not_retry_numeric_handle(self):
        self.model.fail_close = True
        with self.assertRaises(profile.ProfileUnavailable):
            self.store.create()
        self.assertTrue(self.store._uncertain)
        count = len(self.model.closed)
        with self.assertRaises(profile.ProfileUnavailable):
            self.store.close()
        self.assertEqual(len(self.model.closed), count)

    def test_retained_is_not_published_when_root_close_fails_after_lifetime_lock_closed(self):
        lease = self.store.create()
        browser = self.browser(lease)
        self.drain(browser)
        self.model.fail_close_key = str(lease.path)
        with self.assertRaises(profile.ProfileUnavailable):
            lease.finish_drained(browser, retain=True)
        row = json.loads(self.model.nodes[str(self.store.binding_directory) + '/' + profile.BINDING]['data'])
        self.assertEqual(row['state'], 'cleanup_required')
        self.assertNotIn(str(lease.path) + '/' + profile.PROFILE_LOCK, self.model.locks)
        self.assertFalse(lease._released)
        self.assertIs(lease._browser, browser)

    def test_claimed_abort_only_admits_exact_definitely_pre_spawn_private_engine(self):
        lease = self.store.create()
        browser = self.browser(lease)
        self.drain(browser)
        browser._process = None
        for attempted in (None, True):
            browser._spawn_attempted = attempted
            with self.subTest(attempted=attempted), self.assertRaisesRegex(profile.ProfileUnavailable, 'PRE_SPAWN_UNPROVED'):
                lease.abort_claim_before_spawn(browser)
        browser._spawn_attempted = False
        lease.abort_claim_before_spawn(browser)
        self.assertTrue(lease._released)

    def test_final_retained_commit_close_uncertainty_keeps_owner_after_known_profile_closure(self):
        lease = self.store.create()
        browser = self.browser(lease)
        self.drain(browser)
        self.model.fail_retained_commit_close = True
        with self.assertRaises(profile.ProfileUnavailable) as caught:
            lease.finish_drained(browser, retain=True)
        row = json.loads(self.model.nodes[str(self.store.binding_directory) + '/' + profile.BINDING]['data'])
        self.assertEqual(row['state'], 'retained')  # Atomic commit occurred; metadata cleanup is still unproved.
        self.assertEqual(lease._handles, [])
        self.assertTrue(all(handle.value is None for handle in lease._root._handles))
        self.assertTrue(self.store._uncertain)
        self.assertFalse(lease._released)
        self.assertIs(caught.exception.owner, lease)
        self.assertIs(self.store._partial, lease)

    def test_stop_cancellation_survives_cleanup_failure_with_owner_attached(self):
        original = KeyboardInterrupt('cancel fixture')
        with self.assertRaises(KeyboardInterrupt) as caught:
            with self.store._file(self.store._metadata, 'owned-temporary', create=True):
                self.model.fail_close = True
                raise original
        self.assertIs(caught.exception, original)
        self.assertIs(caught.exception.owner, self.store)
        self.assertTrue(self.store._uncertain)
        self.assertIsNotNone(caught.exception.cleanup_error)

    def test_new_pending_native_operation_keeps_handles_and_pinned_root_owners(self):
        with self.assertRaises(profile.ProfileUnavailable):
            with self.store._file(self.store._metadata, 'owned-temporary', create=True):
                self.model.pending = True
        self.assertTrue(self.store._uncertain)
        self.assertTrue(self.store._handles)
        self.assertIsNotNone(self.store._metadata)

    def test_shared_connections_lock_refuses_without_wait_or_other_profile_loss(self):
        with self.store._transaction():
            with self.assertRaisesRegex(profile.ProfileUnavailable, 'PROFILE_BUSY'):
                with self.store_new()._transaction():
                    self.fail('Concurrent metadata transaction was admitted')
        self.assertEqual(self.remembered, {'/other-private-profile'})

    def test_canonical_native_query_uses_original_held_handle_and_normalized_dos_path(self):
        api = profile._SessionAPI.__new__(profile._SessionAPI)
        api.known = lambda: None
        handle = ctypes.c_void_p(123)
        calls = []
        def query(actual, buffer, size, flags):
            calls.append((actual, size, flags))
            buffer.value = '\\\\?\\C:\\Users\\Person\\actual-profile'
            return len(buffer.value)
        api.k = SimpleNamespace(GetFinalPathNameByHandleW=query, GetDriveTypeW=lambda root: 3)
        result = api.final_path(handle)
        self.assertEqual(result, 'C:\\Users\\Person\\actual-profile')
        self.assertEqual(calls, [(handle, 32768, 0)])
        self.assertIsNone(api._session_pending)

    def test_failed_or_nonlocal_canonical_query_keeps_exact_native_buffer_owner(self):
        for result, value in [(0, ''), (32768, ''), (15, '\\\\?\\UNC\\host\\share')]:
            api = profile._SessionAPI.__new__(profile._SessionAPI)
            api.known = lambda: None
            handle = ctypes.c_void_p(123)
            def query(actual, buffer, size, flags):
                buffer.value = value
                return result
            api.k = SimpleNamespace(GetFinalPathNameByHandleW=query)
            with self.subTest(result=result), self.assertRaisesRegex(profile.ProfileUnavailable, 'CANONICAL_PATH_UNPROVED'):
                api.final_path(handle)
            self.assertIs(api._session_pending[0], handle)
            self.assertEqual(len(api._session_pending[1]), 32768)

    def test_verify_rechecks_held_native_canonical_path_before_reusing_browser_path(self):
        owner = profile._PinnedRoot.__new__(profile._PinnedRoot)
        owner._released = False
        owner.path, owner._sid = Path('/canonical-original'), 'S-1-5-21-100'
        handle, observed = ctypes.c_void_p(123), [str(owner.path)]   # As the API returns it: this platform's separators.
        owner._handles = [handle]
        calls = []
        def final_path(actual):
            calls.append(actual)
            return observed[0]
        owner._api = SimpleNamespace(current_sid=lambda: owner._sid, known=lambda: None, final_path=final_path)
        with patch.object(profile._LandingProfile, 'verify', return_value=True) as native_verify:
            self.assertTrue(owner.verify())
            observed[0] = str(Path('/drive-alias-now-points-elsewhere'))
            with self.assertRaisesRegex(profile.ProfileUnavailable, 'CANONICAL_PATH_CHANGED'):
                owner.verify()
            native_verify.assert_called_once_with()
        self.assertEqual(calls, [handle, handle])

    def test_canonical_drive_type_requires_a_known_local_media_class(self):
        for drive_type in (2, 3, 6):
            api = profile._SessionAPI.__new__(profile._SessionAPI)
            api.known = lambda: None
            handle, roots = ctypes.c_void_p(123), []
            def query(actual, buffer, size, flags):
                buffer.value = '\\\\?\\D:\\actual-profile'
                return len(buffer.value)
            def get_type(root):
                roots.append(root)
                return drive_type
            api.k = SimpleNamespace(GetFinalPathNameByHandleW=query, GetDriveTypeW=get_type)
            with self.subTest(drive_type=drive_type):
                self.assertEqual(api.final_path(handle), 'D:\\actual-profile')
                self.assertEqual(roots, ['D:\\'])
                self.assertIsNone(api._session_pending)

    def test_mapped_remote_unknown_invalid_and_unsupported_media_refuse_with_owner_retained(self):
        for drive_type in (0, 1, 4, 5, 7):
            api = profile._SessionAPI.__new__(profile._SessionAPI)
            api.known = lambda: None
            handle, roots = ctypes.c_void_p(123), []
            def query(actual, buffer, size, flags):
                buffer.value = '\\\\?\\Z:\\looks-local-but-unproved'
                return len(buffer.value)
            def get_type(root):
                roots.append(root)
                return drive_type
            api.k = SimpleNamespace(GetFinalPathNameByHandleW=query, GetDriveTypeW=get_type)
            with self.subTest(drive_type=drive_type), self.assertRaisesRegex(profile.ProfileUnavailable, 'LOCAL_VOLUME_UNPROVED'):
                api.final_path(handle)
            self.assertEqual(roots, ['Z:\\'])
            self.assertIs(api._session_pending[0], handle)
            self.assertEqual(api._session_pending[2], 'Z:\\')

    def test_unproved_input_drive_refuses_before_any_ancestor_or_profile_native_open(self):
        calls = []
        class Native:
            def require_local_drive(self, drive):
                calls.append(('drive', drive))
                raise profile.ProfileUnavailable('PROFILE_LOCAL_VOLUME_UNPROVED')
            def current_sid(self):
                calls.append(('sid',))
                return 'S-1-5-21-100'
            def open_directory_into(self, path, output):
                calls.append(('open', path))
                raise AssertionError('A known remote drive reached native file opening')
        with patch.object(profile, 'Path', PureWindowsPath):
            with self.assertRaisesRegex(profile.ProfileUnavailable, 'LOCAL_VOLUME_UNPROVED') as caught:
                profile._PinnedRoot('Z:/network-mapped/profile', create=False, api=Native())
        self.assertEqual(calls, [('drive', 'Z:\\')])
        self.assertTrue(all(handle is None for handle in caught.exception.owner._handles))

    def test_definite_native_sharing_violation_is_busy_without_unknown_handle_adoption(self):
        for name, expected in [('lockfile', 'PROFILE_IN_USE'), ('connections.lock', 'PROFILE_BUSY')]:
            api = profile._SessionAPI.__new__(profile._SessionAPI)
            api.known = lambda: None
            api.IOStatus, api.Unicode, api.Attributes = _native_structures()
            api.n = SimpleNamespace(NtCreateFile=lambda *args: 0xc0000043)
            parent, output = ctypes.c_void_p(123), ctypes.c_void_p()
            with self.subTest(name=name), self.assertRaisesRegex(profile.ProfileUnavailable, expected):
                api.file_into(parent, name, 'S-1-5-21-100', output)
            self.assertIsNone(api._session_pending)
            self.assertIsNone(output.value)
            self.assertEqual(parent.value, 123)
        for status, acquired in [(0x103, None), (0xc0000001, None), (0xc0000043, 456)]:
            api = profile._SessionAPI.__new__(profile._SessionAPI)
            api.known = lambda: None
            api.IOStatus, api.Unicode, api.Attributes = _native_structures()
            def open_result(*args):
                args[0]._obj.value = acquired
                return status
            api.n = SimpleNamespace(NtCreateFile=open_result)
            parent, output = ctypes.c_void_p(123), ctypes.c_void_p()
            with self.subTest(status=status, acquired=acquired), self.assertRaisesRegex(profile.ProfileUnavailable, 'OPEN_UNPROVED'):
                api.file_into(parent, 'lockfile', 'S-1-5-21-100', output)
            self.assertIs(api._session_pending[0], output)
            self.assertIs(api._session_pending[2], parent)


if __name__ == '__main__':
    unittest.main()
