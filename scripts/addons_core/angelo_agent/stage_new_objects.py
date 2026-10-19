# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""IDs a program creates, across a suspension: identity kept, names followed.

A suspended stage holds none of its IDs in Main (``program_stage_bundle``), so the name an ID the program created
holds is free while the approval waits. When the artist takes it (a Shift+D of a sibling, a new object or
material), the staged ID comes back under another name: Blender renames it as the native owner re-attaches it, or
as a file-backed bundle is loaded. Followed by name alone, a separated part, looked up by
name, committed into the Scene Collection, results named the artist's object or material, and a retained preview
lost the object (INTERNAL_ERROR).

Identity, not the name, finds each one again: every new object and every other ID the program created with a public
name (a copied material, a new mesh, group, Action...). A memory-backed suspension keeps the same IDs and records
the name and type each had (``SUSPENDED``, by pointer); a file-backed one marks each with the name it had
(``MARKER``, an ID property only the private bundle ever holds: set just before the write, removed on load). A
move is recorded per ID type (``DRIFTED``: the name a result gave -> the name held now, composed across
suspensions), the staged collections of an object without an alias (``program_names.NAMED``) follow it, and
commit corrects the result (``correct_result``): object fields follow an object's move and material fields a
material's, never a same-named ID of another type, and ``renamed_while_waiting`` lists every
move. A requested object name that was taken fails its derived ``object_name`` check before commit.
"""

from .program_names import NAMED

MARKER = "_angelo_new_object"
SUSPENDED = "suspended_new_names"
DRIFTED = "drifted_new_names"
NOTE = "renamed_while_waiting"
MAX_NOTE_ROWS = 32
_PRIVATE = "__angelo_"


def new_objects(stage):
    """The staged objects this program made: every stage object that is not a declared object's copy."""
    declared = {obj.as_pointer() for obj in stage.objects.values()}
    return [obj for obj in getattr(stage.scene, "objects", ()) if obj.as_pointer() not in declared]


def created(stage):
    """Every ID this program made that holds a public name: its new objects and the other IDs it created."""
    made = {obj.as_pointer(): obj for obj in new_objects(stage)}
    context = getattr(stage, "context", None)
    for block in getattr(context, "created_ids", None) or ():
        try:
            name, kind = block.name, block.id_type
        except ReferenceError:
            continue
        if kind not in ("SCENE", "COLLECTION") and not name.startswith(_PRIVATE):
            made.setdefault(block.as_pointer(), block)
    return list(made.values())


def remember(stage):
    """Before a suspension: the type and name of each created ID, by identity (memory-backed); returns the IDs."""
    made = created(stage)
    if made:
        stage.extras[SUSPENDED] = {block.as_pointer(): [block.id_type, block.name] for block in made}
    return made


def mark(made):
    """Just before a file-backed bundle is written: each created ID carries the name it has."""
    for block in made:
        block[MARKER] = block.name


def unmark(made):
    for block in made:
        if MARKER in block.keys():
            del block[MARKER]


def reattached(stage):
    """After a memory-backed stage is attached again: record the created IDs Blender renamed."""
    names = getattr(stage, "extras", {}).pop(SUSPENDED, None)
    if not names:  # Nothing was created (or nothing was recorded for it).
        return
    moved = {}
    for block in created(stage):
        kind, old = names.get(block.as_pointer(), (None, None))
        if old is not None and old != block.name:
            moved.setdefault(kind, {})[old] = block.name
    follow(stage.extras, moved)


def loaded(blocks):
    """After a file-backed bundle is loaded: {(type, the name each created ID had): ID}, markers removed."""
    held = {}
    for block in blocks:
        try:
            keys = block.keys()
        except (ReferenceError, AttributeError, TypeError):
            continue
        if MARKER in keys:
            held[(block.id_type, str(block[MARKER]))] = block
            del block[MARKER]
    return held


def follow(extras, moved):
    """IDs that came back under other names (``moved``: type -> {old: new}): results and staged collections follow."""
    extras.pop(SUSPENDED, None)
    if not moved:
        return
    drifted = {kind: dict(names) for kind, names in extras.get(DRIFTED, {}).items()}
    for kind, names in moved.items():
        composed = {first: names.get(now, now) for first, now in drifted.get(kind, {}).items()}
        composed.update(names)
        drifted[kind] = {old: new for old, new in composed.items() if old != new}
    extras[DRIFTED] = drifted
    named = extras.get(NAMED)
    if named and moved.get("OBJECT"):
        objects = moved["OBJECT"]
        extras[NAMED] = {objects.get(name, name): members for name, members in named.items()}


def note(extras):
    """The public result note: every created ID that committed under another name than results first gave."""
    rows = [{"type": kind, "staged": old[:64], "committed": new[:64]}
            for kind, names in sorted(extras.get(DRIFTED, {}).items()) for old, new in sorted(names.items())]
    return rows[:MAX_NOTE_ROWS]


def _field(holder, key, names):
    if isinstance(holder, dict) and isinstance(holder.get(key), str) and holder[key] in names:
        holder[key] = names[holder[key]]


def correct_result(result, moved):
    """Rows written before a move name the ID it moved: object fields by the object map, material fields by the
    material map, so a material named like a moved object keeps its name."""
    objects, materials = moved.get("OBJECT", {}), moved.get("MATERIAL", {})
    for row in result.get("operation_results") or ():
        if not isinstance(row, dict):
            continue
        _field(row, "object", objects)
        parts = row.get("created")
        if parts is True:
            _field(row, "name", objects)
        for part in parts if isinstance(parts, list) else ():
            _field(part, "name", objects)
        for part in row.get("parts") or ():
            _field(part, "first", objects)
            _field(part, "last", objects)
        _field(row, "material", materials)
        tree = row.get("tree")
        if isinstance(tree, dict) and tree.get("owner") == "material":
            _field(tree, "name", materials)
    delta = result.get("delta") if isinstance(result.get("delta"), dict) else {}
    for key in ("added", "geometry"):
        for item in delta.get(key) or ():
            _field(item, "name", objects)
    for item in delta.get("details") or ():
        change = (item.get("changes") or {}).get("materials") if isinstance(item, dict) else None
        if isinstance(change, dict) and isinstance(change.get("after"), list):
            change["after"] = [materials.get(name, name) if isinstance(name, str) else name for name in change["after"]]
    return result
