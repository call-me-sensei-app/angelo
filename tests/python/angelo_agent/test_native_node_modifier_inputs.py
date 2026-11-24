# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The add-on sets a Geometry Nodes modifier's inputs where Blender 5.3 keeps them. No Blender.

Blender 5.3 moved a Nodes modifier's inputs from ID properties of the modifier (``modifier["Socket_2"] = 0.1``) to
generated property groups (``modifier.properties.inputs.Socket_2.value``; Blender's own Quick Effects do this). A
modifier no longer takes ID properties at all: the old write raises ``TypeError: bpy_struct[key] = val: id properties
not supported for this type``. Found by the 2.1.0 splat-to-mesh suite (2026-11-15), whose surface pass failed on every
case.

Read from the add-on's sources: no subscript assignment on a name bound to ``<...>.modifiers.new(..., 'NODES')`` in the
same function. ``test_blender_splat_mesh`` proves the write in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def nodes_modifier_call(node):
    return isinstance(node, ast.Call) and ast.unparse(node.func).endswith("modifiers.new") and any(
        isinstance(arg, ast.Constant) and arg.value == 'NODES' for arg in list(node.args) + [k.value for k in node.keywords])


def old_writes():
    found = []
    for path in sorted(ADDON.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {target.id for node in ast.walk(function) if isinstance(node, ast.Assign)
                     and nodes_modifier_call(node.value) for target in node.targets if isinstance(target, ast.Name)}
            for node in ast.walk(function):
                if isinstance(node, (ast.Assign, ast.AugAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Subscript) and getattr(target.value, "id", None) in names:
                            found.append("%s:%d %s" % (path.relative_to(ADDON).as_posix(), node.lineno,
                                                       ast.unparse(target)))
    return found


class NodeModifierInputs(unittest.TestCase):
    def test_scan_sees_nodes_modifiers(self):
        self.assertTrue(any(nodes_modifier_call(node) for path in ADDON.rglob("*.py")
                            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))),
                        "no modifiers.new(..., 'NODES') in the add-on: the scan reads the wrong thing")

    def test_inputs_are_set_through_properties_inputs(self):
        self.assertFalse(old_writes(), "Blender 5.3 refuses ID properties on a modifier; set "
                                       "modifier.properties.inputs.<identifier>.value instead: %s" % old_writes())


if __name__ == "__main__":
    unittest.main()
