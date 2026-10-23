# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's items dragged like any asset: the contracts that hold without a running Blender.

The native drag and drop boxes (``asset_angelo_media.cc``, ``asset_angelo_drag.cc``, ``outliner_dragdrop.cc``) know a
Media Studio asset by the catalogue ids ``studio_assets.CATALOGS`` writes, and hand drops to ``angelo.media_drop``
with its five properties; Media Studio's grid starts the drag with item prefixes. Here: those contracts, the
identifier parsing (an image item's materials map to its pictures in order), the grid's press/release (a click opens,
Shift or Cmd/Ctrl+click selects, a drag carries the pressed item first), writing an item at once before its drag, and
the menus recognizing Media Studio assets. The real drops are ``test_blender_media_drop`` (background) and
``test_blender_media_drop_gui`` (real drags).
"""

import ast
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

def fake_bpy():
    """A stand-in ``bpy`` (as test_native_studio_assets's, built here: importing that test module a second time in a
    batched run would rerun its imports and remove the add-on modules its own tests read)."""
    bpy = types.ModuleType("bpy")
    timers = []
    bpy.app = SimpleNamespace(background=False, binary_path="/nonexistent/Angelo", timers=SimpleNamespace(
        register=lambda f, first_interval=0, persistent=False: timers.append((f, first_interval)),
        is_registered=lambda f: any(t[0] is f for t in timers),
        unregister=lambda f: timers[:] and timers.remove(next(t for t in timers if t[0] is f))),
        handlers=SimpleNamespace(load_factory_preferences_post=[], depsgraph_update_post=[], save_pre=[],
                                 load_post=[], blend_import_post=[], persistent=lambda f: f),
        translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text))
    bpy.timers = timers
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda f: f
    return {"bpy": bpy, "bpy.app": bpy.app, "bpy.app.handlers": handlers}


MODULES = fake_bpy()
MODULES["bpy.props"] = types.SimpleNamespace(StringProperty=lambda **options: None,
                                             BoolProperty=lambda **options: None)
MODULES["bpy.types"] = types.SimpleNamespace(Operator=object)
MODULES["mathutils"] = types.SimpleNamespace(Vector=lambda value=(0.0, 0.0, 0.0): tuple(value))

with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import media_menus, media_place, studio_assets, studio_drag  # noqa: E402

ADDON = ROOT / "scripts/addons_core/angelo_agent"
NATIVE = ROOT / "source/blender/editors"


class NativeContractTests(unittest.TestCase):
    def test_the_native_side_knows_media_studio_by_the_written_catalogue_ids(self):
        source = (NATIVE / "asset/intern/asset_angelo_media.cc").read_text()
        written = {spec[0] for spec in studio_assets.CATALOGS.values()}
        self.assertEqual(set(re.findall(r'_CATALOG = "([0-9a-f-]{36})"', source)), written)

    def test_drops_hand_over_the_operators_properties(self):
        source = (NATIVE / "asset/intern/asset_angelo_media.cc").read_text()
        handed = re.search(r'for \(const char \*name : \{([^}]*)\}\) \{\s*const std::string value', source).group(1)
        operator = ast.parse((ADDON / "media_drop.py").read_text())
        drop = next(n for n in operator.body if isinstance(n, ast.ClassDef) and n.name == "_Drop")
        properties = {n.target.id for n in drop.body if isinstance(n, ast.AnnAssign)}
        self.assertEqual({name.strip(' "') for name in handed.split(",")}, properties)
        self.assertIn('"ANGELO_OT_media_drop"', source)
        self.assertIn('"ANGELO_OT_media_drop_ui"', source)
        # One undo step per drop: the add-on's data drop pushes it, the dispatcher none (an interface-only drop none).
        self.assertNotIn("OPTYPE_UNDO", source)
        flags = {n.name: ast.unparse(n) for n in operator.body if isinstance(n, ast.ClassDef)}
        self.assertIn("'UNDO'", flags["ANGELO_OT_media_drop"])
        self.assertNotIn("'UNDO'", flags["ANGELO_OT_media_drop_ui"])

    def test_every_copy_clears_what_the_last_drop_was_over(self):
        source = (NATIVE / "asset/intern/asset_angelo_media.cc").read_text()
        body = source[source.index("void drop_props_set("):source.index("static const char *refusal")]
        self.assertIn('{"object", "collection", "into"}', body)
        self.assertIn('RNA_string_set(&ptr, name, "")', body)
        outliner = (NATIVE / "space_outliner/outliner_dragdrop.cc").read_text()
        for copy in ("angelo_model_copy", "angelo_image_copy"):
            text = outliner[outliner.index(f"static void {copy}("):]
            text = text[:text.index("\n}\n")]
            self.assertIn("drop_props_set", text, copy)
            self.assertIn("angelo_event(C)", text, f"{copy}: the region's pointer, never an unset mval")

    def test_every_target_the_native_side_names_is_placed(self):
        names = set()
        for path in ("asset/intern/asset_angelo_media.cc", "space_outliner/outliner_dragdrop.cc"):
            names |= set(re.findall(r'"(VIEW3D|OUTLINER|IMAGE|CHAT|STUDIO|NODE|FLOW)"', (NATIVE / path).read_text()))
        self.assertTrue(names)
        self.assertLessEqual(names, set(media_drop_targets()))

    def test_node_editors_are_space_nodes_own(self):
        source = (NATIVE / "asset/intern/asset_angelo_media.cc").read_text()
        self.assertNotIn('"Node Editor"', source, "part B's node_add.cc owns node-editor drops")

    def test_the_grid_drags_item_prefixes(self):
        source = (ADDON / "studio_drag.py").read_text()
        self.assertIn("/{identity}.blend/", source)
        drag = (NATIVE / "asset/intern/asset_angelo_drag.cc").read_text()
        self.assertIn("startswith(prefix)", drag)
        self.assertIn("WM_DRAG_ASSET_LIST", drag, "the selection as a list, as the Asset Browser drags")


def media_drop_targets():
    with mock.patch.dict(sys.modules, MODULES):
        from angelo_agent import media_drop
    return media_drop.TARGETS


class IdentifierTests(unittest.TestCase):
    def test_an_image_items_materials_map_to_its_pictures_in_order(self):
        entry = {"name": "A poster", "files": ["/s/outputs/x/image-1.png", "/s/outputs/x/image-2.png"],
                 "thumbnail": "", "kind": "IMAGE"}
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(media_place, "directories", return_value=(Path(root), Path(root) / "blender-assets")), \
                mock.patch.object(media_place, "_entry", return_value=entry):
            items = media_place.from_identifiers("images/x.blend/Material/A poster 2\nimages/x.blend/Material/A poster\n"
                                                 "models/y.blend/Object/A column\nnot/an/asset\n")
        self.assertEqual([(i.kind, i.id, i.datablock, i.name) for i in items],
                         [("IMAGE", "x", "Material", "A poster 2"), ("IMAGE", "x", "Material", "A poster"),
                          ("3D", "y", "Object", "A column")])
        self.assertEqual([items[0].picture, items[1].picture], entry["files"][::-1])
        self.assertTrue(items[2].blend.endswith("blender-assets/models/y.blend"))


class GridTests(unittest.TestCase):
    def area(self):
        return SimpleNamespace(as_pointer=lambda: 7)

    def event(self, **keys):
        return SimpleNamespace(shift=keys.get("shift", False), ctrl=keys.get("ctrl", False), oskey=keys.get("oskey", False))

    def test_a_click_opens_and_selects_only_that_item(self):
        state = {"selected_ids": ["a", "b"]}
        studio_drag.press(state, "c", (0, 0, 10, 10), self.area(), self.event(), 5, 5)
        self.assertEqual(studio_drag.released(state), ("open", "c", (0, 0, 10, 10), 7))
        self.assertEqual(state["selected_ids"], ["c"])
        self.assertIsNone(studio_drag.released(state), "one release per press")

    def test_shift_or_command_click_toggles_without_opening(self):
        state = {"selected_ids": ["a"]}
        for keys in ({"shift": True}, {"oskey": True}, {"ctrl": True}):
            studio_drag.press(state, "b", (0, 0, 1, 1), self.area(), self.event(**keys), 0, 0)
            self.assertEqual(studio_drag.released(state)[0], "toggle")
        self.assertEqual(state["selected_ids"], ["a", "b"])

    def test_a_drag_starts_past_the_threshold_with_the_pressed_item_first(self):
        state = {"selected_ids": ["a", "b", "c", "hidden"], "visible_assets": ["a", "b", "c"]}
        studio_drag.press(state, "b", (0, 0, 1, 1), self.area(), self.event(), 100, 100)
        context = SimpleNamespace(preferences=SimpleNamespace(inputs=SimpleNamespace(drag_threshold_mouse=3),
                                                              system=SimpleNamespace(ui_scale=1.0)),
                                  temp_override=lambda **k: mock.MagicMock())
        started = []
        with mock.patch.dict(sys.modules, MODULES), mock.patch.object(studio_drag, "start", lambda c, ids: started.append(ids)):
            MODULES["bpy"].context = context
            self.assertFalse(studio_drag.moved(context, None, None, None, state, 102, 101), "inside the threshold")
            self.assertTrue(studio_drag.moved(context, None, None, None, state, 110, 100))
        self.assertEqual(started, [["b", "a", "c"]], "never an item of another tab or folder")
        self.assertNotIn("asset_press", state, "the release after a drag opens nothing")

    def test_dragging_an_unselected_item_drags_it_alone(self):
        state = {"selected_ids": ["a", "c"], "visible_assets": ["a", "b", "c"]}
        studio_drag.press(state, "b", (0, 0, 1, 1), self.area(), self.event(), 0, 0)
        context = SimpleNamespace(preferences=SimpleNamespace(inputs=SimpleNamespace(drag_threshold_mouse=3),
                                                              system=SimpleNamespace(ui_scale=2.0)),
                                  temp_override=lambda **k: mock.MagicMock())
        started = []
        with mock.patch.dict(sys.modules, MODULES), mock.patch.object(studio_drag, "start", lambda c, ids: started.append(ids)):
            MODULES["bpy"].context = context
            self.assertFalse(studio_drag.moved(context, None, None, None, state, 5, 0), "the threshold scales with the UI")
            self.assertTrue(studio_drag.moved(context, None, None, None, state, 7, 0))
        self.assertEqual((started, state["selected_ids"]), ([["b"]], ["b"]))


    def test_a_lost_press_never_drags(self):
        state = {}
        studio_drag.press(state, "b", (0, 0, 1, 1), self.area(), self.event(), 0, 0)
        studio_drag.forget([state])
        self.assertIsNone(studio_drag.released(state))


    def test_only_the_grids_own_drag_dropped_back_on_it_is_ignored_and_only_once(self):
        area, other = SimpleNamespace(as_pointer=lambda: 7), SimpleNamespace(as_pointer=lambda: 9)
        studio_drag._source.update(area=7, items=frozenset({"a"}))
        self.assertFalse(studio_drag.came_back(area, ["b"]), "other items: a drop from elsewhere")
        studio_drag._source.update(area=7, items=frozenset({"a"}))
        self.assertTrue(studio_drag.came_back(area, ["a"]))
        self.assertFalse(studio_drag.came_back(area, ["a"]), "once: the next drop on it is a new one")
        studio_drag._source.update(area=7, items=frozenset({"a"}))
        self.assertFalse(studio_drag.came_back(other, ["a"]), "another Media Studio editor takes it")
        studio_drag._source.update(area=7, items=frozenset({"a"}))
        studio_drag.press({}, "x", (0, 0, 1, 1), area, self.event(), 0, 0)
        self.assertFalse(studio_drag.came_back(area, ["a"]), "a new press forgets the last drag")


class WriteFirstTests(unittest.TestCase):
    def test_a_missing_item_is_written_at_once_and_a_written_one_is_not(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(sys.modules, MODULES):
            from angelo_agent.studio_state import StudioLibrary   # Inside: never leave the add-on in sys.modules.
            library_dir = Path(root) / "media-studio"
            studio = StudioLibrary(library_dir)
            record = studio.add("IMAGE", "A poster", "Fixture", status="ready")
            (studio.result_directory(record["id"]) / "image-1.png").write_bytes(b"\x89PNG fixture")
            record.update(files=["image-1.png"], thumbnail="image-1.png", progress=100)
            studio.update(record)
            directory = studio_assets.asset_directory(library_dir)
            entry = SimpleNamespace(name="Angelo Media Studio", path=str(directory))
            from angelo_agent import studio_assets_host as host
            written = []

            def convert(binary, batch, stop, yield_to=lambda: False):
                for found in batch:
                    Path(found["blend"]).parent.mkdir(parents=True, exist_ok=True)
                    Path(found["blend"]).write_bytes(b"blend")
                    written.append(found["id"])
                return {found["id"]: {"ok": True} for found in batch}
            with mock.patch.object(host, "directories", return_value=(library_dir, directory)), \
                    mock.patch.object(host, "_libraries", return_value=[entry]), \
                    mock.patch.object(host, "convert_batch", convert), \
                    mock.patch.dict(host._state, busy=False, changed=False):
                MODULES["bpy"].app.binary_path = "/nonexistent/Angelo"
                self.assertTrue(studio_drag.ensure_written([record["id"]]))
                self.assertEqual(written, [record["id"]])
                self.assertTrue(host._state["changed"], "the browsers refresh")
                self.assertTrue(studio_drag.ensure_written([record["id"]]))
                self.assertEqual(written, [record["id"]], "an unchanged item is not written again")
                manifest = studio_assets.read_manifest(directory)
                manifest[record["id"]]["failed"] = True
                studio_assets.write_manifest(directory, manifest)
                self.assertFalse(studio_drag.ensure_written([record["id"]]), "failed as it is: drag the files")
                self.assertEqual(written, [record["id"]], "never retried on every drag")
            with mock.patch.object(host, "directories", return_value=(library_dir, directory)), \
                    mock.patch.object(host, "_libraries", return_value=[]):
                self.assertFalse(studio_drag.ensure_written([record["id"]]), "off: the drag carries the files")


class MenuTests(unittest.TestCase):
    def test_only_media_studio_assets_get_the_actions(self):
        models, images = (studio_assets.CATALOGS["3D"][0], studio_assets.CATALOGS["IMAGE"][0])
        asset = lambda catalog, kind, path: SimpleNamespace(  # noqa: E731
            metadata=SimpleNamespace(catalog_id=catalog), id_type=kind, full_library_path=path)
        self.assertEqual(media_menus.studio_item(asset(models, 'OBJECT', "/l/blender-assets/models/abc.blend")),
                         ("abc", "3D"))
        self.assertEqual(media_menus.studio_item(asset(images, 'MATERIAL', "/l/blender-assets/images/def.blend")),
                         ("def", "IMAGE"))
        self.assertIsNone(media_menus.studio_item(asset("00000000-0000-0000-0000-000000000000", 'OBJECT', "/x.blend")))
        self.assertIsNone(media_menus.studio_item(SimpleNamespace(id_type='OBJECT')))

    def test_apply_to_selected_is_offered_for_images_only(self):
        drawn = []
        layout = SimpleNamespace(operator=lambda name, icon=None: drawn.append(name) or SimpleNamespace())
        media_menus.draw_actions(layout, [("a", "3D"), ("b", "IMAGE")])
        self.assertNotIn("angelo.media_apply_to_selected", drawn)
        drawn.clear()
        media_menus.draw_actions(layout, [("b", "IMAGE")])
        self.assertIn("angelo.media_apply_to_selected", drawn)

    def test_studios_popup_offers_the_drag_actions(self):
        source = (ADDON / "studio_asset_ops.py").read_text()
        for name in ("angelo.media_add_to_scene", "angelo.media_apply_to_selected", "angelo.media_add_to_chat"):
            self.assertIn(name, source)


if __name__ == "__main__":
    unittest.main()
