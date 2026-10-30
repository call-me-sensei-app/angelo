# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled host checks for readable controls and truthful staging reviews."""

import ast
from contextlib import nullcontext
import copy
import os
import threading
import sys
import unittest
import uuid
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.agent_workspace_ui import (control_lines, preview_lines, review_identity,
    performance_lines, job_progress_text, approval_preview_reviewed,
    normalized_preview, preview_available, _approval_reviews)
from angelo_agent.stop_notice import stop_message
from angelo_agent import agent_workspace_ui as presentation, display_text, ui_text, program_undo
from angelo_agent.approvals import approval_details
from angelo_agent.task_memory import new_memory, update_memory

UI_NAMES = {"iface_": ui_text.iface_, "n_": ui_text.n_, "notice": ui_text.notice, "rpt_": ui_text.rpt_,
            "Text": display_text.Text, "shown": display_text.shown}  # What ui.py imports at module level.


def descriptor():
    return {'call_id': 'chat/program', 'state': 'PREVIEW', 'stage_id': 'isolated',
            'preview_token': 'exact-preview-identity', 'summary': 'One staged material change',
            'arguments': {'mode': 'PREVIEW', 'operations': [{'kind': 'set_material', 'target': '$cube',
                'parameters': {'base_color': [.100000000001, .100000000002, .3, 1],
                               'roughness': .123456789012345, 'metallic': 0,
                               'nodes': [{'id': 'last-node', 'inputs': {'Alpha': .999999999999999}}],
                               'settings': {'use_clamp': False}}}]},
            'changes': {'added': [], 'modified': [{'name': 'Cube', 'fields': ['material']}]},
            'checks': [{'name': 'Non-degenerate geometry', 'passed': True, 'measured': .0000000001}]}


class AgentWorkspacePresentationTests(unittest.TestCase):
    def test_exact_controls_are_readable_complete_and_round_trip_precise(self):
        lines = '\n'.join(control_lines(descriptor()['arguments']))
        for value in ('Mode: PREVIEW', 'Kind: set_material', 'Target: $cube',
                      '0.100000000001', '0.100000000002', '0.123456789012345',
                      'Alpha: 0.999999999999999', 'Use clamp: No', 'Metallic: 0', 'last-node'):
            self.assertIn(value, lines)
        self.assertLess(lines.index('0.100000000001'), lines.index('0.100000000002'))
        self.assertNotIn('{', lines)
        self.assertNotIn('…', lines)

    def test_all_32_operations_and_last_material_control_remain_visible(self):
        args = descriptor()['arguments']
        args['operations'] = [copy.deepcopy(args['operations'][0]) for _ in range(32)]
        args['operations'][-1]['parameters']['roughness'] = .987654321987654
        lines = '\n'.join(control_lines(args))
        self.assertEqual(lines.count('Kind: set_material'), 32)
        self.assertIn('Item 32:', lines)
        self.assertIn('Roughness: 0.987654321987654', lines)

    def test_preview_never_counts_stage_as_committed_or_hides_missing_checks(self):
        lines = '\n'.join(preview_lines(descriptor(), exact=True))
        self.assertIn('Preview · built on a copy of your scene', lines)
        self.assertIn('not applied to the document', lines)
        self.assertIn('Non-degenerate geometry: Passed', lines)
        self.assertIn('Exact submitted controls', lines)
        self.assertIn('Stage identity: isolated', lines)
        self.assertIn('No verified checks', '\n'.join(preview_lines({'state': 'PREVIEW'})))

    def test_review_identity_changes_for_every_material_control_check_or_stage(self):
        original = descriptor()
        token = review_identity(original)
        for key in ('stage_id', 'preview_token', 'summary', 'checks', 'changes'):
            changed = copy.deepcopy(original)
            changed[key] = 'changed'
            self.assertNotEqual(token, review_identity(changed), key)
        changed = copy.deepcopy(original)
        changed['arguments']['operations'][0]['parameters']['roughness'] += .000001
        self.assertNotEqual(token, review_identity(changed))
        changed = copy.deepcopy(original)
        changed['state'] = 'COMPLETED'
        self.assertEqual(token, review_identity(changed), 'Fingerprint is content identity, not an authorization grant')

    def test_stop_boundary_message_and_metrics_are_honest(self):
        self.assertIn('between steps', stop_message([{'state': 'RUNNING'}]))
        self.assertIn('not applied to your scene', stop_message([{'state': 'PREVIEW'}]))
        self.assertIn('cannot be interrupted', stop_message([], script_running=True))
        self.assertEqual(stop_message([]), 'Stopped', 'Nothing running: no claim that a script was')
        self.assertEqual(job_progress_text({'progress': {'completed': 2, 'total': 8, 'label': 'Validate'}}), 'Validate · 2 / 8')
        self.assertIn('No native performance samples', performance_lines({})[0])
        self.assertIn('Measured native execution', performance_lines({'requests': 2})[0])


