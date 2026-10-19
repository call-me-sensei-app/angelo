# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first-launch setup card and the returning user's start panel, laid out on a fake view.

No Blender: preferences changes go to a recorder. Checked: both cards fit their region at any size with every
control inside the card; the setup card offers the language list (all 21 languages in their own names), both
looks, the Blender-settings toggle only when an earlier version exists, the mouse and Spacebar choices behind the
disclosure, and Continue; each choice reaches Blender's preference; the import row names the app and version its
settings come from (or why there are none), and the card remembers the mouse and Spacebar choices changed on it so
an import keeps them; Escape closes the list before it skips setup; the start panel offers recent scenes, New scene, Open… and What's new and nothing else. The real pixels and
order are checked by the GUI suites (``test_blender_first_run``, ``test_blender_start_panel``).
"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from test_native_notice_cards import FakeView  # noqa: E402
from angelo_agent import card_kit as kit, settings_import as si, setup_card, start_card  # noqa: E402

ADDON = ROOT / "scripts/addons_core/angelo_agent"


def source(app=si.BLENDER, version=(5, 2)):
    return si.Source(app, version)


class Recorder:
    def __init__(self, previous=None, reason=None):
        self.prefs = {"language": "en_US", "look": "dark", "select_mouse": "LEFT", "spacebar_action": "PLAY"}
        self.previous = previous   # A settings_import.Source, or None with ``reason``.
        self.reason = reason or (si.FOUND if previous else si.NONE)

    def languages(self):
        return [(code if code in {"en_US", "es", "pt_BR", "pt_PT", "zh_HANS", "zh_HANT"} else code + "_XX", name)
                for code, name in setup_card.LANGUAGES]

    def language(self):
        return self.prefs["language"]

    def set_language(self, identifier):
        self.prefs["language"] = identifier

    def look(self):
        return self.prefs["look"]

    def set_look(self, look):
        self.prefs["look"] = look

    def get(self, name, default):
        return self.prefs.get(name, default)

    def set(self, name, value):
        self.prefs[name] = value

    def import_source(self):
        return self.previous, self.reason


class Host:
    def __init__(self):
        self.closed = None

    def close(self, how="done"):
        self.closed = how

    def redraw(self):
        pass

    def after(self, seconds, function):
        function()


def actions(plan):
    return [(a, v) for _, a, v in plan.hits]


def texts(plan):
    return [op[1].strip() for op in plan.ops if op[0] in {"text", "mono"}]


class SetupCardTests(unittest.TestCase):
    def card(self, previous=None, reason=None):
        self.outcomes = []
        return setup_card.SetupCard(Recorder(previous, reason), self.outcomes.append)

    def plan(self, card, w=1300, h=1200, scale=1.0):
        return card.layout(FakeView(), w, h, scale, kit.UiState())

    def test_fits_any_region_with_controls_inside(self):
        for previous in (None, source()):
            card = self.card(previous)
            for w, h, scale in ((1300, 1200, 1.0), (520, 700, 1.0), (1300, 1200, 2.0), (420, 480, 1.0)):
                for options in (False, True):
                    card.options_open = options
                    plan = self.plan(card, w, h, scale)
                    x, y, cw, ch = plan.card
                    self.assertGreaterEqual(x, 0)
                    self.assertLessEqual(x + cw, w)
                    self.assertGreaterEqual(y, 0, (w, h, scale, options))
                    for rect, action, value in plan.hits:
                        self.assertTrue(kit.inside(plan.card, rect[0] + 1, rect[1] + 1), (action, value, w, h, scale))

    def test_offers_language_look_and_continue(self):
        shown = actions(self.plan(self.card()))
        for expected in (("language_menu", None), ("look", "dark"), ("look", "light"), ("continue", None), ("options", None)):
            self.assertIn(expected, shown)
        self.assertNotIn(("import", None), shown, "No earlier settings: the toggle row explains instead of acting")
        self.assertIn(("import", None), actions(self.plan(self.card(source()))))

    def test_the_import_row_says_where_the_settings_come_from(self):
        drawn = " ".join(texts(self.plan(self.card(source()))))
        self.assertIn("Use my Blender settings", drawn)
        self.assertIn("from Blender 5.2", drawn)
        drawn = " ".join(texts(self.plan(self.card(source(si.ANGELO, (5, 1))))))
        self.assertIn("Use my earlier Angelo settings", drawn)
        self.assertIn("from Angelo 5.1", drawn)
        self.assertIn("No earlier Blender settings", " ".join(texts(self.plan(self.card()))))
        shared = self.card(None, si.SHARED)
        self.assertIn("already uses the settings folder", " ".join(texts(self.plan(shared))))
        self.assertNotIn(("import", None), actions(self.plan(shared)))

    def test_language_list_has_every_language_in_its_own_name(self):
        self.assertEqual(len(setup_card.LANGUAGES), 21)
        card = self.card()
        card.act("language_menu", None, Host())
        plan = self.plan(card)
        codes = [v for a, v in actions(plan) if a == "language"]
        self.assertEqual(len(codes), 21)
        self.assertEqual(kit.hit_test(plan, *_centre(plan, "language", "ar_XX")), ("language", "ar_XX"),
                         "The open list is above the controls it covers")
        card.act("language", "ar_XX", Host())
        self.assertEqual(card.effects.language(), "ar_XX")
        self.assertFalse(card.menu_open)

    def test_choices_reach_the_preferences(self):
        card, host = self.card(), Host()
        card.act("look", "light", host)
        card.act("options", None, host)
        self.assertIn(("select", "RIGHT"), actions(self.plan(card)))
        self.assertEqual(card.keymap_choices, {}, "Nothing changed yet: an import keeps Blender's own")
        card.act("select", "RIGHT", host)
        card.act("space", "SEARCH", host)
        self.assertEqual(card.effects.prefs, {"language": "en_US", "look": "light", "select_mouse": "RIGHT",
                                              "spacebar_action": "SEARCH"})
        self.assertEqual(card.keymap_choices, {"select_mouse": "RIGHT", "spacebar_action": "SEARCH"})

    def test_continue_saves_or_imports(self):
        card, host = self.card(source()), Host()
        card.act("continue", None, host)
        card.closed(host.closed)
        card, host = self.card(source()), Host()
        card.act("import", None, host)
        card.act("continue", None, host)
        card.closed(host.closed)
        self.assertEqual(self.outcomes, ["import"])
        card = self.card(None)
        card.act("import", None, Host())
        self.assertFalse(card.use_blender, "Nothing to import")

    def test_escape_closes_the_list_then_skips(self):
        card, host = self.card(), Host()
        card.act("language_menu", None, host)
        card.act("escape", None, host)
        self.assertIsNone(host.closed)
        self.assertFalse(card.menu_open)
        card.act("escape", None, host)
        self.assertEqual(host.closed, "skip")
        card.closed(host.closed)
        self.assertEqual(self.outcomes, ["skip"])


