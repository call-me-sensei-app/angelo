# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's window chrome: the title bar and the top bar carry the wordmark's darkest teal.

Dependency-free: reads the theme sources, the GHOST window code and the widget
drawing code. What the compiled app draws is ``test_blender_window_chrome.py``.
"""

import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import window_chrome  # noqa: E402

ROOT = window_chrome.ROOT
CHROME = window_chrome.chrome()
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
DEFAULT_THEME = ROOT / "release/datafiles/userdef/userdef_default_theme.c"
PRESETS = ROOT / "scripts/presets/interface_theme"
GHOST = ROOT / "intern/ghost/intern"


def default_theme_block(name):
    """The members of one ``.name = {...}`` block of the compiled default theme, as ``{member: "rrggbbaa"}``."""
    source = DEFAULT_THEME.read_text(encoding="utf-8")
    block = re.search(r"^ +\.%s = \{\n(.*?)^ +\}," % re.escape(name), source, re.M | re.S).group(1)
    return dict(re.findall(r"\.(\w+) = RGBA\(0x([0-9a-fA-F]{8})\)", block))


def light_theme():
    return ET.parse(PRESETS / "Angelo_Light.xml").getroot()


def light_space(theme, name):
    return theme.find(".//%s//ThemeSpaceGeneric" % name).attrib


class WindowChromeTests(unittest.TestCase):
    def test_chrome_is_the_as_darkest_facet_at_the_same_hue(self):
        facet = window_chrome.darkest_facet()
        self.assertEqual(window_chrome.hex_color(facet), "#087787")
        self.assertEqual(facet, window_chrome.facets()[-1], "The right foot is the darkest facet")
        self.assertEqual(window_chrome.hex_color(CHROME), "#076573")
        for channel, source in zip(CHROME, facet):
            self.assertAlmostEqual(channel, source * window_chrome.TINT, delta=0.5)

    def test_default_theme_colours_only_the_top_bar(self):
        self.assertEqual(default_theme_block("space_topbar")["header"][:6], window_chrome.hex_color(CHROME)[1:])
        # The status bar and the editors keep Blender's headers.
        source = DEFAULT_THEME.read_text(encoding="utf-8")
        spaces = re.findall(r"^  \.(space_\w+) = \{", source, re.M)
        self.assertGreater(len(spaces), 15)
        for space in spaces:
            if space != "space_topbar":
                with self.subTest(space=space):
                    self.assertNotEqual(window_chrome.rgb(default_theme_block(space)["header"]), CHROME)
        self.assertEqual(default_theme_block("space_statusbar")["header"], "181818b3")
        self.assertEqual(default_theme_block("space_view3d")["header"], "303030b3")

    def test_bundled_presets_carry_the_chrome_and_nothing_else_is_tinted(self):
        # "Angelo Dark" is an empty preset: applying it restores the compiled default above.
        dark = ET.parse(PRESETS / "Angelo_Dark.xml").getroot()
        self.assertEqual([child.tag for child in dark], ["Theme", "ThemeStyle"])
        self.assertEqual(list(dark.find("Theme")), [])
        self.assertEqual(sorted(path.name for path in PRESETS.glob("*.xml")), ["Angelo_Dark.xml", "Angelo_Light.xml"])
        light = light_theme()
        self.assertEqual(light_space(light, "topbar")["header"], window_chrome.hex_color(CHROME) + "ff")
        headers = [space.get("header") for space in light.iter("ThemeSpaceGeneric")]
        self.assertEqual(sum(window_chrome.rgb(header) == CHROME for header in headers), 1)
        self.assertEqual(light_space(light, "statusbar")["header"], "#999999ff")

    def test_text_on_the_chrome_reaches_wcag_aa_in_the_default_theme(self):
        pulldown = default_theme_block("wcol_pulldown")
        topbar = default_theme_block("space_topbar")
        on_chrome = {
            "menu titles (pulldown text)": window_chrome.rgb(pulldown["text"]),
            "labels (top bar header text)": window_chrome.rgb(topbar["header_text"]),
            "borderless buttons (regular text)": window_chrome.rgb(default_theme_block("wcol_regular")["text"]),
            "Windows title text": WHITE,
            # AppKit draws the active window's title in its label colour: white at 90% in the dark
            # appearance on macOS 26.3 (read from the app); 85% is the lower, safer figure.
            "macOS title text": window_chrome.over(WHITE, 0.85, CHROME),
        }
        for name, color in on_chrome.items():
            with self.subTest(text=name):
                self.assertGreaterEqual(window_chrome.contrast(color, CHROME), window_chrome.READABLE)
        # Hover: the pulldown's highlight is drawn over the bar, under its highlighted text.
        highlight = pulldown["inner_sel"]
        hovered = window_chrome.over(window_chrome.rgb(highlight), int(highlight[6:], 16) / 255, CHROME)
        self.assertGreaterEqual(window_chrome.contrast(window_chrome.rgb(pulldown["text_sel"]), hovered), window_chrome.READABLE)
        # The exact facet colour would not carry the same text, which is why the chrome is darker.
        facet = window_chrome.darkest_facet()
        self.assertLess(window_chrome.contrast(window_chrome.rgb(pulldown["text"]), facet), window_chrome.READABLE)
        self.assertLess(window_chrome.contrast(window_chrome.over(WHITE, 0.85, facet), facet), window_chrome.READABLE)

    def test_light_theme_relies_on_the_top_bars_own_header_text(self):
        light = light_theme()
        pulldown = light.find(".//wcol_pulldown/ThemeWidgetColors").attrib
        header_text = window_chrome.rgb(light_space(light, "topbar")["header_text"])
        # The dark pulldown text every light header shares cannot be read on the chrome ...
        self.assertLess(window_chrome.contrast(window_chrome.rgb(pulldown["text"]), CHROME), window_chrome.READABLE)
        # ... so the top bar's rule falls back to its header text, which can.
        self.assertGreaterEqual(window_chrome.contrast(header_text, CHROME), window_chrome.READABLE)
        widgets = (ROOT / "source/blender/editors/interface/interface_widgets.cc").read_text(encoding="utf-8")
        rule = widgets.split("static void widget_text_ensure_readable_on_topbar(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("theme_state.spacetype != SPACE_TOPBAR", rule, "Other editors and popups are never touched")
        self.assertIn("constexpr float readable_contrast = 4.5f;", rule)
        self.assertIn("theme::get_color_3ubv(TH_HEADER_TEXT, header_text);", rule)
        self.assertIn("contrast < readable_contrast && widget_color_contrast_ratio(header_text, back) > contrast", rule,
                      "A theme whose text already reads, or whose header text is no better, is drawn as before")
        self.assertEqual(widgets.count("widget_text_ensure_readable_on_topbar(but, wt);"), 1)

    def test_title_bar_text_is_chosen_by_luminance_on_macos_and_windows(self):
        base = (GHOST / "GHOST_Window.cc").read_text(encoding="utf-8")
        rule = base.split("bool GHOST_Window::coloredTitleBarNeedsLightText() const", 1)[1].split("\n}\n", 1)[0]
        for weight in ("0.2126f", "0.7152f", "0.0722f", "12.92f", "2.4f"):
            self.assertIn(weight, rule)
        self.assertIn("(offset * offset) <= (1.05f * 0.05f)", rule)
        cocoa = (GHOST / "GHOST_WindowCocoa.mm").read_text(encoding="utf-8")
        apply = cocoa.split("GHOST_TSuccess GHOST_WindowCocoa::applyWindowDecorationStyle()", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("coloredTitleBarNeedsLightText() ?", apply)
        self.assertNotIn("hsv_v", apply, "The HSV value calls a saturated dark colour light")
        windows = (GHOST / "GHOST_WindowWin32.cc").read_text(encoding="utf-8")
        apply = windows.split("GHOST_TSuccess GHOST_WindowWin32::applyWindowDecorationStyle()", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("constexpr DWORD caption_color_attr = 35;", apply)
        self.assertIn("constexpr DWORD text_color_attr = 36;", apply)
        self.assertIn("coloredTitleBarNeedsLightText() ? RGB(255, 255, 255)", apply)
        self.assertIn("component * 255.0f + 0.5f", apply, "The caption is the theme colour to the byte")
        # The same arithmetic here: the chrome and the facet take light text, stock Blender Light's top bar
        # dark text.
        self.assertTrue(window_chrome.needs_light_text(CHROME))
        self.assertTrue(window_chrome.needs_light_text(window_chrome.darkest_facet()))
        self.assertFalse(window_chrome.needs_light_text(window_chrome.rgb("#999999")))
        self.assertTrue(window_chrome.needs_light_text(window_chrome.rgb("#181818")))
        # The HSV value of the facet is above one half: the previous rule gave it dark title text.
        self.assertGreater(max(window_chrome.darkest_facet()) / 255, 0.5)

    def test_main_windows_take_the_title_bar_colour_from_the_top_bar(self):
        window = (ROOT / "source/blender/windowmanager/intern/wm_window.cc").read_text(encoding="utf-8")
        style = window.split("static void wm_window_decoration_style_set_from_theme(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (WM_window_is_main_top_level(win)) {\n    ui::theme::theme_set(SPACE_TOPBAR, RGN_TYPE_HEADER);", style)
        self.assertIn("ui::theme::get_color_3fv(TH_BACK, titlebar_bg_color);", style)
        # Before its first draw a window is cleared to the colour between the editors, which the bundled
        # themes keep where the top bar used to be: a start does not flash the whole window in the chrome.
        self.assertIn("ui::theme::get_color_3fv(TH_EDITOR_BORDER, window_bg_color);", window)
        border = window_chrome.rgb(re.search(r"\.editor_border = RGBA\(0x([0-9a-f]{8})\)",
                                             DEFAULT_THEME.read_text(encoding="utf-8")).group(1))
        self.assertTrue(all(abs(channel - 0x18) <= 2 for channel in border), "Blender's top bar was #181818")
        self.assertEqual(light_theme().find(".//ThemeUserInterface").get("editor_border"), "#999999",
                         "Stock Blender Light's top bar was #999999")


if __name__ == "__main__":
    unittest.main()
