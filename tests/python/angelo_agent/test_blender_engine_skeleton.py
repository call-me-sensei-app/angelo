# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check in Blender (doc/angelo/engine_skeleton.rst), through angelo_skeleton_check's host route.

* Blender's own Rigify: the human metarig (its names are Unreal's Rigify template) and the rig Rigify generates from
  it (DEF- bones under ORG and MCH bones and a non-deforming root, the case Unreal's auto-retarget doesn't recognise):
  each bone's role by layout, the Unreal, Unity and Godot verdicts.
* A hand-built skinned rig: the influence histogram read from the mesh's groups, a constraint and a driver, the
  armature's scale, an A-pose, and the refusals (Edit Mode, several armatures without a handle).
* The check changes nothing: the file stays clean and every object's state is the same after each call.
Run only in a disposable instance with the exact Angelo binary (``--factory-startup``). No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-skeleton-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import scene_queries as queries, skeleton_check_host as host  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = None   # set after the empty file loads (a load replaces the window)


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def snapshot():
    """Every object's transform, every pose bone's basis and every vertex group weight: what a read must not change."""
    out = []
    for obj in bpy.data.objects:
        out.append((obj.name, tuple(map(tuple, obj.matrix_world)), obj.mode))
        if obj.type == "ARMATURE" and obj.pose is not None:
            out += [(pb.name, tuple(map(tuple, pb.matrix_basis))) for pb in obj.pose.bones]
        if obj.type == "MESH":
            out += [tuple((g.group, round(g.weight, 6)) for g in v.groups) for v in obj.data.vertices[:2000]]
    return out


def call(target, armature=None, fmt=None):
    arguments = {"target": target}
    if armature is not None:
        arguments["armature"] = queries.object_handle(armature)
    if fmt:
        arguments["format"] = fmt
    arguments = validate_call("angelo_skeleton_check", arguments, host=True)
    before, dirty = snapshot(), bpy.data.is_dirty
    result = host.execute(window, "angelo_skeleton_check", arguments)
    check("read_only_%d" % len(report["checks"]), snapshot() == before and bpy.data.is_dirty == dirty)
    json.dumps(result)   # Plain data only.
    check("no_card_text_%d" % len(report["checks"]), '"text"' not in json.dumps(result))
    return result


def statuses(result):
    found = {f["check"]: f["status"] for f in result["findings"]}
    found.update({name: "pass" for name in result["passed"]})
    return found


def rigify():
    bpy.ops.preferences.addon_enable(module="rigify")
    bpy.ops.object.armature_human_metarig_add()
    metarig = bpy.context.view_layer.objects.active
    unreal = call("UNREAL", metarig, "FBX")
    report["metarig_unreal"] = {"verdict": unreal["verdict"], "statuses": statuses(unreal)}
    # The FBX names the root after the object ("metarig"), as Unreal's Rigify template expects.
    # A passing check is listed by name only, and only a "recognised" prediction passes.
    retarget = [f for f in unreal["findings"] if f["check"] == "auto_retarget"]
    check("metarig_recognised_by_unreal", "auto_retarget" in unreal["passed"] if not retarget
          else retarget[0].get("prediction") == "recognised")
    for role, name in (("Hips", "spine"), ("LeftUpperArm", "upper_arm.L"), ("RightLowerLeg", "shin.R"),
                       ("LeftIndexProximal", "f_index.01.L"), ("Head", "spine.006"), ("LeftShoulder", "shoulder.L")):
        check("metarig_role_" + role, unreal["roles"].get(role) == name)
    bpy.ops.pose.rigify_generate()
    rig = bpy.data.objects["rig"]
    results = {target: call(target, rig) for target in ("UNREAL", "UNITY", "GODOT")}
    report["generated"] = {target: {"verdict": r["verdict"], "statuses": statuses(r), "roles": r["roles"],
                                    "notes": r["notes"], "bones": r["bones"]} for target, r in results.items()}
    unreal = results["UNREAL"]
    # Whether Unreal's characteriser takes it is the engine's to say (skeleton_engine_e2e); here, that the check
    # names the DEF- prefix most bones share and that the deforming chain doesn't hang together for Unity.
    retarget = next(f for f in unreal["findings"] if f["check"] == "auto_retarget")
    report["generated_auto_retarget"] = retarget
    check("generated_says_the_prefix", any(d.get("prefix") == "DEF-" for d in retarget.get("details", ())))
    check("generated_unity_names_the_break", statuses(results["UNITY"])["bone_match"] == "fail")
    for role, name in (("Hips", "DEF-spine"), ("LeftUpperArm", "DEF-upper_arm.L"), ("RightUpperLeg", "DEF-thigh.R"),
                       ("LeftLowerArm", "DEF-forearm.L"), ("RightShoulder", "DEF-shoulder.R"),
                       ("LeftHand", "DEF-hand.L"), ("Head", "DEF-spine.006")):
        check("generated_role_" + role, unreal["roles"].get(role) == name)
    check("generated_control_bones_reported", statuses(results["GODOT"])["deform_only"] == "warn")
    for obj in (metarig, rig):
        bpy.data.objects.remove(obj)


