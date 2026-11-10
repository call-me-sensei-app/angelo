# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic owners on actual Driver/Host/Worker; no browser, account, profile or native acceptance."""
import suite_isolation  # noqa: F401
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import team_library_fixtures  # noqa: F401
from angelo_agent import fab_host, fab_host_jobs, fab_login_transition
from angelo_agent.fab_catalog import FabError


class Transport:
    def __init__(self, trace): self.trace, self.revoked = trace, False
    def revoke(self): self.revoked = True; self.trace.append(('revoke', threading.get_ident()))


class Managed:
    login_mode = 'managed'
    def __init__(self, trace): self.trace, self.closed = trace, False
    def acknowledge_user_login(self, *, completed):
        assert completed is True
        self.trace.append(('managed-ack', threading.get_ident()))
    def begin_login(self, stop): raise AssertionError('No implicit CDP sign-in on retained managed auth failure')
    def service_idle(self, stop=None, *, seconds=.25): return None
    def close(self, *, retain_profile=False):
        assert retain_profile is False
        self.trace.append(('managed-close', threading.get_ident())); self.closed = True


class Manual:
    login_mode = 'manual'
    def __init__(self, trace, managed):
        self.trace, self.managed = trace, managed
        self.acknowledged = self.closed = False
        self.failure = None
        self.entered = self.release = None
    def begin_login(self, stop):
        stop.check(); self.trace.append(('foreground', threading.get_ident()))
    def acknowledge_user_login(self, *, completed):
        assert completed is True
        self.acknowledged = True; self.trace.append(('manual-ack', threading.get_ident()))
    def finish_manual_login(self, *, stop):
        assert self.acknowledged
        self.trace.append(('finish', threading.get_ident()))
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(1), 'Synthetic transition release timed out'
        try:
            stop.check()
        except FabError as error:
            error.owner = self  # Exact partial owner remains with the producer on canceled transfer.
            raise
        if self.failure is not None: raise self.failure
        return self.managed
    def service_idle(self, stop=None, *, seconds=.25): return None  # No CDP exists during manual auth.
    def close(self, *, retain_profile=False):
        assert retain_profile is False
        self.trace.append(('manual-close', threading.get_ident())); self.closed = True


class Session:
    """Truthful cached active/disconnect lifecycle required by the real Main pump; no account proof."""
    def __init__(self, on_disconnect=None): self.active, self.on_disconnect = True, on_disconnect
    def disconnect(self):
        self.active = False
        if self.on_disconnect is not None: self.on_disconnect()


def wait_job(host):
    exact = host.job  # Capture before any pump can consume/clear the completed non-agent slot.
    if exact is None: raise AssertionError('No exact synthetic job was enqueued')
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        host.pump()
        if exact.done.is_set():
            host.pump()
            if exact.error is not None: raise AssertionError('Actual synthetic worker job failed') from exact.error
            if host.job is not None: raise AssertionError('Main did not consume this non-agent completion')
            if exact.value is not None: raise AssertionError('Main did not take this exact job result')
            return exact
        threading.Event().wait(.002)
    raise AssertionError('Synthetic actual worker did not finish')


