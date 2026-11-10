# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hover, pressed, keyboard-focus and disabled states for clickables on Angelo's custom-drawn canvases.

The rule: everything clickable must look and feel
clickable. Canvases keep registering clickables as ``(rect, action, value)``
hits; this module draws the states on top in one visual language, taken from
the console's buttons (``.btn--ghost``, ``:focus-visible``):

* hover lays a faint mint-white layer and a brighter hairline over the control,
  and the pointer becomes a hand;
* pressing darkens it from mouse-down until release;
* keyboard focus (Tab, Shift+Tab; Return or Space activates) draws the
  console's 2 px mint ring 3 px outside the control;
* a disabled control is registered as a hit whose action is ``DISABLED`` and
  whose value is the reason, a message id: it gets the not-allowed pointer, no
  layer, no focus, and hovering it shows the reason;
* an ``INERT`` hit takes a press and does nothing (``approval_wait``'s shield
  over a pill just clicked): no layer, no focus, no hand, nothing drawn. Its
  value is when it lapses (``time.monotonic()``); lapsed, it is no hit at all
  (``live``), for hover and press alike, though no redraw removed it yet.
* a control that draws no words of its own (an icon) names itself: ``label(state, action, value, message)``
  registers a message id that its tooltip shows once the pointer has rested on it for ``TIP_DELAY`` (the pointer
  asks for that one redraw), as Blender's own tooltips do;
