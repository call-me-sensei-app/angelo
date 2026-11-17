# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An installed module's body a script runs itself, with names it chose already in the module's namespace, for
test_blender_confinement_escapes: whatever the script planted never runs as Angelo's once the script has ended.

* The script makes the module (`module_from_spec`), plants a name its functions read (`object`), registers it in
  `sys.modules` and runs the body itself (`exec_module`).
* The script's own finder on `sys.meta_path` returns a loader whose `create_module` plants the name, and the import
  system runs the body (the loader's inherited `exec_module` is installed code; no frame of the script's is running):
  a loader class of the script's, or an installed loader with a builtin of the script's choosing as its attribute.

Afterwards Angelo imports the module by its name and calls it: the planted call must not run.
"""

import importlib
import json
import sys

import bpy

FIXTURE = "import json\n\n\ndef run():\n    return object()\n"
PLANT = "functools.partial(setattr, json, 'loads', eval)"


def _fixture(name):
    from pathlib import Path
    root = Path(bpy.utils.user_resource("SCRIPTS", path="modules", create=True))
    (root / (name + ".py")).write_text(FIXTURE)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    return str(root / (name + ".py"))


def _after(name):
    """Angelo, after the script: import the module by its name and call it."""
    loads = json.loads
    try:
        module = importlib.import_module(name)
        module.run()
        result = "ran"
    except Exception as error:   # noqa: BLE001 - reported.
        result = repr(error)[:200]
    planted = json.loads is not loads
    json.loads = loads
    return planted, result


def planted_direct(run, check):
    name = "angelo_fixture_planted_direct"
    _fixture(name)
    outcome = run("import functools, importlib.util, json, sys\nspec = importlib.util.find_spec(NAME)\n"
                  "m = importlib.util.module_from_spec(spec)\nm.object = %s\nsys.modules[NAME] = m\n"
                  "spec.loader.exec_module(m)\n" % PLANT, NAME=name)
    planted, result = _after(name)
    check("planted_direct_load_never_runs_as_angelo", not planted and result == "ran", (outcome, planted, result))


def planted_loader(run, check):
    name = "angelo_fixture_planted_loader"
    path = _fixture(name)
    outcome = run("import functools, importlib.machinery, importlib.util, json, sys\n"
                  "class Loader(importlib.machinery.SourceFileLoader):\n"
                  "    def create_module(self, spec):\n"
                  "        made = importlib.util.spec_from_file_location(spec.name, PATH)\n"
                  "        m = importlib.util.module_from_spec(made)\n"
                  "        m.object = %s\n        return m\n"
                  "class Finder:\n"
                  "    def find_spec(self, name, path=None, target=None):\n"
                  "        if name == NAME:\n"
                  "            return importlib.util.spec_from_file_location(name, PATH, loader=Loader(name, PATH))\n"
                  "sys.meta_path.insert(0, Finder())\nimportlib.import_module(NAME)\n" % PLANT, NAME=name, PATH=path)
    planted, result = _after(name)
    check("planted_loader_never_runs_as_angelo", not planted and result == "ran", (outcome, planted, result))


def planted_loader_attribute(run, check):
    """The same through an installed loader's own attribute: a plain `SourceFileLoader` whose `create_module` the
    script set to a builtin (a partial) handing back the module it prepared."""
    name = "angelo_fixture_planted_attribute"
    path = _fixture(name)
    outcome = run("import functools, importlib.machinery, importlib.util, json, sys, types\n"
                  "m = importlib.util.module_from_spec(importlib.util.spec_from_file_location(NAME, PATH))\n"
                  "m.object = %s\nheld = types.SimpleNamespace(m=m)\n"
                  "loader = importlib.machinery.SourceFileLoader(NAME, PATH)\n"
                  "loader.create_module = functools.partial(getattr, held, 'm')\n"
                  "class Finder:\n"
                  "    def find_spec(self, name, path=None, target=None):\n"
                  "        if name == NAME:\n"
                  "            return importlib.util.spec_from_file_location(name, PATH, loader=loader)\n"
                  "sys.meta_path.insert(0, Finder())\nimportlib.import_module(NAME)\n" % PLANT, NAME=name, PATH=path)
    planted, result = _after(name)
    check("planted_loader_attribute_never_runs_as_angelo", not planted and result == "ran",
          (outcome, planted, result))


def run_all(run, check):
    planted_direct(run, check)
    planted_loader(run, check)
    planted_loader_attribute(run, check)
