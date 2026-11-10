# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which drivers Blender evaluates natively, so an object they drive can be bound, staged and committed.

A scripted driver runs a Python expression and a context property reads
whatever scene and view layer evaluate it (the stage's, while staging), so
neither may be evaluated by a stage, and a driver that is not native makes its
object unbindable (``animation_driver_expressions``). An
Average, Sum, Minimum or Maximum driver whose variables read transforms,
distances, rotation differences or a stored property evaluates natively, like
a constraint. Its definition, every variable and target, is part of the
object's revision (``describe``), so a changed driver fails the binding.

Blender writes a driver's value through RNA from the driver's owner, reading the
old value first, so a destination that is a Python-defined property would run
its getter and setter (in a stage too): such a driver is not native either
(``problem(curve, owner)``). And a stage's depsgraph is active: it builds every
ID a driver's targets name and every ID their paths cross, and writes their
evaluated animation back to the originals, so a staged driver may reach only the
stage's own IDs (``stage_problem``).
"""

import ast

NATIVE_TYPES = frozenset({"AVERAGE", "SUM", "MIN", "MAX"})
NATIVE_VARIABLES = frozenset({"TRANSFORMS", "LOC_DIFF", "ROTATION_DIFF", "SINGLE_PROP"})
_TARGET_FIELDS = ("data_path", "transform_type", "transform_space", "rotation_mode", "bone_target", "id_type")


PYTHON_DESTINATION = "a driver writing a Python-defined property"
WRITES_ELSEWHERE = "a driver that writes another datablock"
STORED_THROUGH = "a driver path that continues past a stored custom property"
OUTSIDE_STAGE = "a driver reading data outside the stage"


def problem(curve, owner=None):
    """None when Blender evaluates ``curve``'s driver natively, else why not (a short reason). ``owner``: the ID whose
    animation data holds it, for its destination (not checked when None)."""
    driver = curve.driver
    if driver.type not in NATIVE_TYPES:
        return "a %s driver" % driver.type.lower()
    for variable in driver.variables:
        if variable.type not in NATIVE_VARIABLES:
            return "a %s driver variable" % variable.type.lower()
        if variable.type == "SINGLE_PROP":
            found = _python_property(variable.targets[0])
            if found:
                return found
    return None if owner is None else destination_problem(owner, curve.data_path)


def destination_problem(owner, path):
    """None when a driver of ``owner`` writing ``path`` writes only ``owner`` (or an ID it embeds: a material's node
    tree) through no Python-defined property, else why not: ``PYTHON_DESTINATION`` (its getter and setter would run)
    or ``WRITES_ELSEWHERE`` (the animation system writes the destination of the original owner, so a destination in
    another datablock writes the person's data whether or not the program commits)."""
    if owner is None or not path:
        return None
    from .dependency_paths import path_steps
    steps = path_steps(path)
    if not steps:
        return PYTHON_DESTINATION   # Not parsed: not proven.
    try:
        from bpy.types import bpy_prop_collection, bpy_struct
    except ImportError:  # Off-host: nothing resolves, so nothing is proven.
        return PYTHON_DESTINATION
    holder, last, left = owner, len(steps) - 1, False
    for position, (text, numeric) in enumerate(steps):
        try:
            if numeric:
                holder = _item(holder, int(text[1:-1]), bpy_prop_collection, bpy_struct)
            elif text.startswith("["):
                stored = not isinstance(holder, bpy_prop_collection)
                holder = _item(holder, ast.literal_eval(text[1:-1]), bpy_prop_collection, bpy_struct)
                if stored and not hasattr(holder, "bl_rna"):
                    if not _stored_keys_only(steps[position + 1:]):
                        return STORED_THROUGH   # Past a stored value: not proven (an ID a group holds).
                    break  # A stored custom property: no getter or setter.
            else:
                name = text.lstrip(".")
                prop = holder.bl_rna.properties.get(name)
                if prop is None:
                    return None   # No such property: Blender writes nothing.
                if getattr(prop, "is_runtime", True):
                    return PYTHON_DESTINATION
                if position < last:
                    holder = getattr(holder, name)
            if not _same(getattr(holder, "id_data", owner), owner):
                left = True
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, SyntaxError, ReferenceError):
            # A step this walk cannot take (an array component, ``location.x``) may still resolve in RNA: Blender's
            # own resolution decides where the value lands; a path it cannot resolve writes nothing.
            return WRITES_ELSEWHERE if written(owner, path) is not None else None
    return WRITES_ELSEWHERE if left and written(owner, path) is not None else None


def written(owner, path):
    """The first datablock other than ``owner`` (or one it embeds) that a destination path writes, or None."""
    from .dependency_paths import INCOMPLETE, read_ids
    read = read_ids(owner, path)
    if read is None:
        return None
    if read is INCOMPLETE:
        return owner   # Cannot be walked: not proven to stay in the owner.
    return next((block for block in read.blocks[1:]
                 if not _same(block, owner) and not getattr(block, "is_embedded_data", False)), None)


def _same(first, second):
    return first is second or (hasattr(first, "as_pointer") and hasattr(second, "as_pointer")
                               and first.as_pointer() == second.as_pointer())


def _item(holder, key, collection, struct):
    """``holder[key]`` through Blender's own subscript (C), never a Python subclass's ``__getitem__``."""
    if isinstance(holder, collection):
        return collection.__getitem__(holder, key)
    if isinstance(holder, struct):
        return struct.__getitem__(holder, key)
    return holder[key]


def reaches(curve, inside=None):
    """Every ID ``curve``'s driver reaches now: each variable target's ID and, for a single-property variable, every
    ID its path crosses (``dependency_paths.read_ids``); None where a path cannot be walked. ``inside``: stop at the
    first ID it denies (returned last), before walking a path from it."""
    from .dependency_paths import INCOMPLETE, read_ids
    found = []
    for variable in curve.driver.variables:
        for target in variable.targets:
            block = target.id
            if block is None:
                continue
            found.append(block)
            if inside is not None and not inside(block):
                return found
            if variable.type == "SINGLE_PROP" and target.data_path:
                read = read_ids(block, target.data_path)
                if read is INCOMPLETE:
                    return None
                for crossed in read.blocks[1:] if read is not None else ():
                    found.append(crossed)
                    if inside is not None and not inside(crossed):
                        return found
    return found


def stage_problem(curve, owner, inside):
    """(reason, the ID it names or None) for a staged driver: ``problem``, or ``OUTSIDE_STAGE`` with the first ID it
    reaches that ``inside(block)`` denies, or (None, None). Checked before any evaluation of the stage."""
    found = problem(curve, owner)
    if found is not None:
        named = written(owner, curve.data_path) if found == WRITES_ELSEWHERE else None
        return found, (None if named is owner else named)
    reached = reaches(curve, inside)
    if reached is None:
        return OUTSIDE_STAGE, None
    if reached and not inside(reached[-1]):
        return OUTSIDE_STAGE, reached[-1]
    return None, None


def stage_refusal(reason, holder, curve, named=None, context=None):
    """The model-visible refusal of a staged driver (``stage_problem``): the datablock it reads or writes, named, or
    why Blender's evaluation of it is not native. Every name is the one the person knows (``_shown``)."""
    from .scene_read_values import label
    driver = "the driver %r has on %s" % (label(_shown(context, holder), 64), label(curve.data_path, 96))
    if reason in (OUTSIDE_STAGE, WRITES_ELSEWHERE):
        verb = "reads" if reason == OUTSIDE_STAGE else "writes"
        if named is None:
            return ("A staged edit cannot evaluate %s: it %s data through a path that cannot be followed. Nothing was "
                    "changed; use reviewed Python for this edit" % (driver, verb))
        kind, name = named.bl_rna.name.lower(), label(_shown(context, named), 64)
        if any(_same(named, block) for block in getattr(context, "owned_ids", ())):
            # The edit's own copy of another datablock: no live write, but no revision of the driver's owner binds it.
            return ("A staged edit cannot evaluate %s: it writes the %s %r, a datablock other than the one that holds "
                    "the driver, which this edit's revision does not bind. Nothing was changed; use reviewed Python "
                    "for this edit" % (driver, kind, name))
        return ("A staged edit cannot evaluate %s: it %s the person's %s %r, and evaluating it in the stage would "
                "change the person's file. Nothing was changed; use reviewed Python for this edit"
                % (driver, verb, kind, name))
    return ("Native staged evaluation runs only drivers Blender evaluates natively, not %s (%s)"
            % (reason, driver))


def _python_property(target):
    """Why a single-property variable's path is not proven native, or None: a step reads anything a Python getter may
    compute (``is_runtime``) or does not resolve, or it continues past a stored custom property (``["name"]`` on a
    struct, not a collection) with anything but stored keys (``STORED_THROUGH``). Every step counts, so an index into
    a Python-defined vector is one."""
    python = "a driver reading a Python-defined property"
    block, path = target.id, target.data_path
    if block is None or not path:
        return None  # Nothing to read.
    from .dependency_paths import path_steps
    steps = path_steps(path)
    if not steps:
        return python
    holder = block
    try:
        from bpy.types import bpy_prop_collection, bpy_struct
    except ImportError:  # Off-host: nothing resolves, so nothing is proven.
        return python
    for position, (text, numeric) in enumerate(steps):
        try:
            if numeric:  # An array element or a collection item by position.
                holder = _item(holder, int(text[1:-1]), bpy_prop_collection, bpy_struct)
            elif text.startswith("["):
                if not isinstance(holder, bpy_prop_collection):
                    # A custom property: stored, with no getter, while only stored keys follow it.
                    return None if _stored_keys_only(steps[position + 1:]) else STORED_THROUGH
                holder = _item(holder, ast.literal_eval(text[1:-1]), bpy_prop_collection, bpy_struct)
            else:
                name = text.lstrip(".")
                prop = holder.bl_rna.properties.get(name)
                if prop is None or getattr(prop, "is_runtime", True):
                    return python
                holder = getattr(holder, name)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, SyntaxError, ReferenceError):
            return python  # Unresolvable: not proven native.
    return None


