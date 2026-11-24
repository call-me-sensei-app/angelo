# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game rig's skeleton plan (``game_rig_names.plan``), as plain data, on rigs shaped the ways real rigs differ.

Unreal finds bones by exact name under their exact parent (``skeleton_match._exact``), so the plan for Unreal must put
every Mannequin name it writes under the Mannequin's parent, whatever the rig: a three-bone or five-bone spine, a
one-bone neck, no clavicles, no root, a deforming root of another name, twist bones between arm segments (Rigify's
DEF chain), deforming bones below non-deforming ones (Rigify's DEF under ORG), and a prop bone whose name the engine
uses. Godot gets a Root above the hips; Unity gets no root and no fillers. Fillers deform nothing: every kept bone is
one of the rig's deforming bones and every filler has no source. Expected to fail on 2.0.0 (06259fa5bb0): the module
does not exist there.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import game_rig_names, skeleton_profiles as profiles  # noqa: E402

UE_PARENT = {name: parent for name, parent, _ in profiles.UE_SKELETON}


def humanoid(trunk=2, neck=1, clavicles=True, root=None, twist=False, org=False, prop=None):
    """(bones parents-first, {bone: parent}, deform set, {role: bone}) of a humanoid built from its roles."""
    bones, parents, deform, roles = [], {}, set(), {}

    def add(name, parent, role=None, deforming=True):
        bones.append(name)
        parents[name] = parent
        if deforming:
            deform.add(name)
        if role:
            roles[role] = name
        return name

    top = None
    if root:
        top = add(root, None, None)
    if org:
        top = add("ORG-torso", top, deforming=False)
    hips = add("b_hips", top, "Hips")
    chest_roles = ["Spine", "Chest", "UpperChest"][:min(trunk, 3)] if trunk <= 3 else ["Spine", "Chest"]
    previous = hips
    for k in range(trunk):
        role = chest_roles[k] if k < len(chest_roles) else ("UpperChest" if k == trunk - 1 else None)
        previous = add("b_trunk%d" % k, previous, role)
    branch = previous
    for k in range(neck):
        previous = add("b_neck%d" % k, previous, "Neck" if k == 0 else None)
    add("b_head", previous, "Head")
    for side in ("Left", "Right"):
        s = side[0]
        shoulder = add("b_clav_" + s, branch, side + "Shoulder") if clavicles else branch
        upper = add("b_upper_" + s, shoulder, side + "UpperArm")
        above = add("b_twist_" + s, upper) if twist else upper
        lower = add("b_lower_" + s, above, side + "LowerArm")
        add("b_hand_" + s, lower, side + "Hand")
        thigh = add("b_thigh_" + s, hips, side + "UpperLeg")
        shin = add("b_shin_" + s, thigh, side + "LowerLeg")
        foot = add("b_foot_" + s, shin, side + "Foot")
        add("b_toe_" + s, foot, side + "Toes")
    if prop:
        add(prop, hips)
    return bones, parents, deform, roles


def plan(rig, target="UNREAL"):
    profile = profiles.PROFILES[target]
    template = profiles.UE_SKELETON if profile.exact_names else None
    return game_rig_names.plan(*rig, profile, template)


class UnrealPlan(unittest.TestCase):
    def assert_parents_as_mannequin(self, result):
        named = {e["name"]: e["parent"] for e in result["bones"]}
        wrong = {n: (p, UE_PARENT[n]) for n, p in named.items() if n in UE_PARENT and p != UE_PARENT[n]}
        self.assertEqual(wrong, {})
        return named

    def test_every_mannequin_name_under_its_parent_whatever_the_rig(self):
        shapes = {"three_trunk": dict(trunk=3), "two_trunk": dict(trunk=2), "five_trunk": dict(trunk=5),
                  "no_clavicles": dict(clavicles=False), "two_neck": dict(neck=2), "twist": dict(twist=True),
                  "under_org": dict(org=True), "named_root": dict(root="Armature_root")}
        for label, shape in shapes.items():
            with self.subTest(label):
                result = plan(humanoid(**shape))
                named = self.assert_parents_as_mannequin(result)
                for required in ("root", "pelvis", "spine_01", "spine_05", "neck_01", "neck_02", "head",
                                 "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "thigh_r", "foot_r", "ball_r"):
                    self.assertIn(required, named)

    def test_fillers_deform_nothing_and_kept_bones_are_the_rigs_deforming_bones(self):
        rig = humanoid(trunk=2, neck=1, clavicles=False, org=True)
        result = plan(rig)
        sources = [e["source"] for e in result["bones"] if e["source"]]
        self.assertEqual(sorted(sources), sorted(rig[2]))
        added = sorted(e["name"] for e in result["bones"] if not e["source"])
        self.assertEqual(added, ["clavicle_l", "clavicle_r", "neck_02", "root", "spine_03", "spine_04", "spine_05"])

    def test_filler_runs_space_between_their_ends(self):
        result = plan(humanoid(trunk=2, clavicles=False))
        runs = {e["name"]: e["run"] for e in result["bones"] if e.get("run")}
        self.assertEqual(runs["spine_03"], (1, 3, "spine_02", "neck_01"))
        self.assertEqual(runs["spine_05"], (3, 3, "spine_02", "neck_01"))
        self.assertEqual(runs["clavicle_l"], (1, 1, "spine_05", "upperarm_l"))
        self.assertEqual(runs["root"], (1, 1, None, "pelvis"))

    def test_existing_trunk_bones_take_the_names_on_their_path(self):
        result = plan(humanoid(trunk=5))
        self.assertEqual([result["names"]["b_trunk%d" % k] for k in range(5)],
                         ["spine_01", "spine_02", "spine_03", "spine_04", "spine_05"])
        self.assertFalse([e for e in result["bones"] if e["name"].startswith("spine") and not e["source"]])

    def test_a_deforming_root_of_another_name_becomes_the_root(self):
        result = plan(humanoid(root="Armature_root"))
        self.assertEqual(result["names"]["Armature_root"], "root")
        self.assertEqual([e for e in result["bones"] if e["name"] == "root"][0]["source"], "Armature_root")

    def test_a_prop_bone_with_an_engine_name_is_renamed_and_said(self):
        result = plan(humanoid(prop="pelvis"))
        self.assertEqual(result["names"]["b_hips"], "pelvis")
        self.assertEqual(result["names"]["pelvis"], "pelvis_extra")
        self.assertTrue(any("pelvis_extra" in note for note in result["notes"]))
        names = [e["name"] for e in result["bones"]]
        self.assertEqual(len(names), len(set(names)))

    def test_twist_bones_stay_below_the_upper_arm(self):
        result = plan(humanoid(twist=True))
        named = {e["name"]: e["parent"] for e in result["bones"]}
        for s in ("l", "r"):   # The helper names the twist bones b_twist_L and b_twist_R; they keep those names.
            self.assertEqual(named["lowerarm_" + s], "upperarm_" + s)
            self.assertEqual(named["b_twist_" + s.upper()], "upperarm_" + s)


class OtherEngines(unittest.TestCase):
    def test_godot_gets_a_root_and_every_required_bone_the_rig_lacks_and_no_other(self):
        """Godot requires both shoulders (SkeletonProfileHumanoid): a rig without clavicles gets them between its
        chest and upper arms, or the game rig fails Godot's own rig check (the app suite, 2026-11-15). Optional
        bones it lacks (Chest, Neck, fingers) are not added."""
        result = plan(humanoid(trunk=2, clavicles=False), "GODOT")
        parent = {e["name"]: e["parent"] for e in result["bones"]}
        added = sorted(e["name"] for e in result["bones"] if not e["source"])
        self.assertEqual(added, ["LeftShoulder", "RightShoulder", "Root"])
        self.assertEqual(parent["Hips"], "Root")
        for side in ("Left", "Right"):
            self.assertEqual(parent[side + "UpperArm"], side + "Shoulder")
            self.assertEqual(parent[side + "Shoulder"], parent["Neck"])   # From the bone the arms hung from.
        with_clavicles = plan(humanoid(trunk=2, clavicles=True), "GODOT")
        self.assertEqual(sorted(e["name"] for e in with_clavicles["bones"] if not e["source"]), ["Root"])

    def test_unity_gets_no_root_and_no_fillers(self):
        result = plan(humanoid(trunk=2, clavicles=False), "UNITY")
        self.assertFalse([e for e in result["bones"] if not e["source"]])
        self.assertEqual({e["name"]: e["parent"] for e in result["bones"]}["Hips"], None)

    def test_unity_leaves_out_a_deforming_root_and_its_skin_goes_to_the_hips(self):
        """A Humanoid avatar ignores bones above the hips, so travel on a root would be lost: the rig's own
        deforming root (no role) is left out, the hips carry its motion, and its vertex group maps to the hips."""
        result = plan(humanoid(trunk=2, root="b_root"), "UNITY")
        names = [e["name"] for e in result["bones"]]
        self.assertNotIn("b_root", names)
        self.assertEqual({e["name"]: e["parent"] for e in result["bones"]}["Hips"], None)
        self.assertEqual(result["names"]["b_root"], "Hips")
        self.assertTrue(any("b_root" in note for note in result["notes"]), result["notes"])

    def test_parents_come_before_children(self):
        for target in ("UNREAL", "GODOT", "UNITY"):
            seen = set()
            for entry in plan(humanoid(twist=True, org=True), target)["bones"]:
                self.assertTrue(entry["parent"] is None or entry["parent"] in seen, (target, entry))
                seen.add(entry["name"])


if __name__ == "__main__":
    unittest.main()
