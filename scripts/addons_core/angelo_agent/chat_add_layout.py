# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the composer's + menu (the launcher) draws everything. Pure geometry, no Blender: host-tested in every language.

The menu is Angelo's main navigation, so every feature must be in view without scrolling (1.0.8: a list of two-line
rows with groups opening inline showed 10 of 19 features at 1440 x 900, 6 with a group open). The launcher puts the
features in a two-column grid of tiles (``CELL``) under their section headings, Attach beside the search field and the
footer's features (Report a bug) as text buttons under one strip that describes the pointed-at tile; a group opens as
a page in the same list area, never inline, so nothing can push a feature out of view. The menu is as tall as its
content, up to the chat's height above the composer; only a chat narrowed below ``TWO_COLUMNS`` (one column) or a
page's own list can scroll.

Text is never cut: a title takes one line or two balanced lines (``text_layout.two_lines``), a title that cannot fit
two lines in half a row takes the whole row, and the strip is as tall as the longest description in the person's
language. Callers pass the text as shown (translated), ``measure(text, size)`` and ``wrap(text, width, size)``.
Coordinates are the canvas's: region pixels, y up; the popup's bottom sits at ``bottom`` (above the composer).
"""

from . import rtl_text, text_layout

MAX_WIDTH, MARGIN, TOP_MARGIN = 480, 12, 12
TWO_COLUMNS = 360          # A narrower menu has one column.
PAD, BAR, BAR_GAP = 8, 34, 6
HEAD, CELL, GAP = 18, 38, 6
TILE, TITLE_X, CHEVRON, END = 28, 42, 18, 8
TITLE, TITLE_LINE = 12.5, 14.5
WHY, WHY_LINE, STRIP_PAD = 11.5, 14, 16
LINK, LINK_ICON, LINK_GAP = 28, 18, 18
ATTACH_PAD = 42            # The Attach button: its + and padding beside its label.
PAGE_HEADER, RESULT, PAGE_ROW = 36, 38, 46   # PAGE_ROW: a page's row (chat_add_menu.HEIGHTS["row"]).
MIN_HEIGHT = 160


def popup_width(width):
    return min(MAX_WIDTH, max(200, width - 2 * MARGIN))


def columns(width):
    return 2 if popup_width(width) >= TWO_COLUMNS else 1


def _title_lines(title, measure, width, group):
    room = width - TITLE_X - (CHEVRON if group else END)
    return text_layout.two_lines(title, lambda part: measure(part, TITLE), room)


def joined(first, second):
    """``first · second`` in reading order, drawn as the canvas draws text: Arabic is stored visual, so the two are
    joined in reading order and turned visual again (else the second would read first)."""
    if not (rtl_text.is_visual(first) or rtl_text.is_visual(second)):
        return "%s · %s" % (first, second)
    logical = [rtl_text.to_logical(part) if rtl_text.is_visual(part) else part for part in (first, second)]
    return rtl_text.to_visual("%s · %s" % tuple(logical))


def describe(item):
    """The strip's text for a tile: its title, then what it does."""
    return joined(item["title"], item["why"]) if item["why"] else item["title"]