class StartCardTests(unittest.TestCase):
    def test_recent_scenes_keep_existing_blend_files_newest_first(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            files = [folder / "a.blend", folder / "b.blend", folder / "gone.blend", folder / "notes.txt"]
            for path in files[:2] + files[3:]:
                path.write_text("x")
            history = folder / "recent-files.txt"
            history.write_text("\n".join(str(p) for p in (files[1], files[2], files[0], files[1], files[3])) + "\n")
            self.assertEqual(start_card.recent_scenes(history), [str(files[1]), str(files[0])])
            self.assertEqual(start_card.recent_scenes(folder / "missing.txt"), [])

    def test_offers_scenes_new_open_and_whats_new_only(self):
        taken = []
        card = start_card.StartCard(["/s/Bridge.blend", "/s/Ring.blend", "/s/Tree.blend"], lambda *a: taken.append(a))
        for w, h, scale in ((1300, 1200, 1.0), (500, 700, 1.0), (1300, 1200, 2.0)):
            plan = card.layout(FakeView(), w, h, scale, kit.UiState())
            shown = actions(plan)
            self.assertEqual([v for a, v in shown if a == "scene"], [0, 1, 2])
            self.assertEqual({a for a, _ in shown}, {"scene", "new", "open", "whats_new"})
            for rect, action, value in plan.hits:
                self.assertTrue(kit.inside(plan.card, rect[0] + 1, rect[1] + 1), (action, value, w, h, scale))
        host = Host()
        card.act("scene", 1, host)
        self.assertEqual((host.closed, taken), ("done", [("scene", "/s/Ring.blend")]))

    def test_untitled_tiles_say_when_they_were_edited(self):
        import time
        now = time.mktime((2026, 10, 14, 15, 0, 0, 0, 0, -1))
        day = 86400
        cases = {0: "Edited today", 1: "Edited yesterday", 3: "Edited 3 days ago", 7: "Edited 1 week ago",
                 20: "Edited 2 weeks ago", 45: "Edited 1 month ago", 400: "Edited 1 year ago"}
        for age, text in cases.items():
            self.assertEqual(start_card.edited(now - age * day, now), text, age)
        self.assertEqual(start_card.tile_lines("/s/Ring.blend", None, now - 3 * day, now)[1][0], "Edited 3 days ago")
        self.assertEqual(start_card.tile_lines("/s/Ring.blend", "Make a ring", now, now)[1][0], "Make a ring")
        self.assertNotIn("/s", start_card.tile_lines("/s/Ring.blend", None, None, now)[1][0], "Never the folder")

    def test_without_recent_scenes(self):
        plan = start_card.StartCard([], lambda *a: None).layout(FakeView(), 1300, 1200, 1.0, kit.UiState())
        self.assertNotIn("scene", {a for a, _ in actions(plan)})


class SourceTests(unittest.TestCase):
    def test_blenders_splash_menus_draw_nothing_while_angelos_card_follows(self):
        source = (ADDON / "splash_setup.py").read_text()
        for name in ("ANGELO_MT_splash_quick_setup", "ANGELO_MT_splash"):
            menu = source.split("class " + name, 1)[1].split("\nclass ", 1)[0]
            self.assertIn("if pending():", menu)
            self.assertIn("_redirect(context,", menu, "Opened later, it gives way to Angelo's card")
            self.assertNotIn("_stock_draw", menu, "Blender's templates and Donate page never draws")
        self.assertNotIn("bpy.ops.wm.splash(", source, "Blender's splash is never reopened")

    def test_strings_are_single_literals(self):
        import ast
        for name in ("setup_card.py", "start_card.py", "firstlaunch_controls.py", "splash_setup.py",
                     "settings_import_ui.py"):
            tree = ast.parse((ADDON / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "n_":
                    self.assertIsInstance(node.args[0], ast.Constant, name)


def _centre(plan, action, value):
    rect = next(r for r, a, v in plan.hits if a == action and v == value)
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


if __name__ == "__main__":
    unittest.main()
