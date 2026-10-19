# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's shared UI components: one drawing of each control, in the card_kit visual language.

Every component is a stateless draw function: a ``view`` (``chat_view`` itself, or ``card_host``'s ``_View``:
anything with ``shape``, ``text``, ``stroke``, ``_font`` and ``_mono``), a rectangle in region pixels
(``(x, y, w, h)``, y up, as canvas hits are), the UI ``scale`` and a ``State``. Hit testing stays with the
caller: register ``(rect, action, value)`` hits as canvases already do, or use the Builder methods
(``card_kit.Builder``), which register them and lay the component out in a card. Colours, sizes and radii come
from ``ui_tokens``. Text goes through ``view.text``, so it is shaped and placed upright in a right-to-left
interface; code stays left to right.

API (one line each; ``ui`` is a ``card_kit.UiState``, a ``canvas_controls`` state dict or a ``Ui`` tuple):

    state_for(ui, action, value=None, enabled=True) -> State      hover/pressed/focus/enabled of one control
    State(hover=False, pressed=False, focus=False, enabled=True, reason=None);  REST = State()
    focus_ring(view, rect, radius, scale)                         the 2 px teal ring 3 px outside a control
    check(view, cx, cy, unit, color, thickness)                   a check mark that is never mirrored
    with clip(x, y, w, h):                                        scissor within the caller's, restored after
    outline(rect, radius, thickness, color)                       a rounded hairline (canvas_controls.ring)
    measure(view, text, size, mono=False) -> float                width in pixels of drawn text
    text(view, value, x, y, size, color, font=None, shaped=True)  view.text that keeps alpha blending on
    blended(view) / @blending                                     every public draw function blends its own
                                                                  shapes, whatever the caller drew before
    draw_op(view, op, scale)                                      card_draw's dispatcher for ("kit", ...) ops
    register_hits(state, hits)        chat canvas: add a component's hits to state["hits"]; canvas_controls
                                      then tracks hover/press/Tab focus for them but draws no layer or ring
                                      of its own (the component does, from state_for(state, action, value))

    ui_kit_status:  chip / notice / code block       ui_kit_controls: toggle / segmented control
    ui_kit_field:   text field (plain, secret)       ui_kit_select:   dropdown
    ui_kit_rows:    list row / provider tile / auto-hiding scrollbar
    card_kit.Builder methods: chip, notice, code_block, toggle, segmented, text_field, select, list_row,
    provider_tile (ui_kit_builder.KitBuilder); destructive buttons are ``style="destructive"``.

Strings reaching these functions are already translated (``ui_text.iface_``), except the kit's own defaults
(chip labels, "Copy", "Details"), which it translates at display time.
"""

from collections import namedtuple
from contextlib import contextmanager

from . import ui_tokens as tk

State = namedtuple("State", ["hover", "pressed", "focus", "enabled", "reason"], defaults=(False, False, False, True, None))
REST = State()
Ui = namedtuple("Ui", ["hover", "pressed", "focus"], defaults=(None, None, None))
_registry = {}


class _Blended:
    """A view whose fills and strokes always blend: blf turns blending off after every text draw, so a
    translucent shape drawn after text would otherwise come out opaque."""

    __slots__ = ("_view",)

    def __init__(self, view):
        self._view = view

    def __getattr__(self, name):
        return getattr(self._view, name)

    def shape(self, *args, **kwargs):
        import gpu
        gpu.state.blend_set('ALPHA')
        self._view.shape(*args, **kwargs)

    def stroke(self, *args, **kwargs):
        import gpu
        gpu.state.blend_set('ALPHA')
        self._view.stroke(*args, **kwargs)


def blended(view):
    return view if isinstance(view, _Blended) else _Blended(view)


def blending(function):
    """Decorator for public draw functions: their ``view`` (first argument) always blends its shapes."""
    import functools

    @functools.wraps(function)
    def wrapper(view, *args, **kwargs):
        return function(blended(view), *args, **kwargs)
    return wrapper


def register_component(name, function):
    """``function(view, rect, scale, ui, **params)``: what a ``("kit", name, rect, params, ui)`` op draws."""
    _registry[name] = function


def state_for(ui, action, value=None, enabled=True, reason=None):
    """The ``State`` of the control ``(action, value)`` under ``ui`` (pressed only while the pointer stays on it)."""
    if ui is None:
        return State(enabled=enabled, reason=reason)
    if isinstance(ui, dict):
        hover, pressed, focus = ui.get("hover"), ui.get("pressed"), ui.get("focus")
    else:
        hover, pressed, focus = ui.hover, ui.pressed, ui.focus
    key = (action, value)
    return State(enabled and hover == key, enabled and pressed == key and hover == key, enabled and focus == key,
                 enabled, reason)


def register_hits(state, hits):
    from . import canvas_controls
    for rect, action, value in hits:
        state.setdefault("hits", []).append((rect, action, value))
        canvas_controls.style(state, action, value, "kit")


def snapshot(ui):
    """A ``Ui`` copy of a card's state, stored in a plan op so drawing never reads a later state."""
    if ui is None:
        return Ui()
    if isinstance(ui, dict):
        return Ui(ui.get("hover"), ui.get("pressed"), ui.get("focus"))
    return Ui(ui.hover, ui.pressed, ui.focus)


