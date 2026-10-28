# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo button's round card: one disc treatment for every round control Angelo draws over the 3D view.

Corner Angelo (``chat_launcher``: the raven while the chat is hidden) and the minimized Sessions panel
(``rail_minimized``: the robot while the panel is minimized) are the same control to the person, so they share this
one drawing, not two copies: a soft shadow ``SHADOW_OFFSET`` below, the card colour, a hairline edge ring, and on
hover the panel-hover fill with a teal ring (darker teal while pressed). The maintainer (2026-10-25): "make it become
a circle like how Angelo button is when chat window is hidden".

The disc and its rings are GPU batches built once per size (``disc``), so a frame draws cached batches only. Sizes are
pixels at a UI scale of 1; coordinates are region pixels, y up. GPU only, inside a POST_PIXEL callback.
"""

import math

from . import card_kit as kit

DISC = 76.0              # The card's diameter (pixels at a UI scale of 1): the marble's compact hero with room.
SHADOW = (0.0, 0.0, 0.0, 0.28)
SHADOW_OFFSET = 2.0      # Pixels (at a UI scale of 1) the shadow sits below the card.
RING = 1.0               # The edge ring's width at rest (pixels at a UI scale of 1) ...
RING_HOVER = 1.5         # ... and under the pointer.
SEGMENTS = 48
_discs = {}              # (radius, thickness) -> GPU batch: the disc (thickness 0) and its ring, built once per size.


def colors(hover, pressed):
    """``(fill, ring, ring width at a UI scale of 1)`` of the card: at rest, under the pointer, held down (pure)."""
    fill = kit.PANEL_HOVER if hover else kit.CARD
    ring = kit.TEAL_PRESSED if pressed else kit.TEAL if hover else kit.EDGE
    return fill, ring, RING_HOVER if hover else RING


def inside(cx, cy, radius, x, y):
    """Whether pixel (``x``, ``y``) is on the disc of ``radius`` centred on (``cx``, ``cy``): never its square's
    corners, which are the scene's (pure)."""
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def pixel_centres(spot, radius, bounds):
    """Nearby contained centres with an integer hit inside native ``bounds`` (left, bottom, right, top).

    Native pixel ownership includes left/bottom and excludes right/top. Keep an already reachable ``spot``;
    otherwise align either axis to nearby integer pixels, clamped to the allowed centre rectangle. A subpixel
    circle may fit geometrically yet have no integer hotspot (a 1x1 box): return no candidates then. Pure;
    the caller still checks its soft obstacles and placement preference. This does not promise a usable target size.
    """
    left, bottom, right, top = bounds
    lo_x, hi_x, lo_y, hi_y = left + radius, right - radius, bottom + radius, top - radius
    if radius <= 0 or lo_x > hi_x or lo_y > hi_y:
        return ()

    def reachable(x, y):
        return (lo_x <= x <= hi_x and lo_y <= y <= hi_y and any(
            left <= px < right and bottom <= py < top and inside(x, y, radius, px, py)
            for px in {math.floor(x), math.ceil(x)} for py in {math.floor(y), math.ceil(y)}))

    cx, cy = spot
    if reachable(cx, cy):
        return (spot,)
    xs = {min(max(x, lo_x), hi_x) for x in (cx, math.floor(cx), math.ceil(cx))}
    ys = {min(max(y, lo_y), hi_y) for y in (cy, math.floor(cy), math.ceil(cy))}
    return tuple((x, y) for x in sorted(xs) for y in sorted(ys) if reachable(x, y))


def draw(cx, cy, radius, scale, hover=False, pressed=False):
    """The round card centred on region pixel (``cx``, ``cy``): shadow, fill and ring as ``colors`` says."""
    fill_color, ring_color, ring = colors(hover, pressed)
    body = disc(radius)
    fill(body, cx, cy - SHADOW_OFFSET * scale, SHADOW)
    fill(body, cx, cy, fill_color)
    fill(disc(radius, ring * scale), cx, cy, ring_color)


def disc(radius, thickness=0.0):
    """A circle (``thickness`` 0) or a ring of ``thickness`` drawn inward, centred on the origin: built once per size,
    so a frame draws cached batches only."""
    key = (round(radius, 2), round(thickness, 2))
    batch = _discs.get(key)
    if batch is None:
        from gpu_extras.batch import batch_for_shader
        from . import canvas_controls
        angles = [2 * math.pi * i / SEGMENTS for i in range(SEGMENTS)]
        outer = [(radius * math.cos(a), radius * math.sin(a), 0.0) for a in angles]
        if thickness <= 0:
            vertices = [(0.0, 0.0, 0.0)] + outer
            indices = [(0, 1 + i, 1 + (i + 1) % SEGMENTS) for i in range(SEGMENTS)]
        else:
            inner = [((radius - thickness) * math.cos(a), (radius - thickness) * math.sin(a), 0.0) for a in angles]
            vertices = outer + inner
            indices = []
            for i in range(SEGMENTS):
                j = (i + 1) % SEGMENTS
                indices += [(i, j, SEGMENTS + i), (j, SEGMENTS + j, SEGMENTS + i)]
        if len(_discs) > 32:   # A few sizes per UI scale; never grows with use.
            _discs.clear()
        batch = _discs[key] = batch_for_shader(canvas_controls._shader_get(), 'TRIS', {"pos": vertices},
                                               indices=indices)
    return batch


def fill(batch, cx, cy, color):
    """Draw a ``disc`` batch centred on (``cx``, ``cy``) in ``color``."""
    import gpu
    from . import canvas_controls
    shader = canvas_controls._shader_get()
    with gpu.matrix.push_pop():
        gpu.matrix.translate((cx, cy))
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)


def release():
    """Drop the cached batches (an add-on unregistering): the next draw builds them again."""
    _discs.clear()
