# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Line breaking for translated text, on canvases and in Blender's native labels. No ``bpy``.

English breaks at spaces. Chinese and Japanese are written without them: a line
may end between any two characters, but not before closing punctuation (。、」)
or after opening punctuation (「（), so breaking at the last space would push a
whole sentence onto the next line after a lone Latin word ("Blender"). A line
never starts with a combining mark (Thai vowels and tone marks, Devanagari
vowel signs), which belongs to the character before it. Korean and every other
script with spaces break at spaces. Thai writes words without spaces and Blender
has no dictionary to find them, so the Thai catalogue marks word boundaries with
ZERO WIDTH SPACE (U+200B, drawn as nothing by every bundled font), and a line may
break after one as after a space. Right-to-left text is wrapped in reading order
by ``rtl_text.rows_to_visual``.
"""

import unicodedata

from . import rtl_text

_NO_START = set("、。，．・：；？！）〕］｝〉》」』】〙〗ー…‥々ゝゞヽヾぁぃぅぇぉっゃゅょァィゥェォッャュョ%,.:;!?)]}")
_NO_END = set("（〔［｛〈《「『【〘〖([{")
BREAK = "\u200b"  # ZERO WIDTH SPACE: an invisible break opportunity between Thai words.


def _tight(character):
    """Characters of scripts written without spaces between words (CJK ideographs, kana, full-width forms)."""
    code = ord(character)
    return (0x3000 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFFEF or 0x20000 <= code <= 0x2FA1F)


def breaks_anywhere(character):
    """May a line break on either side of ``character``? Chinese and Japanese characters (no word spaces)."""
    return _tight(character)


def _mark(character):
    return unicodedata.category(character) in ("Mn", "Mc", "Me")


def line_break(text, fits):
    """Where the first line of ``text`` ends, given that ``text[:fits]`` is the longest prefix that fits."""
    if fits >= len(text):
        return len(text)
    cut = max(1, fits)
    if _tight(text[cut - 1]) or _tight(text[cut]):
        while cut > 1 and (text[cut] in _NO_START or text[cut - 1] in _NO_END or _mark(text[cut])):
            cut -= 1
        return _after_marks(text, cut)
    space = max(text.rfind(" ", 0, cut + 1), text.rfind(BREAK, 0, cut + 1))
    if space > 0:
        return space + 1
    while cut > 1 and _mark(text[cut]):
        cut -= 1
    return _after_marks(text, cut)


def two_lines(text, measure, width):
    """A short label (a menu tile's title) as one line, or as two balanced lines, each ``measure``-d at most ``width``;
    None when it cannot be two lines without cutting a word, so the caller gives it more room, never an ellipsis.

    A line breaks at a space, after a hyphen or at a ZERO WIDTH SPACE. Chinese and Japanese titles with neither
    space nor mark may break between any two characters (never before closing punctuation, ー or small kana, never
    after opening punctuation); a title that marks its word boundaries (U+200B) breaks only there, so
    "ライブ​ブラウザー​プレビュー" never splits inside "ブラウザー". Of the breaks that fit, the one with the
    shorter longer line wins."""
    text = text.strip(" " + BREAK)
    if measure(text) <= width:
        return [text]
    if rtl_text.has_rtl(text):   # Broken in reading order; each line comes back in Blender's visual form (Arabic).
        logical = rtl_text.to_logical(text) if rtl_text.is_visual(text) else text
        lines = _balanced(logical, lambda part: measure(rtl_text.to_visual(part)), width)
        return [rtl_text.to_visual(line) for line in lines] if lines else None
    return _balanced(text, measure, width)


def _balanced(text, measure, width):
    """``two_lines``' choice of break, on reading-order text that does not fit one line."""
    tight = any(_tight(ch) for ch in text) and BREAK not in text and " " not in text
    best = None
    for cut in range(1, len(text)):
        before, after = text[cut - 1], text[cut]
        if before in (" ", BREAK) or (before == "-" and cut > 1 and after not in " -"):
            pass
        elif not (tight and (_tight(before) or _tight(after)) and after not in _NO_START and before not in _NO_END
                  and not _mark(after)):
            continue
        first, second = text[:cut].rstrip(" " + BREAK), text[cut:].lstrip(" " + BREAK)
        if first and second:
            widest = max(measure(first), measure(second))
            if widest <= width and (best is None or widest < best[0]):
                best = (widest, [first, second])
    return best[1] if best else None


def _after_marks(text, cut):
    """At the very start of a line there is nowhere to go back to: keep the marks with their letter instead."""
    while cut < len(text) and _mark(text[cut]):
        cut += 1
    return cut


def columns(text):
    """Width in Latin character columns: an East Asian wide character takes two, a combining mark none."""
    return sum(0 if _mark(ch) or ch == BREAK else 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def label_rows(text, width=96):
    """``text`` as rows of about ``width`` columns, for consecutive Blender labels (which never wrap).

    Right-to-left text wraps in reading order and comes back in Blender's visual form.
    """
    if rtl_text.has_rtl(text):
        return rtl_text.rows_to_visual(text, columns, width)
    rows = []
    for paragraph in text.split("\n"):
        remaining = paragraph
        while columns(remaining) > width:
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if columns(remaining[:middle]) <= width:
                    low = middle
                else:
                    high = middle - 1
            cut = line_break(remaining, low)
            rows.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip(" ")
        rows.append(remaining)
    return rows
