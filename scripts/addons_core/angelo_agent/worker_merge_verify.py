# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a merge keeps, read as a save writes it: no private folder may be in its bytes, and no packed bytes but those
the merge wrote itself (``worker_merge_privacy``, break-it E2E-20 F-L-2; BREAKIT-C7A-2: the cleaning's own recognizer
cannot be its own check).

The kept datablocks (and what a save writes for them besides: a kept linked datablock's placeholder, a Library the
merge added) are written as Save writes them, to a file in a private folder of Angelo's (``saved_copy``: C's
``_bpy._angelo_saved.write_only``, the save's own writer over the same datablocks, never copies of them, and only
those: never ``bpy.data.libraries.write``, which writes copies, and a copy rebuilds what it copies: a modifier's or a
shader effect's name without the bytes after its terminator, a collection exporter without its file path, BREAKIT-C7A-9
H1). So a merge costs its part, never the person's whole file (port review SHOULD 2: the whole file, uncompressed, on
the main thread, for every merge). Nothing else of a save happens: no save handler runs, nothing is packed, no
thumbnail is made, no modified image is saved, no path is remapped; a kept datablock no one uses, which a save skips
but Undo and autosave write, is written too. Only the kept datablocks'
own bytes (each one's ID block and the data blocks after it, until the next datablock's: every one, whatever its size,
its packed files included; BREAKIT-C7A-7 H1) are read, streamed, by ``worker_merge_scan``: for each base private
folder's spellings and this merge's own folders' names, in every view a text transport gives them, each path found
decided by ``PrivateFolders.holds``, as the cleaning decides. So what the cleaning missed is found: a field Blender
saves that no RNA reads, a spelling it cannot cut in place. A place that cannot be read with its context, a kept
datablock the file does not hold, a copy that cannot be written (too little disk: ``Unchecked``) or a file whose DNA
cannot be read all refuse the merge (fail closed).

