# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton e2e runner's clean-up (skeleton_engine_e2e/run.py): Unreal's crash reporter, left running after
the editor crashes on exit, is stopped by PID, and only the one writing under this run's folder."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from host_test_policy import environment_skip

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("skeleton_e2e_run", HERE / "skeleton_engine_e2e/run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

MINE = Path("/Users/someone/aue.noindex/033356/h")
LISTING = """\
  101 CrashReportClient /Users/someone/aue.noindex/033356/h/Library/Application Support/Epic/UnrealEngine/5.8/Saved/Crashes/CrashReport-UE-Skel-pid-9 -Unattended
  102 CrashReportClient /Users/someone/aue.noindex/030249/h/Library/Application Support/Epic/Crashes/CrashReport-UE-Skel-pid-8 -Unattended
  103 CrashReportClient /Users/other/Library/Application Support/Epic/UnrealEngine/5.8/Saved/Crashes/CrashReport-UE-Game-pid-7
  104 /usr/bin/tail -f /Users/someone/aue.noindex/033356/h/Library/Logs/CrashReportClient.log
  105 UnrealEditor /Users/someone/aue.noindex/033356/Skel/Skel.uproject -nullrhi
  106 CrashReportClient /Users/someone/aue.noindex/033356/h2/Library/Crashes/CrashReport-pid-6
"""


# The runner (``ps``, launchd's orphaned reporter, ``Unity.app``) runs on the macOS workstation only; its matching is
# by this host's path spelling, which these macOS listings are.
@unittest.skipIf(sys.platform == "win32", environment_skip("skeleton_engine_e2e/run.py is a macOS workstation tool "
                                                           "(ps, launchd's CrashReportClient)"))
class CrashReporterTests(unittest.TestCase):
    def test_only_this_runs_crash_reporter_is_matched(self):
        # Another run's (102), another Unreal's (103), a command that only names the reporter (104), the editor
        # itself (105) and a sibling folder sharing the prefix (106) are left alone.
        self.assertEqual(run.crash_reporters(MINE, LISTING), [101])

    def test_a_stand_in_reporter_is_stopped_by_its_pid(self):
        scratch = Path(tempfile.mkdtemp()).resolve()
        folder = scratch / "run" / "h"
        reporter = scratch / "CrashReportClient"   # this Python under the reporter's name
        reporter.symlink_to(sys.executable)
        stand_in = subprocess.Popen([str(reporter), "-c", "import time; time.sleep(60)",
                                     str(folder / "Library/Crashes/CrashReport-UE-Skel-pid-1")])
        self.addCleanup(shutil.rmtree, scratch, True)
        try:
            deadline = time.monotonic() + 5
            while stand_in.pid not in run.crash_reporters(folder) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(run.stop_crash_reporters(folder, wait=1.0), [stand_in.pid])
            self.assertEqual(stand_in.wait(timeout=5), -15)
        finally:
            if stand_in.poll() is None:
                stand_in.kill()
                stand_in.wait()


if __name__ == "__main__":
    unittest.main()
