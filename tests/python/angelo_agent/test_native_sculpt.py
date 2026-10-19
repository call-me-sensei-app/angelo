# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.sculpt_spec import resample_paths, SCULPT_NAMES
from angelo_agent.tool_specs import validate_call, BOUNDED_MUTATIONS, COLLABORATOR_TOOLS, native_plan_schema
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.providers import tool_content, ClaudeProvider
from angelo_agent.approvals import approval_message, approval_details
from angelo_agent.references import ReferenceStore


def point(x, pressure=1):
    return {'position': [x, 0, 0], 'pressure': pressure}


def arguments():
    return {'object': 'Artist sculpt', 'mesh_revision': 'a'*64, 'brush': 'CLAY', 'radius': .3, 'strength': .35,
            'direction': 'ADD', 'symmetry': 'X', 'normal': [0, -2, 0], 'front_faces_only': True,
            'spacing': .2, 'paths': [[point(0, .2), point(.3, .8)]]}


class SculptContractTests(unittest.TestCase):
    def test_brush_contract_is_strict_and_copies_nested_values(self):
        source = arguments()
        result = validate_call('angelo_sculpt_batch', source)
        self.assertEqual(result['normal'], [0, -1, 0])
        result['paths'][0][0]['position'][0] = 4
        self.assertEqual(source['paths'][0][0]['position'][0], 0)

    def test_nonfinite_unbounded_and_injected_values_rejected(self):
        for field, bad in [('radius', math.nan), ('radius', -1), ('strength', 2), ('spacing', .00001),
                           ('normal', [0,0,0]), ('normal', [0,math.inf,0]), ('brush', 'EXECUTE_PYTHON'),
                           ('mesh_revision', '../scene.blend'), ('front_faces_only', 1), ('paths', [])]:
            with self.subTest(field=field, bad=bad):
                value = arguments(); value[field] = bad
                with self.assertRaises(ValueError):
                    validate_call('angelo_sculpt_batch', value)
        value = arguments(); value['paths'][0][0]['code'] = 'exec something'
        with self.assertRaises(ValueError):
            validate_call('angelo_sculpt_batch', value)

    def test_arc_length_pressure_and_endpoints(self):
        sampled = resample_paths([[point(0,.2), point(.07,.4), point(.3,.8)]], .06)[0]
        self.assertEqual(len(sampled), 6)
        self.assertEqual(sampled[-1]['position'], [.3,0,0])
        self.assertAlmostEqual(sampled[-1]['pressure'], .8)
        distances = [math.dist(a['position'], b['position']) for a,b in zip(sampled, sampled[1:])]
        self.assertLess(max(distances)-min(distances), 1e-6)

    def test_degenerate_segment_and_point_dab(self):
        sampled = resample_paths([[point(0),point(0),point(.1)], [point(0)]], .05)
        self.assertEqual(list(map(len, sampled)), [3,1])
        self.assertTrue(all(math.isfinite(v) for p in sampled[0] for v in p['position']))

    def test_long_paths_fail_without_allocating_unbounded_dabs(self):
        value = arguments(); value.update(radius=.000001, paths=[[point(0),point(1)]])
        with self.assertRaisesRegex(ValueError, '512'):
            validate_call('angelo_sculpt_batch', value)
        value = arguments(); value['paths'] = [[point(0)] for _ in range(33)]
        with self.assertRaises(ValueError):
            validate_call('angelo_sculpt_batch', value)

    def test_grab_keeps_3d_endpoint_and_rejects_multiple_paths(self):
        value = arguments(); value['brush'] = 'GRAB'
        validate_call('angelo_sculpt_batch', value)
        self.assertEqual(len(resample_paths(value['paths'], .00001, grab=True)[0]), 2)
        value['paths'].append([point(0),point(1)])
        with self.assertRaisesRegex(ValueError, 'Grab'):
            validate_call('angelo_sculpt_batch', value)

    def test_directional_brushes_require_actual_motion(self):
        for brush in ('PINCH','FLATTEN'):
            value = arguments(); value.update(brush=brush, paths=[[point(0)]])
            with self.assertRaisesRegex(ValueError, 'moving'):
                validate_call('angelo_sculpt_batch', value)
            value['paths'] = [[point(0),point(.1)]]
            validate_call('angelo_sculpt_batch', value)

    def test_subdivision_and_capture_limits(self):
        for bad in (True, 4, -1, 1.5):
            with self.assertRaises(ValueError):
                validate_call('angelo_prepare_sculpt', {'object':'Mesh','mesh_revision':'a'*64,'add_levels':bad})
        for bad in (4096, True):
            with self.assertRaises(ValueError):
                validate_call('angelo_sculpt_view', {'object':'Mesh','views':['FRONT'],'resolution':bad})
        self.assertEqual(SCULPT_NAMES & COLLABORATOR_TOOLS, {'angelo_inspect_sculpt', 'angelo_sculpt_view', 'angelo_sculpt_target', 'angelo_sculpt_review'})
        self.assertTrue({'angelo_sculpt_batch', 'angelo_prepare_sculpt'} <= BOUNDED_MUTATIONS)
        allowed = native_plan_schema(COLLABORATOR_TOOLS)
        self.assertNotIn('angelo_sculpt_batch', json.dumps(allowed))

    def test_approval_copy_is_readable_not_json(self):
        args = arguments()
        message = approval_message('angelo_sculpt_batch', args)
        self.assertIn('Clay sculpt pass', message['title'])
        self.assertIn('Stop', message['warning'])
        self.assertNotIn('{', approval_details('angelo_sculpt_batch', args))

    def test_pixel_probe_contract_and_private_camera_authority(self):
        args = {'object':'Mesh','mesh_revision':'a'*64,'view_reference':'92447c82-6f6e-464b-8497-1d65b1bf1cb2', 'pixels':[[.5,.5]]}
        validate_call('angelo_sculpt_target', args)
        for pixels in ([], [[1.1,0]], [[True,0]], [[0,math.nan]], [[0,0,0]]):
            with self.assertRaises(ValueError):
                validate_call('angelo_sculpt_target', args | {'pixels':pixels})
        with tempfile.TemporaryDirectory() as directory:
            refs = ReferenceStore(directory, '92447c82-6f6e-464b-8497-1d65b1bf1cb2')
            record = refs.new_observation('Front')
            with self.assertRaisesRegex(ValueError, 'fresh native'):
                refs.observation_camera(record['id'])
            refs.remember_observation(record, {'object_uid':7,'mesh_revision':'a'*64})
            self.assertEqual(refs.observation_camera(record['id'])['object_uid'], 7)
            refs.discard_observation(record)
            with self.assertRaises(ValueError):
                refs.observation_camera(record['id'])

    def test_sculpt_approval_readonly_stop_and_stale_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.busy = True
            controller.set_document(('scratch',))
            executor = SimpleNamespace(precondition=Mock(return_value='a'), execute=Mock(return_value={'success':True}))
            results = []
            controller.request_tool('sculpt', 'angelo_sculpt_batch', arguments(), results.append)
            controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
            executor.execute.assert_not_called()
            call = next(iter(controller.pending.values()))
            self.assertTrue(call.approval)
            executor.precondition.return_value = 'b'
            controller.execute_tool(call, executor, controller.document)
            self.assertIn('target changed', results[0]['error'])
            executor.execute.assert_not_called()
            self.assertIsNotNone(controller.recovery_barrier.pending)
            controller.close()

    def test_sculpt_readonly_gate_on_a_fresh_request(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.busy = True
            controller.set_document(('scratch',))
            executor = SimpleNamespace(precondition=Mock(return_value='a'), execute=Mock(return_value={'success':True}))
            results = []
            controller.state['options']['approval'] = 'READ_ONLY'
            controller.request_tool('denied', 'angelo_sculpt_batch', arguments(), results.append)
            controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
            self.assertIn('read-only', results[-1]['error'])
            executor.execute.assert_not_called()
            controller.close()

    def test_actual_image_tool_protocol_and_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            refs = ReferenceStore(directory, '92447c82-6f6e-464b-8497-1d65b1bf1cb2')
            record = refs.new_observation('Sculpt front')
            Path(record['path']).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            controller = SimpleNamespace(references=refs, models=[], settings={}, state={'options':{}}, turn_options={})
            content = tool_content(controller, 'angelo_sculpt_view', {'success':True, 'views':[record]})
            self.assertEqual(content[1]['type'], 'inputImage')
            self.assertTrue(content[1]['imageUrl'].startswith('data:image/png;base64,'))
            self.assertNotIn('inputImage', str(tool_content(controller, 'angelo_execute_python', {'views':[record]})))
            record['path'] = '/etc/passwd'
            with self.assertRaises(ValueError):
                tool_content(controller, 'angelo_sculpt_view', {'views':[record]})

    def test_review_images_are_forwarded_and_refreshed_after_a_later_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            controller=Controller(directory)
            old=controller.references.new_observation('Review');old['view']='FRONT'
            fresh=controller.references.new_observation('Current')
            for record in (old,fresh):
                Path(record['path']).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            self.assertEqual(tool_content(controller,'angelo_sculpt_review',{'views':[old]})[1]['type'],'inputImage')
            provider=ClaudeProvider(controller,{})
            operation={'tool':'angelo_sculpt_review','arguments':{'object':'Artist sculpt','baseline':old['id'],'outline':[],'landmarks':[]}}
            provider._turn=Mock(side_effect=[{'reply':'Review then edit','operations':[operation,{'tool':'angelo_sculpt_batch','arguments':arguments()}],'verify':True},
                                             {'reply':'Verified','operations':[],'verify':False}])
            provider._native_call=Mock(side_effect=[{'views':[old],'resolution':512},{'success':True},{'views':[fresh]},{'objects':[]}])
            self.assertEqual(provider.run(None,'Sculpt',observation={}),'completed')
            self.assertEqual(provider._turn.call_args.kwargs['references'],[fresh])
            self.assertEqual(provider._native_call.call_args_list[2].args,('angelo_sculpt_review', operation['arguments']))
            controller.close()

    def test_active_comparison_baseline_survives_bounded_camera_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            refs=ReferenceStore(directory,'92447c82-6f6e-464b-8497-1d65b1bf1cb2')
            baseline=refs.new_observation('Baseline');Path(baseline['path']).write_bytes(b'png')
            refs.remember_observation(baseline,{'object_uid':1})
            for i in range(20):
                refs.observation_camera(baseline['id'])
                record=refs.new_observation(str(i));Path(record['path']).write_bytes(b'png')
                refs.remember_observation(record,{'object_uid':i+2})
            self.assertEqual(refs.observation_camera(baseline['id'])['object_uid'],1)
            self.assertEqual(len(refs._observations),12)

    def test_claude_receives_pixels_in_visual_verification_round(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            record = controller.references.new_observation('Front')
            Path(record['path']).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            provider = ClaudeProvider(controller, {})
            operation = {'tool':'angelo_sculpt_view', 'arguments':{'object':'Mesh','views':['FRONT'],'resolution':512}}
            provider._turn = Mock(side_effect=[{'reply':'Inspecting the shape.', 'operations':[operation], 'verify':False},
                                               {'reply':'The shape needs a lighter crease.', 'operations':[], 'verify':False}])
            provider._native_call = Mock(side_effect=[{'success':True,'views':[record]}, {'objects':[]}])
            self.assertEqual(provider.run(None, 'Review this sculpt', observation={}), 'completed')
            self.assertEqual(provider._turn.call_count, 2)
            self.assertEqual(provider._turn.call_args.kwargs['references'], [record])
            self.assertEqual(controller.references.claude_inputs([record])[0]['type'], 'image')
            controller.close()

    def test_failed_mutation_reports_uncertainty_and_request_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.busy = True
            controller.set_document(('scratch',))
            checkpoint, _ = controller.checkpoints.reserve()
            controller.request_checkpoint = checkpoint['id']
            executor = SimpleNamespace(precondition=Mock(return_value='same'),
                                       execute=Mock(side_effect=RuntimeError('Injected execution failure')))
            results = []
            controller.request_tool('partial', 'angelo_sculpt_batch', arguments(), results.append)
            controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
            controller.execute_tool(next(iter(controller.pending.values())), executor, controller.document)
            self.assertTrue(results[0]['partial_changes_possible'])
            self.assertTrue(results[0]['changes']['uncertain'])
            self.assertEqual(results[0]['request_checkpoint'], checkpoint['id'])
            self.assertIn('Do not automatically retry', results[0]['recovery'])
            controller.close()

    def test_known_preflight_and_readonly_failures_do_not_claim_mutation(self):
        class PreflightError(RuntimeError):
            partial_changes_possible = False
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            controller.busy = True
            controller.set_document(('scratch',))
            executor = SimpleNamespace(precondition=Mock(return_value='same'), execute=Mock(side_effect=PreflightError('Scale blocked')))
            results = []
            controller.request_tool('preflight', 'angelo_sculpt_batch', arguments(), results.append)
            controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
            controller.execute_tool(next(iter(controller.pending.values())), executor, controller.document)
            self.assertNotIn('partial_changes_possible', results[-1])
            executor.execute.side_effect = RuntimeError('Observation unavailable')
            controller.request_tool('read', 'angelo_inspect_sculpt', {'object':'Mesh','probes':[]}, results.append)
            controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
            self.assertNotIn('partial_changes_possible', results[-1])
            controller.close()

    def test_a_classified_preflight_refusal_says_nothing_ran_and_holds_nothing(self):
        """A sculpt edit refused before it ran (sculpt.SculptPreflightError: VALIDATION, or STALE_REVISION for a
        surface that changed) states the scene unchanged, and the request's next edit goes on. Unclassified, one
        such refusal held every later edit of a one-shot."""
        class Preflight(RuntimeError):
            partial_changes_possible = False

            def __init__(self, message, code):
                super().__init__(message)
                self.refusal_code = code
        for code, where in (("VALIDATION", "execute"), ("STALE_REVISION", "execute"), ("VALIDATION", "precondition"),
                            ("STALE_REVISION", "precondition")):
            with self.subTest(code=code, where=where), tempfile.TemporaryDirectory() as directory:
                controller = Controller(directory)
                controller.busy = True
                controller.set_document(('scratch',))
                failing = Mock(side_effect=Preflight('Refused before any edit', code))
                executor = SimpleNamespace(precondition=failing if where == 'precondition' else Mock(return_value='same'),
                                           execute=failing if where == 'execute' else Mock(return_value={'success': True}))
                results = []
                controller.request_tool('refused', 'angelo_sculpt_batch', arguments(), results.append)
                controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
                if controller.pending:
                    controller.execute_tool(next(iter(controller.pending.values())), executor, controller.document)
                refused = results[-1]
                self.assertEqual((refused['error_code'], refused.get('scene_changed')), (code, False))
                self.assertNotIn('partial_changes_possible', refused)
                self.assertNotIn('recovery_required', refused)
                executor.precondition, executor.execute = Mock(return_value='same'), Mock(return_value={'success': True})
                controller.request_tool('next', 'angelo_sculpt_batch', arguments(), results.append)
                controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
                if controller.pending:
                    controller.execute_tool(next(iter(controller.pending.values())), executor, controller.document)
                self.assertIs(results[-1].get('success'), True)   # Not held: the next edit ran.
                controller.close()

    def test_private_depth_camera_memory_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            refs = ReferenceStore(directory, '92447c82-6f6e-464b-8497-1d65b1bf1cb2')
            records = []
            for _ in range(13):
                record = refs.new_observation('Front')
                refs.remember_observation(record, {'depths':object()})
                records.append(record)
            self.assertEqual(len(refs._observations), 12)
            with self.assertRaisesRegex(ValueError, 'fresh native'):
                refs.observation_camera(records[0]['id'])
            self.assertIsNotNone(refs.observation_camera(records[-1]['id'])['depths'])

    def test_claude_discards_views_before_any_later_mutation(self):
        # Include non-sculpt edits: arbitrary reviewed bpy may change the target
        # and file/delegate annotations must not create an invalidation bypass.
        mutations = [
            {'tool':'angelo_sculpt_batch', 'arguments':arguments()},
            {'tool':'angelo_set_transform', 'arguments':{'object':'Artist sculpt','location':[0,1,0]}},
            {'tool':'angelo_execute_python', 'arguments':{'description':'Reviewed edit','code':'result = None'}},
        ]
        for mutation in mutations:
            for verify in (False, True):
                with self.subTest(tool=mutation['tool'], verify=verify), tempfile.TemporaryDirectory() as directory:
                    controller = Controller(directory)
                    old = controller.references.new_observation('Old view')
                    fresh = controller.references.new_observation('Fresh view')
                    for record in (old, fresh):
                        Path(record['path']).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
                    provider = ClaudeProvider(controller, {})
                    view = {'tool':'angelo_sculpt_view', 'arguments':{'object':'Artist sculpt','views':['FRONT'],'resolution':512}}
                    provider._turn = Mock(side_effect=[{'reply':'Edit after viewing.', 'operations':[view,mutation], 'verify':verify},
                                                       {'reply':'Verified the current result.', 'operations':[], 'verify':False}])
                    provider._native_call = Mock(side_effect=[{'success':True,'views':[old]}, {'success':True},
                                                             {'success':True,'views':[fresh]}, {'objects':[]}])
                    self.assertEqual(provider.run(None, 'Sculpt', observation={}), 'completed')
                    self.assertEqual(provider._turn.call_count, 2)
                    self.assertEqual(provider._turn.call_args.kwargs['references'], [fresh])
                    self.assertEqual(provider._native_call.call_args_list[2].args, ('angelo_sculpt_view', view['arguments']))
                    self.assertIn('stale_visual_evidence_discarded', provider._turn.call_args.args[1])
                    controller.close()

    def test_claude_view_after_edit_is_not_recaptured_and_reads_keep_it_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            fresh = controller.references.new_observation('Current view')
            provider = ClaudeProvider(controller, {})
            view = {'tool':'angelo_sculpt_view','arguments':{'object':'Artist sculpt','views':['FRONT'],'resolution':512}}
            read = {'tool':'angelo_inspect_sculpt','arguments':{'object':'Artist sculpt','probes':[]}}
            provider._turn = Mock(side_effect=[{'reply':'Edit then view.', 'operations':[
                {'tool':'angelo_sculpt_batch','arguments':arguments()},view,read], 'verify':False},
                {'reply':'Verified.', 'operations':[], 'verify':False}])
            provider._native_call = Mock(side_effect=[{'success':True}, {'views':[fresh]}, {'object':'Artist sculpt'}, {'objects':[]}])
            self.assertEqual(provider.run(None, 'Sculpt', observation={}), 'completed')
            self.assertEqual(provider._turn.call_args.kwargs['references'], [fresh])
            self.assertEqual(sum(c.args[0]=='angelo_sculpt_view' for c in provider._native_call.call_args_list), 1)
            controller.close()

    def test_claude_failed_or_cancelled_edit_never_forwards_stale_images(self):
        for mode in ('edit_failure','cancelled','recapture_failure'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                controller = Controller(directory)
                old = controller.references.new_observation('Old view')
                provider = ClaudeProvider(controller, {})
                view = {'tool':'angelo_sculpt_view','arguments':{'object':'Artist sculpt','views':['FRONT'],'resolution':512}}
                provider._turn = Mock(return_value={'reply':'View then edit.', 'operations':[
                    view,{'tool':'angelo_sculpt_batch','arguments':arguments()}], 'verify':True})
                calls = []
                def native(name, args, *, owner):
                    self.assertIs(owner.controller, controller)
                    calls.append(name)
                    if name == 'angelo_sculpt_batch':
                        controller.cancelled = mode == 'cancelled'
                        return {'error':'Partial edit'} if mode == 'edit_failure' else {'success':True}
                    return {'views':[old]} if len(calls) == 1 else {'error':'Capture unavailable'}
                provider._native_call = Mock(side_effect=native)
                self.assertEqual(provider.run(None, 'Sculpt', observation={}), 'interrupted')
                self.assertEqual(provider._turn.call_count, 1)
                self.assertEqual(calls.count('angelo_sculpt_view'), 2 if mode=='recapture_failure' else 1)
                controller.close()

    def test_claude_refreshes_views_across_plan_rounds_and_coalesced_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            old = controller.references.new_observation('Before')
            fresh = controller.references.new_observation('After')
            provider = ClaudeProvider(controller, {})
            view = {'tool':'angelo_sculpt_view','arguments':{'object':'Artist sculpt','views':['FRONT'],'resolution':512}}
            provider._turn = Mock(side_effect=[{'reply':'View.', 'operations':[view], 'verify':False},
                {'reply':'Move.', 'operations':[{'tool':'angelo_set_transform','arguments':{'object':'Artist sculpt','location':[1,0,0]}}], 'verify':False},
                {'reply':'Verified.', 'operations':[], 'verify':False}])
            provider._native_call = Mock(side_effect=[{'views':[old]}, {'objects':[]}, {'success':True}, {'views':[fresh]}, {'objects':[]}])
            self.assertEqual(provider.run(None, 'Move', observation={}), 'completed')
            self.assertEqual(provider._native_call.call_args_list[2].args[0], 'angelo_apply_batch')
            self.assertEqual(provider._turn.call_args.kwargs['references'], [fresh])
            controller.close()

    def test_claude_multiple_targets_only_forward_current_images(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            provider = ClaudeProvider(controller, {})
            views = [{'tool':'angelo_sculpt_view','arguments':{'object':name,'views':['FRONT'],'resolution':512}}
                     for name in ('Artist sculpt','Second sculpt')]
            old = [controller.references.new_observation('Old '+str(i)) for i in range(2)]
            fresh = [controller.references.new_observation('Current '+str(i)) for i in range(2)]
            provider._turn = Mock(side_effect=[{'reply':'Observe both then edit.', 'operations':
                views+[{'tool':'angelo_sculpt_batch','arguments':arguments()}], 'verify':False},
                {'reply':'Verified both.', 'operations':[], 'verify':False}])
            # Kept views are captured again most recently asked first, so each answer follows its object.
            answers = {'Artist sculpt': [old[0], fresh[0]], 'Second sculpt': [old[1], fresh[1]]}
            def native(name, args, *, owner):
                if name == 'angelo_sculpt_view':
                    return {'views': [answers[args['object']].pop(0)]}
                return {'success': True} if name == 'angelo_sculpt_batch' else {'objects': []}
            provider._native_call = Mock(side_effect=native)
            self.assertEqual(provider.run(None,'Sculpt',observation={}), 'completed')
            self.assertEqual(provider._turn.call_args.kwargs['references'], fresh)
            self.assertEqual(sum(c.args[0]=='angelo_sculpt_view' for c in provider._native_call.call_args_list),4)
            controller.close()


if __name__ == '__main__':
    unittest.main()
