# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export Action Clips in a background Blender: what each clip is, in both formats (the 2.1.0 clip export break-it
review of 980206f3f0a, M1 and S1-S6; doc/angelo/rigging.rst, "Clips"). Fails on 980206f3f0a.

A humanoid control rig in a 1-250 scene plays a 40-frame walk as its active action and a 20-frame run on an NLA track
whose strip is played at twice its length; a muted track plays an idle. Its skinned mesh has a shape key whose own
NLA track is named like the walk. Checked:

* **M1, each clip's own frames**: the glTF animations and the FBX clips (read back with Blender's importer) last
  exactly what each clip plays (the walk 40 frames, the run's strip 39), never the scene's 250, and differ per clip.
* **S3, one clip definition**: glTF and FBX carry the same clips, the muted track in neither; two tracks playing one
  action give two clips with distinct names.
* **S5/S6**: a clip file holds the armature alone, unless the clip plays shape keys: then their mesh, its shape keys
  animated; the glTF walk carries the mesh's weights.
* **S2, slots**: a strip playing the second slot of a two-slot action exports that slot's motion, and no slot is
  added to any action.
* **S1, nothing of the person's changes**: with the second clip's write made to fail, the export fails and the scene
  is as it was (active action and slot, NLA switch, track names and mutes, object names, frame range, selection), no
  copy left, no clip file left behind.
* **S4**: a hidden mesh, or an armature in an excluded collection, is refused by name with nothing written.

Run through ``run_native_suites.py --include test_blender_clip_export``; check ``clip-export.json`` for ``success``.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-clip-export-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
addon_utils.enable("io_scene_fbx", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False
from qa_humanoid import humanoid, walk  # noqa: E402
from qa_clip_files import fbx_clip, glb  # noqa: E402

REPORT = OUT / "clip-export.json"
ROLES = ["Hips", "Spine", "Chest", "Neck", "Head"] + [
    side + part for side in ("Left", "Right") for part in ("UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg",
                                                         "Foot", "Toes")]
NAMES = {role: "b." + role for role in ROLES}
EXPECTED = 14
state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def export(rig, path, fmt):
    try:
        return bpy.ops.angelo.export_action_clips(armature=rig.name, filepath=str(path), format=fmt)
    except RuntimeError as error:   # A background operator's error report raises.
        state["details"].setdefault("refusals", []).append(str(error).strip()[:240])
        return {'CANCELLED'}


def keyed(name, rig, frames, bone, value):
    action = bpy.data.actions.new(name)
    ad = rig.animation_data_create()
    held = ad.action
    ad.action = action
    pb = rig.pose.bones[NAMES[bone]]
    pb.rotation_mode = 'XYZ'
    for f in frames:
        pb.rotation_euler = Euler((value * math.sin(f / 3.0), 0, 0))
        pb.keyframe_insert("rotation_euler", frame=f)
    ad.action = held
    return action


def scene_fixture():
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 250
    rig = humanoid("Hero", NAMES, 1.2, 40, clavicles=False, root=True)
    walk_action = walk(rig, NAMES)   # Frames 1-40, the active action.
    run = keyed("Run", rig, range(1, 21), "LeftUpperArm", 0.8)
    idle = keyed("Idle", rig, range(1, 31), "Head", 0.2)
    ad = rig.animation_data
    track = ad.nla_tracks.new()
    track.name = "RunTrack"
    strip = track.strips.new("Run", 1, run)
    strip.scale = 2.0   # Played at twice its length: 39 frames.
    muted = ad.nla_tracks.new()
    muted.name = "IdleTrack"
    muted.strips.new("Idle", 1, idle)
    muted.mute = True
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 0, 0.9))
    mesh = bpy.context.active_object
    mesh.name = "Body"
    for bone in rig.data.bones:
        if bone.use_deform:
            mesh.vertex_groups.new(name=bone.name).add(list(range(len(mesh.data.vertices))), 0.1, 'REPLACE')
    mesh.modifiers.new("Armature", 'ARMATURE').object = rig
    mesh.parent = rig
    mesh.shape_key_add(name="Basis")
    smile = mesh.shape_key_add(name="Smile")
    keys = mesh.data.shape_keys
    face = bpy.data.actions.new("WalkFace")
    kad = keys.animation_data_create()
    kad.action = face
    for f, v in ((1, 0.0), (20, 1.0), (40, 0.0)):
        smile.value = v
        smile.keyframe_insert("value", frame=f)
    kad.action = None
    ktrack = kad.nla_tracks.new()
    ktrack.name = walk_action.name   # Named like the walk: it plays in the walk.
    ktrack.strips.new("WalkFace", 1, face)
    return rig, mesh, walk_action


