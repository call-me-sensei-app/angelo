# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The names Blender gave a script's data-blocks in place of the names asked for: ``renamed_by_blender``. Facts only.

Blender keeps a data-block's name unique among the local data-blocks of its kind. A script that creates
"Camera" while the factory "Camera" exists gets "Camera.001"; ``obj.name = "Gate"`` while another "Gate"
exists gives "Gate.001", or leaves a "Gate.001" as it was; a copy, a primitive added again or a mesh made under
the name of one left behind is numbered the same way. A script's result listed the objects it changed by their
new names and nothing of the other kinds, and never said that a name was not the one asked for. Recorded
(1,106 one-shot trials, numbered names): 14 events, 12 refusals and 2 final misses, all
in typed programs (a new camera became "Camera.001"; a castle material became "Castle pale limestone.001" and a
later assign by its name failed), which state their own names (``stage_names``). Replaying the 869 recorded
scripts that completed (550 trials, scripts only, each trial from Blender's factory scene), 44 of the 793 that
completed again had such names: 668 in all, 658 of them meshes, then 6 materials, 2 cameras, a light and a World.
30 of them, in 24 castle scripts, are a mesh made by ``new_from_object`` and named while the mesh it replaced still
held the name, so it stayed numbered: only the observed assignment shows that.

A completed script's result therefore lists (``attach``), per ``bpy.data`` collection, every data-block the
script created or named whose name is not the one asked for. Where the name asked for is known it decides:

* a name the script assigned (``<block>.name = ...``, observed while it runs: ``script_rename_watch``) is listed
  when Blender gave another, a taken name or one cut to 255 bytes (its last assignment, while the block still has
  the name Blender gave); a numbered name the script assigned itself and got is never listed;
* a library override asks for its reference's name (``override_library.reference``);
* a data-block a file-reading call brought in (an import, an append, ``image.open``) is named after the file,
  which may itself number its names: it is never judged by its name's form (``script_rename_watch``).

Anything else the script created or renamed (``bpy.data.<kind>.new``, operators, copies) is judged by Blender
5.2's rule (``main_namemap.cc``, ``BLI_string_split_name_number``), checked in the compiled app by
``test_blender_script_renames.py``:

* the asked-for name splits at its last "." into a base and an all-digit number; a taken name gets the base
  and the lowest free number from 1: ``.001`` to ``.999``, then ``.1000`` and up (``suffixed``). Such a name
  is listed when another local data-block of its kind holds the base, after the script or before it (a holder
  the script deleted after the clash still caused it);
* names are at most 255 bytes. Where ``.001`` no longer fits (a taken name of ``CUT_FROM`` bytes or more)
  Blender cuts the name by one character per clash instead, so such a name is at most ``CUT_STEP`` bytes
  shorter than a taken name of ``CUT_FROM`` bytes or more that begins with it and is one character longer:
  listed when another local data-block holds that name (``_cut``).

Left out: data-blocks the script neither created nor named (an earlier clash is never repeated), names whose
base nobody else holds ("Post.001" with no "Post"), linked data-blocks (their names are unique only within
their library, and a local name never clashes with them), the Render Result and Viewer images (not the
document's), and everything when the script loaded another file (no scene and no object it found is left:
``_replaced``; a script that deletes and remakes every one of them is taken for that too). Not told apart by
names alone, outside an assignment: an asked-for "Post.001" that was taken while "Post" was free (Blender gives
"Post.002", whose base nobody holds: not listed), and ``bpy.data.<kind>.new("Post.001")`` next to "Post"
(listed). In 1,279 distinct recorded scripts no data-block was named either way (two spelled such a name, to
delete a leftover and to look up a node), none appended, imported or overrode anything, and no name was longer
than 31 characters.

In the field, the kinds models look up by name come first and the kinds an Object's data is (``OBJECT_DATA``),
reached through their objects, after them; a run of numbers of one base is one entry
(``"Cube.001 to Cube.058 (58)"``); at most ``MAX_ENTRIES`` entries, the rest counted per kind (``omitted``).

The readings are ``view_scene_check``'s inventory: each collection's session UIDs in one native read and its
names, before the script and, for the same collections, after it; only a collection whose UIDs or names
changed is compared, without a Python step per data-block. Every kind models look up by name is read; object
data is read while the whole stays within ``MAX_NAMES`` data-blocks, each kind counted before its names are
read: a kind that would pass the limit is not read, and the result names it (``not_checked``). A file that
links libraries also has each data-block's ``library`` (and a local one's override) read one by one. Costs are
measured in doc/angelo/performance.rst ("Names Blender gave a script's data-blocks").

