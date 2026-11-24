# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A GUI suite's ``state["observed"]`` record keeps one meaning per key.

Integrator, batch 8 (Linux, Xvfb): test_blender_agent_ux_director_gui stopped at card_reviewed with "'<' not supported
between instances of 'list' and 'int'". Its click() logs every click as observed["clicks"] (a list), and the Approve
retry counted on the same key (observed.get("clicks", 0) < 3). A key read as a number and written as a list is the
class this test refuses, in every suite. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def uses(tree):
    """{key: kinds} for string keys of ``state["observed"]`` and names bound to it: "list" (setdefault(k, [])),
    "number" (get(k, <number>), or a number assigned or compared)."""
    aliases = {"observed"}
    for node in ast.walk(tree):   # seen = state["observed"], and tuple unpacking of the same.
        if isinstance(node, ast.Assign):
            pairs = []
            for target in node.targets:
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    pairs += list(zip(target.elts, node.value.elts))
                else:
                    pairs.append((target, node.value))
            for target, value in pairs:
                if isinstance(target, ast.Name) and ast.unparse(value) == "state['observed']":
                    aliases.add(target.id)

    def observed(node):
        text = ast.unparse(node)
        return text == "state['observed']" or (isinstance(node, ast.Name) and node.id in aliases)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and observed(node.func.value) \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            key, default = node.args[0].value, node.args[1] if len(node.args) > 1 else None
            if node.func.attr == "setdefault" and isinstance(default, ast.List):
                found.setdefault(key, set()).add("list")
            if node.func.attr == "get" and isinstance(default, ast.Constant) and isinstance(default.value, (int, float)) \
                    and not isinstance(default.value, bool):
                found.setdefault(key, set()).add("number")
    return found


class ObservedKeyTests(unittest.TestCase):
    def test_no_suite_uses_one_observed_key_as_a_list_and_a_number(self):
        mixed = []
        for path in sorted(HERE.glob("test_blender_*.py")):
            for key, kinds in uses(ast.parse(path.read_text(encoding="utf-8"))).items():
                if kinds == {"list", "number"}:
                    mixed.append("%s: %s" % (path.name, key))
        self.assertEqual(mixed, [], "one meaning per observed key: a click log is never a counter")

    def test_the_check_sees_the_batch_8_form(self):
        tree = ast.parse('''
def click():
    state["observed"].setdefault("clicks", []).append(1)
def phase():
    call_id, seen = state["observed"]["call"], state["observed"]
    assert seen.get("clicks", 0) < 3
''')
        self.assertEqual(uses(tree), {"clicks": {"list", "number"}})


if __name__ == "__main__":
    unittest.main()
