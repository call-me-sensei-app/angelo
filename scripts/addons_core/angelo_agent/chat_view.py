# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex-style native chat canvas in its own editor, never over the 3D viewport.

Uses Blender's GPU/font API, not a browser, embedded terminal or screenshot UI.
Console remains the native text-input backing store and a readable fallback.
"""

import functools
import math
import sys
import time
import traceback
from functools import lru_cache
from pathlib import Path

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from . import canvas_controls, canvas_rtl, canvas_wrap, chat_card_draw, chat_cards, chat_composer, chat_welcome, reply_overlay, text_layout
from . import chat_rows  # Agent progress rows, work groups and host notices.
from . import chat_notices, chat_replies, chat_scrollbar, marble_draw, scroll_input, ui_tokens
from .ui_text import iface_, plural_
from .chat_changes import change_counts, file_counts
from .chat_titles import tool_title  # Re-exported: console and ui read titles through this module.
from .chat_hit_testing import clip_rect, clipped_hits, release_active
from .chat_drop import defer_console_drop  # Re-exported for window fixtures.
from .chat_composer import cursor_position, set_draft  # Re-exported: drop, quick connect and fixtures use them.

_handler = None
_shader = None
_font = 0
_mono = 0
_views = {}
_scroll = {}
_modals = set()
_enabled = False
_scale = 1.0  # The canvas's one scale factor while it draws (ui_scale); layout is in unscaled pixels.
_input_generation = 0
last_error = None
draw_count = 0
draw_seconds = 0.0  # The last whole draw's time: marble_host keeps its redraws to a share of the main thread.

BG = (0.094, 0.094, 0.094, 1)
CARD = (0.205, 0.205, 0.205, 1)
USER = (0.185, 0.185, 0.185, 1)
TEXT = (0.95, 0.95, 0.95, 1)
MUTED = (0.63, 0.63, 0.63, 1)
LINE = (0.245, 0.245, 0.245, 1)
POPOVER = (0.165, 0.165, 0.165, 1)
BLUE = ui_tokens.TEAL  # Effort, Fast and progress accents: the brand teal (the name stays for its callers).


def shape(x, y, width, height, color, radius=0):
    if width <= 0 or height <= 0:
        return
    canvas_controls.note_shape(x, y, width, height, radius, color)
    if color[3] < 1:
        gpu.state.blend_set('ALPHA')  # Text drawing resets blending; a translucent fill needs it back.
    radius = min(radius, width / 2, height / 2)
    points = []
    for cx, cy, start in ((x + radius, y + radius, 180), (x + width - radius, y + radius, 270),
                          (x + width - radius, y + height - radius, 0), (x + radius, y + height - radius, 90)):
        for step in range(7):
            angle = math.radians(start + step * 15)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), 0))
    vertices = [(x + width / 2, y + height / 2, 0)] + points + [points[0]]
    batch = batch_for_shader(_shader, 'TRIS', {"pos": vertices},
                             indices=[(0, i, i + 1) for i in range(1, len(vertices) - 1)])
    _shader.bind()
    _shader.uniform_float("color", color)
    batch.draw(_shader)


def ui_scale():
    """The chat's one scale factor: the interface scale with the display's pixel size (Preferences > Interface)."""
    return max(0.5, min(4.0, getattr(bpy.context.preferences.system, "ui_scale", 1.0) or 1.0))


def measure(value, size=14, font=None, scale=None, shaped=True):
    """``value``'s width in layout units as ``text`` draws it: shaped as ``text`` shapes it (``shaped``), rasterised
    at ``size * scale`` (``_scale`` while the chat draws), then scaled back. Glyph advances are whole pixels at the
    drawn size, so a width measured at the unscaled size drifts from the drawn text by up to half a pixel per glyph:
    every caret, selection, click-to-caret and wrap measures here, never with ``blf`` at the unscaled size."""
    font, scale = _font if font is None else font, _scale if scale is None else scale
    blf.size(font, size * scale)
    return blf.dimensions(font, canvas_rtl.visual(value) if shaped else value)[0] / scale


def text(value, x, y, size=14, color=TEXT, font=None, shaped=True):
    font, scale = _font if font is None else font, _scale
    value = canvas_rtl.visual(value) if shaped else value
    width = measure(value, size, font, shaped=False) if canvas_rtl.mirrored() else 0  # Already shaped above.
    blf.size(font, size * scale)  # Glyphs rasterised at the scaled size stay sharp under the scaled matrix.
    with canvas_rtl.upright(x, width) as x:
        gpu.matrix.push()
        gpu.matrix.scale((1 / scale, 1 / scale))
        blf.position(font, x * scale, y * scale, 0)
        blf.color(font, *color)
        blf.draw(font, value)
        gpu.matrix.pop()


def wrap(value, width, size, font, ordered=True):
    """Measured wrapping with source offsets for drafts (``ordered=False``); right-to-left rows come back visual.
    Measured as ``text`` draws (``measure``), so a row that fits when wrapped fits when drawn."""
    return _wrap(value, width, size, font, ordered, _scale)


@lru_cache(maxsize=512)
def _wrap(value, width, size, font, ordered, scale):
    return tuple(canvas_wrap.iter_wrap(value, width, size, font, ordered, scale))


wrap.cache_clear = _wrap.cache_clear


def iter_wrap(value, width, size, font, ordered=True):
    """``canvas_wrap.iter_wrap`` for rows the chat draws, prepared inside or outside its draw: at the chat's scale."""
    return canvas_wrap.iter_wrap(value, width, size, font, ordered, ui_scale())


def _scissor(*rect):
    """``canvas_rtl.scissor`` in window pixels: the canvas lays out unscaled (``_scale``), a scissor is not."""
    return tuple(max(1, int(v * _scale)) if i > 1 else int(v * _scale) for i, v in enumerate(canvas_rtl.scissor(*rect)))


def inside(rect, x, y):
    return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]


def stroke(x1, y1, x2, y2, color=MUTED, thickness=1.2):
    length = math.hypot(x2 - x1, y2 - y1)
    if not length:
        return
    if color[3] < 1:
        gpu.state.blend_set('ALPHA')
    dx, dy = -(y2 - y1) * thickness / length / 2, (x2 - x1) * thickness / length / 2
    points = [(x1 + dx, y1 + dy, 0), (x2 + dx, y2 + dy, 0), (x2 - dx, y2 - dy, 0), (x1 - dx, y1 - dy, 0)]
    batch = batch_for_shader(_shader, 'TRIS', {"pos": points}, indices=[(0, 1, 2), (0, 2, 3)])
    _shader.bind()
    _shader.uniform_float("color", color)
    batch.draw(_shader)


