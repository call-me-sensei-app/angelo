# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""*Generate* on every Asset Shelf opens Media Studio on the shelf's tab (1.0.5), on a stand-in ``bpy``.

Covered: which tab a place asks for (``studio_toggle.kind_for`` / ``studio_kind``: a surface's look gives Image,
everything else 3D, across editors, modes and asset types); the one opener (``open_media_studio``: a new window on
the tab, a Studio it opened brought forward with its state, AUTO keeping an open Studio's tab, an unknown kind
refused); the pill's label, icon and tokens (doc/angelo/studio_entry_points.rst); where the pill goes on a shelf
(``asset_shelf_add.place``: the shelf's left end, past the toolbar, the Adjust Last Operation panel and Angelo in the
corner, nowhere when it does not fit); its input is a passive canvas input. The running app's proof is
``test_blender_asset_shelf_add``.
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
                              handlers=handlers, background=True)
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import asset_shelf_add, input_policy, media_shelf, studio_toggle


class KindTests(unittest.TestCase):
    def test_a_surfaces_look_gives_image(self):
        kind = studio_toggle.kind_for
        self.assertEqual(kind('VIEW_3D', 'PAINT_TEXTURE'), "image")
        self.assertEqual(kind('IMAGE_EDITOR', 'OBJECT'), "image")
        self.assertEqual(kind('NODE_EDITOR', 'OBJECT', tree_type='ShaderNodeTree'), "image")
        self.assertEqual(kind('NODE_EDITOR', 'OBJECT', tree_type='TextureNodeTree'), "image")
        self.assertEqual(kind('PROPERTIES', 'OBJECT', properties_tab='MATERIAL'), "image")
        self.assertEqual(kind('PROPERTIES', 'SCULPT', properties_tab='TEXTURE'), "image")
        for asset in ('MATERIAL', 'IMAGE', 'TEXTURE'):
            self.assertEqual(kind('FILE_BROWSER', 'OBJECT', asset_type=asset), "image", asset)

    def test_everything_else_gives_3d(self):
        kind = studio_toggle.kind_for
        for mode in ('SCULPT', 'OBJECT', 'EDIT_MESH', 'PAINT_VERTEX', 'PAINT_WEIGHT', 'PAINT_GREASE_PENCIL',
                     'SCULPT_GREASE_PENCIL', 'SCULPT_CURVES', 'POSE'):
            self.assertEqual(kind('VIEW_3D', mode), "3d", mode)
        self.assertEqual(kind('NODE_EDITOR', 'OBJECT', tree_type='GeometryNodeTree'), "3d")
        self.assertEqual(kind('PROPERTIES', 'OBJECT', properties_tab='OBJECT'), "3d")
        for asset in ('OBJECT', 'COLLECTION', 'MESH', 'NODETREE'):
            self.assertEqual(kind('FILE_BROWSER', 'OBJECT', asset_type=asset), "3d", asset)
        self.assertEqual(kind(None, None), "3d")

    def test_a_brush_asset_leaves_the_tab_to_the_editor_and_mode(self):
        kind = studio_toggle.kind_for
        self.assertEqual(kind('VIEW_3D', 'PAINT_TEXTURE', asset_type='BRUSH'), "image")
        self.assertEqual(kind('VIEW_3D', 'SCULPT', asset_type='BRUSH'), "3d")

    def test_studio_kind_reads_the_context(self):
        context = SimpleNamespace(space_data=SimpleNamespace(type='VIEW_3D'), mode='PAINT_TEXTURE')
        self.assertEqual(studio_toggle.studio_kind(context), "image")
        context = SimpleNamespace(space_data=SimpleNamespace(type='FILE_BROWSER'), mode='OBJECT',
                                  asset=SimpleNamespace(id_type='MATERIAL'))
        self.assertEqual(studio_toggle.studio_kind(context), "image")
        context = SimpleNamespace(space_data=SimpleNamespace(type='PROPERTIES', context='MATERIAL'), mode='OBJECT')
        self.assertEqual(studio_toggle.studio_kind(context), "image")
        self.assertEqual(studio_toggle.studio_kind(SimpleNamespace()), "3d")


