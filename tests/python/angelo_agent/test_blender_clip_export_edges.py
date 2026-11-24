# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export Action Clips at its edges, in a background Blender (the clip export re-read of 30ade86d137, S-A to S-C and
its NITs; doc/angelo/rigging.rst, "Clips"). Fails on 30ade86d137. Checked:

* **S-A, a clip before frame 0**: an action keyed from -20 to 20 lasts 41 frames in both formats (a scene's range
  cannot start below 0, so the stage plays every clip from a non-negative origin), and the scene's range is as it was.
* **S-B, drivers**: a corrective shape key driven by an elbow bone plays in the elbow clip, in both formats; the
  person's driver still reads their own armature afterwards.
* **S-C, data the export cannot stage**: an armature linked from another file, and a library override of it, are
  refused by name, with nothing written and no traceback; when a step of putting the scene back fails, the others
  still run (the scene's frame range and current frame come back) and the failure is reported.
* **NITs**: the current frame's subframe is kept; names near Blender's length limit reach the file whole and come
  back whole;
  a strip's blend in and out and its animated influence play on the stage as in the scene; the FBX model file is
  written in the first clip's first pose (doc/angelo/rigging.rst).

Run through ``run_native_suites.py --include test_blender_clip_export_edges``; check ``clip-export-edges.json``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import shutil
import tempfile
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bpy  # noqa: E402
from mathutils import Euler  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-clip-export-edges-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
addon_utils.enable("io_scene_fbx", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False
from angelo_agent import action_clips, clip_set, clip_stage  # noqa: E402
from qa_clip_files import fbx_clip, glb, glb_document  # noqa: E402
from qa_humanoid import humanoid, walk  # noqa: E402

REPORT = OUT / "clip-export-edges.json"
ROLES = ["Hips", "Spine", "Chest", "Neck", "Head"] + [
    side + part for side in ("Left", "Right") for part in ("UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg",
                                                         "Foot", "Toes")]
NAMES = {role: "b." + role for role in ROLES}
EXPECTED = 12
state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def folder(name):
    path = OUT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def export(rig, path, fmt):
    try:
        return bpy.ops.angelo.export_action_clips(armature=rig.name, filepath=str(path), format=fmt), ""
    except RuntimeError as error:   # A background operator's error report raises.
        return {'CANCELLED'}, str(error).strip()[:400]


def skinned(rig, name):
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 0, 0.9))
    mesh = bpy.context.active_object
    mesh.name = name
    for bone in rig.data.bones:
        if bone.use_deform:
            mesh.vertex_groups.new(name=bone.name).add(list(range(len(mesh.data.vertices))), 0.1, 'REPLACE')
    mesh.modifiers.new("Armature", 'ARMATURE').object = rig
    mesh.parent = rig
    return mesh


def scene_frames():
    scene = bpy.context.scene
    return (scene.frame_start, scene.frame_end, scene.frame_current, round(scene.frame_subframe, 3))


def early_case():
    """S-A: a clip keyed from -20 to 20."""
    rig = humanoid("Early", NAMES, 1.2, 40, clavicles=False, root=True)
    action = bpy.data.actions.new("Leap")
    rig.animation_data_create().action = action
    hips = rig.pose.bones[NAMES["Hips"]]
    for f in range(-20, 21, 5):
        hips.location = (0, 0.02 * f, 0)
        hips.keyframe_insert("location", frame=f)
    out, before = folder("early"), scene_frames()
    result, _ = export(rig, out / "early.glb", 'GLB')
    found = glb(out / "early.glb") if (out / "early.glb").exists() else {}
    check("glb_a_clip_before_frame_0_keeps_its_frames", result == {'FINISHED'}
          and abs(found.get("Leap", {}).get("frames", 0) - 41) <= 1, found)
    result, _ = export(rig, out / "early.fbx", 'FBX')
    read = fbx_clip(out / "early@Leap.fbx") if (out / "early@Leap.fbx").exists() else {}
    check("fbx_a_clip_before_frame_0_keeps_its_frames", result == {'FINISHED'} and abs(read.get("frames", 0) - 41) <= 1
          and scene_frames() == before, {"read": read, "frames": [before, scene_frames()]})
    return rig