Optional evidence: a reading or an observer that fails leaves the field out, and a failed or rolled-back script
gets nothing (``script_after.attach``). Main thread only; ``bpy`` is imported where used.
"""

from array import array
from itertools import compress
from operator import ne

from .script_rename_watch import Names
from .view_scene_check import COLLECTIONS, _inventory

FIELD = "renamed_by_blender"
# The kinds an Object's ``data`` is: models reach them through their objects, so they come after the kinds
# models look up by name, in the field, its cap and the read limit. Of 1,279 distinct recorded scripts, 696
# created meshes by name and 7 looked one up by name, 552 created cameras and none looked one up; objects were
# looked up by name in 764, materials in 235, Worlds in 93, collections in 41 and lights in 43 (a Sun reused).
OBJECT_DATA = frozenset({"meshes", "curves", "lights", "cameras", "armatures", "grease_pencils", "lattices",
                         "metaballs", "volumes", "pointclouds", "hair_curves", "speakers", "lightprobes"})
NAMED = tuple(name for name in COLLECTIONS if name not in OBJECT_DATA)  # Always read.
LIMITED = tuple(name for name in COLLECTIONS if name in OBJECT_DATA)    # Read within MAX_NAMES.
ORDER = NAMED + LIMITED
MAX_NAMES = 50_000        # Data-blocks read before a script, object data last: a kind past it is not read.
MAX_ENTRIES = 12          # Entries per result (a run is one entry); the names past them are counted.
RUN = 3                   # Consecutive numbers of one base shown as one entry, from this many.
CUT_FROM = 252            # Bytes: a taken name this long is cut, not numbered (".001" would pass 255 bytes).
CUT_STEP = 4              # Bytes one cut removes at most: one UTF-8 character.
MAX_NUMBER = 999_999_999  # main_namemap.cc's MAX_NUMBER: Blender never numbers past it.


def before():
    """The document's names before a script (``script_rename_watch.Names``), or None when they cannot be read."""
    try:
        import bpy
        data = bpy.data
        inventory = _inventory(data, NAMED)
        room = MAX_NAMES - sum(len(entry[1]) for entry in inventory.values())
        chosen = []
        for kind in LIMITED:
            collection = getattr(data, kind, None)
            count = 0 if collection is None else len(collection)  # Counted first: no names read past the limit.
            if collection is not None and count <= room:
                chosen.append(kind)
                room -= count
        inventory.update(_inventory(data, chosen))
        return Names(_reading(data, inventory))
    except Exception:  # noqa: BLE001  Optional evidence: the script runs and reports without it.
        return None


def attach(result, names):
    """Add ``renamed_by_blender`` to a completed script's ``result`` in place when Blender gave any of its
    data-blocks another name than the one asked for, or when kinds were left unread; ``names``: ``before``'s, as
    ``script_rename_watch.watching`` filled them while the script ran. Returns ``result``."""
    if names is None or names.unsure:
        return result
    try:
        import bpy
        data = bpy.data
        read = tuple(names.reading[0])  # After the script, the same collections as before it, however many now.
        decided, listed = _asks(names.asks, _kinds(data))
        found = renamed(names.reading, _reading(data, _inventory(data, read)), leave=decided | names.from_files)
        for kind, more in listed.items():
            found[kind] = found.get(kind, []) + [name for name in more if name not in found.get(kind, ())]
        unread = [kind for kind in LIMITED if kind not in read and getattr(data, kind, None)]  # O(1) emptiness.
    except Exception:  # noqa: BLE001  Optional evidence: the script's own result stands without it.
        return result
    if found or unread:
        result[FIELD] = shown(found, unread)
    return result