def snapshot(rig):
    ad = rig.animation_data
    scene = bpy.context.scene
    return {"action": ad.action.name if ad.action else None, "slot": ad.action_slot.identifier if ad.action_slot
            else None, "use_nla": ad.use_nla, "tracks": [(t.name, t.mute, len(t.strips)) for t in ad.nla_tracks],
            "objects": sorted(o.name for o in bpy.data.objects), "frames": (scene.frame_start, scene.frame_end),
            "selected": sorted(o.name for o in bpy.context.selected_objects),
            "slots": {a.name: len(a.slots) for a in bpy.data.actions}}


def clips_case(rig, walk_action):
    folder = OUT / "clips"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    before = snapshot(rig)
    run_strip = rig.animation_data.nla_tracks["RunTrack"].strips[0]
    want = {walk_action.name: 40, "Run": round(run_strip.frame_end - run_strip.frame_start) + 1}
    result = export(rig, folder / "hero.glb", 'GLB')
    found = glb(folder / "hero.glb") if (folder / "hero.glb").exists() else {}
    check("glb_clips_are_the_clips", result == {'FINISHED'} and sorted(found) == sorted(want), sorted(found))
    check("glb_each_clip_lasts_its_own_frames", all(abs(found.get(n, {}).get("frames", 0) - f) <= 1
                                                    for n, f in want.items()), {"found": found, "want": want})
    check("glb_walk_carries_the_shape_keys", "weights" in found.get(walk_action.name, {}).get("paths", []), found)
    result = export(rig, folder / "hero.fbx", 'FBX')
    after = snapshot(rig)   # Before reading the clips back: Blender's importer selects what it brings in.
    files = sorted(p.name for p in folder.glob("hero@*.fbx"))
    check("fbx_clips_are_the_clips", result == {'FINISHED'} and files == sorted("hero@%s.fbx" % n for n in want),
          files)
    read = {n: fbx_clip(folder / ("hero@%s.fbx" % n)) for n in want if (folder / ("hero@%s.fbx" % n)).exists()}
    check("fbx_each_clip_lasts_its_own_frames", read and all(abs(read[n]["frames"] - f) <= 1 for n, f in want.items()
                                                            if n in read), {"read": read, "want": want})
    check("fbx_clip_files_hold_the_armature_alone", read.get("Run", {}).get("types") == ["ARMATURE"], read)
    check("fbx_shape_keys_play_in_their_clip", read.get(walk_action.name, {}).get("shape_keys")
          and "MESH" in read.get(walk_action.name, {}).get("types", []), read)
    check("export_changes_nothing", after == before, {"before": before, "after": after})


