# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real v4 working-pair/file-journal joins; native scene fidelity tested separately."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.files import FileWorkspace
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent import checkpoints as stores, checkpoint_filesystem as filesystem
from angelo_agent import checkpoint_capture as capture
from fake_nt_files import FakeNtApi
import test_native_checkpoint_recovery_resources as recovery_fixtures
import test_native_checkpoint_capture_publication as publication_fixtures
SESSION = recovery_fixtures.SESSION


class BundleFileRestoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery_fixtures.RecoveryResourceTests('test_actual_pair_copied_and_exact_fds_retained_without_scene_authority')
        self.fixture.setUp()
        self.store, self.identity = self.fixture.store, self.fixture.record['id']

    def tearDown(self):
        self.fixture.tearDown()

    def test_raw_bundle_metadata_cannot_admit_file_plan(self):
        with self.assertRaises(ValueError):
            self.store.restore_plan(self.identity)
        with self.assertRaises(ValueError):
            self.store.restore_plan(self.identity, recovery=dict(self.fixture.record))

    def test_exact_pair_admits_empty_plan_without_native_scene_claim(self):
        pair = self.fixture.prepare()
        self.assertEqual(self.store.restore_plan(self.identity, recovery=pair), [])
        self.assertEqual(self.store.restore_since(self.identity, [], recovery=pair), 0)

    def test_bound_pair_preserves_actual_file_journal_restore(self):
        pair = self.fixture.prepare()
        workspace = self.fixture.root.parent / 'workspace'
        workspace.mkdir()
        asset = workspace / 'asset.txt'
        asset.write_text('before')
        FileWorkspace(workspace).execute('angelo_write_file', {'path':'asset.txt','content':'after'},
                                         self.store, self.identity)
        plan = self.store.restore_plan(self.identity, recovery=pair)
        self.assertEqual(len(plan), 1)
        self.assertEqual(asset.read_text(), 'after')
        pair._native_begin()  # Reader holds the resources across safety/file phases.
        self.assertEqual(self.store.restore_since(self.identity, plan, recovery=pair), 1)
        self.assertEqual(asset.read_text(), 'before')
        pair._native_end()

    def test_changed_selected_component_declaration_refuses_fresh_plan(self):
        pair = self.fixture.prepare()
        record = json.loads(self.fixture.metadata.read_text())
        record['scene_hash'] = '0' * 64
        self.fixture.metadata.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            self.store.restore_plan(self.identity, recovery=pair)

    def test_closed_pair_cannot_admit_file_effects(self):
        pair = self.fixture.prepare()
        plan = self.store.restore_plan(self.identity, recovery=pair)
        pair.close()
        with self.assertRaises(ValueError):
            self.store.restore_since(self.identity, plan, recovery=pair)

    def test_different_profile_cannot_adopt_working_pair(self):
        pair = self.fixture.prepare()
        other = CheckpointStore(self.fixture.root.parent / 'other-profile', SESSION)
        other.directory = self.store.directory
        with self.assertRaises(ValueError):
            other.restore_plan(self.identity, recovery=pair)


@contextmanager
def published():
    fixture = publication_fixtures.CapturePublicationTests('test_pair_is_hashed_published_then_finished_after_post')
    fixture.setUp()
    try:
        fixture.prepare()
        fixture.owner.publish(fixture.capability)
        fixture.facts['post_state'] = 'complete'
        fixture.owner.finish(fixture.capability)
        fixture.owner._native_end()
        fixture.owner.abandon()
        yield fixture
    finally:
        fixture.tearDown()


