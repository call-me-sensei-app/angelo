# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""List row, provider tile, letter tile and the auto-hiding scrollbar (``ui_kit``).

API (rects are region pixels ``(x, y, w, h)``, y up; ``ui`` as in ``ui_kit``):

    draw_letter_tile(view, rect, scale, letter, status=None)      monogram tile; status ok | warn | error | None
    row_height(scale, subtitle=False) -> h
    draw_list_row(view, rect, scale, ui=None, title="", subtitle="", lead=None, trailing=None, selected=False,
                  action="row", value=None, enabled=True, time=0.0)
        lead:     None | ("letter", "A"[, status]) | ("dot", rgba) | ("check", checked)
        trailing: None | ("meta", text) | ("check",) | ("chip", kind[, label]) | ("chevron",)
    tile_size(scale) -> (w, h)                                    a provider tile's natural size
    draw_provider_tile(view, rect, scale, ui=None, letter="", name="", caption="", selected=False,
                       action="provider", value=None, enabled=True)    caption: the one-line billing text
    scrollbar_rects(rect, content, visible, offset, scale) -> (track, thumb) | None   (None: nothing to scroll)
    scrollbar_alpha(idle_seconds, hover=False, dragging=False) -> 0..1    shown while used, fades when idle
    drag_offset(start_offset, pointer_dy, rect, content, visible, scale) -> offset   pointer_dy in pixels, y up
    draw_scrollbar(view, rect, scale, content, visible, offset, alpha=1.0, hover=False, dragging=False)
        rect: the scrolled area; the bar sits inside its trailing edge (mirrored with the canvas).
