# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connection this instance saves, and a model catalog that arrives, show in every chat at once.

``ui.pump`` redrew the chats only for another instance's save (``reload_if_changed``); a save of its own (the AI
Providers form, a script, ``ui.connections().add``) asked for no redraw. Until 01565db3fa9 the Generation Flows pump
redrew every chat every 2 s, which hid it; since then an idle chat stays as it was: test_blender_chat_start_states on
9d7a54eab7f, chat_view.draw_count flat for 15 s on Linux Xvfb, the card never drawn (and timed out on Mac 1).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / "scripts/addons_core/angelo_agent/ui.py"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import connections as connections_module  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402


def ui_function(name):
    return next(node for node in ast.parse(UI.read_text(encoding="utf-8")).body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def redraw_helper(console, controllers):
    """``ui._redraw_after_own_save`` compiled on its own, over stand-ins for the module's globals."""
    namespace = {"console": console, "_controllers": controllers, "_connections_seen": 0}
    module = ast.Module(body=[ui_function("_redraw_after_own_save")], type_ignores=[])
    exec(compile(module, str(UI), "exec"), namespace)
    return namespace


class OwnSaveTests(unittest.TestCase):
    def test_each_save_of_its_own_is_counted_and_a_refused_one_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConnectionStore(directory)
            self.assertEqual(store.revision, 0)
            store.add("OPENAI", "API_KEY")
            self.assertEqual(store.revision, 1)
            with patch.object(connections_module.shared_replace, "replace", side_effect=OSError("held open")):
                with self.assertRaises(OSError):
                    store.save()
            self.assertEqual(store.revision, 1, "Nothing changed: nothing to show")
            self.assertFalse(store.reload_if_changed(), "Its own save is not another instance's")

    def test_the_pump_redraws_the_chats_once_per_save_of_its_own(self):
        redraws = []
        console = SimpleNamespace(redraw_chats=lambda controllers: redraws.append(list(controllers)))
        namespace = redraw_helper(console, {"a": "chat"})
        store = SimpleNamespace(revision=0)
        namespace["_redraw_after_own_save"](store)
        self.assertEqual(redraws, [], "Nothing saved yet")
        store.revision = 1
        namespace["_redraw_after_own_save"](store)
        namespace["_redraw_after_own_save"](store)
        self.assertEqual(redraws, [["chat"]], "Once for the save, not again for the same one")

    def test_the_pump_follows_its_own_saves_and_redraws_no_preferences_for_them(self):
        helper = ui_function("_redraw_after_own_save")
        calls = {ast.unparse(node.func) for node in ast.walk(helper) if isinstance(node, ast.Call)}
        self.assertNotIn("preferences_ui.redraw", calls, "A redraw there closes a dialog open over the page")
        pump = [ast.unparse(node.func) for node in ast.walk(ui_function("pump")) if isinstance(node, ast.Call)]
        self.assertIn("_redraw_after_own_save", pump)


class CatalogRedrawTests(unittest.TestCase):
    def test_every_chat_redraws_when_a_model_catalog_arrives_bound_or_not(self):
        """An unbound chat's card reads the catalogs too ("Loading your models…", then "Choose a model to start"):
        the pump bumped only chats bound to the changed connection, so an unbound one stayed on Loading
        (v201-local-models, test_blender_chat_start_states on 9d7a54eab7f)."""
        unconditional = [node for node in ast.walk(ui_function("pump")) if isinstance(node, ast.If)
                         and ast.unparse(node.test) == "catalog_changed"
                         and any(ast.unparse(statement) == "controller.version += 1" for statement in node.body)]
        self.assertTrue(unconditional, "On a catalog change, every chat's version moves, whatever it is bound to")


if __name__ == "__main__":
    unittest.main()
