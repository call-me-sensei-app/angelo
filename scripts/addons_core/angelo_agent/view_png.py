# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Encode GPU-read RGBA8 pixels as an opaque RGB PNG. Standard library only.

This never creates a Blender Image or temporary file, and it never touches
``bpy``, so it may run on a worker thread. ``zlib.compress`` releases the GIL
for large buffers. Rows use PNG filter 0 (none): per-byte filters would need a
Python loop or NumPy, and fast compression of flat viewport backgrounds already
dominates the payload size (see performance.rst).
"""

import struct
import zlib

MAX_DIMENSION = 4096
COMPRESSION_LEVEL = 1
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def encode_png(rgba, width, height, *, bottom_up=True, level=COMPRESSION_LEVEL):
    """Return PNG bytes for tightly packed 8-bit RGBA rows.

    GPU texture reads start at the bottom row; ``bottom_up`` flips them into
    PNG's top-down order. Alpha is dropped: native views draw an opaque
    background, so a fourth channel would only add bytes.
    """
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION):
        raise ValueError("PNG dimensions must be integers from 1 to 4096")
    source = memoryview(rgba).cast("B")
    if len(source) != width * height * 4:
        raise ValueError("Pixel buffer size does not match its RGBA8 dimensions")
    rgb = bytearray(width * height * 3)
    # Extended-slice assignment copies one channel at C speed.
    rgb[0::3] = source[0::4]
    rgb[1::3] = source[1::4]
    rgb[2::3] = source[2::4]
    stride = width * 3
    rows = range(height - 1, -1, -1) if bottom_up else range(height)
    view = memoryview(rgb)
    scanlines = b"".join(b"\x00" + view[row * stride:(row + 1) * stride] for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(scanlines, level))
            + _chunk(b"IEND", b""))
