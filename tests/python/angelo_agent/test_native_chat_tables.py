# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reply's markdown table on the chat canvas (``chat_tables``, drawn by ``chat_replies``), without Blender.

Evidence: the release-1.0.6 bug sweep (GPT-6.1 Sol) showed a material reply's settings table as one paragraph of
raw pipes. Here the real wrapping (``canvas_wrap.iter_wrap``) runs with a fake font of whole-pixel advances, as
``test_native_composer_hint_fit`` does, over tables of several shapes, scripts and chat widths that are not that
reply: every cell's text is drawn, inside its column, inside the reply, and a chat too narrow for a grid stacks
the rows as "Header: value" instead of clipping.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

SIZE, LINE = 13, 21


class FakeBlf:
    """Whole-pixel advances: wide for CJK, narrow otherwise."""

    def __init__(self):
        self.sizes = {}

    def size(self, font, size):
        self.sizes[font] = size

    def dimensions(self, font, value):
        size = self.sizes.get(font, SIZE)
        return (sum(round((1.0 if ord(c) > 0x2E80 else 0.55) * size) for c in value), size)


class TableLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._modules = mock.patch.dict(sys.modules, {"blf": types.ModuleType("blf")})
        cls._modules.start()
        from angelo_agent import canvas_wrap, chat_tables, markdown_blocks
        cls.tables, cls.blocks = chat_tables, markdown_blocks
        cls.fake = FakeBlf()
        cls._blf = mock.patch.object(canvas_wrap, "blf", cls.fake)
        cls._blf.start()
        cls.canvas_wrap = canvas_wrap

    @classmethod
    def tearDownClass(cls):
        cls._blf.stop()
        cls._modules.stop()

    def measure(self, value):
        """As ``chat_view.measure``: shaped first, so Arabic is measured in the forms drawn."""
        from angelo_agent.canvas_rtl import visual
        self.fake.size(0, SIZE)
        return self.fake.dimensions(0, visual(value))[0]

    def wrap(self, value, width):
        return list(self.canvas_wrap.iter_wrap(value, width, SIZE, 0))

    def lay(self, markdown, width):
        table = next(block for block in self.blocks.parse(markdown) if block.kind == "table")
        ops, height = self.tables.layout(table.cells, table.aligns, width, SIZE, LINE, self.measure, self.wrap)
        return table, ops[0], ops[1:], height

    def text_of(self, runs):
        return "".join(value for value, _kind, _url in runs)

    TABLES = (
        "| Input | Value |\n|---|---|\n| Base Color | White |\n| Roughness | 0.02 to 0.05 |",
        "| Step | What to do | Why |\n|:-:|---|--:|\n| 1 | Apply scale before bevelling so widths stay even | "
        "Bevel width follows object scale |\n| 2 | Add a Weighted Normal modifier | Flat faces shade cleanly |",
        "Light | Power | Colour | Size | Notes\n--- | --: | --- | --- | ---\nKey | 1000 W | warm white | 2 m | "
        "soft shadow\nRim | 400 W | cool blue | 0.5 m | separates the subject from a dark background",
        "| 材质 | 设置 |\n|---|---|\n| 玻璃 | 透射权重为一，粗糙度很低，折射率一点四五左右 |\n| 金属 | 金属度为一 |",
        "| Link | Use |\n|---|---|\n| [Principled BSDF](https://docs.blender.org/manual/en/latest/render/shader_nodes/"
        "shader/principled.html) | the `Transmission` input and `IOR` |",
    )
    WIDTHS = (120, 220, 330, 480, 700, 1200)

    def test_every_cell_is_drawn_inside_its_column_and_the_reply_at_every_width(self):
        for markdown in self.TABLES:
            for width in self.WIDTHS:
                with self.subTest(table=markdown[:24], width=width):
                    table, frame, runs, height = self.lay(markdown, width)
                    self.assertEqual(frame[0], "table")
                    self.assertLessEqual(frame[3], width + 0.5, "the grid never overflows the reply")
                    self.assertEqual(frame[4], height)
                    edges = (0,) + frame[5] + (frame[3],)
                    for op in runs:
                        _kind, x, dy, row, _size, _role = op
                        drawn = sum(self.measure(value) for value, _k, _u in row)
                        column = max(i for i, edge in enumerate(edges[:-1]) if edge <= x)
                        self.assertLessEqual(x + drawn, edges[column + 1] + 0.5,
                                             f"{self.text_of(row)!r} leaves its column at {width}")
                        self.assertTrue(0 < dy <= height)
                    shown = "".join(self.text_of(op[3]) for op in runs).replace(" ", "")
                    for row in table.cells[1:] if not frame[5] else table.cells:
                        for cell in row:
                            self.assertIn(self.blocks.inline(cell)[0].replace(" ", ""), shown, "no cell text is lost")

    WORDY = (   # Held out: none of these is the table of the narrow-chat evidence below.
        "| Wood | Hardness | Price | Colour |\n|---|---|---|---|\n| Maple | Very hard | mid-range | creamy/pale white |\n"
        "| Cedar | Soft | affordable | reddish/pinkish brown |\n| Walnut | Hard | premium-priced | chocolate/dark brown |",
        "| Setting | Typical range | Effect |\n|---|---|---|\n| Subsurface Weight | 0.05–0.15 | softens skin-like "
        "surfaces |\n| Sheen Roughness | 0.3–0.6 | velvet-like falloff on cloth |",
        "| Light | Watts | Temperature |\n|---|--:|---|\n| Key | 1,000–1,500 W | 5,600 K daylight |\n"
        "| Fill | 250–400 W | 4,300 K neutral-warm |\n| Rim | 600 W | 7,500 K cool/blue-ish |",
        "| Page | Covers |\n|---|---|\n| <https://docs.blender.org/manual/en/latest/render/cycles/render_settings/"
        "sampling.html> | adaptive sampling and noise thresholds |",
        "| 材质 | 设置 |\n|---|---|\n| 玻璃 | 透射权重为一，粗糙度很低 |\n| 金属 | 金属度为一 |",
    )

    def test_no_ordinary_word_is_cut_in_two_at_any_width(self):
        """Evidence: release-1.0.6 GUI proof at a ~250 px chat cut cell words mid-word ("Moderatel|y hard"). A grid
        column holds its longest word, or the table stacks; only a token wider than LONG_TOKEN (a URL) may break,
        and Chinese breaks between characters by design."""
        for markdown in self.WORDY + self.TABLES:
            table = next(block for block in self.blocks.parse(markdown) if block.kind == "table")
            words, long_words = set(), []
            for row in table.cells:
                for cell in row:
                    for word in self.blocks.inline(cell)[0].split():
                        if self.measure(word) > self.tables.LONG_TOKEN:
                            long_words.append(word)
                        else:
                            words.add(word)
            words |= {word + ":" for word in words}   # A stacked record's "Header:" label.
            for width in (160, 200, 250, 300, 360, 480, 700):
                with self.subTest(table=markdown[:24], width=width):
                    _table, _frame, runs, _height = self.lay(markdown, width)
                    for op in runs:
                        for word in self.text_of(op[3]).split():
                            if word in words or any("一" <= char <= "鿿" for char in word):
                                continue
                            self.assertTrue(any(word in whole for whole in long_words),
                                            f"{word!r} is part of a word cut in two at {width} px")

    def test_a_table_that_fits_keeps_each_column_at_its_natural_width(self):
        _table, frame, runs, _height = self.lay(self.TABLES[0], 700)
        expected = self.measure("Base Color") + 2 * self.tables.PAD_X
        self.assertEqual(frame[5], (expected,), "the second column starts after the widest first cell")
        self.assertEqual(frame[7], self.tables.PAD_Y * 2 + LINE, "a one-line header on its fill")
        self.assertEqual([op[5] for op in runs[:2]], ["heading", "heading"])
        self.assertEqual(len(runs), 6, "one row per cell: nothing wraps")

    def test_short_columns_keep_their_width_and_the_widest_wraps(self):
        _table, frame, runs, _height = self.lay(self.TABLES[1], 330)
        first = frame[5][0]
        self.assertEqual(first, self.measure("Step") + 2 * self.tables.PAD_X)
        self.assertEqual(sum(1 for op in runs if op[1] < first), 3, "the step numbers and header: one row each")
        self.assertGreater(len(runs), 9, "the long cells wrap")

    def test_alignment_places_a_cells_text(self):
        markdown = "| L | C | R |\n|:--|:-:|--:|\n| a | b | c |"
        _table, frame, runs, _height = self.lay(markdown, 700)
        left, centre, right = runs[3:]
        edges = (0,) + frame[5] + (frame[3],)
        pad = self.tables.PAD_X
        self.assertEqual(left[1], pad)
        self.assertAlmostEqual(centre[1], edges[1] + pad + (edges[2] - edges[1] - 2 * pad - self.measure("b")) / 2)
        self.assertAlmostEqual(right[1] + self.measure("c"), edges[3] - pad)

    def test_too_narrow_for_a_grid_stacks_records_of_header_and_value(self):
        _table, frame, runs, _height = self.lay(self.TABLES[2], 220)
        self.assertEqual(frame[5], (), "no columns")
        self.assertEqual(len(frame[6]), 1, "one line between the two records")
        first = runs[0][3]
        self.assertEqual(first[0], ("Light: ", "label", ""))
        self.assertEqual(self.text_of(first), "Light: Key")
        for op in runs:
            self.assertLessEqual(op[1] + sum(self.measure(v) for v, _k, _u in op[3]), 220 - self.tables.PAD_X + 0.5)

    def test_inline_code_and_links_keep_their_runs(self):
        _table, _frame, runs, _height = self.lay(self.TABLES[4], 1200)
        kinds = {kind for op in runs for _value, kind, _url in op[3]}
        self.assertTrue({"link", "code"} <= kinds)
        url = next(url for op in runs for _value, kind, url in op[3] if kind == "link")
        self.assertTrue(url.startswith("https://docs.blender.org/"))

    def test_right_to_left_cells_are_drawn_whole(self):
        _table, _frame, runs, _height = self.lay("| الاسم | القيمة |\n|---|---|\n| **زجاج** | `1.45` |", 700)
        self.assertEqual(len(runs), 4, "nothing wraps at this width")
        self.assertTrue(all(len(op[3]) == 1 and op[3][0][1] == "" for op in runs[:3]), "Arabic: one run per row")
        self.assertEqual(runs[3][3], (("1.45", "code", ""),), "a left-to-right cell keeps its chip")

    def test_a_header_still_streaming_is_drawn(self):
        _table, frame, runs, height = self.lay("| Setting | Value |\n|--", 700)
        self.assertEqual((frame[6], len(runs), frame[7]), ((), 2, height))
        _table, frame, runs, _height = self.lay("| Setting | Value |\n|--", 90)
        self.assertEqual([self.text_of(op[3]) for op in runs], ["Setting", "Value"], "stacked: its names")


