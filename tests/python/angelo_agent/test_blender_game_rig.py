# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine game rig and its clips on a control rig with the things real control rigs carry, in a background Blender.

A procedural humanoid control rig (``qa_humanoid``: no clavicles, a two-bone spine, a one-bone neck, a root, foot IK
controls) plays a walk as its active action and a wave in an NLA track, has a deforming prop bone that happens to be
called ``pelvis``, and a mesh skinned to it and parented to it, with the rig moved after parenting. Checked
(doc/angelo/rigging.rst, "Game rig" and "Clips"):

* **Unreal**: every Mannequin name sits under the Mannequin's parent (the rig check's ``bone_match`` passes, filler
  bones for the missing spine, neck and clavicle bones), no failure; the prop is ``pelvis_extra``; the hips are
  ``pelvis``.
* **One action per action played, each alone**: the walk's bake equals the control rig played with its NLA off (an
  unmuted wave track doesn't blend in), within 1 degree on every limb.
* **The mesh copy stays where the mesh is**, bound to the game rig with its groups renamed; the game rig and the copy
  sit in a collection of their own; the control rig's action, NLA and rotation modes are unchanged.
* **Unity** (Humanoid names, no root) and **Godot** (a Root above the hips): no failure in their rig checks.
* **Rigify's own generated rig** (the human metarig, generated, every deforming bone weighted): for each engine the
  game rig has one root and no failure in that engine's rig check. Its deforming chains start under control bones
  (98 root bones and failed checks on every engine at afee78c028b).
* **Clips**: with one baked action already in an NLA track, the glTF's animations are exactly the baked actions, by
  name, once each, and the track's name is put back; Unity's FBX set is ``model.fbx`` plus one ``model@clip.fbx`` per
  action, each re-importing with its animation; a track of several strips is refused and nothing is written. Each
  pick is vouched as the operator's save browser would. (Where C refuses a write is pinned by the host suite,
  test_native_clip_paths: in a background Blender the guard allows every path, so a refusal can't be seen here.)

Run through ``run_native_suites.py --include test_blender_game_rig``; check ``game-rig.json`` for ``success``.
Expected to fail on 2.0.0 (06259fa5bb0): the operators do not exist there.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import struct
import tempfile
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bpy  # noqa: E402
from mathutils import Euler, Vector  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-game-rig-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False
from qa_humanoid import FRAMES, angle, humanoid, walk, world_dir  # noqa: E402

REPORT = OUT / "game-rig.json"
SAMPLES = (1, 8, 15, 22, 29, 36)
ROLES = ["Hips", "Spine", "Chest", "Neck", "Head", "Root"] + [
    side + part for side in ("Left", "Right")
    for part in ("Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg", "Foot", "Toes", "FootIK")]
NAMES = {role: "ctl." + role for role in ROLES}
UE_LIMBS = {"LeftUpperArm": "upperarm_l", "RightLowerArm": "lowerarm_r", "LeftUpperLeg": "thigh_l",
            "RightLowerLeg": "calf_r", "Spine": "spine_01"}
