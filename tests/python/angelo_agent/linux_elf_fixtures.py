# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, valid 64-bit ELF files for the Linux release checks (``scripts/angelo/elf_info.py``).

``elf_file`` writes the parts those checks read: the header, a ``PT_INTERP`` segment, a dynamic section
(``DT_NEEDED``, ``DT_RUNPATH``) and ``.gnu.version_r`` with the symbol versions each library is needed at. No code;
nothing here runs. Standard library only.
"""

import struct

EM_X86_64, EM_AARCH64 = 62, 183
ET_EXEC, ET_DYN = 2, 3


def elf_file(machine=EM_X86_64, file_type=ET_DYN, interpreter="/lib64/ld-linux-x86-64.so.2",
             needs=(("libc.so.6", ("GLIBC_2.2.5", "GLIBC_2.17")),), runpath="", sections=True, elf_class=2):
    """The bytes of an ELF file. ``needs``: ``(library, (version, ...))`` pairs for .gnu.version_r."""
    strings = bytearray(b"\0")

    def string(text):
        offset = len(strings)
        strings.extend(text.encode() + b"\0")
        return offset

    verneed = bytearray()
    for index, (library, versions) in enumerate(needs):
        aux = bytearray()
        for number, version in enumerate(versions):
            following = 16 if number + 1 < len(versions) else 0
            aux += struct.pack("<IHHII", 0, 0, 2 + number, string(version), following)
        following = 16 + len(aux) if index + 1 < len(needs) else 0
        verneed += struct.pack("<HHIII", 1, len(versions), string(library), 16, following) + aux
    dynamic = bytearray()
    for library, _versions in needs:
        dynamic += struct.pack("<qQ", 1, string(library))
    if runpath:
        dynamic += struct.pack("<qQ", 29, string(runpath))
    dynamic += struct.pack("<qQ", 0, 0)
    names = bytearray(b"\0.dynstr\0.gnu.version_r\0.dynamic\0.shstrtab\0")
    interp = interpreter.encode() + b"\0" if interpreter else b""

    header_size, segment_size = 64, 56
    segments = 1 if interp else 0
    data_start = header_size + segment_size * segments
    blobs = [interp, bytes(strings), bytes(verneed), bytes(dynamic), bytes(names)]
    offsets, at = [], data_start
    for blob in blobs:
        offsets.append(at)
        at += len(blob)
    section_offset = at
    section_count = 5 if sections else 0
    header = bytearray(b"\x7fELF" + bytes([elf_class, 1, 1, 0]) + bytes(8))
    header += struct.pack("<HHIQQQIHHHHHH", file_type, machine, 1, 0, header_size if segments else 0,
                          section_offset if sections else 0, 0, header_size, segment_size, segments, 64,
                          section_count, 4 if sections else 0)
    out = bytearray(header)
    if interp:
        out += struct.pack("<IIQQQQQQ", 3, 4, offsets[0], 0, 0, len(interp), len(interp), 1)
    for blob in blobs:
        out += blob
    if sections:
        def section(name, kind, index, link=0, info=0):
            return struct.pack("<IIQQQQIIQQ", name, kind, 0, 0, offsets[index], len(blobs[index]), link, info, 1, 0)
        out += bytes(64)                                          # SHN_UNDEF
        out += section(1, 3, 1)                                   # .dynstr (SHT_STRTAB)
        out += section(9, 0x6FFFFFFE, 2, link=1, info=len(needs))  # .gnu.version_r -> .dynstr
        out += section(24, 6, 3, link=1)                          # .dynamic -> .dynstr
        out += section(33, 3, 4)                                  # .shstrtab
    return bytes(out)
