# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Part A's share of Media Studio drag and drop, on part E's contract (1.0.5, ``ED_asset_angelo_media.hh``).

On a stand-in ``bpy`` and the sources. Covered: a right-click on a grid item opens the item menu for that item, or for
the whole selection when the item is part of it (as a drag takes it); the menu is E's drag actions
(``media_menus.draw_actions``) plus Open in Image Editor for images, which runs E's placement (``media_place``); the
Properties editor refuses a Media Studio model, and an image off a material slot, through E's refusal, after
Blender's own drops; the Outliner takes any material asset on an object (Media Studio's items stay E's drop); every
new interface string is in the catalogue. The running app's proof is ``test_blender_studio_item_menu``.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
CPP = ROOT / "source/blender/editors/space_api/angelo_studio_drop.cc"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text,
                                                           pgettext_rpt=lambda text, context=None: text))
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import studio_item_menu as menu


class Layout:
    def __init__(self):
        self.rows = []

    def operator(self, idname, icon=None):
        row = SimpleNamespace(idname=idname, item_ids="")
        self.rows.append(row)
        return row

    def separator(self):
        pass


class ItemMenuTests(unittest.TestCase):
    def test_the_right_clicked_item_or_its_selection(self):
        state = {"selected_ids": ["a", "b"]}
        self.assertEqual(menu.items_for(state, "a"), ["a", "b"], "part of the selection: all of it, as a drag")
        self.assertEqual(menu.items_for(state, "c"), ["c"])
        self.assertEqual(menu.items_for({}, "c"), ["c"])

    def draw(self, found):
        layout = Layout()
        media_menus = SimpleNamespace(draw_actions=lambda lay, items: lay.operator("angelo.media_add_to_scene"))
        with mock.patch.object(menu, "kinds", lambda ids: found), \
                mock.patch.dict(sys.modules, {"angelo_agent.media_menus": media_menus}):
            import angelo_agent
            with mock.patch.object(angelo_agent, "media_menus", media_menus, create=True):
                menu.ANGELO_MT_studio_item.draw(SimpleNamespace(layout=layout), None)
        return layout.rows

    def test_the_menu_is_es_actions_plus_open_in_image_editor_for_images(self):
        rows = self.draw([("i1", "IMAGE"), ("i2", "IMAGE")])
        self.assertEqual([r.idname for r in rows], ["angelo.media_add_to_scene", "angelo.media_open_image"])
        self.assertEqual(rows[-1].item_ids, "i1\ni2")
        self.assertEqual([r.idname for r in self.draw([("m", "3D")])], ["angelo.media_add_to_scene"])
        self.assertEqual(self.draw([]), [])

    def test_open_in_image_editor_runs_es_placement(self):
        source = (ADDON / "studio_item_menu.py").read_text()
        execute = source.split("    def execute(self, context):", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("place.open_images(", execute)
        self.assertIn("place.from_ids(", execute)
        self.assertIn("'UNDO'", source.split("class ANGELO_OT_media_open_image", 1)[1].split("def execute", 1)[0])

    def test_a_right_click_on_a_grid_item_opens_it(self):
        source = (ADDON / "studio_input.py").read_text()
        hook = source.split("if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':", 1)[1].split("\n        if ", 1)[0]
        self.assertIn('target[1] == "asset"', hook)
        self.assertIn("studio_item_menu.open_menu(state, target[2])", hook)
        self.assertIn("studio_item_menu.register()", (ADDON / "studio.py").read_text())


class DropTests(unittest.TestCase):
    def test_properties_refuses_through_es_refusal_after_blenders_own_drops(self):
        cpp = CPP.read_text()
        self.assertIn('"ASSET_OT_angelo_media_cannot_drop"', cpp)
        self.assertIn("media::refuse(", cpp)
        self.assertIn("CTX_wm_space_properties(C)", cpp)
        self.assertIn("media::is_absorbed(*C, *drag)", cpp, "the single half of a list drag stays silent")
        self.assertIn('WM_dropboxmap_find("User Interface"', cpp)
        spacetypes = (ROOT / "source/blender/editors/space_api/spacetypes.cc").read_text()
        self.assertGreater(spacetypes.index("ED_dropboxes_angelo_studio_drop();"),
                           spacetypes.index("asset::angelo_media::dropboxes();"))

    def test_the_outliner_takes_any_material_asset_and_leaves_media_studio_items_to_es_drop(self):
        cpp = (ROOT / "source/blender/editors/space_outliner/outliner_dragdrop.cc").read_text()
        poll = cpp.split("static bool material_drop_poll(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("WM_drag_is_ID_type(drag, ID_MA) && !asset::angelo_media::drag_items(*drag)", poll)
        invoke = cpp.split("static wmOperatorStatus material_drop_invoke(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("WM_drag_get_local_ID_or_import_from_asset(C, drag, ID_MA)", invoke)

    def test_every_new_string_is_in_the_catalogue(self):
        pot = (ROOT / "locale/angelo/angelo.pot").read_text()
        for text in ("A 3D model isn't added here: drop it in the 3D View or the Outliner to add it",
                     "Drop an image on a material slot to use it there", "Open an Image Editor to show the image in",
                     "Media Studio Item", "Open in Image Editor"):
            self.assertIn('msgid "%s"' % text, pot)


if __name__ == "__main__":
    unittest.main()
