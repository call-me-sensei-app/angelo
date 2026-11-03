# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The upper body's roles for ``skeleton_roles``: hands by their digits (``hand_rig``), arms, spine, neck, head, eyes
and jaw, by layout. numpy only, no Blender. The rules are stated in ``skeleton_roles``' docstring (steps 3-5).
"""

import numpy as np

from .skeleton_profiles import FINGER_JOINTS, SIDES
from .skeleton_roles import CLAVICLE_REACH, HEAD_TURN, direction, middle_joint, side_of

HAND_OFF_MIDLINE = 0.05   # of the rig's height: a hand's wrist is at least this far from the body's midline


# --- hands, arms, spine and head -------------------------------------------------------------------------------------

def _hand_skeleton(rig, view):
    from . import hand_rig
    index = {b: k for k, b in enumerate(view.bones)}
    rest = np.repeat(np.eye(4)[None], len(view.bones), axis=0)
    for k, b in enumerate(view.bones):
        rest[k, :3, :3] = rig.axes[b]
        rest[k, :3, 3] = rig.heads[b]
    parents = [index.get(view.parent[b], -1) for b in view.bones]
    names = [rig.names[b] for b in view.bones]
    return hand_rig.Skeleton(names, parents, rest, rig.ends[view.bones], rest, rest, [True] * len(names)), index


def hands(rig, view, hips, legs, mapping):
    """[(side index, hand bone, hand_rig.Hand or None)] for up to two hands, outside the legs."""
    from . import hand_rig
    in_legs = set(b for path in legs.values() for i in path for b in view.subtree(i))
    skel, index = _hand_skeleton(rig, view)
    try:
        found = hand_rig.find_hands(skel)
    except hand_rig.HandError as error:
        found = []
        mapping.notes.append("hands: %s" % error)
    try:
        hand_rig.set_palm_normals(found)
    except hand_rig.HandError:
        pass   # No palm normal: the finger-axis check skips that hand.
    centre = rig.heads[hips]
    height = float(np.ptp(rig.heads[view.bones][:, 2]))
    # A hand never sits on the body's midline: a chibi's upper spine, with its short neck and arms, has three
    # "digit chains" too.
    found = [h for h in found if view.bones[h.bone] not in in_legs
             and abs(float((rig.heads[view.bones[h.bone]] - centre) @ mapping.left)) > HAND_OFF_MIDLINE * height]
    hands = {}
    for hand in found:
        bone = view.bones[hand.bone]
        side = side_of(mapping, rig.heads[bone], centre)
        lateral = abs(float((rig.heads[bone] - centre) @ mapping.left))
        if side not in hands or lateral > hands[side][2]:
            hands[side] = (bone, hand, lateral)
    if len(hands) < 2:   # No digits to find a hand by (mittens, a game rig without fingers): the outermost bones.
        rest = [b for b in view.bones if b not in in_legs and b != hips
                and (rig.weighted is None or rig.weighted[b])]   # a hand moves skin; an IK control doesn't
        for side in (0, 1):
            if side in hands:
                continue
            sign = 1.0 if side == 0 else -1.0
            outer = [b for b in rest if sign * float((rig.heads[b] - centre) @ mapping.left) > 0]
            if outer:   # the outermost joint, the wrist: an imported hand's own end is a guess
                bone = max(outer, key=lambda b: sign * float((rig.heads[b] - centre) @ mapping.left))
                hands[side] = (bone, None, 0.0)
                mapping.notes.append("the %s hand has no fingers to find it by: it is the outermost bone, %r"
                                     % (SIDES[side].lower(), rig.names[bone][:64]))
    return [(side, bone, hand) for side, (bone, hand, _) in sorted(hands.items())]


def arms_spine_head(rig, view, hips, legs, hands, mapping):
    in_legs = set(b for path in legs.values() for i in path for b in view.subtree(i))
    upper = [b for b in view.subtree(hips) if b not in in_legs and b != hips]
    if not upper:
        mapping.notes.append("nothing above Hips: no spine")
        return
    spine_path = _trunk(rig, view, hips, in_legs, hands)
    branch = None
    if len(hands) == 2:
        branch = view.lowest_common(hands[0][1], hands[1][1])
        if branch is not None and branch != hips and branch not in view.subtree(hips):
            branch = None
    if branch is not None and branch in spine_path:
        branch = _below_shoulders(rig, view, spine_path, hands, branch)
    if branch is None or branch not in spine_path:
        branch = _branch_by_height(rig, view, spine_path, hands)
        if len(hands) == 2:
            mapping.notes.append("the hands don't hang from the spine in the exported hierarchy: the arms' branch "
                                 "is the spine bone at shoulder height")
    if branch is not None:
        mapping.spine = spine_path[:spine_path.index(branch) + 1]
    else:
        mapping.spine = spine_path[:max(1, len(spine_path) // 2)]
    spine = mapping.spine
    if spine:
        mapping.roles["Spine"] = spine[0]
        if len(spine) >= 2:
            mapping.roles["Chest"] = spine[1]
        if len(spine) >= 3:
            mapping.roles["UpperChest"] = spine[-1]
    _head(rig, view, spine_path, spine, hands, mapping)
    chest = spine[-1] if spine else hips
    mapping.spine_path = spine_path
    for side, hand_bone, hand in hands:
        _arm(rig, view, chest, side, hand_bone, hand, mapping)


def _trunk(rig, view, hips, in_legs, hands):
    """The bones from Hips (exclusive) up through the spine, neck and head: at each bone, the child carrying the most of
    the body (its subtree without the arms and legs), the higher one on a tie. Not toward the highest point: a backpack
    or a hat can reach higher than the head."""
    arms = {b for _, hand, _ in hands for b in [hand] + view.ancestors(hand)} - set(view.ancestors(hips)) - {hips}
    arms -= {b for b in arms if any(h for _, h, _ in hands if b in view.ancestors(h)) and
             all(b in view.ancestors(h) for _, h, _ in hands)}   # shared spine bones above both hands stay
    path, bone = [], hips
    while True:
        kids = [c for c in view.children[bone] if c not in in_legs and c not in arms]
        if not kids:
            return path
        sizes = {c: len([b for b in view.subtree(c) if b not in arms]) for c in kids}
        bone = max(kids, key=lambda c: (sizes[c], rig.ends[c][2]))
        path.append(bone)


def _detached_arm(view, spine_path, hand):
    """The bones above ``hand`` up to the spine, a root, or a bone on the spine path, top first: its arm when the
    hierarchy doesn't connect the hand to the chest."""
    chain, on_spine = [], set(spine_path)
    for bone in view.ancestors(hand):
        if bone in on_spine:
            break
        chain.append(bone)
    return list(reversed(chain))


