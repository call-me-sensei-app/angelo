# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Retargeting by role, the game rig, root motion and per-action clips, in a real background Blender.

Blender has no built-in retargeting; game and mocap pipelines need it (DCC gaps, gap 4; game-engine requests, themes
1-2: retargeted animation must also pass the engine's rig check, with root motion and one clip per action). Angelo
already finds which bone plays each role from a rig's layout, never its names (``skeleton_roles.map_roles``,
``doc/angelo/engine_skeleton.rst``). Plan ``release-2.1.0-plan.md``, sections 6-7.

Two procedural humanoids that differ in everything a real pair differs in: proportions (the target is 0.7 times the
source's height, with longer arms for its size), rest pose (T-pose source, A-pose target), bone names (none shared),
clavicles (the source has them, the target not), a root bone (the target only). The source walks: hips forward and
bobbing, legs and arms swinging.

* ``angelo.retarget_animation`` bakes a new action on the target. Every limb bone (upper and lower arms and legs,
  both sides) and the spine points the way the source's does on every sampled frame (within 5 degrees), whatever
  the rest poses; the hips' height follows the source's scaled by the rigs' hip heights; the source is unchanged.
* ``root_motion='IN_PLACE'`` keeps the hips over the origin; ``'ROOT'`` moves the target's root bone by the
  source's scaled horizontal travel and keeps the hips over the root.
* Feet slide no more than the source's feet do (scaled), plus 1% of the target's height; played in place, the feet
  are measured in the travelling frame (a planted foot moves back with the floor at the travel's speed).
* **Facing**: a target turned 90 degrees about the vertical gets the source's motion turned with it: limbs within 5
  degrees of the turned source's, the hips travelling along the target's own forward.
* **Feet planted** (``plant_feet``): from a source whose feet are planted (leg IK held on the floor while the hips
  travel), the target's feet slide less than 0.5% of its height over each planted stretch; legs are in IK.
* ``angelo.make_game_rig`` turns a control rig (non-deforming controls, an IK constraint, names of its own) into a
  deform-only armature named for Unreal, its actions baked one per action, which ``skeleton_check`` passes for
  Unreal with no failure; ``angelo.extract_root_motion`` moves the hips' horizontal travel to its ``root``.
* ``angelo.export_action_clips`` writes a glTF whose animations are exactly the game rig's actions, by name.

Agent use (the same operations through the agent) is gated on Opus 5.5 and GPT 6.1 Sol by scenario runs, not here.
Run through ``run_native_suites.py --include test_blender_retarget``; check ``retarget.json`` for ``success``.
Expected to fail on 2.0.0 (06259fa5bb0): none of these operators exists there.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import struct
import tempfile
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-retarget-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from qa_humanoid import FRAMES, angle, humanoid, walk, world_dir  # noqa: E402
bpy.context.preferences.use_preferences_save = False

REPORT = OUT / "retarget.json"
SAMPLES = (1, 8, 15, 22, 29, 36)
LIMBS = [side + part for side in ("Left", "Right") for part in ("UpperArm", "LowerArm", "UpperLeg", "LowerLeg")]
state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


SOURCE = {role: role for role in ["Hips", "Spine", "Chest", "Neck", "Head", "Root"] + [
    side + part for side in ("Left", "Right") for part in ("Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg",
                                                           "LowerLeg", "Foot", "Toes")]}
SOURCE = {role: "src_" + role.lower() for role in SOURCE}
TARGET = {role: "Tgt-" + role for role in SOURCE}
TARGET.update({side + "FootIK": "CTRL_" + side + "_foot" for side in ("Left", "Right")})


def run(op, **kw):
    return getattr(bpy.ops.angelo, op)(**kw)


def retarget_case():
    src = humanoid("Source", SOURCE, 1.8, 0, clavicles=True, root=False)
    walk(src, SOURCE)
    src_before = _pose_snapshot(src)
    results = {}
    for mode in ("IN_PLACE", "ROOT"):
        tgt = humanoid("Target" + mode, TARGET, 1.26, 40, clavicles=False, root=True, arm_scale=1.15)
        result = run("retarget_animation", source=src.name, target=tgt.name, root_motion=mode)
        results[mode] = (result, tgt)
    worst, hips_ratio, slides = 0.0, [], {}
    for mode, (result, tgt) in results.items():
        if result != {'FINISHED'}:
            check("retarget_%s_finished" % mode.lower(), False)
            continue
        check("retarget_%s_finished" % mode.lower(), True)
        travel_src, travel_tgt = [], []
        for f in SAMPLES:
            bpy.context.scene.frame_set(f)
            for role in LIMBS + ["Spine"]:
                d_src, _, _ = world_dir(src, SOURCE[role])
                d_tgt, _, _ = world_dir(tgt, TARGET[role])
                worst = max(worst, angle(d_src, d_tgt))
            _, hs, _ = world_dir(src, SOURCE["Hips"])
            _, ht, _ = world_dir(tgt, TARGET["Hips"])
            hips_ratio.append(ht.z / hs.z)
            _, root_head, _ = world_dir(tgt, TARGET["Root"])
            travel_src.append(Vector((hs.x, hs.y)))
            travel_tgt.append((Vector((ht.x, ht.y)), Vector((root_head.x, root_head.y))))
        start_src = travel_src[0]
        span_src = max((p - start_src).length for p in travel_src) * 0.7
        if mode == "IN_PLACE":
            hips_span = max(h.length for h, _ in travel_tgt)
            check("in_place_hips_stay", hips_span < 0.02 * 1.26, {"hips_span": round(hips_span, 4)})
        else:
            root_span = max((r - travel_tgt[0][1]).length for _, r in travel_tgt)
            over_root = max((h - r).length for h, r in travel_tgt)
            check("root_motion_on_root", abs(root_span - span_src) <= 0.05 * span_src + 0.01 and over_root < 0.03,
                  {"root_span": round(root_span, 3), "source_span_scaled": round(span_src, 3),
                   "hips_off_root": round(over_root, 3)})
        slides[mode] = _foot_slide(src, tgt, travelling=(mode == "IN_PLACE"), scale=0.7)
    check("limbs_follow_source", worst < 5.0, {"worst_degrees": round(worst, 2)})
    expected = 1.26 / 1.8
    check("hips_height_scaled", hips_ratio and max(abs(r - expected) for r in hips_ratio) < 0.05 * expected,
          {"ratios": [round(r, 3) for r in hips_ratio[:6]]})
    check("feet_slide_no_more", slides and all(t <= s * 0.7 + 0.01 * 1.26 for s, t in slides.values()),
          {"slides": {m: [round(v, 4) for v in st] for m, st in slides.items()}})
    check("source_unchanged", _pose_snapshot(src) == src_before)
    return src


def _pose_snapshot(obj):
    """Every bone's posed matrix at the sampled frames, rounded: what the source's animation does."""
    out = []
    for f in SAMPLES:
        bpy.context.scene.frame_set(f)
        out.append(tuple(round(v, 5) for pb in obj.pose.bones for row in pb.matrix for v in row))
    return out


def _foot_slide(src, tgt, travelling=False, scale=1.0):
    """(source, target) horizontal travel of the left foot over the frames where the source's foot is lowest. With
    ``travelling`` (the target plays in place), the target's points get the source hips' scaled travel added back."""
    heights = []
    for f in FRAMES:
        bpy.context.scene.frame_set(f)
        _, head, _ = world_dir(src, SOURCE["LeftFoot"])
        heights.append((head.z, f))
    planted = [f for _, f in sorted(heights)[:8]]
    travel = {}
    for name, obj, bone in (("src", src, SOURCE["LeftFoot"]), ("tgt", tgt, TARGET["LeftFoot"])):
        points = []
        for f in sorted(planted):
            bpy.context.scene.frame_set(f)
            _, head, _ = world_dir(obj, bone)
            if travelling and name == "tgt":
                _, hips, _ = world_dir(src, SOURCE["Hips"])
                rest = src.matrix_world @ src.data.bones[SOURCE["Hips"]].head_local
                head = head + Vector((hips.x - rest.x, hips.y - rest.y, 0.0)) * scale
            points.append(Vector((head.x, head.y)))
        travel[name] = sum((b - a).length for a, b in zip(points, points[1:]))
    return travel["src"], travel["tgt"]


def facing_case(src):
    """A target turned 90 degrees: limbs follow the turned source, hips travel along the target's forward."""
    turn = Matrix.Rotation(math.radians(90), 3, 'Z')
    tgt = humanoid("TargetTurned", TARGET, 1.26, 40, clavicles=False, root=True)
    tgt.rotation_euler.z = math.radians(90)
    bpy.context.view_layer.update()
    result = run("retarget_animation", source=src.name, target=tgt.name, root_motion='KEEP')
    worst, travel_error = 180.0, 1.0
    if result == {'FINISHED'}:
        worst, starts = 0.0, None
        for f in SAMPLES:
            bpy.context.scene.frame_set(f)
            for role in LIMBS + ["Spine"]:
                worst = max(worst, angle(turn @ world_dir(src, SOURCE[role])[0], world_dir(tgt, TARGET[role])[0]))
            hs, ht = world_dir(src, SOURCE["Hips"])[1], world_dir(tgt, TARGET["Hips"])[1]
            starts = starts or (hs.copy(), ht.copy())
        moved_src = turn @ Vector(((hs - starts[0]).x, (hs - starts[0]).y, 0.0)) * 0.7
        moved_tgt = Vector(((ht - starts[1]).x, (ht - starts[1]).y, 0.0))
        travel_error = (moved_src - moved_tgt).length / max(moved_src.length, 1e-6)
    check("turned_target_follows_turned_source", worst < 5.0 and travel_error < 0.05,
          {"worst_degrees": round(worst, 2), "travel_error": round(travel_error, 3)})


def planted_walk(obj, names, height):
    """Hips travel at a steady speed; each foot's IK effector stands still on the floor for half a step and swings
    forward, lifted, for the other half: feet that really are planted. Relative to the hips a planted foot runs from
    half a stride ahead to under them, inside the lowered leg's reach."""
    bpy.ops.angelo.add_ik_controls(armature=obj.name)
    action = bpy.data.actions.new(obj.name + "PlantedWalk")
    obj.animation_data_create().action = action
    s, stride = height / 1.8, 0.6 * height / 1.8
    hips = obj.pose.bones[names["Hips"]]
    effectors = {side: next(b.name for b in obj.data.bones if b.get("angelo_ik_kind") == "effector"
                            and b.get("angelo_ik_for") == names[side + "Foot"]) for side in ("Left", "Right")}
    for f in FRAMES:
        forward = stride * (f - 1) / 20
        # The hips sit 10% of the height lower than at rest, so a straight leg can reach every planted place.
        hips.location = obj.data.bones[names["Hips"]].matrix_local.inverted().to_3x3() @ Vector(
            (0, -forward, -0.1 * s))
        hips.keyframe_insert("location", frame=f)
        for side, phase in (("Left", 0), ("Right", 10)):
            t = (f - 1 + phase) % 20
            step = (f - 1 + phase) // 20
            if t < 10:   # Stance: still on the floor.
                offset, lift = stride * step + stride * 0.5 - (stride * phase / 20), 0.0
            else:        # Swing: forward and lifted.
                u = (t - 10) / 10
                offset, lift = stride * step + stride * (0.5 + u) - (stride * phase / 20), 0.1 * s * math.sin(math.pi * u)
            pb = obj.pose.bones[effectors[side]]
            pb.location = obj.data.bones[effectors[side]].matrix_local.inverted().to_3x3() @ Vector((0, -offset, lift))
            pb.keyframe_insert("location", frame=f)
    return action


def planted_case():
    src = humanoid("PlantedSource", SOURCE, 1.8, 0, clavicles=True, root=False)
    planted_walk(src, SOURCE, 1.8)
    tgt = humanoid("PlantedTarget", TARGET, 1.26, 40, clavicles=False, root=True)
    result = run("retarget_animation", source=src.name, target=tgt.name, root_motion='KEEP', plant_feet=True)
    stretches, worst = [], 1.0
    if result == {'FINISHED'}:
        worst, run_ = 0.0, []
        for f in FRAMES:   # The source's stance frames for the left foot, from planted_walk's phase.
            if (f - 1) % 20 < 10:
                run_.append(f)
            elif run_:
                stretches.append(run_)
                run_ = []
        for frames_ in stretches:
            points = []
            for f in frames_:
                bpy.context.scene.frame_set(f)
                head = world_dir(tgt, TARGET["LeftFoot"])[1]
                points.append(Vector((head.x, head.y)))
            worst = max(worst, max((p - points[0]).length for p in points))
    check("planted_feet_do_not_slide", worst < 0.005 * 1.26, {"worst_slide": round(worst, 4),
                                                              "stretches": len(stretches)})


def game_rig_case(src):
    control = humanoid("ControlRig", TARGET, 1.26, 40, clavicles=False, root=True, controls=True)
    run("retarget_animation", source=src.name, target=control.name, root_motion='KEEP')
    result = run("make_game_rig", source=control.name, target_engine='UNREAL')
    games = [o for o in bpy.data.objects if o.type == 'ARMATURE' and o.get("angelo_game_rig_of") == control.name]
    if result != {'FINISHED'} or len(games) != 1:
        check("game_rig_made", False, {"result": sorted(result)})
        return
    game = games[0]
    names = {b.name for b in game.data.bones}
    check("game_rig_deform_only_unreal_names", all(b.use_deform for b in game.data.bones)
          and {"root", "pelvis", "thigh_l", "calf_l", "foot_l", "upperarm_r", "head"} <= names, sorted(names)[:20])
    from angelo_agent import skeleton_check, skeleton_facts
    report = skeleton_check.check(skeleton_facts.gather(bpy.context.scene, game), "UNREAL")
    check("game_rig_passes_unreal_check", report["counts"]["fail"] == 0,
          {"verdict": report["verdict"], "fails": [f["id"] if "id" in f else f.get("title") for f in report["findings"]
                                                  if f["status"] == "fail"]})
    baked = sorted(a.name for a in bpy.data.actions if a.get("angelo_game_rig_of") == control.name)
    check("game_rig_one_action_per_source_action", len(baked) >= 1, baked)
    result = run("extract_root_motion", armature=game.name, mode='ROOT')
    bpy.context.scene.frame_set(SAMPLES[0])
    pelvis0 = game.matrix_world @ game.pose.bones["pelvis"].head
    root0 = game.matrix_world @ game.pose.bones["root"].head
    bpy.context.scene.frame_set(SAMPLES[-1])
    pelvis1 = game.matrix_world @ game.pose.bones["pelvis"].head
    root1 = game.matrix_world @ game.pose.bones["root"].head
    moved = Vector((root1.x - root0.x, root1.y - root0.y)).length
    over = Vector((pelvis1.x - root1.x, pelvis1.y - root1.y)).length
    check("root_motion_extracted", result == {'FINISHED'} and moved > 0.1 and over < 0.03,
          {"root_moved": round(moved, 3), "pelvis_off_root": round(over, 3)})
    path = OUT / "clips.glb"
    result = run("export_action_clips", armature=game.name, filepath=str(path))
    clips = _glb_animation_names(path) if path.exists() else []
    check("clips_one_per_action", result == {'FINISHED'} and sorted(clips) == baked, {"clips": clips, "actions": baked})


def _glb_animation_names(path):
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + length])
    return [a.get("name") for a in document.get("animations", [])]


def main():
    try:
        missing = [op for op in ("retarget_animation", "make_game_rig", "extract_root_motion", "export_action_clips")
                   if not hasattr(bpy.ops.angelo, op)]
        if missing:
            check("operators_exist", False, missing)
        else:
            check("operators_exist", True)
            src = retarget_case()
            facing_case(src)
            planted_case()
            game_rig_case(src)
        state["success"] = len(state["checks"]) >= 15 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_RETARGET", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
