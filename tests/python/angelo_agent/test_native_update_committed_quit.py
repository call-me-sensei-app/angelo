# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Updater invocation/cleanup contracts. Typed native producer and real exit remain native gates."""
import suite_isolation  # noqa: F401
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import update_install
from notices_fakes import Notices

NONCE = 'a' * 32


class CommittedQuitTests(unittest.TestCase):
    def setUp(self):
        context = Notices()
        self.n = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.m = self.n.update_restart
        self.m._installer = None
        self.addCleanup(setattr, self.m, '_installer', None)
        for name in ('depsgraph_update_post', 'load_pre', 'save_pre', 'undo_pre', 'redo_pre'):
            setattr(self.n.bpy.app.handlers, name, [])
        self.owner = object()
        self.events = []
        self.exited = False
        self.live = SimpleNamespace(set_restarting=lambda value: self.events.append(('live', value)),
                                    target=lambda value: value,
                                    preparer=lambda: SimpleNamespace(still_valid=lambda ready: True))
        self.process = SimpleNamespace(stdin=io.BytesIO(), poll=lambda: 0 if self.exited else None,
                                       kill=lambda: self.events.append('kill'))
        self.catalog_generation = 17
        self.native = SimpleNamespace(_update_quit_catalog_generation=lambda: self.catalog_generation,
                                      _update_quit_revoke=lambda owner: self.events.append(('revoke', owner)) or True,
                                      _update_quit_status=lambda owner: dict(schema=1, phase='PENDING', channel_closed=False))
        self.record = dict(process=self.process, owner=self.owner, native=self.native, invocation=NONCE,
                           live=self.live, ready=SimpleNamespace(work='unused'), offer=object(), changed=False,
                           original_closed=False, stopping=False, drain_started=None, kill_attempted=False,
                           started=self.m.time.monotonic(), stamp=('stamp',), catalog_generation=17)
        self.m._installer = self.record
        self.patches = [mock.patch.object(self.m, '_document_stamp', return_value=('stamp',), create=True),
                        mock.patch.object(self.m, '_other_instances', return_value=[]),
                        mock.patch.object(self.m.work_guard, 'reasons', return_value=[]),
                        mock.patch.object(self.m.update_install, 'ready', return_value=True),
                        mock.patch.object(self.m, '_not_started')]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_exact_pending_owner_and_closed_original_can_confirm(self):
        self.record['original_closed'] = True
        self.assertIs(self.m._confirm_installer_quit(NONCE), True)

    def test_second_dirty_catalog_edit_revokes_explicit_discard(self):
        self.record['original_closed'] = True
        # Dirty state was already present and explicitly discarded; its baseline
        # stays valid until a second edit, even with no depsgraph notification.
        self.assertIs(self.m._confirm_installer_quit(NONCE), True)
        self.catalog_generation += 1
        self.assertIs(self.m._confirm_installer_quit(NONCE), False)
        self.assertEqual(self.record['catalog_generation'], 17)

    def test_unavailable_catalog_generation_never_confirms(self):
        self.record['original_closed'] = True
        for value in (0, True, -1, 1 << 64):
            with self.subTest(value=value):
                self.catalog_generation = value
                self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_capture_precedes_helper_start_and_never_refreshes_baseline(self):
        sequence = []
        def capture(native):
            sequence.append('capture')
            return self.catalog_generation
        def start():
            sequence.append('start')
            self.catalog_generation += 1
        with mock.patch.object(self.native, '_update_quit_catalog_generation',
                               side_effect=lambda: capture(self.native)):
            record = self._launch_with(start_callback=start)
        self.assertEqual(sequence, ['capture', 'start'])
        self.assertEqual(record['catalog_generation'], 17)
        self.assertEqual(self.catalog_generation, 18)
        # Poll must stand down before it ever asks native code for a quit owner.
        self.m._poll_installer()
        self.assertTrue(record['stopping'])
        self.assertIsNone(record['owner'])
        self.assertEqual(record['catalog_generation'], 17)

    def test_other_invocation_never_confirms(self):
        self.record['original_closed'] = True
        self.assertIs(self.m._confirm_installer_quit('b' * 32), False)

    def test_open_original_writer_never_confirms(self):
        self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_cancelled_native_handler_never_confirms(self):
        self.record['original_closed'] = True
        self.native._update_quit_status = lambda owner: dict(schema=1, phase='REVOKED', channel_closed=True)
        self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_native_schema_bool_is_not_version_one(self):
        self.record['original_closed'] = True
        self.native._update_quit_status = lambda owner: dict(schema=True, phase='PENDING', channel_closed=False)
        self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_document_change_revokes_the_prior_discard_decision(self):
        self.record['original_closed'] = True
        self.m._installer_document_changed()
        self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_late_work_never_confirms(self):
        self.record['original_closed'] = True
        with mock.patch.object(self.m.work_guard, 'reasons', return_value=['activity']):
            self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_stale_readiness_never_confirms(self):
        self.record['original_closed'] = True
        with mock.patch.object(self.m.update_install, 'ready', return_value=False):
            self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_unknown_native_status_never_confirms(self):
        self.record['original_closed'] = True
        self.native._update_quit_status = mock.Mock(side_effect=RuntimeError('private detail'))
        self.assertIs(self.m._confirm_installer_quit(NONCE), False)

    def test_kill_failure_retains_owner_and_blocks_relaunch(self):
        self.process.kill = mock.Mock(side_effect=OSError('still alive'))
        self.assertIs(self.m._stand_down(self.record), False)
        self.assertIs(self.m._installer, self.record)
        self.assertNotIn(('live', None), self.events)
        with mock.patch.object(self.m.update_install, 'start') as start:
            self.m.launch(object(), object())
        start.assert_not_called()
        self.assertIs(self.m._installer, self.record)

    def test_revoke_failure_retains_even_exited_leader(self):
        self.exited = True
        self.native._update_quit_revoke = mock.Mock(side_effect=RuntimeError('unknown'))
        self.assertIs(self.m._stand_down(self.record), False)
        self.assertIs(self.m._installer, self.record)

    def test_confirmed_revoked_channel_and_exited_leader_release_once(self):
        self.exited = True
        self.assertIs(self.m._stand_down(self.record), True)
        self.assertIsNone(self.m._installer)
        self.assertEqual(self.events.count(('live', None)), 1)
        self.assertIs(self.m._stand_down(self.record), False)

    def test_foreign_record_cannot_revoke_current_owner(self):
        self.assertIs(self.m._stand_down(dict(self.record)), False)
        self.assertEqual(self.events, [])

    def test_ready_timeout_starts_checked_drain_without_quit(self):
        self.record['started'] -= 60
        self.m._poll_installer()
        self.assertTrue(self.record['stopping'])
        self.assertIn('kill', self.events)
        self.assertIs(self.m._installer, self.record)

    def test_native_handler_cancellation_starts_checked_drain(self):
        self.native._update_quit_status = lambda owner: dict(schema=1, phase='REVOKED', channel_closed=True)
        self.m._poll_installer()
        self.assertTrue(self.record['stopping'])
        self.assertIs(self.m._installer, self.record)

    def test_cleanup_deadline_stops_polling_but_keeps_unproved_owner(self):
        self.record['stopping'] = True
        self.record['drain_started'] = self.m.time.monotonic() - 60
        self.assertIsNone(self.m._poll_installer())
        self.assertIs(self.m._installer, self.record)

    def _launch_with(self, *, timer_error=False, card_error=False, start_callback=None):
        self.m._installer = None
        ready = SimpleNamespace(install_root='/app', family='macos', work='/work', version='1.2.3',
                                payload='/new', gatekeeper='0', fingerprint='')
        with mock.patch.object(self.m, '_quit_native', return_value=self.native, create=True), \
             mock.patch.object(self.m, '_ready_module', return_value=self.live), \
             mock.patch.object(self.m, '_requirement', return_value='requirement'), \
             mock.patch.object(self.m, '_executable', return_value='Angelo'), \
             mock.patch.object(self.m, '_launcher', return_value=''), \
             mock.patch.object(self.m.update_install, 'start',
                               side_effect=lambda request: (start_callback() if start_callback else None) or self.process), \
             mock.patch.object(self.m.bpy.app.timers, 'register', side_effect=RuntimeError('timer') if timer_error else None), \
             mock.patch.object(self.m.card_host, 'is_active', return_value=False), \
             mock.patch.object(self.m.card_host, 'request', side_effect=RuntimeError('card') if card_error else None):
            self.m.launch(object(), ready)
        return self.m._installer

    def test_timer_registration_failure_retains_and_revokes_exact_helper(self):
        try:
            record = self._launch_with(timer_error=True)
        except RuntimeError:
            record = self.m._installer  # Old launch leaks its exact helper on setup exception.
        self.assertIsNotNone(record, 'Timer setup failure lost the original installer process owner')
        self.assertIs(record['process'], self.process)
        self.assertTrue(record['stopping'])
        self.assertTrue(self.process.stdin.closed)
        self.assertIn('kill', self.events)

    def test_card_setup_failure_retains_and_revokes_exact_helper(self):
        try:
            record = self._launch_with(card_error=True)
        except RuntimeError:
            record = self.m._installer
        self.assertIsNotNone(record, 'Card setup failure lost the original installer process owner')
        self.assertTrue(record['stopping'])
        self.assertTrue(self.process.stdin.closed)

    def test_missing_native_protocol_never_starts_helper(self):
        self.m._installer = None
        with mock.patch.object(self.m, '_ready_module', return_value=self.live), \
             mock.patch.object(self.m, '_quit_native', side_effect=RuntimeError('unavailable')), \
             mock.patch.object(self.m.update_install, 'start') as start:
            self.m.launch(object(), object())
        start.assert_not_called()
        self.assertIsNone(self.m._installer)

    def test_cancelled_owner_is_revoked_before_popen_kill(self):
        self.native._update_quit_status = lambda owner: dict(schema=1, phase='REVOKED', channel_closed=True)
        self.m._poll_installer()
        self.assertLess(self.events.index(('revoke', self.owner)), self.events.index('kill'))

    def test_lost_revoke_return_never_clears_restarting(self):
        self.exited = True
        self.native._update_quit_revoke = mock.Mock(side_effect=RuntimeError('lost return'))
        self.m._stand_down(self.record)
        self.assertNotIn(('live', None), self.events)
        self.assertIs(self.m._installer, self.record)


class InstallerChannelTests(unittest.TestCase):
    def test_readiness_requires_the_exact_nonce_and_no_extra_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / update_install.READY_FILE
            for value, expected in ((NONCE + '\n', True), ('', False), ('b'*32+'\n', False),
                                    (NONCE + '\nextra', False)):
                path.write_bytes(value.encode('ascii'))   # As both helpers write it: a bare LF (text mode is CRLF on Windows).
                self.assertIs(update_install.ready(folder, NONCE), expected)

    def test_start_refuses_missing_invocation_before_touching_work(self):
        with tempfile.TemporaryDirectory() as folder:
            request = update_install.Request('macos', Path(folder)/'new', 1, '1.0.1', 'Angelo')
            with self.assertRaises(ValueError):
                update_install.start(request, popen=mock.Mock())
            self.assertFalse(request.work.exists())
