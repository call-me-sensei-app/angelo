# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every hand target a body can reach within its ranges of motion is reached (``body_ik_solve.solve``), whatever the
body's size and proportions.

The class behind the 2026-11-18 miss (``test_blender_body_pose``: a deep reach on the 1.2 m rig ended 1.44 cm short
with the ranges on): once a link sits at its limit, FABRIK still pulls the hips only along the chain's straight
line, so the body folded at the hips, legs straight and hips at full height, instead of lowering. Here each target
comes from a pose built to lie within the ranges (forward kinematics from rest: the hips lowered on bent knees, the
pelvis pitched, the chest bent, the shoulder swung, the elbow folded, each at most 80% of its range), so it is
reachable by construction; the solver starts from rest and must put the hand (or both hands) within 1% of the
height. Body shapes are held out from the solver's development (none is in ``test_native_body_ik_solve.BODIES``):
heights 0.7 to 2.1 m, legs 38% to 55% of the height, T to deep A pose, big and small heads. Expected to fail on
93a78e0aa48 (14 of 40 short, 2.2-11.6% of the height).

``SHAPES_2``/``POSES_2`` are a second held-out set, written after the fix was chosen on the first and never used to
choose anything: the fix is judged on it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_body_ik_solve import humanoid  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import body_ik_solve  # noqa: E402

FEET = ("LeftFoot", "RightFoot")
SHAPES = {"small_A": dict(height=0.7, leg=0.4, arm_drop=45, head=0.22),
          "child": dict(height=1.3, leg=0.45, arm_drop=25, head=0.16),
          "tall_T": dict(height=2.1, leg=0.55, arm_drop=0, head=0.1),
          "long_torso_A": dict(height=1.6, leg=0.38, arm_drop=50, head=0.13)}
R = math.radians
RANGES = {"Chest": R(40), "Neck": R(45), "Head": R(45)}
for _s in ("Left", "Right"):
    RANGES.update({_s + "UpperArm": R(30), _s + "LowerArm": R(150), _s + "Hand": R(150), _s + "LowerLeg": R(120),
                   _s + "Foot": R(135)})
# (hips down as a share of the leg, pelvis pitch, chest bend, shoulder swing forward, shoulder swing down, elbow fold)
POSES = {"deep_forward": (0.25, 0.8, 0.8, 0.5, 0.3, 0.1), "squat_low": (0.4, 0.3, 0.5, 0.3, 0.6, 0.2),
         "bend_and_fold": (0.1, 0.6, 0.6, 0.7, 0.0, 0.6), "upright_reach": (0.0, 0.1, 0.2, 0.8, -0.3, 0.3),
         "knees_only": (0.3, 0.0, 0.0, 0.2, 0.2, 0.4)}


SHAPES_2 = {"teen": dict(height=1.55, leg=0.47, arm_drop=15, head=0.14),
            "stocky": dict(height=1.45, leg=0.42, arm_drop=35, head=0.15),
            "lanky": dict(height=1.95, leg=0.52, arm_drop=10, head=0.11),
            "toy": dict(height=0.9, leg=0.5, arm_drop=60, head=0.2)}
POSES_2 = {"half_squat_fold": (0.2, 0.5, 0.4, 0.6, 0.1, 0.5), "low_reach": (0.35, 0.6, 0.7, 0.4, 0.5, 0.2),
           "lean_only": (0.0, 0.7, 0.8, 0.3, 0.2, 0.0), "crouch_elbow": (0.45, 0.2, 0.3, 0.1, 0.4, 0.7)}


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def rot(v, axis, angle):
    """``v`` turned by ``angle`` about the unit ``axis`` (Rodrigues)."""
    c, s = math.cos(angle), math.sin(angle)
    d = sum(a * b for a, b in zip(axis, v))
    cr = (axis[1] * v[2] - axis[2] * v[1], axis[2] * v[0] - axis[0] * v[2], axis[0] * v[1] - axis[1] * v[0])
    return tuple(v[k] * c + cr[k] * s + axis[k] * d * (1 - c) for k in range(3))


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v)


def below(parents, j):
    out, stack = [], [j]
    while stack:
        k = stack.pop()
        out.append(k)
        stack += [c for c, p in parents.items() if p == k]
    return out


def turn_subtree(pos, parents, top, pivot, axis, angle):
    for k in below(parents, top):
        pos[k] = add(pivot, rot(sub(pos[k], pivot), axis, angle))


