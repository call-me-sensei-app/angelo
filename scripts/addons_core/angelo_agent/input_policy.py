# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background canvases never preempt a native modal tool or its mouse grab, and no Angelo modal handler keeps what
is not its input (timers, window events, Blender's hover refresh, the quit key)."""

# The not-connected showcase takes input only over its own 3D view, so the chat beside it stays usable.
CANVAS_INPUTS = frozenset({"angelo.chat_input", "angelo.studio_input", "angelo.connect_hero"})

# The chat keeps keyboard focus for typing, like a text field, so a prompt
# typed with the pointer resting over the 3D view never fires G, X or Tab
# there. View navigation never edits the scene, so these keys always reach the
# editor under the pointer: numpad views, frame all/selected, the view pie and
# NDOF buttons. NUMPAD_ENTER is absent on purpose: it sends the message.
POINTER_KEYS = frozenset({"NUMPAD_" + str(digit) for digit in range(10)} | {
    "NUMPAD_PERIOD", "NUMPAD_PLUS", "NUMPAD_MINUS", "NUMPAD_SLASH", "NUMPAD_ASTERIX", "HOME", "ACCENT_GRAVE"})
# Using the pointer outside the chat moves keyboard focus there, as a left click already did.
FOCUS_GESTURES = frozenset({"MIDDLEMOUSE", "RIGHTMOUSE", "BUTTON4MOUSE", "BUTTON5MOUSE", "BUTTON6MOUSE", "BUTTON7MOUSE",
                            "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
                            "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM"})


# Never the person's input to an overlay (the welcome, a card, the showcase, a sheet, a canvas): timers belong to
# whoever set them and focus leaving the window is the window's. Blender passes these on whatever a handler returns
# (``wm_event_always_pass``); a handler that acted on them would act on something that was never meant for it.
NOT_INPUT = frozenset({"NONE", "WINDOW_DEACTIVATE"})
MODIFIERS = ("shift", "ctrl", "alt", "oskey")


def not_input(event):
    return event.type.startswith("TIMER") or event.type in NOT_INPUT


def refresh_move(event):
    """A pointer move without motion: Blender's own hover refresh (``WM_event_add_mousemove``), not the person.

    A quit waits for exactly this event: ``wm.quit_blender`` (a menu, a script, the system's quit) adds a handler in
    front of the window's modal handlers and one motionless move. A modal handler added after that (a welcome or card
    opening on a timer) sits in front of it, so a handler may use the move for its hover but must pass it on, or the
    quit waits until the pointer leaves. A simulated move (``Window.event_simulate``) has no motion either.
    """
    return event.type == 'MOUSEMOVE' and (event.mouse_x, event.mouse_y) == (
        getattr(event, "mouse_prev_x", None), getattr(event, "mouse_prev_y", None))


def quit_shortcut(event, window_manager):
    """The person's quit key: the Window key map's Quit (Ctrl+Q by default, or the person's own)."""
    keyconfig = getattr(getattr(window_manager, "keyconfigs", None), "user", None)
    keymap = keyconfig.keymaps.get("Window") if keyconfig is not None and event.value == 'PRESS' else None
    return keymap is not None and any(
        item.idname == "wm.quit_blender" and item.active and item.type == event.type and item.value in {'PRESS', 'ANY'}
        and all(getattr(item, name) in (-1, int(getattr(event, name, False))) for name in MODIFIERS)
        for item in keymap.keymap_items)


def overlay_passes(event, window_manager):
    """What every Angelo modal handler passes on wherever the pointer is: what is not input, and the quit key."""
    return not_input(event) or quit_shortcut(event, window_manager)


def follows_pointer(event_type):
    """A navigation key that goes to the editor under the pointer even while the chat has focus."""
    return event_type in POINTER_KEYS or event_type.startswith("NDOF_")


def moves_focus(event_type, value):
    """Pointer use outside the chat (orbit, pan, zoom, context menu) that takes keyboard focus with it."""
    return (event_type in FOCUS_GESTURES and value != "RELEASE") or event_type.startswith("NDOF_")


def operator_identity(operator):
    value = getattr(operator, "bl_idname", "")
    if not value:
        value = getattr(getattr(operator, "bl_rna", None), "identifier", "")
    return value.replace("_OT_", ".", 1).lower()


RENDER_OPERATOR = "render.render"


def _render_ended():
    """Whether no render job runs, so a render operator still on the modal stack is only waiting to leave.

    Blender's render operator stays modal until the first event after its job ended, and an unattended
    window gets none: without this, a finished render (a job, or angelo_view's RENDERED look) would keep
    every later tool waiting for an interaction that is over. Outside Blender the answer is no.
    """
    try:
        import bpy
        return not bpy.app.is_job_running("RENDER")
    except (ImportError, AttributeError, TypeError):
        return False


def active_interactions(window):
    """Window.modal_operators is public RNA; no native memory or UI guessing.

    Chat/Studio inputs are passive window handlers, not foreground operations.
    Every other modal operator gets its events first, including Esc/confirm and
    drag releases. Do not cancel it or change its editor behind the user's back.
    A render operator whose job has ended is no interaction (``_render_ended``).
    """
    names = [operator_identity(op) or "Blender interaction" for op in getattr(window, "modal_operators", ())
             if operator_identity(op) not in CANVAS_INPUTS]
    if RENDER_OPERATOR in names and _render_ended():
        names = [name for name in names if name != RENDER_OPERATOR]
    return names


def interaction_active(window):
    return bool(active_interactions(window))
