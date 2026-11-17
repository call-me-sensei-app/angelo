# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""While a host test file's tests run, the product imports the module objects that file holds.

Many test files import product modules inside ``mock.patch.dict(sys.modules, ...)`` (under a stand-in ``bpy``). The
block's end drops every module first imported inside it from ``sys.modules``, but the package still holds it as an
attribute, and so do the test file and every test helper that took it. From then on two readers can get two
objects under one name: ``from angelo_agent import x`` reads the package's attribute, while the product's own
``from .x import ...`` inside a function reads ``sys.modules`` and, finding nothing, imports a second copy. A test
then patches one copy and the code runs the other. Which test met it depended on which files shared a process (VM
CI, Windows shard 1 of 4 at 02a5d0bb885: ``test_native_edit_tool_hold_review`` after
``test_native_asset_shelf_add``, "the predicate was read 0 times" while the call ran).

The suite also relies on that attribute: a file imports a helper test module "first: it stands in for bpy" and then
takes the product module the helper imported, from the package. So nothing is removed and no import is changed
(removing the attributes, the first fix, broke 56 such tests on VM CI of 57d43cfa3c9). What a file holds is made
true of ``sys.modules`` for as long as its own tests run, and put back after:

* ``held(module)``: the product modules a test module holds: those in its globals, those its imported names came
  from, then the same of each test helper module it took names from. The file's own come first.
* ``bound(modules)``: inside the block ``sys.modules`` and each parent package's attribute hold them.
* ``Loader``: every file's tests are one ``FileModules`` suite, which runs them, and their class and module
  tear-downs, inside ``bound(held(file))``. Named files and discovery both load through it.

Only modules loaded from the product's folders are bound: never a stand-in, the standard library or a test module.
Standard library only.
"""

import contextlib
import os
from pathlib import Path
import sys
import types
import unittest

HERE = Path(__file__).resolve().parent
PRODUCT = (str(HERE.parents[2] / "scripts"),)   # Folders whose modules are the product's.
TESTS = (str(HERE),)
_MISSING = object()
_real = {}   # A module file's path -> its real path (asked once per file).


def _inside(module, folders):
    """True for a module an import loaded from a file under one of ``folders``."""
    path = getattr(module, "__file__", None)
    if not isinstance(path, str) or getattr(module, "__spec__", None) is None:
        return False
    real = _real.get(path)
    if real is None:
        real = _real[path] = os.path.realpath(path)
    return any(real.startswith(os.path.realpath(folder) + os.sep) for folder in folders)


def _came_from(value):
    """The module a global that is no module came from: the one its ``__module__`` names, as an import or the
    package would give it now, when that module's own name for it is this very object. None otherwise."""
    try:
        name, own = getattr(value, "__module__", None), getattr(value, "__name__", None)
        if not isinstance(name, str) or not isinstance(own, str):
            return None
        candidates = [sys.modules.get(name)]
        walked = sys.modules.get(name.partition(".")[0])
        for part in name.split(".")[1:]:
            walked = vars(walked).get(part) if isinstance(walked, types.ModuleType) else None
        candidates.append(walked)
        for module in candidates:
            if isinstance(module, types.ModuleType) and vars(module).get(own) is value:
                return module
    except Exception:  # noqa: BLE001 - a global that cannot be asked holds no module for this.
        return None
    return None


def held(module, product=PRODUCT, tests=TESTS):
    """``{name: module}`` of the product modules test module ``module`` holds (see the module's text)."""
    found, seen, queue = {}, {id(module)}, [module]
    while queue:
        values = list(vars(queue.pop(0)).values())
        owners = [value if isinstance(value, types.ModuleType) else _came_from(value) for value in values]
        # Modules held by name first, then the modules the other names came from.
        for direct in (True, False):
            for value, owner in zip(values, owners):
                if owner is None or (value is owner) is not direct:
                    continue
                if _inside(owner, product):
                    found.setdefault(owner.__name__, owner)
                elif _inside(owner, tests) and id(owner) not in seen:
                    seen.add(id(owner))
                    queue.append(owner)
    return found


@contextlib.contextmanager
def bound(modules):
    """Inside the block ``sys.modules`` holds each of ``modules`` under its name, and its parent package holds it as
    its attribute. Afterwards each is as it was, unless something else replaced it meanwhile."""
    undo = []

    def put(mapping, key, module):
        before = mapping.get(key, _MISSING)
        if before is not module:
            undo.append((mapping, key, before, module))
            mapping[key] = module
    for name in sorted(modules, key=lambda name: name.count(".")):   # A package before its submodules.
        put(sys.modules, name, modules[name])
    for name, module in modules.items():
        parent = sys.modules.get(name.rpartition(".")[0])
        if isinstance(parent, types.ModuleType):
            put(vars(parent), name.rpartition(".")[2], module)
    try:
        yield
    finally:
        for mapping, key, before, ours in reversed(undo):
            if mapping.get(key, _MISSING) is ours:
                if before is _MISSING:
                    del mapping[key]
                else:
                    mapping[key] = before


class FileModules(unittest.TestSuite):
    """One test file's tests, run while the product imports the modules that file holds."""

    def __init__(self, tests=(), modules=None):
        super().__init__(tests)
        self.modules = dict(modules or {})

    def run(self, result, debug=False):
        nested = getattr(result, "_testRunEntered", False) is not False
        with bound(self.modules):
            super().run(result, debug)
            if nested:
                # The file's last class and its module are torn down here, inside the block, as unittest does at
                # the end of a whole run; otherwise they would run when the next file's first test starts.
                self._tearDownPreviousClass(None, result)
                self._handleModuleTearDown(result)
                result._previousTestClass = None
        return result


class Loader(unittest.TestLoader):
    """A file's tests as one ``FileModules`` suite, for named files and for discovery alike."""

    product, tests = PRODUCT, TESTS

    def loadTestsFromModule(self, module, *, pattern=None):
        loaded = super().loadTestsFromModule(module, pattern=pattern)
        return FileModules([loaded], held(module, self.product, self.tests))
