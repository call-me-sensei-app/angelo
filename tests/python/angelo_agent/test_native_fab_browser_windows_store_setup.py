# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Directory/owner controls with native seams mocked; no Windows API/ACL/GUI operation."""
import suite_isolation  # noqa: F401
from pathlib import Path, PureWindowsPath
import ctypes
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_windows_store_setup as setup
from angelo_agent.fab_browser_profile_metadata_windows import _SessionAPI, ProfileUnavailable
from angelo_agent.fab_browser_profile_windows import _native_structures
from angelo_agent.fab_browser_download_file_windows import DownloadFileUnavailable


class Environment:
    def __init__(self):
        self.data = PureWindowsPath(r'C:\Users\User\AppData\Local\Angelo')
        self.directory, self.scratch = self.data / 'connections', self.data / 'scratch'
        self.nodes = {str(value) for value in self.data.parents}
        self.paths, self.events, self.created = {}, [], []
        self.serial, self.sid = 100, 'S-1-5-21-123'
        self.bad_acl, self.close_error = set(), False
        self.legacy, self.records, self.names = False, [], []
        self.pending_legacy = self.close_legacy_error = False

    def opened(self, path, output):
        self.serial += 1
        output.value = self.serial
        self.paths[output.value] = PureWindowsPath(path)


