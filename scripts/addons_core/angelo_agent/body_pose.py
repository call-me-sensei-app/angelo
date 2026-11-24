# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-body posing on any humanoid, by role (doc/angelo/rigging.rst, "Full-body pose").

``pose_body(obj, targets, pin_feet)`` reads the armature's current pose by role (``skeleton_roles.map_roles``), solves
the whole body (``body_ik_solve``: hands, feet, head or hips to world targets, planted feet held) and turns the new
joint positions back into the bones' rotations, starting from the current pose so posing is incremental:

* a bone with one child on the role tree turns by the smallest rotation that points it at its child's new place;
  one with several (the hips, the chest) by the best-fitting rotation of all its children's offsets (Kabsch);
* the hips (the root of the role tree) also move;
* a pinned foot keeps its orientation; a reaching hand and the head turn with the bone they hang from;
* every other bone (fingers, toes, twist bones, controls) keeps its own values and follows its parent.

The result is armature-space matrices for ``rig_pose_write`` (the operator writes them, one Undo step) and the
solver's report: the distance left to each target and whether all were reached. Elbows and knees keep bending the
side they bend at rest (by the rig's facing for a straight limb).

By default the body also keeps to a human's range of motion and its balance (``body_ik_limits``):

* **Limits** by role (``RANGE``): typical active ranges of motion (the American Academy of Orthopaedic Surgeons'
  published values: elbow flexion 150, knee 135, hip flexion 120, neck 45 degrees; the clavicle about 30). Elbows
  and knees count from straight, less the bend they already have at rest; the rest from the rest direction.
* **Balance**: the centre of mass of the body's segments (``MASS``: de Leva 1996, "Adjustments to Zatsiorsky-
  Seluyanov's segment inertia parameters", fractions of the body's mass; a role the rig lacks hands its share to the
  nearest one above it) stays over the planted feet (with their toes), widened by half a foot's breadth (0.055 of
  the height, Winter's anthropometric table). Only when a foot is planted.
"""

import numpy as np

import bpy
from mathutils import Matrix, Vector

from . import body_ik_solve, rig_pose_write, skeleton_profiles
from .game_rig import role_map
from .rig_edit import RigError

TRUNK = ("Hips", "Spine", "Chest", "UpperChest", "Neck", "Head")
ENDS_CARRIED = ("LeftHand", "RightHand", "Head")
# Range of motion in degrees, by the role a link ends at. HINGES count from straight.
RANGE = {"Chest": 40.0, "UpperChest": 30.0, "Neck": 45.0, "Head": 45.0}
for _side in ("Left", "Right"):
    RANGE.update({_side + "Shoulder": 30.0, _side + "UpperArm": 30.0, _side + "LowerArm": 150.0,
                  _side + "Hand": 150.0, _side + "LowerLeg": 120.0, _side + "Foot": 135.0})
HINGES = {side + end: side + mid for side in ("Left", "Right") for mid, end in (("LowerArm", "Hand"), ("LowerLeg", "Foot"))}
# Segment masses, fractions of the body (de Leva 1996), by (from role, to role); a role to itself is a point mass.
MASS = {("Hips", "Spine"): 0.112, ("Spine", "Chest"): 0.163, ("Chest", "UpperChest"): 0.080,
        ("UpperChest", "Neck"): 0.080, ("Head", "Head"): 0.069}
for _side in ("Left", "Right"):
    MASS.update({(_side + "UpperArm", _side + "LowerArm"): 0.027, (_side + "LowerArm", _side + "Hand"): 0.016,
                 (_side + "Hand", _side + "Hand"): 0.006, (_side + "UpperLeg", _side + "LowerLeg"): 0.142,
                 (_side + "LowerLeg", _side + "Foot"): 0.043, (_side + "Foot", _side + "Toes"): 0.014})
FOOT_BREADTH = 0.055   # Of the height.


def _limits(tree, joints):
    """{role: radians} for the roles the rig has: hinges less their rest bend (never below a degree)."""
    import math
    out = {}
    for role, degrees in RANGE.items():
        if role not in tree or tree.get(role) is None:
            continue
        if role in HINGES and HINGES[role] in tree and tree.get(HINGES[role]) is not None:
            mid, base = HINGES[role], tree[HINGES[role]]
            a = Vector(joints[mid]) - Vector(joints[base])
            b = Vector(joints[role]) - Vector(joints[mid])
            bent = math.degrees(a.angle(b)) if a.length > 1e-9 and b.length > 1e-9 else 0.0
            degrees = max(degrees - bent, 1.0)
        out[role] = math.radians(degrees)
    return out


def _segments(tree):
    """(a, b, mass) over the roles the rig has; a missing role's share goes to the nearest present one above it."""
    out = {}
    for (a, b), mass in MASS.items():
        a2, b2 = a, b
        while a2 is not None and a2 not in tree:
            a2 = skeleton_profiles.ROLE_PARENT.get(a2)
        while b2 is not None and b2 not in tree:
            b2 = skeleton_profiles.ROLE_PARENT.get(b2)
        if a2 is None or b2 is None:
            continue
        if b == b2 or b2 == a2:
            key = (a2, b2)
        else:   # The far end is missing: the segment ends where it starts (its mass at the near joint).
            key = (a2, a2)
        out[key] = out.get(key, 0.0) + mass
    return [(a, b, m) for (a, b), m in out.items()]


