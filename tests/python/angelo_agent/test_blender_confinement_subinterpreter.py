# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A sub-interpreter makes every confinement hot path ask, for good (doc/angelo/script_confinement.rst; the audit
hook in ``bpy_angelo_guard_confine_hook.cc``).

The code watcher and the agent-code set are the main interpreter's. Once another interpreter exists, the counts the
hot paths read first can't be trusted, so ``BLI_fileops_confine_always`` is set and every path asks (2.0.0's cost).
``_interpreters.create()`` raises no ``cpython.PyInterpreterState_New`` event (it detaches the thread state first; the
perf re-read of 6a573ad, MUST-1), and unconfined code (the console, an add-on) can load ``_interpreters`` and leave it in
``sys.modules`` for a script. Here, unconfined, as such code would:

* before anything, "always" is off (the app's own start set nothing);
* importing ``_interpreters`` sets it (the import trigger);
* ``_interpreters.create()`` and code run in the new interpreter leave it set, and the app goes on (a script region
  still runs and ends). Where the extension can't load (macOS: Python linked statically), the import attempt alone
  must set it (its ``import`` event comes first) and ``create()`` is not reachable at all.

The add-on is enabled first, as the app registers it at startup before any of the person's code runs: its
``register()`` installs the confinement (``script_confinement_blender.install_app``), and with it the ``_imp`` wrappers
that see a family module made under any name. Code that runs before that register is not this suite's case.

Variants (``ANGELO_SUBINTERP_ROUTE``), one process each since "always" can't be turned off again: ``import`` (the
above), ``alias`` (unconfined, ``_imp.create_dynamic`` / ``create_builtin`` with the spec named ``x._interpreters``:
CPython initializes an extension file by its last name, and no ``import`` event names the family; the re-read of
f3b1e5f, MUST-1. ``create_builtin`` matches the built-in table by full name, so there the alias makes nothing, and
"always" is still set, fail closed, by the name), ``builtin_direct`` (unconfined, ``_imp`` makes ``_interpreters``
under its own name: ``create_builtin`` where it is built in (Windows) raises no ``import`` event either) and
``confined_alias`` (a confined script makes ``x._lsprof`` and then ``x._interpreters`` the same way: both refused, as
native-file modules, and only the second sets "always"). Background ``--factory-startup`` only; no provider, account
or network.

Where ``create()`` is reachable (Windows), the new interpreter is made, run and destroyed on a worker thread, as a
pool of interpreters runs them (``InterpreterPoolExecutor`` gives each its own worker thread);
``ANGELO_SUBINTERP_THREAD=main`` does it on the main thread. On Windows, USD's first set-up after a sub-interpreter
existed aborts the app in its GIL helper (``TfPyLock``, ``PyGILState_Release``: "thread state must be current when
releasing"), whichever thread made it, and so does the release base 2.1.0 was cut from, with the add-on enabled
first too (build3 rc7, rc8, rc9, probe9t and probe9f, 2026-11-22). That crash is recorded, not tested here: this suite
uses no USD after ``create()``. The report is written before ``create()`` too, so a crash after it still shows the
trigger checks.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-confinement-subinterpreter-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "checks": {}, "details": {}}


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if detail is not None:
        REPORT["details"][name] = detail


def always():
    import _bpy
    return _bpy._angelo_guard.confine_counts()["always"]


def family_spec(name):
    """A spec for `_interpreters` under `name`, and the `_imp` function that makes it (built in, or its file)."""
    import importlib.machinery, glob
    if "_interpreters" in sys.builtin_module_names:
        return importlib.machinery.ModuleSpec(name, None, origin="built-in"), "create_builtin"
    for folder in sys.path:
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            found = glob.glob(os.path.join(folder, "_interpreters" + suffix))
            if found:
                return importlib.machinery.ModuleSpec(name, None, origin=found[0]), "create_dynamic"
    return None, None


def native_spec(name, module):
    import importlib.machinery, glob
    if module in sys.builtin_module_names:
        return importlib.machinery.ModuleSpec(name, None, origin="built-in"), "create_builtin"
    for folder in sys.path:
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            found = glob.glob(os.path.join(folder, module + suffix))
            if found:
                return importlib.machinery.ModuleSpec(name, None, origin=found[0]), "create_dynamic"
    return None, None


def confined_make(spec, maker):
    """A confined script that makes `spec` through `_imp.<maker>`: (refused, detail)."""
    import addon_utils
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import script_confinement
    from confinement_attacks_admit import admit
    source = "import _imp\n_imp.%s(SPEC)\n" % maker
    admit("subinterpreter", source)
    try:
        script_confinement.run_confined(source, {"__name__": "__angelo_script__", "SPEC": spec}, None, "subinterpreter")
    except script_confinement.ScriptConfined as error:
        return True, str(script_confinement.reason_of(error))[:300]
    except Exception as error:   # noqa: BLE001 - not a refusal: the module could not be made here.
        return False, repr(error)[:300]
    return False, "made"