* an ``INFO`` hit is no control: its value is an explanation (a message id) shown the same way, with the ordinary
  pointer and no focus (an attachment chip saying why its text can't be read).

The layers follow each control's own corners and fill: ``note_shape`` records
the radius and colour of every rounded rectangle the canvas draws, and a hit
drawn with the same rectangle reuses them. A light control (the primary
Approve) darkens on hover and gains the console primary's mint outline, since a
light layer would not show on it. Text links (``style(state, action, value, "link")``)
get no layer; their draw site reads ``state_of`` and brightens itself.

Drawing reads only the canvas state. Input updates it and tags one redraw when
something changed, so hovering never polls. The input side imports no GPU
module, so host tests exercise it without Blender.
"""

import math
import time

from . import canvas_focus, live_windows, ui_tokens as tk
from .canvas_focus import DISABLED, INFO, INERT, focus_order, focused_hit, hover_shown, move_focus  # noqa: F401 (the API)
from .ui_text import iface_, is_rtl, n_

TIP_DELAY = 0.5   # Seconds the pointer rests on a labelled control before its tooltip shows.
# The states' colours are ui_tokens roles read as they draw (ui_tokens.themed): a hovered control gets veil(0.07) and a
# HAIRLINE_HOVER ring, a pressed one PRESSED_LAYER; a control whose fill stands out from the canvas (``is_light``)
# gets INVERSE_HOVER and the FOCUS ring at 0.75; tooltips are TIP_*; the keyboard ring is FOCUS.
HOVER_ALPHA, INVERSE_RING_ALPHA = 0.07, 0.75
SEND_EMPTY = n_("Type a message or attach a file to send")
_radii = {}
_shader = None


def note_shape(x, y, width, height, radius, color=None):
    """Remember a drawn rounded rectangle's corner radius and fill, so its states match it."""
    if len(_radii) > 4096:  # Canvases that never draw states (no chat open) must not grow this.
        _radii.clear()
    _radii[(x, y, width, height)] = (radius, color)


def _tone(color):
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def is_light(rect):
    """Whether the control was drawn with an opaque fill that stands out from the canvas's own tone (a light fill on
    the dark variant's canvas, a dark one on the light variant's): its hover is the inverse layer and a ring, since the
    canvas's veil would not show on it."""
    color = (_radii.get(tuple(rect)) or (0, None))[1]
    return bool(color) and len(color) == 4 and color[3] > 0.5 and abs(_tone(color) - _tone(tk.CANVAS)) > 0.5


def radius_for(rect, style=None):
    if isinstance(style, (int, float)) and not isinstance(style, bool):
        return style
    drawn = _radii.get(tuple(rect))
    if drawn is not None:
        return min(drawn[0], rect[2] / 2, rect[3] / 2)
    w, h = rect[2], rect[3]
    return h / 2 if abs(w - h) <= 4 and h <= 36 else min(12, h / 2)  # Icon buttons are round.


def live(hit):
    """Whether a hit takes the pointer now: an ``INERT`` hit only until its value, the moment it lapses."""
    return hit[1] != INERT or time.monotonic() <= hit[2]


def hit_at(hits, x, y):
    """The control under the pointer: the first live hit containing it, as the click path picks."""
    return next((hit for hit in hits if hit[0][0] <= x <= hit[0][0] + hit[0][2]
                 and hit[0][1] <= y <= hit[0][1] + hit[0][3] and live(hit)), None)


def style(state, action, value, kind):
    state.setdefault("hit_style", {})[(action, value)] = kind


def label(state, action, value, message):
    """Name the control ``(action, value)`` for its tooltip: a message id, translated where drawn. For controls that
    draw no words of their own; the canvas registers it with the hit, each draw. The first label of a draw after
    ``draw_canvas`` replaces the last draw's, so the pointer reads the labels of the hits it moves over."""
    if state.pop("labels_drawn", False):
        state["hit_labels"] = {}
    state.setdefault("hit_labels", {})[(action, value)] = message


def label_of(state, key):
    """The message id a hovered ``key`` explains itself with: an ``INFO`` hit's own value, else its label, or None."""
    if key is None:
        return None
    return key[1] if key[0] == INFO else (state.get("hit_labels") or {}).get(key)


def hover_tip(state, now):
    """The label to show for the hovered control at ``now`` (``time.monotonic``), once it rested ``TIP_DELAY``."""
    message = label_of(state, state.get("hover"))
    since = state.get("hover_since")
    return message if message and since is not None and now - since >= TIP_DELAY else None


def _redraw_later(delay, still):
    """One redraw after ``delay`` seconds, while ``still()`` holds (Blender's timers; nothing without Blender)."""
    try:
        import bpy
    except ImportError:
        return

    def fire():
        try:
            still()
        except ReferenceError:   # Its area went away meanwhile.
            pass
        return None   # Once.
    bpy.app.timers.register(fire, first_interval=delay)


def state_of(state, action, value):
    """``"pressed"``, ``"hover"``, ``"focus"`` or None, for draw sites that style themselves (links)."""
    key = (action, value)
    if state.get("pressed") == key and state.get("hover") == key:
        return "pressed"
    if state.get("hover") == key:
        return "hover"
    return "focus" if state.get("focus") == key else None


def pointer(window, redraw, state, x, y, hits):
    """Track the pointer over a canvas; True while it is over a control.

    The caller then consumes the move: Blender's console region resets its
    text cursor on every unconsumed move, which would replace the hand.
    """
    state["pointer"] = (x, y)
    if state.get("keyboard_pointer") not in (None, (x, y)):
        state.pop("keyboard_pointer")   # The pointer moved: its hover shows again.
    hit = hit_at(hits, x, y)
    hit = None if hit and hit[1] == INERT else hit   # Nothing to show over an inert hit: no hover, no hand.
    key = (hit[1], hit[2]) if hit else None
    if key != state.get("hover"):
        state["hover"] = key
        state["hover_since"] = time.monotonic()
        redraw()
        if label_of(state, key):   # Its tooltip shows once the pointer rests: one redraw then, if still there.
            _redraw_later(TIP_DELAY, lambda: state.get("hover") == key and (redraw() or True))
    if hit:
        state["cursor"] = {DISABLED: 'STOP', INFO: 'DEFAULT'}.get(hit[1], 'HAND_POINT')  # For window fixtures to read.
        window.cursor_set(state["cursor"])
    else:
        state["cursor"] = None
    return hit is not None


def leave(redraw, state):
    """The pointer left the canvas: no hover, and a press that left with it no longer shows."""
    state.pop("pointer", None)
    # The transcript's and the composer's scrollbars show while hovered: they fade once the pointer has left.
    bars = [state.pop(key) for key in ("scroll_hover", "composer_hover") if state.get(key)]
    if state.get("hover") is not None or state.get("pressed") is not None or bars:
        state["hover"] = state["pressed"] = None
        redraw()


def _pending_reveal(state):
    identity = state.get("session")
    if identity:
        from .approval_wait import pending_shield
        return pending_shield(identity)
    return False


def press(redraw, state, hit):
    if _pending_reveal(state):
        state["hits"] = []
        return  # A release can no longer activate this hidden canvas hit.
    state["pressed"] = (hit[1], hit[2])
    state["focus"] = None  # A pointer click ends keyboard focus, as :focus-visible does.
    redraw()


def release(redraw, state):
    if state.pop("pressed", None) is not None:
        redraw()


def _shader_get():
    global _shader
    if _shader is None:
        import gpu
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


def _contour(x, y, w, h, radius):
    radius = max(0.0, min(radius, w / 2, h / 2))
    points = []
    for cx, cy, start in ((x + radius, y + radius, 180), (x + w - radius, y + radius, 270),
                          (x + w - radius, y + h - radius, 0), (x + radius, y + h - radius, 90)):
        for step in range(7):
            angle = math.radians(start + step * 15)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), 0))
    return points