class ReviewRows(list):
    def add(self):
        item = SimpleNamespace(body='')
        self.append(item)
        return item


def workspace_class(name, namespace, path=None):
    path = Path(path or presentation.__file__)
    module = ast.parse(path.read_text())
    definition = next(node for node in ast.walk(module) if isinstance(node, ast.ClassDef) and node.name == name)
    namespace = {'Operator': object, 'PropertyGroup': object, 'UIList': object,
                 'ANGELO_PG_native_review_line': object, 'review_line_type': object,
                 **{prop: (lambda **_: None) for prop in ('StringProperty', 'BoolProperty', 'CollectionProperty',
                                                          'IntProperty', 'FloatProperty', 'EnumProperty')},
                 'normalized_preview': normalized_preview, 'preview_available': preview_available,
                 'preview_descriptor': presentation.preview_descriptor,
                 'preview_lines': preview_lines, 'review_identity': review_identity,
                 'program_undo': program_undo, 'undo_bridge': None,
                 '_approval_reviews': _approval_reviews, '__package__': 'angelo_agent'} | namespace
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class ExactHumanReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.doc = ('document-instance', 42)
        self.preview = descriptor() | {'preview_id': 'retained-preview'}
        self.controller = SimpleNamespace(state={'id': 'chat'}, document=self.doc, pending={},
            inspect_native_preview=Mock(side_effect=lambda _: copy.deepcopy(self.preview)),
            accept_native_preview=Mock(return_value=True), commit_native_job=Mock(), expire_tool=Mock(return_value=False),
            stop=Mock())
        self.ui = SimpleNamespace(document_key=Mock(return_value=self.doc), main_window=Mock(return_value='window'))
        self.bpy = SimpleNamespace(ops=SimpleNamespace(angelo=SimpleNamespace(approve=Mock(return_value={'FINISHED'}))))
        self.context = SimpleNamespace(mode='OBJECT', preferences=SimpleNamespace(edit=SimpleNamespace(use_global_undo=True, undo_steps=32)),
            window=Mock(), window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'}),
                                                          event_timer_add=Mock(), event_timer_remove=Mock(),
                                                          modal_handler_add=Mock()))
        # The review shows as an Angelo card (review_card over card_host); the host is GPU-side, stood in here.
        self.cards = []
        host = SimpleNamespace(request=self.cards.append, pump=Mock(return_value=True), _queue=[],
                               active_card=lambda: self.cards[-1] if self.cards else None, close=Mock())
        patcher = patch.dict(sys.modules, {'angelo_agent.card_host': host})
        patcher.start()
        self.addCleanup(patcher.stop)
        import angelo_agent
        previous = getattr(angelo_agent, 'card_host', None)
        angelo_agent.card_host = host
        self.addCleanup(lambda: setattr(angelo_agent, 'card_host', previous) if previous else delattr(angelo_agent, 'card_host'))
        def add_lines(operator, lines):
            operator.review_lines[:] = [SimpleNamespace(body=line) for line in lines]
        self.executor = SimpleNamespace(studio_context=None)
        namespace = {'controller_for': lambda _: self.controller, 'ui': self.ui, 'bpy': self.bpy,
                     'BlenderExecutor': lambda _: self.executor, 'add_lines': add_lines}
        self.accept_type = workspace_class('ANGELO_OT_accept_native_preview', namespace)
        self.commit_type = workspace_class('ANGELO_OT_commit_native_job', namespace)
        self.accept = self.accept_type()
        self.accept.preview_id, self.accept.session_id, self.accept.call_id = 'retained-preview', 'chat', ''
        self.accept.review_lines, self.accept.review_confirmed, self.accept.report = ReviewRows(), False, Mock()
        _approval_reviews.clear()

    def test_direct_execute_cannot_accept_even_with_confirm_flag(self):
        self.accept.review_confirmed = True
        self.assertEqual(self.accept.execute(self.context), {'CANCELLED'})
        self.controller.accept_native_preview.assert_not_called()
        self.controller.commit_native_job.assert_not_called()

    def test_review_contains_every_control_and_queues_only_after_explicit_confirmation(self):
        self.assertEqual(self.accept.invoke(self.context, None), {'RUNNING_MODAL'})
        self.assertFalse(self.accept.review_confirmed)
        text = '\n'.join(line.body for line in self.accept.review_lines)
        self.assertIn('Roughness: 0.123456789012345', text)
        self.assertIn('Non-degenerate geometry: Passed', text)
        self.assertEqual(self.accept.execute(self.context), {'CANCELLED'})
        self.controller.accept_native_preview.assert_not_called()
        self.accept.review_confirmed = True
        self.assertEqual(self.accept.execute(self.context), {'FINISHED'})
        self.controller.accept_native_preview.assert_called_once_with('retained-preview', self.executor, self.doc,
                                                                     'exact-preview-identity')
        self.controller.commit_native_job.assert_not_called()
        self.assertIs(self.executor.studio_context, self.controller)

    def test_the_review_card_decides_through_the_unchanged_execute(self):
        from angelo_agent import review_card
        self.assertEqual(self.accept.invoke(self.context, None), {'RUNNING_MODAL'})
        card = self.cards[-1]
        self.assertIn('Roughness: 0.123456789012345', '\n'.join(card.lines))
        key = SimpleNamespace(type='MOUSEMOVE', value='NOTHING')
        self.assertEqual(review_card.wait(self.accept, self.context, key), {'PASS_THROUGH'})
        card.act('accept', None, SimpleNamespace(close=Mock()))  # Unticked: execute refuses, as the dialog did.
        self.assertEqual(review_card.wait(self.accept, self.context, key), {'CANCELLED'})
        self.controller.accept_native_preview.assert_not_called()
        self.accept.invoke(self.context, None)
        card = self.cards[-1]
        card.act('confirm', None, None)
        self.assertTrue(self.accept.review_confirmed)
        card.act('accept', None, SimpleNamespace(close=Mock()))
        self.assertEqual(review_card.wait(self.accept, self.context, key), {'FINISHED'})
        self.controller.accept_native_preview.assert_called_once()
        self.accept.invoke(self.context, None)
        escape = SimpleNamespace(type='ESC', value='PRESS')
        self.assertEqual(review_card.wait(self.accept, self.context, escape), {'CANCELLED'})
        self.controller.accept_native_preview.assert_called_once()

    def test_changed_preview_controls_checks_document_or_missing_stage_reject_acceptance(self):
        for field in ('arguments', 'checks', 'stage_id', 'preview_token'):
            with self.subTest(field=field):
                self.preview = descriptor() | {'preview_id': 'retained-preview'}
                self.accept.invoke(self.context, None)
                self.accept.review_confirmed = True
                self.preview[field] = '' if field == 'stage_id' else 'changed'
                self.assertEqual(self.accept.execute(self.context), {'CANCELLED'})
        self.preview = descriptor() | {'preview_id': 'retained-preview'}
        self.accept.invoke(self.context, None)
        self.accept.review_confirmed = True
        self.ui.document_key.return_value = ('new-document', 99)
        self.assertEqual(self.accept.execute(self.context), {'CANCELLED'})
        self.controller.accept_native_preview.assert_not_called()

    def test_memory_approved_flags_do_not_authorize_acceptance(self):
        self.controller.state['task_memory'] = {'approved': True, 'authority': 'all', 'provenance': {'accepted': True}}
        self.accept.review_confirmed = True
        self.assertEqual(self.accept.execute(self.context), {'CANCELLED'})
        self.controller.accept_native_preview.assert_not_called()

    def test_model_accept_pending_call_requires_same_exact_human_review(self):
        call = SimpleNamespace(id='chat/model-accept', name='angelo_accept_preview', arguments={'preview_id': 'retained-preview'})
        self.controller.pending[call.id] = call
        self.accept.call_id = call.id
        self.assertFalse(approval_preview_reviewed(self.controller, call))
        self.accept.invoke(self.context, None)
        self.accept.review_confirmed = True
        self.assertEqual(self.accept.execute(self.context), {'FINISHED'})
        self.assertTrue(approval_preview_reviewed(self.controller, call))
        self.controller.accept_native_preview.assert_not_called()
        self.bpy.ops.angelo.approve.assert_called_once_with('EXEC_DEFAULT', True, call_id=call.id, session_id='chat')
        self.preview['checks'].append({'name': 'New check', 'passed': False})
        self.assertFalse(approval_preview_reviewed(self.controller, call))

    def test_final_commit_operator_is_undo_enabled_and_preserves_noop_vs_partial_outcomes(self):
        self.assertIn('UNDO', self.commit_type.bl_options)
        call = SimpleNamespace(id='chat/commit')
        self.controller.pending[call.id] = call
        operator = self.commit_type()
        operator.session_id, operator.call_id, operator.report = 'chat', call.id, Mock()
        self.controller.commit_native_job.return_value = {'error': 'Stale revision', 'scene_changed': False}
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.controller.commit_native_job.return_value = {'scene_changed': True, 'success': True}
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.controller.commit_native_job.return_value = {'error': 'Partial native error', 'partial_changes_possible': True}
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.context.preferences.edit.use_global_undo = False
        self.controller.commit_native_job.reset_mock()
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.controller.stop.assert_called_once()
        self.controller.commit_native_job.assert_not_called()

    def test_program_approval_shows_normalized_material_controls_without_json(self):
        from angelo_agent.action_catalog import validate_operation
        operation = validate_operation({'kind': 'material_principled', 'target': '$cube',
            'parameters': {'name': 'Blue', 'inputs': {'Base Color': [.1, .2, .3, 1], 'Roughness': .123456789012345}}})
        arguments = {'mode': 'PREVIEW', 'read_set': [], 'write_set': [], 'operations': [operation], 'expected_checks': []}
        text = approval_details('angelo_scene_program', arguments)
        for value in ('Base Color: 0.1, 0.2, 0.3, 1', 'Roughness: 0.123456789012345', 'Mode: PREVIEW'):
            self.assertIn(value, text)
        # Exactly what the program writes: an input it leaves out is not shown, since none is written
        # (action_schemas.SETTINGS_NOTE).
        for value in ('Metallic', 'IOR', 'Alpha', 'Emission'):
            self.assertNotIn(value, text)
        self.assertNotIn('{', text)
        self.assertNotIn('…', text)

    def test_initial_approval_and_staging_have_no_undo_flag(self):
        path = Path(presentation.__file__).with_name('ui.py')
        module = ast.parse(path.read_text())
        approve = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'ANGELO_OT_approve')
        self.assertFalse(any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'bl_options'
                                                                for target in node.targets) for node in approve.body))
        self.assertIn('commit_mutation=commit_mutation', path.read_text())

    def test_snapshot_fails_closed_before_reserving_when_live_stage_exists(self):
        from angelo_agent import native_programs
        path = Path(presentation.__file__).with_name('ui.py')
        definition = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'capture_checkpoint')
        namespace = {'__package__': 'angelo_agent', **UI_NAMES}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), namespace)
        controller = SimpleNamespace(checkpoints=SimpleNamespace(reserve=Mock()))
        with patch.object(native_programs, 'runtime_is_staging', return_value=True, create=True):
            with self.assertRaisesRegex(RuntimeError, 'Finish the current native program or press Stop'):
                namespace['capture_checkpoint'](controller)
        controller.checkpoints.reserve.assert_not_called()


    def test_private_snapshot_restores_thumbnail_setting_and_dirty_flag_even_on_failure(self):
        from angelo_agent import native_programs, checkpoint_capture, checkpoint_images, checkpoint_scene_map
        from angelo_agent.checkpoints import CheckpointStore
        path = Path(presentation.__file__).with_name('ui.py')
        definition = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'capture_checkpoint')
        # packs: a build whose capture still packs external files (held off around it); autopack: the document's setting.
        for dirty, failed, packs, autopack in ((False, False, True, True), (False, True, True, True), (True, False, False, True),
                                               (True, True, True, False), (False, False, False, False)):
            with self.subTest(dirty=dirty, failed=failed, packs=packs, autopack=autopack), \
                    tempfile.TemporaryDirectory() as directory:
                prefs = SimpleNamespace(is_dirty=dirty, bl_rna=SimpleNamespace(properties={'is_dirty': SimpleNamespace(is_readonly=False)}))
                class Filepaths:
                    def __init__(self):
                        self.value, self.images = 'CAMERA', 'ALWAYS_SAVE'
                    @property
                    def file_preview_type(self):
                        return self.value
                    @file_preview_type.setter
                    def file_preview_type(self, value):
                        self.value, prefs.is_dirty = value, True
                    @property
                    def save_modified_images(self):
                        return self.images
                    @save_modified_images.setter
                    def save_modified_images(self, value):
                        self.images, prefs.is_dirty = value, True
                prefs.filepaths = Filepaths()
                def save(store, original, **arguments):
                    self.assertEqual(prefs.filepaths.file_preview_type, 'NONE')
                    # A private capture never writes the person's image files (checkpoint_policy.rst).
                    self.assertEqual(prefs.filepaths.save_modified_images, 'NEVER_SAVE')
                    # Nor packs external files into the live document: held off only where the build would pack.
                    self.assertEqual(bpy.data.use_autopack, autopack and not packs)
                    if failed:
                        raise RuntimeError('Injected private native save failure')
                    # UI policy/preference test only. The native capsule and
                    # real publication lifecycle have separate acceptance.
                    record, target = store.reserve(original, capture_mode=arguments['capture_mode'],
                                                   purpose=arguments['purpose'])
                    target.write_bytes(b'disposable native snapshot fixture')
                    record = store.complete(record['id'])
                    return (record, object()) if arguments['retain_native'] else record
                bpy = SimpleNamespace(context=SimpleNamespace(preferences=prefs, temp_override=lambda **_: nullcontext()),
                    data=SimpleNamespace(filepath='factory-document.blend', use_autopack=autopack,
                                         scenes=[SimpleNamespace(session_uid=7, name_full='Scene')]), ops=SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=save)),
                    app=SimpleNamespace(handlers=SimpleNamespace(save_pre=[], save_post=[])))
                namespace = {'__package__': 'angelo_agent', **UI_NAMES, 'bpy': bpy, 'main_window': lambda: object(),
                    'state_directory': lambda: directory, 'os': os,
                    '_document_serial': 'load-serial'}
                exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), namespace)
                controller = SimpleNamespace(checkpoints=CheckpointStore(directory, str(uuid.uuid4())),
                    _turn_checkpoint_policy={'capture_mode': 'BEFORE_FIRST_EDIT'})
                with (patch.object(native_programs.WRITER, 'owner', None),
                      patch.object(native_programs, 'runtime_is_staging', return_value=False, create=True),
                      patch.object(checkpoint_capture, 'capture_checkpoint', side_effect=save),
                      patch.object(checkpoint_images, 'capture_packs', return_value=packs)):
                    if failed:
                        with self.assertRaisesRegex(RuntimeError, 'Injected private native save failure'):
                            namespace['capture_checkpoint'](controller)
                    else:
                        record = namespace['capture_checkpoint'](controller)
                        self.assertEqual(record['state'], 'READY')
                        # What each scene is called now, by its session uid in this load (checkpoint_scene_map).
                        self.assertEqual(checkpoint_scene_map.read(controller.checkpoints, record['id']),
                                         {'load': 'load-serial', 'names': [[7, 'Scene']]})
                        self.assertEqual(record['capture_mode'], 'BEFORE_FIRST_EDIT')
                        safety = namespace['capture_checkpoint'](controller, purpose='safety')
                        self.assertEqual(safety['capture_mode'], 'EVERY_REQUEST')
                        self.assertTrue(safety['protected'])
                self.assertEqual(prefs.filepaths.file_preview_type, 'CAMERA')
                self.assertEqual(prefs.filepaths.save_modified_images, 'ALWAYS_SAVE')
                self.assertIs(bpy.data.use_autopack, autopack)
                self.assertEqual(prefs.is_dirty, dirty)


