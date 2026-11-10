# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual pending import trust boundaries; run unchanged against the old donor for genuine assertion reds."""

import suite_isolation  # noqa: F401  First: never expose real provider logins.

import hashlib
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import blender_assets as assets, blender_assets_agent as agent, library_agent, private_roots
from angelo_agent.refusal_codes import RefusalError


class ImmediateWorker:
    """Deterministic worker scheduling, not a replacement for the real byte verification/copy."""
    def __init__(self, work):
        self.cancel, self.done = threading.Event(), threading.Event()
        self.value, self.error = None, None
        try:
            self.value = work(self.cancel)
        except Exception as error:
            self.error = error
        self.done.set()


class ImportIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.original = b'approved-content'
        self.source = self.cache / 'model.blend'
        self.source.write_bytes(self.original)
        file = dict(path=str(self.source), relative='model.blend', size=len(self.original),
                    sha256=hashlib.sha256(self.original).hexdigest())
        self.row = dict(id='blender:' + 'a' * 64, kind='model', origin='bundled', file=file,
                        files=(file,), name='A model', license='CC0', author='An author')
        self.service = SimpleNamespace(window=SimpleNamespace(scene=object()), _require_view=lambda: None)
        self.args = dict(item_id=self.row['id'])

    def test_disabled_and_zero_undo_refuse_before_approval(self):
        for global_undo, steps in ((False, 32), (True, 0)):
            edit = SimpleNamespace(use_global_undo=global_undo, undo_steps=steps)
            bpy = SimpleNamespace(context=SimpleNamespace(preferences=SimpleNamespace(edit=edit)))
            with self.subTest(global_undo=global_undo, steps=steps), patch.dict(sys.modules, bpy=bpy), \
                    patch.object(assets, 'get', return_value=self.row):
                with self.assertRaises(RefusalError):
                    agent.precondition(self.service, self.args)

    def test_pending_import_consumes_private_verified_bytes_after_cache_swap(self):
        consumed, folders = [], []

        def allocate(_prefix):
            folder = self.root / ('owned-' + str(len(folders)))
            folder.mkdir()
            folders.append(folder)
            return folder

        def finish(_scene, row, *_args):
            payload = _args[-1]
            row = payload.row  # Actual _finish uses the held prepared copy, not the mutable catalog row.
            # A native cache rewrite after verification must not replace either native load's input.
            self.source.write_bytes(b'not-the-approved-content')
            with payload.payload.hold(row):
                consumed.append(Path(row['file']['path']).read_bytes())
            payload.close()
            return {'success': True}

        from angelo_agent import blender_asset_commit
        from test_native_asset_stock_transaction import Native
        with patch.object(blender_asset_commit, 'native_api', return_value=Native()), \
                patch.object(agent, 'precondition'), patch.object(assets, 'get', return_value=self.row), \
                patch.object(agent.live_target, 'scene_ref', return_value=lambda: self.service.window.scene), \
                patch.object(library_agent, '_Worker', ImmediateWorker), patch.object(agent, '_finish', finish), \
                patch.object(private_roots, 'allocate', allocate):
            pending = agent.use(self.service, self.args)
            result = None
            for _ in range(4):
                result = pending.poll()
                if result is not None:
                    break
            self.assertTrue(result['success'])
            self.assertEqual(consumed, [self.original])
            self.assertEqual(self.source.read_bytes(), b'not-the-approved-content')
            self.assertTrue(folders, 'Import must own a private copy rather than the mutable native cache')
            self.assertTrue(all(not folder.exists() for folder in folders), 'Completion must release the copy')


    def payload(self):
        from angelo_agent.blender_asset_payload import PayloadCopy
        folder = self.root / 'owned-copy'
        folder.mkdir()
        with patch.object(private_roots, 'allocate', return_value=folder):
            owner = PayloadCopy()
        self.addCleanup(owner.close)
        return owner

    def test_dependency_closure_preserves_layout_and_provenance(self):
        dependency = self.cache / 'nested' / 'dependency.blend'
        dependency.parent.mkdir()
        dependency.write_bytes(b'other-approved-data')
        file = dict(path=str(dependency), relative='nested/dependency.blend', size=dependency.stat().st_size,
                    sha256=hashlib.sha256(dependency.read_bytes()).hexdigest())
        row = dict(self.row, files=(*self.row['files'], file))
        owner = self.payload()
        prepared = owner.prepare(row, threading.Event())
        dependency.write_bytes(b'changed')
        self.assertTrue(owner.owns(prepared))
        self.assertEqual(Path(prepared['files'][1]['path']).relative_to(owner.folder).as_posix(), file['relative'])
        self.assertEqual(Path(prepared['files'][1]['path']).read_bytes(), b'other-approved-data')
        self.assertEqual({k: prepared[k] for k in ('id', 'name', 'license', 'author', 'origin')},
                         {k: row[k] for k in ('id', 'name', 'license', 'author', 'origin')})
        self.assertEqual(self.row['file']['path'], str(self.source))

    def test_corrupt_copy_never_publishes_a_lease(self):
        owner = self.payload()
        self.source.write_bytes(b'x' * len(self.original))  # Same size: only the selected digest can reject it.
        with self.assertRaises(ValueError):
            owner.prepare(self.row, threading.Event())
        self.assertIsNone(owner.row)
        owner.close()
        self.assertFalse(owner.folder.exists())

    def test_closure_budget_refuses_before_reading_any_file(self):
        from angelo_agent.blender_asset_index import MAX_FILE
        owner = self.payload()
        huge = dict(self.row['file'], size=MAX_FILE)
        second = dict(huge, path='/not-opened.blend', relative='other.blend', size=1)
        with patch('os.open', side_effect=AssertionError('Budget admission must precede file I/O')):
            with self.assertRaisesRegex(ValueError, 'staging limit'):
                owner.prepare(dict(self.row, file=huge, files=(huge, second)), threading.Event())
        self.assertIsNone(owner.row)

    def test_close_waits_for_inflight_copy_to_unwind(self):
        from angelo_agent import blender_asset_payload
        owner = self.payload()
        digest = hashlib.sha256()

        def close_during_copy(data):
            digest.update(data)
            owner.close()
            self.assertTrue(owner.folder.exists(), 'Close must not remove an active copy')

        closing_digest = SimpleNamespace(update=close_during_copy, hexdigest=digest.hexdigest)
        with patch.object(blender_asset_payload.hashlib, 'sha256', return_value=closing_digest):
            with self.assertRaises(InterruptedError):
                owner.prepare(self.row, threading.Event())
        self.assertIsNone(owner.row)
        self.assertFalse(owner.folder.exists())

    def test_close_before_worker_start_cannot_recreate_private_folder(self):
        owner = self.payload()
        owner.close()
        self.assertFalse(owner.folder.exists())
        with self.assertRaises(InterruptedError):
            owner.prepare(self.row, threading.Event())
        self.assertIsNone(owner.row)
        self.assertFalse(owner.folder.exists())

    def test_close_during_native_use_defers_cleanup_through_failure_unwind(self):
        from angelo_agent import blender_asset_payload
        owner = self.payload()
        row = owner.prepare(self.row, threading.Event())
        remove = blender_asset_payload.shutil.rmtree
        with patch.object(blender_asset_payload.shutil, 'rmtree', wraps=remove) as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'native rollback'):
                with owner.hold(row):
                    owner.close()
                    owner.close()
                    self.assertFalse(owner.owns(row), 'Closed lease must refuse new admission')
                    with self.assertRaises(ValueError), owner.hold(row):
                        self.fail('Closed owner admitted another hold')
                    self.assertEqual(Path(row['file']['path']).read_bytes(), self.original)
                    self.assertTrue(owner.folder.exists(), 'Active native owner still needs its payload')
                    raise RuntimeError('native rollback')
            owner.close()
            self.assertEqual(cleanup.call_count, 1)
        self.assertFalse(owner.folder.exists())


if __name__ == '__main__':
    unittest.main()
