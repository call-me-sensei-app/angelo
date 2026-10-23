# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object mode's Angelo shelf collapses to its tab strip, starts collapsed and remembers the choice (1.0.5).

On a stand-in ``bpy``: the shelf's three states (``media_shelf.state``: expanded, collapsed, hidden) and the choice
kept per user (an older true/false choice read as expanded/hidden); ``DEFAULT_COLLAPSED`` only on a binary that has
it; picking a tab of a collapsed shelf expands it; the pills stay on the tab strip, with the collapse chevron first
(icon only) on Object mode's shelf. From the source, the native contract: a region collapsed (``RGN_FLAG_COLLAPSED``)
keeps the regions that hide with it, has no unhide tab of its own, is shown in full once it shows again, and each
shelf instance keeps its own collapsed state. The running app's proof is ``test_blender_media_shelf``.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
SOURCE = ROOT / "source/blender"
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
                              handlers=handlers, background=True)
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import asset_shelf_add, media_shelf


def region(kind, width=400, height=60, x=0, y=0):
    return SimpleNamespace(type=kind, width=width, height=height, x=x, y=y)


def area(rows=True, header=True):
    regions = [region('WINDOW', 1200, 800)]
    regions.append(region('ASSET_SHELF', 1200, 80) if rows else region('ASSET_SHELF', 1, 1))
    regions.append(region('ASSET_SHELF_HEADER', 1200, 26, y=80 if rows else 0) if header
                   else region('ASSET_SHELF_HEADER', 1, 1))
    return SimpleNamespace(type='VIEW_3D', regions=regions)


class StateTests(unittest.TestCase):
    def test_three_states_from_the_regions(self):
        self.assertEqual(media_shelf.state(area()), "expanded")
        self.assertEqual(media_shelf.state(area(rows=False)), "collapsed", "Only the tab strip shows")
        self.assertEqual(media_shelf.state(area(rows=False, header=False)), "hidden")

    def test_only_a_settled_change_is_the_persons_choice(self):
        self.assertEqual(media_shelf.change("collapsed", "expanded", True), "expanded")
        self.assertIsNone(media_shelf.change("collapsed", "expanded", False), "Still switching shelves")
        self.assertIsNone(media_shelf.change(None, "collapsed", True), "First sight is not a choice")

    def test_the_choice_is_kept_and_an_older_one_is_read(self):
        folder = Path(tempfile.mkdtemp())
        with mock.patch.object(media_shelf, "_path", lambda: folder / media_shelf.FILE):
            self.assertIsNone(media_shelf.remembered(), "No choice yet: the shelf's own default (collapsed)")
            (folder / media_shelf.FILE).write_text('{"object_shelf_shown": true}', encoding="utf-8")
            self.assertEqual(media_shelf.remembered(), "expanded", "Shown before collapsing existed")
            (folder / media_shelf.FILE).write_text('{"object_shelf_shown": false}', encoding="utf-8")
            self.assertEqual(media_shelf.remembered(), "hidden")
            media_shelf.remember("collapsed")
            self.assertEqual(media_shelf.remembered(), "collapsed")
            self.assertNotIn("object_shelf_shown", (folder / media_shelf.FILE).read_text(encoding="utf-8"))
            media_shelf.remember("sideways")
            self.assertEqual(media_shelf.remembered(), "collapsed", "Only the three states are kept")


class OptionsTests(unittest.TestCase):
    def options_with(self, items):
        enum = SimpleNamespace(keys=lambda: items)
        shelf = SimpleNamespace(bl_rna=SimpleNamespace(properties={"bl_options": SimpleNamespace(enum_items=enum)}))
        with mock.patch.object(media_shelf.bpy.types, "AssetShelf", shelf):
            return media_shelf._options()

    def test_collapsed_by_default_where_the_binary_has_it(self):
        self.assertIn('DEFAULT_COLLAPSED', self.options_with(['DEFAULT_VISIBLE', 'DEFAULT_COLLAPSED']))
        self.assertIn('DEFAULT_VISIBLE', self.options_with(['DEFAULT_VISIBLE', 'DEFAULT_COLLAPSED']))

    def test_an_older_binary_registers_without_it(self):
        self.assertNotIn('DEFAULT_COLLAPSED', self.options_with(['DEFAULT_VISIBLE']))
        self.assertNotIn('DEFAULT_COLLAPSED', media_shelf._options(), "No bl_rna at all (this stand-in)")


class TabTests(unittest.TestCase):
    def setUp(self):
        media_shelf._tabs.clear()
        self.addCleanup(media_shelf._tabs.clear)
        self.shelf = SimpleNamespace(active_catalog_path="Angelo Media Studio")
        self.deferred = []
        patches = (mock.patch.object(media_shelf, "_shelf", lambda window, area: self.shelf),
                   mock.patch.object(media_shelf, "_defer", self.deferred.append))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_picking_a_tab_of_a_collapsed_shelf_expands_it(self):
        media_shelf._expand_on_tab(None, None, 7, "collapsed")
        self.assertEqual(self.deferred, [], "First sight of a collapsed shelf")
        self.shelf.active_catalog_path = "Props"
        media_shelf._expand_on_tab(None, None, 7, "collapsed")
        self.assertEqual(len(self.deferred), 1)

    def test_an_expanded_shelf_is_left_alone(self):
        media_shelf._expand_on_tab(None, None, 7, "expanded")
        self.shelf.active_catalog_path = "Props"
        media_shelf._expand_on_tab(None, None, 7, "expanded")
        self.assertEqual(self.deferred, [])
        self.assertNotIn(7, media_shelf._tabs)

    def test_hiding_in_full_also_drops_the_tab_strip(self):
        source = (ADDON / "media_shelf.py").read_text(encoding="utf-8")
        hidden = source.split('if wanted == "hidden":', 1)[1].split("elif", 1)[0]
        self.assertLess(hidden.index("set_collapsed(window, area, False)"), hidden.index("= False"))


