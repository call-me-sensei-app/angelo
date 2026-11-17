# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every attack of ``attacks_table`` against the confinement kept in C (``_bpy._angelo_guard``), in the real app
(doc/angelo/script_confinement.rst). The same harness and assertions as the host test
(``test_native_script_confinement``, which runs them against the Python stand-in), here with the C hook, the C
trampolines and the C wrappers. Then the module's surface: nothing a script can reach holds state or can be changed
(every name in ``dir()`` and ``__dict__`` is a function, a type or a constant string; ``setattr``, ``delattr``,
``types.ModuleType.__setattr__`` and ``__dict__`` writes change nothing the module serves).

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import contextlib
import io
import json
import runpy
import tempfile
import traceback
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-confinement-attacks-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False}


def harness_results():
    work = Path(tempfile.mkdtemp(prefix="confinement-", dir=OUT))
    argv, sys.argv = sys.argv, [str(HERE / "confinement_attacks.py"), str(work)]
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            runpy.run_path(str(HERE / "confinement_attacks.py"), run_name="__main__")
    finally:
        sys.argv = argv
    return json.loads(captured.getvalue().strip().splitlines()[-1])


def surface_problems():
    import _bpy
    guard = _bpy._angelo_guard
    problems = []
    for name in dir(guard):
        value = getattr(guard, name)
        if name.startswith("__") or isinstance(value, (types.BuiltinFunctionType, type, str)):
            continue
        problems.append("holds %s: %r" % (name, type(value)))
    contents = guard.__dict__   # A copy each time: writing to it changes nothing the module serves.
    if contents is guard.__dict__:
        problems.append("__dict__ is the live dict")
    for name, value in list(contents.items()):
        if name.startswith("__"):
            continue
        if not isinstance(value, (types.BuiltinFunctionType, type, str)):
            problems.append("__dict__ holds %s" % name)
    real = guard.confined
    for attempt in (lambda: setattr(guard, "confined", lambda: False),
                    lambda: delattr(guard, "confined"),
                    lambda: types.ModuleType.__setattr__(guard, "confined", lambda: False),
                    lambda: guard.__dict__.__setitem__("confined", lambda: False),
                    lambda: object.__getattribute__(guard, "__dict__").__setitem__("confined", lambda: False),
                    lambda: setattr(guard.ScriptConfined, "reason", "x"),
                    lambda: setattr(guard.Deferred, "__call__", lambda *a: None)):
        try:
            attempt()
        except (AttributeError, TypeError):
            pass
        if guard.confined is not real:
            problems.append("an attribute was replaced")
    return problems


def main():
    import test_native_script_confinement as host
    results = harness_results()
    REPORT["native"] = results.get("native")
    REPORT["numbers"] = {key: value for key, value in results.items() if key.endswith(("_us", "_ms"))}

    class Native(host.ConfinementAttackTests):
        @classmethod
        def setUpClass(cls):
            cls.results = results

    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(Native))
    stream = io.StringIO()
    outcome = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    REPORT["attacks"] = {"run": outcome.testsRun, "failures": [str(test) for test, _ in outcome.failures],
                         "errors": [str(test) for test, _ in outcome.errors]}
    REPORT["log"] = stream.getvalue()[-6000:]
    REPORT["surface_problems"] = surface_problems()
    REPORT["success"] = bool(results.get("native")) and outcome.wasSuccessful() and not REPORT["surface_problems"]


try:
    main()
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "confinement-attacks.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps({key: REPORT[key] for key in REPORT if key != "log"}, default=str)[:4000])
if REPORT.get("success"):
    print("ANGELO_CONFINEMENT_ATTACKS_OK")
sys.exit(0 if REPORT.get("success") else 1)
