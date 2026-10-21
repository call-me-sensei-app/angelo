# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws a ``card_kit.Plan`` with the chat canvas's shape and text primitives.

GPU only, inside a 3D view's POST_PIXEL callback: no file, no network and no
Blender data. Every control has the same four looks: normal, hover (lighter),
pressed (darker, label one pixel down) and focus (a ring, shown only when the
keyboard moved the focus). A disabled control is dimmed and never highlighted.
``view`` is ``chat_view`` (``shape``, ``text``, ``stroke``, ``_font``, ``_mono``).
"""

import math
from contextlib import ExitStack

import blf
import gpu

from . import canvas_rtl, card_kit as kit, ui_tokens as tk

BUTTON_FILL = {
    "primary": (kit.TEAL, kit.TEAL_HOVER, kit.TEAL_PRESSED),
    "secondary": (kit.LINE, kit.LINE_HOVER, kit.LINE_PRESSED),
    "ghost": ((0, 0, 0, 0), (1, 1, 1, 0.08), (1, 1, 1, 0.14)),
    "destructive": (tk.CORAL, tk.CORAL_HOVER, tk.CORAL_PRESSED),
}
BUTTON_TEXT = {"primary": kit.INK, "secondary": kit.TEXT, "ghost": kit.MUTED, "destructive": kit.INK}


def draw(view, plan):
    gpu.state.blend_set('ALPHA')
    with ExitStack() as box:   # Closed at each ``unclip``, and on the way out if a draw fails.
        for index, op in enumerate(plan.ops):
            _op(view, plan.ops, index, op, box)
    gpu.state.scissor_test_set(False)


def _op(view, ops, index, op, box):
    kind = op[0]
    if kind == "clip" and canvas_rtl.mirrored() and not _rtl_until_unclip(ops, index):
        # Left-to-right content in a right-to-left interface (the English crash report, release notes) keeps
        # its reading layout inside its box. Boxes are centred in their card, so the box itself is unmoved.
        box.enter_context(canvas_rtl.unmirrored())
    if kind != "text" and kind != "mono":
        gpu.state.blend_set('ALPHA')  # Text drawing resets blending.
    if kind == "scrim":
        view.shape(0, 0, op[1][2], op[1][3], kit.SCRIM)
    elif kind == "shape":
        view.shape(*op[1], op[2], op[3])
    elif kind in {"text", "mono"}:
        view.text(op[1], op[2], op[3], op[4], op[5], view._mono if kind == "mono" else view._font)
    elif kind == "button":
        _button(view, *op[1:])
    elif kind == "checkbox":
        _checkbox(view, *op[1:])
    elif kind == "disclosure":
        _disclosure(view, *op[1:])
    elif kind == "icon":
        _icon(view, op[1], op[2])
    elif kind == "closebtn":
        _close_button(view, *op[1:])
    elif kind == "spinner":
        _spinner(view, *op[1:])
    elif kind == "kit":
        from . import ui_kit
        ui_kit.draw_op(view, op, op[5] if len(op) > 5 else 1.0)
    elif kind == "paint":   # A card's own picture (the About card's brand banner): ``op[1](rect)``, GPU only.
        op[1](op[2])
    elif kind == "clip":
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(*canvas_rtl.scissor(*op[1]))
    elif kind == "unclip":
        gpu.state.scissor_test_set(False)
        box.close()


def _rtl_until_unclip(ops, start):
    """True when right-to-left text is drawn between the ``clip`` at ``start`` and its ``unclip``."""
    for op in ops[start + 1:]:
        if op[0] == "unclip":
            return False
        if op[0] in {"text", "mono"} and canvas_rtl.has_rtl(op[1]):
            return True
    return False


def _ring(view, rect, radius, scale):
    x, y, w, h = rect
    d = 3 * scale
    view.shape(x - d, y - d, w + 2 * d, h + 2 * d, kit.FOCUS, radius + d)
    view.shape(x - d + 2 * scale, y - d + 2 * scale, w + 2 * d - 4 * scale, h + 2 * d - 4 * scale, kit.CARD, radius + d - 2 * scale)


def _button(view, rect, label, style, state, size):
    hover, pressed, focus, enabled = state
    x, y, w, h = rect
    scale = size / 13.5
    radius = h / 2
    if focus:
        _ring(view, rect, radius, max(0.6, scale))
    normal, over, down = BUTTON_FILL[style]
    fill = down if pressed else over if hover else normal
    if not enabled:
        fill = (fill[0], fill[1], fill[2], fill[3] * 0.35)
    if fill[3]:
        view.shape(x, y, w, h, fill, radius)
    color = BUTTON_TEXT[style]
    if style == "ghost" and (hover or pressed or focus):
        color = kit.TEXT
    if not enabled:   # Light text at reduced strength on every style: the dark primary label vanished on dim teal.
        color = (kit.TEXT[0], kit.TEXT[1], kit.TEXT[2], 0.42)
    blf.size(view._font, size)
    tw = blf.dimensions(view._font, label)[0]
    view.text(label, x + (w - tw) / 2, y + (h - size * 0.72) / 2 - (1 if pressed else 0), size, color)


def _checkbox(view, rect, checked, state):
    hover, pressed, focus, enabled = state
    x, y, w, h = rect
    s = w / 20
    if focus:
        _ring(view, rect, 6 * s, s)
    if checked:
        view.shape(x, y, w, h, kit.TEAL_PRESSED if pressed else kit.TEAL_HOVER if hover else kit.TEAL, 6 * s)
        view.stroke(x + 5 * s, y + 10 * s, x + 8.5 * s, y + 6 * s, kit.INK, 2.2 * s)
        view.stroke(x + 8.5 * s, y + 6 * s, x + 15 * s, y + 14.5 * s, kit.INK, 2.2 * s)
    else:
        edge = kit.MUTED if hover else kit.LINE_HOVER
        view.shape(x, y, w, h, edge, 6 * s)
        view.shape(x + 1.5 * s, y + 1.5 * s, w - 3 * s, h - 3 * s, kit.LINE_PRESSED if pressed else kit.CARD, 5 * s)


def _disclosure(view, rect, label, expanded, state, size):
    hover, pressed, focus, enabled = state
    x, y, w, h = rect
    s = size / 13.5
    if focus:
        _ring(view, (x - 6 * s, y, w + 12 * s, h), h / 2, s)
    color = kit.TEXT if hover or pressed or focus else kit.MUTED
    cx, cy = x + 7 * s, y + h / 2
    if expanded:
        points = ((cx - 4.5 * s, cy + 2.5 * s), (cx, cy - 2.5 * s), (cx + 4.5 * s, cy + 2.5 * s))
    else:
        points = ((cx - 2.5 * s, cy + 4.5 * s), (cx + 2.5 * s, cy), (cx - 2.5 * s, cy - 4.5 * s))
    view.stroke(*points[0], *points[1], color, 1.8 * s)
    view.stroke(*points[1], *points[2], color, 1.8 * s)
    view.text(label, x + 22 * s, y + (h - size * 0.72) / 2, size, color)
    if hover:
        view.shape(x + 22 * s, y + (h - size * 0.72) / 2 - 3 * s, w - 22 * s, 1 * s, color, 0)


def _icon(view, name, rect):
    x, y, w, h = rect
    s = w / 40
    cx, cy, r = x + w / 2, y + h / 2, w / 2
    if name == "update":
        view.shape(x, y, w, h, kit.TEAL_SOFT, r)
        view.stroke(cx, cy - 9 * s, cx, cy + 9 * s, kit.TEAL, 3 * s)
        view.stroke(cx - 8 * s, cy + 1 * s, cx, cy + 9.5 * s, kit.TEAL, 3 * s)
        view.stroke(cx + 8 * s, cy + 1 * s, cx, cy + 9.5 * s, kit.TEAL, 3 * s)
    elif name == "alert":
        view.shape(x, y, w, h, kit.AMBER_SOFT, r)
        view.shape(cx - 2.2 * s, cy - 2.5 * s, 4.4 * s, 14 * s, kit.AMBER, 2.2 * s)   # The bar, rounded.
        view.shape(cx - 2.6 * s, cy - 11 * s, 5.2 * s, 5.2 * s, kit.AMBER, 2.6 * s)   # The dot.
    elif name == "spark":
        view.shape(x, y, w, h, kit.TEAL_SOFT, r)
        for (dx1, dy1, dx2, dy2, thick) in ((0, -11, 0, 11, 3.2), (-11, 0, 11, 0, 3.2), (-6, -6, 6, 6, 2.0), (-6, 6, 6, -6, 2.0)):
            view.stroke(cx + dx1 * s, cy + dy1 * s, cx + dx2 * s, cy + dy2 * s, kit.TEAL, thick * s)
    elif name == "chat":   # The top bar toggle's speech bubble (chat_toggle.bubble), in the kit's teal.
        view.shape(x, y, w, h, kit.TEAL_SOFT, r)
        view.shape(cx - 10 * s, cy - 4 * s, 20 * s, 14 * s, kit.TEAL, 4.5 * s)
        view.stroke(cx - 6 * s, cy - 3 * s, cx - 8 * s, cy - 9 * s, kit.TEAL, 2.6 * s)
        view.stroke(cx - 8 * s, cy - 9 * s, cx - 1 * s, cy - 3 * s, kit.TEAL, 2.6 * s)
        for dx in (-5, 0, 5):
            view.shape(cx + (dx - 1.6) * s, cy + 1.4 * s, 3.2 * s, 3.2 * s, kit.CARD, 1.6 * s)
    elif name == "check":
        view.shape(x, y, w, h, kit.TEAL_SOFT, r)
        view.stroke(cx - 9 * s, cy, cx - 3 * s, cy - 7 * s, kit.TEAL, 3.2 * s)
        view.stroke(cx - 3 * s, cy - 7 * s, cx + 9.5 * s, cy + 8 * s, kit.TEAL, 3.2 * s)


def _close_button(view, rect, state):
    hover, pressed, focus, enabled = state
    x, y, w, h = rect
    s = w / 28
    if focus:
        _ring(view, rect, w / 2, s)
    view.shape(x, y, w, h, (1, 1, 1, 0.22 if pressed else 0.14 if hover else 0.0), w / 2)
    m, color = w * 0.33, kit.TEXT if hover or pressed or focus else kit.MUTED
    view.stroke(x + m, y + m, x + w - m, y + h - m, color, 1.7 * s)
    view.stroke(x + m, y + h - m, x + w - m, y + m, color, 1.7 * s)


def _spinner(view, cx, cy, radius, now):
    for index in range(10):
        angle = now * 6.0 - index * 0.5
        alpha = 1.0 - index / 10
        px, py = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        r = radius * (0.22 - 0.014 * index)
        view.shape(px - r, py - r, 2 * r, 2 * r, (kit.TEAL[0], kit.TEAL[1], kit.TEAL[2], alpha), r)


# Public names for ``ui_kit`` components that embed the card's own controls.
button, disclosure, ring = _button, _disclosure, _ring
