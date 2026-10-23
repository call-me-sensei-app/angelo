# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The top-bar Chat toggle's speech bubble (``chat_toggle``), drawn as pixels so no image file ships: filled with
three cut-out dots while the chat is shown, an outline while it is hidden. Split from chat_toggle.py (1.0.5) to keep
that module under the size limit; ``chat_toggle.bubble`` and ``chat_toggle.ICON_SIZE`` still resolve."""

ICON_SIZE = 32


def bubble(filled, size=ICON_SIZE, rgb=None):
    """RGBA floats (bottom row first) of the toggle's speech bubble: filled with three cut-out dots while shown,
    an outline while hidden. Light on Angelo's teal top bar; 4x4 supersampled edges."""
    r, g, b = rgb or (0.93, 0.95, 0.94)   # The glyph's colour (glyph_tint); near-white on the dark bar.
    def inside_body(x, y):
        left, right, bottom, top, radius = 3.5, size - 3.5, 9.0, size - 4.0, 6.0
        cx = min(max(x, left + radius), right - radius)
        cy = min(max(y, bottom + radius), top - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 and left <= x <= right and bottom <= y <= top

    def inside_tail(x, y):   # A small triangle under the body's lower left: (8, 10), (15, 10), (6, 3).
        corners = ((8.0, 10.0), (15.0, 10.0), (6.0, 3.0))
        signs = [(bx - ax) * (y - ay) - (by - ay) * (x - ax) for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1])]
        return all(value >= 0 for value in signs) or all(value <= 0 for value in signs)

    def shape(x, y):
        return inside_body(x, y) or inside_tail(x, y)

    def dot(x, y):
        return any((x - cx) ** 2 + (y - 17.5) ** 2 <= 2.4 ** 2 for cx in (10.5, 16.0, 21.5))

    def inner(x, y):   # The outline variant's hollow: the body shrunk by 2.2 px.
        left, right, bottom, top, radius = 5.7, size - 5.7, 11.2, size - 6.2, 3.8
        cx = min(max(x, left + radius), right - radius)
        cy = min(max(y, bottom + radius), top - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 and left <= x <= right and bottom <= y <= top

    pixels = []
    for row in range(size):
        for column in range(size):
            hits = 0
            for sy in range(4):
                for sx in range(4):
                    x, y = column + (sx + 0.5) / 4, row + (sy + 0.5) / 4
                    if filled:
                        hits += shape(x, y) and not dot(x, y)
                    else:
                        hits += shape(x, y) and not inner(x, y)
            alpha = hits / 16
            pixels += (r * alpha, g * alpha, b * alpha, alpha)   # Premultiplied, as Blender draws icons.
    return pixels