class PillTests(unittest.TestCase):
    def test_the_pills_stay_on_a_collapsed_shelfs_tab_strip(self):
        self.assertIsNotNone(asset_shelf_add.shelf_header(area()))
        self.assertIsNotNone(asset_shelf_add.shelf_header(area(rows=False)))
        self.assertIsNone(asset_shelf_add.shelf_header(area(rows=False, header=False)))
        self.assertTrue(asset_shelf_add.collapsed(area(rows=False)))
        self.assertFalse(asset_shelf_add.collapsed(area()))

    def test_the_chevron_comes_first_and_is_icon_only(self):
        widths = {"studio": 90.0, "flows": 120.0}
        rects, labelled = asset_shelf_add.layout((0, 0, 1200, 26), (0, 0, 1200, 800), [], widths, 1.0,
                                                 (asset_shelf_add.FOLD,) + asset_shelf_add.PILLS)
        self.assertTrue(labelled)
        self.assertEqual([action for action, _rect in rects], ["fold", "studio", "flows"])
        fold = rects[0][1]
        self.assertEqual((fold[2], fold[3]), (asset_shelf_add.HEIGHT, asset_shelf_add.HEIGHT))
        self.assertLess(fold[0] + fold[2], rects[1][1][0], "The pills follow it")

    def test_only_object_modes_shelf_on_a_binary_that_collapses(self):
        view = SimpleNamespace(type='VIEW_3D')
        rna = SimpleNamespace(bl_rna=SimpleNamespace(functions={"angelo_set_collapsed": None}, properties={}))
        with mock.patch.object(asset_shelf_add.bpy.types, "AssetShelf", rna):
            self.assertTrue(asset_shelf_add.folds(view, 'OBJECT'))
            self.assertFalse(asset_shelf_add.folds(view, 'SCULPT'), "A brush shelf keeps Blender's own")
            self.assertFalse(asset_shelf_add.folds(SimpleNamespace(type='IMAGE_EDITOR'), 'OBJECT'))
        self.assertFalse(asset_shelf_add.folds(view, 'OBJECT'), "An older binary")

    def test_the_chevron_says_what_it_does(self):
        self.assertEqual(asset_shelf_add.FOLD_TIPS[True], "Show the shelf's assets")
        self.assertEqual(asset_shelf_add.FOLD_TIPS[False], "Collapse the shelf to its tabs")


class NativeTests(unittest.TestCase):
    """The C contract, read from the source (the region code runs only in the built app)."""

    def read(self, path):
        return (SOURCE / path).read_text(encoding="utf-8")

    def test_a_collapsed_region_keeps_the_regions_that_hide_with_it(self):
        area_cc = self.read("editors/screen/area.cc")
        hidden = area_cc.split("static bool region_is_hidden(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("!(region->prev->flag & RGN_FLAG_COLLAPSED)", hidden)
        visibility = area_cc.split("static void region_evaluate_visibility(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("RGN_FLAG_COLLAPSED", visibility)
        self.assertIn("RGN_FLAG_TOO_SMALL", visibility, "A shelf too small for its area still hides its strip")

    def test_no_unhide_tab_and_shown_in_full_once_shown(self):
        area_cc = self.read("editors/screen/area.cc")
        poll = area_cc.split("static bool region_azone_edge_poll(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (region->flag & RGN_FLAG_COLLAPSED) {\n    return false;", poll)
        self.assertIn("if (!(region->flag & RGN_FLAG_HIDDEN)) {\n    region->flag &= ~RGN_FLAG_COLLAPSED;",
                      area_cc)

    def test_each_shelf_keeps_its_own_collapsed_state(self):
        shelf_cc = self.read("editors/asset/intern/asset_shelf.cc")
        self.assertIn("ASSET_SHELF_TYPE_FLAG_DEFAULT_COLLAPSED", shelf_cc)
        self.assertIn("shelf.instance_flag & ASSETSHELF_REGION_IS_COLLAPSED, RGN_FLAG_COLLAPSED", shelf_cc)
        self.assertIn("ASSETSHELF_REGION_IS_COLLAPSED);", shelf_cc)
        rna = self.read("makesrna/intern/rna_ui.cc")
        self.assertIn('"DEFAULT_COLLAPSED"', rna)
        self.assertIn('"angelo_set_collapsed"', rna)
        self.assertIn("FUNC_USE_SELF_ID | FUNC_USE_CONTEXT", rna.split('"angelo_set_collapsed"', 1)[1][:400])


if __name__ == "__main__":
    unittest.main()
