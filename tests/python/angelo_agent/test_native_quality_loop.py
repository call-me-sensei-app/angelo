# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prospective within-turn quality protocol: common inputs, actual send/check paths, and unchanged bounds.

No Blender, native provider or paid call. The existing in-app running method is
executed with stand-ins; request construction, stamping and message validation
are the real host functions, not a duplicate implementation.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import bench_integrity
import bench_messages
import eval_live_tasks
import eval_parallel
import eval_quality_loop as quality
import eval_send_record
import oneshot_session
import scenario_specs
from test_native_bench_send_record import stand_in
from test_native_oneshot_session import Controller

HERE = Path(__file__).resolve().parent
CASES = ('oneshot-chess', 'oneshot-parlour', 'oneshot-roadster', 'oneshot-outfit', 'oneshot-town-centre')


def arm_tasks(name, parallel):
    with mock.patch.object(eval_parallel, 'ENABLED', parallel), \
            mock.patch.object(eval_parallel, 'on', return_value=parallel), \
            mock.patch.object(eval_parallel, 'install_ready_clock'):
        return eval_parallel.begin(eval_live_tasks.tasks_for(name))


class CommonRequestTests(unittest.TestCase):
    def test_original_brief_references_checks_and_raw_scenario_are_preserved(self):
        for name in CASES:
            raw = scenario_specs.tasks(scenario_specs.scenario_id(name))
            before = copy.deepcopy(raw)
            common = eval_live_tasks.tasks_for(name)
            with self.subTest(name=name):
                self.assertEqual(len(common), 1)
                self.assertEqual(raw, before)
                self.assertEqual(scenario_specs.tasks(scenario_specs.scenario_id(name)), before)
                self.assertEqual(common[0]['prompt'], raw[0]['prompt'] + '\n\n' + quality.DIRECTIVE)
                self.assertEqual(common[0]['quality_loop'], quality.metadata())
                self.assertEqual({k: v for k, v in common[0].items() if k not in ('prompt', 'quality_loop')},
                                 {k: v for k, v in raw[0].items() if k != 'prompt'})
        self.assertEqual(quality.DIRECTIVE_SHA256, hashlib.sha256(quality.DIRECTIVE.encode()).hexdigest())

    def test_actual_common_builder_precedes_both_arm_paths(self):
        for name in CASES:
            single, parallel = arm_tasks(name, False)[0], arm_tasks(name, True)[0]
            with self.subTest(name=name):
                self.assertEqual(parallel['prompt'], single['prompt'] + '\n\n' + eval_parallel.PARALLEL_REQUEST)
                self.assertEqual(parallel['references'], single['references'])
                self.assertEqual(parallel['quality_loop'], single['quality_loop'])
                self.assertNotIn('parallel_request', single)
                for arm, task in [('single', single), ('parallel', parallel)]:
                    stamp = bench_integrity.trial_stamp(task)
                    self.assertEqual(bench_messages.why_not(arm, [stamp], bench_messages.registered(name)[arm]), [])

    def test_non_oneshot_scenarios_are_unchanged(self):
        for name in scenario_specs.TASK_SETS[:3]:
            self.assertEqual(eval_live_tasks.tasks_for(name), scenario_specs.tasks(scenario_specs.scenario_id(name)))

    def test_duplicate_or_after_split_composition_is_refused_without_mutating_inputs(self):
        raw = scenario_specs.tasks('oneshot_chess')
        before = copy.deepcopy(raw)
        composed = quality.apply(raw)
        for task in [composed[0], dict(raw[0], parallel_request='already split')]:
            with self.assertRaisesRegex(ValueError, 'once.*before'):
                quality.apply([task])
        self.assertEqual(raw, before)