def place(items, measure, wrap, width, height, bottom, hint="", scroll=0, notes=()):
    """The top level's geometry. ``items``: the menu's entries in order, each with ``title``, ``why``, ``section``
    (text as shown), ``placement`` and ``kind``. Returns the popup ``rect``, the ``search`` field, ``attach`` buttons,
    section ``headings``, grid ``cells`` (each with its title ``lines``), the footer ``strip`` and ``links``, the grid's
    ``list_rect`` and its clamped ``scroll`` / ``max_scroll`` (0 whenever the menu fits). The strip is as tall as the
    longest text it may show: any entry's description, the ``hint`` and the other ``notes`` the caller shows there."""
    w = popup_width(width)
    cols = columns(width)
    cw = (w - 2 * PAD - (cols - 1) * GAP) / cols
    full = w - 2 * PAD
    grid = [(i, item) for i, item in enumerate(items) if item.get("placement", "grid") == "grid"]
    # Grid rows: (heading or None, [(index, column, span, lines)], row height).
    rows, section, current, column = [], None, None, 0
    for index, item in grid:
        if item["section"] and item["section"] != section:
            section, current, column = item["section"], None, 0
            rows.append((section, [], HEAD))
        group = item["kind"] == "group"
        lines = None if cols == 1 else _title_lines(item["title"], measure, cw, group)
        span = lines is None
        if span:
            lines = (_title_lines(item["title"], measure, full, group)
                     or wrap(item["title"], full - TITLE_X - (CHEVRON if group else END), TITLE))
        if current is None or column + (cols if span else 1) > cols:
            current, column = [], 0
            rows.append((None, current, CELL))
        current.append((index, column, span, lines))
        column = cols if span else column + 1
    grid_height = 0
    for n, (heading, cells, _) in enumerate(rows):
        tallest = max([len(lines) for *_, lines in cells] or [1])
        rows[n] = (heading, cells, HEAD if heading else max(CELL, tallest * TITLE_LINE + 10))
        grid_height += rows[n][2]
    # Footer: the strip as tall as the longest description (or the hint), then the footer's text buttons.
    texts = [describe(item) for item in items] + [hint, *notes]
    strip_lines = max(len(wrap(text, w - 28, WHY)) for text in texts if text) if any(texts) else 1
    strip_height = STRIP_PAD + WHY_LINE * strip_lines
    link_rows, lx = [[]], 0
    for index, item in enumerate(items):
        if item.get("placement") == "footer":
            lw = LINK_ICON + 6 + measure(item["title"], WHY) + 4
            if link_rows[-1] and lx + LINK_GAP + lw > w - 2 * 12:
                link_rows.append([])
                lx = 0
            lx += (LINK_GAP if link_rows[-1] else 0) + lw
            link_rows[-1].append((index, lw))
    links_height = LINK * len(link_rows) if link_rows[0] else 0
    footer = strip_height + links_height
    content = PAD + BAR + BAR_GAP + grid_height + footer + PAD
    # However short the chat, a row of tiles (or a page's first row) stays in the list; the popup may reach past its top.
    least = max(CELL, PAGE_HEADER + PAGE_ROW)   # A row of tiles, or a page's header and one of its rows.
    available = max(MIN_HEIGHT, PAD + BAR + BAR_GAP + least + footer + PAD, height - bottom - TOP_MARGIN)
    h = min(available, content)
    x, y = MARGIN, bottom
    list_rect = (x + PAD, y + PAD + footer, full, h - 2 * PAD - BAR - BAR_GAP - footer)
    max_scroll = max(0, grid_height - list_rect[3])
    scroll = max(0, min(max_scroll, scroll))
    # The bar: Attach buttons at its end, the search field taking the rest.
    bar_y, end, attach = y + h - PAD - BAR, x + w - PAD, []
    for index, item in enumerate(items):
        if item.get("placement") == "bar":
            bw = measure(item["title"], TITLE) + ATTACH_PAD
            end -= bw
            attach.append((index, (end, bar_y, bw, BAR)))
            end -= 6
    search = (x + PAD, bar_y, max(60, end - x - PAD), BAR)
    headings, cells, top = [], [], list_rect[1] + list_rect[3] + scroll
    for heading, row_cells, rh in rows:
        top -= rh
        if heading:
            headings.append((heading, (x + PAD, top, full, rh)))
        for index, column, span, lines in row_cells:
            cx = x + PAD + column * (cw + GAP)
            cells.append({"index": index, "rect": (cx, top + 1, full if span else cw, rh - 2), "lines": lines})
    links, ly = [], y + PAD + links_height
    for line in link_rows:
        ly -= LINK
        lx = x + 12
        for index, lw in line:
            links.append((index, (lx, ly, lw, LINK)))
            lx += lw + LINK_GAP
    return {"rect": (x, y, w, h), "columns": cols, "search": search, "attach": attach, "headings": headings,
            "cells": cells, "list_rect": list_rect, "scroll": scroll, "max_scroll": max_scroll,
            "content_height": grid_height, "strip": (x, y + PAD + links_height, w, strip_height),
            "strip_lines": strip_lines, "links": links, "footer_top": y + PAD + footer, "fits": content <= available}


def stack(heights, list_rect, scroll, header=0):
    """A page's or the search results' rows, one under another in the list area below a ``header`` of that height:
    each row's rect, the area they scroll in, and the clamped scroll."""
    lx, ly, lw, lh = list_rect
    area = (lx, ly, lw, max(0, lh - header))
    content = sum(heights)
    max_scroll = max(0, content - area[3])
    scroll = max(0, min(max_scroll, scroll))
    rects, top = [], ly + area[3] + scroll
    for rh in heights:
        top -= rh
        rects.append((lx, top, lw, rh))
    return {"rows": rects, "area": area, "scroll": scroll, "max_scroll": max_scroll, "content_height": content,
            "header": (lx, ly + area[3], lw, header) if header else None}


def in_view(rect, area):
    """Is all of ``rect`` inside ``area`` (a tile a person can click without scrolling)?"""
    return (rect[1] >= area[1] - 0.5 and rect[1] + rect[3] <= area[1] + area[3] + 0.5
            and rect[0] >= area[0] - 0.5 and rect[0] + rect[2] <= area[0] + area[2] + 0.5)