class Window:
    def __init__(self, pointer, areas):
        self.pointer, self.screen = pointer, SimpleNamespace(areas=areas)

    def as_pointer(self):
        return self.pointer


class Area:
    def __init__(self, pointer, studio=False):
        self.pointer, self.studio, self.redraws = pointer, studio, 0

    def as_pointer(self):
        return self.pointer

    def tag_redraw(self):
        self.redraws += 1


class OpenerTests(unittest.TestCase):
    def setUp(self):
        self.views = {}
        studio = types.ModuleType("angelo_agent.studio")
        studio.is_studio = lambda area: area.studio
        studio.view = lambda area: self.views.setdefault(area.as_pointer(), {"kind": "IMAGE", "prompt": ""})
        studio.click = lambda context, state, action, value, rect: state.update(kind=value, focus=None)
        chat_toggle = types.ModuleType("angelo_agent.chat_toggle")
        chat_toggle.redraw_top_bars = lambda: None
        self.main = Window(1, [Area(10)])
        self.windows = [self.main]
        self.closed = []
        context = SimpleNamespace(window=self.main, window_manager=SimpleNamespace(windows=self.windows))
        # Director's path (the window): Manual's tab routing (studio_workspaces.show) declines.
        self.manual = False   # Manual's tab routing declines (Director's path) unless a test turns it on.
        self.tab_shown = []
        self.workspace = None   # Manual's Media Studio tab (its screens), when a test gives it one.
        tabs = SimpleNamespace(show=lambda window, home: self.manual and not self.tab_shown.append((window, home)),
                               active=lambda *args: False, in_manual=lambda window: self.manual,
                               tab=lambda kind: self.workspace)
        fakes = {"studio": studio, "studio_workspaces": tabs, "chat_toggle": chat_toggle}
        patches = [mock.patch.dict(sys.modules, {"angelo_agent." + name: fake for name, fake in fakes.items()}),
                   mock.patch.object(studio_toggle.bpy, "context", context, create=True),
                   mock.patch.object(studio_toggle, "is_studio", lambda area: area.studio),
                   mock.patch.object(studio_toggle, "open_studio", self.open_studio),
                   mock.patch.object(studio_toggle, "close_studio", self.close_studio),
                   mock.patch.object(studio_toggle, "shown_this_session", set()),
                   mock.patch.object(studio_toggle, "_select_tab_when_shown",
                                     lambda window, tab: self.tab_shown.append(("tab", tab)))]
        # ``from . import studio_workspaces`` reads the package's attribute when the package is loaded: in a full run
        # an earlier suite leaves the real modules there (whose ``in_manual`` imports bpy), so they are stood in too.
        package = sys.modules.get("angelo_agent")
        if package is not None:
            patches += [mock.patch.object(package, name, fake, create=True) for name, fake in fakes.items()]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        studio_toggle._opened.clear()
        self.addCleanup(studio_toggle._opened.clear)

    def open_studio(self, window):
        self.made = getattr(self, "made", 0) + 1
        created = Window(100 + self.made, [Area(1000 + self.made, studio=True)])
        self.windows.append(created)
        studio_toggle._opened.add(created.as_pointer())
        self.opened_from = window
        return created

    def close_studio(self, window):
        self.windows.remove(window)
        studio_toggle._opened.discard(window.as_pointer())
        self.closed.append(window.as_pointer())

    def test_opens_a_window_on_the_asked_tab(self):
        for kind, tab in (("3d", "3D"), ("image", "IMAGE")):
            created = studio_toggle.open_media_studio(kind)
            self.assertIs(self.opened_from, self.main)
            self.assertEqual(self.views[created.screen.areas[0].as_pointer()]["kind"], tab)

    def test_a_studio_it_opened_comes_forward_with_what_it_held(self):
        first = studio_toggle.open_media_studio("image")
        self.views[first.screen.areas[0].as_pointer()].update(prompt="a mossy rock", hits=[1])
        second = studio_toggle.open_media_studio("3d")
        self.assertEqual(self.closed, [first.as_pointer()])
        self.assertEqual([w.as_pointer() for w in self.windows], [1, second.as_pointer()])
        state = self.views[second.screen.areas[0].as_pointer()]
        self.assertEqual((state["kind"], state["prompt"]), ("3D", "a mossy rock"))
        self.assertNotIn("hits", state, "Hit areas belong to the old window's draw")

    def test_asked_from_the_studio_window_switches_its_tab_in_place(self):
        first = studio_toggle.open_media_studio("image")
        again = studio_toggle.open_media_studio("3d", first)
        self.assertIs(again, first)
        self.assertEqual(self.closed, [])
        self.assertEqual(self.views[first.screen.areas[0].as_pointer()]["kind"], "3D")

    def test_none_keeps_the_open_studios_tab(self):
        first = studio_toggle.open_media_studio("3d")
        again = studio_toggle.open_media_studio(None)
        self.assertEqual(self.closed, [first.as_pointer()], "Brought forward")
        self.assertEqual(self.views[again.screen.areas[0].as_pointer()]["kind"], "3D")

    def test_auto_reads_the_context_only_before_studio_first_shows(self):
        operator = studio_toggle.ANGELO_OT_open_media_studio()
        operator.report = lambda *args: None
        painting = SimpleNamespace(window=self.main, window_manager=SimpleNamespace(windows=self.windows),
                                   space_data=SimpleNamespace(type='VIEW_3D'), mode='PAINT_TEXTURE')
        operator.kind = 'AUTO'
        operator.execute(painting)
        studio = studio_toggle.opened_window(self.windows)
        self.assertEqual(self.views[studio.screen.areas[0].as_pointer()]["kind"], "IMAGE", "From where they came")
        studio_toggle.shown_this_session.add("window")   # open_studio notes it (stand-in here).
        self.views[studio.screen.areas[0].as_pointer()]["kind"] = "3D"   # The person picks 3D.
        operator.execute(painting)
        studio = studio_toggle.opened_window(self.windows)
        self.assertEqual(self.views[studio.screen.areas[0].as_pointer()]["kind"], "3D", "A general entry keeps it")
        operator.kind = 'IMAGE'
        operator.execute(painting)
        studio = studio_toggle.opened_window(self.windows)
        self.assertEqual(self.views[studio.screen.areas[0].as_pointer()]["kind"], "IMAGE", "A contextual one selects")

    def test_auto_reads_the_context_for_each_destinations_first_show(self):
        """1.0.5 review: from Object mode the first show is 3D in Director's window AND in Manual's tab, though the
        window showed first; each destination keeps its own tab afterwards."""
        operator = studio_toggle.ANGELO_OT_open_media_studio()
        operator.report = lambda *args: None
        modelling = SimpleNamespace(window=self.main, window_manager=SimpleNamespace(windows=self.windows),
                                    space_data=SimpleNamespace(type='VIEW_3D'), mode='OBJECT')
        studio_toggle.shown_this_session.add("window")   # Director's window showed first this session.
        with mock.patch.object(studio_toggle, "destination", lambda window: "tab"):
            self.manual = True
            operator.kind = 'AUTO'
            operator.execute(modelling)
        self.assertEqual(self.tab_shown[-1], ("tab", "3D"), "Manual's first show reads the context too")
        self.assertIn("tab", studio_toggle.shown_this_session)

    def test_manual_mode_uses_its_tab_and_counts_as_shown(self):
        self.manual = True
        shown = studio_toggle.open_media_studio("image")
        self.assertIs(shown, self.main, "Manual: its Media Studio tab, no new window")
        self.assertEqual(self.tab_shown, [(self.main, "studio"), ("tab", "IMAGE")])
        self.assertEqual(len(self.windows), 1)
        self.assertIn("tab", studio_toggle.shown_this_session, "AUTO keeps the tab's tab from now on")
        self.manual = False
        created = studio_toggle.open_media_studio("3d")
        self.assertIsNot(created, self.main, "Director: Media Studio's own window")

    def test_manual_selects_the_tab_before_its_first_frame(self):
        """The destination's Studio area holds the asked tab before the switch lands (no frame of the old tab);
        a screen another window shows keeps its own."""
        mine, theirs = Area(20, studio=True), Area(30, studio=True)
        other = Window(2, [theirs])
        self.windows.append(other)
        self.workspace = SimpleNamespace(screens=[SimpleNamespace(areas=[mine], as_pointer=lambda: 200),
                                                  SimpleNamespace(areas=[theirs], as_pointer=lambda: 300)])
        other.screen = self.workspace.screens[1]
        self.manual = True
        tabs, at_switch = sys.modules["angelo_agent.studio_workspaces"], []
        switch = tabs.show
        tabs.show = lambda window, home: at_switch.append(self.views.get(20, {}).get("kind")) or switch(window, home)
        studio_toggle.open_media_studio("3d")
        self.assertEqual(at_switch, ["3D"], "Selected before the switch, not after its first frame")
        self.assertGreater(mine.redraws, 0)
        self.assertNotIn(30, self.views, "Another window's Media Studio keeps its tab")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            studio_toggle.open_media_studio("video")
        self.assertEqual(len(self.windows), 1)