state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def control_rig():
    rig = humanoid("Control", NAMES, 1.26, 40, clavicles=False, root=True, controls=True)
    walk(rig, NAMES)
    wave = bpy.data.actions.new("Wave")
    ad = rig.animation_data
    held = ad.action
    ad.action = wave
    for f in (1, 20, 40):
        pb = rig.pose.bones[NAMES["LeftUpperArm"]]
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = Euler((0, 0, 1.2 if f == 20 else 0.4))
        pb.keyframe_insert("rotation_euler", frame=f)
    ad.action = held
    track = ad.nla_tracks.new()
    track.name = "WaveTrack"
    track.strips.new("Wave", 1, wave)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    prop = rig.data.edit_bones.new("pelvis")   # A prop bone whose name the engine uses.
    hips = rig.data.edit_bones[NAMES["Hips"]]
    prop.head, prop.tail, prop.parent, prop.use_deform = hips.head + Vector((0.1, 0.1, 0)), hips.head + Vector(
        (0.1, 0.2, 0)), hips, True
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 0.9))
    mesh = bpy.context.active_object
    mesh.name = "Body"
    mesh.vertex_groups.new(name=NAMES["Hips"]).add(list(range(len(mesh.data.vertices))), 1.0, 'REPLACE')
    # A character's skin carries every deforming bone (the role mapping leaves unweighted end bones out, so a skin
    # on the hips alone would hide the fingertips, hands and head; it found no arms, 2026-11-15).
    for bone in rig.data.bones:
        if bone.use_deform and bone.name not in mesh.vertex_groups:
            mesh.vertex_groups.new(name=bone.name).add(list(range(len(mesh.data.vertices))), 0.05, 'REPLACE')
    mesh.modifiers.new("Armature", 'ARMATURE').object = rig
    mesh.parent = rig
    mesh.matrix_parent_inverse = rig.matrix_world.inverted()
    rig.location.x += 1.0   # The rig moves after parenting, so the mesh's parent inverse is not the identity.
    bpy.context.view_layer.update()
    return rig, mesh


def finding(report, name):
    return next((f for f in report["findings"] if f["check"] == name), {})


def unreal_case(rig, mesh):
    from angelo_agent import skeleton_check, skeleton_facts
    modes_before = {pb.name: pb.rotation_mode for pb in rig.pose.bones}
    action_before, nla_before = rig.animation_data.action, rig.animation_data.use_nla
    mesh_world = mesh.matrix_world.copy()
    result = bpy.ops.angelo.make_game_rig(source=rig.name, target_engine='UNREAL')
    games = [o for o in bpy.data.objects if o.type == 'ARMATURE' and o.get("angelo_game_rig_engine") == "UNREAL"]
    check("unreal_made", result == {'FINISHED'} and len(games) == 1, sorted(result))
    if not games:
        return None
    game = games[0]
    report = skeleton_check.check(skeleton_facts.gather(bpy.context.scene, game), "UNREAL")
    # Optional bones may stay unmatched (the fixture's alike digits leave the fingers unmapped, rightly): every
    # required one must match.
    unmatched = [d for d in finding(report, "bone_match").get("details") or () if isinstance(d, dict)]
    check("unreal_no_failure_and_names_under_mannequin_parents",
          report["counts"]["fail"] == 0 and not any(d.get("required") for d in unmatched),
          {"verdict": report["verdict"], "required_unmatched": [d.get("bone") for d in unmatched if d.get("required")],
           "fails": [f["check"] for f in report["findings"] if f["status"] == "fail"]})
    names = {b.name for b in game.data.bones}
    # Each copied bone keeps its source's rest place, axes and length (a new edit bone given a matrix while it has
    # no length loses its direction: every bone pointed up, 2026-11-15).
    worst = 0.0
    for role, ue in list(UE_LIMBS.items()) + [("Hips", "pelvis")]:
        a = game.matrix_world @ game.data.bones[ue].matrix_local if ue in names else None
        b = rig.matrix_world @ rig.data.bones[NAMES[role]].matrix_local
        worst = max(worst, 1.0 if a is None else max(abs(x - y) for ra, rb in zip(a, b) for x, y in zip(ra, rb)),
                    0.0 if a is None else abs(game.data.bones[ue].length - rig.data.bones[NAMES[role]].length))
    check("unreal_bones_rest_like_their_sources", worst < 1e-4, {"worst": round(worst, 6)})
    check("unreal_prop_renamed_hips_are_pelvis", "pelvis_extra" in names and (
        game.matrix_world @ game.data.bones["pelvis"].head_local
        - rig.matrix_world @ rig.data.bones[NAMES["Hips"]].head_local).length < 1e-4)
    baked = [a for a in bpy.data.actions if a.get("angelo_game_rig_of") == rig.name]
    check("one_action_per_action_played", sorted(a.name for a in baked) == sorted(
        ["Wave_unreal", rig.animation_data.action.name + "_unreal"]), sorted(a.name for a in baked))
    walk_baked = next((a for a in baked if not a.name.startswith("Wave")), None)
    worst = 180.0
    if walk_baked:
        game.animation_data.action = walk_baked
        rig.animation_data.use_nla = False
        worst = 0.0
        for f in SAMPLES:
            bpy.context.scene.frame_set(f)
            for role, ue in UE_LIMBS.items():
                worst = max(worst, angle(world_dir(rig, NAMES[role])[0], world_dir(game, ue)[0]))
        rig.animation_data.use_nla = nla_before
    check("bake_is_the_action_alone", worst < 1.0, {"worst_degrees": round(worst, 3)})
    copies = [o for o in bpy.data.objects if o.type == 'MESH' and o.parent is game]
    copy = copies[0] if copies else None
    check("mesh_copy_in_place_and_bound", copy is not None and all(
        abs(a - b) < 1e-5 for ra, rb in zip(copy.matrix_world, mesh_world) for a, b in zip(ra, rb))
        and "pelvis" in copy.vertex_groups and any(m.type == 'ARMATURE' and m.object is game for m in copy.modifiers),
        {"copies": [o.name for o in copies]})
    collection = game.users_collection[0] if game.users_collection else None
    check("own_collection", collection is not None and copy is not None and copy.users_collection[0] is collection
          and game.name not in rig.users_collection[0].objects, collection.name if collection else None)
    check("control_unchanged", rig.animation_data.action is action_before and rig.animation_data.use_nla is nla_before
          and {pb.name: pb.rotation_mode for pb in rig.pose.bones} == modes_before)
    return game


