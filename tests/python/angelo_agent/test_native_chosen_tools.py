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

from angelo_agent import chat_add_layout as launcher, chat_add_menu as menu, chat_add_nav as nav  # noqa: E402
from angelo_agent import chat_destinations as cd  # noqa: E402
from angelo_agent import chosen_tools as ct  # noqa: E402
from angelo_agent import context_ledger, human_base_meshes  # noqa: E402


class MenuRowTests(unittest.TestCase):
    def test_attach_comes_first_then_the_base_meshes_as_one_closed_group(self):
        """1.0.5: the 21 base meshes were listed open; they are one group, closed until the person opens it."""
        rows = ct.menu_rows()
        self.assertEqual(rows[1]["action"], "popup_add_attach")
        (group,) = [r for r in rows if r.get("value") == "base_mesh"]
        self.assertEqual((group["kind"], group["open"], group["subtitle"]), ("group", False, ct.tool("base_mesh").why))
        self.assertFalse(any(r.get("action") == "popup_add_pick" for r in rows))

    def test_every_bundled_base_mesh_is_offered_heads_first(self):
        picks = [r for r in ct.menu_rows(open_groups={"base_mesh"}) if r.get("action") == "popup_add_pick"]
        self.assertEqual(len(picks), len(human_base_meshes.table()["assets"]))
        kinds = [ct.option("base_mesh", r["value"][1])[4] for r in picks]
        self.assertEqual(kinds[:5], ["head"] * 5)
        self.assertEqual(kinds, sorted(kinds, key=ct.KIND_ORDER.index))

    def test_names_are_tidied_for_display_only(self):
        hand = ct.option("base_mesh", "Hand  - Realistic")
        self.assertEqual(hand[1], "Hand – Realistic")   # The bundle's double space; the value stays exact.
        template, fields = ct.option_detail(ct.option("base_mesh", "Head - Stylized"))
        self.assertEqual(template.format(**fields), "38 cm · 5,722 verts")

    def test_the_bundles_misspelling_is_corrected_where_people_read_it_only(self):
        """1.0.5 review row 20: "Primitve (Realistic)" in the menu. The upstream data-block keeps its spelling, since
        appending looks it up by that name, and the old spelling still finds the asset."""
        labels = [row[1] for row in ct.tool("base_mesh").options]
        self.assertFalse([label for label in labels if "Primitve" in label])
        rows = [a for a in human_base_meshes.table()["assets"] if "Primitve" in a["datablock_name"]]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["name"], row["datablock_name"].strip().replace("Primitve", "Primitive"))
            self.assertTrue(human_base_meshes._matches(row, row["datablock_name"]))
            self.assertTrue(human_base_meshes._matches(row, row["name"]))

    def test_no_text_a_person_reads_names_an_internal_object_or_timing(self):
        """Maintainer rule (1.0.5): the chat row read "… (GEO-head_stylized)" and before that "(4 ms)"."""
        root = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
        host = (root / "chosen_tools_host.py").read_text(encoding="utf-8")
        self.assertIn('notice("Added {choice} to start from", choice=name)', host)
        self.assertNotIn("{object}", host)
        self.assertNotIn("{ms}", host)
        start = (root / "asset_start.py").read_text(encoding="utf-8")
        self.assertNotRegex(start, r'raise ValueError\([^)]*datablock_name', "Errors never quote a data-block name")

    def test_the_fit_toggle_needs_a_selected_mesh(self):
        self.assertFalse(any(r["kind"] == "toggle" for r in ct.menu_rows(open_groups={"base_mesh"})))
        toggle = next(r for r in ct.menu_rows(fit_target="Blockout", open_groups={"base_mesh"}) if r["kind"] == "toggle")
        self.assertEqual(toggle["label"], (ct.FIT, {"name": "Blockout"}))

    def test_the_chosen_option_is_marked(self):
        chosen = ct.choose([], "base_mesh", "Head - Stylized")
        marked = [r["value"] for r in ct.menu_rows(chosen, open_groups={"base_mesh"}) if r.get("selected")]
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


class HandsChoiceTests(unittest.TestCase):
    """Work on hands (the person's choice): one row after the base meshes, one click chooses it, and its facts reach
    the model with that request only (an event key of the observation, never repeated as state)."""

    def test_one_row_that_chooses_in_one_click(self):
        (row,) = [r for r in ct.menu_rows() if r.get("value") == "hands"]
        self.assertEqual((row["kind"], row["action"], row["label"]), ("row", "popup_add_choose", "Work on hands"))
        self.assertEqual(ct.choose([], "hands", ct.tool("hands").options[0][0]),
                         [{"tool": "hands", "option": "hands", "fit_selected": False}])

    def test_the_chip_is_interface_text(self):
        self.assertEqual(ct.chip({"tool": "hands", "option": "hands"}), ("Hands", ct.HANDS_KIND))

    def test_its_facts_are_an_event_of_the_request(self):
        self.assertIn("hands", context_ledger.EVENT_OBSERVATION_KEYS)
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/chosen_tools_host.py"
                  ).read_text(encoding="utf-8")
        self.assertIn('return {"hands": outcome["facts"]}', source)
        self.assertIn('notice("Gave the agent the hands of {armature}"', source)


