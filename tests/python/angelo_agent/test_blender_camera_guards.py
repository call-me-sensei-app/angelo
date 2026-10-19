# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an orbit was measured from, read again through Blender when the program commits, and the
driver paths the static walk now reads.

* With the static walk stubbed to "still" (``object_dependencies.walk``), a
  program that moves the camera through its parent, the frame (a camera whose
  location reads its own keyed rotation, which Blender evaluates a step
  late), or before deleting it is refused at commit (``CAMERA_MOVED``) with
  the scene exactly as before; a camera moved after approval is refused too;
  a camera marker that makes another camera the scene's after a frame move is
  seen when the bind-time marker refusal is bypassed.
* A legitimate orbit commits, and so does one measured from a camera the
  program deletes afterwards.
* A same-frame write on a keyed channel commits and the result states that its
  key replaces it at the next frame change (``unkeyed_writes``), never for an
  unanimated channel or a key that gives the written value.
* The walk crosses context paths and quoted keys holding ``]``, counts a
  driver that returns to the camera through a pointer, and no longer marks a
  static World or render setting as time.

Run in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-camera-guards-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import camera_guards, native_programs as native, object_dependencies, program_camera  # noqa: E402
from angelo_agent import scene_queries as queries  # noqa: E402
from angelo_agent.action_camera_light_spec import orbit_location  # noqa: E402
from angelo_agent.scene_settings import scene_handle, settings_revision  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string, "check_ms": []}
window = bpy.context.window_manager.windows[0]
NAME = "angelo_scene_program"
ORBIT = {"azimuth": math.pi, "elevation": .3}
STILL = patch.object(object_dependencies, "walk", lambda *args, **kwargs: object_dependencies.Dependencies())


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else queries.object_handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def entry(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}


def scene_entry():
    return {"handle": scene_handle(window.scene), "revision": settings_revision(window.scene)}


def frame(value):
    return {"kind": "property_set", "target": scene_handle(window.scene), "parameters": {"values": {"frame_current": value}}}


def rim(label):
    return {"kind": "light_create", "target": None, "parameters": {"name": "Rim " + label, "look_at": [0, 0, 0],
                                                                    "orbit": dict(ORBIT)}}


def new_scene(label):
    scene = bpy.data.scenes.new("Guards " + label)
    window.scene = scene
    cam = bpy.data.objects.new("Cam " + label, bpy.data.cameras.new("Cam " + label))
    cam.location = (0, -9, 3)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def empty(name, location=(0, 0, 0)):
    obj = bpy.data.objects.new(name, None)
    obj.location = location
    window.scene.collection.objects.link(obj)
    return obj


def state():
    """What a rolled-back commit must leave exactly as it was: every object's transform and world matrix, the
    scene camera, frame and markers, and the lights in Main."""
    scene = window.scene
    return json.dumps({"objects": {obj.name: [[round(v, 6) for v in obj.location], [round(v, 6) for row in
                                   obj.matrix_world for v in row], obj.parent.name if obj.parent else None]
                                   for obj in scene.objects},
                       "camera": scene.camera.name if scene.camera else None, "frame": scene.frame_current,
                       "markers": [(m.name, m.frame, m.camera.name if m.camera else None) for m in scene.timeline_markers],
                       "lights": sorted(light.name for light in bpy.data.lights)}, sort_keys=True)


def settle():
    window.scene.frame_set(window.scene.frame_current)
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="camera guards")
    queries.invalidate()


def orbit_program(build, before_commit=None):
    """Resolve, approve, stage and commit ``build()`` as the controller does; ``(result, resolved, before, after)``."""
    operations, writes = build()
    before = state()
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": writes, "operations": operations})
        arguments = program_camera.host_program(window, arguments, [])
        approved = native.precondition(window, NAME, arguments)
    except ValueError as error:
        return {"status": "refused before approval", "error": str(error)}, None, before, state()
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if before_commit is not None:
        before_commit()
    if staged.get("ready_to_commit"):
        timed = camera_guards.verify

        def measured(check_):
            start = time.perf_counter()
            try:
                return timed(check_)
            finally:
                report["check_ms"].append(round((time.perf_counter() - start) * 1000, 4))
        with patch.object(camera_guards, "verify", measured):
            bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        staged = job.result or staged
    bpy.context.view_layer.update()
    return ({"status": staged.get("status"), "error": str(staged.get("error") or ""),
             "unkeyed_values_replaced": staged.get("unkeyed_values_replaced"),
             "unkeyed_writes": staged.get("unkeyed_writes")}, arguments, before, state())


