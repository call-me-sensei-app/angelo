# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No-model 3,072-object concurrent edit correctness fixture; native execution requires coordinator GO.

Real lead/worker Controllers and approval operators execute fixed <=64-object
batches. A simulated provider advances only on actual tool answers. Independent
RNA manifests verify four phases, isolation, protected objects, merge Undo/Redo,
and save/reopen. No sleep/barrier is inserted into mutation code. Lack of actual
cross-process mutation overlap is INCOMPLETE, never a pass. Headless pump timings
are raw instrumentation, not UI latency or an approved performance gate.

Use run_native_suites.py with this exact suite; it isolates credentials, checks
the bundled add-on and enforces a 900-second root-process deadline. A separate
reviewed owner-marker supervisor/live handshake is required to cover escaped workers. Its optional
ANGELO_CONCURRENT_EDIT_MODE is parallel (default) or serial; one process/run per
mode. Failure/isolation fault injection remains in the existing worker suites.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from unittest.mock import patch

import bpy

assert '--factory-startup' in sys.argv and bpy.app.background, 'Disposable background factory startup only'
ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'scripts/addons_core'), str(HERE)]
import concurrent_edit_oracle as oracle
import concurrent_edit_payload as payload
from concurrent_edit_runtime import BatchRuntime, HostedBatchRuntime, remember_launch, reclaim_launches

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def serve_worker():
    """Existing WorkerHost QA-probe pattern: a read-only full manifest, no edit/authority path changes."""
    from angelo_agent import worker_host, worker_main
    original = worker_host.WorkerHost.handle

    def handle(host, request):
        if request.get('op') == 'qa_concurrent_manifest':
            return {'value': {'pid': os.getpid(), 'objects': oracle.observe(host.bpy),
                              'revision': host.revision, 'filepath': host.bpy.data.filepath}}
        return original(host, request)
    worker_host.WorkerHost.handle = handle
    worker_main.main(ARGS[1:])


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, separators=(',', ':'), allow_nan=False)
        stream.write('\n')


