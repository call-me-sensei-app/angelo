# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the dependency-free host suite (test_native_*.py) with an explicit skip policy.

Usage, from the repository root:

    python3 tests/python/angelo_agent/run_host_tests.py [--max-skips N] [--timeout SECONDS] [--shard K/N] [TEST_FILE ...]

With test files named (``test_native_*.py``, by name or path), only those run.
A named file whose import raises anything is one failed test, as discovery
reports it, and the files after it still run (``failed_module``).
``--shard K/N`` runs every Nth file of the sorted suite starting with the Kth,
so N processes together run each file exactly once (CI's parallel jobs).
While another run holds the machine's benchmark lock, the whole suite is
refused and named files are rationed (``scripts/angelo/load_guard.py``): run
single files that way, never in a loop of separate interpreters.

Platform, filesystem and external-fixture skips are explicitly marked with
host_test_policy.environment_skip and listed separately. CI installs NumPy
and jsonschema and passes --max-skips 0, so missing dependencies and any
unclassified skip fail the build instead of silently reducing coverage.
Every skip is listed with its reason. Host tests never replace the native
and GUI suites run with the compiled Angelo binary.

The whole run, like each host test file on its own, sees no real provider
login: ``suite_isolation`` (first, below) gives the process a fresh HOME,
CODEX_HOME and CLAUDE_CONFIG_DIR, Angelo's key store and bundled runtime off
and no provider key variables (``provider_isolation``).
"""

import suite_isolation  # noqa: F401  First: no real provider login in any host test (provider_isolation.py).

import argparse
import faulthandler
import os
from pathlib import Path
import sys
import time
import traceback
import types
import unittest

import host_file_modules
from host_test_policy import is_environment_skip, pending_reason
import host_waits

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parents[2] / "scripts/angelo"))   # Appended: shadows no test's import.
import load_guard  # noqa: E402


# Every file's tests run while the product imports the module objects that file holds (host_file_modules): a test
# never patches one copy of a product module while the code runs another.
LOADER = host_file_modules.Loader()
# Blender's own modules: outside Blender, any entry under these names is a test's stand-in.
SLOW_S = 1.0    # A test that takes this long or more is listed with its time as it ends,
SLOWEST = 30    # and the slowest this many again at the end of the run.
BLENDER_MODULES = frozenset({"bpy", "_bpy", "bpy_types", "bpy_extras", "bmesh", "mathutils", "gpu", "gpu_extras", "blf",
                             "bl_math", "bl_ui", "bl_operators", "addon_utils", "aud", "idprop", "imbuf", "freestyle"})


def _stand_in(name, value):
    """True for a sys.modules entry no import made under a name code imports: a namespace, a mock or None, or a module
    built by hand for one of Blender's modules or a package's submodule. Every module an import loads carries its
    spec; a C extension's own top-level helper module (Cython's cython_runtime) has none and is no stand-in, and
    neither is what a real module registers of its own under its own name, module or not."""
    if _part_of_a_real_module(value):
        return False
    if not isinstance(value, types.ModuleType):
        return True
    if getattr(value, "__spec__", None) is not None:
        return False
    return name.partition(".")[0] in BLENDER_MODULES or "." in name


def _part_of_a_real_module(value):
    """What a real, imported module registered of its own: no spec, but the attribute of the module its own name says
    it is. A C extension's own submodule (pyexpat.errors, also registered as xml.parsers.expat.errors): macOS loads
    pyexpat inside the first test that reads XML, so without this every such test failed locally as leaving a
    stand-in; Linux CI has it loaded earlier. And a namespace that is no module at all: up to Python 3.12 typing
    registers two classes as typing.io and typing.re, so the first test of a process to import typing (numpy does)
    failed the same way. Such a value must also say it was defined in that module (``__module__``): an object a test
    made and hung on a real module is the test's."""
    own = getattr(value, "__name__", "")
    parent, _, attribute = own.rpartition(".") if isinstance(own, str) else ("", "", "")
    owner = sys.modules.get(parent) if parent else None
    if (owner is None or getattr(owner, "__spec__", None) is None or parent.partition(".")[0] in BLENDER_MODULES
            or getattr(owner, attribute, None) is not value):
        return False
    return isinstance(value, types.ModuleType) or getattr(value, "__module__", None) == parent


def _added(before):
    """sys.modules names that now hold a stand-in they did not hold in ``before``."""
    return sorted(name for name, value in list(sys.modules.items())
                  if before.get(name) is not value and _stand_in(name, value))


