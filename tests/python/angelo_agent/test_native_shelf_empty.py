# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An Asset Shelf that lists nothing says so and offers Media Studio (1.0.5, ``shelf_empty``), on a stand-in ``bpy``.

Covered: the state shows only when the shelf's library has finished loading and its last build listed nothing (never
while loading, never on a build without Angelo's count); its one line stays centred and inside the shelf, dropping the
message (never the button) when narrow; its button is hit-tested per window and routed through the shelf pills' input
as the "empty" action, which opens Media Studio like the Media Studio pill; the pills show in every interactive run.
It shows only on Angelo Media Studio's own view (``shows_studio``): never on a brush shelf, a person's own catalog,
Current File or another library, and only once the library exists.
The running app's proof is ``test_blender_media_shelf``.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object, Menu=object, AssetShelf=object)
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text),
                              handlers=handlers, background=False)
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import asset_shelf_add, shelf_empty


class StateTests(unittest.TestCase):
    def test_only_a_loaded_shelf_that_lists_nothing(self):
        self.assertTrue(shelf_empty.empty(SimpleNamespace(angelo_list_ready=True, angelo_shown_count=0)))
        self.assertFalse(shelf_empty.empty(SimpleNamespace(angelo_list_ready=False, angelo_shown_count=0)),
                         "Still reading the library: its count can still grow")
        self.assertFalse(shelf_empty.empty(SimpleNamespace(angelo_list_ready=True, angelo_shown_count=3)))
        self.assertFalse(shelf_empty.empty(SimpleNamespace(angelo_list_ready=True, angelo_shown_count=-1)),
                         "Before its first build")
        self.assertFalse(shelf_empty.empty(SimpleNamespace()), "A build without Angelo's count shows nothing")
        self.assertFalse(shelf_empty.empty(None))

    def test_the_wording(self):
        self.assertEqual(shelf_empty.MESSAGE, "Your generated images and models appear here")
        self.assertEqual(shelf_empty.GENERATE, "Generate in Media Studio", "The Asset Browser's empty-state button")


class ScopeTests(unittest.TestCase):
    """Only Angelo Media Studio's view says "Your generated images and models appear here"."""
    SHELF, LIBRARY, TAB = "ANGELO_AST_object_assets", "Angelo Media Studio", "Angelo Media Studio"
    PATHS = (TAB, "Angelo Media Studio/Models", "Angelo Media Studio/Images")

    def shows(self, library, tab, shelf=SHELF, studio_library=LIBRARY, paths=PATHS):
        return shelf_empty.shows_studio(shelf, library, tab, studio_library, paths)

    def test_the_studio_tab_from_every_library_or_its_own(self):
        self.assertTrue(self.shows('ALL', self.TAB))
        self.assertTrue(self.shows(self.LIBRARY, self.TAB))
        self.assertTrue(self.shows('ALL', "Angelo Media Studio/Models"), "A catalog under it")

    def test_the_studio_library_on_its_all_tab(self):
        self.assertTrue(self.shows(self.LIBRARY, ""))
        self.assertTrue(self.shows("My Studio", "", studio_library="My Studio"), "The person renamed the library")

    def test_nothing_on_any_other_empty_shelf(self):
        self.assertFalse(self.shows('ALL', ""), "Every library's All tab is not Angelo's")
        self.assertFalse(self.shows('ALL', "Props/Chairs"), "A person's own catalog")
        self.assertFalse(self.shows('ALL', "Angelo Media Studio Extras"), "A sibling named alike is not under it")
        self.assertFalse(self.shows('LOCAL', ""), "Current File")
        self.assertFalse(self.shows('LOCAL', self.TAB), "Current File, even on the tab")
        self.assertFalse(self.shows('ESSENTIALS', ""))
        self.assertFalse(self.shows("Kitbash", ""), "Another library")
        self.assertFalse(self.shows("Kitbash", self.TAB), "Another library, even on the tab")
        self.assertFalse(self.shows('ALL', self.TAB, shelf="VIEW3D_AST_brush_sculpt"), "A brush shelf")
        self.assertFalse(self.shows(self.LIBRARY, "", shelf="VIEW3D_AST_brush_sculpt"), "A brush shelf on it")

    def test_nothing_before_the_library_exists(self):
        self.assertFalse(self.shows('ALL', self.TAB, studio_library=None))
        self.assertFalse(self.shows('ALL', "", studio_library=None))

    def test_a_library_with_top_level_catalogs_only(self):
        paths = (None, "Models", "Images")   # No parent tab yet: Part E's catalogs at the top.
        self.assertTrue(self.shows('ALL', "Models", paths=paths))
        self.assertFalse(self.shows('ALL', "", paths=paths))

    def test_the_draw_applies_the_scope(self):
        source = (ADDON / "shelf_empty.py").read_text(encoding="utf-8")
        draw = source.split("def _draw(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_studio_view(shelf)", draw)


class LayoutTests(unittest.TestCase):
    def test_one_centred_line_inside_the_shelf(self):
        glyph_x, text_x, (x, y, w, h) = shelf_empty.layout(1000, 80, 300, 150, 1.0)
        self.assertIsNotNone(text_x)
        line = (x + w) - glyph_x
        self.assertAlmostEqual(glyph_x, (1000 - line) / 2)
        self.assertEqual(h, 28.0)
        self.assertAlmostEqual(y, (80 - 28) / 2)
        self.assertLess(text_x + 300, x, "The message ends before the button")

    def test_narrow_drops_the_message_never_the_button(self):
        _glyph, text_x, (x, _y, w, _h) = shelf_empty.layout(260, 80, 300, 150, 1.0)
        self.assertIsNone(text_x)
        self.assertEqual(w, 150 + 2 * shelf_empty.PAD)

    def test_scales_with_the_interface(self):
        _glyph, _text, (_x, _y, w, h) = shelf_empty.layout(2000, 160, 600, 300, 2.0)
        self.assertEqual((w, h), (300 + 4 * shelf_empty.PAD, 56.0))


class InputTests(unittest.TestCase):
    def setUp(self):
        for table in (shelf_empty.places, asset_shelf_add.places):
            table.clear()
            self.addCleanup(table.clear)

    def test_its_button_is_the_empty_action_of_the_pills_input(self):
        asset_shelf_add.places[7] = (1, (("studio", (10, 300, 100, 28)), ("flows", (116, 300, 120, 28))), True, "3d")
        shelf_empty.places[7] = (1, (400, 40, 180, 28))
        self.assertEqual(asset_shelf_add.pill_at(1, 450, 50), (7, "empty"))
        self.assertIsNone(asset_shelf_add.pill_at(2, 450, 50), "Another window")
        self.assertEqual(asset_shelf_add.pill_at(1, 50, 310), (7, "studio"), "The pills still answer")

    def test_the_empty_action_opens_media_studio_not_flows(self):
        source = (ADDON / "asset_shelf_add.py").read_text(encoding="utf-8")
        run = source.split("def activate(", 1)[1].split("\n# -- input", 1)[0]
        self.assertIn('if action == "flows":', run, "Only the Flows pill takes the other route")
        self.assertIn("angelo.open_media_studio", run)

    def test_the_pills_show_in_every_interactive_run(self):
        with mock.patch.object(asset_shelf_add.bpy.app, "background", False):
            self.assertTrue(asset_shelf_add.enabled(), "Automation runs see the product as people do")
        with mock.patch.object(asset_shelf_add.bpy.app, "background", True):
            self.assertFalse(asset_shelf_add.enabled())


if __name__ == "__main__":
    unittest.main()
