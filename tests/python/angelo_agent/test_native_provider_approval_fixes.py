# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled host regressions for retained sculpt evidence and readable approvals."""

import copy
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.approvals import approval_details, approval_message
from angelo_agent.controller import Controller
from angelo_agent.plan_schema import PlanRejected
from angelo_agent.protocol import RpcError
from angelo_agent.providers import ClaudeProvider, _ClaudeSculptEvidence, CLAUDE_PLAN_REJECTIONS, CLAUDE_SCULPT_IMAGE_LIMIT
from angelo_agent.tool_specs import validate_call


def step():
    return {'brush': 'DRAW', 'radius': .3, 'strength': .3, 'direction': 'ADD', 'symmetry': 'NONE',
            'normal': [0, -1, 0], 'front_faces_only': True, 'spacing': .2,
            'paths': [[{'position': [0, -1, 0], 'pressure': 1}]]}


def program():
    return {'tool': 'angelo_sculpt_program',
            'arguments': {'object': 'Sphere', 'mesh_revision': 'a' * 64, 'steps': [step()]}}


def view(angles=('FRONT',), **kwargs):
    return {'tool': 'angelo_sculpt_view',
            'arguments': {'object': 'Sphere', 'views': list(angles), 'resolution': 512, **kwargs}}


def camera(index=0):
    return {'object_uid': 1, 'label': 'FRONT', 'center': [index, 0, 0], 'radius': 1,
            'direction': [0, -1, 0], 'clip_depth': 12, 'camera_distance': 4, 'mesh_revision': 'a' * 64}


def observation(controller, name, *, angle='FRONT', frame=None):
    record = controller.references.new_observation(name)
    record['view'] = angle
    Path(record['path']).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    if frame is not None:
        controller.references.remember_observation(record, frame)
    return record


