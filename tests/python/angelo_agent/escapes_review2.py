# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The coordinator's independent re-read of pieces 2+3 (2026-11-16), for test_blender_confinement_escapes. Each
check failed before its fix and judges the class, not the route.

* NEW-1: an installed module's top level, steered by a script, changes another module's namespace or class: put
  back at the region's end, whatever the new value is (a builtin such as `eval` too).
* NEW-2: code a script wrote is never loaded by Angelo, in a later session too; the import system's steering state a
  script rebinds or changes in place (`sys.pycache_prefix`, `sys.dont_write_bytecode`, a cached finder's `path`) goes
  back.
* NEW-3: a hand-built `weakref.finalize` record runs confined and never at exit.
* SHOULD: an installed generator a script leaves (os.walk) is not watched step by step.
* An installed top level's own write to `os.environ` is refused as a developer tool's setup, and says so.
"""

import json
import os
import subprocess
import sys

import bpy

STEER = ("import sched, importlib.util, functools, json, os\n"
         "spec = importlib.util.spec_from_file_location('angelo_review2_' + NAME, sched.__file__)\n"
         "module = importlib.util.module_from_spec(spec)\n"
         "module.object = functools.partial(%s)\nspec.loader.exec_module(module)\n")


_FRESH = ("configparser", "plistlib", "gettext", "pydoc", "glob", "fnmatch", "tarfile", "pickle")


def definitions(run, check):
    import json.decoder
    loads, decode, scanstring = json.loads, json.JSONDecoder.decode, json.decoder.scanstring
    fresh = [name for name in _FRESH if name not in sys.modules]
    plant = "functools.partial(setattr, json.decoder, 'scanstring', eval)"
    outcomes = {
        "module": run(STEER % "setattr, json, 'loads', eval", NAME="module"),
        "class": run(STEER % "setattr, json.JSONDecoder, 'decode', eval", NAME="class"),
        # The same, one level down: a body run under a name the script chose (inside `json`'s package tree).
        "fresh_copy_named_json": run("import sched, importlib.util, functools, json.decoder\n"
                                     "spec = importlib.util.spec_from_file_location('json', sched.__file__)\n"
                                     "module = importlib.util.module_from_spec(spec)\n"
                                     "module.object = %s\nspec.loader.exec_module(module)\n" % plant),
        "runpy_run_name": run("import runpy, functools, json.decoder\n"
                              "runpy.run_module('sched', init_globals={'object': %s}, run_name='json')\n" % plant),
    }
    # Builtins seeded, then an installed module the import system loads afresh, whose top level calls `object()`:
    # directly, or through installed code (`unittest.mock`).
    for route, seed in (("builtins_then_import", "builtins.object = %s" % plant),
                        ("mock_then_import", "unittest.mock.patch.object(builtins, 'object', %s).start()" % plant)):
        if fresh:
            outcomes[route] = run("import builtins, functools, importlib, json.decoder, unittest.mock\n%s\n"
                                  "importlib.import_module(NAME)\n" % seed, NAME=fresh.pop(0))
    after = {"module": json.loads is loads, "class": json.JSONDecoder.decode is decode,
             "package_tree": json.decoder.scanstring is scanstring}
    json.loads, json.JSONDecoder.decode, json.decoder.scanstring = loads, decode, scanstring
    check("steered_definition_put_back", all(after.values()) and len(outcomes) == 6, (outcomes, after))


def _child(code):
    done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python-expr", code],
                          capture_output=True, text=True, timeout=600)
    for line in done.stdout.splitlines():
        if line.startswith("CHILD "):
            return json.loads(line[6:])
    return {"error": done.stdout[-500:] + done.stderr[-500:]}


def provenance_across_sessions(run, check, plain):
    folder = plain / "earlier-session"
    marker = plain / "earlier-session-ran"
    marker.unlink(missing_ok=True)
    run("import os\nos.makedirs(FOLDER, exist_ok=True)\n"
        "open(os.path.join(FOLDER, 'angelo_earlier_session.py'), 'w').write(BODY)\n",
        FOLDER=str(folder), BODY="open(%r, 'w').write('ran')\n" % str(marker))
    # A later session (a fresh app, the same profile): a script has run, and data steers Angelo's own import there.
    tests = os.path.dirname(os.path.abspath(__file__))
    code = ("import addon_utils, json, sys\n"
            "addon_utils.enable('angelo_agent', default_set=False, persistent=False)\n"
            "sys.path.insert(0, %r)\n"
            "from angelo_agent import script_confinement\nfrom confinement_attacks_admit import admit\n"
            "admit('probe', 'x = 1')\n"
            "script_confinement.run_confined('x = 1', {'__name__': '__angelo_script__'}, None, 'probe')\n"
            "sys.path.insert(0, %r)\n"
            "try:\n    import angelo_earlier_session\n    result = 'imported'\n"
            "except BaseException as error:\n    result = type(error).__name__\n"
            "print('CHILD ' + json.dumps({'result': result}))\n") % (tests, str(folder))
    outcome = _child(code)
    ran = marker.exists()
    marker.unlink(missing_ok=True)
    check("earlier_session_code_never_runs_as_angelo", not ran and outcome.get("result") not in (None, "imported"),
          (outcome, ran))


def import_state(run, check, plain):
    before = {"prefix": sys.pycache_prefix, "bytecode": sys.dont_write_bytecode}
    stdlib = os.path.dirname(os.__file__)
    finder = sys.path_importer_cache.get(stdlib)
    finder_path = getattr(finder, "path", None)
    run("import sys\nsys.pycache_prefix = PLAIN\nsys.dont_write_bytecode = not sys.dont_write_bytecode\n",
        PLAIN=str(plain))
    run("import sys, os\nf = sys.path_importer_cache.get(os.path.dirname(os.__file__))\n"
        "if f is not None:\n    f.path = PLAIN\n", PLAIN=str(plain))
    after = {"prefix": sys.pycache_prefix, "bytecode": sys.dont_write_bytecode}
    now_path = getattr(sys.path_importer_cache.get(stdlib), "path", None)
    sys.pycache_prefix, sys.dont_write_bytecode = before["prefix"], before["bytecode"]
    if finder is not None:
        finder.path = finder_path
    check("import_steering_put_back", before == after and finder is not None and now_path == finder_path,
          (before, after, finder_path, now_path))


def finalize_record(run, check):
    import weakref
    holder = {}
    kept = type("Kept", (), {})()
    run("import weakref\nF = weakref.finalize\nf = object.__new__(F)\ni = F._Info()\n"
        "i.weakref = weakref.ref(KEPT, f)\ni.func = print\ni.args = ('left by the script',)\ni.kwargs = {}\n"
        "i.atexit = True\ni.index = next(F._index_iter)\nF._registry[f] = i\nHELD['record'] = (f, i)\n",
        KEPT=kept, HELD=holder)
    finalizer, info = holder.get("record", (None, None))
    found = {"atexit": getattr(info, "atexit", None), "func": type(getattr(info, "func", None)).__name__}
    if finalizer is not None:
        weakref.finalize._registry.pop(finalizer, None)
    check("hand_built_finalizer_confined", found == {"atexit": False, "func": "Deferred"}, found)


def installed_generator(run, check, plain):
    holder = {}
    run("import os\nHELD['walk'] = os.walk(PLAIN)\n", PLAIN=str(plain), HELD=holder)
    tool = next((tool for tool in range(6) if sys.monitoring.get_tool(tool) == "Angelo confinement"), None)
    events = sys.monitoring.get_local_events(tool, os.walk.__code__) if tool is not None else None
    holder.clear()
    check("installed_generator_not_watched", tool is not None and events == 0, (tool, events))


def installed_import_environ(run, check):
    """An installed top level writing the environment as it loads (a developer tool's setup, or a top level a script
    steered) is refused, and the refusal says what it is; the process is unchanged."""
    holder = {}
    outcome = run("try:\n" + "".join("    " + line + "\n" for line in
                  (STEER % "os.environ.__setitem__, 'ANGELO_REVIEW2_ENV', '1'").splitlines()) +
                  "except Exception as error:\n    HELD['error'] = str(error)\n", NAME="environ", HELD=holder)
    child = subprocess.run([sys.executable, "-I", "-c", "import os; print(os.environ.get('ANGELO_REVIEW2_ENV'))"],
                           capture_output=True, text=True, timeout=60).stdout.strip()
    check("installed_import_environ_refused", "developer tool" in holder.get("error", "") and
          "ANGELO_REVIEW2_ENV" not in os.environ and child == "None", (outcome, holder, child))


def load_frees_old(run, check, plain):
    """A file opened during an agent turn: what the old file's Python held (here a cycle) must become collectable,
    not stay frozen into the collector's oldest generation (automatic collection is off around the probe so only
    the guard's own collection can free it)."""
    import gc
    import weakref
    saved = plain / "review2-load.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)

    class Probe:
        pass
    was = gc.isenabled()
    gc.disable()
    try:
        probe = Probe()
        probe.cycle = probe
        alive = weakref.ref(probe)
        del probe
        outcome = run("bpy.ops.wm.open_mainfile(filepath=PATH, load_ui=False)\n", PATH=str(saved))
        left = alive() is not None
    finally:
        if was:
            gc.enable()
    check("file_loaded_in_turn_frees_old", outcome["refused"] is None and not left, (outcome, left))


def wrapped_after_install(check):
    """Code loaded after the install (a module imported later, binding entry points by name) gets the wrappers: the
    install replaced each at its home, so a later `from os import open` finds the wrapper, never the original."""
    import types
    namespace = {"__name__": "angelo_review2_later"}
    exec("from os import open as o\nfrom _thread import start_new_thread as t\n"
         "from atexit import register as a\nimport signal\ns = signal.signal\n", namespace)   # noqa: S102
    found = {name: isinstance(getattr(namespace[name], "__self__", None), types.ModuleType)
             for name in ("o", "t", "a", "s")}
    check("wrapped_after_install", not any(found.values()), found)


def run_all(run, check, plain):
    definitions(run, check)
    wrapped_after_install(check)
    import_state(run, check, plain)
    finalize_record(run, check)
    installed_generator(run, check, plain)
    installed_import_environ(run, check)
    provenance_across_sessions(run, check, plain)
    load_frees_old(run, check, plain)
