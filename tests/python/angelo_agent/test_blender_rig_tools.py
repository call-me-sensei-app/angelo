# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hand-rigging tools (RIG family) in Blender, on skinned metaball hands bound with heat weights.

Every layout of ``hand_fixture_scenes`` is measured: hands found by bone layout with the palm normal from the
midline or the fingers' curl, the tools' skinning matching Blender's, a mitten's fused fingers named, a planted
weight step reported as a cut. The fold-to-limit fist is solved, its ``bone_set``
operations applied through a real staged scene program, and the committed pose read back by the contact tool at the
solved angles. No tool changes the scene. With ``--gui`` the picture tools draw: each picture's pose beside its
rest pose.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-rig-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import hand_fixture_scenes as scenes  # noqa: E402
from angelo_agent import native_programs as native, rig_host, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages, spent_budget  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "gui": not bpy.app.background}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


class References:
    """A conversation's observation store, in the QA folder: what the picture tools write into."""

    def __init__(self):
        self.folder = OUT / "views"
        self.folder.mkdir(exist_ok=True)
        self.written = []

    def new_observation(self, name):
        import uuid
        path = self.folder / (str(uuid.uuid4()) + ".png")
        path.touch(mode=0o600)
        self.written.append(path)
        return {"path": str(path), "id": path.stem, "name": name, "mime": "image/png", "kind": "image", "bytes": 0}

    def discard_observation(self, reference):
        Path(reference["path"]).unlink(missing_ok=True)


def state(made):
    """The armature's and mesh's revisions, or (where Angelo has no exact revision, as for a mesh whose armature has a
    cross-bone constraint) every pose basis, weight and vertex position."""
    try:
        return (queries.object_revision(window, made["armature"]), queries.object_revision(window, made["mesh"]))
    except Exception:
        arm, mesh = made["armature"], made["mesh"]
        return (tuple(tuple(map(tuple, pb.matrix_basis)) for pb in arm.pose.bones),
                tuple((tuple(v.co), tuple((g.group, round(g.weight, 6)) for g in v.groups)) for v in mesh.data.vertices))


def tool(name, made, **arguments):
    """One RIG tool call as the executor makes it, with the scene's state compared before and after."""
    arguments = validate_call(name, dict(armature=queries.object_handle(made["armature"]), **arguments))
    before = state(made)
    result = rig_host.execute(window, name, arguments, References())
    after = state(made)
    check(name + "_left_the_scene_unchanged_" + str(len(report["checks"])),
          before == after and result["scene_changed"] is False)
    return result


def hand(result, name):
    return next(h for h in result["hands"] if h["hand"] == name)


def layouts():
    made = scenes.build("HANGING")
    found = tool("angelo_rig_hand_inspect", made)
    left = hand(found, "LeftHand")
    check("hanging_two_hands", sorted(h["hand"] for h in found["hands"]) == ["LeftHand", "RightHand"])
    check("hanging_thumb_and_finger_order", left["thumb"][0] == "LeftHandThumb1" and
          [f["role"] for f in left["fingers"]] == ["index", "middle", "ring", "little"] and
          [f["bones"][0] for f in left["fingers"]] == ["LeftHandIndex1", "LeftHandMiddle1", "LeftHandRing1",
                                                       "LeftHandLittle1"])
    check("hanging_palm_faces_the_midline", left["palm_normal_source"] == "MIDLINE"
          and left["palm_normal_world"][0] < -0.95 and hand(found, "RightHand")["palm_normal_world"][0] > 0.95)
    check("skinning_matches_blender", all(c["compared"] and c["largest_difference_finger_radii"] < 0.01
                                          for c in found["skinning_check"]) and not found["uncertain"])
    check("hanging_fingers_apart", left["fused_fingers"] == [] and left["current_outside_limits"] == {})
    report["hanging_weights"] = left["weights"]
    t_pose = tool("angelo_rig_hand_inspect", scenes.build("T_POSE"), hand="LeftHand")
    check("t_pose_palm_from_the_curl", hand(t_pose, "LeftHand")["palm_normal_source"] == "FINGER_CURL"
          and hand(t_pose, "LeftHand")["palm_normal_world"][2] < -0.9)
    cartoon = tool("angelo_rig_hand_inspect", scenes.build("CARTOON"), hand="LeftHand")
    check("cartoon_three_fingers", [f["role"] for f in hand(cartoon, "LeftHand")["fingers"]]
          == ["index", "middle", "little"])
    mitten = tool("angelo_rig_hand_inspect", scenes.build("MITTEN"), hand="LeftHand")
    report["mitten"] = hand(mitten, "LeftHand")["fused_fingers"]
    check("mitten_fingers_fused", len(hand(mitten, "LeftHand")["fused_fingers"]) == 3)
    thigh = tool("angelo_rig_hand_inspect", scenes.build("THIGH"), hand="LeftHand")
    report["thigh_weights"] = hand(thigh, "LeftHand")["weights"]
    check("thigh_skin_never_part_of_the_hand", hand(thigh, "LeftHand")["finger_radius"] > 0)


