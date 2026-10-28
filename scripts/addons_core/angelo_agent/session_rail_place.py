# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the Sessions panel clears in a 3D view, in every position, read from the view as it draws (``obstacles``):
the regions over it, the live view's label, its text info, Angelo's own overlays (the asset shelf's Media Studio and
Generation Flows pills, Angelo in the corner) and the navigation gizmo with its buttons. One set for the column and
the strips (``session_rail_strip.column_insets`` and ``insets`` turn it into each one's room), so no rule can differ
by position. Read-only, inside the rail's draw (``session_rail_draw``); the geometry rules are pure and host-tested in
``session_rail_strip``.
"""


def obstacles(window, area, region, scale):
    """Keywords of ``session_rail_strip.insets`` and ``column_insets`` for ``region``: the toolbar and the sidebar,
    the headers over the view's top, the live view's label room under them (kept whether or not a live view shows,
    so the panel stands still across a switch), the regions stacked at the bottom, the shelf's pills and the corner
    overlays."""
    from . import chat_launcher, live_view_label, session_rail as rail
    left, right, _bottom = chat_launcher.insets(area.regions, region)
    return dict(sides=(left, right), headers=rail.top_inset(area.regions, region), label=live_view_label.room(scale),
                shelf=rail.bottom_inset(area.regions, region), edge=pills(window, area, region),
                corners=corners(window, area, region))


def strip(window, area, region, side, tiles, scale, text, first, keep, more_width):
    """The strip's ``session_rail.Layout`` in ``region`` on ``side``; ``text`` is the view's text inset held steady
    (``session_rail_draw._steady_text``)."""
    from . import session_rail_strip
    size = (region.width, region.height)
    count = len(tiles)
    room = session_rail_strip.readable(
        lambda **found: session_rail_strip.insets(side, size, scale, text=text, more_width=more_width, count=count,
                                                  **found),
        size, scale, session_rail_strip.least(count, more_width), obstacles(window, area, region, scale))
    if room is None:
        return None   # No readable panel fits: its round button stands for it (session_rail_draw).
    return session_rail_strip.layout(tiles, size, scale, room, side, first, keep, more_width)


def column(window, area, region, tiles, scale, text, first, keep, label=None):
    """The column's ``session_rail.Layout`` in ``region``, clear of the same obstacles. The live view's ``label``
    (its rect this frame, or None) is cleared only while it shows, as a corner is: centred under the headers, it
    meets a column only in a narrow view, and the column stays right under the headers otherwise (the session rail
    GUI suite, 2026-10-25: reserving its room always moved the column down by it)."""
    from . import session_rail as rail, session_rail_strip
    size = (region.width, region.height)
    found = obstacles(window, area, region, scale)
    found.update(label=0, corners=list(found["corners"]) + ([tuple(label)] if label else []))
    room = session_rail_strip.readable(
        lambda **kept: session_rail_strip.column_insets(size, scale, len(tiles), text=text, **kept), size, scale,
        rail.least(len(tiles)), found)
    if room is None:
        return None
    return rail.layout(tiles, size, scale, room["left"], room["top"], room["bottom"], first, keep, room["right"])


def pills(window, area, region):
    """The tops of the asset shelf's pills over this view, from the region's bottom: ``asset_shelf_add`` records them
    as it draws them (window pixels), in every layout where a shelf shows."""
    return tuple(y + h for _x, y, _w, h in pill_rects(window, area, region))


def pill_rects(window, area, region):
    """The asset shelf's pills over this view as region rects (``asset_shelf_add`` records them in window pixels as
    it draws them): the panel stands above them, the minimized button keeps clear of them."""
    from . import asset_shelf_add
    placed = asset_shelf_add.places.get(area.as_pointer())
    if not placed or placed[0] != window.as_pointer():
        return ()
    return tuple((x - region.x, y - region.y, w, h) for _action, (x, y, w, h) in placed[1])


def corners(window, area, region):
    """What stands in the view's corners, region rects: Angelo in the corner while the chat is hidden
    (``chat_launcher``, as the shelf's pills read it) and the navigation gizmo with its buttons as Blender draws them
    now (``rail_minimized.navigation_rect``)."""
    from . import rail_minimized
    found = [rect for rect in (angelo(window, area, region),) if rect is not None]
    gizmo = rail_minimized.navigation_rect(area, region)
    if gizmo is not None:
        found.append(tuple(gizmo))
    return found


def angelo(window, area, region):
    """Angelo in the corner while the chat is hidden (``chat_launcher``, as the shelf's pills read it), a region rect;
    None when it is not in this view. The panel and its minimized button both keep clear of it."""
    from . import chat_launcher
    place = chat_launcher._places.get(window.as_pointer())
    if not place or place[0] != area.as_pointer() or window.as_pointer() not in chat_launcher._shown:
        return None
    _area, cx, cy, radius = place
    return cx - radius - region.x, cy - radius - region.y, 2 * radius, 2 * radius
