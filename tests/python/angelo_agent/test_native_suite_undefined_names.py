# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No test file uses a name nothing defines (no Blender).

A GUI suite runs only in the built app, so a name a refactor removed fails only there, late: the dry cut's Mac GUI
round of 2.1.0 found ``test_blender_composer_gui`` raising NameError on every run, because f6adea907a7 (chat points
through ``qa_input.chat_point``) removed ``region, s = chat()[1], scale()`` while two calls still passed ``region``.
This reads every Python file of the test folder (suites, helpers, host tests) with the compiler's own scopes
(``symtable``): a name a function, class or the module reads is defined by that scope, an enclosing one, the module
(an assignment, import, definition or ``global`` assignment), or the builtins. A module with ``import *`` cannot be
judged and is named instead.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import builtins
import symtable
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KNOWN = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__loader__", "__package__",
                              "__builtins__", "__path__", "__class__"}


def undefined(source, name="<test>"):
    """[(name, scope, line)] of the names ``source`` reads that nothing defines; None with ``import *``."""
    if any(isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
           for node in ast.walk(ast.parse(source, name))):
        return None
    top = symtable.symtable(source, name, "exec")
    symbols = top.get_symbols()
    defined = {s.get_name() for s in symbols if s.is_assigned() or s.is_imported() or s.is_namespace()}
    reads = {(s.get_name(), "<module>", 1) for s in symbols if s.is_referenced() and s.get_name() not in defined}

    def walk(table):
        for s in table.get_symbols():
            if s.is_declared_global() and s.is_assigned():
                defined.add(s.get_name())   # ``global x; x = ...`` in a function defines a module name.
            elif s.is_referenced() and s.is_global() and not (s.is_assigned() or s.is_parameter()):
                reads.add((s.get_name(), table.get_name(), table.get_lineno()))
        for child in table.get_children():
            walk(child)

    for child in top.get_children():
        walk(child)
    return sorted(r for r in reads if r[0] not in defined and r[0] not in KNOWN)


class Planted(unittest.TestCase):
    def test_a_name_a_refactor_removed_is_found(self):
        source = ("def chat():\n    return None, 2\n\ndef at(x):\n    return chat()[1] + x\n\n"
                  "def steps():\n    left = chat_rect(region, 3)\n    return left\n")
        found = {name for name, _scope, _line in undefined(source)}
        self.assertEqual(found, {"chat_rect", "region"})

    def test_what_is_defined_somewhere_in_reach_is_not(self):
        source = ("import os\nfrom pathlib import Path as P\nX = 1\n\ndef late():\n    global LATER\n    LATER = 2\n\n"
                  "def outer():\n    y = 3\n    def inner(z):\n        return [w for w in (y, z, X, LATER, os, P, len)]\n"
                  "    return inner\n\nclass C:\n    def m(self):\n        return super().__init__\n")
        self.assertEqual(undefined(source), [])

    def test_a_star_import_cannot_be_judged(self):
        self.assertIsNone(undefined("from os import *\nprint(path)\n"))


class TestFolder(unittest.TestCase):
    def test_no_test_file_reads_an_undefined_name(self):
        problems, unjudged = {}, []
        files = sorted(HERE.rglob("*.py"))
        self.assertGreater(len(files), 100)
        for path in files:
            if "__pycache__" in path.parts:
                continue
            found = undefined(path.read_text(encoding="utf-8"), str(path))
            if found is None:
                unjudged.append(path.name)
            elif found:
                problems[str(path.relative_to(HERE))] = found
        self.assertEqual(problems, {}, "names read but never defined (a NameError when that code runs)")
        self.assertEqual(unjudged, [], "a star import hides which names a module defines")


if __name__ == "__main__":
    unittest.main()