def weight_cut():
    """A step in a finger's weights (0.55 to nothing across one edge, as the rigs' thumb cut was) is reported."""
    made = scenes.build("HANGING")
    mesh, groups = made["mesh"], made["mesh"].vertex_groups
    before = tool("angelo_rig_hand_inspect", made, hand="LeftHand")
    middle2, middle1 = groups["LeftHandMiddle2"], groups["LeftHandMiddle1"]
    head = np.array(made["armature"].data.bones["LeftHandMiddle2"].head_local)
    tail = np.array(made["armature"].data.bones["LeftHandMiddle2"].tail_local)
    axis = (tail - head) / np.linalg.norm(tail - head)
    for v in mesh.data.vertices:
        weights = {g.group: g.weight for g in v.groups}
        if middle2.index in weights or middle1.index in weights:
            ahead = (np.array(v.co) - head) @ axis > 0.002
            for g in list(v.groups):
                groups[g.group].remove([v.index])
            (middle2 if ahead else middle1).add([v.index], 0.55 if ahead else 1.0, "REPLACE")
            if ahead:
                groups["LeftHandMiddle3"].add([v.index], 0.45, "REPLACE")
    after = tool("angelo_rig_hand_inspect", made, hand="LeftHand")
    cuts, earlier = hand(after, "LeftHand")["weights"]["cuts"], hand(before, "LeftHand")["weights"]["cuts"]
    report["weight_cut"] = {"before": earlier, "after": cuts}
    # Heat weights already step at some finger joints (the kit smoothed them for that reason); the planted step adds
    # a full one across the middle finger's first segment.
    check("weight_step_reported", cuts["LeftHandMiddle1"]["largest_jump"] >= 0.9
          and cuts["LeftHandMiddle1"]["edges"] >= earlier.get("LeftHandMiddle1", {}).get("edges", 0) + 5)


