# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every call from one add-on module into a sibling names a function that exists there, with arguments it accepts.

A merge that resolves a conflicted file by taking one side whole can drop a function the other side's callers still
use, or change a signature under them, and nothing fails until that line runs. The 2.1.0 stack tip lost this way
(merge f7703fc3ff4): ``crash_resume.opened``/``closed`` vanished while controller.py and message_end.py still called
them, and ``composer_mentions.for_request`` took a third argument that console.py never passed.

Checked statically, so it covers lines no other test reaches:

- ``module.name(...)`` and ``from .module import name``, where ``module`` is a sibling imported with ``from . import``:
  ``name`` must be bound at the sibling's top level (a def, class, assignment or import, also under a top-level
  ``if``/``try``, or declared ``global`` in a function there).
- For a plain undecorated ``def`` without ``*args``/``**kwargs``, called without ``*``/``**`` unpacking: the call binds,
  so positional count, keyword names and required parameters all fit.

A sibling whose names are dynamic is skipped: a module-level ``__getattr__``, a star import, or a shim that replaces
itself (``sys.modules[__name__] = ...``, as live_windows and change_clock do for their scripts/modules twins). So is an
alias the importing file also binds some other way (a parameter or assignment of that name may not be the module).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"


def _top_level(tree):
    """Names bound at module level, and the plain functions among them ({name: FunctionDef or None})."""
    names, functions = set(), {}

    def statements(body):
        for node in body:
            if isinstance(node, (ast.If, ast.Try, ast.With)):
                yield from statements(node.body)
                yield from statements(getattr(node, "orelse", []))
                yield from statements(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    yield from statements(handler.body)
            else:
                yield node

    for node in statements(tree.body):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            names.add("__getattr__")   # Star import: dynamic.
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Subscript) and "sys.modules" in ast.unparse(target.value)
                                                for target in node.targets):
            names.add("__getattr__")   # Replaces itself in sys.modules: its names are another module's.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            plain = isinstance(node, ast.FunctionDef) and not node.decorator_list
            functions[node.name] = node if plain and node.name not in functions else None
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for leaf in ast.walk(target):
                    if isinstance(leaf, ast.Name):
                        names.add(leaf.id)
                        functions[leaf.id] = None   # Rebound: no longer the def's signature.
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                names.add(bound)
                functions[bound] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            names.update(node.names)
            functions.update({name: None for name in node.names})
    return names, functions


def _bound_otherwise(tree):
    """Names the file binds other than by ``from . import``: parameters, assignment targets, defs and other imports."""
    sibling = {id(alias) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 1
               and node.module is None for alias in node.names}
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.alias) and id(node) not in sibling:
            bound.add(node.asname or node.name.split(".")[0])
    return bound


def _misfit(function, call):
    """Why ``call`` cannot bind to ``function``'s parameters, or None."""
    args = function.args
    if args.vararg or args.kwarg:
        return None
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords):
        return None
    positional = [arg.arg for arg in args.posonlyargs + args.args]
    required = positional[:len(positional) - len(args.defaults)]
    keyword_only = {arg.arg: default is None for arg, default in zip(args.kwonlyargs, args.kw_defaults)}
    if len(call.args) > len(positional):
        return "%d positional, takes at most %d" % (len(call.args), len(positional))
    given = set(positional[:len(call.args)])
    posonly = {arg.arg for arg in args.posonlyargs}
    for keyword in call.keywords:
        if keyword.arg in given:
            return "%s given twice" % keyword.arg
        if keyword.arg in posonly or (keyword.arg not in positional and keyword.arg not in keyword_only):
            return "no parameter %s" % keyword.arg
        given.add(keyword.arg)
    missing = [name for name in required if name not in given]
    missing += [name for name, needed in keyword_only.items() if needed and name not in given]
    return "missing %s" % ", ".join(missing) if missing else None


def misses(addon=ADDON):
    trees = {path.stem: ast.parse(path.read_text(encoding="utf-8"), str(path)) for path in sorted(addon.glob("*.py"))}
    tops = {name: _top_level(tree) for name, tree in trees.items()}
    found = []
    for name, tree in trees.items():
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module is None:
                    aliases.update({alias.asname or alias.name: alias.name for alias in node.names})
                elif node.module in tops and "__getattr__" not in tops[node.module][0]:
                    for alias in node.names:
                        if alias.name != "*" and alias.name not in tops[node.module][0]:
                            found.append("%s.py:%d: %s has no %s" % (name, node.lineno, node.module, alias.name))
        otherwise = _bound_otherwise(tree)
        aliases = {alias: module for alias, module in aliases.items()
                   if module in tops and "__getattr__" not in tops[module][0] and alias not in otherwise}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in aliases):
                continue
            module, attr = aliases[node.func.value.id], node.func.attr
            names, functions = tops[module]
            if attr not in names:
                found.append("%s.py:%d: %s has no %s" % (name, node.lineno, module, attr))
            elif functions.get(attr) is not None:
                why = _misfit(functions[attr], node)
                if why:
                    found.append("%s.py:%d: %s.%s: %s" % (name, node.lineno, module, attr, why))
    return found


class ModuleCallsTests(unittest.TestCase):
    maxDiff = None   # Every miss is listed, never a truncated diff.

    def test_calls_between_add_on_modules_resolve(self):
        self.assertEqual([], misses())

    def test_it_finds_a_missing_function_and_a_call_that_does_not_bind(self):
        """The check itself, on a two-module stand-in package shaped like the merge that motivated it."""
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "marks.py").write_text("def mark():\n    return {}\n\n\ndef picked(chat, message, scene):\n    return None\n")
            (root / "caller.py").write_text(
                "from . import marks\n\n\ndef start(chat, text):\n"
                "    marks.opened(chat)\n    marks.picked(chat, text)\n    marks.picked(chat, text, None, extra=1)\n"
                "    marks.picked(chat, text, scene=None)\n    return marks.mark()\n")
            (root / "importer.py").write_text("from .marks import closed, mark\n")
            self.assertEqual(sorted(misses(root)), sorted([
                "caller.py:5: marks has no opened",
                "caller.py:6: marks.picked: missing scene",
                "caller.py:7: marks.picked: no parameter extra",
                "importer.py:1: marks has no closed",
            ]))

    def test_a_dynamic_module_and_a_shadowed_alias_are_not_judged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "lazy.py").write_text("def __getattr__(name):\n    return name\n")
            (root / "shim.py").write_text("import sys\nimport json as _module\nsys.modules[__name__] = _module\n")
            (root / "starred.py").write_text("from json import *\n")
            (root / "marks.py").write_text("def mark():\n    return {}\n")
            (root / "caller.py").write_text(
                "from . import lazy, marks, shim, starred\n\n\ndef start(marks):\n    lazy.anything()\n    shim.dumps({})\n"
                "    starred.loads('1')\n    return marks.other()\n")
            self.assertEqual(misses(root), [])


if __name__ == "__main__":
    unittest.main()