def _reading(data, inventory):
    """``(inventory, linked, expected)``: in a file that links libraries, each collection's linked session UIDs
    and, for its local library overrides, ``{uid: the reference's name}``; empty otherwise."""
    linked, expected = {}, {}
    if getattr(data, "libraries", None):
        for kind, (uids, _names, _skip) in inventory.items():
            own_linked, own_expected = set(), {}
            for block, uid in zip(getattr(data, kind), array("q", uids)):
                if block.library is not None:
                    own_linked.add(uid)
                    continue
                override = block.override_library
                reference = None if override is None else override.reference
                if reference is not None:
                    own_expected[uid] = reference.name
            if own_linked:
                linked[kind] = frozenset(own_linked)
            if own_expected:
                expected[kind] = own_expected
    return inventory, linked, expected


def _kinds(data):
    """The ``bpy.data`` collection a data-block is in, by its RNA type (or a base type of it), or None."""
    by_type = {prop.fixed_type.identifier: prop.identifier for prop in data.bl_rna.properties
               if prop.type == "COLLECTION" and prop.identifier in COLLECTIONS}

    def kind(block):
        rna = block.bl_rna
        while rna is not None:
            if rna.identifier in by_type:
                return by_type[rna.identifier]
            rna = getattr(rna, "base", None)
        return None
    return kind


def _asks(asks, kind):
    """From the names the script assigned: the session UIDs they decide, and ``{collection: [name]}`` of those
    Blender gave another name. An assignment decides while its block keeps the name Blender gave it then."""
    decided, listed = set(), {}
    for uid, (block, asked, got) in asks.items():
        try:
            final, local, collection = block.name, block.library is None, kind(block)
        except (ReferenceError, AttributeError, RuntimeError, TypeError):
            continue  # Removed since.
        if final != got or not local or collection is None:
            continue  # Renamed again since, by other means: its name's form decides.
        decided.add(uid)
        if got != asked:
            listed.setdefault(collection, []).append(final)
    return decided, listed


def suffixed(name):
    """The base of ``name`` when it has the form Blender gives a taken name (``base.001``), else None."""
    base, dot, digits = name.rpartition(".")
    if not (dot and base and digits.isascii() and digits.isdigit()):
        return None
    if len(digits) == 3 and digits != "000" or 3 < len(digits) and digits[0] != "0" and int(digits) <= MAX_NUMBER:
        return base
    return None


def _size(name):
    return len(name.encode("utf-8", "surrogatepass"))


def _rows(entry, linked):
    """``(uids, names)``: a collection's local data-blocks in Blender's order, or None when its reading is
    incomplete."""
    uids, names, skip = entry
    uids = array("q", uids).tolist()
    if len(uids) != len(names):
        return None
    leave = skip | linked
    if not leave:
        return uids, names
    keep = [uid not in leave for uid in uids]
    return list(compress(uids, keep)), list(compress(names, keep))


def _cut(name, own, *held):
    """Whether ``name`` is one Blender cut: a name one character longer that begins with it, of ``CUT_FROM`` bytes
    or more, is held by another data-block (``own``: the block's own name before the script)."""
    return any(len(other) == len(name) + 1 and other != own and other.startswith(name) and _size(other) >= CUT_FROM
               for names in held for other in names)