class FriendlyTargetLabelTests(unittest.TestCase):
    def test_host_labels_are_cached_once_without_mutating_authoritative_arguments(self):
        import angelo_agent
        from angelo_agent import agent_target_labels
        handle = 'scn:' + 'a' * 32 + ':12'
        arguments = {'read_set': [{'handle': handle, 'revision': 'b' * 64}],
            'write_set': [{'handle': handle, 'revision': 'b' * 64}],
            'operations': [{'kind': 'create_primitive', 'target': None, 'parameters': {'name': 'Created prop', 'alias': 'new'}}]}
        before = copy.deepcopy(arguments)
        call = SimpleNamespace(id='chat/call', name='angelo_scene_program', arguments=arguments)
        controller = SimpleNamespace(state={'id': 'chat'}, pending={call.id: call}, document=('doc',), document_invalid=False)
        window = SimpleNamespace(scene=SimpleNamespace(objects=[SimpleNamespace(session_uid=12, name='Existing hero'),
                                                                SimpleNamespace(session_uid=13, name='Other object')]))
        scene_queries = SimpleNamespace(object_handle=Mock(side_effect=lambda obj: 'scn:' + 'a' * 32 + ':' + str(obj.session_uid)))
        agent_target_labels.clear()
        with patch.dict(sys.modules, {'angelo_agent.scene_queries': scene_queries}), patch.object(angelo_agent, 'scene_queries', scene_queries, create=True):
            agent_target_labels.prepare(controller, window, ('doc',))
            self.assertEqual(call.target_labels, {handle: 'Existing hero', '$new': 'Created prop'})
            scene_queries.object_handle.assert_called_once()
            scene_queries.object_handle.reset_mock()
            agent_target_labels.prepare(controller, window, ('doc',))
            scene_queries.object_handle.assert_not_called()
        self.assertEqual(arguments, before)
        text = '\n'.join(control_lines({'target': handle, 'revision': 'b' * 64}, target_labels=call.target_labels))
        self.assertIn(handle + ' · “Existing hero” (display name only)', text)
        self.assertIn('Revision: ' + 'b' * 64, text)
        agent_target_labels.clear()


