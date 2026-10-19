# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""world_configure through real staged programs: background, environment image, sharing and Undo.

The bound scene's World is configured through the scene handle and settings
revision of ``angelo_scene_settings``. Programs are normalized by
``tool_specs.validate_call`` as a model's plan is; public ``expected_checks``
stay authored-only and each job's private obligations carry the derived
``world_settings`` check (``program_checks``). Run only in a disposable instance with the exact
Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider, account, network request or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-world-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_validation  # noqa: E402
from angelo_agent import scene_settings  # noqa: E402
from angelo_agent.action_world_spec import world_graph  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

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


def settings():
    return scene_settings.execute(window, {})


def configure(entry, mode="APPLY", **parameters):
    return validate_call("angelo_scene_program", {
        "mode": mode, "read_set": [], "write_set": [{"handle": entry["handle"], "revision": entry["revision"]}],
        "operations": [{"kind": "world_configure", "target": entry["handle"], "parameters": parameters}]})


def run(program):
    job = native.begin(window, "angelo_scene_program", program)
    return job, settle(job)


def background(world):
    graph = world_graph(world)
    node = graph["background"]
    return list(node.inputs["Color"].default_value)[:3], node.inputs["Strength"].default_value, graph


def derived_world_checks(job):
    """The job's host-derived World obligations (never in the public arguments)."""
    return [item for item, derived in zip(job.obligations.checks(), job.obligations.derived_flags)
            if derived and item["kind"] == "world_settings"]


def world_checks_passed(job):
    rows = [row for row in job.result.get("checks", []) if row.get("kind") == "world_settings"]
    return bool(rows) and all(row.get("passed") for row in rows)


def colour_and_strength():
    bpy.ops.ed.undo_push(message="world QA baseline")
    queries.invalidate()
    world = window.scene.world
    name, colour, strength = world.name, *background(world)[:2]
    entry = settings()
    check("settings_report_world", entry["world"]["supported_graph"] and abs(entry["world"]["strength"] - strength) < 1e-6)
    program = configure(entry, color=[.1, .2, .3], strength=2.5)
    job, staged = run(program)
    check("world_program_derives_check", program["expected_checks"] == []
          and any(item["values"].get("strength") == 2.5 for item in derived_world_checks(job)))
    check("world_ready_without_live_edit", staged.get("ready_to_commit") and window.scene.world.name == name
          and abs(background(window.scene.world)[1] - strength) < 1e-6)
    delta = next(item for item in job.delta["details"] if item["handle"] == entry["handle"])["changes"]["world"]
    check("world_delta_before_after", abs(delta["before"]["strength"] - strength) < 1e-6 and abs(delta["after"]["strength"] - 2.5) < 1e-6)
    check("world_commits", commit(job) and world_checks_passed(job))
    new_colour, new_strength, _ = background(window.scene.world)
    check("world_values_live", abs(new_strength - 2.5) < 1e-6 and all(abs(a - b) < 1e-6 for a, b in zip(new_colour, [.1, .2, .3]))
          and abs(window.scene.world.color[2] - .3) < 1e-6)
    # The edited copy takes the name (commit adopts it); the replaced World object is untouched.
    check("original_world_untouched", abs(background(world)[1] - strength) < 1e-6 and window.scene.world.name == name
          and window.scene.world != world)
    row = next(item for item in job.result["live_revisions"]["objects"] if item.get("type") == "SCENE")
    check("world_live_revision_is_fresh", row["object_revision"] == settings()["revision"])
    bpy.ops.ed.undo()
    queries.invalidate()
    check("world_one_undo_restores", window.scene.world.name == name and abs(background(window.scene.world)[1] - strength) < 1e-6
          and no_stages())


