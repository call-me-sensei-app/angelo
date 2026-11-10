# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu: Angelo's main navigation (attach a file, open Media Studio or a Generation Flow, choose a
tool Angelo performs before the agent starts, and whatever else registers in ``chat_destinations``).

Drawn on the chat canvas as the chat's popover kind ``"add"`` (``chat_view`` owns opening, closing, outside clicks and
the Esc that closes it): the launcher, placed by ``chat_add_layout``, drawn by ``chat_add_draw``, its view and keys in
``chat_add_nav`` (1.0.8). This module keeps what the + menu shares with Angelo's Flows popup (``flow_menu``) and the
flow start page: the tiles (``draw_glyph``), a list row (``draw_row``, ``draw_rows``), why lines that wrap before they
are elided, and the list's arrow keys. Strings are translated here, at display time.
"""

import math

from . import ui_tokens
from .ui_text import iface_

HEIGHTS = {"section": 28, "note": 22, "toggle": 36, "row": 46, "group": 46}
LINE = 14   # A why line too long for one line wraps to a second, and its row grows by this much.
INDENT = 14   # A row inside a group, in a list that shows the group's header above it.
TEXT_X = 50   # A row's title column, beside its tile; a row with no glyph (a template, a flow) keeps the column, no tile.
ACTIONABLE = ("row", "toggle", "group")
TOKEN_NAMES = {"TILE_FILL": "HIGHLIGHT"}   # The + tile's fill, by its old name (base_mesh_thumbs reads it).
__getattr__ = ui_tokens.forward(TOKEN_NAMES, __name__)


def opening():
    """A glyph's cut-out (the inside of a ring, a link's opening): the tile's own colour, TEAL_SOFT over the menu,
    made opaque. TEAL_SOFT is translucent, so drawn over a teal rim it left the rim solid (a filled disc, a flat
    block). Read as it draws, in the theme's variant."""
    return ui_tokens.over(ui_tokens.TEAL_SOFT, ui_tokens.MENU)


def _label(value):
    if not value:
        return ""
    return iface_(value[0], **value[1]) if isinstance(value, tuple) else iface_(value)


def _shown(row, key):
    """A row's label or subtitle as drawn: a label that is a name (an asset's, a flow's: ``translate`` False) as it
    is; everything else translated."""
    value = row.get(key, "")
    if key == "subtitle" and row.get("shown_subtitle"):   # Already as shown (a search result's place and why).
        return value
    return value if key == "label" and row.get("translate") is False else _label(value)