def _branch_by_height(rig, view, spine_path, hands):
    """The spine bone at the height of the arms' first joints, when the hierarchy doesn't say where they branch."""
    starts = [chain[0] for chain in (_detached_arm(view, spine_path, hand) for _, hand, _ in hands) if chain]
    if not starts or not spine_path:
        return None
    shoulder = float(np.mean([rig.heads[b][2] for b in starts]))
    below = [b for b in spine_path if rig.heads[b][2] <= shoulder]
    return below[-1] if below else None


def _below_shoulders(rig, view, spine_path, hands, branch):
    """The arms' branch, moved down the spine while it starts above the arms' first joints: a 3ds Max Biped hangs
    its clavicles from the neck, which is still the neck."""
    starts = [chain[0] for chain in (_detached_arm(view, spine_path, hand) for _, hand, _ in hands) if chain]
    if not starts:
        return branch
    shoulder = float(np.mean([rig.heads[b][2] for b in starts]))
    index = spine_path.index(branch)
    while index > 0 and rig.heads[spine_path[index]][2] > shoulder:
        index -= 1
    return spine_path[index]


def _head(rig, view, spine_path, spine, hands, mapping):
    above = spine_path[len(spine):]
    if not above:
        mapping.notes.append("no bone above the arms' branch: no neck or head")
        return
    arms = {b for _, hand, _ in hands for b in [hand] + _detached_arm(view, spine_path, hand)}
    # The neck is a run of single links; the head is where it branches (eyes, jaw, face, hair) or ends. A leaf head
    # imported from glTF or FBX has no length to measure, so the run, not the heights, decides.
    near = 0.002 * float(np.ptp(rig.heads[view.bones][:, 2]))

    def continuing(bone):   # a child starting on the bone's own joint organises (Rigify's face), it doesn't continue
        return [c for c in view.children[bone] if c not in arms
                and float(np.linalg.norm(rig.heads[c] - rig.heads[bone])) > near]
    head = above[-1]
    for k, bone in enumerate(above):   # ... or where the next bone turns away from up
        after = above[k + 1] if k + 1 < len(above) else None
        if len(continuing(bone)) != 1 or after is None or direction(rig, after)[2] < HEAD_TURN:
            head = bone
            break
    mapping.roles["Head"] = head
    mapping.neck = above[:above.index(head)]
    if mapping.neck:
        mapping.roles["Neck"] = mapping.neck[0]


