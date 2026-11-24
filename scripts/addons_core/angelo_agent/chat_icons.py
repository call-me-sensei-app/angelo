# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's line icons, drawn with the chat canvas's own primitives (``chat_view.stroke``, ``shape`` and its shader).

Moved out of ``chat_view`` unchanged (2.1.0, its module size); ``chat_view.icon`` is this function."""

import math

from gpu_extras.batch import batch_for_shader

from . import ui_tokens as tk


def icon(name, x, y, color=None):
    from .chat_view import _shader, shape, stroke   # chat_view imports this module: read at the call.
    color = tk.MUTED if color is None else color
    if name == "plus":
        stroke(x - 5, y, x + 5, y, color, 1.5)
        stroke(x, y - 5, x, y + 5, color, 1.5)
    elif name in {"mic", "approval"}:
        if name == "mic":
            shape(x - 3, y - 1, 6, 10, color, 3)
            shape(x - 1.7, y + 0.2, 3.4, 7.5, tk.RAISED, 1.7)
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
