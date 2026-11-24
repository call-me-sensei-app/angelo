# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat bar's Rig group (chat_destinations.RIG_GROUP; v210-features' ruling, 2026-11-18): it takes the hands tile's
place at the top level (no new tile), and holds hands and every rigging row the rigging modules register, each row an
operator that exists. Read from source (no Blender); the layout's row budget is test_native_chat_add_layout's.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import chat_destinations as cd  # noqa: E402


def menu_rows(module):
    """(feature id, operator) of every ``Feature("id", ..., operator="...")`` the module constructs."""
    rows = []
    for node in ast.walk(ast.parse((ADDON / (module + ".py")).read_text(encoding="utf-8"))):
        name = getattr(node.func, "attr", getattr(node.func, "id", "")) if isinstance(node, ast.Call) else ""
        if name == "Feature":
            operator = next((k.value.value for k in node.keywords if k.arg == "operator"), None)
            if node.args and isinstance(node.args[0], ast.Constant) and operator:
                rows.append((node.args[0].value, operator))
    return rows


def idnames(module):
    tree = ast.parse((ADDON / (module + ".py")).read_text(encoding="utf-8"))
    return {node.value.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
            and any(getattr(t, "id", "") == "bl_idname" for t in node.targets)}


class RigGroup(unittest.TestCase):
    def test_rig_is_where_hands_was_and_adds_no_tile(self):
        self.assertIn("rig", cd.TOP_LEVEL)
        self.assertNotIn("hands", cd.TOP_LEVEL)
        self.assertEqual(cd.PARENTS["hands"], cd.RIG_GROUP)
        self.assertIn(cd.RIG_GROUP, cd.GROUPS)

    def test_every_rigging_row_is_a_member_with_a_real_operator(self):
        rows = menu_rows("rigging_ops") + menu_rows("ik_controls")
        self.assertEqual(len(rows), 6)
        operators = idnames("rigging_ops") | idnames("ik_controls")
        for feature_id, operator in rows:
            with self.subTest(feature_id):
                self.assertEqual(cd.PARENTS.get(feature_id), cd.RIG_GROUP)
                self.assertIn(feature_id, cd.SHIPPED)
                self.assertIn(operator, operators)


if __name__ == "__main__":
    unittest.main()
