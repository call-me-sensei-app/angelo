# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual owner-method controls with fake native calls; not actual Windows ABI/Chrome proof. UNRUN."""
import suite_isolation  # noqa: F401
import ctypes
import sys
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE / 'scripts/addons_core'))
from angelo_agent import child_process as child
assert Path(child.__file__).resolve() == (SOURCE / 'scripts/addons_core/angelo_agent/child_process.py').resolve(), \
    'The canonical test must exercise this exact source addon owner'


def job_owner(*, close=True, active=0):
    job = child._WindowsJob.__new__(child._WindowsJob)
    job.handle, job.lock, job.closed, job.required = 123, threading.Lock(), False, True
    job._close_attempted, job.close_unknown, job.ctypes = False, False, ctypes
    def query(handle, kind, output, size, returned):
        output._obj.active = active
        return True
    job.kernel32 = SimpleNamespace(QueryInformationJobObject=mock.Mock(side_effect=query),
                                   CloseHandle=mock.Mock(return_value=close),
                                   TerminateJobObject=mock.Mock(return_value=True))
    return job


def process_owner(job=None):
    owner = child.ChildProcess.__new__(child.ChildProcess)
    owner._child_created = False  # No Popen/native child is constructed by these controls.
    owner.args, owner.returncode = ['synthetic-native-owner'], None
    owner._retain_job, owner._job_ready = True, True
    owner._job = job_owner() if job is None else job
    owner._job_lifecycle_lock = threading.RLock()
    owner._job_force_attempted, owner._passive_drained_job = False, None
    return owner


