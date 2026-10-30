# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A markdown table in an assistant reply, laid out for the chat's width. Pure: no Blender module, so host tests
drive it with their own measuring.

API:
    layout(cells, aligns, width, size, line_height, measure, wrap) -> (ops, height)
        ``cells`` and ``aligns`` are a ``markdown_blocks`` "table" block's. ``measure(text)`` is a width as the
        reply's font draws it at ``size``; ``wrap(text, width)`` returns rows ``(value, source offset)`` as
        ``chat_view.wrap`` does. ``ops`` use the reply's op format (``chat_replies``): one ``("table", 0, dy,
        width, height, columns, rows, header)`` frame first (``columns`` are the inner column edges' x, ``rows``
        the inner row edges' dy, ``header`` the header row's height, 0 when there is none), then a ``("runs", x,
        dy, runs, size, role)`` per text row, ``dy`` being its baseline below the table's top.
    row_runs(value, offset, spans) -> runs
        A wrapped row's text split into (text, kind, url) runs by its source's inline spans.

A table that fits is a grid: each column as wide as its widest cell, the header on a light fill. When the cells
are wider than the chat, a column narrower than its fair share keeps its width and the widest columns share the
rest, wrapping their text between words (``column_widths``). A grid column is never narrower than its longest
word (up to ``LONG_TOKEN``: a longer token, such as a URL, may break), so no ordinary word is cut in two. When the
columns cannot all hold their longest words, or the chat has less than ``MIN_COLUMN`` per column, the table stacks
instead: one record per body row, each cell on its own line as "Header: value", so nothing is clipped or cut. Inline code, links and emphasis inside a cell are drawn as in a
paragraph; a cell with right-to-left text is drawn whole, as a paragraph's is (``canvas_rtl``), and a mirrored
canvas mirrors the grid.
"""

import re

from . import canvas_rtl, text_layout
from .markdown_blocks import inline

PAD_X = 10        # A cell's text inset from its column edges.
PAD_Y = 5         # Space above a cell's first row and below its last.
MIN_COLUMN = 56   # The narrowest readable column of text: below it per column, the table stacks.
CODE_WRAP = 0.86  # A cell with inline code wraps a little narrower: mono chips are wider (as chat_replies).
LONG_TOKEN = 180  # A token wider than this (a URL, a hash) may break inside a column rather than stack the table.
_BREAKS = re.compile("[\\s" + text_layout.BREAK + "]+")   # Where a row may break: spaces and Thai word marks.


def row_runs(value, offset, spans):
    """A row's text split into (text, kind, url) runs by the paragraph's inline spans."""
    runs, cursor, end = [], 0, len(value)
    for start, finish, kind, url in spans:
        begin, stop = max(start - offset, 0), min(finish - offset, end)
        if stop <= begin or begin < cursor:
            continue
        if begin > cursor:
            runs.append((value[cursor:begin], "", ""))
        runs.append((value[begin:stop], kind, url))
        cursor = stop
    if cursor < end:
        runs.append((value[cursor:], "", ""))
    return tuple(runs)


def _text_rows(plain, spans, width, wrap):
    """A cell's wrapped rows as runs; right-to-left text whole (its rows come back visual)."""
    if not plain:
        return ()
    rtl = canvas_rtl.has_rtl(plain)
    narrow = any(span[2] == "code" for span in spans)
    rows = wrap(plain, max(1, width * (CODE_WRAP if narrow else 1)))
    return tuple(((value.rstrip(), "", ""),) if rtl else row_runs(value.rstrip(), offset, spans)
                 for value, offset in rows)


def _longest_word(plain, measure):
    """The widest piece of ``plain`` a row can't break: a word between spaces (``text_layout.line_break``); in
    Chinese and Japanese, where a row may break between any two characters, one character."""
    widest = 0.0
    for word in _BREAKS.split(plain):
        if any(text_layout.breaks_anywhere(char) for char in word):
            widest = max([widest] + [measure(char) for char in word])
        elif word:
            widest = max(widest, measure(word))
    return widest