class PlacementTests(unittest.TestCase):
    HEADER = (0, 100, 1200, 30)     # x, y, w, h in window pixels: the shelf header above the shelf.
    VIEW = (0, 0, 1200, 800)        # The editor's main region (region overlap: under the shelf too).
    SIZE = (110, 26)

    def test_sits_on_the_shelf_top_edge_at_its_left_end(self):
        x, y, w, h = asset_shelf_add.place(self.HEADER, self.VIEW, [], self.SIZE, 1.0)
        self.assertEqual((x, y, w, h), (asset_shelf_add.MARGIN, 130 + asset_shelf_add.LIFT, 110, 26))

    def test_scales_with_the_interface(self):
        x, y, _w, _h = asset_shelf_add.place(self.HEADER, self.VIEW, [], (220, 52), 2.0)
        self.assertEqual((x, y), (2 * asset_shelf_add.MARGIN, 130 + 2 * asset_shelf_add.LIFT))

    def test_clears_the_toolbar_the_last_operation_panel_and_angelo(self):
        toolbar = (0, 130, 44, 600)
        hud = (40, 130, 200, 120)
        angelo = (420, 136, 20, 20)    # Not in the way: stays put once past the panel.
        x, *_ = asset_shelf_add.place(self.HEADER, self.VIEW, [hud, toolbar, angelo], self.SIZE, 1.0)
        self.assertEqual(x, 240 + asset_shelf_add.MARGIN)
        angelo = (250, 136, 76, 76)    # In the way past the panel: Angelo on the chat's left side.
        x, *_ = asset_shelf_add.place(self.HEADER, self.VIEW, [hud, toolbar, angelo], self.SIZE, 1.0)
        self.assertEqual(x, 326 + asset_shelf_add.MARGIN)

    def test_nowhere_when_it_does_not_fit(self):
        self.assertIsNone(asset_shelf_add.place((0, 100, 100, 30), self.VIEW, [], self.SIZE, 1.0))
        self.assertIsNone(asset_shelf_add.place(self.HEADER, (0, 0, 1200, 150), [], self.SIZE, 1.0))

    def test_two_pills_labelled_where_they_fit_else_icons_only(self):
        widths = {"studio": 80, "flows": 110}
        rects, labelled = asset_shelf_add.layout(self.HEADER, self.VIEW, [], widths, 1.0)
        self.assertTrue(labelled)
        (first, a), (second, b) = rects
        self.assertEqual((first, second), ("studio", "flows"), "Media Studio, then Generation Flows")
        self.assertEqual(b[0] - (a[0] + a[2]), asset_shelf_add.BETWEEN)
        self.assertEqual(a[2], asset_shelf_add.pill_width(80, 1.0, True))
        narrow = (0, 100, 200, 30)   # Room for two icons, not two labels: icons only, never an ellipsis.
        rects, labelled = asset_shelf_add.layout(narrow, self.VIEW, [], widths, 1.0)
        self.assertFalse(labelled)
        self.assertEqual(rects[0][1][2], (asset_shelf_add.PAD * 2 + asset_shelf_add.ICON))
        self.assertIsNone(asset_shelf_add.layout((0, 100, 60, 30), self.VIEW, [], widths, 1.0))

    def test_pill_hit_testing_is_per_window_and_pill(self):
        asset_shelf_add.places.clear()
        self.addCleanup(asset_shelf_add.places.clear)
        asset_shelf_add.places[7] = (1, (("studio", (10, 20, 100, 28)), ("flows", (116, 20, 120, 28))), True, "3d")
        self.assertEqual(asset_shelf_add.pill_at(1, 50, 30), (7, "studio"))
        self.assertEqual(asset_shelf_add.pill_at(1, 200, 30), (7, "flows"))
        self.assertIsNone(asset_shelf_add.pill_at(1, 113, 30), "The gap between them is nobody's")
        self.assertIsNone(asset_shelf_add.pill_at(2, 50, 30))


