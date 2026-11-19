# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything the approval path calls in Angelo's window exists in a worker Blender too (``headless_main._register``).

Break-it review H1 (2026-10-23): ``angelo.apply_approved_script`` was registered in the window but not in the
background child (2.0.0's headless MCP child, which shared this start-up with workers), so every approved script
failed in a real child while the in-process harness passed. This audit reads the operators the approval path can call
(``angelo.approve`` and the apply operators) and the operators the start-up registers, and requires each one to be
registered there. Dependency-free (source-level, as bpy is not available).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))


def tree(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


def _angelo_ops(node):
    """``bpy.ops.angelo``, bare or through a guard's wrapper of it (``grant_guard.clicked(bpy.ops.angelo)``)."""
    if isinstance(node, ast.Call) and len(node.args) == 1:
        node = node.args[0]
    return (isinstance(node, ast.Attribute) and node.attr == "angelo" and isinstance(node.value, ast.Attribute)
            and node.value.attr == "ops")


def operator_calls(node):
    """``angelo.<name>`` for every ``bpy.ops.angelo.<name>`` attribute below ``node``, wrapped by a guard or not."""
    return {"angelo." + child.attr for child in ast.walk(node)
            if isinstance(child, ast.Attribute) and _angelo_ops(child.value)}


def method(module, cls, name):
    klass = next(n for n in ast.walk(module) if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)


def idnames(node):
    """Every ``bl_idname = "angelo...."`` assignment below ``node``."""
    return {child.value.value for child in ast.walk(node) if isinstance(child, ast.Assign)
            and any(getattr(target, "id", "") == "bl_idname" for target in child.targets)
            and isinstance(child.value, ast.Constant)}


class HeadlessRegistrationTests(unittest.TestCase):
    def reached(self):
        """The operators the approval path can call: approve and the apply operators (by name, as script_errors
        chooses them), the commit and preview operators."""
        ui = tree("ui.py")
        from angelo_agent import script_errors
        reached = operator_calls(method(ui, "ANGELO_OT_approve", "execute"))
        reached |= operator_calls(method(ui, "ANGELO_OT_apply_approved_tool", "execute"))
        reached |= {"angelo." + script_errors.APPLY, "angelo." + script_errors.APPLY_SCRIPT}
        return reached

    def registered(self):
        """What headless_main._register registers: the preferences, approve, the window's apply operators and the
        workspace operators (agent_workspace_ui.workspace_operators)."""
        from angelo_agent import headless_main
        registered = {"angelo.approve", *headless_main.APPLY_OPERATORS}
        workspace = next(n for n in ast.walk(tree("agent_workspace_ui.py"))
                         if isinstance(n, ast.FunctionDef) and n.name == "workspace_operators")
        registered |= idnames(workspace)
        source = (ADDON / "headless_main.py").read_text(encoding="utf-8")
        self.assertIn("(ui.ANGELO_Preferences, ui.ANGELO_OT_approve) + apply_operators(ui)", source)
        self.assertIn("agent_workspace_ui.register()", source)
        return registered

    def test_every_operator_the_approval_path_reaches_is_registered_in_a_worker(self):
        guarded = {"angelo.review_tool"}   # A worker's scripts are reviewed in the lead's window (worker_consent).
        missing = self.reached() - self.registered() - guarded
        self.assertEqual(missing, set(), "registered in the window but not in a worker Blender")


if __name__ == "__main__":
    unittest.main()
