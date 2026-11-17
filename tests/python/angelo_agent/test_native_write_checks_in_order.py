# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A write check asks C about a value the function already holds (doc/angelo/script_confinement.rst, "Angelo's own
I/O").

``write_sinks`` counts a sink as checked when its function calls ``require_writable``/``may_write`` on a name the sink
depends on, wherever the call stands. Found 2026-11-16 (p10c-3-library, guard piece 10) on 0ebc1e0239c: three checks
named a local bound only later in the loop (``target`` in ``library_agent._previews``, ``blender_asset_previews._copy``
and ``crash_flow.CrashFlow.save_to``), so each raised UnboundLocalError at every call: the agent's library thumbnails,
the Asset Browser previews and Save Report all failed in the app. And one asked C before its own "nothing to remove"
return (``fab_browser_download_file.Landing.remove``): with the native guard, ``require_writable(None)`` raises where the
function meant to return. Host tests never saw either: without the native guard the check answers yes at once, and a
cleanup path ran only in the app.

Every function of the add-on: no check reads a local name before it is bound, and none stands before the function's
own ``if <the same value> is None: return``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
GUARDS = {"require_writable", "may_write"}


def _own_nodes(function):
    """Nodes of ``function``'s own body, not of functions or classes defined in it."""
    pending = list(function.body)
    while pending:
        node = pending.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                pending.append(child)


def _first_bound(function):
    """{name: first line it is bound in ``function``'s own scope}."""
    found = {}

    def bind(name, line):
        found[name] = min(found.get(name, line), line)
    for node in _own_nodes(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bind(node.id, node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bind((alias.asname or alias.name).split(".")[0], node.lineno)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(node.name, node.lineno)
    for node in ast.iter_child_nodes(function):
        for inner in ast.walk(node):
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and inner is not function:
                bind(inner.name, inner.lineno)
    return found


def _guard_calls(function):
    for node in _own_nodes(function):
        if isinstance(node, ast.Call) and node.args:
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name in GUARDS:
                yield node


def problems(tree, label):
    found = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = function.args
        given = {a.arg for a in arguments.posonlyargs + arguments.args + arguments.kwonlyargs}
        given |= {a.arg for a in (arguments.vararg, arguments.kwarg) if a is not None}
        declared = {name for node in _own_nodes(function) if isinstance(node, (ast.Global, ast.Nonlocal))
                    for name in node.names}
        bound = _first_bound(function)
        nones = [(node.lineno, ast.unparse(node.test.left)) for node in _own_nodes(function)
                 if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and len(node.test.ops) == 1
                 and isinstance(node.test.ops[0], ast.Is) and isinstance(node.test.comparators[0], ast.Constant)
                 and node.test.comparators[0].value is None and node.body
                 and isinstance(node.body[0], ast.Return)]
        for call in _guard_calls(function):
            checked = call.args[0]
            for name in {n.id for n in ast.walk(checked) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}:
                if name not in given and name not in declared and bound.get(name, 0) > call.lineno:
                    found.append("%s:%d %s: checks %s before line %d binds it"
                                 % (label, call.lineno, function.name, name, bound[name]))
            text = ast.unparse(checked)
            for line, tested in nones:
                if tested == text and line > call.lineno:
                    found.append("%s:%d %s: checks %s before its own `if %s is None: return` (line %d)"
                                 % (label, call.lineno, function.name, text, text, line))
    return found


class WriteChecksInOrderTests(unittest.TestCase):
    def test_every_check_asks_about_a_value_the_function_holds(self):
        found = [row for path in sorted(ADDON.glob("*.py"))
                 for row in problems(ast.parse(path.read_text(encoding="utf-8")), path.name)]
        self.assertEqual(found, [], "check the folder the value is made in, or move the check below where it is "
                                    "bound or below the function's own None return")

    def test_planted_cases_are_found(self):
        source = ("import owned_paths\n"
                  "def late(folder, items):\n"
                  "    owned_paths.require_writable(target)\n"
                  "    for item in items:\n"
                  "        target = folder / item\n"
                  "        target.write_bytes(b'')\n"
                  "def early_none(self):\n"
                  "    owned_paths.require_writable(self.path)\n"
                  "    if self.path is None: return\n"
                  "def fine(self, folder):\n"
                  "    if self.path is None: return\n"
                  "    owned_paths.require_writable(folder)\n"
                  "    target = folder / 'x'\n"
                  "    owned_paths.may_write(target)\n")
        found = problems(ast.parse(source), "planted.py")
        self.assertEqual([row.split(" ", 2)[1] for row in found], ["late:", "early_none:"], found)


if __name__ == "__main__":
    unittest.main()
