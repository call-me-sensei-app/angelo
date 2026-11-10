# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Safe observations and failed-drain fixtures; no browser/process/network is launched."""
import suite_isolation  # noqa: F401

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
import host_test_policy  # noqa: E402
if sys.platform != 'darwin':   # The macOS browser process's record (fab_browser_process); Windows has its own.
    raise unittest.SkipTest(host_test_policy.environment_skip(
        'the macOS Fab browser record; Windows runs test_native_fab_windows_*, Linux has no Fab browser'))
from angelo_agent import fab_browser_diagnostics as diagnostic
from angelo_agent import fab_browser_ownership as ownership
from angelo_agent.fab_browser_connection import Connection
from angelo_agent.fab_browser_process import BrowserProcess, BrowserUnavailable

MARKER = 'a' * 32


class FabBrowserDiagnosticTests(unittest.TestCase):
    def test_legacy_layout_reason_remains_actionable_and_arbitrary_reasons_stay_closed(self):
        error = BrowserUnavailable('PROFILE_UNAVAILABLE', reason='PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY')
        self.assertEqual(error.reason, 'PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY')
        for unknown in ('arbitrary-unreviewed-native-text', ['PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY'],
                        {'reason': 'PROFILE_LEGACY_LAYOUT_REQUIRES_RECOVERY'}):
            with self.subTest(reason_type=type(unknown).__name__):
                self.assertIsNone(BrowserUnavailable('PROFILE_UNAVAILABLE', reason=unknown).reason)

    def test_untrusted_exception_text_is_not_exposed(self):
        error = ownership.OwnershipUnavailable('credential-bearing text must not escape')
        self.assertEqual(error.code, 'OWNERSHIP_UNAVAILABLE')
        self.assertNotIn('credential-bearing', str(error))

    def test_process_timeout_has_fixed_reason(self):
        with patch.object(ownership.subprocess, 'run', side_effect=subprocess.TimeoutExpired('private', 1)):
            with self.assertRaises(ownership.OwnershipUnavailable) as caught:
                ownership._run(['/bin/ps'], time.monotonic() + 5)
        self.assertEqual(caught.exception.code, 'INSPECTION_TIMEOUT')
        self.assertNotIn('private', str(caught.exception))

    def test_process_denial_has_fixed_reason(self):
        with patch.object(ownership.subprocess, 'run', side_effect=PermissionError('private detail')):
            with self.assertRaises(ownership.OwnershipUnavailable) as caught:
                ownership._run(['/bin/ps'], time.monotonic() + 5)
        self.assertEqual(caught.exception.code, 'INSPECTION_DENIED')

    def test_oversized_inspection_has_fixed_reason(self):
        with patch.object(ownership.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='x' * 9)), \
                patch.object(ownership, 'MAX_OUTPUT', 8):
            with self.assertRaises(ownership.OwnershipUnavailable) as caught:
                ownership._run(['/bin/ps'], time.monotonic() + 5)
        self.assertEqual(caught.exception.code, 'INSPECTION_OVERSIZED')

    def write(self, folder, identity=None):
        folder.chmod(0o700)
        info = folder.stat()
        diagnostic.write_record(folder, identity or (info.st_dev, info.st_ino), MARKER, 12345,
                                (12345, 'fixed synthetic start', '/synthetic/Chrome'), 'spawned')
        return folder / diagnostic.NAME

    def test_record_is_private_observation_with_recoverable_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(Path(directory))
            data = json.loads(path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(data['authority'])
            self.assertEqual(data['marker'], MARKER)
            self.assertEqual(data['leader_identity'], [12345, 'fixed synthetic start', '/synthetic/Chrome'])
            self.assertEqual(list(Path(directory).glob('.fab-owner-*')), [])

    def test_changed_profile_identity_refuses_before_record(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with self.assertRaisesRegex(ValueError, 'profile changed'):
                self.write(folder, (folder.stat().st_dev, folder.stat().st_ino + 1))
            self.assertFalse((folder / diagnostic.NAME).exists())

    def test_record_replacement_never_follows_existing_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'profile';folder.mkdir()
            unrelated = Path(directory) / 'unrelated';unrelated.write_text('preserve')
            (folder / diagnostic.NAME).symlink_to(unrelated)
            path = self.write(folder)
            self.assertFalse(path.is_symlink())
            self.assertEqual(unrelated.read_text(), 'preserve')

    def test_refused_helper_record_is_bounded_and_has_no_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory);folder.chmod(0o700);info = folder.stat()
            helper = dict(pid=41, parent=1, group=99, start='start', program='/synthetic/Chrome',
                          reason='HELPER_OWNERSHIP_UNKNOWN', marker_readable=True, marker_present=False, profile_named=True)
            diagnostic.write_record(folder, (info.st_dev, info.st_ino), MARKER, 12345, None,
                                    'drain_refused', helper=helper)
            record = json.loads((folder / diagnostic.NAME).read_text())
            self.assertFalse(record['authority'])
            self.assertEqual(record['helper'], helper)
            with self.assertRaisesRegex(ValueError, 'helper observation invalid'):
                diagnostic.write_record(folder, (info.st_dev, info.st_ino), MARKER, 12345, None,
                                        'drain_refused', helper=dict(helper, raw_environment='forbidden'))

    def test_failed_close_keeps_profile_and_exact_safe_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory);folder.chmod(0o700);info = folder.stat()
            process = BrowserProcess.__new__(BrowserProcess)
            process._lock = threading.RLock()
            process._read = process._write = None;process._pid = 12345
            process._closed = False;process.profile = folder
            process._profile_identity = info.st_dev, info.st_ino
            process._marker = MARKER;process._leader_identity = (12345, 'start', '/synthetic/Chrome')
            process._record_failure = None
            def refuse():raise ownership.OwnershipUnavailable('Browser helper inspection unknown')
            process._owner = SimpleNamespace(drain=refuse)
            with patch('os.waitpid') as wait, patch('os.kill') as kill:
                with self.assertRaises(BrowserUnavailable) as caught:process.close()
            self.assertEqual(caught.exception.code, 'DRAIN_UNPROVED')
            self.assertEqual(caught.exception.reason, 'HELPER_UNREADABLE')
            self.assertNotIn(MARKER, str(caught.exception))
            self.assertEqual(process._pid, 12345)
            self.assertEqual(json.loads((folder / diagnostic.NAME).read_text())['reason'], 'HELPER_UNREADABLE')
            wait.assert_not_called();kill.assert_not_called()

    def facts(self):
        return dict(origin='fab', language='english', ready_state='complete', body_present=True, sign_in_button=False,
                    sign_in_link=True, sign_in_role=False, scan_limit=False, load_failure_document=False)

    def test_link_observation_does_not_change_authentication_predicate(self):
        connection = Connection.__new__(Connection)
        facts = self.facts()
        with patch.object(connection, '_evaluate', return_value=facts):
            self.assertEqual(connection.page_facts(), facts)
        with patch.object(connection, '_evaluate', return_value={'state': 'site_visible'}):
            self.assertEqual(connection.state(), dict(state='site_visible', account_verified=False, downloads_enabled=False))

    def test_page_facts_refuse_extra_fields_or_non_boolean_values(self):
        connection = Connection.__new__(Connection)
        for value in (dict(self.facts(), token='must refuse'), dict(self.facts(), body_present=1),
                      dict(self.facts(), origin=[]), dict(self.facts(), ready_state={}),
                      dict(self.facts(), language=[])):
            with self.subTest(value=value), patch.object(connection, '_evaluate', return_value=value):
                with self.assertRaisesRegex(BrowserUnavailable, 'SITE_CHANGED'):connection.page_facts()


if __name__ == '__main__':
    unittest.main()
