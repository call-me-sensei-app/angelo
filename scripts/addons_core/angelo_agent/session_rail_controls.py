# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel's own controls in its input handler (``session_rail_input``, kept under its size limit): a wheel
or trackpad over a strip, the header's minimize button, and the round button a minimized panel becomes
(``rail_minimized``; the maintainer, 2026-10-25: "user also should be able to minimize it when they don't want it in
the way"). Minimized and restored are the person's, kept with the preferences (``parallel_setting``).
"""

MOVES = frozenset({'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'})


def along(event, plan):
    """``(travel, unit)`` of a wheel notch or a trackpad swipe over a strip, towards later sessions, interface pixels:
    the wheel turned down, or a swipe on its larger axis whose content moves left (as Blender's horizontal views pan:
    view2d moves by ``prev_x - x``) or up (as the column's). A session per ``plan.step``, a tile and its gap, so tiles
    follow the finger. The deltas are ``scroll_input``'s, as on every surface."""
    from . import scroll_input
    travel = scroll_input.content_travel(event, plan.scale)
    if travel is not None:
        dx, dy = travel
        return (-dx if abs(dx) >= abs(dy) else dy), plan.step
    return -(scroll_input.view_up(event, plan.step, plan.scale) or 0.0), plan.step


def minimize(handler, window):
    """The header's minimize button was clicked: the panel becomes its round button, kept for the next launch; the
    pointer's hover and cursor over the panel go with it."""
    from . import parallel_setting
    parallel_setting.set_rail_minimized(True)
    handler._leave(window)


def minimized():
    """``rail_minimized`` while the person has the panel minimized (``parallel_setting.rail_minimized``), else None."""
    from . import parallel_setting
    if not parallel_setting.rail_minimized():
        return None
    from . import rail_minimized
    return rail_minimized


def restore_event(window, event):
    """While the panel is minimized, its round button's events (``rail_minimized.handle``: its hover and hand cursor,
    its press, and a click that shows the panel again); a move then passes on without the panel's own hover (it has
    no tiles while minimized). The modal's answer, or None for an event that is not the circle's (always None while
    the panel shows)."""
    from . import rail_minimized as module
    if not module.shown(window.as_pointer()):   # Minimized, or no room for a readable panel (``session_rail_place``).
        module.leave(window)   # Also clears a hover when the preference or layout changed before this event.
        return None
    taken = module.handle(window, event)
    if taken:
        return taken
    return {'PASS_THROUGH'} if event.type in MOVES else None


def leave(window):
    """The circle's hover, press and cursor end with the handler's (``ANGELO_OT_session_rail_input._leave``)."""
    from . import rail_minimized
    rail_minimized.leave(window)


def release():
    """Every circle's state goes with a file load and the add-on's unregister."""
    from . import rail_minimized
    rail_minimized.release()