def run():
    mode = os.environ.get('ANGELO_CONCURRENT_EDIT_MODE', 'parallel')
    if mode not in ('serial', 'parallel'):
        raise ValueError('Mode must be serial or parallel')
    out = OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
    artifacts = out / 'concurrent-edit'
    artifacts.mkdir(exist_ok=False)
    os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_KEYCHAIN='1', ANGELO_AGENT_RUNTIME='off',
                      ANGELO_CONNECTIONS_DIR=str(out / 'accounts'), ANGELO_STATE_DIR=str(out / 'state'),
                      ANGELO_ENABLE_PARALLEL_AGENTS='1', ANGELO_PARALLEL_WORKER_CAP='1')
    assert all(Path(os.environ[name]).resolve().is_relative_to(OUT)
               for name in ('ANGELO_STATE_DIR', 'ANGELO_CONNECTIONS_DIR')), 'Isolated QA state only'
    from angelo_agent import ui, worker_routing, worker_sessions, worker_dispatch
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools
    from angelo_agent.headless_main import _register
    from parallel_workers_qa import no_worker_done_event, worker_chat
    from hosted_worker_chat import counted_at_once, hosted, pump_lead
    hosted = hosted()   # Design A′: the worker's batches are its hosted chat's model turn (HostedBatchRuntime).
    import native_addon_guard
    import bench_integrity

    report = {'schema': 1, 'case': oracle.VERSION, 'mode': mode, 'paid_requests': 0, 'checks': {},
              'variant': 'hosted' if hosted else 'lanes',
              'binary': bpy.app.binary_path, 'build': bpy.app.build_hash.decode(), 'platform': sys.platform,
              'clock': vars(time.get_clock_info('monotonic')), 'timing_scope': 'instrumented headless correctness only',
              'ui_latency_measured': False, 'performance_gate': None, 'phases': [], 'raw_receipts': []}
    report['source_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        Path(__file__), HERE / 'concurrent_edit_oracle.py', HERE / 'concurrent_edit_payload.py',
        HERE / 'concurrent_edit_runtime.py')}
    runtimes, worker, lead, fence = [], None, None, None
    provisional_workers, startup_attempted = [], False
    started = time.monotonic()
    deadline = started + 840  # Runner root deadline is 900 s; outer owner supervisor covers separate worker sessions.

    def check(name, condition, detail=None):
        report['checks'][name] = bool(condition)
        if not condition:
            report.setdefault('failures', {})[name] = detail
            raise AssertionError(name)

    def readback(label, actual, wanted):
        write(artifacts / (label + '.json'), actual)
        errors = oracle.compare(wanted, actual)
        check(label, not errors, errors[:12])

    def apply(target, call):
        check('only_fixed_mutation_tools', call.name in ('angelo_execute_python', 'angelo_workers_merge'))
        if call.name == 'angelo_execute_python':
            target.review_script(call)  # Exact fixed-code consent, as the existing Worker Python fixture.
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state['id'])

    def pump():
        chats = [lead] + ([worker.controller] if worker is not None and not hosted else [])
        for chat in chats:
            chat.pump_events()
        if worker is not None and hosted:
            pump_lead([worker.controller])   # Its mirror's frames, as the window's timer applies them.
        before = time.monotonic_ns()
        pump_controller_tools(chats, 0, BlenderExecutor(bpy.context.window_manager.windows[0]), ui.document_key(),
                              apply, budget=ToolDispatchBudget())
        report.setdefault('pump_wall_ns', []).append(time.monotonic_ns() - before)

    def call(controller, tool, arguments):
        answers = []
        controller.request_tool('fixed-qa-%d' % time.monotonic_ns(), tool, arguments, answers.append)
        until = min(deadline, time.monotonic() + 120)
        while not answers and time.monotonic() < until:
            pump()
            time.sleep(.001)  # Host/UI scheduling only, never inside a tool's measured mutation interval.
        check('tool_answered_' + tool + '_' + str(len(report['raw_receipts'])), len(answers) == 1, tool)
        report['raw_receipts'].append({'tool': tool, 'result': answers[0]})
        check('tool_succeeded_' + str(len(report['raw_receipts'])), answers[0].get('success') is True, answers[0])
        return answers[0]

    def phase_run(jobs_by_actor):
        created = [(HostedBatchRuntime(worker, jobs, payload.script, deadline) if hosted and worker is not None
                    and controller is worker.controller else BatchRuntime(controller, jobs, payload.script, deadline))
                   .start() for controller, jobs in jobs_by_actor]
        runtimes.extend(created)
        until = min(deadline, time.monotonic() + 180)
        while not all(runtime.finished.is_set() for runtime in created) and time.monotonic() < until:
            pump()
            time.sleep(.001)
        for runtime in created:
            if not runtime.finished.is_set():
                runtime.cancel()
            check('runtime_joined_' + str(len(report['raw_receipts'])), runtime.join(), runtime.errors)
            report['raw_receipts'].extend(runtime.receipts)
            check('runtime_succeeded_' + str(len(report['raw_receipts'])), not runtime.errors, runtime.errors)
        return [event for runtime in created for event in runtime.events()]

    try:
        guard = native_addon_guard.check(bpy.app.binary_path, ROOT)
        check('installed_addon_matches_source', guard['matches'], guard.get('differing'))
        report['addon_source_digest'] = guard['source']['digest']
        report['test_code'] = bench_integrity.test_code(ROOT)
        with open(bpy.app.binary_path, 'rb') as binary:
            report['binary_sha256'] = hashlib.file_digest(binary, 'sha256').hexdigest()
        report['provider_runtime'] = 'off; fixed in-process stand-in only'
        wanted = {stage: {'lead': oracle.expected(stage, ('lead',)),
                          'worker': oracle.expected(stage, ('worker',), protected=False),
                          'both': oracle.expected(stage)} for stage in oracle.PHASES}
        final = oracle.expected(placed=True)
        frozen = {'case': oracle.VERSION, 'initial_objects': 3072, 'protected': 2, 'final_objects': len(final),
                  'batch_limit': 64, 'tolerance': oracle.TOLERANCE, 'final': final,
                  'checkpoint_hashes': {stage: {actor: oracle.digest(rows) for actor, rows in manifests.items()}
                                        for stage, manifests in wanted.items()}}
        write(artifacts / 'expected.json', frozen)  # Before any fixture mutation.
        report['expected_semantic_sha256'] = oracle.digest(frozen)
        report['expected_file_sha256'] = hashlib.sha256((artifacts / 'expected.json').read_bytes()).hexdigest()
        _register(bpy, ui)
        no_worker_done_event()  # No provider request can be scheduled when this hand-driven fixture ends a part.
        ui.prepare_controller = lambda controller=None: None
        bpy.context.preferences.edit.use_global_undo = True
        lead = ui.get_controller()
        lead.state['options'] = dict(lead.state.get('options') or {}, approval='AUTO_BOUNDED')
        lead.set_document(ui.document_key())
        fence = lead.open_external_read()
        lead.set_trusted_scripts(True)  # Fixed isolated fixture only; native consent tests cover the gate separately.
        if mode == 'parallel':
            start, launch = worker_routing.start, worker_routing.launch

            def probed(controller, part, **options):
                return start(controller, part, **dict(options, script=Path(__file__).resolve()))
            def tracked_launch(*args, **kwargs):
                return remember_launch(launch, provisional_workers, *args, **kwargs)

            startup_attempted = True
            with patch.object(worker_routing, 'start', probed), patch.object(worker_routing, 'launch', tracked_launch):
                worker = worker_chat(out / 'state', out / 'parts' / 'partition.blend', ROOT)
            worker.controller.set_trusted_scripts(True)
            prepare = {'description': 'Prepare isolated worker', 'code': payload.setup(False)}
            if hosted:   # The model's call in the worker's own turn.
                prepared = worker.call('angelo_execute_python', prepare)
                check('hosted_worker_prepared', prepared.get('success') is True, prepared)
            else:
                call(worker.controller, 'angelo_execute_python', prepare)
        call(lead, 'angelo_execute_python', {'description': 'Prepare protected lead scene', 'code': payload.setup(True)})
        readback('protected-initial', oracle.observe(bpy), oracle.expected(actors=()))
        pids = {'lead': os.getpid(), 'worker': worker.worker.process.pid if worker else os.getpid()}
        report['processes'] = pids
        events, jobs = [], []
        for phase in oracle.PHASES:
            phase_started = time.monotonic()
            lead_jobs, worker_jobs = oracle.batches('lead', phase), oracle.batches('worker', phase)
            jobs.extend(lead_jobs + worker_jobs)
            if worker:
                events.extend(phase_run([(worker.controller, worker_jobs), (lead, lead_jobs)]))
            else:
                events.extend(phase_run([(lead, lead_jobs)]))
                events.extend(phase_run([(lead, worker_jobs)]))
            report['phases'].append({'phase': phase, 'wall_s': time.monotonic() - phase_started})
            readback(phase + '-lead', oracle.observe(bpy), wanted[phase]['lead' if worker else 'both'])
            if worker:
                observed = worker.executor.ask('qa_concurrent_manifest')['value']
                check('worker_identity_' + phase, observed['pid'] == pids['worker'])
                readback(phase + '-worker', observed['objects'], wanted[phase]['worker'])
        report['temporal'] = oracle.temporal_proof(events, jobs, pids, parallel=bool(worker))
        write(artifacts / 'execution-events.json', events)
        if worker:
            if not hosted:   # A hosted chat's own turns ended with the stand-in's replies.
                worker.controller.close_external_read(worker.fence)
                worker.fence = None
                worker.controller.emit('message', {'role': 'user', 'text': 'Fixed QA partition complete.'})
                worker.controller.pump_events()
            else:
                worker.wait_idle()
            worker_sessions._parts[worker.session] = (1, 'QA Worker Part', Path(worker.worker.part))
            worker_sessions._started[lead.state['id']] = [worker.session]
            bpy.ops.ed.undo_push(message='Before controlled concurrent merge')
            if hosted:   # Its worker reported its count with its save: the merge is not refused as being checked.
                check('hosted_part_counted_at_its_save', counted_at_once())
            merge_started = time.monotonic()
            merged = call(lead, 'angelo_workers_merge', {'parts': [{'worker': 1, 'placements': [
                {'location': list(oracle.PLACEMENT)}]}]})
            report['merge_wall_s'] = time.monotonic() - merge_started
            check('merge_appended_expected_worker_count', merged['merged'][0]['objects'] == oracle.SURVIVORS, merged)
            # ``ended`` is the connection closing, a moment before the process exits: wait for the exit in the same bound.
            reap_by = time.monotonic() + 30
            ended = worker.worker.ended.wait(30)
            while ended and worker.worker.process.poll() is None and time.monotonic() < reap_by:
                time.sleep(.01)
            check('worker_reaped_after_merge', ended and worker.worker.process.poll() is not None)
            readback('merged', oracle.observe(bpy), final)
            lead.close_external_read(fence)
            fence = None
            check('merge_undo_supported', bpy.ops.ed.undo() == {'FINISHED'})
            readback('merge-undone', oracle.observe(bpy), wanted['delete']['lead'])
            check('merge_redo_supported', bpy.ops.ed.redo() == {'FINISHED'})
            readback('merge-redone', oracle.observe(bpy), final)
        else:
            phase_run([(lead, oracle.batches('worker', 'place'))])
            readback('serial-placed', oracle.observe(bpy), final)
            lead.close_external_read(fence)
            fence = None
            report['undo_scope'] = 'serial control: no merge operation; script Undo not asserted'
        path = artifacts / 'final.blend'
        save_started = time.monotonic()
        check('save_finished', bpy.ops.wm.save_as_mainfile(filepath=str(path)) == {'FINISHED'})
        check('reopen_finished', bpy.ops.wm.open_mainfile(filepath=str(path)) == {'FINISHED'})
        report['save_reopen_wall_s'] = time.monotonic() - save_started
        readback('reopened', oracle.observe(bpy), final)
        check('saved_scene_local_only', not bpy.data.libraries)
        report['status'] = 'complete' if report['temporal']['complete'] else 'incomplete'
        report['success'] = report['temporal']['complete']
        report['incomplete_reason'] = None if report['success'] else report['temporal']['reason']
    except Exception:
        report.update(status='failed', success=False, error=traceback.format_exc())
    finally:
        for runtime in runtimes:
            runtime.cancel()
        try:
            if lead is not None and fence is not None:
                lead.close_external_read(fence)
            worker_routing.stop_all()
            worker_dispatch.close_all()
        except Exception:
            report.update(success=False, status='failed', cleanup_error=traceback.format_exc())
        # Runs even if normal routing cleanup failed: ready/hello may have failed before adopt registered a child.
        report['startup_cleanup'] = reclaim_launches(provisional_workers, startup_attempted)
        report['worker_process_reaped'] = True if report['startup_cleanup']['complete'] else None
        report['runtime_threads_reaped'] = all([runtime.join() for runtime in runtimes])
        if not report['startup_cleanup']['complete'] or not report['runtime_threads_reaped']:
            report.update(success=False, status='failed', cleanup_error='Owned process/thread cleanup not established')
        report['total_wall_s'] = time.monotonic() - started
        report['worker_dispatch_stats'] = dict(worker_dispatch.STATS)
        write(out / 'concurrent-edit.json', report)
        print('CONCURRENT-EDIT-DONE', report.get('success'), flush=True)
    return 0 if report.get('success') else 2 if report.get('status') == 'incomplete' else 1


if ARGS and ARGS[0] == '--worker':
    serve_worker()
else:
    code = run()
    if code:
        os._exit(code)  # Receipt written and owned workers stopped; do not mask a failure with Blender's exit.
