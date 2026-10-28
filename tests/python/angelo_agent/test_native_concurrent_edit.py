# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only adversarial contracts for the structural concurrency fixture; no Blender/model/process launch."""
import ast
import copy
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import concurrent_edit_oracle as oracle
import concurrent_edit_payload as payload
from concurrent_edit_runtime import BatchRuntime, remember_launch, reclaim_launches


class Vec(list):
    x = property(lambda s: s[0], lambda s, v: s.__setitem__(0, v))
    y = property(lambda s: s[1], lambda s, v: s.__setitem__(1, v))
    z = property(lambda s: s[2], lambda s, v: s.__setitem__(2, v))


class ID(dict):
    def __init__(self, name):
        super().__init__()
        self.name = name


class Nodes(dict):
    def __iter__(self):
        return iter(self.values())


class Material(ID):
    def __init__(self, name):
        super().__init__(name)
        self.use_nodes = True
        self.node_tree = NS(nodes=Nodes({'Principled BSDF': NS(type='BSDF_PRINCIPLED', inputs={
            'Base Color': NS(default_value=[0, 0, 0, 1])})}))


class Mesh(ID):
    def __init__(self, name):
        super().__init__(name)
        self.users, self.materials = 0, []

    def from_pydata(self, points, edges, faces):
        self.vertices = [NS(co=Vec(point)) for point in points]
        self.polygons = [NS(vertices=face) for face in faces]

    def update(self):
        pass


class Object(ID):
    def __init__(self, name, mesh):
        super().__init__(name)
        self.data, self.type, self.users_collection = mesh, 'MESH', []
        mesh.users += 1
        self.location, self.scale = (0, 0, 0), (1, 1, 1)

    location = property(lambda s: s._location, lambda s, v: setattr(s, '_location', Vec(v)))
    scale = property(lambda s: s._scale, lambda s, v: setattr(s, '_scale', Vec(v)))
    matrix_world = property(lambda s: [[s.scale[0], 0, 0, s.location[0]], [0, s.scale[1], 0, s.location[1]],
                                       [0, 0, s.scale[2], s.location[2]], [0, 0, 0, 1]])
    material_slots = property(lambda s: [NS(material=m) for m in s.data.materials])


class Collection(ID):
    def __init__(self, name):
        super().__init__(name)
        self.objects = NS(link=lambda obj: obj.users_collection.append(self))


class Store(list):
    def __init__(self, factory, objects=False):
        super().__init__()
        self.factory, self.is_objects = factory, objects

    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)

    def new(self, name, *args):
        original, suffix = name, 0
        while self.get(name) is not None:
            suffix += 1
            name = '%s.%03d' % (original, suffix)
        value = self.factory(name, *args)
        self.append(value)
        return value

    def remove(self, value, **kwargs):
        if self.is_objects:
            value.data.users -= 1
        self[:] = [item for item in self if item is not value]


def fake_bpy():
    objects = Store(Object, objects=True)
    return NS(data=NS(objects=objects, meshes=Store(Mesh), materials=Store(Material), collections=Store(Collection)),
              context=NS(scene=NS(objects=objects, collection=NS(children=NS(link=lambda group: None))),
                         view_layer=NS(update=lambda: None)))


def execute(bpy, code):
    namespace = {}
    with mock.patch.dict(sys.modules, {'bpy': bpy}):
        exec(compile(code, '<fixed QA mutation>', 'exec'), namespace)
    return namespace['result']


