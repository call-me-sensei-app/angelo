# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every menu Angelo registers is opened by something a person can reach. No Blender.

The Approval Policy menu (``ANGELO_MT_approval``: when the chat asks before changes, and Trust scripts for this chat)
was registered and drawn by nothing: no button, row or key opened it, so a person could not trust a chat's scripts
(pipeline-gates, 2026-11-16; the consent suite opened it with ``wm.call_menu`` itself). The maintainer's rule: every
feature is reachable from the chat bar's + menu, through its one registry (``chat_destinations``). The menu is now a
row of Apps and settings; the class check finds any other menu nothing opens.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
C_SOURCES = ROOT / "source/blender"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_destinations as cd  # noqa: E402


def menus():
    """{menu idname: (module, class name)} of every ``Menu`` subclass in the add-on (its ``bl_idname``, else its class
    name, as Blender registers it)."""
    found = {}
    for path in ADDON.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and any(ast.unparse(base) in {"Menu", "bpy.types.Menu"}
                                                       for base in node.bases):
                idname = next((statement.value.value for statement in node.body if isinstance(statement, ast.Assign)
                               and any(getattr(t, "id", "") == "bl_idname" for t in statement.targets)
                               and isinstance(statement.value, ast.Constant)), node.name)
                found[idname] = (path.name, node.name)
    return found


def literals_outside(path, idname, cls):
    """Whether ``path`` names the menu anywhere but in ``cls``'s own body: its idname as a string, or
    ``cls.bl_idname`` / ``cls.__name__`` (a layout's menu, a call_menu)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    own = {id(inner) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == cls
           for inner in ast.walk(node)}
    return any(id(node) not in own and (
        (isinstance(node, ast.Constant) and node.value == idname)
        or (isinstance(node, ast.Attribute) and node.attr in {"bl_idname", "__name__"}
            and ast.unparse(node.value) == cls)) for node in ast.walk(tree))


_c_names = None


def opened_in_c(idname):
    """Whether Blender's C UI code names the menu (``WM_menutype_find`` / ``menu_contents``: + Add Workspace)."""
    global _c_names
    if _c_names is None:
        _c_names = set()
        for folder in ("editors", "windowmanager"):
            for path in (C_SOURCES / folder).rglob("*.cc"):
                text = path.read_text(encoding="utf-8", errors="replace")
                if "ANGELO_MT_" in text:
                    _c_names.update(part.split('"', 1)[0] for part in text.split('"ANGELO_MT_')[1:])
    return idname.startswith("ANGELO_MT_") and idname[len("ANGELO_MT_"):] in _c_names


class MenusReachableTests(unittest.TestCase):
    def test_every_menu_the_add_on_registers_is_opened_by_something(self):
        """Its idname is opened somewhere other than its own class: a layout's menu, a call_menu, Blender's C (which
        draws + Add Workspace's Angelo section), or it replaces one of Blender's own menus (its idname is Blender's)."""
        unopened = []
        for idname, (module, cls) in sorted(menus().items()):
            if not idname.startswith("ANGELO_"):   # Replaces Blender's own menu (WM_MT_splash): Blender opens it.
                continue
            if any(literals_outside(path, idname, cls) for path in ADDON.glob("*.py")) or opened_in_c(idname):
                continue
            unopened.append("%s (%s)" % (idname, module))
        self.assertEqual(unopened, [], "menus nothing opens: give each a way in from the + menu (chat_destinations)")

    def test_the_approval_policy_is_a_row_of_apps_and_settings(self):
        rows = cd._apps_settings_rows((), "", platform="linux", states={app: ("", None) for app in cd.CONNECTED_APPS})
        row = next((row for row in rows if row["value"] == "approval_policy"), None)
        self.assertIsNotNone(row, "Apps and settings offers the approval policy")
        self.assertEqual((row["action"], row["operator"], row["label"]),
                         ("popup_add_open", "angelo.approval_policy", cd.APPROVAL_POLICY))
        order = [row["value"] for row in rows]
        self.assertLess(order.index("ai_providers"), order.index("approval_policy"))
        self.assertLess(order.index("approval_policy"), order.index("snapshot_settings"))

    def test_its_operator_opens_the_approval_policy_menu_and_grants_nothing_itself(self):
        tree = ast.parse((ADDON / "trusted_scripts_ui.py").read_text(encoding="utf-8"))
        operator = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                        and node.name == "ANGELO_OT_approval_policy")
        source = ast.unparse(operator)
        self.assertIn("bl_idname = 'angelo.approval_policy'", source)
        self.assertIn("bpy.ops.wm.call_menu(name='ANGELO_MT_approval')", source)
        guarded = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and ast.unparse(node.func) == "grant_guard.guard"]
        self.assertTrue(guarded and all("ANGELO_OT_approval_policy" not in call for call in guarded),
                        "Opening a menu is no grant (grant_operators.rst); its items are")
        self.assertIn("ANGELO_OT_trusted_scripts", guarded[0])


if __name__ == "__main__":
    unittest.main()
