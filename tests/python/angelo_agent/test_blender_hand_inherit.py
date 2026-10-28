# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hand tools' predicted poses against Blender's evaluated pose, one bone setting at a time, and the fist's wall
time with unrelated vertices in the mesh. Background Blender, no window, no provider:

    Blender -b --factory-startup --python test_blender_hand_inherit.py -- [--timing BASE_PACKAGE_DIR]

Each case reads the armature as the tools do (``rig_read``), asks ``hand_inherit``'s forward model for a finger
pose and its bone_set rotations, applies those rotations to the real pose bones and compares every bone's evaluated
head and tail with the prediction, in finger radii. The old model (full inheritance always) is measured the same
way for the record. ``--timing`` also times ``rig_host.fist`` on the 600-vertex fixture hand with 0 and 199,000
unrelated vertices, the add-on under test and a base package (``BASE_PACKAGE_DIR``, an older ``angelo_agent``)
interleaved in this one process.
"""

import importlib.util
import json
import os
import tempfile
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup instance"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
RADIUS = 0.0075            # the fixture's finger radius (hand_fixtures.tube_skin)
TOLERANCE = 0.01           # finger radii between prediction and Blender (float32 evaluation)
report = {"version": bpy.app.version_string, "cases": {}, "timing": []}


def package(name, folder):
    """``folder`` imported as package ``name`` without running its ``__init__`` (stock Blender has no Angelo)."""
    module = types.ModuleType(name)
    module.__path__ = [str(folder)]
    sys.modules[name] = module
    queries = types.ModuleType(name + ".scene_queries")
    queries.object_handle = lambda obj: "scn:qa:" + obj.name
    sys.modules[name + ".scene_queries"] = queries
    return lambda sub: importlib.import_module(name + "." + sub)


if importlib.util.find_spec("angelo_agent") is not None:   # Angelo's own binary: its add-on, as installed.
    def load(sub):
        return importlib.import_module("angelo_agent." + sub)
else:                                                       # Stock Blender: this checkout's add-on files.
    load = package("angelo_agent", HERE.parents[2] / "scripts/addons_core/angelo_agent")
H, R, HOST = load("hand_rig"), load("rig_read"), load("rig_host")
import hand_fixtures as F  # noqa: E402  (after the package: it builds hand_rig skeletons)


def build(unrelated=0):
    """The fixture hand's armature and its 600-vertex tube skin, plus ``unrelated`` far, unweighted vertices."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    builder = F.hand_bones()
    data = bpy.data.armatures.new("Rig")
    arm = bpy.data.objects.new("Rig", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for i, name in enumerate(builder.names):
        edit = data.edit_bones.new(name)
        edit.head, edit.tail, edit.roll, edit.use_deform = builder.heads[i], builder.tails[i], builder.rolls[i], \
            builder.deform[i]
    for i, name in enumerate(builder.names):
        if builder.parents[i] >= 0:
            data.edit_bones[name].parent = data.edit_bones[builder.names[builder.parents[i]]]
    bpy.ops.object.mode_set(mode="OBJECT")
    skin = F.tube_skin(builder.skeleton())
    points, tris = list(map(tuple, skin.rest)), list(map(tuple, skin.triangles))
    side = int(np.ceil(np.sqrt(max(unrelated, 1))))
    base = len(points)
    for k in range(unrelated):
        points.append((3.0 + (k % side) * 0.001, 3.0 + (k // side) * 0.001, 0.0))
    for k in range(unrelated - side - 1):
        if k % side != side - 1:
            tris.append((base + k, base + k + 1, base + k + side))
    mesh = bpy.data.meshes.new("Body")
    mesh.from_pydata(points, [], tris)
    obj = bpy.data.objects.new("Body", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for b, name in enumerate(builder.names):
        group = obj.vertex_groups.new(name=name)
        pick = skin.bone == b
        for v, w in zip(skin.vertex[pick], skin.weight[pick]):
            group.add([int(v)], float(w), "REPLACE")
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    bpy.context.view_layer.update()
    return arm, obj


def blender_pose(arm, skel):
    bpy.context.view_layer.update()
    posed = np.array([np.array(arm.pose.bones[n].matrix) for n in skel.names])
    tails = np.array([(m @ np.linalg.inv(skel.rest[i]) @ np.append(skel.tails[i], 1.0))[:3]
                      for i, m in enumerate(posed)])
    return posed, tails


def predicted_tails(skel, posed):
    return np.array([(m @ np.linalg.inv(skel.rest[i]) @ np.append(skel.tails[i], 1.0))[:3]
                     for i, m in enumerate(posed)])


def apply(arm, skel, rotations):
    for j, rotation in rotations.items():
        pb = arm.pose.bones[skel.names[j]]
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = H.euler_xyz(rotation)   # As a bone_set writes it.


def old_model(skel, root, rels):
    """The model before 2026-10-27: every bone inherits its parent's full transform."""
    posed, bases = skel.posed.copy(), {}
    below = set(skel.subtree(root))
    for i in skel.order:
        if i == root or i not in below:
            continue
        p, basis = skel.parents[i], skel.basis[i].copy()
        if i in rels:
            r = H.rotation_part(skel.rest[i])
            bases[i] = r.T @ rels[i] @ r
            basis[:3, :3] = bases[i] * np.linalg.norm(basis[:3, :3], axis=0)
        posed[i] = posed[p] @ np.linalg.inv(skel.rest[p]) @ skel.rest[i] @ basis
    return posed, bases


def measure(arm, settings, model):
    """Largest head or tail difference (finger radii) between ``model``'s prediction and Blender's evaluation."""
    for pb in arm.pose.bones:
        pb.matrix_basis.identity()
    hand_bone = arm.pose.bones["LeftHand"]
    hand_bone.rotation_mode = "XYZ"
    hand_bone.rotation_euler = (0.3, -0.2, 0.4)   # The hand turned, so a hinge's parent delta is not identity.
    for name, (key, value) in settings:
        if key == "basis_scale":
            arm.pose.bones[name].scale = value
        elif key == "location":
            arm.pose.bones[name].location = value
    bpy.context.view_layer.update()
    skel = R.Rig._skeleton(SimpleNamespace(armature=arm))
    hand = H.set_palm_normals(H.find_hands(skel))[0]
    frames = H.joint_frames(hand)
    rels = {}
    for digit in hand.fingers[:2]:
        for j, a in zip(digit.joints, ((60, 5, 0), (45, 0, 0), (30, 0, 0))):
            rels[j] = H.joint_rotation(frames[j], *a)
    if model == "new":
        bases = {}
        posed = H.forward_kinematics(skel, hand.bone, rels, bases)
    else:
        posed, bases = old_model(skel, hand.bone, rels)
    apply(arm, skel, bases)
    actual, tails = blender_pose(arm, skel)
    below = skel.subtree(hand.bone)
    heads = np.linalg.norm(actual[below, :3, 3] - posed[below, :3, 3], axis=1)
    ends = np.linalg.norm(tails[below] - predicted_tails(skel, posed)[below], axis=1)
    # The reader's own check: the current pose recomputed from its bases equals Blender's.
    again = R.Rig._skeleton(SimpleNamespace(armature=arm))
    recomputed = H.forward_kinematics(again, hand.bone, {})
    check = float(np.max(np.linalg.norm(recomputed[below, :3, 3] - actual[below, :3, 3], axis=1)))
    return round(float(max(heads.max(), ends.max())) / RADIUS, 4), round(check / RADIUS, 4)


def set_bone(arm, name, key, value):
    if key == "use_connect":
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        setattr(arm.data.edit_bones[name], key, value)
        bpy.ops.object.mode_set(mode="OBJECT")
    else:
        setattr(arm.data.bones[name], key, value)


def cases():
    out = [("full_inheritance", []), ("audit_inherit_rotation_off_middle", [("LeftHandMiddle2", "use_inherit_rotation",
                                                                              False)]),
           ("inherit_rotation_off_knuckle", [("LeftHandMiddle1", "use_inherit_rotation", False)]),
           ("local_location_off", [("LeftHandMiddle2", "use_local_location", False)]),
           ("connected", [("LeftHandMiddle2", "use_connect", True)]),
           ("relative_parent", [("LeftHandMiddle2", "use_relative_parent", True)])]
    for mode in H.I.INHERIT_SCALE:
        out.append(("inherit_scale_" + mode, [("LeftHandMiddle2", "inherit_scale", mode)]))
        out.append(("hinge_inherit_scale_" + mode, [("LeftHandMiddle2", "inherit_scale", mode),
                                                    ("LeftHandMiddle2", "use_inherit_rotation", False)]))
    return out


def run_cases():
    failures = []
    for name, bone_settings in cases():
        arm, _ = build()
        for bone, key, value in bone_settings:
            set_bone(arm, bone, key, value)
        pose = [("LeftHandMiddle1", ("basis_scale", (1.25, 0.8, 1.1)))] if "scale" in name else []
        if name == "local_location_off" or name == "connected":
            pose.append(("LeftHandMiddle2", ("location", (0.003, 0.001, -0.002))))
        new, check = measure(arm, pose, "new")
        old, _ = measure(arm, pose, "old")
        report["cases"][name] = {"new_model_finger_radii": new, "old_model_finger_radii": old,
                                 "current_pose_check_finger_radii": check}
        if new > TOLERANCE or check > TOLERANCE:
            failures.append(name)
    report["failures"] = failures


def run_refusals():
    arm, mesh = build()
    skel = R.Rig._skeleton(SimpleNamespace(armature=arm))
    root = skel.index["LeftHand"]
    out = {}
    constraint = arm.pose.bones["LeftHandIndex2"].constraints.new("COPY_ROTATION")
    constraint.target, constraint.subtarget = arm, "LeftHandMiddle2"
    out["constrained_finger_named"] = R._driven(arm, skel, root) == ["LeftHandIndex2"]
    arm.pose.bones["LeftHandIndex2"].constraints.remove(constraint)
    follower = arm.pose.bones["LeftForeArm"].constraints.new("COPY_ROTATION")
    follower.target, follower.subtarget = arm, "LeftHandIndex1"
    out["follower_outside_named"] = R._driven(arm, skel, root) == ["LeftForeArm"]
    arm.pose.bones["LeftForeArm"].constraints.remove(follower)
    arm.data.pose_position = "REST"
    out["rest_position_refused"] = "Rest Position" in (R._unreproducible(arm, mesh, skel, root) or "")
    arm.data.pose_position = "POSE"
    mesh.parent, mesh.parent_type, mesh.parent_bone = arm, "BONE", "LeftHandIndex1"
    out["bone_parented_mesh_refused"] = "parented to bone" in (R._unreproducible(arm, mesh, skel, root) or "")
    report["refusals"] = out


def time_fist(loader, unrelated):
    rig_read, rig_host, math_ = loader("rig_read"), loader("rig_host"), loader("hand_math")
    arm, mesh = build(unrelated)
    window = SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer)
    started = time.perf_counter()
    rig = rig_read.Rig.of(window, arm, mesh, {"hand": "LeftHand"})
    if hasattr(math_, "Budget"):
        rig.budget = math_.Budget(20.0, started)
    read = time.perf_counter() - started
    result = rig_host.fist(rig, {}, None)
    total = time.perf_counter() - started
    hand = result["hands"][0]
    return {"vertices": len(mesh.data.vertices), "read_s": round(read, 3), "total_s": round(total, 3),
            "middle_flex": hand["fingers"]["middle"]["middle_flex"], "complete": result.get("complete", True),
            "evaluations": hand.get("evaluations")}


run_cases()
run_refusals()
if "--timing" in ARGV:
    base = package("base_agent", Path(ARGV[ARGV.index("--timing") + 1]))
    for round_ in range(2):
        for label, loader in (("base", base), ("fixed", load)):
            for unrelated in (0, 199_000):
                row = time_fist(loader, unrelated)
                row.update(package=label, round=round_)
                report["timing"].append(row)
report["success"] = not report["failures"] and all(report["refusals"].values())
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-hand-inherit-qa"))
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "hand-inherit.json").write_text(json.dumps(report, indent=2))
print("HAND_INHERIT_REPORT " + json.dumps(report), flush=True)
sys.exit(0 if report["success"] else 1)