def draw_glyph(view, glyph, tx, ty):
    """A row's 32 px tile: attach (+), base_mesh (a head on shoulders), studio (a framed spark), flow (three linked
    nodes), new (+ in teal), web (a globe), region (a frame with a teal quarter), link (two chain links), apps (a plug), video (a film
    frame around a play mark), library (a magnifier over tiles), bake_maps (a 2 x 2 grid of maps), tension (a spring),
    feedback (a speech bubble), select (a dashed marquee), goal (a target), anything else a dot. Shapes and strokes only:
    no icon font, nothing mirrored."""
    from . import render_glyphs
    if render_glyphs.draw_tile(view, glyph, tx, ty):   # AI render and the engines (render_glyphs).
        return
    if glyph == "add":   # New Flow: a 16 px ADD glyph, no tile (studio_entry_points.rst, section 2).
        view.stroke(tx + 9, ty + 16, tx + 23, ty + 16, ui_tokens.TEAL, 1.8)
        view.stroke(tx + 16, ty + 9, tx + 16, ty + 23, ui_tokens.TEAL, 1.8)
        return
    if glyph == "attach":
        view.shape(tx, ty, 32, 32, ui_tokens.HIGHLIGHT, 8)
        view.icon("plus", tx + 16, ty + 16, ui_tokens.TEXT)
        return
    view.shape(tx, ty, 32, 32, ui_tokens.TEAL_SOFT, 8)
    teal = ui_tokens.TEAL
    if glyph == "base_mesh":
        view.shape(tx + 11, ty + 13, 10, 12, teal, 5)
        view.shape(tx + 7, ty + 5, 18, 7, teal, 3)
    elif glyph == "studio":
        for a, b in (((8, 8), (24, 8)), ((24, 8), (24, 21)), ((24, 21), (8, 21)), ((8, 21), (8, 8))):
            view.stroke(tx + a[0], ty + a[1], tx + b[0], ty + b[1], teal, 1.6)
        for dx, dy in ((0, 5), (5, 0), (0, -5), (-5, 0)):
            view.stroke(tx + 22, ty + 22, tx + 22 + dx, ty + 22 + dy, teal, 1.8)
    elif glyph in {"flow", "new"}:
        for (ax, ay), (bx, by) in (((10, 16), (22, 22)), ((10, 16), (22, 10))):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.4)
        for cx, cy in ((10, 16), (22, 22), (22, 10)):
            view.shape(tx + cx - 3.5, ty + cy - 3.5, 7, 7, teal, 2)
        if glyph == "new":
            view.shape(tx + 20, ty + 1, 11, 11, ui_tokens.INK, 5.5)
            view.icon("plus", tx + 25.5, ty + 6.5, teal)
    elif glyph == "goal":   # A target: two rings and a centre.
        view.shape(tx + 7, ty + 7, 18, 18, teal, 9)
        view.shape(tx + 8.8, ty + 8.8, 14.4, 14.4, opening(), 7.2)
        view.shape(tx + 11, ty + 11, 10, 10, teal, 5)
        view.shape(tx + 12.6, ty + 12.6, 6.8, 6.8, opening(), 3.4)
        view.shape(tx + 14.5, ty + 14.5, 3, 3, teal, 1.5)
    elif glyph == "web":
        view.shape(tx + 7, ty + 7, 18, 18, teal, 9)
        view.shape(tx + 8.6, ty + 8.6, 14.8, 14.8, opening(), 7.4)
        view.stroke(tx + 8, ty + 16, tx + 24, ty + 16, teal, 1.4)
        for (ax, ay), (bx, by) in (((16, 8), (12.5, 16)), ((12.5, 16), (16, 24)), ((16, 8), (19.5, 16)),
                                   ((19.5, 16), (16, 24))):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.3)
    elif glyph == "video":   # A film frame (two sprocket rows) around a play triangle, drawn in strokes.
        for a, b in (((7, 9), (25, 9)), ((25, 9), (25, 23)), ((25, 23), (7, 23)), ((7, 23), (7, 9))):
            view.stroke(tx + a[0], ty + a[1], tx + b[0], ty + b[1], teal, 1.6)
        for x in (10, 14, 18, 22):
            view.shape(tx + x - 1, ty + 5, 2, 2, teal, 1)
            view.shape(tx + x - 1, ty + 25, 2, 2, teal, 1)
        for (ax, ay), (bx, by) in (((13.5, 12), (13.5, 20)), ((13.5, 12), (20, 16)), ((13.5, 20), (20, 16))):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.6)
    elif glyph == "library":   # A magnifier over a shelf of tiles: the Angelo Library.
        for dx in (7, 14, 21):
            view.shape(tx + dx - 2, ty + 6, 6, 6, teal, 1.5)
        for i in range(16):
            a, b = math.radians(i * 22.5), math.radians((i + 1) * 22.5)
            view.stroke(tx + 15 + 5.5 * math.cos(a), ty + 20 + 5.5 * math.sin(a), tx + 15 + 5.5 * math.cos(b),
                        ty + 20 + 5.5 * math.sin(b), teal, 1.5)
        view.stroke(tx + 19, ty + 16, tx + 24, ty + 11, teal, 1.7)
    elif glyph == "bake_maps":   # A texture set: four maps in a 2 x 2 grid, one of them solid.
        for n, (dx, dy) in enumerate(((7, 7), (17, 7), (7, 17), (17, 17))):
            view.shape(tx + dx, ty + dy, 8, 8, teal, 2)
            if n:
                view.shape(tx + dx + 1.6, ty + dy + 1.6, 4.8, 4.8, opening(), 1.2)
    elif glyph == "region":   # A frame with one teal quarter: a region of the render rendered again.
        for (ax, ay), (bx, by) in (((8, 8), (24, 8)), ((24, 8), (24, 24)), ((24, 24), (8, 24)), ((8, 24), (8, 8))):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.4)
        view.shape(tx + 16, ty + 16, 8, 8, teal, 1.5)
    elif glyph == "skeleton":   # A figure of joints and bones: the rig check for game engines.
        from .skeleton_menu import draw_glyph as skeleton
        skeleton(view, tx, ty, 32, teal)
    elif glyph == "tension":   # A spring, tight (compressed) on the left and open (stretched) on the right.
        xs = (6, 8, 10, 12.5, 15.5, 19.5, 26)
        points = [(x, 11 if index % 2 else 21) for index, x in enumerate(xs)]
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.5)
    elif glyph == "link":   # Two chain links: the Godot live link (rims first, then both openings).
        for dx, dy in ((-4, 2.5), (4, -2.5)):
            view.shape(tx + 16 + dx - 6.5, ty + 16 + dy - 4, 13, 8, teal, 4)
        for dx, dy in ((-4, 2.5), (4, -2.5)):
            view.shape(tx + 16 + dx - 4, ty + 16 + dy - 1.6, 8, 3.2, opening(), 1.6)
    elif glyph == "apps":   # A plug, prongs up and its cable down: Apps and settings (connecting other apps).
        view.shape(tx + 10.5, ty + 19, 3, 7, teal, 1.5)
        view.shape(tx + 18.5, ty + 19, 3, 7, teal, 1.5)
        view.shape(tx + 8, ty + 11, 16, 9, teal, 3)
        view.stroke(tx + 16, ty + 11, tx + 16, ty + 5, teal, 2)
    elif glyph == "feedback":   # A speech bubble with its tail at the lower left: Report a bug, Request a feature.
        view.shape(tx + 6, ty + 10, 20, 15, teal, 5)
        view.shape(tx + 7.6, ty + 11.6, 16.8, 11.8, opening(), 3.6)
        for (ax, ay), (bx, by) in (((10, 11), (8, 6)), ((8, 6), (15, 11))):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.5)
        for x in (11.5, 16, 20.5):
            view.shape(tx + x - 1.2, ty + 16.3, 2.4, 2.4, teal, 1.2)
    elif glyph == "select":   # A dashed marquee around a solid square: selection that reaches what is hidden.
        for start in range(8, 24, 4):
            for (ax, ay), (bx, by) in (((start, 8), (start + 2, 8)), ((start, 24), (start + 2, 24)),
                                       ((8, start), (8, start + 2)), ((24, start), (24, start + 2))):
                view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.4)
        view.shape(tx + 12.5, ty + 12.5, 7, 7, teal, 1.5)
    else:
        view.shape(tx + 12, ty + 12, 8, 8, teal, 4)


