# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``batch`` kernel: copies of a staged unit at the transforms ``batch_spec`` computes.

The unit is one to eight staged objects. Parts whose parent is another part
keep their place relative to it; the others (roots) must share one parent, or
none. Placement is in world space, like the locations a program gives the
objects it creates: an instance is the unit moved to a position, turned and
scaled about the anchor (the first source's origin), whatever the unit is
parented to. ``mirror`` reflects across the plane through the origin of the
roots' parent, along that parent's own axis (the world's when there is no
parent), so parts attached to an object are mirrored to its other side. A
mirrored part has its place reflected and its rotation conjugated and keeps
its scale: it is the exact mirror image of a part that is symmetric about its
own axes, and never has a negative scale.

World matrices come from the stored transforms of the parent chain; nothing
is evaluated here. A parent that is constrained or parented to a bone or to
vertices has no such matrix and is refused, and so is a turn, scale or mirror
that would shear a part under a non-uniformly scaled parent that was not
parented with keep_transform.

Each copy is made as ``object_duplicate`` makes one (the object, its
modifiers, constraints and parent; animated sources are refused), joins its
source's collections and gets an alias and the next free name of its source's
numbering. A linked copy shares its source's data and so its materials: the
staged data itself for a source created in the program, the live data at
commit for a declared one. A full copy owns a copy of the data and shares
the materials.

Work is linear in the copies, and bounded before any is made: the count
(``batch_spec``), the names (all free), the combined evaluated size (what 32
single operations could stage) and, by the stage itself, its ID and vertex
budgets.
"""

import math

import bpy
from mathutils import Euler, Matrix, Vector

from . import batch_spec
from .action_context import _readback
from .action_object_management import DUPLICABLE, _extras, _membership
from .program_names import current_name, occupied, rename_new_object
from .action_schema_types import MAX_EVALUATED_VERTICES, MAX_OPERATIONS
from .program_fields import remap_parent
from .refusal_codes import RefusalError  # Every refusal here is correctable, as object_duplicate's.

SERIAL = "batches"  # stage.extras: batches run so far (the ordinal copy aliases of declared sources carry).
COPIES = "batch_copies"  # stage.extras: objects the program's batches created.
_AXES = {"X": 0, "Y": 1, "Z": 2}


def _unit(context, params):
    """The staged sources, each part's parent index inside the unit (or None) and the roots' shared parent."""
    sources = [context.resolve(reference) for reference in params["sources"]]
    for obj in sources:
        if obj.type not in DUPLICABLE:
            raise RefusalError("batch copies mesh, curve, text, empty, camera and light objects", code="VALIDATION")
        animation = obj.animation_data
        if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
            raise RefusalError("batch ANIMATED_SOURCE: copying animation is reviewed Python", code="UNSUPPORTED")
    parents = [next((index for index, other in enumerate(sources) if obj.parent == other), None) for obj in sources]
    outside = {obj.parent for obj, inner in zip(sources, parents) if inner is None}
    if len(outside) > 1 or parents[0] is not None:
        raise RefusalError("batch UNIT_PARENTS: the first source is a root of the unit, and every root shares one "
                           "parent (or none)", code="VALIDATION")
    return sources, parents


def _world(obj):
    """An object's world matrix from its parent chain's stored transforms, without evaluating anything."""
    chain = []
    while obj is not None:
        if len(obj.constraints) or (obj.parent is not None and obj.parent_type != "OBJECT"):
            raise RefusalError("batch PARENT_SPACE: the unit's parent chain is constrained or parented to a bone or "
                               "to vertices, so its place is not known without evaluating the scene",
                               code="UNSUPPORTED")
        chain.append(obj)
        obj = obj.parent
    matrix = Matrix.Identity(4)
    for item in reversed(chain):
        matrix = matrix @ (item.matrix_parent_inverse @ item.matrix_basis if item.parent is not None
                           else item.matrix_basis)
    return matrix


def _unit_matrices(sources, parents):
    """Each part's world matrix, and the frame its stored transform is relative to (identity for a free root)."""
    outside = sources[0].parent
    above = _world(outside) if outside is not None else Matrix.Identity(4)
    world, frames = [None] * len(sources), [None] * len(sources)

    def matrix(index):
        if world[index] is None:
            obj = sources[index]
            if obj.parent is None:
                frames[index] = Matrix.Identity(4)
            else:
                frames[index] = (above if parents[index] is None else matrix(parents[index])) @ obj.matrix_parent_inverse
            world[index] = frames[index] @ obj.matrix_basis
        return world[index]

    return [matrix(index) for index in range(len(sources))], frames, above


def _instance_matrix(entry, anchor, parts):
    """(the world transform of one instance, a per-axis scale of a single part or None)."""
    rotation, scale = entry["rotation"], entry["scale"]
    own = None
    if scale is not None and len(set(scale)) > 1:
        if parts > 1:
            raise RefusalError("batch UNIFORM_SCALE: a unit of several parts takes one scale factor (equal components)",
                               code="VALIDATION")
        own, scale = Vector(scale), None  # One part: scaled along its own axes, never sheared.
    move = Vector(entry["position"]) - anchor if "position" in entry else Vector(entry["offset"])
    transform = Matrix.Translation(anchor + move)
    if rotation is not None:
        transform = transform @ Euler(rotation, "XYZ").to_matrix().to_4x4()
    if scale is not None:
        transform = transform @ Matrix.Scale(scale[0], 4)
    return transform @ Matrix.Translation(-anchor), own


def _reflection(axis, above):
    """(the world reflection across the parent's plane, the same axis in a part's own space)."""
    local = Matrix.Identity(4)
    local[_AXES[axis]][_AXES[axis]] = -1.0
    location, rotation, _ = above.decompose()
    rigid = Matrix.Translation(location) @ rotation.to_matrix().to_4x4()
    return rigid @ local @ rigid.inverted(), local


def _place(obj, basis):
    """Give ``obj`` a stored transform, as XYZ Euler like every typed transform; refuse one it cannot hold."""
    location, rotation, scale = basis.decompose()
    if any(not math.isfinite(value) or abs(value) > 100000 for value in location) or any(
            not math.isfinite(value) or abs(value) > 1000 or abs(value) < .000001 for value in scale):
        raise RefusalError("batch: an instance's transform exceeds finite bounds", code="VALIDATION")
    held = Matrix.LocRotScale(location, rotation, scale)
    if max(abs(held[row][column] - basis[row][column]) for row in range(3) for column in range(3)) > 1e-4 * max(
            1.0, max(abs(value) for value in scale)):
        raise RefusalError("batch SHEARED: turning, scaling or mirroring this part under its non-uniformly scaled "
                           "parent would shear it; parent it with keep_transform, or apply the parent's scale first",
                           code="UNSUPPORTED")
    obj.rotation_mode = "XYZ"
    obj.location, obj.rotation_euler, obj.scale = location, rotation.to_euler("XYZ"), scale


def _simple(entry):
    return entry["rotation"] is None and entry["scale"] is None and not entry["mirror"]


def _copy(context, source, linked):
    """A copy of a staged object: on its source's staged data when linked, else on a copy of it."""
    copy = context.track_id(source.copy())
    if source.data is not None and not linked:
        data = context.track_id(source.data.copy())
        if getattr(data, "shape_keys", None) is not None:
            context.track_id(data.shape_keys)
        copy.data = data
    return copy


def _logical_name(context, reference, obj):
    """The name commit gives a source: a rename staged in this program, its live name, or a new object's own."""
    return current_name(context.extras, reference, obj)


def _evaluated(context, sources):
    from .action_budget import assert_evaluation_budget
    return sum(assert_evaluation_budget(context, obj) or 0 for obj in sources)


def apply(context, operation):
    """batch: every instance of the unit after the first, as copies; the first is the unit itself."""
    params = operation["parameters"]
    sources, parents = _unit(context, params)
    plan = batch_spec.instances(params)
    total, parts = len(plan), len(sources)
    created = (total - 1) * parts
    made = context.extras.get(COPIES, 0) + created
    if made > batch_spec.MAX_PROGRAM_COPIES:
        raise RefusalError("batch PROGRAM_TOO_LARGE: this program's batches would create %d objects (limit %d)"
                           % (made, batch_spec.MAX_PROGRAM_COPIES), code="LIMIT_EXCEEDED")
    if _evaluated(context, sources) * (total - 1) > MAX_OPERATIONS * MAX_EVALUATED_VERTICES:
        raise RefusalError("batch EVALUATED_TOO_LARGE: the copies' modifier stacks together exceed what a program "
                           "may stage; copy a lighter unit or fewer instances", code="LIMIT_EXCEEDED")
    serial = context.extras.get(SERIAL, 0) + 1
    logical = [_logical_name(context, reference, obj) for reference, obj in zip(params["sources"], sources)]
    numbering = batch_spec.unit_names(logical, total, lambda name: occupied(context.extras, name, bpy.data.objects.get))
    names, aliases = [wanted for wanted, _ in numbering], []
    for part, reference in enumerate(params["sources"]):
        # Taken in program order (program_names): held, or given by a pending rename, and not released earlier.
        taken = next((name for name in names[part]
                      if len(name) > 63 or occupied(context.extras, name, bpy.data.objects.get)), None)
        if taken is not None:
            raise RefusalError("batch NAME_TAKEN: a copy would be named %r, which is taken or too long; rename the "
                               "source (a name ending in a number is continued)" % taken[:64], code="VALIDATION")
        aliases.append(batch_spec.copy_aliases(reference, serial, part, total))
    if any(alias in context.aliases for row in aliases for alias in row):
        raise RefusalError("batch ALIAS_TAKEN: a copy's alias is already defined in this program", code="VALIDATION")
    matrices, frames, above = _unit_matrices(sources, parents)
    anchor, origins = matrices[0].to_translation(), [obj.location.copy() for obj in sources]
    # A world move as a change of each root's stored location (its frame may turn or scale it).
    inverse = [frame.inverted().to_3x3() for frame in frames]
    reflection, own_axis = _reflection(params["mirror"], above) if params["mirror"] is not None else (None, None)
    writes, low, high = {}, list(anchor), list(anchor)
    sources_map = _extras(context, "duplicate_sources", {})
    linked_map, memberships = _extras(context, "linked_duplicates", {}), _extras(context, "memberships", {})
    members = [list(_membership(context, reference)) for reference in params["sources"]]
    for index, entry in enumerate(plan):
        if index:
            placed = [_copy(context, obj, params["linked"]) for obj in sources]
            for part, copy in enumerate(placed):
                wanted = names[part][index - 1]
                if rename_new_object(context, copy, wanted, aliases[part][index - 1], bpy.data.objects.get,
                                     requested=False) != wanted:
                    raise RefusalError("batch NAME_TAKEN: another object is named " + repr(wanted[:64]),
                                       code="VALIDATION")
                if parents[part] is not None:
                    remap_parent(copy, placed[parents[part]])  # Inside the unit: the copy of its parent.
        else:
            placed = sources
        if _simple(entry):
            move = Vector(entry["position"]) - anchor if "position" in entry else Vector(entry["offset"])
            if move.length_squared:
                for part, obj in enumerate(placed):
                    if parents[part] is None:  # Roots move; the parts inside the unit follow their parents.
                        obj.location = origins[part] + inverse[part] @ move
            position = anchor + move
        else:
            transform, own = _instance_matrix(entry, anchor, parts)
            world = [transform @ matrix for matrix in matrices]
            if own is not None:
                world[0] = world[0] @ Matrix.Diagonal(own).to_4x4()
            if entry["mirror"]:
                world = [reflection @ matrix @ own_axis for matrix in world]
            for part, obj in enumerate(placed):
                frame = frames[part] if parents[part] is None else world[parents[part]] @ sources[part].matrix_parent_inverse
                _place(obj, frame.inverted() @ world[part])
            position = world[0].to_translation()
        low = [min(a, b) for a, b in zip(low, position)]
        high = [max(a, b) for a, b in zip(high, position)]
        if index:
            for part, copy in enumerate(placed):
                reference, alias = params["sources"][part], aliases[part][index - 1]
                context.add_copy(copy, alias)
                sources_map[alias] = reference
                if params["linked"]:
                    linked_map[alias] = reference  # Commit points it at its link root's live data.
                memberships[alias] = list(members[part])
        elif not _simple(entry) or "position" in entry:
            writes.update({reference: ["object", "transforms"] for reference in params["sources"]
                           if not reference.startswith("$")})
    context.extras[SERIAL], context.extras[COPIES] = serial, made
    result = {"kind": "batch", "target": None, "instances": total, "created": created, "linked": params["linked"],
              "parts": [dict({"source": logical[part][:64], "copies": total - 1, "first": names[part][0],
                               "last": names[part][-1], "first_alias": aliases[part][0]},
                              **({"stride": numbering[part][1]} if numbering[part][1] > 1 else {}))
                        for part in range(parts)],
              "bounds": [[round(value, 6) for value in low], [round(value, 6) for value in high]]}
    if writes:
        result["writes"] = writes  # program_objects.apply: declared sources this batch moved.
    return result