def environment_image():
    image = bpy.data.images.new("QA HDR", 16, 8, float_buffer=True)
    bpy.ops.ed.undo_push(message="world QA image baseline")
    queries.invalidate()
    image = bpy.data.images["QA HDR"]
    images_before = len(bpy.data.images)
    entry = settings()
    listed = next(item for item in entry["images"] if item["name"] == "QA HDR")
    check("settings_list_images_without_paths", "filepath" not in json.dumps(entry["images"]) and listed["source"] == "GENERATED")
    job, staged = run(configure(entry, mode="PREVIEW", environment=listed["id"], rotation=1.0, strength=1.5))
    check("environment_preview_keeps_live", staged.get("status") == "preview" and world_graph(window.scene.world)["environment"] is None
          and no_stages() and len(bpy.data.images) == images_before)
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": staged["preview_id"]})
    check("environment_accept_ready", settle(accepted).get("ready_to_commit"))
    check("environment_accept_commits", commit(accepted))
    graph = world_graph(window.scene.world)
    check("environment_uses_the_live_image", graph["environment"] is not None and graph["environment"].image == image
          and len(bpy.data.images) == images_before)
    check("environment_rotation_live", abs(graph["mapping"].inputs["Rotation"].default_value[2] - 1.0) < 1e-6)
    verify = scene_validation.execute(window, "angelo_scene_verify", {
        "read_set": [{"handle": settings()["handle"], "revision": settings()["revision"]}],
        "checks": [{"kind": "world_settings", "target": entry["handle"],
                    "values": {"environment": listed["id"], "strength": 1.5, "rotation": 1.0}}]})
    check("verify_tool_reads_world", verify["passed"])
    # Remove the environment and set a plain colour in one operation.
    entry = settings()
    job, _ = run(configure(entry, environment="NONE", color=[.5, .5, .5]))
    check("environment_removed", commit(job) and world_graph(window.scene.world)["environment"] is None
          and abs(background(window.scene.world)[0][0] - .5) < 1e-6 and len(window.scene.world.node_tree.nodes) == 2)
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("environment_two_undos_restore", world_graph(window.scene.world)["environment"] is None and no_stages())


def shared_and_missing_worlds():
    other = bpy.data.scenes.new("QA Other Scene")
    other.world = window.scene.world
    bpy.ops.ed.undo_push(message="world QA shared baseline")
    queries.invalidate()
    shared = window.scene.world
    strength = background(shared)[1]
    job, _ = run(configure(settings(), strength=4.0))
    check("shared_world_isolated", commit(job) and abs(background(window.scene.world)[1] - 4.0) < 1e-6
          and abs(background(bpy.data.scenes["QA Other Scene"].world)[1] - strength) < 1e-6
          and any(item.get("data_users_before", 0) > 1 for item in job.result.get("isolated_shared_data", [])))
    bpy.ops.ed.undo()
    queries.invalidate()
    window.scene.world = None
    bpy.ops.ed.undo_push(message="world QA no world")
    queries.invalidate()
    job, _ = run(configure(settings(), strength=.75, color=[1, .9, .8]))
    check("missing_world_is_created", commit(job) and window.scene.world is not None
          and window.scene.world.name.startswith("Angelo World") and abs(background(window.scene.world)[1] - .75) < 1e-6)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("created_world_undone", window.scene.world is None and no_stages())
    window.scene.world = bpy.data.worlds["World"]


