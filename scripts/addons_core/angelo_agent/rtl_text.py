# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Right-to-left text for Blender's left-to-right text engine (Arabic).

Blender 5.2 draws text left to right with one glyph per character: it does no
bidirectional reordering and no contextual shaping (its ``WITH_FRIBIDI`` and
``WITH_HARFBUZZ`` options are not used by any source file). Its own Arabic
catalogue is therefore stored *pre-shaped and in visual order* (joined
presentation forms, characters reversed), and so is Angelo's.

* ``to_visual(logical)`` turns typed or authored text into that visual form.
* ``to_logical(visual)`` is its inverse (``to_logical`` says exactly when it is
  exact). The canvas needs it because a
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

A filled-in value (a path, a share, a file or model name) is laid out as one
unit, as Unicode's isolates are: ``isolate(value)`` wraps it in FIRST STRONG
ISOLATE ... POP DIRECTIONAL ISOLATE (U+2068 ... U+2069), and ``to_visual``
gives its neutral characters the value's own first strong direction, so
"\\\\server\\share" or "/srv/a/b.blend" keeps its leading and trailing marks
in place inside an Arabic sentence. To the sentence around it the unit is one
character of that direction; a value with no strong character is not isolated
(it is laid out as before). Explicit LEFT-TO-RIGHT and RIGHT-TO-LEFT ISOLATE
(U+2066, U+2067) are honoured too. No isolate mark ever reaches the drawn text.
Visual text cannot say where a unit was, so ``to_visual`` remembers, for the
lines it laid out with units, their reading order with explicit isolates:
``to_logical(visual)`` gives back the exact sentence (no marks), and
``to_logical(visual, isolates=True)`` the marked one, which wrapping
(``rows_to_visual``) and re-laying out use so a unit cut over two rows stays
one unit on each. The isolate marks and the remembered lines are
``rtl_isolate``'s; its public helpers are this module's too (``rtl_text.isolate``).
"""

import re
import unicodedata

from .rtl_isolate import (_LRI, _MARKS, _PDI, _RLI, _explicit, _recall, _remember, carry_isolates,  # noqa: F401
                          isolate, isolated_tail, marked_index, open_isolates, probing, strip_isolates)

_PLACEHOLDER = re.compile(r"\{[A-Za-z_0-9.:!<>^=+\- ]*\}|%(?:\([A-Za-z_]+\))?[-+ #0]*\d*(?:\.\d+)?[sdifxXeEgGc]|%%")
_MIRRORED = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"}
_NUMBER_JOINERS = ".,:/×–-"  # Between digits they stay with the number: 3.5, 12:30, 1,000, 1920×1080, 1–240.
_FORMS = {}  # base letter -> {"isolated"|"final"|"initial"|"medial": presentation form}
_BASE_OF = {}  # presentation form -> base letters
_JOINS_NEXT = set()  # dual-joining letters: connect to the following letter
_JOINS_PREV = set()  # dual- and right-joining letters: connect to the preceding letter
_LAM_ALEF = {}
_TRANSPARENT = set(range(0x064B, 0x0660)) | {0x0670} | set(range(0x06D6, 0x06DD)) | set(range(0x06DF, 0x06E5))
_UNIT = "￼"  # Stands for one laid-out isolate while the text around it is classified (its class is fixed).
_PRESENTATION = re.compile("[\ufb50-\ufdff\ufe70-\ufefc]")   # Arabic Presentation Forms-A and -B, nothing else.


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
    """True when ``text`` holds pre-shaped Arabic presentation forms (U+FB50-U+FDFF, U+FE70-U+FEFC).

    Only those blocks: variation selectors ("❤️"), vertical, half-mark, compatibility and small forms and the byte
    order mark (U+FE00-U+FE6F, U+FEFF) sit between them and are not Arabic.
    """
    return _PRESENTATION.search(text) is not None


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


def _classify(text, paragraph="R", fixed=None):
    """Per-character direction class: ``R`` right-to-left, ``L`` left-to-right, ``D`` a number.

    ``paragraph`` is the direction neutrals take at the ends and between runs of different directions; ``fixed``
    maps positions to a class decided beforehand (a laid-out isolate, ``_UNIT``).
    """
    classes, protected = [None] * len(text), set()
    for position, direction in (fixed or {}).items():
        classes[position] = direction
        protected.add(position)
    for match in _PLACEHOLDER.finditer(text):
        for position in range(match.start(), match.end()):
            if position not in protected:
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
        before = ("R" if resolved[position - 1] == "D" else resolved[position - 1]) if position else paragraph
        after = ("R" if resolved[end] == "D" else resolved[end]) if end < count else paragraph
        direction = before if before == after else paragraph
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
    """Reading-order text to Blender's visual form: shaped, runs reversed, isolates as units. One call per line."""
    return _to_visual(logical, True)


