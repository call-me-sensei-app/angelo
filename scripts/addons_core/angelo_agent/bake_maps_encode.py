# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writing a texture set's files: PNG (8 or 16 bits) and OpenEXR (half or float, ZIP). numpy and zlib only.

No Blender here, so a job encodes on a worker thread while Angelo stays responsive
(``job_bake_maps``): zlib and numpy release the interpreter lock while they work.
Arrays are ``(height, width, channels)`` float32, row 0 at the bottom as Blender
stores pixels; files are written top row first, in bands of about ``BAND_BYTES``
of pixels streamed to the file, so encoding holds a band, never a whole copy.
``colour`` maps get the sRGB transfer in PNG (and the sRGB chunk); EXR always
holds linear values.
"""

import io
import struct
import zlib

import numpy

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_COLOUR_TYPES = {1: 0, 2: 4, 3: 2, 4: 6}   # Channels -> grey, grey+alpha, RGB, RGBA.
EXR_MAGIC, EXR_VERSION = 20000630, 2
EXR_HALF, EXR_FLOAT = 1, 2
EXR_ZIP, EXR_ZIP_LINES = 3, 16
LEVEL = 6
BAND_BYTES = 2 ** 20   # Float pixels composed and encoded at once: a file streams out band by band.


def srgb_encode(linear):
    """Linear values to the sRGB transfer, clipped to 0..1 (a new array; its arithmetic in place)."""
    x = numpy.clip(linear, 0.0, 1.0)
    curve = numpy.power(x, 1.0 / 2.4)
    curve *= 1.055
    curve -= 0.055
    straight = x <= 0.0031308
    x *= 12.92
    numpy.copyto(x, curve, where=~straight)
    return x


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def band_rows(width, channels, lines=1):
    """Rows per band: about ``BAND_BYTES`` of float pixels, a multiple of ``lines`` (an EXR's ZIP block)."""
    rows = max(1, BAND_BYTES // max(1, width * channels * 4))
    return max(lines, rows // lines * lines)


def _bands(height, rows):
    """``(top, count)`` of each band, top row first."""
    return [(top, min(rows, height - top)) for top in range(0, height, rows)]


def write_png(out, band, shape, depth=8, colour=False):
    """Write a PNG of ``shape`` (height, width, 1 to 4 channels) at ``depth`` 8 or 16 bits to the file ``out``.
    ``band(top, count)`` gives the float32 pixels of file rows ``top`` to ``top + count``, top row first."""
    height, width, channels = shape
    header = struct.pack(">IIBBBBB", width, height, depth, PNG_COLOUR_TYPES[channels], 0, 0, 0)
    out.write(PNG_SIGNATURE + _chunk(b"IHDR", header))
    if colour:
        out.write(_chunk(b"sRGB", b"\x00"))
    stream, above = zlib.compressobj(LEVEL), None
    for top, count in _bands(height, band_rows(width, channels)):
        pixels = band(top, count)
        values = srgb_encode(pixels) if colour else numpy.clip(pixels, 0.0, 1.0)
        # The alpha of a colour map is coverage, never sRGB-encoded.
        if colour and channels in (2, 4):
            values[..., -1] = numpy.clip(pixels[..., -1], 0.0, 1.0)
        del pixels
        values *= 65535.0 if depth == 16 else 255.0
        numpy.rint(values, out=values)
        raw = values.astype(">u2" if depth == 16 else numpy.uint8).view(numpy.uint8).reshape(count, -1)
        del values
        # The Up filter (each byte minus the one above): cheap, and smooth maps compress far better with it.
        rows = numpy.empty((count, raw.shape[1] + 1), dtype=numpy.uint8)
        rows[:, 0] = 2
        numpy.subtract(raw[1:], raw[:-1], out=rows[1:, 1:])
        if above is None:
            rows[0, 0], rows[0, 1:] = 0, raw[0]
        else:
            numpy.subtract(raw[0], above, out=rows[0, 1:])
        above = raw[-1].copy()
        del raw
        data = stream.compress(rows)
        if data:
            out.write(_chunk(b"IDAT", data))
    out.write(_chunk(b"IDAT", stream.flush()) + _chunk(b"IEND", b""))


def png_bytes(pixels, depth=8, colour=False):
    """A PNG of ``pixels`` (1 to 4 channels, row 0 at the bottom) at ``depth`` 8 or 16 bits."""
    out = io.BytesIO()
    write_png(out, _rows_of(pixels), pixels.shape, depth, colour)
    return out.getvalue()


def _rows_of(pixels):
    """A ``band`` reading an array whose row 0 is at the bottom."""
    height = pixels.shape[0]
    return lambda top, count: pixels[height - top - count:height - top][::-1]


def _attribute(name, kind, data):
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<i", len(data)) + data


def _zip(block):
    """OpenEXR's ZIP of a contiguous array's bytes: split into even and odd halves, delta-coded, then zlib."""
    data = block.reshape(-1).view(numpy.uint8)
    half = (data.size + 1) // 2
    coded = numpy.empty_like(data)
    coded[:half], coded[half:] = data[0::2], data[1::2]
    delta = coded[1:] - coded[:-1]   # Bytes wrap as the format's 16-bit difference plus 128 does.
    delta += 128
    coded[1:] = delta
    del delta
    return zlib.compress(coded, LEVEL)


def write_exr(out, band, shape, depth=16, premultiply=False):
    """Write a scanline OpenEXR of ``shape`` (height, width, channels 1: Y, 3: RGB, 4: RGBA), half (16) or float
    (32), ZIP-compressed, to the seekable file ``out``; ``band`` as for ``write_png``. ``premultiply`` multiplies RGB
    by A as it goes. The offset table is written last, once the blocks' sizes are known."""
    height, width, channels = shape
    names = {1: "Y", 2: "YA", 3: "RGB", 4: "RGBA"}[channels]
    kind, dtype = (EXR_HALF, "<f2") if depth == 16 else (EXR_FLOAT, "<f4")
    order = sorted(range(channels), key=lambda index: names[index])   # EXR stores channels by name.
    chlist = b"".join(names[index].encode() + b"\0" + struct.pack("<iB3xii", kind, 0, 1, 1) for index in order) + b"\0"
    box = struct.pack("<iiii", 0, 0, width - 1, height - 1)
    header = struct.pack("<ii", EXR_MAGIC, EXR_VERSION) + b"".join([
        _attribute("channels", "chlist", chlist), _attribute("compression", "compression", bytes([EXR_ZIP])),
        _attribute("dataWindow", "box2i", box), _attribute("displayWindow", "box2i", box),
        _attribute("lineOrder", "lineOrder", b"\0"), _attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0)),
        _attribute("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0)),
        _attribute("screenWindowWidth", "float", struct.pack("<f", 1.0))]) + b"\0"
    origin = out.tell()
    out.write(header)
    table = out.tell()
    out.write(bytes(8 * -(-height // EXR_ZIP_LINES)))
    offsets = []
    for top, count in _bands(height, band_rows(width, channels, EXR_ZIP_LINES)):
        pixels = band(top, count)
        if premultiply:
            pixels = numpy.concatenate([pixels[..., :3] * pixels[..., 3:4], pixels[..., 3:4]], axis=-1)
        planar = numpy.empty((count, channels, width), dtype=dtype)   # Each line's channels one after another.
        for position, index in enumerate(order):
            planar[:, position] = pixels[..., index]
        del pixels
        for start in range(0, count, EXR_ZIP_LINES):
            block = planar[start:start + EXR_ZIP_LINES]
            packed = _zip(block)
            data = packed if len(packed) < block.nbytes else block
            offsets.append(out.tell() - origin)
            out.write(struct.pack("<ii", top + start, len(packed) if data is packed else block.nbytes))
            out.write(data)
    end = out.tell()
    out.seek(table)
    out.write(struct.pack("<%dQ" % len(offsets), *offsets))
    out.seek(end)


def exr_bytes(pixels, depth=16, premultiply=False):
    """A scanline OpenEXR of ``pixels`` (row 0 at the bottom; 1: Y, 3: RGB, 4: RGBA), half (16) or float (32)."""
    out = io.BytesIO()
    write_exr(out, _rows_of(pixels), pixels.shape, depth, premultiply)
    return out.getvalue()


def write(out, band, shape, image_format, depth, colour):
    """Write the file in ``image_format`` (PNG or OPEN_EXR) to ``out``, band by band (``write_png``).

    OpenEXR's RGBA is premultiplied by alpha (the format's convention, and how Angelo reads an EXR), so a colour
    map with alpha is stored premultiplied there; PNG keeps straight alpha. A data map's fourth channel (Unity's
    smoothness) is no alpha and is never multiplied in.
    """
    if image_format == "OPEN_EXR":
        write_exr(out, band, shape, depth, premultiply=colour and shape[-1] == 4)
    else:
        write_png(out, band, shape, depth, colour)


def encode(pixels, image_format, depth, colour):
    """The file's bytes in ``image_format`` (``write``) of ``pixels``, row 0 at the bottom."""
    out = io.BytesIO()
    write(out, _rows_of(pixels), pixels.shape, image_format, depth, colour)
    return out.getvalue()