def refused_unchanged(name, outcome, before, after, text="CAMERA_MOVED"):
    report["cases"][name] = outcome
    check(name, outcome["status"] == "failed" and text in outcome["error"] and before == after)


def commit_check():
    scene, cam = new_scene("legit")
    settle()
    outcome, resolved, _, _ = orbit_program(lambda: ([rim("legit")], []))
    light = window.scene.objects.get("Rim legit")
    expected = orbit_location([0, -9, 3], [0, 0, 0], ORBIT)
    report["cases"]["legit"] = dict(outcome, measured_from=resolved and resolved.get("measured_from"))
    check("a_legitimate_orbit_commits_where_it_was_measured", outcome["status"] == "committed" and light is not None
          and all(abs(a - b) < 1e-4 for a, b in zip(light.location, expected))
          and resolved["measured_from"]["cameras"][0]["location"] == [0.0, -9.0, 3.0])

    scene, cam = new_scene("parent")
    holder = empty("Holder parent")
    cam.parent = holder
    settle()
    with STILL:
        outcome, _, before, after = orbit_program(lambda: (
            [op("transform", holder, location=[4, 0, 0]), rim("parent")], [entry(holder)]))
    refused_unchanged("a_parent_move_the_walk_missed_is_refused_at_commit_and_rolled_back", outcome, before, after)

    scene, cam = new_scene("after approval")
    settle()

    def nudge():
        window.scene.camera.location.x += 2
        bpy.context.view_layer.update()
    outcome, _, before, after = orbit_program(lambda: ([rim("after approval")], []), before_commit=nudge)
    report["cases"]["moved_after_approval"] = outcome
    check("a_camera_moved_after_approval_is_refused", outcome["status"] == "failed" and "CAMERA_MOVED" in outcome["error"]
          and window.scene.objects.get("Rim after approval") is None)

    scene, cam = new_scene("self rotation")
    cam.rotation_euler.z = 0.0
    cam.keyframe_insert("rotation_euler", index=2, frame=1)
    cam.rotation_euler.z = 1.2
    cam.keyframe_insert("rotation_euler", index=2, frame=21)
    curve = cam.driver_add("location", 0)
    variable = curve.driver.variables.new()
    variable.type, variable.targets[0].id, variable.targets[0].data_path = "SINGLE_PROP", cam, "rotation_euler[2]"
    curve.driver.expression = "var * 4"
    window.scene.frame_set(1)
    settle()
    with STILL:
        outcome, _, before, after = orbit_program(lambda: ([frame(21), rim("self rotation")], [scene_entry()]))
    refused_unchanged("a_camera_blender_evaluates_a_step_late_is_read_after_its_frame_update", outcome, before, after)

    scene, cam = new_scene("markers")
    other = bpy.data.objects.new("Other markers", bpy.data.cameras.new("Other markers"))
    window.scene.collection.objects.link(other)
    window.scene.timeline_markers.new("A", frame=1).camera = cam
    window.scene.timeline_markers.new("B", frame=10).camera = other
    window.scene.frame_set(1)
    settle()
    with patch.object(native, "check_frame_move", lambda *args, **kwargs: None):
        outcome, _, before, after = orbit_program(lambda: ([frame(15), rim("markers")], [scene_entry()]))
    refused_unchanged("a_marker_switch_after_a_frame_move_is_seen_at_commit", outcome, before, after,
                      "camera marker switches")


def deleted_cameras():
    def program(holder=None):
        made = op("camera_create", None, name="New " + window.scene.name, alias="new", look_at=[0, 0, 0],
                  orbit=dict(ORBIT))
        switch = op("property_set", scene_handle(window.scene), values={"camera": "$new"})
        old = window.scene.camera
        moves = [op("transform", holder, location=[4, 0, 0])] if holder is not None else []
        writes = [scene_entry(), entry(old)] + ([entry(holder)] if holder is not None else [])
        return moves + [made, switch, op("object_delete", old)], writes
    scene, cam = new_scene("replace")
    settle()
    outcome, resolved, _, _ = orbit_program(program)
    new = window.scene.camera
    report["cases"]["replace_camera"] = dict(outcome, measured_from=resolved and resolved.get("measured_from"))
    check("an_orbit_measured_from_a_camera_the_program_deletes_commits", outcome["status"] == "committed"
          and new is not None and new.name == "New Guards replace"
          and all(abs(a - b) < 1e-4 for a, b in zip(new.location, orbit_location([0, -9, 3], [0, 0, 0], ORBIT))))
    scene, cam = new_scene("replace moved")
    holder = empty("Holder replace")
    cam.parent = holder
    settle()
    with STILL:
        outcome, _, before, after = orbit_program(lambda: program(holder))
    refused_unchanged("a_deleted_camera_is_read_before_it_goes", outcome, before, after)


