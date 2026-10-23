# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's glyphs on Blender's widgets take the widget's text colour (1.0.5 review: invisible in Angelo Light).

Chat's bubble, Studio's frame and Flows' nodes were baked near-white; on Angelo Light's light buttons they vanished.
Every place one is drawn on a Blender widget passes ``glyph_tint.colour`` of that widget (a top-bar tool button,
pressed or not; a menu item), and no baker keeps a fixed colour. The app's proof is top_bar_permanence's glyph
contrast in the Light and Dark cases.
"""

import ast
import re
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
GLYPHS = ("icon_id", "studio_toggle.icon_id", "flow_open.icon_id", "chat_toggle.icon_id")


class GlyphTintTests(unittest.TestCase):
    def test_no_baker_keeps_a_fixed_colour(self):
        for name in ("chat_icon.py", "studio_toggle.py", "flow_open.py"):
            source = (ADDON / name).read_text(encoding="utf-8")
            self.assertNotIn("0.93 * alpha", source, name)
            self.assertIn("pixels += (r * alpha, g * alpha, b * alpha, alpha)", source, name)

    def test_every_glyph_drawn_on_a_widget_is_tinted(self):
        """Each icon_value=<glyph>.icon_id(...) passes a colour (the theme's), in every module."""
        untinted = []
        for path in sorted(ADDON.glob("*.py")):
            for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(call, ast.Call):
                    continue
                for keyword in call.keywords:
                    value = keyword.value
                    if keyword.arg == "icon_value" and isinstance(value, ast.Call) \
                            and ast.unparse(value.func) in GLYPHS:
                        needed = 1 if ast.unparse(value.func).endswith("flow_open.icon_id") else 2
                        if path.name == "flow_open.py" and ast.unparse(value.func) == "icon_id":
                            needed = 1
                        if len(value.args) + len(value.keywords) < needed:
                            untinted.append("%s:%d %s" % (path.name, call.lineno, ast.unparse(value)))
        self.assertEqual(untinted, [], "a glyph drawn without the widget's text colour")

    def test_the_colour_is_the_themes_text_colour_for_the_widget(self):
        source = (ADDON / "glyph_tint.py").read_text(encoding="utf-8")
        body = source.split("\ndef colour(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('getattr(theme, "wcol_" + widget)', body)
        self.assertIn("state.text_sel if selected else state.text", body)
        self.assertTrue(re.search(r"except \(.*AttributeError", body), "an unreadable theme falls back")


if __name__ == "__main__":
    unittest.main()