def _tree(roles):
    """{role: parent role} over the roles the rig has (each hung from its nearest present ancestor role)."""
    out = {}
    for role in roles:
        parent = skeleton_profiles.ROLE_PARENT.get(role)
        while parent is not None and parent not in roles:
            parent = skeleton_profiles.ROLE_PARENT.get(parent)
        out[role] = parent
    return out


def _best_rotation(before, after):
    """The rotation (3x3 Matrix) that best turns the offsets ``before`` onto ``after`` (Kabsch, by SVD)."""
    a, b = np.array([tuple(v) for v in before]), np.array([tuple(v) for v in after])
    u, _, vt = np.linalg.svd(a.T @ b)
    d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    r = vt.T @ np.diag((1.0, 1.0, d)) @ u.T
    return Matrix(r.tolist())


def pose_body(obj, targets, pin_feet=True, iterations=160, limits=True, balance=True, patience=8):
    """``targets``: {role: world (x, y, z)} for any of Hips, Head, LeftHand, RightHand, LeftFoot, RightFoot.

    Returns {"desired": {bone: matrix or ("rotation", matrix)}, "errors": {role: distance}, "reached": bool}."""
    roles, mapping, _ = role_map(obj)
    unknown = [r for r in targets if r not in roles]
    if unknown:
        raise RigError("no_role", *unknown)
    bpy.context.view_layer.update()
    world = obj.matrix_world
    current = {role: world @ obj.pose.bones[bone].head for role, bone in roles.items()}
    body = {r: b for r, b in roles.items() if r != "Root"}   # The root bone carries travel, not the body.
    tree = {r: p for r, p in _tree(body).items() if r in skeleton_profiles.ROLE_PARENT}
    joints = {r: tuple(current[r]) for r in tree}
    rest = {r: tuple(world @ obj.data.bones[roles[r]].head_local) for r in tree}   # Ranges count from rest.
    pinned = [r for r in ("LeftFoot", "RightFoot") if pin_feet and r in tree and r not in targets]
    forward = Vector(mapping.forward) if mapping.forward is not None else Vector((0.0, -1.0, 0.0))
    hinges = {}
    for side in ("Left", "Right"):
        for base, mid, end, sign in (("UpperLeg", "LowerLeg", "Foot", 1.0), ("UpperArm", "LowerArm", "Hand", -1.0)):
            names = [side + base, side + mid, side + end]
            if all(n in tree for n in names):
                axis = current[names[2]] - current[names[0]]
                hinges[names[1]] = (names[0], names[2], tuple(axis.cross(forward * sign)))
    height = max(obj.dimensions.z, 0.1)
    support = pinned + [side + "Toes" for side in ("Left", "Right") if side + "Foot" in pinned and side + "Toes" in tree]
    out = body_ik_solve.solve(joints, tree, {r: tuple(v) for r, v in targets.items()}, pinned, hinges, iterations,
                              tolerance=1e-3 * height, limits=_limits(tree, rest) if limits else None, rest=rest,
                              balance={"segments": _segments(tree), "support": support, "up": (0.0, 0.0, 1.0),
                                       "radius": FOOT_BREADTH * height / 2.0} if balance and pinned else None,
                              patience=patience)
    new = {r: Vector(p) for r, p in out["positions"].items()}
    children = {}
    for r, p in tree.items():
        children.setdefault(p, []).append(r)
    turn = {}
    for role in sorted(tree, key=lambda r: len(_chain(tree, r))):
        kids = children.get(role, [])
        if role in ENDS_CARRIED and tree.get(role) in turn:   # Fingers or eyes below don't steer a hand or head.
            turn[role] = turn[tree[role]]
        elif kids:
            before = [current[k] - current[role] for k in kids]
            after = [new[k] - new[role] for k in kids]
            turn[role] = (before[0].rotation_difference(after[0]).to_matrix() if len(kids) == 1
                          else _best_rotation(before, after))
        else:
            turn[role] = Matrix.Identity(3)
    to_arm = world.inverted()
    desired = {}
    for role, rot in turn.items():
        bone = roles[role]
        posed = obj.pose.bones[bone].matrix
        rot_arm = to_arm.to_3x3().normalized() @ rot @ world.to_3x3().normalized()
        m = (rot_arm @ posed.to_3x3()).to_4x4()
        if tree.get(role) is None:
            m.translation = to_arm @ new[role]
            desired[bone] = m
        else:
            desired[bone] = ("rotation", m)
    return {"desired": desired, "errors": out["errors"], "reached": out["reached"], "iterations": out["iterations"],
            "limited": out.get("limited", []), "balance": out.get("balance")}


def _chain(tree, role):
    out = []
    while role is not None:
        out.append(role)
        role = tree.get(role)
    return out


def apply(obj, solved, frame=None):
    """Write the solved pose (and key it at ``frame``); only the role bones' values change."""
    return rig_pose_write.write(obj, solved["desired"], frame)
