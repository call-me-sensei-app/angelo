# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Status chip, inline notice and code block (``ui_kit``'s first three components).

API (rects are region pixels ``(x, y, w, h)``, y up; ``scale`` is the UI scale; ``ui`` as in ``ui_kit``):

    CHIP_KINDS = running, done, failed, needs_you, preview, paid
    chip_size(view, kind, scale, label=None) -> (w, h)
    draw_chip(view, rect, scale, ui=None, kind="running", label=None, time=0.0)
    notice(view, x, top, width, scale, ui=None, draw=True, kind="info", title="", body="", actions=(),
           details=None, expanded=False, details_action=None, details_value=None) -> (height, hits)
        kind: info | warn | error | success; actions: [(action, value, label, style)] (card_kit button styles);
        details: technical text shown mono in a collapsed "Details" disclosure; hits: [(rect, action, value)].
        draw=False only measures (the height and hits a later draw at the same place will have).
    code_block_height(view, code, scale, header=True) -> h
    draw_code_block(view, rect, scale, ui=None, code="", line_numbers=False, label="", copy_action=None,
                    copy_value=None, copied=False)
    code_block_hits(rect, scale, copy_action=None, copy_value=None) -> [(rect, action, value)]
        The header strip (an optional label at the start, Copy at the end) exists when ``copy_action`` or
        ``label`` is given. Lines are never wrapped: long ones are clipped with a fade. Code reads left to right
        in every interface language; the frame and header mirror with the rest of the canvas.
