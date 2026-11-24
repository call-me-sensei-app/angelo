# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A picture's width and height from its file, as the viewer opens it (2.1.0, ``markup_overlay``).

Break-it of 2298d608dd1, S9: the size was read from the file's first 64 KiB only, so a JPEG whose frame header comes
after a large EXIF (with its embedded preview), XMP, Photoshop or ICC block did not open. PNG and WebP keep their size
in the first bytes (``team_library_files.image_size``); a JPEG's segments are walked by seeking past each one's
length to its frame header, reading a few bytes per segment. No ``bpy``.
"""

import struct

from . import markup_doc as md
from .ui_text import n_

# Why a picture doesn't open: English for the log and the agent, shown translated (re-review of b5808fd5a2e, S6).
SIZE_UNREADABLE = n_("This picture's size can't be read")
TOO_WIDE = n_("This picture is wider or taller than Angelo opens (16,384 px at most)")

HEAD = 65536
SEGMENTS = 4096   # A JPEG has a few dozen; a file that never reaches its frame header within this many is refused.
FRAMES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}   # Start-of-frame markers (DHT, JPG and DAC excluded).


def signed(path):
    """Whether the file starts as a PNG, JPEG or WebP does: one whose header must be read before a pixel is."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    return head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8")) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")


def header_size(path):
    """``(width, height)`` as stored in the file's header (PNG, JPEG, WebP), or None for another format (Blender
    reads many more): read before any pixel is decoded (re-review of markup 4e712847d3b, S1). OSError if unreadable."""
    from .team_library_files import image_size
    with open(path, "rb") as handle:
        head = handle.read(HEAD)
        size = image_size(head)
        if size is None and head.startswith(b"\xff\xd8"):
            size = _jpeg(handle)[0]
    return tuple(size) if size else None


def orientation(path):
    """A JPEG's EXIF orientation (1 to 8; 1 where it has none, or for any other format): how its stored pixels turn
    to be seen upright (re-review of markup 4e712847d3b, S3: a phone portrait opened sideways)."""
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"\xff\xd8":
                return 1
            return _jpeg(handle)[1]
    except OSError:
        return 1


def turned(size, turn):
    """``size`` (width, height) as seen after EXIF orientation ``turn``: 5 to 8 swap the sides."""
    return (size[1], size[0]) if turn in (5, 6, 7, 8) else tuple(size)


def picture_size(path):
    """``(width, height)`` of a PNG, JPEG or WebP at ``path`` as it is seen (its EXIF orientation applied), each side
    1 to ``markup_doc.MAX_SIDE`` and at most ``studio_tile_previews.PIXEL_LIMIT`` pixels, the bound every decode
    holds before reading a pixel; else ValueError with the sentence that says why (an ``n_`` constant, translated where
    it is shown)."""
    from .studio_tile_previews import NO_PIXELS, PIXEL_LIMIT, TOO_MANY_PIXELS
    size = header_size(path)
    if not size:
        raise ValueError(SIZE_UNREADABLE)
    if not all(size):
        raise ValueError(NO_PIXELS)
    if not all(v <= md.MAX_SIDE for v in size):
        raise ValueError(TOO_WIDE)
    if size[0] * size[1] > PIXEL_LIMIT:
        raise ValueError(TOO_MANY_PIXELS)
    return turned(size, orientation(path))


def _exif_orientation(payload):
    """Tag 0x0112 of an APP1 Exif payload's first directory, or 1."""
    if not payload.startswith(b"Exif\x00\x00") or len(payload) < 14:
        return 1
    tiff = payload[6:]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None:
        return 1
    try:
        first = struct.unpack(order + "I", tiff[4:8])[0]
        count = struct.unpack(order + "H", tiff[first:first + 2])[0]
        for index in range(min(count, 512)):
            entry = tiff[first + 2 + 12 * index:first + 14 + 12 * index]
            tag, kind = struct.unpack(order + "HH", entry[:4])
            if tag == 0x0112 and kind == 3:
                value = struct.unpack(order + "H", entry[8:10])[0]
                return value if 1 <= value <= 8 else 1
    except struct.error:
        return 1
    return 1


def _jpeg(handle):
    """``(size or None, orientation)`` from a JPEG's segments, the handle just past its start marker."""
    handle.seek(2)
    turn = 1
    for _ in range(SEGMENTS):
        byte = handle.read(1)
        while byte == b"\xff":   # Fill bytes before a marker.
            marker = handle.read(1)
            if marker != b"\xff":
                break
        else:
            return None, turn
        kind = marker[0] if marker else None
        if kind is None:
            return None, turn
        if kind == 0xD8 or kind == 0x01 or 0xD0 <= kind <= 0xD7:
            continue
        length = handle.read(2)
        if len(length) < 2:
            return None, turn
        length = struct.unpack(">H", length)[0]
        if kind in FRAMES:
            frame = handle.read(5)
            if len(frame) < 5:
                return None, turn
            height, width = struct.unpack(">HH", frame[1:5])
            return (width, height), turn
        if length < 2:
            return None, turn
        if kind == 0xE1 and turn == 1:
            payload = handle.read(length - 2)
            turn = _exif_orientation(payload)
            continue
        handle.seek(length - 2, 1)
    return None, turn