def _stored_keys_only(rest):
    """Whether only stored keys (``["key"]``, ``[n]``) follow a stored custom property: stored data all the way, with
    no getter. Anything else (an RNA step, through an ID or struct a group holds) is not proven by this walk."""
    return all(text.startswith("[") for text, _numeric in rest)


def describe(curve, label, owner=None):
    """The revision's account of one driver: its channel, type and every variable's targets (``label`` names)."""
    driver = curve.driver
    variables = []
    for variable in driver.variables:
        targets = []
        for target in variable.targets:
            entry = {field: str(getattr(target, field, "")) for field in _TARGET_FIELDS}
            entry["id"] = label(target.id.name) if target.id is not None else None
            targets.append(entry)
        variables.append({"name": label(variable.name), "type": variable.type, "targets": targets})
    return {"channel": label(curve.data_path, 256), "index": curve.array_index, "type": driver.type,
            "mute": bool(curve.mute), "valid": bool(curve.is_valid), "use_self": bool(getattr(driver, "use_self", False)),
            "variables": variables, "native": problem(curve, owner) is None}


def _shown(context, block):
    """The name the person knows ``block`` by: a staged object copy's live (or renamed) name, looked up by its handle
    (``program_names.current_name``); a staged ID copy's public name (``stage_names.public``); else its own."""
    extras = getattr(context, "extras", None) or {}
    for handle, staged in (getattr(context, "objects", None) or {}).items():
        if _same(staged, block):
            from .program_names import current_name
            return str(current_name(extras, handle, staged))
    from .stage_names import public
    return str(public(extras, str(block.name)))
