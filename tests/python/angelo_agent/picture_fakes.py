# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for Blender's ``imbuf`` module, and the picture files the host tests plant.

``view_picture`` decodes, crops and scales through ``imbuf``; this module does
the same on NumPy arrays, so the host suite runs the real code that decides
what a model is shown: the crop's corners, the checkerboard under transparent
pixels, the PNG it encodes. Only what that code uses is modelled. Pictures are
8-bit PNGs with filter 0 (what ``png_bytes`` and ``view_png`` write); rows are
kept bottom first, as Blender keeps them. Scaling down by a whole number
averages the block (a box filter); other sizes pick the nearest pixel.
"""

import contextlib
import struct
import zlib
from types import ModuleType

import numpy

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_bytes(pixels, depth=8):
    """An 8-bit RGB or RGBA PNG of ``pixels`` (height, width, 3 or 4; top row first). ``depth`` only labels the header."""
    pixels = numpy.ascontiguousarray(pixels, dtype=numpy.uint8)
    height, width, channels = pixels.shape
    rows = b"".join(b"\0" + pixels[row].tobytes() for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, depth, 6 if channels == 4 else 2, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(rows, 1)) + _chunk(b"IEND", b"")


def png_header(width, height, depth=8):
    """The first bytes of a PNG of this size: enough for a header check, not decodable."""
    header = struct.pack(">IIBBBBB", width, height, depth, 2, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header)


def jpeg_header(width, height):
    comment = b"\xff\xfe" + struct.pack(">H", 6) + b"test"
    return b"\xff\xd8" + comment + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width) + b"\x03" + bytes(9)


def exr_header(width, height, origin=(0, 0)):
    """The first bytes of an OpenEXR file whose display window is this size."""
    def attribute(name, kind, value):
        return name + b"\0" + kind + b"\0" + struct.pack("<i", len(value)) + value
    window = struct.pack("<4i", origin[0], origin[1], origin[0] + width - 1, origin[1] + height - 1)
    return (b"\x76\x2f\x31\x01" + struct.pack("<I", 2) + attribute(b"channels", b"chlist", b"R\0" + bytes(16) + b"\0")
            + attribute(b"compression", b"compression", b"\x03") + attribute(b"dataWindow", b"box2i", window)
            + attribute(b"displayWindow", b"box2i", window) + b"\0")


def read_png(data):
    """RGBA rows (top row first) of a PNG written by ``png_bytes`` or ``view_png.encode_png``."""
    if data[:8] != SIGNATURE:
        raise ValueError("not a PNG")
    offset, body, size = 8, b"", None
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        chunk = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            width, height, _depth, colour = struct.unpack(">IIBB", chunk[:10])
            size = (width, height, 4 if colour == 6 else 3)
        elif kind == b"IDAT":
            body += chunk
        offset += 12 + length
    width, height, channels = size
    rows = numpy.frombuffer(zlib.decompress(body), dtype=numpy.uint8).reshape(height, 1 + width * channels)
    if rows[:, 0].any():
        raise ValueError("only filter 0 rows are read")
    pixels = rows[:, 1:].reshape(height, width, channels)
    if channels == 4:
        return pixels.copy()
    return numpy.concatenate([pixels, numpy.full((height, width, 1), 255, dtype=numpy.uint8)], axis=2)


class ImBuf:
    """``imbuf.types.ImBuf`` for an 8-bit picture: rows bottom first, straight alpha."""

    buffer_type = "BYTE"

    def __init__(self, pixels):
        self.rows = numpy.ascontiguousarray(pixels[::-1])
        self.freed = False

    @property
    def size(self):
        return self.rows.shape[1], self.rows.shape[0]

    def crop(self, low, high):  # Inclusive corners, from the bottom-left pixel.
        self.rows = numpy.ascontiguousarray(self.rows[low[1]:high[1] + 1, low[0]:high[0] + 1])

    def resize(self, size, method="FAST"):
        width, height = size
        old_height, old_width = self.rows.shape[:2]
        if method == "BILINEAR" and old_height % height == 0 and old_width % width == 0:
            blocks = self.rows.reshape(height, old_height // height, width, old_width // width, 4).astype(numpy.float64)
            self.rows = (blocks.mean(axis=(1, 3)) + 0.5).astype(numpy.uint8)
        else:
            self.rows = numpy.ascontiguousarray(
                self.rows[numpy.arange(height) * old_height // height][:, numpy.arange(width) * old_width // width])

    def convert_buffer_type(self, _kind):
        pass

    @contextlib.contextmanager
    def with_buffer(self, write=False, region=None):
        yield self.rows

    def free(self):
        self.rows, self.freed = None, True


def imbuf_module():
    """A module object to plant as ``sys.modules["imbuf"]``; ``loaded`` lists what it opened."""
    module = ModuleType("imbuf")
    module.loaded = []

    def load(filepath):
        with open(filepath, "rb") as handle:
            buffer = ImBuf(read_png(handle.read()))
        module.loaded.append(buffer)
        return buffer
    module.load = load
    return module
