# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""node_edit staging and commit: sharing facts, a private World, and the journaled transfer of edited trees.

Staging (``prepare``, inside ``create_stage``'s bounded phase, before the first
bundle) records for every tree a node_edit may touch how many users outside
its target the live tree has (other objects or meshes, other modifiers or
group nodes, other scenes, a fake user): ``node_edit`` refuses a shared tree
unless ``shared`` is ``ISOLATE``. A World edit gets a private copy of the bound
scene's World (any graph): its nested groups are private clones and its
images stay out of the stage, recorded by session UID on their nodes, as
``world_configure`` does.

Commit (``transfer_trees``, ``transfer_world``) takes each edited staged copy
(``node_edit.EDITED``) and, inside the host's Undo operator:

* ``REMAP``: checks again that the live tree has no other user (for a material
  edited for all its users: that those users are the staged ones,
  ``material_sharing.verify``), then gives every use of it the edited copy
  (``ID.user_remap``), journaled so rollback remaps back; the copy then takes
  the original's name (``_adopt_names``);
* ``ASSIGN``: points only the target's own references (its slots, its
  modifier, the bound scene) at the copy, journaled; other users keep the
  original, and the result lists the isolation;
* then resolves the copy's staging leftovers: nested group clones nobody
  edited go back to their live groups, images return by marker, and the
  markers are removed.

