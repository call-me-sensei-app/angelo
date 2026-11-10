# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic owner contracts, actual facade/jobs/binding; no browser or account proof."""
import suite_isolation  # noqa: F401
import threading
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
import team_library_fixtures  # noqa: F401
from angelo_agent import fab_approval, fab_host, fab_host_jobs
from angelo_agent.fab_catalog import FabError


@dataclass(frozen=True)
class Choice:
    identity: str
    private_control: str


class LiveHostTests(unittest.TestCase):
    def test_an_unexpected_failure_says_where_it_came_from_never_its_message(self):
        # Audit 2026-11-08: a live Connect ended in SERVICE_UNAVAILABLE with nothing recorded, so it could not be
        # diagnosed. The console now names the type and frames; the message (a URL, account text) never appears.
        import contextlib, io
        def fails():
            raise RuntimeError("https://example.invalid/?token=secret account@example.invalid")
        try:
            fails()
        except RuntimeError as error:
            shown = io.StringIO()
            with contextlib.redirect_stdout(shown):
                self.assertEqual(fab_host._reason(error), 'SERVICE_UNAVAILABLE')
        text = shown.getvalue()
        self.assertIn("RuntimeError", text)
        self.assertIn("fails", text)
        self.assertNotIn("token", text)
        self.assertNotIn("account@", text)
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            self.assertEqual(fab_host._reason(FabError('AUTH_REQUIRED')), 'ACCOUNT_UNVERIFIED')
        self.assertEqual(quiet.getvalue(), "", "a closed, expected code is not reported")

    def test_connection_lifetime_has_no_operation_deadline(self):
        from unittest.mock import patch
        observed = []
        connection = SimpleNamespace(login_mode="manual", begin_login=lambda stop: stop.check())
        def factory(**kwargs):
            observed.append(kwargs['stop'])
            return connection
        def unavailable(owner, stop):
            stop.check()
            raise FabError('AUTH_REQUIRED')
        driver = fab_host.Driver(factory, unavailable)
        with patch.object(fab_host_jobs.time, 'monotonic', return_value=0):
            driver.connect(threading.Event(), False)
        # The real owner retains this exact event, not a default 900-second Stop.
        self.assertIs(observed[0], driver.lifetime)
        with patch.object(fab_host_jobs.time, 'monotonic', return_value=3600):
            self.assertFalse(observed[0].is_set())
            operation = fab_host_jobs.Stop(driver.lifetime, seconds=2)
        with patch.object(fab_host_jobs.time, 'monotonic', return_value=3603):
            self.assertTrue(operation.is_set())
            self.assertFalse(observed[0].is_set())
        driver.lifetime.set()
        self.assertTrue(observed[0].is_set())

    def test_retired_import_cannot_close_payload_or_cache_before_seal_terminal(self):
        from angelo_agent.fab_native_job import RetiredImport
        calls = []
        job = fab_host_jobs.Job(1, 'seal', lambda stop: None)
        class Payload:
            def close(self): calls.append('native-close')
            def status(self):
                return {'schema': 1, 'state': 'RESTORED', 'published': False,
                        'restored': True, 'closed': True, 'recovery_required': False}
            def quiescent(self, status):
                from angelo_agent.blender_asset_commit import AssetCommit
                return AssetCommit.quiescent(status)
        prepared = SimpleNamespace(close=lambda: calls.append('cache-close'))
        retired = RetiredImport(job, dict(payload=Payload(), prepared=prepared))
        self.assertFalse(retired.poll())
        self.assertEqual(calls, [])
        job.done.set()
        self.assertTrue(retired.poll())
        self.assertEqual(calls, ['native-close', 'cache-close'])

    def test_none_status_without_complete_pristine_witness_retains_cache(self):
        from angelo_agent.fab_native_job import RetiredImport
        from angelo_agent.blender_asset_commit import AssetCommit
        calls = []
        job = fab_host_jobs.Job(1, 'seal', lambda stop: None); job.done.set()
        payload = SimpleNamespace(owner=None, close_report=None, close=lambda: calls.append('native-close'),
                                  status=lambda: None, quiescent=AssetCommit.quiescent)
        retired = RetiredImport(job, dict(payload=payload,
            prepared=SimpleNamespace(close=lambda: calls.append('cache-close'))))
        with self.assertRaises(RuntimeError):
            retired.poll()
        self.assertEqual(calls, ['native-close'])

    def test_actual_asset_commit_successful_pristine_close_releases_after_worker_done(self):
        import time
        from angelo_agent.fab_native_job import RetiredImport
        from angelo_agent.blender_asset_commit import AssetCommit
        calls = []
        def unexpected_native_close(*args):
            self.fail('A transaction that never began must not submit a native close')
        api = SimpleNamespace(asset_pending=lambda: None, asset_close=unexpected_native_close)
        row = {'kind': 'model', 'file': {'path': '/synthetic/asset.glb', 'relative': 'asset.glb',
                                       'size': 4, 'sha256': 'a' * 64}}
        # Use the actual constructor and owned private-copy lifetime, not an incomplete __new__ object.
        payload = AssetCommit(row, deadline=time.monotonic() + 30, api=api, payload_format='glb')
        self.addCleanup(payload.close)
        original_close = payload.payload.close
        def close_copy():
            calls.append('copy-close')
            original_close()
        payload.payload.close = close_copy
        job = fab_host_jobs.Job(1, 'seal', lambda stop: None)
        retired = RetiredImport(job, dict(payload=payload,
            prepared=SimpleNamespace(close=lambda: calls.append('cache-close'))))
        self.assertFalse(retired.poll())
        self.assertEqual(calls, [])
        job.done.set()
        self.assertTrue(retired.poll())
        self.assertEqual(calls, ['copy-close', 'cache-close'])
        self.assertIsNone(payload.scope)
        self.assertFalse(payload.begin_attempted)
        self.assertIsNone(payload.close_report)
        self.assertFalse(payload.payload.folder.exists())

    def test_each_absent_pristine_conjunct_retains_exact_native_cache_owner(self):
        from angelo_agent.fab_native_job import RetiredImport
        from angelo_agent.blender_asset_commit import AssetCommit, _NO_CLOSE_RETURN
        failures = ('scope', 'owner', 'lost-close', 'non-none-close', 'unknown-status',
                    'begin-attempted', 'missing-begin-proof')
        for failure in failures:
            calls = []
            payload = SimpleNamespace(scope=object() if failure == 'scope' else None,
                owner=object() if failure == 'owner' else None,
                begin_attempted=failure == 'begin-attempted', begin_failed_pristine=False,
                close_report=_NO_CLOSE_RETURN, quiescent=AssetCommit.quiescent)
            if failure == 'missing-begin-proof':
                del payload.begin_attempted
            def close():
                calls.append('close')
                if failure == 'lost-close':
                    raise RuntimeError('Lost close return')
                payload.close_report = object() if failure == 'non-none-close' else None
            payload.close = close
            payload.status = lambda: {'schema': 1, 'state': 'RECOVERY_REQUIRED',
                'published': None, 'restored': False, 'closed': False, 'recovery_required': True} \
                if failure == 'unknown-status' else None
            job = fab_host_jobs.Job(1, 'seal', lambda stop: None); job.done.set()
            retired = RetiredImport(job, dict(payload=payload,
                prepared=SimpleNamespace(close=lambda: calls.append('cache-close'))))
            with self.subTest(failure=failure), self.assertRaises(RuntimeError):
                retired.poll()
            self.assertNotIn('cache-close', calls)
            self.assertEqual(calls.count('close'), 1)
            self.assertIs(retired.state['payload'], payload)

    def test_uncertain_retired_native_close_keeps_cache_and_blocks_new_admission(self):
        from angelo_agent.fab_native_job import RetiredImport
        calls = []
        job = fab_host_jobs.Job(1, 'seal', lambda stop: None); job.done.set()
        class Payload:
            def close(self): calls.append('native-close')
            def status(self): return 'unknown'
            def quiescent(self, status): return False
        retired = RetiredImport(job, dict(payload=Payload(),
            prepared=SimpleNamespace(close=lambda: calls.append('cache-close'))))
        host = fab_host.Host(lambda **kw: None)
        host.retire_import(retired)
        host.pump()
        self.assertIs(host.retired_import, retired)
        self.assertNotIn('cache-close', calls)
        self.assertTrue(host.busy())
        with self.assertRaises(FabError):
            host.submit_agent(lambda session, stop: None)
        self.assertEqual(host.snapshot()['reason_code'], 'IMPORT_CLEANUP_REQUIRED')

    def test_plain_snapshot_never_constructs_connection(self):
        calls = []
        host = fab_host.Host(lambda **kw: calls.append(kw))
        self.assertEqual(host.snapshot()['status'], 'disconnected')
        self.assertEqual(calls, [])
        self.assertFalse(host.worker.busy())

    def test_real_main_owner_refuses_worker_snapshot(self):
        host = fab_host.Host(lambda **kw: None)
        errors = []
        def read():
            try:
                host.snapshot()
            except RuntimeError as error:
                errors.append(error)
        thread = threading.Thread(target=read)
        thread.start(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)

    def test_binding_rechecks_original_controller_session_and_choice(self):
        session = SimpleNamespace(active=True)
        controller = object()
        selection = (Choice('item', 'privateA'), Choice('variant', 'privateB'))
        bound = fab_approval.Binding(session, selection, generation='g', controller=controller,
                                    item_id='item', variant_id='variant', scene=3, location=(1, 2, 3))
        args = dict(session=session, generation='g', controller=controller, item_id='item',
                    variant_id='variant', scene=3, location=(1, 2, 3))
        self.assertIs(bound.expected(**args), selection)
        for key, value in [('controller', object()), ('session', SimpleNamespace(active=True)),
                           ('generation', 'other'), ('scene', 4), ('variant_id', 'other')]:
            with self.subTest(key=key), self.assertRaises(FabError):
                bound.expected(**dict(args, **{key: value}))
        self.assertNotIn('privateA', str(tuple(bound)))
        self.assertNotIn('privateB', str(tuple(bound)))

    def test_private_changed_choice_changes_public_binding_digest(self):
        session, controller = SimpleNamespace(active=True), object()
        def bind(secret):
            return fab_approval.Binding(session, (Choice('i', secret), Choice('v', 'same')),
                generation='g', controller=controller, item_id='i', variant_id='v', scene=1, location=())
        self.assertNotEqual(bind('before'), bind('after'))
        self.assertEqual(bind('before'), bind('before'))

    def test_binding_cannot_be_reassigned(self):
        bound = fab_approval.Binding(SimpleNamespace(active=True), (Choice('i', 'a'), Choice('v', 'b')),
            generation='g', controller=object(), item_id='i', variant_id='v', scene=1, location=())
        with self.assertRaises(AttributeError):
            bound._selection = ()

    def test_completed_canceled_result_is_released_once(self):
        cleaned = []
        job = fab_host_jobs.Job(1, 'prepare', lambda stop: None, cleanup=cleaned.append)
        held = object(); job.value = held; job.done.set()
        job.stop(); job.stop()
        self.assertEqual(cleaned, [held])
        with self.assertRaises(FabError):
            job.take()

    def test_early_prepare_failed_cleanup_retains_exact_result_until_retry_succeeds(self):
        from angelo_agent.fab_native_job import RetiredImport
        held, attempts = object(), []
        def cleanup(value):
            self.assertIs(value, held)
            attempts.append(value)
            if len(attempts) <= 2:
                raise RuntimeError('Synthetic lease cleanup failure')
        job = fab_host_jobs.Job(1, 'prepare', lambda stop: held, cleanup=cleanup)
        job.cancel.set(); job.value = held; job.done.set()
        retired = RetiredImport(job, dict(payload=None, prepared=None))
        for _ in range(2):
            with self.assertRaises(RuntimeError): retired.poll()
            self.assertIs(job.value, held)
        self.assertTrue(retired.poll())
        self.assertIsNone(job.value)
        self.assertEqual(len(attempts), 3)

    def test_actual_worker_can_chain_phase_after_done_without_false_busy(self):
        worker = fab_host_jobs.Worker()
        first = worker.submit(fab_host_jobs.Job(1, 'first', lambda stop: 1), replace=False)
        self.assertTrue(first.done.wait(2))
        self.assertEqual(first.take(), 1)
        second = worker.submit(fab_host_jobs.Job(2, 'second', lambda stop: 2), replace=False)
        self.assertTrue(second.done.wait(2))
        self.assertEqual(second.take(), 2)
        self.assertTrue(worker.join(2))

    def test_actual_worker_cancellation_retains_cleanup_owner(self):
        started, release, cleaned = threading.Event(), threading.Event(), []
        worker = fab_host_jobs.Worker()
        held = object()
        def work(stop):
            started.set()
            if not release.wait(2):
                raise RuntimeError('Fixture release not received')
            return held
        job = worker.submit(fab_host_jobs.Job(1, 'prepare', work, cleanup=cleaned.append))
        self.assertTrue(started.wait(2))
        job.stop(); release.set()
        self.assertTrue(job.done.wait(2)); self.assertTrue(worker.join(2))
        self.assertEqual(cleaned, [held])
        with self.assertRaises(FabError):
            job.take()