def driven_case():
    """S-B: a corrective shape key driven by the elbow's bend."""
    rig = humanoid("Bend", NAMES, 1.2, 40, clavicles=False, root=True)
    action = bpy.data.actions.new("BendArm")
    rig.animation_data_create().action = action
    elbow = rig.pose.bones[NAMES["LeftLowerArm"]]
    elbow.rotation_mode = 'XYZ'
    for f in range(1, 21):
        elbow.rotation_euler = Euler((1.5 * (f - 1) / 19, 0, 0))
        elbow.keyframe_insert("rotation_euler", frame=f)
    mesh = skinned(rig, "Elbow")
    mesh.shape_key_add(name="Basis")
    corrective = mesh.shape_key_add(name="Corrective")
    corrective.data[0].co.z += 0.1
    driver = corrective.driver_add("value").driver
    driver.type = 'SUM'
    var = driver.variables.new()
    var.type = 'TRANSFORMS'
    target = var.targets[0]
    target.id, target.bone_target = rig, elbow.name
    target.transform_type, target.transform_space = 'ROT_X', 'LOCAL_SPACE'
    out = folder("driven")
    result, _ = export(rig, out / "bend.fbx", 'FBX')
    read = fbx_clip(out / "bend@BendArm.fbx") if (out / "bend@BendArm.fbx").exists() else {}
    check("fbx_a_driven_shape_key_plays_in_its_clip", result == {'FINISHED'} and "MESH" in read.get("types", [])
          and read.get("shape_keys_move"), read)
    result, _ = export(rig, out / "bend.glb", 'GLB')
    found = glb(out / "bend.glb") if (out / "bend.glb").exists() else {}
    check("glb_a_driven_shape_key_plays_in_its_clip", "weights" in found.get("BendArm", {}).get("paths", []), found)
    drivers = mesh.data.shape_keys.animation_data.drivers
    kept = len(drivers) == 1 and drivers[0].driver.variables[0].targets[0].id == rig
    check("the_persons_driver_still_reads_their_armature", kept,
          [(fc.data_path, [t.id.name if t.id else None for v in fc.driver.variables for t in v.targets])
           for fc in drivers])


def linked_case():
    """S-C: an armature linked from another file, and a library override of it."""
    out = folder("linked")
    rig = humanoid("Borrowed", NAMES, 1.2, 40, clavicles=False, root=True)
    walk(rig, NAMES)
    mesh = skinned(rig, "BorrowedBody")
    library = out / "library.blend"
    bpy.data.libraries.write(str(library), {rig, mesh}, fake_user=True)
    for obj in (mesh, rig):
        bpy.data.objects.remove(obj)
    with bpy.data.libraries.load(str(library), link=True) as (_, into):
        into.objects = ["Borrowed", "BorrowedBody"]
    for obj in into.objects:
        bpy.context.scene.collection.objects.link(obj)
    linked = next(o for o in into.objects if o.type == 'ARMATURE')
    before = (scene_frames(), sorted(o.name for o in bpy.data.objects))
    result, said = export(linked, out / "linked.glb", 'GLB')
    after = (scene_frames(), sorted(o.name for o in bpy.data.objects))
    check("a_linked_armature_is_refused_by_name", result == {'CANCELLED'} and not (out / "linked.glb").exists()
          and "Borrowed" in said and "Traceback" not in said and after == before, said)
    override = linked.override_create(remap_local_usages=True)
    result, said = export(override, out / "override.glb", 'GLB')
    check("a_library_override_is_refused_by_name", result == {'CANCELLED'} and not (out / "override.glb").exists()
          and "Borrowed" in said and "Traceback" not in said, said)


def restore_case(rig):
    """S-C: a step of putting the scene back fails; the others still run, and the failure is reported."""
    scene, out = bpy.context.scene, folder("restore")
    scene.frame_start, scene.frame_end = 1, 250
    scene.frame_set(7, subframe=0.5)
    before = scene_frames()
    real = getattr(clip_stage, "_restore_names", None)

    def failing(*args, **kwargs):
        raise RuntimeError("injected restore failure")
    clip_stage._restore_names = failing
    raised = None
    try:   # Called directly: through the operator, Blender would log the injected error as a suite traceback.
        action_clips.export_action_clips(rig, str(out / "step.glb"), 'GLB')
    except RuntimeError as error:
        raised = str(error)
    finally:
        if real is None:
            del clip_stage._restore_names
        else:
            clip_stage._restore_names = real
    after = scene_frames()
    for block in list(bpy.data.objects) + list(bpy.data.meshes):   # Put back the names the failed step left aside.
        if clip_stage.ASIDE in block.name:
            block.name = block.name.split(clip_stage.ASIDE)[0]
    check("a_failed_restore_step_leaves_the_others_and_is_reported", raised == "injected restore failure"
          and after == before, {"raised": raised, "before": before, "after": after})


