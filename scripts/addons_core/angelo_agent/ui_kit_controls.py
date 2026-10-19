# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Toggle switch and segmented control (``ui_kit``).

API (rects are region pixels ``(x, y, w, h)``, y up; ``ui`` as in ``ui_kit``; the caller flips or selects in
its action handler, these only draw and lay out):

    toggle_size(scale) -> (w, h)
    draw_toggle(view, rect, scale, ui=None, on=False, action=None, value=None, enabled=True)
    draw_toggle_row(view, rect, scale, ui=None, label="", caption="", on=False, action=None, value=None,
                    enabled=True)            label (and an optional muted caption) at the start, the switch at the end
    toggle_row_height(view, width, scale, label, caption="") -> h      label and caption wrap (no cut)
    segmented_width(view, scale, segments) -> natural width (equal segments sized to the longest label)
    segment_rects(rect, count) -> [rect]     equal segments filling the track, in reading order (mirrored with
                                             the canvas)
    segmented_hits(rect, segments, action) -> [(rect, action, value)]
    draw_segmented(view, rect, scale, ui=None, segments=(), selected=None, action="segment", enabled=True)
        segments: [(value, label)] or [(value, label, swatch_rgba)] (a colour sample before the label)

The segmented control marks its selection once: the chosen segment is filled, flush with the track, and the track
has the only border. Swatches are all the same rounded square with the same contrast edge (``SWATCH_EDGE``, a
shadow line inside the sample), so a dark and a light sample differ only in colour: both read as filled samples,
never one as a ring or an empty box.
"""

from . import ui_kit
from . import ui_tokens as tk
from .ui_kit import dim, register_component, state_for, text_middle

TRACK_OFF = (0.200, 0.215, 0.245, 1)
TRACK_OFF_HOVER = (0.250, 0.268, 0.302, 1)
KNOB = (0.950, 0.960, 0.970, 1)
SEG_TRACK = (0.063, 0.071, 0.086, 1)
SEG_SELECTED = (0.270, 0.290, 0.330, 1)
SWATCH_EDGE = (0, 0, 0, 0.45)   # Every swatch's rim: a shadow line inside it, so each reads as a filled sample.


# -- toggle ----------------------------------------------------------------------------------------------------
def toggle_size(scale):
    return tk.TOGGLE_SIZE[0] * scale, tk.TOGGLE_SIZE[1] * scale


@ui_kit.blending
def draw_toggle(view, rect, scale, ui=None, on=False, action=None, value=None, enabled=True):
    x, y, w, h = rect
    st = state_for(ui, action, value, enabled)
    if st.focus:
        ui_kit.focus_ring(view, rect, h / 2, scale)
    if on:
        track = tk.TEAL_PRESSED if st.pressed else tk.TEAL_HOVER if st.hover else tk.TEAL
    else:
        track = tk.LINE_PRESSED if st.pressed else TRACK_OFF_HOVER if st.hover else TRACK_OFF
    if not enabled:
        track = dim(track, 0.4)
    view.shape(x, y, w, h, track, h / 2)
    if not on:
        ui_kit.outline(rect, h / 2, max(1.0, scale), (1, 1, 1, 0.10))
    inset = 2.5 * scale
    knob = h - 2 * inset
    grow = 3 * scale if st.pressed else 0   # The knob stretches while held, as it is about to travel.
    kx = (x + w - inset - knob - grow) if on else (x + inset)
    view.shape(kx, y + inset, knob + grow, knob, KNOB if enabled else dim(KNOB, 0.5), knob / 2)


def _row_lines(view, width, scale, label, caption):
    """The label's and the caption's wrapped lines beside the switch (both wrap; nothing is cut)."""
    text_w = max(40 * scale, width - 20 * scale - toggle_size(scale)[0] - 16 * scale)

    def wrap(text, size):
        return [line.rstrip("\n") for line, _ in view.wrap(text, max(40, int(text_w)), round(size, 2), view._font)] \
            if text else []
    return wrap(label, tk.TYPE_BODY * scale), wrap(caption, tk.TYPE_SMALL * scale)


def toggle_row_height(view, width, scale, label, caption=""):
    labels, captions = _row_lines(view, width, scale, label, caption)
    block = len(labels) * tk.TYPE_BODY * 1.45 * scale + len(captions) * tk.TYPE_SMALL * 1.45 * scale
    return max(tk.ROW_HEIGHT * scale, block + 16 * scale)


@ui_kit.blending
def draw_toggle_row(view, rect, scale, ui=None, label="", caption="", on=False, action=None, value=None,
                    enabled=True):
    x, y, w, h = rect
    st = state_for(ui, action, value, enabled)
    if st.hover or st.pressed:
        view.shape(x, y, w, h, (1, 1, 1, 0.06 if st.pressed else 0.035), tk.RADIUS_CONTROL * scale)
    tw, th = toggle_size(scale)
    labels, captions = _row_lines(view, w, scale, label, caption)
    size, small = tk.TYPE_BODY * scale, tk.TYPE_SMALL * scale
    label_h, caption_h = size * 1.45, small * 1.45
    top = y + (h + len(labels) * label_h + len(captions) * caption_h) / 2   # The text block, centred in the row.
    for line in labels:
        ui_kit.text(view, line, x + 10 * scale, top - label_h / 2 - 0.36 * size, size,
                    tk.TEXT if enabled else dim(tk.TEXT))
        top -= label_h
    for line in captions:
        ui_kit.text(view, line, x + 10 * scale, top - caption_h / 2 - 0.36 * small, small,
                    tk.MUTED if enabled else dim(tk.MUTED))
        top -= caption_h
    track = (x + w - tw - 10 * scale, y + (h - th) / 2, tw, th)
    draw_toggle(view, track, scale, ui, on, action, value, enabled)   # The switch carries the focus ring.


def _draw_toggle_component(view, rect, scale, ui, row=True, **params):
    (draw_toggle_row if row else draw_toggle)(view, rect, scale, ui, **params)


# -- segmented control -----------------------------------------------------------------------------------------
def _segment(spec):
    return (spec[0], spec[1], spec[2] if len(spec) > 2 else None)


def segmented_width(view, scale, segments):
    longest = 0.0
    for spec in segments:
        _, label, swatch = _segment(spec)
        longest = max(longest, ui_kit.measure(view, label, tk.TYPE_SMALL * scale) + (16 * scale if swatch else 0))
    return len(segments) * (longest + 28 * scale)


def segment_rects(rect, count):
    x, y, w, h = rect
    width = w / max(1, count)
    return [(x + index * width, y, width, h) for index in range(count)]


def segmented_hits(rect, segments, action):
    return [(r, action, _segment(spec)[0]) for r, spec in zip(segment_rects(rect, len(segments)), segments)]


@ui_kit.blending
def draw_segmented(view, rect, scale, ui=None, segments=(), selected=None, action="segment", enabled=True):
    x, y, w, h = rect
    radius = h / 2
    view.shape(x, y, w, h, SEG_TRACK, radius)
    size = tk.TYPE_SMALL * scale
    focused = None
    for r, spec in zip(segment_rects(rect, len(segments)), segments):
        value, label, swatch = _segment(spec)
        st = state_for(ui, action, value, enabled)
        chosen = value == selected
        if chosen:   # The one selection mark: a filled segment, flush with the track.
            view.shape(*r, SEG_SELECTED if enabled else dim(SEG_SELECTED, 0.6), radius)
        elif st.hover or st.pressed:
            view.shape(*r, (1, 1, 1, 0.07 if st.pressed else 0.035), radius)
        if st.focus:
            focused = r
        color = tk.TEXT if chosen or st.hover else tk.MUTED
        if not enabled:
            color = dim(color)
        room = r[2] - 16 * scale - (16 * scale if swatch else 0)
        if ui_kit.measure(view, label, size) > room:   # A narrow control: the label is cut with "…", never spills.
            from . import canvas_rtl
            label = canvas_rtl.elide(label, lambda part: ui_kit.measure(view, part, size), max(8 * scale, room))
        tw = ui_kit.measure(view, label, size) + (16 * scale if swatch else 0)
        tx = r[0] + (r[2] - tw) / 2
        if swatch:   # A colour sample: the same square and edge for every swatch, selected or not.
            d, corner = 11 * scale, 3 * scale
            sy = r[1] + (r[3] - d) / 2
            view.shape(tx, sy, d, d, swatch if enabled else dim(swatch, 0.6), corner)
            ui_kit.outline((tx, sy, d, d), corner, max(1.0, scale), SWATCH_EDGE)
            tx += 16 * scale
        text_middle(view, label, tx, r[1] - (1 if st.pressed else 0), r[3], size, color)
    ui_kit.outline(rect, radius, max(1.0, scale), tk.EDGE)   # The control's only border, over the selection.
    if focused is not None:
        ui_kit.focus_ring(view, focused, radius, scale)


register_component("toggle", _draw_toggle_component)
register_component("segmented", draw_segmented)