Nothing here edits a live tree in place, so preview equals commit.
"""

from .derived_checks import DEFAULT_MATERIAL_NAME, DEFAULT_SHADER_NAME
from .program_jobs import ProgramError
from .refusal_codes import refusal_code, refusal_details
from .node_edit_spec import EDITED, MODE, SHARING, SOURCE, is_edited
from . import material_sharing as sharing

IMAGE_SOURCE = "_angelo_image_source_uid"


def _live(collection, uid, owned):
    return next((block for block in collection if int(block.session_uid) == uid and block.as_pointer() not in owned), None)


def other_users(block, allowed, owned, users=None, extra=0):
    """(count, names) of users of a live ``block`` outside ``allowed`` and the stage (fake user counts)."""
    if users is None:
        import bpy
        users = bpy.data.user_map(subset={block}).get(block, set())
    others = [user for user in sharing.affected(users)
              if user.as_pointer() not in owned and not any(user == item for item in allowed)]
    count = len(others) + int(bool(block.use_fake_user)) + extra
    names = [user.name[:48] for user in others[:4]] + (["fake user"] if block.use_fake_user else [])
    return count, ", ".join(names)


def _material_rule(obj):
    """Allowed users of a live material of ``obj``, and whether its data is shared with other objects."""
    data = obj.data
    shared = sharing.shared_data(data)
    return [obj] + ([data] if data is not None and not shared else []), (data if shared else None)


def _groups_in(tree):
    return [node for node in tree.nodes if getattr(node, "node_tree", None) is not None]


# Material kinds that edit a material of their target in place when they name it (action_nodes), and
# their schema default names, which always make a new material (Blender suffixes a colliding one).
MATERIAL_KINDS = {"material_principled": DEFAULT_MATERIAL_NAME, "shader_nodes": DEFAULT_SHADER_NAME}


def edits_in_place(operation):
    """Whether a material operation may edit an existing material: a chosen name, never the default."""
    return operation["kind"] in MATERIAL_KINDS and operation["parameters"]["name"] != MATERIAL_KINDS[operation["kind"]]


def prepare(backend, stage):
    """Record sharing facts for every tree the program's node_edit steps may edit; stage a World copy.

    A material kind naming a material its target already has edits that
    material in place, so the material's facts are recorded too.
    """
    import bpy
    operations = [op for op in stage.binding["program"]["operations"] if op["kind"] == "node_edit" or edits_in_place(op)]
    owned = {block.as_pointer() for block in stage.owned_ids}
    candidates = {}  # (live block pointer, handle) -> (block, handle, allowed, extra users, data shared)
    for operation in operations:
        handle = operation["target"]
        if operation["kind"] in MATERIAL_KINDS:
            obj = stage.originals.get(handle)
            named = [slot.material for slot in (obj.material_slots if obj is not None else ())
                     if slot.material is not None and slot.material.name == operation["parameters"]["name"]]
            if named:
                allowed, shared_data = _material_rule(obj)
                candidates[(named[0].as_pointer(), handle)] = (named[0], handle, allowed, 0, shared_data is not None and any(
                    item.link == "DATA" and item.material == named[0] for item in obj.material_slots))
            continue
        if operation["parameters"]["world"]:
            world = _stage_world(backend, stage)
            candidates[(world.as_pointer(), handle)] = (world, handle, [backend.scene], 0, False)
            continue
        obj = stage.originals.get(handle)
        if obj is None:
            continue  # A new object: node_edit refuses it.
        allowed, shared_data = _material_rule(obj)
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.node_tree is None:
                continue
            # A DATA-linked slot on mesh data other objects share cannot be isolated for this object alone.
            candidates[(material.as_pointer(), handle)] = (material, handle, allowed, 0, shared_data is not None and any(
                item.link == "DATA" and item.material == material for item in obj.material_slots))
            for node in _groups_in(material.node_tree):
                count = sum(1 for item in _groups_in(material.node_tree) if item.node_tree == node.node_tree)
                candidates[(node.node_tree.as_pointer(), handle)] = (node.node_tree, handle, [material], int(count > 1), False)
        for modifier in obj.modifiers:
            group = getattr(modifier, "node_group", None)
            if group is not None:
                count = sum(1 for item in obj.modifiers if getattr(item, "node_group", None) == group)
                candidates[(group.as_pointer(), handle)] = (group, handle, [obj], int(count > 1), False)
    if not candidates:
        return
    users = bpy.data.user_map(subset={entry[0] for entry in candidates.values()})
    facts = stage.extras.setdefault(SHARING, {})
    everyone = sharing.wanted(operations)
    for block, handle, allowed, extra, data_shared in candidates.values():
        count, who = other_users(block, allowed, owned, users.get(block, set()), extra)
        entry = facts.setdefault(handle, {})[str(int(block.session_uid))] = {
            "others": count, "name": block.name[:64], "who": who, "data_shared": data_shared}
        if everyone and count and isinstance(block, bpy.types.Material):
            sharing.record(entry, block, backend.scene, owned)  # The objects an edit for all users would change.


def _stage_world(backend, stage):
    """Give the stage scene a private copy of the bound World (any graph), images by marker; return the live World."""
    from .action_properties import data_family
    world = backend.scene.world
    if world is None or world.node_tree is None:
        raise ProgramError("node_edit NO_WORLD: the scene has no World node tree; create one with world_configure", code='VALIDATION')
    if world.library is not None or getattr(world, "override_library", None) is not None:
        raise ProgramError("node_edit NOT_LOCAL: a linked World cannot be edited natively", code='UNSUPPORTED')
    operations = stage.binding["program"]["operations"]
    if any(op["kind"] == "world_configure" or (op["kind"] == "property_set" and any(
            data_family(path) == "world" for path in op["parameters"]["values"])) for op in operations):
        raise ProgramError("node_edit WORLD_CONFLICT: edit the World's nodes in a program of its own, without "
                           "world_configure or world.* settings", code='UNSUPPORTED')
    if stage.scene.world is not None and stage.scene.world.get("_angelo_stage_source_uid") == int(world.session_uid):
        return world  # Already staged by an earlier World step.
    private = private_world(backend, stage, world)
    private[SOURCE] = int(world.session_uid)
    stage.scene.world = private
    return world


def private_world(backend, stage, world):
    """A private copy of the live ``world`` with its whole graph: nested groups cloned, images kept by marker.

    Nothing live enters the stage bundle; ``finish`` gives the committed copy
    the live groups nobody edited and the images back.
    """
    from .stage_names import hide
    private = hide(stage.extras, backend._track(stage, world.copy()), world.name)
    if private.node_tree is not None:
        backend._track(stage, private.node_tree)
        for node in _groups_in(private.node_tree):
            node.node_tree = backend._clone_tree(stage, node.node_tree)
        _strip_images(private.node_tree, set())
    return private


def _strip_images(tree, seen):
    """Images stay out of the stage bundle: each image node keeps the live image's session UID instead."""
    if tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    for node in tree.nodes:
        image = getattr(node, "image", None)
        if image is not None:
            node[IMAGE_SOURCE] = int(image.session_uid)
            node.image = None
        if getattr(node, "node_tree", None) is not None:
            _strip_images(node.node_tree, seen)