class SpecTests(unittest.TestCase):
    """doc/angelo/studio_entry_points.rst: section 1 (vocabulary), 2 (one icon per concept), 4 (pill tokens)."""

    def test_two_pills_named_as_the_spec_never_add_asset(self):
        self.assertEqual(asset_shelf_add.PILLS, ("studio", "flows"))
        self.assertEqual(asset_shelf_add.LABELS, {"studio": "Media Studio", "flows": "Generation Flows"})
        self.assertEqual(asset_shelf_add.TIPS, {
            "studio": "Media Studio: generate images and 3D models in one step",
            "flows": "Generation Flows: go further, chain generation steps into a flow you can rerun"})
        module = (ADDON / "asset_shelf_add.py").read_text(encoding="utf-8")
        self.assertNotIn('n_("Add asset")', module)
        self.assertNotIn('n_("Generate")', module)

    def test_the_pills_draw_each_concepts_icon_and_the_pill_tokens(self):
        module = (ADDON / "asset_shelf_add.py").read_text(encoding="utf-8")
        self.assertIn("studio_toggle.frame_icon(False", module)
        self.assertIn("flow_open.flow_icon(", module)
        self.assertEqual((asset_shelf_add.HEIGHT, asset_shelf_add.PAD, asset_shelf_add.ICON, asset_shelf_add.GAP,
                          asset_shelf_add.TEXT_SIZE, asset_shelf_add.BETWEEN), (28.0, 12.0, 16.0, 8.0, 12.0, 6.0))
        self.assertIn("kit.TEAL_SOFT", module, "Pressed")
        self.assertNotIn("kit.TEAL,", module, "Never teal-filled")

    def test_media_studio_wears_the_shared_tokens(self):
        """Section 4: studio.py's palette is card_kit/ui_tokens, one for one; no literal colour in its window."""
        import re
        core = (ADDON / "studio.py").read_text(encoding="utf-8")
        for line in ("BG = kit.CARD", "PANEL = kit.PANEL", "FIELD = kit.CARD", "LINE = kit.LINE",
                     "HOVER = kit.PANEL_HOVER", "ACCENT = kit.TEAL", "ACCENT_HOVER = kit.TEAL_HOVER",
                     "ACCENT_PRESSED = kit.TEAL_PRESSED",
                     "ACCENT_TEXT = kit.INK", "FOCUS = kit.FOCUS", "SELECTED = kit.TEAL_SOFT", "TEXT = kit.TEXT",
                     "MUTED = kit.MUTED", "GREEN = tokens.SUCCESS", "ERROR = tokens.DANGER", "SCRIM = kit.SCRIM"):
            name, value = line.split(" = ")
            self.assertRegex(core, r"(?m)^" + name + r" = " + re.escape(value) + r"\b", line)
        literal = re.compile(r"\(\s*(?:0|1)?\.\d+\s*,\s*(?:0|1)?\.?\d*\s*,\s*(?:0|1)?\.?\d*\s*(?:,\s*[\d.]+\s*)?\)")
        for name in ("studio.py", "studio_draw.py", "studio_input.py", "studio_ops.py", "studio_asset_ops.py"):
            text = (ADDON / name).read_text(encoding="utf-8")
            self.assertEqual(literal.findall(text), [], name)
            self.assertNotIn("PURPLE", text, name)
        draw = (ADDON / "studio_draw.py").read_text(encoding="utf-8")
        self.assertIn('FOCUS if state["focus"] == key else LINE', draw, "The focus outline is FOCUS, not the fill")

    def test_the_operator_is_searchable_as_media_studio(self):
        op = studio_toggle.ANGELO_OT_open_media_studio
        self.assertEqual(op.bl_label, "Media Studio")
        self.assertNotIn("INTERNAL", getattr(op, "bl_options", set()))
        self.assertEqual(op.bl_description, "Media Studio: generate images and 3D models in one step")


