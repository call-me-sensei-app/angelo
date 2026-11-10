# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check without Blender (doc/angelo/engine_skeleton.rst).

The rigs here are built from joint positions, so every check runs on known layouts: the same humanoid named four
ways (UE5 Mannequin, Mixamo, Rigify metarig, arbitrary), posed T and A, split into twist segments, turned to face
another way, scaled, with control bones, without a root, and with too many influences. They are the development
rigs; the held-out rigs (real Rigify, Mixamo-rigged and hand-made characters) run in Blender
(test_blender_engine_skeleton.py), and the engines' own imports in the engine e2e.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import skeleton_check, skeleton_checks, skeleton_profiles as profiles, skeleton_unreal  # noqa: E402
from angelo_agent.skeleton_roles import Rig, map_roles  # noqa: E402

FORWARD = np.array([0.0, -1.0, 0.0])
FINGER_Y = {"Index": -0.027, "Middle": -0.009, "Ring": 0.009, "Little": 0.027}


def _ue(role):
    return profiles.UNREAL.names.get(role)


UE_EXTRA = {"spine_03": "spine_03", "spine_04": "spine_04", "neck_02": "neck_02"}


def _axes(direction, hint):
    """Blender-like bone axes: Y along the bone, Z toward ``hint`` (orthogonalised), X = Y x Z."""
    y = direction / np.linalg.norm(direction)
    z = hint - y * float(hint @ y)
    if np.linalg.norm(z) < 1e-6:
        z = np.array([0.0, 0.0, 1.0]) - y * y[2]
    z /= np.linalg.norm(z)
    return np.stack([np.cross(y, z), y, z], axis=1)


class Builder:
    """Joint positions of a 1.8 m humanoid facing -Y (left is +X), as named bones."""

    def __init__(self):
        self.bones = []   # (name, parent, head, tail, z hint, deform)

    def add(self, name, parent, head, tail, hint=FORWARD, deform=True):
        self.bones.append((name, parent, np.array(head, float), np.array(tail, float), np.asarray(hint, float), deform))
        return name

    def rig(self, rotate_z=0.0, scale=1.0, weighted=None):
        names = [b[0] for b in self.bones]
        index = {n: i for i, n in enumerate(names)}
        c, s = math.cos(rotate_z), math.sin(rotate_z)
        turn = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]]) * scale
        heads = np.array([turn @ b[2] for b in self.bones])
        tails = np.array([turn @ b[3] for b in self.bones])
        axes = np.array([turn @ _axes(b[3] - b[2], b[4]) / scale for b in self.bones])
        return Rig(names, [index[b[1]] if b[1] else -1 for b in self.bones], heads, tails, axes,
                   [b[5] for b in self.bones], weighted)


