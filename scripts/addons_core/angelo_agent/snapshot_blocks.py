# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Split a saved .blend file into content-defined segments for deduplication.

Blender 5.x writes stable block identities, so unchanged data serializes to the
same bytes across saves. Cutting the file at its own block headers lets later
snapshots store only the blocks that changed. This module only decides where
to cut: the yielded segments always concatenate to exactly the bytes read, and
callers verify that with a whole-file hash. A header this parser does not
understand, a truncated block or an impossible length never fails a capture;
the remaining bytes simply fall back to fixed-size chunks.

Format references: ``source/blender/blenloader_core/BLO_core_bhead.hh`` and
``BLO_core_blend_header.hh``.

Segment policy (measured on a 590 MB scene, see doc/angelo/performance.rst):

* A block of at least ``STANDALONE_BLOCK_BYTES`` (header included) is its own
  segment, split into ``MAX_SEGMENT_BYTES`` pieces so a partial edit of a large
  array only changes the touched pieces.
* Smaller blocks are grouped. A group starts at every ID block (any code other
  than ``DATA``) and is closed once it reaches ``GROUP_TARGET_BYTES``. One
  object edit therefore changes one small group, while the index stays at a few
  thousand entries per snapshot instead of one per block.
"""

import struct

MAX_SEGMENT_BYTES = 1024 * 1024
STANDALONE_BLOCK_BYTES = 64 * 1024
GROUP_TARGET_BYTES = 16 * 1024

FORMAT_BLOCKS_V1 = "blocks-v1"
FORMAT_BLOCKS_V0 = "blocks-v0"
FORMAT_FIXED = "fixed"

# (code, SDNAnr, old, len, nr): BLEND_FILE_FORMAT_VERSION_1, always little endian.
_LARGE_BHEAD8 = struct.Struct("<4siQqq")
_V1_HEADER = 17


class _Source:
    """Exact reads from a binary handle with a small push-back buffer."""

    def __init__(self, handle):
        self.handle = handle
        self.pending = b""

    def read(self, count):
        if count <= 0:
            return b""
        parts, needed = [], count
        if self.pending:
            parts.append(self.pending[:needed])
            self.pending = self.pending[needed:]
            needed -= len(parts[0])
        while needed:
            data = self.handle.read(needed)
            if not data:
                break
            parts.append(data)
            needed -= len(data)
        return b"".join(parts)

    def unread(self, data):
        self.pending = data + self.pending


def _legacy_layout(header):
    """Decode a pre-5.0 12-byte header into (struct, length field index)."""
    if len(header) < 12 or header[7:8] not in (b"_", b"-") or header[8:9] not in (b"v", b"V"):
        return None
    if not header[9:12].isdigit():
        return None
    order = "<" if header[8:9] == b"v" else ">"
    if header[7:8] == b"_":
        layout = struct.Struct(order + "4siIii")  # BHead4: code, len, old, SDNAnr, nr
    else:
        layout = struct.Struct(order + "4siQii")  # SmallBHead8: code, len, old, SDNAnr, nr
    return layout, 1


def detect_format(header):
    """Return (format name, header bytes, block struct, length field) or fixed."""
    if header[:7] != b"BLENDER":
        return FORMAT_FIXED, 0, None, None
    if (len(header) >= _V1_HEADER and header[7:9] == b"17" and header[9:10] == b"-" and
            header[10:12] == b"01" and header[12:13] == b"v" and header[13:17].isdigit()):
        return FORMAT_BLOCKS_V1, _V1_HEADER, _LARGE_BHEAD8, 3
    legacy = _legacy_layout(header)
    if legacy:
        return FORMAT_BLOCKS_V0, 12, legacy[0], legacy[1]
    return FORMAT_FIXED, 0, None, None


class BlendSegmenter:
    """Iterate over ``handle`` as segments; ``size`` is the expected file size.

    After iteration, ``format`` names the parser used, ``blocks`` counts parsed
    block headers and ``fallback`` explains why fixed chunking took over (empty
    when the whole file was parsed). A shorter-than-expected file yields the
    bytes actually read; the caller's size and hash checks reject it.
    """

    def __init__(self, handle, size):
        self.source = _Source(handle)
        self.size = size
        self.format = FORMAT_FIXED
        self.blocks = 0
        self.fallback = ""
        self._position = 0

    def __iter__(self):
        header = self.source.read(_V1_HEADER)
        self.format, header_size, layout, length_field = detect_format(header)
        if layout is None:
            self.fallback = "unrecognized or compressed file header"  # i18n: ingest diagnostic kept in the stored stats, never drawn
            self.source.unread(header)
            yield from self._fixed()
            return
        self.source.unread(header[header_size:])
        self._position = header_size
        yield header[:header_size]
        yield from self._blocks(layout, length_field)

    def _fixed(self):
        while True:
            piece = self.source.read(MAX_SEGMENT_BYTES)
            if not piece:
                return
            self._position += len(piece)
            yield piece

    def _blocks(self, layout, length_field):
        group, group_size = [], 0
        while True:
            head = self.source.read(layout.size)
            if not head:
                break
            if len(head) < layout.size:
                self.fallback = "truncated block header"  # i18n: ingest diagnostic kept in the stored stats, never drawn
                self.source.unread(head)
                break
            fields = layout.unpack(head)
            code, length = fields[0], fields[length_field]
            if length < 0 or length > self.size - self._position - layout.size:
                self.fallback = "block length outside the file"  # i18n: ingest diagnostic kept in the stored stats, never drawn
                self.source.unread(head)
                break
            self.blocks += 1
            total = layout.size + length
            if total >= STANDALONE_BLOCK_BYTES:
                if group:
                    yield b"".join(group)
                    group, group_size = [], 0
                yield from self._large_block(head, length)
            else:
                if group and (code != b"DATA" or group_size >= GROUP_TARGET_BYTES):
                    yield b"".join(group)
                    group, group_size = [], 0
                data = self.source.read(length)
                self._position += layout.size + len(data)
                group += (head, data)
                group_size += layout.size + len(data)
                if len(data) < length:
                    self.fallback = "truncated block data"  # i18n: ingest diagnostic kept in the stored stats, never drawn
                    break
            if code == b"ENDB":
                break
        if group:
            yield b"".join(group)
        # Bytes after ENDB, or after a block the parser could not trust, are
        # kept exactly; only their deduplication granularity changes.
        yield from self._fixed()

    def _large_block(self, head, length):
        first = self.source.read(min(length, MAX_SEGMENT_BYTES - len(head)))
        self._position += len(head) + len(first)
        yield head + first
        remaining = length - len(first)
        while remaining > 0:
            piece = self.source.read(min(MAX_SEGMENT_BYTES, remaining))
            if not piece:
                self.fallback = "truncated block data"  # i18n: ingest diagnostic kept in the stored stats, never drawn
                return
            self._position += len(piece)
            remaining -= len(piece)
            yield piece
