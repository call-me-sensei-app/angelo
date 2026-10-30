# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every subclass of ``Controller`` in the add-on overrides a method with the base's parameters, or more.

Evidence: ead28411418 gave ``Controller.mark_memory_stale`` a ``rescope`` keyword and ``document_identity._bind``
passed it; ``worker_mirror.MirrorController`` overrode the method with the old parameters, so a worker mirror chat's
rebind raised TypeError (and said its state could not be saved). Any caller of a base method may pass any of its
parameters to whichever chat it holds. Read from the sources (no bpy needed); ``*args``/``**kwargs`` accept anything.
"""

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def _classes():
    found = {}
    for path in sorted(ADDON.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = [base.id if isinstance(base, ast.Name) else getattr(base, "attr", "") for base in node.bases]
                found[node.name] = (path, bases, {item.name: item for item in node.body
                                                  if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))})
    return found


def _accepts(override, base):
    """The base's parameter names the override does not take (empty when it takes them all)."""
    arguments = override.args
    if arguments.vararg is not None and arguments.kwarg is not None:
        return []
    names = {argument.arg for argument in arguments.posonlyargs + arguments.args + arguments.kwonlyargs}
    wanted = base.args.posonlyargs + base.args.args + base.args.kwonlyargs
    missing = [argument.arg for argument in wanted if argument.arg not in names]
    return [] if arguments.kwarg is not None and all(
        argument.arg in names for argument in base.args.posonlyargs + base.args.args) else missing


class OverrideTests(unittest.TestCase):
    def test_every_controller_override_takes_the_bases_parameters(self):
        classes = _classes()
        self.assertIn("Controller", classes)
        base = classes["Controller"][2]
        subclasses, frontier = {}, {"Controller"}
        while frontier:   # Subclasses of subclasses too.
            frontier = {name for name, (_path, bases, _methods) in classes.items()
                        if name not in subclasses and name != "Controller" and set(bases) & (frontier | set(subclasses))}
            subclasses.update((name, classes[name]) for name in frontier)
        self.assertIn("MirrorController", subclasses)
        for name, (path, _bases, methods) in subclasses.items():
            for method, override in methods.items():
                if method in base and not method.startswith("__"):
                    with self.subTest(cls=name, method=method):
                        self.assertEqual(_accepts(override, base[method]), [],
                                         "%s.%s (%s) drops parameters of Controller.%s" % (name, method, path.name, method))


if __name__ == "__main__":
    unittest.main()
