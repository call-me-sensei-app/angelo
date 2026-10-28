# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The transcript's thin, auto-hiding scrollbar, and trackpad scrolling.

API (``chat_view`` calls these):
    draw(state, scroll)    after the transcript, inside its scissor (``state["message_viewport"]``, ``max_scroll``)
    handle(event, state, area, x, y, session, scrolls, scale=1.0) -> True when it was the bar's, a wheel or a trackpad
        pan (``scroll_input``: the system's scroll direction, as Blender's views; ``scale``: window pixels per unit)

The transcript's scroll is measured up from the newest row (0 = at the bottom), as ``chat_view`` keeps it. The
bar shows while the pointer is over the transcript, for a moment after any scroll and while it is dragged; it
widens under the pointer. Dragging the thumb scrolls proportionally; a click on the track jumps there. Nothing is
drawn when everything fits.
"""

import time

from . import live_windows, scroll_input, ui_kit_rows

WIDE, MARGIN, MINIMUM = 8, 4, 28  # The hit strip; the drawn bar is the kit's (ui_kit_rows.draw_scrollbar).
LINGER = 1.2
_HIT = 14


def _geometry(state):
    viewport, max_scroll = state.get("message_viewport"), state.get("max_scroll", 0)
    if not viewport or max_scroll <= 0 or viewport[3] < 40:
        return None
    x, y, width, height = viewport
    total = height + max_scroll
    thumb = max(MINIMUM, (height - 8) * height / total)  # As ui_kit_rows.scrollbar_rects places it.
    track = (x + width - MARGIN - WIDE, y + 4, WIDE, height - 8)
    return track, thumb, max_scroll


def _thumb_rect(geometry, scroll):
    track, thumb, max_scroll = geometry
    y = track[1] + (track[3] - thumb) * min(1, max(0, scroll / max_scroll))
    return track[0], y, track[2], thumb


def draw(state, scroll):
    from . import chat_view as view
    geometry = _geometry(state)
    state["scrollbar"] = None
    if not geometry:
        return
    dragging = bool(state.get("scroll_drag"))
    if not (dragging or state.get("scroll_hover") or time.monotonic() - state.get("scroll_time", 0) < LINGER):
        return
    viewport, max_scroll = state["message_viewport"], geometry[2]
    content, visible, offset = viewport[3] + max_scroll, viewport[3], max_scroll - scroll  # Offset from the top.
    ui_kit_rows.draw_scrollbar(view, viewport, 1.0, content, visible, offset,
                               hover=state.get("scroll_hover") == "bar", dragging=dragging)
    state["scrollbar"] = ui_kit_rows.scrollbar_rects(viewport, content, visible, offset, 1.0)


def _set(scrolls, session, state, value):
    scrolls[session] = max(0, min(state.get("max_scroll", 0), value))
    state["scroll_time"] = time.monotonic()
    _fade_later()


def _redraw_chats():
    import bpy
    from . import console
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if console.is_chat(area):
            area.tag_redraw()


def _fade_later():
    """One redraw after the linger time, so the bar hides by itself."""
    import bpy
    if not bpy.app.timers.is_registered(_fade):
        bpy.app.timers.register(_fade, first_interval=LINGER + 0.05)


def _fade():
    _redraw_chats()
    return None


def handle(event, state, area, x, y, session, scrolls, scale=1.0):
    geometry = _geometry(state)
    if event.type == 'TRACKPADPAN':
        viewport = state.get("message_viewport")
        if not viewport or not viewport[1] <= y <= viewport[1] + viewport[3]:
            return False
    if event.type in scroll_input.SCROLL_EVENTS:
        # Measured up from the newest row: the view moving up (earlier rows) is a larger scroll.
        _set(scrolls, session, state, scrolls.get(session, 0) + scroll_input.view_up(event, 66, scale))
        area.tag_redraw()
        return True
    drag = state.get("scroll_drag")
    if event.type == 'MOUSEMOVE':
        if drag and geometry:
            track, thumb, max_scroll = geometry
            travel = max(1, track[3] - thumb)
            _set(scrolls, session, state, drag[1] + (y - drag[0]) * max_scroll / travel)
            area.tag_redraw()
            return True
        hover = None
        viewport = state.get("message_viewport")
        if viewport and geometry and viewport[1] <= y <= viewport[1] + viewport[3] and 0 <= x <= viewport[2]:
            track = geometry[0]
            hover = "bar" if track[0] - _HIT / 2 <= x <= track[0] + track[2] + MARGIN else "transcript"
        if hover != state.get("scroll_hover"):
            state["scroll_hover"] = hover
            area.tag_redraw()
        return hover == "bar"
    if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and drag:
        state.pop("scroll_drag", None)
        state["scroll_time"] = time.monotonic()
        _fade_later()
        area.tag_redraw()
        return True
    if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and geometry and state.get("scroll_hover") == "bar":
        track, thumb, max_scroll = geometry
        if not track[1] <= y <= track[1] + track[3]:
            return False
        _, ty, _, th = _thumb_rect(geometry, scrolls.get(session, 0))
        if not ty <= y <= ty + th:  # The track: jump so the thumb centres on the pointer.
            _set(scrolls, session, state, (y - thumb / 2 - track[1]) / max(1, track[3] - thumb) * max_scroll)
        state["scroll_drag"] = (y, scrolls.get(session, 0))
        area.tag_redraw()
        return True
    return False