def humanoid(name=lambda role, fallback: fallback, pose="T", spine=5, neck=2, root=True, fingers=True, twist=False,
             metacarpals=True, controls=0, clavicles=True):
    """The humanoid with each bone named ``name(role or None, default UE name)``."""
    b = Builder()
    n = name
    parent = None
    if root:
        parent = b.add(n("Root", "root"), None, (0, 0, 0), (0, -0.25, 0))
    hips = b.add(n("Hips", "pelvis"), parent, (0, 0, 1.0), (0, 0, 1.1))
    previous, z = hips, 1.1
    step = 0.35 / spine
    spine_roles = {0: "Spine", 1: "Chest", spine - 1: "UpperChest"} if spine >= 3 else {0: "Spine", 1: "Chest"}
    for k in range(spine):
        previous = b.add(n(spine_roles.get(k), "spine_%02d" % (k + 1)), previous, (0, 0, z), (0, 0, z + step))
        z += step
    chest = previous
    neck_step = 0.1 / max(neck, 1)
    for k in range(neck):
        previous = b.add(n("Neck" if k == 0 else None, "neck_%02d" % (k + 1)), previous, (0, 0, z), (0, 0, z + neck_step))
        z += neck_step
    head = b.add(n("Head", "head"), previous, (0, 0, z), (0, 0, z + 0.25))
    b.add(n("LeftEye", "eye_l"), head, (0.03, -0.08, z + 0.1), (0.03, -0.1, z + 0.1))
    b.add(n("RightEye", "eye_r"), head, (-0.03, -0.08, z + 0.1), (-0.03, -0.1, z + 0.1))
    drop = math.radians(45.0) if pose == "A" else 0.0
    for side, sign, s in (("Left", 1.0, "l"), ("Right", -1.0, "r")):
        clavicle = (b.add(n(side + "Shoulder", "clavicle_" + s), chest, (sign * 0.02, 0, 1.42), (sign * 0.15, 0, 1.42))
                    if clavicles else chest)
        shoulder = np.array([sign * 0.15, 0, 1.42])
        out = np.array([sign * math.cos(drop), 0.0, -math.sin(drop)])
        elbow, wrist, tip = shoulder + out * 0.28, shoulder + out * 0.53, shoulder + out * 0.61
        if twist:
            upper = b.add(n(side + "UpperArm", "upperarm_" + s), clavicle, shoulder, (shoulder + elbow) / 2)
            upper = b.add(n(None, "upperarm_twist_" + s), upper, (shoulder + elbow) / 2, elbow)
            lower = b.add(n(side + "LowerArm", "lowerarm_" + s), upper, elbow, (elbow + wrist) / 2)
            lower = b.add(n(None, "lowerarm_twist_" + s), lower, (elbow + wrist) / 2, wrist)
        else:
            upper = b.add(n(side + "UpperArm", "upperarm_" + s), clavicle, shoulder, elbow)
            lower = b.add(n(side + "LowerArm", "lowerarm_" + s), upper, elbow, wrist)
        hand = b.add(n(side + "Hand", "hand_" + s), lower, wrist, tip)
        if fingers:
            across = np.array([0.0, 1.0, 0.0])
            palm = np.cross(out, across) * sign   # out of the palm: down in a T-pose
            for finger, offset in FINGER_Y.items():
                ue = {"Index": "index", "Middle": "middle", "Ring": "ring", "Little": "pinky"}[finger]
                base = tip + across * offset
                prev = hand
                if metacarpals:
                    prev = b.add(n(side + finger + "Metacarpal", "%s_metacarpal_%s" % (ue, s)), hand,
                                 wrist + across * offset * 0.6, base, palm)
                for k, joint in enumerate(profiles.FINGER_JOINTS):
                    head_p = base + out * 0.03 * k
                    prev = b.add(n(side + finger + joint, "%s_%02d_%s" % (ue, k + 1, s)), prev, head_p,
                                 head_p + out * 0.03, palm)
            thumb_dir = (out * 0.6 - across * 0.8)
            prev, start = hand, wrist + out * 0.02 - across * 0.02
            for k, joint in enumerate(profiles.THUMB_JOINTS):
                head_p = start + thumb_dir * 0.03 * k
                prev = b.add(n(side + "Thumb" + joint, "thumb_%02d_%s" % (k + 1, s)), prev, head_p,
                             head_p + thumb_dir * 0.03, palm)
    for side, sign, s in (("Left", 1.0, "l"), ("Right", -1.0, "r")):
        hip, knee, ankle = (sign * 0.1, 0, 1.0), (sign * 0.1, -0.01, 0.53), (sign * 0.1, 0, 0.08)
        if twist:
            thigh = b.add(n(side + "UpperLeg", "thigh_" + s), hips, hip, (sign * 0.1, -0.005, 0.765))
            thigh = b.add(n(None, "thigh_twist_" + s), thigh, (sign * 0.1, -0.005, 0.765), knee)
        else:
            thigh = b.add(n(side + "UpperLeg", "thigh_" + s), hips, hip, knee)
        calf = b.add(n(side + "LowerLeg", "calf_" + s), thigh, knee, ankle, -FORWARD)
        foot = b.add(n(side + "Foot", "foot_" + s), calf, ankle, (sign * 0.1, -0.12, 0.02))
        b.add(n(side + "Toes", "ball_" + s), foot, (sign * 0.1, -0.12, 0.02), (sign * 0.1, -0.2, 0.02))
    for k in range(controls):
        b.add("CTRL-%d" % k, None, (0.3 * k, 0.5, 0), (0.3 * k, 0.6, 0), deform=False)
    return b


def facts(rig, armature="Armature", rotation=(0, 0, 0), scale=(1, 1, 1), meshes=(), **extra):
    out = {"armature": armature, "rig": rig, "object": {"rotation": list(rotation), "scale": list(scale)},
           "unit_scale": 1.0, "constraints": {}, "drivers": set(), "bbones": {}, "meshes": list(meshes)}
    out.update(extra)
    return out


def by_check(report):
    return {f["check"]: f for f in report["findings"]}


MIXAMO = {"Root": None, "Hips": "Hips", "Spine": "Spine", "Chest": "Spine1", "UpperChest": "Spine2", "Neck": "Neck",
          "Head": "Head"}


