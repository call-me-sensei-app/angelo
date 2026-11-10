# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual Resend→console and hold→dispatch regressions; the old source must reach assertions."""
import suite_isolation  # noqa: F401
import contextlib
from types import ModuleType, SimpleNamespace
from unittest import mock

from test_native_notice_resend import Stubbed, chat, PHOTO


class ActualPaths(Stubbed):
    def route(self, controller, *, delayed=False):
        import angelo_agent
        import sys
        from angelo_agent import document_scene, worker_routing
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        # The native UI is a boundary, not the lifecycle under test. Importing its whole DCC stack
        # needs Blender/NumPy and would stop before console, routing or pending code is exercised.
        ui = ModuleType('angelo_agent.ui')
        ui.prepare_controller = mock.Mock()
        ui.main_window = mock.Mock(return_value=None)
        stack.enter_context(mock.patch.dict(sys.modules, {'angelo_agent.ui': ui}))
        stack.enter_context(mock.patch.object(angelo_agent, 'ui', ui, create=True))
        stack.enter_context(mock.patch.object(document_scene, 'refuse'))
        stack.enter_context(mock.patch.object(worker_routing, 'worker_snapshots', return_value=delayed))
        delivery = []
        def deliver(owner, window, text, send, failed):
            delivery.append((send, failed))
            if not delayed:
                send(SimpleNamespace(observation={}, recovery_evidence={}))
        if delayed:
            from angelo_agent import worker_queue
            stack.enter_context(mock.patch.object(worker_routing, 'hosted', return_value=False))
            def resume(owner, send_later, failed):
                delivery.append((send_later, failed))
                return True
            stack.enter_context(mock.patch.object(worker_queue, 'resume', side_effect=resume))
        else:
            stack.enter_context(mock.patch.object(worker_routing, 'deliver', side_effect=deliver))
        return delivery

    def test_delivered_steer_does_not_replace_failed_request_or_attachments(self):
        from angelo_agent import chat_resend
        owner = chat()
        owner.state['messages'].insert(-1, {'role': 'user', 'text': 'B: make it blue', 'steer': True, 'steer_id': 7})
        owner.turn_outcome = ('failed', False, False, len(owner.state['messages']))
        owner._request_message_index = len(owner.state['messages']) - 2  # Mutable latest index is not authority.
        owner.turn_running = lambda: False
        owner.chosen_tools = []
        sent = []
        owner.send = lambda text, **options: sent.append((text, options['references']))
        self.route(owner)
        self.assertTrue(chat_resend.resend(owner, owner.state['messages'][-1]))
        self.assertEqual(sent, [('Model a chair', owner.state['messages'][0]['references'])])

    def test_delayed_refusal_keeps_both_controls_and_unrelated_composer(self):
        from angelo_agent import chat_resend, drafts
        owner = chat()
        owner.turn_running = lambda: False
        owner.unsent_draft = None
        owner.emit = lambda kind, body: owner.state['messages'].append(dict(body))
        composer = SimpleNamespace(text='Unrelated new draft', cursor=4, selection=(1, 4), undo=['earlier'])
        before = (dict(vars(composer)), list(owner.attachments), list(owner.chosen_tools))
        callbacks = self.route(owner, delayed=True)
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        prematurely_consumed = getattr(owner, '_resent_row', None)
        from angelo_agent.worker_dispatch import WorkerGone
        callbacks[0][1](WorkerGone('Connection refused'))
        self.assertIsNone(owner.unsent_draft, 'A refused retry must not enter an unrelated composer')
        self.assertIsNone(prematurely_consumed, 'Returning while parked is not admission')
        self.assertIsNotNone(drafts.failed_request(owner, original))
        self.assertTrue(chat_resend.offered(owner, original, 'unknown'))
        self.assertIsNotNone(chat_resend.refusal(owner, original))
        self.assertIsNone(owner.unsent_draft)
        self.assertEqual(before, (vars(composer), owner.attachments, owner.chosen_tools))
        callbacks[0][1](RuntimeError('duplicate'))
        self.assertEqual(chat_resend.refusal(owner, original)['notice']['text'], 'Connection refused')

    def test_held_retry_dispatch_never_runs_unrelated_chosen_tools(self):
        from angelo_agent import chat_pending, chat_resend, chosen_tools_host, steering
        owner = chat()
        owner.state['provider'] = 'codex'
        owner.inbox = steering.Inbox()
        owner.turn_running = lambda: owner.busy
        owner.busy = True
        owner.provider = None
        owner._host_request = None
        sent = []
        owner.send = lambda text, **options: sent.append((text, options))
        self.route(owner)
        with mock.patch.object(chat_pending, 'steerable', return_value=True), mock.patch.object(chat_pending, 'offer'):
            self.assertTrue(chat_resend.resend(owner, owner.state['messages'][-1]))
        held = owner.inbox.items()[0]
        # With pictures this is queued even on the old implementation; reach the actual dispatch defect.
        self.assertFalse(held.can_steer)
        owner.inbox.hold('Neighbor normal request', (), steerable=False)
        owner.busy = False
        with mock.patch.object(chosen_tools_host, 'perform_for_request', return_value=[]) as tools:
            self.assertTrue(chat_pending.dispatch_due(owner))
            tools.assert_not_called()
        self.assertEqual(sent[0][0], 'Model a chair')
        self.assertEqual(sent[0][1]['references'], owner.state['messages'][0]['references'])
        self.assertIs(sent[0][1].get('use_composer_choices'), False)
        self.assertEqual(len(owner.inbox.items()), 1, 'A retry is a standalone request, never merged')
        self.assertEqual(owner.chosen_tools, [{'tool': 'bevel'}])

    def test_first_nonperson_request_before_error_blocks_older_person(self):
        from angelo_agent import drafts
        for row in ({'role': 'user', 'text': 'host', 'request_kind': 'angelo_event'},
                    {'role': 'user', 'text': 'worker', 'request_kind': 'worker_brief'},
                    {'role': 'user', 'text': 'external', 'request_kind': 'external_task'}):
            owner = chat()
            owner.state['messages'].insert(-1, row)
            owner.turn_outcome = ('failed', False, False, len(owner.state['messages']))
            self.assertIsNone(drafts.failed_request(owner))

    def test_pending_reload_retains_whole_retry_policy_and_legacy_defaults(self):
        from angelo_agent import chat_pending, chat_resend, steering
        owner = chat()
        owner.inbox = steering.Inbox()
        owner.busy = True
        owner.turn_running = lambda: owner.busy
        with mock.patch.object(chat_pending, 'steerable', return_value=True), mock.patch.object(chat_pending, 'offer'):
            self.assertTrue(chat_resend.resend(owner, owner.state['messages'][-1]))
        loaded = steering.Inbox()
        loaded.load(owner.inbox.records(), 'relaunch')
        item = loaded.items()[0]
        self.assertFalse(getattr(item, 'run_chosen_tools', True))
        self.assertFalse(item.can_steer)
        self.assertEqual(item.references, owner.state['messages'][0]['references'])
        loaded.load([{'text': 'Legacy normal', 'references': [], 'mode': steering.QUEUE}], 'relaunch')
        self.assertTrue(getattr(loaded.items()[0], 'run_chosen_tools', True))
        self.assertFalse(getattr(loaded.items()[0], 'separate', False))


