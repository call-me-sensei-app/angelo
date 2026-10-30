# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reply markdown parser (markdown_blocks): blocks, inline spans and replies still streaming.

Dependency-free: no Blender, fonts or GPU; the chat's drawing is covered by the GUI suites.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.markdown_blocks import inline, parse, plain_text  # noqa: E402


def kinds(text):
    return [block.kind for block in parse(text)]


class BlockTests(unittest.TestCase):
    def test_paragraphs_join_soft_wraps_and_split_on_blank_lines(self):
        blocks = parse("First line\ncontinues here.\n\nSecond paragraph.")
        self.assertEqual(kinds("First line\ncontinues here.\n\nSecond paragraph."), ["paragraph", "paragraph"])
        self.assertEqual(blocks[0].text, "First line continues here.")

    def test_headings_lists_quotes_and_rules(self):
        text = "# Title\n## Sub\n#### Deep\n- one\n- two\n  - nested\n1. first\n2) second\n> quoted\n> more\n---"
        blocks = parse(text)
        self.assertEqual([b.kind for b in blocks],
                         ["heading", "heading", "heading", "bullet", "bullet", "bullet", "number", "number",
                          "quote", "rule"])
        self.assertEqual([b.level for b in blocks[:3]], [1, 2, 3])
        self.assertEqual((blocks[5].level, blocks[5].marker), (1, "◦"))
        self.assertEqual([b.marker for b in blocks[6:8]], ["1.", "2."])
        self.assertEqual(blocks[8].text, "quoted\nmore")

    def test_list_item_continuation_joins_the_item(self):
        blocks = parse("- a long item\n  that wraps\n- next")
        self.assertEqual([b.text for b in blocks], ["a long item that wraps", "next"])

    def test_fenced_code_keeps_its_text_and_language(self):
        blocks = parse("Run this:\n```python\nimport bpy\n\n  print(1)\n```\nDone.")
        self.assertEqual(kinds("Run this:\n```python\nimport bpy\n```\nDone."), ["paragraph", "code", "paragraph"])
        code = blocks[1]
        self.assertEqual((code.lang, code.text, code.closed), ("python", "import bpy\n\n  print(1)", True))

    def test_markdown_inside_code_is_literal(self):
        code = parse("```\n# not a heading\n- not a list\n**kept**\n```")[0]
        self.assertEqual(code.text, "# not a heading\n- not a list\n**kept**")

    def test_star_rule_is_not_a_bullet(self):
        self.assertEqual(kinds("* * *"), ["rule"])


class TableTests(unittest.TestCase):
    """GitHub tables as models write them (evidence: release-1.0.6 bug sweep, a material reply's settings table drawn
    as one paragraph of raw pipes). The cases are generic shapes, not that reply's words."""

    def test_header_delimiter_and_rows_with_alignment(self):
        blocks = parse("Settings:\n\n| Name | Value | Unit |\n|:--|--:|:-:|\n| Width | `2` | m |\n| Depth | 3 | m |\n\nDone.")
        self.assertEqual([b.kind for b in blocks], ["paragraph", "table", "paragraph"])
        table = blocks[1]
        self.assertEqual(table.cells, (("Name", "Value", "Unit"), ("Width", "`2`", "m"), ("Depth", "3", "m")))
        self.assertEqual(table.aligns, ("left", "right", "center"))
        self.assertTrue(table.closed)

    def test_outer_pipes_are_optional_and_rows_may_be_ragged(self):
        table = parse("a | b\n--- | ---\n1 |\n2 | 3 | 4\n| 5 | 6 |")[0]
        self.assertEqual(table.kind, "table")
        self.assertEqual(table.cells, (("a", "b"), ("1", ""), ("2", "3"), ("5", "6")))
        self.assertEqual(table.aligns, ("", ""))

    def test_escaped_pipes_and_inline_markdown_stay_in_their_cell(self):
        table = parse("| Expr | Note |\n|---|---|\n| `a \\| b` | **or** [doc](https://example.com/x) |")[0]
        self.assertEqual(table.cells[1], ("`a | b`", "**or** [doc](https://example.com/x)"))
        plain, spans = inline(table.cells[1][1])
        self.assertEqual((plain, [kind for _s, _e, kind, _u in spans]), ("or doc", ["link"]))

    def test_a_paragraph_line_right_above_the_header_stays_a_paragraph(self):
        self.assertEqual(kinds("Values below\n| a | b |\n|---|---|\n| 1 | 2 |"), ["paragraph", "table"])

    def test_the_table_ends_at_a_blank_line_or_a_line_without_a_pipe(self):
        self.assertEqual(kinds("| a |\n|---|\n| 1 |\nAfter it"), ["table", "paragraph"])
        self.assertEqual(kinds("| a |\n|---|\n| 1 |\n\n| 2 |"), ["table", "paragraph"])
        self.assertEqual(kinds("| a | b |\n|---|---|\n```\ncode | x\n```"), ["table", "code"])

    def test_pipes_without_a_delimiter_row_stay_a_paragraph(self):
        for text in ("Use a | b to join.\nThen render.", "| a | b |\n| 1 | 2 |", "a | b\n---",
                     "| a | b |\n|---|---|---|", "x | y\n|-x-|---|", "Choose A | B"):
            with self.subTest(text=text):
                self.assertNotIn("table", kinds(text))

    def test_plain_text_lists_the_cells_tab_separated(self):
        self.assertEqual(plain_text("| A | B |\n|---|---|\n| **x** | `y` |"), "A\tB\nx\ty")