def mixamo(role, fallback):
    if role is None:
        return "mixamorig:extra_" + fallback
    if role in MIXAMO:
        return "mixamorig:" + MIXAMO[role]
    for side in profiles.SIDES:
        if role.startswith(side):
            rest = role[len(side):]
            table = {"Shoulder": "Shoulder", "UpperArm": "Arm", "LowerArm": "ForeArm", "Hand": "Hand",
                     "UpperLeg": "UpLeg", "LowerLeg": "Leg", "Foot": "Foot", "Toes": "ToeBase", "Eye": "Eye"}
            if rest in table:
                return "mixamorig:" + side + table[rest]
            for finger, mixamo_finger in (("Thumb", "Thumb"), ("Index", "Index"), ("Middle", "Middle"),
                                          ("Ring", "Ring"), ("Little", "Pinky")):
                if rest.startswith(finger):
                    joint = rest[len(finger):]
                    k = {"Metacarpal": 1, "Proximal": 2 if finger == "Thumb" else 1, "Intermediate": 2,
                         "Distal": 3}[joint]
                    return "mixamorig:%sHand%s%d" % (side, mixamo_finger, k)
    return "mixamorig:" + fallback


_ARBITRARY = {}


def arbitrary(role, fallback):
    """A name that says nothing about the bone, the same for the same bone every time."""
    return _ARBITRARY.setdefault(fallback, "Bone.%03d" % len(_ARBITRARY))


class MappingTests(unittest.TestCase):
    """The layout finds the same roles whatever the names, the facing, the scale or the twist segments."""

    def expect_ue_roles(self, rig, mapping, names):
        for role, ue in profiles.UNREAL.names.items():
            if role == "Root":
                continue
            with self.subTest(role=role):
                self.assertIn(role, mapping.roles, mapping.notes)
                self.assertEqual(rig.names[mapping.roles[role]], names(ue), mapping.notes)

    def test_every_role_of_the_mannequin_layout(self):
        rig = humanoid().rig()
        mapping = map_roles(rig)
        self.expect_ue_roles(rig, mapping, lambda ue: ue)
        self.assertEqual(rig.names[mapping.roles["LeftEye"]], "eye_l")
        self.assertTrue(mapping.facing_measured)
        np.testing.assert_allclose(mapping.left, [1, 0, 0], atol=1e-6)

    def test_names_never_matter(self):
        named = humanoid(name=arbitrary)
        plain = humanoid()
        a, b = map_roles(named.rig()), map_roles(plain.rig())
        self.assertEqual(a.roles, b.roles)

    def test_a_turned_scaled_rig_maps_the_same(self):
        for turn in (math.pi / 2, math.pi, -2.5):
            with self.subTest(turn=turn):
                rig = humanoid().rig(rotate_z=turn, scale=0.01)
                self.expect_ue_roles(rig, map_roles(rig), lambda ue: ue)

    def test_twist_segments_leave_elbow_and_knee_in_place(self):
        rig = humanoid(twist=True).rig()
        mapping = map_roles(rig)
        for role, name in (("LeftUpperArm", "upperarm_l"), ("LeftLowerArm", "lowerarm_l"),
                           ("RightUpperLeg", "thigh_r"), ("RightLowerLeg", "calf_r")):
            self.assertEqual(rig.names[mapping.roles[role]], name)

    def test_a_pose_and_short_spines(self):
        for spine, neck in ((1, 0), (2, 1), (3, 1), (5, 2)):
            with self.subTest(spine=spine, neck=neck):
                rig = humanoid(pose="A", spine=spine, neck=neck).rig()
                mapping = map_roles(rig)
                self.assertEqual(len(mapping.spine), spine)
                self.assertEqual(len(mapping.neck), neck)
                self.assertEqual(rig.names[mapping.roles["Head"]], "head")
                self.assertEqual(rig.names[mapping.roles["LeftHand"]], "hand_l")

    def test_unweighted_end_bones_are_left_out(self):
        b = humanoid()
        b.add("head_end", "head", (0, 0, 1.8), (0, 0, 1.9))
        rig = b.rig()
        self.assertEqual(rig.names[map_roles(rig).roles["Head"]], "head")
        weighted = np.ones(len(b.bones), dtype=bool)
        weighted[-1] = False
        b.add("hair", "head", (0, 0, 1.8), (0, 0.01, 1.95))
        rig = b.rig(weighted=list(weighted) + [False])
        self.assertEqual(rig.names[map_roles(rig).roles["Head"]], "head")

    def test_no_legs_says_why(self):
        b = Builder()
        b.add("a", None, (0, 0, 0), (0, 0, 1))
        b.add("b", "a", (0, 0, 1), (0, 0, 2))
        b.add("c", "b", (0, 0, 2), (0, 0, 3))
        mapping = map_roles(b.rig())
        self.assertNotIn("Hips", mapping.roles)
        self.assertTrue(any("legs" in note for note in mapping.notes), mapping.notes)