def walker():
    def reads(cam):
        from angelo_agent import program_orbits
        follows, moves = program_orbits.camera_reads(cam, lambda item: item.name, scene_handle_of=lambda item: item.name)
        return {"follows": sorted(follows) if follows is not None else None, "moves_with_time": moves}

    scene, cam = new_scene("context")
    ctl = empty("Ctl context", (2, 0, 0))
    bpy.context.view_layer.update()
    curve = cam.driver_add("location", 0)
    variable = curve.driver.variables.new()
    variable.type = "CONTEXT_PROP"
    variable.targets[0].context_property, variable.targets[0].data_path = "ACTIVE_SCENE", 'objects["Ctl context"].location[0]'
    curve.driver.expression = "var"
    found = reads(cam)
    report["cases"]["context_path"] = found
    check("a_context_path_reaches_the_object_it_reads", found == {
        "follows": ["Ctl context", "Guards context#objects"], "moves_with_time": False})

    scene, cam = new_scene("bracket")
    holder, ctl = empty("Holder bracket"), empty("Ctl bracket", (2, 0, 0))
    holder["rig]"] = ctl
    curve = cam.driver_add("location", 0)
    variable = curve.driver.variables.new()
    variable.type, variable.targets[0].id, variable.targets[0].data_path = "SINGLE_PROP", holder, '["rig]"].location[0]'
    curve.driver.expression = "var"
    found = reads(cam)
    report["cases"]["bracket_key"] = found
    check("a_quoted_key_holding_a_bracket_is_crossed", found["follows"] == ["Ctl bracket", "Holder bracket"])

    scene, cam = new_scene("self pointer")
    cam.constraints.new("TRACK_TO").target = empty("Subject self pointer")
    cam["self"] = cam
    cam.rotation_euler.z = 0.0
    cam.keyframe_insert("rotation_euler", index=2, frame=1)
    cam.rotation_euler.z = 1.2
    cam.keyframe_insert("rotation_euler", index=2, frame=21)
    curve = cam.driver_add("location", 0)
    variable = curve.driver.variables.new()
    variable.type, variable.targets[0].id, variable.targets[0].data_path = "SINGLE_PROP", cam, '["self"].rotation_euler[2]'
    curve.driver.expression = "var * 4"
    found = reads(cam)
    report["cases"]["self_pointer"] = found
    check("a_driver_returning_to_the_camera_through_a_pointer_reads_its_rotation", found["moves_with_time"])

    scene, cam = new_scene("static settings")
    world = bpy.data.worlds.new("Guards World")
    world.use_nodes = True
    window.scene.world = world
    window.scene.render.resolution_x = 1000
    for index, (kind, ident, path) in enumerate((
            ("WORLD", world, 'node_tree.nodes["Background"].inputs[1].default_value'),
            ("SCENE", window.scene, "render.resolution_x"))):
        curve = cam.driver_add("location", index)
        variable = curve.driver.variables.new()
        variable.type = "SINGLE_PROP"
        variable.targets[0].id_type, variable.targets[0].id, variable.targets[0].data_path = kind, ident, path
        curve.driver.expression = "var / 1000"
    window.scene.frame_set(1)
    settle()
    found = reads(cam)
    outcome, _, _, _ = orbit_program(lambda: ([frame(21), rim("static settings")], [scene_entry()]))
    report["cases"]["static_settings"] = dict(outcome, walk=found)
    check("a_camera_driven_by_a_static_world_and_render_setting_admits_a_frame_move",
          not found["moves_with_time"] and outcome["status"] == "committed")


