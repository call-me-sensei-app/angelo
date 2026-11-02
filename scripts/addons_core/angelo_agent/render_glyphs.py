# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render switcher's glyphs: one shape each, drawn two ways from the same geometry (1.0.8).

* ``eevee``: a lightning bolt (real time); ``cycles``: a ball lit by one ray (path tracing); ``ai_render``: a
  four-point spark over a frame (the camera's view, restyled); ``engine``: a lens ring (any other engine);
  ``uv_checker``: a framed 2 x 2 grid (the viewport UV checker, ``uv_checker``).
* ``draw_tile`` paints a dropdown row's 32 px tile with the canvas's shapes and strokes (``chat_add_menu.draw_glyph``'s
  style: teal on the soft teal tile); ``icon_id`` bakes the same strokes into icon pixels for the top-bar button, in
  the widget's text colour (``glyph_tint``), 4x4 supersampled like Flows' glyph (``flow_open.flow_icon``).

Geometry is in a 32 x 32 box, y up. Pure apart from the preview collection.
"""

import math

ICON_SIZE = 32
GLYPHS = ("eevee", "cycles", "ai_render", "engine", "uv_checker")
_icons = None


def _spark(cx, cy, r):
    """A four-point spark's outline, as segments."""
    points = []
    for index in range(8):
        angle = index * math.pi / 4
        reach = r if index % 2 == 0 else r * 0.28
        points.append((cx + reach * math.cos(angle), cy + reach * math.sin(angle)))
    return list(zip(points, points[1:] + points[:1]))


def strokes(glyph):
    """``(segments, width, discs)``: line segments ``((ax, ay), (bx, by))`` drawn ``width`` px wide and filled
    discs ``(cx, cy, r)``, in the 32 px box."""
    if glyph == "eevee":
        bolt = [(18, 27), (10, 15), (16, 15), (13, 5), (22, 18), (16, 18), (18, 27)]
        return list(zip(bolt, bolt[1:])), 1.8, []
    if glyph == "cycles":
        ring = [(16 + 8 * math.cos(a * math.pi / 12), 14 + 8 * math.sin(a * math.pi / 12)) for a in range(25)]
        return list(zip(ring, ring[1:])) + [((4, 28), (11, 20))], 1.6, [(20, 18, 2.4)]
    if glyph == "ai_render":
        frame = [(6, 7), (26, 7), (26, 23), (6, 23), (6, 7)]
        return list(zip(frame, frame[1:])) + _spark(21, 21, 7), 1.5, []
    if glyph == "uv_checker":   # A framed 2 x 2 grid with two opposite cells marked (the viewport UV checker).
        frame = [(6, 6), (26, 6), (26, 26), (6, 26), (6, 6)]
        return list(zip(frame, frame[1:])) + [((16, 6), (16, 26)), ((6, 16), (26, 16))], 1.5, [(11, 21, 3), (21, 11, 3)]
    ring = [(16 + 9 * math.cos(a * math.pi / 12), 16 + 9 * math.sin(a * math.pi / 12)) for a in range(25)]
    return list(zip(ring, ring[1:])), 1.6, [(16, 16, 3.2)]


def draw_tile(view, glyph, tx, ty):
    """A dropdown row's tile for ``glyph`` (False when it is not one of these, so the caller draws its own)."""
    if glyph not in GLYPHS:
        return False
    from . import ui_tokens
    view.shape(tx, ty, 32, 32, ui_tokens.TEAL_SOFT, 8)
    segments, width, discs = strokes(glyph)
    for (ax, ay), (bx, by) in segments:
        view.stroke(tx + ax, ty + ay, tx + bx, ty + by, ui_tokens.TEAL, width)
    for cx, cy, r in discs:
        view.shape(tx + cx - r, ty + cy - r, 2 * r, 2 * r, ui_tokens.TEAL, r)
    return True


def _near(x, y, segments, half):
    for (ax, ay), (bx, by) in segments:
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
        if (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2 <= half * half:
            return True
    return False


def pixels(glyph, size=ICON_SIZE, rgb=None):
    """RGBA floats (bottom row first) of ``glyph`` in ``rgb``, premultiplied, 4x4 supersampled."""
    r, g, b = rgb or (0.93, 0.95, 0.94)
    segments, width, discs = strokes(glyph)
    scale = 32.0 / size
    result = []
    for row in range(size):
        for column in range(size):
            hits = 0
            for sy in range(4):
                for sx in range(4):
                    x, y = (column + (sx + 0.5) / 4) * scale, (row + (sy + 0.5) / 4) * scale
                    hits += _near(x, y, segments, width * 0.75) or any(
                        (x - cx) ** 2 + (y - cy) ** 2 <= rr * rr for cx, cy, rr in discs)
            alpha = hits / 16
            result += (r * alpha, g * alpha, b * alpha, alpha)
    return result


def icon_id(glyph, rgb=None):
    """The preview icon of ``glyph`` baked in ``rgb`` (``glyph_tint.colour`` of the widget it sits on)."""
    global _icons
    from . import glyph_tint
    rgb = tuple(rgb or glyph_tint.DEFAULT)
    if _icons is None:
        from bpy.utils import previews
        _icons = previews.new()
    name = glyph_tint.key("render-" + glyph, rgb)
    if name not in _icons:
        preview = _icons.new(name)
        preview.icon_size = (ICON_SIZE, ICON_SIZE)
        preview.icon_pixels_float = pixels(glyph, rgb=rgb)
    return _icons[name].icon_id


def release():
    global _icons
    if _icons is not None:
        from bpy.utils import previews
        previews.remove(_icons)
        _icons = None
