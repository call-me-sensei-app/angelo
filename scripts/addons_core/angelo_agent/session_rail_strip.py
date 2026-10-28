# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel as a strip centred across the top or the bottom of the 3D view (``parallel_setting``'s
``rail_position`` "TOP" or "BOTTOM"; the maintainer, 2026-10-25: "it can potentially be at the top and bottom in the
middle as well. We can let users decide", "Either case it should be a scrollable container").

The column's tiles (``session_rail``) stand side by side under the same header row, in the same scroll container:
the main tile first and kept, a window of workers that pages, a "+N more" cell at the window's leading and trailing
edges where workers are hidden (amber while a hidden one waits for approval, as the header's arrows are), the active
worker brought into view after a switch (``session_rail.keep_in_view``) and the header's minimize button. A click on a
tile switches the big view as the column's does. Pure: host-tested with plain values; ``session_rail_place`` reads
what the strip clears from the view, ``session_rail_draw`` draws it.

**What it clears** (``insets``): at the top the headers over the view, the live view's label under them
(``live_view_label``; its room is kept whether or not a live view shows, so the strip stands still across a switch)
and the view's own text info. The strip stands *below* the text info, never beside it: Blender says where its text
ends (``session_rail.text_inset``) but not how wide its lines are (statistics run far to the right), so beside it
could never be proven clear, and the label it must clear too is centred under the headers anyway. As the column does
(``session_rail.fit_below_text``), a view too short for that gets the strip over the text's last lines rather than
off the view. At the bottom it clears the regions stacked there (``session_rail.bottom_inset``: an asset shelf, its
tab strip, a header flipped down) and the shelf's Media Studio and Generation Flows pills, which Angelo draws over the
view on the shelf's top edge (``asset_shelf_add``; Director hides every shelf, ``director_layout.configure``, so its
view has none until one is shown, and then the same rule holds). At the sides it clears the toolbar and the sidebar,
and what stands in a corner wherever the strip's band crosses it: the navigation gizmo and its buttons at the top
right, Angelo in the corner (``chat_launcher``) at a bottom one. It is centred in the room left between them.

**Sizes:** tiles keep the column's width; a strip takes at most ``SHARE`` of the room's height, since it spans the
middle of the view, so pictures shrink and then go (compact) as the column's do in a short view; a view that cannot
hold the least strip at the person's scale gets it at the largest smaller scale that does (``session_rail.fit``).
"""

from . import session_rail as rail
from .session_rail import GAP, HEADER, INSET, MARGIN, MIN_PICTURE, PAD, PICTURE_RATIO, TEXT_BLOCK, WIDTH

SHARE = 1 / 3      # The most of the room's height a strip takes: the scene it spans is the view's middle too.
MORE_WIDTH = 72    # A "+N more" cell's width (scale 1) when the draw does not measure its words (host tests).
SIDES = ("TOP", "BOTTOM")
READABLE = 0.5     # The least share of the person's scale a panel is drawn at (rail_minimized.SMALLEST, the button's).
LEAVE_EDGE = 2.0   # A strip leaves its edge to clear a corner only where that makes it more than this many times larger.


def least(count, more_width=MORE_WIDTH):
    """The least strip of ``count`` sessions, (height, width) at a scale of 1, margins in: one compact tile, and where
    it can page (more than one session) room for the main tile with a worker, or a "+N more" cell on either side, so
    the main tile stays in view wherever the panel is drawn (``session_rail.least``)."""
    cells = 2 * ((MORE_WIDTH if more_width is None else more_width) + GAP)
    along = max(WIDTH + cells, 2 * WIDTH + GAP) if count > 1 else WIDTH
    return 2 * MARGIN + 2 * PAD + HEADER + TEXT_BLOCK, 2 * MARGIN + 2 * PAD + along


def heights(s, tall):
    """``(panel height, picture height or None)`` of a strip at scale ``s`` in a room ``tall`` pixels high (margins
    out): pictures at their own shape where ``SHARE`` of the room holds them, smaller down to ``MIN_PICTURE``, else
    none (compact)."""
    fixed = (2 * PAD + HEADER + TEXT_BLOCK) * s
    picture = min((WIDTH - 2 * INSET) * PICTURE_RATIO * s, tall * SHARE - fixed - INSET * s)
    if picture < MIN_PICTURE * s:
        return fixed, None
    return fixed + picture + INSET * s, picture


def layout(states, size, scale, insets, side, first=0, keep=None, more_width=None):
    """Where every tile goes in a strip on ``side`` ("TOP" or "BOTTOM") of a region of ``size`` (width, height):
    ``states`` the rail's tiles without rects (``session_rail.layout``'s), ``insets`` the {"left", "right", "top",
    "bottom"} pixels it clears (``insets``), ``first`` and ``keep`` its paging (``session_rail.page``). ``more_width``:
    a "+N more" cell's width at a scale of 1, as wide as its words in this language (the draw measures them). A
    ``session_rail.Layout`` with orientation "HORIZONTAL": ``arrows`` (left, right), ``hidden``, ``attention`` and
    ``more`` (before, after), the same hit actions as the column's (pure)."""
    width, height = size
    left, right, top, bottom = (insets.get(name, 0) for name in ("left", "right", "top", "bottom"))
    count = len(states)
    result = rail.Layout(scale=scale, orientation="HORIZONTAL")
    if not count:
        return result
    cell = MORE_WIDTH if more_width is None else more_width
    s = rail.fit(scale, (height - top - bottom, width - left - right), least(count, cell))
    if s <= 0:
        return result
    tile_w, gap = WIDTH * s, GAP * s
    span = width - left - right - 2 * MARGIN * s   # The widest the panel may be.
    panel_h, picture_h = heights(s, height - top - bottom - 2 * MARGIN * s)
    tile_h = panel_h - (2 * PAD + HEADER) * s
    result.scale, result.compact, result.step = s, picture_h is None, (tile_w + gap) / s
    room = span - 2 * PAD * s
    pinned, shown, lines = states, [], (False, False)
    if count * (tile_w + gap) - gap > room:
        pinned, shown, lines = rail.page(states, room, tile_w + gap, gap, cell * s + gap, first, keep, result)
    items = rail.sequence(pinned, shown, lines)
    extents = [tile_w if kind == "tile" else cell * s for kind, _value in items]
    panel_w = sum(extents) + (len(items) - 1) * gap + 2 * PAD * s
    x = left + MARGIN * s + (span - panel_w) / 2
    y = height - top - MARGIN * s - panel_h if side == "TOP" else bottom + MARGIN * s
    result.panel = (x, y, panel_w, panel_h)
    result.header_at = (x + PAD * s + 2 * s, y + panel_h - PAD * s - 17 * s)
    rail.controls_at(result, x + panel_w - PAD * s, y + panel_h - PAD * s - HEADER * s + 3 * s, s)   # Before tiles.
    at = x + PAD * s
    for (kind, value), extent in zip(items, extents):
        rect = (at, y + PAD * s, extent, tile_h)
        if kind == "tile":
            rail.place_tile(result, value, rect, picture_h, s)
        else:
            rail.more_at(result, value, rect)
        at += extent + gap
    return result