class ObjectShelfTests(unittest.TestCase):
    """``media_shelf``: Object mode's shelf of models and materials, and the person's open/closed choice."""

    def test_lists_models_and_materials_in_object_mode(self):
        shelf = media_shelf.ANGELO_AST_object_assets
        self.assertTrue(shelf.poll(SimpleNamespace(mode='OBJECT')))
        for mode in ('SCULPT', 'PAINT_TEXTURE', 'EDIT_MESH'):
            self.assertFalse(shelf.poll(SimpleNamespace(mode=mode)), mode)
        for kind, listed in (('OBJECT', True), ('COLLECTION', True), ('MATERIAL', True), ('BRUSH', False),
                             ('NODETREE', False), ('WORLD', False)):
            self.assertEqual(shelf.asset_poll(SimpleNamespace(id_type=kind)), listed, kind)
        self.assertIn('DEFAULT_VISIBLE', shelf.bl_options)
        self.assertIn('STORE_ENABLED_CATALOGS_IN_PREFERENCES', shelf.bl_options, "An added tab stays for every file")
        self.assertNotIn('NO_ASSET_DRAG', shelf.bl_options, "Blender's drop boxes place and assign")

    def test_only_a_settled_change_is_the_persons_choice(self):
        self.assertIsNone(media_shelf.change(None, True, True), "First sight is not a choice")
        self.assertIsNone(media_shelf.change(True, True, True))
        self.assertIsNone(media_shelf.change(True, False, False), "Still switching shelves after a mode change")
        self.assertIs(media_shelf.change(True, False, True), False)
        self.assertIs(media_shelf.change(False, True, True), True)

    def test_the_choice_round_trips_through_the_state_folder(self):
        import tempfile
        folder = Path(tempfile.mkdtemp())
        with mock.patch.object(media_shelf, "_path", lambda: folder / media_shelf.FILE):
            self.assertIsNone(media_shelf.remembered())
            for choice in media_shelf.STATES:
                media_shelf.remember(choice)
                self.assertEqual(media_shelf.remembered(), choice)
            (folder / media_shelf.FILE).write_text("not json", encoding="utf-8")
            self.assertIsNone(media_shelf.remembered(), "A damaged file is Blender's default, never an error")

    def test_director_never_counts(self):
        module = (ADDON / "media_shelf.py").read_text(encoding="utf-8")
        self.assertIn("director_layout.is_director(window)", module)

    CATALOGS = {"3d": "Models", "image": "Images"}

    def test_the_library_catalogs_are_read_by_their_fixed_ids(self):
        import tempfile
        folder = Path(tempfile.mkdtemp())
        (folder / "blender_assets.cats.txt").write_text(
            "# comment\nVERSION 1\n\n5c0e3e1a-7d0b-4c8e-9a51-0f6a2d3b7e11:Modelle:Modelle\n"
            "9b2f6c47-1e83-4d5a-b7c2-3a8e4f1d6c22:Bilder:Bilder\nabc:Mine/Rocks:Mine-Rocks\n", encoding="utf-8")
        assets = types.ModuleType("angelo_agent.studio_assets")
        assets.CATALOGS = {"3D": ("5c0e3e1a-7d0b-4c8e-9a51-0f6a2d3b7e11", "Models", "models"),
                           "IMAGE": ("9b2f6c47-1e83-4d5a-b7c2-3a8e4f1d6c22", "Images", "images")}
        assets.CATS_FILE = "blender_assets.cats.txt"
        assets.asset_directory = lambda root: folder
        state = types.ModuleType("angelo_agent.studio_state")
        state.library_directory = lambda: folder
        import angelo_agent   # ``from . import x`` finds a submodule already imported as the package's attribute.
        with mock.patch.dict(sys.modules, {"angelo_agent.studio_assets": assets, "angelo_agent.studio_state": state}), \
                mock.patch.object(angelo_agent, "studio_assets", assets, create=True), \
                mock.patch.object(angelo_agent, "studio_state", state, create=True):
            self.assertEqual(media_shelf.studio_catalogs(), {"3d": "Modelle", "image": "Bilder"})
        missing = types.ModuleType("angelo_agent.studio_assets")   # Before the library exists: no catalogue file.
        missing.asset_directory = lambda root: folder / "absent"
        missing.CATS_FILE, missing.CATALOGS = assets.CATS_FILE, assets.CATALOGS
        with mock.patch.object(angelo_agent, "studio_assets", missing, create=True), \
                mock.patch.object(angelo_agent, "studio_state", state, create=True):
            self.assertEqual(media_shelf.studio_catalogs(), {}, "Before the library exists: nothing to offer")

    def test_the_angelo_media_studio_parent_is_the_one_tab_when_the_library_has_it(self):
        with mock.patch.object(media_shelf, "studio_tab_path", lambda: "Angelo Media Studio"), \
                mock.patch.object(media_shelf, "studio_catalogs", lambda: dict(self.CATALOGS)):
            self.assertEqual(media_shelf.tab_catalogs(), {"studio": "Angelo Media Studio"})
        with mock.patch.object(media_shelf, "studio_tab_path", lambda: None), \
                mock.patch.object(media_shelf, "studio_catalogs", lambda: dict(self.CATALOGS)):
            self.assertEqual(media_shelf.tab_catalogs(), self.CATALOGS, "A library from before the parent")

    def test_the_catalog_tab_shown_picks_the_studio_tab(self):
        kind = media_shelf.catalog_kind
        self.assertEqual(kind("Images", self.CATALOGS), "image")
        self.assertEqual(kind("Images/Skies", self.CATALOGS), "image")
        self.assertEqual(kind("Models", self.CATALOGS), "3d")
        self.assertIsNone(kind("Images-old", self.CATALOGS), "A path prefix is not a sub-catalog")
        self.assertIsNone(kind("", self.CATALOGS), "The All tab: the mode decides")
        # With Part E's catalogue (a fixed parent holding Models and Images), its own catalog_kind names the kind;
        # before it, the flat catalogs do.
        from angelo_agent import studio_assets
        parent = getattr(studio_assets, "TAB_PATH", None) if hasattr(studio_assets, "catalog_kind") else None
        shelf = SimpleNamespace(active_catalog_path=f"{parent}/Images" if parent else "Images")
        with mock.patch.object(media_shelf, "_shelf", lambda w, a: shelf), \
                mock.patch.object(media_shelf, "studio_catalogs", lambda: dict(self.CATALOGS)):
            self.assertEqual(media_shelf.shelf_kind(None, None), "image")
            shelf.active_catalog_path = parent or ""
            self.assertIsNone(media_shelf.shelf_kind(None, None), "the parent (or All) tab: the mode decides")

    def test_each_catalog_is_offered_once_and_a_renamed_one_moves(self):
        tabs = media_shelf.tabs_to_offer
        self.assertEqual(tabs(self.CATALOGS, None, set()),
                         (["Models", "Images"], [], {"3d": "Models", "image": "Images"}), "Models, then Images")
        self.assertEqual(tabs(self.CATALOGS, dict(self.CATALOGS), set())[:2], ([], []), "Removed stays removed")
        german = {"3d": "Modelle", "image": "Bilder"}
        enable, disable, now = tabs(german, dict(self.CATALOGS), {"Models"})
        self.assertEqual((enable, disable, now), (["Modelle"], ["Models"], german), "The kept tab moves; not Images")

    def test_offering_changes_the_shelf_and_remembers(self):
        import tempfile
        folder = Path(tempfile.mkdtemp())
        enabled = set()
        shelf = SimpleNamespace(is_catalog_enabled=lambda path: path in enabled, enable_catalog=enabled.add,
                                disable_catalog=enabled.discard)
        area = SimpleNamespace(tag_redraw=lambda: None)
        with mock.patch.object(media_shelf, "_path", lambda: folder / media_shelf.FILE), \
                mock.patch.object(media_shelf, "_shelf", lambda window, area: shelf), \
                mock.patch.object(media_shelf, "tab_catalogs", lambda: dict(self.CATALOGS)):
            self.assertTrue(media_shelf.offer_tab(None, area))
            self.assertEqual(enabled, {"Models", "Images"})
            enabled.clear()   # The person removes both tabs.
            media_shelf.offer_tab(None, area)
            self.assertEqual(enabled, set(), "Never again")

    def test_not_offered_before_the_library_or_on_a_build_without_the_hook(self):
        import tempfile
        folder = Path(tempfile.mkdtemp())
        with mock.patch.object(media_shelf, "_path", lambda: folder / media_shelf.FILE):
            with mock.patch.object(media_shelf, "_shelf", lambda w, a: SimpleNamespace()), \
                    mock.patch.object(media_shelf, "tab_catalogs", lambda: dict(self.CATALOGS)):
                self.assertFalse(media_shelf.offer_tab(None, SimpleNamespace()))
            with mock.patch.object(media_shelf, "tab_catalogs", lambda: {}):
                self.assertFalse(media_shelf.offer_tab(None, SimpleNamespace()))
            self.assertIsNone(media_shelf._stored().get(media_shelf.OFFERED), "Offered later, once it can be")

    def test_show_my_assets_needs_an_object_mode_view_and_the_library(self):
        view = SimpleNamespace(type='VIEW_3D', width=10, height=10)
        window = SimpleNamespace(screen=SimpleNamespace(areas=[view]),
                                 view_layer=SimpleNamespace(objects=SimpleNamespace(active=SimpleNamespace(mode='SCULPT'))))
        with mock.patch.object(media_shelf, "library_name", lambda: "Angelo Media Studio"):
            self.assertFalse(media_shelf.show_studio_tab(window), "Not in Sculpt mode: Object mode's shelf only")
        window.view_layer.objects.active.mode = 'OBJECT'
        with mock.patch.object(media_shelf, "library_name", lambda: None):
            self.assertFalse(media_shelf.show_studio_tab(window), "No library (removed): nothing to show")


