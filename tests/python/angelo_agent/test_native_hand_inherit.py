# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hand tools' pose composition (hand_inherit) honours every bone setting Blender applies, their compact skin
gives the same answers as the whole mesh's, and one budget bounds a whole solve. No Blender: Blender's evaluated
pose is compared in ``test_blender_hand_inherit``."""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import numpy as np
    from angelo_agent import hand_contact as C, hand_inherit as I, hand_math as M, hand_rig as H, hand_skin as S
    import hand_fixtures as F
except ImportError:
    np = None


def hand_of(**settings):
    """A synthetic hand with ``settings`` ({Skeleton keyword: {bone name: value}}) on chosen bones."""
    skel = F.hand_bones().skeleton()
    if settings:
        values = {"inherit_rotation": list(skel.inherit_rotation), "inherit_scale": list(skel.inherit_scale),
                  "local_location": list(skel.local_location), "connect": list(skel.connect)}
        for key, by_name in settings.items():
            for name, value in by_name.items():
                values[key][skel.index[name]] = value
        skel = H.Skeleton(skel.names, skel.parents, skel.rest, skel.tails, skel.basis, skel.posed, skel.deform,
                          **values)
    return skel, H.set_palm_normals(H.find_hands(skel))[0]


def rotation(axis, degrees):
    return M.axis_angle(M.unit(np.asarray(axis, dtype=np.float64)), math.radians(degrees))


@unittest.skipIf(np is None, "NumPy is not installed")
class Composition(unittest.TestCase):
    def test_full_inheritance_is_the_plain_parent_chain(self):
        skel, hand = hand_of()
        j = hand.fingers[1].joints[0]
        posed = H.forward_kinematics(skel, hand.bone, {j: rotation((0, 1, 0.3), 50)})
        child = hand.fingers[1].joints[1]
        expected = posed[j] @ np.linalg.inv(skel.rest[j]) @ skel.rest[child] @ skel.basis[child]
        np.testing.assert_allclose(posed[child], expected, atol=1e-12)

    def test_a_hinge_finger_keeps_its_rest_orientation_under_an_identity_basis(self):
        # The audit's case (2026-10-27): Inherit Rotation off on a finger bone. Its parent's turn moves its head but
        # never its orientation, so the bone_set must carry the parent's turn for the finger to follow.
        k = F.hand_bones().skeleton()
        middle = k.names[H.set_palm_normals(H.find_hands(k))[0].fingers[1].joints[1]]
        skel, hand = hand_of(inherit_rotation={middle: False})
        j, child = hand.fingers[1].joints[0], skel.index[middle]
        turn = rotation((0, 1, 0.3), 60)
        bases = {}
        posed = H.forward_kinematics(skel, hand.bone, {j: turn, child: np.eye(3)}, bases)
        # Followed the parent: the finger's direction from its head turned with the knuckle.
        before = skel.tails[child] - skel.heads[child]
        after = (posed[child] @ np.linalg.inv(skel.rest[child]) @ np.append(skel.tails[child], 1.0))[:3] \
            - posed[child][:3, 3]
        np.testing.assert_allclose(after, turn @ before, atol=1e-9)
        # ... because its basis carries the turn; an identity basis would have left it at rest orientation.
        self.assertGreater(np.linalg.norm(bases[child] - np.eye(3)), 0.5)
        plain = I.pose_matrix(skel, child, posed[j], np.eye(4))
        np.testing.assert_allclose(plain[:3, :3], skel.rest[child][:3, :3], atol=1e-12)
        np.testing.assert_allclose(plain[:3, 3], posed[child][:3, 3], atol=1e-12)   # The head still moves.
        # And the current angles read the finger relative to its parent, as the tools write them.
        basis = skel.basis.copy()
        basis[j][:3, :3], basis[child][:3, :3] = bases[j], bases[child]
        again = H.Skeleton(skel.names, skel.parents, skel.rest, skel.tails, basis, posed, skel.deform,
                           inherit_rotation=skel.inherit_rotation)
        np.testing.assert_allclose(H.current_relative(again, child), np.eye(3), atol=1e-9)

    def test_every_inherit_scale_mode_follows_blenders_rules(self):
        skel, hand = hand_of()
        j = hand.fingers[1].joints[0]
        child = hand.fingers[1].joints[1]
        parent_pose = skel.posed[j].copy()
        parent_pose[:3, :3] = parent_pose[:3, :3] @ np.diag([1.5, 0.5, 2.0])   # Non-uniform: shear below.
        offs = np.linalg.inv(skel.rest[j]) @ skel.rest[child]
        full = parent_pose @ offs
        for mode in I.INHERIT_SCALE:
            with self.subTest(mode=mode):
                rotscale, loc, post = I.parent_transform(skel.rest[child], skel.rest[j], parent_pose,
                                                         inherit_scale=mode)
                m = rotscale[:3, :3] * post
                np.testing.assert_allclose(loc[:3, 3], full[:3, 3], atol=1e-12)   # Position always follows.
                if mode == "FULL":
                    np.testing.assert_allclose(m, full[:3, :3], atol=1e-12)
                elif mode in ("NONE", "NONE_LEGACY"):
                    np.testing.assert_allclose(np.linalg.norm(rotscale[:3, :3], axis=0), 1.0, atol=1e-9)
                elif mode == "AVERAGE":
                    np.testing.assert_allclose(abs(np.linalg.det(m)), abs(np.linalg.det(parent_pose[:3, :3])),
                                               rtol=1e-9)
                elif mode == "FIX_SHEAR":
                    c = m / np.linalg.norm(m, axis=0)
                    self.assertLess(abs(c[:, 0] @ c[:, 2]), 1e-4)   # Orthogonalised (Blender's 1e-4 threshold).
                    self.assertLess(abs(c[:, 1] @ c[:, 0]), 1e-9)
        # A hinge with no scale inherited takes the parent's rest orientation, unscaled.
        rotscale, _, _ = I.parent_transform(skel.rest[child], skel.rest[j], parent_pose, inherit_rotation=False,
                                            inherit_scale="NONE")
        np.testing.assert_allclose(rotscale[:3, :3], skel.rest[child][:3, :3], atol=1e-12)

    def test_local_location_off_moves_in_the_parents_axes_and_a_connected_bone_ignores_location(self):
        skel, hand = hand_of()
        j, child = hand.fingers[1].joints[0], hand.fingers[1].joints[1]
        basis = np.eye(4)
        basis[:3, 3] = (0.01, 0.0, 0.0)
        for key, value in (("local_location", False), ("connect", True)):
            with self.subTest(setting=key):
                s, _ = hand_of(**{key: {skel.names[child]: value}})
                moved = I.pose_matrix(s, child, s.posed[j], basis)[:3, 3]
                at_rest = I.pose_matrix(s, child, s.posed[j], np.eye(4))[:3, 3]
                if key == "connect":
                    np.testing.assert_allclose(moved, at_rest, atol=1e-15)
                else:
                    np.testing.assert_allclose(moved - at_rest, s.posed[j][:3, :3] @ basis[:3, 3], atol=1e-12)

    def test_an_unknown_inherit_scale_mode_is_refused(self):
        skel, hand = hand_of()
        j, child = hand.fingers[1].joints[0], hand.fingers[1].joints[1]
        with self.assertRaises(ValueError):
            I.parent_transform(skel.rest[child], skel.rest[j], skel.posed[j], inherit_scale="SOMETIMES")


class _Bone(SimpleNamespace):
    pass


def _armature(skel, constraints=None, drivers=(), pose_position="POSE"):
    constraints = constraints or {}
    bones = {name: _Bone(constraints=constraints.get(name, [])) for name in skel.names}
    return SimpleNamespace(pose=SimpleNamespace(bones=bones), data=SimpleNamespace(pose_position=pose_position),
                           animation_data=SimpleNamespace(drivers=list(drivers)))


@unittest.skipIf(np is None, "NumPy is not installed")
class Refusals(unittest.TestCase):
    def setUp(self):
        from angelo_agent import rig_read
        self.read = rig_read
        self.skel, self.hand = hand_of()
        self.mesh = SimpleNamespace(parent=None, parent_type="OBJECT", parent_bone="")

    def test_a_constrained_bone_under_the_hand_is_named(self):
        name = self.skel.names[self.hand.fingers[0].joints[1]]
        arm = _armature(self.skel, {name: [SimpleNamespace(enabled=True, influence=1.0)]})
        self.assertEqual(self.read._driven(arm, self.skel, self.hand.bone), [name])
        off = _armature(self.skel, {name: [SimpleNamespace(enabled=False, influence=1.0)]})
        self.assertEqual(self.read._driven(off, self.skel, self.hand.bone), [])

    def test_a_deforming_bone_elsewhere_following_a_finger_is_named(self):
        forearm = self.skel.names[self.hand.forearm]
        finger = self.skel.names[self.hand.fingers[0].joints[0]]
        arm = _armature(self.skel)
        arm.pose.bones[forearm].constraints = [SimpleNamespace(enabled=True, influence=0.5, target=arm,
                                                               subtarget=finger)]
        self.assertEqual(self.read._driven(arm, self.skel, self.hand.bone), [forearm])
        target = SimpleNamespace(id=None, bone_target=finger)
        driver = SimpleNamespace(data_path='pose.bones["%s"].rotation_euler' % forearm,
                                 driver=SimpleNamespace(variables=[SimpleNamespace(targets=[target])]))
        arm2 = _armature(self.skel, drivers=[driver])
        target.id = arm2
        self.assertEqual(self.read._driven(arm2, self.skel, self.hand.bone), [forearm])

    def test_rest_position_and_a_mesh_parented_under_the_hand_are_refused(self):
        rest = _armature(self.skel, pose_position="REST")
        self.assertIn("Rest Position", self.read._unreproducible(rest, self.mesh, self.skel, self.hand.bone))
        arm = _armature(self.skel)
        self.assertIsNone(self.read._unreproducible(arm, self.mesh, self.skel, self.hand.bone))
        finger = self.skel.names[self.hand.fingers[0].joints[0]]
        parented = SimpleNamespace(parent=arm, parent_type="BONE", parent_bone=finger)
        self.assertIn("parented to bone", self.read._unreproducible(arm, parented, self.skel, self.hand.bone))
        on_hand = SimpleNamespace(parent=arm, parent_type="BONE", parent_bone=self.hand.name)
        self.assertIsNone(self.read._unreproducible(arm, on_hand, self.skel, self.hand.bone))


def _with_unrelated(skin, count):
    """``skin`` with ``count`` unrelated vertices (a far grid, its own triangles and edges, no weights) appended."""
    n = len(skin.rest)
    side = int(math.ceil(math.sqrt(count)))
    grid = np.array([(5.0 + i * 0.01, 5.0 + k * 0.01, 0.0) for i in range(side) for k in range(side)])[:count]
    tris, edges = [], []
    for i in range(side - 1):
        for k in range(side - 1):
            a = i * side + k
            if a + side + 1 < count:
                tris.append((n + a, n + a + 1, n + a + side))
                edges += [(n + a, n + a + 1), (n + a, n + a + side)]
    return S.Skin(np.concatenate([skin.rest, grid]), skin.vertex, skin.bone, skin.weight,
                  np.concatenate([skin.triangles, np.array(tris, dtype=np.int64).reshape(-1, 3)]),
                  np.concatenate([skin.edges, np.array(edges, dtype=np.int64).reshape(-1, 2)]))


@unittest.skipIf(np is None, "NumPy is not installed")
class CompactSkin(unittest.TestCase):
    def test_unrelated_vertices_change_nothing_the_tools_measure(self):
        skel, hand = hand_of()
        skin = F.tube_skin(skel)
        alone, crowded = S.HandSkin(hand, skin), S.HandSkin(hand, _with_unrelated(skin, 5000))
        self.assertEqual(len(crowded.skin.rest), len(alone.skin.rest))
        self.assertAlmostEqual(crowded.finger_radius, alone.finger_radius, places=12)
        np.testing.assert_array_equal(skin.rest[crowded.mesh_index], crowded.skin.rest)
        self.assertEqual(S.weight_audit(crowded), S.weight_audit(alone))
        frames = H.joint_frames(hand)
        j = hand.fingers[1].joints[0]
        posed = H.forward_kinematics(skel, hand.bone, {j: H.joint_rotation(frames[j], 70, 0, 0)})
        positions = [hs.skin.rest.copy() for hs in (alone, crowded)]
        for p, hs in zip(positions, (alone, crowded)):
            p[hs.vertices] = hs.skin.deform(skel, posed, hs.vertices)
        np.testing.assert_allclose(positions[0], positions[1], atol=1e-15)
        pairs = [(1, 2), (2, 1), (0, 1)]
        a = C.Contact(alone).measure(positions[0], posed, pairs, alone.skin.rest, skel.posed)
        b = C.Contact(crowded).measure(positions[1], posed, pairs, crowded.skin.rest, skel.posed)
        self.assertEqual(a, b)

    def test_a_surface_built_from_its_own_vertices_finds_the_same_skin(self):
        skel, hand = hand_of()
        skin = _with_unrelated(F.tube_skin(skel), 400)
        tri = skin.triangles[:60]
        surface = C.Surface(skin.rest, tri)
        self.assertEqual(len(surface.positions), len(np.unique(tri)))
        points = skin.rest[tri[:, 0]] + 0.001
        hits, _, index = surface.nearest(points, 0.05)
        brute = [int(np.argmin([np.linalg.norm(C._closest_on_triangles(p, *(skin.rest[tri[:, i]] for i in range(3)))
                                               [t] - p) for t in range(len(tri))])) for p in points]
        np.testing.assert_array_equal(index, brute)

    def test_the_flood_reaches_what_a_plain_search_reaches(self):
        rng = np.random.default_rng(3)
        count = 300
        edges = rng.integers(0, count, size=(500, 2))
        allowed = rng.random(count) > 0.2
        seeds = np.where(allowed)[0][:3]
        seen, stack = set(int(s) for s in seeds), [int(s) for s in seeds]
        while stack:
            v = stack.pop()
            for a, b in edges:
                if allowed[a] and allowed[b] and v in (a, b):
                    w = int(b if a == v else a)
                    if w not in seen:
                        seen.add(w)
                        stack.append(w)
        np.testing.assert_array_equal(S._flood(count, edges, seeds, allowed), sorted(seen))


@unittest.skipIf(np is None, "NumPy is not installed")
class WholeSolveBudget(unittest.TestCase):
    def setUp(self):
        from angelo_agent import hand_pose as P
        self.P = P
        skel, hand = hand_of()
        self.hs = S.HandSkin(hand, F.tube_skin(skel))

    def test_a_budget_spent_before_the_solve_refuses_with_the_time_it_took(self):
        spent = M.Budget(1.0, started=M.Budget(1.0).clock() - 5.0)
        with self.assertRaisesRegex(M.HandError, "budget ran out before hand .* nothing was solved"):
            self.P.fist(self.hs, budget=spent)

    def test_a_budget_running_out_mid_solve_stops_with_the_stage_named(self):
        budget = M.Budget(3600.0)
        calls = {"n": 0}

        def expire_after_some_steps():
            calls["n"] += 1
            return calls["n"] > 4
        budget.expired = expire_after_some_steps
        out = self.P.fist(self.hs, budget=budget)
        log = out["log"]
        self.assertFalse(log["complete"])
        self.assertEqual(log["budget"]["ran_out_during"], "folding the fingers")
        for j, angles in out["angles"].items():   # A partial result is still within the limits.
            self.assertFalse(H.outside(angles, out["solver"].limits[j]))


if __name__ == "__main__":
    unittest.main()