class BundleConsumerTests(unittest.TestCase):
    def test_full_pair_availability_requires_real_reader_methods_and_exact_sizes(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            self.assertFalse(store.availability(identity)['available'])
            native = SimpleNamespace(prepare_restore=lambda _: None, restore=lambda *_: None, close_restore=lambda _: None)
            with patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)}):
                self.assertTrue(store.availability(identity)['available'])
                (store.folder(identity) / 'sculpt.runtime').write_bytes(b'truncated')
                self.assertFalse(store.availability(identity)['available'])

    def test_complete_pair_can_follow_existing_retention_policy(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            inventory = store.inspect()
            self.assertTrue(inventory['checkpoints'][0]['scene_usable'])
            plan = store.prune_plan({'max_bytes':1})
            self.assertEqual(len(plan['candidates']), 1)
            outcome = store.prune(plan, user_confirmed=True)
            self.assertEqual(len(outcome['removed']), 1)
            tombstone = store.load(identity)
            self.assertEqual((tombstone['version'], tombstone['state']), (4, 'PRUNED'))
            self.assertNotIn('capture_outputs', tombstone)
            self.assertNotIn('sculpt', tombstone)
            self.assertEqual({path.name for path in store.folder(identity).iterdir()}, {'checkpoint.json'})

    def test_corrupt_pair_never_tombstones_or_deletes(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            target = store.folder(identity) / 'scene.blend'
            target.write_bytes(b'X' * len(fixture.base))
            plan = store.prune_plan({'max_bytes':1})
            with self.assertRaises(ValueError):
                store.prune(plan, user_confirmed=True)
            self.assertEqual(store.load(identity)['state'], 'READY')
            self.assertTrue((store.folder(identity) / 'sculpt.runtime').exists())
            self.assertEqual(target.read_bytes(), b'X' * len(fixture.base))

    def test_same_bytes_replaced_inode_is_preserved(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            target = store.folder(identity) / 'scene.blend'
            # Holding the old file keeps POSIX from reusing its inode; on Windows an open handle would only block the
            # delete, and NTFS does not hand a deleted file's ID straight to a new one.
            held = None if os.name == 'nt' else target.open('rb')
            try:
                target.unlink()
                target.write_bytes(fixture.base)
                plan = store.prune_plan({'max_bytes':1})
                with self.assertRaises(ValueError):
                    store.prune(plan, user_confirmed=True)
                self.assertEqual(store.load(identity)['state'], 'READY')
            finally:
                if held is not None:
                    held.close()

    def test_temporary_residue_does_not_create_eviction_pressure_or_deletion(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            (store.folder(identity) / 'sculpt.runtime@').write_bytes(b'unfinished')
            inventory = store.inspect()
            self.assertFalse(inventory['checkpoints'][0]['scene_usable'])
            plan = store.prune_plan({'max_bytes':1})
            self.assertEqual(plan['candidates'], [])
            self.assertEqual(plan['pressure_bytes'], 0)

    def replacement_after_tombstone(self, name):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            path = store.folder(identity) / name
            plan = store.prune_plan({'max_bytes':1})
            original_write = stores._write_json
            replaced = False
            def interleave(folder, metadata_name, record):
                nonlocal replaced
                original_write(folder, metadata_name, record)
                if record['state'] == 'PRUNED' and not replaced:
                    replaced = True
                    path.unlink()
                    path.write_bytes(b'foreign replacement')
            with patch.object(stores, '_write_json', side_effect=interleave), self.assertRaises(ValueError) as caught:
                store.prune(plan, user_confirmed=True)
            self.assertTrue(replaced)
            self.assertEqual(path.read_bytes(), b'foreign replacement')
            self.assertEqual(store.load(identity)['state'], 'PRUNED')
            self.assertTrue(caught.exception.partial_removal)
            self.assertEqual(caught.exception.known_bytes_freed, 0)
            self.assertEqual(caught.exception.removed_checkpoints, ())
            other = 'sculpt.runtime' if name == 'scene.blend' else 'scene.blend'
            self.assertTrue((store.folder(identity) / other).exists())

    def test_base_replacement_after_tombstone_is_preserved(self):
        self.replacement_after_tombstone('scene.blend')

    def test_companion_replacement_after_tombstone_is_preserved(self):
        self.replacement_after_tombstone('sculpt.runtime')

    def test_in_place_content_change_after_tombstone_is_preserved(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            path = store.folder(identity) / 'scene.blend'
            plan = store.prune_plan({'max_bytes':1})
            original_write = stores._write_json
            changed = False
            def interleave(folder, name, record):
                nonlocal changed
                original_write(folder, name, record)
                if record['state'] == 'PRUNED' and not changed:
                    changed = True
                    stat = path.stat()
                    if os.name == 'nt':
                        # Windows: the prune has the file open without write sharing, so it cannot change in place.
                        with self.assertRaises(PermissionError):
                            path.write_bytes(b'X' * len(fixture.base))
                        return
                    path.write_bytes(b'X' * len(fixture.base))
                    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
            if os.name == 'nt':
                with patch.object(stores, '_write_json', side_effect=interleave):
                    try:
                        store.prune(plan, user_confirmed=True)
                    except ValueError:
                        pass  # Either outcome keeps the person's bytes: the change itself was refused.
                self.assertTrue(changed)
                return
            with patch.object(stores, '_write_json', side_effect=interleave), self.assertRaises(ValueError):
                store.prune(plan, user_confirmed=True)
            self.assertEqual(path.read_bytes(), b'X' * len(fixture.base))

    def test_later_replacement_reports_only_known_original_bytes_removed(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            path = store.folder(identity) / 'sculpt.runtime'
            plan = store.prune_plan({'max_bytes':1})
            original_hold = stores.held_for_removal
            @contextmanager
            def interleave(folder, names):
                with original_hold(folder, names) as remove:
                    def remove_then_replace(name):
                        remove(name)
                        if name == 'scene.blend':
                            path.unlink()
                            path.write_bytes(b'foreign after first delete')
                    yield remove_then_replace
            with patch.object(stores, 'held_for_removal', side_effect=interleave), self.assertRaises(ValueError) as caught:
                store.prune(plan, user_confirmed=True)
            self.assertEqual(path.read_bytes(), b'foreign after first delete')
            self.assertFalse((store.folder(identity) / 'scene.blend').exists())
            self.assertEqual(caught.exception.known_bytes_freed, len(fixture.base))
            self.assertEqual(caught.exception.removed_checkpoints, ())
            self.assertTrue(caught.exception.partial_removal)

    def test_hash_return_mutation_cannot_become_a_new_verified_witness(self):
        with published() as fixture:
            store, identity = fixture.store, fixture.owner.capture_id
            path = store.folder(identity) / 'scene.blend'
            plan = store.prune_plan({'max_bytes':1})
            original_hash = capture._hash_descriptor
            changed = False
            def hash_then_change(fd, size):
                nonlocal changed
                digest = original_hash(fd, size)
                if not changed and os.fstat(fd).st_ino == path.stat().st_ino:
                    changed = True
                    stamp = path.stat()
                    path.write_bytes(b'X' * len(fixture.base))
                    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                return digest
            with patch.object(capture, '_hash_descriptor', side_effect=hash_then_change), self.assertRaises(ValueError):
                store.prune(plan, user_confirmed=True)
            self.assertTrue(changed)
            self.assertEqual(store.load(identity)['state'], 'READY')
            self.assertEqual(path.read_bytes(), b'X' * len(fixture.base))
            self.assertTrue((store.folder(identity) / 'sculpt.runtime').exists())


@unittest.skipIf(os.name == 'nt', 'POSIX stand-in; native Windows uses base tests')
class BundleConsumerFakeNtTests(BundleConsumerTests):
    def setUp(self):
        self.api = FakeNtApi()
        self.backend = patch.object(filesystem, '_windows_api', return_value=self.api)
        self.backend.start()
        self.addCleanup(self.backend.stop)


if __name__ == '__main__':
    unittest.main()