class StoreSetupTests(unittest.TestCase):
    def setUp(self):
        self.environment = e = Environment()
        class API:
            def __init__(self):
                self.unknown = False
            def known(self):
                if self.unknown:
                    raise ProfileUnavailable('NATIVE_UNKNOWN', owner=self)
            def require_local_drive(self, anchor):
                e.events.append(('local', anchor))
            def current_sid(self):
                self.known()
                return e.sid
            def open_directory_into(self, path, output):
                e.events.append(('anchor', str(path)))
                e.opened(path, output)
            def _open(self, parent, name, output, **kwargs):
                self.known()
                path = e.paths[parent.value] / name
                e.events.append(('open', str(path), kwargs))
                if str(path) not in e.nodes:
                    raise ProfileUnavailable('MISSING_PARENT', owner=self)
                e.opened(path, output)
            def ensure_persistent_directory_into(self, parent, name, sid, output):
                self.known()
                path = e.paths[parent.value] / name
                result = 'opened' if str(path) in e.nodes else 'created'
                e.events.append(('ensure', str(path), result))
                if result == 'created':
                    e.created.append(str(path))
                    e.nodes.add(str(path))
                e.opened(path, output)
                return result
            def information(self, handle):
                self.known()
                return (1, str(e.paths[handle.value]).encode())
            def verify_acl(self, handle, sid):
                path = str(e.paths[handle.value])
                e.events.append(('acl', path))
                if path in e.bad_acl:
                    raise ProfileUnavailable('DACL_NOT_PRIVATE', owner=self)
            def final_path(self, handle):
                return str(e.paths[handle.value])
            def close(self, handle):
                self.known()
                e.events.append(('parent_close', handle.value))
                if e.close_error or e.close_legacy_error and handle.value == 999:
                    self.unknown = True
                    raise ProfileUnavailable('UNKNOWN_CLOSE', owner=self)
                handle.value = None
            def cleanup(self):
                self.known()
                e.events.append(('cleanup',))
            def file_into(self, parent, name, sid, output, **kwargs):
                e.events.append(('legacy_file', str(e.paths[parent.value]), name, kwargs))
                if e.pending_legacy:
                    self.unknown = True
                    raise ProfileUnavailable('LEGACY_PENDING', owner=self)
                if e.legacy:
                    output.value = 999
                return e.legacy
            def entries(self, handle, deadline):
                self.known()
                e.events.append(('entries', str(e.paths[handle.value])))
                yield from e.names
        class Root:
            def __init__(self, path, *, create, persistent):
                self.path = PureWindowsPath(path)
                self.identity = (2, str(self.path).encode())
                self._persistent, self.released = persistent, False
                self.calls = 0
                e.events.append(('private', str(self.path), create, persistent))
                if str(self.path) in e.bad_acl:
                    raise ProfileUnavailable('DACL_NOT_PRIVATE', owner=self)
            def verify(self):
                if self.released:
                    raise ProfileUnavailable('ROOT_CLOSED', owner=self)
                e.events.append(('private_verify', str(self.path)))
                return True
            def release(self):
                if self.released:
                    return
                self.calls += 1
                e.events.append(('private_release', str(self.path)))
                if getattr(self, 'fail_close', False):
                    raise ProfileUnavailable('ROOT_CLOSE_UNKNOWN', owner=self)
                self.released = True
        module = ModuleType('angelo_agent.fab_browser_profile_session_windows')
        module._PinnedRoot = Root
        module.BINDING = 'fab-browser-profile-windows.json'
        self.Root = Root
        for manager in (patch.object(setup, 'Path', PureWindowsPath),
                        patch.object(setup, '_SessionAPI', API),
                        patch.object(setup.storage, 'data_directory', return_value=e.data),
                        patch.object(setup.storage, 'connection_directory', return_value=e.directory),
                        patch.object(setup.private_roots, 'retained_profiles', side_effect=lambda _: e.records),
                        patch.dict(sys.modules, {module.__name__: module})):
            self.enterContext(manager)

    def prepare(self):
        return setup.prepare_roots(self.environment.directory, self.environment.scratch)

    def test_missing_default_app_directories_and_private_leaves_are_provisioned(self):
        e = self.environment
        owner = self.prepare()
        self.assertEqual(e.created, [str(e.data), str(e.directory), str(e.scratch)])
        self.assertEqual(owner.directory, e.directory)
        self.assertEqual(owner.binding_directory, e.directory / 'fab-browser-windows')
        self.assertEqual(owner.scratch, e.scratch / 'fab-browser-windows')
        self.assertIs(type(owner._metadata), self.Root)
        self.assertIs(type(owner._scratch), self.Root)
        self.assertTrue(owner.verify())

    def test_existing_shared_parent_acl_is_preserved_without_becoming_private_leaf_authority(self):
        e = self.environment
        e.nodes.update(map(str, (e.data, e.directory, e.scratch)))
        e.bad_acl.update(e.nodes)  # Ordinary shared parents deliberately fail strict private ACL.
        owner = self.prepare()
        self.assertTrue(owner.verify())
        self.assertEqual(e.created, [])
        self.assertFalse(any(event[0] == 'acl' for event in e.events))
        self.assertEqual([event[1] for event in e.events if event[0] == 'private'],
                         [str(e.directory / 'fab-browser-windows'), str(e.scratch / 'fab-browser-windows')])

    def test_new_shared_component_private_acl_failure_retains_actual_partial_owner(self):
        e = self.environment
        e.bad_acl.add(str(e.data))
        with self.assertRaises(ProfileUnavailable) as caught:
            self.prepare()
        owner = caught.exception.owner
        self.assertIs(type(owner), setup._SetupRoots)
        self.assertIsNotNone(owner._parents[0]._handles[-3])
        self.assertEqual(owner._metadata, None)
        self.assertIn(str(e.data), e.nodes)  # No guessed deletion of even our new persistent directory.
        owner.release()

    def test_existing_private_leaf_bad_acl_is_refused_not_repaired_or_removed(self):
        e = self.environment
        e.bad_acl.add(str(e.directory / 'fab-browser-windows'))
        with self.assertRaises(ProfileUnavailable) as caught:
            self.prepare()
        owner = caught.exception.owner
        self.assertIsNotNone(owner._metadata)
        self.assertIsNone(owner._scratch)
        self.assertIn(str(e.directory / 'fab-browser-windows'), e.bad_acl)
        self.assertFalse(any(event[0] == 'private_release' for event in e.events))
        owner.release()

    def test_prepublished_not_yet_initialized_owner_has_known_empty_release(self):
        owner = setup._SetupRoots.__new__(setup._SetupRoots)
        self.assertIsNone(owner.directory)
        self.assertIsNone(owner.binding_directory)
        self.assertIsNone(owner.scratch)
        owner.release()
        self.assertTrue(owner._released)
        self.assertEqual(self.environment.events, [])

    def test_reinitialization_cannot_erase_existing_native_root_owners(self):
        owner = self.prepare()
        metadata, scratch = owner._metadata, owner._scratch
        with self.assertRaises(ProfileUnavailable) as caught:
            setup._SetupRoots.__init__(owner, self.environment.directory, self.environment.scratch)
        self.assertIs(caught.exception.owner, owner)
        self.assertIs(owner._metadata, metadata)
        self.assertIs(owner._scratch, scratch)
        self.assertTrue(owner.verify())

    def test_unconfigured_paths_refuse_before_any_native_call(self):
        with self.assertRaises(ProfileUnavailable) as caught:
            setup.prepare_roots(self.environment.data / 'foreign', self.environment.scratch)
        self.assertEqual(self.environment.events, [])
        caught.exception.owner.release()

    def test_arbitrary_missing_ancestor_is_not_created_as_an_app_directory(self):
        e = self.environment
        e.nodes.remove(str(e.data.parent))
        with self.assertRaises(ProfileUnavailable) as caught:
            self.prepare()
        self.assertEqual(e.created, [])
        self.assertEqual(caught.exception.code, 'MISSING_PARENT')
        caught.exception.owner.release()

    def test_parent_native_identity_change_refuses_with_complete_composite_owner(self):
        owner = self.prepare()
        parent = owner._parents[0]
        before = parent._api.information
        parent._api.information = lambda handle: (99, b'changed')
        try:
            with self.assertRaises(ProfileUnavailable) as caught:
                owner.verify()
            self.assertIs(caught.exception.owner, owner)
            self.assertFalse(owner._released)
        finally:
            parent._api.information = before
        self.assertTrue(owner.verify())

    def test_parent_canonical_alias_or_current_sid_change_refuses_without_reopen(self):
        owner = self.prepare()
        parent = owner._parents[0]
        original = parent._api.final_path
        parent._api.final_path = lambda handle: r'C:\changed'
        try:
            with self.assertRaises(ProfileUnavailable) as caught:
                owner.verify()
            self.assertIs(caught.exception.owner, owner)
        finally:
            parent._api.final_path = original
        self.environment.sid = 'S-1-5-21-999'
        with self.assertRaises(ProfileUnavailable) as caught:
            owner.verify()
        self.assertIs(caught.exception.owner, owner)
        self.environment.sid = 'S-1-5-21-123'
        self.assertTrue(owner.verify())

    def test_read_only_parent_pins_have_no_delete_or_reparse_fallback(self):
        self.prepare()
        opens = [event for event in self.environment.events if event[0] == 'open']
        self.assertTrue(opens)
        self.assertTrue(all(event[2] == dict(directory=True, delete=False, no_reparse=True) for event in opens))

    def test_setup_has_no_binding_registry_or_scratch_fallback_io(self):
        self.prepare()
        self.assertFalse(any(event[0] in ('legacy_file', 'entries') for event in self.environment.events))

    def test_same_private_owners_are_available_for_store_adoption_without_reopen(self):
        owner = self.prepare()
        calls = len([event for event in self.environment.events if event[0] == 'private'])
        store = SimpleNamespace(_setup=owner, _metadata=owner._metadata, _scratch=owner._scratch)
        owner._store_owner = store
        self.assertIs(store._metadata, owner._metadata)
        self.assertIs(store._scratch, owner._scratch)
        self.assertEqual(len([event for event in self.environment.events if event[0] == 'private']), calls)

    def test_child_close_failure_preserves_every_parent_pin_and_composite_owner(self):
        owner = self.prepare()
        owner._scratch.fail_close = True
        with self.assertRaises(ProfileUnavailable) as caught:
            owner.release()
        self.assertIs(caught.exception.owner, owner)
        self.assertFalse(owner._metadata.released)
        self.assertFalse(any(event[0] == 'parent_close' for event in self.environment.events))
        self.assertFalse(owner._released)

    def test_unknown_parent_close_is_not_retried_or_followed_by_ancestor_release(self):
        owner = self.prepare()
        self.environment.close_error = True
        with self.assertRaises(ProfileUnavailable):
            owner.release()
        calls = len([event for event in self.environment.events if event[0] == 'parent_close'])
        with self.assertRaises(ProfileUnavailable):
            owner.release()
        self.assertEqual(len([event for event in self.environment.events if event[0] == 'parent_close']), calls)
        self.assertEqual(calls, 1)
        self.assertFalse(owner._parents[0]._released)

    def test_known_release_is_idempotent_and_does_not_delete_directories(self):
        owner = self.prepare()
        before = set(self.environment.nodes)
        owner.release()
        events = list(self.environment.events)
        owner.release()
        self.assertEqual(self.environment.events, events)
        self.assertEqual(self.environment.nodes, before)
        self.assertTrue(owner._released)

    def test_startup_legacy_binding_is_refused_after_exact_read_handle_close(self):
        owner = self.prepare()
        self.environment.legacy = True
        with self.assertRaises(ProfileUnavailable) as caught:
            owner.refuse_legacy()
        self.assertEqual(caught.exception.code, 'PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY')
        self.assertIs(caught.exception.owner, owner)
        self.assertIsNone(owner._legacy_handles[0])
        self.assertFalse(owner._legacy_uncertain)
        self.assertFalse(any(event[0] == 'entries' for event in self.environment.events))

    def test_direct_legacy_registry_record_refuses_without_migration(self):
        owner = self.prepare()
        self.environment.records = [str(self.environment.scratch / (setup.private_roots.FAB_BROWSER_PREFIX + 'old'))]
        with self.assertRaises(ProfileUnavailable) as caught:
            owner.refuse_legacy()
        self.assertEqual(caught.exception.code, 'PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY')
        self.assertEqual(len(self.environment.records), 1)

    def test_new_nested_registry_record_does_not_masquerade_as_direct_legacy(self):
        owner = self.prepare()
        self.environment.records = [str(owner.scratch / (setup.private_roots.FAB_BROWSER_PREFIX + 'new'))]
        owner.refuse_legacy()
        self.assertFalse(owner._legacy_uncertain)

    def test_direct_scratch_legacy_prefix_and_scan_bound_refuse(self):
        owner = self.prepare()
        self.environment.names = [setup.private_roots.FAB_BROWSER_PREFIX + 'old']
        with self.assertRaises(ProfileUnavailable) as caught:
            owner.refuse_legacy()
        self.assertEqual(caught.exception.code, 'PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY')
        self.environment.names = ['ordinary'] * 4097
        with self.assertRaises(ProfileUnavailable) as caught:
            owner.refuse_legacy()
        self.assertEqual(caught.exception.code, 'PROFILE_STORE_LEGACY_SCAN_BOUND')

    def test_unknown_legacy_probe_keeps_native_parent_guards_and_blocks_release(self):
        owner = self.prepare()
        self.environment.pending_legacy = True
        with self.assertRaises(ProfileUnavailable):
            owner.refuse_legacy()
        self.assertTrue(owner._legacy_uncertain)
        self.assertIsNotNone(owner._legacy_handles[0])
        with self.assertRaises(ProfileUnavailable):
            owner.release()
        self.assertFalse(any(event[0] == 'private_release' for event in self.environment.events))

    def test_unknown_legacy_handle_close_retains_exact_output_and_never_retries(self):
        owner = self.prepare()
        self.environment.legacy = self.environment.close_legacy_error = True
        with self.assertRaises(ProfileUnavailable):
            owner.refuse_legacy()
        self.assertTrue(owner._legacy_uncertain)
        self.assertEqual(owner._legacy_handles[0].value, 999)
        with self.assertRaises(ProfileUnavailable):
            owner.refuse_legacy()
        self.assertEqual(len([event for event in self.environment.events if event == ('parent_close', 999)]), 1)