def icon(name, x, y, color=MUTED):
    if name == "plus":
        stroke(x - 5, y, x + 5, y, color, 1.5)
        stroke(x, y - 5, x, y + 5, color, 1.5)
    elif name in {"mic", "approval"}:
        if name == "mic":
            shape(x - 3, y - 1, 6, 10, color, 3)
            shape(x - 1.7, y + 0.2, 3.4, 7.5, CARD, 1.7)
            stroke(x - 5, y + 1, x - 5, y - 2, color)
            stroke(x - 5, y - 2, x - 2, y - 5, color)
            stroke(x - 2, y - 5, x + 2, y - 5, color)
            stroke(x + 2, y - 5, x + 5, y - 2, color)
            stroke(x + 5, y - 2, x + 5, y + 1, color)
            stroke(x, y - 5, x, y - 8, color)
        else:
            for a, b in [((-5, -5), (-5, 5)), ((-5, 5), (2, 7)), ((2, 7), (6, 3)), ((6, 3), (6, -5)), ((6, -5), (-1, -7)), ((-1, -7), (-5, -5)), ((-1, 0), (1, -2)), ((1, -2), (4, 2))]:
                stroke(x + a[0], y + a[1], x + b[0], y + b[1], color)
    elif name == "pencil":
        for a, b in [((-5, -5), (-2, 2)), ((-2, 2), (4, 7)), ((4, 7), (7, 4)), ((7, 4), (1, -2)), ((1, -2), (-5, -5))]:
            stroke(x + a[0], y + a[1], x + b[0], y + b[1], color)
    elif name == "chevron":
        stroke(x - 3, y + 2, x, y - 1, color)
        stroke(x, y - 1, x + 3, y + 2, color)
    elif name == "next":
        stroke(x - 1, y + 3, x + 2, y, color)
        stroke(x + 2, y, x - 1, y - 3, color)
    elif name == "check":
        stroke(x - 4, y, x - 1, y - 3, color, 1.4)
        stroke(x - 1, y - 3, x + 4, y + 4, color, 1.4)
    elif name == "reset":
        for i in range(16):
            a, b = math.radians(65 + i * 18), math.radians(65 + (i + 1) * 18)
            stroke(x + 5 * math.cos(a), y + 5 * math.sin(a), x + 5 * math.cos(b), y + 5 * math.sin(b), color)
        stroke(x - 5, y + 6, x - 5, y + 2, color)
        stroke(x - 5, y + 2, x - 1, y + 2, color)
    elif name == "arrow":
        stroke(x, y - 5, x, y + 5, color, 1.7)
        stroke(x - 4, y + 1, x, y + 5, color, 1.7)
        stroke(x + 4, y + 1, x, y + 5, color, 1.7)
    elif name == "close":
        stroke(x - 3, y - 3, x + 3, y + 3, color)
        stroke(x - 3, y + 3, x + 3, y - 3, color)
    elif name == "fast":
        points = [(x + 1, y + 6, 0), (x - 5, y - 1, 0), (x - 1, y - 1, 0), (x - 2, y - 6, 0), (x + 5, y + 2, 0), (x + 1, y + 2, 0)]
        batch = batch_for_shader(_shader, 'TRIS', {"pos": points}, indices=[(0, 1, 2), (0, 2, 5), (2, 3, 4), (2, 4, 5)])
        _shader.bind()
        _shader.uniform_float("color", color)
        batch.draw(_shader)


def elide(value, width, size=14):
    return canvas_rtl.elide(value, lambda part: measure(part, size), width)


def _flow_wrap(value, width, size):
    return wrap(value, width, size, _font)


def _this_module():
    import sys
    return sys.modules[__name__]


def show_popover(context, kind="controls"):
    """Keep the entire picker in the chat's reserved native editor."""
    from . import console, ui
    from .chat_popovers import model_rows
    if not context.area or not console.is_chat(context.area):
        return False
    controller = ui.get_controller()
    from .chat_settings import clean_options
    for view in _views.values():
        view.pop("popover", None)
    state = _views.setdefault(context.area.as_pointer(), {})
    rows = model_rows(ui.model_choices(controller), clean_options(controller.state.get("options"))["model"], controller.selection_connection_id)
    state["popover"] = {"kind": kind, "session": controller.state["id"],
                        "connection": controller.selection_connection_id, "scroll": 0,
                        "query": "", "collapsed": set(),
                        "cursor": next((i for i, row in enumerate(rows) if row["selected"]), 0)}
    state["focused"] = False
    context.area.tag_redraw()
    return True