class LayoutTests(unittest.TestCase):
    """The menu's geometry and keys: the whole launcher in every language is ``test_native_chat_add_layout``."""

    def measure(self, text, size):
        return len(text) * size * 0.55

    def wrap(self, text, width, size):
        return [text[i:i + 40] for i in range(0, len(text), 40)] or [""]

    def items(self):
        return [dict(entry, title=entry["label"], why=entry.get("subtitle") or "") for entry in cd.entries()]

    def test_the_menu_sits_above_the_composer_inside_the_chat(self):
        geometry = launcher.place(self.items(), self.measure, self.wrap, 420, 700, 140)
        x, y, w, h = geometry["rect"]
        self.assertEqual((x, y, w), (12, 140, 420 - 24))   # As wide as the chat allows (1.0.5), up to MAX_WIDTH.
        self.assertLessEqual(y + h, 700 - launcher.TOP_MARGIN)
        self.assertEqual(launcher.place(self.items(), self.measure, self.wrap, 900, 1400, 140)["rect"][2],
                         launcher.MAX_WIDTH)

    def test_the_base_meshes_page_scrolls_inside_the_menu(self):
        top = launcher.place(self.items(), self.measure, self.wrap, 420, 700, 140)
        rows = ct.base_mesh_rows()
        page = launcher.stack([menu.HEIGHTS["row"]] * len(rows), top["list_rect"], 10 ** 6, launcher.PAGE_HEADER)
        self.assertGreater(page["max_scroll"], 0)   # 21 meshes do not fit; the page scrolls.
        self.assertAlmostEqual(page["rows"][-1][1], page["area"][1])   # Scrolled to its end, the last row reaches the bottom.

    def test_a_long_why_line_wraps_before_it_is_elided(self):
        view = SimpleNamespace(_flow_wrap=lambda text, width, size: [text[i:i + 10] for i in range(0, len(text), 10)],
                               elide=lambda text, width, size: text[:8] + "\u2026")
        self.assertEqual(menu.subtitle_lines(view, "short", 100), ["short"])
        self.assertEqual(menu.subtitle_lines(view, "a" * 20, 100), ["a" * 10] * 2)
        lines = menu.subtitle_lines(view, "b" * 35, 100)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("\u2026"))
        self.assertEqual(menu.row_height({"kind": "row", "height": 60}), 60)

    def test_a_narrow_chat_narrows_the_menu(self):
        self.assertEqual(launcher.place(self.items(), self.measure, self.wrap, 260, 700, 140)["rect"][2], 236)

    def test_the_keys_move_over_the_grid_and_return_activates(self):
        # Stops as a draw records them: (action, value, row, column); a two-column grid under the bar.
        stops = [("popup_add_attach", "attach", -1, 1), ("popup_add_open", "a", 0, 0), ("popup_add_open", "b", 0, 1),
                 ("popup_add_group", "base_mesh", 1, 0), ("popup_add_choose", "hands", 1, 1),
                 ("popup_add_open", "c", 2, 0)]
        popup = {"kind": "add", "stops": stops, "query": "", "page": None}

        def press(kind, shift=False):
            return menu.key(popup, SimpleNamespace(type=kind, unicode="", ctrl=False, oskey=False, shift=shift))
        press('DOWN_ARROW')
        self.assertEqual(popup["cursor"], 0, "the first stop: Attach")
        press('DOWN_ARROW')
        press('RIGHT_ARROW')
        self.assertEqual(stops[popup["cursor"]][1], "b")
        press('DOWN_ARROW')
        self.assertEqual(stops[popup["cursor"]][1], "hands", "down keeps the column")
        press('DOWN_ARROW')
        self.assertEqual(stops[popup["cursor"]][1], "c", "the nearest column of a shorter row")
        press('UP_ARROW')
        press('LEFT_ARROW')
        self.assertEqual(press('RET'), {"action": "popup_add_group", "value": "base_mesh"})
        press('TAB', shift=True)
        self.assertEqual(stops[popup["cursor"]][1], "b")

    def test_typing_searches_and_esc_and_backspace_step_back(self):
        popup = {"kind": "add", "stops": [], "query": "", "page": "flows"}

        def press(kind, text=""):
            menu.key(popup, SimpleNamespace(type=kind, unicode=text, ctrl=False, oskey=False, shift=False))
        press('U', "u")
        press('V', "v")
        self.assertEqual((popup["query"], popup["esc_back"]), ("uv", True))
        press('BACK_SPACE')
        self.assertEqual(popup["query"], "u")
        press('ESC')
        self.assertEqual((popup["query"], popup["page"]), ("", "flows"), "Esc clears the search first")
        press('ESC')
        self.assertEqual((popup["page"], popup["esc_back"]), (None, False), "then goes back; then chat_view closes")
        nav.open_page(popup, "flows")
        press('BACK_SPACE')
        self.assertIsNone(popup["page"], "Backspace with no search goes back")


if __name__ == "__main__":
    unittest.main()