class SculptApprovalFixTests(unittest.TestCase):
    def test_every_material_control_is_distinguishable_in_summary_and_details(self):
        original = program()['arguments']
        variations = {
            'direction': lambda s: s.update(direction='SUBTRACT'),
            'symmetry': lambda s: s.update(symmetry='X'),
            'falloff': lambda s: s.update(falloff='CONSTANT'),
            'automasking': lambda s: s.update(automasking=['FACE_SETS']),
            'normal': lambda s: s.update(normal=[1, 0, 0]),
            'front_faces_only': lambda s: s.update(front_faces_only=False),
            'radius': lambda s: s.update(radius=.300000000001),
            'strength': lambda s: s.update(strength=.6),
            'spacing': lambda s: s.update(spacing=.4),
            'pressure': lambda s: s['paths'][0][0].update(pressure=.4),
            'radius_scale': lambda s: s['paths'][0][0].update(radius_scale=1.5),
            'tilt': lambda s: s['paths'][0][0].update(tilt=[.2, -.3]),
            'position': lambda s: s['paths'][0][0].update(position=[.000000000001, -1, 0]),
            'region': lambda s: s.update(region='Upper lip'),
            'path_count': lambda s: s['paths'].append(copy.deepcopy(s['paths'][0])),
        }
        for name, change in variations.items():
            with self.subTest(control=name):
                changed = copy.deepcopy(original)
                change(changed['steps'][0])
                validate_call('angelo_sculpt_program', changed)
                self.assertNotEqual(approval_message('angelo_sculpt_program', original),
                                    approval_message('angelo_sculpt_program', changed))
                self.assertNotEqual(approval_details('angelo_sculpt_program', original),
                                    approval_details('angelo_sculpt_program', changed))

    def test_details_list_all_controls_in_order_and_do_not_round_away_small_changes(self):
        args = program()['arguments']
        args['steps'][0].update(region='Left eyelid', automasking=['BOUNDARY'], falloff='SHARP')
        args['steps'][0]['paths'][0] = [
            {'position': [.100000000001, 0, 0], 'pressure': .2, 'radius_scale': .5, 'tilt': [.1, -.2]},
            {'position': [.100000000002, 0, 0], 'pressure': .8, 'radius_scale': 1.5, 'tilt': [-.3, .4]},
        ]
        text = approval_details('angelo_sculpt_program', args)
        for value in ('Direction: Add (ADD)', 'Left eyelid', 'World offsets', 'Boundary', 'Sharp',
                      'Symmetry: None', 'Front faces only: Yes', 'Outward viewing normal', 'Spacing:',
                      'Existing masks and hidden faces', '0.100000000001', '0.100000000002',
                      'pressure 0.2', 'radius scale 1.5', 'tilt X -0.3, Y 0.4', 'a' * 64):
            self.assertIn(value, text)
        self.assertLess(text.index('0.100000000001'), text.index('0.100000000002'))
        self.assertNotIn('{', text)
        self.assertNotIn('…', text)

    def test_full_details_keep_maximum_control_points_and_last_pass(self):
        args = program()['arguments']
        # Repeated points are valid controls even when resampling produces only
        # one dab. Review must still show the entire bounded submitted program.
        args['steps'] = [step() for _ in range(8)]
        for item in args['steps']:
            item['paths'][0] = [copy.deepcopy(item['paths'][0][0]) for _ in range(256)]
        args['steps'][-1]['paths'][0][-1]['pressure'] = .123456789012345
        validate_call('angelo_sculpt_program', args)
        text = approval_details('angelo_sculpt_program', args)
        self.assertIn('Pass 8: Draw', text)
        self.assertEqual(text.count('; pressure '), 8 * 256)
        self.assertIn('256. Position X 0, Y -1, Z 0; pressure 0.123456789012345', text)

    def test_batch_has_the_same_complete_controls_as_program(self):
        args = dict(program()['arguments'], **step())
        del args['steps']
        args.update(direction='SUBTRACT', symmetry='Y', automasking=['TOPOLOGY'], falloff='LINEAR')
        args['paths'][0][0].update(radius_scale=1.7, tilt=[-.5, .6])
        text = approval_details('angelo_sculpt_batch', args)
        for value in ('Subtract (SUBTRACT)', 'local Y', 'Topology', 'Linear', 'radius scale 1.7', 'tilt X -0.5, Y 0.6'):
            self.assertIn(value, text)

    def test_region_and_resolution_describe_their_material_settings(self):
        region = {'object': 'Sphere', 'mesh_revision': 'a' * 64, 'name': 'Eyelid',
                  'position': [.2, -.3, .4], 'radius': .15, 'face_set': 7}
        text = approval_details('angelo_sculpt_region', region)
        self.assertIn('X 0.2, Y -0.3, Z 0.4', text)
        self.assertIn('face set 7', text)
        resolution = {'object': 'Sphere', 'mesh_revision': 'a' * 64, 'mode': 'AUTO', 'stage': 'DETAIL',
                      'radius': .05, 'level': 0}
        self.assertIn('Brush radius: 0.05', approval_details('angelo_sculpt_resolution', resolution))