def draw_popover(state, controller, width, height):
    from . import ui
    from .chat_popovers import (FOOTER_PAD, effort_choices, effective_effort, effort_name, model_name, model_rows,
                               model_row_rect, popup_layout, slider_geometry, slider_motion, sparkles, supports_fast)
    from .chat_settings import APPROVALS, clean_options, selected_model
    state["popup_hits"] = []
    popup = state.get("popover")
    if not popup:
        return
    if popup["session"] != controller.state["id"] or popup["connection"] != controller.selection_connection_id:
        state.pop("popover", None)
        return
    kind = popup["kind"]
    if kind == "add":   # The composer's + menu (chat_add_menu).
        from . import chat_add_menu, chosen_tools_host
        target = chosen_tools_host.fit_target(ui.main_window())
        return chat_add_menu.draw(_this_module(), state, controller, width, height, chosen_tools_host.shown_name(target))
    if kind == "usage":
        from . import chat_usage
        return chat_usage.draw(_this_module(), state, controller, width, height)
    options = clean_options(controller.state.get("options"))
    model = selected_model(controller)
    fast_supported = supports_fast(model)
    if kind == "fast_confirm" and (not fast_supported or model["id"] != popup.get("confirm_model")):
        kind = popup["kind"] = "controls"
    claude = controller.selection_provider.startswith("claude_")
    choices = ui.model_choices(controller)
    loading, error = ui.model_catalog_notice(controller)
    notice = error or (iface_("Loading connected models…") if loading else iface_("No models available. Refresh to retry.") if not choices else "")
    rows = model_rows(choices, options["model"], controller.selection_connection_id, popup.get("query", ""), popup.get("collapsed", ()))
    popup["cursor"] = max(0, min(popup.get("cursor", 0), len(rows) - 1))
    anchor = state.get("picker_anchor", (width - 210, 28, 130, 28))
    geometry = popup_layout(kind, width, height, anchor, len(choices), popup.get("scroll", 0),
                            controller.busy, bool(notice), claude, rows, search=True, floor=state.get("add_bottom"))
    popup["layout"] = geometry
    popup["scroll"] = geometry.get("scroll", 0)
    x, y, w, h = geometry["rect"]
    state["popup_rect"] = geometry["rect"]
    shape(x, y, w, h, LINE, 15)
    shape(x + 1, y + 1, w - 2, h - 2, POPOVER, 14)
    old_scissor = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(*_scissor(x + 1, y + 1, w - 2, h - 2))

    def hit(rect, action, value=None):
        visible = clip_rect(rect, canvas_rtl.scissor(*(value / _scale for value in gpu.state.scissor_get())))
        visible = clip_rect(visible, (0, 0, width, height)) if visible else None
        if visible:
            state["popup_hits"].append((visible, action, value))

    def centered(value, cx, baseline, size=13, color=TEXT):
        text(value, cx - measure(value, size) / 2, baseline, size, color)

    def hover(rect, action, value=None):
        if popup.get("hover") == (action, value):
            shape(*rect, LINE, 10)

    busy_height = geometry["busy_height"]
    if kind == "models":
        text(iface_("Select model"), x + 12, y + h - 22, 13, MUTED)
        if busy_height:
            text(iface_("Applies to your next message"), x + 12, y + h - 44, 11, MUTED)
        popup["rows"] = rows
        sx, sy, sw, sh = search_rect = geometry["search_rect"]
        shape(sx, sy, sw, sh, BG, 7)
        query = popup.get("query", "")
        text(elide(query or iface_("Search models or providers"), sw - 18, 12), sx + 9, sy + 9, 12, TEXT if query else MUTED)
        hit(search_rect, "popup_search")
        lx, ly, lw, lh = geometry["list_rect"]
        gpu.state.scissor_set(*_scissor(lx, ly, lw, lh))
        for i, row in enumerate(rows):
            rx, ry, rw, rh = rect = model_row_rect(geometry, i)
            if ry + rh <= ly or ry >= ly + lh:
                continue
            if row["kind"] == "provider":
                action, value = "popup_group", row["value"]
                hover(rect, action, value)
                icon("chevron", rx + 11, ry + 14, MUTED)
                text(elide(row["name"], rw - 60, 12), rx + 25, ry + 9, 12, MUTED)
                text(str(row["count"]), rx + rw - 24, ry + 9, 11, MUTED)
                bottom, top = max(ly, ry), min(ly + lh, ry + rh)
                hit((rx, bottom, rw, top - bottom), action, value)
                continue
            action, value = "popup_select_model", row["value"]
            if popup.get("hover") == (action, value) or (popup.get("keyboard") and popup.get("cursor") == i):
                shape(rx, ry + 1, rw, rh - 2, LINE, 10)
            baseline = ry + rh - 22 if row["subtitle"] else ry + 9
            text(elide(row["name"], rw - 40, 13), rx + 8, baseline, 13)
            if row["subtitle"]:
                text(elide(row["subtitle"], rw - 20, 12), rx + 8, baseline - 18, 12, MUTED)
            if row["selected"]:
                icon("check", rx + rw - 15, baseline + 5, TEXT)
            bottom, top = max(ly, ry), min(ly + lh, ry + rh)
            if row["kind"] != "empty":
                hit((rx, bottom, rw, top - bottom), action, value)
        gpu.state.scissor_set(*_scissor(x + 1, y + 1, w - 2, h - 2))
        if geometry["max_scroll"]:
            thumb_h = max(22, lh * lh / geometry["content_height"])
            thumb_y = ly + lh - thumb_h - (lh - thumb_h) * geometry["scroll"] / geometry["max_scroll"]
            shape(x + w - 4, thumb_y, 2, thumb_h, MUTED, 1)
        footer, pad = (x + 5, y + 3 + FOOTER_PAD, w - 10, 26), FOOTER_PAD   # Room under the last row, as above.
        hover(footer, "popup_refresh")
        icon("reset", x + 17, y + 16 + pad, MUTED)
        text(iface_("Refreshing…") if loading else iface_("Refresh models"), x + 30, y + 11 + pad, 12, MUTED)
        hit(footer, "popup_refresh")
        if claude:
            footer = (x + 5, y + 31 + pad, w - 10, 26)
            hover(footer, "popup_custom_model")
            text(iface_("Enter native model ID…"), x + 13, y + 40 + pad, 12, MUTED)
            hit(footer, "popup_custom_model")
        if notice:
            bottom = y + 35 + pad + (28 if claude else 0)
            for i, (line, _) in enumerate(wrap(str(notice), w - 24, 11, _font)[:2]):
                text(line, x + 12, bottom + 16 - i * 14, 11, MUTED)
    elif kind == "controls":
        choices = effort_choices(model)
        current = effective_effort(model, options["effort"])
        title = effort_name(current) if choices else iface_("Reasoning")
        centered(title, x + w / 2, y + h - 26, 14, BLUE if choices else MUTED)
        # Lightning is the Fast toggle. Neither effort nor reset enables it.
        if fast_supported:
            icon("fast", x + 22, y + h - 20, BLUE if options["fast"] else MUTED)
            hit((x + 8, y + h - 34, 28, 28), "popup_fast")
        icon("reset", x + w - 22, y + h - 20, MUTED)
        if choices:
            hit((x + w - 36, y + h - 34, 28, 28), "popup_reset")
        label = elide(model_name(model) if model else options["model"] or iface_("Select model"), w - 50, 12)
        label_width = measure(label, 12)
        text(label, x + (w - label_width - 10) / 2, y + h - 45, 12, MUTED)
        icon("next", x + (w + label_width) / 2 + 3, y + h - 41, MUTED)
        hit((x + 10, y + h - 56, w - 20, 24), "popup_models")
        if busy_height:
            centered(iface_("Applies to your next message"), x + w / 2, y + h - 67, 11, MUTED)
        track = geometry["slider"]
        tx, ty, tw, th = track
        popup["animated"] = False
        if choices:
            reduce_motion = getattr(bpy.context.preferences.view, "use_reduce_motion", False)
            now = time.monotonic()
            slider = slider_geometry(track, choices, current)
            ratio = slider_motion(popup.get("slider_ratio"), slider["target"], now - popup.get("slider_time", now),
                                  reduce_motion or popup.get("drag_slider", False))
            popup.update(slider_ratio=ratio, slider_time=now, animated=not reduce_motion)
            slider = slider_geometry(track, choices, current, ratio)
            popup["slider_draw"] = slider
            shape(*track, LINE, th / 2)
            shape(*slider["fill"], BLUE, th / 2)
            start, length = tx + 14, tw - 28
            for i, _ in enumerate(choices):
                point = start + (i / (len(choices) - 1) if len(choices) > 1 else 0.5) * length
                color = (0.8, 0.89, 1, 0.8) if point < slider["thumb_x"] else MUTED
                shape(point - 1, ty + th / 2 - 1, 2, 2, color, 1)
            for sx, sy, alpha in sparkles(track, slider["fill"][2], 0 if reduce_motion else now):
                color = (0.83, 0.91, 1, alpha)
                stroke(sx - 1.2, sy, sx + 1.2, sy, color, 1.1)
                stroke(sx, sy - 1.2, sx, sy + 1.2, color, 1.1)
            shape(*slider["thumb"], TEXT, th / 2)
            hit(track, "popup_slider")
        else:
            centered(iface_("Managed by this provider"), x + w / 2, ty + 9, 12, MUTED)
    elif kind == "fast_confirm":
        text(iface_("Enable Fast mode?"), x + 14, y + h - 25, 14)
        explanation = (iface_("Claude Fast uses paid usage credits, not your subscription's included usage. Reasoning effort stays the same.") if claude
                       else iface_("Fast uses your connection's priority tier. Costs or quota usage can be higher. Reasoning effort stays the same."))
        for i, (line, _) in enumerate(wrap(explanation, w - 28, 12, _font)):
            text(line, x + 14, y + h - 50 - i * 18, 12, MUTED)
        if busy_height:
            text(iface_("Applies to your next message"), x + 14, y + 54, 11, MUTED)
        button_width = (w - 38) / 2
        cancel = (x + 14, y + 13, button_width, 29)
        confirm = (x + 24 + button_width, y + 13, button_width, 29)
        shape(*cancel, LINE, 10)
        shape(*confirm, BLUE, 10)
        centered(iface_("Cancel"), cancel[0] + button_width / 2, y + 22, 12)
        centered(iface_("Enable Fast"), confirm[0] + button_width / 2, y + 22, 12)
        hit(cancel, "popup_cancel_fast")
        hit(confirm, "popup_enable_fast", popup.get("confirm_model"))
    elif kind == "approval":
        text(iface_("Approvals"), x + 13, y + h - 25, 13, MUTED)
        for i, (mode, (name, _)) in enumerate(APPROVALS.items()):
            rect = (x + 5, y + h - 69 - i * 34, w - 10, 32)
            hover(rect, "popup_approval", mode)
            text(elide(iface_(name), w - 50, 13), x + 13, rect[1] + 10, 13)
            if options["approval"] == mode:
                icon("check", x + w - 21, rect[1] + 15, TEXT)
            hit(rect, "popup_approval", mode)
        footer = iface_("Scripts run without asking in Auto.") if controller.always_allow_scripts else iface_("General Python always requires review.")
        text(elide(footer, w - 26, 11), x + 13, y + 19, 11, MUTED)  # Always allow scripts (Preferences) in an Auto chat.
        if busy_height:
            text(iface_("Applies to queued / future changes"), x + 13, y + 36, 11, MUTED)
    gpu.state.scissor_set(*old_scissor)
    gpu.state.scissor_test_set(False)
    if kind == "controls" and fast_supported and popup.get("hover") == ("popup_fast", None) and time.monotonic() - popup.get("hover_time", 0) > 0.45:
        hint_y = y + h + 8 if y + h + 48 < height else max(8, y - 48)
        hint_width = min(w, 220)
        shape(x, hint_y, hint_width, 40, LINE, 10)
        text(iface_("Fast mode · On") if options["fast"] else iface_("Fast mode · Off"), x + 10, hint_y + 24, 12)
        text(iface_("Priority tier · may cost more"), x + 10, hint_y + 9, 10, MUTED)
        popup["tooltip_shown"] = True


def draw():
    """The chat region's draw handler. While a turn waits, a frame the marble timer asked for redraws only the
    marks over the chat's kept picture (``marble_frames``); every other draw is the full one, kept for it."""
    from . import console, marble_frames
    area, region = bpy.context.area, bpy.context.region
    if not _enabled or not area or not region or not console.is_chat(area):
        return
    if marble_frames.reuse(area, region):
        return
    with marble_frames.keep(area, region):
        _draw_chat()


