# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a file a render wrote is whole: read from its own bytes, never from its presence. No Blender dependency.

Blender writes each frame straight to its final name (``BKE_image_render_write``;
no temporary name and rename), and a failed write (a full disk) only stops the
animation (``RE_RenderAnim`` sets ``G.is_break``). A crash, a quit or a full disk
during a write therefore leaves a file under the frame's real name that holds
only part of the frame. Counting a frame as finished because its file exists
would publish that torn file as a whole frame (long_jobs.rst, "Keeping frames
after a crash").

``whole(read, size, extension)`` answers True (the format's own end is there),
False (it is not: torn, empty or not that format) or None (a format this module
cannot check). Each check reads a few bytes at the head and tail, plus an
OpenEXR file's header and offset tables, so checking a long sequence costs
little next to rendering it:

* PNG ends with its IEND chunk; JPEG with its EOI marker.
* TIFF names its first directory in its header, written when the file is closed;
  the directory and every strip or tile must end inside the file.
* OpenEXR writes its chunk offset table as zeros first and fills it in when the
  file is closed (``OutputFile``'s destructor). Every offset must point past the
  header and inside the file, and the chunk at the largest offset must end
  inside it. Scanline and tiled parts, single or multipart (a multilayer or
  multi-view file), are read; deep data is None, and so is a header larger than
  ``MAX_EXR_HEADER`` (only its first part is read).

A file that can't be read now (``check_entry``: a permission or I/O error) has no
answer: it raises ``OSError``, never False. False means the file itself is not
whole, and a caller removes such a frame; an unreadable file may be a whole one.
"""

import os
import struct

from . import checkpoint_filesystem as cf

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
EXR_MAGIC = b"\x76\x2f\x31\x01"
MAX_EXR_HEADER = 1 << 20      # Bytes of OpenEXR header read at most; a larger one is not checked (None).
MAX_EXR_CHUNKS = 1 << 22      # Offsets read at most per file.
# Scan lines per chunk of each OpenEXR compression (ImfCompression.h, openexr_compression.h).
EXR_LINES = {0: 1, 1: 1, 2: 1, 3: 16, 4: 32, 5: 16, 6: 32, 7: 32, 8: 32, 9: 256, 10: 256, 11: 32}
EXR_TILED, EXR_LONG_NAMES, EXR_DEEP, EXR_MULTIPART = 0x200, 0x400, 0x800, 0x1000


class _Unchecked(Exception):
    """The header goes on past the ``MAX_EXR_HEADER`` bytes read: the file is not checked (None), never torn."""


def whole(read, size, extension):
    """True, False or None (not checkable) for one file of ``size`` bytes; ``read(offset, count)`` returns its bytes."""
    if size <= 0:
        return False
    extension = extension.lower()
    try:
        if extension == ".png":
            return size >= 20 and read(0, 8) == PNG_SIGNATURE and read(size - 12, 12) == PNG_END
        if extension in (".jpg", ".jpeg"):
            return size >= 4 and read(0, 2) == b"\xff\xd8" and read(size - 2, 2) == b"\xff\xd9"
        if extension == ".exr":
            return _exr_whole(read, size)
        if extension in (".tif", ".tiff"):
            return _tiff_whole(read, size)
    except _Unchecked:
        return None
    except (struct.error, ValueError, IndexError, KeyError, OverflowError, MemoryError):
        return False
    return None


def _exr_headers(read, size, version):
    """``(headers, end)``: each part's attributes as ``{name: (type, bytes)}``, and where the offset tables start.

    A header that runs past the end of what was read is cut short (False) when the whole file was read, and not
    checked (``_Unchecked``) when only its first ``MAX_EXR_HEADER`` bytes were: a valid header may be that large.
    """
    data = read(8, min(size - 8, MAX_EXR_HEADER))
    capped = size - 8 > MAX_EXR_HEADER
    position, headers = 0, []
    multipart = bool(version & EXR_MULTIPART)

    def beyond(what):
        return _Unchecked() if capped else ValueError(what + " beyond what was read")
    while True:
        if position >= len(data):
            raise beyond("header")
        if data[position] == 0:   # An empty name ends a header, or (multipart) the list of headers.
            position += 1
            if not multipart or not headers or headers[-1] == {}:
                break
            headers.append({})
            continue
        if not headers:
            headers.append({})
        name_end = data.find(b"\0", position)
        type_end = data.find(b"\0", name_end + 1) if name_end >= 0 else -1
        if type_end < 0 or type_end + 5 > len(data):
            raise beyond("attribute")
        (length,) = struct.unpack_from("<i", data, type_end + 1)
        start = type_end + 5
        if length < 0:
            raise ValueError("negative attribute size")
        if start + length > len(data):
            raise beyond("attribute")
        headers[-1][data[position:name_end].decode("latin-1")] = (data[name_end + 1:type_end].decode("latin-1"),
                                                                 data[start:start + length])
        position = start + length
    if multipart and headers and headers[-1] == {}:
        headers.pop()
    return headers, 8 + position


def _chunks(header, tiled):
    """How many chunks one part's offset table lists, or None when this module can't tell."""
    if "chunkCount" in header:
        (count,) = struct.unpack("<i", header["chunkCount"][1])
        return count
    kind = header.get("type", ("", b""))[1].rstrip(b"\0")
    if tiled or kind in (b"tiledimage", b"deepscanline", b"deeptile"):
        return None   # A single-part tiled file without chunkCount needs its level layout; Blender writes none.
    xmin, ymin, xmax, ymax = struct.unpack("<4i", header["dataWindow"][1])
    lines = EXR_LINES.get(header["compression"][1][0])
    if lines is None or ymax < ymin:
        return None
    return (ymax - ymin + lines) // lines


def _exr_whole(read, size):
    if size < 8 or read(0, 4) != EXR_MAGIC:
        return False
    (version,) = struct.unpack("<i", read(4, 4))
    if version & EXR_DEEP:
        return None
    headers, position = _exr_headers(read, size, version)
    if not headers:
        return False
    multipart = bool(version & EXR_MULTIPART)
    parts = []
    for header in headers:
        kind = header.get("type", ("", b""))[1].rstrip(b"\0")
        if kind.startswith(b"deep"):
            return None
        count = _chunks(header, bool(version & EXR_TILED) and not multipart)
        if count is None or not 0 < count <= MAX_EXR_CHUNKS:
            return None
        parts.append((count, kind == b"tiledimage" or (not multipart and bool(version & EXR_TILED))))
    largest = (0, 0)   # (offset, part)
    for index, (count, _tiled) in enumerate(parts):
        table = read(position, 8 * count)
        position += 8 * count
        if len(table) != 8 * count:
            return False
        for (offset,) in struct.iter_unpack("<Q", table):
            if not position <= offset < size:   # A zero (never filled in) or past the end: the file was not closed.
                return False
            largest = max(largest, (offset, index))
    offset, index = largest
    head = 4 if multipart else 0   # A multipart chunk starts with its part number.
    fields = 4 if parts[index][1] else 1   # Tile x, y, level x, level y; or the first scan line.
    chunk = read(offset, head + 4 * fields + 8)
    if len(chunk) < head + 4 * fields + 4:
        return False
    (length,) = struct.unpack_from("<i", chunk, head + 4 * fields)
    return length >= 0 and offset + head + 4 * fields + 4 + length <= size


# TIFF tags naming where each strip or tile is and how long (TIFF 6.0): offsets, byte counts.
TIFF_DATA = ((273, 279), (324, 325))
TIFF_TYPES = {3: (2, "H"), 4: (4, "I"), 16: (8, "Q")}   # SHORT, LONG, LONG8 (BigTIFF)
MAX_TIFF_STRIPS = 1 << 16   # Strips or tiles read at most; a file naming more is not checked (None).


def _tiff_whole(read, size):
    """A TIFF is whole when its first directory and every strip or tile it names end inside the file. libtiff
    writes the directory and links it from the header when the file is closed, so a torn file has a header that
    points nowhere (0) or past its end."""
    head = read(0, 16)
    order = {b"II": "<", b"MM": ">"}.get(head[:2])
    if order is None:
        return False
    (magic,) = struct.unpack(order + "H", head[2:4])
    big = magic == 43
    if magic not in (42, 43):
        return False
    offset = struct.unpack(order + ("Q" if big else "I"), head[8:16] if big else head[4:8])[0]
    count_size, entry_size = (8, 20) if big else (2, 12)
    if not 8 <= offset < size:
        return False
    (count,) = struct.unpack(order + ("Q" if big else "H"), read(offset, count_size))
    if count == 0 or count > 4096:   # Bounded before anything that size is read.
        return False
    table = read(offset + count_size, count * entry_size)
    if len(table) != count * entry_size:
        return False
    if offset + count_size + count * entry_size + (8 if big else 4) > size:   # Its link to the next directory.
        return False
    tags = {}
    for index in range(count):
        entry = table[index * entry_size:(index + 1) * entry_size]
        tag, kind = struct.unpack(order + "HH", entry[:4])
        number = struct.unpack(order + ("Q" if big else "I"), entry[4:12] if big else entry[4:8])[0]
        tags[tag] = (kind, number, entry[12:] if big else entry[8:])
    pair = next(((offsets, counts) for offsets, counts in TIFF_DATA if offsets in tags and counts in tags), None)
    if pair is None:
        return None   # No strips or tiles named: not a layout this checks.
    values = []
    for tag in pair:
        kind, number, inline = tags[tag]
        if kind not in TIFF_TYPES or not 0 < number <= MAX_TIFF_STRIPS:
            return None
        width, code = TIFF_TYPES[kind]
        data = inline if width * number <= len(inline) else read(
            struct.unpack(order + ("Q" if big else "I"), inline)[0], width * number)
        if len(data) < width * number:
            return False
        values.append(struct.unpack(order + code * number, data[:width * number]))
    return all(start > 0 and start + length <= size for start, length in zip(*values))


def check_entry(folder, name, extension=None):
    """``whole`` for one file of an anchored folder (``checkpoint_filesystem``), read without following a link.

    A link the path backend finds at the name is False. A file that can't be opened or read now raises its
    ``OSError``: whether it is whole is unknown, and the caller keeps it (``job_recovery``). That includes ELOOP: the
    scan passes only regular files here, so a link now (or, on Windows, a placeholder that changed identity) means
    the file changed under it.
    """
    extension = extension if extension is not None else os.path.splitext(name)[1]
    try:
        descriptor = cf._open_file(folder, name, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except ValueError:
        return False
    with os.fdopen(descriptor, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size

        def read(offset, count):
            handle.seek(offset)
            return handle.read(max(0, count))
        return whole(read, size, extension)