def _to_visual(logical, remember):
    """``to_visual``; ``remember=False`` for a form only measured, never drawn or read back (a wrapping probe)."""
    if not has_rtl(logical) and not any(ch in logical for ch in "٠١"):
        return strip_isolates(logical)
    lines = []
    for line in logical.split("\n"):
        if not has_rtl(line):
            lines.append(strip_isolates(line))
            continue
        explicit = _explicit(line)   # Unchanged without isolate marks; an isolate with no strong letter is unwrapped.
        if not any(mark in explicit for mark in _MARKS):
            shaped = shape(explicit)
            runs = _runs(shaped, _classify(shaped))
            lines.append("".join((_mirror(_reverse(run)) if direction == "R" else run)
                                 for direction, run in reversed(runs)))
            continue
        visual = _layout(shape(explicit), "R")
        if remember:
            _remember(visual, explicit)
        lines.append(visual)
    return "\n".join(lines)


def _layout(text, direction):
    """Visual form of a shaped reading-order line with explicit, balanced isolates in a ``direction`` paragraph.

    Each isolate is laid out on its own, in its own direction, and is one character of that direction to the
    text around it (``_UNIT``, its class fixed). A right-to-left paragraph reverses the order of its runs; a
    left-to-right one keeps it and reverses each stretch of right-to-left runs and the numbers among them.
    """
    flat, units, position = [], {}, 0
    while position < len(text):
        ch = text[position]
        if ch == _PDI:   # Balanced by ``_explicit``; a stray one never reaches here, but is never drawn either.
            position += 1
            continue
        if ch not in (_LRI, _RLI):
            flat.append(ch)
            position += 1
            continue
        depth, end = 1, position + 1
        while end < len(text) and depth:
            depth += text[end] in (_LRI, _RLI)
            depth -= text[end] == _PDI
            end += 1
        inner = "L" if ch == _LRI else "R"
        units[len(flat)] = (inner, _layout(text[position + 1:end - 1 if not depth else end], inner))
        flat.append(_UNIT)
        position = end
    flat = "".join(flat)
    classes = _classify(flat, direction, {index: unit[0] for index, unit in units.items()})
    tokens = [units[index][1] if index in units else ch for index, ch in enumerate(flat)]
    runs, start = [], 0
    for index in range(1, len(tokens) + 1):
        if index == len(tokens) or classes[index] != classes[start]:
            runs.append((classes[start], tokens[start:index]))
            start = index
    if direction == "R":
        return "".join(_reverse_tokens(run) if kind == "R" else "".join(run) for kind, run in reversed(runs))
    out, stretch = [], []
    for kind, run in runs + [("L", None)]:
        if kind != "L":
            stretch.append((kind, run))
            continue
        out.extend(_reverse_tokens(part) if part_kind == "R" else "".join(part)
                   for part_kind, part in reversed(stretch))
        stretch = []
        if run is not None:
            out.append("".join(run))
    return "".join(out)


def _reverse_tokens(tokens):
    """``_mirror(_reverse(run))`` for a run whose laid-out isolates are single tokens (never mirrored inside)."""
    clusters = []
    for token in tokens:
        if clusters and len(token) == 1 and _transparent(token):
            clusters[-1] += token
        else:
            clusters.append(_MIRRORED.get(token, token) if len(token) == 1 else token)
    return "".join(reversed(clusters))


def to_logical(visual, isolates=False):
    """The reading-order text of ``visual`` (``to_visual``'s form), without isolate marks.

    ``to_logical(to_visual(line)) == line`` for a line in base letters (not pre-shaped) without isolate marks, except
    where a "+", "-" or "%" is bound to a number on one side only (``_classify``'s sign rules, as in "1+(ش") or a
    printf directive sits among neutrals. A line ``to_visual`` laid out with isolated units comes back exactly as it
    was given while it is remembered (the 2048 most recently laid out or read back lines; visual text cannot say
    where a unit was): once forgotten it reads as plain visual text, and a line without units whose visual form is
    the same as a remembered line's reads as that line. ``isolates=True`` keeps their explicit marks,
    for text that is laid out again (wrapped, cut, joined) and must keep each unit whole.
    """
    lines = []
    for line in visual.split("\n"):
        marked = _recall(line)
        if marked is not None:
            lines.append(marked if isolates else strip_isolates(marked))
            continue
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
    """Yield the same visual rows, allowing the native review timer to pause.

    An isolated unit (``isolate``) cut between two rows is closed at the end of the first and opened again, in the
    same direction, at the start of the next (``open_isolates``).
    """
    for paragraph in value.split("\n"):
        logical = to_logical(paragraph, isolates=True) if is_visual(paragraph) else _explicit(paragraph)
        if not strip_isolates(logical):
            yield ""
            continue
        remaining = logical
        while remaining:
            lead = len(remaining) - len(remaining.lstrip(_MARKS))   # Isolates opened here are not a row's first letter.
            low, high = lead + 1, min(lead + 64, len(remaining))
            # Probe visible-row prefixes, not half of a long exact control.
            while high < len(remaining) and measure(_to_visual(remaining[:high], False)) <= width:
                low, high = high, min(len(remaining), high * 2)
            while low < high:
                middle = (low + high + 1) // 2
                if measure(_to_visual(remaining[:middle], False)) <= width:
                    low = middle
                else:
                    high = middle - 1
            cut = low
            if cut < len(remaining):
                space = remaining.rfind(" ", 0, cut + 1)
                if space > lead:
                    cut = space + 1
            row, rest = remaining[:cut], remaining[cut:]
            yield to_visual(row.rstrip())
            carried = open_isolates(row)
            remaining = carried + rest if strip_isolates(rest) else ""
