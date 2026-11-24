# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine game rig: a deform-only, engine-named copy of a control rig (doc/angelo/rigging.rst, "Game rig").

``make_game_rig(control, engine)``:

* builds the skeleton ``game_rig_names.plan`` lays out: the control rig's deforming bones at their rest placement,
  named by role for the engine, each under its nearest deforming ancestor; for Unreal, filler bones (deforming
  nothing) where the UE5 Mannequin has a bone the rig lacks, so every name sits under the parent Unreal expects; the
  engine's root;
* bakes every action the control rig plays, one action each, from its evaluated pose (constraints and IK included),
  each with the rig's NLA off, so other tracks never blend in;
* copies the meshes skinned to the control rig, bound to the game rig with their vertex groups renamed, in place;
* puts the game rig and its meshes in one collection of their own; nothing on the control rig changes;
* checks the result with the engine's rig check (``skeleton_check``) and returns it in the receipt.
"""

import bpy
from mathutils import Vector

from . import game_rig_names, rig_pose_write, skeleton_facts, skeleton_profiles
from .rig_edit import RigError, edit_bones, frames, new_bone, nla_off, played_actions, use_action
from .skeleton_roles import map_roles

MARK, ENGINE = "angelo_game_rig_of", "angelo_game_rig_engine"


def role_map(obj):
    """({role: bone}, mapping, rig facts) of ``obj`` by layout (``skeleton_roles.map_roles``)."""
    facts = skeleton_facts.gather(bpy.context.scene, obj)
    rig = facts["rig"]
    mapping = map_roles(rig)
    return {role: rig.names[i] for role, i in mapping.roles.items()}, mapping, facts


FOLLOWING = ('COPY_TRANSFORMS', 'CHILD_OF')   # Constraints by which a deforming bone carries a control bone's motion.


def _follows(control, deform):
    """{control bone: the deforming bone that follows it} through this armature's own Copy Transforms / Child Of
    (enabled, mostly in force): a generated Rigify rig's DEF bones follow its ORG bones."""
    out = {}
    for pose_bone in control.pose.bones:
        if pose_bone.name not in deform:
            continue
        for c in pose_bone.constraints:
            if (c.type in FOLLOWING and c.enabled and c.influence >= 0.5 and getattr(c, "target", None) is control
                    and c.subtarget and c.subtarget != pose_bone.name):
                out.setdefault(c.subtarget, pose_bone.name)
    return out


def _template(profile):
    """The skeleton an exact-name engine expects bone by bone (Unreal's), as (name, parent, role); None otherwise."""
    return skeleton_profiles.UE_SKELETON if profile.exact_names else None


def _place_filler(edit, entry, height):
    k, m, top, below = entry["run"]
    bone = edit[entry["name"]]
    if top is None:
        bone.head, bone.tail = Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.1 * height, 0.0))
        return
    start, end = edit[top].head.copy(), edit[below].head.copy() if below else edit[top].tail.copy()
    bone.head = start.lerp(end, k / (m + 1))
    bone.tail = start.lerp(end, (k + 1) / (m + 1))
    if (bone.tail - bone.head).length < 1e-4 * max(height, 1e-3):
        bone.tail = bone.head + Vector((0.0, 0.0, 0.01 * max(height, 0.1)))


def _collection(control, engine):
    name = "%s %s rig" % (control.name, skeleton_profiles.PROFILES[engine].display)
    collection = bpy.data.collections.new(name)
    parent = control.users_collection[0] if control.users_collection else bpy.context.scene.collection
    parent.children.link(collection)
    return collection


