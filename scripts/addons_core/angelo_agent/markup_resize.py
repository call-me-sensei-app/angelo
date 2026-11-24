# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resize in the picture viewer: a crop the person drags on the picture and a scale, planned in its pixels (2.1.0,
doc/angelo/attachment_markup.rst, "Resize").

``plan`` gives the crop (whole pixels, inside the picture, at least ``MIN_SIDE`` a side), the output size and its
label. The output's longest side is at most ``MAX_SIDE`` (``view_png``'s limit), so a large picture is scaled into it
even at 100%. Applying it (``markup_edit_host.resize``) makes a new picture and keeps the original: resampled
here (``bilinear``) and written with its alpha (``rgba_png``), so a background removed stays removed. Pure;
local, free.
"""

import struct
import zlib

from .ui_text import n_

SCALES = (100, 75, 50, 25)
MIN_SIDE = 8
MAX_SIDE = 4096
SIZE_LABEL = n_("{width} × {height} px")
SCALE_LABEL = n_("{percent}%")


def _crop(size, crop):
    w, h = size
    if crop is None:
        return [0, 0, w, h]
    x, y, cw, ch = (float(v) for v in crop)
    x0, x1 = sorted((x, x + cw))
    y0, y1 = sorted((y, y + ch))
    x0, y0 = int(round(min(max(x0, 0.0), w))), int(round(min(max(y0, 0.0), h)))
    x1, y1 = int(round(min(max(x1, 0.0), w))), int(round(min(max(y1, 0.0), h)))
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        raise ValueError("A crop is at least %d pixels a side" % MIN_SIDE)
    return [x0, y0, x1 - x0, y1 - y0]


def plan(size, crop=None, scale=100):
    if scale not in SCALES:
        raise ValueError("Unknown scale %r" % (scale,))
    box = _crop(size, crop)
    factor = scale / 100.0
    factor = min(factor, MAX_SIDE / float(max(box[2], box[3])))
    out = (max(1, int(round(box[2] * factor))), max(1, int(round(box[3] * factor))))
    changed = box != [0, 0, size[0], size[1]] or out != (box[2], box[3])
    return {"crop": box, "size": out, "changed": changed, "label": (SIZE_LABEL, {"width": out[0], "height": out[1]})}


def rgba_png(rgba, width, height):
    """PNG bytes (colour type 6, alpha kept) for 8-bit RGBA rows, bottom row first. No ``bpy``: a worker may run it."""
    source = memoryview(rgba).cast("B")
    stride = width * 4
    if len(source) != stride * height:
        raise ValueError("Pixel buffer size does not match its RGBA8 dimensions")
    rows = b"".join(b"\x00" + source[row * stride:(row + 1) * stride] for row in range(height - 1, -1, -1))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 1))
            + chunk(b"IEND", b""))


def bilinear(grid, width, height):
    """``grid`` (rows, columns, 4) resampled to ``height`` x ``width``; an area average first when shrinking by 2 or
    more, so fine detail doesn't alias."""
    import numpy as np
    step = min(grid.shape[1] // width, grid.shape[0] // height)
    if step >= 2:
        rows, cols = grid.shape[0] // step, grid.shape[1] // step
        grid = grid[:rows * step, :cols * step].reshape(rows, step, cols, step, 4).mean(axis=(1, 3))
    ys = np.linspace(0, grid.shape[0] - 1, height, dtype=np.float32)
    xs = np.linspace(0, grid.shape[1] - 1, width, dtype=np.float32)
    y0, x0 = np.floor(ys).astype(np.int64), np.floor(xs).astype(np.int64)
    y1, x1 = np.minimum(y0 + 1, grid.shape[0] - 1), np.minimum(x0 + 1, grid.shape[1] - 1)
    fy, fx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    top = grid[y0][:, x0] * (1 - fx) + grid[y0][:, x1] * fx
    bottom = grid[y1][:, x0] * (1 - fx) + grid[y1][:, x1] * fx
    return (top * (1 - fy) + bottom * fy).astype(np.float32)
