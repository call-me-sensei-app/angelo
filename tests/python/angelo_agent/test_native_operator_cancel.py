# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Angelo operator's ``cancel`` returns nothing. No Blender.

Blender calls an operator's ``cancel`` when it removes a running modal's handler (its window closed, a file loaded)
and requires it to return None: anything else is logged as "RuntimeError: expected class …, function cancel to return
None", which the suite runner counts as a failure. 36f0e938310 aliased the AI Providers page input's ``cancel`` to its
``_finish``, which returns ``{'FINISHED', 'PASS_THROUGH'}`` for ``modal``: every closed Preferences window logged it
(pipeline-gates, Linux GUI; test_blender_generation_connect). Here, every operator class in the add-on: a ``cancel``
method returns no value, and a ``cancel`` bound to another name is a method of the class that returns none either.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import unittest

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def returns_a_value(function):
    """Whether ``function`` returns something other than None in its own body (not in a nested function)."""
    stack = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return) and node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None):
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def operators():
    """(module, class) of every operator class: an ``Operator`` base, or a ``bl_idname`` with a dot in it."""
    for path in sorted(ADDON.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(base) for base in node.bases}
            idname = next((statement.value.value for statement in node.body if isinstance(statement, ast.Assign)
                           and any(getattr(t, "id", "") == "bl_idname" for t in statement.targets)
                           and isinstance(statement.value, ast.Constant)), "")
            if bases & {"Operator", "bpy.types.Operator"} or "." in str(idname):
                yield path.name, node


def cancel_problems(cls):
    """Why ``cls``'s ``cancel`` would return a value, or [] (also when it has none)."""
    methods = {node.name: node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    problems = []
    if "cancel" in methods and returns_a_value(methods["cancel"]):
        problems.append("cancel returns a value")
    for statement in cls.body:
        if isinstance(statement, ast.Assign) and any(getattr(t, "id", "") == "cancel" for t in statement.targets):
            target = statement.value
            if isinstance(target, ast.Name) and target.id in methods:
                if returns_a_value(methods[target.id]):
                    problems.append("cancel = %s, which returns a value" % target.id)
            else:
                problems.append("cancel is bound to %s: write a method that returns nothing" % ast.unparse(target))
    return problems


class OperatorCancelTests(unittest.TestCase):
    def test_no_operator_cancel_returns_a_value(self):
        found = {"%s:%s" % (module, cls.name): problems for module, cls in operators()
                 if (problems := cancel_problems(cls))}
        self.assertEqual(found, {}, "Blender requires an operator's cancel to return None")

    def test_the_check_sees_operators_and_their_cancels(self):
        classes = list(operators())
        self.assertGreater(len(classes), 100)
        with_cancel = [cls.name for _, cls in classes if any(
            (isinstance(node, ast.FunctionDef) and node.name == "cancel")
            or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "cancel" for t in node.targets))
            for node in cls.body)]
        self.assertIn("ANGELO_OT_providers_page_input", with_cancel)

    def test_an_alias_to_a_method_that_returns_is_caught(self):
        source = ("class ANGELO_OT_x(Operator):\n"
                  "    def _finish(self):\n        return {'FINISHED'}\n"
                  "    cancel = _finish\n")
        cls = ast.parse(source).body[0]
        self.assertEqual(cancel_problems(cls), ["cancel = _finish, which returns a value"])


if __name__ == "__main__":
    unittest.main()