class OracleTests(unittest.TestCase):
    def test_frozen_case_counts_and_independent_goldens(self):
        self.assertEqual(len(oracle.expected('create')), 3074)
        rows = {row['id']: row for row in oracle.expected(placed=True)}
        self.assertEqual(len(rows), 2818)
        self.assertNotIn('worker:1408', rows)
        self.assertEqual(rows['worker:1407']['matrix'], [[1.25, 0, 0, 94], [0, .75, 0, 54],
                                                       [0, 0, 1.5, 10.25], [0, 0, 0, 1]])
        self.assertEqual(rows['lead:1400']['materials'][0]['rgba'], [.2, .7, .3, 1])
        self.assertEqual(rows['protected:0']['name'], 'QA-worker-0001')
        self.assertEqual([sum(len(job['indices']) for actor in oracle.ACTORS for job in oracle.batches(actor, phase))
                          for phase in oracle.PHASES], [3072, 1024, 616, 256])

    def test_loss_overwrite_material_geometry_identity_and_nonfinite_corruption_are_rejected(self):
        wanted = oracle.expected(placed=True)
        for mutation in ('missing', 'duplicate', 'extra', 'transform', 'paint', 'geometry', 'sentinel', 'nan', 'unidentified'):
            actual = copy.deepcopy(wanted)
            if mutation == 'missing': actual.pop()
            elif mutation == 'duplicate': actual.append(copy.deepcopy(actual[0]))
            elif mutation == 'extra': actual.append(dict(actual[0], id='unexpected:1'))
            elif mutation == 'transform': actual[0]['matrix'][0][3] += 1
            elif mutation == 'paint': actual[0]['materials'][0]['rgba'][0] += .2
            elif mutation == 'geometry': actual[0]['geometry'] = 'wrong cube topology'
            elif mutation == 'sentinel': next(r for r in actual if r['id'] == 'protected:0')['name'] = 'overwritten'
            elif mutation == 'nan': actual[0]['matrix'][0][3] = float('nan')
            else: actual[0]['id'] = ['malformed identity']
            with self.subTest(mutation=mutation):
                self.assertTrue(oracle.compare(wanted, actual))

    def test_actual_generated_mutations_match_independent_oracle_at_every_phase(self):
        worlds = {'lead': fake_bpy(), 'worker': fake_bpy()}
        for actor, world in worlds.items():
            execute(world, payload.setup(actor == 'lead'))
        for phase in oracle.PHASES:
            for actor, world in worlds.items():
                for job in oracle.batches(actor, phase):
                    receipt = execute(world, payload.script(job))
                    self.assertEqual(receipt['mutated'], len(job['indices']))
                    self.assertEqual(receipt['actor'], actor)
                self.assertEqual(oracle.compare(oracle.expected(phase, (actor,), protected=actor == 'lead'),
                                                oracle.observe(world)), [], (actor, phase))
        for job in oracle.batches('worker', 'place'):
            execute(worlds['worker'], payload.script(job))
        combined = oracle.observe(worlds['lead']) + oracle.observe(worlds['worker'])
        self.assertEqual(oracle.compare(oracle.expected(placed=True), combined), [])
        self.assertEqual(len(worlds['lead'].data.meshes) + len(worlds['worker'].data.meshes), 2818)

        # Matched serial control in one document: the same protected name/material
        # collisions and final oracle, with no worker or merge result to trust.
        serial = fake_bpy()
        execute(serial, payload.setup(True))
        for phase in oracle.PHASES:
            for actor in oracle.ACTORS:
                for job in oracle.batches(actor, phase):
                    execute(serial, payload.script(job))
            self.assertEqual(oracle.compare(oracle.expected(phase), oracle.observe(serial)), [])
        for job in oracle.batches('worker', 'place'):
            execute(serial, payload.script(job))
        self.assertEqual(oracle.compare(oracle.expected(placed=True), oracle.observe(serial)), [])
        self.assertEqual(next(o for o in serial.data.objects if o.get('qa_entity_id') == 'protected:0').name,
                         'QA-worker-0001')
        self.assertNotEqual(next(o for o in serial.data.objects if o.get('qa_entity_id') == 'worker:0001').name,
                            'QA-worker-0001')

    def test_payload_bounds_have_no_sleep_wait_or_busy_loop(self):
        job = oracle.batches('lead', 'create')[0]
        for invalid in (dict(job, indices=list(range(65))), dict(job, indices=[]), dict(job, indices=[0, 0]),
                        dict(job, indices=[1536]), dict(job, actor='foreign')):
            with self.assertRaises(ValueError): payload.script(invalid)
        for phase in oracle.PHASES + ('place',):
            tree = ast.parse(payload.script(oracle.batches('lead', phase)[0]))
            self.assertFalse(any(isinstance(n, ast.While) for n in ast.walk(tree)))
            self.assertFalse(any(isinstance(n, ast.Call) and getattr(n.func, 'attr', '') in ('sleep', 'wait')
                                 for n in ast.walk(tree)))