def insets(side, size, scale, sides=(0, 0), headers=0, label=0, text=0, shelf=0, edge=(), corners=(), more_width=None,
           count=2):
    """The ``insets`` a strip on ``side`` clears in a region of ``size``, region pixels: ``sides`` the (left, right)
    pixels the toolbar and the sidebar cover; ``headers`` those of the headers over the view's top
    (``session_rail.top_inset``); ``label`` the live view label's room under them (``live_view_label.room``); ``text``
    where the view's text info ends, from the top (``session_rail.text_inset``, held steady); ``shelf`` the regions
    stacked at the bottom (``session_rail.bottom_inset``); ``edge`` the tops, from the bottom, of Angelo's overlays on
    the bottom edge (the shelf's pills); ``corners`` the (x, y, w, h) of what stands in a corner (the navigation gizmo,
    Angelo in the corner), cleared wherever the strip's band crosses it (``clear``). Both strips keep below the top's
    and above the bottom's, so a strip in a short view still fits between them (pure)."""
    width, height = size
    left, right = sides
    bottom = max([shelf] + list(edge))
    hard = headers + label   # Never covered: the headers and the live label.
    top = max(hard, min(max(text, hard), height - bottom - least(1)[0] * scale))   # Over the text's foot if need be.
    room = {"left": left, "right": right, "top": top, "bottom": bottom}
    need = least(count, MORE_WIDTH if more_width is None else more_width)

    def band(room, s):
        panel_h = heights(s, height - room["top"] - room["bottom"] - 2 * MARGIN * s)[0]
        low = height - room["top"] - MARGIN * s - panel_h if side == "TOP" else room["bottom"]
        high = height - room["top"] if side == "TOP" else room["bottom"] + MARGIN * s + panel_h
        return room["left"], low, width - room["right"], high

    # A strip keeps to its edge: a corner it crosses is cleared beside it, past it along its side only where that
    # more than doubles the strip (``LEAVE_EDGE``; a top strip hanging mid-view no longer reads as at the top).
    return clear(room, corners, size, scale, need, band, ("left", "right", "top" if side == "TOP" else "bottom"),
                 prefer=("left", "right"))


