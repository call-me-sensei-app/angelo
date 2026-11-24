# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Flows popup (``flow_menu``) for the grant guard: its rows as C's consent regions (``flowmenu.row``), and the
popup itself as a cover over every other surface's controls while it is open (``GUARD.cover``; the review of
f6b01a9..da9ba66, SHOULD 2): a move over the popup never aims at a card or Library control beneath, a click on it is
never theirs, and when it closes they start again, so a double-click's second press grants nothing the person did not
see. A cover only ever refuses or restarts: it gives no grant to anyone, an agent script's or not.
"""

import bpy

from . import chat_add_menu, grant_guard, ui_text

SURFACE = "flowmenu"


def _window_rect(region, rect, scale, rtl):
    """``rect`` (x, y, width, height) laid out unscaled in ``region`` as window pixels, mirrored in a right-to-left
    language, exactly as ``grant_guard.canvas_regions`` places a surface's controls."""
    x, y, width, height = rect
    left = region.width - (x + width) * scale if rtl else x * scale
    return int(region.x + left), int(region.y + y * scale), int(width * scale), int(height * scale)


def _cover(window, rect, region=None):
    """Not gated on an agent's run (the last review of da9ba66..1b4e7f0, SHOULD 1): a popup open during one is covered
    as any other, and C refuses only an agent script's own thread (a cover can only narrow). One C could not clear
    goes by itself once its region does (the draw sync), and fails closed until then."""
    authority = grant_guard._authority()
    if authority.available():
        try:
            if region is None:
                authority.GUARD.cover(window.as_pointer(), SURFACE, rect)
            else:
                authority.GUARD.cover(window.as_pointer(), SURFACE, rect, region.as_pointer())
        except (PermissionError, ValueError, TypeError, AttributeError) as error:   # An older guard: no cover.
            print("Angelo: the Flows popup's cover was not registered:", error)


def consent_regions(state, region):
    """Rows as C's regions (``flowmenu.row``): a row's grant (an engine setup) runs in its press's ticket scope; the
    popup's whole list as a cover over the other surfaces."""
    geometry, items, hits = state.get("layout"), state["rows"], []
    if not geometry:
        return
    lx, ly, lw, lh = geometry["list_rect"]
    for index, (rx, ry, rw, rh) in enumerate(geometry["rows"]):
        top, bottom = min(ry + rh, ly + lh), max(ry, ly)
        if items[index]["kind"] in chat_add_menu.ACTIONABLE and top > bottom:
            hits.append(((rx, bottom, rw, top - bottom), "row", index))
    scale, rtl = state.get("scale") or 1.0, ui_text.is_rtl()
    grant_guard.canvas_regions(bpy.context.window, region, SURFACE, hits, scale, rtl, prefix="flowmenu.",
                               focus=("row", state["cursor"]) if state["keyboard"] else None)
    _cover(bpy.context.window, _window_rect(region, geometry["list_rect"], scale, rtl), region)


def closed(window):
    """The popup closed in ``window``: what it covered starts again."""
    if window is not None:
        _cover(window, None)