class UnrealTests(unittest.TestCase):
    def test_a_mannequin_named_rig_is_recognised(self):
        report = skeleton_check.check(facts(humanoid().rig()), "UNREAL")
        found = by_check(report)
        self.assertEqual(found["auto_retarget"]["status"], "pass", found["auto_retarget"])
        self.assertEqual(found["bone_match"]["status"], "pass", found["bone_match"])
        self.assertEqual(report["format"], "GLB")

    def test_a_prefix_on_every_bone_is_removed_but_not_on_most(self):
        every = humanoid(name=lambda role, fallback: "DEF-" + fallback)
        self.assertEqual(by_check(skeleton_check.check(facts(every.rig()), "UNREAL"))["auto_retarget"]["status"],
                         "pass")
        most = humanoid(name=lambda role, fallback: fallback if fallback == "root" else "DEF-" + fallback)
        found = by_check(skeleton_check.check(facts(most.rig()), "UNREAL"))["auto_retarget"]
        self.assertEqual((found["status"], found["prediction"]), ("warn", "unmatched"), found)
        self.assertEqual(found["details"][0]["prefix"], "DEF-")

    def test_other_names_are_matched_by_layout_and_renamed(self):
        report = skeleton_check.check(facts(humanoid(name=arbitrary).rig()), "UNREAL")
        found = by_check(report)
        self.assertEqual((found["auto_retarget"]["status"], found["auto_retarget"]["prediction"]),
                         ("warn", "unmatched"))
        self.assertEqual(found["bone_match"]["status"], "warn")
        renames = next(d["renames"] for d in found["bone_match"]["details"] if "renames" in d)
        self.assertIn({"from": arbitrary("Hips", "pelvis"), "to": "pelvis"}, renames)

    def test_mixamo_and_rigify_metarig_names_match_their_templates(self):
        result = skeleton_unreal.characterise(
            [(b[0], b[1]) for b in humanoid(name=mixamo, root=False, metacarpals=False).bones], "FBX")
        self.assertEqual(result["best"]["template"], "Mixamo")
        self.assertTrue(result["recognised"])
        def blender(name):   # How Blender names them: upper_arm.L, not upper_arm_L.
            return name if name is None or name[-2:] not in ("_L", "_R") else name[:-2] + "." + name[-1]
        dotted = [(blender(n), blender(p)) for n, p in profiles._rigify_metarig()]
        result = skeleton_unreal.characterise(dotted, "FBX")
        self.assertEqual(result["best"]["template"], "Blender Rigify")
        self.assertGreater(result["best"]["score"], 0.5)

    def test_the_template_is_chosen_by_bones_under_their_parent_not_by_score(self):
        # Rigify's generated rig as a .glb: many template names, none under its template parent. Unreal chooses no
        # template then (5.8.2 applied nothing), however high the name share.
        flat = [("rig", None)] + [(n, "rig") for n, _ in profiles._rigify_metarig()[1:]]
        result = skeleton_unreal.characterise(flat, "GLB")
        rigify = next(s for s in result["scores"] if s["template"] == "Blender Rigify")
        self.assertGreater(rigify["score"], 0.4)
        self.assertEqual(rigify["parented"], 0)
        self.assertIsNone(result["best"])
        self.assertEqual(result["prediction"], "unmatched")

    def test_recognised_only_when_no_unmodelled_template_could_change_it(self):
        # A template Angelo doesn't model is chosen only with more matching bones, so its score is at least that
        # count over the largest template; below the threshold there, the outcome is uncertain.
        bones = list(profiles._mixamo())
        for keep, expected in ((len(bones), "recognised"), (9, "uncertain"), (10, "recognised")):
            result = skeleton_unreal.characterise(bones[:keep], "FBX")
            self.assertEqual(result["best"]["template"], "Mixamo")
            self.assertEqual(result["prediction"], expected, (keep, result["bound"], result["best"]["score"]))
        self.assertEqual(result["largest"], profiles.UE_LARGEST_TEMPLATE)

    def test_names_read_as_unreals_import_writes_them(self):
        # Unreal 5.8's import (Interchange, FBX and glTF): an FBX drops the namespace, so Mixamo's "mixamorig:" names
        # under an armature-object root still match (100Avatars Jennifer: 17 chains applied); a glTF keeps it as a
        # prefix every bone shares, which the characteriser removes.
        bones = [("Armature_char_052", None)] + [("mixamorig:" + name, "mixamorig:" + parent if parent else
                                                 "Armature_char_052") for name, parent in profiles._mixamo()]
        self.assertEqual(skeleton_unreal.characterise(bones, "FBX")["prediction"], "recognised")
        glb = skeleton_unreal.characterise(bones[1:], "GLB")
        self.assertEqual((glb["prefix"], glb["prediction"]), ("mixamorig_", "recognised"))
        for name, fmt, shown in (("ns:sub:Hips", "FBX", "Hips"), ("ns:Hips", "GLB", "ns_Hips"),
                                 ("Left Arm", "FBX", "Left-Arm"), ("arm+L", "GLB", "arm_L"),
                                 ("f(1)|a.L", "FBX", "f_1__a_L")):
            self.assertEqual(skeleton_unreal.engine_name(name, fmt), shown)

    def test_the_armature_object_is_a_root_bone_in_an_fbx_unless_named_armature(self):
        rig = humanoid().rig()
        named = by_check(skeleton_check.check(facts(rig, armature="Armature"), "UNREAL", "FBX"))
        other = by_check(skeleton_check.check(facts(rig, armature="Hero"), "UNREAL", "FBX"))
        self.assertEqual(named["single_root"]["status"], "pass")
        self.assertIn("Hero", other["single_root"]["found"])
        self.assertIn("Hero", other["root_motion"]["found"])

    def test_a_gltf_with_several_roots_gets_unreals_proxy_root(self):
        """Observed in UE 5.8.2 (skeleton_engine_e2e): one joint above a glTF skin's several roots."""
        rig = humanoid(controls=2).rig()
        glb = by_check(skeleton_check.check(facts(rig, armature="Hero"), "UNREAL", "GLB"))
        self.assertEqual(glb["single_root"]["status"], "pass")
        self.assertIn("Hero_ProxyTrueRootJoint", glb["single_root"]["found"])

    def test_no_root_bone_fails_root_motion(self):
        found = by_check(skeleton_check.check(facts(humanoid(root=False).rig()), "UNREAL"))
        self.assertEqual(found["root_motion"]["status"], "fail")
        self.assertIn("pelvis", found["root_motion"]["fix"])