def twin_case():
    """Two tracks playing one action, and a two-slot action whose strip plays its second slot."""
    folder = OUT / "twin"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    rig = humanoid("Twin", NAMES, 1.2, 40, clavicles=False, root=True)
    jog = keyed("Jog", rig, range(1, 11), "Head", 0.3)
    ad = rig.animation_data_create()
    for name in ("JogA", "JogB"):
        track = ad.nla_tracks.new()
        track.name = name
        track.strips.new("Jog", 1, jog)
    slotted = bpy.data.actions.new("Slotted")
    hips = rig.pose.bones[NAMES["Hips"]]
    ad.action = slotted   # Its first slot is made for the rig: keys sideways on it (another object's motion).
    for f, v in ((1, 0.0), (10, 0.5)):
        hips.location = (v, 0.0, 0.0)
        hips.keyframe_insert("location", index=0, frame=f)
    mine = slotted.slots.new('OBJECT', "Mine")   # The second slot: the motion the strip plays.
    ad.action_slot = mine
    for f, v in ((1, 0.0), (10, 0.5)):
        hips.location = (0.0, 0.0, v)
        hips.keyframe_insert("location", index=2, frame=f)
    ad.action = None
    hips.location = (0.0, 0.0, 0.0)
    track = ad.nla_tracks.new()
    track.name = "SlottedTrack"
    strip = track.strips.new("Slotted", 1, slotted)
    strip.action_slot = mine
    slots_before = {a.name: len(a.slots) for a in bpy.data.actions}
    result = export(rig, folder / "twin.glb", 'GLB')
    found = glb(folder / "twin.glb") if (folder / "twin.glb").exists() else {}
    jogs = [n for n in found if n.startswith("Jog")]
    check("two_tracks_of_one_action_are_two_named_clips", result == {'FINISHED'} and len(jogs) == 2
          and len(set(jogs)) == 2, sorted(found))
    export(rig, folder / "twin.fbx", 'FBX')
    clip = folder / "twin@Slotted.fbx"

    def hips_move(obj):
        """World-space hips travel from frame 1 to 10 (independent of how a file converts bone axes)."""
        scene = bpy.context.scene
        scene.frame_set(1)
        start = obj.matrix_world @ obj.pose.bones[NAMES["Hips"]].head
        scene.frame_set(10)
        return (obj.matrix_world @ obj.pose.bones[NAMES["Hips"]].head) - start
    for other in ("JogA", "JogB"):
        ad.nla_tracks[other].mute = True
    want = hips_move(rig)   # The rig playing the strip, with its own slot.
    for other in ("JogA", "JogB"):
        ad.nla_tracks[other].mute = False
    got = None
    if clip.exists():
        before_objects = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=str(clip))
        imported = next((o for o in bpy.data.objects if o not in before_objects and o.type == 'ARMATURE'), None)
        got = hips_move(imported) if imported else None
        for obj in [o for o in bpy.data.objects if o not in before_objects]:
            bpy.data.objects.remove(obj)
    ok = got is not None and want.length > 0.05 and (got - want).length < 0.25 * want.length
    check("a_strip_exports_its_own_slot", ok, {"want": [round(v, 3) for v in want],
                                               "got": None if got is None else [round(v, 3) for v in got]})
    check("no_slot_is_added_to_an_action", {a: n for a, n in {a.name: len(a.slots) for a in bpy.data.actions}.items()
                                            if a in slots_before} == slots_before)
    return rig


def failure_case(rig):
    import angelo_agent.action_clips as clips_module
    folder = OUT / "failure"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    before = snapshot(rig)
    real, calls = clips_module.write_fbx, []

    def failing(path, objects, bake):
        calls.append(path)
        if len(calls) == 3:   # The model, the first clip, then the second clip fails.
            raise RuntimeError("injected failure")
        return real(path, objects, bake)
    clips_module.write_fbx = failing
    raised = None
    try:   # Called directly: through the operator, Blender would log the injected error as a suite traceback.
        clips_module.export_action_clips(rig, str(folder / "hero.fbx"), 'FBX')
    except RuntimeError as error:
        raised = str(error)
    finally:
        clips_module.write_fbx = real
    left = sorted(p.name for p in folder.glob("hero@*.fbx"))
    check("a_failed_export_changes_nothing", raised == "injected failure" and snapshot(rig) == before and not left,
          {"raised": raised, "before": before, "after": snapshot(rig), "left": left, "calls": len(calls)})


def refusals(rig, mesh):
    folder = OUT / "refused"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    mesh.hide_set(True)
    result = export(rig, folder / "hidden.glb", 'GLB')
    mesh.hide_set(False)
    check("a_hidden_mesh_is_refused_by_name", result == {'CANCELLED'} and not (folder / "hidden.glb").exists()
          and any(mesh.name in r for r in state["details"].get("refusals", [])), state["details"].get("refusals"))
    collection = bpy.data.collections.new("Aside")
    bpy.context.scene.collection.children.link(collection)
    collection.objects.link(rig)
    bpy.context.scene.collection.objects.unlink(rig)
    bpy.context.view_layer.layer_collection.children["Aside"].exclude = True
    selected = sorted(o.name for o in bpy.context.selected_objects)
    result = export(rig, folder / "excluded.glb", 'GLB')
    check("an_excluded_armature_is_refused_by_name", result == {'CANCELLED'} and not (folder / "excluded.glb").exists()
          and sorted(o.name for o in bpy.context.selected_objects) == selected, state["details"].get("refusals"))


def main():
    try:
        rig, mesh, walk_action = scene_fixture()
        clips_case(rig, walk_action)
        twin_case()
        failure_case(rig)
        refusals(rig, mesh)
        state["success"] = len(state["checks"]) == EXPECTED and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_CLIP_EXPORT", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v),
         "count": len(state["checks"])}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