def settings_paths_of_a_shared_world():
    """world.* paths of property_set on a World other scenes use (once written in place, undisclosed).

    The bound scene gets an edited copy, the other scenes keep the original, the
    result reports the isolation and one Undo restores the shared World. A World
    that gains a user after staging refuses the commit before any mutation.
    """
    other = bpy.data.scenes.get("QA Other Scene") or bpy.data.scenes.new("QA Other Scene")
    other.world = window.scene.world
    bpy.ops.ed.undo_push(message="world QA shared settings baseline")
    queries.invalidate()
    shared = window.scene.world
    colour = list(shared.color)
    graph = len(shared.node_tree.nodes) if shared.node_tree else 0

    def by_path(entry, **values):
        return validate_call("angelo_scene_program", {
            "mode": "APPLY", "read_set": [], "write_set": [{"handle": entry["handle"], "revision": entry["revision"]}],
            "operations": [{"kind": "property_set", "target": entry["handle"],
                            "parameters": {"values": {"world." + key: value for key, value in values.items()}}}]})

    job, staged = run(by_path(settings(), color=[1.0, 0.0, 0.0]))
    check("shared_world_path_stages", staged.get("ready_to_commit"))
    committed = commit(job)
    mine, theirs = window.scene.world, bpy.data.scenes["QA Other Scene"].world
    report["shared_world_path"] = {"result": {key: job.result.get(key) for key in ("status", "error", "isolated_shared_data")},
                                   "mine": mine.name, "theirs": theirs.name}
    check("shared_world_path_isolated", committed and mine != theirs and theirs == shared
          and list(mine.color) == [1.0, 0.0, 0.0] and [round(v, 6) for v in theirs.color] == [round(v, 6) for v in colour]
          and (len(mine.node_tree.nodes) if mine.node_tree else 0) == graph
          and any(item.get("data_users_before", 0) > 1 for item in job.result.get("isolated_shared_data", [])))
    check("shared_world_path_check_passed", job.result.get("checks") and all(row.get("passed") for row in job.result["checks"]))
    bpy.ops.ed.undo()
    queries.invalidate()
    check("shared_world_path_undone", window.scene.world == bpy.data.scenes["QA Other Scene"].world
          and [round(v, 6) for v in window.scene.world.color] == [round(v, 6) for v in colour] and no_stages())
    # world_configure and a world.* path in one program: the copy carries both, the original keeps its values.
    entry = settings()
    program = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "write_set": [{"handle": entry["handle"], "revision": entry["revision"]}],
        "operations": [{"kind": "world_configure", "target": entry["handle"], "parameters": {"strength": 2.0}},
                       {"kind": "property_set", "target": entry["handle"], "parameters": {"values": {"world.color": [0.0, 1.0, 0.0]}}}]})
    job, _ = run(program)
    check("configure_and_path_keep_the_shared_original", commit(job)
          and list(window.scene.world.color) == [0.0, 1.0, 0.0]
          and [round(v, 6) for v in bpy.data.scenes["QA Other Scene"].world.color] == [round(v, 6) for v in colour])
    bpy.ops.ed.undo()
    queries.invalidate()
    # Unshared when staged, shared at commit: writing in place would change the other scene.
    bpy.data.scenes["QA Other Scene"].world = None
    queries.invalidate()
    job, staged = run(by_path(settings(), color=[0.0, 0.0, 1.0]))
    check("unshared_world_path_stages", staged.get("ready_to_commit"))
    bpy.data.scenes["QA Other Scene"].world = window.scene.world
    bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report["world_gained_user"] = {key: job.result.get(key) for key in ("status", "error", "scene_changed")}
    check("a_world_that_gained_a_user_is_refused", job.result.get("status") == "failed"
          and "gained other users" in job.result.get("error", "") and not job.result.get("scene_changed")
          and [round(v, 6) for v in window.scene.world.color] == [round(v, 6) for v in colour] and no_stages())
    queries.invalidate()


def rotation_without_environment():
    """A rotation has nothing to turn on a plain-colour World: it is ignored when there is more to do."""
    bpy.ops.ed.undo_push(message="world QA rotation baseline")
    queries.invalidate()
    # The recorded plan: colour, strength, no environment, and the rotation a field-filling model added.
    program = configure(settings(), color=[.48, .32, .2], strength=.35, environment="NONE", rotation=0.0)
    operation = program["operations"][0]
    job, _ = run(program)
    check("rotation_beside_removed_environment_is_dropped", "rotation" not in operation["parameters"]
          and len(derived_world_checks(job)) == 1 and "rotation" not in derived_world_checks(job)[0]["values"])
    check("golden_hour_world_commits", commit(job) and world_checks_passed(job)
          and abs(background(window.scene.world)[1] - .35) < 1e-6 and world_graph(window.scene.world)["environment"] is None)
    shown = json.dumps(job.result.get("operation_results") or job.delta)  # What the World shows (world_surface).
    check("world_result_says_what_it_shows", '"shows": "COLOR"' in shown and '"family": "warm"' in shown)
    # No environment field at all: the staged World decides, and the rest of the request still lands.
    program = configure(settings(), strength=1.25, rotation=.5)
    job, _ = run(program)
    check("rotation_is_kept_until_the_world_is_known", program["operations"][0]["parameters"]["rotation"] == .5
          and len(derived_world_checks(job)) == 1 and derived_world_checks(job)[0]["values"]["rotation"] == .5)
    check("rotation_ignored_without_environment", commit(job) and world_checks_passed(job)
          and abs(background(window.scene.world)[1] - 1.25) < 1e-6)
    check("ignored_rotation_is_reported", "rotation_ignored" in json.dumps(job.result.get("operation_results")
                                                                            or job.delta))
    # A rotation and nothing else is not reported as a finished edit.
    failure(configure(settings(), rotation=.5), "NO_ENVIRONMENT", "rotation_alone_is_refused")
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    check("rotation_programs_undo", no_stages())