def main():
    import _bpy
    guard = getattr(_bpy, "_angelo_guard", None)
    route = os.environ.get("ANGELO_SUBINTERP_ROUTE", "import")
    REPORT["route"] = route
    check("native_guard", guard is not None and hasattr(guard, "confine_counts"))
    check("not_always_at_start", always() is False, guard.confine_counts())
    check("family_not_loaded_at_start", not any(name.startswith("_interp") for name in sys.modules),
          sorted(name for name in sys.modules if name.startswith("_interp")))
    import addon_utils
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)   # As the app does at startup.
    check("not_always_after_install", always() is False, guard.confine_counts())
    _interpreters = None
    if route == "import":
        try:
            import _interpreters   # Unconfined, as the console or an add-on would.
        except ImportError as error:
            # macOS: the app links Python statically and the extension needs a symbol it doesn't export, so the
            # module can't load at all; the `import` event comes before the load, so the trigger still fires.
            REPORT["details"]["family_unloadable_here"] = str(error)[:300]
        check("import_sets_always", always() is True, guard.confine_counts())
    elif route == "alias":
        import _imp
        spec, maker = family_spec("x._interpreters")
        check("family_found", spec is not None, maker)
        try:
            _interpreters = getattr(_imp, maker)(spec)   # Unconfined, under another package's name.
            REPORT["details"]["alias_made"] = repr(_interpreters)[:300]
        except Exception as error:   # noqa: BLE001 - macOS can't load it; the check comes before the load.
            REPORT["details"]["alias_made"] = repr(error)[:300]
        check("aliased_creation_sets_always", always() is True, guard.confine_counts())
        if maker == "create_builtin":
            # The built-in table is looked up by full name: the alias makes nothing. Pinned, so a CPython that did
            # make it would fail here rather than pass unseen.
            loaded = sorted(name for name in sys.modules if name.rsplit(".", 1)[-1].startswith("_interp"))
            check("builtin_alias_makes_nothing", _interpreters is None and not loaded,
                  {"made": repr(_interpreters)[:200], "loaded": loaded})
        if _interpreters is not None and not hasattr(_interpreters, "create"):
            _interpreters = None
    elif route == "builtin_direct":
        import _imp
        spec, maker = family_spec("_interpreters")
        check("family_found", spec is not None, maker)
        try:
            made = getattr(_imp, maker)(spec)   # Unconfined, under its own name, with no `import` statement.
            REPORT["details"]["direct_made"] = repr(made)[:300]
        except Exception as error:   # noqa: BLE001 - macOS can't load it; the check comes before the load.
            REPORT["details"]["direct_made"] = repr(error)[:300]
        check("direct_creation_sets_always", always() is True, guard.confine_counts())
    elif route == "confined_alias":
        spec, maker = native_spec("x._lsprof", "_lsprof")
        check("native_found", spec is not None, maker)
        refused, detail = confined_make(spec, maker)
        check("aliased_native_module_refused", refused, detail)
        check("native_alias_leaves_always_off", always() is False, guard.confine_counts())
        spec, maker = family_spec("x._interpreters")
        refused, detail = confined_make(spec, maker)
        check("aliased_family_refused", refused, detail)
        check("aliased_family_sets_always", always() is True, guard.confine_counts())
    if _interpreters is not None:
        on = os.environ.get("ANGELO_SUBINTERP_THREAD", "worker")
        REPORT["thread"] = on
        write_report()   # Before create(): a crash after it still leaves the trigger checks.

        def make_run_destroy():
            interpreter = _interpreters.create()
            try:
                _interpreters.run_string(interpreter, "x = sum(range(10))\n")
            finally:
                _interpreters.destroy(interpreter)

        if on == "main":
            make_run_destroy()
        else:
            import threading
            failed = []

            def worker():
                try:
                    make_run_destroy()
                except BaseException:   # noqa: BLE001 - reported below.
                    failed.append(traceback.format_exc())
            thread = threading.Thread(target=worker, name="subinterpreter")
            thread.start()
            thread.join()
            check("create_and_run_on_a_worker", not failed, failed[0][-600:] if failed else None)
        check("create_and_run_keep_always", always() is True, guard.confine_counts())
    from angelo_agent import script_confinement
    from confinement_attacks_admit import admit
    source = "y = 2 + 2"
    admit("subinterpreter", source)
    namespace = {"__name__": "__angelo_script__"}
    script_confinement.run_confined(source, namespace, None, "subinterpreter")
    check("a_script_still_runs", namespace.get("y") == 4)
    check("still_always_after_a_script", always() is True, guard.confine_counts())
    REPORT["success"] = all(REPORT["checks"].values())


def write_report():
    (OUT / "confinement-subinterpreter.json").write_text(json.dumps(REPORT, indent=2, default=str))


try:
    main()
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
write_report()
print(json.dumps(REPORT, default=str)[:4000])
if REPORT.get("success"):
    print("ANGELO_CONFINEMENT_SUBINTERPRETER_OK")
sys.exit(0 if REPORT.get("success") else 1)
