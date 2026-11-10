# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Humanoid rig hierarchies for Blender suites, rebuilt from published naming (no third-party file is used).

UE5 Mannequin naming (the repo fixture skeleton_engine_e2e/make_rigs.ue_layout), Mixamo's ``mixamorig:`` joints,
VRM 1.0 under VRoid's ``Root`` (``J_Bip_*``) and an Auto-Rig Pro-style control hierarchy (approximate, from its
documented names). Used as held-out rigs by test_blender_program_bone_depth and test_blender_action_revision_rigs.
"""

import bpy


def layout_rig(name, layout, window=None):
    """An armature object named ``name`` with ``layout``'s (bone, parent) hierarchy, linked to the window's scene."""
    window = window or bpy.context.window_manager.windows[0]
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    window.scene.collection.objects.link(obj)
    window.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    for index, (bone, parent) in enumerate(layout):
        edit = data.edit_bones.new(bone)
        edit.head, edit.tail = (index * 0.01, 0, 0), (index * 0.01, 0, 0.1)
        if parent:
            edit.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    window.view_layer.update()
    return obj


def chain(length):
    return [("C%d" % index, "C%d" % (index - 1) if index else None) for index in range(length)]


def ue5():
    """skeleton_engine_e2e/make_rigs.ue_layout's hierarchy (names and parents)."""
    b = [("root", None), ("pelvis", "root")]
    parent = "pelvis"
    for k in range(1, 6):
        b.append(("spine_%02d" % k, parent))
        parent = "spine_%02d" % k
    b += [("neck_01", "spine_05"), ("neck_02", "neck_01"), ("head", "neck_02")]
    for s in ("l", "r"):
        b += [("clavicle_" + s, "spine_05"), ("upperarm_" + s, "clavicle_" + s), ("lowerarm_" + s, "upperarm_" + s),
              ("hand_" + s, "lowerarm_" + s)]
        for finger in ("index", "middle", "ring", "pinky"):
            b.append(("%s_metacarpal_%s" % (finger, s), "hand_" + s))
            previous = "%s_metacarpal_%s" % (finger, s)
            for k in range(3):
                b.append(("%s_%02d_%s" % (finger, k + 1, s), previous))
                previous = "%s_%02d_%s" % (finger, k + 1, s)
        previous = "hand_" + s
        for k in range(3):
            b.append(("thumb_%02d_%s" % (k + 1, s), previous))
            previous = "thumb_%02d_%s" % (k + 1, s)
        b += [("thigh_" + s, "pelvis"), ("calf_" + s, "thigh_" + s), ("foot_" + s, "calf_" + s), ("ball_" + s, "foot_" + s)]
    return b


def mixamo():
    p = "mixamorig:"
    b = [(p + "Hips", None), (p + "Spine", p + "Hips"), (p + "Spine1", p + "Spine"), (p + "Spine2", p + "Spine1"),
         (p + "Neck", p + "Spine2"), (p + "Head", p + "Neck"), (p + "HeadTop_End", p + "Head")]
    for s in ("Left", "Right"):
        b += [(p + s + "Shoulder", p + "Spine2"), (p + s + "Arm", p + s + "Shoulder"), (p + s + "ForeArm", p + s + "Arm"),
              (p + s + "Hand", p + s + "ForeArm")]
        for finger in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
            previous = p + s + "Hand"
            for k in range(1, 5):
                b.append((p + s + "Hand" + finger + str(k), previous))
                previous = p + s + "Hand" + finger + str(k)
        b += [(p + s + "UpLeg", p + "Hips"), (p + s + "Leg", p + s + "UpLeg"), (p + s + "Foot", p + s + "Leg"),
              (p + s + "ToeBase", p + s + "Foot"), (p + s + "Toe_End", p + s + "ToeBase")]
    return b


def vrm():
    b = [("Root", None), ("J_Bip_C_Hips", "Root"), ("J_Bip_C_Spine", "J_Bip_C_Hips"), ("J_Bip_C_Chest", "J_Bip_C_Spine"),
         ("J_Bip_C_UpperChest", "J_Bip_C_Chest"), ("J_Bip_C_Neck", "J_Bip_C_UpperChest"),
         ("J_Bip_C_Head", "J_Bip_C_Neck")]
    for s in ("L", "R"):
        b += [("J_Bip_%s_Shoulder" % s, "J_Bip_C_UpperChest"), ("J_Bip_%s_UpperArm" % s, "J_Bip_%s_Shoulder" % s),
              ("J_Bip_%s_LowerArm" % s, "J_Bip_%s_UpperArm" % s), ("J_Bip_%s_Hand" % s, "J_Bip_%s_LowerArm" % s)]
        for finger in ("Thumb", "Index", "Middle", "Ring", "Little"):
            previous = "J_Bip_%s_Hand" % s
            for k in range(1, 4):
                b.append(("J_Bip_%s_%s%d" % (s, finger, k), previous))
                previous = "J_Bip_%s_%s%d" % (s, finger, k)
        b += [("J_Bip_%s_UpperLeg" % s, "J_Bip_C_Hips"), ("J_Bip_%s_LowerLeg" % s, "J_Bip_%s_UpperLeg" % s),
              ("J_Bip_%s_Foot" % s, "J_Bip_%s_LowerLeg" % s)]
    return b


def arp():
    b = [("c_traj", None), ("c_root_master.x", "c_traj"), ("c_root.x", "c_root_master.x"),
         ("c_spine_01.x", "c_root.x"), ("c_spine_02.x", "c_spine_01.x"), ("c_spine_03.x", "c_spine_02.x")]
    for s in ("l", "r"):
        b += [("c_shoulder.%s" % s, "c_spine_03.x"), ("c_arm_fk.%s" % s, "c_shoulder.%s" % s),
              ("c_forearm_fk.%s" % s, "c_arm_fk.%s" % s), ("c_hand_fk.%s" % s, "c_forearm_fk.%s" % s)]
        for finger in ("index", "middle", "ring", "pinky"):
            b.append(("c_%s1_base.%s" % (finger, s), "c_hand_fk.%s" % s))
            previous = "c_%s1_base.%s" % (finger, s)
            for k in range(1, 4):
                b.append(("c_%s%d.%s" % (finger, k, s), previous))
                previous = "c_%s%d.%s" % (finger, k, s)
    return b


def depth(bone):
    count = 1
    while bone.parent is not None:
        bone, count = bone.parent, count + 1
    return count
