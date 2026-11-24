# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Root motion from the hips (doc/angelo/rigging.rst, "Root motion").

``extract_root_motion(armature, mode, yaw)``, for every action the armature plays (each with its NLA off):

* ``ROOT``: the hips' travel along the floor moves to the bone above the hips (the root), and with ``yaw`` their turn
  about the vertical too (a turning clip turns the root, as Unreal and Godot read it). The root's other children keep
  their place in the world, so foot effectors hanging from the root stay planted; the body doesn't move.
* ``IN_PLACE``: the travel (and with ``yaw`` the turn) is taken out of the hips; the root, if any, is left alone.

The hips' rest place is the reference, so a clip that starts away from the origin keeps that offset on the root, and
running it again changes nothing. Bones are written in their own rotation modes (``rig_pose_write``).
"""

import math

import bpy
from mathutils import Matrix, Vector

from . import rig_pose_write
from .game_rig import role_map
from .rig_edit import RigError, frames, nla_off, played_actions, top_above, use_action


def _yaw(matrix, rest, forward):
    """The turn about world Z of ``matrix`` from ``rest`` (both world 3x3), measured on ``forward``."""
    turned = matrix @ rest.inverted() @ forward
    if Vector((turned.x, turned.y)).length < 1e-6:
        return 0.0
    return math.atan2(turned.y, turned.x) - math.atan2(forward.y, forward.x)


def extract_root_motion(armature, mode='ROOT', yaw=False):
    """Returns {"actions": [names], "root": root bone or None}."""
    roles, mapping, _ = role_map(armature)
    if "Hips" not in roles:
        raise RigError("no_hips")
    hips = roles["Hips"]
    root = top_above(armature, hips) if mode == 'ROOT' else None
    if mode == 'ROOT' and root is None:
        raise RigError("no_root")
    forward = Vector(mapping.forward) if mapping.forward is not None else Vector((0.0, -1.0, 0.0))
    forward.z = 0.0
    forward = forward.normalized() if forward.length else Vector((0.0, -1.0, 0.0))
    world = armature.matrix_world
    to_arm = world.inverted()
    hips_rest = world @ armature.data.bones[hips].matrix_local
    root_children = [b.name for b in armature.data.bones[root].children] if root else []
    hips_parent = armature.data.bones[hips].parent
    sampled = [hips] + root_children + ([hips_parent.name] if hips_parent and not root else [])
    scene, current = bpy.context.scene, bpy.context.scene.frame_current
    ad = armature.animation_data
    held = ad.action if ad else None
    done = []
    try:
        with nla_off(armature):
            for action in played_actions(armature):
                use_action(armature, action)
                samples = {}
                for frame in frames(action):
                    scene.frame_set(frame)
                    samples[frame] = {name: armature.pose.bones[name].matrix.copy()
                                      for name in sampled}
                keys = rig_pose_write.Keys(armature)
                for frame, pose in samples.items():
                    hips_world = world @ pose[hips]
                    travel = hips_world.translation - hips_rest.translation
                    angle = _yaw(hips_world.to_3x3(), hips_rest.to_3x3(), forward) if yaw else 0.0
                    pivot = Vector((hips_rest.translation.x, hips_rest.translation.y, 0.0))
                    move = (Matrix.Translation(Vector((travel.x, travel.y, 0.0)) + pivot)
                            @ Matrix.Rotation(angle, 4, 'Z') @ Matrix.Translation(-pivot))
                    move_arm = to_arm @ move @ world
                    if mode == 'ROOT':
                        rest_root = armature.data.bones[root].matrix_local
                        desired = {root: move_arm @ rest_root}
                        desired.update({name: pose[name] for name in root_children})
                        rig_pose_write.write(armature, desired, frame, keys=keys)
                    else:
                        desired = {hips: move_arm.inverted() @ pose[hips]}
                        if hips_parent:   # The parent as it was on this frame, so the hips' local value is right.
                            desired[hips_parent.name] = pose[hips_parent.name]
                        rig_pose_write.write(armature, desired, frame, keyed={hips}, keys=keys)
                keys.flush()
                done.append(action.name)
    finally:
        if ad:
            ad.action = held
        scene.frame_set(current)
    return {"actions": done, "root": root}