class UnityGodotTests(unittest.TestCase):
    def test_any_names_pass_unity_and_godot_maps_them(self):
        for target in ("UNITY", "GODOT"):
            with self.subTest(target=target):
                found = by_check(skeleton_check.check(facts(humanoid(name=arbitrary).rig()), target))
                self.assertIn(found["bone_match"]["status"], ("pass", "info"), found["bone_match"])
                self.assertEqual(found["rest_pose"]["status"], "pass", found["rest_pose"])

    def test_an_a_pose_is_not_a_t_pose(self):
        found = by_check(skeleton_check.check(facts(humanoid(pose="A").rig()), "UNITY"))
        self.assertEqual(found["rest_pose"]["status"], "warn")
        self.assertIn("upper arm 45°", found["rest_pose"]["found"])
        self.assertEqual(by_check(skeleton_check.check(facts(humanoid(pose="A").rig()), "UNREAL"))
                         ["rest_pose"]["status"], "pass")

    def test_a_missing_required_bone_fails(self):
        b = humanoid(spine=1, neck=0, fingers=False, clavicles=False)   # Godot 4.7.2 requires both shoulders
        found = by_check(skeleton_check.check(facts(b.rig()), "GODOT"))
        self.assertEqual(found["bone_match"]["status"], "fail")
        self.assertIn("LeftShoulder", found["bone_match"]["found"])
        self.assertEqual(by_check(skeleton_check.check(facts(b.rig()), "UNITY"))["bone_match"]["status"], "pass")

    def test_a_hierarchy_break_fails(self):
        b = humanoid()
        b.bones = [(name, "root" if name == "thigh_l" else parent, *rest) for name, parent, *rest in b.bones]
        found = by_check(skeleton_check.check(facts(b.rig()), "UNITY"))
        self.assertEqual(found["bone_match"]["status"], "fail", found["bone_match"])
        self.assertIn("LeftUpperLeg", found["bone_match"]["found"])

    def test_root_motion_per_engine(self):
        rig = humanoid(root=False).rig()
        self.assertEqual(by_check(skeleton_check.check(facts(rig), "UNITY"))["root_motion"]["status"], "pass")
        self.assertEqual(by_check(skeleton_check.check(facts(rig), "GODOT"))["root_motion"]["status"], "warn")

    def test_facing_away(self):
        found = by_check(skeleton_check.check(facts(humanoid().rig(rotate_z=math.pi)), "GODOT"))
        self.assertEqual(found["facing"]["status"], "warn")


