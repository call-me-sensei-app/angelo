# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the dependency-free host suite (test_native_*.py) with an explicit skip policy.

Usage, from the repository root:

    python3 tests/python/angelo_agent/run_host_tests.py [--max-skips N] [--timeout SECONDS]

Some tests skip by design when optional dependencies (NumPy, jsonschema) are
missing. CI installs them and passes --max-skips 0, so a newly skipped test
fails the build instead of silently reducing coverage. Every skip is listed
with its reason. Host tests never replace the native and GUI suites run with
the compiled Angelo binary.
"""

import argparse
import faulthandler
import os
from pathlib import Path
import sys
import unittest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Angelo's dependency-free host tests.")
    parser.add_argument("--max-skips", type=int, default=None,
                        help="fail when more than this many tests are skipped")
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
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    for test, reason in result.skipped:
        print(f"SKIPPED {test.id()}: {reason}")
    if not result.wasSuccessful():
        return 1
    if args.max_skips is not None and len(result.skipped) > args.max_skips:
        print(f"{len(result.skipped)} tests were skipped; the limit is {args.max_skips}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
