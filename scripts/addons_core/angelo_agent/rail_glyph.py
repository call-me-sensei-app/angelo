# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own glyphs for the Sessions panel, drawn with ``chat_view``'s primitives like every card_kit glyph
(rounded shapes, circles as fully rounded shapes, stroked lines): never an emoji, never a stock Blender icon.

* ``robot(cx, cy, size, color, eyes=None)``: the minimized panel's icon (``rail_minimized``): a rounded head with
  ears, a short antenna with a ball, and round eyes and a mouth cut out in ``eyes`` (the fill behind the glyph:
  ``kit.CARD`` at rest, ``kit.PANEL_HOVER`` under the pointer).
* ``minimize(cx, cy, size, color)``: the panel header's minimize control, an arrow into the top-right corner (where
  the panel goes), at the paged arrows' stroke weight (``session_rail_draw._arrow``).
* ``chevron(cx, cy, size, color, direction)``: the paged arrows' chevron, for the "+N more" rows at the panel's edges.
* ``badge_rect`` and ``approval_dot``: where the worker count and the amber approval mark sit on the minimized
  circle, so every drawer and its hit test agree (the Sessions rail in doc/angelo/agent_integration.rst).

Every glyph is centred on region pixel (``cx``, ``cy``) and stays inside the ``size`` square around it at any size
(a stroke's square end may reach past it by half its thickness).
Edges land on whole pixels (the GPU draws without anti-aliasing), the same at 1x and 2x: the centre snaps to the
nearest pixel and mirrored parts mirror exactly about it, so both eyes and both ears are always the same.
Sizes are in region pixels: callers multiply by the UI scale.
"""

import math

from . import card_kit as kit, ui_tokens as tk

STROKE = 0.1875   # A chevron's stroke per its width: session_rail_draw._arrow's 1.5 px over 8 px.


def _view():
    from . import chat_view
    return chat_view


def _r(value):
    """Rounded half away from zero, never to even (Python's ``round``): mirrored parts round alike."""
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def _disc(view, x, y, radius, color):
    view.shape(x - radius, y - radius, 2 * radius, 2 * radius, color, radius)


def robot(cx, cy, size, color, eyes=None):
    """The robot head inside a ``size`` pixel square, in ``color``; eyes and mouth in ``eyes`` (default
    ``kit.CARD``, the minimized circle's fill). Drawn on a 32-unit grid: the antenna's ball reaches 14 units up, the
    head 14 down, so it stands 28 of the square's 32 units tall and keeps two clear at each edge for the pixel
    snapping."""
    view = _view()
    eyes = kit.CARD if eyes is None else eyes
    u = size / 32
    x0, y0 = math.floor(cx + 0.5), math.floor(cy + 0.5)
    # Head: 22 units wide, 18 tall (units -14 to 4), corners as round as a friendly face reads.
    half = max(2, _r(11 * u))
    top, bottom = y0 + _r(4 * u), y0 - _r(14 * u)
    radius = max(1.0, 5.5 * u)
    # Ears: 3 units out from the head's sides, 7 units tall, centred on the eyes' row.
    ear_w, ear_h, ear_y = max(1, _r(3 * u)), max(2, _r(7 * u)), y0 - _r(9 * u)
    view.shape(x0 - half - ear_w, ear_y, ear_w + 1, ear_h, color, min(1.5 * u, ear_w / 2))
    view.shape(x0 + half - 1, ear_y, ear_w + 1, ear_h, color, min(1.5 * u, ear_w / 2))
    view.shape(x0 - half, bottom, 2 * half, top - bottom, color, radius)
    # Antenna: a stem from inside the head to a ball 10 units up; the stem two units wide (a glyph's stroke).
    ball = max(1.5, _r(2.5 * u))
    ball_y = y0 + _r(14 * u) - ball
    stem = max(1, _r(1.0 * u))
    view.shape(x0 - stem, top - 1, 2 * stem, ball_y - top + 1, color, 0)
    _disc(view, x0, ball_y, ball, color)
    # Eyes: round, 6 units across, 10 units apart; the mouth a short rounded bar below them.
    eye, apart, eye_y = max(1.5, _r(3 * u)), max(2, _r(5 * u)), y0 - _r(4 * u)
    _disc(view, x0 - apart, eye_y, eye, eyes)
    _disc(view, x0 + apart, eye_y, eye, eyes)
    mouth_w, mouth_h = max(2, _r(4 * u)), max(1, _r(1.5 * u))
    view.shape(x0 - mouth_w, y0 - _r(10 * u) - mouth_h, 2 * mouth_w, mouth_h, eyes, mouth_h / 2)


def minimize(cx, cy, size, color):
    """The minimize control's glyph: a diagonal arrow from the lower left into the upper-right corner, its head the
    corner itself (two arms meeting square). ``size`` is its square (10 px at UI scale 1 in a 20 px button, the
    paged arrows' size); the stroke is the chevron's (``STROKE``: 1.5 px at 1x)."""
    view = _view()
    thick = max(1.0, STROKE * size * 0.8)
    half = size / 2 - thick / 2
    x1, y1 = cx + half, cy + half   # The corner.
    arm = 0.62 * size
    view.stroke(x1 + thick / 2, y1, x1 - arm, y1, color, thick)      # The head's top arm, out past the corner...
    view.stroke(x1, y1 + thick / 2, x1, y1 - arm, color, thick)      # ...and its right arm, so the corner is square.
    tail = 0.5 * thick
    view.stroke(cx - half + tail, cy - half + tail, x1 - tail, y1 - tail, color, thick)


def chevron(cx, cy, size, color, direction="DOWN"):
    """The paged arrows' chevron (``session_rail_draw._arrow``), ``size`` pixels wide across its arms (8 px at UI
    scale 1), pointing ``direction``: 'UP', 'DOWN', 'LEFT' or 'RIGHT'."""
    view = _view()
    d = size / 2
    thick = max(1.0, STROKE * size)
    tip = d / 2   # Half its depth: the arms span ``d`` along the direction it points.
    if direction not in {"UP", "DOWN", "LEFT", "RIGHT"}:
        raise ValueError(f"chevron direction {direction!r} is not UP, DOWN, LEFT or RIGHT")
    if direction in {"UP", "DOWN"}:
        sign = 1 if direction == "UP" else -1
        view.stroke(cx - d, cy - sign * tip, cx, cy + sign * tip, color, thick)
        view.stroke(cx, cy + sign * tip, cx + d, cy - sign * tip, color, thick)
    else:
        sign = 1 if direction == "RIGHT" else -1
        view.stroke(cx - sign * tip, cy - d, cx + sign * tip, cy, color, thick)
        view.stroke(cx + sign * tip, cy, cx - sign * tip, cy + d, color, thick)


# -- the minimized circle's marks (pure: rects in region pixels, y up) ------------------------------------------------
BADGE_H = 18      # The count badge's height at UI scale 1: the rail header's count chip.
BADGE_PAD = 5     # Its digits' side padding.
BADGE_RING = 2    # The fill-coloured ring around the badge and the approval dot, parting them from the rim.
DOT = 10          # The amber approval dot's diameter: the rail's arrow dot (6 px) grown for a 76 px button.
ROBOT = 0.42      # The robot's square per the circle's diameter: 32 px in the 76 px circle at 1x.


def robot_size(radius):
    """The robot's ``size`` (its square) in a circle of ``radius``: a 32 px square in the 76 px circle at UI scale 1,
    64 px at 2x; the robot itself is 28 of those 32 units tall (``robot``)."""
    return _r(2 * radius * ROBOT)


def badge_rect(cx, cy, radius, text_width, scale):
    """The worker count's pill: centred on the circle's rim at its lower right (45 degrees), never wider leftward
    than it must be and never past the circle's square with its ring: a count wider than the room grows to the left.
    Returns ``(x, y, w, h)``; the digits are centred in it."""
    h = BADGE_H * scale
    w = max(h, text_width + 2 * BADGE_PAD * scale)
    ring = BADGE_RING * scale
    mx, my = cx + radius * math.sqrt(0.5), cy - radius * math.sqrt(0.5)
    x = min(mx - w / 2, cx + radius - w - ring)
    y = max(my - h / 2, cy - radius + ring)
    return (x, y, w, h)


def approval_dot(cx, cy, radius, scale):
    """The amber approval mark: a ``DOT`` px disc centred on the circle's rim at its upper right (45 degrees), inside
    the circle's square with its ring. Returns ``(x, y, d)``: the disc's lower-left corner and diameter."""
    d = DOT * scale
    mx, my = cx + radius * math.sqrt(0.5), cy + radius * math.sqrt(0.5)
    ring = BADGE_RING * scale
    mx, my = min(mx, cx + radius - d / 2 - ring), min(my, cy + radius - d / 2 - ring)
    return (mx - d / 2, my - d / 2, d)


def placeholder_y(picture, badge, scale):
    """The baseline of a picture's placeholder words ("Nothing built yet"), centred in the band between the role chip
    at its foot (``_chip``: 6 px in, 18 px tall) and the approval badge at its head (or its top edge), 4 px clear of
    each; None when that band is shorter than the words (``tk.TYPE_MICRO``): in a short picture they would cover the
    chips (the rail positions GUI suite, 2026-10-25: Director at 2x) (pure)."""
    px, py, pw, ph = picture
    low = py + (6 + 18 + 4) * scale
    high = (badge[1] if badge is not None else py + ph) - 4 * scale
    if high - low < tk.TYPE_MICRO * scale:
        return None
    return (low + high) / 2 - 4 * scale
