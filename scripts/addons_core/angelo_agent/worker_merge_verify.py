# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a merge keeps, read as Blender writes it: no private folder may be in its bytes (``worker_merge_privacy``,
break-it E2E-20 F-L-2; BREAKIT-C7A-2: the cleaning's own recognizer cannot be its own check).

The kept datablocks are written to a file in a private folder of Angelo's (``bpy.data.libraries.write``: the same
writer as every save, Undo step and autosave), and each datablock's own blocks (its ID block and the data blocks after
it, until the next datablock's) are searched, read as text and folded as the spellings are, for each base private
folder's spellings (``PrivateFolders.needles``: as written, JSON-escaped, URL-encoded) and this merge's own folders'
names; each path found there is decoded and decided by ``PrivateFolders.holds``, as the cleaning decides (so the
default workspace's files, which stay where they are, never count, and a ``workspace/..`` path into the state folder
does). The bytes are read in every view a text transport gives them besides (``views``): percent- and form-encoded
(a "+" a space), HTML entities and backslash escapes undone, again until nothing changes, and as UTF-16 either way
round (BREAKIT-C7A-5 M2), so a spelling the cleaning cannot cut refuses the merge instead of passing. So what the
cleaning missed is found: a field Blender saves that no RNA reads (a multi-view image's views), a spelling no rule
foresaw. Raw arrays over ``RAW_LIMIT`` (geometry) are not searched: no path is written as one; each packed file's
bytes are, whole (BREAKIT-C7A-5 H1: the limit exempted exactly those). Bytes no view reads as text (a compressed
stream) are not readable here: the scrub never packs a worker's own bytes for that (``worker_merge_media``).
Datablocks the write adds to make the file whole (the person's scene a driver now reads) are not the part's, and are
not searched. ``libraries.write`` writes copies whose name buffers are fresh, so the bytes after a terminator are not
seen here: the scrub writes each kept datablock's name whole for that.
"""

import html
import os
import re
import struct
from pathlib import Path
from urllib.parse import unquote_plus

# Blender's ID codes (``DNA_ID_enums.h``), by the RNA ``id_type`` of each kind a part can hold.
CODES = {"ACTION": b"AC", "ARMATURE": b"AR", "BRUSH": b"BR", "CACHEFILE": b"CF", "CAMERA": b"CA", "COLLECTION": b"GR",
         "CURVE": b"CU", "CURVES": b"CV", "FONT": b"VF", "GREASEPENCIL": b"GD", "GREASEPENCIL_V3": b"GP",
         "IMAGE": b"IM", "KEY": b"KE", "LATTICE": b"LT", "LIBRARY": b"LI", "LIGHT": b"LA", "LIGHT_PROBE": b"LP",
         "LINESTYLE": b"LS", "MASK": b"MS", "MATERIAL": b"MA", "MESH": b"ME", "META": b"MB", "MOVIECLIP": b"MC",
         "NODETREE": b"NT", "OBJECT": b"OB", "PAINTCURVE": b"PC", "PALETTE": b"PL", "PARTICLE": b"PA",
         "POINTCLOUD": b"PT", "SCENE": b"SC", "SCREEN": b"SR", "SOUND": b"SO", "SPEAKER": b"SK", "TEXT": b"TX",
         "TEXTURE": b"TE", "VOLUME": b"VO", "WINDOWMANAGER": b"WM", "WORKSPACE": b"WS", "WORLD": b"WO"}
NAME_AT, NAME_SIZE = 40, 258   # ``ID.name``: after five pointers, the type's code then the name.
RAW_LIMIT = 1 << 20   # Raw blocks (SDNA index 0) larger than this are arrays, never searched (BREAKIT-C7A-3 H3).
ENDS = re.compile(r"[\0\s\"'`,;()<>|]")   # Where a path in written bytes ends.
ESCAPES = re.compile(r"\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|([\\/\"']))")
DECODINGS = 4   # Rounds of decoding (an encoded path inside an encoded URL inside JSON is three).


def leaks(bpy, kept, private):
    """The names of the kept datablocks whose written bytes still hold a private folder, sorted."""
    from . import private_roots
    kept = [item for item in kept if item.library is None]
    if not kept:
        return []
    for scene in bpy.data.scenes:   # libraries.write copies a scene it pulls in (a driver's) with its view layers'
        for view_layer in scene.view_layers:   # bases, which the scrub's removals left out of sync: synced first.
            view_layer.active_layer_collection
    with private_roots.TemporaryDirectory(prefix="angelo-merge-check-") as folder:
        path = os.path.join(folder, "part.blend")
        bpy.data.libraries.write(path, set(kept), fake_user=False, compress=False)
        data = Path(path).read_bytes()
    keys = {_key(item): item.name for item in kept}
    blocks = _blocks(data)
    if blocks is None:   # Not a file this reader knows: every byte of it.
        return sorted(keys.values()) if _hit(private, data) else []
    found = set()
    for key, ranges in blocks:
        name = keys.get(key) or keys.get((None, key[1]))
        if name is not None and name not in found:
            if _hit(private, b"".join(data[start:end] for start, end in ranges)):
                found.add(name)
    from .worker_merge_media import packed
    for item in kept:   # Each packed file's bytes, whole.
        if item.name not in found and any(_hit(private, bytes(file.data)) is not None for file in packed(item)):
            found.add(item.name)
    return sorted(found)


def holds_private(private, data):
    """Whether ``data`` (bytes) holds a private path in any view, as the check reads written bytes."""
    return _hit(private, data) is not None


def _key(item):
    code = CODES.get(getattr(item, "id_type", ""))
    return (code, item.name.encode("utf-8"))


def _hit(private, chunk):
    """What in ``chunk`` (written bytes, in each of its ``views``) is a private path (a base folder's spelling and what
    follows, decoded, that ``holds`` calls private) or this merge's own folder's name, or None. Bytes and needles are
    folded the same way, as text, so a folder named with any letter is found (BREAKIT-C7A-4 H1)."""
    from .worker_merge_paths import _unquoted
    for text in views(chunk):
        for start, found in private.finds(text):
            end = ENDS.search(text, found)
            path = text[start:end.start() if end else len(text)]
            if private.holds(_unquoted(path)):
                return path
        folded = text.casefold()
        for name in private.names:
            if name in folded:
                return name
    return None


def views(chunk):
    """``chunk`` (bytes) as each text a text transport could make of it: as UTF-8; decoded until nothing changes; and
    as UTF-16, either byte order and either alignment."""
    text = chunk.decode("utf-8", "surrogateescape")
    yield text
    for _ in range(DECODINGS):
        decoded = _decoded(text)
        if decoded == text:
            break
        text = decoded
        yield text
    for codec in ("utf-16-le", "utf-16-be"):
        for skip in (0, 1):
            yield chunk[skip:].decode(codec, "replace")


def _decoded(text):
    """One round of a text transport undone: percent- and form-encoding, HTML entities, backslash escapes."""
    if "%" in text or "+" in text:
        text = unquote_plus(text, errors="surrogateescape")
    if "&" in text:
        text = html.unescape(text)
    if "\\" in text:
        text = ESCAPES.sub(lambda match: chr(int(match.group(1) or match.group(2), 16)) if match.group(1)
                           or match.group(2) else match.group(3), text)
    return text


def _blocks(data):
    """``[((code, name), [(start, end)])]``: each datablock's searched bytes in a written file (its ID block and its
    data blocks, raw arrays over ``RAW_LIMIT`` left out), or None when it is not one."""
    if not data.startswith(b"BLENDER"):
        return None
    if data[7:9].isdigit():   # Blender 5.0 and later: "BLENDER17-01v0502", large block headers.
        offset, head, layout = int(data[7:9]), 32, "<4siQqq"
    elif data[7:8] == b"-" and data[8:9] == b"v":   # Earlier: "BLENDER-v502", 8-byte pointers.
        offset, head, layout = 12, 24, "<4siQii"
    else:
        return None
    found, current = [], None
    while offset + head <= len(data):
        fields = struct.unpack_from(layout, data, offset)
        code = fields[0]
        length, sdna = (fields[3], fields[1]) if head == 32 else (fields[1], fields[3])
        start, offset = offset, offset + head + max(0, length)
        if code == b"ENDB":
            break
        is_id = code[2:] == b"\0\0" and code[:2] != b"\0\0"
        if current is not None and not is_id and code != b"DATA":   # The file's own blocks (GLOB names the file).
            current = None
        if is_id:
            body = data[start + head + NAME_AT:start + head + NAME_AT + NAME_SIZE]
            name = body.split(b"\0", 1)[0]
            current = ((name[:2], name[2:]), [])
            found.append(current)
        if current is not None and not (sdna == 0 and length > RAW_LIMIT):
            current[1].append((start, offset))
    return found
