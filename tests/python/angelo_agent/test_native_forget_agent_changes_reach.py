# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Forget Agent Changes in a Folder is reached from the chat bar's + menu and the AI Providers canvas page, not only
from the add-on's preferences.

2.0.0 drew it only in Add-ons ▸ Angelo Agent Chat and the page-less AI Providers fallback. The interactive canvas
page had no such row, and the + menu (Angelo's main navigation: every feature is reached from its one registry,
``chat_destinations``) had none either. The coordinator's ruling of 2026-11-17 put both in 2.1.0. Both new ways in run
the same grant operator in the person's click: the + menu through ``grant_guard.clicked``, the canvas page in its
press's ticket (``providers_page_host.act``). Each row's line is what the click does, the operator's own description
(one string, translated once), never the protection the action lifts: the review's B-SHOULD-1 (2026-11-18) read the
first version's line ("Code agent scripts wrote never loads as Angelo's own…") as what the click gives. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
LOCALE = ROOT / "locale/angelo"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_destinations as cd, providers_page_model as model  # noqa: E402


def _literal(nodes, name):
    """The string a module or class body assigns to ``name``, through ``n_("...")`` too."""
    for node in nodes:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == name for target in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "n_":
                value = value.args[0]
            if isinstance(value, ast.Constant):
                return value.value
    raise AssertionError("no literal %s" % name)


def operator_facts():
    """``(bl_idname, bl_label, bl_description, module source)`` of the operator, read from its module (it imports
    bpy)."""
    source = (ADDON / "agent_changes_forget.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "ANGELO_OT_forget_agent_changes")
    return _literal(cls.body, "bl_idname"), _literal(cls.body, "bl_label"), _literal(cls.body, "bl_description"), source


def _properties(**names):
    return {name: SimpleNamespace(name=name.replace("_", " ").title(), description="What %s does" % name)
            for name in names}


def permissions():
    prefs = SimpleNamespace(helper_standing_consent=False, always_allow_scripts=False,
                            bl_rna=SimpleNamespace(properties=_properties(helper_standing_consent=1,
                                                                          always_allow_scripts=1)))
    policy = SimpleNamespace(allow_auto_approval=True, require_ask_for_edits=False)
    return model._permissions(prefs, policy)["rows"]


class ChatBarRowTests(unittest.TestCase):
    def test_apps_and_settings_offers_it_after_the_approval_policy(self):
        idname, label, does, _source = operator_facts()
        rows = cd._apps_settings_rows((), "", platform="linux", states={app: ("", None) for app in cd.CONNECTED_APPS})
        row = next((row for row in rows if row["value"] == "forget_agent_changes"), None)
        self.assertIsNotNone(row, "Apps and settings offers Forget Agent Changes")
        self.assertEqual((row["action"], row["operator"], row["label"], row["subtitle"], row["properties"]),
                         ("popup_add_open", idname, label, does, ()))
        order = [row["value"] for row in rows]
        self.assertLess(order.index("approval_policy"), order.index("forget_agent_changes"))
        self.assertLess(order.index("forget_agent_changes"), order.index("snapshot_settings"))

    def test_the_rows_click_is_the_persons_grant(self):
        """A row's angelo operator runs through ``grant_guard.clicked`` (chat_add_nav), invoked: the folder browser
        opens and the person's pick is the consent (``grant_browse``)."""
        source = (ADDON / "chat_add_nav.py").read_text(encoding="utf-8")
        self.assertIn("grant_guard.clicked() if group == \"angelo\"", source)
        self.assertIn("getattr(ops, name)('INVOKE_DEFAULT', True, **dict(properties))", source)


class CanvasRowTests(unittest.TestCase):
    def test_agent_permissions_has_it_right_after_always_allow_scripts(self):
        _idname, label, does, _source = operator_facts()
        rows = permissions()
        found = [index for index, row in enumerate(rows)
                 if any(button[1] == "forget_agent_changes" for button in row.get("buttons", ()))]
        self.assertEqual(len(found), 1, rows)
        scripts = next(index for index, row in enumerate(rows) if row.get("value") == "always_allow_scripts")
        row = rows[found[0]]
        self.assertEqual(found[0], scripts + 1)
        self.assertEqual((row["name"], row["lines"], row["buttons"]),
                         (label, [does], [("Browse", "forget_agent_changes", "", "ghost")]))

    def test_its_button_invokes_the_operator(self):
        idname, _label, _explained, _source = operator_facts()
        tree = ast.parse((ADDON / "providers_page_actions.py").read_text(encoding="utf-8"))
        table = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                     and any(getattr(target, "id", "") == "SIMPLE_OPERATORS" for target in node.targets))
        entries = {key.value: ast.literal_eval(value) for key, value in zip(table.keys, table.values)
                   if isinstance(key, ast.Constant)}
        self.assertEqual(entries.get("forget_agent_changes"), (idname.split(".", 1)[1], "INVOKE_DEFAULT", None))
        host = (ADDON / "providers_page_host.py").read_text(encoding="utf-8")
        self.assertIn('grant_guard.ticketed("page.", action, value, _act', host)   # In the press's grant scope.

    def test_the_operator_stays_a_grant(self):
        source = operator_facts()[3]
        self.assertIn("grant_guard.guard(ANGELO_OT_forget_agent_changes)", source)
        self.assertIn("forget = grant_guard.entry(forget)", source)


class TranslationTests(unittest.TestCase):
    def test_every_catalogue_translates_the_rows_line(self):
        """The rows' line is the operator's description, already in every catalogue in the default context."""
        does = operator_facts()[2]
        catalogues = sorted((LOCALE / "po").glob("*.po")) + [LOCALE / "src/ar.po"]
        for path in catalogues + [LOCALE / "angelo.pot"]:
            text = path.read_text(encoding="utf-8")
            found = re.findall(r'(?m)^(msgctxt "[^"]*"\n)?msgid "%s"\nmsgstr "(.*)"$' % re.escape(does), text)
            with self.subTest(catalogue=path.name):
                self.assertEqual([context for context, _value in found], [""], path)
                if path.suffix == ".po":
                    self.assertTrue(found[0][1], path)

    def test_every_catalogue_has_the_menu_label_in_the_default_context(self):
        """The + menu translates its rows in the default context; the label's translation is the operator's own."""
        label = operator_facts()[1]
        escaped = re.escape(label)
        catalogues = sorted((LOCALE / "po").glob("*.po")) + [LOCALE / "src/ar.po"]
        self.assertGreaterEqual(len(catalogues), 21)
        for path in catalogues + [LOCALE / "angelo.pot"]:
            text = path.read_text(encoding="utf-8")
            plain = re.findall(r'(?m)^(msgctxt "[^"]*"\n)?msgid "%s"\nmsgstr "(.*)"$' % escaped, text)
            default = [value for context, value in plain if not context]
            operator = [value for context, value in plain if context == 'msgctxt "Operator"\n']
            with self.subTest(catalogue=path.name):
                self.assertEqual(len(default), 1, path)
                self.assertEqual(default, operator, path)
                if path.suffix == ".po":
                    self.assertTrue(default[0], path)


if __name__ == "__main__":
    unittest.main()
