# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Camera/light actions through real staged programs, commit, Undo and revisions.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
Set ANGELO_QA_DIR (report directory) plus isolated BLENDER_USER_CONFIG,
ANGELO_CONNECTIONS_DIR and ANGELO_STATE_DIR. No provider, account, user
project, network request or paid call is involved; the fixture edits only its
factory-startup scene.
"""

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Euler, Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-camera-light-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_validation
from angelo_agent.action_camera_light_spec import aim_error, look_at_euler
from program_qa_helpers import args_for, no_stages, commit_operator

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return "FINISHED" in result and job.result.get("status") == "committed"


def run(program):
    return settle(native.begin(window, "angelo_scene_program", program))


def operation(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def handle(obj):
    return queries.object_handle(obj)


def revision(obj):
    return queries.object_revision(window, obj)


def objects():
    return window.scene.objects


def reset_queries():
    # Hosts call invalidate() after Undo/Redo ID replacement.
    queries.invalidate()


def revision_coverage():
    camera, light = objects()["Camera"], objects()["Light"]
    check("camera_and_light_have_exact_revisions", len(revision(camera)) == 64 and len(revision(light)) == 64)
    for obj, path, value in ((camera, "lens", 85.), (camera, "clip_end", 321.), (camera, "ortho_scale", 3.),
                             (light, "energy", 42.), (light, "color", (1., .5, .25)), (light, "shadow_soft_size", .7)):
        before, light_before = revision(obj), revision(light if obj is camera else camera)
        original = getattr(obj.data, path)
        original = tuple(original) if path == "color" else original
        setattr(obj.data, path, value)  # Deliberately no depsgraph update or invalidation.
        changed = revision(obj)
        other = revision(light if obj is camera else camera)
        setattr(obj.data, path, original)
        check("revision_tracks_" + obj.type.lower() + "_" + path, changed != before and revision(obj) == before)
        check("unrelated_revision_unchanged_" + path, other == light_before)
    before, distance = revision(camera), camera.data.dof.focus_distance
    camera.data.dof.focus_distance = distance + 2.5
    check("revision_tracks_camera_dof", revision(camera) != before)
    camera.data.dof.focus_distance = distance
    emission = next(node for node in light.data.node_tree.nodes if node.bl_idname == "ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 3.
    try:
        revision(light)
    except queries.SceneQueryError as error:
        check("custom_light_graph_has_no_program_revision", "light_node_tree" in str(error))
    else:
        check("custom_light_graph_has_no_program_revision", False)
    emission.inputs["Strength"].default_value = 1.
    detail = queries.execute(window, "angelo_scene_object", {"object": handle(camera), "sections": ["CAMERA_LIGHT"]})
    check("camera_detail_section", detail["sections"]["camera_light"]["settings"]["lens"] == camera.data.lens and detail["revision_exact"])
    token = detail["observation_token"]
    camera.data.lens = 24.
    delta = queries.execute(window, "angelo_scene_delta", {"observation_token": token})
    check("delta_reports_camera_setting_change", any(row["change"] == "MODIFIED" and "camera_light" in row["fields"]
                                                     for row in delta["changes"]) and delta["delta_exact"])
    camera.data.lens = 50.


def aim_parity():
    """The pure solver agrees with mathutils' -Z/Y tracking convention."""
    worst = 0.
    # Vertical views are a documented deterministic fallback, tested on the host.
    for origin, point in (((7, -6, 5), (0, 0, 0)), ((0, 4, 1), (0, -2, 1)), ((-3, -3, -2), (5, 1, 9)), ((1, 2, 3), (1.5, 2, -8))):
        direction = Vector(point) - Vector(origin)
        expected = direction.to_track_quat("-Z", "Y").to_matrix()
        actual = Euler(look_at_euler(origin, point), "XYZ").to_matrix()
        worst = max(worst, max(abs(actual[i][j] - expected[i][j]) for i in range(3) for j in range(3)))
    report["look_at_parity_worst"] = worst  # mathutils stores float32 values.
    check("look_at_matches_mathutils_track_quat", worst < 1e-5)