class ManualTransitionTests(unittest.TestCase):
    def setup_driver(self):
        trace, calls = [], []
        managed = Managed(trace); manual = Manual(trace, managed); transport = Transport(trace)
        def factory(**kwargs): calls.append(kwargs); return manual
        def observe(owner, stop):
            stop.check(); self.assertIs(owner, managed)
            trace.append(('observe-new', threading.get_ident())); return transport
        return fab_host.Driver(factory, observe), manual, managed, transport, trace, calls

    def test_fresh_manual_foregrounds_before_cached_wait_without_account_reads(self):
        driver, manual, _, _, trace, calls = self.setup_driver()
        result = driver.connect(threading.Event(), False)
        self.assertEqual(result, dict(transport=None, status='sign_in_required',
                                     reason_code='SIGN_IN_REQUIRED', can_search=False))
        self.assertEqual([name for name, _ in trace], ['foreground'])
        self.assertEqual(calls[0]['login_mode'], 'manual')
        self.assertIs(calls[0]['stop'], driver.lifetime)
        self.assertIs(calls[0]['resume'], True)
        self.assertFalse(manual.acknowledged)
        driver.close()

    def test_person_ack_replaces_runtime_then_observes_only_new_managed_owner(self):
        driver, manual, managed, transport, trace, _ = self.setup_driver()
        driver.connection = manual
        old_transport = Transport(trace); driver.transport = old_transport
        result = driver.connect(threading.Event(), True)
        self.assertEqual([name for name, _ in trace], ['revoke', 'manual-ack', 'finish', 'observe-new'])
        self.assertTrue(old_transport.revoked)
        self.assertIs(driver.connection, managed)
        self.assertIs(result['transport'], transport)
        self.assertEqual(result['status'], 'connected')
        driver.close()

    def test_retained_managed_resume_verifies_without_manual_or_login_calls(self):
        trace = []; managed = Managed(trace); transport = Transport(trace); replacement = Transport(trace); calls = []
        observations = iter((transport, replacement))
        def factory(**kwargs): calls.append(kwargs); return managed
        def observe(owner, stop): self.assertIs(owner, managed); return next(observations)
        driver = fab_host.Driver(factory, observe)
        self.assertIs(driver.connect(threading.Event(), False)['transport'], transport)
        self.assertEqual(trace, [])
        self.assertIs(driver.connect(threading.Event(), True)['transport'], replacement)
        self.assertIs(driver.connection, managed)
        self.assertEqual([name for name, _ in trace], ['revoke', 'managed-ack'])
        self.assertTrue(transport.revoked)
        self.assertFalse(replacement.revoked)
        driver.close()

    def test_managed_auth_required_is_closed_without_cdp_login_or_profile_purge(self):
        trace = []; managed = Managed(trace)
        def refused(owner, stop): raise FabError('AUTH_REQUIRED')
        driver = fab_host.Driver(lambda **kwargs: managed, refused)
        result = driver.connect(threading.Event(), False)
        self.assertIsNone(result['transport'])
        self.assertEqual((result['status'], result['reason_code']), ('error', 'ACCOUNT_UNVERIFIED'))
        self.assertIs(driver.connection, managed)
        self.assertFalse(managed.closed); self.assertEqual(trace, [])
        driver.close()

    def test_partial_owner_transfers_exactly_and_cleanup_cannot_be_skipped(self):
        driver, manual, _, _, _, _ = self.setup_driver()
        partial = SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError('not drained')))
        error = RuntimeError('synthetic partial transition'); error.owner = partial; manual.failure = error
        driver.connection = manual
        with self.assertRaises(fab_login_transition.TransitionFailed):
            driver.connect(threading.Event(), True)
        self.assertIs(driver.partial, partial); self.assertIsNone(driver.connection)
        with self.assertRaises(RuntimeError): driver.close()
        self.assertIs(driver.partial, partial); self.assertTrue(driver.cleanup_required)
        partial.close = lambda: None
        driver.close()
        self.assertIsNone(driver.partial); self.assertFalse(driver.cleanup_required)

    def test_lost_transition_owner_remains_unknown_and_never_purges_old_profile(self):
        driver, manual, _, _, trace, _ = self.setup_driver()
        manual.failure = RuntimeError('producer lost its owner'); driver.connection = manual
        with self.assertRaises(fab_login_transition.TransitionFailed): driver.connect(threading.Event(), True)
        retained = driver.partial
        self.assertIsInstance(retained, fab_login_transition.UnknownTransition)
        self.assertIs(retained.original, manual)
        with self.assertRaises(fab_login_transition.TransitionFailed): driver.close()
        self.assertIs(driver.partial, retained); self.assertFalse(manual.closed)
        self.assertNotIn('manual-close', [name for name, _ in trace])

    def test_same_or_missing_runtime_return_never_observes_old_manual_owner(self):
        for returned in ('same', None):
            with self.subTest(returned=returned):
                driver, manual, _, _, trace, _ = self.setup_driver()
                manual.managed = manual if returned == 'same' else None
                driver.connection = manual
                with self.assertRaises(fab_login_transition.TransitionFailed): driver.connect(threading.Event(), True)
                self.assertIsInstance(driver.partial, fab_login_transition.UnknownTransition)
                self.assertNotIn('observe-new', [name for name, _ in trace])

    def test_unknown_cached_mode_refuses_before_foreground_or_observation(self):
        driver, manual, _, _, trace, _ = self.setup_driver()
        manual.login_mode = True
        with self.assertRaises(fab_login_transition.TransitionFailed): driver.connect(threading.Event(), False)
        self.assertIs(driver.partial, manual); self.assertEqual(trace, [])
        driver.close()

    def test_actual_host_rotates_tickets_revokes_session_and_transitions_on_one_worker(self):
        _, manual, managed, transport, trace, _ = self.setup_driver()
        calls, sessions = [], []
        def factory(**kwargs): calls.append(threading.get_ident()); return manual
        def observe(owner, stop):
            self.assertIs(owner, managed); trace.append(('observe-new', threading.get_ident())); return transport
        def session_factory(value):
            sessions.append(threading.get_ident()); return Session()
        host = fab_host.Host(factory, observe=observe, session_factory=session_factory)
        try:
            host.connect(); wait_job(host)
            old_generation, request = host.view['generation'], host.view['request_id']
            revoked = []; old_session = Session(lambda: revoked.append(True)); host.session = old_session
            host.selection = object(); host.view['import_ready'] = True
            host.connect()
            self.assertEqual(revoked, [True]); self.assertFalse(old_session.active); self.assertIsNone(host.selection)
            self.assertNotEqual(host.view['generation'], old_generation)
            self.assertGreater(host.view['request_id'], request)
            wait_job(host)
            self.assertEqual(host.view['status'], 'connected')
            self.assertIs(host.driver.connection, managed)
            worker_ids = {tid for name, tid in trace if name in ('foreground', 'manual-ack', 'finish', 'observe-new')}
            self.assertEqual(len(worker_ids), 1)
            self.assertNotIn(threading.get_ident(), worker_ids)
            self.assertEqual(sessions, [threading.get_ident()])  # Cached Session construction stays on Main.
        finally:
            host.disconnect(); self.assertTrue(host.worker.join(1)); host.pump()

    def test_actual_completed_job_stop_and_clock_advance_do_not_cancel_new_runtime_lifetime(self):
        _, manual, managed, transport, _, _ = self.setup_driver()
        clock = [0.0]
        searches = []
        def factory(**kwargs):
            managed.lifetime = kwargs['stop']  # Closed producer contract: persistent event, never Job Stop.
            return manual
        def observe(owner, stop):
            self.assertIs(owner, managed); stop.check(); return transport
        def public_search(query, limit, stop):
            self.assertFalse(managed.lifetime.is_set())
            stop.check(); searches.append((query, limit))
            return dict(items=[], metadata_only=True, import_ready=False, account_verified=False)
        managed.public_search = public_search
        host = fab_host.Host(factory, observe=observe,
                             session_factory=lambda value: Session())
        with patch.object(fab_host_jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
            try:
                host.connect(); wait_job(host)
                host.connect(); completed_connection_job = wait_job(host)
                self.assertIsNone(host.job)
                completed_connection_job.stop()  # Actual public cleanup/cancel method on the captured consumed Job.
                self.assertTrue(completed_connection_job.cancel.is_set())
                clock[0] = 3600.0  # Beyond either connect operation deadline; current per-search Stop is fresh.
                host.search('chair'); wait_job(host)
                self.assertTrue(completed_connection_job.cancel.is_set())  # New search submission cannot normalize the old cancel.
                self.assertIs(managed.lifetime, host.driver.lifetime)
                self.assertFalse(host.driver.lifetime.is_set())
                self.assertEqual(searches, [('chair', 8)])
                self.assertEqual(host.view['search_status'], 'ready')
                self.assertIs(host.driver.connection, managed)
            finally:
                host.disconnect(); self.assertTrue(host.worker.join(1)); host.pump()

    def test_disconnect_during_transition_cancels_and_closes_exact_owner_on_same_worker(self):
        driver, manual, _, _, trace, _ = self.setup_driver()
        manual.entered, manual.release = threading.Event(), threading.Event()
        host = fab_host.Host(lambda **kwargs: manual, observe=driver.observe, session_factory=lambda value: None)
        try:
            host.connect(); wait_job(host)
            host.connect(); self.assertTrue(manual.entered.wait(1))
            host.disconnect(); self.assertTrue(host.driver.lifetime.is_set())
            manual.release.set(); self.assertTrue(host.worker.join(1)); host.pump()
            self.assertIsNone(host.driver.connection); self.assertIsNone(host.driver.partial)
            self.assertTrue(manual.closed)
            self.assertNotIn('observe-new', [name for name, _ in trace])
            self.assertEqual({tid for name, tid in trace if name in ('finish', 'manual-close')},
                             {next(tid for name, tid in trace if name == 'finish')})
        finally:
            manual.release.set()
            if host.driver.connection is not None or host.driver.partial is not None:
                host.disconnect(); self.assertTrue(host.worker.join(1)); host.pump()


if __name__ == '__main__': unittest.main()