class SendEvidenceTests(unittest.TestCase):
    def test_actual_at_send_record_keeps_full_prompt_and_protocol_identity(self):
        for parallel in (False, True):
            task = arm_tasks('oneshot-town-centre', parallel)[0]
            trial = stand_in(task)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / eval_send_record.NAME
                with mock.patch.dict(os.environ, {eval_send_record.ENV: str(path)}):
                    eval_send_record.at_send(trial, task, 1)
                record = json.loads(path.read_text())['trials'][0]['unfinished_task']
            self.assertEqual(record['prompt_sha256'], bench_integrity.prompt_sha256(task['prompt']))
            self.assertEqual(record['quality_loop'], quality.metadata())
            self.assertEqual(record['test_code']['sha256'], bench_integrity.test_code()['sha256'])
            record['quality_loop']['version'] = 'changed copy'
            self.assertEqual(task['quality_loop'], quality.metadata())

    def test_missing_changed_duplicated_or_moved_directive_fails_the_existing_message_check(self):
        for name in CASES:
            raw = scenario_specs.tasks(scenario_specs.scenario_id(name))[0]['prompt']
            for arm, parallel in [('single', False), ('parallel', True)]:
                task = arm_tasks(name, parallel)[0]
                suffix = '\n\n' + eval_parallel.PARALLEL_REQUEST if parallel else ''
                variants = [raw + suffix, raw + '\n\n' + quality.DIRECTIVE.replace('Continue until', 'Stop before') + suffix,
                            task['prompt'] + '\n\n' + quality.DIRECTIVE,
                            quality.DIRECTIVE + '\n\n' + raw + suffix]
                for text in variants:
                    with self.subTest(name=name, arm=arm, text=text[-55:]):
                        stamp = bench_integrity.trial_stamp(dict(task, prompt=text))
                        self.assertTrue(bench_messages.why_not(arm, [stamp], bench_messages.registered(name)[arm]))

    def test_original_reference_files_reach_attach_and_only_one_user_send_is_allowed(self):
        for parallel in (False, True):
            task = arm_tasks('oneshot-town-centre', parallel)[0]
            with tempfile.TemporaryDirectory() as folder:
                files = []
                for i, view in enumerate(task['references']):
                    path = Path(folder) / ('ref%d.png' % i)
                    path.write_bytes(('unchanged image %d' % i).encode())
                    files.append((view, path, hashlib.sha256(path.read_bytes()).hexdigest()))
                controller, record = Controller(), {}
                controller.references.import_images = mock.Mock(side_effect=lambda paths, existing: [
                    {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths])
                session = oneshot_session.OneShotSession('oneshot-town-centre', Path(folder))
                with mock.patch.object(scenario_specs, 'reference_files', return_value=files):
                    session.attach(controller, task, record)
                controller.send(task['prompt'])
                self.assertEqual(controller.sent, [task['prompt']])
                self.assertEqual(record['references'], [{'view': v, 'sha256': h} for v, _, h in files])
                self.assertEqual([x['sha256'] for x in controller.attachments], [h for _, _, h in files])
                with self.assertRaisesRegex(RuntimeError, 'exactly one message'):
                    controller.send('Start an automatic quality retry')
                self.assertEqual(len(controller.sent), 1)


class BoundariesTests(unittest.TestCase):
    def test_actual_town_session_keeps_eighty_dollar_continuous_usage_cap_and_approvals(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {oneshot_session.COST_CAP_ENV: ''}):
            session = oneshot_session.OneShotSession('oneshot-town-centre', Path(folder))
            controller = Controller()
            session.controller = controller
            controller._request_metrics.usage = {'input_tokens': 1}
            self.assertEqual(session.cost_cap, 80)
            with mock.patch.object(session, 'price', side_effect=[(20, None), (79, None), (80, None), (90, None)]):
                for expected_stops in (0, 0, 1, 1):
                    session.watch()
                    self.assertEqual(controller.stops, expected_stops)
            self.assertEqual(session.state['stop']['cap_usd'], 80)
            self.assertEqual(session.decide('angelo_execute_python'), 'approve')
            for tool in ('angelo_delegate', 'angelo_studio_generate', 'angelo_write_file'):
                self.assertEqual(session.decide(tool), 'decline')

    def test_actual_running_method_never_resets_deadline_for_another_quality_iteration(self):
        tree = ast.parse((HERE / 'eval_live_agent_session.py').read_text())
        trial = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Trial')
        method = next(n for n in trial.body if isinstance(n, ast.FunctionDef) and n.name == 'on_running')
        clock, ending = mock.Mock(), NS(turn_ended=lambda c: False, stop=mock.Mock())
        env = {'time': clock, 'end': ending, 'STOP_GRACE': 30.0}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(HERE / 'eval_live_agent_session.py'), 'exec'), env)
        controller = Controller()
        state = NS(answer_approvals=mock.Mock(), grading=NS(observe=mock.Mock()), controller=controller,
                   message_index=0, sent=100.0, deadline=100.0 + 2654, phase='running')
        for now in (101, 1000, 2000, 2753):
            clock.monotonic.return_value = now
            self.assertEqual(env['on_running'](state), .05)
            self.assertEqual(state.deadline, 2754)
            ending.stop.assert_not_called()
        clock.monotonic.return_value = 2755
        env['on_running'](state)
        ending.stop.assert_called_once_with(state, 'timeout')
        self.assertEqual(state.phase, 'stopping')
        self.assertEqual(controller.sent, [])


if __name__ == '__main__':
    unittest.main()
