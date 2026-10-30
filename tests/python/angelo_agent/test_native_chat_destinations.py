# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu is one registry (``chat_destinations``), Angelo's main navigation (1.0.5). No Blender.

Covered: every feature Angelo ships (``TOP_LEVEL``) is registered by its own module and appears in the menu as one
top-level row; a feature added by another module (as web export adds its own) appears with no change to the menu;
groups are closed until opened, their rows follow their header while open, and the person's choice is remembered in
a file (a damaged file closes every group); the Generation Flows group offers New, the templates and the person's
flows (``flow_open.menu_rows``); a click on a row runs its operator; a worker's chat lists no chosen tool.
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_destinations as cd  # noqa: E402


def registrations():
    """``{feature id: module}`` for every ``chat_destinations.Feature(...)`` an add-on module constructs."""
    found = {}
    for path in ADDON.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "Feature":
                if node.args and isinstance(node.args[0], ast.Constant):
                    found[node.args[0].value] = path.stem
    return found


class RegistryTests(unittest.TestCase):
    def setUp(self):
        saved = dict(cd._features)
        self.addCleanup(lambda: (cd._features.clear(), cd._features.update(saved)))

    def test_every_shipped_feature_is_registered_by_its_module_and_shown(self):
        found = registrations()
        for feature_id in cd.TOP_LEVEL:
            self.assertIn(feature_id, found, "registered by no module: " + feature_id)
        for feature_id, module in found.items():
            self.assertIn(feature_id, cd.TOP_LEVEL, "%s registers %s; list it in TOP_LEVEL" % (module, feature_id))
        for module in set(found.values()) - {"chat_destinations"}:
            source = (ADDON / (module + ".py")).read_text()
            self.assertIn("chat_destinations.register(", source, module)
            self.assertIn("chat_destinations.unregister(", source, module)
        # Registered as they are when the add-on runs, each feature is one top-level row of the menu.
        cd.register(cd.Feature("media_studio", "Media Studio", glyph="studio", order=10, operator="angelo.toggle_studio"))
        cd.register(cd.Feature("workflows", "Workflows", glyph="studio", order=25, operator="angelo.open_media_studio",
                               properties=(("kind", "WORKFLOW"),)))
        cd.register(cd.Feature("flows", "Generation Flows", glyph="flow", order=20, children=lambda *_: []))
        cd.register(cd.Feature("web_export", "Web export", glyph="web", order=50, operator="angelo.web_export"))
        cd.register(cd.Feature("web_preview", "Live browser preview", glyph="web", order=51, operator="angelo.web_preview"))
        cd.register(cd.Feature("godot_link", "Godot live link", glyph="link", order=52, children=lambda *_: []))
        rows = cd.menu_rows()
        top = [row["value"] for row in rows if row["kind"] in {"row", "group"} and not row.get("depth")]
        self.assertEqual(top, list(cd.TOP_LEVEL), "menu order is TOP_LEVEL's")

    def test_another_module_adds_an_entry_without_touching_the_menu(self):
        cd.register(cd.Feature("web_export", "Web export", "Publish the scene as a web page", glyph="web", order=50,
                               section="Share", operator="angelo.web_export"))
        rows = cd.menu_rows()
        row = next(row for row in rows if row.get("value") == "web_export")
        self.assertEqual((row["action"], row["operator"]), ("popup_add_open", "angelo.web_export"))
        self.assertEqual(rows[rows.index(row) - 1], {"kind": "section", "label": "Share"})
        cd.unregister("web_export")
        self.assertFalse(any(row.get("value") == "web_export" for row in cd.menu_rows()))

    def test_a_workers_chat_is_offered_no_tool_its_send_would_not_run(self):
        """Chosen tools act on Angelo's own scene at Send (console.execute skips them in a worker's chat), so a
        worker's chat lists none of them; the rest of the menu is unchanged."""
        lead_only = {item.id for item in cd.features() if item.lead_only}
        self.assertEqual(lead_only, {"base_mesh", "hands"})
        lead, worker = cd.menu_rows(open_groups={"base_mesh"}), cd.menu_rows(open_groups={"base_mesh"}, worker=True)
        self.assertFalse(any(row.get("feature") in lead_only or row.get("value") in lead_only for row in worker))
        self.assertEqual([row for row in lead if row.get("feature") not in lead_only and row.get("value") not in lead_only
                          and row["kind"] != "section"], [row for row in worker if row["kind"] != "section"])
        menu = (ADDON / "chat_add_menu.py").read_text(encoding="utf-8")
        self.assertIn("worker=bool(worker_snapshots(controller))", menu)

    def test_a_feature_is_one_row_or_a_group(self):
        for bad in (cd.Feature("x", "X"), cd.Feature("x", "X", operator="a.b", children=lambda *_: [])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cd.register(bad)


class GroupTests(unittest.TestCase):
    def test_groups_are_closed_until_opened_and_their_rows_follow_the_header(self):
        closed = cd.menu_rows()
        group = next(row for row in closed if row.get("value") == "base_mesh")
        self.assertEqual((group["kind"], group["open"]), ("group", False))
        self.assertFalse(any(row.get("depth") for row in closed), "nothing of a closed group is listed")
        opened = cd.menu_rows(open_groups={"base_mesh"})
        index = next(i for i, row in enumerate(opened) if row.get("value") == "base_mesh")
        children = []
        for row in opened[index + 1:]:   # Up to the next top-level row (Work on hands follows the group).
            if not row.get("depth"):
                break
            children.append(row)
        self.assertTrue(children and all(row["feature"] == "base_mesh" for row in children))
        self.assertEqual(len(children), len(opened) - len(closed))

    def test_the_choice_is_remembered_and_a_damaged_file_closes_every_group(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "state" / "chat-add-menu.json")
            memory = cd.GroupMemory(path)
            self.assertFalse(memory.is_open("base_mesh"))
            self.assertTrue(memory.toggle("base_mesh"))
            self.assertTrue(cd.GroupMemory(path).is_open("base_mesh"), "kept across launches")
            self.assertFalse(cd.GroupMemory(path).toggle("base_mesh"))
            self.assertEqual(json.loads(Path(path).read_text()), {"open": []})
            Path(path).write_text("{not json")
            self.assertEqual(cd.GroupMemory(path).open, set())


class MenuSourceTests(unittest.TestCase):
    def test_flows_offer_new_templates_and_yours(self):
        source = (ADDON / "flow_open.py").read_text()
        rows = source.split("\ndef menu_rows(", 1)[1].split("\ndef ", 1)[0]
        for needed in ('leaf("flows", NEW_ROW, NEW', "flow_templates.listing()", '("template", entry["id"])',
                       "flow_trees()[:LISTED]", "translate=False"):
            self.assertIn(needed, rows)

    def test_a_row_runs_its_operator_and_a_group_click_keeps_the_menu_open(self):
        source = (ADDON / "chat_add_menu.py").read_text()
        dispatch = source.split("\ndef dispatch(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("('INVOKE_DEFAULT', True, **dict(row.get(\"properties\") or ()))", dispatch,
                      "an undoable row (New Flow, a template) is its own undo step")
        group = dispatch.split('elif action == "popup_add_group":', 1)[1].split("elif ", 1)[0]
        self.assertIn("memory().toggle(value)", group)
        self.assertNotIn('state.pop("popover"', group)


if __name__ == "__main__":
    unittest.main()
