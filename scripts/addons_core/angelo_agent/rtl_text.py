# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Right-to-left text for Blender's left-to-right text engine (Arabic).

Blender 5.2 draws text left to right with one glyph per character: it does no
bidirectional reordering and no contextual shaping (its ``WITH_FRIBIDI`` and
``WITH_HARFBUZZ`` options are not used by any source file). Its own Arabic
catalogue is therefore stored *pre-shaped and in visual order* (joined
presentation forms, characters reversed), and so is Angelo's.

* ``to_visual(logical)`` turns typed or authored text into that visual form.
* ``to_logical(visual)`` is its exact inverse. The canvas needs it because a
  paragraph must be *wrapped in reading order* and only then put in visual
  order, row by row; wrapping a visual string would put the end of the
  sentence on the first row.
* ``is_visual`` tells pre-shaped catalogue text (presentation forms, U+FB50
  to U+FEFF) from raw Arabic (base letters, U+0600 to U+06FF).

This is a deliberately small subset of the Unicode bidirectional algorithm: a
right-to-left paragraph made of right-to-left runs and left-to-right runs
(Latin names, numbers, ``{placeholders}``, shortcuts), with neutral characters
between two runs of the same direction joining them and every other neutral
taking the paragraph direction. No ``bpy`` and no third-party packages.
"""

import re
import unicodedata

_PLACEHOLDER = re.compile(r"\{[A-Za-z_0-9.:!<>^=+\- ]*\}|%(?:\([A-Za-z_]+\))?[-+ #0]*\d*(?:\.\d+)?[sdifxXeEgGc]|%%")
_MIRRORED = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"}
_NUMBER_JOINERS = ".,:/×–-"  # Between digits they stay with the number: 3.5, 12:30, 1,000, 1920×1080, 1–240.
_FORMS = {}  # base letter -> {"isolated"|"final"|"initial"|"medial": presentation form}
_BASE_OF = {}  # presentation form -> base letters
_JOINS_NEXT = set()  # dual-joining letters: connect to the following letter
_JOINS_PREV = set()  # dual- and right-joining letters: connect to the preceding letter
_LAM_ALEF = {}
_TRANSPARENT = set(range(0x064B, 0x0660)) | {0x0670} | set(range(0x06D6, 0x06DD)) | set(range(0x06DF, 0x06E5))


def _build_tables():
    for code in list(range(0xFB50, 0xFDFF)) + list(range(0xFE70, 0xFEFD)):
        decomposition = unicodedata.decomposition(chr(code))
        if not decomposition.startswith("<"):
            continue
        tag, _, rest = decomposition.partition("> ")
        tag = tag[1:]
        bases = "".join(chr(int(part, 16)) for part in rest.split())
        if tag not in ("isolated", "final", "initial", "medial"):
            continue
        _BASE_OF[chr(code)] = bases
        if len(bases) == 1:
            _FORMS.setdefault(bases, {}).setdefault(tag, chr(code))
        elif bases[0] == "ل" and bases[1] in "آأإا":
            _LAM_ALEF.setdefault(bases[1], {})[tag] = chr(code)
    for base, forms in _FORMS.items():
        if "initial" in forms and "medial" in forms:
            _JOINS_NEXT.add(base)
            _JOINS_PREV.add(base)
        elif "final" in forms:
            _JOINS_PREV.add(base)
    # Tatweel (kashida) joins on both sides but has no presentation form; Blender's own catalogue,
    # shaped with fribidi, also leaves the standalone hamza (U+0621) unshaped.
    _JOINS_NEXT.add("\u0640")
    _JOINS_PREV.add("\u0640")
    _FORMS.pop("\u0621", None)


_build_tables()


def is_visual(text):
    """True when ``text`` holds pre-shaped Arabic presentation forms."""
    return any("ﭐ" <= ch <= "﻿" and ch != "﻿" for ch in text)


def has_rtl(text):
    """True when ``text`` has right-to-left letters, raw or pre-shaped."""
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text)


def _transparent(ch):
    return ord(ch) in _TRANSPARENT


def shape(text):
    """Replace Arabic base letters with their joined presentation forms (logical order kept)."""
    out, count, skip = [], len(text), set()
    for index, ch in enumerate(text):
        if index in skip:
            continue
        if ch not in _FORMS or ch in _BASE_OF:
            out.append(ch)
            continue
        previous = next((text[i] for i in range(index - 1, -1, -1) if not _transparent(text[i])), "")
        following = next((text[i] for i in range(index + 1, count) if not _transparent(text[i])), "")
        joined_before = previous in _JOINS_NEXT
        if ch == "ل" and following in _LAM_ALEF:  # Lam + alef is one ligature glyph.
            out.append(_LAM_ALEF[following]["final" if joined_before else "isolated"])
            # The alef is in the ligature; a vowel mark between them ("اتصالًا") stays after it, on the glyph.
            skip.add(next(i for i in range(index + 1, count) if not _transparent(text[i])))
            continue
        joined_after = ch in _JOINS_NEXT and following in _JOINS_PREV
        forms = _FORMS[ch]
        if joined_before and joined_after and "medial" in forms:
            form = "medial"
        elif joined_before and "final" in forms:
            form = "final"
        elif joined_after and "initial" in forms:
            form = "initial"
        else:
            form = "isolated"
        out.append(forms.get(form) or forms.get("isolated") or ch)
    return "".join(out)


def unshape(text):
    """Inverse of ``shape``: presentation forms back to base letters.

    Marks after a lam-alef ligature belong to the lam (``shape`` keeps them there), so they go back
    between the two letters. Text that put a mark after the alef ("ـلاً") comes back as "ـلًا", the same
    word drawn the same way.
    """
    out, index, count = [], 0, len(text)
    while index < count:
        ch = text[index]
        base = _BASE_OF.get(ch, ch)
        if len(base) == 2 and base[0] == "ل":
            end = index + 1
            while end < count and _transparent(text[end]):
                end += 1
            out.append(base[0] + text[index + 1:end] + base[1])
            index = end
            continue
        if ch != "﻿":
            out.append(base)
        index += 1
    return "".join(out)


def _classify(text):
    """Per-character direction class: ``R`` right-to-left, ``L`` left-to-right, ``N`` neutral."""
    classes, protected = [None] * len(text), set()
    for match in _PLACEHOLDER.finditer(text):
        for position in range(match.start(), match.end()):
            classes[position] = "L"
            protected.add(position)
    for position, ch in enumerate(text):
        if classes[position] is not None:
            continue
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL"):
            classes[position] = "R"
        elif bidi == "NSM" and position and classes[position - 1] == "R":
            classes[position] = "R"
        elif bidi == "L":
            classes[position] = "L"
        elif bidi in ("EN", "AN"):
            classes[position] = "D"
        else:
            classes[position] = "N"
    for position, ch in enumerate(text):  # Numbers keep their separators and signs.
        if (classes[position] == "N" and ch in _NUMBER_JOINERS and 0 < position < len(text) - 1
                and text[position - 1].isdigit() and text[position + 1].isdigit()):
            classes[position] = "D"
        elif classes[position] == "N" and ch in "%+-" and position + 1 < len(text) and text[position + 1].isdigit() \
                and (position == 0 or classes[position - 1] != "R" or ch != "-"):
            classes[position] = "D" if ch != "-" else "N"
        elif classes[position] == "N" and ch == "%" and position and text[position - 1].isdigit():
            classes[position] = "D"
    _numbers_by_latin(classes)
    resolved, count = list(classes), len(text)
    position = 0
    while position < count:
        if resolved[position] != "N":
            position += 1
            continue
        end = position
        while end < count and resolved[end] == "N":
            end += 1
        # A number counts as right to left for the neutrals around it (as in the Unicode algorithm, N1).
        before = ("R" if resolved[position - 1] == "D" else resolved[position - 1]) if position else "R"
        after = ("R" if resolved[end] == "D" else resolved[end]) if end < count else "R"
        direction = before if before == after else "R"
        for index in range(position, end):
            resolved[index] = direction
        position = end
    return resolved


def _numbers_by_latin(classes):
    """A number next to Latin text (only neutrals between) belongs to it: "Angelo 1.1", "Blender 5.2".

    Otherwise a number is its own left-to-right run placed in right-to-left order, so numbers separated by
    " · " keep their places ("الأطر 1–240 · 1920×1080 · 128 عينةً"). The test looks both ways, unlike the
    Unicode algorithm's W7, so that reversing the runs gives back the same runs (``to_logical`` is exact).
    """
    count, following = len(classes), None
    right = [None] * count
    for index in range(count - 1, -1, -1):
        right[index] = following
        if classes[index] in ("L", "R"):
            following = classes[index]
    position, left = 0, None
    while position < count:
        if classes[position] != "D":
            if classes[position] in ("L", "R"):
                left = classes[position]
            position += 1
            continue
        end = position
        while end < count and classes[end] == "D":
            end += 1
        # Preserve the previous scan's propagation from already converted runs,
        # while finding the following strong direction once, not per number.
        if "L" in (left, right[end - 1]):
            classes[position:end] = ["L"] * (end - position)
            left = "L"
        position = end


def _runs(text, classes):
    runs, start = [], 0
    for position in range(1, len(text) + 1):
        if position == len(text) or classes[position] != classes[start]:
            runs.append((classes[start], text[start:position]))
            start = position
    return runs


def _mirror(text):
    return "".join(_MIRRORED.get(ch, ch) for ch in text)


def _reverse(run):
    """A right-to-left run in the other order, letter by letter, each letter keeping its marks after it.

    Blender draws a combining mark over the glyph before it, so a vowel mark or shadda must follow its
    letter in visual order too (as fribidi's REORDER_NSM stores Blender's own catalogue). Reversing a letter
    and its marks again restores reading order, so this is its own inverse.
    """
    clusters = []
    for ch in run:
        if clusters and _transparent(ch):
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return "".join(reversed(clusters))


def to_visual(logical):
    """Reading-order text to Blender's visual form: shaped, runs reversed. One call per line."""
    if not has_rtl(logical) and not any(ch in logical for ch in "٠١"):
        return logical
    lines = []
    for line in logical.split("\n"):
        if not has_rtl(line):
            lines.append(line)
            continue
        shaped = shape(line)
        runs = _runs(shaped, _classify(shaped))
        lines.append("".join((_mirror(_reverse(run)) if direction == "R" else run)
                             for direction, run in reversed(runs)))
    return "\n".join(lines)