class PassiveTests(unittest.TestCase):
    def setUp(self):
        self.wait = mock.patch.object(subprocess.Popen, 'wait', return_value=0).start()
        self.kill = mock.patch.object(subprocess.Popen, 'kill', side_effect=AssertionError('no kill')).start()
        self.terminate = mock.patch.object(subprocess.Popen, 'terminate', side_effect=AssertionError('no terminate')).start()
        self.addCleanup(mock.patch.stopall)

    def deadline(self):
        return time.monotonic() + 10

    def assert_no_signal(self, owner):
        owner._job.kernel32.TerminateJobObject.assert_not_called()
        self.kill.assert_not_called()
        self.terminate.assert_not_called()

    def test_normal_zero_exit_then_checked_empty_then_known_close(self):
        owner = process_owner()
        events = []
        self.wait.side_effect = lambda **kwargs: (events.append('wait_zero') or 0)
        original_query = owner._job.kernel32.QueryInformationJobObject.side_effect
        def query(*args):
            events.append('checked_empty')
            return original_query(*args)
        owner._job.kernel32.QueryInformationJobObject.side_effect = query
        owner._job.kernel32.CloseHandle.side_effect = lambda value: (events.append('known_close') or True)
        self.assertIsNone(owner.finish_job_passive(self.deadline()))
        self.assertEqual(events, ['wait_zero', 'checked_empty', 'checked_empty', 'known_close'])
        self.assertIs(owner._passive_drained_job, owner._job)
        self.assertTrue(owner._job.closed)
        self.assertIsNone(owner._job.handle)
        self.assert_no_signal(owner)

    def test_nonzero_exit_refuses_and_keeps_job_without_close_or_signal(self):
        owner = process_owner()
        self.wait.return_value = 7
        with self.assertRaises(child.ChildCleanupUnavailable) as caught:
            owner.finish_job_passive(self.deadline())
        self.assertIs(caught.exception.owner, owner)
        self.assertEqual(owner._job.handle, 123)
        owner._job.kernel32.QueryInformationJobObject.assert_not_called()
        owner._job.kernel32.CloseHandle.assert_not_called()
        self.assert_no_signal(owner)

    def test_leader_wait_timeout_retains_exact_owner(self):
        owner = process_owner()
        self.wait.side_effect = subprocess.TimeoutExpired('synthetic', 0)
        with self.assertRaises(child.ChildCleanupUnavailable) as caught:
            owner.finish_job_passive(self.deadline())
        self.assertIs(caught.exception.owner, owner)
        self.assertFalse(owner._job.closed)
        self.assert_no_signal(owner)

    def test_remaining_helpers_never_trigger_termination_or_close(self):
        owner = process_owner()
        owner._job.wait_empty = mock.Mock(side_effect=subprocess.TimeoutExpired('live helpers', 0))
        with self.assertRaises(child.ChildCleanupUnavailable) as caught:
            owner.finish_job_passive(self.deadline())
        self.assertIs(caught.exception.owner, owner)
        owner._job.kernel32.CloseHandle.assert_not_called()
        self.assert_no_signal(owner)

    def test_accounting_failure_retains_job_without_signal(self):
        owner = process_owner()
        owner._job.kernel32.QueryInformationJobObject.side_effect = None
        owner._job.kernel32.QueryInformationJobObject.return_value = False
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        owner._job.kernel32.CloseHandle.assert_not_called()
        self.assert_no_signal(owner)

    def test_partial_attachment_is_not_passive_completion(self):
        owner = process_owner()
        owner._job_ready = False
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        self.wait.assert_not_called()
        self.assert_no_signal(owner)

    def test_prior_forced_closed_job_cannot_masquerade_as_normal_exit(self):
        owner = process_owner()
        owner._job_force_attempted = True
        owner._job.closed, owner._job.handle = True, None
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        self.wait.assert_not_called()
        self.assert_no_signal(owner)

    def test_closed_flag_without_own_passive_proof_refuses(self):
        owner = process_owner()
        owner._job.closed, owner._job.handle = True, None
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        self.wait.assert_not_called()
        self.assert_no_signal(owner)

    def test_successful_passive_repeat_uses_only_exact_stored_proof(self):
        owner = process_owner()
        owner.finish_job_passive(self.deadline())
        count = owner._job.kernel32.QueryInformationJobObject.call_count
        owner.finish_job_passive(self.deadline())
        self.wait.assert_called_once()
        self.assertEqual(owner._job.kernel32.QueryInformationJobObject.call_count, count)
        owner._job.kernel32.CloseHandle.assert_called_once_with(123)
        self.assert_no_signal(owner)

    def test_passive_proof_from_other_job_is_not_accepted(self):
        owner = process_owner()
        owner._passive_drained_job = job_owner()
        owner._job.closed, owner._job.handle = True, None
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        self.wait.assert_not_called()
        self.assert_no_signal(owner)

    def test_native_false_close_latches_and_never_queries_signals_or_recloses(self):
        owner = process_owner(job_owner(close=False))
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        job = owner._job
        self.assertTrue(job.close_unknown)
        self.assertEqual(job.handle, 123)
        query_count = job.kernel32.QueryInformationJobObject.call_count
        for action in (job.active_count, job.terminate_owned, job.close):
            with self.assertRaises(OSError): action()
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        self.assertEqual(job.kernel32.QueryInformationJobObject.call_count, query_count)
        job.kernel32.CloseHandle.assert_called_once_with(123)
        self.assert_no_signal(owner)

    def test_lost_close_return_preserves_owner_and_never_repeats_native_attempt(self):
        owner = process_owner()
        owner._job.kernel32.CloseHandle.side_effect = RuntimeError('native return lost')
        with self.assertRaises(child.ChildCleanupUnavailable) as caught:
            owner.finish_job_passive(self.deadline())
        self.assertIs(caught.exception.owner, owner)
        self.assertTrue(owner._job.close_unknown)
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        owner._job.kernel32.CloseHandle.assert_called_once_with(123)
        self.assert_no_signal(owner)

    def test_force_drain_after_unknown_close_cannot_use_stale_numeric_job_handle(self):
        owner = process_owner(job_owner(close=False))
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        with self.assertRaises(child.ChildCleanupUnavailable): owner.drain_job(self.deadline())
        owner._job.kernel32.CloseHandle.assert_called_once_with(123)
        owner._job.kernel32.TerminateJobObject.assert_not_called()
        self.kill.assert_not_called()

    def test_force_intent_is_latched_before_even_failed_native_termination(self):
        owner = process_owner()
        def refused(handle, code):
            self.assertTrue(owner._job_force_attempted)
            return False
        owner._job.kernel32.TerminateJobObject.side_effect = refused
        with self.assertRaises(child.ChildCleanupUnavailable): owner.drain_job(self.deadline())
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(self.deadline())
        self.kill.assert_not_called()
        self.terminate.assert_not_called()

    def test_nonretained_owner_is_rejected_before_wait(self):
        owner = process_owner()
        owner._retain_job = False
        with self.assertRaises(ValueError): owner.finish_job_passive(self.deadline())
        self.wait.assert_not_called()
        self.assert_no_signal(owner)

    def test_expired_deadline_does_not_query_or_signal_native_handles(self):
        owner = process_owner()
        with self.assertRaises(child.ChildCleanupUnavailable): owner.finish_job_passive(time.monotonic() - 1)
        self.wait.assert_not_called()
        owner._job.kernel32.QueryInformationJobObject.assert_not_called()
        self.assert_no_signal(owner)


if __name__ == '__main__': unittest.main()
