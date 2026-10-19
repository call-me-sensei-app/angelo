# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws the AI Providers page's plan (``providers_page_layout``) with the chat canvas's primitives.

GPU only, inside the Preferences region's POST_PIXEL callback. The page's own ops (chips, tiles, switches,
stroke glyphs, buttons with a leading icon) are drawn here; everything else goes to ``card_draw`` so a button,
ring or spinner looks the same as on every other Angelo card.
"""

import math
from contextlib import ExitStack

import blf
import gpu

from . import card_draw, card_kit as kit
from .providers_page_layout import PAGE_BG, TONES

TILE = (0.170, 0.184, 0.214, 1)


def draw(view, plan, region_w, region_h, now=0.0):
    gpu.state.blend_set('ALPHA')
    with ExitStack() as box:
        for index, op in enumerate(plan.ops):
            kind = op[0]
            if kind != "text":
                gpu.state.blend_set('ALPHA')
            if kind == "page_bg":
                view.shape(0, 0, region_w, region_h, PAGE_BG, 0)
            elif kind == "pbutton":
                _button(view, *op[1:])
            elif kind == "iconbtn":
                _icon_button(view, *op[1:])
            elif kind == "chip":
                _chip(view, *op[1:], now)
            elif kind == "tile":
                _tile(view, *op[1:])
            elif kind == "glyph":
                glyph(view, op[1], op[2], op[3])
            elif kind == "switch":
                _switch(view, *op[1:])
            elif kind == "mark":
                _mark(view, op[1])
            elif kind == "paint":   # The raven banner (welcome_screen's art), drawn like the About card's.
                _banner(op[2])
            else:
                card_draw._op(view, plan.ops, index, op, box)
    gpu.state.scissor_test_set(False)


def _button(view, rect, label, style, state, size, icon):
    if not icon:
        card_draw._button(view, rect, label, style, state, size)
        return
    hover, pressed, focus, enabled = state
    x, y, w, h = rect
    s = size / 13.5
    if focus:
        card_draw._ring(view, rect, h / 2, max(0.6, s))
    normal, over, down = card_draw.BUTTON_FILL[style]
    fill = down if pressed else over if hover else normal
    if not enabled:
        fill = (fill[0], fill[1], fill[2], fill[3] * 0.35)
    if fill[3]:
        view.shape(x, y, w, h, fill, h / 2)
    color = card_draw.BUTTON_TEXT[style]
    if style == "ghost":
        color = kit.TEXT if hover or pressed or focus else kit.MUTED
    if not enabled:
        color = (kit.TEXT[0], kit.TEXT[1], kit.TEXT[2], 0.42)
    blf.size(view._font, size)
    tw = blf.dimensions(view._font, label)[0]
    left = x + (w - tw - 18 * s) / 2
    drop = 1 if pressed else 0
    glyph(view, icon, (left, y + (h - 12 * s) / 2 - drop, 12 * s, 12 * s), color)
    view.text(label, left + 18 * s, y + (h - size * 0.72) / 2 - drop, size, color)


def _icon_button(view, rect, name, state):
    hover, pressed, focus, enabled = state
    x, y, w, h = rect
    s = w / 30
    if focus:
        card_draw._ring(view, rect, w / 2, s)
    if hover or pressed:
        view.shape(x, y, w, h, (1, 1, 1, 0.14 if pressed else 0.08), w / 2)
    color = kit.TEXT if hover or pressed or focus else kit.MUTED
    glyph(view, name, (x + 8 * s, y + 8 * s, 14 * s, 14 * s), color)


def _chip(view, rect, label, tone, size, spinner, now):
    x, y, w, h = rect
    color, soft = TONES[tone]
    view.shape(x, y, w, h, soft, h / 2)
    s = size / 11.5
    tx = x + 9 * s
    if spinner:
        card_draw._spinner(view, tx + 4 * s, y + h / 2, 4.5 * s, now)
        tx += 14 * s
    elif tone == "teal":
        glyph(view, "check", (tx, y + (h - 9 * s) / 2, 9 * s, 9 * s), color)
        tx += 14 * s
    view.text(label, tx, y + (h - size * 0.72) / 2, size, kit.MUTED if tone == "muted" else color)


def _tile(view, rect, letter, name, dot):
    x, y, w, h = rect
    s = w / 40
    view.shape(x, y, w, h, kit.LINE, 12 * s)
    view.shape(x + 1 * s, y + 1 * s, w - 2 * s, h - 2 * s, TILE, 11 * s)
    if letter:
        size = 16 * s
        blf.size(view._font, size)
        tw = blf.dimensions(view._font, letter)[0]
        view.text(letter, x + (w - tw) / 2, y + (h - size * 0.72) / 2, size, kit.TEXT)
    elif name:
        glyph(view, name, (x + 11 * s, y + 11 * s, 18 * s, 18 * s), kit.TEAL)
    if dot:
        r = 5 * s
        view.shape(x + w - r * 1.4 - 1.5 * s, y - r * 0.6 - 1.5 * s, 2 * r + 3 * s, 2 * r + 3 * s, kit.PANEL, r + 1.5 * s)
        view.shape(x + w - r * 1.4, y - r * 0.6, 2 * r, 2 * r, TONES.get(dot, TONES["muted"])[0], r)


def _switch(view, rect, on, state):
    hover, pressed, focus, enabled = state
    x, y, w, h = rect
    s = h / 22
    if focus:
        card_draw._ring(view, rect, h / 2, s)
    track = (kit.TEAL_HOVER if hover else kit.TEAL) if on else (kit.LINE_HOVER if hover else kit.LINE)
    if not enabled:
        track = (track[0], track[1], track[2], 0.4)
    view.shape(x, y, w, h, track, h / 2)
    r = h / 2 - 3 * s
    cx = x + w - h / 2 if on else x + h / 2
    knob = kit.INK if on else (kit.TEXT if enabled else kit.MUTED)
    view.shape(cx - r, y + h / 2 - r, 2 * r, 2 * r, knob, r)


def _banner(rect):
    from gpu_extras.batch import batch_for_shader
    from .providers_page_model import ART
    if ART["pixels"] is None:
        return
    width, height = ART["size"]
    if ART["texture"] is None:
        ART["texture"] = gpu.types.GPUTexture((width, height), format='RGBA16F',
                                              data=gpu.types.Buffer('FLOAT', [width * height * 4], ART["pixels"]))
    shader = gpu.shader.from_builtin('IMAGE')
    x, y, w, h = rect
    batch = batch_for_shader(shader, 'TRIS', {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                                              "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]}, indices=[(0, 1, 2), (0, 2, 3)])
    shader.bind()
    shader.uniform_sampler("image", ART["texture"])
    batch.draw(shader)


def _mark(view, rect):
    """The hero's mark: the facet badge (teal rings around the raven's spark), drawn, not a picture."""
    x, y, w, h = rect
    cx, cy, r = x + w / 2, y + h / 2, w / 2
    view.shape(x, y, w, h, kit.TEAL_SOFT, r)
    view.shape(x + w * 0.14, y + h * 0.14, w * 0.72, h * 0.72, (0.169, 0.769, 0.749, 0.28), r * 0.72)
    s = w / 72
    for (dx1, dy1, dx2, dy2, thick) in ((0, -16, 0, 16, 4.0), (-16, 0, 16, 0, 4.0), (-9, -9, 9, 9, 2.6), (-9, 9, 9, -9, 2.6)):
        view.stroke(cx + dx1 * s, cy + dy1 * s, cx + dx2 * s, cy + dy2 * s, kit.TEAL, thick * s)


def glyph(view, name, rect, color):
    """Small stroke icons (lines and circles, like ``chat_view.icon``), in ``rect``."""
    x, y, w, h = rect
    s = w / 12
    cx, cy = x + w / 2, y + h / 2
    t = max(1.0, 1.4 * s)
    line = view.stroke
    if name == "plus":
        line(cx, y + 1 * s, cx, y + h - 1 * s, color, t)
        line(x + 1 * s, cy, x + w - 1 * s, cy, color, t)
    elif name == "pencil":
        line(x + 2 * s, y + 2 * s, x + 9.5 * s, y + 9.5 * s, color, t * 1.3)
        line(x + 1.5 * s, y + 1.5 * s, x + 4 * s, y + 2 * s, color, t)
    elif name == "more":
        for dx in (-4, 0, 4):
            view.shape(cx + dx * s - 1.3 * s, cy - 1.3 * s, 2.6 * s, 2.6 * s, color, 1.3 * s)
    elif name == "lock":
        view.shape(x + 2 * s, y + 1 * s, 8 * s, 6 * s, color, 1.5 * s)
        _arc(view, cx, y + 7 * s, 2.6 * s, 0, 180, color, t)
        line(cx - 2.6 * s, y + 7 * s, cx - 2.6 * s, y + 6.5 * s, color, t)
    elif name == "check":
        line(x + 1 * s, cy, x + 4.5 * s, y + 2.5 * s, color, t * 1.2)
        line(x + 4.5 * s, y + 2.5 * s, x + 11 * s, y + 9.5 * s, color, t * 1.2)
    elif name == "alert":
        _arc(view, cx, cy, 5.2 * s, 0, 360, color, t)
        line(cx, cy - 0.5 * s, cx, cy + 3 * s, color, t)
        view.shape(cx - 0.8 * s, cy - 3 * s, 1.6 * s, 1.6 * s, color, 0.8 * s)
    elif name == "info":
        _arc(view, cx, cy, 5.2 * s, 0, 360, color, t)
        line(cx, cy - 3 * s, cx, cy + 0.5 * s, color, t)
        view.shape(cx - 0.8 * s, cy + 1.8 * s, 1.6 * s, 1.6 * s, color, 0.8 * s)
    elif name == "external":
        line(x + 1.5 * s, y + 1.5 * s, x + 1.5 * s, y + 9 * s, color, t)
        line(x + 1.5 * s, y + 1.5 * s, x + 9 * s, y + 1.5 * s, color, t)
        line(x + 5 * s, y + 6 * s, x + 10.5 * s, y + 10.5 * s, color, t)
        line(x + 6 * s, y + 10.5 * s, x + 10.5 * s, y + 10.5 * s, color, t)
        line(x + 10.5 * s, y + 10.5 * s, x + 10.5 * s, y + 6 * s, color, t)
    elif name == "people":
        _arc(view, x + 4.5 * s, y + 8 * s, 2.2 * s, 0, 360, color, t)
        _arc(view, x + 4.5 * s, y + 1.5 * s, 3.6 * s, 0, 180, color, t)
        _arc(view, x + 8.8 * s, y + 8.6 * s, 1.8 * s, 0, 360, color, t)
        _arc(view, x + 8.8 * s, y + 2.6 * s, 3 * s, 0, 180, color, t)
    elif name == "model":
        _arc(view, cx, cy, 4.6 * s, 0, 360, color, t)
        view.shape(cx - 1.4 * s, cy - 1.4 * s, 2.8 * s, 2.8 * s, color, 1.4 * s)
    elif name == "list":
        for row in (2.5, 6, 9.5):
            view.shape(x + 1 * s, y + row * s - 0.7 * s, 1.4 * s, 1.4 * s, color, 0.7 * s)
            line(x + 4 * s, y + row * s, x + 11 * s, y + row * s, color, t)
    elif name == "coins":
        _arc(view, cx, cy, 5 * s, 0, 360, color, t)
        line(cx, cy - 2.5 * s, cx, cy + 2.5 * s, color, t)
    elif name == "shield":
        line(cx, y + 0.8 * s, x + 1.8 * s, y + 4.5 * s, color, t)
        line(cx, y + 0.8 * s, x + 10.2 * s, y + 4.5 * s, color, t)
        line(x + 1.8 * s, y + 4.5 * s, x + 1.8 * s, y + 9.5 * s, color, t)
        line(x + 10.2 * s, y + 4.5 * s, x + 10.2 * s, y + 9.5 * s, color, t)
        line(x + 1.8 * s, y + 9.5 * s, x + 10.2 * s, y + 9.5 * s, color, t)
    elif name == "terminal":
        line(x + 1.5 * s, y + 9 * s, x + 5 * s, y + 6 * s, color, t)
        line(x + 5 * s, y + 6 * s, x + 1.5 * s, y + 3 * s, color, t)
        line(x + 6.5 * s, y + 2 * s, x + 11 * s, y + 2 * s, color, t)
    elif name == "refresh":
        _arc(view, cx, cy, 4.6 * s, 40, 330, color, t)
        line(cx + 4.6 * s * 0.77, cy + 4.6 * s * 0.64, cx + 4.6 * s * 0.77 + 2.6 * s, cy + 4.6 * s * 0.64, color, t)
        line(cx + 4.6 * s * 0.77, cy + 4.6 * s * 0.64, cx + 4.6 * s * 0.77, cy + 4.6 * s * 0.64 + 2.6 * s, color, t)
    elif name == "spark":
        line(cx, y, cx, y + h, color, t)
        line(x, cy, x + w, cy, color, t)
        line(x + 2.5 * s, y + 2.5 * s, x + 9.5 * s, y + 9.5 * s, color, t * 0.8)
        line(x + 2.5 * s, y + 9.5 * s, x + 9.5 * s, y + 2.5 * s, color, t * 0.8)


def _arc(view, cx, cy, r, start, end, color, thickness):
    steps = max(6, int((end - start) / 20))
    points = [(cx + r * math.cos(math.radians(start + (end - start) * i / steps)),
               cy + r * math.sin(math.radians(start + (end - start) * i / steps))) for i in range(steps + 1)]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        view.stroke(x1, y1, x2, y2, color, thickness)
