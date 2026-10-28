# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hand rig math (hand_rig): hand detection, palm normals, joint frames and angles, forward kinematics, skinning,
the checklist and the weight audit, on synthetic hands of several layouts (hand_fixtures). No Blender."""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import numpy as np
    from angelo_agent import hand_rig as H, hand_skin as S
    import hand_fixtures as F
except ImportError:
    np = H = S = F = None


def hand_of(**kwargs):
    builder = F.hand_bones(**kwargs)
    skel = builder.skeleton()
    hands = H.set_palm_normals(H.find_hands(skel))
    return skel, hands


@unittest.skipIf(np is None, "NumPy is not installed")
class Detection(unittest.TestCase):
    def test_a_hand_is_found_with_its_thumb_and_fingers_in_order(self):
        for kwargs in ({}, {"fingers": 3}, {"metacarpals": True}, {"end_bones": True}, {"side": "Right"},
                       {"t_pose": True}, {"middle": 0.03, "palm": 0.035}):
            with self.subTest(**kwargs):
                skel, hands = hand_of(**kwargs)
                self.assertEqual(len(hands), 1)
                hand = hands[0]
                self.assertTrue(skel.names[hand.thumb.joints[0]].endswith("Thumb1"))
                roles = [d.role for d in hand.fingers]
                names = [skel.names[d.joints[0]] for d in hand.fingers]
                for role, name in zip(roles, names):
                    self.assertIn(role.capitalize(), name)
                self.assertEqual(len(roles), kwargs.get("fingers", 4))
                if kwargs.get("metacarpals"):
                    self.assertTrue(all(d.metacarpal is not None for d in hand.fingers))
                self.assertTrue(all(len(d.joints) == 3 for d in hand.digits))

    def test_chains_that_are_no_hand_never_refuse_the_hands(self):
        # A skirt (four evenly spread two-bone chains under the hips) has no thumb: unnamed, it is skipped.
        b = F.hand_bones()
        for k in range(4):
            a = k * math.pi / 2
            top = np.array([0.1 * math.cos(a), 0.1 * math.sin(a), 0.9])
            parent = b.bone("Skirt%d_1" % k, "Hips", top, top + (0, 0, -0.1))
            b.bone("Skirt%d_2" % k, parent, top + (0, 0, -0.1), top + (0, 0, -0.2))
        skel = b.skeleton()
        self.assertEqual([h.name for h in H.find_hands(skel)], ["LeftHand"])
        with self.assertRaisesRegex(H.HandError, "clearly the thumb"):
            H.find_hands(skel, hand="Hips")

    def test_a_named_bone_that_is_no_hand_is_refused_with_the_reason(self):
        skel, _ = hand_of()
        with self.assertRaisesRegex(H.HandError, "above a hand"):
            H.find_hands(skel, hand="LeftForeArm")
        with self.assertRaisesRegex(H.HandError, "digit chains"):
            H.find_hands(skel, hand="LeftHandIndex1")
        with self.assertRaisesRegex(H.HandError, "no bone named"):
            H.find_hands(skel, hand="Nope")


@unittest.skipIf(np is None, "NumPy is not installed")
class PalmNormal(unittest.TestCase):
    def test_hanging_hands_face_the_midline_and_a_t_pose_follows_the_curl(self):
        _, (left,) = hand_of(side="Left")
        self.assertEqual(left.palm_source, "MIDLINE")
        self.assertGreater(left.palm_normal @ np.array([-1.0, 0, 0]), 0.99)
        _, (right,) = hand_of(side="Right")
        self.assertGreater(right.palm_normal @ np.array([1.0, 0, 0]), 0.99)
        _, (t_pose,) = hand_of(t_pose=True)
        self.assertEqual(t_pose.palm_source, "FINGER_CURL")
        self.assertGreater(t_pose.palm_normal @ np.array([0, 0, -1.0]), 0.95)

    def test_a_t_pose_with_straight_fingers_asks_for_the_normal(self):
        skel = F.hand_bones(t_pose=True, rest_curl=0.0).skeleton()
        hands = H.find_hands(skel)
        with self.assertRaisesRegex(H.HandError, "give palm_normal"):
            H.set_palm_normals(hands)
        H.set_palm_normals(hands, {"LeftHand": (0, 0, -1)})
        self.assertEqual(hands[0].palm_source, "GIVEN")

    def test_the_normal_ignores_the_knuckle_arc(self):
        # The knuckles' arch never tilts the palm normal: it comes from the forearm and the midline only.
        _, (flat,) = hand_of(rest_curl=8.0)
        b = F.hand_bones()
        for name in list(b.names):
            if "Little" in name or "Ring" in name:
                i = b.names.index(name)
                b.heads[i] = b.heads[i] + np.array([-0.004, 0, 0])
                b.tails[i] = b.tails[i] + np.array([-0.004, 0, 0])
        (arched,) = H.set_palm_normals(H.find_hands(b.skeleton()))
        self.assertGreater(flat.palm_normal @ arched.palm_normal, 0.9999)


@unittest.skipIf(np is None, "NumPy is not installed")
class FramesAndAngles(unittest.TestCase):
    def test_flex_curls_toward_the_palm_and_spread_toward_the_thumb(self):
        skel, (hand,) = hand_of()
        frames = H.joint_frames(hand)
        index = hand.fingers[0].joints[0]
        frame = frames[index]
        curled = H.joint_rotation(frame, 60, 0, 0) @ frame.a
        self.assertGreater(curled @ hand.palm_normal, math.sin(math.radians(55)))
        spread = H.joint_rotation(frame, 0, 15, 0) @ frame.a
        thumb_side = skel.heads[hand.thumb.joints[0]] - skel.heads[index]
        self.assertGreater((spread - frame.a) @ thumb_side, 0)

    def test_angles_round_trip(self):
        _, (hand,) = hand_of()
        frames = H.joint_frames(hand)
        rng = np.random.default_rng(3)
        for j, frame in frames.items():
            for _ in range(20):
                want = (rng.uniform(-80, 100), rng.uniform(-25, 25), rng.uniform(-30, 30))
                got = H.joint_angles(H.joint_rotation(frame, *want), frame)
                np.testing.assert_allclose(got, want, atol=1e-6)

    def test_frames_do_not_depend_on_bone_roll(self):
        _, (a,) = hand_of(roll_seed=1)
        _, (b,) = hand_of(roll_seed=2)
        fa, fb = H.joint_frames(a), H.joint_frames(b)
        for j in fa:
            np.testing.assert_allclose(fa[j].a, fb[j].a, atol=1e-12)
            np.testing.assert_allclose(fa[j].r, fb[j].r, atol=1e-12)


@unittest.skipIf(np is None, "NumPy is not installed")
class Kinematics(unittest.TestCase):
    def test_a_joint_rotation_written_as_a_pose_basis_reads_back(self):
        skel, (hand,) = hand_of(roll_seed=5)
        frames = H.joint_frames(hand)
        j = hand.fingers[1].joints[0]
        rel = H.joint_rotation(frames[j], 70, 5, 0)
        posed = H.forward_kinematics(skel, hand.bone, {j: rel})
        child = hand.fingers[1].joints[1]
        # The child's head moved with the knuckle: its direction from the knuckle turned by rel.
        before = skel.heads[child] - skel.heads[j]
        after = posed[child][:3, 3] - posed[j][:3, 3]
        np.testing.assert_allclose(after, rel @ before, atol=1e-9)
        # The basis that gives rel, put back into the skeleton, reads as rel again.
        basis = skel.basis.copy()
        basis[j][:3, :3] = H.basis_rotation(skel, j, rel, posed)
        again = H.Skeleton(skel.names, skel.parents, skel.rest, skel.tails, basis, posed, skel.deform)
        np.testing.assert_allclose(H.current_relative(again, j), rel, atol=1e-9)
        np.testing.assert_allclose(H.euler_matrix(H.euler_xyz(basis[j][:3, :3])), basis[j][:3, :3], atol=1e-9)

    def test_skinning_the_rest_pose_keeps_the_rest_positions_and_a_pose_moves_only_its_skin(self):
        skel, (hand,) = hand_of()
        skin = F.tube_skin(skel)
        np.testing.assert_allclose(skin.deform(skel, skel.posed), skin.rest, atol=1e-12)
        frames = H.joint_frames(hand)
        j = hand.fingers[0].joints[0]
        posed = H.forward_kinematics(skel, hand.bone, {j: H.joint_rotation(frames[j], 80, 0, 0)})
        moved = np.linalg.norm(skin.deform(skel, posed) - skin.rest, axis=1) > 1e-9
        index_share = skin.weights_of(hand.fingers[0].joints)
        self.assertTrue(np.all(index_share[moved] > 0))
        self.assertTrue(moved.any())


@unittest.skipIf(np is None, "NumPy is not installed")
class SkinAndChecklist(unittest.TestCase):
    def test_finger_radius_and_parts(self):
        skel, (hand,) = hand_of()
        hs = S.HandSkin(hand, F.tube_skin(skel, radius=0.0075))
        self.assertAlmostEqual(hs.finger_radius, 0.0075, delta=0.0008)
        self.assertEqual(hs.foreign_bones, [])
        self.assertTrue(all(len(hs.part_vertices(k)) for k in range(len(hand.digits))))

    def test_weight_from_outside_the_arm_is_reported(self):
        skel, (hand,) = hand_of()
        hs = S.HandSkin(hand, F.tube_skin(skel, leak=0.2))
        self.assertEqual(hs.foreign_bones, ["LeftUpLeg"])
        self.assertGreater(S.weight_audit(hs)["foreign_vertices"], 0)

    def test_a_weight_step_is_a_cut_and_a_fade_is_not(self):
        skel, (hand,) = hand_of()
        # Joints within a digit fade over two rings of edges (0.5 each): not cuts. Where a digit's tube meets the
        # palm's the weight steps from the hand bone to the digit's: cuts, as the fixture is made.
        cuts = S.weight_audit(S.HandSkin(hand, F.tube_skin(skel)))["cuts"]
        self.assertNotIn("LeftHandMiddle2", cuts)
        self.assertIn("LeftHandMiddle1", cuts)
        audit = S.weight_audit(S.HandSkin(hand, F.tube_skin(skel, cut="LeftHandMiddle2")))
        self.assertEqual(audit["cuts"]["LeftHandMiddle2"]["largest_jump"], 1.0)
        self.assertEqual(audit["cuts"]["LeftHandMiddle2"]["worst_near_joint"], "LeftHandMiddle2")

    def test_checklist_passes_the_proportioned_hand_and_fails_a_short_middle_finger(self):
        _, (hand,) = hand_of()
        items = {i["item"]: i["ok"] for i in S.checklist(hand)}
        self.assertTrue(all(items.values()), items)
        old = dict(F.LENGTH)
        try:
            F.LENGTH["middle"] = 0.8
            _, (short,) = hand_of()
        finally:
            F.LENGTH.clear()
            F.LENGTH.update(old)
        items = {i["item"]: i["ok"] for i in S.checklist(short)}
        self.assertFalse(items["#4 the middle finger is the longest"])

    def test_limits_default_by_role_and_take_overrides(self):
        skel, (hand,) = hand_of()
        limits = H.limits_for(hand, {"LeftHandIndex1": {"flex": [-10, 80]}})
        index = hand.fingers[0].joints[0]
        self.assertEqual(limits[index]["flex"], (-10.0, 80.0))
        self.assertEqual(limits[hand.fingers[-1].joints[0]]["flex"], (-30, 100))
        self.assertEqual(H.outside((95, 0, 0), limits[index]), {"flex": 15.0})
        with self.assertRaises(H.HandError):
            H.limits_for(hand, {"LeftHandIndex1": {"flex": [10, -10]}})


if __name__ == "__main__":
    unittest.main()
