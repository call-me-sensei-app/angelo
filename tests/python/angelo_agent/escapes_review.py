# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The coordinator's independent review of Phase C piece 1 (2026-11-15), for test_blender_confinement_escapes. Each
check ports a route the review named and failed before the fix; the fixes are classes, so each check judges where
the effect lands.

* MUST-1: `os.environ` is a view of the process's environment: its data can't be changed apart from it (`_data`,
  `dict.__setitem__`, a rebinding of `os.environ`), and a program Angelo starts sees none of it.
* MUST-2: no installed module's top level makes a script's change for it (its namespace given the script's
  callable, then the module run again).
* MUST-3 (and the generator window as a whole): a step of a generator the script left runs as a region, so what it
  leaves (a `weakref.finalize`, a `threading._register_atexit` callable, a codec error handler) is confined as a
  region's would be.
* MUST-4: code from a file a script wrote never runs as Angelo's, whatever steered the import
  (`sys.path_importer_cache`, `sys.path`).
* SHOULD: a registry entry a generator adds goes; a rebinding made on another thread goes when its step ends;
  `gc.freeze`/`unfreeze` and the confinement's own `sys.monitoring` tool are not a script's.
* No false refusal: `timeit` and a first `weakref.finalize` from a generator work.
"""

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import threading

PLANT = "ANGELO_REVIEW_PLANT"


def _child_env():
    code = "import json, os; print(json.dumps(os.environ.get(%r)))" % PLANT
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    return json.loads(done.stdout.strip().splitlines()[-1])


def _resume(run, body, **names):
    """A generator the script leaves, resumed after its run on this thread: its first step's outcome."""
    holder = {}
    outcome = run("import os, sys, gc, codecs, weakref, threading, functools, timeit, copyreg, builtins\n"
                  "def later():\n" + "".join("    %s\n" % line for line in body.splitlines()) +
                  "    yield 'done'\n"
                  "HELD['generator'] = later()\n", HELD=holder, **names)
    if "generator" not in holder:
        return "not made: %r" % (outcome,), holder
    try:
        step = str(next(holder["generator"]))
    except Exception as error:   # noqa: BLE001 - recorded.
        step = type(error).__name__
    return step, holder


def environment(run, check):
    key = PLANT.encode() if os.name != "nt" else PLANT
    planted = (key, b"/written-by-the-script.so" if os.name != "nt" else "x")
    routes = {
        "data": run("import os\nos.environ._data[KEY] = VALUE\n", KEY=planted[0], VALUE=planted[1]),
        "dict_setitem": run("import os\ndict.__setitem__(os.environ, 'ANGELO_REVIEW_PLANT', 'x')\n"),
        "environb": run("import os\nif hasattr(os, 'environb'):\n    os.environb[b'ANGELO_REVIEW_PLANT'] = b'x'\n"
                        "else:\n    os.environ['ANGELO_REVIEW_PLANT'] = 'x'\n"),
        "rebound": run("import os\nos.environ = dict(os.environ, ANGELO_REVIEW_PLANT='x')\n"),
    }
    step, _ = _resume(run, "os.environ._data[KEY] = VALUE", KEY=planted[0], VALUE=planted[1])
    seen = {"mapping": PLANT in os.environ, "dict": PLANT in dict(os.environ), "child": _child_env(),
            "child_from_mapping": subprocess.run(
                [sys.executable, "-I", "-c", "import os; print(os.environ.get(%r))" % PLANT],
                env=dict(os.environ), capture_output=True, text=True, timeout=60).stdout.strip(),
            "view": type(os.environ).__name__}
    check("environ_mapping_is_the_environment", not seen["mapping"] and not seen["dict"] and seen["child"] is None
          and seen["child_from_mapping"] == "None" and seen["view"] == "Environ",
          (routes, step, seen))


def steered_import(run, check, plain):
    before = {"cwd": os.getcwd(), "env": _child_env()}
    mask = os.umask(0o022)
    os.umask(mask)
    # An installed module's top level made to call the script's choice: `sched.py` runs `_sentinel = object()`, here
    # in a fresh namespace where the script put its `object` first (the review's `runpy.run_module(...,
    # init_globals=...)` is the same shape; `runpy` itself doesn't import in a script here).
    outcomes = {name: run("import sched, importlib.util, functools, os\n"
                          "spec = importlib.util.spec_from_file_location('angelo_review_' + NAME, sched.__file__)\n"
                          "module = importlib.util.module_from_spec(spec)\n"
                          "module.object = functools.partial(%s)\nspec.loader.exec_module(module)\n" % call,
                          PLAIN=str(plain), NAME=name)["refused"]
                for name, call in (("putenv", "os.putenv, %r, 'x'" % PLANT),
                                   ("chdir", "os.chdir, PLAIN"),
                                   ("umask", "os.umask, 0"))}
    mask_after = os.umask(0o022)
    os.umask(mask_after)
    after = {"cwd": os.getcwd(), "env": _child_env()}
    check("steered_import_refused", all(reason is not None for reason in outcomes.values()) and before == after and
          mask == mask_after, (outcomes, before, after, mask, mask_after))


