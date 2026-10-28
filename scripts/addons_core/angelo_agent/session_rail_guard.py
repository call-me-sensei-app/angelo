# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The session rail's input handler's guards (``session_rail_input``): what happens to a press when the handler
raises, and when a press ends. Each wraps the handler's ``modal`` and keeps its own rule."""

import functools
import traceback


def ends_presses(modal):
    """A press on a tile ends with its release wherever it lands (over the rail, the view, another area) or when the
    handler raises: a press dragged off its tile and released elsewhere stayed pending, holding the rail still under
    new text (break-it P1-15 M1)."""

    @functools.wraps(modal)
    def ended(self, context, event):
        try:
            return modal(self, context, event)
        finally:
            window = context.window
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and window is not None \
                    and window.as_pointer() == self.window:
                self._unpress(window)
    return ended


def fail_closed(modal):
    """One failed event never ends the rail's input, and never lets a press reach a document no one sees: over a
    live view a press that raised is held, elsewhere it passes (break-it P1-1 M2)."""

    @functools.wraps(modal)
    def kept(self, context, event):
        try:
            return modal(self, context, event)
        except Exception:   # noqa: BLE001
            traceback.print_exc()
            from . import live_view_draw
            pressed = getattr(event, "value", "") in {'PRESS', 'CLICK', 'DOUBLE_CLICK', 'CLICK_DRAG'}
            window = context.window
            area = live_view_draw.target(window) if window is not None and live_view_draw.active_worker() else None
            over = area is not None and (area.x <= event.mouse_x < area.x + area.width
                                         and area.y <= event.mouse_y < area.y + area.height)
            return {'RUNNING_MODAL'} if pressed and over else {'PASS_THROUGH'}   # Elsewhere a bug blocks nothing.
    return kept
