# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every string a merged part's datablocks hold, and the private paths in them cut (``worker_merge_privacy``,
break-it E2E-20 F-L-2 and its reviews, BREAKIT-C7A and BREAKIT-C7A-2).

Blender's own path walk skips strings that are saved (a generated image's file path, a point cache's folder while it
caches in memory, Dynamic Paint's output folder, a node's string, a node modifier's input, an ID property, a Text's
lines), so ``strings`` walks them all: RNA strings of each datablock and its own structs and embedded datablocks
(never another datablock), and ID properties, Blender's system ones included, with their bytes and lists. A collection
whose items can hold no string (a mesh's vertices, a curve's points) is never walked; every single struct is read,
since one may hold only ID properties. ``clean_strings`` cuts each private path found (``PrivateFolders.clean``),
each fixed-size buffer written whole first. What a string cannot show (a field Blender saves but no RNA reads, the
bytes after a terminator) is ``worker_merge_verify``'s.
"""

from .worker_merge_paths import DETOUR, _basename, overwrite

PATH_SUBTYPES = ("FILE_PATH", "DIR_PATH", "FILE_NAME", "BYTE_STRING")
LARGE = 1 << 22   # Text or bytes longer than this are cleaned a window at a time (BREAKIT-C7A-9 L2).
_STRINGY = {}


def _stringy(rna):
    """Whether a struct of this RNA type, or of any type derived from it (a collection of interface items holds
    string sockets), can hold a string, itself or below (never through another datablock): a collection of a mesh's
    vertices, a curve's points and the like is never walked."""
    key = rna.identifier
    if key not in _STRINGY:
        _STRINGY[key] = True   # While it is decided: a cycle is walked, never skipped.
        found = any(_stringy(derived.bl_rna) for derived in _derived(key))
        for prop in rna.properties if not found else ():
            if prop.identifier == "rna_type":
                continue
            if prop.type == "STRING":
                found = True
                break
            target = getattr(prop, "fixed_type", None) if prop.type in ("POINTER", "COLLECTION") else None
            if target is None:
                continue
            if _is_id(target):
                found = found or prop.type == "POINTER"   # An embedded datablock (a material's node tree) is walked.
            elif _stringy(target):
                found = True
            if found:
                break
        _STRINGY[key] = found
    return _STRINGY[key]


def _derived(identifier):
    """The RNA types derived from ``identifier``'s, as Python knows them (``bpy.types``)."""
    import bpy
    found, stack = [], list(getattr(getattr(bpy.types, identifier, None), "__subclasses__", list)())
    while stack:
        cls = stack.pop()
        if getattr(cls, "bl_rna", None) is not None and cls not in found:
            found.append(cls)
            stack.extend(cls.__subclasses__())
    return found


def _is_id(rna):
    while rna is not None:
        if rna.identifier == "ID":
            return True
        rna = rna.base
    return False


def strings(bpy, item):
    """Every string ``item`` holds: ``("rna", struct, identifier, subtype, writable)`` and ``("property", group,
    key)`` for its ID properties holding text (Blender's system ones too), its own structs and embedded datablocks,
    never another datablock. A ``name_raw`` sibling is taken for ``name`` (a raw path sets no reload)."""
    stack, seen = [item], set()
    while stack:
        struct = stack.pop()
        pointer = (struct.as_pointer(), struct.bl_rna.identifier)   # A view of the same data is another struct:
        if pointer in seen:                                          # a node modifier's ``properties`` (its inputs).
            continue
        seen.add(pointer)
        yield from _properties(struct)
        rna = struct.bl_rna
        for prop in rna.properties:
            name = prop.identifier
            if name == "rna_type":
                continue
            if prop.type == "STRING":
                if name + "_raw" not in rna.properties:
                    yield ("rna", struct, name, prop.subtype, not prop.is_readonly)
                continue
            target = getattr(prop, "fixed_type", None) if prop.type in ("POINTER", "COLLECTION") else None
            if target is None or (prop.type == "COLLECTION" and (_is_id(target) or not _stringy(target))):
                continue   # One struct is always read (it may hold only ID properties: a node modifier's inputs).
            try:
                value = getattr(struct, name)
            except Exception:   # A getter that refuses here (no context, an unsupported state): nothing to read.
                continue
            if prop.type == "POINTER":
                if value is not None and not (isinstance(value, bpy.types.ID) and not value.is_embedded_data):
                    stack.append(value)
            else:
                stack.extend(element for element in value if element is not None)


def _properties(struct):
    """``("property", group, key)`` for each ID property of ``struct`` that holds text, bytes or a list (nested
    groups walked)."""
    groups = []
    try:
        groups.append(struct if len(struct.keys()) else None)
    except (TypeError, AttributeError):   # A type with no ID properties.
        pass
    system = getattr(struct, "bl_system_properties_get", None)
    if callable(system):
        try:
            groups.append(system())
        except (TypeError, RuntimeError):
            pass
    stack = [group for group in groups if group is not None]
    while stack:
        group = stack.pop()
        for key in list(group.keys()):
            value = group[key]
            if isinstance(value, (str, bytes, list, tuple)) or type(value).__name__ == "IDPropertyArray":
                yield ("property", group, key)
            elif hasattr(value, "keys"):
                stack.append(value)


