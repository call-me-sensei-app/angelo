# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""property_set through real staged programs: RNA checks, read-back, commit, Undo and revisions.

Covers object settings, camera and text data, and the bound scene's settings
(render, colour management, frame range, active camera) with the scene handle
and settings revision of ``angelo_scene_settings``. Every program is normalized
by ``tool_specs.validate_call`` as a model's plan is; its public
``expected_checks`` stay authored-only and the job's private obligations carry
the derived ``property`` checks (``program_checks``). Run only in a disposable
instance with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``.
No provider, account, network request or paid call is involved.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-property-set-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_validation  # noqa: E402
from angelo_agent import scene_settings as settings_module  # noqa: E402
from angelo_agent.scene_settings import settings_revision  # noqa: E402
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


def objects():
    return window.scene.objects


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def scene_binding():
    settings = settings_module.execute(window, {})
    return {"handle": settings["handle"], "revision": settings["revision"]}, settings


def program(operations, writes=(), reads=(), mode="APPLY"):
    """A model-style program, normalized by validate_call; the job derives its host checks."""
    return validate_call("angelo_scene_program", {"mode": mode, "read_set": list(reads), "write_set": list(writes),
                                                  "operations": operations})


def property_set(target, **values):
    return {"kind": "property_set", "target": target, "parameters": {"values": values}}


def values_of(target, paths):
    result = {}
    for path in paths:
        holder = target
        *parents, name = path.split(".")
        for parent in parents:
            holder = getattr(holder, parent)
        value = getattr(holder, name)
        result[path] = list(value) if hasattr(value, "__len__") and not isinstance(value, str) else value
    return result


def derived_property_checks(job):
    """The job's host-derived property obligations (never in the public arguments)."""
    return [item for item, derived in zip(job.obligations.checks(), job.obligations.derived_flags)
            if derived and item["kind"] == "property"]


def derived_property_checks_passed(job, arguments):
    rows = [row for row in job.result.get("checks", []) if row.get("kind") == "property"]
    required = [item for item in job.obligations.checks() if item["kind"] == "property"]
    return (arguments["expected_checks"] == [] and bool(derived_property_checks(job)) and len(rows) == len(required)
            and all(row.get("passed") for row in rows))


def object_settings():
    cube = objects()["Cube"]
    paths = ("hide_render", "color", "pass_index", "display_type", "visible_shadow")
    before = values_of(cube, paths)
    bpy.ops.ed.undo_push(message="property_set QA object baseline")
    arguments = program([property_set(handle(cube), hide_render=True, color=[1, .25, 0, 1], pass_index=7,
                                      display_type="WIRE", visible_shadow=False)], writes=[binding(cube)])
    job = native.begin(window, "angelo_scene_program", arguments)
    check("object_program_derives_property_check", arguments["expected_checks"] == []
          and any(item["target"] == handle(cube) and item["values"].get("pass_index") == 7
                  for item in derived_property_checks(job)))
    staged = settle(job)
    check("object_ready_without_live_edit", staged.get("ready_to_commit") and values_of(cube, paths) == before)
    details = job.delta["details"][0]["changes"]["properties"]
    check("object_delta_reports_before_and_after", details["pass_index"] == {"before": before["pass_index"], "after": 7})
    changes = {row["path"]: row for row in job.operation_results[0]["changes"]}
    check("object_receipt_old_and_new_values", changes["hide_render"]["before"] is False and changes["hide_render"]["after"] is True)
    check("object_commits", commit(job) and cube.hide_render and cube.pass_index == 7 and cube.display_type == "WIRE"
          and not cube.visible_shadow and abs(cube.color[1] - .25) < 1e-6)
    check("object_derived_check_passed", derived_property_checks_passed(job, arguments))
    check("object_data_identity_kept", "properties" in job.result["changes"]["modified"][0]["fields"]
          and job.result["changes"]["modified"][0]["properties"] == ["hide_render", "display_type", "color", "pass_index",
                                                                     "visible_shadow"])
    bpy.ops.ed.undo()
    queries.invalidate()
    cube = objects()["Cube"]
    check("object_one_undo_restores", values_of(cube, paths) == before and no_stages())
    bpy.ops.ed.redo()
    queries.invalidate()
    cube = objects()["Cube"]
    check("object_redo_reapplies", cube.hide_render and cube.pass_index == 7)
    bpy.ops.ed.undo()
    queries.invalidate()


