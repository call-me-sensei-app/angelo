# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed object-channel transfer shared by stage cloning and live journals.

No arbitrary RNA fields or evaluated matrices are copied. Parent remapping must
preserve these local channels and the parent inverse before a graph update;
Blender's parent setter can otherwise erase the artist's inverse transform.
"""

TRANSFORM_FIELDS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "rotation_mode", "scale",
                    "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")


def transform_values(obj):
    return {name: getattr(obj, name) if isinstance(getattr(obj, name), str) else tuple(getattr(obj, name))
            for name in TRANSFORM_FIELDS}


def set_transforms(obj, values):
    # A representation change can convert stored rotations. Select mode FIRST.
    obj.rotation_mode = values["rotation_mode"]
    for name, value in values.items():
        if name != "rotation_mode":
            setattr(obj, name, value)


def repoint(holder, prop, live, staged):
    """Point a committed object's ID property at the live object its staged target stood for.

    Assigning ``Object.parent`` clears the parent inverse, so a parent that is
    already the live object is left alone and any other is remapped in place.
    Only an Object is remapped as one. Of the other structs with an object
    ``parent``, a particle system's holds no inverse and is assigned; a Grease
    Pencil layer's is refused before any write (``layer_parent_swaps``), so a
    layer reaching this point is refused, never treated as an Object.
    """
    import bpy
    if prop.identifier != "parent" or not hasattr(holder, "matrix_parent_inverse"):
        setattr(holder, prop.identifier, live)
    elif live == staged:
        return
    elif isinstance(holder, bpy.types.Object):
        remap_parent(holder, live)
    else:
        from .program_jobs import ProgramError
        raise ProgramError(LAYER_PARENT_REFUSAL, code="UNSUPPORTED")


LAYER_PARENT_REFUSAL = ("This Grease Pencil object has a layer parented to an object. An edit that replaces its "
                        "data (a stroke operator, or a data setting such as property_set on data.*) would have to "
                        "set that layer's parent again, and Blender then recomputes the layer's parent inverse from "
                        "where the parent is now, which moves the strokes. Use reviewed Python for this edit; moves, "
                        "modifier settings and other object-level edits work")


def layer_parent_swaps(stage, swaps):
    """Refuse a program whose commit would make staged Grease Pencil data with a parented layer live.

    A data swap (``swaps``: commit's data-swap resources) makes the staged data
    live and must then point each layer at its live parent. Blender's only way
    to set a layer's parent recomputes the layer's read-only inverse (the
    artist's, set when the layer was parented) from the parent's current world
    matrix, and leaks the bone name it replaces: no Python route keeps the
    inverse. Reads only; stage validation runs it at every hydration and
    before commit's first write.
    """
    import bpy
    from .program_jobs import ProgramError
    for handle, staged in stage.objects.items():
        data = staged.data
        if (stage.changes.get(handle, set()) & swaps and isinstance(data, bpy.types.GreasePencil)
                and any(layer.parent is not None for layer in data.layers)):
            raise ProgramError(LAYER_PARENT_REFUSAL, code="UNSUPPORTED", details={"handles": [handle]})


def remap_parent(obj, parent):
    """Change only the parent ID identity, not its inverse/local transform state."""
    channels = transform_values(obj)
    parent_type, parent_bone = obj.parent_type, obj.parent_bone
    inverse = obj.matrix_parent_inverse.copy()
    obj.parent = parent
    obj.parent_type, obj.parent_bone = parent_type, parent_bone
    set_transforms(obj, channels)
    obj.matrix_parent_inverse = inverse