class StructureTests(unittest.TestCase):
    def test_control_bones_reach_the_engine_unless_deform_only(self):
        rig = humanoid(controls=3).rig()
        self.assertEqual(by_check(skeleton_check.check(facts(rig), "GODOT"))["deform_only"]["status"], "warn")
        self.assertEqual(by_check(skeleton_check.check(facts(rig), "UNITY"))["single_root"]["status"], "info")

    def test_fbx_adds_leaf_bones_glb_does_not(self):
        rig = humanoid().rig()
        self.assertEqual(by_check(skeleton_check.check(facts(rig), "UNITY", "FBX"))["leaf_bones"]["status"], "warn")
        self.assertEqual(by_check(skeleton_check.check(facts(rig), "GODOT", "GLB"))["leaf_bones"]["status"], "pass")

    def test_influences_against_each_engine(self):
        mesh = {"name": "Body", "histogram": {2: 100, 4: 50, 5: 7, 9: 1}}
        unity = by_check(skeleton_check.check(facts(humanoid().rig(), meshes=[mesh]), "UNITY"))["influences"]
        godot = by_check(skeleton_check.check(facts(humanoid().rig(), meshes=[mesh]), "GODOT"))["influences"]
        unreal = by_check(skeleton_check.check(facts(humanoid().rig(), meshes=[mesh]), "UNREAL", "FBX"))["influences"]
        self.assertEqual((unity["status"], godot["status"], unreal["status"]), ("warn", "warn", "pass"))
        self.assertIn("over the limit: 8 ", unity["found"])
        self.assertIn("over the limit: 1 ", godot["found"])

    def test_transform_constraints_and_bendy_bones(self):
        rig = humanoid().rig()
        found = by_check(skeleton_check.check(facts(rig, rotation=(90, 0, 0), scale=(0.01,) * 3,
                                                    constraints={3: 1}, drivers={4}, bbones={5: 3}), "UNREAL"))
        self.assertEqual(found["transform"]["status"], "warn")
        self.assertIn("scale", found["transform"]["found"])
        self.assertEqual(found["constraints"]["status"], "warn")
        self.assertIn("drivers (2)", found["constraints"]["found"])
        self.assertEqual(found["bendy_bones"]["status"], "warn")

    def test_every_finding_says_what_it_looked_for_and_found_with_a_known_source(self):
        for target in profiles.TARGETS:
            for fmt in profiles.FORMATS:
                report = skeleton_check.check(facts(humanoid(pose="A", controls=1).rig()), target, fmt)
                for f in report["findings"]:
                    with self.subTest(target=target, fmt=fmt, check=f["check"]):
                        self.assertTrue(f["looked_for"] and f["found"])
                        self.assertIn(f["source"], profiles.SOURCES)
                        self.assertIn(f["status"], ("pass", "info", "warn", "fail"))
                        if f["status"] in ("warn", "fail"):
                            self.assertTrue(f.get("fix"), f)
                self.assertEqual(set(report["sources"]), {f["source"] for f in report["findings"]})

    def test_the_agent_gets_passed_checks_by_name(self):
        report = skeleton_check.check(facts(humanoid().rig()), "UNREAL")
        agent = skeleton_check.for_agent(report)
        self.assertIn("auto_retarget", agent["passed"])
        self.assertTrue(all(f["status"] != "pass" for f in agent["findings"]))
        self.assertNotIn('"text"', json.dumps(skeleton_check.for_agent(
            skeleton_check.check(facts(humanoid(pose="A").rig()), "UNITY"))))

    def test_the_card_translates_each_template_before_filling_it(self):
        report = skeleton_check.check(facts(humanoid(pose="A", controls=2).rig()), "UNITY", "FBX")
        for f in report["findings"]:
            shown = skeleton_checks.render(f, lambda template: "<" + template + ">")
            english = skeleton_checks.render(f, lambda template: template)
            self.assertEqual(english["looked_for"], f["looked_for"])
            self.assertEqual(english["found"], f["found"])
            for field in skeleton_checks.TEXT_FIELDS:
                for template, params in f["text"][field]:
                    self.assertIn("<" + template.format(**params) + ">", shown[field])