def data_settings():
    bpy.ops.object.text_add(location=(4, 0, 0))
    text = bpy.context.object
    text.name = "QA Text"
    bpy.ops.ed.undo_push(message="property_set QA text baseline")
    queries.invalidate()
    arguments = program([property_set(handle(text), **{"data.body": "Angelo", "data.size": 2.5, "data.extrude": .1,
                                                       "data.align_x": "CENTER"})], writes=[binding(text)])
    job = native.begin(window, "angelo_scene_program", arguments)
    settle(job)
    check("text_data_commits", commit(job) and text.data.body == "Angelo" and abs(text.data.size - 2.5) < 1e-6
          and text.data.align_x == "CENTER" and abs(text.data.extrude - .1) < 1e-6)
    check("text_derived_check_passed", derived_property_checks_passed(job, arguments))
    camera = objects()["Camera"]
    lens = camera.data.lens
    arguments = program([property_set(handle(camera), **{"data.dof.use_dof": True, "data.dof.focus_distance": 4.5,
                                                         "data.dof.aperture_fstop": 1.8}),
                         {"kind": "camera_configure", "target": handle(camera), "parameters": {"settings": {"lens": 35}}}],
                        writes=[binding(camera)])
    job = native.begin(window, "angelo_scene_program", arguments)
    settle(job)
    check("camera_dof_and_lens_commit_together", commit(job) and camera.data.dof.use_dof
          and abs(camera.data.dof.focus_distance - 4.5) < 1e-5 and abs(camera.data.lens - 35) < 1e-5)
    bpy.ops.ed.undo()
    queries.invalidate()
    camera = objects()["Camera"]
    check("camera_one_undo_restores", not camera.data.dof.use_dof and camera.data.lens == lens)


