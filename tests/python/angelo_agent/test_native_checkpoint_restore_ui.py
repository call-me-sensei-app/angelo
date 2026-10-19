# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recovery fault matrix; native fixture separately proves actual file/scene effects."""

from contextlib import nullcontext
import copy
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import checkpoint_restore_ui as flow


class Controller:
    def __init__(self, identity, timeline):
        self.state = {'id': identity, 'messages': [{'role': 'user', 'text': 'Before request',
            'checkpoint': 'selected', 'checkpoint_scope': 'before_request'}], 'native_id': 'old-native',
            'runtime_sessions': {'old': {'native_id': 'old-native'}}, 'task_memory': {'stale': False}}
        self.document, self.request_checkpoint, self.busy = ('old', 1), 'selected', False
        self.document_invalid, self.version, self.lock = False, 0, threading.RLock()
        self.timeline, self.saved, self.saves, self.fail_save = timeline, None, 0, None
        self.rebound = False
        self.checkpoints = SimpleNamespace(load=lambda _: {'id': 'selected', 'created': 1})
        self.redact = str
        self.project_scope_for_restore = Mock(return_value='host-issued-scope')
        self.store = SimpleNamespace(invalidate=Mock())  # The journal: badges on old rows rewrite it whole.

    def save(self):
        self.saves += 1
        self.timeline.append(('save', self.state['id'], self.saves))
        if self.saves == self.fail_save:
            raise OSError('Injected ' + self.state['id'] + ' transcript write failure')
        self.saved = copy.deepcopy(self.state)

    def rebind_after_restore(self, document, checkpoint, *, project_scope=None):
        self.timeline.append(('rebind', self.state['id']))
        self.document, self.document_invalid, self.request_checkpoint = document, False, None
        self.state.update(native_id=None, runtime_sessions={})
        self.state['task_memory'].update(stale=True)
        self.rebound = True
        return {'warnings': [], 'memory_rebound': project_scope is not None}


