# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything the approval path calls in Angelo's window exists in the headless child too, or is never reached there.

Break-it review H1 (2026-10-23): ``angelo.apply_approved_script`` was registered in the window but not in the headless
child, so every approved script file failed in a real child while the in-process harness passed. This audit reads
the operators the approval path can call (``angelo.approve`` and the apply operators, and the headless loop itself)
and the operators the child registers (``headless_main._register``), and requires each one to be registered there or
guarded by a headless refusal before it can be reached. Dependency-free (source-level, as bpy is not available); the
real child runs in test_blender_headless_mcp.py.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))


def tree(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


def operator_calls(node):
    """``angelo.<name>`` for every ``bpy.ops.angelo.<name>`` attribute below ``node``."""
    found = set()
    for child in ast.walk(node):
        if (isinstance(child, ast.Attribute) and isinstance(child.value, ast.Attribute) and child.value.attr == "angelo"
                and isinstance(child.value.value, ast.Attribute) and child.value.value.attr == "ops"):
            found.add("angelo." + child.attr)
    return found


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
        chooses them), the commit and preview operators, and anything the headless loop calls directly."""
        ui = tree("ui.py")
        from angelo_agent import script_errors
        reached = operator_calls(method(ui, "ANGELO_OT_approve", "execute"))
        reached |= operator_calls(method(ui, "ANGELO_OT_apply_approved_tool", "execute"))
        reached |= {"angelo." + script_errors.APPLY, "angelo." + script_errors.APPLY_SCRIPT}
        reached |= operator_calls(method(tree("headless_host.py"), "HeadlessHost", "run"))
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

    def test_every_operator_the_approval_path_reaches_is_registered_in_the_child_or_guarded(self):
        guarded = {"angelo.review_tool"}   # Only for a script its consent no longer covers: refuse_unauthorized.
        missing = self.reached() - self.registered() - guarded
        self.assertEqual(missing, set(), "registered in the window but not in the headless child")

    def test_the_review_path_is_refused_before_approve_runs_in_the_child(self):
        run = method(tree("headless_host.py"), "HeadlessHost", "run")
        apply = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == "apply_mutation")
        first = apply.body[0]
        self.assertIsInstance(first, ast.If)
        self.assertIn("refuse_unauthorized", ast.unparse(first.test))
        self.assertIn("angelo.review_tool", operator_calls(method(tree("ui.py"), "ANGELO_OT_approve", "execute")),
                      "if approve stops calling review_tool, drop it from the guarded set")

    def test_refuse_unauthorized_finishes_a_script_its_consent_no_longer_covers(self):
        from angelo_agent.headless_host import HeadlessHost
        finished = []
        host = HeadlessHost.__new__(HeadlessHost)
        host.source = Path("/p/scene.blend")
        controller = SimpleNamespace(script_authorized=lambda call: call.ok, script_file_refusal=lambda call: "policy",
                                     finish_tool=lambda call, result: finished.append(result))
        script = SimpleNamespace(path="/w/build.py")
        self.assertFalse(host.refuse_unauthorized(controller, SimpleNamespace(name="angelo_create_cube", ok=False)))
        self.assertFalse(host.refuse_unauthorized(controller, SimpleNamespace(name="angelo_execute_python", ok=True,
                                                                              script_file=script)))
        self.assertEqual(finished, [])
        self.assertTrue(host.refuse_unauthorized(controller, SimpleNamespace(name="angelo_execute_python", ok=False,
                                                                             script_file=script)))
        self.assertEqual(finished[0]["error_code"], "NEEDS_GUI")
        self.assertIn("AllowAutoApproval", finished[0]["error"])
        self.assertFalse(finished[0]["scene_changed"])


if __name__ == "__main__":
    unittest.main()
