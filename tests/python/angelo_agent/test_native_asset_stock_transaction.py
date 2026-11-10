# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Client protocol/real private-copy fixtures, NOT native Undo/import acceptance.

Native operator, dirty-image, Brush and save/reopen coverage belongs to the separate
actual Blender fixture. No fake native receipt here earns that acceptance.
"""
import suite_isolation  # noqa: F401
from pathlib import Path
import hashlib
import ast
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import blender_asset_commit as commit
from angelo_agent.team_library_files import ImportFailed, NotImported, Refused


class Native:
    def __init__(self):
        self.calls = []
        self.pending = None
        self.scope = object()
        self.state = dict(schema=1, state='ACTIVE', published=False, restored=False,
                          recovery_required=False, recoverable=False, closed=False, error=None)
        self.lose_finish = self.lose_close = self.fail_restore = False

    def asset_transaction_capabilities(self):
        return dict(schema=1, **dict.fromkeys(commit.TRANSACTION_FLAGS, True))

    def asset_pending(self):
        return self.pending

    def asset_begin(self, *, source_paths=()):
        self.source_paths = source_paths
        self.calls.append(('begin',))
        self.pending = self.scope
        return self.scope

    def asset_finish(self, scope, *, keep):
        assert scope is self.scope
        self.calls.append(('finish', keep))
        if not keep and self.fail_restore:
            self.state.update(state='RECOVERY_REQUIRED', published=False,
                              recovery_required=True, recoverable=True, error='Test-only restore failure')
        else:
            self.state.update(state='COMMITTED' if keep else 'RESTORED', published=keep, restored=not keep)
        if self.lose_finish:
            raise MemoryError('Test-only lost return after stored facts')
        return dict(self.state)

    def asset_status(self, scope):
        assert scope is self.scope
        self.calls.append(('status',))
        return dict(self.state)

    def asset_recover(self, scope):
        assert scope is self.scope
        self.calls.append(('recover',))
        self.state.update(state='RESTORED', published=False, restored=True,
                          recovery_required=False, recoverable=False, error=None)
        return dict(self.state)

    def asset_close(self, scope):
        assert scope is self.scope
        self.calls.append(('close',))
        if self.state['state'] not in ('COMMITTED', 'RESTORED'):
            raise RuntimeError('Test-only unsettled close')
        self.state['closed'] = True
        self.pending = None
        if self.lose_close:
            raise MemoryError('Test-only lost close return')
        return dict(self.state)


class StockAssetClientTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        path = Path(self.folder.name) / 'asset.glb'
        # Deliberately just protocol bytes: the stock operator is a TEST DOUBLE in this suite.
        path.write_bytes(b'client-protocol-private-copy')
        self.row = dict(id='fab:12345678-1234-1234-1234-123456789abc', name='Selected', kind='model',
            origin='fab', license='Fab Standard', author='Author', file=dict(relative='asset.glb',
            path=str(path), size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.api = Native()
        self.payload = None
        self.undo = patch('angelo_agent.blender_assets.require_undo')
        self.undo_mock = self.undo.start()
        self.effect = lambda _context, row, **_kw: dict(path=row['file']['path'], source='test-double')
        self.modules = patch.dict(sys.modules, {'angelo_agent.asset_stock_import':
            SimpleNamespace(apply_glb=lambda context, row, **kw: self.effect(context, row, **kw))})
        self.modules.start()

    def tearDown(self):
        # These are TEST-DOUBLE native owners, never a native HOLD discharge.
        if self.payload:
            self.payload.payload.close()
        commit._recovery_owner = None  # Test-double owner only.
        self.modules.stop()
        self.undo.stop()
        self.folder.cleanup()

    def prepared(self):
        self.payload = commit.AssetCommit(self.row, deadline=time.monotonic() + 60,
                                         api=self.api, payload_format='glb')
        errors = []
        def worker():
            try:
                self.payload.seal_payload(self.row, threading.Event())
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(errors, [])
        return self.payload

    def finish(self, **kw):
        return self.payload.finish(SimpleNamespace(mode='OBJECT', window_manager=SimpleNamespace(session_uid=1),
            window=object(), scene=object(), view_layer=object()), self.row, **kw)

    def test_lost_begin_adopts_actual_pending_and_uses_stored_recovery(self):
        payload = self.prepared()
        def begin(**_kw):
            self.api.pending = self.api.scope
            self.api.state.update(state='RECOVERY_REQUIRED', recovery_required=True, recoverable=True)
            raise MemoryError('Test-only lost begin return')
        self.api.asset_begin = begin
        with self.assertRaises(ImportFailed):
            self.finish()
        self.assertIs(payload.scope, self.api.scope)
        self.assertIs(commit._recovery_owner, payload)
        self.assertTrue(Path(payload.row['file']['path']).exists())
        self.assertFalse(payload.begin_failed_pristine)
        self.assertNotIn(('finish', False), self.api.calls)

    def test_absent_pending_after_context_drift_is_not_pristine(self):
        payload = self.prepared()
        context = SimpleNamespace(mode='OBJECT', window_manager=SimpleNamespace(session_uid=1),
                                  window=object(), scene=object(), view_layer=object())
        def begin(**_kw):
            context.window_manager.session_uid = 2
            raise RuntimeError('Test-only document replacement before begin return')
        self.api.asset_begin = begin
        with self.assertRaises(ImportFailed) as caught:
            payload.finish(context, self.row)
        self.assertFalse(caught.exception.scene_unchanged)
        self.assertFalse(payload.begin_failed_pristine)
        self.assertIs(commit._recovery_owner, payload)
        self.assertTrue(Path(payload.row['file']['path']).exists())

    def test_pending_query_failure_retains_private_copy(self):
        payload = self.prepared()
        def begin(**_kw):
            self.api.asset_pending = lambda: (_ for _ in ()).throw(RuntimeError('Lost query'))
            raise RuntimeError('Lost begin')
        self.api.asset_begin = begin
        with self.assertRaises(ImportFailed):
            self.finish()
        self.assertIs(commit._recovery_owner, payload)
        self.assertTrue(Path(payload.row['file']['path']).exists())
        self.assertFalse(payload.begin_failed_pristine)

    def test_reloaded_module_state_discovers_native_pending_without_replaying(self):
        self.api.pending = self.api.scope
        self.api.state.update(state='RECOVERY_REQUIRED', recovery_required=True, recoverable=True)
        commit._recovery_owner = None
        with patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_undo=self.api)}):
            self.assertEqual(commit.recovery_status()['state'], 'RECOVERY_REQUIRED')
            self.assertIs(commit._recovery_owner.scope, self.api.scope)
            self.assertEqual(self.api.calls, [('status',)])
            self.assertTrue(commit.recover_pending()['closed'])
        self.assertIsNone(commit._recovery_owner)

    def test_focused_capability_uses_bpy_accessor_and_does_not_accept_old_full_flags(self):
        with patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_undo=self.api)}):
            self.assertIs(commit.native_api('glb'), self.api)
            self.api.asset_transaction_capabilities = lambda: dict(schema=1, modes=(),
                payload_owner_ready=True, semantic_inspection_ready=True)
            with self.assertRaises(NotImported):
                commit.native_api('blend')

    def test_real_worker_copy_is_private_hash_verified_and_held_during_stock_call(self):
        payload = self.prepared()
        source = Path(self.row['file']['path'])
        copied = Path(payload.row['file']['path'])
        self.assertNotEqual(copied, source)
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        def effect(_context, row, **_kw):
            self.assertEqual(self.api.calls, [('begin',)])
            self.assertEqual(self.api.source_paths, ((str(source.resolve()), str(copied.resolve())),))
            self.assertEqual(Path(row['file']['path']).read_bytes(), source.read_bytes())
            self.assertTrue(payload.payload.owns(payload.row))
            return {'actual_copy_path': row['file']['path']}
        self.effect = effect
        result = self.finish()
        self.assertEqual(result['actual_copy_path'], str(copied))
        self.assertTrue(payload.quiescent(payload.status()))
        self.assertFalse(copied.exists())
        self.assertIsNone(commit._recovery_owner)

    def test_lost_finish_return_queries_stored_commit_without_replay(self):
        self.prepared()
        self.api.lose_finish = True
        self.finish()
        self.assertEqual([call for call in self.api.calls if call[0] == 'finish'], [('finish', True)])
        self.assertTrue(self.api.state['closed'])

    def test_lost_close_return_queries_exact_closed_status_without_replay(self):
        self.prepared()
        self.api.lose_close = True
        self.finish()
        self.payload.close()
        self.assertEqual(self.api.calls.count(('close',)), 1)
        self.assertIsNone(commit._recovery_owner)

    def test_operator_error_earns_unchanged_only_from_restored_closed_receipt(self):
        self.prepared()
        def effect(*_args, **_kw):
            raise RuntimeError('Test-only stock operator failure')
        self.effect = effect
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        self.assertTrue(caught.exception.scene_unchanged)
        self.assertEqual([call for call in self.api.calls if call[0] == 'finish'], [('finish', False)])
        self.assertTrue(self.api.state['closed'])

    def test_formatter_error_is_inside_transaction_before_publication(self):
        self.prepared()
        def formatter(_facts):
            raise RuntimeError('Test-only formatter failure')
        with self.assertRaises(ImportFailed) as caught:
            self.finish(format_result=formatter)
        self.assertTrue(caught.exception.scene_unchanged)
        self.assertNotIn(('finish', True), self.api.calls)

    def test_reentrant_stop_waits_until_stock_operator_unwinds(self):
        payload = self.prepared()
        def effect(*_args, **_kw):
            payload.close()
            self.assertNotIn(('finish', False), self.api.calls)
            self.assertTrue(Path(payload.row['file']['path']).exists())
            return {'stopped': True}
        self.effect = effect
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        self.assertTrue(caught.exception.scene_unchanged)
        self.assertEqual([call for call in self.api.calls if call[0] == 'finish'], [('finish', False)])

    def test_unknown_restore_retains_actual_client_owner_and_private_copy(self):
        payload = self.prepared()
        self.api.fail_restore = True
        def effect(*_args, **_kw):
            raise RuntimeError('Test-only effect failure')
        self.effect = effect
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        self.assertFalse(caught.exception.scene_unchanged)
        self.assertIs(commit._recovery_owner, payload)
        self.assertTrue(Path(payload.row['file']['path']).exists())
        self.assertEqual(commit.recovery_status()['state'], 'RECOVERY_REQUIRED')
        with self.assertRaises(NotImported):
            commit.AssetCommit(self.row, deadline=time.monotonic() + 60, api=self.api, payload_format='glb')
        self.assertEqual(self.api.calls.count(('begin',)), 1)

    def test_explicit_recovery_uses_distinct_native_route_then_releases_owner(self):
        payload = self.prepared()
        self.api.fail_restore = True
        def effect(*_args, **_kw):
            raise RuntimeError('Test-only effect failure')
        self.effect = effect
        with self.assertRaises(ImportFailed):
            self.finish()
        self.assertIs(commit._recovery_owner, payload)
        self.assertTrue(commit.recover_pending()['closed'])
        self.assertEqual(self.api.calls.count(('recover',)), 1)
        self.assertEqual([call for call in self.api.calls if call[0] == 'finish'], [('finish', False)])
        self.assertIsNone(commit._recovery_owner)

    def test_native_begin_exception_is_preimport_by_focused_abi_but_none_return_is_not(self):
        self.prepared()
        def failed_begin(**_kwargs):
            raise RuntimeError('Test double for synchronously disposed native begin failure')
        self.api.asset_begin = failed_begin
        with self.assertRaises(NotImported):
            self.finish()
        self.assertIsNone(commit._recovery_owner)
        self.assertTrue(self.payload.begin_failed_pristine)
        self.prepared()
        self.api.asset_begin = lambda **_kwargs: None
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        self.assertFalse(caught.exception.scene_unchanged)
        self.assertIs(commit._recovery_owner, self.payload)
        self.assertFalse(self.payload.begin_failed_pristine)

    def test_reserved_source_subtree_is_untouched_and_refusal_requests_native_restore(self):
        # Execute the ACTUAL production stamp function with explicit dictionary-ID doubles.
        # This tests two-pass refusal/protocol, not Blender IDProperty free or native restore.
        source = ROOT / 'scripts/addons_core/angelo_agent/asset_stock_import.py'
        tree = ast.parse(source.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'stamp_created')
        namespace = {'Refused': Refused, 'NotImported': NotImported,
                     'library_import': SimpleNamespace(PROPERTY='angelo_library')}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        class IDDouble(dict):
            id_type = 'OBJECT'
        first, conflicting = IDDouble(), IDDouble()
        subtree = {'nested': {'artist source value': 37}}
        conflicting['angelo_blender_asset'] = subtree
        self.prepared()
        def effect(*_args, **_kw):
            namespace['stamp_created']((first, conflicting), self.row)
        self.effect = effect
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        self.assertIn('reserved Angelo provenance fields', str(caught.exception))
        self.assertTrue(caught.exception.scene_unchanged)
        self.assertEqual(first, {})
        self.assertIs(conflicting['angelo_blender_asset'], subtree)
        self.assertEqual(subtree, {'nested': {'artist source value': 37}})
        self.assertEqual([call for call in self.api.calls if call[0] == 'finish'], [('finish', False)])

    def test_preferences_and_selection_are_rechecked_before_begin(self):
        self.prepared()
        self.undo_mock.side_effect = NotImported('Enable Undo')
        with self.assertRaises(NotImported):
            self.finish()
        self.assertEqual(self.api.calls, [])
        self.undo_mock.side_effect = None
        row = dict(self.row, id='fab:22345678-1234-1234-1234-123456789abc')
        with self.assertRaises(NotImported):
            self.payload.finish(SimpleNamespace(mode='OBJECT'), row)
        self.assertEqual(self.api.calls, [])

    def test_receipt_rejects_integer_published_instead_of_native_boolean(self):
        with self.assertRaises(RuntimeError):
            commit._receipt(dict(self.api.state, published=1))

class StockAssetChangeConsumerTest(unittest.TestCase):
    def test_both_source_formatters_keep_real_scene_diff_rows_for_existing_change_chip(self):
        from angelo_agent import blender_assets_agent, fab_native_job, chat_changes
        from angelo_agent.scene_signature import scene_diff
        before = {'objects': {}, 'count': 0, 'truncated': False}
        after = {'objects': {'17': {'name': 'Actual observed addition', 'type': 'MESH', 'vertices': 4}},
                 'count': 1, 'truncated': False}
        changes = scene_diff(before, after)
        obj = SimpleNamespace(name='Actual observed addition', type='MESH')
        row = dict(id='selected', name='Selected', kind='model', license='CC0', author='Author')
        common = dict(schema=1, item_id='selected', title='Selected', kind='model', id_type='OBJECT',
            datablock=obj.name, usage='placed_model', license='CC0', author='Author', objects=(obj,),
            object_count=1, modifiers=(), loaded_only=False, changes=changes)
        # Only handle conversion is a test double. The diff producer and chat consumer are real.
        with patch.dict(sys.modules, {'angelo_agent.scene_queries':
                                      SimpleNamespace(object_handle=lambda _obj: 'fixture-handle')}):
            results = (blender_assets_agent._native_result(dict(common, source='blender'), row),
                       fab_native_job._result(dict(common, source='fab'), row))
        for result in results:
            self.assertIs(result['changes'], changes)
            self.assertEqual(result['objects'][0]['name'], 'Actual observed addition')
            report = chat_changes.scene_change_report(result)
            self.assertEqual(report['added'][0]['name'], 'Actual observed addition')
            self.assertFalse(report['truncated'])


class StockDependencyGuardTest(unittest.TestCase):
    @staticmethod
    def guards():
        source = ROOT / 'scripts/addons_core/angelo_agent/blender_asset_import.py'
        tree = ast.parse(source.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('_inspect_packed_image', '_inspect')]
        namespace = {'Refused': Refused, 'ids': lambda blocks: dict(enumerate(blocks))}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
        return namespace

    def test_actual_mesh_cache_filepath_guard_refuses_before_artist_evaluation(self):
        inspect = self.guards()['_inspect']
        modifier = SimpleNamespace(type='MESH_CACHE', filepath='/unverified/external.mdd')
        obj = SimpleNamespace(id_type='OBJECT', modifiers=[modifier], animation_data=None, node_tree=None)
        with self.assertRaisesRegex(Refused, 'external Mesh Cache'):
            inspect([obj])
        self.assertEqual(modifier.filepath, '/unverified/external.mdd')
        modifier.filepath = ''
        inspect([obj])  # No blanket modifier/type refusal; native empty path is disabled.

    def test_bundled_absolute_relative_and_private_library_tokens_use_only_exact_copy_routes(self):
        source = ROOT / 'scripts/addons_core/angelo_agent/blender_asset_import.py'
        tree = ast.parse(source.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_append')
        assets_tree = ast.parse((ROOT / 'scripts/addons_core/angelo_agent/blender_assets.py').read_text())
        resolver = next(node for node in assets_tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'dependency_path')
        namespace = {'Path': Path, 'NotImported': NotImported, 'Refused': Refused,
                     'index': SimpleNamespace(TYPES={'OBJECT': ('objects', 'model')}), 'ids': lambda _data: {}}
        exec(compile(ast.Module(body=[resolver], type_ignores=[]), str(source), 'exec'), namespace)
        namespace['blender_assets'] = SimpleNamespace(dependency_path=namespace['dependency_path'])
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            original, private = root / 'original', root / 'private'
            source_root, source_dep = original / 'root.blend', original / 'dep.blend'
            copy_root, copy_dep = private / 'root.blend', private / 'dep.blend'
            files = tuple({'path': str(path)} for path in (copy_root, copy_dep))
            row = dict(id_type='OBJECT', name='Chosen', origin='bundled', file=files[0], files=files,
                       _source_paths=((str(source_root), str(copy_root)), (str(source_dep), str(copy_dep))))
            chosen = object()
            class Libraries:
                def __init__(self, token): self.token, self.calls, self.live, self.removed = token, [], [], []
                def __iter__(self): return iter(self.live)
                def remove(self, library):
                    self.removed.append(library.filepath)
                    self.live.remove(library)
                def load(self, path, **_kwargs):
                    assert path in (str(copy_root), str(copy_dep)), 'Unverified original fallback attempted'
                    self.calls.append(path)
                    libraries = [SimpleNamespace(filepath=self.token)] if path == str(copy_root) else []
                    source = SimpleNamespace(libraries=libraries, objects=['Chosen'])
                    target = SimpleNamespace(objects=[])
                    store = self
                    class Load:
                        def __enter__(self): return source, target
                        def __exit__(self, kind, *_args):
                            if kind is None and target.objects:
                                target.objects = [chosen]
                                store.live.append(SimpleNamespace(filepath=store.token, as_pointer=lambda: 17))
                    return Load()
            for token in (str(source_dep), '//dep.blend', str(copy_dep)):
                libraries = Libraries(token)
                self.assertIs(namespace['_append'](SimpleNamespace(libraries=libraries), row), chosen)
                self.assertEqual(set(libraries.calls), {str(copy_root), str(copy_dep)})
                self.assertEqual(libraries.removed, [token])
                self.assertEqual(libraries.live, [])
            for token in (str(root / 'unapproved.blend'), '//outside.blend'):
                with self.assertRaisesRegex(Refused, 'unverified'):
                    namespace['_append'](SimpleNamespace(libraries=Libraries(token)), row)

    def test_actual_packed_profile_requires_every_single_view_tile_and_positive_payload(self):
        inspect = self.guards()['_inspect_packed_image']
        def packed(tile=1001, view=0, size=4):
            return SimpleNamespace(tile_number=tile, view=view,
                                   packed_file=SimpleNamespace(size=size))
        image = SimpleNamespace(source='FILE', use_multiview=False, is_multiview=False,
                                tiles=[SimpleNamespace(number=1001)], packed_files=[packed()])
        inspect(image)
        image.source = 'TILED'
        image.tiles.append(SimpleNamespace(number=1002))
        with self.assertRaisesRegex(Refused, 'incomplete packed'):
            inspect(image)  # First packed_file alone would have passed the old guard.
        image.packed_files.append(packed(1002))
        inspect(image)
        for invalid in ([packed(), packed()], [packed(), packed(1002, view=1)],
                        [packed(), packed(1002, size=0)]):
            image.packed_files = invalid
            with self.assertRaisesRegex(Refused, 'incomplete packed'):
                inspect(image)
        image.packed_files = [packed(), packed(1002)]
        image.use_multiview = True
        with self.assertRaisesRegex(Refused, 'multiview'):
            inspect(image)
        image.use_multiview = False
        for source in ('MOVIE', 'SEQUENCE'):
            image.source = source
            with self.assertRaisesRegex(Refused, 'movie or image sequence'):
                inspect(image)


class AssetRecoveryUIHandlerTest(unittest.TestCase):
    """Actual UI handler bodies with host UI doubles; no GUI/native acceptance."""

    def setUp(self):
        tree = ast.parse((ROOT / 'scripts/addons_core/angelo_agent/library_ui.py').read_text())
        names = {'_asset_recovery_status', 'ANGELO_OT_library_recover', 'ANGELO_PT_asset_recovery'}
        selected = ast.Module(body=[node for node in tree.body if getattr(node, 'name', '') in names], type_ignores=[])
        self.reports, self.redraws = [], []
        reports = self.reports
        class Operator:
            def report(self, level, message):
                reports.append((level, message))
        self.env = dict(__package__='angelo_agent', Operator=Operator, Panel=type('Panel', (), {}),
                        rpt_=lambda text: text, iface_=lambda text: text)
        exec(compile(selected, 'library_ui.py:selected-actual-handlers', 'exec'), self.env)
        self.operator = self.env['ANGELO_OT_library_recover']
        self.panel = self.env['ANGELO_PT_asset_recovery']
        self.overlay = patch.dict(sys.modules, {'angelo_agent.library_overlay': SimpleNamespace(
            redraw_all=lambda: self.redraws.append(True))})
        self.overlay.start()

    def tearDown(self):
        self.overlay.stop()

    def test_readonly_poll_absent_unsupported_and_uncertain(self):
        with patch.object(commit, 'recovery_status', return_value=None), patch.object(commit, 'recover_pending') as recover:
            self.assertFalse(self.panel.poll(None))
            self.assertFalse(self.operator.poll(None))
            recover.assert_not_called()
        with patch.object(commit, 'recovery_status', side_effect=NotImported('Old app')):
            self.assertFalse(self.panel.poll(None))
        with patch.object(commit, 'recovery_status', side_effect=ReferenceError('Dead RNA')):
            self.assertTrue(self.panel.poll(None))
            self.assertFalse(self.operator.poll(None))

    def test_explicit_handler_rechecks_and_invokes_native_recovery_once(self):
        with patch.object(commit, 'recovery_status', return_value={'recoverable': True}), \
             patch.object(commit, 'recover_pending', return_value={'state': 'RESTORED', 'closed': True}) as recover:
            self.assertTrue(self.operator.poll(None))
            self.assertEqual(self.operator().execute(None), {'FINISHED'})
            recover.assert_called_once_with()
        self.assertEqual(self.operator.bl_options, {'REGISTER'})

    def test_unavailable_or_failed_recovery_never_reports_success_or_retries(self):
        with patch.object(commit, 'recovery_status', return_value={'recoverable': False}), \
             patch.object(commit, 'recover_pending') as recover:
            self.assertEqual(self.operator().execute(None), {'CANCELLED'})
            recover.assert_not_called()
        with patch.object(commit, 'recovery_status', return_value={'recoverable': True}), \
             patch.object(commit, 'recover_pending', side_effect=RuntimeError('Unknown result')) as recover:
            self.assertEqual(self.operator().execute(None), {'CANCELLED'})
            recover.assert_called_once_with()
        self.assertTrue(all(level == {'ERROR'} for level, _text in self.reports))
        self.assertTrue(all('Keep this document open' in text for _level, text in self.reports))

    def test_busy_and_unknown_draw_remain_readonly_and_offer_no_action(self):
        calls = []
        layout = SimpleNamespace(column=lambda **_kw: layout,
            label=lambda **kw: calls.append(('label', kw['text'])),
            operator=lambda *args, **kw: calls.append(('operator', args)))
        panel = self.panel()
        panel.layout = layout
        with patch.object(commit, '_recovery_owner', SimpleNamespace(applying=True)), \
             patch.object(commit, 'recovery_status', return_value={'recoverable': True}), \
             patch.object(commit, 'recover_pending') as recover:
            self.assertFalse(self.operator.poll(None))
            panel.draw(None)
            recover.assert_not_called()
        with patch.object(commit, 'recovery_status', side_effect=RuntimeError('Unknown native status')):
            panel.draw(None)
        self.assertFalse(any(kind == 'operator' for kind, _ in calls))


if __name__ == '__main__':
    unittest.main()