TITLE_LINE = 15   # A row title's second line, when it takes two (the + menu's pages: never cut).


def wrap_subtitles(view, rows, list_width, whole=False):
    """Each row's why line as drawn (``subtitle_lines``) and the row's height to hold it: a why line wraps to a second
    line before it is elided. ``whole`` (the + menu's pages and search results): nothing is cut; the title takes
    two balanced lines when it must (``title_lines``) and the why line every line it needs."""
    from . import text_layout
    for row in rows:
        if row["kind"] in {"row", "group"}:
            avail = list_width - (INDENT if row.get("depth") else 0) - 74
            text = _shown(row, "subtitle")
            if whole:
                row["subtitle_lines"] = [(line[0] if isinstance(line, tuple) else line).rstrip()
                                         for line in view._flow_wrap(text, avail, 11.5)] if text else []
                title, room = _shown(row, "label"), list_width - (INDENT if row.get("depth") else 0) - TEXT_X - 24
                row["title_lines"] = (text_layout.two_lines(title, lambda part: view.measure(part, 13), room) or
                                      [(line[0] if isinstance(line, tuple) else line).rstrip()
                                       for line in view._flow_wrap(title, room, 13)])
            else:
                row["subtitle_lines"] = subtitle_lines(view, text, avail)
            extra = TITLE_LINE * max(0, len(row.get("title_lines") or [1]) - 1)
            row["height"] = HEIGHTS[row["kind"]] + LINE * max(0, len(row["subtitle_lines"]) - 1) + extra