def scene_settings():
    bpy.ops.object.camera_add(location=(0, -8, 2))
    second = bpy.context.object
    second.name = "QA Second Camera"
    bpy.ops.ed.undo_push(message="property_set QA scene baseline")
    queries.invalidate()
    scene = window.scene
    paths = ("render.engine", "render.resolution_x", "render.resolution_y", "render.resolution_percentage",
             "render.film_transparent", "view_settings.view_transform", "view_settings.exposure", "frame_start",
             "frame_end", "render.fps", "eevee.taa_render_samples")
    before = values_of(scene, paths)
    original_camera = scene.camera
    camera_name = original_camera.name
    scene_entry, settings = scene_binding()
    check("settings_read_reports_scene_values", settings["values"]["render.resolution_x"] == before["render.resolution_x"]
          and settings["values"]["camera"] == handle(original_camera) and len(settings["revision"]) == 64)
    values = {"render.engine": "BLENDER_WORKBENCH", "render.resolution_x": 1280, "render.resolution_y": 720,
              "render.resolution_percentage": 50, "render.film_transparent": True,
              "view_settings.view_transform": "Standard", "view_settings.exposure": .5, "frame_start": 10,
              "frame_end": 120, "render.fps": 30, "eevee.taa_render_samples": 32, "camera": handle(second)}
    arguments = program([property_set(scene_entry["handle"], **values)], writes=[scene_entry], reads=[binding(second)])
    job = native.begin(window, "angelo_scene_program", arguments)
    staged = settle(job)
    check("scene_ready_without_live_edit", staged.get("ready_to_commit") and values_of(scene, paths) == before
          and scene.camera == original_camera)
    details = next(item for item in job.delta["details"] if item["handle"] == scene_entry["handle"])["changes"]["properties"]
    check("scene_delta_before_after", details["render.resolution_x"] == {"before": before["render.resolution_x"], "after": 1280}
          and details["camera"]["before"] == handle(original_camera) and details["camera"]["after"] == handle(second))
    check("scene_commits", commit(job))
    after = values_of(scene, paths)
    check("scene_values_live", after["render.engine"] == "BLENDER_WORKBENCH" and after["render.resolution_x"] == 1280
          and after["render.resolution_percentage"] == 50 and after["render.film_transparent"]
          and after["view_settings.view_transform"] == "Standard" and abs(after["view_settings.exposure"] - .5) < 1e-6
          and (after["frame_start"], after["frame_end"]) == (10, 120) and after["render.fps"] == 30
          and after["eevee.taa_render_samples"] == 32 and scene.camera == second)
    check("scene_derived_check_passed", derived_property_checks_passed(job, arguments))
    row = next(item for item in job.result["live_revisions"]["objects"] if item.get("type") == "SCENE")
    check("scene_live_revision_is_fresh", row["object_revision"] == settings_revision(scene) and row["handle"] == scene_entry["handle"])
    # Chain on the post-commit revision without another read.
    chained = program([property_set(row["handle"], **{"render.resolution_percentage": 75})],
                      writes=[{"handle": row["handle"], "revision": row["object_revision"]}])
    job = native.begin(window, "angelo_scene_program", chained)
    settle(job)
    check("scene_chained_commit", commit(job) and scene.render.resolution_percentage == 75)
    verify = scene_validation.execute(window, "angelo_scene_verify", {
        "read_set": [scene_binding()[0], binding(second)],
        "checks": [{"kind": "property", "target": scene_entry["handle"],
                    "values": {"render.resolution_percentage": 75, "camera": handle(second), "frame_end": 120}}]})
    check("verify_tool_binds_scene_settings", verify["passed"])
    try:
        scene_validation.execute(window, "angelo_scene_verify", {"read_set": [scene_entry],
                                 "checks": [{"kind": "property", "target": scene_entry["handle"], "values": {"frame_end": 120}}]})
    except ValueError as error:
        check("verify_tool_refuses_stale_scene_revision", "changed" in str(error))
    else:
        check("verify_tool_refuses_stale_scene_revision", False)
    bpy.ops.ed.undo()
    bpy.ops.ed.undo()
    queries.invalidate()
    scene = window.scene
    report["scene_after_undo"] = {"values": values_of(scene, paths), "before": before,
                                  "camera": scene.camera.name if scene.camera else None}
    check("scene_two_undos_restore", values_of(scene, paths) == before and scene.camera.name == camera_name and no_stages())


def admitted_scene_paths(scene):
    """Every scene path rna_rules.resolve admits on this scene, found by walking the instance (not the plan)."""
    from angelo_agent import rna_rules
    from angelo_agent.scene_settings import UNREVISIONED_ROOTS
    found, pending = set(), [(scene, ())]
    while pending:
        holder, segments = pending.pop()
        for prop in holder.bl_rna.properties:
            if prop.identifier == "rna_type":
                continue
            path = segments + (prop.identifier,)
            if not segments and (prop.identifier not in rna_rules.SCENE_ROOTS or prop.identifier in UNREVISIONED_ROOTS):
                continue  # The World is digested whole (world_state); the current frame binds as the program's frame.
            if prop.type == "POINTER" and getattr(prop.fixed_type, "identifier", None) != "Object":
                child = getattr(holder, prop.identifier, None)
                if child is not None and len(path) < rna_rules.MAX_SEGMENTS and not hasattr(child, "session_uid"):
                    pending.append((child, path))
                continue
            try:
                rna_rules.resolve(scene, ".".join(path))
            except rna_rules.PropertyPathError:
                continue
            found.add(".".join(path))
    return found


