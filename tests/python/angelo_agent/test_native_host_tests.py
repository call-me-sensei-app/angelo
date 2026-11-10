# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict host skip policy: expected environments never hide missing coverage or failures."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import io
import unittest
from unittest import mock

import host_test_policy
from host_test_policy import environment_skip
import run_host_tests as runner


class HostSkipPolicyTests(unittest.TestCase):
    def run_fixture(self, tests, maximum=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        suite = unittest.TestSuite(tests)
        with mock.patch.object(runner.unittest.defaultTestLoader, "discover", return_value=suite), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = runner.main(["--max-skips", str(maximum)])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_decorated_and_dynamic_environment_skips_are_allowed_and_reported(self):
        class Fixture(unittest.TestCase):
            @unittest.skip(environment_skip("requires another platform"))
            def test_decorated(self):
                self.fail("not applicable")

            def test_dynamic(self):
                self.skipTest(environment_skip("requires a filesystem capability"))

        status, stdout, _ = self.run_fixture(unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
        self.assertEqual(status, 0)
        self.assertIn("requires another platform", stdout)
        self.assertIn("requires a filesystem capability", stdout)
        self.assertIn("2 expected environmental, 0 unclassified", stdout)

    def test_an_unmarked_skip_still_fails_even_with_the_same_reason_text(self):
        class Fixture(unittest.TestCase):
            def test_expected(self):
                self.skipTest(environment_skip("missing fixture"))

            def test_unexpected(self):
                self.skipTest("missing fixture")

        status, stdout, stderr = self.run_fixture(unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
        self.assertEqual(status, 1)
        self.assertIn("1 expected environmental, 1 unclassified", stdout)
        self.assertIn("1 unclassified tests were skipped; the limit is 0", stderr)

    def test_the_explicit_limit_still_applies_to_unclassified_skips(self):
        class Fixture(unittest.TestCase):
            def test_dependency(self):
                self.skipTest("NumPy is not installed")

        for maximum, expected in ((0, 1), (1, 0)):
            with self.subTest(maximum=maximum):
                status, _, _ = self.run_fixture([Fixture("test_dependency")], maximum)
                self.assertEqual(status, expected)

    def test_a_test_failure_is_never_excused_by_environmental_skips(self):
        class Fixture(unittest.TestCase):
            def test_expected(self):
                self.skipTest(environment_skip("requires an optional runtime"))

            def test_broken(self):
                self.fail("real regression")

        status, _, stderr = self.run_fixture(unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
        self.assertEqual(status, 1)
        self.assertIn("real regression", stderr)


class StandInTests(unittest.TestCase):
    def test_a_c_extensions_own_submodule_is_real_and_a_hand_made_one_is_not(self):
        """pyexpat registers pyexpat.errors (and xml.parsers.expat.errors) without a spec; macOS loads it inside the
        first test that reads XML, which then failed as leaving a stand-in. A module made by hand still counts."""
        import sys
        import types
        import xml.parsers.expat   # noqa: F401  Loads pyexpat and its submodules, as a test reading XML does.
        for name in ("pyexpat.errors", "xml.parsers.expat.errors", "pyexpat.model"):
            with self.subTest(name=name):
                self.assertFalse(runner._stand_in(name, sys.modules[name]))
        made = types.ModuleType("pyexpat.errors")
        self.assertTrue(runner._stand_in("pyexpat.errors", made), "the real module's attribute is another object")
        self.assertTrue(runner._stand_in("bpy.types", types.ModuleType("bpy.types")))
        self.assertTrue(runner._stand_in("angelo_agent.fake", types.ModuleType("angelo_agent.fake")))


class WindowsFixPendingTests(unittest.TestCase):
    def run_marked(self, passes):
        with mock.patch.object(host_test_policy.os, "name", "nt"):
            class Fixture(unittest.TestCase):
                @host_test_policy.windows_fix_pending("the lock fix (PR B)")
                def test_defect(self):
                    self.assertTrue(passes)
        return HostSkipPolicyTests.run_fixture(self, [Fixture("test_defect")])

    def test_a_pending_fix_is_an_expected_failure_listed_with_its_reason(self):
        status, stdout, _ = self.run_marked(passes=False)
        self.assertEqual(status, 0)
        self.assertIn("EXPECTED FAILURE", stdout)
        self.assertIn("the lock fix (PR B)", stdout)
        self.assertIn("Expected failures: 1", stdout)

    def test_a_marker_that_outlives_its_fix_fails_the_run(self):
        status, _, _ = self.run_marked(passes=True)
        self.assertEqual(status, 1)

    def test_off_windows_the_test_runs_as_usual(self):
        class Fixture(unittest.TestCase):
            @host_test_policy.windows_fix_pending("the lock fix (PR B)")
            def test_defect(self):
                self.fail("real failure")

        if host_test_policy.os.name == "nt":
            self.skipTest(environment_skip("checks the marker's behaviour off Windows"))
        status, _, _ = HostSkipPolicyTests.run_fixture(self, [Fixture("test_defect")])
        self.assertEqual(status, 1)


class ShardTests(unittest.TestCase):
    def shard_names(self, shard):
        loaded = []
        with mock.patch.object(runner.unittest.defaultTestLoader, "loadTestsFromNames",
                               side_effect=lambda names: loaded.extend(names) or unittest.TestSuite()), \
                mock.patch.object(runner.load_guard, "refused", return_value=0) as refused, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(["--shard", shard]), 0)
        self.assertIsNone(refused.call_args.args[1], "a shard is refused like the whole suite, never rationed")
        return loaded

    def test_the_shards_together_run_every_file_exactly_once(self):
        every = sorted(path.stem for path in runner.HERE.glob("test_native_*.py"))
        shards = [self.shard_names("%d/4" % index) for index in range(1, 5)]
        self.assertEqual(sorted(name for shard in shards for name in shard), every)
        self.assertTrue(all(shards), "no shard is empty")

    def test_a_shard_names_no_files_and_stays_in_range(self):
        for argv in (["--shard", "0/4"], ["--shard", "5/4"], ["--shard", "two"],
                     ["--shard", "1/2", "test_native_host_tests.py"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                runner.main(argv)


if __name__ == "__main__":
    unittest.main()