class MirrorPaths(Stubbed):
    def setUp(self):
        import tempfile
        import uuid
        from pathlib import Path
        from angelo_agent.controller import Controller
        from angelo_agent.worker_mirror import MirrorController
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory, session = Path(temporary.name), str(uuid.uuid4())
        worker = Controller(directory, session_id=session)
        worker.save()
        self.owner = MirrorController(directory, session, lambda *args, **kwargs: {'ok': True})
        rows = [{'role': 'user', 'text': 'A exact request'}, {'role': 'error', 'text': 'RuntimeError: offline'}]
        self.owner.state['messages'] = rows
        self.owner.turn_outcome = ('failed', False, False, len(rows))
        self.owner.unsent_draft = None
        self.owner.chosen_tools = []
        self.route = ActualPaths.route.__get__(self)
        self.route(self.owner)
        from angelo_agent import worker_mirror_ops as ops
        self.ops = ops
        def queue(mirror, op, kind, before=None, **fields):
            return ops.Request(mirror, op, kind, fields)
        self.queue = mock.patch.object(ops, 'submit', side_effect=queue)
        self.queue.start()
        self.addCleanup(self.queue.stop)

    def test_real_mirror_send_return_is_not_ack_and_refusal_stays_notice_owned(self):
        from angelo_agent import chat_resend, drafts
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        self.assertIsNone(getattr(owner, '_resent_row', None))
        self.ops._refused(request, {'ok': False, 'error': {'code': 'CANCELLED'}})
        self.assertIsNone(owner.unsent_draft)
        self.assertIsNotNone(drafts.failed_request(owner))
        self.assertIsNotNone(chat_resend.refusal(owner, owner.state['messages'][-1]))
        self.assertFalse(self.ops._confirmed(request), 'Late ack cannot take a refused request')

    def test_admitted_mirror_later_not_sent_returns_retry_controls_not_composer(self):
        from angelo_agent import chat_resend, drafts, worker_mirror_events
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        self.assertTrue(self.ops._confirmed(request, {"ok": True, "_epoch": 31, "_generation": 7}))
        self.assertIs(owner._resent_row, original)
        owner._worker_request = (31, 7)
        owner.state['messages'].append({'role': 'user', 'text': 'A exact request'})
        worker_mirror_events.finish(owner, {'status': 'failed', 'not_sent': True, '_epoch': 31, '_generation': 7})
        self.assertIsNone(owner.unsent_draft)
        self.assertIsNotNone(drafts.failed_request(owner))
        self.assertTrue(chat_resend.offered(owner, owner.state['messages'][-1], 'unknown'))

    def test_old_request_refusal_cannot_change_replacement_attempt(self):
        from angelo_agent import chat_resend
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        old = owner._sending
        replacement = self.ops.Request(owner, 'chat.send', 'send', {'text': 'new request'})
        owner._request_generation += 1
        owner._sending = replacement
        before = (list(owner.state['messages']), owner.version, owner.unsent_draft)
        self.assertFalse(self.ops._refused(old, {'ok': False, 'error': {'code': 'CANCELLED'}}))
        self.assertEqual(before, (owner.state['messages'], owner.version, owner.unsent_draft))
        self.assertIs(owner._sending, replacement)

    def test_synchronous_parked_restart_failure_keeps_notice_and_draft(self):
        from angelo_agent import chat_resend, drafts, worker_mirror_park, worker_queue
        owner = self.owner
        owner.send_op = None
        original = owner.state['messages'][-1]
        def refuse_resume(mirror, later, failed):
            failed(RuntimeError('Cannot restart this worker'))
            return True
        with mock.patch.object(worker_mirror_park, 'waits', return_value=True), \
                mock.patch.object(worker_queue, 'resume', side_effect=refuse_resume):
            self.assertTrue(chat_resend.resend(owner, original))
        self.assertIsNone(owner.unsent_draft)
        self.assertIsNotNone(drafts.failed_request(owner))
        self.assertIsNotNone(chat_resend.refusal(owner, owner.state['messages'][-1]))

    def test_withdraw_queued_retry_recovers_notice_without_composer_restore(self):
        from angelo_agent import chat_resend, drafts
        owner = self.owner
        self.assertTrue(chat_resend.resend(owner, owner.state['messages'][-1]))
        request = owner._sending
        owner._op_lane = self.ops.Lane()
        self.assertTrue(self.ops.withdraw(owner))
        self.assertIsNone(owner.unsent_draft)
        self.assertIsNotNone(drafts.failed_request(owner))
        self.assertFalse(self.ops._confirmed(request))

    def test_ack_before_frame_binds_once_and_duplicate_ack_does_not_repeat(self):
        from angelo_agent import chat_resend, worker_mirror_events
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        answer = {'ok': True, '_epoch': 31, '_generation': 7}
        self.assertTrue(self.ops._confirmed(request, answer))
        receipt = owner._send_submission
        self.assertIs(owner._resent_row, original)
        version = owner.version
        self.assertFalse(self.ops._confirmed(request, answer))
        self.assertEqual(owner.version, version)
        owner._worker_request = (31, 7)
        worker_mirror_events.signs(owner, {'accepted': True, '_epoch': 31, '_generation': 7})
        self.assertIsNone(owner._send_submission)
        self.assertTrue(receipt._settled)

    def test_finished_before_ack_reconciles_only_after_matching_exact_answer(self):
        from angelo_agent import chat_resend, worker_mirror_events
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        owner._worker_request = (31, 7)
        self.ops.took(owner)   # A user frame alone does not identify the local Send.
        self.assertIs(owner._sending, request)
        worker_mirror_events.finish(owner, {'status': 'completed', '_epoch': 31, '_generation': 7})
        self.assertIsNone(getattr(owner, '_resent_row', None))
        self.assertTrue(self.ops._confirmed(request, {'ok': True, '_epoch': 31, '_generation': 7}))
        self.assertIs(owner._resent_row, original)
        self.assertIsNone(owner._send_submission, 'The matching completed turn cannot leave a pending owner')

    def test_not_sent_before_ack_waits_for_exact_causal_answer_then_recovers(self):
        from angelo_agent import chat_resend, drafts, worker_mirror_events
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        owner._worker_request = (31, 7)
        worker_mirror_events.finish(owner, {'status': 'failed', 'not_sent': True, '_epoch': 31, '_generation': 7})
        self.assertIsNone(owner.unsent_draft)
        self.assertIsNone(getattr(owner, '_resent_row', None))
        self.assertTrue(self.ops._confirmed(request, {'ok': True, '_epoch': 31, '_generation': 7}))
        self.assertIsNotNone(drafts.failed_request(owner))
        self.assertIsNone(owner._send_submission)

    def test_newer_unrelated_worker_pair_cannot_rebind_or_consume_origin(self):
        from angelo_agent import chat_resend, worker_mirror_events
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        receipt = owner._send_submission
        owner._worker_request = (31, 8)
        worker_mirror_events.signs(owner, {'accepted': True, '_epoch': 31, '_generation': 8})
        self.ops._confirmed(request, {'ok': True, '_epoch': 31, '_generation': 7})
        self.assertIsNone(getattr(owner, '_resent_row', None))
        self.assertFalse(receipt._settled)
        self.assertEqual(receipt._worker_pair, (31, 7))
        self.assertFalse(receipt.bind_worker(request, (31, 8)), 'A bound pair is immutable')
        self.assertEqual(receipt._worker_pair, (31, 7))
        self.assertFalse(chat_resend.offered(owner, original, 'unknown'))
        self.assertIsNotNone(owner._resending, 'A new worker pair cannot unlock duplicate retry')
        self.assertIsNone(owner.unsent_draft)

    def test_pairless_old_worker_ack_is_explicit_unknown_not_refusal_or_delivery(self):
        from angelo_agent import chat_resend
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        self.ops._confirmed(request, {'ok': True})
        self.assertIsNone(getattr(owner, '_resent_row', None))
        self.assertIsNotNone(owner._resending)
        self.assertFalse(chat_resend.offered(owner, original, 'unknown'), 'Unknown delivery must block duplicate retry')
        self.assertIn('delivery is unknown', chat_resend.refusal(owner, original)['notice']['text'])
        self.assertIsNone(owner.unsent_draft)

    def test_lost_ack_and_worker_end_after_send_do_not_invent_not_sent(self):
        from angelo_agent import chat_resend
        owner = self.owner
        original = owner.state['messages'][-1]
        self.assertTrue(chat_resend.resend(owner, original))
        request = owner._sending
        request.state = 'sent'
        self.ops.worker_ended(owner)
        self.assertIsNone(getattr(owner, '_resent_row', None))
        self.assertIsNone(owner.unsent_draft)
        self.assertIsNotNone(owner._resending)
        self.assertIn('delivery is unknown', chat_resend.refusal(owner, original)['notice']['text'])
        self.assertFalse(self.ops._confirmed(request, {'ok': True, '_epoch': 31, '_generation': 7}))