def create_apply_undo_redo():
    bpy.ops.ed.undo_push(message="Camera/light QA create baseline")
    count = len(objects())
    checks = [{"kind": "camera_settings", "target": "$shot", "values": {"projection": "PERSP", "lens": 40}, "tolerance": 1e-5},
              {"kind": "aim", "target": "$shot", "point": [0, 0, 0], "tolerance": 1e-4},
              {"kind": "light_settings", "target": "$rim", "values": {"light_type": "SPOT", "energy": 250, "spot_size": .5}, "tolerance": 1e-5},
              {"kind": "aim", "target": "$rim", "point": [0, 0, 1], "tolerance": 1e-4}]
    program = args_for(window, [
        operation("camera_create", name="QA Shot", alias="shot", location=[5, -5, 3], look_at=[0, 0, 0], settings={"lens": 40}),
        operation("light_create", name="QA Rim", alias="rim", location=[-3, -3, 4], look_at=[0, 0, 1],
                  settings={"light_type": "SPOT", "energy": 250, "spot_size": .5})], checks=checks)
    job = native.begin(window, "angelo_scene_program", program)
    intent = settle(job)
    check("create_program_ready_without_live_edit", intent.get("ready_to_commit") and len(objects()) == count and no_stages())
    readback = job.operation_results
    check("create_readback_reports_settings_and_aim", readback[0]["camera_settings"]["lens"] == 40 and
          readback[0]["aim_error_radians"] < 1e-5 and readback[1]["light_settings"]["light_type"] == "SPOT")
    check("create_commits_through_undo_operator", commit(job) and no_stages())
    shot, rim = objects()["QA Shot"], objects()["QA Rim"]
    check("created_camera_settings_live", shot.type == "CAMERA" and abs(shot.data.lens - 40) < 1e-5 and shot.data.users == 1)
    check("created_spot_light_live", rim.data.type == "SPOT" and abs(rim.data.energy - 250) < 1e-4 and
          aim_error([list(row) for row in rim.matrix_world], (0, 0, 1)) < 1e-4)
    check("created_objects_have_exact_revisions", len(revision(shot)) == 64 and len(revision(rim)) == 64)
    bpy.ops.ed.undo()
    reset_queries()
    check("one_undo_removes_cameras_and_lights", "QA Shot" not in objects() and "QA Rim" not in objects() and no_stages())
    bpy.ops.ed.redo()
    reset_queries()
    check("one_redo_restores_cameras_and_lights", "QA Shot" in objects() and abs(objects()["QA Rim"].data.energy - 250) < 1e-4)
    bpy.ops.ed.undo()
    reset_queries()


def preview_accept():
    bpy.ops.ed.undo_push(message="Camera/light QA preview baseline")
    program = args_for(window, [operation("light_create", name="QA Preview Sun", alias="sun", location=[0, 0, 6],
                                          look_at=[1, 1, 0], settings={"light_type": "SUN", "energy": 3})],
                       mode="PREVIEW", checks=[{"kind": "light_settings", "target": "$sun", "values": {"energy": 3}, "tolerance": 1e-5}])
    result = run(program)
    added = result.get("delta", {}).get("geometry", [])
    check("preview_retained_without_live_edit", result.get("status") == "preview" and "QA Preview Sun" not in objects() and no_stages())
    check("preview_reports_light_limits_honestly", "lights are not drawn" in result.get("preview_note", "") and
          any(item.get("light", {}).get("energy") == 3 for item in added))
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("accepted_preview_ready", settle(accepted).get("ready_to_commit"))
    check("accepted_preview_commits", commit(accepted) and objects()["QA Preview Sun"].data.type == "SUN")
    bpy.ops.ed.undo()
    reset_queries()
    check("preview_accept_one_undo", "QA Preview Sun" not in objects() and no_stages())


def configure_existing_camera():
    camera = objects()["Camera"]
    lens, data_name = camera.data.lens, camera.data.name
    bpy.ops.ed.undo_push(message="Camera/light QA configure baseline")
    program = args_for(window, [operation("camera_configure", handle(camera), settings={"lens": 85, "clip_end": 300})], writes=[camera],
                       checks=[{"kind": "camera_settings", "target": handle(camera), "values": {"lens": 85, "clip_end": 300}, "tolerance": 1e-5}])
    old = revision(camera)
    job = native.begin(window, "angelo_scene_program", program)
    check("configure_ready_without_live_edit", settle(job).get("ready_to_commit") and camera.data.lens == lens)
    delta = job.delta["details"][0]["changes"]["camera"]
    check("configure_delta_before_after", delta["before"]["lens"] == lens and delta["after"]["lens"] == 85)
    check("configure_commits", commit(job) and abs(camera.data.lens - 85) < 1e-5 and abs(camera.data.clip_end - 300) < 1e-3)
    check("configure_changes_revision", revision(camera) != old)
    bpy.ops.ed.undo()
    reset_queries()
    camera = objects()["Camera"]
    check("configure_one_undo_restores", camera.data.lens == lens and camera.data.name == data_name)
    # look_at alone transfers only the transform: data identity is preserved.
    before_data = camera.data.name
    program = args_for(window, [operation("camera_configure", handle(camera), look_at=[0, 0, 0])], writes=[camera])
    job = native.begin(window, "angelo_scene_program", program)
    settle(job)
    check("look_at_only_keeps_data_identity", commit(job) and camera.data.name == before_data and
          aim_error([list(row) for row in camera.matrix_world], (0, 0, 0)) < 1e-4)
    bpy.ops.ed.undo()
    reset_queries()