"""

import math

from . import canvas_rtl, ui_kit
from . import ui_tokens as tk
from .ui_kit import REST, State, dim, register_component, state_for, text_middle
from .ui_text import iface_, n_

CODE_FILL = (0.063, 0.071, 0.086, 1)       # Inset one step darker than the card.
CODE_HEADER = (0.082, 0.090, 0.110, 1)
NOTICE_FILL = tk.PANEL
NEUTRAL_SOFT = (1, 1, 1, 0.08)

# kind -> (default label, accent, soft fill, mark)
CHIP_KINDS = {
    "running": (n_("Running"), tk.TEAL, tk.TEAL_SOFT, "pulse"),
    "done": (n_("Done"), tk.TEAL, tk.TEAL_SOFT, "check"),
    "failed": (n_("Failed"), tk.CORAL, tk.CORAL_SOFT, "cross"),
    "needs_you": (n_("Needs you"), tk.AMBER, tk.AMBER_SOFT, "dot"),
    "preview": (n_("Preview"), tk.SUBTITLE, NEUTRAL_SOFT, "ring"),
    "paid": (n_("Paid"), tk.AMBER, tk.AMBER_SOFT, "coin"),
}
NOTICE_KINDS = {"info": (tk.TEAL, "info"), "warn": (tk.AMBER, "warn"), "error": (tk.CORAL, "error"),
                "success": (tk.TEAL, "check")}
COPY, COPIED, DETAILS = n_("Copy"), n_("Copied"), n_("Details")


# -- status chip ---------------------------------------------------------------------------------------------
def _chip_label(kind, label):
    return label if label is not None else iface_(CHIP_KINDS[kind][0])


def chip_size(view, kind, scale, label=None):
    size = tk.TYPE_MICRO * scale
    width = ui_kit.measure(view, _chip_label(kind, label), size) + (9 + 8 + 6 + 10) * scale
    return width, tk.CHIP_HEIGHT * scale


@ui_kit.blending
def draw_chip(view, rect, scale, ui=None, kind="running", label=None, time=0.0):
    x, y, w, h = rect
    _, accent, soft, mark = CHIP_KINDS[kind]
    view.shape(x, y, w, h, soft, h / 2)
    cx, cy, r = x + 13 * scale, y + h / 2, 3.5 * scale
    if mark == "pulse":   # A dot with a breathing halo: work is happening.
        phase = 0.5 + 0.5 * math.sin(time * 4.0)
        halo = r + (1.5 + 2.5 * phase) * scale
        view.shape(cx - halo, cy - halo, 2 * halo, 2 * halo, dim(accent, 0.18 + 0.2 * (1 - phase)), halo)
        view.shape(cx - r, cy - r, 2 * r, 2 * r, accent, r)
    elif mark == "check":
        ui_kit.check(view, cx, cy, 0.9 * scale, accent, 1.6 * scale)
    elif mark == "cross":
        d = 3.0 * scale
        view.stroke(cx - d, cy - d, cx + d, cy + d, accent, 1.6 * scale)
        view.stroke(cx - d, cy + d, cx + d, cy - d, accent, 1.6 * scale)
    elif mark == "ring":
        ui_kit.outline((cx - r, cy - r, 2 * r, 2 * r), r, max(1.0, 1.4 * scale), accent)
    elif mark == "coin":   # A filled disc with a dark centre: a coin, for work that spends credit.
        view.shape(cx - r, cy - r, 2 * r, 2 * r, accent, r)
        inner = r * 0.42
        view.shape(cx - inner, cy - inner, 2 * inner, 2 * inner, tk.CARD, inner)
    else:
        view.shape(cx - r, cy - r, 2 * r, 2 * r, accent, r)
    size = tk.TYPE_MICRO * scale
    text_middle(view, _chip_label(kind, label), x + (9 + 8 + 6) * scale, y, h, size, accent)


# -- inline notice ---------------------------------------------------------------------------------------------
def _lines(view, text, width, size, mono=False):
    font = view._mono if mono else view._font
    return [line.rstrip("\n") for line, _ in view.wrap(text, max(40, int(width)), round(size, 2), font)] if text else []


@ui_kit.blending
def notice(view, x, top, width, scale, ui=None, draw=True, kind="info", title="", body="", actions=(),
           details=None, expanded=False, details_action=None, details_value=None):
    """Lay out (and with ``draw``, draw) a notice whose top-left corner is ``(x, top)``; returns ``(height, hits)``."""
    s = scale
    accent, mark = NOTICE_KINDS[kind]
    pad, icon = 14 * s, 18 * s
    tx = x + pad + icon + 10 * s
    text_w = width - (tx - x) - pad
    title_size, body_size, mono_size = tk.TYPE_BODY * s, 13 * s, tk.TYPE_MONO * s
    title_lines = _lines(view, title, text_w, title_size)
    body_lines = _lines(view, body, text_w, body_size)
    detail_lines = _lines(view, details, text_w - 20 * s, mono_size, mono=True) if details and expanded else []
    y = top - pad
    ops, hits = [], []
    for line in title_lines:
        ops.append(("text", line, tx, y, title_size, title_size * 1.4, tk.TEXT, False))
        y -= title_size * 1.4
    if body_lines:
        y -= 2 * s
    for line in body_lines:
        ops.append(("text", line, tx, y, body_size, body_size * 1.5, tk.SUBTITLE, False))
        y -= body_size * 1.5
    if actions:
        y -= 10 * s
        bh, bx = tk.BUTTON_HEIGHT_SMALL * s, tx
        for action, value, label, style in actions:
            bw = ui_kit.measure(view, label, 12.5 * s) + 28 * s
            if bx > tx and bx + bw > x + width - pad:
                bx, y = tx, y - bh - 8 * s
            rect = (bx, y - bh, bw, bh)
            ops.append(("button", rect, label, style, action, value))
            hits.append((rect, action, value))
            bx += bw + 8 * s
        y -= bh
    if details:
        y -= 6 * s
        dh = 13 * s * 1.7
        label = iface_(DETAILS)
        dw = ui_kit.measure(view, label, 13 * s) + 22 * s
        rect = (tx, y - dh, dw, dh)
        ops.append(("disclosure", rect, label, expanded, details_action, details_value))
        if details_action:
            hits.append((rect, details_action, details_value))
        y -= dh
        if detail_lines:
            y -= 4 * s
            box_h = len(detail_lines) * mono_size * 1.5 + 16 * s
            ops.append(("details", (tx, y - box_h, text_w, box_h), detail_lines, mono_size))
            y -= box_h
    height = top - y + pad
    if draw:
        _draw_notice(view, (x, top - height, width, height), s, ui, accent, mark, ops)
    return height, hits


def _draw_notice(view, rect, s, ui, accent, mark, ops):
    from . import card_draw
    x, y, w, h = rect
    view.shape(x, y, w, h, NOTICE_FILL, tk.RADIUS_PANEL * s)
    ui_kit.outline(rect, tk.RADIUS_PANEL * s, max(1.0, s), dim(accent, 0.35))
    view.shape(x + 1 * s, y + 10 * s, 3 * s, h - 20 * s, accent, 1.5 * s)   # The accent bar at the leading edge.
    icon = 18 * s
    ix, iy = x + 14 * s, y + h - 14 * s - icon - 1 * s
    _notice_mark(view, ix, iy, icon, accent, mark, s)
    for op in ops:
        kind = op[0]
        if kind == "text":
            _, line, tx, top, size, leading, color, mono = op
            ui_kit.text(view, line, tx, top - leading / 2 - 0.36 * size, size, color)
        elif kind == "button":
            _, brect, label, style, action, value = op
            card_draw.button(view, brect, label, style, tuple(state_for(ui, action, value))[:4], 12.5 * s)
        elif kind == "disclosure":
            _, drect, label, expanded, action, value = op
            card_draw.disclosure(view, drect, label, expanded, tuple(state_for(ui, action, value))[:4], 13 * s)
        elif kind == "details":
            _, box, lines, size = op
            view.shape(*box, CODE_FILL, tk.RADIUS_CONTROL * s)
            for index, line in enumerate(lines):
                base = box[1] + box[3] - 8 * s - (index + 0.5) * size * 1.5 - 0.36 * size
                with canvas_rtl.upright(box[0] + 10 * s, ui_kit.measure(view, line, size, mono=True)) as lx:
                    with canvas_rtl.unmirrored():
                        ui_kit.text(view, line, lx, base, size, tk.MUTED, view._mono, False)


def _notice_mark(view, x, y, size, accent, mark, s):
    cx, cy, r = x + size / 2, y + size / 2, size / 2
    view.shape(x, y, size, size, dim(accent, 0.2), r)
    t = 1.7 * s
    if mark == "info":
        view.shape(cx - 0.9 * s, cy - 4.5 * s, 1.8 * s, 6 * s, accent, 0.9 * s)
        view.shape(cx - 1.1 * s, cy + 2.8 * s, 2.2 * s, 2.2 * s, accent, 1.1 * s)
    elif mark == "warn":
        view.shape(cx - 0.9 * s, cy - 1.5 * s, 1.8 * s, 6 * s, accent, 0.9 * s)
        view.shape(cx - 1.1 * s, cy - 4.8 * s, 2.2 * s, 2.2 * s, accent, 1.1 * s)
    elif mark == "error":
        d = 3.4 * s
        view.stroke(cx - d, cy - d, cx + d, cy + d, accent, t)
        view.stroke(cx - d, cy + d, cx + d, cy - d, accent, t)
    else:
        ui_kit.check(view, cx, cy, s, accent, t)


def _draw_notice_component(view, rect, scale, ui, **params):
    notice(view, rect[0], rect[1] + rect[3], rect[2], scale, ui, True, **params)


# -- code block -----------------------------------------------------------------------------------------------
def _code_metrics(scale):
    size = tk.TYPE_MONO * scale
    return size, size * 1.6, 12 * scale, 28 * scale   # size, line height, padding, header height


def code_block_height(view, code, scale, header=True):
    size, line_h, pad, head = _code_metrics(scale)
    return len(code.split("\n")) * line_h + 2 * pad + (head if header else 0)


def _copy_label_width(view, scale):
    """Wide enough for "Copy" and "Copied", so the button never changes size when it is used."""
    return max(ui_kit.measure(view, iface_(COPY), 11 * scale), ui_kit.measure(view, iface_(COPIED), 11 * scale))


def _copy_rect(rect, scale, label_w):
    x, y, w, h = rect
    head = 28 * scale
    bw = label_w + 20 * scale
    return (x + w - bw - 6 * scale, y + h - head + 4 * scale, bw, head - 8 * scale)


def code_block_hits(rect, scale, copy_action=None, copy_value=None, view=None):
    if not copy_action:
        return []
    label_w = _copy_label_width(view, scale) if view is not None else 44 * scale
    return [(_copy_rect(rect, scale, label_w), copy_action, copy_value)]


@ui_kit.blending
def draw_code_block(view, rect, scale, ui=None, code="", line_numbers=False, label="", copy_action=None,
                    copy_value=None, copied=False):
    import gpu
    x, y, w, h = rect
    s = scale
    size, line_h, pad, head = _code_metrics(s)
    radius = tk.RADIUS_CONTROL * s
    header = bool(copy_action or label)
    view.shape(x, y, w, h, CODE_FILL, radius)
    body_top = y + h - (head if header else 0)
    if header:
        view.shape(x, body_top, w, head, CODE_HEADER, radius)
        view.shape(x, body_top, w, radius, CODE_HEADER, 0)   # Square bottom corners where the body joins.
        view.shape(x, body_top, w, max(1.0, s), tk.EDGE, 0)
        if label:
            text_middle(view, label, x + pad, body_top, head, 11 * s, tk.MUTED)
        if copy_action:
            text = iface_(COPIED if copied else COPY)
            crect = _copy_rect(rect, s, _copy_label_width(view, s))
            st = state_for(ui, copy_action, copy_value)
            fill = (1, 1, 1, 0.16 if st.pressed else 0.10 if st.hover else 0.0)
            if fill[3]:
                view.shape(*crect, fill, crect[3] / 2)
            if st.focus:
                ui_kit.focus_ring(view, crect, crect[3] / 2, s)
            color = tk.TEAL if copied else tk.TEXT if (st.hover or st.focus) else tk.MUTED
            tw = ui_kit.measure(view, text, 11 * s)
            text_middle(view, text, crect[0] + (crect[2] - tw) / 2, crect[1] - (1 if st.pressed else 0), crect[3],
                        11 * s, color)
    lines = code.split("\n")
    digits = len(str(len(lines)))
    gutter = (ui_kit.measure(view, "8" * digits, size, mono=True) + 14 * s) if line_numbers else 0
    visible_w = w - 2 * pad - gutter
    clipped = False
    with canvas_rtl.upright(x, w) as rx:     # Code reads left to right in every interface language.
        with canvas_rtl.unmirrored():
            with ui_kit.clip(rx + 1, y + 1, w - 2, body_top - y - 2):
                for index, line in enumerate(lines):
                    base = body_top - pad - (index + 0.5) * line_h - 0.36 * size
                    if base + size < y:
                        break
                    if line_numbers:
                        number = str(index + 1)
                        nw = ui_kit.measure(view, number, size, mono=True)
                        ui_kit.text(view, number, rx + pad + gutter - 14 * s - nw, base, size, dim(tk.MUTED, 0.6), view._mono, False)
                    if line:
                        ui_kit.text(view, line, rx + pad + gutter, base, size, tk.TEXT, view._mono, False)
                        clipped = clipped or ui_kit.measure(view, line, size, mono=True) > visible_w
                if clipped:   # A fade to the fill, so a cut line reads as continuing rather than broken.
                    fade, steps = 32 * s, 12
                    for step in range(steps):
                        alpha = (step + 1) / steps
                        gpu.state.blend_set('ALPHA')
                        view.shape(rx + w - pad - fade + step * fade / steps, y + 1, fade / steps + 1, body_top - y - 2,
                                   (CODE_FILL[0], CODE_FILL[1], CODE_FILL[2], alpha), 0)
                    view.shape(rx + w - pad, y + 1, pad - 1, body_top - y - 2, CODE_FILL, 0)
    ui_kit.outline(rect, radius, max(1.0, s), tk.EDGE)   # Last, so the header's fill never covers it.


register_component("chip", draw_chip)
register_component("notice", _draw_notice_component)
register_component("code_block", draw_code_block)
__all__ = ["CHIP_KINDS", "NOTICE_KINDS", "chip_size", "draw_chip", "notice", "code_block_height", "draw_code_block",
           "code_block_hits", "REST", "State"]
