# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A modifier's whole state, so that a modifier the commit removed comes back exactly as it was (the rollback,
``program_transfers``). No ``bpy`` import: the RNA structs come in.

Its writable settings are ``program_transfers._settings``. Everything else it owns is walked through RNA here: every
struct it owns (Geometry Nodes' input, output and panel stores under ``properties``, curve mappings, profiles,
simulation settings), and every collection inside those. Blender 5.x moved Geometry Nodes inputs out of the
modifier's ID properties into ``properties.inputs.<identifier>`` (``value``, ``type``, ``attribute_name``,
``layer_name``); a modifier has no ID properties at all in 5.3, so reading them gave every input back at its default
(the data loss v210-features found, 2026-11-15). The walk follows RNA, not a list of known fields, so a socket kind,
a field or a store that a later Blender adds is captured too.

What RNA cannot write back is never silently dropped. A read-only value that holds data (a bind, Multires levels, a
baked cache, a hook's vertex indices), a struct whose content is not RNA (a particle system), a collection of bakes,
or a collection of an unknown kind makes ``capture`` raise ``Unrestorable``, naming it, BEFORE anything is removed:
staging refuses the removal (``action_modifiers``), and the commit does too (``program_transfers._sync_stack``).
``restore`` writes the state into the re-created modifier, then captures it again: anything that differs fails
loudly (``ProgramError``)."""

from .program_jobs import ProgramError
from .rna_fields import _RNA_SKIP

MAX_DEPTH = 6   # modifier > properties > inputs > socket > value; CurveMapping > curves > points.

# Read-only values Blender derives or keeps for itself: they hold no data a restore must write. Each reason says why.
DERIVED = {
    "execution_time": "the time the last evaluation took",
    "persistent_uid": "an identifier Blender assigns to each new modifier",
    "is_override_data": "whether the modifier comes from a library override",
    "face_count": "Decimate's evaluated face count",
    "gpu_subdivision_status": "whether the GPU evaluates the subdivision",
    "hair_grid_min": "the cloth solver's last hair grid", "hair_grid_max": "the cloth solver's last hair grid",
    "hair_grid_resolution": "the cloth solver's last hair grid",
    "is_baking": "a bake running now", "is_outdated": "whether the cache needs a new bake",
    "is_frame_skip": "whether the cache skips frames now", "info": "the cache's status line",
    "name": "a generated store's label (a socket's name comes from its node group)",
    "bake_id": "the bake's node identifier, from the node group",
}
# Collections Blender fills or derives itself: never written back.
DERIVED_COLLECTIONS = {
    "node_warnings": "the last evaluation's warnings",
    "segments": "a profile's evaluated segments, from its points",
}
# Collections whose length Blender sets from the restored settings (fixed or counted): their items are written back
# in order, and a length that differs after the settings is a loud failure.
SIZED_BY_SETTINGS = {
    "curves": "a curve mapping's curves (one per channel)",
    "projectors": "UV Project's projectors (projector_count)",
    "point_caches": "a simulation's cache list",
}
# Struct kinds whose content is not RNA: removing a modifier that owns one would discard it.
OPAQUE = {
    "ParticleSystem": "a particle system's particles, settings link and caches",
}
# Stores a removed modifier would lose with no RNA to write back, by collection name: refused when not empty.
HELD_DATA = {
    "bakes": "Geometry Nodes bakes, whose baked or packed data Blender keeps outside RNA",
    "data_blocks": "a bake's data-block mapping",
}


class Unrestorable(ValueError):
    """``where`` (a path below the modifier) holds data a re-created modifier could not get back; ``why`` says what."""

    def __init__(self, where, why):
        super().__init__("%s: %s" % (where, why))
        self.where, self.why = where, why


def refusal(modifier):
    """Why removing ``modifier`` could not be undone exactly, or None."""
    try:
        capture(modifier)
    except Unrestorable as error:
        return "The modifier %r holds %s (%s), which undoing its removal could not put back" % (
            modifier.name, error.why, error.where)
    return None


def capture(modifier):
    """The modifier's whole state, for ``restore``; raises ``Unrestorable`` for any store it could not write back."""
    from .program_transfers import _settings
    return {"name": modifier.name, "type": modifier.type, "settings": _settings(modifier),
            "stores": _walk(modifier, modifier.name, 0, top=True)}


def _held(value):
    return bool(len(value)) if hasattr(value, "__len__") and not isinstance(value, str) else bool(value)


def _plain(prop, value):
    dimensions = getattr(prop, "array_dimensions", ())
    if prop.type == "FLOAT" and len(dimensions) > 1 and dimensions[1] > 0:
        return value.copy()   # A matrix stays one: its rows as tuples would transpose it.
    if prop.type in {"BOOLEAN", "INT", "FLOAT"} and getattr(prop, "is_array", False):
        return tuple(value)
    return value


def _walk(struct, path, depth, top=False):
    """``{name: value | {"__struct__": ...} | {"__items__": [...]}}`` of what ``struct`` owns. At the top (the modifier
    itself) writable values are ``_settings``'s; below it, every writable value is captured here."""
    state = {}
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name in _RNA_SKIP or (top and name in {"name", "type"}):
            continue
        where = "%s.%s" % (path, name)
        value = getattr(struct, name)
        if prop.type == "COLLECTION":
            if name in DERIVED_COLLECTIONS:
                continue
            if name in HELD_DATA and len(value):
                raise Unrestorable(where, HELD_DATA[name])
            if depth >= MAX_DEPTH:
                raise Unrestorable(where, "a store nested too deep to put back exactly")
            if len(value) and name not in SIZED_BY_SETTINGS and not _resizable(value):
                raise Unrestorable(where, "a collection of %s that cannot be rebuilt" % prop.fixed_type.identifier)
            state[name] = {"__items__": [_walk(item, "%s[%d]" % (where, index), depth + 1)
                                         for index, item in enumerate(value)]}
        elif prop.type == "POINTER" and prop.is_readonly:
            if value is None or hasattr(value, "users") or value.id_data != struct.id_data:
                continue   # An ID, or a struct another data-block owns (a bake's node): a reference, not a store.
            kind = value.bl_rna.identifier
            if kind in OPAQUE:
                raise Unrestorable(where, OPAQUE[kind])
            if depth >= MAX_DEPTH:
                raise Unrestorable(where, "a store nested too deep to put back exactly")
            state[name] = {"__struct__": _walk(value, where, depth + 1)}
        elif prop.is_readonly:
            if name not in DERIVED and _held(value):
                raise Unrestorable(where, "%s = %r, which only Blender itself sets" % (name, value))
        elif not top:
            state[name] = _plain(prop, value)
    return state


def _resizable(collection):
    return (hasattr(collection, "new") or hasattr(collection, "add")) and hasattr(collection, "remove")


def _resize(collection, count, where):
    """Give ``collection`` ``count`` items (a curve's or a profile's points), each written afterwards."""
    while len(collection) > count and hasattr(collection, "remove"):
        collection.remove(collection[len(collection) - 1])
    while len(collection) < count and _resizable(collection):
        before = len(collection)
        (collection.new if hasattr(collection, "new") else collection.add)(0.0, 0.0)
        if len(collection) == before:
            break
    if len(collection) != count:
        raise ProgramError("A restored modifier's %s has %d items, not the %d it had" % (where, len(collection), count),
                           code='INTERNAL_ERROR')


def _put(struct, state, mapping, path):
    from .program_transfers import _live_id, held_exactly
    for name, value in state.items():
        where = "%s.%s" % (path, name)
        if isinstance(value, dict) and "__struct__" in value:
            _put(getattr(struct, name), value["__struct__"], mapping, where)
        elif isinstance(value, dict) and "__items__" in value:
            collection, items = getattr(struct, name), value["__items__"]
            if name not in SIZED_BY_SETTINGS:
                _resize(collection, len(items), where)
            elif len(collection) != len(items):
                raise ProgramError("A restored modifier's %s has %d items, not the %d it had"
                                   % (where, len(collection), len(items)), code='INTERNAL_ERROR')
            for index, item in enumerate(items):
                _put(collection[index], item, mapping, "%s[%d]" % (where, index))
        else:
            expected = _live_id(value, mapping)
            if not held_exactly(getattr(struct, name), expected):
                setattr(struct, name, expected)   # Only what differs, exactly: a rewrite of a held value can clamp it.
    if hasattr(struct, "update") and callable(struct.update) and struct.bl_rna.identifier in {"CurveMapping",
                                                                                               "CurveProfile"}:
        struct.update()   # Their evaluated table follows the points written above.


def restore(modifier, state, mapping):
    """Write ``state`` (``capture``'s) into ``modifier``, a fresh one of the same type, then prove it: the modifier's
    own state captured again must equal it, or this fails loudly."""
    from .program_transfers import _live_id, _set_settings
    _set_settings(modifier, state["settings"], mapping)   # First: a node group builds the stores written next.
    _put(modifier, state["stores"], mapping, modifier.name)
    try:
        again = capture(modifier)
    except Unrestorable as error:
        raise ProgramError("A restored modifier holds a store it did not have: " + str(error), code='INTERNAL_ERROR')
    expected = _mapped(state["stores"], mapping, _live_id)
    if _comparable(again["stores"]) != _comparable(expected):
        raise ProgramError("The restored modifier %r did not get back every input and setting it had" % modifier.name,
                           code='INTERNAL_ERROR')


def _mapped(state, mapping, live_id):
    if isinstance(state, dict):
        return {key: _mapped(value, mapping, live_id) for key, value in state.items()}
    if isinstance(state, list):
        return [_mapped(value, mapping, live_id) for value in state]
    return live_id(state, mapping)


def _comparable(state):
    """``state`` with matrices as nested tuples and data-blocks by identity, so two captures compare by value."""
    if isinstance(state, dict):
        return {key: _comparable(value) for key, value in state.items()}
    if isinstance(state, list):
        return [_comparable(value) for value in state]
    if hasattr(state, "as_pointer"):
        return ("pointer", state.as_pointer())
    if hasattr(state, "to_tuple") and hasattr(state, "row"):
        return tuple(tuple(row) for row in state)
    return state
