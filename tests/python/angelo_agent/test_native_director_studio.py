# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No Blender/provider access: Studio tools, billing gates and async receipts."""

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent.studio_agent import StudioAgent, StudioPending, generation_options
from angelo_agent.studio_spec import STUDIO_NAMES, STUDIO_USAGE
from angelo_agent.studio_state import StudioLibrary
from angelo_agent.studio_generation import StudioJobs
from angelo_agent.generative import GenerationBinding
from angelo_agent.tool_specs import validate_call, BOUNDED_MUTATIONS, COLLABORATOR_TOOLS, dynamic_tools, native_plan_schema
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.approvals import approval_message, approval_details
from angelo_agent.providers import INSTRUCTIONS, tool_content

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+axtoAAAAASUVORK5CYII=')
ACCOUNT = str(uuid.uuid4())
MODEL = 'gemini-3.1-flash-image'


class MediaToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.controller = Controller(str(self.root / 'state'))
        self.controller.set_document(('fixture', 1))
        self.controller.busy = True
        self.library = StudioLibrary(self.root / 'library')
        self.requests = []
        def transport(url, **kwargs):
            self.requests.append((url, kwargs))
            return json.dumps({'steps': [{'type': 'model_output', 'content': [
                {'type': 'image', 'data': base64.b64encode(PNG).decode()}]}]}).encode()
        self.jobs = StudioJobs(self.library, transport)
        self.binding = GenerationBinding(ACCOUNT, 'gemini_images_api', (MODEL,), True, 'private-fixture-key')
        self.choices = [{'id': MODEL, 'name': 'Nano Banana 2', 'mode': 'gemini_images_api',
                         'connection': ACCOUNT, 'account': 'Named fixture account', 'ready': True, 'verified': True}]
        self.enhancer = Mock(return_value={'label': 'Named enhancer · API billing', 'signature': ('bound',),
            'run': lambda cancel: 'Improved prompt', 'connection': ACCOUNT})
        self.import_model = Mock(return_value={'success': True, 'objects': ['Fixture model']})
        self.service = StudioAgent(self.library, self.jobs, lambda: self.choices,
            lambda identity: self.binding, self.controller, self.import_model, self.enhancer)
        self.controller.media_service = self.service
        self.executor = Mock()
        self.executor.precondition.side_effect = self.service.precondition
        self.executor.usage_label.side_effect = self.service.usage_label
        self.executor.execute.side_effect = self.service.execute

    def tearDown(self):
        self.controller.cancel_tools('Fixture complete')
        self.controller.close()
        self.jobs.close()
        self.temp.cleanup()

    def args(self, **updates):
        return dict({'connection_id': ACCOUNT, 'model': MODEL, 'prompt': 'A blue sword'}, **updates)

    def attach(self):
        path = self.root / 'reference.png'
        path.write_bytes(PNG)
        reference = self.controller.references.import_images([str(path)])[0]
        self.controller.turn_attachments = [reference]
        return reference

    def wait(self, job):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = job.poll()
            if result is not None:
                return result
            time.sleep(.01)
        self.fail('Fixture worker did not finish')

    def test_undo_invalidated_preview_handles_are_discarded_safely(self):
        from angelo_agent.studio_previews import preview_alive, release_preview
        class RemovedImage:
            @property
            def name(self):
                raise ReferenceError('StructRNA has been removed')
        images = Mock()
        entry = (RemovedImage(), object())
        self.assertFalse(preview_alive(entry, images))
        release_preview(entry, images)
        images.remove.assert_not_called()
        image = SimpleNamespace(name='Fixture')
        images.get.return_value = image
        release_preview((image, object()), images)
        images.remove.assert_called_once_with(image)

    def test_enhancement_override_requires_exact_account_and_model_together(self):
        args = {'prompt': 'A sword', 'kind': 'IMAGE', 'generation_model': MODEL}
        for update in ({'connection_id': ACCOUNT}, {'model': 'gemini-3.8-flash'}):
            with self.assertRaises(ValueError):
                validate_call('angelo_studio_enhance', dict(args, **update))
        checked = validate_call('angelo_studio_enhance', dict(args, connection_id=ACCOUNT, model='gemini-3.8-flash'))
        self.assertEqual(checked['connection_id'], ACCOUNT)

    def test_toolset_registered_in_both_native_adapters_and_no_auto_billing(self):
        self.assertTrue(STUDIO_NAMES <= {t['name'] for t in dynamic_tools()})
        # Claude plans name the Studio tools and read their schemas on demand.
        from angelo_agent.tagged_unions import members
        self.assertTrue(STUDIO_NAMES <= set(members(native_plan_schema()['properties']['operations']['items'])[2]))
        self.assertFalse(STUDIO_NAMES & BOUNDED_MUTATIONS)
        self.assertFalse(STUDIO_NAMES & COLLABORATOR_TOOLS)
        # Facts-only text: what the catalog lists, in place of "first call".
        self.assertIn('angelo_studio_catalog lists the exact available connection_id/model pairs', INSTRUCTIONS)

    def test_catalog_never_uses_provider_or_exposes_key(self):
        result = self.service.execute('angelo_studio_catalog', {})
        self.assertEqual(result['kinds'], ['IMAGE', '3D'])
        self.assertEqual(result['models'][0]['connection'], ACCOUNT)
        self.assertIn('aspect', result['models'][0]['controls'])
        self.assertNotIn('private-fixture-key', json.dumps(result))
        self.assertEqual(self.requests, [])

    def test_schema_rejects_unbounded_unknown_or_badly_typed_arguments(self):
        for updates in ({'options': {'texture': 1}}, {'options': {'polycount': True}}, {'options': {'seed': 123}},
                        {'references': [{'source': 'FILE', 'id': '/tmp/secret'}]}, {'references': [{'source': 'ASSET', 'id': '/tmp/image'}]},
                        {'connection_id': 'work'}, {'prompt': 'x' * 16001}, {'options': {'quality': 'ultra'}},
                        {'references': [{'source': 'URL', 'id': 'https://example.org/ref.png'}] * 5}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_call('angelo_studio_generate', self.args(**updates))
        source = self.args(options={'aspect': '16:9'})
        checked = validate_call('angelo_studio_generate', source)
        checked['options']['aspect'] = '9:16'
        self.assertEqual(source['options']['aspect'], '16:9')

    def test_capability_controls_fail_closed(self):
        for mode, opts in [('gemini_images_api', {'quality': 'high'}), ('tripo_api', {'background': 'transparent'}),
                           ('meshy_api', {'aspect': '1:1'}), ('seedream_api', {'resolution': '1K'})]:
            with self.assertRaises(ValueError):
                generation_options(mode, 'model', opts)

    def test_account_unavailable_never_falls_back(self):
        self.choices[0]['ready'] = False
        with self.assertRaisesRegex(ValueError, 'exact generation account'):
            self.service.precondition('angelo_studio_generate', self.args())
        self.assertEqual(self.requests, [])
        self.choices[0]['ready'] = True
        with self.assertRaises(ValueError):
            self.service.precondition('angelo_studio_generate', self.args(connection_id=str(uuid.uuid4())))
        self.assertEqual(self.requests, [])

    def test_reference_ownership_and_public_url_validation_before_approval(self):
        reference = self.attach()
        args = self.args(references=[{'source': 'ATTACHMENT', 'id': reference['id']}])
        self.service.precondition('angelo_studio_generate', args)
        self.controller.turn_attachments.clear()
        with self.assertRaisesRegex(ValueError, 'THIS conversation'):
            self.service.precondition('angelo_studio_generate', args)
        for url in ('file:///tmp/x.png', 'http://example.org/x.png', 'https://127.0.0.1/x.png'):
            with self.assertRaises(ValueError):
                self.service.precondition('angelo_studio_generate', self.args(references=[{'source': 'URL', 'id': url}]))
        self.assertEqual(self.requests, [])

    def test_auto_mode_still_requires_individual_billing_approval(self):
        self.controller.state['options']['approval'] = 'AUTO_BOUNDED'
        answers = []
        self.controller.request_tool('paid', 'angelo_studio_generate', self.args(), answers.append)
        self.controller.pump_tools(self.executor, self.controller.document, apply_mutation=Mock(),
                                   budget=ToolDispatchBudget(seconds=60))
        call = next(iter(self.controller.pending.values()))
        self.assertTrue(call.approval)
        self.assertIn('Named fixture account', call.usage_label)
        self.assertEqual(self.requests, [])
        self.assertEqual(answers, [])
        self.controller.decline_tool(call.id)
        self.assertIn('declined', answers[0]['error'])
        self.assertEqual(self.requests, [])

    def test_readonly_refuses_generation_and_library_changes(self):
        self.controller.state['options']['approval'] = 'READ_ONLY'
        for name, args in [('angelo_studio_generate', self.args()), ('angelo_studio_create_folder', {'name': 'Props'})]:
            answers = []
            self.controller.request_tool(name, name, args, answers.append)
            self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
            self.assertIn('read-only', answers[0]['error'])
        self.assertEqual(self.requests, [])
        self.assertFalse(self.library.folders)

    def test_deferred_completion_delivers_actual_output_and_admitted_preview(self):
        answers = []
        self.controller.request_tool('paid', 'angelo_studio_generate', self.args(), answers.append)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        call = next(iter(self.controller.pending.values()))
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertIn(call.id, self.controller.media_jobs)
        self.assertEqual(answers, [])
        deadline = time.monotonic() + 3
        while not answers and time.monotonic() < deadline:
            self.controller.pump_events()
            time.sleep(.01)
        self.assertEqual(answers[0]['status'], 'ready')
        self.assertTrue(self.library.checked_file(self.library.assets[answers[0]['id']], answers[0]['files'][0]).is_file())
        self.assertEqual(self.controller.references.checked_path(answers[0]['views'][0]).read_bytes(), PNG)
        self.import_model.assert_not_called()
        self.assertEqual(len(self.requests), 1)

    def test_billing_key_rotation_invalidates_pending_approval(self):
        answers = []
        self.controller.request_tool('paid', 'angelo_studio_generate', self.args(), answers.append)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        call = next(iter(self.controller.pending.values()))
        self.binding = GenerationBinding(ACCOUNT, 'gemini_images_api', (MODEL,), True, 'different-private-key')
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertIn('target changed', answers[0]['error'])
        self.assertEqual(self.requests, [])

    def test_enhancement_suggests_without_applying_or_generating(self):
        result = self.wait(self.service.execute('angelo_studio_enhance',
            {'prompt': 'A sword', 'kind': 'IMAGE', 'generation_model': MODEL}))
        self.assertEqual(result['original_prompt'], 'A sword')
        self.assertEqual(result['enhanced_prompt'], 'Improved prompt')
        self.assertFalse(result['applied'])
        self.assertEqual(self.requests, [])

    def test_enhancement_failure_shows_only_sanitized_provider_errors(self):
        from angelo_agent.studio_generation import GenerationError
        args = {'prompt': 'A sword', 'kind': 'IMAGE', 'generation_model': MODEL}
        def rejected(cancel):
            raise GenerationError('Gemini Images rejected the request (HTTP 400): INVALID_ARGUMENT — Bad field. Not retried')
        self.enhancer.return_value = dict(self.enhancer.return_value, run=rejected)
        result = self.wait(self.service.execute('angelo_studio_enhance', args))
        self.assertTrue(result['error'].startswith('Enhancement did not complete; original prompt unchanged.'))
        self.assertIn('INVALID_ARGUMENT — Bad field', result['error'])
        def crashed(cancel):
            raise RuntimeError('unsanitized internal detail /private/path')
        self.enhancer.return_value = dict(self.enhancer.return_value, run=crashed)
        result = self.wait(self.service.execute('angelo_studio_enhance', args))
        self.assertNotIn('/private/path', result['error'])

    def test_folders_import_search_move_export_and_reference_reuse(self):
        ref = self.attach()
        folder = self.service.execute('angelo_studio_create_folder', {'name': 'Props'})['folder']
        asset = self.service.execute('angelo_studio_import_image', {'reference_id': ref['id'], 'folder': folder['id']})
        self.assertEqual(self.service.execute('angelo_studio_list', {'folder': folder['id'], 'query': 'REFERENCE'})['total'], 1)
        self.service.execute('angelo_studio_move_asset', {'asset_id': asset['id'], 'folder': ''})
        self.assertEqual(self.service.execute('angelo_studio_list', {'folder': 'UNFILED'})['total'], 1)
        generated = self.wait(self.service.execute('angelo_studio_generate', self.args(references=[{'source': 'ASSET', 'id': asset['id']}])) )
        self.assertEqual(generated['reference_names'], ['reference.png'])
        checkpoint, snapshot = self.controller.checkpoints.reserve()
        snapshot.write_bytes(b'synthetic host-only Studio checkpoint')
        self.controller.checkpoints.complete(checkpoint['id'])
        self.controller.request_checkpoint = checkpoint['id']
        result = self.service.execute('angelo_studio_export', {'asset_id': asset['id'], 'path': 'exports/image.png'})
        self.assertEqual((self.controller.files.root / result['path']).read_bytes(), PNG)
        self.assertFalse(result['blender_undoable'])
        self.assertTrue(result['journaled'])
        self.assertEqual(result['file_changes']['written'], ['exports/image.png'])
        for path in ('exports/image.png', '../secret.png', '/tmp/secret.png', 'exports/output.glb'):
            with self.assertRaises(ValueError):
                self.service.precondition('angelo_studio_export', {'asset_id': asset['id'], 'path': path})

    def test_paid_meshy_stages_and_full_prompt_visible_in_approval(self):
        args = self.args(model='meshy-7', prompt='A sculpture ' * 70)
        message = approval_message('angelo_studio_generate', args, 'Work account · API billing')
        self.assertIn('Work account', message['body'])
        self.assertIn('Two billable stages', message['body'])
        self.assertIn('NOT your coding subscription', message['warning'])
        self.assertIn(args['prompt'], approval_details('angelo_studio_generate', args))

    def test_stop_closes_deferred_monitor_and_reports_billing_uncertainty(self):
        cancel = Mock()
        answers = []
        self.controller.request_tool('paid', 'angelo_studio_generate', self.args(), answers.append)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        call = next(iter(self.controller.pending.values()))
        self.executor.execute.side_effect = None
        self.executor.execute.return_value = StudioPending(lambda: None, cancel, asset_id=str(uuid.uuid4()))
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.controller.stop()
        cancel.assert_called_once()
        self.assertIn('billed', answers[0]['error'])
        self.assertFalse(self.controller.media_jobs)

    def test_scene_switch_cancels_pending_usage_without_retry(self):
        answers = []
        self.controller.request_tool('paid', 'angelo_studio_generate', self.args(), answers.append)
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        self.controller.set_document(('another', 2))
        self.assertIn('document changed', answers[0]['error'].lower())
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
