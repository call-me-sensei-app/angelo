# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``material_assign`` and the rule that one program states a material once.

``material_assign`` gives its target a material that already exists, found by
its exact name on staged objects only: on ``source`` (a declared object or an
earlier creation), else on the target itself, among the materials this program
created, then on any other staged object. Nothing is looked up by name in the
file, so the material's state is always part of a declared object's revision.
The target's slot receives the staged copy itself: commit swaps an unedited
copy back for the live material it stands for (``program_identities``), so
both objects use one datablock. ``copy_as`` assigns an independent copy under
a new name instead. A fresh material isolated earlier in this request also
resolves by its original chosen name, through the host's private identity
proof; the isolated datablock keeps its own actual name.

A material kind (``material_principled``, ``shader_nodes``) that names a
material an earlier operation of the same program stated assigns that
material when its values are the same and is refused when they differ
(``MATERIAL_REDEFINED``): repeating a definition never makes a second
datablock with a suffixed name.
"""

import bpy

from .refusal_codes import RefusalError

from .action_context import _mesh, _face_selection, _readback
from .material_rules import agrees, definition as _definition, names_only, not_in_program, redefined

# stage.extras: material name -> {"definition": canonical JSON, "id": the staged datablock's name}.
DEFINED = "program_materials"
# ID property on a copy ``copy_as`` made: commit resolves the staging leftovers of its tree.
COPY = "_angelo_material_copy"
def staged_name(material):
    """The name commit gives a staged material: its live original's for a copy of one, else its own."""
    from .program_verification import _committed_material_name
    return _committed_material_name(material, True)


def result_name(context, material):
    """The name a result gives a staged material: the name commit gives it, an isolated copy's own included."""
    from .program_verification import kept_name
    return kept_name(context.extras, material)


def remember(context, operation, material, held=False):
    """Record what a material kind stated, so a later operation naming it reuses the datablock: for an edit in place
    (``held``) merged into what this program stated of it before, else what a material held before it had."""
    defined_ = context.extras.setdefault(DEFINED, {})
    earlier = defined_.get(operation["parameters"]["name"], {}).get("definition") if held else None
    defined_[operation["parameters"]["name"]] = {"definition": _definition(operation, earlier, held), "id": material.name}


def defined(context, operation):
    """The staged material an earlier operation stated under this name, None, or a refusal when the values differ."""
    name = operation["parameters"]["name"]
    entry = context.extras.get(DEFINED, {}).get(name)
    if entry is None:
        return None
    material = bpy.data.materials.get(entry["id"])
    if material is None or material not in context.owned_ids:
        return None
    if not agrees(entry["definition"], operation):
        if names_only(operation):  # Only its name: the material as this program stated it.
            return material
        raise redefined(operation)  # program_rules refuses it before approval; this is the stage's own check.
    return material


def name_taken(kind, name):
    return RefusalError("%s NAME_TAKEN: a material named %r already exists in the file; choose another name, or give "
                        "this object the existing one with material_assign (source: an object that has it)"
                        % (kind, name[:64]), code="VALIDATION")


def _lookup_name(context, target, material):
    from .program_material_facts import logical_material_name
    return logical_material_name(context, target, material) or staged_name(material)


def _named(context, target, obj, name):
    return next((slot.material for slot in obj.material_slots
                 if slot.material is not None
                 and _lookup_name(context, target, slot.material) == name), None)


def _find(context, obj, params, target=None):
    name = params["material"]
    if params["source"] is not None:
        source = context.resolve(params["source"])
        material = _named(context, params["source"], source, name)
        if material is None:
            names = sorted({_lookup_name(context, params["source"], slot.material)[:64]
                            for slot in source.material_slots if slot.material})
            raise RefusalError("material_assign MATERIAL_NOT_FOUND: the source has no material named %r; its "
                               "materials: %s" % (name[:64], ", ".join(repr(item) for item in names[:8]) or "none"),
                               code="VALIDATION")
        return material
    material = _named(context, target, obj, name)
    if material is None:
        entry = context.extras.get(DEFINED, {}).get(name)
        material = bpy.data.materials.get(entry["id"]) if entry else None
    if material is None:
        staged = list(context.objects.items()) + list(context.aliases.items())
        material = next((found for found in (_named(context, reference, other, name) for reference, other in staged)
                         if found is not None), None)
    if material is None:
        raise not_in_program(name)
    return material


def _copy(context, material, name):
    """An independent copy for ``copy_as``: a new datablock under ``name``, its nested groups resolved at commit."""
    from .node_edit_spec import EDITED, MODE, SOURCE
    animation = material.animation_data
    if animation is not None and (animation.action is not None or len(animation.drivers)):
        raise RefusalError("material_assign ANIMATED_MATERIAL: copying an animated material is reviewed Python",
                           code="UNSUPPORTED")
    private = context.track_id(material.copy())
    if private.node_tree is not None:
        context.owned_ids.add(private.node_tree)  # Embedded: removed with its material.
    for key in (SOURCE, EDITED, MODE):
        if key in private.keys():
            del private[key]
    private.name = name
    if private.name != name:
        raise name_taken("material_assign", name)
    private[COPY] = 1
    from .program_material_facts import record_copy
    record_copy(context, material, private, name)
    return private


