# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewer's fourth read of piece 7a (2026-11-16), for test_blender_confinement_escapes. Each check failed
before its fix and judges the class, not the route.

* MUST-1: nothing of the script's runs unjournaled while a region settles: neither a hook the import system would
  call, nor an object's `__del__` as the undo releases it.
* MUST-2: a `weakref.finalize` record the region found, changed in place, goes back.
* MUST-3(a): what a script leaves in an installed module's namespace, data included, goes back (an opener), Angelo's
  own modules too when the script caused it (an Angelo function storing what the script gave it).
* SHOULD: a step of a generator the script left, run outside it, can't leave a finder on `sys.meta_path` or a call
  on the interpreter's exit list.
"""

import json
import sys
import threading
import weakref

_SHIFT = ("import json\n"
          "def shift(*args):\n    json.loads = eval\n")


def _loads_kept(loads):
    kept = json.loads is loads
    json.loads = loads
    return kept


def settling_runs_nothing(run, check):
    import importlib.util
    import os
    loads = json.loads
    fresh = next(name for name in ("graphlib", "getopt", "filecmp", "cmd", "codeop", "quopri",
                                   "zipapp", "pyclbr", "symtable", "timeit", "netrc", "shelve", "tabnanny")
                 if name not in sys.modules and importlib.util.find_spec(name) is not None)
    # A module imported afresh (a name the region gained), then a path hook (the import system calls hooks for a path
    # entry it has no finder cached for) that rebinds and declines, with the standard library's cached finder gone:
    # anything resolving the name again at the region's end would call it, unjournaled.
    hooked = run(_SHIFT + "import importlib, os, sys\nimportlib.import_module(NAME)\n"
                 "def hook(entry):\n    shift()\n    raise ImportError\n"
                 "sys.path_hooks.insert(0, hook)\nsys.path_importer_cache.pop(STDLIB, None)\n",
                 NAME=fresh, STDLIB=os.path.dirname(os.__file__))
    after_hook = _loads_kept(loads)
    # An object put into an installed module that rebinds when it goes: the undo releases it.
    released = run(_SHIFT + "class Shift:\n    def __call__(self):\n        pass\n"
                   "    def __del__(self):\n        shift()\n"
                   "json._angelo_review4 = Shift()\n")
    after_release = _loads_kept(loads)
    json.__dict__.pop("_angelo_review4", None)
    check("settling_runs_nothing_unjournaled", after_hook and after_release, (hooked, after_hook, released,
                                                                             after_release))


def finalize_in_place(run, check):
    class Kept:
        pass
    kept = Kept()
    finalizer = weakref.finalize(kept, print, "Angelo's own cleanup")
    info = weakref.finalize._registry[finalizer]
    before = (info.func, info.args, info.atexit)
    outcome = run("import weakref\ninfo = weakref.finalize._registry[F]\n"
                  "info.func = print\ninfo.args = ('the script\\'s call',)\ninfo.atexit = True\n", F=finalizer)
    after = (info.func, info.args, info.atexit)
    finalizer.detach()
    check("finalize_record_changed_in_place_put_back", outcome["refused"] is None and after == before,
          (outcome, after == before))


def namespace_data(run, check):
    import urllib.request
    opener = urllib.request._opener
    outcome = run("import ssl, urllib.request\n"
                  "context = ssl._create_unverified_context()\n"
                  "urllib.request.install_opener(urllib.request.build_opener(\n"
                  "    urllib.request.HTTPSHandler(context=context), urllib.request.ProxyHandler({'https': 'x:1'})))\n")
    left = urllib.request._opener
    urllib.request._opener = opener
    check("namespace_data_put_back", outcome["refused"] is None and left is opener, (outcome, type(left).__name__))


def steered_angelo_state(run, check):
    """Angelo's own code changing its own package is its tool state only when no frame of the script's runs: a script
    calling an Angelo function that stores what it was given leaves nothing behind."""
    from angelo_agent import blender_assets
    saved = (blender_assets._bundled, blender_assets._bundled_files, blender_assets._bundled_notes,
             dict(blender_assets._rows))
    held = {}
    outcome = run("try:\n    import sys\n    blender_assets = sys.modules['angelo_agent.blender_assets']\n"
                  "    blender_assets.publish([], [], files=(), notes=(NOTE,))\n"
                  "    HELD['notes'] = blender_assets._bundled_notes\n"
                  "except Exception as error:\n    HELD['refused'] = str(error)\n", HELD=held, NOTE="the script's note")
    left = blender_assets._bundled_notes
    blender_assets._bundled, blender_assets._bundled_files, blender_assets._bundled_notes = saved[:3]
    blender_assets._rows.clear()
    blender_assets._rows.update(saved[3])
    # Put back when it ends, or never reached (Angelo's modules are opaque to scripts).
    reached = held.get("notes") == ("the script's note",) or "isn't open to scripts" in held.get("refused", "")
    check("angelo_state_from_script_put_back", outcome["refused"] is None and reached and left is saved[2],
          (outcome, held, left))


def generator_steps(run, check):
    held = {}
    outcome = run("import sys, threading, functools\n"
                  "class Finder:\n    def find_spec(self, name, path=None, target=None):\n        return None\n"
                  "def later():\n    yield 1\n    sys.meta_path.insert(0, Finder())\n"
                  "    threading._threading_atexits.append(functools.partial(print, 'at exit'))\n    yield 2\n"
                  "g = later()\nnext(g)\nHELD['g'] = g\n", HELD=held)
    meta, exits = list(sys.meta_path), list(threading._threading_atexits)
    try:
        next(held["g"])   # A step outside the script, as Angelo's own code might drive it.
    except Exception as error:   # noqa: BLE001 - a refusal is fine too.
        held["error"] = repr(error)[:200]
    added = [type(finder).__name__ for finder in sys.meta_path if not any(finder is old for old in meta)]
    planted = [item for item in threading._threading_atexits if not any(item is old for old in exits)]
    sys.meta_path[:] = meta
    threading._threading_atexits[:] = exits
    held.pop("g", None)
    check("generator_step_leaves_no_import_steering", outcome["refused"] is None and not added and not planted,
          (outcome, added, len(planted), held.get("error")))


def run_all(run, check):
    settling_runs_nothing(run, check)
    finalize_in_place(run, check)
    namespace_data(run, check)
    steered_angelo_state(run, check)
    generator_steps(run, check)