class PersistentDirectoryNativeTests(unittest.TestCase):
    def api(self, *, result=0, information=2, output=456, ios_status=0, lost=False):
        api = _SessionAPI.__new__(_SessionAPI)
        api._session_pending = api._pending_open = api._pending_query = None
        api._pending_read = api._pending_create = api._created_root = None
        api._unknown_close, api._unknown_free = {}, {}
        api._temporary = [None] * 4
        api.IOStatus, api.Unicode, api.Attributes = _native_structures()
        def descriptor(text, revision, pointer, length):
            pointer._obj.value = 700
            return 1
        api.a = SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=Mock(side_effect=descriptor))
        api.k = SimpleNamespace(LocalFree=Mock(return_value=None))
        events = []
        def create(handle, access, attributes, ios, allocation, flags, share, disposition, options, ea, length):
            self.assertIs(api._pending_create[0], handle._obj)
            self.assertIs(api._pending_create[1], ios._obj)
            events.append((access, attributes._obj.root, flags, share, disposition, options))
            handle._obj.value = output
            ios._obj.value.status, ios._obj.information = ios_status, information
            if lost:
                raise OSError('lost native return')
            return result
        api.n = SimpleNamespace(NtCreateFile=Mock(side_effect=create))
        return api, events

    def test_actual_native_request_is_open_if_readonly_and_keeps_share_delete_denied(self):
        api, events = self.api()
        output = ctypes.c_void_p()
        self.assertEqual(api.ensure_persistent_directory_into(ctypes.c_void_p(123), 'fab-browser-windows',
                                                              'S-1-5-21-123', output), 'created')
        access, parent, flags, share, disposition, options = events[0]
        self.assertEqual((parent, flags, share, disposition), (123, 0x10, 1, 3))
        self.assertEqual(access & 0x00010000, 0)
        self.assertEqual(options, 0x1 | 0x20 | 0x00200000)
        self.assertIsNone(api._pending_create)
        self.assertIs(api._created_root[0], output)
        api.k.LocalFree.assert_called_once_with(700)

    def test_existing_native_open_is_not_reported_as_creation_or_acl_rewrite(self):
        api, _ = self.api(information=1)
        self.assertEqual(api.ensure_persistent_directory_into(ctypes.c_void_p(123), 'existing',
                                                              'S-1-5-21-123', ctypes.c_void_p()), 'opened')
        self.assertIsNone(api._created_root)
        self.assertFalse(hasattr(api.a, 'SetSecurityInfo'))

    def test_pending_or_lost_native_return_keeps_output_iosb_descriptor_and_parent(self):
        for kwargs in (dict(result=0x103, output=None, ios_status=0x103), dict(lost=True)):
            api, _ = self.api(**kwargs)
            output, parent = ctypes.c_void_p(), ctypes.c_void_p(123)
            with self.assertRaises((ProfileUnavailable, OSError)):
                api.ensure_persistent_directory_into(parent, 'pending', 'S-1-5-21-123', output)
            self.assertIs(api._pending_create[0], output)
            self.assertIs(api._pending_create[2], parent)
            self.assertIsNotNone(api._temporary[2])
            api.k.LocalFree.assert_not_called()
            with self.assertRaises(DownloadFileUnavailable):
                api.ensure_persistent_directory_into(parent, 'retry', 'S-1-5-21-123', ctypes.c_void_p())
            api.n.NtCreateFile.assert_called_once()

    def test_contradictory_success_is_not_accepted_or_cleaned_as_known(self):
        for kwargs in (dict(information=9), dict(output=None), dict(ios_status=0x103)):
            api, _ = self.api(**kwargs)
            with self.assertRaises(ProfileUnavailable):
                api.ensure_persistent_directory_into(ctypes.c_void_p(123), 'wrong', 'S-1-5-21-123', ctypes.c_void_p())
            self.assertIsNotNone(api._pending_create)
            api.k.LocalFree.assert_not_called()

    def test_definite_sharing_violation_without_handle_is_known_busy_not_unknown(self):
        api, _ = self.api(result=0xc0000043, output=None)
        with self.assertRaises(ProfileUnavailable) as caught:
            api.ensure_persistent_directory_into(ctypes.c_void_p(123), 'busy', 'S-1-5-21-123', ctypes.c_void_p())
        self.assertEqual(caught.exception.code, 'PROFILE_STORE_BUSY')
        self.assertIsNone(api._pending_create)
        api.k.LocalFree.assert_called_once()

    def test_security_free_unknown_blocks_retry_without_reusing_freed_pointer(self):
        api, _ = self.api()
        api.k.LocalFree.side_effect = OSError('lost security free')
        with self.assertRaises(OSError):
            api.ensure_persistent_directory_into(ctypes.c_void_p(123), 'created', 'S-1-5-21-123', ctypes.c_void_p())
        self.assertIn(2, api._unknown_free)
        with self.assertRaises(DownloadFileUnavailable):
            api.ensure_persistent_directory_into(ctypes.c_void_p(123), 'retry', 'S-1-5-21-123', ctypes.c_void_p())
        api.k.LocalFree.assert_called_once()
        api.n.NtCreateFile.assert_called_once()

    def test_removable_creator_keeps_existing_delete_access_and_file_create_behavior(self):
        api, events = self.api()
        self.assertTrue(api.create_directory_into(ctypes.c_void_p(123), 'removable', 'S-1-5-21-123', ctypes.c_void_p()))
        self.assertNotEqual(events[0][0] & 0x00010000, 0)
        self.assertEqual((events[0][3], events[0][4]), (1, 2))


if __name__ == '__main__':
    unittest.main()