class ProjectBriefEditorTests(unittest.TestCase):
    def setUp(self):
        self.doc = ('document-instance', 42)
        self.controller = SimpleNamespace(state={'id': 'chat', 'options': {'approval': 'ASK'},
            'task_memory': new_memory(document_identity=self.doc)}, document=self.doc, version=0,
            lock=threading.RLock(), save=Mock())
        self.ui = SimpleNamespace(document_key=lambda: self.doc)
        self.delegate = Mock(return_value={'RUNNING_MODAL'})
        self.context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'})))
        namespace = {'controller_for': lambda _: self.controller, 'ui': self.ui, 'uuid': uuid,
                     'bpy': SimpleNamespace(ops=SimpleNamespace(angelo=SimpleNamespace(use_project_brief=self.delegate))),
                     'memory_snapshot': presentation.memory_snapshot,
                     'control_lines': control_lines,
                     'add_lines': lambda operator, values: operator.review_lines.extend(SimpleNamespace(body=value) for value in values)}
        self.operator = workspace_class('ANGELO_OT_project_brief', namespace)()
        self.operator.session_id, self.operator.report, self.operator.review_lines = 'chat', Mock(), []

    def test_mismatched_brief_dialog_owns_modal_lifecycle_not_wrapper(self):
        self.doc = ('new-native-instance', 43)
        self.controller.document = self.doc
        self.assertEqual(self.operator.invoke(self.context, None), {'FINISHED'})
        self.delegate.assert_called_once_with('INVOKE_DEFAULT', session_id='chat')
        self.context.window_manager.invoke_props_dialog.assert_not_called()
        self.controller.save.assert_not_called()

    def test_mismatched_brief_delegate_cancellation_is_preserved(self):
        self.doc = ('new-native-instance', 43)
        self.controller.document = self.doc
        self.delegate.return_value = {'CANCELLED'}
        self.assertEqual(self.operator.invoke(self.context, None), {'CANCELLED'})
        self.controller.save.assert_not_called()

    def test_brief_save_is_real_structured_data_not_a_permission_grant(self):
        self.assertEqual(self.operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.operator.objective, self.operator.style = 'Blue character', 'Soft stylized'
        self.operator.constraints = 'Keep base topology\nDo not overwrite manual edits'
        self.operator.planned_steps = 'Observe\nPreview\nReview'
        self.operator.unit_system, self.operator.scale_length = 'METRIC', .01
        self.assertEqual(self.operator.execute(self.context), {'FINISHED'})
        memory = self.controller.state['task_memory']
        self.assertEqual(memory['brief']['objective'], 'Blue character')
        self.assertEqual(memory['brief']['constraints'], ['Keep base topology', 'Do not overwrite manual edits'])
        self.assertEqual(memory['planned_steps'], ['Observe', 'Preview', 'Review'])
        self.assertEqual(memory['brief']['units'], {'system': 'METRIC', 'scale_length': .01})
        self.assertEqual(self.controller.state['options']['approval'], 'ASK')
        self.assertNotIn('approved', memory)
        self.controller.save.assert_called_once()

    def test_concurrent_brief_revision_cannot_be_blindly_overwritten(self):
        self.operator.invoke(self.context, None)
        self.controller.state = update_memory(self.controller.state,
            {'expected_revision': 0, 'patch': {'objective': 'Concurrent human update'}}, self.doc,
            provenance={'kind': 'approved_update', 'id': 'human-concurrent'})
        self.operator.objective = 'Stale editor'
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(self.controller.state['task_memory']['brief']['objective'], 'Concurrent human update')
        self.controller.save.assert_not_called()


class SnapshotSettingsUiTests(unittest.TestCase):
    def setUp(self):
        from angelo_agent.checkpoint_policy import CheckpointPolicyStore
        from angelo_agent.checkpoints import CheckpointStore
        from angelo_agent.checkpoint_settings_ui import protected_snapshot_refs
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.policy = CheckpointPolicyStore(self.root)
        self.controller = SimpleNamespace(state={'id': str(uuid.uuid4())}, busy=False, request_checkpoint=None,
            checkpoints=CheckpointStore(self.root, str(uuid.uuid4())))
        self.controller.state['id'] = self.controller.checkpoints.session_id
        self.ui = SimpleNamespace(state_directory=lambda: str(self.root), get_controller=lambda: self.controller,
            _controllers={'one': self.controller})
        self.context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'})))
        path = Path(presentation.__file__).with_name('checkpoint_settings_ui.py')
        namespace = {'ui': self.ui, 'CheckpointPolicyStore': CheckpointPolicyStore,
            'protected_snapshot_refs': protected_snapshot_refs,
            'lines': lambda operator, values: operator.review_lines.extend(SimpleNamespace(body=value) for value in values)}
        self.settings_type = workspace_class('ANGELO_OT_checkpoint_settings', namespace, path)
        self.retention_type = workspace_class('ANGELO_OT_checkpoint_retention', namespace, path)

    def tearDown(self):
        self.directory.cleanup()

    def test_every_request_is_preserved_until_explicit_opt_in(self):
        operator = self.settings_type()
        operator.report = Mock()
        self.assertEqual(operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.assertEqual(operator.capture_mode, 'EVERY_REQUEST')
        self.assertFalse(operator.user_confirmed)
        operator.capture_mode = 'BEFORE_FIRST_EDIT'
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(self.policy.load()['capture_mode'], 'EVERY_REQUEST')
        operator.user_confirmed = True
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.assertEqual(self.policy.load()['capture_mode'], 'BEFORE_FIRST_EDIT')

    def test_retention_lists_exact_measured_removals_and_protects_latest_recovery(self):
        records = []
        for index in range(3):
            record, path = self.controller.checkpoints.reserve()
            path.write_bytes(b'factory-test-scene-' + bytes([index]))
            records.append(self.controller.checkpoints.complete(record['id']))
        self.controller.request_checkpoint = records[-1]['id']
        self.policy.save({'max_count': 1}, user_confirmed=True)
        operator = self.retention_type()
        operator.report, operator.review_lines = Mock(), []
        self.assertEqual(operator.invoke(self.context, None), {'RUNNING_MODAL'})
        text = '\n'.join(line.body for line in operator.review_lines)
        for record in records[:-1]:
            self.assertIn(self.controller.state['id'] + '/' + record['id'], text)
        self.assertNotIn(records[-1]['id'], [item['id'] for item in operator._plan['candidates']])
        self.assertIn('Protected: ' + self.controller.state['id'] + '/' + records[-1]['id'], text)
        self.assertIn(str(operator._plan['candidate_bytes']) + ' measured bytes', text)
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertTrue(all(self.controller.checkpoints.availability(record['id'])['available'] for record in records))
        operator.user_confirmed = True
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.assertTrue(self.controller.checkpoints.availability(records[-1]['id'])['available'])
        self.assertTrue(all(self.controller.checkpoints.availability(record['id'])['state'] == 'PRUNED' for record in records[:-1]))


class CheckpointBoundaryLabelTests(unittest.TestCase):
    def test_every_request_explicit_receipt_and_deferred_staging_have_distinct_promises(self):
        from angelo_agent.checkpoint_review_ui import checkpoint_review, restore_notice
        identity = 'exact-snapshot'
        every = checkpoint_review({'id': identity, 'capture_mode': 'EVERY_REQUEST'},
            [{'role': 'user', 'checkpoint': identity, 'checkpoint_scope': 'before_request'}])
        deferred = checkpoint_review({'id': identity, 'capture_mode': 'BEFORE_FIRST_EDIT'},
            [{'role': 'user', 'checkpoint': identity, 'checkpoint_scope': 'before_native_staging'}])
        self.assertEqual(every['scope'], 'before_request')
        self.assertIn('before this request', every['question'])
        self.assertIn('before that request', restore_notice(every, 0))
        self.assertEqual(deferred['scope'], 'before_native_staging')
        self.assertIn('before Angelo started working on', deferred['question'])
        self.assertIn('includes edits you made', deferred['note'])
        self.assertNotIn('before that request', restore_notice(deferred, 0))
        self.assertNotIn('before this request', deferred['question'])

    def test_first_edit_human_acceptance_and_safety_scopes_are_not_model_message_boundaries(self):
        from angelo_agent.checkpoint_review_ui import checkpoint_review
        record = {'id': 'point', 'capture_mode': 'BEFORE_FIRST_EDIT'}
        first = checkpoint_review(record, [{'role': 'user', 'checkpoint': 'point', 'checkpoint_scope': 'before_first_approved_edit'}])
        self.assertEqual(first['scope'], 'before_first_approved_edit')
        self.assertIn('after sending the request', first['note'])
        human = checkpoint_review({'id': 'point', 'capture_mode': 'EVERY_REQUEST'},
            [{'role': 'user', 'request_kind': 'native_preview_accept', 'checkpoint': 'point', 'checkpoint_scope': 'before_request'}])
        self.assertEqual(human['scope'], 'before_preview_acceptance')
        self.assertIn('not when you sent the original message', human['note'])
        staged_human = checkpoint_review(record,
            [{'role': 'user', 'request_kind': 'native_preview_accept', 'checkpoint': 'point', 'checkpoint_scope': 'before_native_staging'}])
        self.assertEqual(staged_human['scope'], 'before_preview_acceptance_staging')
        safety = checkpoint_review({'id': 'point', 'purpose': 'safety'}, [{'role': 'system', 'restore_checkpoint': 'point'}])
        self.assertEqual(safety['scope'], 'before_restore')

    def test_unknown_mismatched_and_contradictory_scope_never_guesses_before_message(self):
        from angelo_agent.checkpoint_review_ui import checkpoint_review
        for record, messages in [
            ({'id': 'point', 'capture_mode': 'EVERY_REQUEST'}, []),
            ({'id': 'point', 'capture_mode': 'EVERY_REQUEST'}, [{'checkpoint': 'other', 'checkpoint_scope': 'before_request'}]),
            ({'id': 'point', 'capture_mode': 'BEFORE_FIRST_EDIT'}, [{'role': 'user', 'checkpoint': 'point', 'checkpoint_scope': 'before_request'}]),
            ({'id': 'point', 'capture_mode': 'EVERY_REQUEST'}, [{'role': 'system', 'checkpoint': 'point', 'checkpoint_scope': 'before_request'}]),
        ]:
            with self.subTest(record=record, messages=messages):
                review = checkpoint_review(record, messages)
                self.assertEqual(review['scope'], 'recorded')
                self.assertIn('not recorded', review['note'])
                self.assertNotIn('before this request', review['question'])


if __name__ == '__main__':
    unittest.main()