def _draw_chat():
    global last_error, draw_count, draw_seconds
    from . import console, ui
    from .chat_settings import clean_options, selected_model
    from .chat_layout import composer_layout
    from .chat_popovers import effective_effort, effort_name, model_name, supports_fast
    global _scale
    area, region = bpy.context.area, bpy.context.region
    if not _enabled or not area or not region or not console.is_chat(area):
        return
    _scale = ui_scale()
    started = time.perf_counter()
    gpu.matrix.push()
    gpu.matrix.scale((_scale, _scale))  # Everything below lays out in unscaled pixels; scissors are scaled back.
    try:
        controller = ui.get_controller()
        from .agent_workspace_ui import native_cards, control_lines, preview_available, draw_native_preview
        workspace_cards = native_cards(controller)
        active_native_jobs = [card for card in workspace_cards if card.get("state") != "PREVIEW"]
        width, height = region.width / _scale, region.height / _scale
        canvas_rtl.begin(width)  # Mirrored in a right-to-left language; ``end`` in ``finally``.
        size = max(12, int(area.spaces.active.font_size * 0.8))
        line_height = size + 8
        state = _views.setdefault(area.as_pointer(), {})
        state["hits"] = []
        state["message_rects"] = {}
        expanded = state.setdefault("expanded", set())
        line = area.spaces.active.history[-1] if area.spaces.active.history else None
        draft = line.body if line else ""
        draft_lines = wrap(draft, max(40, width - 56), size, _font, ordered=False)
        attachment_rows = math.ceil((len(controller.attachments) + len(getattr(controller, "chosen_tools", ()) or ())) / 2)
        geometry = composer_layout(width, len(draft_lines), attachment_rows)
        cx, composer_bottom, cw, composer_height = geometry["rect"]
        # Line spacing follows the reference, but respects native text-size changes.
        composer_height += max(0, min(8, len(draft_lines)) - 1) * (line_height - 22)
        changed, added, removed = change_counts(controller.state["messages"])
        changed_files, written_files, deleted_files = file_counts(controller.state["messages"])
        message_bottom = composer_bottom + composer_height + (66 if changed or changed_files else 28)
        gpu.state.blend_set('ALPHA')
        shape(0, 0, width, height, BG)
        blocks = []
        for index, message in enumerate(reply_overlay.shown(controller, controller.state["messages"])[-160:]):
            role = message["role"]
            identity = message.get("call_id") or (controller.state["id"], id(message))
            if role == "tool" and message["text"].startswith("Approval required"):
                continue
            if role == "user":
                rows = wrap(console.display_text(message), max(40, geometry["bubble_max_width"] - 32), size, _font)
                bubble_width = min(geometry["bubble_max_width"], max(measure(value, size) for value, _ in rows) + 32)
                blocks.append(("user", message, rows, len(rows) * line_height + 24 + 40, identity, bubble_width))
            elif role in {"activity", "thinking"}:  # Progress rows and work groups: chat_rows.
                blocks.append(chat_rows.activity_block(message, role, identity, expanded, width, size, line_height, state))
            elif role in {"system", "tool"} and (block := chat_rows.notice_block(message, identity, expanded, width, size, line_height)):
                blocks.append(block)
            elif role in {"system", "tool"}:
                # Native notices and legacy tools share Codex's quiet activity rows.
                value = console.display_text(message)
                rows = wrap(value, max(40, width - 60), size, _font)
                blocks.append(("notice", message, rows, len(rows) * line_height + 24, identity, 0))
            elif role == "assistant" and not message.get("text"):
                continue  # Nothing to draw (a streamed reply held back so far, reply_overlay).
            elif role == "error":  # A notice card, never a reply (chat_notices).
                blocks.append(chat_notices.block(message, identity, message is controller.state["messages"][-1], ui, controller, width, size, line_height, expanded))
            else:  # Markdown laid out once per reply text and width (chat_replies).
                blocks.append(chat_replies.block(message, identity, console.display_text(message), width, size, line_height))
        blocks = marble_draw.with_hero(chat_rows.group_blocks(blocks, expanded, controller.busy), controller)
        pending = next((c for c in tuple(controller.pending.values()) if c.approval), None)
        from . import flow_chat
        if pending and flow_chat.is_run_review(pending):
            details = ("approval", pending.id) in expanded
            _, card_height = flow_chat.approval_layout(pending, 16, 0, width - 32, _flow_wrap, line_height, details)
            blocks.append(("flow_approval", {"call": pending, "details": details}, (), card_height + 12, pending.id, 0))
        elif pending:
            card = chat_card_draw.approval_block(controller, pending, area, width, size, line_height, expanded, state)
            if card:
                blocks.append(card)
        elif controller.busy and not active_native_jobs:
            blocks.append(chat_rows.live_block(controller, width, size))  # With a plan's provisional steps (plan_steps).
        for descriptor in workspace_cards:
            if descriptor.get("state") == "PREVIEW":
                key = ("native_preview", descriptor["preview_id"])
                blocks.append(chat_card_draw.preview_block(descriptor, key, width, size, line_height, expanded,
                                                           preview_available(descriptor)))
            else:
                blocks.append(chat_card_draw.job_block(descriptor, width, size, line_height))
        reconnect = chat_welcome.reconnect_height(ui, width, size) if blocks and not ui.connection_available() else 0
        message_bottom += reconnect  # The reconnect row sits between the transcript and the composer, never over rows.
        total = sum(block[3] for block in blocks)
        from . import chat_usage
        bar = chat_usage.bar_height(controller, blocks, connected := ui.connection_available())  # The sticky bar's strip.
        visible = max(40, height - bar - message_bottom - 20)
        identity = controller.state["id"]
        scroll = _scroll.get(identity, 0)
        previous = state.get("content_height", total) if state.get("session") == identity else total
        if scroll > 0:
            scroll += total - previous
        scroll = max(0, min(scroll, max(0, total - visible)))
        _scroll[identity] = scroll
        state.update(session=identity, content_height=total, max_scroll=max(0, total - visible))
        top = message_bottom + max(total, visible) - scroll
        old_scissor = gpu.state.scissor_get()
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(*_scissor(0, int(message_bottom), width, height - bar - int(message_bottom)))
        message_viewport = (0, int(message_bottom), width, max(0, height - bar - int(message_bottom)))
        state["message_viewport"] = message_viewport
        for role, message, rows, block_height, key, bubble_width in blocks:
            bottom = top - block_height
            if bottom < height - bar and top > message_bottom:
                if role == "user":
                    x = width - 16 - bubble_width
                    shape(x, bottom + 40, bubble_width, block_height - 40, USER, 18)
                    y = top - line_height - 8
                    for value, _ in rows:
                        text(value.rstrip(), x + 16, y, size)
                        y -= line_height
                    if message.get("checkpoint"):
                        text(iface_("Restore snapshot"), width - 126, bottom + 15, 11, MUTED)
                        state["hits"].append(((width - 142, bottom + 4, 126, 28), "restore", message["checkpoint"]))
                elif role in chat_rows.ROLES:
                    chat_rows.draw(role, message, rows, block_height, key, top, bottom, width, size, line_height, state)
                elif role in {"native_preview", "native_job", "approval"}:
                    chat_card_draw.draw(role, message, rows, key, top, bottom, {
                        "state": state, "controller": controller, "width": width, "size": size, "expanded": expanded,
                        "line_height": line_height, "band": (message_bottom, height), "draw_image": draw_native_preview})
                elif role == "flow_approval":
                    from . import flow_chat, studio
                    ops, _ = flow_chat.approval_layout(message["call"], 16, top, width - 32, _flow_wrap, line_height, message["details"])
                    flow_chat.draw(ops, state["hits"], elide, shape, text, studio.thumbnail)
                elif role == "reply":
                    chat_replies.draw(state, message, key, top, bottom, width, size, line_height, (message_bottom, height))
                elif role == "notice_card":
                    chat_notices.draw(state, ui, controller, message, key, top, bottom, width, size, line_height)
                else:  # Native notices and legacy tools (system and tool rows): quiet activity rows.
                    icon("pencil", 22, top - 15)
                    y = top - line_height
                    for value, offset in rows:
                        text(value.rstrip(), 40, y, size, MUTED)
                        y -= line_height
                    if message.get("restore_checkpoint"):
                        text(iface_("Undo restore"), 40, bottom + 4, 11, MUTED)
                        state["hits"].append(((32, bottom, 116, 24), "restore", message["restore_checkpoint"]))
            top = bottom
        chat_scrollbar.draw(state, scroll)
        state["hits"] = clipped_hits(state["hits"], message_viewport)
        state["message_rects"] = {key: visible for key, rect in state["message_rects"].items()
            if (visible := clip_rect(rect, message_viewport)) is not None}
        gpu.state.scissor_set(*old_scissor)
        gpu.state.scissor_test_set(False)
        if bar:
            chat_usage.draw_bar_safely(_this_module(), state, controller, width, height, connected)
        waiting = not (connected or blocks)  # No account: the bar still navigates (+); its field waits (1.0.5).
        # Connect card, examples or a reconnect row; never Preferences by itself.
        if not blocks:
            chat_welcome.draw_empty(state, ui, width, height - bar, composer_bottom + composer_height + 8, size, connected)
        elif not connected:
            chat_welcome.draw_reconnect_row(state, ui, width, message_bottom - reconnect + 8, size)
        if changed or changed_files:
            chat_card_draw.draw_changes_pill(state, width, composer_bottom + composer_height + 8, changed, changed_files,
                                             added + written_files, removed + deleted_files)
        if not waiting and state.pop("composer_hidden", False):
            state["focus_request"] = state["focused"] = True  # The field takes the keyboard once it can be used.
        if waiting:
            state["composer_hidden"] = True   # Nothing to type into yet: Tab and Return choose a connect option.
        chat_composer.draw(state, ui, controller, geometry=geometry, composer_bottom=composer_bottom,
                           composer_height=composer_height, line=line, draft=draft, draft_lines=draft_lines, size=size,
                           line_height=line_height, width=width, attachment_rows=attachment_rows, connected=connected,
                           waiting=waiting)
        _draw_blocked(state, width, composer_bottom + composer_height + 8)
        state["geometry"] = geometry
        state["hits"] = clipped_hits(state["hits"], (0, 0, width, height))
        state["hit_region"] = (region.width, region.height)
        canvas_controls.draw_canvas(state, width)
        draw_popover(state, controller, width, height)
        gpu.state.blend_set('NONE')
        draw_count += 1
        last_error = None
    except Exception as error:
        last_error = str(error)
        # A failed/partial redraw has no trustworthy clickable geometry.
        state = _views.get(area.as_pointer(), {})
        state["hits"], state["popup_hits"], state["message_rects"] = [], [], {}
        state.pop("hit_region", None)
        # An open picker that failed to draw would take every click while showing nothing: close it, so the next
        # draw is the chat without it and its controls work.
        state.pop("popover", None)
        gpu.state.blend_set('NONE')
        gpu.state.scissor_test_set(False)
    finally:
        canvas_rtl.end()
        gpu.matrix.pop()
        _scale = 1.0
        draw_seconds = time.perf_counter() - started


