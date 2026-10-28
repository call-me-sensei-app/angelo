# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the dependency-free host suite (test_native_*.py) with an explicit skip policy.

Usage, from the repository root:

    python3 tests/python/angelo_agent/run_host_tests.py [--max-skips N] [--timeout SECONDS]

Platform, filesystem and external-fixture skips are explicitly marked with
host_test_policy.environment_skip and listed separately. CI installs NumPy
and jsonschema and passes --max-skips 0, so missing dependencies and any
unclassified skip fail the build instead of silently reducing coverage.
Every skip is listed with its reason. Host tests never replace the native
and GUI suites run with the compiled Angelo binary.
"""

import argparse
import faulthandler
import os
from pathlib import Path
import sys
import types
import unittest

from host_test_policy import is_environment_skip


# Blender's own modules: outside Blender, any entry under these names is a test's stand-in.
BLENDER_MODULES = frozenset({"bpy", "_bpy", "bpy_types", "bpy_extras", "bmesh", "mathutils", "gpu", "gpu_extras", "blf",
                             "bl_math", "bl_ui", "bl_operators", "addon_utils", "aud", "idprop", "imbuf", "freestyle"})


def _stand_in(name, value):
    """True for a sys.modules entry no import made under a name code imports: a namespace, a mock or None, or a module
    built by hand for one of Blender's modules or a package's submodule. Every module an import loads carries its
    spec; a C extension's own top-level helper module (Cython's cython_runtime) has none and is no stand-in."""
    if not isinstance(value, types.ModuleType):
        return True
    if getattr(value, "__spec__", None) is not None:
        return False
    return name.partition(".")[0] in BLENDER_MODULES or "." in name


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
        self._after, self._last_class, self._last_id = dict(sys.modules), type(test), test.id()
        super().stopTest(test)

    _after, _last_class, _last_id = {}, None, None

    @staticmethod
    def _restore(before, names):
        for name in names:
            if name in before:
                sys.modules[name] = before[name]
            else:
                del sys.modules[name]

    def _fail(self, test, what):
        try:
            raise AssertionError(f"{what}; put right so later tests see what they would alone. Stop every "
                                 "sys.modules patch in reverse order, with addCleanup.")
        except AssertionError:
            self.addFailure(test, sys.exc_info())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Angelo's dependency-free host tests.")
    parser.add_argument("--max-skips", type=int, default=None,
                        help="fail when more than this many unclassified tests are skipped; explicitly marked "
                             "environmental skips are listed separately")
    parser.add_argument("--timeout", type=float, default=None,
                        help="after this many seconds print every thread's stack and exit, so a hung test names "
                             "itself instead of holding the machine")
    args = parser.parse_args(argv)
    if args.timeout:
        faulthandler.dump_traceback_later(args.timeout, exit=True)
    # Every conversation save in every test reads its journal back and requires it to reproduce the saved state
    # (sessions.VERIFY_JOURNAL): a row changed in place that no save is told about fails the test that changed it.
    os.environ.setdefault("ANGELO_JOURNAL_VERIFY", "1")
    here = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(here), pattern="test_native_*.py", top_level_dir=str(here))
    # Every module puts the add-on's folder first on sys.path when imported, so one process ends discovery with
    # hundreds of copies; each import that misses (bpy outside Blender) then checks them all. The first copy is
    # the one that resolves, so keeping only first copies changes no import.
    sys.path[:] = list(dict.fromkeys(sys.path))
    result = unittest.TextTestRunner(verbosity=1, resultclass=IsolatedResult).run(suite)
    for test, reason in result.skipped:
        print(f"SKIPPED {test.id()}: {reason}")
    unexpected = [(test, reason) for test, reason in result.skipped if not is_environment_skip(reason)]
    if result.skipped:
        print(f"Skips: {len(result.skipped) - len(unexpected)} expected environmental, "
              f"{len(unexpected)} unclassified.")
    if not result.wasSuccessful():
        return 1
    if args.max_skips is not None and len(unexpected) > args.max_skips:
        print(f"{len(unexpected)} unclassified tests were skipped; the limit is {args.max_skips}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