class ProfileDataTests(unittest.TestCase):
    def test_roles_are_godots_56(self):
        self.assertEqual(len(profiles.ROLES), 56)
        self.assertEqual(profiles.ROLE_PARENT["Hips"], "Root")
        self.assertEqual(profiles.ROLE_PARENT["LeftThumbProximal"], "LeftThumbMetacarpal")

    def test_unity_has_15_required_and_no_root(self):
        self.assertEqual(len(profiles.UNITY.required), 15)
        self.assertNotIn("Root", profiles.UNITY.names)
        self.assertEqual(profiles.UNITY.names["LeftThumbMetacarpal"], "LeftThumbProximal")

    def test_the_mannequin_profile_has_64_bones_under_one_root(self):
        self.assertEqual(len(profiles.UE_SKELETON), 64)
        self.assertEqual([n for n, p, _ in profiles.UE_SKELETON if p is None], ["root"])
        names = {n for n, _, _ in profiles.UE_SKELETON}
        self.assertTrue(all(p in names for _, p, _ in profiles.UE_SKELETON if p))

    def test_every_profile_rule_names_a_source(self):
        for profile in profiles.PROFILES.values():
            for key in profile.sources.values():
                self.assertIn(key, profiles.SOURCES)
            for source, versions in profiles.SOURCES.values():
                self.assertTrue(source and versions)


