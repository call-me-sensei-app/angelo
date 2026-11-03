# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""load_guard refuses heavy work beside another run's timed benchmark trial, and the runners and builds ask it first.

Three sessions on 2026-10-23 loaded the benchmark Mac during timed trials although their briefs forbade it: a loop
over 30 to 179 test files run one at a time, a Codex release build with a host suite, and one unittest call over 161
modules. Every case here uses a fake lock (``ANGELO_BILLED_LOCK``), never the machine's real one.
"""

import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import load_guard as guard  # noqa: E402

HOLDER = "timed-trial-999999999"   # NAME-PID; no process has PID 999999999.


class LockCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lock = Path(folder.name) / "billed.lock"
        self.env = {guard.LOCK_ENV: str(self.lock)}

    def hold(self, owner=HOLDER):
        self.lock.mkdir()
        (self.lock / "owner").write_text(owner + "\n", encoding="utf-8")

    def check(self, files=None, env=None, now=1000000.0, ancestors=lambda: {4321, 4322}):
        return guard.check(files, "test work", environ=self.env if env is None else env, now=now, ancestors=ancestors)


class CheckTests(LockCase):
    def test_no_lock_passes_and_writes_nothing(self):
        self.assertIsNone(self.check())
        self.assertIsNone(self.check(files=50))
        self.assertEqual(list(self.lock.parent.iterdir()), [])

    def test_heavy_work_is_refused_with_the_owner_and_the_ways_forward(self):
        self.hold()
        refusal = self.check()
        self.assertIn("Refused: test work", refusal)
        self.assertIn(HOLDER, refusal)
        self.assertIn(str(self.lock), refusal)
        self.assertIn("heavy work", refusal)
        self.assertIn("Never copy the owner token", refusal)

    def test_a_lock_whose_owner_is_not_written_yet_still_refuses(self):
        self.lock.mkdir()
        self.assertIn("not written yet", self.check())

    def test_the_holders_token_passes(self):
        self.hold()
        self.assertIsNone(self.check(env=dict(self.env, **{guard.TOKEN_ENV: HOLDER})))
        self.assertIsNotNone(self.check(env=dict(self.env, **{guard.TOKEN_ENV: "someone-else-1"})))

    def test_a_descendant_of_the_process_the_token_names_passes(self):
        self.hold("slot-4321-777")
        self.assertIsNone(self.check())
        self.assertIsNotNone(self.check(ancestors=lambda: {5000}))

    def test_pid_one_in_a_token_is_never_an_ancestor_match(self):
        self.hold("slot-1-0")
        self.assertIsNotNone(self.check(ancestors=lambda: {1, 0}))


class LightWorkTests(LockCase):
    def test_a_loop_of_single_files_is_refused_once_the_window_is_spent(self):
        self.hold()
        for index in range(guard.LIGHT_FILES):
            self.assertIsNone(self.check(files=1, now=1000000.0 + index), index)
        refusal = self.check(files=1, now=1000000.0 + guard.LIGHT_FILES)
        self.assertIn("a loop over test files is a suite", refusal)
        self.assertIn("%d test file(s) already ran" % guard.LIGHT_FILES, refusal)

    def test_one_call_over_many_modules_is_refused_at_once(self):
        self.hold()
        self.assertIsNotNone(self.check(files=guard.LIGHT_FILES + 1))
        self.assertIsNone(self.check(files=guard.LIGHT_FILES))

    def test_runs_older_than_the_window_no_longer_count(self):
        self.hold()
        self.assertIsNone(self.check(files=guard.LIGHT_FILES, now=1000000.0))
        self.assertIsNotNone(self.check(files=1, now=1000000.0 + guard.LIGHT_WINDOW - 1))
        self.assertIsNone(self.check(files=1, now=1000000.0 + guard.LIGHT_WINDOW + 1))

    def test_the_ledger_sits_beside_the_lock_and_skips_unreadable_lines(self):
        self.hold()
        ledger = guard.ledger_path(self.lock)
        ledger.write_text("garbage\n\n999\tx\n", encoding="utf-8")
        self.assertIsNone(self.check(files=3))
        self.assertEqual(ledger.parent, self.lock.parent)
        self.assertEqual(guard.recent_files(ledger, 1000000.0), 3)
        self.assertIn(HOLDER, ledger.read_text(encoding="utf-8").splitlines()[-1])


class AncestorTests(unittest.TestCase):
    def test_the_chain_walks_parents_up_to_pid_one(self):
        listing = " 1 0\n 10 1\n 20 10\n 30 20\n 99 1\n"
        run = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=listing)  # noqa: E731
        self.assertEqual(guard.ancestor_pids(30, run=run), {30, 20, 10})

    def test_a_failing_ps_means_no_ancestors(self):
        def run(*args, **kwargs):
            raise OSError("no ps")
        self.assertEqual(guard.ancestor_pids(30, run=run), set())

    def test_this_process_is_its_own_first_ancestor(self):
        if sys.platform == "win32":
            self.skipTest("no ps on Windows; no machine there holds the lock")
        self.assertIn(os.getpid(), guard.ancestor_pids())


class CommandLineTests(LockCase):
    def test_exit_status_and_message(self):
        stream = io.StringIO()
        self.assertEqual(guard.refused("build", stream=stream, environ=self.env), 0)
        self.hold()
        self.assertEqual(guard.refused("build", stream=stream, environ=self.env), guard.REFUSED)
        self.assertIn("Refused: build", stream.getvalue())

    def test_files_must_be_positive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            guard.main(["host tests", "--files", "0"])


@unittest.skipIf(sys.platform == "win32", "the runners' shell entry points; no Windows machine holds the lock")
class EntryPointTests(LockCase):
    """The real entry points, run as a person or agent would, against a fake lock."""

    def run_entry(self, *command):
        env = {key: value for key, value in os.environ.items() if key != guard.TOKEN_ENV}
        env.update(self.env)
        return subprocess.run(list(command), cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)

    def assert_refused(self, result):
        self.assertEqual(result.returncode, guard.REFUSED, result.stdout + result.stderr)
        self.assertIn(HOLDER, result.stderr)

    def test_the_whole_host_suite_is_refused_and_a_named_file_runs(self):
        self.hold()
        runner = str(HERE / "run_host_tests.py")
        self.assert_refused(self.run_entry(sys.executable, runner))
        too_many = ["test_native_module_size.py"] * (guard.LIGHT_FILES + 1)
        self.assert_refused(self.run_entry(sys.executable, runner, *too_many))
        single = self.run_entry(sys.executable, runner, "tests/python/angelo_agent/test_native_module_size.py")
        self.assertEqual(single.returncode, 0, single.stdout + single.stderr)
        self.assertIn("OK", single.stderr)

    def test_an_unknown_file_is_a_usage_error_not_a_ration(self):
        self.hold()
        result = self.run_entry(sys.executable, str(HERE / "run_host_tests.py"), "nothing_here.py")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(guard.ledger_path(self.lock).exists())

    def test_native_suites_are_refused_but_listing_them_is_not(self):
        self.hold()
        runner = str(HERE / "run_native_suites.py")
        self.assert_refused(self.run_entry(sys.executable, runner, "--binary", sys.executable))
        self.assertEqual(self.run_entry(sys.executable, runner, "--list").returncode, 0)

    def test_build_scripts_are_refused_first(self):
        bash = shutil.which("bash")
        if not bash or not shutil.which("python3"):
            self.skipTest("needs bash and python3 on PATH")
        self.hold()
        for script in ("build_native.sh", "build_release_macos.sh", "build_agent_runtime_macos.sh"):
            with self.subTest(script=script):
                self.assert_refused(self.run_entry(bash, str(ROOT / "scripts/angelo" / script), "fork", "out"))


if __name__ == "__main__":
    unittest.main()