Packed bytes are found by the file's own DNA: every packed file Blender writes is a ``PackedFile`` struct, wherever its
owner keeps it (an image's files and autosave files, a sound's, a font's, a volume's, a library's, a geometry-nodes
bake's; BREAKIT-C7A-9 H2), so a kept datablock holding one the merge did not write again itself is reported
(``packed``) and refuses the merge: a worker's own bytes are never kept, however a field holds them. Bytes no view reads
as text (a compressed stream, base64) are not readable here; that is why they are never kept.
"""

import os
import shutil
import struct

# Blender's ID codes (``DNA_ID_enums.h``), by the RNA ``id_type`` of each kind a part can hold.
CODES = {"ACTION": b"AC", "ARMATURE": b"AR", "BRUSH": b"BR", "CACHEFILE": b"CF", "CAMERA": b"CA", "COLLECTION": b"GR",
         "CURVE": b"CU", "CURVES": b"CV", "FONT": b"VF", "GREASEPENCIL": b"GD", "GREASEPENCIL_V3": b"GP",
         "IMAGE": b"IM", "KEY": b"KE", "LATTICE": b"LT", "LIBRARY": b"LI", "LIGHT": b"LA", "LIGHT_PROBE": b"LP",
         "LINESTYLE": b"LS", "MASK": b"MS", "MATERIAL": b"MA", "MESH": b"ME", "META": b"MB", "MOVIECLIP": b"MC",
         "NODETREE": b"NT", "OBJECT": b"OB", "PAINTCURVE": b"PC", "PALETTE": b"PL", "PARTICLE": b"PA",
         "POINTCLOUD": b"PT", "SCENE": b"SC", "SCREEN": b"SR", "SOUND": b"SO", "SPEAKER": b"SK", "TEXT": b"TX",
         "TEXTURE": b"TE", "VOLUME": b"VO", "WINDOWMANAGER": b"WM", "WORKSPACE": b"WS", "WORLD": b"WO"}
NAME_AT, NAME_SIZE = 40, 258   # ``ID.name``: after five pointers, the type's code then the name.
LIBRARY_AT = 24   # ``ID.lib``: the fourth pointer; set, the datablock is linked (packed in the file or not).
FILE_BLOCKS = {b"GLOB", b"DNA1", b"TEST", b"REND", b"USER", b"ENDB"}   # Blocks a save writes for the file itself.
HEADERS = 1 << 20   # Bytes read at a time for block headers.
SPARE = 1 << 28   # Disk left free for the copy of the kept datablocks (a full disk fails closed).


class Unchecked(Exception):
    """The check could not write its copy of the file: the merge refuses, saying why."""


def check(bpy, kept, private, written=(), also=()):
    """``(leaks, packed)``: the kept datablocks whose bytes, as a save writes them, hold a private folder; and those
    holding packed bytes the merge did not write itself (``written``: datablocks); each sorted. ``also``: what a save
    writes for the merge besides its local datablocks, read the same way but never required in the file: a kept
    linked datablock's placeholder (its name, which no merge can change: BREAKIT-C7A-14 H1) and a Library the merge
    added. Raises ``Unchecked``."""
    from . import private_roots
    from .worker_merge_scan import FileReader, Scanner
    kept = [item for item in kept if item.library is None]
    extra = {_key(item): item.name for item in also}
    if not kept and not extra:
        return [], []
    keys, mine = {_key(item): item.name for item in kept}, {_key(item) for item in written}
    scanner, found, packs = Scanner(private), set(), set()
    with private_roots.TemporaryDirectory(prefix="angelo-merge-check-") as folder:
        path = os.path.join(folder, "check.blend")
        # A save skips a datablock no one uses; Undo and autosave write it: written here too, by a fake user for the
        # copy only.
        unused = [item for item in kept if item.users == 0 and not item.use_fake_user]
        try:
            for item in unused:
                item.use_fake_user = True
            saved_copy(path, kept + list(also))
        finally:
            for item in unused:
                item.use_fake_user = False
        with open(path, "rb") as file:
            reader = FileReader(file)
            linked = []
            blocks, kinds = _blocks(reader, linked)
            if blocks is None:   # Not a file this reader knows: none of it can be read.
                return sorted(list(keys.values()) + list(extra.values())), []
            if b"PackedFile" not in kinds.values():   # Its DNA unread: no packed file could be told (fail closed).
                raise Unchecked("its check copy's DNA could not be read")
            read = set()   # Every set is by datablock (ID code and name): a name is unique only within one kind.
            for key, start, end, structs in blocks:
                own = key if key in keys else (None, key[1]) if (None, key[1]) in keys else None
                if own is None:
                    continue
                read.add(own)
                if any(kinds.get(index) == b"PackedFile" for index in structs) and own not in mine:
                    packs.add(own)
                if own not in found and _holds(scanner, reader, start, end):
                    found.add(own)
            # What a save writes besides (a Library is written as a local block, a linked datablock as its own).
            for key, start, end, structs in [block for block in blocks if block[0] not in keys] + linked:
                if key in extra:
                    if any(kinds.get(index) == b"PackedFile" for index in structs):
                        packs.add(key)
                    if key not in found and _holds(scanner, reader, start, end):
                        found.add(key)
    # A kept datablock whose bytes were not found in the file was not read: fail closed (BREAKIT-C7A-7 review L-3).
    names = {**extra, **keys}
    return sorted(names[own] for own in found | (set(keys) - read)), sorted(names[own] for own in packs)


def saved_copy(path, ids):
    """Only ``ids`` (datablocks of the open file) written to ``path`` exactly as Save writes them, uncompressed, and
    nothing else of a save done (C's ``write_only``). Raises ``Unchecked`` when the disk is short or the write fails."""
    from _bpy import _angelo_saved
    free = shutil.disk_usage(os.path.dirname(path)).free
    if free < SPARE:
        raise Unchecked("its check copy needs about %d MB free and the disk has %d MB" % (SPARE >> 20, free >> 20))
    try:
        _angelo_saved.write_only(path, list(ids))
    except (OSError, TypeError, ValueError, RuntimeError) as error:   # A full disk, a refusal: the write's own words.
        raise Unchecked("its check copy could not be written (%s)" % str(error).strip()[:200]) from None
    if not os.path.isfile(path):
        raise Unchecked("its check copy could not be written")


def holds_private(private, data):
    """Whether ``data`` (bytes) holds a private path in any view, as the check reads written bytes."""
    from .worker_merge_scan import BytesReader, Scanner
    return _holds(Scanner(private), BytesReader(data), 0, len(data))


def _holds(scanner, reader, start, end):
    from .worker_merge_scan import Unreadable
    try:
        return scanner.hit(reader, start, end) is not None
    except Unreadable:   # A place that cannot be read with its context: fail closed.
        return True


def _key(item):
    code = CODES.get(getattr(item, "id_type", ""))
    return (code, item.name.encode("utf-8"))


def _blocks(reader, linked=None):
    """``([((code, name), start, end, struct indices)], {struct index: type name})``: each local datablock's bytes in
    a written file (its ID block and its data blocks, every one), its block headers read from a buffer of the file in
    order, and the file's own DNA; ``(None, {})`` when it is not one. A linked datablock (a placeholder, or one packed
    in the file) is no one's: a kept datablock is local (BREAKIT-C7A-10 M2); each linked one's bytes, keyed by the
    code and name it is written with, go to ``linked`` (a list) when one is given."""
    head = reader.read(0, 12)
    if not head.startswith(b"BLENDER"):
        return None, {}
    if head[7:9].isdigit():   # Blender 5.0 and later: "BLENDER17-01v0502", large block headers.
        offset, size, layout = int(head[7:9]), 32, "<4siQqq"
    elif head[7:8] == b"-" and head[8:9] == b"v":   # Earlier: "BLENDER-v502", 8-byte pointers.
        offset, size, layout = 12, 24, "<4siQii"
    else:
        return None, {}
    found, current, buffer, at, dna = [], None, b"", 0, None   # ``buffer``: the file's bytes from ``at``.
    while offset + size <= reader.size:
        if offset + size + NAME_AT + NAME_SIZE > at + len(buffer):   # A header and an ID's name, whole.
            at, buffer = offset, reader.read(offset, offset + HEADERS)
        fields = struct.unpack_from(layout, buffer, offset - at)
        code = fields[0]
        length, index = (fields[3], fields[1]) if size == 32 else (fields[1], fields[3])
        start, offset = offset, offset + size + max(0, length)
        if code == b"ENDB":
            break
        if code == b"DNA1":
            dna = (start + size, start + size + max(0, length))
        is_id = code[2:] == b"\0\0" and code[:2] != b"\0\0"
        if current is not None and not is_id and code != b"DATA":
            if code not in FILE_BLOCKS:   # Not its data and not the file's own: what it holds is no one's to say.
                raise Unchecked("the copy holds a block Angelo doesn't know (%s)" % code.decode("ascii", "replace"))
            current = None   # The file's own blocks (GLOB names the file).
        if is_id:
            name = buffer[start - at + size + NAME_AT:start - at + size + NAME_AT + NAME_SIZE].split(b"\0", 1)[0]
            library = struct.unpack_from("<Q", buffer, start - at + size + LIBRARY_AT)[0]
            local = name[:2] == code[:2] and not library   # Never a linked one, though named as a kept one.
            current = [(name[:2], name[2:]) if local else (None, name), start, offset, {index}]
            found.append(current)
        elif current is not None:
            current[2] = offset
            current[3].add(index)
    kinds = _struct_names(reader.read(*dna)) if dna else {}
    if linked is not None:
        linked.extend(((key[1][:2], key[1][2:]), start, min(end, reader.size), structs)
                      for key, start, end, structs in found if key[0] is None)
    return [(tuple(key), start, min(end, reader.size), structs) for key, start, end, structs in found
            if key[0] is not None], kinds


def _struct_names(body):
    """``{struct index: type name}`` from a file's DNA (``dna_genfile.cc``: names, types, their lengths, structs)."""
    try:
        if body[:8] != b"SDNANAME":
            return {}
        at = 12
        for _ in range(struct.unpack_from("<i", body, 8)[0]):
            at = body.index(b"\0", at) + 1
        at = (at + 3) & ~3
        if body[at:at + 4] != b"TYPE":
            return {}
        count, at, types = struct.unpack_from("<i", body, at + 4)[0], at + 8, []
        for _ in range(count):
            end = body.index(b"\0", at)
            types.append(body[at:end])
            at = end + 1
        at = (at + 3) & ~3
        if body[at:at + 4] != b"TLEN":
            return {}
        at = (at + 4 + 2 * count + 3) & ~3
        if body[at:at + 4] != b"STRC":
            return {}
        count, at, names = struct.unpack_from("<i", body, at + 4)[0], at + 8, {}
        for index in range(count):
            kind, members = struct.unpack_from("<hh", body, at)
            names[index] = types[kind]
            at += 4 + 4 * members
        return names
    except (ValueError, struct.error, IndexError):
        return {}
