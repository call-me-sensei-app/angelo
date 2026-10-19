# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-family live transfers the commit uses: settings path by path, modifier and constraint
stacks by value, and appended NLA tracks, each journaled for rollback."""
from .program_jobs import ProgramError
from .program_dependencies import _RNA_SKIP
from .action_properties import data_family, holder_and_name, plain


_SETTINGS_UI_FIELDS = frozenset({"is_active", "show_expanded"})
# Added objects a result lists by name (a batch can add a thousand); the count is always complete.
MAX_RESULT_ROWS = 128


def _settings(value, depth=0):
    from .generic_settings import NESTED_SETTINGS_TYPES
    from .rna_rules import DENIED_STRUCTS
    result = {}
    nested = depth or getattr(value, "type", None) in NESTED_SETTINGS_TYPES
    for prop in value.bl_rna.properties:
        if (nested and depth < 2 and prop.type == "POINTER" and prop.is_readonly and prop.identifier not in _RNA_SKIP
                and not getattr(prop, "is_runtime", False)):
            # Simulation settings structs (cloth, soft body, collision): copied field by field.
            child = getattr(value, prop.identifier, None)
            if child is not None and not hasattr(child, "users") and child.bl_rna.identifier not in DENIED_STRUCTS:
                result[prop.identifier] = {"__struct__": _settings(child, depth + 1)}
            continue
        if prop.is_readonly or prop.identifier in _RNA_SKIP or prop.identifier in {"name", "type"} | _SETTINGS_UI_FIELDS:
            continue
        if prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM", "POINTER"}:
            item = getattr(value, prop.identifier)
            # PointerProperty has no is_array in supported Blender builds.
            # Preserve ID/RNA identities; only numeric arrays are value tuples.
            # A matrix (two-dimensional array, e.g. Child Of's inverse) stays a
            # Matrix copy: assigning its row tuples would transpose it.
            if prop.type == "FLOAT" and len(getattr(prop, "array_dimensions", ())) > 1 and prop.array_dimensions[1] > 0:
                result[prop.identifier] = item.copy()
            else:
                result[prop.identifier] = tuple(item) if prop.type in {"BOOLEAN", "INT", "FLOAT"} and getattr(prop, "is_array", False) else item
    return result


def _live_id(value, mapping):
    return mapping.get(value.as_pointer(), value) if value is not None and hasattr(value, "as_pointer") else value


def _set_settings(value, settings, mapping):
    for name, item in settings.items():
        if isinstance(item, dict) and "__struct__" in item:
            _set_settings(getattr(value, name), item["__struct__"], mapping)
            continue
        expected = _live_id(item, mapping)
        setattr(value, name, expected)
        actual = getattr(value, name)
        actual = tuple(actual) if isinstance(expected, tuple) else actual
        if actual != expected:
            raise ProgramError("A committed supported setting did not retain its staged value: " + name, code='INTERNAL_ERROR')


def touched_paths(resources):
    from .program_verification import touched_paths as paths
    return paths(resources)


def modified_row(handle, name, resources):
    from .program_verification import modified_row as row
    return row(handle, name, resources)


def _transfer_settings(live, staged, paths, mapping, journal, *, commit_scope=None):
    """Copy allow-listed settings from a validated staged owner to its live owner.

    Each previous value is journaled before the write; an object pointer maps
    its staged copy to the live object. A value that does not read back
    exactly raises, and the caller's rollback restores the journal.
    """
    for path in paths:
        holder, name = holder_and_name(live, path)
        source, _ = holder_and_name(staged, path)
        value = getattr(source, name)
        pointer = holder.bl_rna.properties[name].type == "POINTER"
        value = _live_id(value, mapping) if pointer else plain(value)
        if isinstance(getattr(holder, name), (set, frozenset)):
            value = set(value)  # A flag enum.
        previous = getattr(holder, name)
        if commit_scope is not None and path == "render.engine" and live is commit_scope.backend.scene:
            commit_scope.engine_transfer(live, previous, value, journal)
        else:
            journal.append((holder, name, previous if pointer or isinstance(previous, (set, frozenset)) else plain(previous)))
            setattr(holder, name, value)
        actual = getattr(holder, name)
        if (actual != value) if pointer or isinstance(value, set) else (plain(actual) != value):
            raise ProgramError("A committed setting did not retain its staged value: " + path, code='INTERNAL_ERROR')
        if commit_scope is not None:
            commit_scope.record_transfer(getattr(source, "id_data", staged), getattr(holder, "id_data", live),
                                         "property_owner", path)


def transfer_scene_settings(scene, stage, mapping, journal, isolated, replaced_worlds, kept_worlds, commit_scope=None):
    """Copy the bound scene's written settings to the live ``scene``; give it its staged World when that is the edit.

    ``world.`` paths ride in the staged World copy when the scene takes it:
    ``world_configure``'s copy (assigned by the caller) or the edited copy of
    a World other scenes share (``program_preimages.assign_isolated_world``),
    so the World the other scenes keep is never written.
    """
    from .program_preimages import assign_isolated_world, isolated_world
    changes = stage.changes[stage.binding["scene"]["handle"]]
    paths = touched_paths(changes)
    copied = "world" in changes or isolated_world(stage)
    if copied:
        paths = [path for path in paths if data_family(path) != "world"]
    _transfer_settings(scene, stage.scene, paths, mapping, journal, commit_scope=commit_scope)
    if copied and "world" not in changes:
        pair = assign_isolated_world(scene, stage, journal, isolated,
                                     commit_scope.record_transfer if commit_scope is not None else None)
        replaced_worlds.append(pair)
        kept_worlds.append(pair[1])


def _id_property_values(block):
    """Plain copies of a modifier's ID properties (Geometry Nodes inputs), for rollback."""
    values = {}
    try:
        keys = list(block.keys())
    except TypeError:
        return values  # Only Geometry Nodes modifiers carry ID properties.
    for key in keys:
        value = block[key]
        values[key] = value.to_dict() if hasattr(value, "to_dict") else value.to_list() if hasattr(value, "to_list") else value
    return values