def stale_rejections():
    light = objects()["Light"]
    energy = light.data.energy
    observed = revision(light)
    light.data.energy = 123.  # Manual edit after observation.
    program = args_for(window, [operation("light_configure", handle(light), settings={"energy": 5})], writes=[light])
    program["write_set"][0]["revision"] = observed
    result = run(program)
    check("stale_revision_rejected_at_bind", result.get("status") == "failed" and "revision" in result["error"] and
          light.data.energy == 123. and no_stages())
    light.data.energy = energy
    job = native.begin(window, "angelo_scene_program", args_for(window, [
        operation("light_configure", handle(light), settings={"energy": 5})], writes=[light]))
    check("staged_before_manual_edit", settle(job).get("ready_to_commit"))
    light.data.color = (0., 1., 0.)  # Manual edit while the stage awaits commit.
    result = job.commit()
    check("manual_edit_rejects_commit_without_overwrite", result["status"] == "failed" and not result["scene_changed"] and
          tuple(light.data.color) == (0., 1., 0.) and light.data.energy == energy and no_stages())
    light.data.color = (1., 1., 1.)


def verification_tool():
    camera, light = objects()["Camera"], objects()["Light"]
    bound = [{"handle": handle(camera), "revision": revision(camera)}, {"handle": handle(light), "revision": revision(light)}]
    result = scene_validation.execute(window, "angelo_scene_verify", {"read_set": bound, "checks": [
        {"kind": "camera_settings", "target": handle(camera), "values": {"lens": camera.data.lens}, "tolerance": 1e-5},
        {"kind": "camera_settings", "target": handle(camera), "values": {"lens": 12}, "tolerance": 1e-5},
        {"kind": "aim", "target": handle(light), "point": [0, 0, 0], "tolerance": .1}]})
    check("verify_pass_fail_inconclusive", [item["status"] for item in result["checks"]] == ["pass", "fail", "inconclusive"]
          and not result["success"])
    camera.data.keyframe_insert("lens", frame=1)
    animated = scene_validation.execute(window, "angelo_scene_verify", {
        "read_set": [{"handle": handle(camera), "revision": revision(camera)}],
        "checks": [{"kind": "camera_settings", "target": handle(camera), "values": {"lens": camera.data.lens}, "tolerance": 1e-5}]})
    check("animated_camera_data_inconclusive", animated["checks"][0]["status"] == "inconclusive")
    result = run(args_for(window, [operation("camera_configure", handle(camera), settings={"lens": 20})], writes=[camera]))
    check("animated_camera_data_configure_refused", result.get("status") == "failed" and "Animated" in result["error"] and no_stages())
    action = camera.data.animation_data.action
    camera.data.animation_data_clear()
    bpy.data.actions.remove(action)


def shared_light_data():
    light = objects()["Light"]
    fill = bpy.data.objects.new("QA Fill", light.data)
    window.scene.collection.objects.link(fill)
    window.view_layer.update()
    shared, energy = light.data, light.data.energy
    bpy.ops.ed.undo_push(message="Camera/light QA shared baseline")
    job = native.begin(window, "angelo_scene_program", args_for(window, [
        operation("light_configure", handle(light), settings={"energy": 42})], writes=[light]))
    settle(job)
    note = job.delta["details"][0]["changes"].get("shared_data", {})
    check("shared_data_isolation_previewed", note.get("users_before") == 2)
    check("shared_light_commit", commit(job))
    isolated = report["last_commit"].get("isolated_shared_data", [])
    check("shared_light_isolated_per_policy", light.data != fill.data and fill.data == shared and abs(light.data.energy - 42) < 1e-5
          and fill.data.energy == energy and shared.users == 1 and isolated and isolated[0]["data_users_before"] == 2)
    bpy.ops.ed.undo()
    reset_queries()
    light, fill = objects()["Light"], objects()["QA Fill"]
    check("shared_light_undo_restores_sharing", light.data == fill.data and light.data.energy == energy)
    bpy.data.objects.remove(fill, do_unlink=True)


def dependency_and_aim_guards():
    camera, cube = objects()["Camera"], objects()["Cube"]
    camera.data.dof.focus_object = cube
    window.view_layer.update()
    result = run(args_for(window, [operation("camera_configure", handle(camera), settings={"lens": 70})], writes=[camera]))
    check("undeclared_focus_object_rejected", result.get("status") == "failed" and "camera focus" in result["error"])
    bpy.ops.ed.undo_push(message="Camera/light QA focus baseline")
    job = native.begin(window, "angelo_scene_program", args_for(window, [
        operation("camera_configure", handle(camera), settings={"lens": 70})], writes=[camera], reads=[cube]))
    check("declared_focus_object_commits", settle(job).get("ready_to_commit") and commit(job))
    check("focus_object_remapped_to_live_object", camera.data.dof.focus_object == cube and cube.name in objects())
    bpy.ops.ed.undo()
    reset_queries()
    camera, cube = objects()["Camera"], objects()["Cube"]
    camera.data.dof.focus_object = None
    camera.parent = cube
    window.view_layer.update()
    result = run(args_for(window, [operation("camera_configure", handle(camera), look_at=[0, 0, 0])], writes=[camera], reads=[cube]))
    check("parented_look_at_refused", result.get("status") == "failed" and "unparented" in result["error"] and no_stages())
    camera.parent = None
    window.view_layer.update()