def generic_scene_paths_are_bound():
    """The settings revision covers every scene path property_set can write.

    It used to digest only the common paths, so a manual change to, say,
    render.border_min_x after observation was silently overwritten.
    """
    from angelo_agent.rna_rules import PROVEN_RUNTIME
    bpy.ops.ed.undo_push(message="property_set QA generic baseline")
    queries.invalidate()
    scene = window.scene
    admitted = admitted_scene_paths(scene)
    generic = settings_module.generic_values(scene)
    covered = set(settings_module.SCENE_PATHS) | set(PROVEN_RUNTIME) | set(generic)
    report["generic_scene_paths"] = {"admitted": len(admitted), "generic": len(generic),
                                     "uncovered": sorted(admitted - covered)[:40],
                                     "not_admitted": sorted(set(generic) - admitted)[:40]}
    check("every_admitted_scene_path_is_in_the_revision", admitted and not admitted - covered)

    def absent_struct(path):  # A settings struct this scene does not have is digested as None, not by its paths.
        holder = scene
        for segment in path.split("."):
            holder = getattr(holder, segment, None)
        return holder is None and generic[path] is None

    check("the_revision_covers_nothing_else_by_path", all(absent_struct(path) for path in set(generic) - admitted))
    # Two candidate values each: the first that differs from the current one is written.
    samples = {"render.border_min_x": (.25, .5), "eevee.gi_diffuse_bounces": (5, 6), "safe_areas.title": ([.2, .2], [.3, .3]),
               "sync_mode": ("AUDIO_SYNC", "NONE"), "use_audio": (False, True), "render.compositor_device": ("CPU", "GPU"),
               "render.line_thickness": (2.0, 3.0), "render.hair_type": ("STRIP", "STRAND")}
    changed = {}
    for path, candidates in samples.items():
        holder = scene
        *parents, name = path.split(".")
        for parent in parents:
            holder = getattr(holder, parent)
        old = getattr(holder, name)
        old = list(old) if hasattr(old, "__len__") and not isinstance(old, str) else old
        value = next(item for item in candidates if not (item == old or isinstance(item, list) and
                                                         all(abs(a - b) < 1e-6 for a, b in zip(item, old))))
        before = settings_revision(scene)
        setattr(holder, name, value)
        changed[path] = settings_revision(scene) != before
        setattr(holder, name, old)
        changed[path] = changed[path] and settings_revision(scene) == before
    report["generic_revision_changes"] = changed
    check("a_generic_setting_changes_the_revision", all(changed.values()))
    # A manual edit after observation: the plan is refused, the edit survives.
    entry, _ = scene_binding()
    scene.render.border_min_x = .3
    job = native.begin(window, "angelo_scene_program", program(
        [property_set(entry["handle"], **{"render.border_min_x": .1})], writes=[entry]))
    result = settle(job)
    check("a_stale_generic_binding_is_refused", result.get("status") == "failed"
          and result.get("error_code") == "STALE_SETTING" and abs(scene.render.border_min_x - .3) < 1e-6 and no_stages())
    entry, _ = scene_binding()
    scene.eevee.gi_diffuse_bounces = 7  # A setting the program does not write still makes it stale.
    job = native.begin(window, "angelo_scene_program", program(
        [property_set(entry["handle"], **{"render.resolution_percentage": 40})], writes=[entry]))
    result = settle(job)
    check("an_untouched_generic_change_is_refused", result.get("status") == "failed"
          and result.get("error_code") == "STALE_SETTING" and scene.render.resolution_percentage != 40)
    import time
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        for _ in range(20):
            settings_module.generic_values(scene)
        timings.append((time.perf_counter() - started) / 20)
    revisions = []
    for _ in range(5):
        started = time.perf_counter()
        for _ in range(20):
            settings_revision(scene)
        revisions.append((time.perf_counter() - started) / 20)
    report["revision_cost_ms"] = {"generic_values_min": min(timings) * 1000, "settings_revision_min": min(revisions) * 1000}
    bpy.ops.ed.undo()
    queries.invalidate()


def preview_accept():
    bpy.ops.ed.undo_push(message="property_set QA preview baseline")
    scene = window.scene
    exposure = scene.view_settings.exposure
    scene_entry, _ = scene_binding()
    arguments = program([property_set(scene_entry["handle"], **{"view_settings.exposure": -1.25, "render.fps": 25})],
                        writes=[scene_entry], mode="PREVIEW")
    result = settle(native.begin(window, "angelo_scene_program", arguments))
    check("scene_preview_keeps_live", result.get("status") == "preview" and scene.view_settings.exposure == exposure and no_stages())
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("scene_preview_accept_ready", settle(accepted).get("ready_to_commit"))
    check("scene_preview_accept_commits", commit(accepted) and abs(scene.view_settings.exposure + 1.25) < 1e-6
          and scene.render.fps == 25)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("scene_preview_one_undo", window.scene.view_settings.exposure == exposure)


