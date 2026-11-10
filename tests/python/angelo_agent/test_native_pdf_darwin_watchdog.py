# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mocked Darwin accounting/identity, real output facade and actual worker admission: no native calls/processes."""
import suite_isolation  # noqa: F401
import io
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import pdf_darwin_watchdog as policy, pdf_process_output as output, pdf_text, pdf_text_worker as worker


class Pipe(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.sent = b""
    def write(self, value):
        self.sent += value
        return super().write(value)


class Process:
    def __init__(self):
        self.pid, self.returncode = 71, None
        self.stdin, self.stdout = Pipe(), io.BytesIO()
        self.killed, self.waits, self.polls = False, [], 0
    def poll(self):
        self.polls += 1
        return self.returncode
    def kill(self):
        self.killed, self.returncode = True, -9
    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.returncode = -9 if self.killed else 0
        return self.returncode


class Accounting:
    def __init__(self, rows=None):
        self.rows, self.pids = list(rows or []), []
    def sample(self, pid):
        self.pids.append(pid)
        row = self.rows.pop(0) if self.rows else (123, 20, 0)
        if isinstance(row, BaseException):
            raise row
        return row


def token(pid=71, start=123, threshold=100):
    return policy.TOKEN + ("%d:%d:%d\n" % (pid, start, threshold)).encode("ascii")


class WatchdogTests(unittest.TestCase):
    def test_actual_adapter_uses_v0_96_byte_struct_and_zero_success(self):
        import ctypes as c
        calls = []
        class Function:
            result = 0
            def __call__(self, pid, flavor, pointer):
                calls.append((pid, flavor, c.sizeof(pointer._obj)))
                pointer._obj.start, pointer._obj.footprint = 123, 20
                return self.result
        function = Function()
        with mock.patch.object(policy.sys, 'platform', 'darwin'), \
                mock.patch.object(c, 'CDLL', return_value=types.SimpleNamespace(proc_pid_rusage=function)):
            api = policy.Accounting()
        self.assertEqual(c.sizeof(api.Usage), 96)
        self.assertEqual(api.sample(71), (123, 20, 0))
        self.assertEqual(calls, [(71, 0, 96)])
        self.assertEqual(function.restype, c.c_int)
        function.result = -1
        with mock.patch.object(c, 'get_errno', return_value=13), self.assertRaises(policy.AccountingUnavailable) as caught:
            api.sample(71)
        self.assertEqual(caught.exception.errno, 13)

    def test_grant_requires_valid_initial_and_current_accounting_and_closes_pipe(self):
        process, accounting = Process(), Accounting()
        watch = policy.Watchdog(process, 100, accounting=accounting)
        self.assertEqual(process.stdin.sent, b"")
        watch.grant()
        self.assertEqual(process.stdin.sent, token())
        self.assertTrue(process.stdin.closed)
        self.assertEqual(accounting.pids, [71, 71])
        with self.assertRaises(policy.AccountingUnavailable):
            watch.grant()

    def test_threshold_or_missing_initial_accounting_never_grants(self):
        for row in ((123, 100, 0), (123, 101, 0), (0, 20, 0), (123, 0, 0), OSError("not available")):
            with self.subTest(row=row):
                process = Process()
                with self.assertRaises(OSError):
                    policy.Watchdog(process, 100, accounting=Accounting([row]))
                self.assertEqual(process.stdin.sent, b"")

    def test_changed_start_identity_refuses_before_grant_even_same_pid(self):
        process = Process()
        watch = policy.Watchdog(process, 100, accounting=Accounting([(123, 20, 0), (124, 20, 0)]))
        with self.assertRaisesRegex(policy.AccountingUnavailable, "start identity"):
            watch.grant()
        self.assertEqual(process.stdin.sent, b"")
        self.assertFalse(process.killed, "identity sampling itself never signals an observed PID")

    def test_live_accounting_loss_is_a_refusal(self):
        process = Process()
        watch = policy.Watchdog(process, 100, accounting=Accounting([(123, 20, 0), OSError("lost")]))
        with self.assertRaises(OSError):
            watch.check()
        self.assertEqual(process.polls, 1)
        self.assertIsNone(process.returncode)

    def test_accounting_loss_only_after_owned_child_exit_is_normal_completion(self):
        process = Process()
        watch = policy.Watchdog(process, 100, accounting=Accounting([(123, 20, 0), OSError("exited")]))
        process.poll = lambda: setattr(process, 'returncode', 0) or 0
        self.assertFalse(watch.check())
        self.assertEqual(process.returncode, 0)

    def test_changed_process_pid_or_already_reaped_owner_refuses_initial_admission(self):
        process = Process()
        watch = policy.Watchdog(process, 100, accounting=Accounting())
        process.pid = 72
        with self.assertRaises(policy.AccountingUnavailable):
            watch.check()
        process.returncode = 0
        with self.assertRaises(policy.AccountingUnavailable):
            policy.Watchdog(process, 100, accounting=Accounting())

    def test_admission_pipe_failure_is_resource_refusal_without_granted_state(self):
        process = Process()
        process.stdin.write = mock.Mock(side_effect=BrokenPipeError('closed'))
        watch = policy.Watchdog(process, 100, accounting=Accounting())
        with self.assertRaises(policy.AccountingUnavailable):
            watch.grant()
        self.assertFalse(watch.granted)

    def test_sampling_is_not_a_hard_peak_cap(self):
        # A real peak between these observations is deliberately not asserted impossible by this contract.
        watch = policy.Watchdog(Process(), 100, accounting=Accounting([(123, 20, 0), (123, 99, 0)]))
        self.assertTrue(watch.check())
        self.assertEqual(watch.threshold, 100)

    def test_worker_token_requires_exact_self_start_and_threshold(self):
        with mock.patch.object(policy.os, 'getpid', return_value=71):
            policy.await_parent(100, source=io.BytesIO(token()), accounting=Accounting())
            for value in (b'', b'x\n', token(pid=72), token(start=124), token(threshold=101),
                          policy.TOKEN + b'1:' * 100 + b'\n'):
                with self.subTest(value=value), self.assertRaises(policy.AccountingUnavailable):
                    policy.await_parent(100, source=io.BytesIO(value), accounting=Accounting())

    def test_worker_checks_own_footprint_before_parser_after_token(self):
        with mock.patch.object(policy.os, 'getpid', return_value=71), self.assertRaises(policy.FootprintExceeded):
            policy.await_parent(100, source=io.BytesIO(token()), accounting=Accounting([(123, 100, 0)]))


class OutputFacadeTests(unittest.TestCase):
    def invoke(self, process, accounting, chunks, *, clock=None, limit=8):
        with mock.patch.object(output.sys, 'platform', 'darwin'), \
                mock.patch.object(output.os, 'name', 'posix'), \
                mock.patch.object(output.subprocess, 'Popen', return_value=process) as spawn, \
                mock.patch.object(policy, 'Accounting', return_value=accounting), \
                mock.patch.object(output, '_chunk', side_effect=chunks), \
                mock.patch.object(output.time, 'monotonic', side_effect=clock or (lambda: 0)):
            result = output.run(['python', '-I', '-S', 'worker'], memory_bytes=100,
                                output_bytes=limit, timeout=30, env={})
        self.assertIs(spawn.call_args.kwargs['stdin'], subprocess.PIPE)
        self.assertIs(spawn.call_args.kwargs['stderr'], subprocess.DEVNULL)
        return result

    def test_actual_output_facade_grants_before_read_and_samples_through_eof(self):
        process, api = Process(), Accounting()
        def chunks(*args):
            self.assertEqual(process.stdin.sent, token())
            return b'ab' if len(api.pids) == 3 else b''
        result = self.invoke(process, api, chunks)
        self.assertEqual(result.stdout, b'ab')
        self.assertGreaterEqual(len(api.pids), 5, "initial/grant/read/read/EOF exit all accounted")
        self.assertTrue(process.stdout.closed)
        self.assertFalse(process.killed)

    def test_actual_facade_initial_accounting_failure_kills_reaps_without_grant(self):
        process = Process()
        with self.assertRaises(OSError):
            self.invoke(process, Accounting([OSError('missing')]), [])
        self.assertEqual(process.stdin.sent, b'')
        self.assertTrue(process.killed and process.stdin.closed and process.stdout.closed)
        self.assertIn(5, process.waits)

    def test_actual_facade_crossing_threshold_after_grant_kills_and_reaps(self):
        process = Process()
        with self.assertRaises(policy.FootprintExceeded):
            self.invoke(process, Accounting([(123, 20, 0), (123, 20, 0), (123, 100, 0)]), [])
        self.assertEqual(process.stdin.sent, token())
        self.assertTrue(process.killed and process.stdout.closed)

    def test_actual_facade_start_mismatch_after_grant_refuses_and_cleans_owned_child(self):
        process = Process()
        with self.assertRaisesRegex(policy.AccountingUnavailable, 'start identity'):
            self.invoke(process, Accounting([(123, 20, 0), (123, 20, 0), (124, 20, 0)]), [])
        self.assertEqual(process.pid, 71)
        self.assertTrue(process.killed and process.stdout.closed)

    def test_darwin_cleanup_failure_is_reported_and_pipe_ownership_closed(self):
        process = Process()
        process.kill = mock.Mock(side_effect=OSError('termination uncertainty'))
        process.wait = mock.Mock(side_effect=subprocess.TimeoutExpired('worker', 5))
        with self.assertRaisesRegex(OSError, 'termination uncertainty') as caught:
            self.invoke(process, Accounting([(123, 20, 0), (123, 20, 0), (123, 100, 0)]), [])
        self.assertIn('TimeoutExpired', ' '.join(caught.exception.__notes__))
        self.assertTrue(process.stdin.closed and process.stdout.closed)
        process.wait.assert_called_once_with(timeout=5)

    def test_observed_crossing_in_final_exited_sample_still_refuses_answer(self):
        process = Process()
        process.poll = lambda: setattr(process, 'returncode', 0) or 0
        api = Accounting([(123, 20, 0)] * 3 + [(123, 100, 456)])
        with self.assertRaises(policy.FootprintExceeded):
            self.invoke(process, api, [b'answer'])
        self.assertEqual(process.returncode, 0)
        self.assertFalse(process.killed)
        self.assertTrue(process.stdout.closed)

    def test_actual_facade_accounting_loss_while_live_kills_and_reaps(self):
        process = Process()
        with self.assertRaises(OSError):
            self.invoke(process, Accounting([(123, 20, 0), (123, 20, 0), OSError('lost')]), [])
        self.assertTrue(process.killed and process.stdout.closed)

    def test_actual_facade_timeout_and_output_limit_still_kill_reap(self):
        for clock, chunks, kind in (([0, 31], [], subprocess.TimeoutExpired),
                                    (None, [b'123456789'], output.OutputLimitExceeded)):
            with self.subTest(kind=kind):
                process = Process()
                with self.assertRaises(kind):
                    self.invoke(process, Accounting(), chunks, clock=clock)
                self.assertTrue(process.killed and process.stdout.closed)

    def test_eof_with_still_running_child_keeps_sampling_until_threshold(self):
        process = Process()
        def wait(timeout=None):
            process.waits.append(timeout)
            if timeout == 5:
                return process.returncode
            raise subprocess.TimeoutExpired('worker', timeout)
        process.wait = wait
        api = Accounting([(123, 20, 0)] * 4 + [(123, 100, 0)])
        with self.assertRaises(policy.FootprintExceeded):
            self.invoke(process, api, [b''])
        self.assertTrue(process.killed and process.stdout.closed)
        self.assertGreaterEqual(len(api.pids), 5)

    def test_linux_stdin_stays_devnull_and_no_watchdog_is_constructed(self):
        process = Process()
        with mock.patch.object(output.sys, 'platform', 'linux'), \
                mock.patch.object(output.os, 'name', 'posix'), \
                mock.patch.object(output.subprocess, 'Popen', return_value=process) as spawn, \
                mock.patch.object(output, '_chunk', return_value=b''), \
                mock.patch.object(policy, 'Watchdog') as watcher:
            output.run(['worker'], memory_bytes=100, output_bytes=8, timeout=30, env={})
        self.assertIs(spawn.call_args.kwargs['stdin'], subprocess.DEVNULL)
        watcher.assert_not_called()

    def test_public_extract_maps_sampled_crossing_and_accounting_refusal(self):
        for failure, code in ((policy.FootprintExceeded('threshold'), 'limit'),
                              (policy.AccountingUnavailable('lost'), 'resource')):
            with self.subTest(code=code), mock.patch.object(pdf_text, 'available', return_value=True), \
                    mock.patch.object(output, 'run', side_effect=failure), self.assertRaises(pdf_text.Unreadable) as caught:
                pdf_text.extract('brief.pdf')
            self.assertEqual(caught.exception.code, code)


class WorkerBarrierTests(unittest.TestCase):
    def test_actual_worker_admit_calls_darwin_handshake_before_read_and_never_hard_as(self):
        for good in (False, True):
            with self.subTest(good=good):
                order = []
                def await_parent(value):
                    order.append('handshake')
                    policy.await_parent(value, source=io.BytesIO(token() if good else b''), accounting=Accounting())
                module = types.SimpleNamespace(await_parent=await_parent)
                spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda m: None))
                with mock.patch.object(worker.sys, 'platform', 'darwin'), \
                        mock.patch.object(policy.os, 'getpid', return_value=71), \
                        mock.patch('importlib.util.spec_from_file_location', return_value=spec), \
                        mock.patch('importlib.util.module_from_spec', return_value=module), \
                        mock.patch.object(worker, 'read', side_effect=lambda *args: order.append('read') or {}), \
                        mock.patch.object(worker, 'answer') as answer:
                    worker.main(['worker', 'reader', 'brief.pdf', '20', '64', '100', '20', '100'])
                self.assertEqual(order, ['handshake', 'read'] if good else ['handshake'])
                if not good:
                    answer.assert_called_once_with(ok=False, code='resource')


if __name__ == '__main__':
    unittest.main()
