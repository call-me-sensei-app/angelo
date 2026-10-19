# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Committed IDs keep their live identities and names.

Staging copies every written object's data, Actions, materials and node groups
(materials a program cannot write stay live: stage_borrowing), so without these
steps each commit would leave ``.001`` duplicates behind:
unmodified material and node-group clones are swapped back for the live IDs
they copy, a replacing copy adopts the name of the ID it replaces when nothing
else uses that one, and image-texture nodes and World environments receive the
live images staging named by ID only. Called by ``ProgramCommitMixin.commit``
inside the Undo operator; renames are journaled for rollback.
"""

from .program_jobs import ProgramError
from .refusal_codes import refusal_code, refusal_details
from .program_dependencies import _bpy


def _assign_images(stage, *, record_transfer=None):
    """Give new materials' image-texture nodes the loaded images staging named by ID only."""
    from .action_node_graphs import IMAGE_MARKER
    from .action_world import resolve_image
    from .action_materials import finish_copies
    # A material_assign copy restores nested group identities under the same
    # observer as explicit node edits; native finalization needs both mappings.
    finish_copies(stage, record_transfer=record_transfer)
    for block in stage.owned_ids + list(stage.context.created_ids):
        tree = getattr(block, "node_tree", None) if isinstance(block, _bpy().types.Material) else None
        for node in (tree.nodes if tree is not None else ()):
            if IMAGE_MARKER in node.keys():
                try:
                    image = resolve_image(node[IMAGE_MARKER])
                except ValueError as error:
                    raise ProgramError("The texture image is no longer loaded; read the scene settings again: "
                                       + str(error)[:120], code=refusal_code(error), details=refusal_details(error))
                node.image = image
                del node[IMAGE_MARKER]
                if node.image != image:
                    raise ProgramError("The texture image could not be assigned", code='INTERNAL_ERROR')


def _assign_environment_image(world, extras):
    """Give a committed World's environment node the live image staging kept by reference."""
    from .action_world import intended_image
    from .action_world_spec import world_graph
    environment = world_graph(world)["environment"]
    if environment is None:
        return
    wanted = extras["world_image"] if "world_image" in extras else extras.get("world_image_original")
    image = intended_image(extras)
    if wanted is not None and image is None:
        raise ProgramError("The environment image is no longer loaded; read the scene settings again", code='STALE_DEPENDENCY')
    environment.image = image
    if environment.image != image:
        raise ProgramError("The committed World did not retain its environment image", code='INTERNAL_ERROR')


def live_source(clone, stage):
    """The live material or node group a staged clone copies (by the session UID it carries), or None."""
    from .program_objects import MATERIAL_SOURCE
    if clone is None or MATERIAL_SOURCE not in clone.keys():
        return None
    owned = {block.as_pointer() for block in stage.owned_ids + list(stage.context.created_ids)}
    uid = int(clone[MATERIAL_SOURCE])
    collection = {"MATERIAL": _bpy().data.materials, "NODETREE": _bpy().data.node_groups}[clone.id_type]
    return next((item for item in collection if int(item.session_uid) == uid and item.as_pointer() not in owned), None)


def settings_material(material, stage):
    """The live material ``property_set``'s material paths are written into at commit, or None for a new one.

    A staged clone stands for the live material it copies; one the live file no
    longer has refuses the commit. A material this program made (no marker) is
    already the object's after the data and slot transfer, values included.
    """
    from .program_objects import MATERIAL_SOURCE
    live = live_source(material, stage)
    if live is None and material is not None and MATERIAL_SOURCE in material.keys():
        raise ProgramError("The edited material is no longer in the file", code='STALE_DEPENDENCY')
    return live


def _restore_source_identities(objects, stage, *, record_transfer=None):
    """Give committed objects their live materials and node groups instead of unmodified stage clones.

    Staging clones every material and node group of a written object that it
    does not borrow (stage_borrowing), so the stage graph is private, and no
    kernel edits an existing one (material and node kinds create new IDs), so a
    clone is still identical to the live ID it copies. Keeping the clone would leave a duplicate (``Material.001``,
    ``Geometry Nodes.001``) behind every committed edit. The original is found
    by the session UID the clone carries; a missing original keeps the clone,
    without the stage-only property. An optional ``record_transfer(staged_id,
    actual_live_id, reason, occurrence='')`` observes successful assignments
    before source markers are removed; it must not retain RNA references.
    """
    from .program_objects import MATERIAL_SOURCE
    owned = {block.as_pointer() for block in stage.owned_ids + list(stage.context.created_ids)}
    collections = {"MATERIAL": _bpy().data.materials, "NODETREE": _bpy().data.node_groups}
    originals = {}

    def original(clone):
        if clone is None or clone.as_pointer() not in owned or MATERIAL_SOURCE not in clone.keys():
            return clone
        key = (clone.id_type, int(clone[MATERIAL_SOURCE]))
        if key not in originals:
            originals[key] = next((item for item in collections[clone.id_type]
                                   if int(item.session_uid) == key[1] and item.as_pointer() not in owned), None)
        if originals[key] is None:
            if record_transfer is None:
                del clone[MATERIAL_SOURCE]
            return clone
        return originals[key]

    def record(clone, live, occurrence):
        if record_transfer is None or clone.as_pointer() not in owned:
            return
        reason = "source_identity_restored" if clone != live else "source_identity_retained"
        record_transfer(clone, live, reason, occurrence)
        if clone.id_type == "MATERIAL" and clone.node_tree is not None and live.node_tree is not None:
            record_transfer(clone.node_tree, live.node_tree, reason, occurrence + "/node_tree")
        if clone == live and MATERIAL_SOURCE in clone.keys():
            del clone[MATERIAL_SOURCE]

    for obj in objects:
        for index, slot in enumerate(obj.material_slots):
            if slot.material is not None:
                clone = slot.material
                slot.material = original(clone)
                if record_transfer is not None:
                    record(clone, slot.material, "object:%s/material_slots/%s" % (obj.as_pointer(), index))
        for modifier in obj.modifiers:
            if getattr(modifier, "node_group", None) is not None:
                clone = modifier.node_group
                modifier.node_group = original(clone)
                if record_transfer is not None:
                    record(clone, modifier.node_group, "object:%s/modifier:%s/node_group" %
                           (obj.as_pointer(), modifier.as_pointer()))


def _adopt_names(pairs, journal):
    """Give each committed copy the name of the live ID it replaced, when nothing uses that ID any more.

    Staging copies data, Actions and Worlds, which Blender names with a
    suffix (``Cube.001``). After commit the replaced ID has no users when the
    edited object was its only user, so the copy takes its name and the orphan
    the suffixed one; a replaced ID that other users keep (shared data) keeps
    its name. Every rename is journaled for rollback.
    """
    import uuid
    for pair in pairs:
        old, new = pair[0], pair[1]
        owner = pair[2] if len(pair) > 2 else old  # A shape-key Key follows its mesh's users.
        if old is None or new is None or old == new or owner.users or old.library is not None:
            continue
        # The orphan takes the name back too, so Blender gives it the lowest free suffix: the copy's own name may
        # be private (stage_names), and a private name never reaches the file.
        wanted = old.name
        for block, name in ((old, "__angelo_rename_" + uuid.uuid4().hex[:12]), (new, wanted), (old, wanted)):
            journal.append((block, "name", block.name))
            block.name = name
        if new.name != wanted:
            raise ProgramError("A committed copy could not take the name of the data it replaced: " + wanted[:64],
                               code='INTERNAL_ERROR')

