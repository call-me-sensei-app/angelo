# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's icon-only buttons draw their icon centred and no text at all (1.0.5, ``studio_draw.button``).

Evidence: ux-coherence's review (D1, 2026-10-23): a stray "…" between Media Studio's Refresh and Import. The 30 px
icon-only Refresh button elided its empty title into a lone ellipsis at its edge. ``button`` is run from its source
against a recording canvas (the module itself needs the running app's ``gpu``). The running app's proof is
``test_blender_studio_palette`` (the strip between Refresh and Import is plain background in every capture).
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/addons_core/angelo_agent/studio_draw.py"


class Canvas:
    def __init__(self):
        self.calls = []

    def shape(self, *args):
        self.calls.append(("shape",) + args)

    def icon(self, name, x, y, color):
        self.calls.append(("icon", name, x, y))

    def text(self, value, x, y, size, color):
        self.calls.append(("text", value, x, y))

    def elide(self, value, width, size):
        return value if width >= 7 * len(value) else (value[:max(1, int(width // 7) - 1)] + "…")


def button_function(canvas, hits):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "button")
    names = {key: (0, 0, 0, 1) for key in ("ACCENT", "ACCENT_HOVER", "ACCENT_PRESSED", "SELECTED", "HOVER", "FIELD",
                                           "LINE", "ACCENT_TEXT", "TEXT", "MUTED")}
    namespace = dict(names, canvas=canvas, hit=lambda state, rect, action, value: hits.append((rect, action)))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["button"]


class IconOnlyTests(unittest.TestCase):
    def setUp(self):
        self.canvas, self.hits = Canvas(), []
        self.button = button_function(self.canvas, self.hits)

    def test_an_empty_title_draws_no_text_and_centres_the_icon(self):
        self.button({}, (100, 50, 30, 32), "", "refresh", icon="reset")
        self.assertEqual([c for c in self.canvas.calls if c[0] == "text"], [], "No text, not even an ellipsis")
        icon = next(c for c in self.canvas.calls if c[0] == "icon")
        self.assertEqual((icon[2], icon[3]), (115, 66), "Centred in the button")
        self.assertEqual(self.hits, [((100, 50, 30, 32), "refresh")], "Still clickable")

    def test_a_titled_button_keeps_its_icon_and_label(self):
        self.button({}, (0, 0, 89, 32), "Import", "import", icon="plus")
        texts = [c for c in self.canvas.calls if c[0] == "text"]
        self.assertEqual([t[1] for t in texts], ["Import"])
        icon = next(c for c in self.canvas.calls if c[0] == "icon")
        self.assertLess(icon[2], texts[0][2], "The icon leads the label")


if __name__ == "__main__":
    unittest.main()
