# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check's pose and axis checks: the rest pose against the profile's, which way the rig faces,
and each mapped bone's primary (Y) and secondary (X) axis. No Blender dependency.

* **Rest pose.** Unity and Godot want a T-pose: arms straight out to the sides, legs straight down, the spine up.
  Each limb is measured as its joints' direction (shoulder joint to elbow, elbow to wrist, hip to ankle), never
  from a bone's roll, so it means the same in every rig. ``T_POSE_DEGREES`` is Angelo's tolerance, not an engine's.
* **Facing.** Blender's forward is -Y; the exporters turn it into each engine's forward (Godot's profile faces +Z).
* **Primary axis.** Godot's profile has each bone's +Y pointing from its joint to the next joint of its chain;
  ``PRIMARY_DEGREES`` is the tolerance.
* **Secondary axis.** In Godot's profile a positive rotation about a bone's +X bends the joint like a contracting
  muscle. Rotating a bone about +X moves its tail toward its +Z, so for the hinge joints, whose bend is the same in
  every humanoid, +Z must point the way the joint bends: the forearm forward, the shin back, a finger toward the
  palm.
"""

import math

import numpy as np

from .skeleton_checks import INFO, PASS, WARN, finding, listed, say
from .skeleton_profiles import FINGER_JOINTS, FINGERS, SIDES, THUMB_JOINTS
from .ui_text import n_

T_POSE_DEGREES = 15.0
PRIMARY_DEGREES = 20.0
FACING_DEGREES = 30.0


def _chain_children():
    out = {"Hips": "Spine", "Spine": "Chest", "Chest": "UpperChest", "UpperChest": "Neck", "Neck": "Head"}
    for side in SIDES:
        out.update({side + "Shoulder": side + "UpperArm", side + "UpperArm": side + "LowerArm",
                    side + "LowerArm": side + "Hand", side + "UpperLeg": side + "LowerLeg",
                    side + "LowerLeg": side + "Foot", side + "Foot": side + "Toes"})
        for a, b in zip(THUMB_JOINTS, THUMB_JOINTS[1:]):
            out[side + "Thumb" + a] = side + "Thumb" + b
        for finger in FINGERS:
            for a, b in zip(FINGER_JOINTS, FINGER_JOINTS[1:]):
                out[side + finger + a] = side + finger + b
    return out


CHAIN_CHILD = _chain_children()
# Hinge joints and where their bend takes the bone's tail: "forward", "back", or "palm" (the hand's palm normal).
BENDS = {}
for _side in SIDES:
    BENDS[_side + "LowerArm"] = "forward"
    BENDS[_side + "LowerLeg"] = "back"
    for _finger in FINGERS:
        for _joint in FINGER_JOINTS:
            BENDS[_side + _finger + _joint] = "palm"


def _angle(a, b):
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-12 or nb < 1e-12:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b) / (na * nb)))))


def _joint(rig, mapping, role):
    bone = mapping.bone(role)
    return None if bone is None else rig.heads[bone]


# The limbs the T-pose is measured on: (label template, from role, to role, wanted direction).
LIMBS = (
    (n_("left upper arm {degrees}°"), "LeftUpperArm", "LeftLowerArm", "left"),
    (n_("left forearm {degrees}°"), "LeftLowerArm", "LeftHand", "left"),
    (n_("right upper arm {degrees}°"), "RightUpperArm", "RightLowerArm", "right"),
    (n_("right forearm {degrees}°"), "RightLowerArm", "RightHand", "right"),
    (n_("left leg {degrees}°"), "LeftUpperLeg", "LeftFoot", "down"),
    (n_("right leg {degrees}°"), "RightUpperLeg", "RightFoot", "down"),
    (n_("spine {degrees}°"), "Hips", "Head", "up"),
)
FIX_POSE = {"UNITY": n_("Pose the rig in a T-pose and apply it as the rest pose with its meshes, or let Unity pose "
                        "the avatar (Rig > Configure > Pose > Enforce T-Pose)"),
            "GODOT": n_("Pose the rig in a T-pose and apply it as the rest pose with its meshes, or let Godot fix it "
                        "on import (Advanced Import Settings > Retarget > Rest Fixer > Fix Silhouette)")}


def rest_pose(rig, mapping, profile):
    source = profile.sources["rest"]
    if profile.rest_pose is None:
        return finding("rest_pose", PASS, say(n_("any rest pose ({engine} aligns poses when it retargets)"),
                                              engine=profile.display), say(n_("not required")), source)
    looked = say(n_("a T-pose: arms out to the sides and legs straight down, within {degrees}°"),
                 degrees="%d" % T_POSE_DEGREES)
    if mapping.left is None:
        return finding("rest_pose", INFO, looked, say(n_("the rig's facing is unknown: not measured")), source)
    want = {"left": mapping.left, "right": -mapping.left, "down": np.array([0.0, 0.0, -1.0]),
            "up": np.array([0.0, 0.0, 1.0])}
    measured, off = [], []
    for label, a, b, direction in LIMBS:
        pa, pb = _joint(rig, mapping, a), _joint(rig, mapping, b)
        angle = None if pa is None or pb is None else _angle(pb - pa, want[direction])
        if angle is None:
            continue
        piece = say(label, degrees="%.0f" % angle)
        measured.append(piece)
        if angle > T_POSE_DEGREES:
            off.append(piece)
    if not measured:
        return finding("rest_pose", INFO, looked, say(n_("no limbs mapped: not measured")), source)
    if not off:
        return finding("rest_pose", PASS, looked, [say(n_("a T-pose"))] + measured, source)
    return finding("rest_pose", WARN, looked, [say(n_("not a T-pose"))] + off, source,
                   say(FIX_POSE[profile.target]))


def facing(mapping, profile):
    looked = say(n_("the character facing -Y, Blender's forward, which the exporter turns into {engine}'s"),  # app-name: Blender's axis convention, which Angelo keeps
                 engine=profile.display)
    if not mapping.facing_measured:
        return finding("facing", INFO, looked, say(n_("the feet show no direction: not measured")), "facing")
    found = say(n_("facing {degrees}° from -Y (by the feet)"),
                degrees="%.0f" % _angle(mapping.forward, np.array([0.0, -1.0, 0.0])))
    if _angle(mapping.forward, np.array([0.0, -1.0, 0.0])) <= FACING_DEGREES:
        return finding("facing", PASS, looked, found, "facing")
    return finding("facing", WARN, looked, found, "facing",
                   say(n_("Rotate the character to face -Y and apply the rotation, so it faces forward in the "
                          "engine")))


AXIS_NOTE = {"fixed_on_import": n_("Godot's Rest Fixer (Overwrite Axis, on once a BoneMap is set) rewrites these on "
                                   "import; with it off, recalculate the rolls (Armature > Bone Roll)"),
             "info": n_("{engine} doesn't require bone axes; animation made for a rig with the same axes retargets "
                        "most directly")}


def bone_axes(rig, mapping, profile):
    looked = say(n_("each bone's +Y toward its chain's next joint (within {degrees}°), and +Z the way its "
                    "hinge bends (forearm forward, shin back, fingers toward the palm)"),
                 degrees="%d" % PRIMARY_DEGREES)
    primary, secondary, measured = [], [], 0
    for role, child in CHAIN_CHILD.items():
        bone, next_joint = mapping.bone(role), _joint(rig, mapping, child)
        if bone is None or next_joint is None:
            continue
        measured += 1
        angle = _angle(rig.axes[bone][:, 1], next_joint - rig.heads[bone])
        if angle is not None and angle > PRIMARY_DEGREES:
            primary.append("%s %.0f" % (rig.names[bone], angle))
    for role, bend in BENDS.items():
        bone = mapping.bone(role)
        if bone is None:
            continue
        side = next(s for s in SIDES if role.startswith(s))
        want = {"forward": mapping.forward, "back": None if mapping.forward is None else -mapping.forward,
                "palm": mapping.palm.get(side)}[bend]
        if want is not None and float(rig.axes[bone][:, 2] @ want) < 0:
            secondary.append(rig.names[bone])
    source = profile.sources["axes"]
    if not measured:
        return finding("bone_axes", INFO, looked, say(n_("no chains mapped: not measured")), source)
    if not primary and not secondary:
        return finding("bone_axes", PASS, looked, say(n_("chain bones measured: {count}, all aligned"),
                                                      count=measured), source)
    found = []
    if primary:
        found.append(say(n_("bones pointing away from the next joint ({count}): {bones}"), count=len(primary),
                         bones=listed(primary)))
    if secondary:
        found.append(say(n_("hinge bones bending toward -Z ({count}): {bones}"), count=len(secondary),
                         bones=listed(secondary)))
    return finding("bone_axes", INFO, looked, found, source, say(AXIS_NOTE[profile.axes], engine=profile.display))
