# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A suite's macOS crash reports move beside it and its log says what crashed (crash_reports, native_suite_process).

Found on 1.1.0's cut 08e2b0f7842 (macOS, 2026-11-13): test_blender_prefs_and_quit's quit-open launch crashed 0.9 s
after restoring its windows, and its report was dropped ("the limit of 25 logs has been reached") because suites that
crash the app on purpose had left 38 Angelo reports in ~/Library/Logs/DiagnosticReports. Covered on a stand-in report
folder: a report of a test's process written during the suite moves into the suite's folder with a summary of the
crash; the person's own app's report (launchd's child), older reports and other processes' stay; a report still being
written is waited for only when asked; the runner hands the folder only to its own runs, so host tests and stand-in
launches never touch the real one.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import crash_reports  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
import native_suite_process  # noqa: E402

BODY = {"procName": "Angelo", "pid": 31949, "parentProc": "python3.13",
        "exception": {"type": "EXC_CRASH", "signal": "SIGABRT"},
        "termination": {"namespace": "LIBXPC", "indicator": "API misuse"},
        "asi": {"libxpc.dylib": ["API MISUSE: example"]}, "faultingThread": 0,
        "usedImages": [{"name": "libxpc.dylib"}, {"name": "Angelo"}],
        "threads": [{"queue": "com.apple.main-thread", "frames": [
            {"imageIndex": 0, "symbol": "_xpc_api_misuse"}, {"imageIndex": 1, "symbol": "main",
                                                               "sourceFile": "creator.cc", "sourceLine": 12}]}]}


def write(folder, name, body, age=0.0):
    path = Path(folder) / name
    header = {"app_name": body.get("procName"), "timestamp": "2026-11-13 14:04:16.00 +0900", "app_version": "1.1.0"}
    path.write_text(json.dumps(header) + "\n" + json.dumps(body), encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


class TakeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.reports, self.dest = self.root / "DiagnosticReports", self.root / "suite" / "crash-reports"
        self.reports.mkdir()

    def test_a_tests_crash_moves_beside_the_suite_with_what_crashed(self):
        since = time.time() - 5
        write(self.reports, "Angelo-2026-11-13-140416.ips", BODY)
        taken = crash_reports.take(since, self.dest, reports=self.reports)
        self.assertEqual([Path(item["report"]).name for item in taken], ["Angelo-2026-11-13-140416.ips"])
        self.assertFalse((self.reports / "Angelo-2026-11-13-140416.ips").exists(), "it no longer counts toward 25")
        self.assertTrue((self.dest / "Angelo-2026-11-13-140416.ips").is_file())
        text = taken[0]["summary"]
        for part in ("pid 31949", "EXC_CRASH SIGABRT", "LIBXPC API misuse", "API MISUSE", "_xpc_api_misuse",
                     "main (creator.cc:12)"):
            self.assertIn(part, text)

    def test_the_persons_app_older_reports_and_other_processes_stay(self):
        since = time.time() - 5
        write(self.reports, "Angelo-person.ips", dict(BODY, parentProc="launchd"))
        write(self.reports, "Angelo-yesterday.ips", BODY, age=3600)
        write(self.reports, "python3.13-now.ips", dict(BODY, procName="python3.13"))
        self.assertEqual(crash_reports.take(since, self.dest, reports=self.reports), [])
        self.assertEqual(sorted(path.name for path in self.reports.iterdir()),
                         ["Angelo-person.ips", "Angelo-yesterday.ips", "python3.13-now.ips"])

    def test_a_report_still_being_written_is_waited_for_only_when_asked(self):
        since, now = time.time() - 5, [0.0]

        def sleep(seconds):
            now[0] += seconds
            if now[0] >= 1.0 and not (self.reports / "Angelo-late.ips").exists():
                write(self.reports, "Angelo-late.ips", BODY)

        self.assertEqual(crash_reports.take(since, self.dest, reports=self.reports, clock=lambda: now[0], sleep=sleep),
                         [])
        taken = crash_reports.take(since, self.dest, wait=5.0, reports=self.reports, clock=lambda: now[0], sleep=sleep)
        self.assertEqual([Path(item["report"]).name for item in taken], ["Angelo-late.ips"])

    def test_the_suites_log_ends_with_the_summary(self):
        log = self.root / "suite.log"
        log.write_text("suite output\n", encoding="utf-8")
        write(self.reports, "Angelo-2026-11-13-140416.ips", BODY)
        plan = {"directory": self.root / "suite", "log": log}
        taken = native_suite_process._crash_reports(plan, time.time() - 5, -6, None, self.reports)
        self.assertEqual(len(taken), 1)
        text = log.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("suite output\n"))
        self.assertIn("crash report (macOS)", text)
        self.assertIn("_xpc_api_misuse", text)

    def test_only_the_runners_own_run_hands_over_the_real_folder(self):
        import inspect
        import run_native_suites
        source = inspect.getsource(run_native_suites.main)
        self.assertIn("reports=crash_reports.folder() if launch is subprocess.Popen else None", source)
        self.assertIsNone(inspect.signature(native_suite_process.run_plan).parameters["reports"].default)
        self.assertIsNone(inspect.signature(native_suite_process.run_all).parameters["reports"].default)

    @unittest.skipUnless(sys.platform == "darwin", environment_skip("macOS's report folder"))
    def test_the_folder_is_the_accounts_never_the_sandboxed_home(self):
        import pwd
        self.assertEqual(crash_reports.folder(),
                         Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "Logs" / "DiagnosticReports")


if __name__ == "__main__":
    unittest.main()
