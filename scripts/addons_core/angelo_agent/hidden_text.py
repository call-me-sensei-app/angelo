# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Characters a person cannot see in text a model wrote and the person is shown: one check for every such field.

Decided by Unicode property, never a hand list (break-it review N3 of plan mode, 2026-11-18): a hand list refused the
zero-width space every Thai sentence breaks its words with, and passed variation selectors, the soft hyphen and the
Hangul fillers. A character is hidden when it is:

* a control (Cc), except a newline where lines are allowed;
* a format character (Cf), private use (Co), a surrogate (Cs), unassigned in this Python's Unicode (Cn), or a line or
  paragraph separator (Zl, Zp);
* Default_Ignorable_Code_Point (Unicode's DerivedCoreProperties: soft hyphen, combining grapheme joiner, Hangul
  fillers, variation selectors, tag characters ...), which Python's ``unicodedata`` does not expose, so its ranges
  are listed here from the standard (Unicode 15.1).

Allowed in the context that needs them, as real text uses them:

* ZERO WIDTH SPACE between words of a script written without spaces (Thai, Lao, Khmer, Myanmar, Tai Tham, Japanese,
  Chinese): beside a character of one, never at an edge or beside a space;
* ZERO WIDTH NON-JOINER and JOINER beside a letter of a joining or Indic script (Arabic, Syriac, N'Ko, Mongolian,
  the Indic scripts), or joining two emoji;
* LEFT-TO-RIGHT / RIGHT-TO-LEFT / ARABIC LETTER MARK in text that holds right-to-left letters;
* VARIATION SELECTOR-15 and -16 right after an emoji or a keycap base.

Bidirectional embeddings, overrides and isolates, tag characters and every other ignorable stay hidden. No bpy.
"""

import unicodedata

# Default_Ignorable_Code_Point, Unicode 15.1 DerivedCoreProperties.txt (ranges inclusive).
DEFAULT_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
                     (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
                     (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
                     (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})
ZWSP, ZWNJ, ZWJ, LRM, RLM, ALM, VS15, VS16 = "​", "‌", "‍", "‎", "‏", "؜", "︎", "️"
# Scripts written without spaces between words, where a zero-width space marks a word or line break: Thai, Lao,
# Myanmar, Khmer, Tai Tham, and Japanese and Chinese (kana, CJK ideographs and punctuation, fullwidth forms; Angelo's
# own Japanese catalogue breaks its lines this way).
UNSPACED = ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0xAA60, 0xAA7F), (0xA9E0, 0xA9FF), (0x1780, 0x17FF), (0x19E0, 0x19FF),
            (0x1A20, 0x1AAF), (0x3000, 0x30FF), (0x31F0, 0x31FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
            (0xFF00, 0xFFEF), (0x20000, 0x3134F))
# Joining and Indic scripts, where ZWNJ and ZWJ choose a letter's form.
JOINING = ((0x0600, 0x06FF), (0x0700, 0x074F), (0x0750, 0x077F), (0x07C0, 0x07FF), (0x0860, 0x08FF), (0x0900, 0x0DFF),
           (0x1800, 0x18AF), (0xA8E0, 0xA8FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFC), (0x10D00, 0x10D3F))


def _within(char, ranges):
    if not char:
        return False
    code = ord(char)
    return any(low <= code <= high for low, high in ranges)


def _ignorable(char):
    return _within(char, DEFAULT_IGNORABLE)


def _emoji(char):
    """An emoji or a symbol an emoji sequence joins (So, and the skin-tone modifiers); a coarse reading of
    Extended_Pictographic, which unicodedata does not expose either."""
    return bool(char) and (unicodedata.category(char) == "So" or 0x1F3FB <= ord(char) <= 0x1F3FF)


def _allowed(text, index):
    char = text[index]
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if char == ZWSP:
        spaced = not before or not after or before.isspace() or after.isspace()
        return not spaced and (_within(before, UNSPACED) or _within(after, UNSPACED))
    if char in (ZWNJ, ZWJ):
        if _within(before, JOINING) or _within(after, JOINING):
            return True
        return char == ZWJ and (_emoji(before) or before in (VS16,)) and _emoji(after)
    if char in (LRM, RLM, ALM):   # Only in text with right-to-left letters (never counting the marks themselves).
        return any(unicodedata.bidirectional(c) in ("R", "AL") and unicodedata.category(c).startswith("L")
                   for c in text)
    if char in (VS15, VS16):
        return _emoji(before) or (before != "" and before in "0123456789#*")   # Never at the start ("" is in every str).
    return False


def hidden(char, newlines=False):
    """Whether ``char`` is never seen, out of context (``find`` decides in context)."""
    if char == "\n":
        return not newlines
    return unicodedata.category(char) in HIDDEN_CATEGORIES or _ignorable(char)


def find(text, newlines=False):
    """The index of the first character of ``text`` a person cannot see there, or None. ``newlines``: a line break is
    allowed (a summary, a note); otherwise text is one line."""
    for index, char in enumerate(text):
        if hidden(char, newlines) and not _allowed(text, index):
            return index
    return None


def name(char):
    """``U+XXXX`` for a refusal that names the character without showing it."""
    return "U+%04X" % ord(char)
