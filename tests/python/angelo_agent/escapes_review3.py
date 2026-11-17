# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewer's third read of pieces 4-6 (2026-11-16), for test_blender_confinement_escapes. Each check failed
before its fix and judges the class, not the route.

* MUST-A: an installed file loaded under a name the script chose (`_bootstrap._load`, or a finder it put on
  `sys.meta_path`) never stays in `sys.modules` under that name.
* MUST-B: a record of any kind a script puts in `weakref.finalize._registry` never runs at interpreter exit.
* SHOULD 1: ordinary placements refuse no later import in the run (an interpreter hook; a patch made after the
  imports; installed code placing installed code); a placement a later import could call refuses it, naming it.
* SHOULD 5: a placement in a module-level registry seeds like one in a namespace; a fresh import's own definition
  is kept only in its own package (a module changing its own namespace, as `typing.__getattr__` does, is its own).
"""

import importlib
import importlib.util
import os
import sys
import weakref

import bpy

# Small installed modules with no side effects at import, picked unloaded at the time of each check.
_CANDIDATES = ("graphlib", "getopt", "filecmp", "cmd", "codeop", "quopri", "zipapp", "pyclbr", "symtable", "timeit",
               "netrc", "shelve", "difflib", "textwrap", "secrets", "plistlib", "fractions", "statistics", "calendar",
               "optparse", "pickletools", "colorsys", "wave", "tabnanny")


def _fresh():
    for name in _CANDIDATES:
        if name not in sys.modules and name not in _fresh.taken and importlib.util.find_spec(name) is not None:
            _fresh.taken.add(name)
            return name
    raise RuntimeError("no unloaded installed module left to import")


_fresh.taken = set()


def _shape(module):
    return {key: type(value).__name__ for key, value in vars(module).items()}


def _origin_honest(name):
    """Whether `sys.modules[name]`, if any, is the file the installed finders resolve `name` to."""
    module = sys.modules.get(name)
    if module is None:
        return True
    found = getattr(getattr(module, "__spec__", None), "origin", None)
    sys.modules.pop(name)
    try:
        real = importlib.util.find_spec(name)
    finally:
        sys.modules[name] = module
    return real is not None and found is not None and os.path.realpath(found) == os.path.realpath(real.origin)


def remapped_names(run, check):
    """MUST-A: pickle's file loaded as another (unloaded) installed module's name."""
    direct, finder = _fresh(), _fresh()
    outcomes = {
        "load": run("import importlib._bootstrap as b, importlib.util as u, pickle\n"
                    "b._load(u.spec_from_file_location(NAME, pickle.__file__))\n", NAME=direct),
        "finder": run("import importlib, importlib.util as u, pickle, sys\n"
                      "class Finder:\n"
                      "    def find_spec(self, name, path=None, target=None):\n"
                      "        if name == NAME:\n"
                      "            return u.spec_from_file_location(name, pickle.__file__)\n"
                      "sys.meta_path.insert(0, Finder())\nimportlib.import_module(NAME)\n", NAME=finder),
    }
    honest = {name: _origin_honest(name) for name in (direct, finder)}
    for name in (direct, finder):
        if not honest[name]:
            sys.modules.pop(name, None)
    check("remapped_name_never_kept", all(honest.values()), (outcomes, honest))


def finalize_records(run, check):
    """MUST-B: `_exitfunc` and `__call__` accept any record with atexit/index/func/args."""
    finalizer_type = weakref.finalize
    before = dict(finalizer_type._registry)
    kept = type("Kept", (), {})()
    outcome = run("import types, weakref\nF = weakref.finalize\n"
                  "a = object.__new__(F)\n"
                  "F._registry[a] = types.SimpleNamespace(weakref=weakref.ref(KEPT, a), func=print,\n"
                  "    args=('namespace record',), kwargs=None, atexit=True, index=next(F._index_iter))\n"
                  "class Info(F._Info):\n    atexit = property(lambda self: True)\n"
                  "b = object.__new__(F)\ni = object.__new__(Info)\ni.weakref = weakref.ref(KEPT, b)\ni.func = print\n"
                  "i.args = ('subclass record',)\ni.kwargs = {}\ni.index = next(F._index_iter)\nF._registry[b] = i\n"
                  "class Key(F):\n    __slots__ = ()\n"
                  "c = object.__new__(Key)\nj = F._Info()\nj.weakref = weakref.ref(KEPT, c)\nj.func = print\n"
                  "j.args = ('key record',)\nj.kwargs = {}\nj.atexit = True\nj.index = next(F._index_iter)\n"
                  "F._registry[c] = j\n", KEPT=kept)
    added = {key: info for key, info in finalizer_type._registry.items() if key not in before}
    left = []
    for key, info in added.items():
        exact = type(key) is finalizer_type and type(info) is finalizer_type._Info
        if not (exact and info.atexit is False and type(info.func).__name__ == "Deferred"):
            left.append((type(key).__name__, type(info).__name__, getattr(info, "atexit", None)))
    for key in added:
        finalizer_type._registry.pop(key, None)
    check("finalize_records_of_any_kind_never_at_exit", outcome["refused"] is None and not left, (outcome, left))


def multiprocessing_finalizers(run, check):
    """The other registry run at exit (`multiprocessing.util._run_finalizers`): nothing the script adds stays."""
    import multiprocessing.util as util
    before = dict(util._finalizer_registry)
    outcome = run("import functools, multiprocessing.util as util\n"
                  "util._finalizer_registry[(None, 999999)] = functools.partial(print, 'at exit')\n")
    added = {key: value for key, value in util._finalizer_registry.items() if key not in before}
    for key in added:
        util._finalizer_registry.pop(key, None)
    check("multiprocessing_finalizers_never_at_exit", outcome["refused"] is None and not added, (outcome, added))


def imports_after_placements(run, check):
    """SHOULD 1: an interpreter hook set (the interpreter calls it, an import doesn't), or a patch made after the
    imports it needs, then a module imported for the first time (an exporter's lazy submodule): works. A patch made
    first is refused, and the refusal names what the script placed."""
    shapes, outcomes = {}, {}
    patch = "unittest.mock.patch.object(json, 'dumps', lambda *a, **k: '').start()"
    for route, before, after in (("hook", "sys.excepthook = lambda *a: None", ""), ("patch_after", "", patch)):
        name, held = _fresh(), {}
        # An import statement, as an exporter's lazy import is (the import system's own audited path).
        outcomes[route] = run("import json, sys, unittest.mock\n%s\nmodule = __import__(NAME)\n%s\n"
                              "HELD['shape'] = sorted(vars(module))\nunittest.mock.patch.stopall()\n" % (before, after),
                              NAME=name, HELD=held)
        module = importlib.import_module(name)   # Angelo's own import afterwards: whole, whatever the run did.
        shapes[route] = bool(held.get("shape")) and set(held["shape"]) <= set(vars(module))
    check("import_after_placement_works", all(outcome["refused"] is None for outcome in outcomes.values()) and
          all(shapes.values()), (outcomes, shapes))
    name, held = _fresh(), {}
    refused = run("import json, unittest.mock\n" + patch + "\n"
                  "try:\n    __import__(NAME)\nexcept Exception as error:\n    HELD['error'] = str(error)\n"
                  "finally:\n    unittest.mock.patch.stopall()\n", NAME=name, HELD=held)
    check("seeded_import_refusal_names_cause", "json.dumps" in held.get("error", "") and name not in sys.modules,
          (refused, held))
    # Installed code placing installed code seeds nothing: what the import defines stays as it defined it.
    name = _fresh()
    held = {}
    outcome = run("import logging, json\nlogging.captureWarnings(True)\n"
                  "HELD['shape'] = json.dumps(SHAPE(__import__(NAME)))\nlogging.captureWarnings(False)\n",
                  NAME=name, HELD=held, SHAPE=_shape)
    module = sys.modules.get(name)
    import json
    check("installed_placement_seeds_nothing", outcome["refused"] is None and module is not None and
          json.loads(held.get("shape", "{}")) == _shape(module), (outcome, module is not None))


def registry_seeding(run, check):
    """SHOULD 5: a callable put in a module-level registry (`encodings._cache`) seeds: an import afterwards defines
    nothing that outlives the run."""
    name, held = _fresh(), {}
    outcome = run("import encodings, functools, importlib\n"
                  "encodings._cache['angelo_review3'] = functools.partial(print)\n"
                  "try:\n    importlib.import_module(NAME)\nexcept Exception as error:\n"
                  "    HELD['error'] = str(error)\n", NAME=name, HELD=held)
    left = name in sys.modules
    module = importlib.import_module(name)
    check("registry_placement_seeds", "angelo_review3" in held.get("error", "") and not left and module is not None,
          (outcome, held, left))


def cross_package_definitions(run, check):
    """SHOULD 5: a fresh import's top level changing another package's module is not that module's definition
    (put back); changing its own package's is (kept)."""
    from pathlib import Path
    root = Path(bpy.utils.user_resource("SCRIPTS", path="modules", create=True))
    package = root / "angelo_fixture_review3"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("from . import _impl\n\n\ndef helper():\n    return 'own'\n\n\n"
                                         "_impl.helper = helper\nimport json\n\n\n"
                                         "def dumps(*args, **kwargs):\n    return 'other'\n\n\njson.dumps = dumps\n")
    (package / "_impl.py").write_text("def helper():\n    return None\n")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    import json
    dumps = json.dumps
    outcome = run("import angelo_fixture_review3\n")
    impl = sys.modules.get("angelo_fixture_review3._impl")
    found = {"own": impl.helper() if impl is not None else None, "other": json.dumps is not dumps}
    json.dumps = dumps
    check("definition_only_in_own_package", outcome["refused"] is None and found == {"own": "own", "other": False},
          (outcome, found))


def run_all(run, check):
    remapped_names(run, check)
    finalize_records(run, check)
    multiprocessing_finalizers(run, check)
    imports_after_placements(run, check)
    registry_seeding(run, check)
    cross_package_definitions(run, check)
