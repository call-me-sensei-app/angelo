# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hand rig math for the RIG tools (``rig_spec``): which bones form a hand, its anatomical joint frames and angles,
joint limits and poses by forward kinematics. numpy only, no Blender. The skin is ``hand_skin``'s.

The rules come from rigging two chibi reference rigs (A and B, 2026-10-21 to 10-24) and are
stated in doc/angelo/hand_rigging.rst:

* A joint's frame comes from the rest joint positions, never from bone roll, so it means the same in every rig. The
  palm normal is perpendicular to the forearm and points out of the palm. It is never the normal of the knuckles'
  plane: their arch tilts that plane by about 20 degrees, and fingers bent about it curl askew.
* A joint rotation is split as the contract of the rigs' skeleton does: flex (curl toward the palm; the thumb toward
  the fingers), spread (toward the thumb side; the thumb toward the palm) and twist (about the bone).
* Every distance the tools judge by is a fraction of the hand's own finger radius, never millimetres of one hand.

Arrays: positions are float64 (n, 3) in armature space; 4x4 matrices are armature space, as Blender's
``Bone.matrix_local`` (rest) and ``PoseBone.matrix`` (posed).
"""

import math

import numpy as np

from . import hand_inherit as I
from .hand_math import (Budget, HandError, axis_angle, euler_matrix, euler_xyz, perpendicular, quaternion,  # noqa: F401
                        rotation_between, rotation_part, unit)


# --- skeleton ---------------------------------------------------------------------------------------------------------

class Skeleton:
    """An armature's bones as arrays: names, parent indices (-1 for none), rest matrices (``Bone.matrix_local``),
    rest tails, pose bases (``PoseBone.matrix_basis``), posed matrices (``PoseBone.matrix``), whether each bone
    deforms (``Bone.use_deform``) and the settings that change how its pose composes (``hand_inherit``):
    ``inherit_rotation``, ``inherit_scale`` (a mode name), ``local_location``, ``connect`` and ``relative_parent``,
    Blender's defaults when not given. Armature space throughout."""

    def __init__(self, names, parents, rest, tails, basis, posed, deform, inherit_rotation=None, inherit_scale=None,
                 local_location=None, connect=None, relative_parent=None):
        self.names = list(names)
        self.index = {name: i for i, name in enumerate(self.names)}
        self.parents = np.asarray(parents, dtype=np.int64)
        self.rest = np.asarray(rest, dtype=np.float64).reshape(-1, 4, 4)
        self.tails = np.asarray(tails, dtype=np.float64).reshape(-1, 3)
        self.basis = np.asarray(basis, dtype=np.float64).reshape(-1, 4, 4)
        self.posed = np.asarray(posed, dtype=np.float64).reshape(-1, 4, 4)
        self.deform = np.asarray(deform, dtype=bool)
        n = len(self.names)

        def flags(values, default):
            return np.asarray([default] * n if values is None else values, dtype=bool)
        self.inherit_rotation = flags(inherit_rotation, True)
        self.inherit_scale = list(inherit_scale) if inherit_scale is not None else ["FULL"] * n
        self.local_location = flags(local_location, True)
        self.connect = flags(connect, False)
        self.relative_parent = flags(relative_parent, False)
        # Per bone, once: the rest rotation, the offset from the parent's rest (Blender's offs_bone) and whether the
        # bone inherits plainly (the common case, composed as one product in hand_inherit).
        self.rest_rotation = np.array([rotation_part(m) for m in self.rest]).reshape(-1, 3, 3)
        self.offsets = np.array([np.linalg.inv(self.rest[p]) @ self.rest[i] if p >= 0 else self.rest[i]
                                 for i, p in enumerate(self.parents)]).reshape(-1, 4, 4)
        self.plain = np.array([bool(r) and mode == "FULL" and bool(loc) and not bool(c) for r, mode, loc, c in zip(
            self.inherit_rotation, self.inherit_scale, self.local_location, self.connect)], dtype=bool)
        self.heads = self.rest[:, :3, 3].copy()
        self.children = [[] for _ in self.names]
        for i, p in enumerate(self.parents):
            if p >= 0:
                self.children[p].append(i)
        # Parents before children (Blender keeps that order already; this does not rely on it).
        order, stack = [], [i for i, p in enumerate(self.parents) if p < 0]
        while stack:
            i = stack.pop()
            order.append(i)
            stack.extend(reversed(self.children[i]))
        self.order = order

    def subtree(self, root):
        out, stack = [], [root]
        while stack:
            i = stack.pop()
            out.append(i)
            stack.extend(self.children[i])
        return out

    def ancestors(self, i):
        out = []
        p = self.parents[i]
        while p >= 0:
            out.append(int(p))
            p = self.parents[p]
        return out