def feasible_pose(joints, parents, h, pose):
    """A pose within the ranges by construction, from rest; facing -Y, X to the body's left."""
    down, pitch, chest, fwd, low, elbow = pose
    pos = dict(joints)
    leg = math.dist(joints["LeftUpperLeg"], joints["LeftLowerLeg"]) + math.dist(joints["LeftLowerLeg"], joints["LeftFoot"])
    drop = down * leg
    for k in below(parents, "Hips"):   # Lower everything above the feet's chains...
        if not any(k.startswith(s + part) for s in ("Left", "Right") for part in ("LowerLeg", "Foot")):
            pos[k] = add(pos[k], (0.0, 0.0, -drop))
    for side in ("Left", "Right"):   # ... and put each knee forward on the triangle hip-knee-ankle.
        hip, ankle = pos[side + "UpperLeg"], pos[side + "Foot"]
        l1 = math.dist(joints[side + "UpperLeg"], joints[side + "LowerLeg"])
        l2 = math.dist(joints[side + "LowerLeg"], joints[side + "Foot"])
        d = math.dist(hip, ankle)
        x = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
        y = math.sqrt(max(l1 * l1 - x * x, 0.0))
        u = unit(sub(ankle, hip))
        side_dir = unit(sub((0.0, -1.0, 0.0), tuple(c * u[1] * -1 for c in u)))
        pos[side + "LowerLeg"] = add(hip, add(tuple(c * x for c in u), tuple(c * y for c in side_dir)))
    x_axis = (1.0, 0.0, 0.0)
    turn_subtree(pos, parents, "Spine", pos["Hips"], x_axis, -pitch * R(80))   # Pelvis forward (no range: 80 deg).
    turn_subtree(pos, parents, "Chest", pos["Spine"], x_axis, -chest * 0.8 * RANGES["Chest"])
    shoulder = pos["RightUpperArm"]
    turn_subtree(pos, parents, "RightLowerArm", shoulder, x_axis, -fwd * 0.8 * R(90))   # Arm forward...
    turn_subtree(pos, parents, "RightLowerArm", shoulder, (0.0, 1.0, 0.0), -low * 0.8 * R(45))   # ... and down.
    a, b = sub(pos["RightLowerArm"], shoulder), sub(pos["RightHand"], pos["RightLowerArm"])
    axis = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    if math.sqrt(sum(c * c for c in axis)) > 1e-9:   # The fold keeps the side the elbow bends at rest.
        turn_subtree(pos, parents, "RightHand", pos["RightLowerArm"], unit(axis), elbow * 0.8 * R(150))
    return pos


class ReachWithinRange(unittest.TestCase):
    def test_every_target_a_pose_within_range_reaches_is_reached(self):
        self.assertEqual(self.misses(SHAPES, POSES), [])

    def test_the_second_held_out_set_too(self):
        self.assertEqual(self.misses(SHAPES_2, POSES_2), [])

    def test_straight_legs_that_must_bend_bend_forward(self):
        """A body standing on perfectly straight legs (common in imported rigs) lowered by the reach: the settled legs
        have no side of their own, and a knee must still bend the side the rig faces (forward, -Y), never back."""
        for label, shape in SHAPES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape["height"]
                for side in ("Left", "Right"):   # Straighten the knees; a straight knee bends by the facing (-Y).
                    hip, ankle = joints[side + "UpperLeg"], joints[side + "Foot"]
                    joints[side + "LowerLeg"] = tuple((a + b) / 2 for a, b in zip(hip, ankle))
                    axis = sub(ankle, hip)   # As body_pose does: the limb's axis crossed with forward.
                    hinges[side + "LowerLeg"] = (side + "UpperLeg", side + "Foot", (axis[1] * 0 - axis[2] * -1.0,
                                                 axis[2] * 0 - axis[0] * 0, axis[0] * -1.0 - axis[1] * 0))
                posed = feasible_pose(joints, parents, h, POSES["knees_only"])
                hand = joints["RightHand"]
                for name, targets in (("both hands lowered", {k: posed[k] for k in ("RightHand", "LeftHand")}),
                                      ("one hand forward and down", {"RightHand": (hand[0] + 0.1 * h, -0.25 * h,
                                                                                   hand[2] - 0.35 * h)}),
                                      ("hips forward and down", {"Hips": (0.0, -0.08 * h,
                                                                          joints["Hips"][2] - 0.15 * h)})):
                    out = body_ik_solve.solve(joints, parents, targets, pinned=FEET, hinges=hinges, iterations=160,
                                              tolerance=1e-3 * h, limits=RANGES, patience=8)
                    self.assert_knees_forward(out["positions"], h, (label, name))

    def assert_knees_forward(self, pos, h, case):
        for side in ("Left", "Right"):
            hip, knee, ankle = (pos[side + r] for r in ("UpperLeg", "LowerLeg", "Foot"))
            t = sum((k - a) * (b - a) for k, a, b in zip(knee, hip, ankle)) / max(
                sum((b - a) ** 2 for a, b in zip(hip, ankle)), 1e-12)
            line_y = hip[1] + t * (ankle[1] - hip[1])
            self.assertLessEqual(knee[1] - line_y, 1e-6 * h, (case, side, knee[1] - line_y))

    def misses(self, shapes, poses):
        misses = []
        for label, shape in shapes.items():
            joints, parents, hinges = humanoid(**shape)
            h = shape["height"]
            for name, pose in poses.items():
                target = feasible_pose(joints, parents, h, pose)
                for hands in (("RightHand",), ("RightHand", "LeftHand")):
                    out = body_ik_solve.solve(joints, parents, {k: target[k] for k in hands}, pinned=FEET,
                                              hinges=hinges, iterations=160, tolerance=1e-3 * h, limits=RANGES,
                                              patience=8)
                    worst = max(out["errors"][k] for k in hands) / h
                    if worst > 0.01:
                        misses.append("%s %s %s: %.2f%% of the height" % (label, name, "+".join(hands), worst * 100))
        return misses


if __name__ == "__main__":
    unittest.main()