def implicit_scene():
    """The turn context states the scene's handle and settings revision; a null World target is that scene."""
    from angelo_agent import context_ledger, turn_context
    from angelo_agent.identifiers import IdentifierTable, expand_call
    bpy.ops.ed.undo_push(message="world QA implicit scene baseline")
    queries.invalidate()
    observation = turn_context.prepare(window, None).observation
    entry = settings()
    stated = observation["scene_settings"]
    check("turn_context_states_the_scene", {key: stated[key] for key in ("handle", "revision")} == {
        "handle": entry["handle"], "revision": entry["revision"]})
    # The scene camera: where the person's render looks from, and along which direction.
    camera = window.scene.camera
    forward = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    check("turn_context_states_the_scene_camera", stated["camera"]["handle"] == queries.object_handle(camera)
          and max(abs(a - b) for a, b in zip(stated["camera"]["location"], camera.matrix_world.translation)) < 1e-5
          and max(abs(a - b) for a, b in zip(stated["camera"]["direction"], forward)) < 1e-3)
    table = IdentifierTable()
    shown = context_ledger.compact_observation(observation, table)
    check("model_reads_short_scene_forms", shown["scene_settings"]["handle"].startswith("o")
          and len(shown["scene_settings"]["revision"]) == 8)
    plan = {"mode": "APPLY", "operations": [
        {"kind": "world_configure", "target": None, "parameters": {"color": [.2, .3, .4], "strength": 1.5}},
        {"kind": "property_set", "target": shown["scene_settings"]["handle"],
         "parameters": {"values": {"frame_end": 120}}}]}
    program = validate_call("angelo_scene_program", expand_call("angelo_scene_program", plan, table))
    check("scene_bound_without_a_settings_read", program["write_set"] == [{"handle": entry["handle"],
                                                                         "revision": entry["revision"]}]
          and program["operations"][0]["target"] == entry["handle"])
    job, _ = run(program)
    check("implicit_scene_program_commits", commit(job) and abs(background(window.scene.world)[1] - 1.5) < 1e-6
          and window.scene.frame_end == 120)
    # A setting changed since the model's view fails closed, exactly as an explicit stale revision does.
    stale = validate_call("angelo_scene_program", expand_call("angelo_scene_program", plan, table))
    job, result = run(stale)
    check("stale_inferred_scene_binding_fails_closed", result.get("status") == "failed"
          and "scene settings changed" in result["error"] and no_stages())
    # An object where the scene belongs is refused with what to pass.
    cube = window.scene.objects["Cube"]
    wrong = validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [
        {"handle": queries.object_handle(cube), "revision": queries.object_revision(window, cube)}], "operations": [
        {"kind": "world_configure", "target": queries.object_handle(cube), "parameters": {"strength": 2.0}}]})
    job, result = run(wrong)
    check("object_as_world_owner_names_the_scene", result.get("status") == "failed"
          and "OWNER_TYPE_MISMATCH" in result["error"] and "scene_settings.handle" in result["error"] and no_stages())
    bpy.ops.ed.undo()
    queries.invalidate()
    check("implicit_scene_one_undo", window.scene.frame_end != 120 and no_stages())


