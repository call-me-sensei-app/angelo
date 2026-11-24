#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Just enough of the ELF format to check a Linux release binary before it ships.

``read(path)`` says whether a file is 64-bit little-endian ELF code and, if so, its machine, its type, its program
interpreter, its ``RPATH``/``RUNPATH`` and every symbol version it needs from each shared library
(``.gnu.version_r``). ``glibc_problems(info, ceiling)`` refuses a binary that needs a glibc symbol version newer
than ``ceiling`` (or glibc's private interface): such a binary does not start on an older distribution, with a
loader error the person cannot act on. ``objdump -T`` says the same, but needs binutils on the checking host; this
reads the headers directly, so a macOS or Windows host checks a Linux build too.

Standard library only; no bpy. x86-64 and AArch64 (little-endian, 64-bit), which is all Angelo builds for Linux.
"""

import re
import struct
from dataclasses import dataclass

MACHINES = {62: "x86_64", 183: "aarch64"}
TYPES = {1: "relocatable", 2: "executable", 3: "shared", 4: "core"}
PT_INTERP, PT_DYNAMIC = 3, 2
SHT_DYNAMIC, SHT_GNU_VERNEED = 6, 0x6FFFFFFE
DT_NEEDED, DT_RPATH, DT_RUNPATH, DT_STRTAB = 1, 15, 29, 5
MAX_SECTIONS = 1 << 16
MAX_VERSION_ENTRIES = 1 << 16
GLIBC_VERSION = re.compile(r"GLIBC_(\d+(?:\.\d+)*)\Z")
SYMBOL_VERSION = re.compile(r"([A-Z][A-Z_]*?)_(\d+(?:\.\d+)*)\Z")   # GLIBC_2.28, GCC_7.0.0, GLIBCXX_3.4.25
ORIGIN = ("$ORIGIN", "${ORIGIN}")


@dataclass(frozen=True)
class Elf:
    machine: str            # "x86_64", "aarch64", or "machine <n>"
    file_type: str          # "executable", "shared" (a PIE executable or a library), ...
    interpreter: str        # "" for a static executable or a library
    needed: tuple           # DT_NEEDED library names
    runpaths: tuple         # DT_RPATH and DT_RUNPATH entries
    versions: tuple         # (library, version name) pairs from .gnu.version_r
    has_sections: bool      # False when the section headers are gone (versions then cannot be read)


def _string(data, offset):
    end = data.find(b"\0", offset)
    if offset < 0 or offset >= len(data) or end < 0:
        raise ValueError("a string offset lies outside its table")
    return data[offset:end].decode("utf-8", "replace")


def read(path):
    """The ELF facts of ``path``, or None when it is not 64-bit little-endian ELF. Raises ValueError for a
    truncated or inconsistent file and OSError when it cannot be read."""
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 64 or data[:4] != b"\x7fELF":
        return None
    if data[4] != 2 or data[5] != 1:   # ELFCLASS64, ELFDATA2LSB
        return None
    file_type, machine = struct.unpack_from("<HH", data, 16)
    phoff, shoff = struct.unpack_from("<QQ", data, 32)
    phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHH", data, 54)

    interpreter, dynamic = "", None
    for index in range(phnum):
        start = phoff + index * phentsize
        if phentsize < 56 or start + 56 > len(data):
            raise ValueError("the program headers run past the end of the file")
        kind, _flags, offset, _vaddr, _paddr, size = struct.unpack_from("<IIQQQQ", data, start)
        if offset + size > len(data):
            raise ValueError("a segment runs past the end of the file")
        if kind == PT_INTERP:
            interpreter = data[offset:offset + size].split(b"\0", 1)[0].decode("utf-8", "replace")

    sections = []
    if shoff and shnum:
        if shnum > MAX_SECTIONS or shentsize < 64 or shoff + shnum * shentsize > len(data):
            raise ValueError("the section headers run past the end of the file")
        for index in range(shnum):
            sections.append(struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shentsize))

    def body(section):
        offset, size = section[4], section[5]
        if offset + size > len(data):
            raise ValueError("a section runs past the end of the file")
        return data[offset:offset + size]

    def linked_strings(section):
        link = section[6]
        if link >= len(sections):
            raise ValueError("a section names a string table that does not exist")
        return body(sections[link])

    needed, runpaths, versions = [], [], []
    for section in sections:
        if section[1] == SHT_DYNAMIC:
            dynamic = section
        elif section[1] == SHT_GNU_VERNEED:
            table, strings, count = body(section), linked_strings(section), section[7]
            offset = 0
            for _entry in range(min(count, MAX_VERSION_ENTRIES)):
                if offset + 16 > len(table):
                    raise ValueError(".gnu.version_r runs past its section")
                _version, aux_count, file_name, aux, following = struct.unpack_from("<HHIII", table, offset)
                library = _string(strings, file_name)
                at = offset + aux
                for _aux in range(min(aux_count, MAX_VERSION_ENTRIES)):
                    if at + 16 > len(table):
                        raise ValueError(".gnu.version_r runs past its section")
                    _hash, _flags, _other, name, next_aux = struct.unpack_from("<IHHII", table, at)
                    versions.append((library, _string(strings, name)))
                    if not next_aux:
                        break
                    at += next_aux
                if not following:
                    break
                offset += following
    if dynamic is not None:
        table, strings = body(dynamic), linked_strings(dynamic)
        for at in range(0, len(table) - 15, 16):
            tag, value = struct.unpack_from("<qQ", table, at)
            if tag == 0:
                break
            if tag == DT_NEEDED:
                needed.append(_string(strings, value))
            elif tag in (DT_RPATH, DT_RUNPATH):
                runpaths += [part for part in _string(strings, value).split(":") if part]
    return Elf(MACHINES.get(machine, "machine %d" % machine), TYPES.get(file_type, "type %d" % file_type),
               interpreter, tuple(needed), tuple(runpaths), tuple(versions), bool(sections))


def _version_key(text):
    return tuple(int(part) for part in text.split("."))


def glibc_versions(info):
    """The glibc symbol versions ``info`` needs (``("2.2.5", "2.17", ...)``), oldest first; ``GLIBC_PRIVATE`` is
    left out (``glibc_problems`` refuses it)."""
    found = {match.group(1) for _library, name in info.versions for match in [GLIBC_VERSION.match(name)] if match}
    return tuple(sorted(found, key=_version_key))


def glibc_problems(info, ceiling, name="the binary"):
    """Why ``info`` would not start on glibc ``ceiling`` (empty: it would). A dynamically linked file whose section
    headers are gone is refused too: its version needs cannot be read, so the floor is unproven."""
    if info.interpreter and not info.has_sections:
        return ["%s has no section headers, so its glibc needs cannot be read (strip it with --strip-unneeded, not "
                "sstrip)" % name]
    problems = []
    newer = [version for version in glibc_versions(info) if _version_key(version) > _version_key(ceiling)]
    if newer:
        problems.append("%s needs glibc %s, newer than the %s every supported distribution has: build it on a "
                        "glibc %s system (Rocky Linux 8, as upstream Blender does)" % (
                            name, newer[-1], ceiling, ceiling))
    if any(version == "GLIBC_PRIVATE" for _library, version in info.versions):
        problems.append("%s uses glibc's private interface (GLIBC_PRIVATE), which changes between releases" % name)
    return problems


def floor_problems(info, interpreter, ceilings, name="the binary"):
    """Why ``info`` would not start, or would load something else, on the oldest supported system (empty: it would).

    * Its program interpreter must be ``interpreter`` (a Nix-store or builder loader starts nowhere else).
    * Every ``RPATH``/``RUNPATH`` entry must start with ``$ORIGIN``: an absolute folder is the builder's (or absent),
      a relative one resolves against the current folder, and ``/tmp`` is anyone's.
    * Every versioned symbol it needs must be no newer than ``ceilings`` (``{"GLIBC": "2.28", "GCC": "7.0.0"}``), by
      the version's prefix; a prefix the ceilings don't name is unproven, so it is refused too.
    """
    if info.interpreter and not info.has_sections:
        return ["%s has no section headers, so its version needs cannot be read (strip it with --strip-unneeded, not "
                "sstrip)" % name]
    problems = []
    if info.interpreter and info.interpreter != interpreter:
        problems.append("%s's program interpreter is %s, not the system's %s" % (name, info.interpreter, interpreter))
    for entry in info.runpaths:
        if not entry.startswith(ORIGIN):
            problems.append("%s searches %s for libraries: only $ORIGIN-relative folders may be searched" % (
                name, entry))
    newest = {}
    for library, version in info.versions:
        match = SYMBOL_VERSION.match(version)
        if version == "GLIBC_PRIVATE":
            problems.append("%s uses glibc's private interface (GLIBC_PRIVATE), which changes between releases" % name)
        elif not match or match.group(1) not in ceilings:
            problems.append("%s needs %s from %s, which the supported systems' ceilings don't cover" % (
                name, version, library))
        elif _version_key(match.group(2)) > _version_key(ceilings[match.group(1)]):
            key = (match.group(1), library)
            if key not in newest or _version_key(match.group(2)) > _version_key(newest[key]):
                newest[key] = match.group(2)
    for (prefix, library), version in sorted(newest.items()):
        problems.append("%s needs %s %s from %s, newer than the %s every supported system has: build it on the "
                        "oldest supported system (Rocky Linux 8, as upstream Blender does)" % (
                            name, prefix, version, library, ceilings[prefix]))
    return list(dict.fromkeys(problems))
