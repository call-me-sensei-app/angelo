# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's press on a grant control that was not armed yet is answered quietly, with a hint on the control
(doc/angelo/grant_operators.rst, "Authority in C"; the coordinator's ruling on the 0.3 s arm, 2026-11-16).

A control that reaches a grant takes a press only after the person's real move onto it once it has been on screen
``ARM_SECONDS``. C records a press that came sooner, or with no move onto the control since (``GUARD.unarmed``: which
control, and where). Its refusal is never an error report, a toast or "Only your own click…": the press does nothing
and the control shows, for a few seconds, what works. Any small move inside it then arms it, a hand's jitter
included, so the next click works without leaving the control.

``answer`` runs a surface's click (``grant_guard.ticketed``) with that press in hand: a guarded operator it reaches
refuses quietly (``refused``) and the hint is shown once it returns. A press on Blender's own button reaches the
operator directly, which asks ``refused`` too. Anything else a click does runs as it always does.
"""

import threading
import time

from .ui_text import n_

HINT = n_("Move onto the button, then click")
HINT_SECONDS = 3.0
_answering = threading.local()   # The unarmed press a surface's click is running for, and whether a grant refused it.
_shown = {}      # region pointer -> ((x, y, width, height) in region pixels, until): the hints on screen.
_handlers = {}   # (space type, region type) -> the draw handler that draws them there.


def take(idname, digest):
    """The person's unarmed press on control ``idname``/``digest`` (C's record, taken once), or None. Never while an
    agent acts on this thread: its call to the same control is refused as the agent's, and the person's record stays
    for their own press (``agent_tool_scope.agent_acting``)."""
    from . import agent_tool_scope, grant_authority
    if not grant_authority.available() or getattr(grant_authority.GUARD, "unarmed", None) is None:
        return None
    if agent_tool_scope.agent_acting():
        return None
    return grant_authority.GUARD.unarmed(idname, digest)


def answer(idname, digest, function, *args, **kwargs):
    """``function(*args, **kwargs)``, a surface's click that minted no ticket: when it was the person's press on that
    control before it armed, a grant it reaches refuses quietly and the control shows the hint."""
    spot = take(idname, digest)
    if spot is None:
        return function(*args, **kwargs)
    _answering.spot, _answering.refused = spot, False
    try:
        return function(*args, **kwargs)
    finally:
        refused, _answering.spot = _answering.refused, None
        if refused:
            show(spot)


def refused(idname):
    """In a guarded operator's refusal for want of the person's click: whether it is their press made before the
    control armed, answered with the hint (True: report nothing) rather than an error."""
    if getattr(_answering, "spot", None) is not None:
        _answering.refused = True
        return True
    spot = take(idname, "")   # Blender's own button (or "prop.<name>"): the operator itself is where it lands.
    if spot is None:
        return False
    show(spot)
    return True


def show(spot):
    """The hint on the control at ``spot`` (window, x, y, width, height in window pixels) for ``HINT_SECONDS``."""
    import bpy
    from . import live_windows
    window_pointer, x, y, width, height = spot
    window = live_windows.find(bpy.context.window_manager, window_pointer)
    if window is None:
        return
    cx, cy = x + width / 2, y + height / 2
    for area in live_windows.areas(window):
        for region in area.regions:
            if region.x <= cx < region.x + region.width and region.y <= cy < region.y + region.height:
                _shown[region.as_pointer()] = ((x - region.x, y - region.y, width, height),
                                               time.monotonic() + HINT_SECONDS)
                key = (type(area.spaces.active), region.type)
                if key not in _handlers:
                    _handlers[key] = key[0].draw_handler_add(_draw, (), region.type, 'POST_PIXEL')
                region.tag_redraw()
                if not bpy.app.timers.is_registered(_expire):
                    bpy.app.timers.register(_expire, first_interval=HINT_SECONDS)
                return


def showing():
    """The hints on screen now, as region pointers (window fixtures read them)."""
    now = time.monotonic()
    return [pointer for pointer, (_rect, until) in _shown.items() if until > now]


def _draw():
    import bpy
    region = bpy.context.region
    found = _shown.get(region.as_pointer()) if region else None
    if found is None or found[1] <= time.monotonic():
        return
    from . import canvas_controls, chat_view
    from .ui_text import iface_
    canvas_controls.tip(chat_view, found[0], iface_(HINT), chat_view.ui_scale(), region.width, region.height)


def _expire():
    """Drop the hints whose time is up, redraw where they were, and keep no draw handler without a hint."""
    import bpy
    from . import live_windows
    now = time.monotonic()
    for pointer in [p for p, (_rect, until) in _shown.items() if until <= now]:
        del _shown[pointer]
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        area.tag_redraw()
    if _shown:
        return max(0.1, min(until for _rect, until in _shown.values()) - now)
    for (space, _region_type), handler in list(_handlers.items()):
        space.draw_handler_remove(handler, _region_type)
    _handlers.clear()
    return None


def unregister():
    """The add-on unloads: no hint and no draw handler stays behind."""
    import bpy
    _shown.clear()
    if bpy.app.timers.is_registered(_expire):
        bpy.app.timers.unregister(_expire)
    for (space, region_type), handler in list(_handlers.items()):
        space.draw_handler_remove(handler, region_type)
    _handlers.clear()
