# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Program refusals that read more than one operation (pure Python, no bpy); ``validate_program`` runs them.

* Linked data: a linked duplicate, or a batch's linked copies, share their
  source's data, and commit keeps that relation live (``program_transfers``).
  A later operation that writes the data of any object in such a group would
  change all of them (or break the link), so it is refused before anything is
  staged (``LINKED_EDIT``); an edit before the duplicate is copied with it.
  Giving every object of the group the same material is the one later data
  write that is all of theirs: commit points each copy at its link root's data,
  so a copy shows what the root's data holds, and that equals what the program
  gave the copy only when both end with the same slots and faces. It is
  admitted when every member gets the same material on every face from a state
  without material slots: each member is an object this program made without
  slots (``create_primitive``, ``create_object``, or a copy of one) that no
  earlier operation gave any, the material comes from one material kind with
  no face selection, slot 0 or the default (or several naming the same
  material: a ``copy_as`` copy, and a statement without a name, are each a new
  one), and every member is the target or an ``also`` object of one of them.
  GPT-6.1 Sol and Opus wrote that shape and lost a round to the refusal in four
  recorded trials. A copy whose root got the material only in an ``also`` slot
  would keep the old one on its faces, so a member with slots before the write is
  refused. Which datablock a name reaches is staging's to decide (``source``,
  an isolated copy, a library material), and commit refuses a copy whose staged
  materials or face indices are not its root's
  (``commit_fidelity.linked_layouts``).
* A material restated with other values: staging edits a material its target
  holds in place, and refuses restating it on a target that does not hold it
  (``action_materials.defined``). Where the program knows what an object holds
  (one it made, given slots only by material steps), that refusal comes before
  approval (``MATERIAL_REDEFINED``).
* The active material: a program that writes ``active_material.`` settings of
  an object and also changes its material slots or its active slot
  (``active_material_index``, a slot operator, a material assignment after the
  write, or an existing material assigned before it) would name, or lose the
  write to, a material it did not bind (``ACTIVE_SLOT_CONFLICT``). A
  ``material_assign`` with ``copy_as`` before the write is the advised route to
  a local copy and stays allowed.