class ClaudeSculptEvidenceFixTests(unittest.TestCase):
    def test_front_and_right_are_both_refreshed_after_the_same_object_changes(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            provider = ClaudeProvider(host, {})
            provider._turn = Mock(side_effect=[
                {'reply': 'Observe and sculpt', 'operations': [view(), view(('RIGHT',)), program()], 'verify': True},
                {'reply': 'Verified', 'operations': [], 'verify': False}])
            count = 0
            def native(name, args, *, owner):
                self.assertIs(owner.controller, host)
                nonlocal count
                if name == 'angelo_sculpt_view':
                    count += 1
                    return {'views': [observation(host, f'{count}: {angle}', angle=angle) for angle in args['views']]}
                return {'success': True}
            provider._native_call = Mock(side_effect=native)
            self.assertEqual(provider.run(None, 'Sculpt', observation={}), 'completed')
            images = provider._turn.call_args.kwargs['references']
            self.assertEqual([v['view'] for v in images], ['FRONT', 'RIGHT'])
            self.assertEqual([v['name'] for v in images], ['3: FRONT', '3: RIGHT'])
            self.assertEqual(count, 3, 'Matching views should share one native refresh call')

    def test_six_requested_images_are_forwarded_without_tail_truncation(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            provider = ClaudeProvider(host, {})
            wide = view(('FRONT', 'RIGHT', 'THREE_QUARTER'))
            close = view(('FRONT', 'RIGHT', 'THREE_QUARTER'), region='Left eye')
            provider._turn = Mock(side_effect=[
                {'reply': 'Observe and sculpt', 'operations': [wide, close, program()], 'verify': False},
                {'reply': 'Verified', 'operations': [], 'verify': False}])
            captures = 0
            def native(name, args, *, owner):
                self.assertIs(owner.controller, host)
                nonlocal captures
                if name == 'angelo_sculpt_view':
                    captures += 1
                    return {'views': [observation(host, f'{captures}: {args.get("region", "whole")} {angle}', angle=angle)
                                      for angle in args['views']]}
                return {'success': True}
            provider._native_call = Mock(side_effect=native)
            self.assertEqual(provider.run(None, 'Sculpt', observation={}), 'completed')
            images = provider._turn.call_args.kwargs['references']
            self.assertEqual(len(images), 6)
            self.assertEqual(len({v['name'] for v in images}), 6)
            self.assertTrue(all(v['name'].startswith(('3:', '4:')) for v in images))
            self.assertEqual(captures, 4)
            context = json.loads(provider._turn.call_args.args[1].split('\n', 1)[1])
            self.assertEqual([v['id'] for v in context['visual_evidence']], [v['id'] for v in images])

    def test_review_refresh_preserves_comparison_targets_and_fixed_region_frame(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            baseline = observation(host, 'Small eye baseline', frame=camera())
            fixed = view(framing_reference=baseline['id'], region='Eye')
            review = {'tool': 'angelo_sculpt_review', 'arguments': {
                'object': 'Sphere', 'baseline': baseline['id'],
                'outline': [[.2, .2], [.8, .2], [.5, .8]], 'landmarks': [{'region': 'Eye', 'pixel': [.5, .5]}]}}
            provider = ClaudeProvider(host, {})
            provider._turn = Mock(side_effect=[
                {'reply': 'Compare then edit', 'operations': [view(), fixed, review, program()], 'verify': True},
                {'reply': 'Verified', 'operations': [], 'verify': False}])
            captures = 0
            def native(name, args, *, owner):
                self.assertIs(owner.controller, host)
                nonlocal captures
                if name in {'angelo_sculpt_view', 'angelo_sculpt_review'}:
                    captures += 1
                    return {'views': [observation(host, f'{captures}: {name}')], 'review': {'measurement': captures}}
                return {'success': True}
            provider._native_call = Mock(side_effect=native)
            self.assertEqual(provider.run(None, 'Sculpt', observation={}), 'completed')
            refreshed = [(call.args[0], call.args[1]) for call in provider._native_call.call_args_list[4:7]]
            self.assertIn(('angelo_sculpt_view', fixed['arguments']), refreshed)
            self.assertIn(('angelo_sculpt_review', review['arguments']), refreshed)
            self.assertEqual(len(provider._turn.call_args.kwargs['references']), 3)
            self.assertIn('"measurement":6', provider._turn.call_args.args[1])

    def test_over_budget_plan_is_rejected_before_its_first_mutation(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            provider = ClaudeProvider(host, {})
            provider._turn = Mock(return_value={'reply': 'Too many views', 'operations':
                [program()] + [view(region=f'Region {i}') for i in range(CLAUDE_SCULPT_IMAGE_LIMIT + 1)], 'verify': True})
            provider._native_call = Mock()
            with self.assertRaisesRegex(PlanRejected, "None of this plan's operations were run"):
                provider.run(None, 'Sculpt', observation={})
            provider._native_call.assert_not_called()
            self.assertEqual(provider._turn.call_count, 1 + CLAUDE_PLAN_REJECTIONS, 'returned for correction first')
            self.assertIn('has 12 of 12 left (6 of them for sculpt views or reviews, which are limited to 6); '
                          'this plan asks for 7 new ones', provider._turn.call_args.args[1])

    def test_limit_applies_across_rounds_without_discarding_previous_views(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            provider = ClaudeProvider(host, {})
            provider._turn = Mock(side_effect=[
                {'reply': 'Observe', 'operations': [view(region=f'Region {i}') for i in range(6)], 'verify': True},
                {'reply': 'Edit and observe more', 'operations': [program(), view(region='Seventh')], 'verify': True},
                {'reply': 'Six views are enough.', 'operations': [], 'verify': False}])
            def native(name, args, *, owner):
                self.assertIs(owner.controller, host)
                return {'views': [observation(host, args['region'])]} if name == 'angelo_sculpt_view' else {'success': True}
            provider._native_call = Mock(side_effect=native)
            self.assertEqual(provider.run(None, 'Sculpt', observation={}), 'completed',
                             'the plan over the budget is returned with what is left; the turn goes on')
            self.assertEqual(len(provider._turn.call_args_list[1].kwargs['references']), 6)
            correction = provider._turn.call_args.args[1]
            self.assertIn('this request keeps 6 distinct view images', correction)
            self.assertIn('has 6 of 12 left (0 of them for sculpt views or reviews, which are limited to 6); '
                          'this plan asks for 1 new ones', correction)
            self.assertIn('earlier views are kept', correction)
            self.assertNotIn('angelo_sculpt_program', [call.args[0] for call in provider._native_call.call_args_list])

    def test_overlapping_angles_and_equivalent_fixed_camera_ids_deduplicate(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            evidence = _ClaudeSculptEvidence(host.references)
            combined = view(('FRONT', 'RIGHT'))
            evidence.admit(combined, {'views': [observation(host, a, angle=a) for a in combined['arguments']['views']]})
            evidence.admit(view(), {'views': [observation(host, 'New front')]})
            self.assertEqual(len(evidence.requests), 2)
            for i in range(10):
                baseline = observation(host, f'Equivalent camera {i}', frame=camera())
                operation = view(framing_reference=baseline['id'])
                evidence.check_plan([operation])
                evidence.admit(operation, {'views': [observation(host, f'Fixed {i}', frame=camera())]})
            self.assertEqual(len(evidence.requests), 3)
            self.assertEqual(len(evidence.take_images()), 3)

    def test_all_six_comparison_baselines_survive_repeated_refreshes(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            evidence = _ClaudeSculptEvidence(host.references)
            operations = []
            for i in range(6):
                baseline = observation(host, f'Baseline {i}', frame=camera(i))
                operation = {'tool': 'angelo_sculpt_review', 'arguments': {
                    'object': 'Sphere', 'baseline': baseline['id'], 'outline': [], 'landmarks': []}}
                operations.append(operation)
                evidence.pin_cameras(operations)
                evidence.admit(operation, {'views': [observation(host, f'Current {i}', frame=camera(i))]})
            for round_index in range(4):
                evidence.take_images()
                evidence.invalidate()
                for i, operation in enumerate(evidence.refresh_operations()):
                    evidence.pin_cameras()
                    host.references.observation_camera(operation['arguments']['baseline'])
                    evidence.admit(operation, {'views': [observation(host, f'{round_index}: Current {i}', frame=camera(i))]})
                self.assertEqual(len(evidence.feedback), 6)
                self.assertFalse(evidence.dirty)
            for operation in operations:
                host.references.observation_camera(operation['arguments']['baseline'])
            self.assertEqual(len(host.references._observations), 12)

    def test_incomplete_image_result_stops_before_later_edits(self):
        with tempfile.TemporaryDirectory() as directory, closing(Controller(directory)) as host:
            provider = ClaudeProvider(host, {})
            provider._turn = Mock(return_value={'reply': 'Observe then edit',
                'operations': [view(('FRONT', 'RIGHT')), program()], 'verify': True})
            provider._native_call = Mock(return_value={'views': [observation(host, 'Only front')]})
            with self.assertRaisesRegex(RpcError, 'every requested image'):
                provider.run(None, 'Sculpt', observation={})
            self.assertEqual(provider._native_call.call_count, 1)


if __name__ == '__main__':
    unittest.main()
