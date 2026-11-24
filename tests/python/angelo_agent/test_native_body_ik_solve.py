# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The full-body pose solver (``body_ik_solve.solve``) as plain data, on humanoids of unlike proportions.

Posing by hands and feet (DCC gaps, gap 4; the rigging lane's item 3) must move the whole body: a hand reaching past
the arm's reach pulls the chest and moves the hips while planted feet stay. Checked on an adult and a chibi (short
legs, big head), in T and A pose: every bone keeps its length; pinned feet stay; knees and elbows keep bending to
their rest side (never backwards); a target out of the body's reach is reported, never faked; joints on no path to a
target keep their place relative to their parent; a body with no pins reaches by moving as a whole. Expected to fail
on 5fc885e: the module does not exist there.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import body_ik_solve  # noqa: E402


def humanoid(height=1.8, leg=0.5, arm_drop=0.0, head=0.12):
    """Joints of a humanoid facing -Y (feet on the floor), by role; ``leg`` is the legs' share of the height."""
    s = height
    hip = leg * s
    chest = hip + 0.3 * s * (1 - leg) / 0.5
    neck = chest + 0.06 * s
    drop = math.radians(arm_drop)
    joints = {"Hips": (0, 0, hip), "Spine": (0, 0, hip + 0.08 * s), "Chest": (0, 0, chest), "Neck": (0, 0, neck),
              "Head": (0, 0, neck + head * s)}
    parents = {"Hips": None, "Spine": "Hips", "Chest": "Spine", "Neck": "Chest", "Head": "Neck"}
    for side, x in (("Left", 1), ("Right", -1)):
        sh = (x * 0.1 * s, 0, chest)
        d = (x * math.cos(drop), 0.0, -math.sin(drop))
        el = (sh[0] + d[0] * 0.16 * s, 0.0, sh[2] + d[2] * 0.16 * s)
        el = (el[0], 0.005 * s, el[2])   # A slight rest bend backwards: elbows bend forward when the arm folds.
        wr = (el[0] + d[0] * 0.15 * s, 0.0, el[2] + d[2] * 0.15 * s)
        joints.update({side + "UpperArm": sh, side + "LowerArm": el, side + "Hand": wr})
        parents.update({side + "UpperArm": "Chest", side + "LowerArm": side + "UpperArm", side + "Hand": side + "LowerArm"})
        knee = (x * 0.09 * s, -0.01 * s, hip / 2)   # Knees slightly forward at rest.
        joints.update({side + "UpperLeg": (x * 0.09 * s, 0, hip), side + "LowerLeg": knee,
                       side + "Foot": (x * 0.09 * s, 0, 0.0)})
        parents.update({side + "UpperLeg": "Hips", side + "LowerLeg": side + "UpperLeg", side + "Foot": side + "LowerLeg"})
    hinges = {}
    for side in ("Left", "Right"):
        hinges[side + "LowerLeg"] = (side + "UpperLeg", side + "Foot", (1, 0, 0))
        hinges[side + "LowerArm"] = (side + "UpperArm", side + "Hand", (1, 0, 0))
    return joints, parents, hinges


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def bend_side(base, joint, end):
    """The sign of the bend: the joint's offset from the base-end line, along -Y (forward)."""
    t = sum((j - b) * (e - b) for j, b, e in zip(joint, base, end)) / max(dist(base, end) ** 2, 1e-12)
    on_line = tuple(b + t * (e - b) for b, e in zip(base, end))
    return -(joint[1] - on_line[1])


BODIES = {"adult_T": dict(), "adult_A": dict(arm_drop=40), "chibi": dict(height=1.0, leg=0.3, head=0.3)}


