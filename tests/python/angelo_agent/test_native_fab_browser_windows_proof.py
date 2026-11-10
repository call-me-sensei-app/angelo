# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned-launch/window policy controls; no DLL, browser, authentication or GUI execution."""
import suite_isolation  # noqa: F401
from pathlib import Path
import ctypes
from ctypes import wintypes
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import fab_browser_windows_proof as proof


class Browser:
    pass


class Child:
    pass


class Job:
    pass


class Lease:
    def verify(self):
        self.verifications += 1
        return True


class Native:
    def __init__(self):
        self.events = []
        self.value = (123, 456, r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        self.owned_windows = (7,)
        self.on_process = None
        self.on_post = None

    def process(self, handle, job):
        self.events.append(('process', handle, job))
        if self.on_process:
            self.on_process()
        return self.value

    def interactive(self, browser, pid):
        return 4  # Active session independent of console/RDP transport.

    def windows(self, pid, **kwargs):
        self.events.append(('windows', pid, kwargs['visible']))
        return self.owned_windows

    def foreground(self, window, pid):
        self.events.append(('foreground', window, pid))

    def foreground_current(self, window, pid):
        self.events.append(('foreground_current', window, pid))

    def window_current(self, window, pid):
        self.events.append(('window_current', window, pid))

    def post_close(self, window):
        self.events.append(('post', window))
        if self.on_post:
            self.on_post(window)


class WindowsBrowserProofTests(unittest.TestCase):
    def setUp(self):
        self.browser = b = Browser()
        b._lock = threading.RLock()
        b._closed = b._drained = b._retention_revoked = False
        b._manual_login, b._pipes = True, None
        b._runtime_generation, b._runtime_epoch_token = object(), 'a' * 32
        b._program = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
        b.profile = r'C:\private\profile'
        b._launch_arguments = (b._program, '--user-data-dir=' + b.profile,
                               '--no-first-run', '--new-window', 'https://www.fab.com/')
        b._process = child = Child()
        child.args, child.pid, child._handle = b._launch_arguments, 123, 101
        child._job_lifecycle_lock = threading.RLock()
        child._retain_job = child._job_ready = True
        child._job_force_attempted, child._passive_drained_job = False, None
        child._job = job = Job()
        job.lock, job.handle, job.required = threading.Lock(), 202, True
        job.closed = job.close_unknown = job._close_attempted = False
        b._profile_lease = lease = Lease()
        lease._browser, lease._released = b, False
        lease.path, lease.identity, lease.generation = b.profile, (9, b'identity'), 'b' * 32
        lease.verifications = 0
        modules = {}
        for name, exports in (
                ('fab_browser_process_windows', {'WindowsBrowserProcess': Browser}),
                ('fab_browser_profile_session_windows', {'WindowsProfileLease': Lease}),
                ('child_process', {'ChildProcess': Child, '_WindowsJob': Job})):
            module = ModuleType('angelo_agent.' + name)
            module.__dict__.update(exports)
            modules[module.__name__] = module
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, modules).start()
        self.native = Native()
        patch.object(proof, '_Native', return_value=self.native).start()

    def refused(self, function=None):
        with self.assertRaises(proof.WindowsProofUnavailable) as caught:
            (function or (lambda: proof.prove(self.browser)))()
        self.assertIs(caught.exception.owner, self.browser)
        return caught.exception

    def test_exact_retained_resources_and_original_argv_are_bound(self):
        b = self.browser
        value = proof.prove(b)
        self.assertIs(value.browser, b)
        self.assertIs(value.child, b._process)
        self.assertIs(value.job, b._process._job)
        self.assertIs(value.lease, b._profile_lease)
        self.assertIs(value.arguments, b._launch_arguments)
        self.assertIs(value.runtime, b._runtime_generation)
        self.assertEqual((value.pid, value.creation, value.process_handle, value.job_handle), (123, 456, 101, 202))
        self.assertEqual(b._profile_lease.verifications, 4)
        self.assertNotIn('profile', repr(value))
        with self.assertRaises(AttributeError):
            value.creation = 1

    def test_equal_but_different_launch_tuple_is_not_actual_dispatch(self):
        self.browser._process.args = tuple(list(self.browser._launch_arguments))
        self.refused()
        self.assertEqual(self.native.events, [])

    def test_duplicate_or_wrong_profile_flag_refuses(self):
        for flag in ('--user-data-dir=C:\\other', '--user-data-directory=ambiguous'):
            original = self.browser._launch_arguments
            self.browser._launch_arguments = original + (flag,)
            self.browser._process.args = self.browser._launch_arguments
            self.refused()
            self.browser._launch_arguments = self.browser._process.args = original

    def test_manual_browser_cannot_have_managed_pipe(self):
        self.browser._pipes = object()
        self.refused()

    def test_uncertain_closed_forced_or_passive_job_is_not_queried(self):
        b, child, job = self.browser, self.browser._process, self.browser._process._job
        for owner, field, value in ((job, 'closed', True), (job, 'close_unknown', True),
                                    (job, '_close_attempted', True), (job, 'handle', None),
                                    (child, '_job_force_attempted', True),
                                    (child, '_passive_drained_job', job), (child, '_job_ready', False)):
            with self.subTest(field=field):
                before = getattr(owner, field)
                setattr(owner, field, value)
                self.refused()
                setattr(owner, field, before)
        self.assertEqual(self.native.events, [])
        self.assertIs(b._process, child)

    def test_profile_lease_must_be_mutually_bound(self):
        self.browser._profile_lease._browser = Browser()
        self.refused()
        self.assertEqual(self.native.events, [])

    def test_native_wrong_pid_program_or_creation_refuses(self):
        for value in ((999, 456, self.browser._program), (123, 456, r'C:\foreign.exe'),
                      (123, 0, self.browser._program)):
            self.native.value = value
            self.refused()

    def test_runtime_change_between_native_queries_invalidates_binding(self):
        count = 0
        def change():
            nonlocal count
            count += 1
            if count == 2:
                self.browser._runtime_generation = object()
        self.native.on_process = change
        self.refused()

    def test_profile_failure_retains_exact_owners(self):
        lease = self.browser._profile_lease
        lease.verify = Mock(side_effect=OSError('unknown profile'))
        self.refused()
        self.assertIs(self.browser._profile_lease, lease)
        self.assertFalse(lease._released)

    def test_foreground_requests_only_unique_owned_window_then_rechecks(self):
        value = proof.prove(self.browser, foreground=True)
        self.assertEqual(value.window, 7)
        self.assertEqual([event[0] for event in self.native.events],
                         ['process', 'windows', 'foreground', 'process', 'foreground_current'])

    def test_no_or_multiple_windows_never_request_foreground(self):
        for windows in ((), (7, 8)):
            self.native.owned_windows = windows
            self.refused(lambda: proof.prove(self.browser, foreground=True))
        self.assertFalse(any(event[0] == 'foreground' for event in self.native.events))

    def test_denied_foreground_does_not_claim_manual_auth(self):
        self.native.foreground = Mock(side_effect=OSError('foreground denied'))
        self.refused(lambda: proof.prove(self.browser, foreground=True))
        self.assertFalse(hasattr(self.browser, '_manual_auth_active'))

    def test_managed_binding_requires_exact_pipe_flag(self):
        b = self.browser
        b._manual_login, b._pipes = False, object()
        self.refused()
        b._launch_arguments += ('--remote-debugging-pipe',)
        b._process.args = b._launch_arguments
        self.assertIs(proof.prove(b).browser, b)

    def test_all_owned_windows_receive_normal_close_after_prepublished_intent(self):
        b = self.browser
        self.native.owned_windows = (7, 8, 9)
        def post(window):
            request = b._windows_close_request
            self.assertIs(request.binding.browser, b)
            self.assertEqual(request.windows, (7, 8, 9))
            self.assertGreater(request.attempted, request.posted)
        self.native.on_post = post
        value = proof.request_normal_close(b, proof.time.monotonic() + 10)
        self.assertEqual((value.attempted, value.posted), (3, 3))
        self.assertTrue(value.complete)
        self.assertFalse(value.uncertain)
        self.assertEqual([event for event in self.native.events if event[0] == 'post'],
                         [('post', 7), ('post', 8), ('post', 9)])
        self.assertFalse(b._drained)
        self.assertFalse(b._profile_lease._released)

    def test_partial_lost_post_is_retained_and_never_reposted(self):
        b = self.browser
        self.native.owned_windows = (7, 8)
        self.native.on_post = lambda window: (_ for _ in ()).throw(OSError('lost reply')) if window == 8 else None
        error = self.refused(lambda: proof.request_normal_close(b, proof.time.monotonic() + 10))
        request = b._windows_close_request
        self.assertIs(type(error), proof.NormalCloseRequestUncertain)
        self.assertIs(error.request, request)
        self.assertIs(error.owner, b)
        self.assertEqual((request.attempted, request.posted), (2, 1))
        self.assertTrue(request.uncertain)
        self.assertIs(request.binding.child, b._process)
        self.assertIs(proof.request_normal_close(b, proof.time.monotonic() + 10), request)
        self.assertEqual([event for event in self.native.events if event[0] == 'post'], [('post', 7), ('post', 8)])

    def test_previous_close_receipt_rechecks_all_stored_owners_without_live_process_query(self):
        b = self.browser
        request = proof.request_normal_close(b, proof.time.monotonic() + 10)
        self.native.process = Mock(side_effect=OSError('leader already exited'))
        original_queries = len(self.native.events)
        self.assertIs(proof.request_normal_close(b, proof.time.monotonic() + 10), request)
        self.native.process.assert_not_called()
        self.assertEqual(len(self.native.events), original_queries)
        self.assertGreater(b._profile_lease.verifications, 4)

    def test_previous_receipt_rejects_changed_handles_argv_profile_identity_generation_or_path(self):
        b = self.browser
        request = proof.request_normal_close(b, proof.time.monotonic() + 10)
        copied = tuple(list(b._launch_arguments))
        changes = ((b._process, '_handle', 999), (b._process._job, 'handle', 999),
                   (b._process, 'pid', 999), (b, '_launch_arguments', copied),
                   (b._process, 'args', copied), (b, '_program', r'C:\foreign.exe'),
                   (b._profile_lease, 'identity', (999, b'other')),
                   (b._profile_lease, 'generation', 'c' * 32),
                   (b._profile_lease, 'path', r'C:\other'), (b, 'profile', r'C:\other'))
        for owner, field, value in changes:
            with self.subTest(field=field):
                original = getattr(owner, field)
                setattr(owner, field, value)
                try:
                    error = self.refused(lambda: proof.request_normal_close(b, proof.time.monotonic() + 10))
                    self.assertIs(type(error), proof.WindowsProofUnavailable)
                    self.assertIs(b._windows_close_request, request)
                finally:
                    setattr(owner, field, original)
        self.assertEqual([event for event in self.native.events if event[0] == 'post'], [('post', 7)])

    def test_old_message_receipt_cannot_turn_fresh_binding_failure_into_lost_post(self):
        b = self.browser
        proof.request_normal_close(b, proof.time.monotonic() + 10)
        original = b._windows_close_request
        b._process._job.close_unknown = True
        error = self.refused(lambda: proof.request_normal_close(b, proof.time.monotonic() + 10))
        self.assertIs(type(error), proof.WindowsProofUnavailable)
        self.assertIs(b._windows_close_request, original)
        self.assertEqual([event for event in self.native.events if event[0] == 'post'], [('post', 7)])

    def test_expired_close_deadline_has_no_request_or_message(self):
        self.refused(lambda: proof.request_normal_close(self.browser, proof.time.monotonic() - 1))
        self.assertFalse(hasattr(self.browser, '_windows_close_request'))
        self.assertEqual(self.native.events, [])

    def test_close_request_allows_backend_already_marked_closed(self):
        self.browser._closed = True
        self.assertTrue(proof.request_normal_close(self.browser, proof.time.monotonic() + 10).complete)
        self.refused()


