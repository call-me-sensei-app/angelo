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
  layer, no focus, and hovering it shows the reason.

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

from .ui_text import iface_, n_

DISABLED = "disabled"
HOVER_LAYER = (0.902, 1.0, 0.957, 0.07)  # rgba(230, 255, 244, .07): the console's ghost button hover.
HOVER_LINE = (0.953, 0.925, 0.886, 0.26)  # --line-3
PRESSED_LAYER = (0.0, 0.0, 0.0, 0.24)
LIGHT_HOVER_LAYER = (0.0, 0.0, 0.0, 0.10)
LIGHT_HOVER_LINE = (0.631, 0.957, 0.839, 0.75)  # .btn--primary:hover ring, rgba(161, 244, 214, .6) and up.
FOCUS_RING = (0.631, 0.957, 0.839, 1)  # --mint-300
LINK = (0.631, 0.957, 0.839, 1)  # .link-button
LINK_HOVER = (0.902, 1.0, 0.937, 1)  # --mint-100
TIP_FILL = (0.055, 0.047, 0.043, 0.97)  # --ink-1
TIP_LINE = (0.953, 0.925, 0.886, 0.14)  # --line-2
TIP_TEXT = (0.953, 0.925, 0.886, 1)  # --text
SEND_EMPTY = n_("Type a message or attach a file to send")
_radii = {}
_shader = None


def note_shape(x, y, width, height, radius, color=None):
    """Remember a drawn rounded rectangle's corner radius and fill, so its states match it."""
    if len(_radii) > 4096:  # Canvases that never draw states (no chat open) must not grow this.
        _radii.clear()
    _radii[(x, y, width, height)] = (radius, color)


def is_light(rect):
    """Whether the control was drawn with a light, opaque fill."""
    color = (_radii.get(tuple(rect)) or (0, None))[1]
    return bool(color) and len(color) == 4 and color[3] > 0.5 and (
        0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) > 0.6


def radius_for(rect, style=None):
    if isinstance(style, (int, float)) and not isinstance(style, bool):
        return style
    drawn = _radii.get(tuple(rect))
    if drawn is not None:
        return min(drawn[0], rect[2] / 2, rect[3] / 2)
    w, h = rect[2], rect[3]
    return h / 2 if abs(w - h) <= 4 and h <= 36 else min(12, h / 2)  # Icon buttons are round.


def hit_at(hits, x, y):
    """The control under the pointer: the first hit containing it, as the click path picks."""
    return next((hit for hit in hits if hit[0][0] <= x <= hit[0][0] + hit[0][2]
                 and hit[0][1] <= y <= hit[0][1] + hit[0][3]), None)


def style(state, action, value, kind):
    state.setdefault("hit_style", {})[(action, value)] = kind


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
    key = (hit[1], hit[2]) if hit else None
    if key != state.get("hover"):
        state["hover"] = key
        redraw()
    if hit:
        state["cursor"] = 'STOP' if hit[1] == DISABLED else 'HAND_POINT'  # Kept for window fixtures to read.
        window.cursor_set(state["cursor"])
    else:
        state["cursor"] = None
    return hit is not None


def leave(redraw, state):
    """The pointer left the canvas: no hover, and a press that left with it no longer shows."""
    state.pop("pointer", None)
    if state.get("hover") is not None or state.get("pressed") is not None:
        state["hover"] = state["pressed"] = None
        redraw()


def press(redraw, state, hit):
    state["pressed"] = (hit[1], hit[2])
    state["focus"] = None  # A pointer click ends keyboard focus, as :focus-visible does.
    redraw()


def release(redraw, state):
    if state.pop("pressed", None) is not None:
        redraw()


def focus_order(hits):
    """Clickables in reading order: top to bottom, then left to right; disabled ones are skipped."""
    return [(action, value) for rect, action, value in sorted(
        (hit for hit in hits if hit[1] != DISABLED), key=lambda hit: (-(hit[0][1] + hit[0][3]), hit[0][0]))]


def move_focus(state, backwards=False):
    """Tab to the next control (Shift+Tab: previous); past either end focus returns to the text field."""
    order = list(dict.fromkeys(focus_order(state.get("hits", []))))
    current = state.get("focus")
    if not order:
        state["focus"] = None
        return
    if current in order:
        index = order.index(current) + (-1 if backwards else 1)
    else:
        index = len(order) - 1 if backwards else 0
    state["focus"] = order[index] if 0 <= index < len(order) else None
    # One focus indicator at a time: while the keyboard moves focus, a pointer resting on another control shows no
    # hover until it moves (``hover_shown``).
    state["keyboard_pointer"] = state.get("pointer") if state["focus"] is not None else None


def hover_shown(state):
    """The hover to draw: none while keyboard focus is shown and the pointer has not moved since it was set."""
    if state.get("focus") is not None and state.get("keyboard_pointer") is not None \
            and state.get("keyboard_pointer") == state.get("pointer"):
        return None
    return state.get("hover")