class UnchangedEntryPoints(Stubbed):
    def test_actual_voice_entry_still_checks_model_chat_before_work(self):
        from angelo_agent.controller import Controller
        class ModelChatRefused(RuntimeError):
            pass
        owner = SimpleNamespace(require_model_chat=mock.Mock(side_effect=ModelChatRefused('voice entry reached')))
        with self.assertRaisesRegex(ModelChatRefused, 'voice entry reached'):
            Controller.start_voice(owner)
        owner.require_model_chat.assert_called_once_with()

    def test_actual_worker_send_answer_identifies_post_send_request(self):
        from angelo_agent.worker_chat_host import WorkerChatHost
        chat = SimpleNamespace(_epoch=11, _request_generation=6, attachments=[])
        def send(text, **options):
            self.assertEqual(text, 'Actual request')
            chat._epoch, chat._request_generation = 12, 7
        chat.send = send
        host = SimpleNamespace(arrivals=SimpleNamespace(send=lambda chat, request, start: start()))
        answer = WorkerChatHost._send(host, chat, {'text': 'Actual request', 'attachments': []})
        self.assertEqual(answer, {'ok': True, '_epoch': 12, '_generation': 7})


class LocalControllerPaths(Stubbed):
    def test_real_local_send_and_pump_not_started_recovers_notice_without_composer_restore(self):
        import tempfile
        from angelo_agent import chat_resend, drafts, thread_start
        from angelo_agent.controller import Controller
        with tempfile.TemporaryDirectory() as directory:
            owner = Controller(directory)
            try:
                original = {'role': 'error', 'text': 'RuntimeError: offline'}
                owner.state['messages'] = [{'role': 'user', 'text': 'A exact request'}, original]
                owner.turn_outcome = ('failed', False, False, 2)
                owner.chosen_tools = []
                route = ActualPaths.route.__get__(self)
                route(owner)
                with mock.patch.object(thread_start, 'started', return_value=False) as start:
                    self.assertTrue(chat_resend.resend(owner, original))
                    owner.pump_events()
                start.assert_called_once()
                self.assertIsNone(owner.unsent_draft)
                self.assertIsNotNone(drafts.failed_request(owner))
                self.assertIsNone(owner._send_submission)
                self.assertTrue(chat_resend.offered(owner, owner.state['messages'][-1], 'unknown'))
            finally:
                owner.close()   # Drain telemetry/usage writers before TemporaryDirectory removes their files.