class IsolatedResult(unittest.TextTestResult):
    """Fails the test that leaves a stand-in module behind (``bpy`` above all) and takes it out again.

    The whole suite shares one process, so what outlives its test changes what every later file sees: code that
    imports ``bpy`` only inside Blender finds the stand-in and fails there instead of the test that left it. Checked
    when the test's cleanups have run; between two tests of one class it also catches a stand-in a thread or timer of
    the earlier test put in late. A class or module fixture may hold a stand-in for its whole class."""

    def startTest(self, test):
        self._started = time.monotonic()
        between = self._started - self._stopped if self._stopped else 0.0
        if between >= SLOW_S:   # A class's or module's set-up or tear-down (unittest times no test for them).
            print(f"\nSLOW {between:.1f} s between {self._last_id} and {test.id()} (set-ups, tear-downs)", flush=True)
        self._fixture_ran_out = host_waits.take()   # Ran out in those set-ups and tear-downs.
        late = _added(self._after) if type(test) is self._last_class else []
        if late:
            self._restore(self._after, late)
            self._fail(test, f"stand-in module(s) {', '.join(late)} appeared in sys.modules after {self._last_id} "
                             "ended (a thread or timer it started)")
        self._before = dict(sys.modules)
        super().startTest(test)

    def stopTest(self, test):
        left = _added(self._before)
        if left:
            self._restore(self._before, left)
            self._fail(test, f"stand-in module(s) {', '.join(left)} outlived this test in sys.modules")
        for ran_out, where in ((self._fixture_ran_out, " in the set-ups and tear-downs before this test"),
                               (host_waits.take(), "")):
            if ran_out:   # host_waits.watch(): a wait given the hang cap ran it out.
                self._failed(test, "a wait given host_waits.CAP_S ran out%s at %s: a hang, or a wait that expects "
                                   "nothing (a window) given the cap; a window keeps its short time, marked "
                                   "'# window: <why>'" % (where, ", ".join(ran_out)))
        spent = time.monotonic() - self._started
        if spent >= SLOW_S:   # Printed as it ends: a log cut by a job's time limit still has every time so far.
            self.slow.append((spent, test.id()))
            print(f"\nSLOW {spent:.1f} s {test.id()}", flush=True)
        self._after, self._last_class, self._last_id = dict(sys.modules), type(test), test.id()
        self._stopped = time.monotonic()
        super().stopTest(test)

    _after, _last_class, _last_id, _started, _stopped, _fixture_ran_out = {}, None, None, 0.0, 0.0, ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slow = []   # (seconds, test id) of every test that took SLOW_S or more.

    @staticmethod
    def _restore(before, names):
        for name in names:
            if name in before:
                sys.modules[name] = before[name]
            else:
                del sys.modules[name]

    def _fail(self, test, what):
        self._failed(test, f"{what}; put right so later tests see what they would alone. Stop every "
                           "sys.modules patch in reverse order, with addCleanup.")

    def _failed(self, test, message):
        try:
            raise AssertionError(message)
        except AssertionError:
            self.addFailure(test, sys.exc_info())


def _shard(text):
    """``K/N`` -> (K, N), for argparse."""
    index, _, count = text.partition("/")
    try:
        index, count = int(index), int(count)
    except ValueError:
        raise argparse.ArgumentTypeError("expected K/N, for example 2/4") from None
    if not 1 <= index <= count:
        raise argparse.ArgumentTypeError("K must be between 1 and N")
    return index, count


def skipped_module(name, reason):
    """One skipped test standing for module ``name``, which raised SkipTest(``reason``) on import."""
    def test(self):
        raise unittest.SkipTest(reason)
    return type(name, (unittest.TestCase,), {"test_module": test})("test_module")