class _RemapBack:
    """A settings-journal entry: rollback gives every user of the committed copy the live tree back."""

    def __init__(self, committed):
        self.committed = committed

    @property
    def target(self):
        return self.committed

    @target.setter
    def target(self, previous):
        self.committed.user_remap(previous)


class _SlotBack:
    """A settings-journal entry restoring one material slot of a live object."""

    def __init__(self, obj, index):
        self.obj, self.index = obj, index

    @property
    def material(self):
        return self.obj.material_slots[self.index].material

    @material.setter
    def material(self, value):
        self.obj.material_slots[self.index].material = value


def _owned(stage):
    return {block.as_pointer() for block in list(stage.owned_ids) + list(stage.context.created_ids)}


def finish(tree, stage, owned, seen=None, *, record_transfer=None):
    """Resolve an edited tree's staging leftovers before it becomes live (it has no live users yet to journal).

    Nested group clones nobody edited go back to their live groups; an isolated
    edited group (carried by this tree) is resolved the same way and unmarked.
    """
    import bpy
    from .action_node_graphs import IMAGE_MARKER
    from .action_world import resolve_image
    seen = set() if seen is None else seen
    seen.add(tree.as_pointer())
    for node in tree.nodes:
        group = getattr(node, "node_tree", None)
        if group is not None and SOURCE in group.keys():  # A nested group clone nobody edited: the live group.
            live = _live(bpy.data.node_groups, int(group[SOURCE]), owned)
            if live is not None:
                node.node_tree = live
                if record_transfer is not None:
                    record_transfer(group, node.node_tree, "nested_source_restored",
                                    "node_tree:%s/node:%s/node_tree" % (tree.as_pointer(), node.as_pointer()))
            else:
                if record_transfer is not None:
                    record_transfer(group, group, "nested_source_retained",
                                    "node_tree:%s/node:%s/node_tree" % (tree.as_pointer(), node.as_pointer()))
                del group[SOURCE]
        elif group is not None and group.as_pointer() in owned and group.as_pointer() not in seen:
            finish(group, stage, owned, seen, record_transfer=record_transfer)  # An edited or new group this tree carries.
            if record_transfer is not None:
                record_transfer(group, group, "node_tree_retained",
                                "node_tree:%s/node:%s/node_tree" % (tree.as_pointer(), node.as_pointer()))
            _unmark(group)
        if IMAGE_MARKER in node.keys():
            try:
                node.image = resolve_image(node[IMAGE_MARKER])
            except ValueError as error:
                raise ProgramError("node_edit IMAGE_EXPIRED: read the scene settings again: " + str(error)[:120],
                                   code=refusal_code(error), details=refusal_details(error))
            del node[IMAGE_MARKER]
        if IMAGE_SOURCE in node.keys():
            image = _live(bpy.data.images, int(node[IMAGE_SOURCE]), owned)
            if image is None:
                raise ProgramError("node_edit IMAGE_GONE: an image of the edited World is no longer loaded", code='STALE_DEPENDENCY')
            node.image = image
            del node[IMAGE_SOURCE]


def _unmark(block):
    for key in (EDITED, MODE, sharing.USERS):
        if key in block.keys():
            del block[key]


def _record_transfer(block, live, reason, occurrence, record_transfer):
    """Observe the decided IDs, including an owner's embedded tree, without retaining RNA."""
    if record_transfer is None:
        return
    record_transfer(block, live, reason, occurrence)
    if block.id_type in {"MATERIAL", "WORLD"} and block.node_tree is not None and live.node_tree is not None:
        record_transfer(block.node_tree, live.node_tree, reason, occurrence + "/node_tree")


def _transfer(block, live, allowed, extra, assign, journal, replaced, isolated, owned, stage=None, *,
              record_transfer=None, occurrence=""):
    if block[MODE] == "REMAP":
        if sharing.users_of(block) is not None:  # Edited for all its users: they must be the staged ones.
            sharing.verify(block, live, _bound_scene(stage), owned, stage.extras)
        else:
            count, who = other_users(live, allowed, owned, extra=extra)
            if count:
                raise ProgramError("node_edit NODE_TREE_SHARED: %r gained other users before commit (%s); observe "
                                   "again and edit it with shared ISOLATE" % (live.name[:64], who), code='STALE_DEPENDENCY')
        journal.append((_RemapBack(block), "target", live))
        live.user_remap(block)
        _record_transfer(block, block, "node_tree_remapped", occurrence, record_transfer)
        replaced.append((live, block))
    else:
        assign()
        isolated.append({"name": live.name[:64], "data_users_before": live.users,
                         "policy": "Only this target receives the edited node tree; other users keep the original"})


