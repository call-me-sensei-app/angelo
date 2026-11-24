# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A picture the viewer makes for the chat, encoded within the chat's image limit (2.1.0).

Break-it of 2298d608dd1, M1: the marked picture, Resize's result and the 3D view's capture were encoded with no size
budget, so a phone photo's marked PNG (6.23 MiB from a 1.9 MB JPEG) was refused by the reference store at Send. Every
writer (``markup_ui.capture_view``, ``markup_send.send``, ``markup_edit_host._encode_then``) encodes here: as it
is when it fits, else stepped down by one whole factor on both sides (block means of the 8-bit pixels), chosen from
how far over the limit the last encode was, until it fits. No ``bpy``: a worker thread runs it.
"""

import math

from . import markup_resize, view_pixels
from .ui_text import n_
from .view_png import encode_png

TOO_LARGE = n_("This picture can't be made small enough to attach")
HEADROOM = 1.15   # A smaller picture's PNG shrinks a little less than its pixel count.


def encode_within(rgba, width, height, limit, *, alpha=False):
    """``(png, width, height)`` for 8-bit RGBA rows (bottom row first), at most ``limit`` bytes; ``alpha`` keeps the
    transparency (colour type 6), else RGB. ValueError(TOO_LARGE) when not even a one-pixel side would fit."""
    import numpy
    rows = numpy.frombuffer(memoryview(rgba).cast("B"), dtype=numpy.uint8).reshape(height, width, 4)
    while True:
        h, w = rows.shape[:2]
        data = markup_resize.rgba_png(rows.tobytes(), w, h) if alpha else encode_png(rows.tobytes(), w, h)
        if len(data) <= limit:
            return data, w, h
        step = max(2, math.ceil(math.sqrt(len(data) / limit * HEADROOM)))
        if min(w, h) // step < 1:
            raise ValueError(TOO_LARGE)
        rows = numpy.ascontiguousarray((view_pixels.box_downscale(rows, step) + 0.5).astype(numpy.uint8))