def clean_strings(bpy, kept, private):
    """Every private path in a kept datablock's strings cut (``PrivateFolders.clean``); the names of the datablocks
    changed."""
    changed = set()
    for item in kept:
        for found in list(strings(bpy, item)):
            if _clean_one(found, item, private):
                changed.add(item.name)
    return sorted(changed)


def _clean_one(found, item, private):
    if found[0] == "rna":
        _kind, struct, name, subtype, writable = found
        text = getattr(struct, name)
        new = _clean_text(text, item, private, subtype in PATH_SUBTYPES) if isinstance(text, str) and text else None
        if new is None or not writable:
            return False
        overwrite(struct, name, new)
        return True
    _kind, group, key = found
    new = _clean_value(group[key], item, private)
    if new is None:
        return False
    group[key] = new
    return True


def _clean_value(value, item, private, depth=0):
    """``value`` (text, bytes, or a list of them and of groups) with its private paths cut, or None when unchanged."""
    if isinstance(value, (str, bytes)):
        return _clean_text(value, item, private) if value else None
    if depth > 8 or not isinstance(value, (list, tuple)) and type(value).__name__ != "IDPropertyArray":
        return None
    items, changed = list(value), False
    for index, element in enumerate(items):
        if hasattr(element, "keys") and not isinstance(element, (str, bytes)):
            for key in list(element.keys()):   # A group in a list: cleaned in place, never assigned again.
                new = _clean_value(element[key], item, private, depth + 1)
                if new is not None:
                    element[key] = new
            continue
        new = _clean_value(element, item, private, depth + 1)
        if new is not None:
            items[index], changed = new, True
    if not changed:
        return None
    return [element.to_dict() if hasattr(element, "to_dict") else element for element in items]


def _clean_text(value, item, private, path_like=False):
    """``value`` (text or bytes, UTF-8 read loosely) with its private paths cut, or None when unchanged. One longer than
    ``LARGE`` is read a window at a time, each with the context the path rule reads around its own part, so memory is
    the value, its result and a window, never the value decoded and folded whole (BREAKIT-C7A-9 L2: a 1 GB bytes
    property peaked at 12.9 GB)."""
    wide = isinstance(value, bytes)
    if len(value) <= LARGE:
        text = value.decode("utf-8", "surrogateescape") if wide else value
        new = private.clean(text, item, path_like and not wide)
        return new.encode("utf-8", "surrogateescape") if wide and new is not None else new
    from .worker_merge_scan import NARROW, Scanner
    margin = DETOUR + max(map(len, private.needles), default=0) + 64
    scanner, cuts = Scanner(private), []   # A window with none of its names' letters (in any ASCII case) is skipped.
    anchors = [anchor if wide else anchor.decode("ascii") for anchor, families in scanner.anchors if families == NARROW]
    for base in range(0, len(value), LARGE):
        low, high = max(0, base - margin), min(len(value), base + LARGE + margin)
        window = value[low:high]
        lowered = window.lower()
        if not scanner.plain and not any(anchor in lowered for anchor in anchors):
            continue
        del lowered
        text = window.decode("utf-8", "surrogateescape") if wide else window
        places = private.finds(text)
        at = _offsets(text, low, wide, (found for _start, found in places))
        places = [(start, found) for start, found in places if base <= at[found] < base + LARGE]   # Its own part.
        ends, reach = [], 0
        for start, found in places:   # One cut decides where a place inside it ends: never read again (SHOULD 1).
            if start < reach:
                continue
            end = private.path_end(text, start, found)
            if end is not None:
                ends.append((start, end))
                reach = end
        at = _offsets(text, low, wide, (index for pair in ends for index in pair))
        cuts += [(at[start], at[end], _basename(text[start:end])) for start, end in ends]
    if not cuts:
        return None
    pieces, at = [], 0
    for start, end, name in sorted(cuts):
        if start >= at:
            pieces += [value[at:start], name.encode("utf-8", "surrogateescape") if wide else name]
            at = end
    pieces.append(value[at:])
    return (b"" if wide else "").join(pieces)


def _offsets(text, low, wide, indices):
    """``{index: the value's own offset}`` for characters of a window starting at ``low``: as UTF-8 bytes when
    ``wide``, each found by encoding only the text since the one before, so a window's places cost its length once
    (BREAKIT-C7A-10 M3: per place, its whole prefix again)."""
    found, at, offset = {}, 0, low
    for index in sorted(set(indices)):
        if wide:
            offset += len(text[at:index].encode("utf-8", "surrogateescape"))
        else:
            offset = low + index
        found[index], at = offset, index
    return found
