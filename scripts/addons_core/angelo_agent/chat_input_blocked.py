# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat whose window another tool holds, and the overlays drawn over a chat that take its keys.

The chat's input handler (``chat_view.ANGELO_OT_chat_input.modal``) passes every event on while another tool holds
the window; a click on the chat meanwhile records which tool, so the chat says how to end it (``chat_view`` draws
the notice), and the notice goes with the first event the chat takes again. Per-area state lives in
``chat_view._views``. No interface text of its own.
"""

import time

from . import live_windows


def note_blocked_click(window, event, blocking):
    """A click on a chat while another tool holds this window's input: the chat shows which, and how to end it."""
    from . import chat_view, console
    for area in live_windows.areas(window):
        if console.is_chat(area) and area.x <= event.mouse_x < area.x + area.width and area.y <= event.mouse_y < area.y + area.height:
            chat_view._views.setdefault(area.as_pointer(), {})["blocked"] = (tuple(blocking), time.monotonic())
            area.tag_redraw()


def over_overlay(window, event):
    """Whether the pointer rests on the area the welcome screen or a card that takes keys is drawn over."""
    from . import card_host, intro
    for area in live_windows.areas(window):
        if area.x <= event.mouse_x < area.x + area.width and area.y <= event.mouse_y < area.y + area.height:
            pointer = area.as_pointer()
            return (intro.is_open() and intro._open.get("area") == pointer) or card_host.takes_keys(pointer)
    return False


def clear_blocked_notice(window):
    """The tool ended: its notice goes with the first event the chat takes again."""
    from . import chat_view, console
    for area in live_windows.areas(window):
        if console.is_chat(area) and chat_view._views.get(area.as_pointer(), {}).pop("blocked", None):
            area.tag_redraw()