class ReplyLayoutTests(unittest.TestCase):
    """``chat_replies`` places the table among the reply's other blocks."""

    def test_the_table_sits_between_its_paragraphs(self):
        from angelo_agent import chat_replies
        fake = FakeBlf()

        def measure(value, size=14, font=None):
            fake.size(0, size)
            return fake.dimensions(0, value)[0]

        def wrap(value, width, size, font, ordered=True):
            rows, words = [], value.split(" ")
            line = ""
            for word in words:
                candidate = (line + " " + word).strip()
                if line and measure(candidate, size) > width:
                    rows.append((line, 0))
                    line = word
                else:
                    line = candidate
            return rows + [(line, 0)]

        view = types.SimpleNamespace(measure=measure, wrap=wrap)
        text = "Before.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter."
        with mock.patch.object(chat_replies, "_view", lambda: view):
            ops, height = chat_replies._layout(text, 400, SIZE, LINE, 0, 0, 1.0)
        kinds = [op[0] for op in ops]
        self.assertEqual(kinds[0], "runs")
        table = kinds.index("table")
        frame = ops[table]
        self.assertGreater(frame[2], ops[0][2], "below the first paragraph")
        self.assertEqual([op[3][0][0] for op in ops[table + 1:table + 5]], ["A", "B", "1", "2"])
        for op in ops[table + 1:table + 5]:
            self.assertTrue(frame[2] < op[2] <= frame[2] + frame[4], "its text is inside its frame")
        self.assertGreater(ops[-1][2], frame[2] + frame[4], "the next paragraph is below it")
        self.assertGreaterEqual(height, ops[-1][2])


if __name__ == "__main__":
    unittest.main()