def ring(x, y, w, h, radius, thickness, color):
    """A rounded-rectangle outline of ``thickness`` drawn inward from the given bounds."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    outer = _contour(x, y, w, h, radius)
    inner = _contour(x + thickness, y + thickness, w - 2 * thickness, h - 2 * thickness, radius - thickness)
    count = len(outer)
    vertices = outer + inner
    indices = []
    for i in range(count):
        j = (i + 1) % count
        indices += [(i, j, count + i), (j, count + j, count + i)]
    shader = _shader_get()
    batch = batch_for_shader(shader, 'TRIS', {"pos": vertices}, indices=indices)
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def key(event, state, redraw):
    """Keyboard focus over the chat canvas's controls: ``canvas_focus.key``, the rule every Angelo surface uses.

    None: not a focus key (the text field takes it); False: handled; ``(action, value)``: activate that control.
    Tab and Shift+Tab move focus (past either end back to the text field), the arrows move it while a control has it,
    Return or Space activates on its release (``on_release``: a held key never confirms a dialog the action opened),
    Escape or typing returns to the text field. The canvas mirrors right to left.
    """
    if _pending_reveal(state):
        state.pop("armed", None)
        return False
    return canvas_focus.key(event, state, redraw, mirrored=is_rtl(), on_release=True)


def releases(event, state):
    """The release of Return or Space that activates the control its press armed (``key``'s, on the chat)."""
    return event.value == 'RELEASE' and event.type in canvas_focus.ACTIVATE and state.get("armed") is not None


def draw_canvas(state, region_width):
    """The chat canvas's states, after its hits were rebuilt: hover follows the content under a still pointer."""
    from . import chat_view as view
    hits = state.get("hits", [])
    if state.get("pointer"):
        hit = hit_at(hits, *state["pointer"])
        key = (hit[1], hit[2]) if hit and hit[1] != INERT else None
        if key != state.get("hover"):   # The content moved under a still pointer: its rest starts now.
            state["hover_since"] = time.monotonic()
        state["hover"] = key
    state["hover"] = hover_shown(state)
    draw(view, hits, state.get("hover"), state.get("pressed"), state.get("focus"), state.get("hit_style"),
         region_width=region_width, clear_of=state.get("composer_card"), tip_message=hover_tip(state, time.monotonic()))
    state["labels_drawn"] = True   # The next draw's labels replace these (``label``); the pointer reads them meanwhile.
    _radii.clear()


def draw(view, hits, hover=None, pressed=None, focus=None, styles=None, scale=1.0, layers=True, region_width=None,
         clear_of=None, tip_message=None):
    """Draw the state layers over a canvas already drawn: call it last, before popovers.

    ``layers=False`` keeps only the focus ring and the pressed layer, for
    canvases that already colour their own hover (the welcome screen). ``clear_of``: a card holding text (the
    composer); a tooltip for a control inside it opens outside it (``tip_rect``). ``tip_message``: the hovered
    control's label or explanation, due now (``hover_tip``).
    """
    import gpu
    styles = styles or {}
    drawn = set()
    for rect, action, value in hits:
        key = (action, value)
        if key in drawn or action in {DISABLED, INERT, INFO} or styles.get(key) in {"link", "kit"}:   # kit: ui_kit draws its own
            continue
        radius = radius_for(rect, styles.get(key))
        if key == pressed and key == hover:
            gpu.state.blend_set('ALPHA')
            view.shape(*rect, tk.PRESSED_LAYER, radius)
            ring(*rect, radius, max(1, round(scale)), tk.HAIRLINE_HOVER)
            drawn.add(key)
        elif key == hover and layers and is_light(rect):
            gpu.state.blend_set('ALPHA')
            view.shape(*rect, tk.INVERSE_HOVER, radius)
            gap = 1.5 * scale
            ring(rect[0] - gap, rect[1] - gap, rect[2] + 2 * gap, rect[3] + 2 * gap, radius + gap, max(1, round(scale)),
                 tk.alpha(tk.FOCUS, INVERSE_RING_ALPHA))
            drawn.add(key)
        elif key == hover and layers:
            gpu.state.blend_set('ALPHA')
            view.shape(*rect, tk.veil(HOVER_ALPHA), radius)
            ring(*rect, radius, max(1, round(scale)), tk.HAIRLINE_HOVER)
            drawn.add(key)
    target = next((hit for hit in hits if (hit[1], hit[2]) == focus), None) if focus and styles.get(focus) != "kit" else None
    if target:   # The one ring (canvas_focus) on every surface.
        canvas_focus.ring_around(target[0], radius_for(target[0], styles.get(focus)) if styles.get(focus) != "link"
                                 else 6 * scale, scale)
    disabled = next((hit for hit in hits if hit[1] == DISABLED and (hit[1], hit[2]) == hover), None)
    if disabled:
        tip(view, disabled[0], iface_(disabled[2]), scale, region_width, clear_of=clear_of)
    elif tip_message:
        named = next((hit for hit in hits if (hit[1], hit[2]) == hover), None)
        if named:
            tip(view, named[0], iface_(tip_message), scale, region_width, clear_of=clear_of)


def tip_rect(anchor, width, height, scale=1.0, region_width=None, region_height=None, clear_of=None):
    """Where a ``width`` x ``height`` tooltip for the control ``anchor`` goes: centred above it; near an edge,
    flush with the control's own outer edge (the send button's right edge, not the window's); always at least
    ``8 * scale`` inside the region; below the control when there is no room above. A control inside ``clear_of``
    (a card holding text, such as the composer and its placeholder) gets its tooltip above that card, so the
    bubble never covers the card's text (1.0.5 review: the disabled-send reason sat on the placeholder)."""
    margin = 8 * scale
    x = anchor[0] + anchor[2] / 2 - width / 2
    if region_width:
        if x + width > region_width - margin:
            x = min(anchor[0] + anchor[2] - width, region_width - margin - width)
        elif x < margin:
            x = max(anchor[0], margin)
        x = max(margin, min(region_width - margin - width, x))
    y = anchor[1] + anchor[3] + 6 * scale
    if clear_of and _inside(anchor, clear_of) and y < clear_of[1] + clear_of[3]:
        y = clear_of[1] + clear_of[3] + 6 * scale
    if region_height and y + height > region_height - margin:
        below = anchor[1] - 6 * scale - height
        y = below if below >= margin else max(margin, region_height - margin - height)
    return x, y, width, height


def _inside(rect, outer):
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    return outer[0] <= cx <= outer[0] + outer[2] and outer[1] <= cy <= outer[1] + outer[3]


def tip_wrap_width(scale=1.0, region_width=None):
    """The widest a tooltip's text may run: 260 px, narrower in a narrow region so the bubble fits it."""
    limit = 260 * scale
    if region_width:
        limit = max(80 * scale, min(limit, region_width - 16 * scale - 16 * scale))
    return limit


last_tip = None   # (x, y, w, h) of the last tooltip drawn, in region pixels; window fixtures read it.


def tip(view, rect, message, scale=1.0, region_width=None, region_height=None, clear_of=None):
    """A small explanation bubble above ``rect``, kept inside the region (``tip_rect``). Drawn mirrored about the
    region (``canvas_rtl.about``): inside a surface mirrored about its own rect it still stays in view and reads from
    the reading start; inside the region's own mirrored draw (the chat), or a caller's ``about``, it is the same."""
    if region_width is None or region_height is None:
        try:
            import bpy
            region = bpy.context.region
            region_width = region.width if region_width is None and region else region_width
            region_height = region.height if region_height is None and region else region_height
        except (AttributeError, ImportError):
            pass
    if not region_width:
        return _tip(view, rect, message, scale, region_width, region_height, clear_of)
    from . import canvas_rtl
    with canvas_rtl.about(region_width) as place:
        return _tip(view, place(tuple(rect)), message, scale, region_width, region_height,
                    place(tuple(clear_of)) if clear_of else clear_of)


def _tip(view, rect, message, scale, region_width, region_height, clear_of):
    global last_tip
    import gpu
    size = round(11 * scale)
    pad = 8 * scale
    lines = [value.rstrip() for value, _ in view.wrap(message, tip_wrap_width(scale, region_width), size, view._font)]
    from .ui_kit import measure
    width = max(measure(view, line, size) for line in lines) + 2 * pad  # As drawn.
    height = len(lines) * (size + 4 * scale) + 2 * pad - 4 * scale
    x, y, width, height = last_tip = tip_rect(rect, width, height, scale, region_width, region_height, clear_of)
    gpu.state.blend_set('ALPHA')
    view.shape(x, y, width, height, tk.TIP_FILL, 8 * scale)
    ring(x, y, width, height, 8 * scale, 1, tk.TIP_LINE)
    # A right-to-left language reads each line from the right. A mirrored canvas already places text so
    # (canvas_rtl.upright); an unmirrored one (a 3D-view overlay) aligns it here.
    from . import canvas_rtl, ui_text
    align_right = ui_text.is_rtl() and not canvas_rtl.mirrored()
    for index, line in enumerate(lines):
        lx = x + width - pad - measure(view, line, size) if align_right else x + pad
        view.text(line, lx, y + height - pad - size + 1 - index * (size + 4 * scale), size, tk.TIP_TEXT)


def leave_areas(window, views, is_canvas):
    """The pointer is over none of the window's canvases: clear any hover they still show."""
    for area in live_windows.areas(window):
        state = views.get(area.as_pointer())
        if state and is_canvas(area):
            leave(area.tag_redraw, state)