def eighth_review():
    """Frame-update settings, float32 tolerance far out, and unkeyed animated values a frame update replaces."""
    scene, cam = new_scene("preview range")
    other = bpy.data.objects.new("Other preview range", bpy.data.cameras.new("Other preview range"))
    other.location = (9, 0, 3)
    window.scene.collection.objects.link(other)
    window.scene.timeline_markers.new("A", frame=1).camera = cam
    window.scene.timeline_markers.new("B", frame=30).camera = other
    window.scene.frame_set(1)
    settle()
    preview = op("property_set", scene_handle(window.scene), values={"use_preview_range": True})
    outcome, _, _, _ = orbit_program(lambda: ([preview, rim("preview range")], [scene_entry()]))
    report["cases"]["preview_range_beside_markers"] = outcome
    check("a_frame_update_setting_beside_camera_markers_is_refused_before_approval",
          outcome["status"] == "refused before approval" and "writes use_preview_range" in outcome["error"])

    scene, cam = new_scene("far")
    cam.location = (99990, 0, 3)
    settle()

    def nudge():
        window.scene.camera.location.x += .6
        bpy.context.view_layer.update()
    outcome, _, before, after = orbit_program(lambda: ([rim("far")], []), before_commit=nudge)
    report["cases"]["far_camera_moved_after_approval"] = outcome
    check("a_far_camera_moved_after_approval_is_refused_and_named_as_such", outcome["status"] == "failed"
          and "moved after it was measured" in outcome["error"] and window.scene.objects.get("Rim far") is None)

    scene, cam = new_scene("unkeyed")
    cube = empty("Keyed unkeyed")
    for frame_number, x in ((1, 0.0), (21, 4.0)):
        cube.location.x = x
        cube.keyframe_insert("location", index=0, frame=frame_number)
    window.scene.frame_set(1)
    cube.location.x = 2.5  # The artist's edit, not keyed yet.
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="unkeyed")
    queries.invalidate()
    outcome, _, _, _ = orbit_program(lambda: ([frame(5), rim("unkeyed")], [scene_entry()]))
    report["cases"]["unkeyed_value_beside_a_frame_move"] = outcome
    check("a_committed_frame_update_names_the_unkeyed_values_it_replaced", outcome["status"] == "committed"
          and outcome["unkeyed_values_replaced"]
          and [(item["id"], item["value"], item["now"]) for item in outcome["unkeyed_values_replaced"]["channels"]]
          == [("Keyed unkeyed", 2.5, round(bpy.data.objects["Keyed unkeyed"].location.x, 6))])  # What its keys give.


def unkeyed_writes():
    """A same-frame write the kernel admits on a channel an animation holds commits, and the result says so: for the
    written channel the key replaces at the next frame change, never for an unkeyed one or a key giving that value."""
    def keyed_empty(name, path="location", index=0):
        obj = empty(name)
        for frame_number, value in ((1, 0.0), (21, 4.0)):
            getattr(obj, path)[index] = value
            obj.keyframe_insert(path, index=index, frame=frame_number)
        return obj

    def run_one(label, build):
        scene, cam = new_scene(label)
        target = build()
        window.scene.frame_set(1)
        settle()
        target = bpy.data.objects[target]
        staged, job = None, None
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [entry(target)],
                                         "operations": [op("transform", target, location=list(build.location))]})
        approved = native.precondition(window, NAME, arguments)
        job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
        for _ in range(4096):
            staged = job.poll()
            if staged.get("final") or staged.get("ready_to_commit"):
                break
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        result = job.result or staged
        written = list(target.location)
        window.scene.frame_set(2)
        window.scene.frame_set(1)
        replaced = [index for index in range(3) if abs(target.location[index] - written[index]) > 1e-4]
        fact = (result.get("unkeyed_writes") or {}).get("channels", [])
        report["cases"]["unkeyed_" + label] = {"status": result.get("status"), "fact": fact, "replaced": replaced}
        return result.get("status"), [(item["path"], item["index"]) for item in fact], replaced

    def keyed_x():
        return keyed_empty("Keyed x").name
    keyed_x.location = (2.0, 1.0, 0.0)
    status, fact, replaced = run_one("keyed x", keyed_x)
    check("a_written_keyed_channel_is_stated_as_replaced_at_the_next_frame_change",
          status == "committed" and fact == [("location", 0)] and replaced == [0])

    def same_as_key():
        return keyed_empty("Same as key").name
    same_as_key.location = (0.0, 1.0, 0.0)
    status, fact, replaced = run_one("same as key", same_as_key)
    check("a_write_the_key_already_gives_is_not_stated", status == "committed" and fact == [] and replaced == [])

    def nla_off():  # An NLA track Blender does not evaluate (use_nla off) replaces nothing.
        obj = keyed_empty("NLA off")
        action = obj.animation_data.action
        obj.animation_data.nla_tracks.new().strips.new("Strip", 1, action)
        obj.animation_data.action, obj.animation_data.use_nla = None, False
        return obj.name
    nla_off.location = (2.0, 0.0, 0.0)
    status, fact, replaced = run_one("nla off", nla_off)
    check("an_animation_blender_does_not_evaluate_is_never_stated", status == "committed" and fact == []
          and replaced == [])

    def plain():
        return empty("Plain").name
    plain.location = (2.0, 0.0, 0.0)
    status, fact, replaced = run_one("plain", plain)
    check("an_unanimated_channel_is_never_stated", status == "committed" and fact == [] and replaced == [])