def _bound_scene(stage):
    import bpy
    uid = stage.binding["scene_uid"]
    return next(scene for scene in bpy.data.scenes if int(scene.session_uid) == uid)


def transfer_trees(original, staged, stage, old_data, journal, replaced, isolated, *, record_transfer=None):
    """Give a written object its edited material and Geometry Nodes trees (after any data swap).

    Optional ``record_transfer(staged_id, actual_live_id, reason, occurrence='')``
    observes successful transfers before markers are removed. The observer must
    copy primitive identities without retaining these RNA references.
    """
    import bpy
    owned = _owned(stage)
    roots, nested = [], {}
    for index, slot in enumerate(staged.material_slots):  # A material in several slots is one root, its groups once each.
        material = slot.material
        if material is not None and material.node_tree is not None:
            if is_edited(material) and all(material != item for item, _, _ in roots):
                roots.append((material, None, index))
            nested.update({node.node_tree.as_pointer(): (node.node_tree, material, node)
                           for node in _groups_in(material.node_tree) if is_edited(node.node_tree)})
    for modifier in staged.modifiers:
        group = getattr(modifier, "node_group", None)
        if group is not None and is_edited(group):
            roots.append((group, modifier.name, None))
    for group, material, node in nested.values():  # Inner trees first: an edited root then points at the edited group.
        if group[MODE] != "REMAP":
            continue  # Isolated: the edited root carries it.
        live = _live(bpy.data.node_groups, int(group[EDITED]), owned)
        root = _live(bpy.data.materials, int(material.get(EDITED, material.get(SOURCE, 0))), owned)
        if live is None or root is None:
            raise ProgramError("node_edit NODE_TREE_GONE: the edited node group was removed before commit", code='STALE_DEPENDENCY')
        # Remapping the group edits its root material for every user of that material: it must be the target's own.
        allowed, _shared_data = _material_rule(original)
        root_others, who = other_users(root, allowed + ([old_data] if old_data is not None and not old_data.users else []),
                                       owned)
        if root_others:
            raise ProgramError("node_edit NODE_TREE_SHARED: %r gained other users before commit (%s); observe again "
                               "and edit it with shared ISOLATE" % (root.name[:64], who), code='STALE_DEPENDENCY')
        count = sum(1 for node in _groups_in(root.node_tree) if node.node_tree == live)
        occurrence = ("node_tree:%s/node:%s/node_tree" % (material.node_tree.as_pointer(), node.as_pointer())
                      if record_transfer is not None else "")
        _transfer(group, live, [root], int(count > 1), None, journal, replaced, isolated, owned,
                  record_transfer=record_transfer, occurrence=occurrence)
        finish(group, stage, owned, record_transfer=record_transfer)
        _unmark(group)
    for block, modifier_name, slot_index in roots:
        if modifier_name is None:
            live = _live(bpy.data.materials, int(block[EDITED]), owned)
            if live is None:
                raise ProgramError("node_edit NODE_TREE_GONE: the edited material was removed before commit", code='STALE_DEPENDENCY')
            allowed, _shared = _material_rule(original)
            if old_data is not None and old_data != original.data and not old_data.users:
                allowed.append(old_data)  # The swapped-out data no object uses any more.

            def assign(live=live, block=block):
                for index, slot in enumerate(original.material_slots):
                    if slot.material == live:
                        if slot.link == "DATA" and original.data.users > 1:
                            raise ProgramError("node_edit DATA_SHARED: the slot is linked to mesh data other objects "
                                               "share; link it to the object first", code='UNSUPPORTED')
                        journal.append((_SlotBack(original, index), "material", live))
                        original.material_slots[index].material = block
                        if record_transfer is not None:
                            _record_transfer(block, original.material_slots[index].material, "node_tree_assigned",
                                             "object:%s/material_slots/%s" % (original.as_pointer(), index), record_transfer)
                    elif record_transfer is not None and slot.material == block:
                        # A preceding data swap can already have carried this edited material into its live slot.
                        _record_transfer(block, slot.material, "node_tree_retained",
                                         "object:%s/material_slots/%s" % (original.as_pointer(), index), record_transfer)

            occurrence = ("object:%s/material_slots/%s" % (original.as_pointer(), slot_index)
                          if record_transfer is not None else "")
            _transfer(block, live, allowed, 0, assign, journal, replaced, isolated, owned, stage,
                      record_transfer=record_transfer, occurrence=occurrence)
            finish(block.node_tree, stage, owned, record_transfer=record_transfer)
        else:
            live = _live(bpy.data.node_groups, int(block[EDITED]), owned)
            modifier = original.modifiers.get(modifier_name)
            if live is None or modifier is None or modifier.type != "NODES":
                raise ProgramError("node_edit NODE_TREE_GONE: the edited node group or its modifier changed before commit",
                                   code='STALE_DEPENDENCY')
            count = sum(1 for item in original.modifiers if getattr(item, "node_group", None) == live)

            def assign(modifier=modifier, block=block):
                journal.append((modifier, "node_group", modifier.node_group))
                modifier.node_group = block
                if record_transfer is not None:
                    _record_transfer(block, modifier.node_group, "node_tree_assigned",
                                     "object:%s/modifier:%s/node_group" %
                                     (original.as_pointer(), modifier.as_pointer()), record_transfer)

            occurrence = ("object:%s/modifier:%s/node_group" % (original.as_pointer(), modifier.as_pointer())
                          if record_transfer is not None else "")
            _transfer(block, live, [original], int(count > 1), assign, journal, replaced, isolated, owned,
                      record_transfer=record_transfer, occurrence=occurrence)
            finish(block, stage, owned, record_transfer=record_transfer)
        _unmark(block)