def other_engines(rig):
    from angelo_agent import skeleton_check, skeleton_facts
    for engine, wanted in (("UNITY", {"Hips", "LeftUpperLeg", "RightHand"}), ("GODOT", {"Root", "Hips", "Head"})):
        result = bpy.ops.angelo.make_game_rig(source=rig.name, target_engine=engine)
        game = next((o for o in bpy.data.objects if o.get("angelo_game_rig_engine") == engine), None)
        report = skeleton_check.check(skeleton_facts.gather(bpy.context.scene, game), engine) if game else None
        names = {b.name for b in game.data.bones} if game else set()
        hips_parent = game.data.bones["Hips"].parent if game and "Hips" in names else None
        root_ok = (hips_parent is None) if engine == "UNITY" else (hips_parent is not None and hips_parent.name == "Root")
        check("%s_names_root_no_failure" % engine.lower(), result == {'FINISHED'} and report is not None
              and report["counts"]["fail"] == 0 and wanted <= names and root_ok,
              {"verdict": report and report["verdict"], "missing": sorted(wanted - names), "result": sorted(result),
               "hips_parent": hips_parent.name if hips_parent else None,
               "fails": [(f["check"], f.get("found")) for f in report["findings"] if f["status"] == "fail"]
               if report else None})


def rigify_case():
    """Rigify's human, generated; a point cloud weighted one vertex per deforming bone stands in for a skin."""
    from angelo_agent import skeleton_check, skeleton_facts
    bpy.ops.preferences.addon_enable(module="rigify")
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.armature_human_metarig_add()
    metarig = bpy.context.view_layer.objects.active
    bpy.ops.pose.rigify_generate()
    rig = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and o.data.get("rig_id") and o is not metarig)
    bpy.ops.object.mode_set(mode='OBJECT')
    deform = [b for b in rig.data.bones if b.use_deform]
    mesh = bpy.data.meshes.new("rigify_skin")
    mesh.from_pydata([tuple(rig.matrix_world @ ((b.head_local + b.tail_local) / 2)) for b in deform], [], [])
    skin = bpy.data.objects.new("rigify_skin", mesh)
    bpy.context.scene.collection.objects.link(skin)
    skin.modifiers.new("Armature", 'ARMATURE').object = rig
    for k, bone in enumerate(deform):
        skin.vertex_groups.new(name=bone.name).add([k], 1.0, 'REPLACE')
    for engine in ("UNREAL", "UNITY", "GODOT"):
        result = bpy.ops.angelo.make_game_rig(source=rig.name, target_engine=engine)
        game = next((o for o in bpy.data.objects if o.get("angelo_game_rig_of") == rig.name
                     and o.get("angelo_game_rig_engine") == engine), None)
        report = skeleton_check.check(skeleton_facts.gather(bpy.context.scene, game), engine) if game else None
        tops = [b.name for b in game.data.bones if b.parent is None] if game else []
        check("rigify_%s_one_root_no_failure" % engine.lower(), result == {'FINISHED'} and report is not None
              and len(tops) == 1 and report["counts"]["fail"] == 0,
              {"tops": tops[:8], "top_count": len(tops), "verdict": report and report["verdict"],
               "fails": [(f["check"], f.get("found")) for f in report["findings"] if f["status"] == "fail"]
               if report else None})