"""

from . import ui_kit
from . import ui_tokens as tk
from .ui_kit import dim, register_component, state_for, text_middle

TILE_FILL = (0.122, 0.133, 0.157, 1)
TILE_MARK = (0.063, 0.071, 0.086, 1)
STATUS = {"ok": tk.TEAL, "warn": tk.AMBER, "error": tk.CORAL}
SHOW_SECONDS, FADE_SECONDS = 1.2, 0.35


@ui_kit.blending
def draw_letter_tile(view, rect, scale, letter, status=None):
    x, y, w, h = rect
    radius = 7 * scale
    view.shape(*rect, TILE_MARK, radius)
    ui_kit.outline(rect, radius, max(1.0, scale), (1, 1, 1, 0.12))
    size = h * 0.46
    tw = ui_kit.measure(view, letter, size)
    text_middle(view, letter, x + (w - tw) / 2, y, h, size, tk.TEXT)
    if status in STATUS:   # A status dot on the tile's corner, cut out of the card with a ring of card colour.
        r = 4 * scale
        cx, cy = x + w - 1 * scale, y + 1 * scale
        view.shape(cx - r - 2 * scale, cy - r - 2 * scale, 2 * r + 4 * scale, 2 * r + 4 * scale, tk.CARD, r + 2 * scale)
        view.shape(cx - r, cy - r, 2 * r, 2 * r, STATUS[status], r)


def row_height(scale, subtitle=False):
    return (54 if subtitle else tk.ROW_HEIGHT) * scale


@ui_kit.blending
def draw_list_row(view, rect, scale, ui=None, title="", subtitle="", lead=None, trailing=None, selected=False,
                  action="row", value=None, enabled=True, time=0.0):
    from . import ui_kit_status
    x, y, w, h = rect
    s = scale
    st = state_for(ui, action, value, enabled)
    radius = tk.RADIUS_CONTROL * s
    if selected:
        view.shape(*rect, (tk.TEAL[0], tk.TEAL[1], tk.TEAL[2], 0.10), radius)
        ui_kit.outline(rect, radius, max(1.0, s), dim(tk.TEAL, 0.55))
    if st.hover or st.pressed:
        view.shape(*rect, (1, 1, 1, 0.07 if st.pressed else 0.04), radius)
    if st.focus:
        ui_kit.focus_ring(view, rect, radius, s)
    tx = x + 12 * s
    if lead:
        kind = lead[0]
        if kind == "letter":
            side = 28 * s
            draw_letter_tile(view, (tx, y + (h - side) / 2, side, side), s, lead[1], lead[2] if len(lead) > 2 else None)
            tx += side + 12 * s
        elif kind == "dot":
            r = 4 * s
            view.shape(tx, y + h / 2 - r, 2 * r, 2 * r, lead[1], r)
            tx += 2 * r + 12 * s
        elif kind == "check":
            side = 18 * s
            _checkmark_box(view, (tx, y + (h - side) / 2, side, side), s, lead[1])
            tx += side + 12 * s
    right = x + w - 12 * s
    if trailing:
        kind = trailing[0]
        if kind == "meta":
            size = tk.TYPE_SMALL * s
            mw = ui_kit.measure(view, trailing[1], size)
            text_middle(view, trailing[1], right - mw, y, h, size, tk.MUTED)
            right -= mw + 12 * s
        elif kind == "check":
            cx, cy = right - 6 * s, y + h / 2
            mark = tk.TEAL if enabled else dim(tk.MUTED)
            ui_kit.check(view, cx, cy, 1.15 * s, mark, 1.8 * s)
            right -= 22 * s
        elif kind == "chip":
            label = trailing[2] if len(trailing) > 2 else None
            cw, ch = ui_kit_status.chip_size(view, trailing[1], s, label)
            ui_kit_status.draw_chip(view, (right - cw, y + (h - ch) / 2, cw, ch), s, None, trailing[1], label, time)
            right -= cw + 12 * s
        elif kind == "chevron":
            cx, cy = right - 4 * s, y + h / 2
            view.stroke(cx - 2.5 * s, cy + 4.5 * s, cx + 2 * s, cy, tk.MUTED, 1.6 * s)
            view.stroke(cx + 2 * s, cy, cx - 2.5 * s, cy - 4.5 * s, tk.MUTED, 1.6 * s)
            right -= 18 * s
    width = max(20 * s, right - tx)
    color = tk.TEXT if enabled else dim(tk.TEXT)
    size, small = tk.TYPE_BODY * s, tk.TYPE_SMALL * s
    title = _elide(view, title, width, size)
    if subtitle:
        ui_kit.text(view, title, tx, y + h / 2 + 3 * s, size, color)
        ui_kit.text(view, _elide(view, subtitle, width, small), tx, y + h / 2 - small - 2 * s, small,
                    tk.MUTED if enabled else dim(tk.MUTED))
    else:
        text_middle(view, title, tx, y, h, size, color)


def _elide(view, text, width, size):
    from . import canvas_rtl
    return canvas_rtl.elide(text, lambda part: ui_kit.measure(view, part, size), width) if text else text


def _checkmark_box(view, rect, s, checked):
    x, y, w, h = rect
    if checked:
        view.shape(*rect, tk.TEAL, 5 * s)
        ui_kit.check(view, x + w / 2, y + h / 2, w / 18 * 1.2, tk.INK, 2 * s)
    else:
        ui_kit.outline(rect, 5 * s, max(1.0, 1.4 * s), tk.MUTED)


def tile_size(scale):
    return 150 * scale, 60 * scale


@ui_kit.blending
def draw_provider_tile(view, rect, scale, ui=None, letter="", name="", caption="", selected=False,
                       action="provider", value=None, enabled=True):
    x, y, w, h = rect
    s = scale
    st = state_for(ui, action, value, enabled)
    radius = tk.RADIUS_PANEL * s
    if st.focus:
        ui_kit.focus_ring(view, rect, radius, s)
    fill = tk.PANEL_HOVER if st.hover and not st.pressed else tk.LINE_PRESSED if st.pressed else TILE_FILL
    view.shape(*rect, fill if enabled else dim(fill, 0.5), radius)
    if selected:
        ui_kit.outline(rect, radius, max(1.0, 1.6 * s), tk.TEAL)
    else:
        ui_kit.outline(rect, radius, max(1.0, s), (1, 1, 1, 0.10 if st.hover else 0.06))
    side = 28 * s
    draw_letter_tile(view, (x + 12 * s, y + (h - side) / 2, side, side), s, letter)
    tx = x + 12 * s + side + 10 * s
    width = x + w - 10 * s - tx
    size, small = tk.TYPE_BODY * s, tk.TYPE_SMALL * s
    color = tk.TEXT if enabled else dim(tk.TEXT)
    ui_kit.text(view, _elide(view, name, width, size), tx, y + h / 2 + 3 * s, size, color)
    ui_kit.text(view, _elide(view, caption, width, small), tx, y + h / 2 - small - 2 * s, small,
                tk.MUTED if enabled else dim(tk.MUTED))


# -- scrollbar ---------------------------------------------------------------------------------------------------
def scrollbar_rects(rect, content, visible, offset, scale):
    if content <= visible + 0.5:
        return None
    x, y, w, h = rect
    track = (x + w - 8 * scale, y + 4 * scale, 4 * scale, h - 8 * scale)
    thumb_h = max(28 * scale, track[3] * visible / content)
    travel = track[3] - thumb_h
    ratio = max(0.0, min(1.0, offset / max(1.0, content - visible)))
    return track, (track[0], track[1] + travel * (1 - ratio), track[2], thumb_h)


def scrollbar_alpha(idle_seconds, hover=False, dragging=False):
    if hover or dragging:
        return 1.0
    if idle_seconds <= SHOW_SECONDS:
        return 1.0
    return max(0.0, 1.0 - (idle_seconds - SHOW_SECONDS) / FADE_SECONDS)


def drag_offset(start_offset, pointer_dy, rect, content, visible, scale):
    """The offset after dragging the thumb ``pointer_dy`` pixels (positive: up, toward the start)."""
    rects = scrollbar_rects(rect, content, visible, start_offset, scale)
    if rects is None:
        return 0.0
    track, thumb = rects
    travel = max(1.0, track[3] - thumb[3])
    offset = start_offset - pointer_dy * (content - visible) / travel
    return max(0.0, min(content - visible, offset))


@ui_kit.blending
def draw_scrollbar(view, rect, scale, content, visible, offset, alpha=1.0, hover=False, dragging=False):
    rects = scrollbar_rects(rect, content, visible, offset, scale)
    if rects is None or alpha <= 0:
        return
    track, thumb = rects
    if hover or dragging:   # Wider and brighter under the pointer, so it reads as grabbable.
        grow = 2 * scale
        track = (track[0] - grow, track[1], track[2] + grow, track[3])
        thumb = (thumb[0] - grow, thumb[1], thumb[2] + grow, thumb[3])
        view.shape(*track, (1, 1, 1, 0.07 * alpha), track[2] / 2)
    view.shape(*thumb, (1, 1, 1, (0.55 if dragging else 0.40 if hover else 0.26) * alpha), thumb[2] / 2)


register_component("list_row", draw_list_row)
register_component("provider_tile", draw_provider_tile)
register_component("scrollbar", draw_scrollbar)