class WiringTests(unittest.TestCase):
    def test_studios_top_row_hook_matches_the_pill_it_calls(self):
        """studio.top_row_end calls studio_to_flow.draw_pill with the arguments that function takes (a mismatch
        raised inside Studio's draw and left the window blank below its tabs: preview line on d3055f1f64e)."""
        import ast
        module = ADDON / "studio_to_flow.py"
        if not module.exists():
            self.skipTest("Part A's studio_to_flow is not on this branch")
        tree = ast.parse(module.read_text(encoding="utf-8"))
        pill = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "draw_pill")
        self.assertEqual([a.arg for a in pill.args.args], ["state", "width", "height", "canvas"])
        core = (ADDON / "studio.py").read_text(encoding="utf-8")
        self.assertIn("studio_to_flow.draw_pill(state, width, height, canvas=canvas)", core)
        turn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "turn_into_flow")
        self.assertEqual([a.arg for a in turn.args.args], ["context", "state"])
        self.assertIn("studio_to_flow.turn_into_flow(context, state)", core)

    def test_its_input_is_a_passive_canvas_input(self):
        self.assertIn(asset_shelf_add.IDENTITY, input_policy.CANVAS_INPUTS)
        self.assertEqual(asset_shelf_add.ANGELO_OT_asset_shelf_add_input.bl_idname, asset_shelf_add.IDENTITY)

    def test_registered_with_the_add_on_and_drawn_on_every_shelf_editor(self):
        source = (ADDON / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("asset_shelf_add.register()", source)
        self.assertIn("asset_shelf_add.unregister()", source)
        module = (ADDON / "asset_shelf_add.py").read_text(encoding="utf-8")
        for space in ("SpaceView3D", "SpaceImageEditor"):
            self.assertIn(space, module)
        self.assertNotIn("VIEW3D_AST_", module, "The pill follows shelf regions, never a list of shelf classes")

    def test_the_click_goes_through_the_one_opener(self):
        module = (ADDON / "asset_shelf_add.py").read_text(encoding="utf-8")
        self.assertIn("angelo.open_media_studio", module)
        self.assertEqual(studio_toggle.ANGELO_OT_open_media_studio.bl_idname, "angelo.open_media_studio")


if __name__ == "__main__":
    unittest.main()