# --- hand detection ---------------------------------------------------------------------------------------------------

ROLES_BY_COUNT = {2: ("index", "little"), 3: ("index", "middle", "little"), 4: ("index", "middle", "ring", "little")}
MIN_CHAINS, MAX_CHAINS = 3, 6
THUMB_ANGLE_MARGIN = math.radians(10.0)   # the thumb's chain points this much further from the hand's axis
THUMB_BASE_MARGIN = 0.15                  # or its base sits this much (hand lengths) nearer the wrist than the next


class Digit:
    """One finger or the thumb: ``joints`` (bone indices, base to tip; each bone's head is a joint), the
    ``metacarpal`` bone that belongs to the palm (or None), its ``role`` and ``label``."""

    def __init__(self, joints, metacarpal, role):
        self.joints, self.metacarpal, self.role = list(joints), metacarpal, role


class Hand:
    """A detected hand: its bone, the forearm (its deforming parent, or None), the digits (thumb first, then the
    fingers from the thumb side), the wrist (the hand bone's head) and the palm normal with where it came from."""

    def __init__(self, skeleton, bone, forearm, thumb, fingers):
        self.skeleton, self.bone, self.forearm = skeleton, bone, forearm
        self.thumb, self.fingers = thumb, fingers
        self.digits = [thumb] + fingers
        self.wrist = skeleton.heads[bone]
        self.palm_normal = None
        self.palm_source = None
        self.notes = []

    @property
    def name(self):
        return self.skeleton.names[self.bone]

    def joint_names(self):
        return [self.skeleton.names[j] for d in self.digits for j in d.joints]

    def bones(self):
        """Every bone of the hand: the hand bone, metacarpals and digit joints."""
        out = {self.bone}
        for d in self.digits:
            out.update(d.joints)
            if d.metacarpal is not None:
                out.add(d.metacarpal)
        return out

    def tip(self, digit):
        return self.skeleton.tails[digit.joints[-1]]

    def axis(self):
        """Unit wrist -> middle finger's knuckle (the central finger's without one): the hand's length direction,
        along which the checklist's knuckle arc is measured (the rigs' arc: index and ring 3 mm, little 8 mm behind
        the middle knuckle along it). A mean of the knuckles tilts toward the arc it should measure."""
        middle = next((f for f in self.fingers if f.role == "middle"), self.fingers[len(self.fingers) // 2])
        return unit(self.skeleton.heads[middle.joints[0]] - self.wrist)

    def plane_normal(self):
        """The hand's own palm plane (its length and the knuckle row), normal toward the palm side. For measuring
        the hand's shape only (the thumb held forward of the palm); joint frames never use it (``joint_frames``)."""
        row = self.skeleton.heads[self.fingers[-1].joints[0]] - self.skeleton.heads[self.fingers[0].joints[0]]
        n = unit(np.cross(row, self.axis()))
        return n if self.palm_normal is None or float(n @ self.palm_normal) >= 0 else -n

    def forearm_axis(self):
        """Unit forearm direction toward the hand: forearm head -> wrist, else the hand's own axis."""
        if self.forearm is None:
            return self.axis()
        return unit(self.wrist - self.skeleton.heads[self.forearm])


def _digit_paths(skel, hand_bone):
    """Bone paths from each child of the hand bone down single-child links, with non-deforming bones dropped.
    A child with several digits below it (a shared palm bone) leads each of those digits."""
    paths = []
    for child in skel.children[hand_bone]:
        path = [child]
        while len(skel.children[path[-1]]) == 1 and len(path) < 6:
            path.append(skel.children[path[-1]][0])
        if len(skel.children[path[-1]]) > 1:
            if len(path) > 1:
                continue   # More than one bone before the digits split: an arm or a spine, not a palm bone.
            shared = path
            for grandchild in skel.children[shared[-1]]:
                sub = [grandchild]
                while len(skel.children[sub[-1]]) == 1 and len(sub) < 6:
                    sub.append(skel.children[sub[-1]][0])
                paths.append((shared, sub))
        else:
            paths.append(([], path))
    out = []
    for shared, path in paths:
        # A digit ends the limb: nothing that deforms branches off it or follows it (a spine reaching the arms, a
        # leg reaching a foot rig, is not a digit).
        if any(skel.deform[d] for d in skel.subtree(path[-1])[1:]) or len(skel.children[path[-1]]) > 1:
            continue
        bones = [b for b in path if skel.deform[b]]
        palm = [b for b in shared if skel.deform[b]]
        if len(bones) == 4:   # A metacarpal and three phalanges.
            palm, bones = palm + bones[:1], bones[1:]
        if 2 <= len(bones) <= 3:
            out.append((palm[-1] if palm else None, bones))
    return out


def find_hands(skel, hand=None, thumb=None):
    """Every hand of the armature (or the named hand bone): bones with 3-6 digit chains of 2-3 deforming bones
    below them. The thumb is the chain pointing furthest from the hand's axis or based nearest the wrist; when
    neither margin is clear, ``thumb`` (a bone of the thumb's chain) must name it."""
    if hand is not None and hand not in skel.index:
        raise HandError("The armature has no bone named %r" % hand[:64])
    candidates = [skel.index[hand]] if hand is not None else range(len(skel.names))
    found = {}
    for h in candidates:
        chains = _digit_paths(skel, h)
        if not MIN_CHAINS <= len(chains) <= MAX_CHAINS:
            if hand is not None:
                raise HandError("Bone %r has %d digit chains of 2-3 deforming bones below it; a hand has 3 to 6"
                                % (skel.names[h][:64], len(chains)))
            continue
        found[h] = chains
    # A forearm whose one child is the hand also has the hand's digits below a single shared bone: that shared bone
    # is the hand, not a palm bone of the forearm.
    def is_hand(bone):
        return bone in found or MIN_CHAINS <= len(_digit_paths(skel, bone)) <= MAX_CHAINS
    above = {h for h, chains in found.items() if any(is_hand(palm) for palm, _ in chains if palm is not None)}
    if hand is not None and above:
        raise HandError("Bone %r is above a hand; name the hand bone itself" % hand[:64])
    hands = []
    for h, chains in found.items():
        if h in above:
            continue
        try:
            hands.append(_classify(skel, h, chains, thumb))
        except HandError:
            if hand is not None:
                raise
            # Unnamed: a bone whose chains are no hand (a skirt, hair, a tail) never refuses the armature's hands.
    return hands


def _classify(skel, h, chains, thumb_name):
    wrist = skel.heads[h]
    tips = [skel.tails[bones[-1]] for _, bones in chains]
    bases = [skel.heads[bones[0]] for _, bones in chains]
    axis = unit(np.mean(tips, axis=0) - wrist)
    length = max(float((t - wrist) @ axis) for t in tips)
    angles = [math.acos(max(-1.0, min(1.0, float(unit(t - b) @ axis)))) for b, t in zip(bases, tips)]
    nearness = [float((b - wrist) @ axis) / length for b in bases]
    if thumb_name is not None:
        if thumb_name not in skel.index:
            raise HandError("The armature has no bone named %r" % thumb_name[:64])
        named = skel.index[thumb_name]
        found = [i for i, (palm, bones) in enumerate(chains) if named in bones or named == palm]
        if not found:
            raise HandError("Bone %r is not in a digit chain of hand %r" % (thumb_name[:64], skel.names[h][:64]))
        t = found[0]
    else:
        by_angle = sorted(range(len(chains)), key=lambda i: -angles[i])
        by_base = sorted(range(len(chains)), key=lambda i: nearness[i])
        if angles[by_angle[0]] - angles[by_angle[1]] >= THUMB_ANGLE_MARGIN:
            t = by_angle[0]
        elif nearness[by_base[1]] - nearness[by_base[0]] >= THUMB_BASE_MARGIN:
            t = by_base[0]
        else:
            raise HandError("Hand %r: no digit chain is clearly the thumb (none points or sits apart from the "
                            "others); give thumb, a bone of the thumb's chain" % skel.names[h][:64])
    fingers = [i for i in range(len(chains)) if i != t]
    if len(fingers) not in ROLES_BY_COUNT:
        raise HandError("Hand %r has %d fingers besides the thumb; the tools measure 2 to 4"
                        % (skel.names[h][:64], len(fingers)))
    # Fingers from the thumb side: along the knuckle row's main direction, the one nearest the thumb first.
    knuckles = np.array([skel.heads[chains[i][1][0]] for i in fingers])
    across = knuckles - knuckles.mean(axis=0)
    across -= np.outer(across @ axis, axis)
    row = np.linalg.svd(across)[2][0] if np.abs(across).sum() > 1e-12 else np.cross(axis, (0.0, 0.0, 1.0))
    thumb_point = skel.heads[chains[t][1][min(1, len(chains[t][1]) - 1)]]
    nearest = min(range(len(fingers)), key=lambda k: float(np.linalg.norm(knuckles[k] - thumb_point)))
    projection = knuckles @ row
    if projection[nearest] > projection.mean():
        projection = -projection
    ordered = [fingers[k] for k in np.argsort(projection, kind="stable")]
    roles = ROLES_BY_COUNT[len(fingers)]
    forearm = next((a for a in skel.ancestors(h) if skel.deform[a]), None)
    thumb = Digit(chains[t][1], chains[t][0], "thumb")
    digits = [Digit(chains[i][1], chains[i][0], role) for i, role in zip(ordered, roles)]
    return Hand(skel, h, forearm, thumb, digits)


# --- palm normal ------------------------------------------------------------------------------------------------------

MIDLINE_MIN = 0.5      # the inward direction keeps at least this much once the forearm's part is removed
CURL_MIN = math.sin(math.radians(4.0))


def curl_direction(hand):
    """Where the fingers' rest curl turns them (perpendicular to the forearm), or None when they lie straight:
    fingers bend toward the palm, so this is the palm side by anatomy, whatever the rest pose."""
    skel, fore = hand.skeleton, hand.forearm_axis()
    total = np.zeros(3)
    for f in hand.fingers:
        first = unit(skel.heads[f.joints[1]] - skel.heads[f.joints[0]])
        last = unit(skel.tails[f.joints[-1]] - skel.heads[f.joints[-1]])
        total += perpendicular(last - first, fore)
    total /= len(hand.fingers)
    return unit(total) if np.linalg.norm(total) >= CURL_MIN else None


def set_palm_normals(hands, given=None):
    """Each hand's palm normal (unit, out of the palm, perpendicular to its forearm) and its source.

    ``given`` maps hand bone name -> a direction (armature space): used as stated. Otherwise the rigs' rule, for a
    rest pose with the arms down and the palms facing the thighs: toward the body's midline (between the two hands
    when there are two, else armature x = 0). When the forearm runs across the body (a T-pose) that direction is
    lost, and the fingers' rest curl gives the palm side. With neither, the hand is refused: give palm_normal."""
    given = given or {}
    if len(hands) >= 2:
        centre = np.mean([h.wrist for h in hands], axis=0)
    else:
        centre = None
    for hand in hands:
        fore = hand.forearm_axis()
        curl = curl_direction(hand)
        if hand.name in given:
            n = perpendicular(np.asarray(given[hand.name], dtype=np.float64), fore)
            if np.linalg.norm(n) < MIDLINE_MIN * np.linalg.norm(given[hand.name]):
                raise HandError("Hand %r: palm_normal runs along the forearm; give a direction across it"
                                % hand.name[:64])
            hand.palm_normal, hand.palm_source = unit(n), "GIVEN"
            continue
        if centre is not None:
            inward = centre - hand.wrist
            inward = np.array([inward[0], 0.0, 0.0]) if abs(inward[0]) > 1e-9 else inward
        else:
            inward = np.array([-math.copysign(1.0, hand.wrist[0]) if abs(hand.wrist[0]) > 1e-9 else 0.0, 0.0, 0.0])
        n = perpendicular(unit(inward) if np.linalg.norm(inward) > 1e-12 else np.zeros(3), fore) \
            if np.linalg.norm(inward) > 1e-12 else np.zeros(3)
        if np.linalg.norm(n) >= MIDLINE_MIN:
            n = unit(n)
            if curl is not None and float(n @ curl) < -0.3:
                hand.notes.append("The fingers curl away from the body's midline: the palm faces outward, so the "
                                  "palm normal follows the curl's side")
                n = -n
            hand.palm_normal, hand.palm_source = n, "MIDLINE"
        elif curl is not None:
            hand.palm_normal, hand.palm_source = curl, "FINGER_CURL"
        else:
            raise HandError("Hand %r: the forearm runs across the body and the fingers lie straight, so the palm "
                            "side is unknown; give palm_normal for this hand (a direction out of the palm)"
                            % hand.name[:64])
    return hands


# --- joint frames and angles ------------------------------------------------------------------------------------------

class Frame:
    """One joint's anatomical axes: the bone's rest direction ``a``, the reference ``r`` (flex turns a toward it),
    the flex axis ``k`` = a x r and the ``sign`` making positive spread go toward the thumb side (the thumb:
    toward the palm)."""

    def __init__(self, a, r, sign):
        self.a, self.r = a, r
        self.k = np.cross(a, r)
        self.sign = sign


def joint_frames(hand):
    """{bone index: Frame} for every digit joint of the hand (the rigs' skeleton contract, ``jointLimits.axes``)."""
    skel, n = hand.skeleton, hand.palm_normal
    finger_joints = [skel.heads[j] for f in hand.fingers for j in f.joints]
    centre = np.mean(finger_joints, axis=0)
    t = hand.thumb.joints
    thumb_mid = (skel.heads[t[0]] + skel.heads[t[1]]) / 2.0
    across = skel.heads[hand.fingers[0].joints[0]] - skel.heads[hand.fingers[-1].joints[0]]
    frames = {}
    for digit in hand.digits:
        joints = digit.joints
        for i, j in enumerate(joints):
            k = i if i < len(joints) - 1 else i - 1
            a = unit(skel.heads[joints[k + 1]] - skel.heads[joints[k]])
            r = n if digit.role != "thumb" else centre - thumb_mid
            r = unit(perpendicular(r, a))
            side = n if digit.role == "thumb" else across
            sign = 1.0 if float(np.cross(r, a) @ side) >= 0 else -1.0
            frames[j] = Frame(a, r, sign)
    return frames


def joint_angles(rel, frame):
    """(flex, spread, twist) in degrees of joint rotation ``rel`` (3x3, relative to the parent, rest frame)."""
    a2 = rel @ frame.a
    flex = math.degrees(math.atan2(float(a2 @ frame.r), float(a2 @ frame.a)))
    spread = frame.sign * math.degrees(math.asin(max(-1.0, min(1.0, -float(a2 @ frame.k)))))
    q = quaternion(rel)
    twist = math.degrees(2.0 * math.atan2(float(q[1:] @ frame.a), q[0]))
    if twist > 180.0:
        twist -= 360.0
    return flex, spread, twist


def joint_rotation(frame, flex, spread, twist):
    """The rotation of a joint at (flex, spread, twist) degrees: twist about a, then the swing of a to its new
    direction."""
    f, s = math.radians(flex), math.radians(spread * frame.sign)
    a2 = (frame.a * math.cos(f) + frame.r * math.sin(f)) * math.cos(s) - frame.k * math.sin(s)
    return rotation_between(frame.a, a2) @ axis_angle(frame.a, math.radians(twist))


# --- joint limits -----------------------------------------------------------------------------------------------------

# Degrees, relative to the parent, from the rest pose; the rigs' skeleton contract (1.2.0), from the human hand's
# active range of motion (doc/angelo/hand_rigging.rst, the checklist's #10 and #14).
DEFAULT_LIMITS = {
    "thumb": ({"flex": (-20, 50), "spread": (-15, 70), "twist": (-30, 30)},
              {"flex": (-10, 60), "spread": (-10, 10), "twist": (-10, 10)},
              {"flex": (-20, 80), "spread": (-5, 5), "twist": (-5, 5)}),
    "index": ({"flex": (-30, 90), "spread": (-15, 20), "twist": (-5, 5)},
              {"flex": (0, 110), "spread": (0, 0), "twist": (0, 0)},
              {"flex": (-10, 90), "spread": (0, 0), "twist": (0, 0)}),
    "middle": ({"flex": (-30, 90), "spread": (-15, 15), "twist": (-5, 5)},
               {"flex": (0, 110), "spread": (0, 0), "twist": (0, 0)},
               {"flex": (-10, 90), "spread": (0, 0), "twist": (0, 0)}),
    "ring": ({"flex": (-30, 95), "spread": (-15, 15), "twist": (-5, 5)},
             {"flex": (0, 110), "spread": (0, 0), "twist": (0, 0)},
             {"flex": (-10, 90), "spread": (0, 0), "twist": (0, 0)}),
    "little": ({"flex": (-30, 100), "spread": (-25, 15), "twist": (-5, 5)},
               {"flex": (0, 110), "spread": (0, 0), "twist": (0, 0)},
               {"flex": (-10, 90), "spread": (0, 0), "twist": (0, 0)}),
}
DIP_RATIO = 0.67           # a finger's end joint follows its middle joint by this share (#11)
LIMIT_TOLERANCE = 0.5      # degrees outside a range that still count as inside (float noise on [0, 0] hinges)
AXES = ("flex", "spread", "twist")


def limits_for(hand, overrides=None):
    """{bone index: {axis: (lo, hi)}}: the defaults by role, with ``overrides`` ({bone name: {axis: [lo, hi]}})
    replacing single axes."""
    overrides = overrides or {}
    out = {}
    for digit in hand.digits:
        for i, j in enumerate(digit.joints):
            out[j] = {axis: tuple(DEFAULT_LIMITS[digit.role][i][axis]) for axis in AXES}
            for axis, span in (overrides.get(hand.skeleton.names[j]) or {}).items():
                lo, hi = float(span[0]), float(span[1])
                if lo > hi:
                    raise HandError("Limit %s of %r has its low end above its high end"
                                    % (axis, hand.skeleton.names[j][:64]))
                out[j][axis] = (lo, hi)
    return out


def clamp(angles, limit):
    return tuple(min(max(v, limit[axis][0]), limit[axis][1]) for v, axis in zip(angles, AXES))


def outside(angles, limit, tolerance=LIMIT_TOLERANCE):
    """The axes of ``angles`` outside ``limit`` by more than ``tolerance`` degrees: {axis: degrees outside}."""
    out = {}
    for v, axis in zip(angles, AXES):
        lo, hi = limit[axis]
        if v < lo - tolerance:
            out[axis] = round(v - lo, 1)
        elif v > hi + tolerance:
            out[axis] = round(v - hi, 1)
    return out


# --- poses ------------------------------------------------------------------------------------------------------------
# Composed as Blender composes them, every inheritance setting honoured (``hand_inherit``).

def current_relative(skel, bone):
    """A bone's current joint rotation relative to its parent, in the rest frame (R B R^-1 under full inheritance)."""
    p = skel.parents[bone]
    if p >= 0 and skel.plain[bone]:
        r = skel.rest_rotation[bone]
        return r @ rotation_part(skel.basis[bone]) @ r.T
    posed = I.pose_matrix(skel, bone, skel.posed[p] if p >= 0 else None, skel.basis[bone])
    return I.relative(skel, bone, posed, skel.posed)


def basis_rotation(skel, bone, rel, posed):
    """The pose-basis rotation (3x3, bone space) that gives joint rotation ``rel`` with its parent posed as in
    ``posed`` (the pose being built: a hinge bone's basis depends on its parent's turn)."""
    p = skel.parents[bone]
    return I.basis_for(skel, bone, rel, posed[p] if p >= 0 else None)


forward_kinematics = I.forward_kinematics
