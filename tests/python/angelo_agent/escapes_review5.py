# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewer's fifth read (piece 8, 2026-11-16), for test_blender_confinement_escapes. Each check failed before
its fix and judges the class, not the route.

* MUST-1: a module the region gained stays only when the loader that reads its body reads the file its name resolves
  to: a wrapper is judged by the loader it holds (`LazyLoader`), and a spec's origin, being any text its maker gave,
  decides nothing (a file loader of another module's file under a forged origin).
* MUST-3: logging is as the region found it at the end of every kind of region, a step of a generator the script
  left included.
* SHOULD: a loaded package's `__path__` changed in place goes back.
"""

import importlib.util
import logging
import os
import sys

_FRESH = ("plistlib", "graphlib", "getopt", "filecmp", "cmd", "codeop", "quopri", "zipapp", "pyclbr", "symtable",
          "timeit", "netrc", "shelve", "tabnanny", "sched", "colorsys", "fnmatch", "mailcap", "stringprep")


def _fresh_names(count):
    names = [name for name in _FRESH if name not in sys.modules and importlib.util.find_spec(name) is not None]
    return names[:count]


def _loaded_from(module):
    """The file the module's body was read from (the loader's own path, unwrapped from a LazyLoader)."""
    spec = getattr(module, "__spec__", None)
    loader = getattr(spec, "loader", None)
    while type(loader).__name__ == "LazyLoader":
        loader = loader.__dict__.get("loader")
    return getattr(loader, "path", None)


def gained_module_judged_by_its_loader(run, check):
    import pickle
    names = _fresh_names(2)
    lazy, forged = names[0], names[1]
    real = {name: importlib.util.find_spec(name).origin for name in names}
    outcome = run("import importlib.util, importlib.machinery, importlib._bootstrap as bootstrap\n"
                  # A lazy loader holding a file loader of pickle's file, under another module's name.
                  "inner = importlib.machinery.SourceFileLoader(LAZY, PICKLE)\n"
                  "spec = importlib.util.spec_from_loader(LAZY, importlib.util.LazyLoader(inner), origin=REAL_LAZY)\n"
                  "spec.has_location = True\n"   # `__file__` names the real file too.
                  "bootstrap._load(spec)\n"
                  # A file loader of pickle's file, its spec naming the real file as its origin.
                  "spec = importlib.machinery.ModuleSpec(FORGED, importlib.machinery.SourceFileLoader(FORGED, PICKLE),"
                  " origin=REAL_FORGED)\n"
                  "spec.has_location = True\n"
                  "bootstrap._load(spec)\n",
                  LAZY=lazy, FORGED=forged, PICKLE=pickle.__file__, REAL_LAZY=real[lazy], REAL_FORGED=real[forged])
    left = {}
    for name in names:
        module = sys.modules.get(name)
        if module is not None and _loaded_from(module) == pickle.__file__:
            left[name] = type(module).__name__
            sys.modules.pop(name, None)
    check("gained_module_judged_by_its_loader", not left, (outcome, left))


def package_path_in_place(run, check):
    import email
    before = list(email.__path__)
    outcome = run("import email, tempfile\nemail.__path__.insert(0, tempfile.gettempdir())\n"
                  "email.__path__.append('elsewhere')\n")
    left = list(email.__path__)
    email.__path__[:] = before
    check("package_path_changed_in_place_put_back", outcome["refused"] is None and left == before,
          (outcome, left[:3]))


def logging_after_a_step(run, check):
    root = logging.getLogger()
    angelo = logging.getLogger("angelo_agent")
    before = (list(root.handlers), root.level, list(angelo.handlers), angelo.propagate)
    held = {}
    outcome = run("import logging, logging.handlers\n"
                  "def later():\n    yield 1\n"
                  "    logging.getLogger().addHandler(logging.handlers.SocketHandler('127.0.0.1', 9))\n"
                  "    logging.getLogger().setLevel(logging.DEBUG)\n"
                  "    logging.getLogger('angelo_agent').addHandler(logging.NullHandler())\n"
                  "    yield 2\n"
                  "g = later()\nnext(g)\nHELD['g'] = g\n", HELD=held)
    try:
        next(held["g"])   # A step outside the script, as Angelo's own code might drive it.
    except Exception as error:   # noqa: BLE001 - a refusal is fine too.
        held["error"] = repr(error)[:200]
    after = (list(root.handlers), root.level, list(angelo.handlers), angelo.propagate)
    added = [type(h).__name__ for h in after[0] + after[2] if not any(h is old for old in before[0] + before[2])]
    root.handlers[:], angelo.handlers[:] = before[0], before[2]
    root.setLevel(before[1])
    held.pop("g", None)
    check("logging_as_found_after_a_generator_step", outcome["refused"] is None and not added and
          after[1] == before[1], (outcome, added, after[1], before[1], held.get("error")))


def run_all(run, check):
    gained_module_judged_by_its_loader(run, check)
    package_path_in_place(run, check)
    logging_after_a_step(run, check)


if __name__ == "__main__":
    print(os.path.basename(__file__), "runs inside test_blender_confinement_escapes")
