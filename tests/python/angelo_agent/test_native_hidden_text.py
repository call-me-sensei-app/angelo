# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``hidden_text``: characters a person cannot see, by Unicode property with the contexts real text needs (break-it
review N3 of plan mode). Refused: every Default_Ignorable or format, private-use, unassigned or control character a
hand list missed or wrongly allowed. Allowed: what every one of Angelo's 20 catalogues and real writing hold (Thai word
breaks, Persian and Indic joiners, bidi marks in right-to-left text, emoji sequences). No Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))

from angelo_agent import hidden_text  # noqa: E402
from i18n import po  # noqa: E402


class HiddenTests(unittest.TestCase):
    def test_what_no_one_can_see_is_found(self):
        cases = {"soft hyphen": "co­op", "combining grapheme joiner": "a͏b", "Arabic letter mark in Latin":
                 "a؜b", "Hangul fillers": "ᅟᅠㅤﾠ", "Mongolian selectors": "a᠋b",
                 "variation selector after a letter": "a️b", "a selector first": "\ufe0f", "a text selector first": "\ufe0eStart", "ideographic selector": "一\U000e0100",
                 "tag characters": "hi\U000e0041\U000e007f", "deprecated formats": "a⁪b", "annotation": "a￹b",
                 "zero-width space in Latin": "a​b", "joiner in Latin": "a‍b", "non-joiner in Latin": "a‌b",
                 "marks in left-to-right text": "a‎b‏", "override": "a‮b", "isolate": "a⁦b",
                 "word joiner": "a⁠b", "BOM": "﻿a", "line separator": "a b", "NUL": "a\x00b",
                 "escape": "a\x1bb", "tab": "a\tb", "private use": "ab", "zero-width space at an edge": "​แผน",
                 "zero-width space beside a space": "แผน ​นี้"}
        for name, text in cases.items():
            with self.subTest(name):
                self.assertIsNotNone(hidden_text.find(text), repr(text))

    def test_what_real_writing_needs_is_allowed(self):
        cases = {"Thai word breaks": "วางแผน​ก่อน​เริ่ม", "Thai beside Latin": "Angelo​ยัง",
                 "Japanese line breaks": "マップを\u200bベイク", "Chinese": "计划\u200b已结束",
                 "Lao": "ລາວ​ພາສາ", "Khmer": "ខ្មែរ​ភាសា", "Myanmar": "မြန်မာ​စာ",
                 "Persian non-joiner": "می‌خواهم", "Hindi joiner": "क्‍ष", "Malayalam": "ന്‍ന",
                 "emoji family": "\U0001F468‍\U0001F469‍\U0001F467", "red heart": "❤️",
                 "keycap": "1️⃣", "Hebrew with a mark": "שלום‎ 2026", "Arabic with a mark": "مرحبا‏!",
                 "plain text": "Block out the body, then cut the windows."}
        for name, text in cases.items():
            with self.subTest(name):
                self.assertIsNone(hidden_text.find(text), repr(text))

    def test_newlines_only_where_lines_are_allowed(self):
        self.assertIsNotNone(hidden_text.find("one\ntwo"))
        self.assertIsNone(hidden_text.find("one\ntwo", newlines=True))
        self.assertIsNotNone(hidden_text.find("one\r\ntwo", newlines=True))

    def test_every_catalogue_in_every_language_reads_as_visible(self):
        """Angelo's 20 catalogues and the Arabic source (reading order), msgid and msgstr: nothing a translator wrote
        to be seen is taken for hidden (the hand list refused Thai's 34,531 word breaks)."""
        files = sorted((ROOT / "locale/angelo/po").glob("*.po")) + [ROOT / "locale/angelo/src/ar.po"]
        self.assertGreaterEqual(len(files), 21)
        checked = 0
        for path in files:
            _header, entries = po.read(str(path))
            for entry in entries:
                for text in (entry.id, entry.str):
                    found = hidden_text.find(text, newlines=True)
                    self.assertIsNone(found, "%s: %r has %s" % (path.name, text[:60],
                                                                hidden_text.name(text[found]) if found is not None else ""))
                    checked += 1
        self.assertGreater(checked, 100000)


if __name__ == "__main__":
    unittest.main()