def _collection(old, new, leave=frozenset(), expected=None):
    """The names in one collection Blender gave in place of taken ones (module docstring), in Blender's order.
    ``leave``: session UIDs judged elsewhere; ``expected``: ``{uid: name asked for}`` of library overrides."""
    (old_uids, old_names), (new_uids, new_names) = old, new
    expected = expected or {}
    was = dict(zip(old_uids, old_names))
    # Created or renamed by the script: its name is not the one its UID had. Native iteration, no Python per block.
    moved = list(compress(zip(new_uids, new_names), map(ne, map(was.get, new_uids), new_names)))
    found, held_now, held_before = [], None, None
    for uid, name in moved:
        if uid in leave:
            continue
        if uid in expected:  # An override asks for its reference's name.
            if name != expected[uid]:
                found.append(name)
            continue
        base = suffixed(name)
        if base is None and _size(name) < CUT_FROM - CUT_STEP:
            continue  # Neither of Blender's forms: the script's own name.
        if held_now is None:
            held_now, held_before = set(new_names), set(old_names)
        own = was.get(uid)  # Its name before the script, if it existed: never its own holder.
        if base is not None:
            if base in held_now or base != own and base in held_before:
                found.append(name)
        elif _cut(name, own, held_now, held_before):
            found.append(name)
    return found


def _replaced(old, new):
    """Whether another document replaced the one read before (the script loaded a file): both readings have its
    scenes and none of them, nor any of its objects, is left. Undo keeps session UIDs; a load gives new ones."""
    for collection in ("scenes", "objects"):
        before_entry, after_entry = old.get(collection), new.get(collection)
        if before_entry and after_entry and not set(array("q", before_entry[0])).isdisjoint(
                array("q", after_entry[0])):
            return False
    return bool(old.get("scenes") and old["scenes"][0] and new.get("scenes"))


def renamed(before_reading, after_reading, leave=frozenset()):
    """``{collection: [name, ...]}`` of the names Blender gave in place of taken ones, between two readings
    (``_reading``'s), by the names' form; ``leave``: session UIDs judged elsewhere (``attach``). Nothing when the
    script replaced the document: every name in it is new to the reading before."""
    (old, old_linked, _old_expected), (new, new_linked, expected) = before_reading, after_reading
    if _replaced(old, new):
        return {}
    found = {}
    for collection in COLLECTIONS:
        if collection not in old or collection not in new or old[collection] == new[collection]:
            continue
        before_rows = _rows(old[collection], old_linked.get(collection, frozenset()))
        after_rows = _rows(new[collection], new_linked.get(collection, frozenset()))
        if before_rows is None or after_rows is None:
            continue
        names = _collection(before_rows, after_rows, leave, expected.get(collection))
        if names:
            found[collection] = names
    return found


def _entries(names):
    """``[(entry, count)]`` in the order of ``names``: each name, and each run of ``RUN`` or more consecutive
    numbers of one base as ``"<first> to <last> (<count>)"``."""
    groups = {}
    for name in names:
        base = suffixed(name)
        groups.setdefault(name if base is None else (base,), []).append(name)  # A base is a tuple: never a name.
    entries = []
    for key, members in groups.items():
        if not isinstance(key, tuple):
            entries.append((key, 1))
            continue
        rows = sorted((int(name.rpartition(".")[2]), name) for name in members)
        start = 0
        for index in range(1, len(rows) + 1):
            if index < len(rows) and rows[index][0] == rows[index - 1][0] + 1:
                continue
            run = rows[start:index]
            if len(run) >= RUN:
                entries.append(("%s to %s (%d)" % (run[0][1], run[-1][1], len(run)), len(run)))
            else:
                entries.extend((name, 1) for _number, name in run)
            start = index
    return entries


def shown(found, unread=()):
    """The result's field: ``{collection: [entry, ...]}`` in ``ORDER``, at most ``MAX_ENTRIES`` entries in all;
    ``omitted``: ``{collection: names}`` left out past them; ``not_checked``: the kinds ``unread``."""
    field, count, omitted = {}, 0, {}
    for collection in ORDER:
        for entry, names in _entries(found.get(collection, ())):
            if count == MAX_ENTRIES:
                omitted[collection] = omitted.get(collection, 0) + names
                continue
            field.setdefault(collection, []).append(entry)
            count += 1
    if omitted:
        field["omitted"] = omitted
    if unread:
        field["not_checked"] = list(unread)
    return field