def make_game_rig(control, engine):
    """Returns a receipt: {"game_rig", "engine", "bones", "added", "notes", "actions", "meshes", "check"}."""
    profile = skeleton_profiles.PROFILES[engine]
    roles, _, facts = role_map(control)
    bones = [b.name for b in rig_pose_write.ordered(control)]
    parents = {b.name: (b.parent.name if b.parent else None) for b in control.data.bones}
    deform = {b.name for b in control.data.bones if b.use_deform}
    if not deform:
        raise RigError("no_deform")
    plan = game_rig_names.plan(bones, parents, deform, roles, profile, _template(profile), _follows(control, deform))
    height = max(control.dimensions.z, 0.1)
    collection = _collection(control, engine)
    data = bpy.data.armatures.new("%s_%s" % (control.name, engine.lower()))
    game = bpy.data.objects.new(data.name, data)
    collection.objects.link(game)
    game.matrix_world = control.matrix_world.copy()
    game[MARK], game[ENGINE] = control.name, engine
    with edit_bones(game) as edit:
        for entry in plan["bones"]:
            if entry["source"]:
                src = control.data.bones[entry["source"]]
                eb = new_bone(edit, entry["name"], src.matrix_local.copy(), src.length)
            else:
                eb = edit.new(entry["name"])
                eb.head, eb.tail = Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.01, 0.0))
            eb.use_deform = True
        for entry in plan["bones"]:
            if entry["parent"]:
                edit[entry["name"]].parent = edit[entry["parent"]]
            if entry.get("run"):
                _place_filler(edit, entry, height)
    baked = _bake(control, game, plan)
    meshes = _rebind_meshes(control, game, plan["names"], collection)
    from . import skeleton_check
    report = skeleton_check.check(skeleton_facts.gather(bpy.context.scene, game), engine)
    return {"game_rig": game, "engine": engine, "bones": len(plan["bones"]),
            "added": [e["name"] for e in plan["bones"] if not e["source"]], "notes": plan["notes"],
            "actions": baked, "meshes": meshes,
            "check": {"verdict": report["verdict"], "counts": report["counts"],
                      "problems": [f["check"] for f in report["findings"] if f["status"] in ("fail", "warn")]}}


def _bake(control, game, plan):
    sources = {e["name"]: e["source"] for e in plan["bones"] if e["source"]}
    scene = bpy.context.scene
    current = scene.frame_current
    ad = control.animation_data
    held = (ad.action, ad.action_slot) if ad else (None, None)
    to_game = game.matrix_world.inverted() @ control.matrix_world
    baked = []
    try:
        with nla_off(control):
            for action in played_actions(control):
                use_action(control, action)
                out = bpy.data.actions.new("%s_%s" % (action.name, game[ENGINE].lower()))
                out[MARK] = control.name
                use_action(game, out)
                keys = rig_pose_write.Keys(game)
                for frame in frames(action):
                    scene.frame_set(frame)
                    desired = {name: to_game @ control.pose.bones[src].matrix for name, src in sources.items()}
                    rig_pose_write.write(game, desired, frame, keys=keys)
                keys.flush()
                baked.append(out.name)
    finally:
        if ad:
            ad.action = held[0]
            if held[0] is not None and held[1] is not None:
                ad.action_slot = held[1]
        scene.frame_set(current)
    if baked:
        use_action(game, bpy.data.actions[baked[0]])
    return baked


def _rename_groups(obj, names):
    """Rename ``obj``'s vertex groups by ``names``; groups that map to one name are merged (weights added, capped at
    1), as a bone left out of the game rig hands its skin to the bone that carries its motion."""
    renamed = [(g, names[g.name]) for g in obj.vertex_groups if g.name in names]
    for k, (group, _) in enumerate(renamed):   # Two passes: a new name may be another group's old one.
        group.name = "angelo_tmp_group_%d" % k
    first = {}
    for group, name in renamed:
        if name not in first:
            group.name = name
            first[name] = group
            continue
        keep = first[name]
        for vertex in obj.data.vertices:
            weight = next((g.weight for g in vertex.groups if g.group == group.index), 0.0)
            if weight:
                current = next((g.weight for g in vertex.groups if g.group == keep.index), 0.0)
                keep.add([vertex.index], min(1.0, current + weight), 'REPLACE')
        obj.vertex_groups.remove(group)


def _rebind_meshes(control, game, names, collection):
    copies = []
    for obj in list(bpy.context.scene.objects):
        if obj.type != 'MESH' or not any(m.type == 'ARMATURE' and m.object is control for m in obj.modifiers):
            continue
        world = obj.matrix_world.copy()
        copy = obj.copy()
        copy.data = obj.data.copy()
        collection.objects.link(copy)
        _rename_groups(copy, names)

        for modifier in copy.modifiers:
            if modifier.type == 'ARMATURE' and modifier.object is control:
                modifier.object = game
        copy.parent = game
        copy.matrix_world = world
        copies.append(copy.name)
    return copies