def _draw_blocked(state, width, bottom):
    """The notice for a click another tool held (``chat_start_states.blocked_card``), resting on ``bottom``."""
    from . import chat_start_states, ui_kit_status
    tools = chat_start_states.blocked_shown(state.get("blocked"), time.monotonic())
    state["drawn_blocked"] = tools  # For window fixtures.
    if not tools:
        return
    names = [bpy.app.translations.pgettext_iface(tool, "Operator") for tool in tools]  # Blender's own names.
    card = chat_start_states.blocked_card(names)
    inner = max(120, width - 32)
    height, _ = ui_kit_status.notice(sys.modules[__name__], 16, 0, inner, 1.0, None, False, **card)
    ui_kit_status.notice(sys.modules[__name__], 16, bottom + height, inner, 1.0, None, True, **card)


def dispatch_action(context, controller, action, value):
    from . import console
    from .chat_settings import clean_options
    state = _views.get(context.area.as_pointer(), {})
    if action == "copy":
        context.window_manager.clipboard = value
    elif action == "send":
        console.execute(bpy.context)
    elif action in {"stop", "stop_native_job"}:
        bpy.ops.angelo.stop(session_id=controller.state["id"] if action == "stop" else value)
    elif action == "accept_native_preview":
        bpy.ops.angelo.accept_native_preview('INVOKE_DEFAULT', session_id=value[0], preview_id=value[1])
    elif action == "discard_native_preview":
        bpy.ops.angelo.discard_native_preview(session_id=value[0], preview_id=value[1])
    elif action == "approve":
        from .approval_review import approve_current_review
        if approve_current_review(controller, value, state):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=value)
    elif action == "decline":
        bpy.ops.angelo.decline(call_id=value)
    elif action == "settings":
        bpy.ops.angelo.settings()
    elif action.startswith("welcome_"):
        chat_welcome.dispatch(context, controller, state, action, value)
    elif action in {"model", "fast"}:
        # The composer pill opens the compact reasoning/Fast card; its model
        # breadcrumb opens the separate Codex-style model list.
        from . import ui
        # Otherwise the chat's card offers Connect; a connection that can't be prepared says why in the chat.
        if chat_welcome.open_models(ui, controller):
            controller = ui.get_controller()
            show_popover(context, "controls")
            if action == "fast":
                was_enabled = clean_options(controller.state.get("options"))["fast"]
                dispatch_popup(context, controller, state, "popup_fast")
                if was_enabled:
                    state.pop("popover", None)
    elif action == "approval":
        show_popover(context, "approval")
    elif action == "attach":   # The + menu: attachments and tools the person chooses (chat_add_menu).
        show_popover(context, "add")
    elif action == "usage":   # The usage meter's card (chat_usage).
        show_popover(context, "usage")
    elif action == "remove_chosen":
        controller.chosen_tools = [c for i, c in enumerate(getattr(controller, "chosen_tools", []) or []) if i != value]
        controller.version += 1
    elif action == "remove_reference":
        bpy.ops.angelo.remove_reference(index=value)
    elif action == "voice":
        bpy.ops.angelo.voice('INVOKE_DEFAULT')
    elif action == "expand":
        expanded = state.setdefault("expanded", set())
        expanded.remove(value) if value in expanded else expanded.add(value)
    elif action == "changes":
        bpy.ops.angelo.changes('INVOKE_DEFAULT')
    elif action == "review":
        expanded = state.setdefault("expanded", set())
        expanded.remove(("approval", value)) if ("approval", value) in expanded else bpy.ops.angelo.review_tool(call_id=value)
    elif action == "restore":
        bpy.ops.angelo.restore_checkpoint('INVOKE_DEFAULT', checkpoint_id=value)
    elif action == "flow_details":
        expanded = state.setdefault("expanded", set())
        expanded.symmetric_difference_update({("approval", value)})
    elif action.startswith(("flow_", "job_", "script_")):
        try:
            chat_cards.dispatch(context, controller, state, action, value)
        except (ValueError, OSError, RuntimeError) as error:
            controller.status = str(error)[:200]
    elif action == "url":
        from .markdown_text import safe_url
        if safe_url(value):
            bpy.ops.wm.url_open(url=value)