class TemporalTests(unittest.TestCase):
    def fixture(self, overlap=True):
        jobs = [oracle.batches(actor, 'create')[0] for actor in oracle.ACTORS]
        starts = [10, 11 if overlap else 201]
        events = [dict(job, admitted_ns=1, start_ns=starts[i], end_ns=starts[i] + 128,
                       answered_ns=400, pid=i + 101, mutated=64, main_thread=True,
                       mutation_spans=[[index, starts[i] + offset * 2, starts[i] + offset * 2 + 2]
                                       for offset, index in enumerate(job['indices'])])
                  for i, job in enumerate(jobs)]
        return jobs, events

    def test_actual_intervals_not_waits_or_pending_requests_decide_overlap(self):
        jobs, events = self.fixture()
        proof = oracle.temporal_proof(events, jobs, {'lead': 101, 'worker': 102}, parallel=True)
        self.assertTrue(proof['complete'])
        self.assertEqual(proof['overlap_pairs'][0]['overlap_ns'], 127)
        jobs, events = self.fixture(False)
        self.assertFalse(oracle.temporal_proof(events, jobs, {'lead': 101, 'worker': 102}, parallel=True)['complete'])
        self.assertTrue(oracle.temporal_proof(events, jobs, {'lead': 101, 'worker': 102}, parallel=False)['complete'])

    def test_missing_duplicate_wrong_process_owner_count_or_clock_never_pass(self):
        for mutation in ('missing', 'duplicate', 'pid', 'actor', 'indices', 'count', 'clock', 'thread', 'no_spans', 'span_outside'):
            jobs, events = self.fixture()
            if mutation == 'missing': events.pop()
            elif mutation == 'duplicate': events.append(dict(events[0]))
            elif mutation == 'pid': events[1]['pid'] = 101
            elif mutation == 'actor': events[1]['actor'] = 'lead'
            elif mutation == 'indices': events[1]['indices'] = [999]
            elif mutation == 'count': events[1]['mutated'] = 0
            elif mutation == 'clock': events[1]['end_ns'] = 999
            elif mutation == 'thread': events[1]['main_thread'] = False
            elif mutation == 'no_spans': events[1].pop('mutation_spans')
            else: events[1]['mutation_spans'][0][2] = 999
            with self.subTest(mutation=mutation):
                self.assertFalse(oracle.temporal_proof(events, jobs, {'lead': 101, 'worker': 102}, parallel=True)['complete'])


