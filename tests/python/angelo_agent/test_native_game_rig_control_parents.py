# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game rig's hierarchy when the control rig's deforming bones hang from control bones (``game_rig_names.plan``).

A generated Rigify rig, and control rigs built the same way, start every deforming chain under non-deforming bones:
the upper arm under the shoulder's mechanism bone, the thigh under the torso's, the face under its ORG bone. Hanging
each kept bone from its nearest DEFORMING ancestor then gave the engine export Angelo made from Rigify's own
generated rig 98 root bones, with the shoulders, upper arms and thighs under no parent (skeleton_engine_e2e on
afee78c028b, 2026-11-18: every engine's rig check failed on all three game rigs). The plan must give:

* a non-deforming ancestor that a deforming bone follows (``follows``: Copy Transforms, Child Of) counts as that
  deforming bone, so the face hangs from the head and the pelvis flesh from the hips;
* a bone that plays a role hangs from the bone of its nearest present ancestor role whenever the rig's own
  hierarchy doesn't already put it below that bone (twist bones and a second neck bone in between are kept);
* anything still without a parent hangs from the top bone: one root.

Checked for Unreal (every Mannequin name under its Mannequin parent), Godot and Unity (each role bone under its role
parent), with and without ``follows``; the ordinary rigs' plans don't change. Expected to fail on 93b528a3f40.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_game_rig_names import UE_PARENT, humanoid, plan as plan_plain  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import game_rig_names, skeleton_profiles as profiles  # noqa: E402


def control_rig():
    """(bones, parents, deform, roles, follows) shaped like a generated Rigify human: ORG bones (non-deforming) carry
    the hierarchy, each DEF bone follows its ORG bone, and every DEF chain starts under a control bone."""
    bones, parents, deform, roles, follows = [], {}, set(), {}, {}

    def add(name, parent, role=None, deforming=False, follow=None):
        bones.append(name)
        parents[name] = parent
        if deforming:
            deform.add(name)
        if role:
            roles[role] = name
        if follow:
            follows[follow] = name
        return name

    add("root", None)
    add("MCH-torso", "root")
    spine_roles = ["Hips", "Spine", "Chest", "UpperChest", "Neck", None, "Head"]
    org = add("ORG-spine", "MCH-torso")
    previous_def = None
    for k, role in enumerate(spine_roles):
        if k:
            org = add("ORG-spine.%03d" % k, org)
        name = "DEF-spine" + ("" if k == 0 else ".%03d" % k)
        previous_def = add(name, previous_def or "MCH-torso", role, True, org)
    add("ORG-face", "ORG-spine.006")
    add("DEF-jaw", "ORG-face", None, True)
    add("DEF-lid.T.L", "ORG-face", None, True)
    add("DEF-pelvis.L", "ORG-spine", None, True)
    add("DEF-pelvis.R", "ORG-spine", None, True)
    for side, s in (("Left", "L"), ("Right", "R")):
        o_sh = add("ORG-shoulder." + s, "ORG-spine.003")
        add("DEF-shoulder." + s, o_sh, side + "Shoulder", True, o_sh)
        o_up = add("ORG-upper_arm." + s, o_sh)
        o_lo = add("ORG-forearm." + s, o_up)
        o_ha = add("ORG-hand." + s, o_lo)
        mch = add("MCH-upper_arm_parent." + s, o_sh)
        up = add("DEF-upper_arm." + s, mch, side + "UpperArm", True, o_up)
        tw = add("DEF-upper_arm.%s.001" % s, up, None, True)
        lo = add("DEF-forearm." + s, tw, side + "LowerArm", True, o_lo)
        tw2 = add("DEF-forearm.%s.001" % s, lo, None, True)
        add("DEF-hand." + s, tw2, side + "Hand", True, o_ha)
        o_th = add("ORG-thigh." + s, "ORG-spine")
        o_sn = add("ORG-shin." + s, o_th)
        o_ft = add("ORG-foot." + s, o_sn)
        o_to = add("ORG-toe." + s, o_ft)
        mch = add("MCH-thigh_parent." + s, "MCH-torso")
        th = add("DEF-thigh." + s, mch, side + "UpperLeg", True, o_th)
        sn = add("DEF-shin." + s, th, side + "LowerLeg", True, o_sn)
        ft = add("DEF-foot." + s, sn, side + "Foot", True, o_ft)
        add("DEF-toe." + s, ft, side + "Toes", True, o_to)
    return bones, parents, deform, roles, follows


def plan(rig, target, follows=True):
    bones, parents, deform, roles, follow = rig
    profile = profiles.PROFILES[target]
    template = profiles.UE_SKELETON if profile.exact_names else None
    return game_rig_names.plan(bones, parents, deform, roles, profile, template, follows=follow if follows else None)


def role_parent(role, present):
    up = profiles.ROLE_PARENT.get(role)
    while up is not None and up not in present:
        up = profiles.ROLE_PARENT.get(up)
    return up


class ControlRigParents(unittest.TestCase):
    def check(self, result, target):
        named = {e["name"]: e["parent"] for e in result["bones"]}
        tops = [n for n, p in named.items() if p is None]
        self.assertEqual(len(tops), 1, tops[:10])
        role = {e["name"]: e["role"] for e in result["bones"]}
        if target == "UNREAL":
            wrong = {n: (p, UE_PARENT[n]) for n, p in named.items() if n in UE_PARENT and p != UE_PARENT[n]}
            self.assertEqual(wrong, {})
        else:
            present = {r for r in role.values() if r}
            by_role = {r: n for n, r in role.items() if r}
            for name, r in role.items():   # Up through role-less bones (a second neck bone), the role's parent.
                if r and r != "Root" and role_parent(r, present):
                    up = named[name]
                    while up is not None and not role.get(up):
                        up = named[up]
                    self.assertEqual(up, by_role[role_parent(r, present)], (target, name))
        return named

    def test_one_root_and_every_role_under_its_parent_on_each_engine(self):
        for target in ("UNREAL", "GODOT", "UNITY"):
            for follows in (True, False):
                with self.subTest(target=target, follows=follows):
                    self.check(plan(control_rig(), target, follows), target)

    def test_what_a_bone_follows_places_the_bones_with_no_role(self):
        for target in ("UNREAL", "GODOT", "UNITY"):
            with self.subTest(target):
                result = plan(control_rig(), target)
                named = self.check(result, target)
                names = result["names"]
                self.assertEqual(named[names["DEF-jaw"]], names["DEF-spine.006"])   # Under the head.
                self.assertEqual(named[names["DEF-pelvis.L"]], names["DEF-spine"])   # Under the hips.
                if target == "UNREAL":   # Unreal's rule: a role bone below a twist bone hangs from its template parent.
                    self.assertEqual(named[names["DEF-forearm.L"]], names["DEF-upper_arm.L"])
                    self.assertEqual(named[names["DEF-upper_arm.L.001"]], names["DEF-upper_arm.L"])
                else:   # Twist kept between.
                    self.assertEqual(named[names["DEF-forearm.L"]], names["DEF-upper_arm.L.001"])

    def test_ordinary_rigs_plan_as_before(self):
        shapes = (dict(), dict(trunk=3), dict(clavicles=False), dict(twist=True), dict(org=True),
                  dict(root="Armature_root"), dict(prop="pelvis"))
        for target in ("UNREAL", "GODOT", "UNITY"):
            for shape in shapes:
                with self.subTest(target=target, shape=shape):
                    rig = humanoid(**shape)
                    profile = profiles.PROFILES[target]
                    template = profiles.UE_SKELETON if profile.exact_names else None
                    self.assertEqual(game_rig_names.plan(*rig, profile, template, follows={}),
                                     plan_plain(rig, target))


if __name__ == "__main__":
    unittest.main()