def dispatch_popup(context, controller, state, action, value=None):
    from .chat_settings import clean_options, selected_model
    from .chat_popovers import effective_effort
    popup = state.get("popover")
    if not popup:
        return
    if action.startswith("popup_add_"):
        from . import chat_add_menu
        chat_add_menu.dispatch(context, controller, state, action, value)
    elif action.startswith("popup_usage_"):
        from . import chat_usage
        chat_usage.dispatch(context, controller, state, action, value)
    elif action == "popup_models":
        show_popover(context, "models")
    elif action == "popup_select_model":
        arguments = {"connection_id": value[0], "model": value[1]} if isinstance(value, (tuple, list)) else {"model": value}
        if 'FINISHED' in bpy.ops.angelo.select_model(**arguments):
            state.pop("popover", None)
    elif action == "popup_group":
        if popup.get("query"):
            return  # Search expands matching groups; don't hide the matches.
        collapsed = popup.setdefault("collapsed", set())
        collapsed.remove(value) if value in collapsed else collapsed.add(value)
        popup.update(scroll=0, cursor=0, hover=None)
        popup.pop("layout", None)
        popup.pop("pending", None)
        state["popup_hits"] = []
    elif action == "popup_search":
        popup["search_focus"] = True
    elif action == "popup_refresh":
        bpy.ops.angelo.refresh_models()
    elif action == "popup_custom_model":
        state.pop("popover", None)
        bpy.ops.angelo.custom_model('INVOKE_DEFAULT')
    elif action == "popup_effort":
        bpy.ops.angelo.set_effort(effort=value)
    elif action == "popup_reset":
        model = selected_model(controller)
        if model:
            bpy.ops.angelo.set_effort(effort=effective_effort(model, model["default_effort"]))
    elif action == "popup_fast":
        options = clean_options(controller.state.get("options"))
        model = selected_model(controller)
        if options["fast"]:
            bpy.ops.angelo.toggle_fast('EXEC_DEFAULT')
        elif model and model["fast_tier"]:
            popup.update(kind="fast_confirm", confirm_model=model["id"], hover=None, keyboard=False)
    elif action == "popup_cancel_fast":
        popup.update(kind="controls", hover=None)
    elif action == "popup_enable_fast":
        model = selected_model(controller)
        # Bind billing confirmation to exactly this chat, connection and model.
        # A catalog/account/session change may invalidate it, never widen it.
        if (popup["kind"] == "fast_confirm" and popup["session"] == controller.state["id"]
                and popup["connection"] == controller.selection_connection_id and model
                and model["id"] == value == popup.get("confirm_model") and model["fast_tier"]
                and not clean_options(controller.state.get("options"))["fast"]):
            bpy.ops.angelo.toggle_fast('EXEC_DEFAULT')
        popup.update(kind="controls", hover=None)
    elif action == "popup_approval":
        if 'FINISHED' in bpy.ops.angelo.set_approval(mode=value):
            state.pop("popover", None)


def popover_event(context, controller, state, event, x, y):
    """Modal input shield: popup events must never send/type/scroll behind it."""
    from .chat_settings import clean_options, selected_model
    from .chat_popovers import effort_choices, effective_effort, reveal_model, slider_value
    popup = state.get("popover")
    if not popup:
        return False

    def slide():
        model = selected_model(controller)
        choice = slider_value(popup["layout"]["slider"], x, effort_choices(model))
        if choice and choice != clean_options(controller.state.get("options"))["effort"]:
            dispatch_popup(context, controller, state, "popup_effort", choice)

    if event.type == 'MOUSEMOVE':
        if popup.get("drag_slider") and popup["kind"] == "controls":
            slide()
        else:
            hovered = next(((action, value) for rect, action, value in reversed(state.get("popup_hits", [])) if inside(rect, x, y)), None)
            if hovered != popup.get("hover"):
                popup["hover"] = hovered
                popup["hover_time"] = time.monotonic()
                popup["tooltip_shown"] = False
                popup["keyboard"] = False
        return True
    if event.type in scroll_input.SCROLL_EVENTS:
        if popup["kind"] in {"models", "add"} and "layout" in popup:
            # Measured down from the list's top: the view moving up is a smaller scroll.
            delta = -scroll_input.view_up(event, 58, ui_scale())
            popup["scroll"] = max(0, min(popup["layout"]["max_scroll"], popup["scroll"] + delta))
            popup["hover"] = None
        return True
    if event.type == 'LEFTMOUSE':
        if event.value == 'PRESS':
            if not inside(state.get("popup_rect", (0, 0, 0, 0)), x, y):
                state.pop("popover", None)
                return True  # Dismiss only; no surprise Send or scene edit.
            item = next((item for item in reversed(state.get("popup_hits", [])) if inside(item[0], x, y)), None)
            popup["pending"] = item
            if item and item[1] == "popup_slider":
                popup["drag_slider"] = True
                popup["pending"] = None
                slide()
        elif event.value == 'RELEASE':
            if popup.pop("drag_slider", False):
                slide()
            item = popup.pop("pending", None)
            if item and release_active(item, state.get("popup_hits", []), x, y):
                dispatch_popup(context, controller, state, item[1], item[2])
        return True
    if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
        state.pop("popover", None)
        return True
    if event.type == 'ESC' and event.value == 'PRESS':
        if popup["kind"] == "fast_confirm":
            popup.update(kind="controls", hover=None)
        elif popup["kind"] == "models" and popup.get("query"):
            popup.update(query="", scroll=0, cursor=0, pending=None, hover=None)
            popup.pop("layout", None)
            popup["rows"] = []
            state["popup_hits"] = []
        else:
            state.pop("popover", None)
        return True
    if event.value == 'PRESS':
        if popup["kind"] == "add":
            from . import chat_add_menu
            row = chat_add_menu.key(popup, event)
            if row is not None:
                dispatch_popup(context, controller, state, row["action"], row["value"])
        elif popup["kind"] == "models":
            from . import ui
            from .chat_popovers import model_rows

            def search(value):
                popup.update(query=value[:80], scroll=0, keyboard=False, hover=None, pending=None, search_all=False)
                popup["rows"] = model_rows(ui.model_choices(controller), clean_options(controller.state.get("options"))["model"],
                                           controller.selection_connection_id, popup["query"], popup.get("collapsed", ()))
                popup["cursor"] = next((i for i, row in enumerate(popup["rows"]) if row["kind"] in {"model", "default"}), 0)
                popup.pop("layout", None)
                state["popup_hits"] = []

            modifier = event.ctrl or event.oskey
            if modifier and event.type == 'A':
                popup["search_all"] = True
                return True
            if modifier and event.type == 'V':
                search(("" if popup.get("search_all") else popup.get("query", "")) + context.window_manager.clipboard)
                return True
            if event.type in {'BACK_SPACE', 'DEL'}:
                search("" if modifier or popup.get("search_all") or event.type == 'DEL' else popup.get("query", "")[:-1])
                return True
            if event.unicode and event.unicode.isprintable() and not modifier:
                search(("" if popup.get("search_all") else popup.get("query", "")) + event.unicode)
                return True
            rows = popup.get("rows", [])
            if rows and event.type in {'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'}:
                cursor = popup.get("cursor", 0)
                cursor = 0 if event.type == 'HOME' else len(rows) - 1 if event.type == 'END' else max(0, min(len(rows) - 1, cursor + (-1 if event.type == 'UP_ARROW' else 1)))
                popup.update(cursor=cursor, keyboard=True, hover=None)
                if "layout" in popup:
                    popup["scroll"] = reveal_model(popup["layout"], cursor)
            elif rows and event.type in {'RET', 'NUMPAD_ENTER'}:
                row = rows[min(popup.get("cursor", 0), len(rows) - 1)]
                if row["kind"] != "empty":
                    dispatch_popup(context, controller, state, "popup_group" if row["kind"] == "provider" else "popup_select_model", row["value"])
        elif popup["kind"] == "controls" and event.type in {'LEFT_ARROW', 'RIGHT_ARROW', 'HOME', 'END'}:
            model = selected_model(controller)
            choices = effort_choices(model)
            if choices:
                index = choices.index(effective_effort(model, clean_options(controller.state.get("options"))["effort"]))
                index = 0 if event.type == 'HOME' else len(choices) - 1 if event.type == 'END' else max(0, min(len(choices) - 1, index + (-1 if event.type == 'LEFT_ARROW' else 1)))
                dispatch_popup(context, controller, state, "popup_effort", choices[index])
        # Swallow typing/Enter while a picker is open, especially during billing
        # confirmation. Enable Fast requires its explicit button, not Enter.
        return True
    return True


def _kept_alive(modal):
    """One failed event never ends the chat's input. Blender removes a Python modal that raises without calling its
    cancel, so the chat would stay drawn and dead until a restart; the event is logged and passed on instead."""
    @functools.wraps(modal)
    def kept(self, context, event):
        try:
            return modal(self, context, event)
        except Exception:
            traceback.print_exc()
            return {'PASS_THROUGH'}
    return kept


