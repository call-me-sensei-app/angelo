# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict host skip policy: expected environments never hide missing coverage or failures."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import host_test_policy
from host_test_policy import environment_skip, required_skip
import run_host_tests as runner


class HostSkipPolicyTests(unittest.TestCase):
    def run_fixture(self, tests, maximum=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        suite = unittest.TestSuite(tests)
        # On the loader class: whichever loader the runner uses, it never discovers the real suite from here
        # (a stand-in on one loader object missed the runner's own, and this test ran the whole suite).
        with mock.patch.object(unittest.TestLoader, "discover", return_value=suite), \
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
                self.skipTest(required_skip("missing fixture"))

        status, stdout, stderr = self.run_fixture(unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
        self.assertEqual(status, 1)
        self.assertIn("1 expected environmental, 1 unclassified", stdout)
        self.assertIn("1 unclassified tests were skipped; the limit is 0", stderr)

    def test_the_explicit_limit_still_applies_to_unclassified_skips(self):
        class Fixture(unittest.TestCase):
            def test_dependency(self):
                self.skipTest(required_skip("NumPy is not installed"))

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

    def test_what_a_real_module_registers_of_its_own_is_real_whatever_its_type(self):
        """Up to Python 3.12 ``typing`` registers two classes of its own, its ``io`` and ``re`` namespaces, as
        ``typing.io`` and ``typing.re``. The first test of a process to import typing (numpy does) was failed for
        leaving two stand-ins: ``test_native_stale_frames`` run alone with a system Python 3.12.3 (pipeline-gates,
        Linux, 2026-11-16), while the full suite passed, where an earlier file had imported it. The rule is not
        typing's: what a real, imported module defines and registers under its own dotted name is that module's."""
        import sys
        import types
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "host_tests_owner_module.py").write_text(
                "import sys\n\n\nclass io:\n    pass\n\n\nio.__name__ = __name__ + '.io'\n"
                "sys.modules[io.__name__] = io\n", encoding="utf-8")
            sys.path.insert(0, folder)
            self.addCleanup(sys.path.remove, folder)
            for name in ("host_tests_owner_module", "host_tests_owner_module.io"):
                self.addCleanup(sys.modules.pop, name, None)
            import host_tests_owner_module as owner
            own = sys.modules["host_tests_owner_module.io"]
            self.assertFalse(runner._stand_in("host_tests_owner_module.io", own))
            twin = type("io", (), {"__module__": "host_tests_owner_module"})
            twin.__name__ = "host_tests_owner_module.io"
            self.assertTrue(runner._stand_in("host_tests_owner_module.io", twin), "not the object the module holds")
            planted = type("re", (), {})   # Defined here, then hung on the real module: a test's doing, not its own.
            planted.__name__ = "host_tests_owner_module.re"
            owner.re = planted
            self.assertTrue(runner._stand_in("host_tests_owner_module.re", planted))
        for name in ("typing.io", "typing.re"):   # The case itself, where this Python still has it.
            if name in sys.modules:
                self.assertFalse(runner._stand_in(name, sys.modules[name]), name)
        fake = types.SimpleNamespace(types=type("types", (), {"__module__": "bpy"}))
        fake.types.__name__ = "bpy.types"
        with mock.patch.dict(sys.modules, {"bpy": fake}):   # A stand-in's own parts stay stand-ins.
            self.assertTrue(runner._stand_in("bpy.types", fake.types))
        self.assertTrue(runner._stand_in("angelo_agent.fake", types.SimpleNamespace()))
        self.assertTrue(runner._stand_in("angelo_agent.fake", mock.MagicMock()))
        self.assertTrue(runner._stand_in("bpy", None))


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
        with mock.patch.object(unittest.TestLoader, "loadTestsFromNames",   # On the class: see run_fixture.
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


class NamedFilesTests(unittest.TestCase):
    """Files named on the command line, as CI's shards and its files-alone runs name theirs.

    For a named file unittest's loader turns only an ImportError into a failed test. Any other exception while the
    file is imported left the loader and ended the run: on VM CI (a542c361067, win-1) one test file's AttributeError
    at import stopped its Windows shard before a single test of it ran, where discovery on Linux reported one error
    and ran the rest."""

    FINE = "import unittest\n\n\nclass Fine(unittest.TestCase):\n    def test_it_runs(self):\n        pass\n"
    BROKEN = {
        "an attribute that is not there": "import unittest\nMISSING = unittest.no_such_attribute\n",
        "a name that is not defined": "VALUE = never_defined\n",
        "a syntax error": "def broken(:\n    pass\n",
        "a module that is not installed": "import no_such_module_for_this_fixture\n",
    }

    def run_named(self, files):
        """``(status, output)`` of ``main`` with the fixture ``files`` ({name: source}) named on its command line."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for name, source in files.items():
            (Path(folder.name) / (name + ".py")).write_text(source, encoding="utf-8")
        path = list(sys.path)
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in files])
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), path))
        output = io.StringIO()
        with mock.patch.object(runner, "HERE", Path(folder.name)), \
                mock.patch.object(runner.load_guard, "refused", return_value=0), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = runner.main(["--max-skips", "0"] + [name + ".py" for name in files])
        return status, output.getvalue()

    def test_a_named_file_that_fails_to_import_is_one_failed_test_and_the_others_still_run(self):
        for index, (what, source) in enumerate(self.BROKEN.items()):
            with self.subTest(what):
                broken, fine = "test_native_fixture_broken_%d" % index, "test_native_fixture_fine_%d" % index
                status, output = self.run_named({broken: source, fine: self.FINE})
                self.assertEqual(status, 1, output)
                self.assertIn("Ran 2 tests", output, "the file after it ran, and the broken one counts as one test")
                self.assertIn("Failed to import test module: " + broken, output)
                self.assertIn("FAILED (errors=1)", output)

    def test_the_report_names_what_the_import_raised_and_where(self):
        status, output = self.run_named(
            {"test_native_fixture_attribute": self.BROKEN["an attribute that is not there"]})
        self.assertEqual(status, 1)
        self.assertIn("AttributeError", output)
        self.assertIn("no_such_attribute", output)
        self.assertIn("test_native_fixture_attribute.py", output, "the traceback reaches the file's own line")

    def test_a_named_file_that_skips_itself_is_still_one_skipped_test(self):
        skips = "import unittest\nraise unittest.SkipTest(%r)\n" % environment_skip("a platform this file is not for")
        status, output = self.run_named({"test_native_fixture_skips": skips, "test_native_fixture_runs": self.FINE})
        self.assertEqual(status, 0, output)
        self.assertIn("Ran 2 tests", output)
        self.assertIn("1 expected environmental, 0 unclassified", output)

    def test_named_files_that_import_run_as_before(self):
        status, output = self.run_named({"test_native_fixture_only": self.FINE})
        self.assertEqual(status, 0, output)
        self.assertIn("Ran 1 test", output)


if __name__ == "__main__":
    unittest.main()
