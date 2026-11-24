# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-body IK controls on any humanoid, by role, in a real background Blender.

Posing and fixing animation by hands and feet (Maya HumanIK's effectors; DCC gaps, gap 4) needs IK controls the
rig may not have: a mocap or game skeleton has only its deforming bones. ``angelo.add_ik_controls`` adds them by role
(``skeleton_roles.map_roles``), whatever the bone names (plan ``release-2.1.0-plan.md``, sections 6-7).

On two unlike humanoids (a T-pose rig without a root, an A-pose rig with a root and other names):

* adding the controls moves nothing: every bone's posed head and tail stay where they were (the pole angles are
  solved for the rest pose);
* each hand and foot gets a non-deforming effector at its joint, and each elbow and knee a pole on the side the limb
  bends to (knees forward, elbows back, by the rig's own facing); two-bone IK runs on the forearms and shins;
* moving a foot effector brings the foot joint to it (within 1% of the rig's height) with the knee still bending
  toward its pole; moving a hand effector does the same for the wrist;
* lowering the hips keeps both feet where they were (the effectors hang from the root, or from nothing, never from
  the hips) and the knees bend forward;
* running it again replaces its controls instead of adding more; no control deforms.
* **IK/FK switch** (``angelo.set_ik_fk``): an arm posed by its effector switched to FK keeps its pose (within 0.5%
  of the height) and no longer follows the effector; switched back to IK it keeps its pose again.
* **Retargeting onto a rig with these controls** follows the source (limbs within 5 degrees) with the limbs in FK
  (the IK is keyed off) and in IK (the effectors keyed where the hands and feet are).
* A limb the rig already drives (a constraint of its own on the upper arm, as a generated Rigify rig's deforming
  bones have) is refused and nothing is added; a hidden armature gets its controls and stays hidden.

Run through ``run_native_suites.py --include test_blender_ik_controls``; check ``ik-controls.json`` for ``success``.
Expected to fail on 2.0.0 (06259fa5bb0): the operator does not exist there.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-ik-controls-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False
from qa_humanoid import angle, humanoid, walk, world_dir  # noqa: E402

REPORT = OUT / "ik-controls.json"
state = {"success": False, "checks": {}, "details": {}}
ROLES = ["Hips", "Spine", "Chest", "Neck", "Head", "Root"] + [
    side + part for side in ("Left", "Right")
    for part in ("Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg", "Foot", "Toes")]


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def posed(obj):
    bpy.context.view_layer.update()
    return {pb.name: (obj.matrix_world @ pb.head, obj.matrix_world @ pb.tail) for pb in obj.pose.bones}


def controls(obj):
    return [b for b in obj.data.bones if b.get("angelo_ik_control")]


def joint(obj, name):
    bpy.context.view_layer.update()
    return obj.matrix_world @ obj.pose.bones[name].head


def effector_for(obj, role_bone):
    """The effector bone added for a hand or foot bone (recorded on the control as ``angelo_ik_for``)."""
    return next((b.name for b in controls(obj) if b.get("angelo_ik_for") == role_bone and b.get("angelo_ik_kind")
                 == "effector"), None)


def case(label, names, height, drop, clavicles, root):
    obj = humanoid(label, names, height, drop, clavicles=clavicles, root=root)
    before = posed(obj)
    result = bpy.ops.angelo.add_ik_controls(armature=obj.name)
    after = posed(obj)
    drift = max(max((a[0] - b[0]).length, (a[1] - b[1]).length) for k, (a, b) in
                ((k, (after[k], before[k])) for k in before))
    made = controls(obj)
    check("%s_finished" % label, result == {'FINISHED'})
    check("%s_rest_unmoved" % label, drift < 1e-3 * height, {"drift": round(drift, 6)})
    check("%s_controls_do_not_deform" % label, made and not any(b.use_deform for b in made), len(made))

    foot = names["LeftFoot"]
    effector = effector_for(obj, foot)
    knee_before = joint(obj, names["LeftLowerLeg"])
    target = joint(obj, foot) + Vector((0.0, -0.1, 0.15)) * (height / 1.8)
    pb = obj.pose.bones[effector] if effector else None
    if pb:
        pb.location = obj.data.bones[effector].matrix_local.inverted().to_3x3() @ (
            obj.matrix_world.inverted().to_3x3() @ (target - joint(obj, effector)))
    reach = (joint(obj, foot) - target).length if pb else 1.0
    toward_pole(label + "_knee", obj, names["LeftUpperLeg"], names["LeftLowerLeg"], foot)
    knee_after = joint(obj, names["LeftLowerLeg"])
    forward = Vector((0, -1, 0))
    check("%s_foot_reaches_effector" % label, reach < 0.01 * height and (knee_after - knee_before).dot(forward) > 0,
          {"reach": round(reach, 4)})
    if pb:
        pb.location = (0, 0, 0)

    hand = names["RightHand"]
    effector = effector_for(obj, hand)
    pb = obj.pose.bones[effector] if effector else None
    # Within reach in any pose: a third of the way back toward the shoulder, and forward (the elbow must bend). A
    # target past the arm's length is the out-of-reach case, not this one (the A pose's was 5.5 cm too far).
    shoulder = joint(obj, names["RightUpperArm"])
    target = joint(obj, hand) + (shoulder - joint(obj, hand)) / 3 + Vector((0.0, -0.1, 0.0)) * (height / 1.8)
    if pb:
        pb.location = obj.data.bones[effector].matrix_local.inverted().to_3x3() @ (
            obj.matrix_world.inverted().to_3x3() @ (target - joint(obj, effector)))
    reach = (joint(obj, hand) - target).length if pb else 1.0
    check("%s_hand_reaches_effector" % label, reach < 0.01 * height, {"reach": round(reach, 4)})
    toward_pole(label + "_elbow", obj, names["RightUpperArm"], names["RightLowerArm"], hand)
    if pb:
        pb.location = (0, 0, 0)

    feet = {side: joint(obj, names[side + "Foot"]) for side in ("Left", "Right")}
    knee = joint(obj, names["LeftLowerLeg"])
    obj.pose.bones[names["Hips"]].location = obj.data.bones[names["Hips"]].matrix_local.inverted().to_3x3() @ (
        obj.matrix_world.inverted().to_3x3() @ Vector((0, 0, -0.15 * height / 1.8)))
    moved = max((joint(obj, names[side + "Foot"]) - feet[side]).length for side in feet)
    knee_forward = (joint(obj, names["LeftLowerLeg"]) - knee).dot(forward)
    check("%s_feet_stay_when_hips_drop" % label, moved < 0.01 * height and knee_forward > 0,
          {"feet_moved": round(moved, 4), "knee_forward": round(knee_forward, 4)})
    obj.pose.bones[names["Hips"]].location = (0, 0, 0)

    bpy.ops.angelo.add_ik_controls(armature=obj.name)
    check("%s_rerun_replaces" % label, len(controls(obj)) == len(made))


def toward_pole(label, obj, upper, lower, end):
    """A limb posed by its effector bends toward its own pole: around the root-to-end line, the elbow or knee lies
    within 30 degrees of the pole (a quarter turn off bent arms up instead of back, 2026-11-15; knees forward only
    asked for any forward component)."""
    import math
    pole = next((b.name for b in obj.data.bones if b.get("angelo_ik_kind") == "pole" and b.get("angelo_ik_for") == end),
                None)
    root, tip = joint(obj, upper), joint(obj, end)
    axis = (tip - root).normalized()

    def square(v):
        return v - axis * v.dot(axis)
    bend, toward = square(joint(obj, lower) - root), square(joint(obj, pole) - root) if pole else None
    degrees = math.degrees(bend.angle(toward)) if toward is not None and bend.length > 1e-6 else 180.0
    check("%s_bends_toward_its_pole" % label, degrees < 30.0, {"degrees": round(degrees, 1)})


def move_effector(obj, effector, target):
    """Put ``effector``'s head at world ``target`` through its location (from its rest place, in its own axes)."""
    rest = obj.matrix_world @ obj.data.bones[effector].head_local
    obj.pose.bones[effector].location = obj.data.bones[effector].matrix_local.inverted().to_3x3() @ (
        obj.matrix_world.inverted().to_3x3() @ (target - rest))


def arm_joints(obj, names):
    return [joint(obj, names[side]) for side in ("LeftUpperArm", "LeftLowerArm", "LeftHand")]


def switch_case():
    names = {role: "sw_" + role for role in ROLES}
    obj = humanoid("Switch", names, 1.6, 0, clavicles=True, root=True)
    bpy.ops.angelo.add_ik_controls(armature=obj.name)
    effector = effector_for(obj, names["LeftHand"])
    move_effector(obj, effector, joint(obj, names["LeftHand"]) + Vector((-0.1, -0.15, -0.1)))
    posed = arm_joints(obj, names)
    bpy.ops.angelo.set_ik_fk(armature=obj.name, mode='FK', limb='ARMS')
    fk = arm_joints(obj, names)
    from angelo_agent import ik_controls
    limb = next((l for l in ik_controls.limbs(obj) if l["end"] == names["LeftHand"]), None)

    def influences():   # What the switch sets: the arm follows the IK mechanism, the hand turns with the effector.
        return {c.influence for c in ik_controls.switches(obj, limb)} if limb else {None}
    moved = max((a - b).length for a, b in zip(posed, fk))
    check("fk_snap_keeps_pose", influences() == {0.0} and moved < 0.005 * 1.6,
          {"influences": sorted(influences(), key=str), "moved": round(moved, 4)})
    obj.pose.bones[effector].location = (0, 0, 0)
    check("fk_ignores_effector", max((a - b).length for a, b in zip(fk, arm_joints(obj, names))) < 1e-4)
    move_effector(obj, effector, joint(obj, names["LeftHand"]) + Vector((0.0, 0.1, 0.0)))
    before_ik = arm_joints(obj, names)
    bpy.ops.angelo.set_ik_fk(armature=obj.name, mode='IK', limb='ARMS')
    after_ik = arm_joints(obj, names)
    moved = [round((a - b).length, 4) for a, b in zip(before_ik, after_ik)]   # Shoulder, elbow, hand.
    check("ik_snap_keeps_pose", influences() == {1.0} and max(moved) < 0.005 * 1.6,
          {"influences": sorted(influences(), key=str), "moved": moved})


def retarget_onto_ik_case():
    src_names = {role: "src_" + role for role in ROLES}
    src = humanoid("IKSource", src_names, 1.8, 0, clavicles=True, root=False)
    walk(src, src_names)
    limbs = [side + part for side in ("Left", "Right") for part in ("UpperArm", "LowerArm", "UpperLeg", "LowerLeg")]
    worst = {}
    for mode in ("FK", "IK"):
        names = {role: "ik%s_%s" % (mode, role) for role in ROLES}
        tgt = humanoid("IKTarget" + mode, names, 1.3, 40, clavicles=False, root=True)
        bpy.ops.angelo.add_ik_controls(armature=tgt.name)
        result = bpy.ops.angelo.retarget_animation(source=src.name, target=tgt.name, limbs=mode)
        worst[mode] = 180.0 if result != {'FINISHED'} else 0.0
        if result == {'FINISHED'}:
            for f in (1, 8, 15, 22, 29, 36):
                bpy.context.scene.frame_set(f)
                for role in limbs:
                    worst[mode] = max(worst[mode], angle(world_dir(src, src_names[role])[0],
                                                         world_dir(tgt, names[role])[0]))
    check("retarget_onto_ik_rig_follows_source", all(v < 5.0 for v in worst.values()),
          {m: round(v, 2) for m, v in worst.items()})


def refusal_and_hidden_case():
    names = {role: "dr_" + role for role in ROLES}
    obj = humanoid("Driven", names, 1.6, 0, clavicles=True, root=True)
    constraint = obj.pose.bones[names["LeftUpperArm"]].constraints.new('COPY_ROTATION')
    constraint.target, constraint.subtarget = obj, names["Chest"]
    try:
        result = bpy.ops.angelo.add_ik_controls(armature=obj.name)
    except RuntimeError:
        result = {'CANCELLED'}
    check("driven_limb_refused", result == {'CANCELLED'} and not controls(obj))
    names = {role: "hd_" + role for role in ROLES}
    hidden = humanoid("Hidden", names, 1.6, 0, clavicles=True, root=True)
    hidden.hide_set(True)
    try:
        result = bpy.ops.angelo.add_ik_controls(armature=hidden.name)
    except RuntimeError as error:
        result = {"error": str(error)}
    check("hidden_armature_gets_controls_stays_hidden", result == {'FINISHED'} and len(controls(hidden)) == 8
          and hidden.hide_get(), {"result": str(result)})


def main():
    try:
        if not hasattr(bpy.ops.angelo, "add_ik_controls"):
            check("operator_exists", False)
        else:
            check("operator_exists", True)
            case("TPose", {role: "a_" + role for role in ROLES}, 1.8, 0, clavicles=True, root=False)
            case("APose", {role: "B." + role.lower() for role in ROLES}, 1.3, 40, clavicles=False, root=True)
            switch_case()
            retarget_onto_ik_case()
            refusal_and_hidden_case()
        state["success"] = len(state["checks"]) == 1 + 2 * 9 + 6 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_IK_CONTROLS", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