def to_logical(visual):
    """Exact inverse of ``to_visual``."""
    lines = []
    for line in visual.split("\n"):
        if not has_rtl(line):
            lines.append(line)
            continue
        runs = _runs(line, _classify(line))
        lines.append(unshape("".join((_mirror(_reverse(run)) if direction == "R" else run)
                                     for direction, run in reversed(runs))))
    return "\n".join(lines)


def rows_to_visual(value, measure, width):
    """Wrap ``value`` (raw or pre-shaped) in reading order within ``width``; rows come back visual.

    ``measure(text)`` is the width of drawn (visual) text. Returns a list of rows.
    """
    return list(iter_rows_to_visual(value, measure, width))


def iter_rows_to_visual(value, measure, width):
    """Yield the same visual rows, allowing the native review timer to pause."""
    for paragraph in value.split("\n"):
        logical = to_logical(paragraph) if is_visual(paragraph) else paragraph
        if not logical:
            yield ""
            continue
        remaining = logical
        while remaining:
            low, high = 1, min(64, len(remaining))
            # Probe visible-row prefixes, not half of a long exact control.
            while high < len(remaining) and measure(to_visual(remaining[:high])) <= width:
                low, high = high, min(len(remaining), high * 2)
            while low < high:
                middle = (low + high + 1) // 2
                if measure(to_visual(remaining[:middle])) <= width:
                    low = middle
                else:
                    high = middle - 1
            cut = low
            if cut < len(remaining):
                space = remaining.rfind(" ", 0, cut + 1)
                if space > 0:
                    cut = space + 1
            yield to_visual(remaining[:cut].rstrip())
            remaining = remaining[cut:]
