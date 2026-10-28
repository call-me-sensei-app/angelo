# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic skinned hands for the hand-rig tests: bones and a tube mesh around them, numpy only, no Blender.

A hand hangs from a forearm with the palm toward the body's midline (the rigs' rest pose), or
lies in a T-pose with the palm down. Proportions, bone roll, finger count and rest curl vary by argument, so a rule
tuned to one hand fails on another. The suites build the same layouts as real Blender meshes
(``hand_fixture_scenes``); these arrays exercise the math without Blender.
"""

import math

import numpy as np

from angelo_agent.hand_rig import Skeleton
from angelo_agent.hand_skin import Skin

ROLES = ("index", "middle", "ring", "little")
# Knuckle-to-tip lengths as a share of the middle finger's (the checklist's #4) and the knuckle arc (#5).
LENGTH = {"index": 0.9, "middle": 1.0, "ring": 0.93, "little": 0.72}
ARC = {"index": -0.03, "middle": 0.0, "ring": -0.03, "little": -0.08}


def _frame(direction, roll):
    """A Blender-like bone matrix rotation: y along the bone, x and z by ``roll`` about it."""
    y = direction / np.linalg.norm(direction)
    helper = np.array([0.0, 0.0, 1.0]) if abs(y[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    x = np.cross(y, helper)
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    c, s = math.cos(roll), math.sin(roll)
    x, z = c * x + s * z, -s * x + c * z
    return np.stack([x, y, z], axis=1)


class Builder:
    def __init__(self):
        self.names, self.parents, self.heads, self.tails, self.deform, self.rolls = [], [], [], [], [], []

    def bone(self, name, parent, head, tail, deform=True, roll=0.0):
        self.names.append(name)
        self.parents.append(self.names.index(parent) if parent else -1)
        self.heads.append(np.asarray(head, float))
        self.tails.append(np.asarray(tail, float))
        self.deform.append(deform)
        self.rolls.append(roll)
        return name

    def skeleton(self):
        n = len(self.names)
        rest = np.tile(np.eye(4), (n, 1, 1))
        for i in range(n):
            rest[i, :3, :3] = _frame(self.tails[i] - self.heads[i], self.rolls[i])
            rest[i, :3, 3] = self.heads[i]
        basis = np.tile(np.eye(4), (n, 1, 1))
        return Skeleton(self.names, self.parents, rest, self.tails, basis, rest.copy(), self.deform)


def hand_bones(side="Left", fingers=4, middle=0.06, palm=0.07, rest_curl=8.0, t_pose=False, roll_seed=0,
               metacarpals=False, end_bones=False, prefix=""):
    """A skeleton with spine, shoulder, upper arm, forearm, hand and digits. Lengths in metres."""
    rng = np.random.default_rng(roll_seed)
    s = 1.0 if side == "Left" else -1.0
    b = Builder()
    b.bone(prefix + "Hips", None, (0, 0, 0.9), (0, 0, 1.1))
    b.bone(prefix + side + "UpLeg", prefix + "Hips", (0.1 * s, 0, 0.9), (0.1 * s, 0, 0.5))
    shoulder = np.array([0.18 * s, 0, 1.4])
    elbow = shoulder + (np.array([0.25 * s, 0, 0]) if t_pose else np.array([0.02 * s, 0, -0.25]))
    wrist = elbow + (np.array([0.24 * s, 0, 0]) if t_pose else np.array([0.0, 0, -0.24]))
    along = np.array([s, 0, 0]) if t_pose else np.array([0, 0, -1.0])
    palm_n = np.array([0, 0, -1.0]) if t_pose else np.array([-s, 0, 0])   # out of the palm
    across = np.cross(palm_n, along) * s                                      # index -> little
    if np.linalg.norm(across) < 1e-9:
        across = np.array([0, 1.0, 0])
    across /= np.linalg.norm(across)
    if across @ np.array([0, 1.0, 0]) < 0:   # the thumb is in front (-Y), the little finger behind
        across = -across
    b.bone(prefix + side + "Arm", prefix + "Hips", shoulder, elbow, roll=rng.uniform(-3, 3))
    b.bone(prefix + side + "ForeArm", prefix + side + "Arm", elbow, wrist, roll=rng.uniform(-3, 3))
    knuckle_mid = wrist + along * palm
    b.bone(prefix + side + "Hand", prefix + side + "ForeArm", wrist, knuckle_mid, roll=rng.uniform(-3, 3))
    roles = {4: ROLES, 3: ("index", "middle", "little"), 2: ("index", "little")}[fingers]
    width = 0.018
    for k, role in enumerate(roles):
        # The middle finger lines up with the forearm, as in a hand; the others step out from it.
        centre = roles.index("middle") if "middle" in roles else (len(roles) - 1) / 2
        offset = (k - centre) * width
        knuckle = knuckle_mid + across * offset + along * ARC[role] * middle
        total = LENGTH[role] * middle
        parent = prefix + side + "Hand"
        if metacarpals:
            parent = b.bone(prefix + side + "Meta" + role, parent, wrist + across * offset * 0.6, knuckle,
                            roll=rng.uniform(-3, 3))
        point, direction = knuckle, along.copy()
        for j, share in enumerate((0.5, 0.3, 0.2)):
            angle = math.radians(rest_curl * (j + 1) / 3)
            direction = math.cos(angle) * direction + math.sin(angle) * palm_n
            direction /= np.linalg.norm(direction)
            end = point + direction * total * share
            parent = b.bone("%s%sHand%s%d" % (prefix, side, role.capitalize(), j + 1), parent, point, end,
                            roll=rng.uniform(-3, 3))
            point = end
        if end_bones:
            b.bone("%s%sHand%s4" % (prefix, side, role.capitalize()), parent, point, point + direction * 0.005,
                   deform=False)
    # The thumb: based low on the palm on the index side, forward of the palm and angled away from the fingers.
    base = wrist + along * palm * 0.2 - across * width * 1.2
    direction = 0.55 * along - 0.6 * across + 0.45 * palm_n
    direction /= np.linalg.norm(direction)
    parent = prefix + side + "Hand"
    point = base
    for j, length in enumerate((0.03, 0.022, 0.018)):
        end = point + direction * length
        parent = b.bone("%s%sHandThumb%d" % (prefix, side, j + 1), parent, point, end, roll=rng.uniform(-3, 3))
        point = end
    return b


def tube_skin(skeleton, radius=0.0075, ring=8, palm_radius=0.022, leak=0.0, cut=None):
    """A tube of rings around every digit bone and the hand and forearm, weighted to its bone and blended at the
    joints. ``leak`` adds that much weight of the thigh bone to some palm vertices; ``cut`` (a bone name) switches
    to that bone from its parent in one step instead of blending."""
    positions, vertex, bone, weight, edges, triangles = [], [], [], [], [], []
    index = skeleton.index

    def add_ring(centre, axis, r, weights):
        axis = axis / np.linalg.norm(axis)
        helper = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
        u = np.cross(axis, helper)
        u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        start = len(positions)
        for t in range(ring):
            a = 2 * math.pi * t / ring
            positions.append(centre + r * (math.cos(a) * u + math.sin(a) * v))
            for name, w in weights.items():
                vertex.append(start + t)
                bone.append(index[name])
                weight.append(w)
        for t in range(ring):
            edges.append((start + t, start + (t + 1) % ring))
        return start

    def join(a, b_):
        for t in range(ring):
            t2 = (t + 1) % ring
            edges.append((a + t, b_ + t))
            triangles.append((a + t, a + t2, b_ + t2))
            triangles.append((a + t, b_ + t2, b_ + t))

    def chain(names, r, steps=4, attach=None):
        previous = attach
        for i, name in enumerate(names):
            k = index[name]
            h, t = skeleton.heads[k], skeleton.tails[k]
            for s in range(steps):
                f = s / steps
                w = {name: 1.0}
                if i > 0 and s == 0 and name != cut:
                    w = {name: 0.5, names[i - 1]: 0.5}
                start = add_ring(h + (t - h) * f, t - h, r, w)
                if previous is not None:
                    join(previous, start)
                previous = start
        k = index[names[-1]]
        end = add_ring(skeleton.tails[k], skeleton.tails[k] - skeleton.heads[k], r * 0.6, {names[-1]: 1.0})
        join(previous, end)
        return end

    side = next(n for n in skeleton.names if n.endswith("Hand")).replace("Hand", "")
    hand = side + "Hand"
    fore = chain([side + "ForeArm"], palm_radius * 1.1)
    k = index[hand]
    palm_rings = []
    previous = None
    for s in range(5):
        f = s / 4
        start = add_ring(skeleton.heads[k] + (skeleton.tails[k] - skeleton.heads[k]) * f,
                         skeleton.tails[k] - skeleton.heads[k], palm_radius, {hand: 1.0})
        if previous is not None:
            join(previous, start)
        previous = start
        palm_rings.append(start)
    edges.append((fore, palm_rings[0]))
    for name in skeleton.names:
        if name.endswith("1") and "Hand" in name:
            digit = [name, name[:-1] + "2", name[:-1] + "3"]
            digit = [d for d in digit if d in index]
            first = len(positions)
            chain(digit, radius * (1.3 if "Thumb" in name else 1.0))
            nearest = palm_rings[-1] if "Thumb" not in name else palm_rings[1]
            edges.append((first, nearest))
    if leak:
        # Heat weights leaking across a small gap (rig B: a hand hanging by the thigh got thigh weight): some palm
        # vertices also carry weight of a bone outside the arm.
        for t in range(ring // 2):
            vertex.append(palm_rings[2] + t)
            bone.append(index[side + "UpLeg"])
            weight.append(leak)
    return Skin(np.array(positions), vertex, bone, weight, triangles, edges)