def everyday_scene_requests():
    """Two one-message requests as a model writes them from the turn context alone: no lookup, no scene handle."""
    from angelo_agent import context_ledger, turn_context
    from angelo_agent.identifiers import IdentifierTable, expand_call

    def written(operations):
        table = IdentifierTable()
        shown = context_ledger.compact_observation(turn_context.prepare(window, None).observation, table)
        short = {row["name"]: row["handle"] for row in shown["objects"]}
        plan = {"mode": "APPLY", "operations": operations(short)}
        return validate_call("angelo_scene_program", expand_call("angelo_scene_program", plan, table)), table, short

    # "Add a camera at (6, -8, 4) aimed at the cube with a 50 mm lens, make it the scene camera, delete the old one."
    bpy.ops.ed.undo_push(message="world QA camera request baseline")
    queries.invalidate()
    program, table, short = written(lambda short: [
        {"kind": "camera_create", "parameters": {"name": "View Camera", "alias": "cam", "location": [6, -8, 4],
                                                 "look_at": [0, 0, 0], "settings": {"lens": 50}}},
        {"kind": "property_set", "parameters": {"values": {"camera": "$cam"}}},
        {"kind": "object_delete", "target": short["Camera"], "parameters": {}}])
    job, _ = run(program)
    committed = commit(job)
    cameras = [obj for obj in window.scene.objects if obj.type == "CAMERA"]
    check("camera_request_commits_without_a_lookup", committed and [obj.name for obj in cameras] == ["View Camera"]
          and window.scene.camera == cameras[0] and abs(cameras[0].data.lens - 50) < 1e-6
          and all(row.get("passed") for row in job.result["checks"]) and len(job.result["checks"]) >= 4)
    # The model put the new camera where the scene belongs: answered before anything is staged, with what to write.
    for target in ("$cam", short["Cube"]):
        try:
            expanded = expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [
                {"kind": "camera_create", "parameters": {"alias": "cam"}},
                {"kind": "property_set", "target": target, "parameters": {"values": {"camera": "$cam"}}}]}, table)
            validate_call("angelo_scene_program", expanded)
            error = ""
        except ValueError as refusal:
            error = str(refusal)
        check("scene_setting_on_an_object_names_the_remedy_" + target.strip("$"),
              "OWNER_TYPE_MISMATCH" in error and "leave target out" in error and no_stages())
    bpy.ops.ed.undo()
    queries.invalidate()
    check("camera_request_one_undo", window.scene.camera.name == "Camera" and "View Camera" not in bpy.data.objects)

    # "Light the cube with a key, a fill and a rim area light, ... and switch to Cycles at 128 samples with denoising."
    bpy.ops.ed.undo_push(message="world QA lighting request baseline")
    queries.invalidate()

    def light(name, location, energy, size):
        return {"kind": "light_create", "parameters": {"name": name, "location": location, "look_at": [0, 0, 0],
                                                       "settings": {"light_type": "AREA", "energy": energy, "size": size}}}
    program, _, _ = written(lambda short: [
        light("Key", [4, -4, 5], 1000, 2), light("Fill", [-2, -6, 2.5], 250, 3), light("Rim", [-5, 5, 3.5], 600, 1.5),
        {"kind": "world_configure", "parameters": {"color": [.35, .5, .8], "strength": 1}},
        {"kind": "property_set", "parameters": {"values": {"render.engine": "CYCLES", "cycles.samples": 128,
                                                            "cycles.use_denoising": True}}}])
    job, _ = run(program)
    committed = commit(job)
    areas = [obj for obj in window.scene.objects if obj.type == "LIGHT" and obj.data.type == "AREA"]
    check("lights_world_and_render_settings_in_one_program", committed and len(areas) == 3
          and window.scene.render.engine == "CYCLES" and window.scene.cycles.samples == 128
          and window.scene.cycles.use_denoising and abs(background(window.scene.world)[1] - 1) < 1e-6
          and all(row.get("passed") for row in job.result["checks"]))
    bpy.ops.ed.undo()
    queries.invalidate()
    check("lighting_request_one_undo", not [obj for obj in window.scene.objects if obj.type == "LIGHT"
                                           and obj.data.type == "AREA"] and window.scene.render.engine != "CYCLES")