class ANGELO_OT_chat_input(bpy.types.Operator):
    bl_idname = "angelo.chat_input"
    bl_label = "Native Chat Input"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self.input_generation = _input_generation
        self.window_pointer = context.window.as_pointer()
        self.selected_all = False
        self.anchor = None
        self.dragging = False
        self.session = None
        self.pending_click = None
        self.focused_area = context.area.as_pointer()
        _modals.add(self.window_pointer)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        if getattr(self, "input_generation", _input_generation) == _input_generation:
            _modals.discard(self.window_pointer)

    @_kept_alive
    def modal(self, context, event):
        from . import console, ui
        if not _enabled or getattr(self, "input_generation", _input_generation) != _input_generation:
            self.cancel(context)
            return {'CANCELLED', 'PASS_THROUGH'}  # A retired handler: its last event belongs to what is below.
        window = context.window
        if not window or window.as_pointer() != self.window_pointer:
            return {'PASS_THROUGH'}
        ui.team_account_ui.note_window_event(event.type)  # Focus coming back re-checks the team's providers.
        from . import marble_host
        marble_host.note_event(event.type, event.value)  # The marble wait pauses while the person navigates or drags.
        for chat in [a for a in window.screen.areas if console.is_chat(a) and _views.get(a.as_pointer(), {}).pop("focus_request", False)]:
            self.focused_area, _views[chat.as_pointer()]["focused"] = chat.as_pointer(), True  # The composer appeared.
        from .input_policy import canvas_blocking, edits_field, follows_pointer, moves_focus, overlay_passes
        if overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}  # Timers, window events and the quit key, even while a picker is open.
        blocking = canvas_blocking(window)
        if blocking:
            self.dragging = False
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                note_blocked_click(window, event, blocking)  # Never a silent click: the chat says what holds it.
            return {'PASS_THROUGH'}
        clear_blocked_notice(window)
        if 'MOUSE' not in event.type and over_overlay(window, event):
            return {'PASS_THROUGH'}  # Keys over the welcome or a card are theirs (Tab, Return, Esc, a card's field).
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.dragging = False
        mouse_event = 'MOUSE' in event.type or event.type in {'TRACKPADPAN', 'TRACKPADZOOM', 'EVT_DROP'}
        popup_area = next((a for a in window.screen.areas if console.is_chat(a)
                           and _views.get(a.as_pointer(), {}).get("popover")), None)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.pending_click = None
            self.focused_area = None
            for view in _views.values():
                view["focused"] = False
            if popup_area:
                popup_region = next((r for r in popup_area.regions if r.type == 'WINDOW'), None)
                if not popup_region or not (popup_region.x <= event.mouse_x < popup_region.x + popup_region.width
                                            and popup_region.y <= event.mouse_y < popup_region.y + popup_region.height):
                    _views[popup_area.as_pointer()].pop("popover", None)
                    popup_area.tag_redraw()
                    popup_area = None
        area = next((a for a in window.screen.areas if console.is_chat(a)
                     and a.x <= event.mouse_x < a.x + a.width and a.y <= event.mouse_y < a.y + a.height), None)
        if not mouse_event and popup_area:
            area = popup_area  # A picker keeps keyboard focus even off hover.
        over_chat = any(console.is_chat(a) and a.x <= event.mouse_x < a.x + a.width and a.y <= event.mouse_y < a.y + a.height
                        for a in window.screen.areas)
        if not over_chat and moves_focus(event.type, event.value):
            self.focused_area = None  # Orbiting, scrolling or a menu click elsewhere takes keyboard focus there.
            for view in _views.values():
                view["focused"] = False
        if not over_chat and not popup_area and (follows_pointer(event.type) or moves_focus(event.type, event.value)):
            return {'PASS_THROUGH'}  # Never take view navigation away from the editor under the pointer.
        if area is None and not mouse_event and self.focused_area:
            area = next((a for a in window.screen.areas if a.as_pointer() == self.focused_area and console.is_chat(a)), None)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
        if not region or (mouse_event and not (region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height)):
            if event.type == 'MOUSEMOVE':
                canvas_controls.leave_areas(window, _views, console.is_chat)
            return {'PASS_THROUGH'}
        state = _views.get(area.as_pointer(), {})
        state["input_serial"] = state.get("input_serial", 0) + 1  # This chat's picture may change (marble_frames).
        scale = ui_scale()  # Hits are laid out unscaled (draw).
        x, y = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width) / scale, (event.mouse_y - region.y) / scale
        controller = ui.get_controller()
        if event.type == 'EVT_DROP':
            state.pop("popover", None)
            # Installed-binary compatibility: stock Console inserts a quoted
            # dropped path. Observe ONLY a real native drop event, then turn
            # that inserted path into an attachment and restore the draft.
            # The compiled fork uses the native multi-file FileHandler route.
            defer_console_drop(window, area, controller)
            return {'PASS_THROUGH'}
        if self.session != controller.state["id"]:
            self.session = controller.state["id"]
            self.pending_click = None
            self.anchor, self.selected_all = None, False
            state["selection"] = None
        popup = state.get("popover")
        if popup and (popup["session"] != controller.state["id"] or popup["connection"] != controller.selection_connection_id):
            state.pop("popover", None)
        if state.get("popover"):
            self.pending_click = None
            self.dragging = False
            with context.temp_override(window=window, area=area, region=region):
                popover_event(bpy.context, controller, state, event, x, y)
            if not state.get("popover"):
                self.focused_area = area.as_pointer()
                state["focused"] = True
            area.tag_redraw()
            return {'RUNNING_MODAL'}

        def point_to_caret():
            rows = state.get("draft_rows", [])
            if not rows:
                return 0
            value, offset, _ = min(rows, key=lambda row: abs(row[2] - y))
            size, drawn = state.get("font_size", 14), state.get("text_scale", scale)  # As the composer drew it.
            plain = canvas_rtl.pointer_x(x, region.width / scale)  # The text field is never mirrored.
            return offset + chat_composer.column_at(value, plain, size, drawn)

        if event.type == 'MOUSEMOVE' and self.dragging and area.spaces.active.history:
            line = area.spaces.active.history[-1]
            caret = point_to_caret()
            set_draft(line, line.body, caret)
            state["selection"] = tuple(sorted((self.anchor, caret)))
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if chat_scrollbar.handle(event, state, area, x, y, controller.state["id"], _scroll, scale):  # Bar, wheel, trackpad.
            self.pending_click = None
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            hovered = next((key for key, rect in state.get("message_rects", {}).items() if inside(rect, x, y)), None)
            if hovered != state.get("hover_message"):
                state["hover_message"] = hovered
                area.tag_redraw()
            hits = state.get("hits", []) if state.get("hit_region") == (region.width, region.height) else []
            if canvas_controls.pointer(window, area.tag_redraw, state, x, y, hits):
                return {'RUNNING_MODAL'}  # Unconsumed, the console would put its text cursor back.
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.dragging:
            self.dragging = False
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            # Composer input owns its visible surface, not transcript controls
            # that happen to have obsolete geometry beneath it.
            if inside(state.get("composer", (0, 0, 0, 0)), x, y) and area.spaces.active.history:
                line = area.spaces.active.history[-1]
                caret = point_to_caret()
                set_draft(line, line.body, caret)
                self.focused_area = area.as_pointer()
                state["focused"] = True
                self.anchor, self.dragging = caret, True
                self.pending_click = None
                state["selection"] = None
                self.selected_all = False
                area.tag_redraw()
                return {'RUNNING_MODAL'}
            hits = state.get("hits", []) if state.get("hit_region") == (region.width, region.height) else []
            for rect, action, value in hits:
                if inside(rect, x, y):
                    # Activate on RELEASE like an ordinary desktop button. A
                    # popup created on PRESS inherits Blender's held-button
                    # menu gesture and incorrectly closes/selects on release.
                    self.pending_click = (rect, action, value, area.as_pointer())
                    if action == "approve":
                        state["pressed_approval_review"] = state.get("approval_review")
                    canvas_controls.press(area.tag_redraw, state, (rect, action, value))
                    return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.pending_click:
            rect, action, value, pointer = self.pending_click
            self.pending_click = None
            canvas_controls.release(area.tag_redraw, state)
            if (pointer == area.as_pointer() and state.get("hit_region") == (region.width, region.height)
                    and release_active((rect, action, value), state.get("hits", []), x, y)):
                with context.temp_override(window=window, area=area, region=region):
                    dispatch_action(bpy.context, controller, action, value)
                if state.pop("focus_request", False):
                    self.focused_area, state["focused"] = area.as_pointer(), True
                area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.value != 'PRESS' or not area.spaces.active.history:
            return {'PASS_THROUGH'}
        activate = canvas_controls.key(event, state, area.tag_redraw)
        if activate is not None:
            if activate:
                with context.temp_override(window=window, area=area, region=region):
                    dispatch_action(bpy.context, controller, *activate)
            return {'RUNNING_MODAL'}
        if state.get("composer_hidden"):
            # The field waits (Tab, Return pick a connect option); its Console keymap must not type in (edits_field).
            return {'RUNNING_MODAL'} if over_chat and edits_field(event) else {'PASS_THROUGH'}
        line = area.spaces.active.history[-1]
        value, caret = line.body, cursor_position(line)
        selected = (0, len(value)) if self.selected_all else state.get("selection")
        if selected and selected[0] == selected[1]:
            selected = None
        modifier = event.ctrl or event.oskey
        insert = None
        if modifier and event.type == 'A':
            self.selected_all = True
            state["selection"] = (0, len(value))
        elif modifier and event.type in {'C', 'X'}:
            if selected:
                context.window_manager.clipboard = value[selected[0]:selected[1]]
                if event.type == 'X':
                    set_draft(line, value[:selected[0]] + value[selected[1]:], selected[0])
                    self.selected_all = False
                    state["selection"] = None
        elif modifier and event.type == 'V':
            insert = context.window_manager.clipboard.replace("\r\n", "\n")
        elif event.type in {'RET', 'NUMPAD_ENTER'}:
            if event.shift:
                insert = "\n"
            else:
                with context.temp_override(window=window, area=area, region=region):
                    console.execute(bpy.context)
                self.selected_all = False
                state["selection"] = None
        elif event.type == 'BACK_SPACE':
            set_draft(line, value[:selected[0]] + value[selected[1]:] if selected else value[:max(0, caret - 1)] + value[caret:], selected[0] if selected else max(0, caret - 1))
            self.selected_all = False
            state["selection"] = None
        elif event.type == 'DEL':
            set_draft(line, value[:selected[0]] + value[selected[1]:] if selected else value[:caret] + value[caret + 1:], selected[0] if selected else caret)
            self.selected_all = False
            state["selection"] = None
        elif event.type in {'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'}:
            old_caret = caret
            if event.type in {'UP_ARROW', 'DOWN_ARROW'}:
                rows = state.get("draft_lines", ((value, 0),))
                size, drawn = state.get("font_size", 14), state.get("text_scale", scale)  # As the composer drew it.
                caret = chat_composer.vertical_caret(rows, caret, event.type == 'UP_ARROW', size, drawn)
            else:
                caret = max(0, caret - 1) if event.type == 'LEFT_ARROW' else min(len(value), caret + 1) if event.type == 'RIGHT_ARROW' else 0 if event.type == 'HOME' else len(value)
            set_draft(line, value, caret)
            if event.shift:
                if not state.get("selection"):
                    self.anchor = old_caret
                state["selection"] = tuple(sorted((self.anchor, caret)))
            else:
                state["selection"] = None
            self.selected_all = False
        elif event.type == 'ESC':
            self.selected_all = False
            state["selection"] = None
        elif event.unicode and not modifier and not event.alt:
            insert = event.unicode
        else:
            return {'PASS_THROUGH'}
        if insert is not None:
            if selected:
                value, caret = value[:selected[0]] + value[selected[1]:], selected[0]
            set_draft(line, value[:caret] + insert + value[caret:], caret + len(insert))
            self.selected_all = False
            state["selection"] = None
        area.tag_redraw()
        return {'RUNNING_MODAL'}