def generator_leftovers(run, check):
    import codecs
    import weakref
    holder_object = type("Kept", (), {})()
    exits_before = len(getattr(threading, "_threading_atexits", []))
    step, holder = _resume(run, "HELD['finalizer'] = weakref.finalize(KEPT, print, 'left by the script')\n"
                                "threading._register_atexit(functools.partial(print, 'left by the script'))\n"
                                "codecs.register_error('angelo_review', lambda error: ('?', error.end))",
                           KEPT=holder_object)
    finalizer = holder.get("finalizer")
    exits = list(getattr(threading, "_threading_atexits", []))[exits_before:]   # What the step registered.
    try:
        handler = codecs.lookup_error("angelo_review")
    except LookupError:
        handler = None
    found = {"step": step, "finalizer_at_exit": getattr(finalizer, "atexit", None),
             "thread_exit": [_registered(entry) for entry in exits],
             "error_handler": type(handler).__name__ if handler is not None else None}
    if finalizer is not None:
        finalizer.detach()
    for entry in exits:
        threading._threading_atexits.remove(entry)
    check("generator_leftovers_confined", step == "done" and found["finalizer_at_exit"] is False and
          found["thread_exit"] == ["Deferred"] and found["error_handler"] == "Deferred", found)


def region_leftovers(run, check):
    """The same registrations in a script's own run (a region): its finalizer's object is too small to be recorded,
    and `threading._register_atexit` wraps the callable in a partial of its own; both are confined as made."""
    holder_object = type("Kept", (), {})()
    exits_before = len(getattr(threading, "_threading_atexits", []))
    holder = {}
    outcome = run("import weakref, threading, functools\n"
                  "HELD['finalizer'] = weakref.finalize(KEPT, print, 'left by the script')\n"
                  "threading._register_atexit(functools.partial(print, 'left by the script'))\n",
                  KEPT=holder_object, HELD=holder)
    finalizer = holder.get("finalizer")
    exits = list(getattr(threading, "_threading_atexits", []))[exits_before:]
    found = {"outcome": outcome, "finalizer_at_exit": getattr(finalizer, "atexit", None),
             "thread_exit": [_registered(entry) for entry in exits]}
    if finalizer is not None:
        finalizer.detach()
    for entry in exits:
        threading._threading_atexits.remove(entry)
    check("region_leftovers_confined", found["finalizer_at_exit"] is False and found["thread_exit"] == ["Deferred"],
          found)


def _registered(entry):
    """What a thread-exit entry calls: threading keeps `lambda: func(*arg, **kwargs)`."""
    for cell in getattr(entry, "__closure__", None) or ():
        if callable(cell.cell_contents):
            return type(cell.cell_contents).__name__
    return type(entry).__name__


def _free_module_names(count):
    names = [name for name in ("tabnanny", "quopri", "colorsys", "sched", "netrc", "fileinput", "shelve", "mailbox",
                               "graphlib", "tomllib", "wave", "plistlib", "cmd", "imghdr", "sndhdr", "pydoc_data")
             if name not in sys.modules and importlib.util.find_spec(name) is not None]
    return names[:count]


def planted_imports(run, check, plain):
    names = _free_module_names(2)
    if len(names) < 2:
        check("planted_import_never_runs_as_angelo", False, names)
        return
    cached, pathed = names
    marker = plain / "planted-import-ran"
    marker.unlink(missing_ok=True)
    body = "open(%r, 'w').write('ran')\n" % str(marker)
    folder = plain / "planted-imports"
    run("import os, sys, importlib.machinery\nos.makedirs(FOLDER, exist_ok=True)\n"
        "for name in NAMES:\n    open(os.path.join(FOLDER, name + '.py'), 'w').write(BODY)\n"
        "sys.path_importer_cache[os.path.dirname(os.__file__)] = importlib.machinery.FileFinder(\n"
        "    FOLDER, (importlib.machinery.SourceFileLoader, ['.py']))\n",
        FOLDER=str(folder), NAMES=names, BODY=body)
    step, _ = _resume(run, "sys.path.insert(0, FOLDER)", FOLDER=str(folder))
    results = {}
    for name in names:   # Angelo's own later import (unconfined).
        try:
            module = importlib.import_module(name)
            results[name] = getattr(module, "__file__", None)
        except Exception as error:   # noqa: BLE001 - a refusal is the pass.
            results[name] = type(error).__name__
    ran = marker.exists()
    marker.unlink(missing_ok=True)
    while str(folder) in sys.path:
        sys.path.remove(str(folder))
    sys.path_importer_cache.pop(os.path.dirname(os.__file__), None)
    for name in names:
        module = sys.modules.get(name)
        if module is not None and str(folder) in str(getattr(module, "__file__", "")):
            del sys.modules[name]
    check("planted_import_never_runs_as_angelo", not ran and
          not any(str(folder) in str(value) for value in results.values()), (step, results, ran))