def measurement_effects():
    """Measuring changes nothing a frame change keeps (an unbaked physics cache), nor what an ID holds
    on channels the list could not take (each ID is listed whole or not at all)."""
    import bmesh
    from angelo_agent import animated_values
    scene, cam = new_scene("cache")
    mesh = bpy.data.meshes.new("Cloth")
    shape = bmesh.new()
    bmesh.ops.create_grid(shape, x_segments=8, y_segments=8, size=1.0)
    shape.to_mesh(mesh)
    shape.free()
    cloth = bpy.data.objects.new("Cloth", mesh)
    scene.collection.objects.link(cloth)
    cache = cloth.modifiers.new("Cloth", "CLOTH").point_cache
    cache.frame_start, cache.frame_end, scene.frame_start, scene.frame_end = 1, 30, 1, 30
    for value, key in ((0.0, 1), (2.0, 30)):
        cloth.location.z = value
        cloth.keyframe_insert("location", frame=key)
    for value in range(1, 13):
        scene.frame_set(value)
    settle()
    held = cache.info
    outcome, _, _, _ = orbit_program(lambda: ([frame(5)], [scene_entry()]))
    report["cases"]["cache"] = dict(outcome, before=held, after=cache.info)
    check("measuring_a_frame_program_keeps_an_unbaked_physics_cache", outcome["status"] == "committed"
          and cache.info == held and "outdated" not in cache.info)

    scene, cam = new_scene("partial")
    owners = []
    for name in ("First", "Second"):
        rig = bpy.data.objects.new(name, bpy.data.armatures.new(name))
        scene.collection.objects.link(rig)
        window.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        rig.data.edit_bones.new("Bone").tail = (0, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")
        bone = rig.pose.bones["Bone"]
        for value, key in ((0.0, 1), (1.0, 21)):
            rig.location.x = bone.location.x = value
            rig.keyframe_insert("location", frame=key)
            bone.keyframe_insert("location", frame=key)
        owners.append(rig)
    scene.frame_set(1)
    for rig in owners:
        rig.pose.bones["Bone"].location.x = 0.7  # The artist's unkeyed pose edit; the program writes the transform.
    bpy.context.view_layer.update()  # No frame change (settle's would replace the edits first).
    bpy.ops.ed.undo_push(message="camera guards")
    queries.invalidate()
    with patch.object(animated_values, "MAX_CHANNELS", 3):  # Room for one rig's two paths, not the other's.
        outcome, _, _, _ = orbit_program(lambda: ([op("transform", rig, location=[3.0, 0.0, 0.0]) for rig in owners],
                                                  [entry(rig) for rig in owners]))
    report["cases"]["partial"] = dict(outcome, poses={rig.name: rig.pose.bones["Bone"].location.x for rig in owners})
    check_partial = outcome
    scene, cam = new_scene("strip")  # An NLA strip's animated influence, set without a key: evaluated, so listed.
    obj = empty("Strip")
    for value, key in ((0.0, 1), (2.0, 21)):
        obj.location.z = value
        obj.keyframe_insert("location", frame=key)
    action, obj.animation_data.action = obj.animation_data.action, None
    strip = obj.animation_data.nla_tracks.new().strips.new("Walk", 1, action)
    strip.use_animated_influence = True
    for key in (1, 21):
        strip.fcurves.find("influence").keyframe_points.insert(key, 1.0)
    scene.frame_set(1)
    strip.influence = 0.3
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="camera guards")
    queries.invalidate()
    strip_outcome, _, _, _ = orbit_program(lambda: ([op("transform", obj, location=[3.0, 0.0, 0.0])], [entry(obj)]))
    report["cases"]["strip"] = dict(strip_outcome, influence=strip.influence)
    check("an_nla_strips_unkeyed_influence_is_written_back", strip_outcome["status"] == "committed"
          and abs(strip.influence - 0.3) < 1e-6)
    outcome = check_partial
    check("an_id_the_list_cannot_take_whole_is_not_evaluated_and_the_statement_says_partial",
          outcome["status"] == "committed" and all(abs(rig.pose.bones["Bone"].location.x - 0.7) < 1e-6 for rig in owners)
          and (outcome["unkeyed_writes"] or {}).get("partial") is True)


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes) + list(bpy.data.materials))
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    commit_check()
    deleted_cameras()
    walker()
    eighth_review()
    unkeyed_writes()
    measurement_effects()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report.pop("last_commit", None)
(OUT / "camera-guards.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_CAMERA_GUARDS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