def draw_row(view, row, rect, glyphs=None):
    """A row's tile (``glyphs``, default ``draw_glyph``; none for a row without a glyph), title and why lines, and its
    chevron (a group) or check (a chosen option), inside ``rect``. Angelo's Flows popup (``flow_menu``) draws its rows
    with it too, so the two menus' rows never drift apart."""
    rx, ry, rw, rh = rect
    glyph, thumbnail = row.get("glyph", "dot"), row.get("thumbnail")
    tx = rx + TEXT_X
    if glyph or thumbnail:   # A tile is a picture: never mirrored in a right-to-left language, only placed (P7).
        from . import base_mesh_thumbs, canvas_rtl
        with canvas_rtl.upright(rx + 8, 32) as gx:
            # A row with its own picture (a base mesh's preview, SD3) shows it; else its drawn glyph.
            if not (thumbnail and base_mesh_thumbs.draw(view, thumbnail, gx, ry + (rh - 32) / 2)) and glyph:
                (glyphs or draw_glyph)(view, glyph, gx, ry + (rh - 32) / 2)
    lines, titles = row.get("subtitle_lines", []), row.get("title_lines")
    if titles:   # Wrapped whole (wrap_subtitles' ``whole``): the title's lines, then the why lines under them.
        top = ry + rh - 21 if lines else ry + rh / 2 + (len(titles) - 1) * TITLE_LINE / 2 - 5
        for n, line in enumerate(titles):
            view.text(line, tx, top - TITLE_LINE * n, 13)
        below = TITLE_LINE * (len(titles) - 1)
    else:
        view.text(view.elide(_shown(row, "label"), rx + rw - 24 - tx, 13), tx, ry + (rh - 21 if lines else 17), 13)
        below = 0
    for n, line in enumerate(lines):
        view.text(line, tx, ry + rh - 37 - below - LINE * n, 11.5, ui_tokens.MUTED)
    if row["kind"] == "group":   # Open: the chevron points down at its rows; closed: it points on.
        view.icon("chevron" if row.get("open") else "next", rx + rw - 16, ry + rh / 2, ui_tokens.MUTED)
    elif row.get("selected"):   # The shared check: a symbol, so it reads the same in a mirrored (right-to-left) draw.
        from . import ui_kit
        ui_kit.check(view, rx + rw - 16, ry + rh / 2, 1.0, ui_tokens.TEAL, 1.6)


def row_height(row):
    return row.get("height") or HEIGHTS[row["kind"]]


def subtitle_lines(view, text, width):
    """A why line as drawn: one line, or two when it is longer (the second elided if it is longer still)."""
    if not text:
        return []
    lines = [(line[0] if isinstance(line, tuple) else line).rstrip() for line in view._flow_wrap(text, width, 11.5)]
    if len(lines) <= 2:
        return lines or [text]
    first = lines[0]
    rest = text[len(first):].lstrip() if text.startswith(first) else " ".join(lines[1:])
    return [first, view.elide(rest, width, 11.5)]


def actionable(rows):
    return [i for i, row in enumerate(rows) if row["kind"] in ACTIONABLE]


def draw_rows(view, state, popup, rows, rects, area, width, height, first=0, pinned=False):
    """A list's rows (a + menu group's page, its search results): sections, notes, the fit toggle and rows with their
    hover or keyboard highlight and hits, clipped to ``area``. Returns the keyboard's stops, in order: ``first`` is
    the first row's index in the whole list; a ``pinned`` row's stop has no rect (it never scrolls into view)."""
    import gpu
    lx, ly, lw, lh = area
    old_scissor = gpu.state.scissor_get()
    gpu.state.scissor_set(*view._scissor(lx, ly, lw, lh))
    stops = popup.get("stops") or []
    cursor = popup.get("cursor") if popup.get("keyboard") else None
    pointed = tuple(stops[cursor][:2]) if cursor is not None and cursor < len(stops) else None
    found = []
    for i, (row, rect) in enumerate(zip(rows, rects), first):
        rx, ry, rw, rh = rect
        if ry + rh <= ly or ry >= ly + lh:   # Out of view: not drawn, still a keyboard stop (the list scrolls to it).
            if row["kind"] in ACTIONABLE:
                found.append((row["action"], row["value"], i, 0, rect))
            continue
        if row.get("depth"):
            rx, rw = rx + INDENT, rw - INDENT
        kind = row["kind"]
        if kind == "section":
            view.text(view.elide(_label(row["label"]), rw - 16, 11), rx + 8, ry + 8, 11, ui_tokens.MUTED)   # As translated
            continue
        if kind == "note":
            view.text(view.elide(_label(row["label"]), rw - 16, 11.5), rx + 8, ry + 6, 11.5, ui_tokens.MUTED)
            continue
        target = (row["action"], row["value"])
        if popup.get("hover") == target or pointed == target:
            view.shape(rx, ry + 1, rw, rh - 2, ui_tokens.HIGHLIGHT, 9)
        if pointed == target:   # The keyboard's place: the shared ring.
            from . import ui_kit
            ui_kit.focus_ring(view, (rx, ry + 1, rw, rh - 2), 9, 1.0)
        if kind == "toggle":
            track = (rx + 8, ry + 9, 30, 17)
            view.shape(*track, ui_tokens.TEAL if row["on"] else ui_tokens.CONTROL_OFF, 8)
            view.shape(track[0] + (15 if row["on"] else 2), track[1] + 2, 13, 13,
                       ui_tokens.INK if row["on"] else ui_tokens.TEXT, 6)
            view.text(view.elide(_label(row["label"]), rw - 56, 12), rx + 48, ry + 13, 12)
        else:
            draw_row(view, row, (rx, ry, rw, rh))
        bottom_y, top_y = max(ly, ry), min(ly + lh, ry + rh)
        _hit(view, state, (rx, bottom_y, rw, top_y - bottom_y), row["action"], row["value"], width, height)
        found.append((row["action"], row["value"], i, 0) if pinned else (row["action"], row["value"], i, 0, rect))
    gpu.state.scissor_set(*old_scissor)
    return found