class StreamingTests(unittest.TestCase):
    """A reply drawn while it streams: every prefix parses, an open fence is an open code block."""

    REPLY = ("## Plan\nI'll add **two** lights:\n\n1. A `Sun` at 45°\n2. A [fill](https://example.com/x)\n\n"
             "```python\nimport bpy\nbpy.ops.object.light_add(type='SUN')\n```\n> Undo is one step.\n")

    def test_every_prefix_parses(self):
        for end in range(len(self.REPLY) + 1):
            blocks = parse(self.REPLY[:end])
            for block in blocks:
                inline(block.text)
            self.assertIsInstance(blocks, tuple)

    def test_unclosed_fence_is_an_open_code_block(self):
        partial = self.REPLY[:self.REPLY.index("SUN")]
        code = parse(partial)[-1]
        self.assertEqual((code.kind, code.closed, code.lang), ("code", False, "python"))
        self.assertTrue(code.text.startswith("import bpy"))

    def test_a_lone_fence_opening_is_an_empty_open_block(self):
        self.assertEqual(parse("Here:\n```")[-1][:2], ("code", ""))
        self.assertFalse(parse("Here:\n```")[-1].closed)

    def test_every_prefix_of_a_table_parses_and_never_loses_a_row(self):
        reply = "Try:\n\n| Setting | Value |\n| --- | :---: |\n| Roughness | 0.1 |\n| IOR | 1.45 |\n\nThen render."
        for end in range(len(reply) + 1):
            blocks = parse(reply[:end])
            tables = [block for block in blocks if block.kind == "table"]
            for table in tables:
                self.assertTrue(all(len(row) == 2 for row in table.cells), reply[:end])
            if end >= reply.index("| Roughness"):
                self.assertEqual(len(tables), 1, "once its delimiter is complete it stays a table: " + reply[:end])

    def test_a_delimiter_still_arriving_is_a_table_of_its_header(self):
        for partial in ("| a | b |\n|", "| a | b |\n|-", "| a | b |\n| :-- | -"):
            with self.subTest(partial=partial):
                table = parse(partial)[-1]
                self.assertEqual((table.kind, table.cells, table.closed), ("table", (("a", "b"),), False))
        self.assertNotIn("table", kinds("| a | b |\n|-\n"), "an ended line is not still arriving")

    def test_unclosed_inline_code_and_link_stay_literal(self):
        self.assertEqual(inline("use `Sun"), ("use `Sun", []))
        self.assertEqual(inline("see [fill](https://exa"), ("see [fill](https://exa", []))


class InlineTests(unittest.TestCase):
    def test_code_spans_and_emphasis(self):
        plain, spans = inline("Set **Strength** to `2.5` in `Sun Light`.")
        self.assertEqual(plain, "Set Strength to 2.5 in Sun Light.")
        self.assertEqual([(plain[s:e], kind) for s, e, kind, _ in spans], [("2.5", "code"), ("Sun Light", "code")])

    def test_links_are_checked(self):
        plain, spans = inline("[docs](https://docs.blender.org/) and [bad](javascript:alert) <https://a.io/b>")
        self.assertEqual(plain, "docs and bad https://a.io/b")
        self.assertEqual([(plain[s:e], url) for s, e, kind, url in spans if kind == "link"],
                         [("docs", "https://docs.blender.org/"), ("https://a.io/b", "https://a.io/b")])

    def test_emphasis_inside_code_is_kept(self):
        self.assertEqual(inline("`a**b`")[0], "a**b")

    def test_arithmetic_stars_are_kept(self):
        self.assertEqual(inline("2*3*4")[0], "2*3*4")

    def test_plain_text_for_copy(self):
        self.assertEqual(plain_text("# Hi\n- **a** `b`\n```\nx = 1\n```"), "Hi\n• a b\nx = 1")


if __name__ == "__main__":
    unittest.main()