def note_blocked_click(window, event, blocking):
    """A click on a chat while another tool holds this window's input: the chat shows which, and how to end it."""
    from . import console
    for area in window.screen.areas:
        if console.is_chat(area) and area.x <= event.mouse_x < area.x + area.width and area.y <= event.mouse_y < area.y + area.height:
            _views.setdefault(area.as_pointer(), {})["blocked"] = (tuple(blocking), time.monotonic())
            area.tag_redraw()


def over_overlay(window, event):
    """Whether the pointer rests on the area the welcome screen or a notice card is drawn over."""
    from . import card_host, intro
    for area in window.screen.areas:
        if area.x <= event.mouse_x < area.x + area.width and area.y <= event.mouse_y < area.y + area.height:
            pointer = area.as_pointer()
            return (intro.is_open() and intro._open.get("area") == pointer) or card_host.shown_on(pointer)
    return False


def clear_blocked_notice(window):
    """The tool ended: its notice goes with the first event the chat takes again."""
    from . import console
    for area in window.screen.areas:
        if console.is_chat(area) and _views.get(area.as_pointer(), {}).pop("blocked", None):
            area.tag_redraw()


def ensure_input(window, area):
    """Start the chat's input handler. It starts even while another tool holds the window: it passes every event
    on until that tool ends, and a click on the chat meanwhile gets the notice saying which tool holds it."""
    if bpy.app.background:
        return
    from .input_policy import handler_running
    if window.as_pointer() in _modals and not handler_running(window, "angelo.chat_input"):
        _modals.discard(window.as_pointer())  # Ended without its cancel (it raised): start it again.
    if window.as_pointer() not in _modals:
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.angelo.chat_input('INVOKE_DEFAULT')


def animate_popovers():
    """Redraw only the chat region, and only while its animated card is open."""
    if not _enabled:
        return None
    from . import marble_host
    marble_host.poll()  # Starts the marble wait's own timer when the drawn chat runs a turn.
    from .approval_review import prepare_canvas_reviews
    animated = prepare_canvas_reviews()
    reduce_motion = getattr(bpy.context.preferences.view, "use_reduce_motion", False)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            popup = _views.get(area.as_pointer(), {}).get("popover")
            if not popup or popup["kind"] != "controls":
                continue
            moving = popup.get("animated") and not reduce_motion
            tooltip_due = (popup.get("hover") == ("popup_fast", None) and not popup.get("tooltip_shown")
                           and time.monotonic() - popup.get("hover_time", 0) > 0.45)
            if moving or tooltip_due:
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region:
                    region.tag_redraw()
                    animated = animated or moving
    return 1 / 60 if animated else 0.1


def reload_fonts():
    """File loading invalidates custom BLF IDs; rebind outside draw callbacks."""
    global _font, _mono
    bundled_mono = bpy.utils.system_resource('DATAFILES', path="fonts/DejaVuSansMono.woff2")  # Windows and Linux.
    _font = next((blf.load(p) for p in ("/System/Library/Fonts/SFNS.ttf",) if Path(p).is_file()), 0)  # 0: Blender's Inter.
    _mono = next((blf.load(p) for p in ("/System/Library/Fonts/Menlo.ttc", bundled_mono) if p and Path(p).is_file()), 0)
    wrap.cache_clear()
    chat_replies._layout.cache_clear()  # Font ids are part of a reply's cached layout.


def register():
    global _handler, _shader, _enabled, _input_generation
    _input_generation += 1
    reload_fonts()
    _shader = gpu.shader.from_builtin('UNIFORM_COLOR') if not bpy.app.background else None
    bpy.utils.register_class(ANGELO_OT_chat_input)
    _enabled = True
    if not bpy.app.background:
        _handler = bpy.types.SpaceConsole.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')
        bpy.app.timers.register(animate_popovers, first_interval=0.1, persistent=True)
        from . import voice
        voice.prime()  # Finds ffmpeg/ffplay once, outside drawing.


def unregister():
    global _handler, _enabled
    _enabled = False
    if bpy.app.timers.is_registered(animate_popovers):
        bpy.app.timers.unregister(animate_popovers)
    from . import marble_host
    marble_host.stop()
    if _handler:
        bpy.types.SpaceConsole.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    bpy.utils.unregister_class(ANGELO_OT_chat_input)
    _modals.clear()
    _views.clear()
    _scroll.clear()
    wrap.cache_clear()
