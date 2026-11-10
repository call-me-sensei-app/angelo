# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual timer/Host/Worker idle publication with synthetic owners; no bpy/browser/account/native proof."""
import suite_isolation  # noqa: F401
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import team_library_fixtures  # noqa: F401
from angelo_agent import fab_host, library_host


class IdleMainPumpTests(unittest.TestCase):
    def run_with_timer(self, host, callback):
        import angelo_agent
        redraws = []
        overlay = SimpleNamespace(redraw_all=lambda: redraws.append(('overlay', threading.get_ident())))
        preferences = SimpleNamespace(redraw=lambda: redraws.append(('preferences', threading.get_ident())))
        with patch.object(fab_host, '_host', host), patch.object(library_host, '_library', None), \
                patch.dict(sys.modules, {'angelo_agent.library_overlay': overlay,
                                        'angelo_agent.preferences_ui': preferences}), \
                patch.object(angelo_agent, 'library_overlay', overlay, create=True), \
                patch.object(angelo_agent, 'preferences_ui', preferences, create=True):
            callback(redraws)

    def test_actual_timer_consumes_idle_failure_after_foreground_job_is_done(self):
        fail = threading.Event()
        trace, revoked, disconnected = [], [], []
        class Connection:
            login_mode = 'managed'
            def service_idle(self, stop=None, *, seconds=.25):
                if fail.is_set():
                    trace.append(('idle-error', threading.get_ident()))
                    raise RuntimeError('synthetic observer taint')
            def close(self, *, retain_profile=False):
                assert retain_profile is False
                trace.append(('close', threading.get_ident()))
        connection = Connection()
        transport = SimpleNamespace(revoke=lambda: revoked.append(threading.get_ident()))
        session = SimpleNamespace(disconnect=lambda: disconnected.append(threading.get_ident()))
        host = fab_host.Host(lambda **kwargs: connection, observe=lambda owner, stop: transport,
                             session_factory=lambda observed: session)
        def scenario(redraws):
            try:
                host.connect()
                self.assertTrue(host.job.done.wait(1))
                next_interval = library_host._tick()  # Actual bpy timer's returned interval is its reschedule owner.
                self.assertEqual(host.view['status'], 'connected')
                self.assertFalse(fab_host.busy(), 'Idle-connected browser must not become UI busy/spinning')
                old_request = host.view['request_id']
                fail.set()
                self.assertTrue(host.worker.join(1))  # Full callback/publication completes without a Main poll.
                self.assertEqual(host.view['status'], 'connected')  # Only Main may change the cached UI.
                if next_interval is not None:  # Actual timer scheduling: an old None return never runs again.
                    final_interval = library_host._tick()
                else:
                    final_interval = None
                self.assertEqual(host.view['status'], 'error')
                self.assertEqual(host.view['reason_code'], 'SERVICE_UNAVAILABLE')
                self.assertGreater(host.view['request_id'], old_request)
                self.assertIsNone(host.session)
                self.assertEqual(disconnected, [threading.get_ident()])
                self.assertTrue(revoked)
                self.assertEqual(next_interval, library_host.TICK)
                self.assertIsNone(final_interval)  # Known closed owner and consumed error need no timer.
                self.assertFalse(fab_host.busy())
                worker_ids = {tid for _, tid in trace}
                self.assertEqual(len(worker_ids), 1)  # Browser service and close share the actual serialized worker.
                self.assertNotIn(threading.get_ident(), worker_ids)
                self.assertTrue(worker_ids <= set(revoked))
                self.assertTrue(set(revoked) <= worker_ids | {threading.get_ident()})  # In-memory revoke also runs Main.
                self.assertTrue(redraws)
                self.assertEqual({tid for _, tid in redraws}, {threading.get_ident()})
            finally:
                fail.set()
                host.disconnect()
                self.assertTrue(host.worker.join(1))
                host.pump()
        self.run_with_timer(host, scenario)

    def test_actual_timer_consumes_close_published_after_its_pump_before_idle_predicate(self):
        for fail_close in (False, True):
            with self.subTest(fail_close=fail_close):
                entered, release = threading.Event(), threading.Event()
                should_fail = [fail_close]
                class Connection:
                    login_mode = 'managed'
                    def service_idle(self, stop=None, *, seconds=.25): return None
                    def close(self, *, retain_profile=False):
                        entered.set()
                        assert release.wait(1), 'Synthetic close release timed out'
                        if should_fail[0]: raise RuntimeError('synthetic close unproved')
                connection = Connection()
                host = fab_host.Host(lambda **kwargs: connection,
                    observe=lambda owner, stop: SimpleNamespace(revoke=lambda: None),
                    session_factory=lambda value: SimpleNamespace(disconnect=lambda: None))
                def scenario(redraws):
                    try:
                        host.connect(); self.assertTrue(host.job.done.wait(1)); library_host._tick()
                        host.disconnect(); self.assertTrue(entered.wait(1))
                        close_job = host.job
                        original_pump = fab_host.pump
                        def publish_after_pump():
                            self.assertFalse(close_job.done.is_set())
                            changed = original_pump()  # Actual Main pump sees the still-pending close.
                            release.set()
                            self.assertTrue(close_job.done.wait(1))  # Force completion before needs_pump reads busy.
                            return changed
                        with patch.object(fab_host, 'pump', publish_after_pump):
                            next_interval = library_host._tick()
                        self.assertFalse(host.busy())
                        self.assertEqual(host.view['status'], 'disconnecting')
                        if next_interval is not None:
                            final_interval = library_host._tick()
                        else:
                            final_interval = None
                        self.assertEqual(host.view['status'], 'cleanup_required' if fail_close else 'disconnected')
                        self.assertEqual(next_interval, library_host.TICK)
                        self.assertIsNone(host.job)  # The non-agent completion has actually been consumed.
                        if fail_close:
                            self.assertIs(host.driver.connection, connection)
                            self.assertTrue(host.driver.cleanup_required)
                            self.assertEqual(final_interval, library_host.TICK)
                        else:
                            self.assertIsNone(host.driver.connection)
                            self.assertIsNone(final_interval)
                    finally:
                        should_fail[0] = False; release.set()
                        host.disconnect(); self.assertTrue(host.worker.join(1)); host.pump()
                self.run_with_timer(host, scenario)
        # A completed agent result belongs to its Controller, not Main's non-agent completion pump.
        host = fab_host.Host(lambda **kwargs: self.fail('No browser for a completed agent result'))
        from angelo_agent.fab_host_jobs import Job
        host.job = Job(1, 'agent', lambda cancel: None); host.job.done.set()
        def no_spinning_timer(redraws):
            self.assertIsNone(library_host._tick())
            self.assertIsNotNone(host.job)
            self.assertFalse(host.busy())
        self.run_with_timer(host, no_spinning_timer)

    def test_empty_registered_owner_neither_constructs_browser_nor_keeps_timer(self):
        calls = []
        host = fab_host.Host(lambda **kwargs: calls.append(kwargs))
        def scenario(redraws):
            self.assertIsNone(library_host._tick())
            self.assertFalse(fab_host.busy())
            self.assertEqual(calls, [])
            self.assertTrue(host.worker.join(.01))
        self.run_with_timer(host, scenario)


if __name__ == '__main__': unittest.main()