def column_insets(size, scale, count, sides=(0, 0), headers=0, label=0, text=0, shelf=0, edge=(), corners=()):
    """The column's room (``session_rail.layout``'s insets) from the same obstacles as a strip's (``insets``), so no
    rule differs by position (the rail positions GUI suite, 2026-10-25: the column's foot ran over the shelf's pills):
    below the headers, the live view's label and the view's text info (``session_rail.fit_below_text``: over the
    text's last lines only in a view too short), above the bottom regions and the shelf's pills, beside the toolbar
    and the sidebar; a corner thing in its band (Angelo in the corner with the chat docked left, the navigation gizmo
    in a narrow view) is cleared above, below or beside it, whichever leaves the largest column (``clear``) (pure)."""
    width, height = size
    left, right = sides
    bottom = max([shelf] + list(edge))
    hard = headers + label
    top = rail.fit_below_text(hard, max(text, hard), count, height, scale, bottom)
    room = {"left": left, "right": right, "top": top, "bottom": bottom}
    need = rail.least(count)

    def band(room, s):
        return room["left"], room["bottom"], room["left"] + need[1] * s, height - room["top"]

    return clear(room, corners, size, scale, need, band, ("top", "bottom", "right", "left"))


def readable(compute, size, scale, need, obstacles):
    """The room ``compute(**obstacles)`` gives (``insets`` or ``column_insets`` with their keywords), unless the panel
    (``need``: its least size at a scale of 1) would be drawn there below ``READABLE`` of the person's ``scale``: then
    the room with the corner things covered (the navigation gizmo, Angelo in the corner, a live label beside a
    column: drawn in the view itself, under the panel, so it still takes its clicks). None when that is not readable
    either: the toolbar and the sidebar are regions drawn over the view and take the clicks under them, so a panel
    there could be neither seen nor used, and the round button stands for it (``rail_minimized``; Windows T4,
    2026-10-26: a 1280x737 window at 2x left a 12 px panel) (pure)."""
    width, height = size
    for relaxed in ({}, {"corners": ()}):
        room = compute(**{**obstacles, **relaxed})
        s = rail.fit(scale, (height - room["top"] - room["bottom"], width - room["left"] - room["right"]), need)
        if s >= READABLE * scale - 1e-9:
            return room
    return None


def clear(room, corners, size, scale, need, band, ways, prefer=None):
    """``room`` with each of ``corners`` the panel's ``band`` (``band(room, scale)``: x0, y0, x1, y1 at that scale)
    crosses cleared one of ``ways``: "left" or "right" (the room ends beside it), "top" or "bottom" (the room ends
    past it along that edge), whichever leaves the largest panel (``session_rail.fit`` of ``need``, its least size);
    the first of ``ways`` when they tie; one of ``prefer`` unless another leaves a panel ``LEAVE_EDGE`` times its
    size. Corners are clipped to the region first (Blender's gizmo outgrows a short view), and
    which side a corner stands on is never guessed from the region's middle: a sidebar can cover half of it. A corner
    no way clears without leaving no room at all is covered rather than the panel lost. Corner by corner first; where
    a corner is still crossed after that, every joint choice of ways is tried (the review, 2026-10-25: one corner's
    best way can leave another none) (pure)."""
    width, height = size

    def fits(candidate):
        return rail.fit(scale, (height - candidate["top"] - candidate["bottom"],
                                width - candidate["left"] - candidate["right"]), need)

    clipped = [(max(0.0, x), max(0.0, y), min(float(width), x + w), min(float(height), y + h))
               for x, y, w, h in corners]
    clipped = [rect for rect in clipped if rect[2] > rect[0] and rect[3] > rect[1]]
    base = (dict(room), ())

    def crossed(candidate, rect):
        s = fits(candidate)
        bx0, by0, bx1, by1 = band(candidate, s if s > 0 else scale)
        return rect[0] < bx1 and rect[2] > bx0 and rect[1] < by1 and rect[3] > by0

    for _round in range(len(clipped) + 1):   # Clearing one can move the panel onto another: again until none moves.
        moved = False
        for x0, y0, x1, y1 in clipped:
            if not crossed(room, (x0, y0, x1, y1)):
                continue
            reach = {"left": x1, "right": width - x0, "top": height - y0, "bottom": y1}
            options = [{**room, way: max(room[way], reach[way])} for way in ways]
            best = max(options, key=fits)
            if prefer:
                kept = max((option for way, option in zip(ways, options) if way in prefer), key=fits)
                best = best if fits(best) > LEAVE_EDGE * fits(kept) else kept
            if fits(best) > 0 and best != room:
                room, moved = best, True
        if not moved:
            break
    if not any(crossed(room, rect) for rect in clipped):
        return room
    joint = [base]
    for x0, y0, x1, y1 in clipped:
        reach = {"left": x1, "right": width - x0, "top": height - y0, "bottom": y1}
        joint = [({**option, way: max(option[way], reach[way])}, kept + (way in (prefer or ways),))
                 for option, kept in ((o, k) for o, k in joint) for way in ways]
    cleared = [(option, kept) for option, kept in joint
               if fits(option) > 0 and not any(crossed(option, rect) for rect in clipped)]
    if not cleared:
        return room
    best = max(cleared, key=lambda pair: fits(pair[0]) / LEAVE_EDGE ** pair[1].count(False))
    return best[0]
