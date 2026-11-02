# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's translated buttons are as wide as their label in the interface language (dogfood B-006, 1.0.8:
Japanese cut "Hide Create" and "Import" off at fixed pixel widths).

The width rule is ``studio_state.button_width``; the top row is ``studio_state.top_row``. Labels come from the shipped
catalogues the way the app finds them (Blender's first, then Angelo's). The measure stands in for the font: a CJK
character one em, anything else 0.58 em, wider than Inter's average, so a pass here is not a near miss in the app.
A source guard keeps the class closed: no Studio button draws translated text at a fixed literal width.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from angelo_agent.studio_state import BUTTON_PAD, ICON_ROOM, button_width, chip_grid, top_row  # noqa: E402
from i18n import po  # noqa: E402

LABELS = [("Image", ""), ("3D", ""), ("Video", ""), ("Workflows", ""), ("Hide Create", ""), ("Create", ""),
          ("Import", ""), ("URL", ""), ("Expand", "Angelo"), ("AI Enhance", ""), ("Review & apply", ""),
          ("Discard", ""), ("Open Create", ""), ("Cancel", ""), ("Apply prompt", "")]
LANGUAGES = {"ja": ("ja", "ja"), "de": ("de", "de")}
STUDIO_FILES = ("studio.py", "studio_draw.py", "studio_video_ui.py")


def measure(text, size):
    return sum(size * (1.0 if ord(c) > 0x2E80 else 0.58) for c in text)


def catalogue(blender_code, angelo_code):
    found = {}
    for path in (ROOT / "locale/angelo/po" / f"{angelo_code}.po", ROOT / "locale/po" / f"{blender_code}.po"):
        if path.is_file():   # Blender's is read last, so its wording wins, as at runtime.
            found.update({(e.id, e.ctxt or ""): e.str for e in po.read(str(path))[1] if e.str and not e.obsolete})
    return found


class ButtonWidthTests(unittest.TestCase):
    def test_every_label_fits_its_button_in_japanese_and_german(self):
        for code, (blender, angelo) in LANGUAGES.items():
            words = catalogue(blender, angelo)
            for label, context in LABELS:
                shown = words.get((label, context)) or words.get((label, "")) or label
                for size, icon in ((12, False), (11, True), (10, False)):
                    with self.subTest(language=code, label=label, size=size):
                        width = button_width(shown, measure, size, icon, minimum=60)
                        self.assertLessEqual(measure(shown, size), width - BUTTON_PAD - (ICON_ROOM if icon else 0))

    def test_english_keeps_its_widths_and_the_top_row_runs_left_to_right(self):
        self.assertEqual(button_width("Image", measure, minimum=95), 95)
        for code, (blender, angelo) in LANGUAGES.items():
            words = catalogue(blender, angelo)
            entries = [(words.get((label, ""), label), False, 70, 8) for label in ("Image", "3D", "Video", "Workflows")]
            entries.append((words.get(("Hide Create", ""), "Hide Create"), False, 112, 14))
            with self.subTest(language=code):
                placed, end = top_row(entries, measure)
                for (x, width), (nx, _w) in zip(placed, placed[1:]):
                    self.assertLess(x + width, nx, "no overlap")
                self.assertEqual(end, placed[-1][0] + placed[-1][1])

    def test_chip_rows_show_every_label_whole_and_wrap_in_balanced_rows(self):
        # The Video tab's aspect row cut "21:9" to "2…" when six chips shared a narrow column.
        rows = (("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"), ("480p", "720p", "1080p", "4k"), ("768P", "1080P"),
                ("16:9", "9:16"))
        for labels in rows:
            for width in range(180, 620, 20):
                for size, cell in ((10, 0), (11, 64)):
                    with self.subTest(labels=labels, width=width, size=size):
                        chip, places = chip_grid(labels, measure, size, width, cell=cell)
                        self.assertLessEqual(measure(max(labels, key=lambda t: measure(t, size)), size), chip - BUTTON_PAD)
                        self.assertTrue(all(dx + chip <= width + 1e-6 for dx, _row in places), "inside the column")
                        per_row = [sum(1 for _dx, r in places if r == row) for row in range(places[-1][1] + 1)]
                        self.assertLessEqual(max(per_row) - min(per_row), 1, "balanced rows")

    def test_no_studio_button_draws_translated_text_at_a_fixed_width(self):
        addon = ROOT / "scripts/addons_core/angelo_agent"
        for name in STUDIO_FILES:
            for node in ast.walk(ast.parse((addon / name).read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "button" and len(node.args) >= 3):
                    continue
                rect, title = node.args[1], node.args[2]
                fixed = isinstance(rect, ast.Tuple) and len(rect.elts) == 4 and isinstance(rect.elts[2], ast.Constant)
                translated = isinstance(title, ast.Call) and getattr(title.func, "id", "") == "iface_"
                with self.subTest(file=name, line=node.lineno):
                    self.assertFalse(fixed and translated, "measure the label (studio_draw.fit)")


if __name__ == "__main__":
    unittest.main()
