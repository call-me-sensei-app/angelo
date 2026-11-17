# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer and an input method (IME: Japanese, Korean, Chinese and the like).

API:
    handle(event, window, state, line, session=None, controller=None, accepts=True) -> bool
        From the chat's input handler (``chat_view``), before the composer's keys. True when the event was the input
        method's: a composition event (committed text goes through ``composer_input.insert``), or a key the input
        method is using while it composes (Backspace, arrows, Return edit the composition, never the draft).
    preview(state, line) -> Preview | None    the draft as drawn while composing (``chat_view`` lays it out)
    caret(state, line) -> int                 the caret the composer draws: inside the composition while composing
    draw_marks(view, state, size, line_height)  the composition's underlines, over the draft's rows
    place(state, x, y, height, accepts)       the candidate window at the caret (``Window.ime_attach``), or released
    release(window_pointer)                   the chat let go of the keyboard: the input method goes with it

Blender 5.3 made the Console an input method client (``console_main_region_cursor_ime``, ``console.insert``). The
chat composer is drawn over a Console area by Python, so the stock path placed the candidate window at the Console's
own hidden line, drew no composition and inserted the commit past the composer: no selection replaced, no Undo, no
redraw until the next event (2.0.0 check, then numbered 1.1.0; the B2 report of 2026-11-11). Here the composer owns
the session itself (``wmIMEOwnerType::Script``, ``WM_window_IME_script_attach``): it keeps the keyboard wherever the
pointer is, as the composer does for typing.

A composition replaces the selection when it is committed, so the preview shows it in the selection's place. Offsets
from the window (``ime_composition_cursor``, ``ime_composition_selection``) are characters; -1 is "not reported".
"""

from collections import namedtuple

from . import composer_input
from .chat_composer import column_x, cursor_position

EVENTS = frozenset({"IME_COMPOSITE_START", "IME_COMPOSITE_EVENT", "IME_COMPOSITE_END"})
# Not keys: the pointer, the wheel and the trackpad still act while composing (an input method ignores them).
_POINTER_PREFIXES = ("MOUSE", "WHEEL", "TRACKPAD", "NDOF", "TIMER", "EVT_", "WINDOW_")
_ATTACHED = {}   # Window pointer -> the area pointer whose composer attached the window's input method.

# ``value``: the draft as drawn; ``caret``: inside the composition; ``span``: the composition (start, end);
# ``segment``: the part being converted (start, end) or None. All in characters of ``value``.
Preview = namedtuple("Preview", "value caret span segment")


def _keyboard(event):
    kind = event.type or ""
    if kind in EVENTS or kind.endswith("MOUSE") or kind.startswith(_POINTER_PREFIXES) or kind == "NONE":
        return False
    return event.value in {"PRESS", "RELEASE"}


def composing(window):
    return bool(getattr(window, "is_ime_composing", False))


def _track(state, window, kind):
    """What the composer shows of the composition after this event (``state["composer_ime"]``)."""
    text = "" if kind == "IME_COMPOSITE_END" or not composing(window) else (window.ime_composition or "")
    if not text:
        state.pop("composer_ime", None)
        return
    cursor = window.ime_composition_cursor
    segment = tuple(window.ime_composition_selection)
    state["composer_ime"] = (text, cursor, segment)


def handle(event, window, state, line, session=None, controller=None, accepts=True):
    if event.type in EVENTS:
        result = window.ime_result if event.type == "IME_COMPOSITE_EVENT" else ""
        if result and accepts:
            # The composer's own insert: over the selection, one Undo step with the typing around it.
            composer_input.insert(state, line, result, "type", session=session, controller=controller)
            state["composer_goal"] = None
        if accepts:
            state["ime_target"] = True   # Its next draw keeps the session here (``place``), even if not focused.
            _track(state, window, event.type)
        return True   # Never the Console's: ``console.insert`` would type past the composer.
    return composing(window) and _keyboard(event)


def preview(state, line):
    ime = state.get("composer_ime")
    if not ime or line is None:
        state.pop("composer_ime_shown", None)
        return None
    text, cursor, segment = ime
    draft = composer_input._draft(state, line)
    start, end = draft.selection if draft.selection else (draft.caret, draft.caret)
    value = draft.value[:start] + text + draft.value[end:]
    inside = cursor if 0 <= cursor <= len(text) else len(text)
    shown_segment = None
    if 0 <= segment[0] < segment[1] <= len(text):
        shown_segment = (start + segment[0], start + segment[1])
    shown = Preview(value, start + inside, (start, start + len(text)), shown_segment)
    state["composer_ime_shown"] = shown   # The draw's caret and underlines (``caret``, ``draw_marks``).
    return shown


def caret(state, line):
    shown = state.get("composer_ime_shown")
    return shown.caret if shown else cursor_position(line)


def draw_marks(view, state, size, line_height):
    """A thin underline under the composition and a thick one under the part being converted, as text fields do."""
    shown = state.get("composer_ime_shown")
    if not shown:
        return
    from . import ui_tokens
    for value, offset, y in state.get("draft_rows", ()):
        for span, thickness in ((shown.span, 1), (shown.segment, 2)):
            if span is None or span[1] <= span[0]:
                continue   # No segment reported (macOS and Wayland report none), or an empty composition.
            begin, end = span
            left, right = max(begin, offset), min(end, offset + len(value))
            if right <= left:
                continue
            x = column_x(value, left - offset, size)
            view.shape(x, y - 4, column_x(value, right - offset, size) - x, thickness, ui_tokens.TEXT)


def place(state, x, y, height, accepts):
    """Called by the composer's draw with its caret (layout units, the caret's bottom). The composer that has the
    keyboard, or that a composition went to, holds the window's input method at its caret; the others leave it."""
    import bpy
    context = bpy.context
    window, region, area = (getattr(context, name, None) for name in ("window", "region", "area"))
    if window is None or region is None or area is None or not hasattr(window, "ime_attach"):
        return   # Drawn outside a window (a GUI check's off-screen draw), or a Blender without the session API.
    wants = accepts and (state.get("focused", True) or state.get("ime_target"))
    pointer, here = window.as_pointer(), area.as_pointer()
    if not composing(window):
        state.pop("ime_target", None)
    if wants:
        scale = state.get("text_scale") or 1.0   # The draw's one scale: layout units to region pixels.
        attached = window.ime_attach(int(region.x + x * scale), int(region.y + y * scale), max(1, int(height * scale)))
        if attached:
            _ATTACHED[pointer] = here
        else:
            _ATTACHED.pop(pointer, None)
    elif _ATTACHED.get(pointer) == here:
        _ATTACHED.pop(pointer, None)
        window.ime_detach()


def release(window_pointer):
    """The chat let go of the keyboard in the window ``window_pointer``: the session its composer held ends."""
    if _ATTACHED.pop(window_pointer, None) is None:
        return
    import bpy
    from . import live_windows
    # A window between screens is still found: ending the session never touches its screen.
    window = live_windows.find(getattr(bpy.context, "window_manager", None), window_pointer, live=False)
    if window is not None:
        window.ime_detach()


def clear():
    """The add-on unregisters: no session it began outlives it."""
    for window_pointer in list(_ATTACHED):
        release(window_pointer)
