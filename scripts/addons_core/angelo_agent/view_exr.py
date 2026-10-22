# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A bounded OpenEXR reader for the render passes ``angelo_view`` shows, and a minimal writer. NumPy and stdlib.

Blender reads a multilayer OpenEXR's first layer and pass into an Image, and
Python cannot ask it for another (no ImageUser reaches ``Image.pixels`` or
``save_render``). So the passes a model asks for (``view_passes``) are read
here, from a file Blender wrote (doc/angelo/view_sources.rst, "Render
passes and view layers").

* **What is read here.** Scanline images, single-part (interleaved) or
  multi-part (Blender's default: one part a pass), uncompressed or with RLE,
  ZIPS or ZIP compression (zlib, predictor, byte interleave), HALF, FLOAT and
  UINT channels without sub-sampling. Only the requested channels are kept, and
  in a multi-part file only the requested part's chunks are read.
* **What OpenImageIO reads** (``view_exr_codecs``): every other part that is
  not deep: PIZ, PXR24, B44, B44A, DWAA, DWAB and HTJ2K compression, and tiled
  parts. It runs in a child process of the bundled interpreter, so a damaged
  file can stop that child but never Angelo. This file still parses every
  header itself (bounded, no library), so the parts and channels a model is
  told about never depend on the library.
* **What is refused, with the reason.** Deep files, sub-sampled channels,
  pictures over ``MAX_PIXELS``, and a part this reader does not handle when
  the child decoder is unavailable or fails. Every size and offset is checked
  against the file before it is used, so a damaged file raises ``ExrError``
  instead of allocating.

Rows are returned top row first (OpenEXR's increasing y). ``write_rgba`` writes
an uncompressed single-part RGBA float file, which Blender's own loader reads.
"""

import struct
import zlib

MAGIC = b"\x76\x2f\x31\x01"
COMPRESSION_NAMES = ("NONE", "RLE", "ZIPS", "ZIP", "PIZ", "PXR24", "B44", "B44A", "DWAA", "DWAB", "HTJ2K256",
                     "HTJ2K32")
LINES_PER_CHUNK = {0: 1, 1: 1, 2: 1, 3: 16}  # The compressions read here; every other goes to view_exr_codecs.
SAMPLES = {0: ("<u4", 4), 1: ("<f2", 2), 2: ("<f4", 4)}  # UINT, HALF, FLOAT.
MAX_PIXELS = 36_000_000      # view_picture.MAX_FLOAT_PIXELS: the data window of one part.
MAX_ATTRIBUTE = 1 << 20      # Bytes of an attribute that is parsed; larger ones (manifests) are skipped.
MAX_PARTS = 1024
MAX_NAME = 256               # OpenEXR's long-name limit (255 characters and the terminator).
WANTED = frozenset({"channels", "compression", "dataWindow", "displayWindow", "name", "type", "chunkCount", "view"})


class ExrError(ValueError):
    """A file this reader does not read, with the reason a model can be told."""


class Part:
    """One image of the file: its channels in stored order, compression, windows and chunk offsets."""

    def __init__(self, attributes, index):
        self.index = index
        self.name = attributes.get("name", "")
        self.view = attributes.get("view", "")
        self.kind = attributes.get("type", "scanlineimage")
        self.channels = attributes.get("channels")
        self.compression = attributes.get("compression")
        self.data_window = attributes.get("dataWindow")
        self.display_window = attributes.get("displayWindow", self.data_window)
        self.chunk_count = attributes.get("chunkCount")
        self.offsets = []
        if self.channels is None or self.compression is None or self.data_window is None:
            raise ExrError("an OpenEXR part lacks its channels, compression or data window")
        # Read by this module's own decoder (scanlines, no compression or RLE/ZIPS/ZIP); any other part is OpenImageIO's.
        self.native = self.kind in ("scanlineimage", "") and self.compression in LINES_PER_CHUNK

    @property
    def size(self):
        x0, y0, x1, y1 = self.data_window
        return x1 - x0 + 1, y1 - y0 + 1


def _read(handle, count):
    data = handle.read(count)
    if len(data) != count:
        raise ExrError("the OpenEXR file ends early")
    return data


def _string(handle):
    data = bytearray()
    while True:
        byte = handle.read(1)
        if not byte:
            raise ExrError("the OpenEXR header ends early")
        if byte == b"\0":
            return data.decode("utf-8", "replace")
        data += byte
        if len(data) >= MAX_NAME:
            raise ExrError("an OpenEXR header name is too long")


def _channels(value):
    channels, offset = [], 0
    while offset < len(value) and value[offset]:
        end = value.find(b"\0", offset)
        if end < 0 or end + 17 > len(value):
            raise ExrError("the OpenEXR channel list is damaged")
        kind, _linear, x_sampling, y_sampling = struct.unpack_from("<iB3xii", value, end + 1)
        channels.append((value[offset:end].decode("utf-8", "replace"), kind, x_sampling, y_sampling))
        offset = end + 17
    return channels


# The kind each wanted attribute must declare; a file that declares another kind has no such attribute for this reader.
KINDS = {"channels": "chlist", "compression": "compression", "dataWindow": "box2i", "displayWindow": "box2i",
         "name": "string", "type": "string", "chunkCount": "int", "view": "string"}


def _parse(name, kind, value):
    if kind != KINDS[name]:
        return None
    if name == "channels":
        return _channels(value)
    if name == "compression":
        return value[0] if len(value) == 1 else None
    if kind == "box2i":
        return struct.unpack("<4i", value) if len(value) == 16 else None
    if kind == "string":
        return value.decode("utf-8", "replace")
    return struct.unpack("<i", value)[0] if len(value) == 4 else None


def _header(handle, size):
    """One header's wanted attributes; None for the empty header that ends a multi-part list."""
    attributes, first = {}, True
    while True:
        name = _string(handle)
        if not name:
            return None if first else attributes
        first = False
        kind = _string(handle)
        length = struct.unpack("<i", _read(handle, 4))[0]
        if length < 0 or handle.tell() + length > size:
            raise ExrError("an OpenEXR attribute runs past the end of the file")
        if name in WANTED and length <= MAX_ATTRIBUTE:
            value = _parse(name, kind, _read(handle, length))
            if value is not None:   # A wrong kind or length is no attribute: the required ones then refuse the part.
                attributes[name] = value
        else:
            handle.seek(length, 1)


def read_parts(handle):
    """The file's parts with their chunk offsets; ``(parts, multipart)``. ``handle`` is a binary file."""
    handle.seek(0, 2)
    size = handle.tell()
    handle.seek(0)
    head = handle.read(8)
    if len(head) < 8 or head[:4] != MAGIC:
        raise ExrError("the file is not OpenEXR")
    flags = struct.unpack("<I", head[4:])[0]
    if flags & 0x800:
        raise ExrError("the file holds deep data, which a view does not show")
    multipart = bool(flags & 0x1000)
    parts = []
    while len(parts) < MAX_PARTS:
        attributes = _header(handle, size)
        if attributes is None:
            break
        if flags & 0x200 and not multipart:
            attributes["type"] = "tiledimage"  # A single-part tiled file says so in its flags, not in a type attribute.
        parts.append(Part(attributes, len(parts)))
        if not multipart:
            break
    if not parts:
        raise ExrError("the file has no image")
    lost = False   # The offset tables follow one another: once one's length is unknown, no later table can be found.
    for part in parts:
        count = part.chunk_count
        if not lost and not part.native:   # Its chunks are OpenImageIO's, but its table must be passed to find the next.
            if count is None or not 0 < count <= size // 8:
                lost = True
            else:
                handle.seek(8 * count, 1)
            continue
        if lost:
            part.native = False
            continue
        if count is None:
            count = -(-part.size[1] // LINES_PER_CHUNK[part.compression])
        if not 0 < count <= size // 8:
            raise ExrError("the OpenEXR offset table is damaged")
        table = _read(handle, 8 * count)
        part.offsets = list(struct.unpack("<%dQ" % count, table))
    return parts, multipart


def compression_name(part):
    code = part.compression
    return COMPRESSION_NAMES[code] if 0 <= code < len(COMPRESSION_NAMES) else "an unknown method %d" % code


def _rle(data, expected):
    out, index = bytearray(), 0
    while index < len(data) and len(out) < expected:
        count = data[index] - 256 if data[index] > 127 else data[index]
        if count < 0:
            out += data[index + 1:index + 1 - count]
            index += 1 - count
        else:
            out += data[index + 1:index + 2] * (count + 1)
            index += 2
    return bytes(out)


def _reordered(data):
    """Undo OpenEXR's byte predictor, then its interleave of the two halves."""
    import numpy
    deltas = numpy.frombuffer(data, dtype=numpy.uint8).copy()
    deltas[1:] -= 128  # Wraps modulo 256, as the predictor does.
    values = numpy.cumsum(deltas, dtype=numpy.uint8)
    out = numpy.empty_like(values)
    half = (len(values) + 1) // 2
    out[0::2], out[1::2] = values[:half], values[half:]
    return out.tobytes()


def _decompress(data, compression, expected):
    if len(data) == expected:
        return data  # OpenEXR stores a chunk as it is when compression would not make it smaller.
    if compression in (2, 3):
        try:
            raw = zlib.decompressobj().decompress(data, expected)
        except zlib.error as error:
            raise ExrError("an OpenEXR chunk does not decompress (%s)" % error) from None
    elif compression == 1:
        raw = _rle(data, expected)
    else:
        raise ExrError("an OpenEXR chunk is compressed although the file says it is not")
    if len(raw) != expected:
        raise ExrError("an OpenEXR chunk decompresses to the wrong size")
    return _reordered(raw)


def read_channels(path, part_index, names):
    """``{name: float32 rows (height, width), top row first}`` of the named channels of one part."""
    import numpy
    with open(path, "rb") as handle:
        parts, multipart = read_parts(handle)
        handle.seek(0, 2)
        size = handle.tell()
        part = parts[part_index]
        if part.kind not in ("scanlineimage", "tiledimage", ""):
            raise ExrError("the part %r is %s, which a view does not show" % (part.name[:64], part.kind))
        width, height = part.size
        if width < 1 or height < 1 or width * height > MAX_PIXELS:
            raise ExrError("the picture is %dx%d: more than the %d million pixels a view reads"
                           % (width, height, MAX_PIXELS // 1_000_000))
        fields = []
        for index, (name, kind, x_sampling, y_sampling) in enumerate(part.channels):
            if kind not in SAMPLES or (x_sampling, y_sampling) != (1, 1):
                raise ExrError("channel %r is sub-sampled or of an unknown type" % name[:64])
            fields.append(("c%d" % index, SAMPLES[kind][0], (width,)))
        line = numpy.dtype(fields)
        wanted = {name: "c%d" % index for index, (name, *_rest) in enumerate(part.channels) if name in names}
        missing = [name for name in names if name not in wanted]
        if missing:
            raise ExrError("the part %r has no channel %r" % (part.name[:64], missing[0][:64]))
        if not part.native:
            from . import view_exr_codecs
            return view_exr_codecs.decode(path, part, list(names), compression_name(part))
        out = {name: numpy.zeros((height, width), dtype=numpy.float32) for name in names}
        lines, top = LINES_PER_CHUNK[part.compression], part.data_window[1]
        for offset in part.offsets:
            if not 0 < offset < size:
                raise ExrError("an OpenEXR chunk offset lies outside the file")
            handle.seek(offset)
            if multipart and struct.unpack("<i", _read(handle, 4))[0] != part_index:
                raise ExrError("an OpenEXR chunk belongs to another part")
            y, length = struct.unpack("<ii", _read(handle, 8))
            count = min(lines, part.data_window[3] - y + 1)
            if not top <= y <= part.data_window[3] or (y - top) % lines or not 0 < length <= count * line.itemsize:
                raise ExrError("an OpenEXR chunk is damaged")
            raw = _decompress(_read(handle, length), part.compression, count * line.itemsize)
            rows = numpy.frombuffer(raw, dtype=line, count=count)
            for name, field in wanted.items():
                out[name][y - top:y - top + count] = rows[field]
        return out


def write_rgba(path, rows):
    """An uncompressed single-part OpenEXR of float RGBA ``rows`` (height, width, 4), top row first."""
    import numpy
    rows = numpy.ascontiguousarray(rows, dtype=numpy.float32)
    height, width = rows.shape[:2]

    def attribute(name, kind, value):
        return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<i", len(value)) + value
    names = ("A", "B", "G", "R")  # Stored sorted by name.
    channels = b"".join(name.encode() + b"\0" + struct.pack("<iB3xii", 2, 0, 1, 1) for name in names) + b"\0"
    window = struct.pack("<4i", 0, 0, width - 1, height - 1)
    header = (MAGIC + struct.pack("<I", 2) + attribute("channels", "chlist", channels)
              + attribute("compression", "compression", b"\0") + attribute("dataWindow", "box2i", window)
              + attribute("displayWindow", "box2i", window) + attribute("lineOrder", "lineOrder", b"\0")
              + attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0))
              + attribute("screenWindowCenter", "v2f", struct.pack("<2f", 0.0, 0.0))
              + attribute("screenWindowWidth", "float", struct.pack("<f", 1.0)) + b"\0")
    line_bytes = width * 4 * 4
    start = len(header) + 8 * height
    offsets = struct.pack("<%dQ" % height, *(start + y * (8 + line_bytes) for y in range(height)))
    order = [3, 2, 1, 0]  # Channel A, B, G, R of each RGBA pixel.
    with open(path, "wb") as handle:
        handle.write(header + offsets)
        for y in range(height):
            handle.write(struct.pack("<ii", y, line_bytes))
            handle.write(numpy.ascontiguousarray(rows[y][:, order].T).tobytes())
