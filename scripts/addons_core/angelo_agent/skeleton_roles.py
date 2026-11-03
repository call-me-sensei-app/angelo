# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which bone of a rig plays each humanoid role (``skeleton_profiles.ROLES``), found from the rig's layout. numpy only,
no Blender.

Names are never read: Rigify, Mixamo, Auto-Rig Pro, a game engine's mannequin and a hand-made rig name the same bone
differently, and an engine profile is judged on which bone *is* the thigh, not on what it is called. The mapping
works in world space, Z up, on the bones that deform (all bones when none does), each parented to its nearest
deforming ancestor (the hierarchy a deform-only glTF export writes), with unweighted end bones left out:

1. **Legs.** The bones that reach the floor fall into two groups across the body; each group's deepest bone ends a
   leg. Hips is the lowest bone both legs hang from, at hip height (else the bone nearest the legs' tops). Down each leg: the upper leg is the first bone that points
   down, the foot the first bone in the lowest quarter of the leg, the toes the bone after it, and the knee the joint
   nearest half way along the leg (twist segments sit at a quarter and three quarters).
2. **Facing.** Forward is the feet's direction from ankle to toe joint (the joints the file holds); left is up x
   forward. A rig whose feet don't show a direction (no feet, feet pointing down) faces -Y, Blender's convention,
   and says so: an imported armature's own rotation is often only the file format's axes.
3. **Hands** (``skeleton_upper``) are found by their digits (``hand_rig.find_hands``: thumb and fingers by layout); without them, by the
   outermost bone on each side. The arms branch from the lowest bone both hands hang from (the upper chest). An arm
   whose first bone starts near the body's midline starts with a shoulder (clavicle); the elbow is the joint nearest
   half way from the shoulder joint to the wrist.
4. **Spine and head.** The bones from Hips to the arms' branch (moved down while it starts above the shoulder joints:
   a Biped's clavicles hang from its neck) are the spine (Spine, Chest, UpperChest); above it, toward the rig's
   highest point, the neck is the run of single links and the Head the bone where it branches (eyes, jaw, face) or
   ends.
5. **Eyes and jaw** (optional): a mirrored pair below the head, forward of its joint; a midline child of the head
   pointing forward and down.

Every guess the layout can't settle is a note in the result, never silence. doc/angelo/engine_skeleton.rst.
"""

import math
import re

import numpy as np

from .skeleton_profiles import SIDES

UP = np.array([0.0, 0.0, 1.0])
LOW_BAND = 0.1        # of the rig's height: bones that reach this near the floor are feet and toes
FOOT_ZONE = 0.25      # of the leg's height: the foot starts in the leg's lowest quarter
DOWN = -0.5           # a bone pointing down has a direction z below this
HIP_HEIGHT = 0.6      # of the hip joints' height: the bone both legs hang from starts at least this high
LEG_SEGMENT = 0.25    # of its joint's height above the floor: a thigh or shin is at least this long
LEG_DOWN = -0.75      # a thigh or shin points down this steeply (a foot sloping to the ball does not)
FOOT_FORWARD = 0.3    # a foot points forward when this share of its length is horizontal
HEAD_TURN = 0.2       # a bone on the way up from the neck that points up less than this is no neck or head
CLAVICLE_REACH = 0.15  # of the arm's sideways reach: an arm's first bone starting nearer the midline is a clavicle
END_NAME = re.compile(r"(?:[._ -]?end)$", re.IGNORECASE)   # Blender's FBX leaf bones (_end) and similar end bones


class Rig:
    """A rig as plain arrays, world space: ``names``; ``parents`` (index, -1 for none); ``heads``/``tails`` (n, 3);
    ``axes`` (n, 3, 3), each bone's rest rotation whose columns are its X, Y (along the bone) and Z axes; ``deform``;
    ``weighted`` (bool per bone: some vertex has its weight, or None when no mesh is skinned to the rig);
    and ``origin``, the armature object's location, in world space."""

    def __init__(self, names, parents, heads, tails, axes, deform, weighted=None, origin=(0.0, 0.0, 0.0)):
        self.names = list(names)
        self.parents = np.asarray(parents, dtype=np.int64).reshape(-1)
        self.heads = np.asarray(heads, dtype=np.float64).reshape(-1, 3)
        self.tails = np.asarray(tails, dtype=np.float64).reshape(-1, 3)
        self.ends = self.tails   # set by map_roles: each bone's end as a joint (joint_ends)
        self.axes = np.asarray(axes, dtype=np.float64).reshape(-1, 3, 3)
        self.deform = np.asarray(deform, dtype=bool).reshape(-1)
        self.weighted = None if weighted is None else np.asarray(weighted, dtype=bool).reshape(-1)
        self.origin = np.asarray(origin, dtype=np.float64)
        self.children = [[] for _ in self.names]
        for i, p in enumerate(self.parents):
            if p >= 0:
                self.children[p].append(i)

    def ancestors(self, i):
        out, p = [], self.parents[i]
        while p >= 0:
            out.append(int(p))
            p = self.parents[p]
        return out

    def is_end_bone(self, i):
        """A leaf that moves no vertex: weights say so when a mesh is skinned, else an end-bone name (``_end``)."""
        if self.children[i]:
            return False
        if self.weighted is not None:
            return not bool(self.weighted[i])
        return bool(END_NAME.search(self.names[i]))


class View:
    """A subset of a rig's bones, each parented to its nearest ancestor in the subset."""

    def __init__(self, rig, keep):
        self.rig = rig
        self.bones = [i for i in range(len(rig.names)) if keep[i]]
        kept = set(self.bones)
        self.parent = {i: next((a for a in rig.ancestors(i) if a in kept), -1) for i in self.bones}
        self.children = {i: [] for i in self.bones}
        for i, p in self.parent.items():
            if p >= 0:
                self.children[p].append(i)

    def ancestors(self, i):
        out, p = [], self.parent[i]
        while p >= 0:
            out.append(p)
            p = self.parent[p]
        return out

    def depth(self, i):
        return len(self.ancestors(i))

    def path(self, top, bottom):
        """Bones from ``top`` (exclusive) down to ``bottom`` (inclusive), or None when ``bottom`` isn't below it."""
        chain = [bottom] + self.ancestors(bottom)
        if top not in chain:
            return None
        return list(reversed(chain[:chain.index(top)]))

    def lowest_common(self, a, b):
        above_a = [a] + self.ancestors(a)
        return next((x for x in [b] + self.ancestors(b) if x in above_a), None)

    def subtree(self, i):
        out, stack = [], [i]
        while stack:
            j = stack.pop()
            out.append(j)
            stack.extend(self.children[j])
        return out


class Mapping:
    """``roles`` role -> bone index; ``spine`` the bones from Hips (exclusive) to the arms' branch (inclusive); ``neck``
    the bones between the branch and the head; ``forward``/``left`` unit world vectors; ``notes`` what the layout
    could not settle; ``view`` the bones the mapping chose among."""

    def __init__(self, view):
        self.view, self.roles, self.spine, self.neck, self.notes = view, {}, [], [], []
        self.spine_path = []   # Hips (exclusive) to the highest bone: the spine, neck and head
        self.forward = self.left = None
        self.facing_measured = False   # forward came from the feet, not from the armature's -Y
        self.palm = {}   # side -> unit palm normal (hand_rig), for the finger-axis check

    def bone(self, role):
        return self.roles.get(role)


def _unit(v, fallback=None):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 and math.isfinite(n) else fallback


def direction(rig, i):
    return _unit(rig.ends[i] - rig.heads[i], np.zeros(3))


def joint_ends(rig, view):
    """Where each bone ends as a joint: its children's joints (their mean when it has several), else its tail, no
    longer than twice its parent's joint-to-joint length. An imported skeleton (glTF, FBX without automatic bone
    orientation, 3ds Max Biped) stores no bone ends, so Blender's tails for it are guesses that can point anywhere and
    run metres long; joints are what the file holds."""
    ends = rig.tails.copy()
    if not view.bones:
        return ends
    heads = rig.heads[view.bones]
    near = max(0.002 * float(np.ptp(heads, axis=0).max()), 1e-9)   # a child on the bone's own joint says nothing
    for i in view.bones:
        kids = [c for c in view.children[i] if float(np.linalg.norm(rig.heads[c] - rig.heads[i])) > near]
        if kids:   # the child that carries the chain on (a VRM thigh's skirt bones are leaves); fingers tie
            sizes = {c: len(view.subtree(c)) for c in kids}
            kids = [c for c in kids if sizes[c] == max(sizes.values())]
            end = np.mean(rig.heads[kids], axis=0)
            if float(np.linalg.norm(end - rig.heads[i])) > near:
                ends[i] = end
                continue
        parent = view.parent[i]
        if parent >= 0:
            limit = 2.0 * float(np.linalg.norm(rig.heads[i] - rig.heads[parent]))
            segment = rig.tails[i] - rig.heads[i]
            length = float(np.linalg.norm(segment))
            if limit > near and length > limit:
                ends[i] = rig.heads[i] + segment * (limit / length)
    return ends


def map_roles(rig):
    """The rig's ``Mapping``. Never raises for an odd rig: what can't be found is missing, with a note."""
    keep = rig.deform.copy() if rig.deform.any() else np.ones(len(rig.names), dtype=bool)
    lengths = np.linalg.norm(rig.tails - rig.heads, axis=1)
    for i in range(len(rig.names)):
        if keep[i] and (rig.is_end_bone(i) or lengths[i] < 1e-9):
            keep[i] = False
    view = View(rig, keep)
    rig.ends = joint_ends(rig, view)
    mapping = Mapping(view)
    if len(view.bones) < 3:
        mapping.notes.append("fewer than 3 deforming bones: no humanoid layout to map")
        return mapping
    points = np.concatenate([rig.heads[view.bones], rig.ends[view.bones]])
    floor, top = float(points[:, 2].min()), float(points[:, 2].max())
    height = top - floor
    if height <= 1e-9:
        mapping.notes.append("the rig is flat (no height): no humanoid layout to map")
        return mapping
    legs = _legs(rig, view, floor, height, mapping)
    hips = _hips(rig, view, legs, mapping, floor)
    _facing(rig, view, legs, mapping, floor)
    if hips is None:
        return mapping
    mapping.roles["Hips"] = hips
    from . import skeleton_upper
    hands = skeleton_upper.hands(rig, view, hips, legs, mapping)
    skeleton_upper.arms_spine_head(rig, view, hips, legs, hands, mapping)
    for path in legs.values():
        _leg_roles(rig, hips, path, floor, mapping)
    skeleton_upper.face(rig, view, mapping, height)
    return mapping


# --- legs and hips ---------------------------------------------------------------------------------------------------

def _long_down(rig, i, floor):
    """A thigh or a shin: a bone pointing down that covers at least ``LEG_SEGMENT`` of its joint's height above the
    floor, joint to joint (relative to its own drop, so a chibi's short legs count)."""
    drop = float(rig.heads[i][2]) - floor
    return (direction(rig, i)[2] < LEG_DOWN and drop > 0
            and float(np.linalg.norm(rig.ends[i] - rig.heads[i])) >= LEG_SEGMENT * drop)


def _two_groups(rig, bones, height):
    """The bones split into two groups across the floor plane (two-means from the farthest pair), or None."""
    if len(bones) < 2:
        return None
    flat = np.array([rig.ends[i][:2] if rig.ends[i][2] <= rig.heads[i][2] else rig.heads[i][:2] for i in bones])
    distances = np.linalg.norm(flat[:, None, :] - flat[None, :, :], axis=2)
    a, b = np.unravel_index(int(np.argmax(distances)), distances.shape)
    if distances[a, b] < 0.02 * height:
        return None
    centres = flat[[a, b]].copy()
    for _ in range(8):
        group = np.argmin(np.linalg.norm(flat[:, None, :] - centres[None, :, :], axis=2), axis=1)
        if len(set(group.tolist())) < 2:
            return None
        centres = np.array([flat[group == k].mean(axis=0) for k in (0, 1)])
    return [[bones[j] for j in range(len(bones)) if group[j] == k] for k in (0, 1)]


def _by_leg(rig, view, bones, floor):
    """The two groups of ``bones`` that hang from the same topmost thigh or shin, the two reaching lowest; None
    without two. The hierarchy, not positions, so feet set close together never split toes from shins."""
    groups = {}
    for i in bones:
        tops = [a for a in [i] + view.ancestors(i) if _long_down(rig, a, floor)]
        if tops:
            groups.setdefault(tops[-1], []).append(i)
    if len(groups) < 2:
        return None
    ranked = sorted(groups.values(), key=lambda g: min(min(rig.heads[i][2], rig.ends[i][2]) for i in g))
    return ranked[:2]


def _chain_end(view, i):
    while len(view.children[i]) == 1:
        i = view.children[i][0]
    return i


def _legs(rig, view, floor, height, mapping):
    """{0: bones, 1: bones}: each leg as its deepest bone's chain (root first, in the view), unsided; {} when the rig
    has no two legs. A leg's lowest bones reach the floor and hang from a thigh or shin; floor bones that hang from
    none (IK foot controls under the root) are no leg's, and the legs are then the lowest down-pointing chains."""
    low = [i for i in view.bones if min(rig.heads[i][2], rig.ends[i][2]) <= floor + LOW_BAND * height]
    carried = [i for i in low if any(_long_down(rig, a, floor) for a in [i] + view.ancestors(i))]
    groups = _by_leg(rig, view, carried, floor) or _two_groups(rig, carried, height)
    if groups is None:
        ends = list(dict.fromkeys(_chain_end(view, i) for i in view.bones if _long_down(rig, i, floor)))
        groups = _two_groups(rig, ends, height)
        if groups is not None:
            mapping.notes.append("the bones at the floor hang from no leg (foot controls?): the legs are the lowest "
                                 "chains pointing down, and they end above the floor")
    if groups is None:
        mapping.notes.append("no two legs reach toward the floor: no legs found")
        return {}
    legs = {}
    for k, members in enumerate(groups):
        top = rig.heads[([members[0]] + view.ancestors(members[0]))[-1]]   # toes reach farther than a heel
        end = max(members, key=lambda i: (view.depth(i), _horizontal(rig.ends[i] - top)))
        legs[k] = list(reversed([end] + view.ancestors(end)))
    return legs


def _hips(rig, view, legs, mapping, floor):
    if len(legs) != 2:
        return None
    above = view.lowest_common(legs[0][-1], legs[1][-1])
    if above is not None:
        below = [legs[k][legs[k].index(above) + 1:] for k in (0, 1)]
        hip_joints = [rig.heads[next((i for i in path if direction(rig, i)[2] < DOWN), path[0])][2]
                      for path in below if path]
        # Both legs hang from it, and it sits at hip height (not a root bone on the floor that a leg was
        # reparented to).
        if len(hip_joints) == 2 and rig.heads[above][2] >= floor + HIP_HEIGHT * (min(hip_joints) - floor):
            legs[0], legs[1] = below
            return above
    # The legs aren't below one bone at hip height in the hierarchy the engine gets (a control rig's deforming bones
    # parented through non-deforming ones, a leg parented elsewhere). Hips is then the bone, outside both legs,
    # nearest the legs' tops.
    mapping.notes.append("the legs are not below one deforming bone at hip height: Hips is the bone nearest the tops "
                         "of both legs")
    for k in (0, 1):
        down = [i for i in legs[k] if direction(rig, i)[2] < DOWN]
        if down:
            legs[k] = legs[k][legs[k].index(down[0]):]
    in_legs = set(legs[0]) | set(legs[1])
    middle = (rig.heads[legs[0][0]] + rig.heads[legs[1][0]]) / 2
    others = [i for i in view.bones if i not in in_legs]
    if not others:
        return None
    return min(others, key=lambda i: float(np.linalg.norm(rig.heads[i] - middle)))


def _horizontal(v):
    return float(np.linalg.norm(np.asarray(v)[:2]))


def _foot_direction(rig, view, path, floor):
    """Horizontal ankle-to-toes direction of one leg and its length: toward the joint below the ankle that reaches
    farthest from it (the toes, not a heel); joints the file holds come before a leaf's guessed end. Zero when the
    leg has no foot below its shin."""
    downs = [k for k, i in enumerate(path) if _long_down(rig, i, floor)]
    if not downs or downs[-1] + 1 >= len(path):
        return np.zeros(3), 0.0
    foot = path[downs[-1] + 1]
    ankle = rig.heads[foot]
    below = view.subtree(foot)
    points = [rig.heads[b] for b in below if b != foot] + [rig.ends[b] for b in below if not view.children[b]]
    tip = max(points, key=lambda point: _horizontal(point - ankle))
    step = (tip - ankle) * np.array([1.0, 1.0, 0.0])
    length = float(np.linalg.norm(tip - ankle))
    if length <= 1e-12 or _horizontal(step) < FOOT_FORWARD * length:
        return np.zeros(3), 0.0
    return step, length


def _facing(rig, view, legs, mapping, floor):
    forward = np.zeros(3)
    found = [_foot_direction(rig, view, path, floor) for path in legs.values()]
    if len(found) == 2 and all(length > 0 for _, length in found):
        forward = found[0][0] + found[1][0]
    forward = _unit(forward)
    mapping.facing_measured = forward is not None
    if forward is None:   # Blender's convention; an imported armature's own rotation is often the format's axes
        forward = np.array([0.0, -1.0, 0.0])
        mapping.notes.append("the feet show no forward direction: the rig is taken to face -Y, Blender's forward")
    mapping.forward = forward
    mapping.left = _unit(np.cross(UP, forward))


def side_of(mapping, point, centre):
    """0 (Left) or 1 (Right): which side of ``centre`` the point is on, by the rig's facing."""
    return 0 if float((point - centre) @ mapping.left) >= 0 else 1


def _arc(rig, joints):
    """Cumulative distance along joint positions."""
    steps = [0.0] + [float(np.linalg.norm(rig_b - rig_a)) for rig_a, rig_b in zip(joints, joints[1:])]
    return np.cumsum(steps)


def middle_joint(rig, bones, end_point):
    """Index into ``bones`` (not the first) of the bone whose head is nearest half way from the first bone's head to
    ``end_point``, or None when there is no joint between them."""
    if len(bones) < 2:
        return None
    joints = [rig.heads[i] for i in bones] + [end_point]
    arc = _arc(rig, joints)
    if arc[-1] <= 1e-12:
        return None
    return min(range(1, len(bones)), key=lambda k: abs(arc[k] / arc[-1] - 0.5))


def _leg_roles(rig, hips, path, floor, mapping):
    if not path:
        return
    side = SIDES[side_of(mapping, rig.heads[path[0]], rig.heads[hips])]
    if side + "UpperLeg" in mapping.roles:
        mapping.notes.append("both legs are on the %s side of Hips: the right one is unmapped" % side.lower())
        return
    down = [k for k, i in enumerate(path) if direction(rig, i)[2] < DOWN]
    if not down:
        mapping.notes.append("the %s leg has no bone pointing down" % side.lower())
        return
    start = down[0]
    upper = path[start]
    top = rig.heads[upper][2]
    zone = floor + FOOT_ZONE * (top - floor)
    foot_k = next((k for k in range(start + 1, len(path)) if rig.heads[path[k]][2] <= zone), None)
    mapping.roles[side + "UpperLeg"] = upper
    if foot_k is None:
        mapping.notes.append("the %s leg has no joint in its lowest quarter: no foot" % side.lower())
        lower_bones = path[start:]
        end_point = rig.ends[path[-1]]
    else:
        mapping.roles[side + "Foot"] = path[foot_k]
        if foot_k + 1 < len(path):
            mapping.roles[side + "Toes"] = path[foot_k + 1]
        lower_bones = path[start:foot_k]
        end_point = rig.heads[path[foot_k]]
    knee = middle_joint(rig, lower_bones, end_point)
    if knee is not None:
        mapping.roles[side + "LowerLeg"] = lower_bones[knee]
    else:
        mapping.notes.append("the %s leg has no knee joint between hip and ankle" % side.lower())
