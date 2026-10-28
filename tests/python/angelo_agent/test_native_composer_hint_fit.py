# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's hint is judged against what the drawn chat can hold, at every interface scale and pixel size.

Evidence: release-106's Windows T4 GUI run (C3 f7606d8b379) failed test_blender_session_rail's "whole at 2x" check.
There the display's pixel size is 1, so the 2x chat was 163 layout units wide (half a Retina Mac's at the same
setting); the widest shipped hint (Japanese, naming Blender's longest scene name) needs more than the composer's eight
rows at that width, and the composer showed its first whole sentence, then an ellipsis, as documented. The check
assumed every 2x chat holds it. Here the real wrapping (``canvas_wrap.iter_wrap``) measures with whole-pixel glyph
advances at the rasterised size, as Blender draws, so widths in pixels and scales combine as on a real display.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

JAPANESE = ("このチャットはシーン「Ground floor living room with the long bay window and fireplace」で作業しています。"
            "続けるには、そのシーンに切り替えてください。")   # The widest shipped hint of the T4 run.
ENGLISH = ("This chat works on scene 'Ground floor living room with the long bay window and fireplace'. "
           "Switch to that scene to continue.")
GERMAN = ("Dieser Chat arbeitet in der Szene „Ground floor living room with the long bay window and fireplace“. "
          "Wechsle zu dieser Szene, um fortzufahren.")
CHINESE = "这个对话在场景「Ground floor living room with the long bay window and fireplace」中工作。切换到该场景以继续。"
HELD_OUT = (ENGLISH, GERMAN, CHINESE, JAPANESE, "Do anything", "甲" * 30 + "。" + "乙" * 400 + "。", "wordy " * 300)

# (chat area pixels, display pixel size, interface scale): the T4 VM (1x pixels), a Retina Mac (2x pixels), a 150%
# Windows laptop, and narrow and wide docks; the chat lays out in pixels / (pixel size * scale) units.
DISPLAYS = [(px, pixel, ui) for px in (240, 326, 480, 700, 1300) for pixel in (1, 1.5, 2) for ui in (1, 1.5, 2)]


class FakeBlf:
    """Whole-pixel advances at the rasterised size, as Blender's text renderer without subpixel positioning."""

    def __init__(self):
        self.sizes = {}

    def size(self, font, size):
        self.sizes[font] = size

    def dimensions(self, font, value):
        size = self.sizes[font]
        return (sum(round((1.0 if ord(c) > 0x2E80 else 0.55) * size) for c in value), size)


class HintFit(unittest.TestCase):
    """``chat_composer`` and ``canvas_wrap`` imported with stub Blender modules for this class only."""

    @classmethod
    def setUpClass(cls):
        stubs = {name: mock.MagicMock(name=name) for name in (
            "bpy", "bpy.props", "bpy.types", "bpy.app", "bpy.app.handlers", "blf", "gpu", "gpu_extras",
            "gpu_extras.batch", "mathutils", "bpy_extras", "bpy_extras.io_utils", "bl_math")}
        cls._before = set(sys.modules)
        cls._modules = mock.patch.dict(sys.modules, stubs)
        cls._modules.start()
        from angelo_agent import canvas_wrap, chat_composer
        import composer_hint_qa
        cls.wrapper, cls.composer, cls.qa = canvas_wrap, chat_composer, composer_hint_qa
        cls.blf = FakeBlf()
        cls._blf = mock.patch.object(canvas_wrap, "blf", cls.blf)
        cls._blf.start()

    @classmethod
    def tearDownClass(cls):
        cls._blf.stop()
        added = [name for name in sys.modules if name not in cls._before and name.startswith("angelo_agent.")]
        cls._modules.stop()
        package = sys.modules.get("angelo_agent")
        for name in added:
            if package is not None and name.count(".") == 1:
                package.__dict__.pop(name.split(".", 1)[1], None)
        sys.modules.pop("composer_hint_qa", None)

    def wrap_at(self, scale):
        def wrap(value, width, size, font, ordered=True):
            return list(self.wrapper.iter_wrap(value, width, size, font, ordered, scale))
        return wrap

    def judge(self, rows, text, width, scale, size=12):
        wrap = self.wrap_at(scale)
        return self.qa.verdict(rows, text, lambda value: len(wrap(value, self.composer.hint_width(width), size, 7)),
                               self.composer.HINT_ROWS, self.composer.sentence_ends)

    def test_the_drawn_hint_meets_its_verdict_on_every_display(self):
        seen = set()
        for pixels, pixel_size, ui in DISPLAYS:
            scale = pixel_size * ui
            width = pixels / scale
            for text in HELD_OUT:
                rows = self.composer.hint_rows(text, width, 12, self.wrap_at(scale), 7)
                regime, ok = self.judge(rows, text, width, scale)
                seen.add(regime)
                self.assertTrue(ok, (pixels, pixel_size, ui, regime, text[:12], rows))
                self.assertLessEqual(len(rows), self.composer.HINT_ROWS)
                for row in rows:   # Every row within the wrap width, measured as drawn at this scale.
                    self.blf.size(7, 12 * scale)
                    self.assertLessEqual(self.blf.dimensions(7, row.rstrip("…"))[0] / scale,
                                         self.composer.hint_width(width), (pixels, scale, row))
        self.assertEqual(seen, {"whole", "sentences", "prefix"})   # Every regime exercised, not one.

    def test_the_t4_2x_chat_shows_the_first_sentence_and_a_wide_one_the_whole_hint(self):
        """The same 2x setting on a 326-pixel chat: on the T4's 1x display (163 units) the Japanese hint takes more
        than eight rows, so its first sentence, then an ellipsis; on a Retina display (81 units) not even that
        sentence fits, so every row; 1300 Retina pixels (325 units) hold it whole."""
        rows = self.composer.hint_rows(JAPANESE, 326 / 2.0, 12, self.wrap_at(2.0), 7)
        self.assertEqual(self.judge(rows, JAPANESE, 326 / 2.0, 2.0), ("sentences", True), rows)
        self.assertEqual("".join(rows).replace(" ", ""),   # Rows drop the spaces they end on.
                         JAPANESE[:JAPANESE.index("。") + 1].replace(" ", "") + "…")
        rows = self.composer.hint_rows(JAPANESE, 326 / 4.0, 12, self.wrap_at(4.0), 7)
        self.assertEqual(self.judge(rows, JAPANESE, 326 / 4.0, 4.0), ("prefix", True), rows)
        self.assertEqual(len(rows), self.composer.HINT_ROWS)
        rows = self.composer.hint_rows(JAPANESE, 1300 / 4.0, 12, self.wrap_at(4.0), 7)
        self.assertEqual(self.judge(rows, JAPANESE, 1300 / 4.0, 4.0), ("whole", True), rows)

    def test_the_verdict_refuses_what_the_composer_must_never_draw(self):
        width, scale = 163, 2.0
        rows = self.composer.hint_rows(JAPANESE, width, 12, self.wrap_at(scale), 7)
        first = JAPANESE[:JAPANESE.index("。") + 1]
        cut = rows[:-1] + [rows[-1].replace("す。", "す")]   # Mid-sentence.
        self.assertFalse(self.judge(cut, JAPANESE, width, scale)[1])
        self.assertFalse(self.judge(rows[:3] + ["…"], JAPANESE, width, scale)[1])   # Fewer sentences than fit.
        self.assertFalse(self.judge([first], JAPANESE, width, scale)[1])   # Dropped text with no ellipsis.
        self.assertFalse(self.judge(rows * 2, JAPANESE, width, scale)[1])   # More rows than the composer has.
        wide = self.composer.hint_rows(ENGLISH, 700, 12, self.wrap_at(1.0), 7)
        self.assertEqual(self.judge(wide, ENGLISH, 700, 1.0), ("whole", True))
        self.assertFalse(self.judge([wide[0] + "…"], ENGLISH, 700, 1.0)[1])   # An ellipsis where it fits.
        self.assertFalse(self.judge([ENGLISH.replace("Switch", "Go")], ENGLISH, 700, 1.0)[1])   # Other words.

    def test_the_composer_records_the_width_its_hint_wrapped_to(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/chat_composer.py").read_text(encoding="utf-8")
        self.assertIn('state["hint_width"] = hint_width(width)', source)
        self.assertIn("wrap(value, hint_width(width), size, font, ordered=True)", source)
        suite = (Path(__file__).resolve().parent / "test_blender_session_rail.py").read_text(encoding="utf-8")
        self.assertNotIn("composer_hint_qa.whole(", suite)   # Every hint check there is judged by the drawn width.
        self.assertIn("composer_hint_qa.as_the_chat_allows(chat_area(), state['long_hint'])", suite)


if __name__ == "__main__":
    unittest.main()
