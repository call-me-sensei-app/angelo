# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dropdown / select (``ui_kit``): a closed control showing the choice, and an anchored popover list.

The popover opens below its control (above when the region has no room below), is drawn last so it covers
the content under it, and is keyboard navigable: ``select_key`` moves the highlight with the arrows, Home and
End, chooses with Return or Space, and closes with Escape or Tab. The caller keeps ``open`` and
``highlighted`` (a card attribute) and applies a choice in its action handler.

API (rects are region pixels ``(x, y, w, h)``, y up; ``ui`` as in ``ui_kit``; options ``[(value, label)]``):

    draw_select(view, rect, scale, ui=None, text="", placeholder="", open=False, action="select", value=None,
                enabled=True)                                     the closed control: choice + chevron
    popover_rects(anchor, count, scale, region_h=None, flip=True) -> (panel, [row rects])
    popover_hits(anchor, options, scale, action="option", region_h=None, flip=True) -> [(rect, action, value)]
    draw_popover(view, anchor, scale, ui=None, options=(), selected=None, highlighted=None, action="option",
                 region_h=None, flip=True)       anchor: the closed control's rect; drawn after everything else
    select_key(options, highlighted, kind) -> (highlighted, chosen_value_or_None, close)
"""

from . import ui_kit
from . import ui_tokens as tk
from .ui_kit import dim, register_component, state_for, text_middle

FIELD_FILL = (0.063, 0.071, 0.086, 1)
POPOVER = (0.110, 0.120, 0.142, 1)
ROW_HEIGHT = 32


@ui_kit.blending
def draw_select(view, rect, scale, ui=None, text="", placeholder="", open=False, action="select", value=None,
                enabled=True):
    x, y, w, h = rect
    s = scale
    st = state_for(ui, action, value, enabled)
    radius = tk.RADIUS_CONTROL * s
    if st.focus:
        ui_kit.focus_ring(view, rect, radius, s)
    fill = (0.082, 0.092, 0.110, 1) if st.hover or open else FIELD_FILL
    view.shape(*rect, fill if enabled else dim(fill, 0.5), radius)
    ui_kit.outline(rect, radius, max(1.0, s), tk.TEAL if open else (1, 1, 1, 0.22 if st.hover else 0.12))
    size = tk.TYPE_BODY * s
    shown = text or placeholder
    color = (tk.TEXT if text else dim(tk.MUTED, 0.8)) if enabled else dim(tk.TEXT)
    text_middle(view, ui_kit_elide(view, shown, w - 44 * s, size), x + 12 * s, y - (1 if st.pressed else 0), h, size,
                color)
    cx, cy = x + w - 18 * s, y + h / 2
    chevron = tk.TEXT if st.hover or open else tk.MUTED
    d = 4 * s
    if open:   # Points up while the list is open.
        view.stroke(cx - d, cy - d / 2, cx, cy + d / 2, chevron, 1.6 * s)
        view.stroke(cx, cy + d / 2, cx + d, cy - d / 2, chevron, 1.6 * s)
    else:
        view.stroke(cx - d, cy + d / 2, cx, cy - d / 2, chevron, 1.6 * s)
        view.stroke(cx, cy - d / 2, cx + d, cy + d / 2, chevron, 1.6 * s)


def ui_kit_elide(view, text, width, size):
    from . import canvas_rtl
    return canvas_rtl.elide(text, lambda part: ui_kit.measure(view, part, size), width)


def popover_rects(anchor, count, scale, region_h=None, flip=True):
    """The panel below ``anchor`` (above it when ``flip`` and there is no room below), and its row rects."""
    x, y, w, h = anchor
    pad, row = 4 * scale, ROW_HEIGHT * scale
    height = count * row + 2 * pad
    top = y - 4 * scale - height
    if flip and top < 4 * scale:
        above = y + h + 4 * scale
        if region_h is None or above + height <= region_h - 4 * scale:
            top = above
    panel = (x, top, w, height)
    rows = [(x + pad, top + height - pad - (index + 1) * row, w - 2 * pad, row) for index in range(count)]
    return panel, rows


def popover_hits(anchor, options, scale, action="option", region_h=None, flip=True):
    _, rows = popover_rects(anchor, len(options), scale, region_h, flip)
    return [(rect, action, option[0]) for rect, option in zip(rows, options)]


@ui_kit.blending
def draw_popover(view, anchor, scale, ui=None, options=(), selected=None, highlighted=None, action="option",
                 region_h=None, flip=True):
    s = scale
    panel, rows = popover_rects(anchor, len(options), s, region_h, flip)
    for depth, alpha in ((8, 0.06), (4, 0.10), (2, 0.14)):   # Lifted above the card.
        d = depth * s
        view.shape(panel[0] - d, panel[1] - d - 2 * s, panel[2] + 2 * d, panel[3] + 2 * d, (0, 0, 0, alpha),
                   tk.RADIUS_CONTROL * s + d)
    view.shape(*panel, POPOVER, tk.RADIUS_CONTROL * s)
    ui_kit.outline(panel, tk.RADIUS_CONTROL * s, max(1.0, s), (1, 1, 1, 0.12))
    size = tk.TYPE_BODY * s
    pointer_on_row = bool(ui) and any(state_for(ui, action, option[0]).hover for option in options)
    for rect, (value, label) in zip(rows, options):
        st = state_for(ui, action, value)
        lit = st.hover or (value == highlighted and not pointer_on_row)   # One lit row: the pointer's, else the keys'.
        if lit or st.pressed:
            view.shape(*rect, (1, 1, 1, 0.10 if st.pressed else 0.055), 6 * s)
        chosen = value == selected
        text_middle(view, label, rect[0] + 10 * s, rect[1], rect[3], size, tk.TEXT if chosen or lit else tk.SUBTITLE)
        if chosen:   # A teal check at the end of the chosen row.
            cx, cy = rect[0] + rect[2] - 16 * s, rect[1] + rect[3] / 2
            ui_kit.check(view, cx, cy, s, tk.TEAL, 1.7 * s)


def select_key(options, highlighted, kind):
    values = [option[0] for option in options]
    if not values:
        return highlighted, None, kind in {'ESC', 'TAB'}
    index = values.index(highlighted) if highlighted in values else -1
    if kind in {'DOWN_ARROW', 'UP_ARROW'}:
        step = 1 if kind == 'DOWN_ARROW' else -1
        index = (index + step) % len(values) if index >= 0 else (0 if step > 0 else len(values) - 1)
        return values[index], None, False
    if kind in {'HOME', 'END'}:
        return values[0 if kind == 'HOME' else -1], None, False
    if kind in {'RET', 'NUMPAD_ENTER', 'SPACE'}:
        return highlighted, (highlighted if index >= 0 else None), True
    if kind in {'ESC', 'TAB'}:
        return highlighted, None, True
    return highlighted, None, False


def _draw_select_component(view, rect, scale, ui, **params):
    draw_select(view, rect, scale, ui, **params)


def _draw_popover_component(view, rect, scale, ui, **params):
    draw_popover(view, rect, scale, ui, **params)


register_component("select", _draw_select_component)
register_component("select_popover", _draw_popover_component)
