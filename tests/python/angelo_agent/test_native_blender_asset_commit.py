# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Current stock transaction host contracts, never native capture acceptance.

Exercises approved private-copy cancellation, actual target routing and mode selection,
lost native returns, restoration receipts and retained transaction ownership.
"""
import suite_isolation  # noqa: F401
from pathlib import Path
import sys
import os
import threading
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import blender_asset_commit as commit, blender_asset_payload
from angelo_agent.team_library_files import ImportFailed, NotImported
import test_native_asset_stock_transaction as stock


class AssetCommitTests(unittest.TestCase):
    setUp = stock.StockAssetClientTest.setUp
    tearDown = stock.StockAssetClientTest.tearDown
    prepared = stock.StockAssetClientTest.prepared
    finish = stock.StockAssetClientTest.finish

    def test_all_focused_capabilities_and_methods_are_required_without_old_graph_flags(self):
        valid = self.api.asset_transaction_capabilities()
        for flag in commit.TRANSACTION_FLAGS:
            caps = dict(valid, **{flag: False})
            with self.subTest(flag=flag), patch.object(self.api, 'asset_transaction_capabilities', return_value=caps), \
                 patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_undo=self.api)}):
                with self.assertRaises(NotImported):
                    commit.native_api()
        for method in commit.METHODS:
            with self.subTest(method=method), patch.object(self.api, method, None), \
                 patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_undo=self.api)}):
                with self.assertRaises(NotImported):
                    commit.native_api()
        self.assertEqual(self.api.calls, [])

    def test_malformed_focused_capability_schema_and_extra_old_authority_are_refused(self):
        valid = self.api.asset_transaction_capabilities()
        for caps in (dict(valid, schema=True), dict(valid, schema=1.0),
                     dict(valid, modes=commit.MODES), dict(valid, payload_owner_ready=True)):
            with self.subTest(caps=caps), patch.object(self.api, 'asset_transaction_capabilities', return_value=caps), \
                 patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_undo=self.api)}):
                with self.assertRaises(NotImported):
                    commit.native_api()

    def test_begin_scope_and_format_precede_publication_without_separate_seal_api(self):
        self.prepared()
        def effect(*_args, **_kw):
            self.assertIs(commit._recovery_owner, self.payload)
            self.assertIs(self.payload.scope, self.api.scope)
            self.api.calls.append(('effect',))
            return {'actual_copy': self.payload.row['file']['path']}
        self.effect = effect
        self.finish(format_result=lambda facts: self.api.calls.append(('format',)) or facts)
        names = [call[0] for call in self.api.calls]
        self.assertLess(names.index('begin'), names.index('effect'))
        self.assertLess(names.index('effect'), names.index('format'))
        self.assertLess(names.index('format'), names.index('finish'))
        self.assertLess(names.index('finish'), names.index('close'))

    def test_restored_without_confirmed_close_retains_copy_and_never_claims_unchanged(self):
        payload = self.prepared()
        self.effect = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('Private native failure'))
        self.api.asset_close = Mock(side_effect=RuntimeError('Unknown close'))
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        self.assertFalse(caught.exception.scene_unchanged)
        self.assertIs(commit._recovery_owner, payload)
        self.assertTrue(Path(payload.row['file']['path']).exists())
        self.api.asset_close.assert_called_once_with(self.api.scope)

    def test_lost_restore_return_observes_exact_scope_without_repeating_finish(self):
        self.prepared()
        self.api.lose_finish = True
        self.effect = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('Native apply error'))
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        self.assertTrue(caught.exception.scene_unchanged)
        self.assertEqual([call for call in self.api.calls if call[0] == 'finish'], [('finish', False)])

    def test_cancel_before_copy_cannot_recreate_folder_or_enter_native_scope(self):
        self.payload = commit.AssetCommit(self.row, deadline=__import__('time').monotonic()+60,
                                         api=self.api, payload_format='glb')
        folder = self.payload.payload.folder
        self.payload.close()
        with self.assertRaises(InterruptedError):
            self.payload.seal_payload(self.row, threading.Event())
        self.assertFalse(folder.exists())
        self.assertEqual(self.api.calls, [])

    def test_copy_error_closes_actual_source_descriptor_and_never_publishes_row(self):
        self.payload = commit.AssetCommit(self.row, deadline=__import__('time').monotonic()+60,
                                         api=self.api, payload_format='glb')
        descriptors = []
        original_open = os.open
        def tracked(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            descriptors.append(fd)
            return fd
        with patch.object(blender_asset_payload.os, 'open', side_effect=tracked), \
             patch.object(Path, 'open', side_effect=OSError('Private output error')):
            with self.assertRaises(OSError):
                self.payload.seal_payload(self.row, threading.Event())
        self.assertIsNone(self.payload.row)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])
        self.assertEqual(self.api.calls, [])

    def test_later_copy_source_error_closes_prior_descriptor_and_keeps_row_unpublished(self):
        self.payload = commit.AssetCommit(self.row, deadline=__import__('time').monotonic()+60,
                                         api=self.api, payload_format='blend')
        first = self.payload.selected['file']
        missing = dict(first, relative='deps/missing.blend', path=str(Path(self.folder.name)/'missing.blend'))
        self.payload.selected['files'] = (first, missing)
        descriptors = []
        original_open = os.open
        def tracked(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            descriptors.append(fd)
            return fd
        with patch.object(blender_asset_payload.os, 'open', side_effect=tracked):
            with self.assertRaises(FileNotFoundError):
                self.payload.seal_payload(self.row, threading.Event())
        self.assertIsNone(self.payload.row)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])
        self.assertEqual(self.api.calls, [])

    def test_actual_agent_finish_preserves_requested_target_order(self):
        from angelo_agent import blender_assets_agent as agent
        first = SimpleNamespace(session_uid=11, type='MESH', library=None, data=SimpleNamespace(library=None))
        second = SimpleNamespace(session_uid=22, type='MESH', library=None, data=SimpleNamespace(library=None))
        scene = SimpleNamespace(objects=[first, second])
        area = SimpleNamespace(type='VIEW_3D', regions=[SimpleNamespace(type='WINDOW')])
        context = SimpleNamespace(temp_override=lambda **_kwargs: nullcontext())
        payload = SimpleNamespace(finish=Mock(return_value={'fixture': 'native protocol, not effect proof'}))
        window = object()
        modules = {'bpy': SimpleNamespace(context=context),
                   'angelo_agent.library_agent': SimpleNamespace(_window_for=lambda _scene: window)}
        from angelo_agent import live_windows
        with patch.dict(sys.modules, modules), patch.object(live_windows, 'areas', return_value=[area]):
            agent._finish_held(scene, {'id': 'item'}, None, [22, 11], payload)
        self.assertEqual(payload.finish.call_args.kwargs['targets'], [second, first])

    def test_actual_agent_finish_refuses_duplicate_targets_before_native_effects(self):
        from angelo_agent import blender_assets_agent as agent, live_windows
        obj = SimpleNamespace(session_uid=11, type='MESH', library=None, data=SimpleNamespace(library=None))
        scene = SimpleNamespace(objects=[obj])
        payload = SimpleNamespace(finish=Mock())
        area = SimpleNamespace(type='VIEW_3D', regions=[SimpleNamespace(type='WINDOW')])
        context = SimpleNamespace(temp_override=lambda **_kwargs: nullcontext())
        modules = {'bpy': SimpleNamespace(context=context),
                   'angelo_agent.library_agent': SimpleNamespace(_window_for=lambda _scene: object())}
        with patch.dict(sys.modules, modules), patch.object(live_windows, 'areas', return_value=[area]) as areas:
            with self.assertRaises(NotImported):
                agent._finish_held(scene, {'id': 'item'}, None, [11, 11], payload)
        payload.finish.assert_not_called()
        areas.assert_not_called()

    def test_seven_existing_cases_select_only_the_five_declared_modes(self):
        cases = (('model', 'OBJECT', (), 'import_objects'),
                 ('model', 'COLLECTION', (), 'import_objects'),
                 ('material', 'MATERIAL', (object(),), 'assign_material'),
                 ('world', 'WORLD', (), 'use_world'),
                 ('node_group', 'NODETREE', (object(),), 'apply_geometry_nodes'),
                 ('node_group', 'NODETREE', (), 'load_datablock'),
                 ('brush', 'BRUSH', (), 'load_datablock'))
        for kind, id_type, targets, mode in cases:
            with self.subTest(kind=kind, id_type=id_type, targets=bool(targets)):
                self.assertEqual(commit.mode_for(dict(kind=kind, id_type=id_type), targets), mode)


if __name__ == '__main__':
    unittest.main()
