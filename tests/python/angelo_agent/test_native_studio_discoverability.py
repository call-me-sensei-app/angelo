# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio and Generation Flows are discoverable (1.0.5), on a stand-in ``bpy``.

Before 1.0.5 Media Studio was reachable only through the editor-type menu, which Director never shows, and nothing in
the welcome or the empty chat named it. Covered: the Studio button's icon and which window it toggles
(``studio_toggle``); where it is drawn (Director's top bar beside Chat, the Angelo menu in both modes); the welcome's
Media Studio slide opens it and the welcome fits, in English, German and French (the real catalogues). The empty chat's generation suggestions are
``test_native_first_run``'s; the running app's proof is ``test_blender_studio_discoverability``.
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
sys.path.insert(0, str(ROOT / "scripts/angelo"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object, Menu=object)
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)   # Any property type: a placeholder.
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text))
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}


with mock.patch.dict(sys.modules, fake_bpy()):
    from angelo_agent import flow_starters, studio_toggle, welcome_screen


def area(kind, width, height, language=None):
    space = SimpleNamespace(language=language)
    return SimpleNamespace(type=kind, ui_type=kind, width=width, height=height, spaces=SimpleNamespace(active=space),
                           regions=[SimpleNamespace(type='WINDOW')])


def window(pointer, areas):
    return SimpleNamespace(as_pointer=lambda: pointer, screen=SimpleNamespace(areas=areas))


class IconTests(unittest.TestCase):
    def test_filled_while_open_outlined_while_closed_with_the_spark_in_both(self):
        size = studio_toggle.ICON_SIZE
        filled, outline = studio_toggle.frame_icon(True), studio_toggle.frame_icon(False)
        self.assertEqual(len(filled), size * size * 4)
        coverage = [sum(pixels[3::4]) for pixels in (filled, outline)]
        self.assertGreater(coverage[0], coverage[1] * 1.5, "Open reads heavier at a glance, as Chat's bubble does")
        spark = ((size - 7) * size + (size - 7)) * 4 + 3   # The spark's centre pixel's alpha.
        self.assertEqual((filled[spark], outline[spark]), (1.0, 1.0))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in filled + outline))


class WindowTests(unittest.TestCase):
    def setUp(self):
        studio_toggle._opened.clear()
        self.addCleanup(studio_toggle._opened.clear)

    def test_only_a_window_it_opened_is_toggled_and_a_closed_one_is_forgotten(self):
        studio = window(2, [area("CONSOLE", 900, 700, language="angelo_genai")])
        mine = window(3, [area("CONSOLE", 900, 700, language="angelo_genai")])
        main = window(1, [area("VIEW_3D", 1200, 800)])
        studio_toggle._opened.add(3)
        self.assertIs(studio_toggle.opened_window([main, studio, mine]), mine,
                      "A Studio the person placed in a layout is never closed from the button")
        self.assertIsNone(studio_toggle.opened_window([main, studio]), "Closed by the person: forgotten")
        self.assertEqual(studio_toggle._opened, set())

    def test_a_window_no_longer_showing_the_studio_is_forgotten(self):
        studio_toggle._opened.add(3)
        repurposed = window(3, [area("VIEW_3D", 900, 700)])
        self.assertIsNone(studio_toggle.opened_window([repurposed]))

    def test_the_largest_3d_view_is_copied_never_a_bar(self):
        small, large = area("VIEW_3D", 400, 300), area("VIEW_3D", 1200, 800)
        bar, chat = area("TOPBAR", 3000, 40), area("CONSOLE", 2000, 900, language="angelo")
        self.assertIs(studio_toggle.source_area(window(1, [bar, small, chat, large])), large)
        self.assertIs(studio_toggle.source_area(window(1, [bar, chat])), chat, "No 3D view: the largest editor")
        self.assertIsNone(studio_toggle.source_area(window(1, [bar])))