def frame_rebind():
    """A frame step after approval rebinds an unchanged object-and-scene program once; real edits fail closed.

    Regression: the precondition gained the scene-settings binding while
    ``rebind`` and ``program_context`` still expected five fields, so no frame
    move was ever rebound (test_blender_program_stress: "The target changed
    after approval" for every frame_step and playback program).
    """
    from angelo_agent.program_context import precondition_change
    from angelo_agent.program_jobs import ProgramError
    name = "angelo_scene_program"
    scene, cube = window.scene, objects()["Cube"]
    start, percentage, pass_index = scene.frame_current, scene.render.resolution_percentage, cube.pass_index
    bpy.ops.ed.undo_push(message="property_set QA frame rebind baseline")

    def approve():
        scene_entry, _ = scene_binding()
        arguments = program([property_set(handle(cube), pass_index=pass_index + 3),
                             property_set(scene_entry["handle"], **{"render.resolution_percentage": 40})],
                            writes=[binding(cube), scene_entry])
        return arguments, native.precondition(window, name, arguments)

    arguments, approved = approve()
    scene.frame_set(start + 1)
    try:
        native.precondition(window, name, arguments)
    except ProgramError:
        pass  # Object revisions carry the frame; the controller then asks for a rebind.
    rebound = native.rebind(window, name, arguments, approved)
    check("frame_step_rebinds_object_and_scene_program", isinstance(rebound, dict) and "arguments" in rebound)
    rebound_scene = next(entry for entry in rebound["arguments"]["write_set"] if entry["handle"] == scene_binding()[0]["handle"])
    check("rebind_keeps_the_frame_free_scene_revision", rebound_scene["revision"] == scene_binding()[0]["revision"]
          and rebound["precondition"] == native.precondition(window, name, rebound["arguments"]))
    job = native.begin(window, name, rebound["arguments"])
    settle(job)
    check("rebound_program_commits", commit(job) and cube.pass_index == pass_index + 3
          and scene.render.resolution_percentage == 40)
    bpy.ops.ed.undo()
    queries.invalidate()
    scene, cube = window.scene, objects()["Cube"]
    check("rebound_commit_one_undo", cube.pass_index == pass_index and scene.render.resolution_percentage == percentage)
    # A creation program (no object inputs, as the stress suite's cube) is a frame-only change.
    create = program([{"kind": "create_primitive", "target": None, "parameters": {
        "primitive": "CUBE", "name": "QA Frame Cube", "size": 2, "location": [0, 0, 0], "alias": "framecube"}}])
    approved = native.precondition(window, name, create)
    scene.frame_set(scene.frame_current + 1)
    change = precondition_change(approved, native.precondition(window, name, create))
    rebound = native.rebind(window, name, create, approved)
    check("frame_step_classified_and_rebound_for_creation", change is not None and change["kind"] == "frame"
          and change["rebindable"] and isinstance(rebound, dict) and "arguments" in rebound)
    # A manual edit between approval and the frame step is never rebound.
    for label, edit, undo in (
            ("scene", lambda: setattr(scene.render, "resolution_x", scene.render.resolution_x + 2),
             lambda: setattr(scene.render, "resolution_x", scene.render.resolution_x - 2)),
            ("object", lambda: setattr(cube.location, "x", cube.location.x + 1),
             lambda: setattr(cube.location, "x", cube.location.x - 1))):
        arguments, approved = approve()
        edit()
        scene.frame_set(scene.frame_current + 1)
        refused = native.rebind(window, name, arguments, approved)
        check("frame_step_after_%s_edit_fails_closed" % label, isinstance(refused, dict) and "refused" in refused
              and "changed too" in refused["refused"])
        undo()
    scene.frame_set(start)
    queries.invalidate()


def cycles_compiled_in():
    """Cycles is an optional build feature: ``blender_lite.cmake`` omits it, ``blender_release.cmake`` has it."""
    return bpy.app.build_options.cycles and hasattr(window.scene, "cycles")