def skinned_rig():
    """A plain rig of six bones in an A-pose, scaled, with a mesh whose vertices carry up to 6 influences."""
    data = bpy.data.armatures.new("Body")
    arm = bpy.data.objects.new("Body", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    joints = {"hips": ((0, 0, 1.0), (0, 0, 1.1), None), "spine": ((0, 0, 1.1), (0, 0, 1.45), "hips"),
              "head": ((0, 0, 1.55), (0, 0, 1.8), "spine"),
              "arm.L": ((0.15, 0, 1.42), (0.35, 0, 1.22), "spine"), "fore.L": ((0.35, 0, 1.22), (0.53, 0, 1.04), "arm.L"),
              "hand.L": ((0.53, 0, 1.04), (0.58, 0, 0.99), "fore.L"),
              "arm.R": ((-0.15, 0, 1.42), (-0.35, 0, 1.22), "spine"),
              "fore.R": ((-0.35, 0, 1.22), (-0.53, 0, 1.04), "arm.R"),
              "hand.R": ((-0.53, 0, 1.04), (-0.58, 0, 0.99), "fore.R"),
              "thigh.L": ((0.1, 0, 1.0), (0.1, -0.01, 0.53), "hips"), "shin.L": ((0.1, -0.01, 0.53), (0.1, 0, 0.08), "thigh.L"),
              "foot.L": ((0.1, 0, 0.08), (0.1, -0.15, 0.0), "shin.L"),
              "thigh.R": ((-0.1, 0, 1.0), (-0.1, -0.01, 0.53), "hips"),
              "shin.R": ((-0.1, -0.01, 0.53), (-0.1, 0, 0.08), "thigh.R"),
              "foot.R": ((-0.1, 0, 0.08), (-0.1, -0.15, 0.0), "shin.R")}
    for name, (head, tail, parent) in joints.items():
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.scale = (0.01, 0.01, 0.01)
    mesh = bpy.data.meshes.new("Skin")
    mesh.from_pydata([(0, 0, 1.0 + 0.01 * i) for i in range(10 + len(joints))], [], [])
    skin = bpy.data.objects.new("Skin", mesh)
    bpy.context.scene.collection.objects.link(skin)
    skin.modifiers.new("Armature", "ARMATURE").object = arm
    names = list(joints)
    for name in names:
        skin.vertex_groups.new(name=name)
    for v in range(10):
        for k in range(1 + v % 6):   # 1 to 6 influences per vertex
            skin.vertex_groups[names[k]].add([v], 0.5, "REPLACE")
    for k, name in enumerate(names):   # every bone moves skin, as in a real character
        skin.vertex_groups[name].add([10 + k], 1.0, "REPLACE")
    pose = arm.pose.bones["head"].constraints.new("DAMPED_TRACK")
    pose.target = skin
    driver = arm.pose.bones["spine"].driver_add("rotation_euler", 0)
    driver.driver.expression = "0"
    bpy.context.view_layer.update()
    return arm, skin


def custom():
    arm, skin = skinned_rig()
    unity = call("UNITY", arm)
    report["custom_unity"] = unity
    found = {f["check"]: f for f in unity["findings"]}
    check("custom_a_pose_warns", found["rest_pose"]["status"] == "warn")
    check("custom_scale_warns", found["transform"]["status"] == "warn" and "scale" in found["transform"]["found"])
    check("custom_constraint_and_driver", "drivers (2)" in found["constraints"]["found"])
    check("custom_influences_over_4", found["influences"]["status"] == "warn"
          and found["influences"]["found"].startswith("vertices over the limit: 2 "))   # 5 and 6 influences
    check("custom_roles", unity["roles"].get("LeftUpperArm") == "arm.L" and unity["roles"].get("Hips") == "hips")
    godot = statuses(call("GODOT", arm))
    check("custom_godot_reads_8", godot["influences"] == "pass")
    # Several armatures: the check needs a handle unless the active object is one of them.
    other = bpy.data.objects.new("Other", bpy.data.armatures.new("Other"))
    bpy.context.scene.collection.objects.link(other)
    bpy.context.view_layer.objects.active = skin
    try:
        call("UNITY")
        check("several_armatures_refused", False)
    except RefusalError as error:
        check("several_armatures_refused", "name one" in str(error))
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        call("UNITY", arm)
        check("edit_mode_refused", False)
    except RefusalError as error:
        check("edit_mode_refused", "Edit Mode" in str(error))
    bpy.ops.object.mode_set(mode="OBJECT")


def run_checks():
    global window
    bpy.ops.wm.read_homefile(use_empty=True)
    window = bpy.context.window_manager.windows[0]
    rigify()
    custom()
    report["success"] = True


def run():
    try:
        run_checks()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    (OUT / "engine-skeleton.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_ENGINE_SKELETON", json.dumps({k: report.get(k) for k in ("success", "paid_requests")}), flush=True)


run()
assert report.get("success"), report.get("error")