def orbit_rig():
    """A key, a fill and a rim written in the scene camera's frame land where the camera sees them (program_orbits).

    Measured as the mini-lighting one-shot measures it (oneshot_mini_checks._check_light_rig): the rim behind the
    cube from the factory camera (oneshot_mini_checks.behind), key and fill not behind, the key the strongest of the
    other two at the cube.
    """
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.program_spec import validate_program
    from angelo_agent.refusal_codes import RefusalError
    from oneshot_mini_checks import behind
    camera, count = window.scene.camera, len(objects())
    area = lambda energy: {"light_type": "AREA", "energy": energy, "size": 2}
    rig = [operation("light_create", name="QA Key", alias="key", look_at=[0, 0, 0],
                     orbit={"azimuth": -math.pi / 4, "elevation": .6}, settings=area(1000)),
           operation("light_create", name="QA Fill", alias="fill", look_at=[0, 0, 0],
                     orbit={"azimuth": math.pi / 3, "elevation": .3}, settings=area(300)),
           operation("light_create", name="QA Rim", alias="rim", look_at=[0, 0, 0],
                     orbit={"azimuth": math.pi, "elevation": .7, "distance": 6}, settings=area(800))]
    executor = BlenderExecutor(window)
    submitted = validate_program(args_for(window, rig))
    try:
        executor.precondition("angelo_scene_program", submitted)
    except RefusalError as error:
        check("unresolved_orbit_refused_at_precondition", error.code == "VALIDATION" and no_stages())
    else:
        check("unresolved_orbit_refused_at_precondition", False)
    result = run(submitted)
    check("unresolved_orbit_refused_by_the_kernel", result.get("status") == "failed" and "unresolved" in result["error"]
          and len(objects()) == count and no_stages())
    resolved = executor.orbit_program(submitted)
    check("orbit_resolves_to_locations", all("orbit" not in op["parameters"] for op in resolved["operations"]))
    bpy.ops.ed.undo_push(message="Camera/light QA orbit baseline")
    executor.precondition("angelo_scene_program", resolved)
    job = native.begin(window, "angelo_scene_program", resolved)
    check("orbit_program_ready", settle(job).get("ready_to_commit") and len(objects()) == count)
    check("orbit_program_commits", commit(job) and no_stages())
    viewer = tuple(camera.matrix_world.translation)
    view = (camera.matrix_world.translation - Vector((0, 0, 0))).normalized()
    report["orbit_camera_cosines"] = {name: objects()[name].matrix_world.translation.normalized().dot(view)
                                      for name in ("QA Key", "QA Fill", "QA Rim")}
    seen_behind = {name: behind(tuple(objects()[name].matrix_world.translation), viewer)
                   for name in ("QA Key", "QA Fill", "QA Rim")}
    report["orbit_behind"] = seen_behind
    check("orbit_rim_behind_the_cube_from_the_camera", seen_behind["QA Rim"])
    check("orbit_key_and_fill_in_front", not seen_behind["QA Key"] and not seen_behind["QA Fill"])
    reach = {name: objects()[name].data.energy / objects()[name].matrix_world.translation.length_squared
             for name in ("QA Key", "QA Fill")}
    check("orbit_key_strongest_at_the_cube", reach["QA Key"] > 1.2 * reach["QA Fill"])
    check("orbit_lights_aim_at_the_cube", all(aim_error([list(row) for row in objects()[name].matrix_world], (0, 0, 0)) < 1e-4
                                              for name in ("QA Key", "QA Fill", "QA Rim")))
    check("orbit_distance_kept", abs(objects()["QA Rim"].matrix_world.translation.length - 6) < 1e-4)
    bpy.ops.ed.undo()
    reset_queries()
    check("orbit_one_undo", "QA Rim" not in objects() and no_stages())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    reset_queries()
    revision_coverage()
    aim_parity()
    create_apply_undo_redo()
    preview_accept()
    configure_existing_camera()
    stale_rejections()
    verification_tool()
    shared_light_data()
    dependency_and_aim_guards()
    orbit_rig()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "camera-light-programs.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_CAMERA_LIGHT", json.dumps({key: report[key] for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