class CheckpointRestoreUITests(unittest.TestCase):
    def setUp(self):
        self.timeline = []
        self.active, self.other = Controller('active', self.timeline), Controller('other', self.timeline)
        self.operator = SimpleNamespace(checkpoint_id='selected', report=Mock())
        self.plan = [(Path('/private/workspace/a.txt'), Path('/private/backups/a.bin'), {'root': '/private/workspace', 'path': 'a.txt'}),
                     (Path('/private/workspace/b.txt'), Path('/private/backups/b.bin'), {'root': '/private/workspace', 'path': 'b.txt'})]
        self.record = {'id': 'selected', 'created': 1, 'original_path': '/private/original.blend', 'capture_mode': 'EVERY_REQUEST'}
        self.active.checkpoints = SimpleNamespace(restore_plan=Mock(return_value=self.plan),
            recovery_copy=Mock(return_value=(self.record, Path('/private/recovered.blend'))), journal=Mock(),
            restore_since=Mock(return_value=2), load=lambda _: self.record)
        self.ui = SimpleNamespace(get_controller=lambda: self.active, _controllers={'active': self.active, 'other': self.other},
            _restoring_snapshot=False, _recovery_original_path='', capture_checkpoint=Mock(return_value={'id': 'safety'}),
            remember_draft=Mock(), main_window=lambda: 'native-window', setup_chat_area=Mock(),
            console=SimpleNamespace(_versions={}, _states={}, is_chat=lambda _: True), chat_view=SimpleNamespace(_modals=set()))
        self.ui.bpy = SimpleNamespace(data=SimpleNamespace(filepath='/private/original.blend'), context=SimpleNamespace(
            temp_override=lambda **_: nullcontext(), window_manager=SimpleNamespace(windows=[SimpleNamespace(screen=SimpleNamespace(areas=['chat']))])),
            ops=SimpleNamespace(wm=SimpleNamespace(open_mainfile=Mock(side_effect=self.open_scene))))
        self.ui.document_key = lambda: ('new', 2) if self.ui.bpy.data.filepath == '/private/recovered.blend' else ('old', 1)

    def open_scene(self, **kwargs):
        self.timeline.append(('open',))
        self.assertIsNotNone(self.active.saved)
        self.assertEqual(self.active.saved['messages'][-1]['restore_checkpoint'], 'safety')
        self.assertEqual(self.active.saved['messages'][-1]['restore_state'], 'pending')
        self.ui.bpy.data.filepath = kwargs['filepath']
        return {'FINISHED'}

    def assert_finalized(self):
        for target in (self.active, self.other):
            self.assertEqual(target.document, self.ui.document_key())
            self.assertIsNone(target.state['native_id'])
            self.assertEqual(target.state['runtime_sessions'], {})
            self.assertIsNone(target.request_checkpoint)
            self.assertTrue(target.state['task_memory']['stale'])
        self.assertEqual(self.ui._recovery_original_path, '/private/original.blend')
        self.assertFalse(self.ui._restoring_snapshot)

    def test_ordinary_restore_persists_safety_before_open_and_rebinds_every_chat_before_saving(self):
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assert_finalized()
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'complete')
        self.assertEqual(self.active.state['messages'][-1]['restored_files'], 2)
        self.active.checkpoints.journal.assert_any_call('safety', Path('/private/workspace'), 'a.txt', restore_entry=self.plan[0][2])
        self.assertLess(self.timeline.index(('rebind', 'other')), self.timeline.index(('save', 'other', 1)))
        self.assertEqual(self.timeline[0], ('save', 'active', 1))
        for target in (self.active, self.other):  # Reverted badges on old rows: each journal is rewritten whole.
            target.store.invalidate.assert_called_once_with()

    def test_unsaved_images_are_read_before_the_safety_capture(self):
        # What is unsaved when Restore begins is what it discards, whatever the capture does afterwards.
        dirty = ['Texture']
        def capture(*_args, **_kwargs):
            self.timeline.append(('capture',))
            dirty.clear()  # Even a capture that saved or cleaned the pixels cannot hide them from the notice.
            return {'id': 'safety'}
        def unsaved(_bpy):
            self.timeline.append(('unsaved',))
            return list(dirty)
        self.ui.capture_checkpoint.side_effect = capture
        with patch.object(flow.checkpoint_images, 'unsaved_images', side_effect=unsaved):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assertLess(self.timeline.index(('unsaved',)), self.timeline.index(('capture',)))
        notice = next(m for m in self.active.state['messages'] if m.get('restore_checkpoint') == 'safety')
        self.assertEqual(notice['unsaved_images'], ['Texture'])
        self.assertTrue(any('Texture' in m.get('text', '') for m in self.active.state['messages'][-3:]))

    def test_restored_file_count_is_the_files_that_changed(self):
        from angelo_agent.checkpoint_restore import RestoredFiles
        for applied, changed in ((2, 0), (2, 1), (2, 2)):
            with self.subTest(changed=changed):
                self.setUp()
                self.active.checkpoints.restore_since.return_value = RestoredFiles(applied, changed)
                self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
                notice = self.active.state['messages'][-1]
                self.assertEqual(notice['restore_state'], 'complete')
                self.assertEqual(notice['restored_files'], changed)
                self.assertIn('reverted %d file changes' % changed, notice['text'])

    def test_preopen_safety_link_save_failure_has_no_scene_or_file_effects(self):
        self.active.fail_save = 1
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'CANCELLED'})
        self.ui.bpy.ops.wm.open_mainfile.assert_not_called()
        self.active.checkpoints.restore_since.assert_not_called()
        self.assertFalse(self.active.rebound or self.other.rebound)
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'not_applied')
        self.assertEqual(self.active.state['messages'][-1]['restore_checkpoint'], 'safety')

    def test_each_postopen_transcript_failure_preserves_actual_scene_binding_and_durable_safety(self):
        for failed in ('other', 'active', 'both'):
            with self.subTest(failed=failed):
                self.setUp()
                self.other.fail_save = 1 if failed in {'other', 'both'} else None
                self.active.fail_save = 2 if failed in {'active', 'both'} else None
                self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
                self.assert_finalized()
                self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'partial')
                self.assertEqual(self.active.saved['messages'][-1]['restore_checkpoint'], 'safety')
                if failed in {'active', 'both'}:
                    self.assertEqual(self.active.saved['messages'][-1]['restore_state'], 'pending')
                    self.assertIn('earlier saved safety link', self.active.state['messages'][-1]['text'])
                self.assertIn('WARNING', self.operator.report.call_args.args[0])

    def test_file_failure_count_is_unknown_unless_store_supplies_actual_progress(self):
        for count in (None, 0, 1):
            with self.subTest(count=count):
                self.setUp()
                error = OSError('Injected file publication failure')
                if count is not None:
                    error.restored_files = count
                self.active.checkpoints.restore_since.side_effect = error
                self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
                self.assert_finalized()
                notice = self.active.state['messages'][-1]
                self.assertEqual(notice['restore_state'], 'partial')
                if count is None:
                    self.assertNotIn('restored_files', notice)
                    self.assertIn('count is unknown', notice['text'])
                else:
                    self.assertEqual(notice['restored_files'], count)
                    self.assertIn(str(count) + ' file changes were restored before', notice['text'])

    def test_optional_project_receipt_failure_cannot_prevent_other_chat_rebinding(self):
        self.other.project_scope_for_restore.side_effect = OSError('Project storage disappeared')
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assert_finalized()
        self.assertIn('public notes remain isolated', self.active.state['messages'][-1]['text'])

    def test_native_open_handler_error_after_real_load_still_finishes_partial_recovery(self):
        def opened_then_error(**kwargs):
            self.open_scene(**kwargs)
            raise RuntimeError('Native load-post handler failed')
        self.ui.bpy.ops.wm.open_mainfile.side_effect = opened_then_error
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assert_finalized()
        self.assertIn('load reported', self.active.state['messages'][-1]['text'])

    def test_unopened_scene_failure_is_not_a_false_success(self):
        self.ui.bpy.ops.wm.open_mainfile.side_effect = OSError('Invalid recovery scene')
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'CANCELLED'})
        self.assertFalse(self.active.rebound or self.other.rebound)
        self.active.checkpoints.restore_since.assert_not_called()
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'not_applied')

    def test_view_refresh_error_is_postopen_partial_not_cancellation(self):
        self.ui.setup_chat_area.side_effect = OSError('Layout metadata could not be saved')
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assert_finalized()
        self.assertIn('layout refresh failed', self.active.state['messages'][-1]['text'])

    def bundle(self):
        self.record.update(version=4)
        owner = SimpleNamespace(record=self.record, path=Path('/private/recovered.blend'), close=Mock())
        prepared, safety_cap = object(), object()
        def prepare(resource):
            self.assertIs(resource, owner)
            self.timeline.append(('prepare-native',))
            return prepared
        def safety(*args, **kwargs):
            self.timeline.append(('safety',))
            self.assertEqual(kwargs, {'purpose': 'safety', '_retain_native': True})
            return {'id': 'safety', 'state': 'READY', 'purpose': 'safety', 'protected': True}, safety_cap
        def restore(target, safety):
            self.assertIs(target, prepared)
            self.assertIs(safety, safety_cap)
            self.open_scene(filepath=str(owner.path))
            return True
        self.ui.capture_checkpoint.side_effect = safety
        native = SimpleNamespace(prepare_restore=Mock(side_effect=prepare), restore=Mock(side_effect=restore),
                                 close_restore=Mock(), capture_release=Mock(side_effect=lambda _: self.timeline.append(('release-safety',))),
                                 failed_restore_safety=Mock(return_value=None), clear_failed_restore=Mock(),
                                 reload_failed_restore=Mock())
        def restore_files(*args, **kwargs):
            self.assertEqual(native.capture_release.call_count, 1)
            self.assertEqual(native.close_restore.call_count, 0)
            self.assertEqual(owner.close.call_count, 0)
            self.timeline.append(('restore-files',))
            return 2
        self.active.checkpoints.restore_since.side_effect = restore_files
        return owner, native

    def test_bundle_native_preparation_precedes_real_safety_handoff_and_saved_notice(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        native.quiesce_jobs = Mock(side_effect=lambda: self.timeline.append(('quiesce',)))
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assertLess(self.timeline.index(('prepare-native',)), self.timeline.index(('safety',)))
        # Preview and render jobs stop before the safety capture, so none writes into Main after it.
        self.assertLess(self.timeline.index(('quiesce',)), self.timeline.index(('safety',)))
        self.assertLess(self.timeline.index(('safety',)), self.timeline.index(('save', 'active', 1)))
        self.assertLess(self.timeline.index(('open',)), self.timeline.index(('release-safety',)))
        self.assertLess(self.timeline.index(('release-safety',)), self.timeline.index(('restore-files',)))
        self.assert_finalized()
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'complete')
        self.ui.bpy.ops.wm.open_mainfile.assert_not_called()
        self.active.checkpoints.restore_plan.assert_called_once_with('selected', recovery=owner)
        self.active.checkpoints.restore_since.assert_called_once_with('selected', self.plan, recovery=owner)
        native.close_restore.assert_called_once()
        native.capture_release.assert_called_once()
        owner.close.assert_called_once()

    def test_bundle_handler_error_is_reported_like_file_open_and_never_fails_restore(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        native.restore_handler_errors = Mock(return_value=1)
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'complete')
        reports = [call.args for call in self.operator.report.call_args_list]
        self.assertIn(({'WARNING'}, "An add-on's file handler raised an error during Restore; see the system console "
                                    "for details"), reports)
        native.restore_handler_errors.return_value = 0
        self.operator.report.reset_mock()
        owner, native = self.bundle()
        native.restore_handler_errors = Mock(return_value=0)
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.operator.report.assert_not_called()

    def test_bundle_safety_failure_has_no_native_replacement_and_releases_prepared_pair(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        self.ui.capture_checkpoint.side_effect = RuntimeError('Complete safety failed')
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'CANCELLED'})
        native.restore.assert_not_called()
        native.close_restore.assert_called_once()
        native.capture_release.assert_not_called()
        owner.close.assert_called_once()
        self.active.checkpoints.restore_since.assert_not_called()

    def test_bundle_prepare_failure_closes_pair_before_any_safety_or_scene_effect(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        native.prepare_restore.side_effect = ValueError('Invalid native companion')
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'CANCELLED'})
        self.ui.capture_checkpoint.assert_not_called()
        native.restore.assert_not_called()
        native.close_restore.assert_not_called()
        native.capture_release.assert_not_called()
        owner.close.assert_called_once()

    def test_bundle_native_failure_after_replacement_is_partial_with_safety_link(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        def partial(*args):
            self.open_scene(filepath=str(owner.path))
            raise RuntimeError('Cold runtime installation failed')
        native.restore.side_effect = partial
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'partial')
        self.assertEqual(self.active.state['messages'][-1]['restore_checkpoint'], 'safety')
        native.close_restore.assert_called_once()
        native.capture_release.assert_called_once()
        owner.close.assert_called_once()

    def failed_after_replacement(self, owner, native, reload):
        """Native replaced the document, then refused its Sculpt install and admitted a safety reload."""
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        def partial(*args):
            self.open_scene(filepath=str(owner.path))
            raise RuntimeError('Checkpoint Restore failed after replacing the document (Error: install refused)')
        native.restore.side_effect = partial
        native.failed_restore_safety.side_effect = lambda: 'safety'
        safety_owner = SimpleNamespace(record={'id': 'safety', 'created': 2, 'original_path': '/private/pre-restore.blend'},
                                       path=Path('/private/safety-working.blend'), close=Mock())
        safety_prepared = object()
        records = {'selected': self.record, 'safety': {'id': 'safety', 'created': 2, 'version': 4}}
        self.active.checkpoints.load = lambda identity: records[identity]
        def prepare_recovery(store, identity):
            self.timeline.append(('recovery', identity))
            return owner if identity == 'selected' else safety_owner
        def prepare(resource):
            return safety_prepared if resource is safety_owner else self.prepared_selected
        self.prepared_selected = object()
        native.prepare_restore.side_effect = prepare
        def reloaded(prepared):
            self.assertIs(prepared, safety_prepared)
            self.assertEqual(native.capture_release.call_count, 1, 'the safety pin closes before it is a recovery source')
            self.timeline.append(('reload',))
            return reload(prepared)
        native.reload_failed_restore.side_effect = reloaded
        native.restore.side_effect = lambda target, safety: (self.assertIs(target, self.prepared_selected), partial())[1]
        return patch.object(CheckpointRecovery, 'prepare', side_effect=prepare_recovery), safety_owner

    def test_bundle_failure_after_replacement_reloads_the_exact_safety_document(self):
        owner, native = self.bundle()
        def reload(prepared):
            self.ui.bpy.data.filepath = '/private/safety-working.blend'
            return True
        recovery, safety_owner = self.failed_after_replacement(owner, native, reload)
        with recovery, patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)}):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        notice = self.active.state['messages'][-1]
        self.assertEqual(notice['restore_state'], 'not_applied')
        self.assertTrue(notice['scene_reloaded'])
        self.assertIn('reloaded exactly from the protected safety snapshot', notice['text'])
        self.assertIn('install refused', notice['restore_reason'], 'the native reason is kept')
        self.assertNotIn('install refused', notice['text'], 'never English inside the translated sentence')
        self.active.checkpoints.restore_since.assert_not_called()
        self.assertEqual(self.ui._recovery_original_path, '/private/pre-restore.blend')
        self.assertEqual(native.close_restore.call_count, 2)
        owner.close.assert_called_once()
        safety_owner.close.assert_called_once()
        native.clear_failed_restore.assert_called_once()
        self.assertTrue(self.active.rebound and self.other.rebound)
        self.assertEqual(self.active.saved['messages'][-1]['restore_state'], 'not_applied')
        self.assertEqual(self.operator.report.call_args[0][0], {'WARNING'})

    def test_bundle_transient_reload_failure_is_retried_before_anything_else(self):
        owner, native = self.bundle()
        attempts = []
        def reload(prepared):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError('Reloading the safety checkpoint failed after replacing the document (transient)')
            self.ui.bpy.data.filepath = '/private/safety-working.blend'
            return True
        recovery, safety_owner = self.failed_after_replacement(owner, native, reload)
        with recovery, patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)}):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        notice = self.active.state['messages'][-1]
        self.assertEqual(len(attempts), 2)
        self.assertEqual((notice['restore_state'], notice['scene_reloaded']), ('not_applied', True))
        self.assertNotIn('load_handlers_suspended', notice)
        self.assertNotIn('Reloading the safety snapshot failed', notice['text'], 'the retried failure is not the outcome')
        self.ui.bpy.ops.wm.open_mainfile.assert_not_called()

    def test_bundle_every_load_handler_reloads_exactly_with_add_on_handlers_suspended(self):
        owner, native = self.bundle()
        # Angelo's own handler: its code is in the package folder. Ownership never trusts __module__.
        own = eval(compile('lambda *_: None', str(Path(flow.__file__).resolve().parent / 'ui.py'), 'eval'))
        addon = lambda *_: None
        addon.__module__ = 'mesh_tools.handlers'
        addon._bpy_persistent = True
        spoof = lambda *_: None
        spoof.__module__ = 'angelo_agent.ui'  # Claims to be Angelo's: still an add-on's code.
        lookalike = lambda *_: None
        lookalike.__module__ = 'angelo_agent_extra'
        self.ui.bpy.app = SimpleNamespace(handlers=SimpleNamespace(load_pre=[spoof], load_post=[own, addon, lookalike]))
        self.assertEqual([flow._bundled(self.ui, h) for h in (own, addon, spoof, lookalike)], [True, False, False, False])
        seen = []
        def reload(prepared):  # The add-on edits on every load: only a reload without it succeeds.
            seen.append(list(self.ui.bpy.app.handlers.load_post))
            if addon in self.ui.bpy.app.handlers.load_post:
                raise RuntimeError('Reloading the safety checkpoint failed after replacing the document')
            self.ui.bpy.data.filepath = '/private/safety-working.blend'
            return True
        recovery, safety_owner = self.failed_after_replacement(owner, native, reload)
        here = Path(__file__).resolve()
        with recovery, patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native),
                                                'mesh_tools.handlers': SimpleNamespace(__file__=str(here))}):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assertEqual(seen, [[own, addon, lookalike]] * 2 + [[own]], 'retried once, then suspended add-ons only')
        self.assertEqual(self.ui.bpy.app.handlers.load_post, [own, addon], 'persistent handlers are put back')
        self.assertEqual(self.ui.bpy.app.handlers.load_pre, [], 'a non-persistent one is not, as a file load drops it')
        notice = self.active.state['messages'][-1]
        self.assertEqual((notice['restore_state'], notice['scene_reloaded']), ('not_applied', True))
        # A module name is believed only when its file is the handler's code file; otherwise the file names it.
        self.assertEqual(notice['load_handlers_suspended'], [here.name, 'mesh_tools'])
        self.assertIn('nothing was lost', notice['text'])
        self.assertIn('one of these add-ons changes the file when it loads: ' + here.name + ', mesh_tools', notice['text'])
        self.assertIn('skipped for this document and runs again the next time a file is opened', notice['text'])
        self.assertIn('Disable the add-on that changes the file, then Restore again', notice['text'])
        self.assertIn('mesh_tools', self.operator.report.call_args[0][1])
        self.ui.bpy.ops.wm.open_mainfile.assert_not_called()
        self.active.checkpoints.restore_since.assert_not_called()

    def test_bundle_failed_safety_reload_is_partial_with_both_reasons(self):
        owner, native = self.bundle()
        def reload(prepared):
            raise RuntimeError('Reloading the safety checkpoint failed after replacing the document')
        recovery, safety_owner = self.failed_after_replacement(owner, native, reload)
        self.ui.bpy.app = SimpleNamespace(handlers=SimpleNamespace(load_pre=[], load_post=[]))
        with recovery, patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)}):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        notice = self.active.state['messages'][-1]
        self.assertEqual(notice['restore_state'], 'partial')
        self.assertIn('Reloading the safety snapshot failed', notice['text'])
        self.assertIn('install refused', notice['text'])
        self.assertIn('lacks recorded Sculpt work', notice['text'], 'never presented as the recorded state')
        self.assertNotIn('replaced by the recorded state', notice['text'])
        self.ui.bpy.ops.wm.open_mainfile.assert_not_called()
        self.assertEqual(native.reload_failed_restore.call_count, 3, 'two exact reloads, then one with handlers suspended')
        self.assertEqual(safety_owner.close.call_count, 3)
        native.clear_failed_restore.assert_called_once()

    def test_bundle_failure_before_replacement_never_reloads(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        native.restore.side_effect = RuntimeError('Checkpoint Restore was refused before replacing the document')
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'CANCELLED'})
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'not_applied')
        native.reload_failed_restore.assert_not_called()
        self.active.checkpoints.restore_since.assert_not_called()

    def test_bundle_cleanup_failure_is_partial_and_all_owned_cleanup_is_attempted(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        native.close_restore.side_effect = OSError('Native cleanup failed')
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'partial')
        self.assertIn('release failed', self.active.state['messages'][-1]['text'])
        native.close_restore.assert_called_once()
        native.capture_release.assert_called_once()
        owner.close.assert_called_once()

    def test_bundle_safety_release_failure_is_partial_and_never_retried(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        native.capture_release.side_effect = OSError('Safety cleanup failed')
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'partial')
        self.assertIn('Safety checkpoint resource release failed', self.active.state['messages'][-1]['text'])
        native.capture_release.assert_called_once()
        native.close_restore.assert_called_once()
        owner.close.assert_called_once()

    def test_bundle_preopen_notice_failure_releases_safety_without_replacement(self):
        from angelo_agent.checkpoint_recovery import CheckpointRecovery
        owner, native = self.bundle()
        self.active.fail_save = 1
        with (patch.object(CheckpointRecovery, 'prepare', return_value=owner),
              patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)})):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'CANCELLED'})
        native.restore.assert_not_called()
        native.capture_release.assert_called_once()
        native.close_restore.assert_called_once()
        owner.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