def measure(view, text, size, mono=False):
    font = view._mono if mono else view._font
    if hasattr(view, "measure"):   # card_host's view and host-test fakes measure; chat_view draws with blf.
        return view.measure(text, size, font)
    import blf
    blf.size(font, size)
    return blf.dimensions(font, text)[0]


def outline(rect, radius, thickness, color):
    from . import canvas_controls
    canvas_controls.ring(rect[0], rect[1], rect[2], rect[3], radius, thickness, color)


def focus_ring(view, rect, radius, scale):
    gap = 3 * scale
    outline((rect[0] - gap, rect[1] - gap, rect[2] + 2 * gap, rect[3] + 2 * gap), radius + gap,
            max(1.0, tk.FOCUS_RING * scale), tk.FOCUS)


def check(view, cx, cy, unit, color, thickness):
    """A check mark centred on ``(cx, cy)``, ``unit`` pixels per step. It reads the same in a mirrored
    (right-to-left) draw: a check mark is a symbol, not a direction, so its strokes are pre-mirrored there."""
    from . import canvas_rtl
    flip = -1 if canvas_rtl.mirrored() else 1
    points = ((-4.0, 0.0), (-1.2, -3.0), (4.2, 3.4))
    (x1, y1), (x2, y2), (x3, y3) = ((cx + flip * px * unit, cy + py * unit) for px, py in points)
    view.stroke(x1, y1, x2, y2, color, thickness)
    view.stroke(x2, y2, x3, y3, color, thickness)


@contextmanager
def clip(x, y, w, h):
    """Scissor to ``(x, y, w, h)`` (region pixels, as already placed) inside whatever the caller clips to, then
    put the caller's scissor rectangle back: a component inside a clipped scroll box never unclips its host."""
    import gpu
    previous = tuple(gpu.state.scissor_get())
    px, py, pw, ph = previous
    if pw > 0 and ph > 0:
        x0, y0 = max(x, px), max(y, py)
        w, h = min(x + w, px + pw) - x0, min(y + h, py + ph) - y0
        x, y = x0, y0
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(x), int(y), max(1, int(w)), max(1, int(h)))
    try:
        yield
    finally:
        gpu.state.scissor_set(*previous)


def dim(color, factor=0.42):
    return (color[0], color[1], color[2], color[3] * factor)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def text(view, value, x, y, size, color, font=None, shaped=True):
    """``view.text`` that leaves alpha blending on (blf turns it off, and the next translucent fill would be opaque)."""
    import gpu
    view.text(value, x, y, size, color, view._font if font is None else font, shaped)
    gpu.state.blend_set('ALPHA')


def text_middle(view, label, x, rect_y, rect_h, size, color, mono=False, shaped=True):
    """Text vertically centred in a band (blf's baseline sits ``0.36 * size`` below the centre line)."""
    text(view, label, x, rect_y + rect_h / 2 - 0.36 * size, size, color, view._mono if mono else view._font, shaped)


def draw_op(view, op, scale):
    """Draw a ``("kit", name, rect, params, ui)`` op from a card plan."""
    _, name, rect, params, ui = op[:5]
    if name not in _registry:
        _ensure_loaded()
    import gpu
    gpu.state.blend_set('ALPHA')
    _registry[name](view, rect, scale, ui, **params)


def _ensure_loaded():
    """Import every kit module (each registers its components when imported)."""
    from . import ui_kit_controls, ui_kit_field, ui_kit_rows, ui_kit_select, ui_kit_status  # noqa: F401
