# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, valid image files written byte by byte, and torn copies of them, for the frame-completeness tests.

The OpenEXR files follow the file layout of the OpenEXR specification
(``openexrfilelayout``): magic and version, header attributes, chunk offset
tables, chunks. The native suite checks the same rules on files Blender writes.
"""

import struct
import zlib


def png(width=2, height=2):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + b"\x80" * (3 * width) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def jpeg():
    return b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x01" * 40 + b"\xff\xd9"


def _attribute(name, kind, value):
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<i", len(value)) + value


def _header(width, height, compression=0, extra=b""):
    channels = b"R\0" + struct.pack("<iB3xii", 1, 0, 1, 1) + b"\0"
    window = struct.pack("<4i", 0, 0, width - 1, height - 1)
    return (_attribute("channels", "chlist", channels) + _attribute("compression", "compression", bytes([compression]))
            + _attribute("dataWindow", "box2i", window) + _attribute("displayWindow", "box2i", window)
            + _attribute("lineOrder", "lineOrder", b"\0") + _attribute("pixelAspectRatio", "float", struct.pack("<f", 1))
            + _attribute("screenWindowCenter", "v2f", struct.pack("<2f", 0, 0))
            + _attribute("screenWindowWidth", "float", struct.pack("<f", 1)) + extra)


def exr(width=4, height=3, *, multipart=0, filled=True):
    """A scanline OpenEXR file without compression; ``multipart`` parts (0: single part); ``filled=False``: the
    offset tables as OpenEXR leaves them until the file is closed (zeros)."""
    row = b"\x00\x3c" * width   # half 1.0
    if not multipart:
        head = b"\x76\x2f\x31\x01" + struct.pack("<i", 2) + _header(width, height) + b"\0"
        table_at = len(head)
        chunks, offsets, position = b"", [], table_at + 8 * height
        for y in range(height):
            offsets.append(position)
            piece = struct.pack("<ii", y, len(row)) + row
            chunks += piece
            position += len(piece)
        table = b"".join(struct.pack("<Q", offset if filled else 0) for offset in offsets)
        return head + table + chunks
    head = b"\x76\x2f\x31\x01" + struct.pack("<i", 2 | 0x1000)
    for part in range(multipart):
        head += _header(width, height, extra=_attribute("name", "string", b"layer%d" % part)
                        + _attribute("type", "string", b"scanlineimage")
                        + _attribute("chunkCount", "int", struct.pack("<i", height))) + b"\0"
    head += b"\0"
    position = len(head) + 8 * height * multipart
    tables, chunks = b"", b""
    for part in range(multipart):
        for y in range(height):
            tables += struct.pack("<Q", position if filled else 0)
            piece = struct.pack("<iii", part, y, len(row)) + row
            chunks += piece
            position += len(piece)
    return head + tables + chunks


def tiled_exr():
    """A single-part tiled file without chunkCount: its chunk count needs its level layout (not checked: None)."""
    tiles = _attribute("tiles", "tiledesc", struct.pack("<IIB", 2, 2, 0))
    return b"\x76\x2f\x31\x01" + struct.pack("<i", 2 | 0x200) + _header(4, 4, extra=tiles) + b"\0" + b"\0" * 32
