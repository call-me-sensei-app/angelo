# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the top bar's Chat button is, in window pixels (``anchors``): the chat hint's arrow, the Flows popup under
its button (``flow_menu.open_from_top_bar``) and the GUI suites' clicks read it (split from ``chat_toggle``, 1.0.5)."""

import bpy

from . import live_windows


def anchor_rect(region_x, region_y, scale_factor, units, unit, view_to_region=None):
    """``(left, right, bottom)`` of the toggle in window pixels. It is the first item of the top bar's right-hand
    region, laid out from ``4 * scale_factor`` pixels in its view (``ED_region_header_layout``: global areas use that
    offset). ``view_to_region`` maps that view x to the region's pixels: in Director the region is wider than its
    content and its view keeps the content flush right, so the button sat ~217 px right of region.x + 4 (part F's
    35f5b544698 measure; the Flows popup anchored from it opened left of its button)."""
    start = int(4.0 * scale_factor)
    left = region_x + (view_to_region(start) if view_to_region is not None else start)
    return left, left + units * unit, region_y


class _Anchors(dict):
    """window pointer -> (left, right, bottom): the toggle's rectangle in window pixels, read through the top bar
    region's view as it is NOW. The layout runs before the region's view settles, so a value kept from a draw went
    stale after any redraw that reset the view (Director: the bar's content sits flush right; the interim dbe022dddb9
    line's second Studio click went to 1737 instead of 1954). A read is live only while the window that drew it is
    open with the same screen; otherwise the value kept from its last draw."""

    def __getitem__(self, pointer):
        return _live_rect(pointer) or dict.__getitem__(self, pointer)

    def get(self, pointer, default=None):
        return _live_rect(pointer) or dict.get(self, pointer, default)


anchors = _Anchors()
_live = {}   # window pointer -> (screen pointer, region, scale, units, unit) from the toggle's last draw.


def _live_rect(pointer):
    entry = _live.get(pointer)
    if entry is None:
        return None
    screen, region, scale, units, unit = entry
    window = live_windows.find(bpy.context.window_manager, pointer, live=False)
    if window is not None and window.screen is None:
        return None   # Between screens: the entry is kept for the window's next frame.
    if window is None or window.screen.as_pointer() != screen:
        _live.pop(pointer, None)   # Closed, or another screen: the region may be gone.
        return None
    try:
        view = region.view2d
        return anchor_rect(region.x, region.y, scale, units, unit,
                           lambda x: view.view_to_region(x, 0, clip=False)[0])
    except (ReferenceError, AttributeError):
        _live.pop(pointer, None)
        return None


def record(context, region, units, unit):
    """The Chat button was drawn in ``region`` at ``units`` x ``unit`` pixels: keep its rectangle and the region to
    read it through later."""
    view = region.view2d
    scale = context.preferences.system.ui_scale
    rect = anchor_rect(region.x, region.y, scale, units, unit, lambda x: view.view_to_region(x, 0, clip=False)[0])
    pointer = context.window.as_pointer()
    if dict.get(anchors, pointer) != rect:
        # The region's view settles a draw after its layout (Director's flush-right content): a changed anchor asks
        # for one more top-bar draw, so the kept value settles too (reads go through the view: _Anchors).
        _settle_anchor_later()
    anchors[pointer] = rect
    _live[pointer] = (context.window.screen.as_pointer(), region, scale, units, unit)


_anchor_settling = [False]


def _settle_anchor_later():
    """One top-bar redraw from a timer (a draw callback never redraws)."""
    if _anchor_settling[0] or bpy.app.background:
        return
    _anchor_settling[0] = True

    def redraw():
        _anchor_settling[0] = False
        from .chat_toggle import redraw_top_bars
        redraw_top_bars()
        return None
    bpy.app.timers.register(redraw, first_interval=0.05)