def draw_scrollbar(view, popup, area):
    """The thin thumb beside a list that scrolls (``popup["layout"]["max_scroll"]``)."""
    geometry = popup.get("layout") or {}
    if not geometry.get("max_scroll"):
        return
    lx, ly, lw, lh = area
    content = lh + geometry["max_scroll"]
    thumb_h = max(22, lh * lh / content)
    thumb_y = ly + lh - thumb_h - (lh - thumb_h) * popup.get("scroll", 0) / geometry["max_scroll"]
    view.shape(lx + lw + 2, thumb_y, 2, thumb_h, ui_tokens.MUTED, 1)


def _hit(view, state, rect, action, value, width, height):
    from .chat_hit_testing import clip_rect
    visible = clip_rect(rect, (0, 0, width, height))
    if visible:
        state["popup_hits"].append((visible, action, value))


def draw(view, state, controller, width, height, window_fit_target):
    """The open + menu (``chat_add_draw``); ``window_fit_target`` is the selected mesh's shown name or ''."""
    from . import chat_add_draw
    chat_add_draw.draw(view, state, controller, width, height, window_fit_target)


def dispatch(context, controller, state, action, value):
    """A click (or Return) on one of the + menu's tiles, rows, buttons or links (``chat_add_nav``)."""
    from . import chat_add_nav
    chat_add_nav.dispatch(context, controller, state, action, value)


def key(popup, event):
    """A key press in an open menu. The + menu (``kind`` "add") is the launcher's (``chat_add_nav.key``: typing
    searches, arrows move in the grid). A plain list (the Flows popup): the arrow keys move over the actionable rows;
    returns the row to activate on Return, else None."""
    if popup.get("kind") == "add":
        from . import chat_add_nav
        try:
            import bpy
            clipboard = bpy.context.window_manager.clipboard
        except (ImportError, AttributeError):
            clipboard = ""
        from .ui_text import is_rtl
        return chat_add_nav.key(popup, event, clipboard if getattr(event, "type", "") == 'V' else "", rtl=is_rtl())
    rows = popup.get("rows") or []
    stops = actionable(rows)
    if not stops:
        return None
    cursor = popup.get("cursor") if popup.get("cursor") in stops else None
    if event.type in {'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'}:
        if event.type == 'HOME' or (cursor is None and event.type == 'DOWN_ARROW'):
            cursor = stops[0]
        elif event.type == 'END' or cursor is None:
            cursor = stops[-1]
        else:
            index = stops.index(cursor) + (-1 if event.type == 'UP_ARROW' else 1)
            cursor = stops[max(0, min(len(stops) - 1, index))]
        popup.update(cursor=cursor, keyboard=True, hover=None)
        geometry = popup.get("layout")
        if geometry:   # Keep the cursor's row in view.
            _, ry, _, rh = geometry["rows"][cursor]
            _, ly, _, lh = geometry["list_rect"]
            if ry < ly:
                popup["scroll"] = geometry["scroll"] + (ly - ry)
            elif ry + rh > ly + lh:
                popup["scroll"] = max(0, geometry["scroll"] - (ry + rh - ly - lh))
        return None
    if event.type in {'RET', 'NUMPAD_ENTER'} and cursor is not None:
        return rows[cursor]
    return None
