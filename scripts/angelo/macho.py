#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Just enough of the Mach-O format to inventory an app bundle before it is signed.

``read(path)`` says whether a file is Mach-O code and, for each architecture slice, its file type, the
libraries it loads and its ``LC_RPATH`` entries. The release signer
(``sign_notarize_macos.py``) uses it to find every file that needs a signature and to refuse code that
cannot be signed (object files) or that would load something from outside the bundle and the system.
Reading headers directly keeps the scan to a few bytes per file, where ``file``/``otool`` would start
two processes for each of the bundle's seven thousand files.

Standard library only; no bpy. Little-endian slices only (arm64, x86_64), which is all macOS runs.
"""

import struct
from dataclasses import dataclass

MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
FILE_TYPES = {1: "object", 2: "executable", 6: "dylib", 7: "dylinker", 8: "bundle", 9: "dylib_stub", 10: "dsym",
              11: "kext_bundle"}
CPU_TYPES = {0x0100000C: "arm64", 0x01000007: "x86_64", 0x0200000C: "arm64_32", 7: "i386", 12: "arm"}
# LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LAZY_LOAD_DYLIB, LC_LOAD_UPWARD_DYLIB.
DYLIB_COMMANDS = {0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023}
LC_RPATH = 0x8000001C
MAX_FAT_ARCHS = 8          # A Java class file also starts with 0xCAFEBABE; its "count" is a class version (45+).
MAX_LOAD_COMMANDS = 4 * 1024 * 1024


@dataclass(frozen=True)
class Slice:
    arch: str
    file_type: str
    dylibs: tuple
    rpaths: tuple


@dataclass(frozen=True)
class MachO:
    slices: tuple

    @property
    def file_type(self):
        """The slices' file type (they agree in any real binary)."""
        return self.slices[0].file_type

    @property
    def archs(self):
        return tuple(s.arch for s in self.slices)

    @property
    def dylibs(self):
        return tuple(sorted({d for s in self.slices for d in s.dylibs}))

    @property
    def rpaths(self):
        return tuple(sorted({r for s in self.slices for r in s.rpaths}))


def _c_string(data, start, end):
    raw = data[start:end]
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def _slice(handle, offset):
    handle.seek(offset)
    head = handle.read(32)
    if len(head) < 28:
        return None
    magic = struct.unpack_from("<I", head)[0]
    if magic not in (MH_MAGIC, MH_MAGIC_64):
        return None
    cpu, _subtype, file_type, ncmds, sizeofcmds, _flags = struct.unpack_from("<iiIIII", head, 4)
    if sizeofcmds > MAX_LOAD_COMMANDS:
        raise ValueError("load commands larger than %d bytes" % MAX_LOAD_COMMANDS)
    handle.seek(offset + (32 if magic == MH_MAGIC_64 else 28))
    commands = handle.read(sizeofcmds)
    dylibs, rpaths, position = [], [], 0
    for _ in range(ncmds):
        if position + 8 > len(commands):
            raise ValueError("truncated load commands")
        command, size = struct.unpack_from("<II", commands, position)
        if size < 8 or position + size > len(commands):
            raise ValueError("malformed load command")
        if command in DYLIB_COMMANDS or command == LC_RPATH:
            name_offset = struct.unpack_from("<I", commands, position + 8)[0]
            text = _c_string(commands, position + name_offset, position + size)
            (rpaths if command == LC_RPATH else dylibs).append(text)
        position += size
    return Slice(CPU_TYPES.get(cpu & 0xFFFFFFFF, hex(cpu & 0xFFFFFFFF)), FILE_TYPES.get(file_type, str(file_type)),
                 tuple(dylibs), tuple(rpaths))


def read(path):
    """A ``MachO`` for a Mach-O file (thin or universal), None for anything else."""
    with open(path, "rb") as handle:
        head = handle.read(8)
        if len(head) < 8:
            return None
        if struct.unpack_from("<I", head)[0] in (MH_MAGIC, MH_MAGIC_64):
            thin = _slice(handle, 0)
            return MachO((thin,)) if thin else None
        magic, count = struct.unpack_from(">II", head)
        if magic not in (FAT_MAGIC, FAT_MAGIC_64) or not 0 < count <= MAX_FAT_ARCHS:
            return None
        entry = 32 if magic == FAT_MAGIC_64 else 20
        table = handle.read(entry * count)
        slices = []
        for index in range(count):
            if magic == FAT_MAGIC_64:
                offset = struct.unpack_from(">Q", table, index * entry + 8)[0]
            else:
                offset = struct.unpack_from(">I", table, index * entry + 8)[0]
            part = _slice(handle, offset)
            if part is None:
                return None
            slices.append(part)
        return MachO(tuple(slices))


def is_system_path(name):
    """A dependency the system provides (or the dyld shared cache stands in for)."""
    return name.startswith(("/usr/lib/", "/System/Library/"))


def is_relative_path(name):
    return name.startswith(("@rpath/", "@loader_path/", "@executable_path/")) or name in (
        "@loader_path", "@executable_path")