def registries_and_threads(run, check):
    import builtins
    import copyreg
    planted_type = type("AngeloReviewPlanted", (), {})
    step, _ = _resume(run, "copyreg.dispatch_table[PLANTED] = lambda obj: (str, ('planted',))", PLANTED=planted_type)
    registry = planted_type in copyreg.dispatch_table
    copyreg.dispatch_table.pop(planted_type, None)
    holder = {}
    run("import builtins\ndef later():\n    builtins.angelo_review_thread = lambda: 'planted'\n    yield 'done'\n"
        "HELD['generator'] = later()\n", HELD=holder)
    steps = []
    worker = threading.Thread(target=lambda: steps.append(next(holder["generator"])))
    worker.start()
    worker.join(60)
    other_thread = hasattr(builtins, "angelo_review_thread")
    if other_thread:
        del builtins.angelo_review_thread
    check("generator_registry_and_thread_put_back", step == "done" and steps == ["done"] and not registry and
          not other_thread, (step, steps, registry, other_thread))


def confinement_machinery(run, check):
    refused = {name: run(source)["refused"] for name, source in (
        ("gc_freeze", "import gc\ngc.freeze()\n"),
        ("gc_unfreeze", "import gc\ngc.unfreeze()\n"),
        ("monitoring_free", "import sys\nfor tool in range(6):\n    sys.monitoring.free_tool_id(tool)\n"),
        ("monitoring_events", "import sys\nfor tool in range(6):\n    sys.monitoring.set_events(tool, 0)\n"))}
    check("confinement_machinery_refused", all(reason is not None for reason in refused.values()), refused)


def no_false_refusal(run, check):
    kept = type("Kept", (), {})()
    step, holder = _resume(run, "HELD['time'] = timeit.timeit('1', number=1)\n"
                                "HELD['finalizer'] = weakref.finalize(KEPT, print, 'done')",
                           KEPT=kept)
    finalizer = holder.get("finalizer")
    if finalizer is not None:
        finalizer.detach()
    check("generator_ordinary_work", step == "done" and isinstance(holder.get("time"), float) and
          finalizer is not None, (step, holder))


def frozen_modules(run, check):
    """Every module Python ships frozen (its code compiled into the binary: ``runpy``, ``zipimport``, ``os``...) imports
    from a script, fresh or again: its unmarshal is installed code's, never a script's bytes. The import system's own
    bootstrap and ``site`` are not executed a second time (that would rerun start-up), but their code still loads."""
    import _imp
    names = sorted(_imp._frozen_module_names())
    holder = {}
    outcome = run("import _imp, importlib, importlib.util\n"
                  "AGAIN = {'_frozen_importlib', '_frozen_importlib_external', 'importlib._bootstrap',\n"
                  "         'importlib._bootstrap_external', 'site'}\n"
                  "for name in NAMES:\n"
                  "    try:\n"
                  "        assert _imp.get_frozen_object(name) is not None\n"
                  "        importlib.import_module(name)\n"
                  "        if name not in AGAIN:\n"
                  "            spec = importlib.util.find_spec(name)\n"
                  "            spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
                  "        HELD[name] = 'ok'\n"
                  "    except BaseException as error:\n"
                  "        HELD[name] = repr(error)[:160]\n", NAMES=names, HELD=holder)
    failed = {name: result for name, result in holder.items() if result != "ok"}
    # Frozen code a script executes itself runs confined (its frames sit under the script's), and bytes the caller
    # passes as `data` are judged as any other unmarshal.
    executed = run("import _imp\nns = {'__name__': 'angelo_frozen_os'}\nexec(_imp.get_frozen_object('os'), ns)\n"
                   "ns['popen']('true')\n")
    # (Python reports a refused unmarshal of frozen data as "Frozen object ... is invalid": no code comes back.)
    loaded = {}
    supplied = run("import _imp, marshal\ntry:\n"
                   "    HELD['code'] = _imp.get_frozen_object('os', marshal.dumps(compile('x = 1', '<script>', 'exec')))\n"
                   "except BaseException as error:\n    HELD['error'] = repr(error)[:160]\n", HELD=loaded)
    check("frozen_code_stays_confined", executed["refused"] is not None and "code" not in loaded,
          (executed, supplied, loaded))
    check("frozen_modules_import_from_script", bool(names) and not failed and len(holder) == len(names) and
          outcome["refused"] is None and "error" not in outcome, (outcome, len(names), failed))


def run_all(run, check, plain):
    environment(run, check)
    steered_import(run, check, plain)
    generator_leftovers(run, check)
    region_leftovers(run, check)
    planted_imports(run, check, plain)
    registries_and_threads(run, check)
    confinement_machinery(run, check)
    no_false_refusal(run, check)
    frozen_modules(run, check)
