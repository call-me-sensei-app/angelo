# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``blender_stand_in.StandIn`` hands back the modules its imports read, after whatever ran before it in the process.

The 2.0.1 CI of record (f9f749b4af7, Linux and Windows): test_native_external_entry_elsewhere passed alone, but in
the whole suite ``ui`` read an ``angelo_agent.console`` that was in no ``sys.modules``. ``from . import x`` takes the
package's attribute ``x`` before it imports, and an earlier test that imported a real submodule under
``mock.patch.dict(sys.modules)`` leaves exactly that: the attribute, without its ``sys.modules`` entry. The stand-in
dropped only the attributes of modules still in ``sys.modules``. Here such leftovers are planted for every submodule
the stand-in's modules read, so the test needs no particular order of the suite.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from blender_stand_in import StandIn  # noqa: E402

NAMES = ("ui", "external_ui", "external_setup")


def submodules_read(modules):
    """The ``angelo_agent.<name>`` package submodules that ``modules`` hold as attributes."""
    return sorted({value.__name__.split(".")[1] for module in modules.values() for value in vars(module).values()
                   if isinstance(value, types.ModuleType) and value.__name__.startswith("angelo_agent.")})


def mismatches(modules):
    """``module.attribute`` for each angelo_agent module a handed-back module reads that ``sys.modules`` lacks or
    holds as another object."""
    return ["%s.%s" % (name, attribute) for name, module in modules.items()
            for attribute, value in vars(module).items()
            if isinstance(value, types.ModuleType) and value.__name__.startswith("angelo_agent.")
            and sys.modules.get(value.__name__) is not value]


class LeftoverTests(unittest.TestCase):
    def setUp(self):
        with StandIn(NAMES) as modules:
            self.read = submodules_read(modules)
        self.assertIn("console", self.read, "ui reads console (the CI of record's case)")

    def plant(self, in_sys_modules):
        """A leftover copy of each submodule read, as an earlier test can leave one; put back by a cleanup."""
        package = vars(angelo_agent)
        before = {name: (package.get(name, self), sys.modules.get("angelo_agent." + name, self)) for name in self.read}

        def put_back():
            for name, (attribute, entry) in before.items():
                for holder, key, value in ((package, name, attribute), (sys.modules, "angelo_agent." + name, entry)):
                    if value is self:
                        holder.pop(key, None)
                    else:
                        holder[key] = value
        self.addCleanup(put_back)
        planted = {}
        for name in self.read:
            planted[name] = package[name] = types.ModuleType("angelo_agent." + name)
            if in_sys_modules:
                sys.modules["angelo_agent." + name] = types.ModuleType("angelo_agent." + name)
            else:
                sys.modules.pop("angelo_agent." + name, None)
        return planted, {key: sys.modules.get(key) for key in list(sys.modules) if key.startswith("angelo_agent.")}

    def check(self, in_sys_modules):
        planted, entries = self.plant(in_sys_modules)
        with StandIn(NAMES) as modules:
            self.assertEqual(mismatches(modules), [], "every module read is the one sys.modules holds")
        self.assertEqual({name: vars(angelo_agent).get(name) for name in planted}, planted, "the package put back")
        self.assertEqual({key: sys.modules.get(key) for key in list(sys.modules) if key.startswith("angelo_agent.")},
                         entries, "sys.modules put back")

    def test_an_attribute_whose_module_left_sys_modules(self):
        """Failed on dd6a9660c59: an import read the leftover (in the CI of record, ui.console: a KeyError)."""
        self.check(in_sys_modules=False)

    def test_an_attribute_that_is_another_copy_than_sys_modules_holds(self):
        self.check(in_sys_modules=True)


if __name__ == "__main__":
    unittest.main()
