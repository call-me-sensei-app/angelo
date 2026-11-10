# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real disposable macOS helper refuses absent/malformed committed-exit input before swap.

POSIX-only focused fixture; actual Windows helper and compiled native publisher need their own gates.
No real installed app, relaunch, login, or release payload is used.
"""
import suite_isolation  # noqa: F401
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import host_test_policy
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import update_install

NONCE = 'a' * 32


@unittest.skipIf(sys.platform == 'win32', host_test_policy.environment_skip(
    "the macOS update helper is a bash script; Windows installs through its own installer channel "
    "(test_native_update_committed_quit)"))
class HelperAuthorizationTests(unittest.TestCase):
    def refuses(self, frame):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, new = root/'old.app', root/'new.app'
            app.mkdir(); new.mkdir()
            (app/'artist-marker').write_text('old')
            (new/'release-marker').write_text('new')
            request = update_install.Request('macos', root/'work', os.getpid(), '1.2.3', 'Angelo',
                                            relaunch=False, app=str(app), new=str(new), invocation=NONCE)
            helper = update_install.prepare_folder(request)
            completed = subprocess.run(['/bin/bash', '--noprofile', '--norc', str(helper)],
                input=frame, env=update_install.helper_environment(request, {'HOME': folder}),
                capture_output=True, timeout=5)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(update_install.read_result(request.work),
                             dict(status='aborted', step='authorization', version='1.2.3'))
            self.assertEqual((app/'artist-marker').read_text(), 'old')
            self.assertEqual((new/'release-marker').read_text(), 'new')

    def test_ordinary_exit_without_frame_never_installs(self):
        self.refuses(b'')

    def test_partial_frame_never_installs(self):
        self.refuses(('ANGELO_UPDATE_COMMIT:'+NONCE[:16]).encode())

    def test_complete_line_without_newline_never_installs(self):
        self.refuses(('ANGELO_UPDATE_COMMIT:'+NONCE).encode())

    def test_different_invocation_never_installs(self):
        self.refuses(('ANGELO_UPDATE_COMMIT:'+'b'*32+'\n').encode())

    def test_valid_line_with_extra_bytes_never_installs(self):
        self.refuses(('ANGELO_UPDATE_COMMIT:'+NONCE+'\nextra').encode())