def cycles_engine():
    """Where Cycles is compiled in, its engine and typed settings apply, read back and undo (the refusal is the lite twin)."""
    if not cycles_compiled_in():
        return
    scene = window.scene
    bpy.ops.ed.undo_push(message="property_set QA cycles baseline")
    queries.invalidate()
    before = {"render.engine": scene.render.engine, "cycles.samples": scene.cycles.samples,
              "cycles.use_denoising": scene.cycles.use_denoising}
    scene_entry, _ = scene_binding()
    arguments = program([property_set(scene_entry["handle"], **{"render.engine": "CYCLES", "cycles.samples": 48,
                                                               "cycles.use_denoising": False})], writes=[scene_entry])
    job = native.begin(window, "angelo_scene_program", arguments)
    staged = settle(job)
    check("cycles_ready_without_live_edit", staged.get("ready_to_commit")
          and values_of(scene, tuple(before)) == before)
    check("cycles_commits", commit(job) and scene.render.engine == "CYCLES" and scene.cycles.samples == 48
          and not scene.cycles.use_denoising)
    check("cycles_derived_check_passed", derived_property_checks_passed(job, arguments))
    bpy.ops.ed.undo()
    queries.invalidate()
    check("cycles_one_undo_restores", values_of(window.scene, tuple(before)) == before and no_stages())


def failure(arguments, code, name):
    scene = window.scene
    before = settings_revision(scene)
    cube_before = queries.object_revision(window, objects()["Cube"])
    result = settle(native.begin(window, "angelo_scene_program", arguments))
    check(name, result.get("status") == "failed" and code in result.get("error", "") and no_stages()
          and settings_revision(scene) == before and queries.object_revision(window, objects()["Cube"]) == cube_before)


def rejections():
    scene = window.scene
    cube = objects()["Cube"]
    scene_entry, _ = scene_binding()
    if not cycles_compiled_in():
        failure(program([property_set(scene_entry["handle"], **{"render.engine": "CYCLES"})], writes=[scene_entry]),
                "ENUM_ITEM_UNAVAILABLE", "unavailable_engine_refused")
    failure(program([property_set(scene_entry["handle"], **{"view_settings.look": "Not A Look"})], writes=[scene_entry]),
            "ENUM_ITEM_UNAVAILABLE", "unknown_dynamic_enum_refused")
    failure(program([property_set(handle(cube), **{"render.resolution_x": 640})], writes=[binding(cube)]),
            "OWNER_TYPE_MISMATCH", "scene_path_on_object_refused")
    failure(program([property_set(scene_entry["handle"], hide_render=True)], writes=[scene_entry]),
            "OWNER_TYPE_MISMATCH", "object_path_on_scene_refused")
    failure(program([property_set(handle(cube), **{"data.body": "x"})], writes=[binding(cube)]),
            "PROPERTY_MISSING_IN_BUILD", "text_path_on_mesh_refused")  # A mesh has no text body.
    failure(program([property_set(scene_entry["handle"], camera=handle(cube))], writes=[scene_entry], reads=[binding(cube)]),
            "OBJECT_TYPE_MISMATCH", "non_camera_active_camera_refused")
    for bad, code in (({"render.filepath": "/tmp/out"}, "FILE_PATH"), ({"animation_data.drivers": 1}, "SCRIPT_OR_DRIVER"),
                      ({'["custom"]': 1}, "CUSTOM_PROPERTY_OR_INDEX"), ({"parent": None}, "ID_POINTER")):
        try:
            program([property_set(handle(cube), **bad)], writes=[binding(cube)])
        except ValueError as error:
            check("typed_rejection_" + code.lower(), code in str(error))
        else:
            check("typed_rejection_" + code.lower(), False)
    # A manual change after the read invalidates the bound settings revision.
    stale, _ = scene_binding()
    resolution = scene.render.resolution_x
    scene.render.resolution_x = resolution + 2
    result = settle(native.begin(window, "angelo_scene_program",
                                 program([property_set(stale["handle"], **{"render.resolution_percentage": 30})], writes=[stale])))
    check("stale_scene_revision_fails_closed", result.get("status") == "failed" and "scene settings changed" in result["error"]
          and scene.render.resolution_percentage != 30 and no_stages())
    scene.render.resolution_x = resolution


