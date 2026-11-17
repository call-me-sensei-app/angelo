# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An add-on that replaces the Extensions list's row drawer never blanks the list (``bl_pkg.extension_draw_compat``).

Blender 5.3 added ``show_type`` to ``bl_extension_ui.extension_draw_item`` (a12f839dd9c). An add-on that replaced
the function with the 5.2 argument list (BlenderKit, #166392, #166400, BlenderKit#2356) raised ``TypeError`` on
every row, which ends the panel's draw: the list was empty. Covered:

* the built-in drawer gets every argument;
* a replacement is given only the arguments it takes, so its own rows still draw, and one line names its module
  and what it was not given, once;
* a replacement that takes ``**kwargs`` gets everything and nothing is printed;
* a replacement that still raises draws the row as Blender does, with one line;
* ``bl_extension_ui`` draws every row through this, with the drawer as the module holds it now (a replacement
  included) and the built-in kept at import.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BL_PKG = ROOT / "scripts/addons_core/bl_pkg"


def load():
    spec = importlib.util.spec_from_file_location("extension_draw_compat_under_test",
                                                  BL_PKG / "extension_draw_compat.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARGUMENTS = {"pkg_id": "blenderkit", "item_local": None, "item_remote": None, "is_enabled": True,
             "is_outdated": False, "show": False, "show_type": True, "mark": None, "repo_index": 0, "repo_item": None,
             "operation_in_progress": False, "extensions_warnings": {}, "show_developer_ui": False}


def builtin(layout, *, pkg_id, item_local, item_remote, is_enabled, is_outdated, show, show_type, mark, repo_index,
            repo_item, operation_in_progress, extensions_warnings, show_developer_ui):
    layout.append(("builtin", pkg_id, show_type))


def older(layout, *, pkg_id, item_local, item_remote, is_enabled, is_outdated, show, mark, repo_index, repo_item,
          operation_in_progress, extensions_warnings, show_developer_ui):
    """The 5.2 argument list, as BlenderKit replaced it."""
    layout.append(("older", pkg_id))


older.__module__ = "blenderkit.override_extension_draw"


class DrawTests(unittest.TestCase):
    def setUp(self):
        self.compat, self.lines, self.layout = load(), [], []

    def draw(self, drawer):
        self.compat.draw(drawer, builtin, self.layout, dict(ARGUMENTS), report=self.lines.append)

    def test_the_builtin_drawer_gets_every_argument(self):
        self.draw(builtin)
        self.assertEqual(self.layout, [("builtin", "blenderkit", True)])
        self.assertEqual(self.lines, [])

    def test_a_replacement_with_the_older_arguments_still_draws_its_rows_and_says_so_once(self):
        for _ in range(3):
            self.draw(older)
        self.assertEqual(self.layout, [("older", "blenderkit")] * 3)
        self.assertEqual(len(self.lines), 1, self.lines)
        self.assertIn("blenderkit.override_extension_draw", self.lines[0])
        self.assertIn("show_type", self.lines[0])

    def test_a_replacement_that_takes_any_keyword_gets_everything_quietly(self):
        seen = []

        def flexible(layout, **arguments):
            seen.append(sorted(arguments))
        self.draw(flexible)
        self.assertEqual(seen, [sorted(ARGUMENTS)])
        self.assertEqual(self.lines, [])

    def test_a_replacement_that_fails_draws_the_row_as_blender_does(self):
        def broken(layout, *, pkg_id, needs_this):
            raise AssertionError("never called: it needs an argument nobody gives")

        def raising(layout, **arguments):
            raise TypeError("the add-on's own bug")
        for drawer in (broken, raising, broken):
            self.draw(drawer)
        self.assertEqual(self.layout, [("builtin", "blenderkit", True)] * 3)
        self.assertEqual(len(self.lines), 2, "one line per replacing function")


class WiringTests(unittest.TestCase):
    def test_every_row_is_drawn_through_the_compat_layer(self):
        source = (BL_PKG / "bl_extension_ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "extension_draw_item"]
        self.assertEqual(calls, [], "a direct call raises on a replacement with other arguments")
        draws = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(".draw")
                 and "extension_draw_compat" in ast.unparse(n.func)]
        self.assertEqual(len(draws), 1)
        self.assertEqual([ast.unparse(a) for a in draws[0].args[:2]],
                         ["extension_draw_item", "_EXTENSION_DRAW_ITEM_BUILTIN"])
        self.assertIn("_EXTENSION_DRAW_ITEM_BUILTIN = extension_draw_item", source)


if __name__ == "__main__":
    unittest.main()