class PlacementTests(unittest.TestCase):
    def test_studio_and_flows_beside_chat_in_both_modes_top_bar_and_in_the_angelo_menu(self):
        """Both modes reach both directly (maintainer, 1.0.5): no editor-type menu needed; compact when tight."""
        topbar = (ADDON / "director.py").read_text().split("\ndef draw_topbar(", 1)[1].split("\ndef ", 1)[0]
        chat = topbar.index("chat_toggle.draw(self.layout, context, level >= top_bar_fit.CHAT_SHORT")
        studio = topbar.index("studio_toggle.draw(self.layout, context, level >= top_bar_fit.STUDIO_ICONS)")
        flows = topbar.index("flow_open.draw_top_bar(self.layout, context, level >= top_bar_fit.STUDIO_ICONS)")
        self.assertLess(chat, studio, "Studio follows Chat")
        self.assertLess(studio, flows, "Flows follows Studio")
        self.assertNotIn("if active:", topbar[chat:flows], "the same buttons in Director and Manual")
        self.assertIn("top_bar_fit.compact(context, active,", topbar[:chat])
        source = (ADDON / "top_bar_fit.py").read_text()
        self.assertIn("FULL, CHAT_SHORT, NO_VIEW_LAYER, STUDIO_ICONS, CHAT_ICON = range(5)", source,
                      "less important items give way first; Studio and Flows only lose their labels")
        source = (ADDON / "studio_toggle.py").read_text()
        entries = source.split("\ndef menu_entries(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn(".mode = 'OPEN'", entries, "A menu entry opens; only the button toggles")
        self.assertIn(".kind = 'AUTO'", entries, "the shared opener when it is registered")
        self.assertIn('layout.menu("ANGELO_MT_flows"', entries)
        self.assertIn('menu_entries(self.layout, "TOPBAR_MT_blender")', source.split("\ndef menu_item(", 1)[1])
        window = (ADDON / "director.py").read_text().split("\ndef window_menu(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('studio_toggle.menu_entries(self.layout, "TOPBAR_MT_window")', window, "Window > Media Studio")
        self.assertIn("bpy.types.TOPBAR_MT_blender.prepend(menu_item)", source)
        self.assertIn("bpy.types.TOPBAR_MT_blender.remove(menu_item)", source)
        init = (ADDON / "__init__.py").read_text()
        self.assertIn("studio_toggle.register()", init)
        self.assertIn("studio_toggle.unregister()", init)

    def test_it_opens_a_window_and_never_rearranges_the_layout(self):
        source = (ADDON / "studio_toggle.py").read_text()
        window = source.split("\ndef new_window(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("bpy.ops.screen.area_dupli('INVOKE_DEFAULT')", window)
        opening = source.split("\ndef open_studio(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("created = new_window(window)", opening)
        self.assertIn("area = created.screen.areas[0]\n    select_console(created, area, generative=True)", opening)
        self.assertIn("area.spaces.active.show_region_header = False", opening, "No stock header in its own window")
        self.assertNotIn("area_close", source.split("\ndef close_studio(", 1)[0], "Only its own window is closed")


class FlowTests(unittest.TestCase):
    def test_each_template_is_a_valid_flow_of_todays_node_types(self):
        from angelo_agent import flow_spec
        ids = [entry["id"] for entry in flow_starters.STARTERS]
        self.assertEqual(ids, ["image_to_3d", "image_reference_3d"])
        for entry in flow_starters.STARTERS:
            flow = flow_starters.instantiate(entry["id"])
            self.assertTrue(set(node["type"] for node in flow["nodes"]) <= set(flow_spec.NODE_TYPES))
            self.assertEqual(flow["nodes"][0]["type"], "IMAGE", "starts from the person's image")
            self.assertEqual(flow["nodes"][-1]["type"], "PLACE_IN_SCENE", "ends in the scene")
            self.assertFalse(any(node.get("connection_id") for node in flow["nodes"]), "names no account")
        flow = flow_starters.instantiate("image_to_3d")
        flow["nodes"].clear()
        self.assertTrue(flow_starters.instantiate("image_to_3d")["nodes"], "the table is never edited")
        with self.assertRaises(KeyError):
            flow_starters.instantiate("nothing")

    def test_flows_open_in_their_own_window_reused_for_the_next(self):
        source = (ADDON / "flow_open.py").read_text()
        show = source.split("\ndef show(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("target = flow_window(bpy.context.window_manager.windows)", show)
        self.assertIn("studio_toggle.new_window(window, NOT_OPENED)", show)
        self.assertIn("area.ui_type = flow_nodes.TREE", show)
        starter = source.split("\ndef from_starter(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("flow_tree.write_flow(tree, flow)", starter)
        menu = source.split("class ANGELO_MT_flows", 1)[1].split("\ndef ", 1)[0]
        for needed in ("NEW", "TEMPLATES", "flow_starters.STARTERS", "YOURS"):
            self.assertIn(needed, menu)


class View:
    """Measures as a proportional font roughly does; wraps greedily (enough to compare layouts)."""
    _font = _mono = 0

    def measure(self, text, size, font):
        return len(text) * size * 0.55

    def elide(self, text, width, size, font=0):
        return text

    def wrap(self, text, width, size, font):
        lines, line = [], ""
        for word in text.split():
            trial = (line + " " + word).strip()
            if line and self.measure(trial, size, font) > width:
                lines.append((line, 0))
                line = word
            else:
                line = trial
        return lines + [(line, 0)]


def catalogue(code):
    from i18n import po
    if code == "en":
        return {}
    _, entries = po.read(str(ROOT / "locale/angelo/po" / (code + ".po")))
    return {(entry.ctxt, entry.id): entry.str for entry in entries if entry.str}


class WelcomeCardTests(unittest.TestCase):
    """The welcome's Media Studio entry is its slideshow's second slide since 1.0.5 (``welcome_slides``; the
    slideshow's own rules are ``test_native_welcome_slides``)."""

    def plan(self, code, width=1100, height=860, slide=1):
        table = catalogue(code)

        def iface(text, context=""):
            return table.get((context or "", text)) or table.get(("", text)) or text
        patches = [mock.patch.object(welcome_screen, "iface_", iface),
                   mock.patch.object(welcome_screen.welcome_slides, "iface_", iface),
                   mock.patch.object(welcome_screen, "shortcut_keys", lambda: ["Ctrl", "Alt", "M"])]
        for patch in patches:
            patch.start()
        try:
            return welcome_screen.layout(View(), width, height, 1.0, slide=slide)
        finally:
            for patch in patches:
                patch.stop()

    def test_the_media_studio_slide_opens_media_studio(self):
        plan = self.plan("en")
        (rect,) = [rect for rect, action, value in plan["hits"] if (action, value) == ("slide_try", "studio")]
        x, y, w, h = plan["card"]
        self.assertTrue(x <= rect[0] and rect[0] + rect[2] <= x + w and y <= rect[1] and rect[1] + rect[3] <= y + h)
        intro = (ADDON / "intro.py").read_text()
        self.assertIn('if action == "slide_try":\n            welcome_slides.open_feature(context.window, value)', intro)
        slides = (ADDON / "welcome_slides.py").read_text()
        self.assertIn('"studio": _open_studio', slides)

    def test_fits_in_english_german_and_french(self):
        for code in ("en", "de", "fr"):
            for width, height in ((1100, 860), (900, 700)):
                with self.subTest(code=code, size=(width, height)):
                    plan = self.plan(code, width, height)
                    self.assertLessEqual(plan["height"], height - 32 * plan["scale"])

    def test_the_switch_line_and_its_keys_sit_above_the_slideshow(self):
        for code in ("en", "de", "fr"):
            plan = self.plan(code)
            box = plan["slides"]
            keys = [op[1] for op in plan["ops"] if op[0] == "keycap"]
            self.assertTrue(keys, code)
            for rect in keys:
                self.assertGreater(rect[1], box[1] + box[3], code)


if __name__ == "__main__":
    unittest.main()