class RuntimeTests(unittest.TestCase):
    def test_actual_fixed_plan_advances_only_after_answer_and_preserves_raw_evidence(self):
        calls = []
        first = threading.Event()
        def request(call_id, tool, arguments, answer):
            calls.append((call_id, tool, arguments, answer))
            first.set()
        jobs = oracle.batches('lead', 'create')[:2]
        runtime = BatchRuntime(NS(request_tool=request), jobs, payload.script, time.monotonic() + 3).start()
        try:
            self.assertTrue(first.wait(1))
            self.assertEqual(len(calls), 1)
            calls[0][3]({'success': True, 'result': {'job_id': jobs[0]['job_id']}})
            deadline = time.monotonic() + 1
            while len(calls) < 2 and time.monotonic() < deadline: time.sleep(.001)
            self.assertEqual(len(calls), 2)
            calls[1][3]({'success': True, 'result': {'job_id': jobs[1]['job_id']}})
            self.assertTrue(runtime.finished.wait(1))
            self.assertEqual(runtime.errors, [])
            self.assertEqual(len(runtime.receipts), 2)
            self.assertTrue(all(row['admitted_ns'] <= row['answered_ns'] for row in runtime.receipts))
        finally:
            runtime.cancel()
            self.assertTrue(runtime.join())

    def test_failed_or_cancelled_batch_admits_no_later_mutation(self):
        for failure in (True, False):
            calls = []
            def request(call_id, tool, arguments, answer):
                calls.append(call_id)
                if failure: answer({'success': False, 'error': 'fixture failure'})
            runtime = BatchRuntime(NS(request_tool=request), oracle.batches('lead', 'create')[:2], payload.script,
                                   time.monotonic() + 2).start()
            deadline = time.monotonic() + 1
            while not calls and time.monotonic() < deadline: time.sleep(.001)
            if not failure: runtime.cancel()
            self.assertTrue(runtime.finished.wait(1))
            self.assertTrue(runtime.join())
            self.assertEqual(len(calls), 1)
            self.assertTrue(runtime.errors)

    def test_after_spawn_ready_failure_keeps_and_reclaims_only_its_provisional_child(self):
        state = {'returncode': None}
        process = NS(pid=12345, poll=lambda: state['returncode'])
        worker = NS(process=process, stop=mock.Mock(side_effect=lambda: state.update(returncode=-15)))
        unrelated = NS(process=NS(pid=12346), stop=mock.Mock())
        retained = []
        with self.assertRaisesRegex(RuntimeError, 'hello failed'):
            remember_launch(lambda: worker, retained)
            raise RuntimeError('hello failed before controller adoption')
        self.assertEqual(retained, [worker])
        result = reclaim_launches(retained, attempted=True)
        self.assertTrue(result['complete'])
        self.assertEqual(result['workers'], [{'pid': 12345, 'reaped': True}])
        worker.stop.assert_called_once_with()
        unrelated.stop.assert_not_called()

    def test_unknown_startup_handle_or_failed_cleanup_never_reports_reaped(self):
        self.assertTrue(reclaim_launches([], attempted=False)['complete'])
        unknown = reclaim_launches([], attempted=True)
        self.assertFalse(unknown['complete'])
        self.assertTrue(unknown['unknown_startup_ownership'])
        missing = reclaim_launches([NS(process=None)], attempted=True)
        self.assertFalse(missing['complete'])
        self.assertIsNone(missing['workers'][0]['reaped'])
        stuck = NS(process=NS(pid=12345, poll=lambda: None), stop=mock.Mock(side_effect=RuntimeError('stop failed')))
        result = reclaim_launches([stuck], attempted=True)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['workers'][0]['reaped'])

    def test_native_fixture_uses_existing_owners_and_declares_no_performance_gate(self):
        source = (Path(__file__).parent / 'test_blender_concurrent_edit.py').read_text()
        ast.parse(source)
        for call in ('pump_controller_tools(', "bpy.ops.angelo.approve(", 'controller.request_tool(', "bpy.ops.ed.undo()", "bpy.ops.ed.redo()", 'bpy.ops.wm.open_mainfile('):
            self.assertIn(call, source)
        self.assertIn("'performance_gate': None", source)
        self.assertNotIn('subprocess.', source)
        import native_suite_manifest
        suite = next(s for s in native_suite_manifest.SUITES if s.name == 'test_blender_concurrent_edit')
        self.assertEqual((suite.kind, suite.timeout, suite.serial), (native_suite_manifest.BACKGROUND, 900, True))
        self.assertEqual(suite.live_env, ())


if __name__ == '__main__':
    unittest.main()