def place(obj, mesh, material, slot, selected):
    """Put ``material`` in ``slot`` (the next free one when it equals the slot count) and on the selected faces."""
    if slot > len(mesh.materials) or slot >= 32:
        raise RefusalError("Material slot must be existing or the next append slot, with at most 32 slots",
                           code="VALIDATION")
    if slot == len(mesh.materials):
        mesh.materials.append(material)
    else:
        mesh.materials[slot] = material
    # OBJECT-linked slots override mesh.materials. Preserve the link mode and
    # assign the effective slot too; otherwise faces still render the old ID.
    obj.material_slots[slot].material = material
    for polygon in selected:
        polygon.material_index = slot
    if obj.material_slots[slot].material != material:
        raise RefusalError("Native material assignment did not update the effective object slot", code="INTERNAL_ERROR")


def assign(context, obj, operation):
    """material_assign: an existing material (or a copy of it) on the target's slot and faces."""
    from . import material_sharing
    mesh = _mesh(context, obj, slots=True)
    params = operation["parameters"]
    material = _find(context, obj, params, operation["target"])
    if material not in context.owned_ids:
        raise RefusalError("material_assign NOT_STAGED: the material is not a private staged copy", code="INTERNAL_ERROR")
    held = [index for index, slot in enumerate(obj.material_slots) if slot.material == material]
    if params["copy_as"] is not None:
        material, held = _copy(context, material, params["copy_as"]), []
    else:
        material_sharing.note_assignment(context, material)
    selected = _face_selection(mesh, params)
    if params["slot"] == -1:
        slot = held[0] if held else len(mesh.materials)
    else:
        slot = params["slot"]
    place(obj, mesh, material, slot, selected)
    return _readback(obj, operation, material=result_name(context, material), slot=slot,
                     slot_link=obj.material_slots[slot].link, assigned_faces=len(selected),
                     copied=params["copy_as"] is not None, **also(context, operation, material))


def also(context, operation, material, editing=False):
    """Give the operation's material to its ``also`` objects, so each renders it.

    An object that holds that live material and whose base faces already use it keeps its faces (its other materials
    too); any other gets every face on the slot that holds it, else on a new last slot, as the target does when no
    face field is given (face fields name the target's faces). Held means the same live material
    (``material_faces.live_identity``): staging gives each object its own private copy of a live material. Where
    evaluation decides which slots render (``material_faces.faces_decide``), the result says checks read its slots.

    Returns result fields: the objects' names, and ``writes`` for the declared
    ones (program_objects.apply records what commit transfers).
    """
    from . import material_sharing
    from .action_object_management import _live_name
    from .material_faces import all_faces_to, faces_decide, give_faces, slots_holding
    references = operation["parameters"]["also"]
    if not references:
        return {}
    if editing:
        raise RefusalError("%s ALSO_NEW_MATERIAL: also assigns a material this operation creates; material_assign "
                           "gives an existing material to more objects" % operation["kind"], code="VALIDATION")
    names, writes, slots_only = [], {}, []
    for reference in references:
        other = context.resolve(reference)
        mesh = _mesh(context, other, slots=True)
        held = slots_holding(other, material)
        # The held slot takes the operation's own datablock (no face moves): the object's private copy and it are one
        # live material, and commit proves the assignment by that datablock (finish_operation).
        place(other, mesh, material, held[0] if held else len(mesh.materials), [])
        if held:
            give_faces(other, mesh, held)
        else:   # A new slot: every face (a stale index past the old last slot would read as this one).
            all_faces_to(mesh, len(mesh.materials) - 1)
        if not faces_decide(other):
            slots_only.append(other.name[:64])
        names.append((context.extras.get("renames", {}).get(reference) or _live_name(context, reference) or other.name)[:64])
        if not reference.startswith("$"):
            writes[reference] = ["object", "mesh", "materials"]
    material_sharing.note_assignment(context, material)
    result = dict({"also": names}, **({"writes": writes} if writes else {}))
    if slots_only:
        result["also_slots_only"] = {"objects": slots_only, "note": "These have no faces, or particles, a modifier "
                                     "or Edit Mode decide which slots they render, so their checks read only their "
                                     "slots."}
    return result


def finish_copies(stage, *, record_transfer=None):
    """At commit: a copied material's nested group clones go back to their live groups (``node_edit_commit.finish``)."""
    from .node_edit_commit import _owned, finish
    owned = None
    for block in list(stage.owned_ids) + list(stage.context.created_ids):
        if isinstance(block, bpy.types.Material) and COPY in block.keys():
            owned = _owned(stage) if owned is None else owned
            del block[COPY]
            if block.node_tree is not None:
                finish(block.node_tree, stage, owned, record_transfer=record_transfer)
