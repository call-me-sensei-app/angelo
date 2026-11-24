# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Retargeting by role (doc/angelo/rigging.rst, "Retargeting").

Bones are paired by the role each plays, found from each rig's layout (``skeleton_roles.map_roles``, the rig check's
mapping), never by name, so Rigify, Mixamo, Auto-Rig Pro, VRoid, a Biped, a BVH and a hand-made rig pair alike.

``retarget(source, target, root_motion, limbs, plant_feet)`` bakes the source's active action onto the target as a
new action. Per frame:

* **Pose matching**: each paired bone takes the source bone's change from its rest pose, turned into the target's
  facing (the rigs may face different ways), applied after turning the target's rest direction onto the source's
  (joint to joint, ``Rig.ends``), so a T-pose source drives an A-pose target and limbs point the same way.
* **Hips**: their travel scales by the rigs' hip heights above their floors, turned into the target's facing.
  ``root_motion``: ``KEEP`` as is; ``IN_PLACE`` drops the travel along the floor; ``ROOT`` gives it to the bone above
  the hips.
* Unpaired target bones (extra fingers, twist bones, props) keep their own values; only paired bones are keyed.
* **IK controls** (``ik_controls``) on the target: ``limbs='FK'`` keys them off; ``'IK'`` keys each effector where the
  retargeted hand or foot is and each pole on the side that limb bends (``ik_controls.pole_place``), so IK bends it
  the way the source does.
* **Feet planted** (``plant_feet``): frames where a source foot is on the floor and still are found; on the target the
  foot's effector holds one place on the floor for each such stretch (in the travelling frame when the clip is played
  in place), so the feet don't slide. The legs are switched to IK for it (controls are added if the rig has none).

The source is never changed.
"""

import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import ik_controls, rig_pose_write, skeleton_profiles
from .game_rig import role_map
from .rig_edit import RigError, frames, top_above, use_action

CONTACT_HEIGHT, CONTACT_SPEED = 0.03, 0.006   # Fractions of the source's height (per frame for the speed).


def _floor_and_height(rig):
    """The lowest joint's height and the rig's height, world space, from its rest joints."""
    zs = [h[2] for h in rig.heads] + [e[2] for e in rig.ends]
    return min(zs), max(max(zs) - min(zs), 1e-6)


def _facing_turn(s_mapping, t_mapping):
    """The turn about world Z from the source's facing to the target's."""
    def yaw(mapping):
        f = Vector(mapping.forward) if mapping.forward is not None else Vector((0.0, -1.0, 0.0))
        return math.atan2(f.y, f.x)
    return Quaternion((0.0, 0.0, 1.0), yaw(t_mapping) - yaw(s_mapping)).to_matrix()


def _contacts(source, foot, height, floor, frame_list):
    """Frames where the source's ``foot`` joint is on the floor and still, as runs of at least two frames."""
    scene, positions = bpy.context.scene, {}
    for frame in frame_list:
        scene.frame_set(frame)
        positions[frame] = source.matrix_world @ source.pose.bones[foot].head
    low = min(p.z for p in positions.values())
    on = []
    for prev, frame in zip(frame_list, frame_list[1:]):
        step = (positions[frame] - positions[prev]).xy.length
        if positions[frame].z <= low + CONTACT_HEIGHT * height and step <= CONTACT_SPEED * height:
            on += [f for f in (prev, frame) if f not in on]
    runs, run = [], []
    for frame in frame_list:
        if frame in on:
            run.append(frame)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return [r for r in runs if len(r) >= 2]