def focused_hit(state):
    key = state.get("focus")
    return next((hit for hit in state.get("hits", []) if (hit[1], hit[2]) == key), None) if key else None


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
    """Keyboard focus over a canvas's controls.

    None: not a focus key (the text field takes it); False: handled; ``(action, value)``: activate that control.
    Tab and Shift+Tab move focus, Return or Space activates, Escape or typing returns to the text field.
    """
    if event.value != 'PRESS':
        return None
    if event.type == 'TAB' and not (event.ctrl or event.oskey or event.alt):
        move_focus(state, event.shift)
        redraw()
        return False
    if state.get("focus") is None:
        return None
    hit = focused_hit(state)
    if event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'} and hit:
        return hit[1], hit[2]
    state["focus"] = None
    redraw()
    return False if event.type == 'ESC' else None


def draw_canvas(state, region_width):
    """The chat canvas's states, after its hits were rebuilt: hover follows the content under a still pointer."""
    from . import chat_view as view
    hits = state.get("hits", [])
    if state.get("pointer"):
        hit = hit_at(hits, *state["pointer"])
        state["hover"] = (hit[1], hit[2]) if hit else None
    state["hover"] = hover_shown(state)
    draw(view, hits, state.get("hover"), state.get("pressed"), state.get("focus"), state.get("hit_style"),
         region_width=region_width, clear_of=state.get("composer_card"))
    _radii.clear()


def draw(view, hits, hover=None, pressed=None, focus=None, styles=None, scale=1.0, layers=True, region_width=None,
         clear_of=None):
    """Draw the state layers over a canvas already drawn: call it last, before popovers.

    ``layers=False`` keeps only the focus ring and the pressed layer, for
    canvases that already colour their own hover (the welcome screen). ``clear_of``: a card holding text (the
    composer); a tooltip for a control inside it opens outside it (``tip_rect``).
    """
    import gpu
    styles = styles or {}
    drawn = set()
    for rect, action, value in hits:
        key = (action, value)
        if key in drawn or action == DISABLED or styles.get(key) in {"link", "kit"}:   # kit: ui_kit draws its own
            continue
        radius = radius_for(rect, styles.get(key))
        if key == pressed and key == hover:
            gpu.state.blend_set('ALPHA')
            view.shape(*rect, PRESSED_LAYER, radius)
            ring(*rect, radius, max(1, round(scale)), HOVER_LINE)
            drawn.add(key)
        elif key == hover and layers and is_light(rect):
            gpu.state.blend_set('ALPHA')
            view.shape(*rect, LIGHT_HOVER_LAYER, radius)
            gap = 1.5 * scale
            ring(rect[0] - gap, rect[1] - gap, rect[2] + 2 * gap, rect[3] + 2 * gap, radius + gap, max(1, round(scale)),
                 LIGHT_HOVER_LINE)
            drawn.add(key)
        elif key == hover and layers:
            gpu.state.blend_set('ALPHA')
            view.shape(*rect, HOVER_LAYER, radius)
            ring(*rect, radius, max(1, round(scale)), HOVER_LINE)
            drawn.add(key)
    target = next((hit for hit in hits if (hit[1], hit[2]) == focus), None) if focus and styles.get(focus) != "kit" else None
    if target:
        rect, gap, width = target[0], 3 * scale, 2 * scale
        radius = radius_for(rect, styles.get(focus)) if styles.get(focus) != "link" else 6 * scale
        ring(rect[0] - gap, rect[1] - gap, rect[2] + 2 * gap, rect[3] + 2 * gap, radius + gap, width, FOCUS_RING)
    disabled = next((hit for hit in hits if hit[1] == DISABLED and (hit[1], hit[2]) == hover), None)
    if disabled:
        tip(view, disabled[0], iface_(disabled[2]), scale, region_width, clear_of=clear_of)


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
    """A small explanation bubble above ``rect``, kept inside the region (``tip_rect``)."""
    global last_tip
    import gpu
    if region_height is None:
        try:
            import bpy
            region_height = bpy.context.region.height if bpy.context.region else None
        except (AttributeError, ImportError):
            region_height = None
    size = round(11 * scale)
    pad = 8 * scale
    lines = [value.rstrip() for value, _ in view.wrap(message, tip_wrap_width(scale, region_width), size, view._font)]
    from .ui_kit import measure
    width = max(measure(view, line, size) for line in lines) + 2 * pad  # As drawn.
    height = len(lines) * (size + 4 * scale) + 2 * pad - 4 * scale
    x, y, width, height = last_tip = tip_rect(rect, width, height, scale, region_width, region_height, clear_of)
    gpu.state.blend_set('ALPHA')
    view.shape(x, y, width, height, TIP_FILL, 8 * scale)
    ring(x, y, width, height, 8 * scale, 1, TIP_LINE)
    for index, line in enumerate(lines):
        view.text(line, x + pad, y + height - pad - size + 1 - index * (size + 4 * scale), size, TIP_TEXT)


def leave_areas(window, views, is_canvas):
    """The pointer is over none of the window's canvases: clear any hover they still show."""
    for area in window.screen.areas:
        state = views.get(area.as_pointer())
        if state and is_canvas(area):
            leave(area.tag_redraw, state)
