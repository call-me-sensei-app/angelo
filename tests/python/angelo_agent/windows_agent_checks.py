# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's Windows validation that host tests cannot do (Windows only).

Two parts, each writing a JSON report:

* ``ntfs``: the Windows file backend against a real file system
  (``windows_ntfs_checks.py``; needs an administrator);
* ``cli``: Claude Code and Codex discovery and spawning (``windows_cli_checks.py``),
  meant to run inside the app started from the desktop after the official CLIs
  were installed for the signed-in user.

With a Python 3.13::

    python windows_agent_checks.py ntfs --report ntfs.json

Inside the app (Blender passes the arguments after ``--``; a window run quits by itself)::

    Angelo-launcher.exe --factory-startup --no-window-focus --python windows_agent_checks.py -- cli --report cli.json --desktop
    Angelo.exe --background --factory-startup --python windows_agent_checks.py -- ntfs --report ntfs-app.json

The Windows validation machine runs both (doc/angelo/app_identity.rst,
"Running it on Google Cloud"). Nothing here signs in, sends a prompt or reads a
credential.
"""

import argparse
import json
import os
import sys
import time
import traceback
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE.parents[2] / "scripts/addons_core")):
    if _path not in sys.path:
        sys.path.append(_path)   # After Blender's own add-on folder, so an app run tests the add-on it ships.


def _parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("part", choices=("ntfs", "cli"))
    parser.add_argument("--report", required=True)
    parser.add_argument("--work", default="", help="scratch folder (default: a new temporary folder)")
    parser.add_argument("--desktop", action="store_true", help="a signed-in desktop session (enables the browser step)")
    return parser.parse_args(argv)


class _Recorder(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases[test.id()] = {"ok": True}

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases[test.id()] = {"ok": False, "detail": self._exc_info_to_string(err, test)[-2000:]}

    def addError(self, test, err):
        super().addError(test, err)
        self.cases[test.id()] = {"ok": False, "detail": self._exc_info_to_string(err, test)[-2000:]}

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.cases[test.id()] = {"ok": None, "skipped": reason}

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.cases[subtest.id()] = {"ok": False, "detail": self._exc_info_to_string(err, test)[-2000:]}


def run_ntfs():
    import windows_ntfs_checks
    suite = unittest.defaultTestLoader.loadTestsFromModule(windows_ntfs_checks)
    result = unittest.TextTestRunner(verbosity=2, resultclass=_Recorder, stream=sys.stdout).run(suite)
    return {"run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "cases": result.cases}, result.wasSuccessful()


def run_cli(args):
    import windows_cli_checks
    steps = windows_cli_checks.run(args.work or None, desktop=args.desktop)
    failed = [name for name, step in steps.items() if step["ok"] is False]
    return {"steps": steps, "failed": failed}, not failed


def main(argv):
    args = _parse(argv)
    report = {"python": sys.version.split()[0], "executable": sys.executable, "in_app": "bpy" in sys.modules,
              "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ok = False
    try:
        if os.name != "nt":
            raise SystemExit("These checks are for Windows.")
        part, ok = run_ntfs() if args.part == "ntfs" else run_cli(args)
        report.update(part)
    except BaseException as error:   # The report must say why, whatever happened.
        report["error"] = "".join(traceback.format_exception(error))[-4000:]
    report["ok"] = ok
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("ANGELO_WINDOWS_CHECKS %s %s" % (args.part, "OK" if ok else "FAILED"), flush=True)
    return 0 if ok else 1


def _blender_arguments():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


if __name__ == "__main__":
    try:
        import bpy
    except ImportError:
        bpy = None
    if bpy is None:
        sys.exit(main(sys.argv[1:]))
    elif bpy.app.background:
        code = main(_blender_arguments())
        sys.stdout.flush()
        os._exit(code)   # Blender would otherwise exit 0 after the script.
    else:
        # A window run: let the window come up first, then check, then quit (the report holds the outcome).
        def _check():
            main(_blender_arguments())
            bpy.ops.wm.quit_blender()

        bpy.app.timers.register(_check, first_interval=3.0)
