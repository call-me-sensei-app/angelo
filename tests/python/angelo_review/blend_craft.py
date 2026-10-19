# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Crafted .blend files for testing the metadata scanner. Pure Python, no bpy.

The DNA, GLOB and an Object block are taken from a real uncompressed Blender 5.2 file (`base`),
so crafted files pass the scanner's DNA preflight and differ only where an attack wants them to.
"""
import os
import struct

LARGE = struct.Struct('<4sIQqq')     # LargeBHead8: code, SDNAnr, old, len, nr (32 bytes)
SMALL = struct.Struct('<4siQii')     # SmallBHead8: code, len, old, SDNAnr, nr (24 bytes)
WINDOW = 64 * 1024


class Base:
    """Pieces of a real file: header, GLOB, DNA1, an OB block and its ID name offset."""

    def __init__(self, path):
        data = open(path, 'rb').read()
        assert data[:17] == b'BLENDER17-01v0502', data[:17]
        self.header = data[:17]
        self.blocks = {}
        off = 17
        while off < len(data):
            code, sdna, old, length, nr = LARGE.unpack_from(data, off)
            payload = data[off + LARGE.size:off + LARGE.size + length]
            self.blocks.setdefault(code, (sdna, old, length, nr, payload))
            if code == b'ENDB':
                break
            off += LARGE.size + length
        self.dna = self.blocks[b'DNA1'][4]
        self.glob = self.blocks[b'GLOB']
        self.ob = self.blocks[b'OB\x00\x00']
        self.name_offset = self.ob[4].find(b'OBCube')
        assert self.name_offset == 40
        # The scanner reads ID.name and ID.flag; 64 bytes past the name's 258 cover both.
        self.id_bytes = self.name_offset + 258 + 64


def large(code, payload=b'', sdna=0, nr=1, length=None, old=0):
    if isinstance(code, str):
        code = code.encode()
    code = code.ljust(4, b'\0')
    return LARGE.pack(code, sdna, old, len(payload) if length is None else length, nr) + payload


def small(code, payload=b'', sdna=0, nr=1, length=None, old=0):
    if isinstance(code, str):
        code = code.encode()
    code = code.ljust(4, b'\0')
    return SMALL.pack(code, len(payload) if length is None else length, old, sdna, nr) + payload


def object_payload(base, name):
    payload = bytearray(base.ob[4][:base.id_bytes])
    raw = ('OB' + name).encode()[:257]
    payload[base.name_offset:base.name_offset + 258] = raw + b'\0' * (258 - len(raw))
    return bytes(payload)


def object_block(base, name, block=large):
    return block('OB', object_payload(base, name), sdna=base.ob[0])


def glob_block(base, block=large):
    return block('GLOB', base.glob[4], sdna=base.glob[0])


def dna_block(base, block=large):
    return block('DNA1', base.dna)


def endb(block=large):
    return block('ENDB')


def file_start(base, block=large, dna_first=True):
    header = base.header if block is large else b'BLENDER-v405'
    out = bytearray(header)
    out += glob_block(base, block)
    if dna_first:
        out += dna_block(base, block)
    return out


def write(path, data):
    with open(path, 'wb') as f:
        f.write(data)
    return path


def sparse_zero_blocks(path, base, blocks, objects=()):
    """GLOB, DNA1, `blocks` all-zero LargeBHead8 headers (code 0, len 0) as a hole, then ENDB.

    A zero header is a valid empty block, so the scanner walks every one of them.
    """
    start = bytes(file_start(base))
    tail = b''.join(object_block(base, name) for name in objects) + endb()
    with open(path, 'wb') as f:
        f.write(start)
        f.seek(len(start) + blocks * LARGE.size)
        f.write(tail)
    return path


def zstd_compress(data, level=3):
    import zstandard
    return zstandard.ZstdCompressor(level=level).compress(data)


def gzip_compress(data):
    import gzip
    return gzip.compress(data, compresslevel=6)