class HumanTwoPhaseTests(unittest.TestCase):
    def test_actual_human_host_pump_waits_for_seal_before_native_finish(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        import sys
        import angelo_agent
        from angelo_agent import fab_library_agent, fab_native_job, fab_provider, ui_text
        from angelo_agent.fab_catalog import Item, Variant
        from angelo_agent.fab_cache import PayloadProof
        from pathlib import Path
        order, released, gate = [], [], threading.Event()
        item = Item('00000000-0000-0000-0000-000000000001', 'Model', 'Author', 'Fab Standard',
                    (Variant('v', 'r', 'low', 4),), 'owned', ())
        expected = item, item.variants[0]
        session = SimpleNamespace(active=True, _lock=threading.RLock())
        def selection(identity, variant):
            self.assertEqual((identity, variant), ('fab:' + item.identity, 'v'))
            return expected
        session.selection = selection
        prepared = fab_provider.PreparedImport(item, {}, False,
            SimpleNamespace(release=lambda: released.append('cache')), session,
            SimpleNamespace(check=lambda: None), PayloadProof(Path('/synthetic/asset.glb'), 4, 'a' * 64))
        def prepare(identity, variant, *, expected, cancel):
            self.assertEqual((identity, variant), ('fab:' + item.identity, 'v'))
            self.assertEqual(expected, (item, item.variants[0]))
            order.append('prepare')
            return prepared
        session.prepare = prepare
        host = fab_host.Host(lambda **kw: None)
        host.session, host.selection = session, expected
        scene = SimpleNamespace(cursor=SimpleNamespace(location=(1, 2, 3)))
        window = SimpleNamespace(scene=scene)
        area = SimpleNamespace(type='VIEW_3D', as_pointer=lambda: 7, tag_redraw=lambda: None)
        region = SimpleNamespace(type='WINDOW'); area.regions = [region]
        state = {}
        overlay = SimpleNamespace(state_for=lambda area: state, _views={7: state})
        actions = SimpleNamespace(_record=lambda *args: order.append('result'), _notice=lambda *args: None)
        class Payload:
            def __init__(self, row, **kw): self.closed = self.sealed = False
            def seal_payload(self, row, stop):
                order.append('seal-start')
                if not gate.wait(2): raise RuntimeError('Fixture gate not released')
                stop.is_set()
                self.sealed = True; order.append('seal-end')
            def finish(self, context, row, **kw):
                if not self.sealed: raise AssertionError('Native finish before seal return')
                order.append('finish'); self.closed = True
                return dict(success=True)
        context = SimpleNamespace(temp_override=lambda **kw: nullcontext())
        fake_bpy = SimpleNamespace(context=context)
        bindings = {'angelo_agent.library_overlay': overlay, 'angelo_agent.library_actions': actions, 'bpy': fake_bpy}
        with patch.dict(sys.modules, bindings), patch.object(angelo_agent, 'library_overlay', overlay, create=True), \
                patch.object(angelo_agent, 'library_actions', actions, create=True), \
                patch.object(ui_text, 'iface_', side_effect=lambda text, **kw: text.format(**kw)), \
                patch.object(fab_native_job, 'ready'), patch.object(fab_native_job, 'AssetCommit', Payload), \
                patch.object(fab_native_job.live_target, 'window_ref', side_effect=lambda x: lambda: x), \
                patch.object(fab_native_job.live_target, 'scene_ref', side_effect=lambda x: lambda: x):
            from angelo_agent import live_windows
            with patch.object(live_windows, 'areas', return_value=[area]):
                host.import_pending = fab_library_agent.human_use(host, window, area, region,
                    'fab:' + item.identity, 'v')
                self.assertTrue(host.job.done.wait(2))
                host.pump()
                self.assertNotIn('finish', order)
                self.assertEqual(released, [])
                gate.set()
                self.assertTrue(host.job.done.wait(2))
                host.pump()
                self.assertEqual(order.count('finish'), 1)
                self.assertLess(order.index('seal-end'), order.index('finish'))
                self.assertIsNone(host.import_pending)
                self.assertTrue(host.worker.join(2))
                self.assertTrue(released)



class AgentTeardownTests(unittest.TestCase):
    def test_actual_agent_disconnect_retains_seal_and_cache_without_future_controller_poll(self):
        from pathlib import Path
        from unittest.mock import patch
        from angelo_agent import fab_library_agent, fab_native_job, fab_provider
        from angelo_agent.blender_asset_commit import AssetCommit, _NO_CLOSE_RETURN
        from angelo_agent.fab_catalog import Item, Variant
        from angelo_agent.fab_cache import PayloadProof
        order, gate, sealing = [], threading.Event(), threading.Event()
        item = Item('00000000-0000-0000-0000-000000000001', 'Model', 'Author', 'Fab Standard',
                    (Variant('v', 'r', 'low', 4),), 'owned', ())
        session = SimpleNamespace(active=True, _lock=threading.RLock())
        session.disconnect = lambda: setattr(session, 'active', False)
        prepared = fab_provider.PreparedImport(item, {}, False,
            SimpleNamespace(release=lambda: order.append('cache-close')), session,
            SimpleNamespace(check=lambda: None), PayloadProof(Path('/synthetic/asset.glb'), 4, 'a' * 64))
        session.prepare = lambda *args, **kw: prepared
        host = fab_host.Host(lambda **kw: None); host.session = session
        host.driver.connection = SimpleNamespace(close=lambda **kw: order.append('browser-close'))
        scene = SimpleNamespace(session_uid=7, cursor=SimpleNamespace(location=(1, 2, 3)))
        window, controller = SimpleNamespace(scene=scene), SimpleNamespace()
        args = dict(item_id='fab:' + item.identity, variant_id='v')
        binding = fab_approval.Binding(session, (item, item.variants[0]), generation=host.ticket()[0],
            controller=controller, item_id=args['item_id'], variant_id='v', scene=7, location=(1, 2, 3))
        controller.executing_call = SimpleNamespace(name='angelo_library_import', arguments=args, precondition=binding)
        class Payload:
            scope = owner = None
            begin_attempted = begin_failed_pristine = False
            close_report = _NO_CLOSE_RETURN
            quiescent = staticmethod(AssetCommit.quiescent)
            def __init__(self, row, **kw): self.sealed = False
            def seal_payload(self, row, stop):
                sealing.set()
                if not gate.wait(2): raise RuntimeError('Fixture gate not released')
                self.sealed = True; order.append('seal-end')
            def close(self):
                if not self.sealed: raise AssertionError('Close raced active seal')
                order.append('native-close'); self.close_report = None
            def status(self): return None
            def finish(self, *args, **kw): raise AssertionError('Canceled agent reached artist mutation')
        with patch.object(fab_host, 'owner', return_value=host), patch.object(fab_native_job, 'ready'), \
                patch.object(fab_native_job, 'AssetCommit', Payload), \
                patch.object(fab_native_job.live_target, 'window_ref', side_effect=lambda x: lambda: x), \
                patch.object(fab_native_job.live_target, 'scene_ref', side_effect=lambda x: lambda: x):
            pending = fab_library_agent.use(SimpleNamespace(controller=controller, window=window), args)
            self.assertIs(host.active_import, pending)
            self.assertTrue(host.job.done.wait(2)); self.assertIsNone(pending.poll())
            self.assertTrue(sealing.wait(2))
            host.disconnect()
            self.assertIsNone(host.active_import)
            self.assertIsNotNone(host.retired_import)
            self.assertNotIn('native-close', order); self.assertNotIn('cache-close', order)
            # The controller drops its pending and never polls it again.
            controller.executing_call = None
            gate.set(); self.assertTrue(host.worker.join(2))
            host.pump()
            self.assertIsNone(host.retired_import)
            self.assertLess(order.index('seal-end'), order.index('native-close'))
            self.assertLess(order.index('native-close'), order.index('cache-close'))
            self.assertIn('browser-close', order)
            with patch.object(fab_host, '_host', host):
                fab_host.unregister()
                self.assertIsNone(fab_host._host)


if __name__ == '__main__':
    unittest.main()
