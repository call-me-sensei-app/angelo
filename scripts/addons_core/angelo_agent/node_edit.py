# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged ``node_edit`` kernel: resolve one existing tree, keep it private, apply the steps, keep the graph valid.

Ownership. Staging already copies every material and node group of a written
object (``program_objects``; each clone carries ``SOURCE``, the live session
UID it copies) and, for a World edit, the bound scene's World
(``node_edit_commit.prepare``). This kernel edits only those private copies:

* a copy another staged user shares (a staged duplicate made earlier in the
  program) is copied first, so only the target's references change;
* all slots of the target that hold copies of the same live material point at
  one edited copy, so a material used in several slots stays one material;
* the edited copy trades ``SOURCE`` for ``EDITED`` (the same live UID) and
  ``MODE``: ``REMAP`` when the live tree has no other user (commit replaces
  every use with the copy), ``ASSIGN`` under ``shared: ISOLATE`` (commit gives
  only the target's own reference the copy). Sharing facts come from staging
  (``stage.extras[SHARING]``); commit checks them again on the live scene;
* a material edited under ``shared: ALL_USERS`` is ``REMAP`` too, with the
  names of the objects that use it (``material_sharing``): every staged object
  holding a copy of it shows the one edited copy, and commit replaces every
  use after checking the users again;
* a material this program created has no live UID. ``ISOLATE`` copies only
  the target's exact references and records private request-local provenance
  for later chosen-name lookup; other fresh material IDs remain unrelated.

Validity after every operation: no output node added, removed, duplicated,
swapped or grouped; a linked output surface stays linked; every link valid and
the graph acyclic; at most 256 nodes and 512 links. A Geometry Nodes tree must
stay inside the closed catalog contract (``node_edit_geometry``), whose
evaluated size is bounded again before any evaluation.
"""

import bpy

from .refusal_codes import refusal_code, refusal_details
from .node_edit_spec import EDITED, MODE, SOURCE, SHARING, NodeEditError, is_edited  # noqa: F401 (re-exported)
from . import material_sharing as sharing, node_edit_ops as ops


def source_uid(block):
    """The live session UID a staged copy stands for, or None for an ID this program created.

    A fresh staging clone's SOURCE wins over an EDITED marker it copied from a live tree.
    """
    keys = block.keys()
    for marker in (SOURCE, EDITED):
        if marker in keys:
            return int(block[marker])
    return None


_edited = is_edited
# A fact, not a refusal: Blender itself edits a linked datablock by making it local.
LIBRARY_COPY = ("%r comes from the linked library %r: as Blender does, the edit is a local copy, which this file's own "
                "users now show%s; the library file is unchanged, and so is every other file that links it")
LIBRARY_KEEPS = " (data linked from a library keeps the library's material: %s)"


def library_copy(block):
    """``LIBRARY_COPY`` for a staged copy whose live original is linked from a library (or overrides one), else None."""
    uid = source_uid(block)
    if uid is None:
        return None
    collection = (bpy.data.materials if isinstance(block, bpy.types.Material) else bpy.data.worlds
                  if isinstance(block, bpy.types.World) else bpy.data.node_groups)
    live = next((item for item in collection if int(item.session_uid) == uid and item != block), None)
    override = getattr(live, "override_library", None) if live is not None else None
    library = live.library if live is not None else None
    if library is None and override is not None and override.reference is not None:
        library = override.reference.library
    if library is None:
        return None
    from .material_sharing import kept
    linked = sorted(user.name[:48] for user in kept(bpy.data.user_map(subset={live}).get(live, ()))
                    if getattr(user, "library", None) is not None)  # Not changed: material_sharing.affected.
    return LIBRARY_COPY % (live.name[:64], library.name[:64],
                           LIBRARY_KEEPS % ", ".join(repr(name) for name in linked[:4]) if linked else "")


def _facts(context, target, uid):
    facts = context.extras.get(SHARING, {}).get(target, {}).get(str(uid))
    if facts is None and (target.startswith("$") or sharing.assigned(context, uid)):
        # The material came to this object during the program (material_assign, also, a copy of an object):
        # nothing bound its other users for this target. A correctable refusal that names the remedy.
        from .program_verification import _committed_material_name
        from .material_rules import shared_in_program
        live = next((block for block in bpy.data.materials if int(block.session_uid) == uid), None)
        name = repr(_committed_material_name(live)[:64]) if live is not None else "The material"
        raise NodeEditError(*shared_in_program(name, arrived=True))
    if facts is None:
        # Its users were not bound for this target: an unbound object, or a material that reached a bound one in
        # this program some way the bound obligations did not foresee. The remedy for each, never an internal error.
        raise NodeEditError("SHARING_UNKNOWN", "its users were not bound for this target; bind the object in the "
                                               "write set, or, if the material came to it in this program, edit it on "
                                               "the object it came from or in a later program",
                            refusal_code="VALIDATION")
    return facts


def _shared(context, target, block):
    """Whether the live tree a staged copy stands for has users outside the target (or is already isolated)."""
    uid = source_uid(block)
    if uid is None:
        return False
    if _edited(block):
        return block[MODE] == "ASSIGN"
    return _facts(context, target, uid)["others"] > 0


def _mark(context, target, block, shared, isolate=False):
    """Record that ``block`` (a staged copy) is edited, refusing a shared live tree unless ISOLATE.

    ``isolate`` forces ASSIGN for a nested group whose root is isolated: its live
    group then stays with the root's other users.
    """
    uid = source_uid(block)
    if uid is None:
        return  # Created in this program: nothing live to transfer or protect.
    facts = _facts(context, target, uid)
    others, name, who = facts["others"], facts["name"], facts["who"]
    if _edited(block):
        if block[MODE] == "ASSIGN" and shared != "ISOLATE":
            raise NodeEditError("NODE_TREE_SHARED", "%r is isolated by an earlier step; keep shared ISOLATE" % name)
        if sharing.users_of(block) is not None and shared != sharing.ALL_USERS:
            raise NodeEditError("NODE_TREE_SHARED", "%r is edited for all its users by an earlier step; keep shared "
                                                    "ALL_USERS" % name)
        return
    if others and shared == sharing.ALL_USERS:
        sharing.mark(block, facts)  # Refuses a user outside the bound scene before anything is marked.
        _swap_marker(block, uid)
        return
    if others and shared == "REFUSE":
        raise NodeEditError("NODE_TREE_SHARED", "%r is also used by %d other user(s)%s; pass shared ALL_USERS to edit "
                                                "a material for every object that uses it, shared ISOLATE to give "
                                                "only this target a private edited copy, or edit it with reviewed "
                                                "Python" % (name, others, (" (" + who + ")") if who else ""))
    if others and facts.get("data_shared"):
        raise NodeEditError("DATA_SHARED", "%r sits in a slot linked to mesh data other objects share, so it cannot be "
                                           "isolated for this object alone; link the slot to the object first" % name)
    _swap_marker(block, uid)[MODE] = "ASSIGN" if others or isolate else "REMAP"


def _swap_marker(block, uid):
    """Trade a staged clone's SOURCE marker for EDITED (the same live UID); return the block."""
    block[EDITED] = uid
    if SOURCE in block.keys():
        del block[SOURCE]
    return block


def _local_animation(*blocks):
    from .material_rules import animated
    if animated(*blocks):
        raise NodeEditError("ANIMATED_TREE", "animated or driven node settings are edited with reviewed Python")


def logical_name(context, target, material):
    """The name a program knows ``material`` by: for a staged copy, the live material's; else its own.

    Staging names a copy with a suffix ("Stone.001") while the live material keeps "Stone". The target's
    sharing facts hold the live name when staging recorded them; on any other holder (the source a
    material_assign reuses, an alias, another object given the copy) the live material is found by the
    session UID the copy carries. Never the copy's own name: that broke the second reuse of a material.
    """
    uid = source_uid(material)
    if uid is None:
        from .program_material_facts import logical_material_name
        return logical_material_name(context, target, material) or material.name
    facts = context.extras.get(SHARING, {}).get(target, {}).get(str(uid))
    if facts:
        return facts["name"]
    from .program_verification import _committed_material_name
    return _committed_material_name(material, True)


def _references(obj, block):
    """How many of the staged object's own material references hold ``block`` (slots and the data behind them)."""
    data = list(getattr(obj.data, "materials", ()))
    count = 0
    for index, slot in enumerate(obj.material_slots):
        count += slot.material == block
        if slot.link == "OBJECT":
            count += index < len(data) and data[index] == block
    return count


def _material(context, target, obj, params, own):
    name = params["material"]
    slots = [(index, slot) for index, slot in enumerate(obj.material_slots)
             if slot.material is not None and logical_name(context, target, slot.material) == name]
    if not slots:
        names = sorted({logical_name(context, target, slot.material) for slot in obj.material_slots if slot.material})
        raise NodeEditError("MATERIAL_NOT_FOUND", "no material named %r on this object; its materials: %s"
                                                  % (name[:64], ", ".join(repr(item[:64]) for item in names[:8]) or "none"))
    material = next((slot.material for _index, slot in slots if _edited(slot.material)), slots[0][1].material)
    if material.node_tree is None:
        raise NodeEditError("NO_NODE_TREE", "the material has no node tree")
    if material not in context.owned_ids:
        raise NodeEditError("NOT_STAGED", "the material is not a private staged copy")
    _local_animation(material, material.node_tree)
    if not own:
        return material
    source_material = material
    uid, everyone = source_uid(material), params["shared"] == sharing.ALL_USERS
    if everyone:  # One edited copy for every staged object that holds a copy of the same live material.
        sharing.unify(context, material, uid)
    elif material.users > _references(obj, material):
        from .material_rules import shared_in_program
        if uid is None and params["shared"] == "REFUSE":
            raise NodeEditError(*shared_in_program(repr(name[:64]), created=True))
        if not _edited(material) and sharing.assigned(context, uid):
            raise NodeEditError(*shared_in_program(repr(name[:64]), assigned=True))
        if _edited(material) and params["shared"] == "REFUSE":
            # Its edited copy is another object's too now: never copied for this one silently.
            raise NodeEditError(*shared_in_program(repr(name[:64]), edited=True))
        # A staged duplicate shares this copy: copy it for the target.
        private = context.track_id(material.copy())
        context.owned_ids.add(private.node_tree)  # Embedded: removed with its material.
        from .program_material_facts import record_isolation
        record_isolation(context, target, material, private)
        material = private
    data = getattr(obj.data, "materials", None)
    for index, slot in enumerate(obj.material_slots):
        # Every reference of the target to a copy of the same live material: one edited material.
        same = lambda block: block is not None and (block in (source_material, material)
                                                    or (uid is not None and source_uid(block) == uid))
        if same(slot.material) and slot.material != material:
            obj.material_slots[index].material = material
        if slot.link == "OBJECT" and data is not None and index < len(data) and same(data[index]) and data[index] != material:
            data[index] = material
    _mark(context, target, material, params["shared"])
    return material


def _modifier(context, target, obj, params, own):
    modifier = obj.modifiers.get(params["modifier"])
    if modifier is None or modifier.type != "NODES" or modifier.node_group is None:
        names = [repr(item.name[:64]) for item in obj.modifiers if item.type == "NODES"][:8]
        raise NodeEditError("MODIFIER_NOT_FOUND", "no Geometry Nodes modifier with a group is named %r; its Geometry "
                                                  "Nodes modifiers: %s" % (params["modifier"][:64], ", ".join(names) or "none"))
    tree = modifier.node_group
    _local_animation(tree)
    if tree not in context.owned_ids:
        raise NodeEditError("NOT_STAGED", "the node group is not a private staged copy")
    if not own:
        return tree
    if tree.users > sum(1 for item in obj.modifiers if getattr(item, "node_group", None) == tree):
        private = context.track_id(tree.copy())
        if tree in context.node_graphs:
            import copy
            context.node_graphs[private] = copy.deepcopy(context.node_graphs[tree])
        modifier.node_group = private
        tree = private
    _mark(context, target, tree, params["shared"])
    return tree


def _world(context, target, params, own):
    if target not in getattr(context, "scene_handles", ()):
        raise NodeEditError("OWNER_TYPE_MISMATCH", "world needs the bound scene handle (angelo_scene_settings) as target")
    world = context.scene.world
    if world is None or world.node_tree is None:
        raise NodeEditError("NO_WORLD", "the scene has no World node tree; create one with world_configure first")
    if world not in context.owned_ids:
        raise NodeEditError("NOT_STAGED", "the World is not a private staged copy")
    _local_animation(world, world.node_tree)
    if own:
        _mark(context, target, world, params["shared"])
    return world


def _nested(context, target, root, params):
    """The group of a group node in the root tree, private to that node (one level)."""
    if isinstance(root, bpy.types.World):
        raise NodeEditError("WORLD_NESTED_GROUP", "groups inside the World are not in its revision; edit them with "
                                                  "reviewed Python")

    def find(tree):
        node = tree.nodes.get(params["group"])
        group = getattr(node, "node_tree", None) if node is not None and node.bl_idname == "ShaderNodeGroup" else None
        if group is None:
            raise NodeEditError("GROUP_NOT_FOUND", "no group node with a group is named %r" % params["group"][:64])
        if group not in context.owned_ids:
            raise NodeEditError("NOT_STAGED", "the node group is not a private staged copy")
        _local_animation(group)
        return node, group

    node, group = find(root.node_tree)
    # A group inside a shared root is shared through it: remapping it would edit the root's other users.
    root_shared, group_shared = _shared(context, target, root), _shared(context, target, group)
    if params["shared"] == "REFUSE" and (root_shared or group_shared):
        _mark(context, target, group if group_shared else root, "REFUSE")  # Raises NODE_TREE_SHARED with the users.
    # The root is owned too: its group node may change (a private copy, an isolated edit), and every slot of
    # the target holding a copy of that material must show the same edited group.
    root = _owned_root(context, target, root, params)
    node, group = find(root.node_tree)
    if group.users > 1:
        from .program_material_facts import record_copy
        private = context.track_id(group.copy())
        record_copy(context, group, private, group.name)
        node.node_tree = group = private
    _mark(context, target, group, params["shared"], isolate=root_shared)
    return root, group


def _owned_root(context, target, root, params):
    if isinstance(root, bpy.types.Material):
        return _material(context, target, context.resolve(target), params, True)
    _mark(context, target, root, params["shared"])
    return root


def apply(context, operation):
    """node_edit: the steps on the private staged tree, validated as a whole, with a read-back."""
    params, target = operation["parameters"], operation["target"]
    nested = params["group"] is not None
    obj = None
    world = None
    if params["world"]:
        root = world = _world(context, target, params, not nested)
        tree = root.node_tree
    else:
        if target in getattr(context, "scene_handles", ()):
            raise NodeEditError("OWNER_TYPE_MISMATCH", "material and modifier trees need an object handle as target")
        if target.startswith("$"):
            raise NodeEditError("NEW_OBJECT", "build a new object's graph with shader_nodes or geometry_nodes, or edit "
                                              "it in a later program")
        obj = context.resolve(target)
        from .action_budget import assert_evaluation_budget
        assert_evaluation_budget(context, obj)
        if params["material"] is not None:
            root = _material(context, target, obj, params, not nested)
            tree = root.node_tree
        else:
            root = tree = _modifier(context, target, obj, params, not nested)
    family = "GEOMETRY" if tree.bl_idname == "GeometryNodeTree" else "SHADER"
    if nested:
        if family == "GEOMETRY":
            raise NodeEditError("GN_CONTRACT", "Geometry Nodes catalog graphs have no groups",
                                refusal_code="UNSUPPORTED")
        root, tree = _nested(context, target, root, params)
    # A nested edit can change the group node's sockets in the root tree, which must stay valid too.
    root_state = ops.EditState(context, root.node_tree, family) if nested else None
    state = ops.EditState(context, tree, family, nested=nested, world=params["world"])
    state.group_node = root.node_tree.nodes.get(params["group"]) if nested else None
    for index, edit in enumerate(params["edits"]):
        try:
            ops.apply(state, edit)
        except ValueError as error:  # NodeEditError, rna_rules.PropertyPathError, node_generic.NodeRuleError...
            raise NodeEditError(getattr(error, "code", "REFUSED"), "step %d (%s): %s" % (
                index, edit["op"], str(error).split(": ", 1)[-1] if isinstance(error, NodeEditError) else str(error)),
                                refusal_code=refusal_code(error), details=refusal_details(error))
    state.validate()
    if root_state is not None:
        root_state.validate()
    result = {"kind": "node_edit", "target": target,
              "tree": {"owner": "world" if params["world"] else "material" if params["material"] is not None else "modifier",
                       "name": logical_name(context, target, root) if obj is not None and params["material"] is not None
                       else params["modifier"] or "World", "group": params["group"]},
              "nodes": len(tree.nodes), "links": len(tree.links), **state.summary()}
    if world is not None:  # What the edited World shows a camera: its sky or flat colour (world_surface).
        from .world_surface import shows
        result["shows"] = shows(world, getattr(getattr(context.scene, "render", None), "engine", None))
    if family == "GEOMETRY":
        from .node_edit_geometry import refresh
        result["evaluated_vertex_bound"] = refresh(context, obj, tree)
    marked = root if not nested else tree
    if params["material"] is not None and obj is not None and root.get(MODE) == "ASSIGN":
        from .program_verification import kept_name  # An isolated copy commits as "<name>.001": name it so.
        result["tree"]["name"] = kept_name(context.extras, root)
    if _edited(marked):
        copied = library_copy(marked)
        if copied is not None:
            result["library_copy"] = copied
        result["shared_policy"] = "ISOLATED" if marked[MODE] == "ASSIGN" else "SINGLE_USER"
        users = sharing.users_of(marked)
        if users is not None:  # Named again, from the live scene, in the committed result (shared_edits).
            result.update(shared_policy="ALL_USERS", shared_users=users[:sharing.MAX_NAMED_USERS], shared_user_count=len(users))
    return result
