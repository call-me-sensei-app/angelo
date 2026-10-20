# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu and the person's chosen tools outside Blender: rows, choices, layout and keys.

The host step that places a base mesh (``chosen_tools_host``, ``asset_start``) and the drawn menu are checked in
the app by ``test_blender_chosen_tools``.
"""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import chat_add_menu as menu  # noqa: E402
from angelo_agent import chosen_tools as ct  # noqa: E402
from angelo_agent import context_ledger, human_base_meshes  # noqa: E402


class MenuRowTests(unittest.TestCase):
    def test_attach_comes_first_then_the_tool_with_its_why(self):
        rows = ct.menu_rows()
        self.assertEqual([r["kind"] for r in rows[:5]], ["section", "row", "section", "note", "row"])
        self.assertEqual(rows[1]["action"], "popup_add_attach")
        self.assertEqual((rows[2]["label"], rows[3]["label"]), (ct.tool("base_mesh").title, ct.tool("base_mesh").why))

    def test_every_bundled_base_mesh_is_offered_heads_first(self):
        picks = [r for r in ct.menu_rows() if r.get("action") == "popup_add_pick"]
        self.assertEqual(len(picks), len(human_base_meshes.table()["assets"]))
        kinds = [ct.option("base_mesh", r["value"][1])[4] for r in picks]
        self.assertEqual(kinds[:5], ["head"] * 5)
        self.assertEqual(kinds, sorted(kinds, key=ct.KIND_ORDER.index))

    def test_names_are_tidied_for_display_only(self):
        hand = ct.option("base_mesh", "Hand  - Realistic")
        self.assertEqual(hand[1], "Hand – Realistic")   # The bundle's double space; the value stays exact.
        template, fields = ct.option_detail(ct.option("base_mesh", "Head - Stylized"))
        self.assertEqual(template.format(**fields), "38 cm · 5,722 verts")

    def test_the_fit_toggle_needs_a_selected_mesh(self):
        self.assertFalse(any(r["kind"] == "toggle" for r in ct.menu_rows()))
        toggle = next(r for r in ct.menu_rows(fit_target="Blockout") if r["kind"] == "toggle")
        self.assertEqual(toggle["label"], (ct.FIT, {"name": "Blockout"}))

    def test_the_chosen_option_is_marked(self):
        chosen = ct.choose([], "base_mesh", "Head - Stylized")
        marked = [r["value"] for r in ct.menu_rows(chosen) if r.get("selected")]
        self.assertEqual(marked, [("base_mesh", "Head - Stylized")])


class ChoiceTests(unittest.TestCase):
    def test_one_choice_per_tool(self):
        chosen = ct.choose([], "base_mesh", "Head - Stylized")
        chosen = ct.choose(chosen, "base_mesh", "Body Male - Stylized", fit_selected=True)
        self.assertEqual(chosen, [{"tool": "base_mesh", "option": "Body Male - Stylized", "fit_selected": True}])

    def test_unknown_options_are_refused(self):
        for bad in (("base_mesh", "Dragon"), ("nothing", "Head - Stylized")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ct.choose([], *bad)

    def test_chip(self):
        choice = ct.choose([], "base_mesh", "Head - Stylized")[0]
        self.assertEqual(ct.chip(choice), ("Head – Stylized", ct.CHIP_KIND))
        # The chat row names an outcome the same way; an unknown name is shown as it is.
        self.assertEqual(ct.chip({"tool": "base_mesh", "option": "Hand  - Realistic"})[0], "Hand – Realistic")
        self.assertEqual(ct.chip({"tool": "base_mesh", "option": "No Such Mesh"})[0], "No Such Mesh")

    def test_nothing_reaches_the_model_beyond_its_scene_facts(self):
        self.assertNotIn("person_chose", context_ledger.EVENT_OBSERVATION_KEYS)
        self.assertFalse(hasattr(ct, "fact"))


class LayoutTests(unittest.TestCase):
    def test_the_menu_sits_above_the_composer_and_scrolls(self):
        rows = ct.menu_rows()
        geometry = menu.layout(rows, 420, 700, bottom=140)
        x, y, w, h = geometry["rect"]
        self.assertEqual((x, y, w), (12, 140, menu.WIDTH))
        self.assertLessEqual(y + h, 700 - 12)
        self.assertGreater(geometry["max_scroll"], 0)   # 21 meshes do not fit; the list scrolls.
        self.assertEqual(len(geometry["rows"]), len(rows))
        scrolled = menu.layout(rows, 420, 700, bottom=140, scroll=10 ** 6)
        self.assertEqual(scrolled["scroll"], scrolled["max_scroll"])
        self.assertAlmostEqual(scrolled["rows"][-1][1], scrolled["list_rect"][1])   # The last row reaches the bottom.

    def test_a_narrow_chat_narrows_the_menu(self):
        self.assertEqual(menu.layout(ct.menu_rows(), 260, 700, bottom=140)["rect"][2], 236)

    def test_arrow_keys_move_over_actionable_rows_and_return_activates(self):
        rows = ct.menu_rows()
        popup = {"rows": rows, "layout": menu.layout(rows, 420, 700, bottom=140)}
        menu.key(popup, SimpleNamespace(type='DOWN_ARROW'))
        self.assertEqual(rows[popup["cursor"]]["action"], "popup_add_attach")
        menu.key(popup, SimpleNamespace(type='DOWN_ARROW'))
        self.assertEqual(rows[popup["cursor"]]["action"], "popup_add_pick")
        self.assertIs(menu.key(popup, SimpleNamespace(type='RET')), rows[popup["cursor"]])
        menu.key(popup, SimpleNamespace(type='END'))
        self.assertGreater(popup["scroll"], 0)   # The last row was scrolled into view.


if __name__ == "__main__":
    unittest.main()