def _fill_above(floors, natural, room):
    """Widths between each column's floor and its natural width, summing to ``room``: the widest columns share it,
    capped at one common width as high as fits, and no column drops below its floor."""
    def total(cap):
        return sum(max(floor, min(wide, cap)) for floor, wide in zip(floors, natural))
    low, high = 0.0, max(natural)
    for _ in range(48):
        middle = (low + high) / 2
        low, high = (middle, high) if total(middle) <= room else (low, middle)
    return [max(floor, min(wide, low)) for floor, wide in zip(floors, natural)]


def column_widths(parsed, width, measure):
    """Each column's text width for a table of ``parsed`` cells ((plain, spans) rows), or None when it must stack.

    A column narrower than its fair share keeps its natural width, so short columns ("Base Color") never wrap; the
    widest columns share what is left and wrap their text in it, each at least as wide as its longest word."""
    columns = len(parsed[0])
    room = width - columns * 2 * PAD_X
    if room < columns * MIN_COLUMN:
        return None
    natural = [max(1.0, max(measure(row[j][0]) for row in parsed)) for j in range(columns)]
    if sum(natural) <= room:
        return natural
    floors = []
    for j in range(columns):
        longest = min(LONG_TOKEN, max(_longest_word(row[j][0], measure) for row in parsed))
        code = any(span[2] == "code" for row in parsed for span in row[j][1])
        floors.append(min(natural[j], longest / (CODE_WRAP if code else 1) + 1))   # +1: whole-pixel rounding.
    if sum(floors) > room:
        return None   # Some column would cut a word: the stacked records keep every word whole.
    return _fill_above(floors, natural, room)


def _aligned(x, column, runs, align, measure):
    if align not in {"right", "center"}:
        return x
    used = sum(measure(value) for value, _kind, _url in runs)
    spare = max(0.0, column - used)
    return x + (spare if align == "right" else spare / 2)


def layout(cells, aligns, width, size, line_height, measure, wrap):
    """Draw ops for a table ``width`` wide at most, and its height (see the module notes)."""
    parsed = [[inline(cell) for cell in row] for row in cells]
    widths = column_widths(parsed, width, measure)
    if widths is None:
        return _stacked(parsed, width, size, line_height, wrap)
    ops, dy, edges = [], 0, []
    lefts = [sum(widths[:j]) + j * 2 * PAD_X for j in range(len(widths))]
    total = sum(widths) + len(widths) * 2 * PAD_X
    header = 0
    for index, row in enumerate(parsed):
        laid = [_text_rows(plain, spans, widths[j], wrap) for j, (plain, spans) in enumerate(row)]
        height = max(1, max(len(rows) for rows in laid)) * line_height + 2 * PAD_Y
        for j, rows in enumerate(laid):
            for k, runs in enumerate(rows):
                x = _aligned(lefts[j] + PAD_X, widths[j], runs, aligns[j] if j < len(aligns) else "", measure)
                ops.append(("runs", x, dy + PAD_Y + (k + 1) * line_height - 6, runs, size,
                            "heading" if index == 0 else "body"))
        dy += height
        if index == 0:
            header = height
        if index < len(parsed) - 1:
            edges.append(dy)
    return [("table", 0, 0, total, dy, tuple(lefts[1:]), tuple(edges), header)] + ops, dy


def _stacked(parsed, width, size, line_height, wrap):
    """One record per body row, "Header: value" per cell; a header without rows yet lists its names."""
    head = [plain for plain, _spans in parsed[0]]
    records = parsed[1:] or [[("", [])] * len(head)]
    ops, dy, edges = [], 0, []
    for index, row in enumerate(records):
        dy += PAD_Y
        for name, (plain, spans) in zip(head, row):
            if name and plain:
                label = name + ": "
                text, shift = label + plain, len(label)
                spans = [(0, len(label), "label", "")] + [(s + shift, e + shift, kind, url) for s, e, kind, url in spans]
            elif name:
                text, spans = name, [(0, len(name), "label", "")]
            else:
                text = plain
            for runs in _text_rows(text, spans, width - 2 * PAD_X, wrap):
                dy += line_height
                ops.append(("runs", PAD_X, dy - 6, runs, size, "body"))
        dy += PAD_Y
        if index < len(records) - 1:
            edges.append(dy)
    return [("table", 0, 0, width, dy, (), tuple(edges), 0)] + ops, dy