def after_a_script():
    """A script changed the World and made an object: the program that follows needs no lookup and is not stale."""
    from angelo_agent import blender_tools, context_ledger, script_after, turn_context
    from angelo_agent.identifiers import IdentifierTable, expand_call
    bpy.ops.ed.undo_push(message="world QA after-script baseline")
    queries.invalidate()
    scene = window.scene
    table, stale_table = IdentifierTable(), IdentifierTable()
    observation = turn_context.prepare(window, None).observation
    context_ledger.compact_observation(observation, table)
    context_ledger.compact_observation(observation, stale_table)
    observed = observation["scene_settings"]["revision"]
    # The script's effect, applied as the executor runs one: a new World on the scene and a generated object.
    before = blender_tools.scene_signature(scene)
    scene.world = bpy.data.worlds.new("QA Script Sky")
    arch = bpy.data.objects.new("QA Script Arch", bpy.data.meshes.new("QA Script Arch"))
    arch.data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    scene.collection.objects.link(arch)
    window.view_layer.update()
    result = {"success": True, "result": None, "output": "",
              "changes": blender_tools.scene_diff(before, blender_tools.scene_signature(scene))}
    script_after.attach(window, result)
    after = result.get("after") or {}
    rows = after.get("objects") or []
    check("script_result_states_the_scene_as_it_is_now", after.get("scene_settings", {}).get("revision")
          == scene_settings.settings_revision(scene) != observed)
    check("script_result_names_what_it_made_by_handle_and_revision", [row["name"] for row in rows] == ["QA Script Arch"]
          and rows[0]["handle"] == queries.object_handle(arch)
          and rows[0]["object_revision"] == queries.object_revision(window, arch))
    shown = table.compact(result)
    context_ledger.observe_committed(table, result)
    short = shown["after"]["objects"][0]["handle"]
    check("model_reads_short_forms_in_the_script_result", short.startswith("o") and len(short) < 6
          and len(shown["after"]["objects"][0]["object_revision"]) == 8
          and len(json.dumps(shown["after"], separators=(",", ":"))) < 400)

    def plan(target):
        return {"mode": "APPLY", "operations": [
            {"kind": "transform", "target": target, "parameters": {"location": [0, 0, 2]}},
            {"kind": "property_set", "parameters": {"values": {"frame_end": 90}}}]}
    # Without the read-back the host still holds the settings revision from before the script.
    try:
        stale = validate_call("angelo_scene_program", expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [
            plan(None)["operations"][1]]}, stale_table))
        job, refused = run(stale)
        error = refused.get("error", "")
    except ValueError as refusal:
        error = str(refusal)
    check("without_the_read_back_the_scene_binding_is_stale", "scene settings changed" in error and no_stages())
    program = validate_call("angelo_scene_program", expand_call("angelo_scene_program", plan(short), table))
    job, _ = run(program)
    check("program_after_a_script_commits_without_a_lookup", commit(job) and scene.frame_end == 90
          and abs(arch.location.z - 2) < 1e-6 and all(row.get("passed") for row in job.result["checks"]))
    # A script that failed, or a result without the executor's read-back, gets nothing.
    for broken in ({"success": False, "error": "boom", "changes": result["changes"]}, {"success": True}):
        check("no_read_back_for_%s" % ("a_failed_script" if "error" in broken else "a_result_without_changes"),
              "after" not in script_after.attach(window, dict(broken)))
    bpy.ops.ed.undo()
    queries.invalidate()


def failure(program, text, name):
    revision = settings()["revision"]
    job, result = run(program)
    check(name, result.get("status") == "failed" and text in result.get("error", "") and no_stages()
          and settings()["revision"] == revision)


def rejections():
    queries.invalidate()
    world = window.scene.world
    tree = world.node_tree
    rgb = tree.nodes.new("ShaderNodeRGB")
    link = tree.links.new(rgb.outputs["Color"], world_graph_background(tree).inputs["Color"])
    failure(configure(settings(), strength=3.0), "CUSTOM_WORLD_GRAPH", "custom_world_graph_refused")
    tree.links.remove(link)
    tree.nodes.remove(rgb)
    entry = settings()
    stale = dict(entry)
    background(world)[2]["background"].inputs["Strength"].default_value += 1.0
    job, result = run(configure(stale, strength=9.0))
    check("stale_world_fails_closed", result.get("status") == "failed" and "scene settings changed" in result["error"]
          and background(world)[1] != 9.0 and no_stages())
    entry = settings()
    image_id = next(item["id"] for item in entry["images"] if item["name"] == "QA HDR")
    epoch, uid = image_id.split(":")[1], image_id.split(":")[3]
    expired = image_id.replace(epoch, "0" * 32)
    failure(configure(entry, environment=expired), "IMAGE_EXPIRED", "expired_image_id_refused")
    missing = image_id.replace(":" + uid, ":999999")
    failure(configure(settings(), environment=missing), "IMAGE_NOT_FOUND", "missing_image_refused")
    configure(settings(), color=[.1, .1, .1], environment=image_id)  # Admitted: the colour is reported unused.
    for parameters, code in (({"environment": "/tmp/sky.exr"}, ""),):
        try:
            configure(settings(), **parameters)
        except ValueError as error:
            check("static_rejection_" + (code or "file_path"), code in str(error))
        else:
            check("static_rejection_" + (code or "file_path"), False)


def world_graph_background(tree):
    return next(node for node in tree.nodes if node.type == "BACKGROUND")


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    colour_and_strength()
    environment_image()
    shared_and_missing_worlds()
    settings_paths_of_a_shared_world()
    rotation_without_environment()
    implicit_scene()
    everyday_scene_requests()
    after_a_script()
    rejections()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "world.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_WORLD", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
