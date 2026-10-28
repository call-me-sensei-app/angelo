# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed-plan stand-in provider for the concurrent edit native fixture; no model/network.

Only Controller.request_tool is called on this thread, as from a real provider.
Each next batch waits for its predecessor's actual answer; errors stop admission.
Blender access stays entirely inside the existing native executor/worker owners.
"""
import hashlib
import threading
import time
import uuid


class BatchRuntime:
    def __init__(self, controller, jobs, make_script, deadline):
        self.controller, self.jobs, self.make_script, self.deadline = controller, list(jobs), make_script, deadline
        self.receipts, self.errors = [], []
        self.cancelled, self.finished = threading.Event(), threading.Event()
        self._answer, self._lock = None, threading.Lock()
        self._prefix = 'concurrent-qa-' + uuid.uuid4().hex
        self.thread = threading.Thread(target=self._run, name='ConcurrentEditQA', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            if self._answer is not None:
                self._answer.set()

    def join(self, timeout=1):
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def _run(self):
        try:
            for index, job in enumerate(self.jobs):
                if self.cancelled.is_set() or time.monotonic() >= self.deadline:
                    raise RuntimeError('Batch admission cancelled or deadline reached')
                code = self.make_script(job)
                done, reply = threading.Event(), []
                receipt = {'job': dict(job), 'call_id': self._prefix + '-' + str(index),
                           'script_sha256': hashlib.sha256(code.encode()).hexdigest(), 'admitted_ns': time.monotonic_ns()}
                self.receipts.append(receipt)
                with self._lock:
                    self._answer = done

                def answered(value, receipt=receipt, reply=reply, done=done):
                    with self._lock:
                        if reply:
                            self.errors.append('A batch answered more than once')
                            return
                        reply.append(value)
                        receipt.update(answered_ns=time.monotonic_ns(), tool_result=value)
                    done.set()
                self.controller.request_tool(receipt['call_id'], 'angelo_execute_python',
                                             {'description': 'QA bounded ' + job['job_id'], 'code': code}, answered)
                if not done.wait(max(0, min(120, self.deadline - time.monotonic()))) or self.cancelled.is_set():
                    raise RuntimeError('Batch did not finish before its bound')
                value = reply[0]
                if not isinstance(value, dict) or value.get('success') is not True or value.get('error'):
                    raise RuntimeError('Native batch failed; no further batch admitted')
        except Exception as error:
            self.errors.append('%s: %s' % (type(error).__name__, error))
        finally:
            self.finished.set()

    def events(self):
        with self._lock:
            return [dict(row['tool_result']['result'], admitted_ns=row['admitted_ns'], answered_ns=row['answered_ns'])
                    for row in self.receipts if isinstance(row.get('tool_result'), dict)
                    and isinstance(row['tool_result'].get('result'), dict)]


class HostedBatchRuntime:
    """``BatchRuntime`` for a worker chat hosted in its worker (design A′, ``hosted_worker_chat``): the same fixed
    plan, played as ONE model turn by the stand-in runtime: each batch is its next tool call, sent only once the previous
    one is answered, and an error stops the rest. The worker's Controller admits and runs them on its own main
    thread. ``admitted_ns``/``answered_ns`` are the model's send and answer times (the same monotonic clock); the
    batch's own clocks come from its result in the chat's journal, as the lead's mirror applied it."""

    def __init__(self, chat, jobs, make_script, deadline):
        self.chat, self.jobs, self.make_script, self.deadline = chat, list(jobs), make_script, deadline
        self.receipts, self.errors = [], []
        self.cancelled, self.finished = threading.Event(), threading.Event()
        self._prefix = 'concurrent-qa-' + uuid.uuid4().hex
        self.thread = threading.Thread(target=self._watch, name='ConcurrentEditHostedQA', daemon=True)

    def start(self):
        steps = []
        for index, job in enumerate(self.jobs):
            code = self.make_script(job)
            receipt = {'job': dict(job), 'call_id': self._prefix + '-' + str(index),
                       'script_sha256': hashlib.sha256(code.encode()).hexdigest()}
            self.receipts.append(receipt)
            steps.append({'call': {'id': receipt['call_id'], 'tool': 'angelo_execute_python',
                                   'arguments': {'description': 'QA bounded ' + job['job_id'], 'code': code}},
                          'stop_if_failed': True})
        self.chat.say('Run the fixed QA batches.', steps)
        self.thread.start()
        return self

    def cancel(self):
        self.cancelled.set()

    def join(self, timeout=1):
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def _rows(self):
        wanted = {receipt['call_id'] for receipt in self.receipts}
        return {row['id']: row for row in self.chat.model_log('tool_result') if row.get('id') in wanted}

    def _watch(self):
        """Off the main thread: only the stand-in's log file is read here (never bpy, never the mirror)."""
        try:
            while not self.cancelled.is_set() and time.monotonic() < self.deadline:
                rows = self._rows()
                failed = [row for row in rows.values() if row.get('success') is not True]
                if failed:
                    raise RuntimeError('Native batch failed; no further batch admitted: %s' % failed[0].get('text'))
                if len(rows) == len(self.receipts):
                    for receipt in self.receipts:
                        row = rows[receipt['call_id']]
                        receipt.update(admitted_ns=row['sent_ns'], answered_ns=row['answered_ns'])
                    return
                time.sleep(0.01)
            raise RuntimeError('Batch admission cancelled or deadline reached')
        except Exception as error:
            self.errors.append('%s: %s' % (type(error).__name__, error))
        finally:
            self.finished.set()

    def events(self, timeout=30.0):
        """Main thread, once finished: each batch's result from the chat's journal (the mirror pumped until all are
        there), with the model's clocks."""
        until = time.monotonic() + timeout
        results = {}
        while time.monotonic() < until:
            self.chat.pump()
            results = {receipt['call_id']: self.chat.result_of(receipt['call_id']) for receipt in self.receipts}
            if all(isinstance(value, dict) for value in results.values()):
                break
        events = []
        for receipt in self.receipts:
            value = results.get(receipt['call_id'])
            receipt['tool_result'] = value
            if isinstance(value, dict) and isinstance(value.get('result'), dict) and 'answered_ns' in receipt:
                events.append(dict(value['result'], admitted_ns=receipt['admitted_ns'],
                                   answered_ns=receipt['answered_ns']))
        return events


def remember_launch(factory, owned, *args, **kwargs):
    """Keep the existing WorkerProcess owner before ready/hello/adopt can fail."""
    worker = factory(*args, **kwargs)
    owned.append(worker)
    return worker


def reclaim_launches(owned, attempted):
    """Reclaim only retained WorkerProcess objects; absence of a handle is not reap proof.

    No PID scan or generic signal is used. The product owner performs its own
    bounded stop. Startup errors still fail the fixture regardless of cleanup.
    """
    rows = []
    for worker in owned:
        process = getattr(worker, 'process', None)
        row = {'pid': getattr(process, 'pid', None), 'reaped': None}
        try:
            if process is not None:
                if process.poll() is None:
                    worker.stop()
                row['reaped'] = process.poll() is not None
            else:
                row['reason'] = 'No retained process handle; cleanup not established'
        except Exception as error:
            row['reason'] = type(error).__name__ + ': retained worker cleanup not established'
        rows.append(row)
    complete = all(row['reaped'] is True for row in rows) if owned else not attempted
    return {'complete': complete, 'attempted': attempted, 'workers': rows,
            'unknown_startup_ownership': attempted and not owned}