def fist():
    made = scenes.build("HANGING")
    solved = tool("angelo_rig_hand_fist", made, hand="LeftHand")
    left = hand(solved, "LeftHand")
    report["fist"] = {k: left[k] for k in ("fingers", "thumb", "contacts", "seconds", "evaluations")}
    check("fist_inside_limits", left["outside_limits"] == {})
    check("fist_knuckles_at_their_limits", all(f["knuckle_flex"] >= 89.5 for f in left["fingers"].values()))
    check("fist_operations_cover_every_joint", len(solved["operations"]) == 15 and all(
        op["kind"] == "bone_set" and set(op["parameters"]) == {"bone", "rotation"} for op in solved["operations"]))
    arm = made["armature"]
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "operations": solved["operations"],
        "write_set": [{"handle": queries.object_handle(arm), "revision": queries.object_revision(window, arm)}]})
    job = native.begin(window, "angelo_scene_program", arguments)
    with spent_budget():
        for _ in range(400):
            result = job.poll()
            if result.get("final") or result.get("ready_to_commit"):
                break
    committed = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report["fist_program"] = {key: job.result.get(key) for key in ("status", "error")}
    check("fist_applied_through_a_scene_program", "FINISHED" in committed and job.result.get("status") == "committed"
          and no_stages())
    posed = hand(tool("angelo_rig_hand_contact", made, hand="LeftHand"), "LeftHand")
    error = max(max(abs(a - b) for a, b in zip(posed["angles"][bone], angles))
                for bone, angles in left["angles"].items())
    report["fist_read_back_degrees"] = error
    check("fist_reads_back_at_the_solved_angles", error < 0.5)
    check("fist_contacts_read_back", posed["outside_limits"] == {}
          and abs(posed["contacts"]["index-middle"]["depth_finger_radii"]
                  - left["contacts"]["index-middle"]["depth_finger_radii"]) < 0.02)
    reach = tool("angelo_rig_hand_range", scenes.build("HANGING"), hand="LeftHand", joints=["LeftHandIndex1"])
    rows = {(r["bone"], r["axis"]): r for r in reach["joints"]}
    check("range_rows", set(rows) == {("LeftHandIndex1", "flex"), ("LeftHandIndex1", "spread")}
          and rows[("LeftHandIndex1", "flex")]["reach"][1] <= 90)


def apply_program(arm, operations):
    """Commit ``operations`` through a staged scene program, as the agent applies a solver's result."""
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "operations": operations,
        "write_set": [{"handle": queries.object_handle(arm), "revision": queries.object_revision(window, arm)}]})
    job = native.begin(window, "angelo_scene_program", arguments)
    with spent_budget():
        for _ in range(400):
            result = job.poll()
            if result.get("final") or result.get("ready_to_commit"):
                break
    committed = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return "FINISHED" in committed and job.result.get("status") == "committed" and no_stages()


def pose_tool():
    """Anatomical angles in, bone_set operations out: the index curl reads back at the asked angles, the end joint
    following, a bend past the limit clamped and named, and the other fingers untouched."""
    made = scenes.build("HANGING")
    asked = tool("angelo_rig_hand_pose", made, hand="LeftHand",
                 angles=[{"bone": "LeftHandIndex1", "flex": 85}, {"bone": "LeftHandIndex2", "flex": 130}])
    check("pose_clamps_past_the_limit", asked["clamped_to_limits"]["LeftHandIndex2"]["set"][0] == 110.0)
    check("pose_end_joint_follows", abs(asked["angles"]["LeftHandIndex3"][0] - 73.7) < 0.2
          and len(asked["operations"]) == 3)
    check("pose_applied", apply_program(made["armature"], asked["operations"]))
    posed = hand(tool("angelo_rig_hand_contact", made, hand="LeftHand"), "LeftHand")
    report["pose_read_back"] = {k: posed["angles"][k] for k in ("LeftHandIndex1", "LeftHandIndex2", "LeftHandIndex3")}
    check("pose_reads_back", abs(posed["angles"]["LeftHandIndex1"][0] - 85) < 0.5
          and abs(posed["angles"]["LeftHandIndex2"][0] - 110) < 0.5 and all(
              abs(v) < 0.5 for k, a in posed["angles"].items() if "Index" not in k for v in a))