def subframe_case(rig):
    scene, out = bpy.context.scene, folder("subframe")
    scene.frame_set(12, subframe=0.25)
    before = scene_frames()
    result, _ = export(rig, out / "sub.glb", 'GLB')
    check("the_current_subframe_is_kept", result == {'FINISHED'} and scene_frames() == before,
          {"before": before, "after": scene_frames()})


def name_limit():
    """The longest name Blender keeps, in bytes (probed: it has changed between releases)."""
    probe = bpy.data.objects.new("x" * 1000, None)
    limit = len(probe.name.encode())
    bpy.data.objects.remove(probe)
    return limit


def long_names_case():
    """Names a few bytes short of Blender's limit: an aside name is cut there, the person's must come back whole."""
    limit = name_limit()
    long_rig, long_mesh = "R" * (limit - 4), "M" * (limit - 4)
    rig = humanoid(long_rig, NAMES, 1.2, 40, clavicles=False, root=True)
    walk(rig, NAMES)
    mesh = skinned(rig, long_mesh)
    out = folder("long")
    result, _ = export(rig, out / "long.glb", 'GLB')
    nodes = [n.get("name") for n in glb_document(out / "long.glb").get("nodes", [])] if (out / "long.glb").exists() \
        else []
    check("long_names_reach_the_file_and_come_back_whole", result == {'FINISHED'} and long_rig in nodes
          and long_mesh in nodes and rig.name == long_rig and mesh.name == long_mesh,
          {"limit": limit, "nodes": [n[:12] + "..%d" % len(n) for n in nodes if n][:6], "rig": len(rig.name),
           "mesh": len(mesh.name)})


def blend_case():
    """A strip's blend in and out and animated influence, on the stage; the FBX model file's pose."""
    rig = humanoid("Blend", NAMES, 1.2, 40, clavicles=False, root=True)
    ad = rig.animation_data_create()
    head = rig.pose.bones[NAMES["Head"]]
    head.rotation_mode = 'XYZ'
    actions = {}
    for name, frames in (("Sway", range(5, 31)), ("Wave", range(1, 21))):
        actions[name] = bpy.data.actions.new(name)
        ad.action = actions[name]
        for f in frames:
            head.rotation_euler = Euler((0.3 * math.sin(f / 3.0), 0, 0))
            head.keyframe_insert("rotation_euler", frame=f)
    ad.action = actions["Sway"]   # The first clip: frames 5 to 30.
    track = ad.nla_tracks.new()
    track.name = "WaveTrack"
    strip = track.strips.new("Wave", 1, actions["Wave"])
    strip.use_auto_blend = False
    strip.blend_in, strip.blend_out = 3.0, 2.0
    strip.use_animated_influence = True
    for f, v in ((1, 0.2), (20, 0.8)):
        strip.influence = v
        strip.keyframe_insert("influence", frame=f)
    clips = clip_set.clips(rig)
    with clip_stage.stage(rig, [], clips) as staged:
        played = staged.tracks["Wave"][0][1].strips[0]
        curve = played.fcurves.find("influence")
        got = {"blend": [played.blend_in, played.blend_out], "animated": played.use_animated_influence,
               "influence": [round(k.co[1], 3) for k in curve.keyframe_points] if curve else []}
    check("a_strips_blend_and_animated_influence_play_on_the_stage",
          got == {"blend": [3.0, 2.0], "animated": True, "influence": [0.2, 0.8]}, got)
    out, seen, real = folder("model"), [], action_clips.write_fbx

    def watching(path, objects, bake):
        if not bake:   # The model file: which tracks play, and at which frame.
            arm = next(o for o in bpy.context.selected_objects if o.type == 'ARMATURE')
            seen.append({"frame": bpy.context.scene.frame_current,
                         "playing": sorted(t.name for t in arm.animation_data.nla_tracks if not t.mute)})
        return real(path, objects, bake)
    action_clips.write_fbx = watching
    try:
        result, _ = export(rig, out / "blend.fbx", 'FBX')
    finally:
        action_clips.write_fbx = real
    check("the_fbx_model_is_written_in_the_first_clips_first_pose", result == {'FINISHED'}
          and seen == [{"frame": 5, "playing": ["Sway"]}], seen)


def main():
    try:
        rig = early_case()
        driven_case()
        linked_case()
        restore_case(rig)
        subframe_case(rig)
        long_names_case()
        blend_case()
        state["success"] = len(state["checks"]) == EXPECTED and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_CLIP_EXPORT_EDGES", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v),
         "count": len(state["checks"])}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