def current_frame():
    """frame_current is the scene's (rna_rules.SCENE_ROOTS): it commits, moves the frame every revision includes and
    says so, and stays out of the settings revision, so a timeline move remains a frame-only change (program_context)."""
    bpy.ops.ed.undo_push(message="property_set QA current frame baseline")
    queries.invalidate()
    scene = window.scene
    frame = scene.frame_current
    before = settings_revision(scene)
    scene.frame_current = frame + 1
    check("current_frame_outside_the_settings_revision", settings_revision(scene) == before)
    scene.frame_current = frame
    queries.invalidate()
    scene_entry, _settings = scene_binding()
    job = native.begin(window, "angelo_scene_program",
                       program([property_set(scene_entry["handle"], frame_current=frame + 7)], writes=[scene_entry]))
    staged = settle(job)
    check("current_frame_stages_without_live_edit", staged.get("ready_to_commit") and scene.frame_current == frame)
    check("current_frame_commits", commit(job) and scene.frame_current == frame + 7)
    check("current_frame_result_says_earlier_revisions_moved", "frame_moved" in job.result)
    bpy.ops.ed.undo()
    scene = window.scene  # Undo replaced the scene's data.
    scene.frame_current = frame
    queries.invalidate()
    check("current_frame_leaves_no_stage", no_stages())
    # A timeline move after approving a frame write is a changed setting of this program, never a frame-only rebind.
    scene_entry, _settings = scene_binding()
    arguments = program([property_set(scene_entry["handle"], frame_current=frame + 7)], writes=[scene_entry])
    approved = native.precondition(window, "angelo_scene_program", arguments)
    scene.frame_current = frame + 2
    moved = native.rebind(window, "angelo_scene_program", arguments, approved)
    check("current_frame_program_not_rebound_after_a_timeline_move", isinstance(moved, dict)
          and moved.get("error_code") == "STALE_SETTING" and "sets the current frame" in moved.get("refused", ""))
    scene.frame_current = frame
    queries.invalidate()
    # A camera-bound marker would make its camera the scene's on the frame change, after the checks: refused at bind.
    marker = scene.timeline_markers.new("QA Cut", frame=frame + 7)
    marker.camera = scene.camera
    queries.invalidate()
    scene_entry, _settings = scene_binding()
    try:
        native.precondition(window, "angelo_scene_program",
                            program([property_set(scene_entry["handle"], frame_current=frame + 7)], writes=[scene_entry]))
        refused = ""
    except ValueError as error:
        refused = str(error)
    check("current_frame_refused_beside_camera_markers", "FRAME_CAMERA_MARKERS" in refused and "'QA Cut'" in refused
          and scene.frame_current == frame and no_stages())
    scene.timeline_markers.remove(marker)
    # An orbit beside the frame move, measured from a camera whose animation moves it: refused before approval.
    from angelo_agent import program_camera
    camera = scene.camera
    camera.keyframe_insert("location", frame=frame)
    camera.location.x += 3
    camera.keyframe_insert("location", frame=frame + 7)
    scene.frame_set(frame)
    queries.invalidate()
    scene_entry, _settings = scene_binding()
    rim = {"kind": "light_create", "target": None, "parameters": {
        "name": "QA Rim", "look_at": [0, 0, 0], "orbit": {"azimuth": 3.14159, "elevation": .5}}}
    try:
        program_camera.host_program(window, program([property_set(scene_entry["handle"], frame_current=frame + 7), rim],
                                                    writes=[scene_entry]))
        refused = ""
    except ValueError as error:
        refused = str(error)
    check("orbit_beside_a_frame_move_refused_for_an_animated_camera", "moves with time" in refused)
    camera.animation_data_clear()
    scene.frame_set(frame)
    queries.invalidate()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    object_settings()
    data_settings()
    scene_settings()
    generic_scene_paths_are_bound()
    current_frame()
    preview_accept()
    frame_rebind()
    rejections()
    cycles_engine()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "property-set.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_PROPERTY_SET", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