class FullBody(unittest.TestCase):
    def check_lengths(self, joints, parents, pos):
        for j, p in parents.items():
            if p:
                self.assertAlmostEqual(dist(pos[j], pos[p]), dist(joints[j], joints[p]), delta=1e-6, msg=j)

    def test_reach_past_the_arm_moves_the_body_feet_stay_knees_forward(self):
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                hand = joints["RightHand"]
                target = (hand[0] + 0.1 * h, -0.25 * h, hand[2] - 0.35 * h)   # Forward and down: a bend is needed.
                out = body_ik_solve.solve(joints, parents, {"RightHand": target},
                                          pinned=("LeftFoot", "RightFoot"), hinges=hinges, tolerance=1e-3 * h)
                pos = out["positions"]
                self.assertTrue(out["reached"], out["errors"])
                self.check_lengths(joints, parents, pos)
                self.check_branches_rigid(joints, parents, pos, h)
                for side in ("Left", "Right"):
                    self.assertLess(dist(pos[side + "Foot"], joints[side + "Foot"]), 1e-3 * h)
                    self.assertGreaterEqual(bend_side(pos[side + "UpperLeg"], pos[side + "LowerLeg"],
                                                      pos[side + "Foot"]), -1e-9)
                self.assertGreater(dist(pos["Hips"], joints["Hips"]), 1e-3 * h)   # The body took part.

    def check_branches_rigid(self, joints, parents, pos, h):
        """Around every joint with several children (hips, chest), the joint and its children move as one bone:
        the distances between them and the handedness of their offsets are kept, so one rotation of that bone
        puts every child where the solver did (a mere length check lets a branch's shape deform)."""
        children = {}
        for j, p in parents.items():
            if p is not None:
                children.setdefault(p, []).append(j)
        for p, kids in children.items():
            if len(kids) < 2:
                continue
            for i, a in enumerate(kids):
                for b in kids[i + 1:]:
                    self.assertAlmostEqual(dist(pos[a], pos[b]), dist(joints[a], joints[b]), delta=1e-4 * h,
                                           msg="%s: %s-%s" % (p, a, b))
            if len(kids) >= 3:
                def volume(points):
                    u, v, w = (tuple(x - y for x, y in zip(points[k], points[p])) for k in kids[:3])
                    return (u[0] * (v[1] * w[2] - v[2] * w[1]) - u[1] * (v[0] * w[2] - v[2] * w[0])
                            + u[2] * (v[0] * w[1] - v[1] * w[0]))
                self.assertAlmostEqual(volume(pos), volume(joints), delta=1e-4 * h ** 3, msg=p)

    def test_branching_joints_move_rigidly(self):
        """The class behind the app's body-pose misses (redgreen-2, 2026-11-15): FABRIK put each child of the hips
        and chest on its own, so the bones' rotations could not reproduce the solve; the hand missed and the
        planted feet slid. Every case here: reaching, lowering the hips, out of reach, no pins."""
        for label, shape in BODIES.items():
            joints, parents, hinges = humanoid(**shape)
            h = shape.get("height", 1.8)
            hand, hips = joints["RightHand"], joints["Hips"]
            cases = {
                "reach": ({"RightHand": (hand[0] + 0.1 * h, -0.25 * h, hand[2] - 0.35 * h)}, ("LeftFoot", "RightFoot")),
                "hips_down": ({"Hips": (hips[0], hips[1], hips[2] - 0.12 * h)}, ("LeftFoot", "RightFoot")),
                "out_of_reach": ({"RightHand": (0, -3.0 * h, h)}, ("LeftFoot", "RightFoot")),
                "two_hands": ({"RightHand": (hand[0], -0.3 * h, hand[2] + 0.2 * h),
                               "LeftHand": (joints["LeftHand"][0] + 0.1 * h, -0.2 * h, hand[2] - 0.1 * h)},
                              ("LeftFoot", "RightFoot")),
                "no_pins": ({"LeftHand": (joints["LeftHand"][0] + 1.0 * h, 0.0, hand[2])}, ()),
            }
            for name, (targets, pinned) in cases.items():
                with self.subTest(label + ":" + name):
                    out = body_ik_solve.solve(joints, parents, targets, pinned=pinned, hinges=hinges,
                                              tolerance=1e-3 * h)
                    self.check_lengths(joints, parents, out["positions"])
                    self.check_branches_rigid(joints, parents, out["positions"], h)

    def test_out_of_reach_is_reported_not_faked(self):
        joints, parents, hinges = humanoid()
        out = body_ik_solve.solve(joints, parents, {"RightHand": (0, -3.0, 1.0)}, pinned=("LeftFoot", "RightFoot"),
                                  hinges=hinges)
        self.assertFalse(out["reached"])
        self.assertGreater(out["errors"]["RightHand"], 0.5)
        self.check_lengths(joints, parents, out["positions"])

    def test_lowering_the_hips_bends_the_knees_forward(self):
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                hips = joints["Hips"]
                out = body_ik_solve.solve(joints, parents, {"Hips": (hips[0], hips[1], hips[2] - 0.12 * h)},
                                          pinned=("LeftFoot", "RightFoot"), hinges=hinges, tolerance=1e-3 * h)
                pos = out["positions"]
                self.assertTrue(out["reached"], out["errors"])
                for side in ("Left", "Right"):
                    self.assertGreater(bend_side(pos[side + "UpperLeg"], pos[side + "LowerLeg"],
                                                 pos[side + "Foot"]), 0.01 * h)

    def test_joints_on_no_path_keep_their_place_relative_to_their_parent(self):
        joints, parents, hinges = humanoid()
        hand = joints["RightHand"]
        out = body_ik_solve.solve(joints, parents, {"RightHand": (hand[0], -0.3, hand[2] - 0.3)},
                                  pinned=("LeftFoot", "RightFoot"), hinges=hinges)
        pos = out["positions"]
        rest = tuple(a - b for a, b in zip(joints["Head"], joints["Neck"]))
        now = tuple(a - b for a, b in zip(pos["Head"], pos["Neck"]))
        self.assertLess(dist(rest, now), 1e-9)

    def test_no_pins_reaches_by_moving_as_a_whole(self):
        joints, parents, hinges = humanoid()
        hand = joints["LeftHand"]
        out = body_ik_solve.solve(joints, parents, {"LeftHand": (hand[0] + 1.0, hand[1], hand[2])}, hinges=hinges)
        self.assertTrue(out["reached"], out["errors"])
        self.check_lengths(joints, parents, out["positions"])

    def test_deterministic_and_bounded(self):
        joints, parents, hinges = humanoid()
        args = (joints, parents, {"RightHand": (0, -3.0, 1.0)})
        a = body_ik_solve.solve(*args, pinned=("LeftFoot",), hinges=hinges, iterations=10)
        b = body_ik_solve.solve(*args, pinned=("LeftFoot",), hinges=hinges, iterations=10)
        self.assertEqual(a, b)
        self.assertEqual(a["iterations"], 10)


if __name__ == "__main__":
    unittest.main()
