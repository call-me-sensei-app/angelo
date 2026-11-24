# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Joint limits and balance in the full-body pose solver (``body_ik_solve.solve``), as plain data.

Finding 13 of the rigging review (2026-11-15): the solver reached targets with no joint limits, so an elbow folded
through the upper arm or a neck bent past what any body does, and with no balance a hand reaching far forward left
the body's centre of mass far outside its planted feet ("reach the top shelf, feet planted" leaned over the void).

* **Limits** (``limits={joint: angle}``): the link from a joint's parent to the joint never swings further than the
  angle from its rest direction carried by the parent's frame; a joint with several children turns them as one, and
  that turn is held to the tightest of their limits. A target the limits keep out of reach is reported, never faked.
* **Balance** (``balance={"segments", "support", "radius", "up"}``): the centre of mass of the given segments, seen
  along ``up``, stays over the support (the planted joints' hull, widened by ``radius``). Pins come first, then
  balance, then targets. A pose that already balances is left exactly as without it.

Checked on an adult (T and A pose) and a chibi, reaching forward, sideways and back, on two feet and one. The check
of balance computes the centre of mass here, independently of the solver. Expected to fail on afee78c028b: ``solve``
takes neither argument there; the rest-pose ranges on d4391b5bfc8 (no ``rest``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_body_ik_solve import BODIES, dist, humanoid  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import body_ik_solve  # noqa: E402

FEET = ("LeftFoot", "RightFoot")


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def angle(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    c = sum(x * y for x, y in zip(a, b)) / max(na * nb, 1e-12)
    return math.acos(max(-1.0, min(1.0, c)))


def arc(a, b):
    """The 3x3 matrix of the smallest rotation turning direction ``a`` onto ``b`` (Rodrigues)."""
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    a, b = tuple(x / na for x in a), tuple(x / nb for x in b)
    v = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    c = sum(x * y for x, y in zip(a, b))
    k = [[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]]
    kk = [[sum(k[i][m] * k[m][j] for m in range(3)) for j in range(3)] for i in range(3)]
    f = 1.0 / (1.0 + c) if c > -1.0 + 1e-12 else 0.0
    return [[(1.0 if i == j else 0.0) + k[i][j] + kk[i][j] * f for j in range(3)] for i in range(3)]


def mat_mul(a, b):
    return [[sum(a[i][m] * b[m][j] for m in range(3)) for j in range(3)] for i in range(3)]


def apply(m, v):
    return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))


def carried_frames(joints, parents, pos):
    """{joint: the rotation carried to the link ending at it}: from the root, each link's frame is its parent link's
    frame turned by the smallest arc onto where the link now points (the root's frame is the identity)."""
    frames = {}

    def frame(j):
        if j in frames:
            return frames[j]
        p = parents[j]
        if p is None:
            frames[j] = [[1.0 if i == k else 0.0 for k in range(3)] for i in range(3)]
            return frames[j]
        up = frame(p)
        frames[j] = mat_mul(arc(apply(up, sub(joints[j], joints[p])), sub(pos[j], pos[p])), up)
        return frames[j]
    for j in parents:
        frame(j)
    return frames


def swing(joints, parents, pos, j, frames):
    """How far the link from ``j``'s parent to ``j`` points from its rest direction carried by the parent's frame."""
    p = parents[j]
    return angle(sub(pos[j], pos[p]), apply(frames[p], sub(joints[j], joints[p])))


def segments_of(joints):
    """Segment masses (fractions of the body) of the test humanoids: trunk, head, arms, legs."""
    seg = [("Hips", "Spine", 0.11), ("Spine", "Chest", 0.16), ("Chest", "Neck", 0.16), ("Neck", "Head", 0.08)]
    for s in ("Left", "Right"):
        seg += [(s + "UpperArm", s + "LowerArm", 0.027), (s + "LowerArm", s + "Hand", 0.016),
                (s + "Hand", s + "Hand", 0.006), (s + "UpperLeg", s + "LowerLeg", 0.14),
                (s + "LowerLeg", s + "Foot", 0.043), (s + "Foot", s + "Foot", 0.014)]
    return seg


def com(pos, segments):
    total = sum(m for _, _, m in segments)
    return tuple(sum(m * (pos[a][k] + pos[b][k]) / 2.0 for a, b, m in segments) / total for k in range(3))


def outside(point, support, radius):
    """How far ``point`` lies beyond the support seen from above (z up): the hull of ``support`` widened by
    ``radius``. Brute force: the distance to the hull is the largest over its edges' outward offsets, or for one or two
    points the distance to that point or segment."""
    pts = sorted(set((p[0], p[1]) for p in support))
    q = (point[0], point[1])

    def seg_dist(a, b):
        ab, aq = (b[0] - a[0], b[1] - a[1]), (q[0] - a[0], q[1] - a[1])
        t = max(0.0, min(1.0, (aq[0] * ab[0] + aq[1] * ab[1]) / max(ab[0] ** 2 + ab[1] ** 2, 1e-18)))
        return math.hypot(q[0] - a[0] - t * ab[0], q[1] - a[1] - t * ab[1])
    if len(pts) == 1:
        return max(0.0, math.hypot(q[0] - pts[0][0], q[1] - pts[0][1]) - radius)
    if len(pts) == 2:
        return max(0.0, seg_dist(*pts) - radius)
    raise AssertionError("the test bodies stand on one or two points")


def balance_of(joints, support, h):
    return {"segments": segments_of(joints), "support": support, "radius": 0.03 * h, "up": (0.0, 0.0, 1.0)}


class Limits(unittest.TestCase):
    def test_an_elbow_never_folds_past_its_limit(self):
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                sh = joints["RightUpperArm"]
                target = (sh[0] + 0.02 * h, sh[1] + 0.03 * h, sh[2] + 0.01 * h)   # The hand back at the shoulder.
                limits = {"RightHand": math.radians(140.0)}
                free = body_ik_solve.solve(joints, parents, {"RightHand": target}, pinned=FEET, hinges=hinges,
                                           tolerance=1e-3 * h)
                frames = carried_frames(joints, parents, free["positions"])
                self.assertGreater(swing(joints, parents, free["positions"], "RightHand", frames),
                                   limits["RightHand"] + 0.05, "the case needs the limit")
                out = body_ik_solve.solve(joints, parents, {"RightHand": target}, pinned=FEET, hinges=hinges,
                                          tolerance=1e-3 * h, limits=limits)
                pos = out["positions"]
                frames = carried_frames(joints, parents, pos)
                self.assertLessEqual(swing(joints, parents, pos, "RightHand", frames), limits["RightHand"] + 1e-6)
                # The body may still bring the hand there another way (moving the hips); whatever it does, what is
                # left is what the solver reports.
                self.assertAlmostEqual(out["errors"]["RightHand"], dist(pos["RightHand"], target), delta=1e-9)
                self.assertEqual(out["reached"], out["errors"]["RightHand"] <= 1e-3 * h)
                for j, p in parents.items():
                    if p:
                        self.assertAlmostEqual(dist(pos[j], pos[p]), dist(joints[j], joints[p]), delta=1e-6, msg=j)
                for foot in FEET:
                    self.assertLess(dist(pos[foot], joints[foot]), 1e-3 * h)

    def test_a_neck_never_bends_past_its_limit(self):
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                neck = joints["Neck"]
                target = (neck[0] + 0.12 * h, neck[1], neck[2] - 0.02 * h)   # The head down beside the neck.
                limits = {"Head": math.radians(35.0)}
                out = body_ik_solve.solve(joints, parents, {"Head": target}, pinned=FEET, hinges=hinges,
                                          tolerance=1e-3 * h, limits=limits)
                frames = carried_frames(joints, parents, out["positions"])
                self.assertLessEqual(swing(joints, parents, out["positions"], "Head", frames), limits["Head"] + 1e-6)

    def test_a_branch_turns_no_further_than_its_tightest_child_allows(self):
        """The chest turns its neck and both arms as one: each of their links stays within the limit of the chest's
        children from where the spine's frame carries them."""
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                limit = math.radians(15.0)
                limits = {"Neck": limit, "LeftUpperArm": math.radians(60.0), "RightUpperArm": math.radians(60.0)}
                hand = joints["RightHand"]
                targets = {"RightHand": (hand[0] + 0.3 * h, 0.5 * h, hand[2] - 0.2 * h),   # Far behind, low.
                           "LeftHand": (joints["LeftHand"][0] + 0.3 * h, 0.5 * h, hand[2] - 0.2 * h)}
                out = body_ik_solve.solve(joints, parents, targets, pinned=FEET, hinges=hinges, tolerance=1e-3 * h,
                                          limits=limits)
                pos = out["positions"]
                frames = carried_frames(joints, parents, pos)
                for child in ("Neck", "LeftUpperArm", "RightUpperArm"):
                    self.assertLessEqual(swing(joints, parents, pos, child, frames), limit + 1e-6, child)

    def test_limits_nothing_reaches_change_nothing(self):
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                hand = joints["RightHand"]
                target = (hand[0] + 0.1 * h, -0.25 * h, hand[2] - 0.35 * h)
                wide = {j: math.radians(179.0) for j, p in parents.items() if p}
                a = body_ik_solve.solve(joints, parents, {"RightHand": target}, pinned=FEET, hinges=hinges,
                                        tolerance=1e-3 * h)
                b = body_ik_solve.solve(joints, parents, {"RightHand": target}, pinned=FEET, hinges=hinges,
                                        tolerance=1e-3 * h, limits=wide)
                for j in joints:
                    self.assertLess(dist(a["positions"][j], b["positions"][j]), 1e-9, j)


    def test_ranges_count_from_the_rest_pose_not_from_the_pose_it_starts_in(self):
        """Posing is incremental (the solve starts from the current pose), but a range of motion is the body's: a
        neck already bent most of its range bends only the rest of it, however many times it is posed."""
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                limit = math.radians(35.0)
                neck = joints["Neck"]
                target = (neck[0] + 0.12 * h, neck[1], neck[2] - 0.02 * h)
                start = dict(joints)
                for _ in range(3):   # Pose, then pose again from the result.
                    out = body_ik_solve.solve(start, parents, {"Head": target}, pinned=FEET, hinges=hinges,
                                              tolerance=1e-3 * h, limits={"Head": limit}, rest=joints)
                    start = out["positions"]
                frames = carried_frames(joints, parents, start)
                self.assertLessEqual(swing(joints, parents, start, "Head", frames), limit + 1e-6)


class Balance(unittest.TestCase):
    def reach(self, joints, h, direction):
        hand = joints["RightHand"]
        far = {"forward": (hand[0], -0.9 * h, hand[2]), "side": (hand[0] - 0.8 * h, 0.0, hand[2] - 0.1 * h),
               "back": (hand[0], 0.8 * h, hand[2] - 0.1 * h)}
        return {"RightHand": far[direction]}

    def test_a_far_reach_keeps_the_centre_of_mass_over_the_feet(self):
        for label, shape in BODIES.items():
            joints, parents, hinges = humanoid(**shape)
            h = shape.get("height", 1.8)
            support = [joints[f] for f in FEET]
            for direction in ("forward", "side", "back"):
                with self.subTest(label + ":" + direction):
                    targets = self.reach(joints, h, direction)
                    free = body_ik_solve.solve(joints, parents, targets, pinned=FEET, hinges=hinges,
                                               tolerance=1e-3 * h)
                    self.assertGreater(outside(com(free["positions"], segments_of(joints)), support, 0.03 * h),
                                       0.01 * h, "the case needs balance")
                    out = body_ik_solve.solve(joints, parents, targets, pinned=FEET, hinges=hinges,
                                              tolerance=1e-3 * h, balance=balance_of(joints, FEET, h))
                    pos = out["positions"]
                    self.assertLessEqual(outside(com(pos, segments_of(joints)), support, 0.03 * h), 1e-3 * h)
                    self.assertLessEqual(out["balance"]["outside"], 1e-3 * h)
                    for foot in FEET:
                        self.assertLess(dist(pos[foot], joints[foot]), 1e-3 * h)
                    for j, p in parents.items():
                        if p:
                            self.assertAlmostEqual(dist(pos[j], pos[p]), dist(joints[j], joints[p]), delta=1e-6)
                    self.assertFalse(out["reached"])   # The hand is reported short, never faked.

    def test_on_one_foot_the_centre_of_mass_stays_over_that_foot(self):
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                targets = {"RightFoot": (joints["RightFoot"][0] - 0.3 * h, -0.2 * h, 0.3 * h)}   # A kick, out.
                out = body_ik_solve.solve(joints, parents, targets, pinned=("LeftFoot",), hinges=hinges,
                                          tolerance=1e-3 * h, balance=balance_of(joints, ("LeftFoot",), h))
                pos = out["positions"]
                self.assertLessEqual(outside(com(pos, segments_of(joints)), [joints["LeftFoot"]], 0.03 * h),
                                     1e-3 * h)
                self.assertLess(dist(pos["LeftFoot"], joints["LeftFoot"]), 1e-3 * h)

    def test_a_balanced_pose_is_left_as_it_was(self):
        for label, shape in BODIES.items():
            with self.subTest(label):
                joints, parents, hinges = humanoid(**shape)
                h = shape.get("height", 1.8)
                hand = joints["RightHand"]
                targets = {"RightHand": (hand[0], hand[1], hand[2] + 0.05 * h)}   # The hand raised a little.
                a = body_ik_solve.solve(joints, parents, targets, pinned=FEET, hinges=hinges, tolerance=1e-3 * h)
                self.assertEqual(outside(com(a["positions"], segments_of(joints)), [joints[f] for f in FEET],
                                         0.03 * h), 0.0, "the case must already balance")
                b = body_ik_solve.solve(joints, parents, targets, pinned=FEET, hinges=hinges, tolerance=1e-3 * h,
                                        balance=balance_of(joints, FEET, h))
                for j in joints:
                    self.assertLess(dist(a["positions"][j], b["positions"][j]), 1e-9, j)
                self.assertEqual(b["balance"]["outside"], 0.0)


if __name__ == "__main__":
    unittest.main()