def _sync_stack(original, staged, journal):
    """Remove the live modifiers the stage removed or applied, then order the stack like the stage.

    Each removed modifier's type, index, RNA settings and ID properties are
    journaled first; ``_restore_stacks`` rebuilds them on rollback.
    """
    names = [modifier.name for modifier in staged.modifiers]
    before = [modifier.name for modifier in original.modifiers]
    for index in reversed(range(len(original.modifiers))):
        modifier = original.modifiers[index]
        if modifier.name not in names:
            journal.append(("removed", original, index, {"name": modifier.name, "type": modifier.type,
                                                         "settings": _settings(modifier),
                                                         "id_properties": _id_property_values(modifier)}))
            original.modifiers.remove(modifier)
    if [modifier.name for modifier in original.modifiers] != names:
        journal.append(("order", original, before))
        for index, name in enumerate(names):
            current = [modifier.name for modifier in original.modifiers].index(name)
            if current != index:
                original.modifiers.move(current, index)
    if [modifier.name for modifier in original.modifiers] != names:
        raise ProgramError("The committed modifier stack does not match its validated staged order", code='INTERNAL_ERROR')


def active_state(obj):
    """The object's active modifier and active NLA track, for a rollback to set again.

    ``modifiers.new`` and ``nla_tracks.new`` activate what they add, a rollback's
    re-created modifiers included, so commit and rollback move the active flags.
    A track is named by index and name: rebuilt tracks are new RNA, never the old pointer.
    """
    tracks = obj.animation_data.nla_tracks if obj.animation_data else None
    track = next(((index, item.name) for index, item in enumerate(tracks or ()) if item.active), None)
    return obj.modifiers.active.name if obj.modifiers.active else None, track


def restore_active_state(obj, state):
    modifier, track = state
    obj.modifiers.active = obj.modifiers.get(modifier) if modifier is not None else None
    tracks = obj.animation_data.nla_tracks if obj.animation_data else None
    if tracks is not None:
        index, name = track if track is not None else (None, None)
        same = index is not None and index < len(tracks) and tracks[index].name == name
        tracks.active = tracks[index] if same else None


def _restore_stacks(journal):
    for _kind, obj, records in [entry for entry in journal if entry[0] == "constraints"]:
        from .operator_state import restore_constraints  # A constraint stack sync_constraints replaced.
        restore_constraints(obj, records)
    removed = [entry for entry in journal if entry[0] == "removed"]
    for _kind, obj, index, record in sorted(removed, key=lambda entry: entry[2]):
        modifier = obj.modifiers.new(record["name"], record["type"])
        _set_settings(modifier, record["settings"], {})
        for key, value in record["id_properties"].items():
            modifier[key] = value
        obj.modifiers.move(len(obj.modifiers) - 1, min(index, len(obj.modifiers) - 1))
    for _kind, obj, before in [entry for entry in journal if entry[0] == "order"]:
        for index, name in enumerate(name for name in before if obj.modifiers.get(name) is not None):
            current = [modifier.name for modifier in obj.modifiers].index(name)
            if current != index:
                obj.modifiers.move(current, index)


_STRIP_SETTINGS = ("blend_type", "extrapolation", "influence", "use_animated_influence", "mute", "scale", "repeat",
                   "use_reverse", "use_sync_length", "blend_in", "blend_out")


def _append_tracks(original, staged, journal):
    """Give the live object the NLA tracks staging appended, referencing the staged Actions."""
    source = staged.animation_data
    animation = original.animation_data_create()
    for track in list(source.nla_tracks)[len(animation.nla_tracks):]:
        created = animation.nla_tracks.new()
        journal.append((animation, created))
        created.name = track.name
        for strip in track.strips:
            target = created.strips.new(strip.name, int(strip.frame_start), strip.action)
            if hasattr(strip, "action_slot"):
                target.action_slot = strip.action_slot
            for name in _STRIP_SETTINGS:
                if hasattr(strip, name):
                    setattr(target, name, getattr(strip, name))
            if (target.frame_start, target.frame_end, target.action) != (strip.frame_start, strip.frame_end, strip.action):
                raise ProgramError("A committed NLA strip does not match its staged push-down", code='INTERNAL_ERROR')