def failed_module(name, error):
    """One failed test standing for module ``name``, whose import raised ``error``.

    For a file named on the command line unittest's loader turns only an ImportError into a failed test. Any other
    exception at import (an attribute a module no longer has, an undefined name, a syntax error) left the loader
    and ended the run before a single test ran, where discovery reports one error and goes on. CI's shards and its
    files-alone runs name their files, so one such file hid every other file of its shard."""
    report = "Failed to import test module: %s\n%s" % (name, "".join(traceback.format_exception(error)))

    def test(self):
        raise ImportError(report)
    return type(name, (unittest.TestCase,), {name: test})(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Angelo's dependency-free host tests.")
    parser.add_argument("--max-skips", type=int, default=None,
                        help="fail when more than this many unclassified tests are skipped; explicitly marked "
                             "environmental skips are listed separately")
    parser.add_argument("--timeout", type=float, default=None,
                        help="after this many seconds print every thread's stack and exit, so a hung test names "
                             "itself instead of holding the machine")
    parser.add_argument("--shard", type=_shard, default=None, metavar="K/N",
                        help="run only the Kth of N equal slices of the suite's files (1 <= K <= N)")
    parser.add_argument("files", nargs="*", metavar="TEST_FILE", help="run only these test_native_*.py files")
    args = parser.parse_args(argv)
    names = [Path(name).name[:-3] if name.endswith(".py") else Path(name).name for name in args.files]
    if args.shard:
        if names:
            parser.error("--shard slices the whole suite; name no test files with it")
        index, count = args.shard
        names = [path.stem for path in sorted(HERE.glob("test_native_*.py"))][index - 1::count]
    for name in names:
        if not name.startswith("test_native_") or not (HERE / (name + ".py")).is_file():
            parser.error("not a host test file in %s: %s" % (HERE, name))
    # A shard is a slice of the whole suite, so the lock refuses it like the suite instead of rationing its files.
    if args.shard:
        what, named = "shard %d/%d of the host suite" % args.shard, None
    elif names:
        what, named = "%d host test file(s)" % len(names), len(names)
    else:
        what, named = "the whole host suite", None
    status = load_guard.refused(what, named)
    if status:
        return status
    if args.timeout:
        faulthandler.dump_traceback_later(args.timeout, exit=True)
    # Every conversation save in every test reads its journal back and requires it to reproduce the saved state
    # (sessions.VERIFY_JOURNAL): a row changed in place that no save is told about fails the test that changed it.
    os.environ.setdefault("ANGELO_JOURNAL_VERIFY", "1")
    # And every save requires each row its file holds that changed in place to have been touched
    # (session_writer.CHECK_TOUCHES): a pump's owed save writes only rows new or touched.
    os.environ.setdefault("ANGELO_JOURNAL_TOUCHES", "1")
    if names:
        sys.path.insert(0, str(HERE))
        suite = unittest.TestSuite()
        for name in names:
            try:
                suite.addTest(LOADER.loadTestsFromNames([name]))
            except unittest.SkipTest as skip:   # A module that skips itself (a platform it can't import on): one
                suite.addTest(skipped_module(name, str(skip)))   # skipped test, as discovery reports it.
            except Exception as error:  # noqa: BLE001 - whatever its import raised, the file is one failed test.
                suite.addTest(failed_module(name, error))
    else:
        suite = LOADER.discover(str(HERE), pattern="test_native_*.py", top_level_dir=str(HERE))
    # Every module puts the add-on's folder first on sys.path when imported, so one process ends discovery with
    # hundreds of copies; each import that misses (bpy outside Blender) then checks them all. The first copy is
    # the one that resolves, so keeping only first copies changes no import.
    sys.path[:] = list(dict.fromkeys(sys.path))
    host_waits.watch()
    result = unittest.TextTestRunner(verbosity=1, resultclass=IsolatedResult).run(suite)
    for spent, test in sorted(result.slow, reverse=True)[:SLOWEST]:   # Per-test timing in every log.
        print(f"SLOWEST {spent:.1f} s {test}")
    last = host_waits.take()   # In the last class's or module's tear-down: no test after it to fail.
    if last:
        print("A wait given host_waits.CAP_S ran out in the last tear-downs at %s: a hang, or a window given the cap."
              % ", ".join(last), file=sys.stderr)
    for test, reason in result.skipped:
        print(f"SKIPPED {test.id()}: {reason}")
    unexpected = [(test, reason) for test, reason in result.skipped if not is_environment_skip(reason)]
    if result.skipped:
        print(f"Skips: {len(result.skipped) - len(unexpected)} expected environmental, "
              f"{len(unexpected)} unclassified.")
    for test, _ in result.expectedFailures:
        print(f"EXPECTED FAILURE {test.id()}: {pending_reason(test) or 'no windows_fix_pending reason'}")
    if result.expectedFailures:
        print(f"Expected failures: {len(result.expectedFailures)} (product fixes pending).")
    if not result.wasSuccessful() or last:
        return 1
    if args.max_skips is not None and len(unexpected) > args.max_skips:
        print(f"{len(unexpected)} unclassified tests were skipped; the limit is {args.max_skips}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
