# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A canvas's thin fills and strokes stay visible at any interface scale.

The canvases (the chat, Flows' menus, the cards) lay out in unscaled units and draw under the GPU model-view matrix
(``chat_view.ui_scale()``, the right-to-left mirror). A fill narrower than one device pixel can cover no pixel
centre, and the rasteriser then draws nothing: under a fractional scale below 1 the composer's caret, one unit wide,
vanished at most positions (Xvfb at 75 dpi, scale 0.875: ``test_blender_chat_scroll_caret``, 2.0.0), and every
other hairline (a divider, an underline, a 1-unit border) could do the same. A person at such a scale saw no caret.

``covered`` grows a fill's thin side to exactly one device pixel on the pixel grid around its centre, as Blender's
own interface keeps a hairline one pixel wide (``U.pixelsize``); ``thick`` keeps a stroke at least one device pixel
thick. Anything a pixel or more across is drawn as laid out. Pure arithmetic on the matrix's numbers, so host tests
check it (``test_native_canvas_pixels``)."""

import math


def _cover(start, size, scale, offset):
    """``start``/``size`` along one axis, drawn at ``scale`` (negative when mirrored) plus ``offset`` pixels."""
    if size <= 0 or not scale or abs(size * scale) >= 1:
        return start, size
    left = math.floor(scale * (start + size / 2) + offset)   # The device pixel holding the fill's centre.
    a, b = (left - offset) / scale, (left + 1 - offset) / scale
    return min(a, b), abs(b - a)


def covered(x, y, width, height, matrix):
    """``(x, y, width, height)`` with a side under one device pixel grown to cover exactly one (``matrix``: the
    model-view matrix, rows indexable, no rotation)."""
    x, width = _cover(x, width, matrix[0][0], matrix[0][3])
    y, height = _cover(y, height, matrix[1][1], matrix[1][3])
    return x, y, width, height


def thick(thickness, matrix):
    """A stroke's ``thickness`` in layout units, at least one device pixel under ``matrix``."""
    scale = math.hypot(matrix[0][0], matrix[1][0])
    return max(thickness, 1 / scale) if scale else thickness
