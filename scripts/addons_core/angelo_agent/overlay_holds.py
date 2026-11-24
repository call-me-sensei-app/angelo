# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an Angelo overlay drawn over the window keeps from the editors under it (2.1.0, re-reviews of markup
b5808fd5a2e and 0690d5c): the chat's pass-on never reaches a covered editor (``holds_covered``), its Undo never
reaches a scene an overlay hides, and an overlay's handler stays ahead of a chat handler made again (``keep_ahead``).
Overlays register here (``markup_overlay``); the chat's one modal is wrapped (``chat_view``)."""

from .input_policy import bound, not_input

# Editors an Angelo overlay draws over, so no key or drop is handed on to them (re-review of markup b5808fd5a2e, M1):
# name -> ``covers(window, x, y)``, registered by each overlay (``markup_overlay``). The chat's pass-on asks here.
COVERS = {}
COVERS_WINDOW = {}   # name -> ``covering(window)``: whether the overlay hides part of the window's scene (N2).
APP_KEYS = frozenset({'S'})   # Save and Save As (Cmd on macOS, Ctrl elsewhere): handed on by every Angelo surface.


# Overlays whose handler must stay ahead of the chat's, so a key the chat would hold over them reaches them first
# (re-read of 0690d5c, S1): name -> ``come_forward(window)``, called after the chat's handler is made again.
AHEAD = {}


def keep_ahead(window):
    """Each registered overlay over ``window`` adds its handler again, ahead of the one just made."""
    for come_forward in list(AHEAD.values()):
        try:
            come_forward(window)
        except Exception:   # noqa: BLE001 - one overlay's failure never stops the chat's input from starting.
            import traceback
            traceback.print_exc()


def app_shortcut(event):
    """A key every Angelo surface hands on whoever holds the keyboard (``APP_KEYS`` with the platform's command)."""
    import sys
    command = event.oskey if sys.platform == "darwin" else event.ctrl
    return event.type in APP_KEYS and bool(command) and not event.alt


def covered(window, x, y):
    """Whether an Angelo overlay draws over the editor at window pixels (x, y). A check that fails counts as covered:
    a key held for one event costs less than one reaching a hidden scene."""
    for covers in list(COVERS.values()):
        try:
            if covers(window, x, y):
                return True
        except Exception:   # noqa: BLE001 - fail closed.
            return True
    return False


SCENE_UNDO = frozenset({"ed.undo", "ed.redo"})   # The Screen key map's Undo and Redo (the person's own keys).


def scene_undo(event, window_manager):
    """Whether this press is the person's Undo or Redo key (their Screen key map, as ``quit_shortcut`` reads Quit)."""
    return bound(event, window_manager, "Screen", SCENE_UNDO)


def scene_hidden(window):
    """Whether an Angelo overlay hides part of ``window``'s scene now (a check that fails counts as hidden)."""
    for covering in list(COVERS_WINDOW.values()):
        try:
            if covering(window):
                return True
        except Exception:   # noqa: BLE001 - fail closed.
            return True
    return False


def into_covered(window, event, window_manager=None):
    """Whether handing ``event`` on would put a key (press or release) or a drop into a covered editor, or undo a
    scene an overlay hides from a chat holding the keyboard (re-read of 0690d5c, N2, narrowed on 41fd23a: the chat's
    Undo with no draft history fell back to the scene's under the picture viewer; an Undo over a visible editor with
    no chat holding the keyboard is the person's and goes on; Edit > Undo and Undo History, deliberate and seen, go
    on too)."""
    if not_input(event) or app_shortcut(event) or window is None:
        return False
    pointer = "MOUSE" in event.type or event.type.startswith(("TRACKPAD", "NDOF", "ACTIONZONE"))
    if event.type != 'EVT_DROP' and pointer:
        return False   # The pointer's own path (an area's action zones too, N3): the overlay takes it first.
    if scene_hidden(window) and scene_undo(event, window_manager):
        from .keyboard_owner import chat_has_it
        if chat_has_it(window):
            return True
    return covered(window, event.mouse_x, event.mouse_y)


def holds_covered(modal):
    """A chat modal whose pass-on never reaches an editor an Angelo overlay covers: what it would hand on there (a
    key it doesn't use, every key while its composer is hidden, a navigation key, a drop) is held instead. One check
    for every overlay, at the one place the chat hands events on (re-review of markup b5808fd5a2e, M1: Ctrl+J joined
    the meshes under the picture viewer, Ctrl+Numpad 0 set the camera, a dropped file was imported)."""
    import functools

    @functools.wraps(modal)
    def held(self, context, event):
        result = modal(self, context, event)
        if 'PASS_THROUGH' in result and into_covered(getattr(context, "window", None), event,
                                                      getattr(context, "window_manager", None)):
            return (set(result) - {'PASS_THROUGH'}) or {'RUNNING_MODAL'}   # A retired handler still ends (S2).
        return result
    return held