def transfer_world(scene, stage, journal, replaced, isolated, *, record_transfer=None):
    """Give the bound scene its edited World; return it so commit keeps it.

    ``record_transfer`` has the same synchronous observer contract as ``transfer_trees``.
    """
    import bpy
    world = stage.scene.world
    owned = _owned(stage)
    if world is None or not is_edited(world):
        raise ProgramError("node_edit WORLD_CHANGED: the staged World was not edited", code='INTERNAL_ERROR')
    live = scene.world
    if live is None or int(live.session_uid) != int(world[EDITED]):
        raise ProgramError("node_edit WORLD_CHANGED: the scene's World changed before commit", code='STALE_SETTING')

    def assign():
        journal.append((scene, "world", live))
        scene.world = world
        if record_transfer is not None:
            _record_transfer(world, scene.world, "node_tree_assigned", "scene:%s/world" % scene.as_pointer(), record_transfer)

    occurrence = "scene:%s/world" % scene.as_pointer() if record_transfer is not None else ""
    _transfer(world, live, [scene], 0, assign, journal, replaced, isolated, owned,
              record_transfer=record_transfer, occurrence=occurrence)
    finish(world.node_tree, stage, owned, record_transfer=record_transfer)
    _unmark(world)
    if scene.world != world:
        raise ProgramError("node_edit WORLD_CHANGED: the scene did not take its edited World", code='INTERNAL_ERROR')
    return world


def delta_rows(before, after, logical):
    """Preview rows: each edited tree of a staged object, with its live and staged node and link counts."""
    rows = []
    for slot_before, slot in zip(before.material_slots, after.material_slots):
        block = slot.material
        if block is not None and is_edited(block) and block.node_tree is not None:
            live = slot_before.material
            rows.append({"material": logical(block), "policy": block[MODE],
                         "nodes": [len(live.node_tree.nodes) if live and live.node_tree else None, len(block.node_tree.nodes)],
                         "links": [len(live.node_tree.links) if live and live.node_tree else None, len(block.node_tree.links)]})
    for modifier in after.modifiers:
        group = getattr(modifier, "node_group", None)
        if group is not None and is_edited(group):
            live = getattr(before.modifiers.get(modifier.name), "node_group", None)
            rows.append({"modifier": modifier.name[:64], "policy": group[MODE],
                         "nodes": [len(live.nodes) if live else None, len(group.nodes)],
                         "links": [len(live.links) if live else None, len(group.links)]})
    return rows[:16]


def world_row(live, staged):
    if staged is None or not is_edited(staged):
        return None
    return {"world": live.name[:64] if live else None, "policy": staged[MODE],
            "nodes": [len(live.node_tree.nodes) if live and live.node_tree else None, len(staged.node_tree.nodes)],
            "links": [len(live.node_tree.links) if live and live.node_tree else None, len(staged.node_tree.links)]}
