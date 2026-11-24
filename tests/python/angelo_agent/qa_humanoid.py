# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Procedural humanoid armatures and a walk for the rigging suites (test_blender_retarget, test_blender_ik_controls).

A standing humanoid (feet on the floor, facing -Y) with names, height, arm drop (T or A pose), clavicles, a root and
optional non-deforming controls chosen by the caller, so suites pair rigs that differ the way real rigs do.
"""

import math

import bpy
from mathutils import Euler, Vector

FRAMES = range(1, 41)


def humanoid(name, names, height, arm_drop_degrees, clavicles, root, arm_scale=1.0, controls=False):
    """A standing humanoid (feet on the floor, facing -Y) as an armature; ``names`` maps role -> bone name."""
    s = height / 1.8
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.edit_bones

    def bone(role, head, tail, parent=None, deform=True):
        b = eb.new(names.get(role, role))
        b.head, b.tail = Vector(head) * s, Vector(tail) * s
        b.parent = eb[names.get(parent, parent)] if parent else None
        b.use_connect = False
        b.use_deform = deform
        return b
    if root:
        bone("Root", (0, 0, 0), (0, 0.3, 0))
    bone("Hips", (0, 0, 1.0), (0, 0, 1.15), "Root" if root else None)
    bone("Spine", (0, 0, 1.15), (0, 0, 1.3), "Hips")
    bone("Chest", (0, 0, 1.3), (0, 0, 1.45), "Spine")
    bone("Neck", (0, 0, 1.45), (0, 0, 1.55), "Chest")
    bone("Head", (0, 0, 1.55), (0, 0, 1.78), "Neck")
    drop = math.radians(arm_drop_degrees)
    for side, x in (("Left", 1), ("Right", -1)):
        shoulder = (x * 0.18, 0, 1.42)
        if clavicles:
            bone(side + "Shoulder", (x * 0.03, 0, 1.42), shoulder, "Chest")
        top = side + "Shoulder" if clavicles else "Chest"
        reach = 0.28 * arm_scale
        d = Vector((x * math.cos(drop), 0, -math.sin(drop)))
        elbow = Vector(shoulder) + d * reach
        wrist = elbow + d * reach
        hand_end = wrist + d * 0.08
        bone(side + "UpperArm", shoulder, elbow, top)
        bone(side + "LowerArm", elbow, wrist, side + "UpperArm")
        bone(side + "Hand", wrist, hand_end, side + "LowerArm")
        for k, offset in enumerate((-0.02, 0.0, 0.02)):   # Three digits, so the hand is found by its digits.
            base = hand_end + Vector((0, offset, 0))
            bone("%sDigit%d1" % (side, k), base, base + d * 0.04, side + "Hand")
            bone("%sDigit%d2" % (side, k), base + d * 0.04, base + d * 0.07, "%sDigit%d1" % (side, k))
        hip = (x * 0.1, 0, 1.0)
        bone(side + "UpperLeg", hip, (x * 0.1, 0, 0.55), "Hips")
        bone(side + "LowerLeg", (x * 0.1, 0, 0.55), (x * 0.1, 0, 0.1), side + "UpperLeg")
        bone(side + "Foot", (x * 0.1, 0, 0.1), (x * 0.1, -0.12, 0.02), side + "LowerLeg")
        bone(side + "Toes", (x * 0.1, -0.12, 0.02), (x * 0.1, -0.2, 0.0), side + "Foot")
        if controls:
            bone(side + "FootIK", (x * 0.1, 0.05, 0.0), (x * 0.1, 0.25, 0.0), "Root" if root else None, deform=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    if controls:
        for side in ("Left", "Right"):
            ik = obj.pose.bones[names.get(side + "LowerLeg", side + "LowerLeg")].constraints.new('IK')
            ik.target, ik.subtarget, ik.chain_count = obj, names.get(side + "FootIK", side + "FootIK"), 2
    return obj


def walk(obj, names):
    """A procedural walk: hips forward and bobbing, legs and arms swinging, as one action."""
    action = bpy.data.actions.new(obj.name + "Walk")
    obj.animation_data_create().action = action
    pb = obj.pose.bones
    for f in FRAMES:
        t = 2 * math.pi * (f - 1) / 20
        hips = pb[names["Hips"]]
        hips.location = (0, 0.04 * math.sin(2 * t), -0.6 * (f - 1) / 20)   # bone axes: Y up along the bone
        hips.keyframe_insert("location", frame=f)
        for side, sign in (("Left", 1), ("Right", -1)):
            for role, angle in ((side + "UpperLeg", 0.45 * sign * math.sin(t)),
                                (side + "LowerLeg", 0.35 * max(0.0, -sign * math.sin(t))),
                                (side + "UpperArm", 0.4 * -sign * math.sin(t)),
                                (side + "LowerArm", 0.25)):
                bone = pb[names[role]]
                bone.rotation_mode = 'XYZ'
                bone.rotation_euler = Euler((angle, 0, 0))
                bone.keyframe_insert("rotation_euler", frame=f)
    return action


def world_dir(obj, bone_name):
    pb = obj.pose.bones[bone_name]
    head, tail = obj.matrix_world @ pb.head, obj.matrix_world @ pb.tail
    return (tail - head).normalized(), head, tail


def angle(a, b):
    return math.degrees(a.angle(b)) if a.length and b.length else 180.0