def glb_animations(path):
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    return [a.get("name") for a in json.loads(data[20:20 + length]).get("animations", [])]


def clips_case(game):
    baked = sorted(a.name for a in bpy.data.actions if a.get("angelo_game_rig_of") == game.get("angelo_game_rig_of")
                   and a.name.endswith("_unreal"))
    ad = game.animation_data
    other = next(a for a in bpy.data.actions if a.name in baked and a is not ad.action)
    track = ad.nla_tracks.new()
    track.name = "ExistingTrack"
    track.strips.new(other.name, 1, other)
    from angelo_agent import owned_paths
    path = OUT / "clips.glb"
    owned_paths.vouch(str(path))   # The person's pick in the operator's save browser.
    result = bpy.ops.angelo.export_action_clips(armature=game.name, filepath=str(path), format='GLB')
    clips = glb_animations(path) if path.exists() else []
    check("glb_clips_exactly_the_actions", result == {'FINISHED'} and sorted(clips) == baked
          and track.name == "ExistingTrack" and len(ad.nla_tracks) == 1, {"clips": clips, "actions": baked})
    fbx = OUT / "hero.fbx"
    owned_paths.vouch(str(fbx))
    result = bpy.ops.angelo.export_action_clips(armature=game.name, filepath=str(fbx), format='FBX')
    files = sorted(p.name for p in OUT.glob("hero*.fbx"))
    expected = sorted(["hero.fbx"] + ["hero@%s.fbx" % name for name in baked])
    imported = 0
    if result == {'FINISHED'} and files == expected:
        before = set(bpy.data.actions)
        bpy.ops.import_scene.fbx(filepath=str(OUT / ("hero@%s.fbx" % baked[0])))
        imported = sum(1 for a in bpy.data.actions if a not in before and a.frame_range[1] - a.frame_range[0] >= 30)
    check("fbx_model_and_one_file_per_clip", files == expected and imported >= 1,
          {"files": files, "expected": expected, "imported_actions": imported})
    second = ad.nla_tracks.new()
    second.name = "Sequence"
    second.strips.new(baked[0], 1, bpy.data.actions[baked[0]])
    second.strips.new(baked[1], 100, bpy.data.actions[baked[1]])
    refused = OUT / "refused.glb"
    owned_paths.vouch(str(refused))   # So the refusal is the sequence's, not the path's.
    try:
        result = bpy.ops.angelo.export_action_clips(armature=game.name, filepath=str(refused), format='GLB')
    except RuntimeError:
        result = {'CANCELLED'}
    check("multi_strip_track_refused", result == {'CANCELLED'} and not refused.exists())


def main():
    try:
        rig, mesh = control_rig()
        game = unreal_case(rig, mesh)
        other_engines(rig)
        if game:
            clips_case(game)
        rigify_case()
        state["success"] = len(state["checks"]) == 17 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_GAME_RIG", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
