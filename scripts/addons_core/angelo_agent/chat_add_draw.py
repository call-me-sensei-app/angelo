# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws the composer's + menu, the launcher (``chat_add_layout`` places it, ``chat_add_nav`` holds its view).

The bar (search field, Attach), then the top level's grid of tiles, a group's page (Back and the group's rows, drawn by
``chat_add_menu.draw_rows`` as the Flows popup draws them) or the search results, then the footer: the strip that
describes the pointed-at tile, and the footer's text buttons (Report a bug, Request a feature). Every clickable thing
records its hit (action, value) and, for the keyboard, its stop (row, column) in reading order. Tiles are pictures:
in a right-to-left language the canvas mirrors the grid and ``canvas_rtl.upright`` keeps each tile unmirrored.
"""

import functools
import math

from . import canvas_rtl, chat_add_layout as layout, chat_add_menu as menu, chat_add_nav as nav
from . import chat_destinations, ui_tokens
from .ui_text import iface_

CHOOSING = ("popup_add_pick", "popup_add_choose", "popup_add_fit")   # Rows that choose a tool Angelo adds at Send.


@functools.lru_cache(maxsize=4096)
def _width(view, text, size, scale, font, probe):
    """``view.measure`` remembered per text, size, interface scale and font: the menu redraws on every pointer move,
    and a two-line title tries each break it may take."""
    return view.measure(text, size)


PROBE = "Mg0 · あ"   # Measured uncached once per layout: a reloaded font (same id, other metrics) changes the key.


def _measure(view):
    key = (getattr(view, "_scale", 1.0), getattr(view, "_font", 0), view.measure(PROBE, layout.TITLE))
    return lambda text, size: _width(view, text, size, *key)


def _lines(view, text, width, size):
    return [(line[0] if isinstance(line, tuple) else line).rstrip() for line in view._flow_wrap(text, width, size)]


def _hit(view, state, rect, action, value, clip, width, height):
    from .chat_hit_testing import clip_rect
    visible = clip_rect(rect, clip) if clip else rect
    visible = clip_rect(visible, (0, 0, width, height)) if visible else None
    if visible:
        state["popup_hits"].append((visible, action, value))
        return visible
    return None


def _focused(popup, action, value):
    stops, cursor = popup.get("stops") or [], popup.get("cursor")
    return (popup.get("keyboard") and cursor is not None and cursor < len(stops)
            and stops[cursor][:2] == (action, value))


def _highlight(view, popup, rect, action, value, radius=9):
    x, y, w, h = rect
    if popup.get("hover") == (action, value) or _focused(popup, action, value):
        view.shape(x, y, w, h, ui_tokens.HIGHLIGHT, radius)
    if _focused(popup, action, value):   # The keyboard's place: the shared ring, so it reads without colour.
        from . import ui_kit
        ui_kit.focus_ring(view, rect, radius, 1.0)


def _magnifier(view, x, y, color):
    points = [(x + 4.8 * math.cos(math.radians(i * 30)), y + 4.8 * math.sin(math.radians(i * 30))) for i in range(13)]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        view.stroke(ax, ay, bx, by, color, 1.4)
    view.stroke(x + 3.6, y - 3.6, x + 7.5, y - 7.5, color, 1.6)


def _bar(view, state, popup, geometry, items, width, height):
    sx, sy, sw, sh = geometry["search"]
    query = popup.get("query", "")
    view.shape(sx, sy, sw, sh, ui_tokens.TEAL if query else ui_tokens.HIGHLIGHT, 9)
    view.shape(sx + 1, sy + 1, sw - 2, sh - 2, ui_tokens.WELL, 8)
    _magnifier(view, sx + 16, sy + sh / 2 + 1, ui_tokens.MUTED)
    shown = query or iface_(nav.SEARCH)
    view.text(view.elide(shown, sw - 44, 12.5), sx + 30, sy + 12, 12.5, ui_tokens.TEXT if query else ui_tokens.MUTED)
    if query:   # The caret after the typed text: typing always goes here.
        cx = sx + 30 + min(view.measure(shown, 12.5), sw - 44) + 2
        if canvas_rtl.mirrored() and not canvas_rtl.has_rtl(shown):   # Latin typed in a mirrored field reads
            cx = sx + 30 - 4                                            # left to right: its end is the field's start.
        view.shape(cx, sy + 9, 1.5, 16, ui_tokens.TEAL)
    _hit(view, state, geometry["search"], "popup_add_search", None, None, width, height)
    stops = []
    for index, rect in geometry["attach"]:
        item = items[index]
        _highlight(view, popup, rect, item["action"], item["value"])
        if popup.get("hover") != (item["action"], item["value"]) and not _focused(popup, item["action"], item["value"]):
            view.shape(*rect, ui_tokens.HIGHLIGHT, 9)
        compact = geometry.get("attach_compact")
        if item.get("glyph") == "goal":
            cx, cy = rect[0] + (rect[2] / 2 if compact else 17), rect[1] + rect[3] / 2
            view.shape(cx - 7, cy - 7, 14, 14, ui_tokens.TEAL, 7)
            view.shape(cx - 5.4, cy - 5.4, 10.8, 10.8, ui_tokens.HIGHLIGHT, 5.4)
            view.shape(cx - 2.5, cy - 2.5, 5, 5, ui_tokens.TEAL, 2.5)
        else:
            view.icon("plus", rect[0] + (rect[2] / 2 if compact else 17), rect[1] + rect[3] / 2, ui_tokens.TEXT)
        if not compact:   # Icon-only when the field needs the room; the hover strip still names the button.
            view.text(item["title"], rect[0] + 30, rect[1] + 12, layout.TITLE)
        _hit(view, state, rect, item["action"], item["value"], None, width, height)
        stops.append((item["action"], item["value"], -1, 1 + len(stops)))
    return stops


def _cells(view, state, popup, geometry, items, chosen, width, height):
    import gpu
    lx, ly, lw, lh = clip = geometry["list_rect"]
    old = gpu.state.scissor_get()
    gpu.state.scissor_set(*view._scissor(lx, ly, lw, lh))
    for heading, (hx, hy, hw, hh) in geometry["headings"]:
        view.text(heading, hx + 6, hy + 5, 11, ui_tokens.MUTED)   # As translated: never upper-cased
    picked = {choice["tool"] for choice in chosen}
    rows, stops = sorted({round(cell["rect"][1], 1) for cell in geometry["cells"]}, reverse=True), []
    for cell in geometry["cells"]:
        item, (x, y, w, h) = items[cell["index"]], cell["rect"]
        _highlight(view, popup, cell["rect"], item["action"], item["value"])
        with canvas_rtl.upright(x + 4, 32) as gx:
            menu.draw_glyph(view, item.get("glyph") or "dot", gx, y + (h - 32) / 2)
        lines = cell["lines"]
        top = y + (h + len(lines) * layout.TITLE_LINE) / 2
        for n, line in enumerate(lines):
            view.text(line, x + layout.TITLE_X, top - (n + 1) * layout.TITLE_LINE + 3.5, layout.TITLE)
        if item["kind"] == "group":
            view.icon("next", x + w - 10, y + h / 2, ui_tokens.MUTED)
        if item["feature"] in picked:   # A chosen tool (a base mesh, the hands): a teal check on its tile.
            from . import ui_kit
            ui_kit.check(view, x + w - (24 if item["kind"] == "group" else 12), y + h / 2, 1.0, ui_tokens.TEAL, 1.6)
        _hit(view, state, cell["rect"], item["action"], item["value"], clip, width, height)
        column = 0 if x <= lx + 1 else 1   # Every tile is a stop, in view or not (a narrow chat's grid scrolls).
        stops.append((item["action"], item["value"], rows.index(round(y, 1)), column, cell["rect"]))
    gpu.state.scissor_set(*old)
    return stops


def _footer(view, state, popup, geometry, items, text, width, height, name=""):
    """The strip (``text``; its leading ``name``, the pointed-at tile's title, in the brighter text colour) and the
    footer's text buttons."""
    x, _, w, _ = geometry["rect"]
    view.shape(x + 1, geometry["footer_top"], w - 2, 1, ui_tokens.HIGHLIGHT)
    sx, sy, sw, sh = geometry["strip"]
    for n, line in enumerate(_lines(view, text, sw - 28, layout.WHY)[:max(1, geometry["strip_lines"])]):
        baseline = sy + sh - layout.STRIP_PAD / 2 - (n + 1) * layout.WHY_LINE + 3.5
        view.text(line, sx + 14, baseline, layout.WHY, ui_tokens.MUTED)
        if not n and name and line.startswith(name) and not canvas_rtl.mirrored() and not canvas_rtl.has_rtl(line):
            view.text(name, sx + 14, baseline, layout.WHY, ui_tokens.TEXT)
    stops = []
    if geometry["links"]:
        view.shape(x + 1, sy, w - 2, 1, ui_tokens.HIGHLIGHT)
    for index, (lx, ly, lw, lh) in geometry["links"]:
        item = items[index]
        lit = popup.get("hover") == (item["action"], item["value"]) or _focused(popup, item["action"], item["value"])
        _highlight(view, popup, (lx - 4, ly + 3, lw + 8, lh - 6), item["action"], item["value"], 7)
        view.shape(lx, ly + 10, 14, 10, ui_tokens.TEAL, 3.5)   # A speech bubble: a teal rim, the popover inside,
        view.shape(lx + 1.5, ly + 11.5, 11, 7, ui_tokens.MENU, 2.2)   # and its tail at the lower left.
        view.stroke(lx + 3, ly + 10.5, lx + 1.5, ly + 6.5, ui_tokens.TEAL, 1.4)
        view.stroke(lx + 1.5, ly + 6.5, lx + 6.5, ly + 10.5, ui_tokens.TEAL, 1.4)
        view.text(item["title"], lx + layout.LINK_ICON + 6, ly + 10, layout.WHY,
                  ui_tokens.TEXT if lit else ui_tokens.MUTED)
        _hit(view, state, (lx - 4, ly, lw + 8, lh), item["action"], item["value"], None, width, height)
        stops.append((item["action"], item["value"], 10 ** 6, len(stops)))
    return stops


def _list(view, state, popup, geometry, rows, header, width, height):
    """A group's page or the search results: rows as the Flows popup draws them, scrolling inside the list area. A
    page's leading toggles (the base meshes' "Match the size of …") are pinned under its header: they apply to the
    chosen row, so scrolling to that row never hides them."""
    menu.wrap_subtitles(view, rows, geometry["list_rect"][2], whole=True)   # Never cut: rows grow instead.
    heights = [menu.row_height(row) for row in rows]
    pinned = 0
    while header and pinned < len(rows) and rows[pinned]["kind"] == "toggle":
        pinned += 1
    top = (layout.PAGE_HEADER if header else 0) + sum(heights[:pinned])
    placed = layout.stack(heights[pinned:], geometry["list_rect"], popup.get("scroll", 0), top)
    chosen = next((i for i, row in enumerate(rows[pinned:]) if row.get("selected")), None)
    if popup.pop("reveal_selected", False) and chosen is not None:   # Centred in the page's list.
        above = sum(heights[pinned:][:chosen])
        target = above - (placed["area"][3] - heights[pinned + chosen]) / 2
        placed = layout.stack(heights[pinned:], geometry["list_rect"], target, top)
    popup["scroll"] = placed["scroll"]
    stops = []
    if header:
        group = chat_destinations.feature(popup["page"])
        hx, hy, hw, hh = placed["header"]
        hy = hy + hh - layout.PAGE_HEADER   # The Back row at the header's top, pinned toggles under it.
        back = (hx, hy + 5, 26, 26)
        target = ("popup_add_back", popup["page"])
        _highlight(view, popup, back, *target, 7)
        if popup.get("hover") != target and not _focused(popup, *target):
            view.shape(*back, ui_tokens.HIGHLIGHT, 7)
        view.stroke(hx + 15, hy + 23, hx + 10, hy + 18, ui_tokens.TEXT, 1.6)
        view.stroke(hx + 10, hy + 18, hx + 15, hy + 13, ui_tokens.TEXT, 1.6)
        title = menu._label(group.title) if group else ""
        view.text(view.elide(title, hw - 44, 13), hx + 36, hy + 13, 13)
        _hit(view, state, back, "popup_add_back", popup["page"], None, width, height)
        stops.append(("popup_add_back", popup["page"], -1, 0))
        rects, below = [], hy
        for rh in heights[:pinned]:
            below -= rh
            rects.append((hx, below, hw, rh))
        if pinned:
            stops += menu.draw_rows(view, state, popup, rows[:pinned], rects,
                                    (hx, below, hw, hy - below), width, height, pinned=True)
    stops += menu.draw_rows(view, state, popup, rows[pinned:], placed["rows"], placed["area"], width, height,
                            first=pinned)
    if header:   # The list scrolls under this line: a row cut at the top is cut by a rule, not by the header.
        ax, ay, aw, ah = placed["area"]
        view.shape(ax, ay + ah, aw, 1, ui_tokens.HIGHLIGHT)
    return placed, stops


def top_level(view, width, height, bottom, worker=False, scroll=0):
    """The menu's entries, their shown text (``items``) and the top level's geometry for a chat of ``width`` x
    ``height`` whose composer's top is ``bottom``, measured with the canvas's fonts in the current language: what
    ``draw`` places, and what the GUI fold suite checks at other window sizes."""
    entries = chat_destinations.entries(worker)
    items = [dict(entry, title=menu._shown(entry, "label"), why=menu._shown(entry, "subtitle"),
                  section=menu._label(entry["section"])) for entry in entries]
    from . import chosen_tools
    geometry = layout.place(items, _measure(view), lambda text, w, size: _lines(view, text, w, size), width, height,
                            bottom, iface_(nav.HINT), scroll, notes=(iface_(chosen_tools.FOOTER),),
                            search_label=iface_(nav.SEARCH))
    return entries, items, geometry


def draw(view, state, controller, width, height, fit_target):
    """Draw the open menu; ``fit_target`` is the selected mesh's shown name or ''. Everything is clipped: the chrome to
    the popup, and each list (the grid, a page's pinned rows, its scrolling rows) to its own area, so no row ever draws
    over the bar, a page's header, the strip or the footer (1.0.8 cut 03cb462ddb5: the scissor was set but its test was
    off, and a scrolled page's rows ran over the header and the hint)."""
    import gpu
    gpu.state.scissor_test_set(True)
    try:
        _draw(view, state, controller, width, height, fit_target)
    finally:
        gpu.state.scissor_test_set(False)


def _draw(view, state, controller, width, height, fit_target):
    import gpu
    from .worker_routing import worker_snapshots   # A worker's chat is offered only what its Send runs.
    popup = state["popover"]
    chosen = list(getattr(controller, "chosen_tools", ()) or ())
    worker = bool(worker_snapshots(controller))
    if not popup.get("launcher"):   # A new menu: no keyboard stop yet (the popover's cursor was another list's).
        popup.update(launcher=True, cursor=None, keyboard=False)
    popup["esc_back"] = bool(popup.get("query") or popup.get("page"))
    if not fit_target:
        popup["fit"] = False
    current = nav.mode(popup)
    entries, items, geometry = top_level(view, width, height, state.get("add_bottom", 120), worker,
                                         popup.get("scroll", 0) if current == "top" else 0)
    popup["entries"] = entries
    hint = iface_(nav.HINT)
    x, y, w, h = geometry["rect"]
    state["popup_rect"] = geometry["rect"]
    gpu.state.scissor_set(*view._scissor(x, y, w, h))   # The chrome: the popup; each list sets its own area.
    view.shape(x, y, w, h, ui_tokens.HIGHLIGHT, 15)
    view.shape(x + 1, y + 1, w - 2, h - 2, ui_tokens.MENU, 14)
    stops = _bar(view, state, popup, geometry, items, width, height)
    describe = None
    if current == "top":
        popup["rows"] = entries
        popup["scroll"] = geometry["scroll"]
        stops += _cells(view, state, popup, geometry, items, chosen, width, height)
        scroll = {"max_scroll": geometry["max_scroll"], "area": geometry["list_rect"]}
        lookup = {(item["action"], item["value"]): item for item in items}
    else:
        if current == "page":
            rows = chat_destinations.group_rows(popup["page"], chosen, fit_target, worker)
        else:
            key = (popup["query"], fit_target, worker, repr(chosen))   # Searched once per change, not per redraw.
            if popup.get("results_for") != key:
                popup["results"] = nav.search(popup["query"], chosen, fit_target, worker, menu._shown)
                popup["results_for"] = key
            rows = [dict(row) for row in popup["results"]]
            if not rows:
                rows = [{"kind": "note", "label": (nav.NO_MATCH, {"query": popup["query"]})}]
        for row in rows:
            if row["kind"] == "toggle":
                row["on"] = bool(popup.get("fit"))
        popup["rows"] = rows
        placed, list_stops = _list(view, state, popup, geometry, rows, current == "page", width, height)
        stops += list_stops
        scroll = {"max_scroll": placed["max_scroll"], "area": placed["area"]}
        # A page's or a result's rows say what they do on their own lines; the strip describes the page itself: what
        # a chosen tool does (Angelo adds it at Send) on a page of choices, else the group's own description.
        lookup = {(item["action"], item["value"]): item for item in items}
        if current == "page":
            from . import chosen_tools
            group = lookup.get(("popup_add_group", popup["page"]))
            if any(row.get("action") in CHOOSING for row in rows):
                describe = iface_(chosen_tools.FOOTER)
            elif group and group.get("why"):
                describe = layout.describe(group)
    pointed = popup.get("hover")
    if popup.get("keyboard") and popup.get("cursor") is not None and popup["cursor"] < len(popup.get("stops") or []):
        pointed = tuple(popup["stops"][popup["cursor"]][:2])
    item, name = lookup.get(pointed), ""
    if item and item.get("why"):
        describe, name = layout.describe(item), item["title"]
    stops += _footer(view, state, popup, geometry, items, describe or hint, width, height, name)
    popup["stops"] = stops
    popup["layout"] = dict(geometry, drawn_scroll=popup.get("scroll", 0), **scroll)
    menu.draw_scrollbar(view, popup, scroll["area"])