def _arm(rig, view, chest, side, hand_bone, hand, mapping):
    name = SIDES[side]
    mapping.roles[name + "Hand"] = hand_bone
    path = view.path(chest, hand_bone)
    if path is not None:   # the arm starts where the spine and neck end (a Biped's clavicles hang from its neck)
        path = [b for b in path if b not in set(mapping.spine_path)]
    if path is None:   # A control rig's deforming arm parented through other bones: its own chain, by layout.
        mapping.notes.append("the %s hand isn't below the chest bone %r: its arm is the deforming chain above it"
                             % (name.lower(), rig.names[chest][:64]))
        path = _detached_arm(view, mapping.spine_path, hand_bone) + [hand_bone]
    arm = path[:-1]
    if not arm:
        mapping.notes.append("the %s hand hangs straight from the chest: no arm bones" % name.lower())
        return
    centre = rig.heads[mapping.roles["Hips"]]
    reach = abs(float((rig.heads[hand_bone] - centre) @ mapping.left))
    first = abs(float((rig.heads[arm[0]] - centre) @ mapping.left))
    if len(arm) >= 2 and reach > 1e-9 and first < CLAVICLE_REACH * reach:
        mapping.roles[name + "Shoulder"] = arm[0]
        arm = arm[1:]
    else:
        clavicle = _loose_clavicle(rig, view, mapping, arm[0], centre, reach, set(path) | set(mapping.spine_path))
        if clavicle is not None:
            mapping.roles[name + "Shoulder"] = clavicle
    mapping.roles[name + "UpperArm"] = arm[0]
    elbow = middle_joint(rig, arm, rig.heads[hand_bone])
    if elbow is not None:
        mapping.roles[name + "LowerArm"] = arm[elbow]
    else:
        mapping.notes.append("the %s arm has no elbow joint between shoulder and wrist" % name.lower())
    if hand is not None:
        _fingers(view, name, hand, mapping)


def _loose_clavicle(rig, view, mapping, upper, centre, reach, taken):
    """A clavicle the hierarchy doesn't put above the upper arm (a control rig's deforming bones): the one deforming
    bone that starts near the midline and ends where the upper arm starts."""
    near = CLAVICLE_REACH * reach
    found = [b for b in view.bones if b not in taken
             and abs(float((rig.heads[b] - centre) @ mapping.left)) < near
             and float(np.linalg.norm(rig.ends[b] - rig.heads[upper])) < near]
    return found[0] if len(found) == 1 else None


def _fingers(view, name, hand, mapping):
    def bone(k):
        return view.bones[k]
    thumb = hand.thumb
    joints = list(thumb.joints)
    if len(joints) < 3 and thumb.metacarpal is not None:
        joints = [thumb.metacarpal] + joints
    for joint, k in zip(("Metacarpal", "Proximal", "Distal"), joints[-3:] if len(joints) >= 3 else joints):
        mapping.roles[name + "Thumb" + joint] = bone(k)
    for digit in hand.fingers:
        finger = {"index": "Index", "middle": "Middle", "ring": "Ring", "little": "Little"}[digit.role]
        for joint, k in zip(FINGER_JOINTS, digit.joints):
            mapping.roles[name + finger + joint] = bone(k)
        if digit.metacarpal is not None:
            mapping.roles[name + finger + "Metacarpal"] = bone(digit.metacarpal)
    if hand.palm_normal is not None:
        mapping.palm[name] = np.asarray(hand.palm_normal, dtype=np.float64)


# --- eyes and jaw ----------------------------------------------------------------------------------------------------

def face(rig, view, mapping, height):
    head = mapping.roles.get("Head")
    if head is None or mapping.forward is None:
        return
    centre, fwd, left = rig.heads[head], mapping.forward, mapping.left
    below = [b for b in view.subtree(head) if b != head]
    tol = 0.02 * height

    def lateral(b):
        return float((rig.heads[b] - centre) @ left)

    def ahead(b):
        return float((rig.heads[b] - centre) @ fwd)
    pairs = []
    for a in below:
        for b in below:
            if (lateral(a) > 0.005 * height and lateral(b) < 0 and abs(lateral(a) + lateral(b)) < tol
                    and abs(rig.heads[a][2] - rig.heads[b][2]) < tol and abs(ahead(a) - ahead(b)) < tol
                    and ahead(a) > 0 and rig.heads[a][2] > centre[2] and not view.children[a]
                    and not view.children[b]):
                pairs.append((a, b))
    if len(pairs) == 1:
        mapping.roles["LeftEye"], mapping.roles["RightEye"] = pairs[0]
    elif len(pairs) > 1:
        mapping.notes.append("%d mirrored bone pairs in the head could be the eyes: left unmapped" % len(pairs))
    jaws = [b for b in view.children[head] if abs(lateral(b)) < 0.01 * height
            and float((rig.ends[b] - rig.heads[b]) @ fwd) > 0 and rig.ends[b][2] < rig.heads[b][2]]
    if len(jaws) == 1:
        mapping.roles["Jaw"] = jaws[0]
