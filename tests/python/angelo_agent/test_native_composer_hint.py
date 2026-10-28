# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer shows its hint whole: wrapped as a draft wraps and never cut mid-sentence (``chat_composer.hint_rows``).

Evidence: the away hint (``document_scene.AWAY``) was clipped to one row with an ellipsis in the session_rail GUI
capture (2026-10-25), and translations run longer than English. Here a fixed-width wrap stands in for the real fonts;
test_blender_session_rail draws the longest hint in the real composer at 1x and 2x.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import chat_composer, document_scene  # noqa: E402

CHARS = 10   # The stand-in wrap's row: this many characters per 100 units of width (any font would do).


def wrap(text, width, size, font, ordered=False):
    """Rows of whole words, at most ``width / 10`` characters each, with their offsets, as ``chat_view.wrap`` returns."""
    limit, rows, row, start = max(1, int(width * CHARS / 100)), [], "", 0
    for word in text.split(" "):
        candidate = (row + " " + word) if row else word
        if len(candidate) > limit and row:
            rows.append((row + " ", start))
            start += len(row) + 1
            row = word
        else:
            row = candidate
    return rows + [(row, start)]


def measured_wrap(text, width, size, font, ordered=True):
    """``canvas_wrap.iter_wrap`` with every character 10 units wide: right-to-left text in reading order with visual
    rows (``rtl_text``) when ``ordered``, else rows broken where the chat breaks them (``text_layout.line_break``)."""
    from angelo_agent import rtl_text, text_layout
    if ordered and rtl_text.has_rtl(text):
        return [(row, 0) for row in rtl_text.iter_rows_to_visual(text, lambda part: 10 * len(part), width)]
    rows, offset, limit = [], 0, max(1, int(width // 10))
    remaining = text
    while remaining:
        cut = text_layout.line_break(remaining, min(limit, len(remaining)))
        rows.append((remaining[:cut], offset))
        offset, remaining = offset + cut, remaining[cut:]
    return rows or [("", 0)]


def rows(text, width):
    return chat_composer.hint_rows(text, width, 12, wrap, None)


class HintTests(unittest.TestCase):
    def test_a_hint_that_fits_one_row_is_that_row(self):
        self.assertEqual(rows("Do anything", 400), ["Do anything"])

    def test_a_long_hint_is_drawn_whole_on_as_many_rows_as_it_needs(self):
        text = document_scene.AWAY.format(scene="Ein sehr langer Szenenname für das Wohnzimmer im Erdgeschoss")
        drawn = rows(text, 300)
        self.assertGreater(len(drawn), 1)
        self.assertLessEqual(len(drawn), chat_composer.HINT_ROWS)
        self.assertEqual(" ".join(drawn), text)   # Every word, nothing clipped, no ellipsis.

    def test_a_chat_too_narrow_shows_whole_sentences_then_an_ellipsis(self):
        text = "This chat works on scene 'Kitchen'. " + "Switch to that scene to continue right now please. " * 3
        drawn = rows(text.strip(), 196)   # 14 characters a row: the whole text needs more than the cap.
        self.assertLessEqual(len(drawn), chat_composer.HINT_ROWS)
        shown = " ".join(drawn)
        self.assertTrue(shown.endswith(".…"), shown)   # Ends after a whole sentence.
        self.assertTrue(shown.startswith("This chat works on scene 'Kitchen'."), shown)   # The fact, first and whole.

    def test_one_sentence_longer_than_the_cap_fills_it_and_says_it_goes_on(self):
        drawn = rows("word " * 200, 60)
        self.assertEqual(len(drawn), chat_composer.HINT_ROWS)
        self.assertTrue(drawn[-1].endswith("…"))

    def test_sentence_ends_in_the_scripts_angelo_ships(self):
        for end in (".", "!", "?", "。", "！", "？", "؟", "।"):
            self.assertEqual(chat_composer.sentence_ends("A" + end + " B"), [2], end)
        self.assertEqual(chat_composer.sentence_ends("scene 'v1.2' here"), [])   # A dot inside a word is no end.
        self.assertEqual(chat_composer.sentence_ends("Say \"stop.\" Then"), [11])   # After its closing quote.
        # Chinese and Japanese start the next sentence unspaced after a full-width end (break-it R5-L-2).
        self.assertEqual(chat_composer.sentence_ends("甲。乙！丙？丁"), [2, 4, 6])
        self.assertEqual(chat_composer.sentence_ends("「好。」次"), [4])
        self.assertEqual(chat_composer.sentence_ends("v1.2.3 a.b"), [])

    def test_an_early_cjk_sentence_that_fits_is_shown_whole(self):
        text = "甲" * 20 + "。" + "乙" * 200 + "。"   # No space after the first sentence's end, as Chinese is written.
        drawn = chat_composer.hint_rows(text, 156, 12, measured_wrap, None)
        self.assertEqual("".join(drawn), "甲" * 20 + "。…", drawn)   # Not eight rows cut mid-sentence.

    def test_the_ellipsis_is_measured_on_its_row(self):
        for text in ("乙" * 300, "wordy " * 200, "x" * 500):   # No sentence fits: as much as fits, then the ellipsis.
            drawn = chat_composer.hint_rows(text, 156, 12, measured_wrap, None)
            self.assertEqual(len(drawn), chat_composer.HINT_ROWS, text[:8])
            self.assertTrue(drawn[-1].endswith("…"), drawn)
            self.assertTrue(all(10 * len(row) <= 100 for row in drawn), drawn)   # Every row within the width.

    def test_the_fallback_cuts_near_its_fitted_end_in_mixed_scripts(self):
        """Break-it R4M1-R2-L-1: a space far back (a Latin word before a long Chinese run) is no place to cut."""
        for text in ("Blender " + "甲" * 200, "シーン 'Kitchen' " + "乙" * 300, "abc " + "x" * 500):
            drawn = chat_composer.hint_rows(text, 156, 12, measured_wrap, None)
            self.assertEqual(len(drawn), chat_composer.HINT_ROWS, drawn)   # Every row the composer has, used.
            self.assertTrue(all(10 * len(row) <= 100 for row in drawn), drawn)
            self.assertGreaterEqual(len("".join(drawn)), 7 * 9, drawn)   # The fitting text, not one word of it.
        drawn = chat_composer.hint_rows("word " * 200, 156, 12, measured_wrap, None)
        self.assertTrue(all(row.rstrip("…").split(" ")[-1] == "word" for row in drawn), drawn)   # Whole words.

    def test_an_arabic_hint_reads_from_its_first_row(self):
        from angelo_agent import rtl_text
        logical = ("هذا نص عربي يوضح المشهد الحالي وطريقة العودة إليه. " * 3).strip()
        visual = rtl_text.to_visual(logical)   # As the shipped catalogue holds it (pre-shaped, visual order).
        drawn = chat_composer.hint_rows(visual, 356, 12, measured_wrap, None)
        expected = [row.rstrip() for row in rtl_text.iter_rows_to_visual(visual, lambda part: 10 * len(part), 300)]
        self.assertGreater(len(drawn), 1)
        self.assertEqual(drawn, expected)
        self.assertEqual(" ".join(rtl_text.to_logical(row) for row in drawn), logical)   # Rows in reading order.

    def test_a_long_arabic_hint_keeps_its_first_sentence_in_reading_order(self):
        from angelo_agent import rtl_text
        first = "هذا المحادثة تعمل على مشهد المطبخ."
        logical = first + " " + " ".join(["انتقل إلى ذلك المشهد للمتابعة الآن من فضلك."] * 12)
        drawn = chat_composer.hint_rows(rtl_text.to_visual(logical), 196, 12, measured_wrap, None)
        self.assertLessEqual(len(drawn), chat_composer.HINT_ROWS)
        read = " ".join(rtl_text.to_logical(row) for row in drawn)
        self.assertTrue(read.startswith(first) and read.endswith(".…"), read)   # The fact first; whole sentences.

    def test_the_composer_holds_the_hints_rows_while_the_draft_is_empty(self):
        from unittest import mock
        with mock.patch.object(chat_composer, "hint", lambda controller: "one two three four five six"):
            self.assertEqual(chat_composer.composer_rows("", [("", 0)], None, 156, 12, wrap, None), 3)
            self.assertEqual(chat_composer.composer_rows("typed", [("typed", 0)], None, 156, 12, wrap, None), 1)
        source = (ROOT / "scripts/addons_core/angelo_agent/chat_view.py").read_text(encoding="utf-8")
        self.assertIn("shown_rows := chat_composer.composer_rows(draft, draft_lines, controller", source)
        self.assertIn("min(8, shown_rows)", source)
        self.assertEqual(chat_composer.HINT_ROWS, 8)   # The draft's cap (chat_layout.composer_layout).


if __name__ == "__main__":
    unittest.main()
