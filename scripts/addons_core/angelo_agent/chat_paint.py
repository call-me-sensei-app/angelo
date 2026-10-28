# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A dock is done when the chat has drawn as the chat, not when its area exists (doc/angelo/agent_chat.rst, "Show and
hide").

The dock's last step makes the area the chat (``ui.setup_chat_area``) in a timer tick, and the dock reported success in
that same tick, before the area's first draw as the chat: corner Angelo went and ``on_complete`` ran on a frame that
had not shown the chat yet. The move now holds (``ui._layout_timer``) until the chat area's region has drawn after that
step, at most ``WAIT`` seconds (in practice one frame), and only then reports.

What it does not fix, because it was never broken: the app draws the chat in the first frame after the dock (Mac 1
diagnostics, 2026-10-23: the region's own pixels and Blender's own screenshot showed it 0.06 s after the dock, shipped
1.0.4 too). The "second Properties editor where the chat was" in release-105's screenshot review (row 3) came from the
GUI suites' capture: two forced buffer swaps (``wm.redraw_timer`` DRAW_WIN_SWAP) then Blender's front-buffer read,
which on macOS returned the window from mid-dock. The suites therefore judge the chat by its region's own pixels.
"""

import time

import bpy

from . import live_windows

WAIT = 2.0          # Seconds: a chat that has not drawn by then is reported anyway (a hidden or minimised window).
_drawn = {}         # chat area pointer -> when its region last drew (time.monotonic), while a dock waits.
_handler = None


def _record():
    """After the chat's own draw (POST_PIXEL): this area has drawn."""
    from . import console
    area = bpy.context.area
    if area is not None and console.is_chat(area):
        _drawn[area.as_pointer()] = time.monotonic()


def _stop_watching():
    global _handler
    if _handler is not None:
        bpy.types.SpaceConsole.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    _drawn.clear()


def finish_dock(window, area, on_complete=None):
    """Hold the dock (``ui._layout_timer``) until ``area`` of ``window`` has drawn as the chat, then end it: corner
    Angelo settles (``chat_launcher.settled``) and ``on_complete(window, area)`` runs, if the area still exists."""
    global _handler
    from . import ui
    pointer, window_pointer, since = area.as_pointer(), window.as_pointer(), time.monotonic()
    if _handler is None:
        _handler = bpy.types.SpaceConsole.draw_handler_add(_record, (), 'WINDOW', 'POST_PIXEL')
    area.tag_redraw()

    def check(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        if _drawn.get(pointer, 0.0) <= since and time.monotonic() - since < WAIT:
            return 0.02
        _stop_watching()
        ui._layout_timer = None
        from . import chat_launcher
        chat_launcher.settled(window_pointer)
        chat = next((a for a in live_windows.areas(target) if a.as_pointer() == pointer), None)
        if on_complete and chat is not None:
            try:
                on_complete(target, chat)
            except Exception as error:   # noqa: BLE001  A caller's follow-up never strands the move.
                print("Angelo: after the chat docked:", error)
        return None

    ui._layout_timer = live_windows.deferred(lambda: bpy.context.window_manager, window_pointer, check)
    bpy.app.timers.register(ui._layout_timer, first_interval=0.02)


def unregister():
    _stop_watching()