class WindowsNativeAdapterTests(unittest.TestCase):
    def native(self):
        native = proof._Native.__new__(proof._Native)
        native.c, native.w = ctypes, wintypes
        native.callback = lambda function: function
        native.k, native.u, native.d = SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
        native.wts = SimpleNamespace()
        return native

    def test_process_query_uses_exact_borrowed_handles_and_actual_job_result(self):
        native = self.native()
        native.k.WaitForSingleObject = Mock(return_value=258)
        native.k.GetProcessId = Mock(return_value=123)
        def times(handle, created, *rest):
            created._obj.dwLowDateTime = 456
            return 1
        native.k.GetProcessTimes = Mock(side_effect=times)
        def member(handle, job, output):
            output._obj.value = 1
            return 1
        native.k.IsProcessInJob = Mock(side_effect=member)
        def image(handle, flags, output, count):
            output.value = r'C:\chrome.exe'
            count._obj.value = len(output.value)
            return 1
        native.k.QueryFullProcessImageNameW = Mock(side_effect=image)
        self.assertEqual(native.process(101, 202), (123, 456, r'C:\chrome.exe'))
        self.assertEqual(native.k.IsProcessInJob.call_args.args[:2], (101, 202))
        native.k.IsProcessInJob.side_effect = lambda handle, job, output: 1
        with self.assertRaises(OSError):
            native.process(101, 202)
        native.k.WaitForSingleObject.return_value = 0
        with self.assertRaises(OSError):
            native.process(101, 202)

    def test_window_scan_filters_foreign_windows_without_reading_titles(self):
        native = self.native()
        native.u.IsWindow = lambda window: 1
        native.u.GetAncestor = lambda window, flag: window
        def pid(window, output):
            output._obj.value = 123 if window in (7, 8) else 999
            return 1
        native.u.GetWindowThreadProcessId = pid
        native.u.EnumWindows = lambda callback, _: all(callback(window, 0) for window in (3, 7, 8))
        native.u.IsWindowVisible = lambda window: window == 7
        native.u.IsIconic = lambda window: 0
        native.d.DwmGetWindowAttribute = lambda *args: 0
        self.assertEqual(native.windows(123, visible=True, deadline=proof.time.monotonic() + 10), (7,))
        self.assertEqual(native.windows(123, visible=False, deadline=proof.time.monotonic() + 10), (7, 8))

    def test_owned_window_limit_refuses_instead_of_truncating(self):
        native = self.native()
        native.u.IsWindow = lambda window: 1
        native.u.GetAncestor = lambda window, flag: window
        def pid(window, output):
            output._obj.value = 123
            return 1
        native.u.GetWindowThreadProcessId = pid
        native.u.EnumWindows = lambda callback, _: all(callback(window, 0) for window in range(1, 67))
        with self.assertRaises(OSError):
            native.windows(123, visible=False, deadline=proof.time.monotonic() + 10)


    def test_foreground_uses_real_request_and_observation_with_pid_recheck(self):
        native = self.native()
        native.u.IsWindow = Mock(return_value=1)
        native.u.GetAncestor = Mock(side_effect=lambda window, _: window)
        def pid(window, output):
            output._obj.value = 123
            return 1
        native.u.GetWindowThreadProcessId = Mock(side_effect=pid)
        native.u.SetForegroundWindow = Mock(return_value=1)
        native.u.GetForegroundWindow = Mock(return_value=7)
        native.foreground(7, 123)
        native.u.SetForegroundWindow.assert_called_once_with(7)
        native.u.GetForegroundWindow.return_value = 8
        with self.assertRaises(OSError):
            native.foreground_current(7, 123)
        native.u.SetForegroundWindow.return_value = 0
        with self.assertRaises(OSError):
            native.foreground(7, 123)

    def test_normal_close_posts_only_scalar_wm_close_and_does_not_wait_for_exit(self):
        native = self.native()
        native.u.PostMessageW = Mock(return_value=1)
        native.post_close(7)
        native.u.PostMessageW.assert_called_once_with(7, 0x0010, 0, 0)
        native.u.PostMessageW.return_value = 0
        with self.assertRaises(OSError):
            native.post_close(7)

    def test_changed_hwnd_owner_is_refused_before_foreground_request(self):
        native = self.native()
        native.u.IsWindow = lambda window: 1
        native.u.GetAncestor = lambda window, _: window
        def pid(window, output):
            output._obj.value = 999
            return 1
        native.u.GetWindowThreadProcessId = pid
        native.u.SetForegroundWindow = Mock()
        with self.assertRaises(OSError):
            native.foreground(7, 123)
        native.u.SetForegroundWindow.assert_not_called()


    def interactive_native(self, *, session=4, remote=None, state=0, length=4,
                           station='WinSta0', desktop='Default', receiving=True):
        native = self.native()
        native.k.GetCurrentProcessId = lambda: 50
        native.k.GetCurrentThreadId = lambda: 55
        def sessions(pid, output):
            output._obj.value = session if pid == 50 or remote is None else remote
            return 1
        native.k.ProcessIdToSessionId = sessions
        native.u.GetProcessWindowStation = lambda: 10
        native.u.GetThreadDesktop = lambda thread: 11
        def info(handle, key, output, size, needed):
            if key == 2:
                output.value = station if handle == 10 else desktop
                needed._obj.value = (len(output.value) + 1) * ctypes.sizeof(ctypes.c_wchar)
            else:
                self.assertEqual((handle, key), (11, 6))
                output._obj.value = receiving
                needed._obj.value = size
            return 1
        native.u.GetUserObjectInformationW = info
        native._fixture_enum = ctypes.c_int32(state)
        def query(server, selected, field, pointer, size):
            self.assertIsNone(server)
            self.assertEqual((selected, field), (session, 8))
            self.assertTrue(browser._windows_wts_query.issued)
            pointer._obj.value = ctypes.addressof(native._fixture_enum)
            size._obj.value = length
            return 1
        browser = SimpleNamespace()
        native.wts.WTSQuerySessionInformationW = Mock(side_effect=query)
        native.wts.WTSFreeMemory = Mock()
        return native, browser

    def test_active_console_and_rdp_sessions_use_same_input_desktop_contract(self):
        for session in (1, 4):
            native, browser = self.interactive_native(session=session)
            self.assertEqual(native.interactive(browser, 123), session)
            request = browser._windows_wts_query
            self.assertTrue(request.free_attempted and request.complete and request.freed)
            self.assertIsNone(request.pointer.value)
            native.wts.WTSFreeMemory.assert_called_once()
            proof.require_query_cleanup(browser)

    def test_service_zero_or_foreign_session_refuses_before_wts_allocation(self):
        for kwargs in ({'session': 0}, {'session': 4, 'remote': 5}):
            native, browser = self.interactive_native(**kwargs)
            with self.assertRaises(OSError):
                native.interactive(browser, 123)
            native.wts.WTSQuerySessionInformationW.assert_not_called()
            self.assertFalse(hasattr(browser, '_windows_wts_query'))

    def test_disconnected_or_wrong_fixed_response_is_freed_then_refused(self):
        for kwargs in ({'state': 4}, {'length': 8}):
            native, browser = self.interactive_native(**kwargs)
            with self.assertRaises(OSError):
                native.interactive(browser, 123)
            native.wts.WTSFreeMemory.assert_called_once()
            self.assertTrue(browser._windows_wts_query.freed)

    def test_noninteractive_station_desktop_or_input_flag_refuses(self):
        for kwargs in ({'station': 'Service-0x0-3e7$'}, {'desktop': 'Winlogon'}, {'receiving': False}):
            native, browser = self.interactive_native(**kwargs)
            with self.assertRaises(OSError):
                native.interactive(browser, 123)
            self.assertTrue(browser._windows_wts_query.freed)

    def test_unknown_wts_free_retains_pointer_and_never_reads_or_frees_it_again(self):
        native, browser = self.interactive_native()
        native.wts.WTSFreeMemory.side_effect = OSError('lost free return')
        with self.assertRaises(OSError):
            native.interactive(browser, 123)
        request = browser._windows_wts_query
        self.assertTrue(request.free_attempted and request.uncertain)
        self.assertFalse(request.freed)
        with self.assertRaises(proof.WindowsProofUnavailable) as caught:
            proof.require_query_cleanup(browser)
        self.assertIs(caught.exception.owner, browser)
        self.assertEqual(request.pointer.value, ctypes.addressof(native._fixture_enum))
        with self.assertRaises(OSError):
            native.interactive(browser, 123)
        self.assertIs(browser._windows_wts_query, request)
        native.wts.WTSFreeMemory.assert_called_once()
        native.wts.WTSQuerySessionInformationW.assert_called_once()

    def test_lost_wts_query_return_retains_prepublished_output_without_guessing_free(self):
        native, browser = self.interactive_native()
        actual = native.wts.WTSQuerySessionInformationW.side_effect
        def lost(*args):
            actual(*args)
            raise OSError('lost query return')
        native.wts.WTSQuerySessionInformationW.side_effect = lost
        with self.assertRaises(OSError):
            native.interactive(browser, 123)
        request = browser._windows_wts_query
        self.assertTrue(request.uncertain)
        self.assertEqual(request.pointer.value, ctypes.addressof(native._fixture_enum))
        native.wts.WTSFreeMemory.assert_not_called()
        with self.assertRaises(OSError):
            native.interactive(browser, 123)
        native.wts.WTSQuerySessionInformationW.assert_called_once()

    def test_known_no_allocation_query_failure_can_retry_but_unexpected_pointer_cannot(self):
        native, browser = self.interactive_native()
        original = native.wts.WTSQuerySessionInformationW.side_effect
        native.wts.WTSQuerySessionInformationW.side_effect = lambda *args: 0
        with self.assertRaises(OSError):
            native.interactive(browser, 123)
        self.assertTrue(browser._windows_wts_query.complete and browser._windows_wts_query.freed)
        native.wts.WTSQuerySessionInformationW.side_effect = original
        self.assertEqual(native.interactive(browser, 123), 4)
        native, browser = self.interactive_native()
        original = native.wts.WTSQuerySessionInformationW.side_effect
        def unexpected(*args):
            original(*args)
            return 0
        native.wts.WTSQuerySessionInformationW.side_effect = unexpected
        with self.assertRaises(OSError):
            native.interactive(browser, 123)
        self.assertTrue(browser._windows_wts_query.uncertain)
        native.wts.WTSFreeMemory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