def hands_choice():
    """Work on hands (the + menu): at Send the selected armature's hands and the guidance join the request's
    observation; the scene and the person's mode stay as they were."""
    from angelo_agent import chosen_tools_host
    made = scenes.build("HANGING")
    arm = made["armature"]
    window.view_layer.objects.active = arm
    before = (queries.object_revision(window, arm), queries.object_revision(window, made["mesh"]))
    outcomes, _ = chosen_tools_host.perform([{"tool": "hands", "option": "hands", "fit_selected": False}], window)
    facts = chosen_tools_host.observation_facts((outcomes, [0]))
    check("hands_choice_read", outcomes[0]["done_by_angelo"] and outcomes[0]["armature_name"] == arm.name
          and [h["hand"] for h in facts["hands"]["hands"]] == ["LeftHand", "RightHand"]
          and "fold" in facts["hands"]["guidance"] and "angelo_rig_hand_pose" in facts["hands"]["guidance"])
    check("hands_choice_changes_nothing", before == (queries.object_revision(window, arm),
                                                     queries.object_revision(window, made["mesh"])))
    second = bpy.data.objects.new("Other Rig", bpy.data.armatures.new("Other Rig"))
    window.scene.collection.objects.link(second)
    window.view_layer.objects.active = None
    outcomes, _ = chosen_tools_host.perform([{"tool": "hands", "option": "hands", "fit_selected": False}], window)
    check("hands_choice_asks_which_armature", not outcomes[0]["done_by_angelo"]
          and "select the character's armature" in outcomes[0]["not_done"]
          and chosen_tools_host.observation_facts((outcomes, [0])) == {})


def control_rig_and_mirror():
    """Finger bones that follow a constraint (a control rig) are named, and the pose tools refuse them: bone_set
    rotations there would be overridden. A mirrored armature keeps its palm's side."""
    made = scenes.build("HANGING")
    arm = made["armature"]
    constraint = arm.pose.bones["LeftHandIndex2"].constraints.new("COPY_ROTATION")
    constraint.target, constraint.subtarget = arm, "LeftHandMiddle2"
    found = tool("angelo_rig_hand_inspect", made, hand="LeftHand")
    check("driven_fingers_named", any(u["code"] == "driven_fingers" and "LeftHandIndex2" in u["detail"]
                                      for u in found["uncertainty"]))
    try:
        rig_host.execute(window, "angelo_rig_hand_fist", validate_call("angelo_rig_hand_fist", {
            "armature": queries.object_handle(arm), "hand": "LeftHand"}), References())
        refused = None
    except Exception as error:
        refused = error
    check("driven_fingers_refused", refused is not None and "constraints or drivers" in str(refused))
    made = scenes.build("HANGING")
    made["armature"].scale.x = -1.0
    window.view_layer.update()
    mirrored = hand(tool("angelo_rig_hand_inspect", made, hand="LeftHand"), "LeftHand")
    check("mirrored_palm_keeps_its_side", mirrored["palm_normal_world"][0] > 0.95)


def pictures():
    made = scenes.build("HANGING")
    views = tool("angelo_rig_hand_views", made, hand="LeftHand", views=["BACK", "PALM_THUMB"], markers=True)["views"]
    check("views_drawn", len(views) == 2 and all(Path(v["path"]).stat().st_size == v["bytes"] > 1000 for v in views)
          and all(v["layout"] == "current (left), rest (right)" and v["width"] == 768 for v in views))
    solved = tool("angelo_rig_hand_fist", made, hand="LeftHand", pictures=True, views=["THUMB_SIDE"])
    check("fist_picture", len(solved["views"]) == 1 and solved["views"][0]["hand_view"] == "THUMB_SIDE")
    sheets = tool("angelo_rig_hand_range", made, hand="LeftHand", pictures=True, view="PALM",
                  joints=["LeftHandIndex1", "LeftHandIndex2"])["views"]
    check("range_sheet", len(sheets) == 1 and "rows: LeftHandIndex1, LeftHandIndex2" in sheets[0]["layout"])


def run_checks():
    bpy.utils.register_class(commit_operator(report))
    layouts()
    weight_cut()
    fist()
    pose_tool()
    hands_choice()
    control_rig_and_mirror()
    if not bpy.app.background:
        pictures()
    report["success"] = True


def run():
    try:
        run_checks()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
    (OUT / "rig-tools.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_RIG_TOOLS", json.dumps({k: report.get(k) for k in ("success", "paid_requests")}), flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    return None


if bpy.app.background:
    run()
    assert report.get("success"), report.get("error")
else:
    bpy.app.timers.register(run, first_interval=1)