Without these rules, linked duplicates edited later were committed unlinked;
slot hops were refused only at staging, naming a staged private copy; and a
material write before a slot removal was listed as a change and lost.
"""

from .refusal_codes import RefusalError
from .rna_rules import PropertyPathError

LINKED_EDIT = ("object_duplicate LINKED_EDIT: %s shares its data with a linked duplicate or linked copies made "
               "earlier in this program, so writing that data would change all of them; edit before duplicating, "
               "give every one of them the same material, or duplicate with linked false")
UNNAMED = ("; a material_principled or shader_nodes without a name makes a new material each time, so name it, or "
           "state it once with also listing the others")
SLOT_CONFLICT = ("this program also changes the object's material slots or active slot, so its active_material "
                 "settings would name, or be lost with, a material it did not bind; set the material's settings in a "
                 "separate program (a material_assign with copy_as into the active slot before them is allowed)")
# Material slot operators that keep the active material: selection, face assignment, and moving the active slot
# (its material moves with it).
_SLOT_KEEPERS = frozenset({"object.material_slot_select", "object.material_slot_deselect", "object.material_slot_assign",
                           "object.material_slot_move"})
_MATERIAL_KINDS = frozenset({"material_assign", "material_principled", "shader_nodes"})
_DATA_FAMILIES = frozenset({"mesh", "armature", "camera", "light", "curve", "data"})  # program_commit.DATA_SWAP_RESOURCES
# Kinds that make an object without material slots, and other kinds than the material ones that can give it some.
_SLOTLESS_MAKERS = frozenset({"create_primitive", "create_object"})
_SLOT_MAKERS = frozenset({"operator_run", "property_set", "object_join", "mesh_separate", "object_convert", "node_edit"})


def check(operations):
    """Raise for the first cross-operation refusal of canonical ``operations``."""
    linked_edits(operations)
    slot_conflict(operations)


def linked_edits(operations):
    from .action_catalog import resource_dependencies
    from .batch_spec import copy_aliases, instance_count
    from .material_rules import STATING_KINDS, agrees, definition, names_only, redefined
    from .node_edit_commit import edits_in_place
    group, batches = {}, 0

    def root(reference):
        while group.get(reference, reference) != reference:
            reference = group[reference]
        return reference

    shared, slotless, stated = set(), set(), {}
    held = {}  # Program object -> names of the materials this program gave it, while nothing else gave it slots.
    copies = {}  # A copy_as name -> the operation's own material (_whole_material).
    materials = {}  # Link root -> [material, members given it, members when it was given].

    def copied(source, alias):
        if source in slotless:
            slotless.add(alias)
        if source in held:
            held[alias] = set(held[source])

    for index, operation in enumerate(operations):
        kind, params = operation["kind"], operation["parameters"]
        if kind in _SLOTLESS_MAKERS and params.get("alias"):
            slotless.add("$" + params["alias"].lstrip("$"))
            held["$" + params["alias"].lstrip("$")] = set()
        if kind == "object_duplicate":
            alias = "$" + params["alias"].lstrip("$")
            copied(params["source"], alias)
            if params["linked"]:
                group[root(alias)] = root(params["source"])
                shared.update({alias, params["source"]})
            continue
        if kind == "batch":
            batches += 1
            for part, source in enumerate(params["sources"]):
                for alias in copy_aliases(source, batches, part, instance_count(params)):
                    copied(source, alias)
                    if params["linked"]:
                        group[root(alias)] = root(source)
                        shared.update({alias, source})
            continue
        touched = ([operation["target"]] + (list(params.get("sources", ())) if kind == "object_join" else [])
                   + list(params.get("also") or ()))  # A material step's also objects get their slots edited too.
        hit = [reference for reference in touched if reference is not None and reference in shared]
        if kind in STATING_KINDS and edits_in_place(operation) and not names_only(operation):
            earlier = stated.get(params["name"])
            if params["name"] in held.get(operation["target"], (params["name"],)):  # Edited in place, or new.
                # A live target may or may not hold it: recorded as new (Blender's defaults), the looser reading, so
                # this early gate never refuses what the bound obligations, which know, accept.
                stated[params["name"]] = definition(operation, earlier)
            elif earlier is not None and not agrees(earlier, operation):
                # On a program object the program gave no such material it would restate one, which staging refuses
                # (action_materials.defined): refused before approval instead, by the same rule (material_rules).
                raise redefined(operation)
            elif earlier is None:
                stated[params["name"]] = definition(operation)
        if hit and (kind == "object_join" or set(resource_dependencies(operation)["mutates"]) & _DATA_FAMILIES):
            material = _whole_material(operation, index, copies)
            for reference in hit:
                given = materials.setdefault(root(reference), [material, set(), set()])
                if (material is None or given[0] != material
                        or reference not in slotless and reference not in given[1]):
                    _refuse_linked_edit(reference, unnamed=_unnamed(material) or _unnamed(given[0]))
                given[1].add(reference)
                given[2].update(member for member in shared if root(member) == root(reference))
        if kind in _MATERIAL_KINDS:
            name = params.get("copy_as") or params.get("material") or params.get("name")
            if params.get("copy_as"):
                copies[params["copy_as"]] = ("copy", index)
            for reference in touched:
                if reference in held:
                    held[reference].add(name)
        elif kind in _SLOT_MAKERS:
            for reference in touched:
                held.pop(reference, None)  # Its slots are no longer only what this program's material steps gave.
        if kind in _MATERIAL_KINDS or kind in _SLOT_MAKERS:
            slotless.difference_update(touched)
    for material, given, members in materials.values():
        if members - given:  # A copy the material would reach only through the shared data.
            _refuse_linked_edit(sorted(members - given)[0])


def _whole_material(operation, index, copies):
    """The material a material kind gives every face of its target and of slotless ``also`` objects, else None.

    By name, as the material rules bind it, except where staging makes a new
    datablock: a ``copy_as`` copy is this operation's own (and a later
    reference to its name is that copy), and so is a statement under the
    kind's default name, which never edits or reuses an earlier one
    (``node_edit_commit.edits_in_place``). Which datablock a name reaches
    through ``source`` is decided at staging; commit refuses a copy that ends
    on another than its root (``commit_fidelity.linked_layouts``).
    """
    from .node_edit_commit import MATERIAL_KINDS, edits_in_place
    params = operation["parameters"]
    if (operation["kind"] not in _MATERIAL_KINDS or params.get("face_indices") or params.get("face_selector") is not None
            or params.get("slot", -1) not in (-1, 0)):
        return None
    if params.get("copy_as"):
        return ("copy", index)
    if operation["kind"] in MATERIAL_KINDS and not edits_in_place(operation):
        return ("unnamed", index)
    name = params.get("material") or params.get("name")
    return copies.get(name, name)


def _unnamed(material):
    return isinstance(material, tuple) and material[0] == "unnamed"


def _refuse_linked_edit(reference, unnamed=False):
    # Never a raw handle: the model knows an alias, and a declared object as the duplicated one.
    raise RefusalError(LINKED_EDIT % (repr(reference[:64]) if reference.startswith("$") else "a duplicated object")
                       + (UNNAMED if unnamed else ""), code="VALIDATION")


def slot_conflict(operations):
    from .action_properties import family
    first = {}  # Object -> index of its first active_material write.
    for index, operation in enumerate(operations):
        if operation["kind"] in ("property_set", "property_keyframe") and any(
                family(path) == "material" for path in operation["parameters"]["values"]):
            first.setdefault(operation["target"], index)
    for index, operation in enumerate(operations):
        kind, params = operation["kind"], operation["parameters"]
        for target in [operation["target"]] + list(params.get("also") or ()):
            if target not in first:
                continue
            hop = ((kind in ("property_set", "property_keyframe") and "active_material_index" in params["values"])
                   or (kind == "operator_run" and params["operator"].startswith("object.material_slot_")
                       and params["operator"] not in _SLOT_KEEPERS)
                   or (kind in _MATERIAL_KINDS and (index > first[target]
                                                    or (kind == "material_assign" and not params.get("copy_as")))))
            if hop:
                raise PropertyPathError("ACTIVE_SLOT_CONFLICT", "active_material_index", SLOT_CONFLICT)
