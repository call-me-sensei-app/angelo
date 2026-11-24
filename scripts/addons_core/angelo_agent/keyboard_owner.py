# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One keyboard owner per window (2.1.0): the chat's field, or a surface drawn over the window that takes the keyboard
(a live view, the picture viewer).

The chat's handler sits behind the surfaces drawn over it, so it never sees a press another surface takes; it drops
its focused area at its next event when a surface asks (``blur_request``). Every surface that takes the keyboard (on a
press, or as it opens with it) calls ``take`` here, never its own copy (re-review of markup 4e712847d3b, M2: the
viewer took the keyboard and the chat kept it, so Return sent the chat's draft; of b5808fd5a2e, S1: the + menu's
row opened the viewer and the chat took the keyboard back as its menu closed, which ``blur_request`` now stops).

``chat_takes`` is the one rule for a key pressed with the pointer over a surface while a chat holds the keyboard:
the chat acts on it (``composer_input.takes_off_chat``) or nobody does (re-review of b5808fd5a2e, M1: the viewer
handed every key to the chat, which passed what it didn't use to the 3D view under the viewer). What the chat still
hands on is held by ``overlay_holds.holds_covered``. A hidden canvas forgets its focus (``launcher_waiting``), which is
no surface taking the keyboard.
"""

from . import live_windows


def _chats(window):
    """The window's chat areas and their view states, read now: a list, never a generator that holds ``window``
    across its steps (test_native_live_target: a held window goes through live_target)."""
    from . import chat_view, console
    return [(area, chat_view._views[area.as_pointer()]) for area in live_windows.areas(window)
            if console.is_chat(area) and chat_view._views.get(area.as_pointer()) is not None]


def take(window):
    """The chat's field gives up the keyboard, and its open menu closes, as a click elsewhere does (at the chat's next
    event, and before its menu's own row returns)."""
    for area, state in _chats(window):
        state["focused"], state["blur_request"] = False, True
        state.pop("popover", None)
        area.tag_redraw()


def chat_has_it(window, menus=False):
    """Whether a chat in ``window`` holds the keyboard: its field shows focus, or (``menus``) its menu is open. A live
    view leaves an open menu out (R8-1: keys over it are its own); the picture viewer counts one, so typing into the
    chat's + menu reaches it (re-review of b5808fd5a2e, N2)."""
    return any(state.get("focused") or (menus and state.get("popover")) for _area, state in _chats(window))


def chat_takes(window, event, menus=False):
    """Whether a chat of ``window`` holds the keyboard and acts on ``event`` with the pointer over a surface: the
    input method's events (they carry no text: re-read of 0690d5c, M1, Chinese, Japanese and Korean typing was held
    over the viewer), every key while the window composes, and the keys the field takes (``takes_off_chat``)."""
    from . import composer_ime, composer_input, input_policy
    if not chat_has_it(window, menus):
        return False
    if event.type in composer_ime.EVENTS or composer_ime.composing(window):
        return True
    return not input_policy.follows_pointer(event.type) and composer_input.takes_off_chat(event)
