# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a wheel turn or a trackpad swipe moves Angelo's own scrollable surfaces: as Blender's own views move.

Every Angelo-drawn surface that scrolls (the chat transcript, the chat's pickers, card scroll boxes, the Generative
AI editor's columns and lists) asks ``view_up`` how far its view moves, so none can scroll against the person's
system setting.

The operating system has already applied the person's scroll direction (macOS natural scrolling, the Windows
touchpad setting, libinput's natural scrolling on Linux) to the deltas Blender receives, and Blender's views
follow those deltas unchanged: ``view2d`` pans its view by ``prev_xy - xy`` for a trackpad pan (``MOUSEPAN``,
Python's ``TRACKPADPAN``) and up for ``WHEELUPMOUSE``. ``WM_event_absolute_delta_y`` is the opposite question (the
physical direction of the fingers, for changing a value; Blender's Trackpad direction preference only sets that
flag) and is not what a scrolling surface wants.

API:
    view_up(event, wheel_step, scale=1.0) -> float or None
        How far the view moves toward the top of its content (positive: earlier rows come into view), in the
        surface's own layout units. ``wheel_step`` is one wheel notch; a trackpad swipe moves the content by its
        delta, as Blender's views do (one pixel per pixel on macOS; ``scale``: window pixels per layout unit). None
        for other events.

A surface whose scroll offset is measured down from its top subtracts the result; one measured up from its bottom
(the chat transcript, 0 = the newest row) adds it.
"""

WHEEL_EVENTS = frozenset({'WHEELUPMOUSE', 'WHEELDOWNMOUSE'})
SCROLL_EVENTS = WHEEL_EVENTS | {'TRACKPADPAN'}


def view_up(event, wheel_step, scale=1.0):
    if event.type == 'WHEELUPMOUSE':
        return float(wheel_step)
    if event.type == 'WHEELDOWNMOUSE':
        return -float(wheel_step)
    if event.type == 'TRACKPADPAN':
        return (event.mouse_prev_y - event.mouse_y) / (scale or 1.0)  # As view2d's pan: deltay = prev_y - y.
    return None


def content_travel(event, scale=1.0):
    if event.type != 'TRACKPADPAN':
        return None
    scale = scale or 1.0
    return (event.mouse_x - event.mouse_prev_x) / scale, (event.mouse_y - event.mouse_prev_y) / scale
