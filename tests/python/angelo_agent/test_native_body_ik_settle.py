# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settling a planted leg (``body_ik_solve._settle``): the pinned foot lands on its goal and the knee bends the side
its hinge prefers, whatever stale place the knee is in.

The class behind the 2026-11-18 miss (``test_blender_body_pose`` on fb7238ead59: a reach lowered the 1.2 m rig's
hips and both knees bent 14 cm backwards): the body had moved as a whole, the hips forward and down, while the knees
stayed where they were; settling took "the knee's side" from that stale knee, now behind the hips. Checked on legs of
unlike lengths and proportions with the top moved forward, back, sideways, down and up, the knee left at its old
place, straight or bent either way. Expected to fail on 91b38f4f5c1 (``_settle`` takes no hinge sides there).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import itertools
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import body_ik_solve  # noqa: E402

PARENTS = {"Hips": None, "Thigh": "Hips", "Knee": "Thigh", "Foot": "Knee", "Spine": "Hips"}
CHILDREN = {"Hips": ["Thigh", "Spine"], "Thigh": ["Knee"], "Knee": ["Foot"], "Foot": []}


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class Settle(unittest.TestCase):
    def test_the_knee_bends_the_preferred_side_from_any_stale_place(self):
        forward = (0.0, -1.0, 0.0)
        for (thigh, shin), move, knee_off in itertools.product(
                ((0.45, 0.42), (0.3, 0.35), (0.6, 0.5)),
                ((0.0, -0.15, -0.12), (0.0, 0.15, -0.1), (0.12, 0.0, -0.1), (0.0, 0.0, -0.2), (0.05, -0.05, 0.0)),
                (0.0, 0.03, -0.03)):   # Straight; bent back; bent forward (the knee's stale place).
            with self.subTest(legs=(thigh, shin), move=move, knee=knee_off):
                foot = (0.1, 0.0, 0.0)
                rest_top = (0.1, 0.0, thigh + shin - 0.01)
                stale_knee = (0.1, knee_off, shin)
                top = tuple(a + b for a, b in zip(rest_top, move))
                pos = {"Hips": (0.0, 0.0, top[2]), "Thigh": top, "Knee": stale_knee, "Foot": foot,
                       "Spine": (0.0, 0.0, top[2] + 0.1)}
                length = {"Thigh": 0.1, "Knee": thigh, "Foot": shin, "Spine": 0.1}
                axis = sub(foot, rest_top)
                normal = cross(axis, forward)   # A knee bending forward (body_pose's straight-limb rule).
                body_ik_solve._settle(pos, "Foot", foot, PARENTS, CHILDREN, length, normals={"Knee": normal})
                self.assertLess(math.dist(pos["Foot"], foot), 1e-9)
                self.assertAlmostEqual(math.dist(pos["Thigh"], pos["Knee"]), thigh, delta=1e-9)
                bend = cross(sub(pos["Foot"], pos["Thigh"]), sub(pos["Knee"], pos["Thigh"]))
                if math.sqrt(dot(bend, bend)) > 1e-9:   # A leg left straight has no side.
                    self.assertGreater(dot(bend, normal), 0.0)


if __name__ == "__main__":
    unittest.main()