class ReportCardTests(unittest.TestCase):
    """The person's card, laid out with a fake view: what it shows for each status, its actions, its size."""

    def setUp(self):
        from types import SimpleNamespace
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_native_notice_cards import VIEW
        from angelo_agent import card_kit, skeleton_report_card
        self.kit, self.cards, self.view = card_kit, skeleton_report_card, VIEW
        self.calls = []

        def runner(scene, name, target, fmt):
            self.calls.append((name, target, fmt))
            return skeleton_check.check(facts(humanoid(pose="A", controls=2).rig(), armature=name), target, fmt)
        self.card = skeleton_report_card.SkeletonReportCard("Hero", lambda: object(), "UNITY", runner=runner)
        self.redraws = []
        self.host = SimpleNamespace(redraw=lambda: self.redraws.append(1), close=lambda how: self.redraws.append(how),
                                    after=lambda seconds, function: None)

    def plan(self, width=1280, height=900):
        return self.card.layout(self.view, width, height, 1.0, self.kit.UiState())

    def notices(self, plan):
        return [op[3] for op in plan.ops if op[0] == "kit" and op[1] == "notice"]

    def test_problems_show_as_notices_with_their_fix_and_passes_as_chips(self):
        plan = self.plan()
        notices = self.notices(plan)
        report = self.card.report
        problems = sorted((f for f in report["findings"] if f["status"] in ("warn", "fail")),
                          key=lambda f: f["status"] != "fail")[:self.cards.FIRST_PROBLEMS]
        self.assertEqual(len(notices), len(problems))
        for notice, found in zip(notices, problems):
            self.assertEqual(notice["title"], self.cards.CHECKS[found["check"]])
            self.assertIn(found["fix"][1:], notice["body"])
            self.assertIn(found["looked_for"], notice["details"])
        chips = [op[3]["label"] for op in plan.ops if op[0] == "kit" and op[1] == "chip"]
        self.assertIn(self.cards.VERDICT[report["verdict"]][1], chips)
        for found in report["findings"]:
            if found["status"] == "pass":
                self.assertIn(self.cards.CHECKS[found["check"]], chips)
        actions = {action for _rect, action, _value in plan.hits}
        self.assertTrue({"target", "format", "copy", "again", "close"} <= actions, actions)

    def test_notes_fold_details_expand_and_the_engine_switches(self):
        infos = [f for f in self.card.report["findings"] if f["status"] == "info"]
        before = len(self.notices(self.plan()))
        self.card.act("notes", None, self.host)
        self.assertEqual(len(self.notices(self.plan())), before + len(infos))
        self.card.act("details", "rest_pose", self.host)
        self.assertIn("rest_pose", self.card.expanded)
        self.card.act("target", "UNREAL", self.host)   # Unreal's usual export, not Unity's FBX
        self.assertEqual((self.calls[-1], self.card.fmt), (("Hero", "UNREAL", "AUTO"), "GLB"))
        self.assertTrue(any(f["check"] == "auto_retarget" for f in self.card.report["findings"]))
        self.card.act("format", "FBX", self.host)
        self.assertEqual(self.calls[-1], ("Hero", "UNREAL", "FBX"))
        self.assertEqual(self.cards.LAST_TARGET[0], "UNREAL")
        self.cards.LAST_TARGET[0] = "UNREAL"

    def test_a_failed_run_says_why_and_a_small_region_still_fits(self):
        def broken(scene, name, target, fmt):
            raise LookupError("gone")
        card = self.cards.SkeletonReportCard("Hero", lambda: object(), "GODOT", runner=broken)
        plan = card.layout(self.view, 1280, 900, 1.0, self.kit.UiState())
        [notice] = [op[3] for op in plan.ops if op[0] == "kit" and op[1] == "notice"]
        self.assertEqual(notice["kind"], "error")
        gone = self.cards.SkeletonReportCard("Hero", lambda: None, "GODOT")
        self.assertIsNone(gone.report)
        plan = self.plan(560, 460)
        x, y, w, h = plan.card
        self.assertTrue(0 <= x and x + w <= 560 and 0 <= y, plan.card)

    def open_everything(self):
        self.card.act("more", None, self.host)
        self.card.act("notes", None, self.host)
        for found in self.card.report["findings"]:
            if found["status"] != "pass":
                self.card.act("details", found["check"], self.host)

    def test_with_every_section_open_the_findings_scroll_at_full_size(self):
        self.open_everything()
        tall = self.plan(1280, 2400)
        self.assertNotIn("findings", tall.scroll)   # room enough: the card is as tall as its content
        height = 640
        ui = self.kit.UiState()
        plan = self.card.layout(self.view, 1280, height, 1.0, ui)
        self.assertEqual(plan.scale, 1.0)            # never shrunk to fit
        x, y, w, h = plan.card
        self.assertTrue(y >= 0 and y + h <= height, plan.card)
        box = plan.scroll["findings"]
        self.assertGreater(box["content"], box["visible"])
        self.assertAlmostEqual(tall.card[3] - h, box["content"] - box["visible"], places=3)
        def actions(plan):
            return {(action, value): rect for rect, action, value in plan.hits}
        top = actions(plan)
        for control in (("copy", None), ("again", None), ("close", None)):
            rect = top[control]   # the buttons stay put, inside the card and outside the scrolling window
            self.assertTrue(y <= rect[1] and rect[1] + rect[3] <= y + h, (control, rect))
            self.assertFalse(self.kit.inside(box["rect"], rect[0] + 1, rect[1] + 1), control)
        for rect, action, value in plan.hits:   # nothing clickable outside the window from the scrolled part
            if action == "details":
                self.assertTrue(box["rect"][1] <= rect[1] and rect[1] + rect[3] <= box["rect"][1] + box["rect"][3])
        ui.scroll["findings"] = self.kit.clamp_scroll(plan, "findings", 10 ** 6)   # to the end, as the wheel does
        bottom = self.card.layout(self.view, 1280, height, 1.0, ui)
        def last_chip(plan):   # the last thing in the findings, the passed chips' last row: (bottom, top) in y up
            rect = [op[2] for op in plan.ops if op[0] == "kit" and op[1] == "chip"][-1]
            return rect[1], rect[1] + rect[3]
        window = (box["rect"][1], box["rect"][1] + box["rect"][3])
        self.assertLess(last_chip(plan)[0], window[0])            # below the window at first
        self.assertTrue(window[0] <= last_chip(bottom)[0] and last_chip(bottom)[1] <= window[1])   # scrolled to it
        self.assertEqual(actions(bottom)[("copy", None)], top[("copy", None)])
        shown = [op for op in bottom.ops if op[0] in {"clip", "unclip"}]
        self.assertEqual([op[0] for op in shown], ["clip", "unclip"])
        self.assertNotEqual(set(actions(bottom)), set(top))   # other details links are under the pointer now

    def test_the_copied_report_is_what_the_card_shows(self):
        text = self.card.text()
        for found in self.card.report["findings"]:
            self.assertIn(self.cards.CHECKS[found["check"]] + ": " + found["found"], text)

    def test_every_check_has_a_title_and_every_word_is_marked_for_translation(self):
        self.assertEqual(set(self.cards.CHECKS), set(skeleton_check.ORDER))
        import ast
        addon = ROOT / "scripts/addons_core/angelo_agent"
        for module in ("skeleton_checks.py", "skeleton_pose.py", "skeleton_match.py"):
            tree = ast.parse((addon / module).read_text(encoding="utf-8"))
            for node in ast.walk(tree):   # Every say() piece's template is an n_ literal or a name bound to one.
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "say" and node.args:
                    first = node.args[0]
                    marked = (isinstance(first, ast.Call) and getattr(first.func, "id", "") == "n_") or isinstance(
                        first, (ast.Name, ast.Subscript, ast.IfExp))
                    self.assertTrue(marked, "%s line %d" % (module, node.lineno))


if __name__ == "__main__":
    unittest.main()
