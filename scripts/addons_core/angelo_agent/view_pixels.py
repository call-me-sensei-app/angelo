# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pixel helpers shared by the ``angelo_view`` sources that make their own pictures. NumPy and stdlib only.

The image data-block, UV layout, editor, pass and strip sources
(doc/angelo/view_sources.rst) turn arrays into the one picture file that
``view_finished.shown`` or ``view_capture`` delivers. Rows here are **top row
first**, RGBA, unless a function says otherwise; Blender's buffers are bottom
row first and are flipped once where they are read. Nothing here touches
``bpy``.
"""

import struct
import zlib

CHECKER = (204, 153, 16)  # view_picture.CHECKER: light grey, dark grey, square size in pixels.
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ROW_BLOCK = 512  # Rows converted at a time, so temporary float arrays stay a few megabytes.


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_rgba_png(rows, level=1):
    """PNG bytes (8-bit RGBA, filter 0) of uint8 rows shaped (height, width, 4), top row first."""
    import numpy
    rows = numpy.ascontiguousarray(rows, dtype=numpy.uint8)
    height, width, channels = rows.shape
    if channels != 4 or not 1 <= width <= 65535 or not 1 <= height <= 65535:
        raise ValueError("encode_rgba_png takes RGBA rows of 1 to 65535 pixels a side")
    lines = numpy.zeros((height, width * 4 + 1), dtype=numpy.uint8)  # Column 0: filter type 0 (none).
    lines[:, 1:] = rows.reshape(height, width * 4)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(lines.tobytes(), level))
            + _chunk(b"IEND", b""))


def box_factor(width, height, long_side):
    """The whole-number factor that brings the longer side down to at least ``long_side`` (1: no reduction)."""
    return max(1, max(width, height) // max(1, long_side))


def box_downscale(values, factor):
    """``values`` (height, width, channels) averaged over ``factor`` x ``factor`` blocks; edge remainders dropped."""
    import numpy
    if factor <= 1:
        return values
    height, width = values.shape[0] // factor * factor, values.shape[1] // factor * factor
    blocks = values[:height, :width].reshape(height // factor, factor, width // factor, factor, values.shape[2])
    return blocks.mean(axis=(1, 3), dtype=numpy.float32)


def srgb_encode(linear):
    """Scene-linear values through the sRGB transfer function (Blender's Standard view on an sRGB display)."""
    import numpy
    linear = numpy.clip(numpy.nan_to_num(linear, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    return numpy.where(linear <= 0.0031308, linear * 12.92, 1.055 * numpy.power(linear, 1 / 2.4) - 0.055)


def to_bytes(values):
    """Values from 0 to 1 as rounded uint8 (outside clipped, NaN black)."""
    import numpy
    return (numpy.clip(numpy.nan_to_num(values, nan=0.0), 0.0, 1.0) * 255.0 + 0.5).astype(numpy.uint8)


def checkerboard(height, width):
    """An opaque grey checkerboard (top row first), what transparent pixels and missing textures show."""
    import numpy
    light, dark, square = CHECKER
    cells = (numpy.arange(height)[:, None] // square + numpy.arange(width)[None, :] // square) & 1
    board = numpy.empty((height, width, 4), dtype=numpy.uint8)
    board[..., :3] = numpy.where(cells, dark, light)[..., None]
    board[..., 3] = 255
    return board


# A 3x5 bitmap of each character a frame label uses (rows top first, 1 = ink).
_GLYPHS = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
    "-": ("000", "000", "111", "000", "000"),
}


def draw_label(rows, text, x, y, scale=2):
    """Write ``text`` (digits and minus) in white on a dark box at (x, y) of top-first RGBA ``rows``, in place."""
    glyph_width, glyph_height = 4 * scale, 5 * scale
    bottom, right = y + glyph_height + 2 * scale, x + glyph_width * len(text) + scale
    rows[y:bottom, x:right, :3] = 20  # Slicing clips the box at the picture's edges.
    for index, character in enumerate(text):
        for row, line in enumerate(_GLYPHS.get(character, _GLYPHS["-"])):
            for column, ink in enumerate(line):
                if ink == "1":
                    top, left = y + scale + row * scale, x + scale + index * glyph_width + column * scale
                    rows[top:top + scale, left:left + scale, :3] = 255
    return rows