def retarget(source, target, root_motion='KEEP', limbs='FK', plant_feet=False):
    """Returns a receipt: {"action", "paired", "unpaired", "planted", "notes"}."""
    if source is target:
        raise RigError("same_rig")
    action = source.animation_data.action if source.animation_data else None
    if action is None:
        raise RigError("no_action")
    s_roles, s_map, s_facts = role_map(source)
    t_roles, t_map, t_facts = role_map(target)
    pairs = [(role, s_roles[role], t_roles[role]) for role in skeleton_profiles.ROLE_PARENT
             if role in s_roles and role in t_roles]
    if not any(role == "Hips" for role, _, _ in pairs):
        raise RigError("no_hips")
    notes = []
    if plant_feet and not any(l["end"] in (t_roles.get("LeftFoot"), t_roles.get("RightFoot"))
                              for l in ik_controls.limbs(target)):
        ik_controls.add_ik_controls(target)
        notes.append("added IK controls to plant the feet")
    s_rig, t_rig = s_facts["rig"], t_facts["rig"]
    s_joint = {n: (Vector(s_rig.heads[i]), Vector(s_rig.ends[i])) for i, n in enumerate(s_rig.names)}
    t_joint = {n: (Vector(t_rig.heads[i]), Vector(t_rig.ends[i])) for i, n in enumerate(t_rig.names)}
    s_floor, s_height = _floor_and_height(s_rig)
    t_floor, _ = _floor_and_height(t_rig)
    face = _facing_turn(s_map, t_map)
    s_world, t_world = source.matrix_world.to_3x3().normalized(), target.matrix_world.to_3x3().normalized()
    t_inv = target.matrix_world.inverted()
    s_hips, t_hips = s_roles["Hips"], t_roles["Hips"]
    s_hips_rest = source.matrix_world @ source.data.bones[s_hips].head_local
    t_hips_rest = target.matrix_world @ target.data.bones[t_hips].head_local
    scale = (t_hips_rest.z - t_floor) / max(s_hips_rest.z - s_floor, 1e-9)
    align = {}
    for role, s_name, t_name in pairs:
        s_dir = face @ (s_joint[s_name][1] - s_joint[s_name][0])
        t_dir = t_joint[t_name][1] - t_joint[t_name][0]
        turn = t_dir.normalized().rotation_difference(s_dir.normalized()).to_matrix() \
            if t_dir.length and s_dir.length else Matrix.Identity(3)
        s_rest_rot = (s_world @ source.data.bones[s_name].matrix_local.to_3x3()).normalized()
        t_rest_rot = (t_world @ target.data.bones[t_name].matrix_local.to_3x3()).normalized()
        align[role] = (s_rest_rot.inverted(), turn @ t_rest_rot)
    root = top_above(target, t_hips) if root_motion == 'ROOT' else None
    if root_motion == 'ROOT' and root is None:
        raise RigError("no_root")
    baked = bpy.data.actions.new("%s_%s" % (target.name, action.name))
    use_action(target, baked)
    controls = ik_controls.limbs(target)
    feet = {t_roles.get(side + "Foot"): side for side in ("Left", "Right")}
    ik_ends = {l["end"] for l in controls if limbs == 'IK' or (plant_feet and l["end"] in feet)}
    keyed = {t for _, _, t in pairs} | ({root} if root else set())
    scene, current = bpy.context.scene, bpy.context.scene.frame_current
    frame_list = list(frames(action))
    removed, effector_at, anchors_at = {}, {}, {}
    anchors = {target.data.bones[l[k]].parent.name for l in controls for k in ("effector", "pole")
               if l[k] and target.data.bones[l[k]].parent}
    pole_at = {}
    keys = rig_pose_write.Keys(target)
    try:
        for frame in frame_list:
            scene.frame_set(frame)
            desired = {}
            travel = face @ ((source.matrix_world @ source.pose.bones[s_hips].head) - s_hips_rest) * scale
            horizontal = Vector((travel.x, travel.y, 0.0))
            if root_motion in ('IN_PLACE', 'ROOT'):
                travel -= horizontal
            removed[frame] = horizontal if root_motion == 'IN_PLACE' else Vector()
            if root:
                desired[root] = Matrix.Translation(t_inv.to_3x3() @ horizontal) @ target.data.bones[root].matrix_local
            for role, s_name, t_name in pairs:
                s_rest_inv, t_rest_turned = align[role]
                s_pose_rot = (s_world @ source.pose.bones[s_name].matrix.to_3x3()).normalized()
                world_rot = face @ s_pose_rot @ s_rest_inv @ face.inverted() @ t_rest_turned
                local = (t_inv.to_3x3() @ world_rot).to_4x4()
                if role == "Hips":
                    local.translation = t_inv @ (t_hips_rest + travel + (horizontal if root else Vector()))
                    desired[t_name] = local
                else:
                    desired[t_name] = ("rotation", local)
            pose = rig_pose_write.write(target, desired, frame, keyed, keys=keys)
            for limb in controls:
                effector_at.setdefault(limb["effector"], {})[frame] = pose[limb["end"]]
                if limb["pole"]:   # On the side the retargeted limb bends, so IK bends it the same way.
                    last = pole_at.get(limb["pole"], {}).get(frame - 1, pose[limb["pole"]])
                    pole_at.setdefault(limb["pole"], {})[frame] = ik_controls.pole_place(limb, pose, last)
            anchors_at[frame] = {name: pose[name] for name in anchors}
        keys.flush()
        planted = _plant(source, s_roles, s_height, s_floor, frame_list, feet, controls, effector_at, removed,
                         target) if plant_feet else 0
        _key_controls(target, controls, ik_ends, effector_at, anchors_at, frame_list, pole_at)
    finally:
        scene.frame_set(current)
    return {"action": baked, "paired": [r for r, _, _ in pairs],
            "unpaired": sorted(set(t_roles) - {r for r, _, _ in pairs}), "planted": planted, "notes": notes}


def _plant(source, s_roles, s_height, s_floor, frame_list, feet, controls, effector_at, removed, target):
    """Hold each foot effector at one floor place per source contact stretch; returns the stretches planted."""
    t_inv3 = target.matrix_world.inverted().to_3x3()
    planted = 0
    for limb in controls:
        side = feet.get(limb["end"])
        if side is None or ("%sFoot" % side) not in s_roles:
            continue
        at = effector_at[limb["effector"]]
        for run in _contacts(source, s_roles[side + "Foot"], s_height, s_floor, frame_list):
            # In the travelling frame (the clip played in place) the planted foot moves back with the floor.
            held = sum(((at[f].translation + t_inv3 @ removed[f]) for f in run), Vector()) / len(run)
            for f in run:
                m = at[f].copy()
                m.translation = Vector((held.x, held.y, m.translation.z)) - t_inv3 @ removed[f]
                at[f] = m
            planted += 1
    return planted


def _key_controls(target, controls, ik_ends, effector_at, anchors_at, frame_list, pole_at):
    for limb in controls:
        influence = 1.0 if limb["end"] in ik_ends else 0.0
        for constraint in ik_controls.switches(target, limb):
            constraint.influence = influence
            constraint.keyframe_insert("influence", frame=frame_list[0])
        if limb["end"] not in ik_ends:
            continue
        controls_keyed = [limb["effector"]] + ([limb["pole"]] if limb["pole"] in pole_at else [])
        keys = rig_pose_write.Keys(target)
        for frame in frame_list:
            desired = {limb["effector"]: effector_at[limb["effector"]][frame]}
            if limb["pole"] in pole_at:
                desired[limb["pole"]] = pole_at[limb["pole"]][frame]
            for name in controls_keyed:
                parent = target.data.bones[name].parent
                if parent:
                    desired[parent.name] = anchors_at[frame][parent.name]
            rig_pose_write.write(target, desired, frame, keyed=set(controls_keyed), keys=keys)
        keys.flush()
