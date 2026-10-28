# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict host skip policy: expected environments never hide missing coverage or failures."""

import contextlib
import io
import unittest
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
