# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only: a native suite's profile is shallow enough for Windows paths, whatever the suite is named.

Windows build4 (2026-10-27): ``test_blender_parallel_lead@lanes`` passed repeat runs 1-9 and failed 10-20, each
time one character past MAX_PATH. Its workers' chat journals live under the suite's ``%LOCALAPPDATA%``, which the
runner nested below the suite's own folder, so the depth grew with the suite's name and the caller's output folder.
"""

from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_manifest as manifest
import native_suite_paths
import native_suite_process
import run_native_suites as runner

SUITES = {suite.name: suite for suite in manifest.expand(manifest.SUITES)}   # Variants by their run names.
LANES = "test_blender_parallel_lead@lanes"


class DepthTests(unittest.TestCase):
    def test_data_depth_is_the_products_deepest_worker_path(self):
        """The product's own measure of a worker's deepest path (``worker_paths``, pinned against its writers by
        test_native_worker_paths), for a private folder in Angelo's data folder."""
        from angelo_agent import private_roots, storage, worker_paths
        local = "C:\\Users\\fixture\\AppData\\Local"
        scratch = Path(local, storage.DATA_FOLDER, private_roots.SCRATCH)
        self.assertEqual(worker_paths.deepest(scratch)[0] - len(str(Path(local))), native_suite_paths.ANGELO_DATA_DEPTH)
        self.assertEqual(worker_paths.WINDOWS_LIMIT, native_suite_paths.WINDOWS_PATH_LIMIT)

    def test_refusal_only_on_windows_past_the_limit(self):
        limit, depth = native_suite_paths.WINDOWS_PATH_LIMIT, native_suite_paths.ANGELO_DATA_DEPTH
        refusal = native_suite_paths.windows_depth_refusal
        self.assertIsNone(refusal("C:\\" + "x" * (limit - depth - 3), "win32"))   # Exactly at the limit.
        reason = refusal("C:\\" + "x" * (limit - depth - 2), "win32")
        self.assertIn("%d > %d" % (limit + 1, limit), reason)
        self.assertIn("shorter --output", reason)
        self.assertIsNone(refusal("/" + "x" * 900, "darwin"))
        self.assertIsNone(refusal(None, "win32"))   # A LIVE suite without the variable: nothing to measure.


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-profile-test-")
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name).resolve()
        self.base = {"PATH": "/usr/bin:/bin", "HOME": "/Users/real-user-fixture"}

    def plan(self, name, run_dir=None, platform=None):
        return runner.plan_suite(SUITES[name], run_dir or self.run_dir, self.run_dir / "Angelo", self.base,
                                 platform=platform)

    def test_profile_depth_never_depends_on_the_suites_name(self):
        names = sorted({name for name, suite in SUITES.items() if suite.kind != manifest.LIVE},
                       key=len)
        short, long = self.plan(names[0]), self.plan(names[-1])
        self.assertGreater(len(names[-1]), len(names[0]))
        self.assertEqual(len(short["env"]["LOCALAPPDATA"]), len(long["env"]["LOCALAPPDATA"]))
        for plan in (short, long):
            home = Path(plan["env"]["HOME"])
            self.assertTrue(home.is_dir() and home.is_relative_to(plan["profile"]))
            self.assertTrue(Path(plan["env"]["TMPDIR"]).is_relative_to(plan["profile"]))
            self.assertTrue(Path(plan["env"]["LOCALAPPDATA"]).is_relative_to(home))
            self.assertTrue(plan["profile"].is_relative_to(self.run_dir / runner.PROFILE_FOLDER))
            self.assertFalse(plan["profile"].is_relative_to(plan["directory"]))
        self.assertNotEqual(short["profile"], long["profile"])

    def test_a_too_deep_windows_run_is_refused_before_it_starts(self):
        lanes = self.plan(LANES, platform="win32")
        self.assertEqual(lanes["preconditions"], [])   # This test's own temporary folder is shallow.
        deep = self.run_dir / ("d" * (native_suite_paths.WINDOWS_PATH_LIMIT - len(str(self.run_dir))))
        refused = self.plan(LANES, run_dir=deep, platform="win32")
        self.assertEqual(len(refused["preconditions"]), 1)
        self.assertIn("too deep for Windows paths", refused["preconditions"][0])
        self.assertEqual(self.plan(LANES, run_dir=self.run_dir / "x", platform="darwin")["preconditions"], [])

    def test_evidence_reads_the_profiles_logs_and_crash_files(self):
        plan = self.plan(LANES)
        worker_log = Path(plan["env"]["LOCALAPPDATA"], "Angelo/scratch/angelo-worker-x/worker.log")
        worker_log.parent.mkdir(parents=True)
        worker_log.write_text("Traceback (most recent call last):\n  File \"x.py\"\nValueError: boom\n")
        Path(plan["env"]["TMPDIR"], "angelo.crash.txt").write_text("crashed\n")
        reasons = native_suite_process.evidence(plan, 0.0, 1)
        self.assertTrue(any("worker.log" in reason for reason in reasons), reasons)
        self.assertTrue(any(reason.startswith("crash log written") for reason in reasons), reasons)


if __name__ == "__main__":
    unittest.main()
