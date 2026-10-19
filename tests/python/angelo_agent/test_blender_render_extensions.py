# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a render would run and reach, read from the real application, and animated settings held for a job.

Run in an isolated ``--background --factory-startup`` process with the
Angelo add-on enabled (no provider, account or network):

* **No false alarms.** The factory scene with Cycles, EEVEE or Workbench, the
  shipped add-ons and Angelo's own handlers registered, runs no add-on or
  script Python by ``render_extensions``' account, for renders and bakes.
* **Found.** A handler from a module outside the shipped scripts and an
  add-on's ``RenderEngine`` are found and named; a look refuses them, a marked
  host handler does not count, and a bound job's recheck refuses a change.
  Every handler Blender and Angelo register, in every list, is shipped.
* **Drivers whose Python would run.** With the runner's ``--disable-autoexec``
  a ``bpy`` expression on the Cube is really blocked (its value stays 0) and
  does not count, while a function an add-on put in the driver namespace
  really runs and counts. A driver of an object in an unrelated scene does not
  block a look. With auto-execution on, the full expression runs and blocks
  the look; switched off again, it keeps counting until a load, because
  Blender keeps the expression it compiled.
* **Held.** Keyed ``cycles.samples`` (64 at frame 1, 128 at frame 2) and a keyed
  view-layer count are muted by a job's ``Overrides``: evaluating frame 2 keeps
  the job's 8, and a tiny real Cycles render of frame 2 reports 8 samples;
  after the restore the animation runs again. ``bound`` reads 128.
* **Depsgraph handlers** (review P0, mode-aware): an add-on handler on
  ``depsgraph_update_post`` refuses a look in Read-only and Ask, is allowed and
  named in Auto, is listed in a job's approval, and a swap while queued is
  stale; when a view's own write dirties the depsgraph the handler really runs
  and turns the world red, and the check after the view finds it.
* **Message-bus subscribers** (review P1, same rule): the application lists
  the Python subscribers to the settings a look writes, by property, by struct
  and by instance; Angelo's own (the auto-execution preference) is listed but
  never counts, nor does one to a setting the look leaves alone. A look
  refuses them in Read-only and Ask and names them in Auto; a job's report
  lists none.
* **Reached.** A scene strip makes the Sequencer render another scene: a look
  refuses, a job names it, and that scene's File Output node refuses the job.

Run through ``run_native_suites.py --include test_blender_render_extensions``;
check ``render-extensions.json`` for ``success``.
"""

import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-render-extensions-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"checks": {}, "success": False}
ADDON = '''import bpy


class QA_RENDER_ENGINE(bpy.types.RenderEngine):
    bl_idname = "QA_EXTENSION_ENGINE"
    bl_label = "QA Extension Engine"


def on_render(*_args):
    return None


CALLS = []


def driver_call(frame):
    CALLS.append(frame)
    return 3.0


UPDATES = []


def on_update(scene, depsgraph=None):
    UPDATES.append(scene.name)
    if scene.world is not None:
        scene.world.color = (1.0, 0.0, 0.0)  # What the reviewer's add-on did: an unapproved scene change.


def on_change(scene, depsgraph=None):
    return None


def on_message(*_args):
    return None
'''


def check(name, value):
    REPORT["checks"][name] = bool(value)


def refused(function, *args):
    try:
        function(*args)
    except ValueError as error:
        return str(error)
    return None


def every_handler_is_shipped(policy):
    """Blender's own and Angelo's own handlers, in every list, never count (the add-on is enabled)."""
    roots, seen, outside = policy._shipped_roots(), 0, []
    for name in dir(bpy.app.handlers):
        functions = getattr(bpy.app.handlers, name)
        if name.startswith("_") or not isinstance(functions, list):
            continue
        for function in functions:
            seen += 1
            if not policy._shipped(policy._code_file(function), roots):
                outside.append("%s: %s" % (name, policy._label(function)))
    REPORT["factory_handlers"] = {"seen": seen, "outside_shipped_scripts": outside}
    check("factory_handlers_seen", seen > 0)
    check("factory_handlers_all_shipped", outside == [])


def no_false_alarms(policy):
    every_handler_is_shipped(policy)
    scene = bpy.context.scene
    for engine in ("CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        scene.render.engine = engine
        for kind in (policy.RENDER, policy.TEXTURE_BAKE, policy.FRAME_BAKE):
            report = policy.inspect(scene, kind)
            REPORT.setdefault("factory", {})["%s/%s" % (engine, kind)] = report.summary()
            check("factory_%s_%s_runs_no_extension_code" % (engine.lower(), kind.lower()), not report.any)
        check("factory_%s_look_admitted" % engine.lower(), refused(policy.refuse_look, scene) is None)
    scene.render.engine = "CYCLES"
    # Every other view (view_policy): Blender's and Angelo's own handlers and subscribers never count.
    from angelo_agent import view_capture, view_render_restore
    for shading in ("SOLID", "WIREFRAME", "ATTRIBUTE", "MATERIAL", "LIT"):
        check("factory_%s_capture_runs_no_extension_code" % shading.lower(),
              policy.view_report(view_capture.display_keys(shading), True).view_python == [])
    written = view_render_restore.written_keys(scene, view_render_restore.picture_values(scene)[0])
    check("factory_saved_picture_runs_no_extension_code", policy.view_report(written, True).view_python == [])


def found(policy):
    scene = bpy.context.scene
    (OUT / "qa_render_addon.py").write_text(ADDON)
    sys.path.insert(0, str(OUT))
    import qa_render_addon
    bpy.app.handlers.render_pre.append(qa_render_addon.on_render)
    try:
        report = policy.inspect(scene, policy.RENDER)
        check("outside_handler_found", ("render_pre", "qa_render_addon.on_render") in report.handlers)
        message = refused(policy.refuse_look, scene) or ""
        check("look_refuses_and_names_the_approved_route", "qa_render_addon.on_render" in message
              and "angelo_job_start RENDER_STILL" in message)
        runner = SimpleNamespace(scene=scene, request={"kind": "RENDER_STILL"})
        policy.bind(runner)
        check("bound_job_recheck_unchanged", refused(policy.recheck, runner) is None)
        policy.host_handler(qa_render_addon.on_render)
        check("marked_host_handler_not_counted", not policy.inspect(scene, policy.RENDER).handlers)
        check("bound_job_recheck_refuses_a_change", refused(policy.recheck, runner) == policy.CHANGED)
    finally:
        bpy.app.handlers.render_pre.remove(qa_render_addon.on_render)
    drivers(policy, scene, qa_render_addon)
    bpy.utils.register_class(qa_render_addon.QA_RENDER_ENGINE)
    try:
        scene.render.engine = "QA_EXTENSION_ENGINE"
        check("add_on_engine_found",
              policy.inspect(scene, policy.RENDER).engine_label == "QA Extension Engine (qa_render_addon)")
    finally:
        scene.render.engine = "CYCLES"
        bpy.utils.unregister_class(qa_render_addon.QA_RENDER_ENGINE)


def counted(policy, scene):
    return [label for _identity, label in policy.inspect(scene, policy.RENDER).drivers]


def evaluate(scene, frame):
    scene.frame_set(frame)
    return bpy.data.objects["Cube"].location.copy()


def drivers(policy, scene, addon):
    """Which drivers count, against what Blender really runs (the runner starts with --disable-autoexec)."""
    from angelo_agent import render_drivers
    preferences = bpy.context.preferences.filepaths
    cube = bpy.data.objects["Cube"]
    check("runner_starts_in_restricted_mode", not render_drivers.auto_execute() and render_drivers.restricted())
    simple = cube.driver_add("location", 0).driver
    simple.expression = "frame / 10"
    check("simple_expression_not_counted", simple.is_simple_expression and not counted(policy, scene))
    cube.driver_remove("location", 0)
    blocked = cube.driver_add("location", 2).driver
    blocked.expression = "len(bpy.data.objects) * 0 + 7.0"
    check("restricted_mode_really_blocks_a_bpy_expression", evaluate(scene, 2).z == 0.0)
    check("blocked_expression_does_not_count", counted(policy, scene) == [])
    check("look_admitted_with_a_blocked_driver", refused(policy.refuse_look, scene) is None)
    bpy.app.driver_namespace["qa_driver_call"] = addon.driver_call
    called = cube.driver_add("location", 1).driver
    called.expression = "qa_driver_call(frame)"
    check("restricted_mode_really_runs_a_namespace_function",
          evaluate(scene, 3).y == 3.0 and addon.CALLS)
    check("namespace_function_counts_in_restricted_mode", counted(policy, scene) == ["Cube: location[1]"])
    cube.driver_remove("location", 1)
    other = bpy.data.scenes.new("Unrelated")
    elsewhere = bpy.data.objects.new("Elsewhere", None)
    other.collection.objects.link(elsewhere)
    elsewhere.driver_add("location", 1).driver.expression = "qa_driver_call(frame)"
    check("unrelated_scene_driver_does_not_block_a_look", counted(policy, scene) == []
          and refused(policy.refuse_look, scene) is None)
    check("unrelated_scene_driver_counts_for_its_own_scene", counted(policy, other) == ["Elsewhere: location[1]"])
    preferences.use_scripts_auto_execute = True  # Sets the document's flag at once, as the preference does.
    blocked.expression = "len(bpy.data.objects) * 0 + 7.0"  # Compiled again, now in full.
    check("auto_execution_runs_the_full_expression", evaluate(scene, 4).z == 7.0)
    check("driver_on_rendered_object_blocks_a_look", counted(policy, scene) == ["Cube: location[2]"]
          and "Cube: location[2]" in (refused(policy.refuse_look, scene) or ""))
    preferences.use_scripts_auto_execute = False
    check("compiled_in_full_keeps_counting_after_switching_off", not render_drivers.restricted()
          and counted(policy, scene) == ["Cube: location[2]"])
    # What the conservative rule rests on: Blender still runs the expression it compiled in full.
    REPORT["full_expression_after_switching_off"] = evaluate(scene, 5).z
    render_drivers.document_loaded()  # As load_post does: a load compiles every driver again.
    check("a_load_trusts_restricted_mode_again", render_drivers.restricted() and counted(policy, scene) == [])
    cube.driver_remove("location", 2)
    bpy.data.objects.remove(elsewhere)
    bpy.data.scenes.remove(other)
    del bpy.app.driver_namespace["qa_driver_call"]
    scene.frame_set(1)


def held():
    from angelo_agent import job_animated_settings as animated
    from angelo_agent import job_render_samples, job_settings
    from angelo_agent.render_extensions import host_handler
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.use_adaptive_sampling, scene.cycles.use_denoising = "CPU", False, False
    scene.render.resolution_x = scene.render.resolution_y = 16
    scene.render.resolution_percentage = 100
    for frame, count in ((1, 64), (2, 128)):
        scene.cycles.samples = count
        scene.cycles.keyframe_insert("samples", frame=frame)
    layer = scene.view_layers[0]
    layer.samples = 0
    layer.keyframe_insert("samples", frame=1)
    path = layer.path_from_id() + ".samples"
    check("layer_channel_matches_its_job_path", len(animated.channels(scene, [path])) == 1)
    scene.frame_set(1)
    check("bound_reads_the_greatest_keyed_count", animated.bound(scene, "cycles.samples", 64, (1, 2)) == 128)
    window = SimpleNamespace(scene=scene, view_layer=layer)
    overrides = job_settings.Overrides(scene, "qa-held")
    overrides.apply({"cycles.samples": 8, **job_render_samples.settings(window, 8)})
    curves = animated.channels(scene, ["cycles.samples", path])
    check("job_mutes_the_keyed_settings", len(curves) == 2 and all(curve.mute for _where, curve in curves))
    scene.frame_set(2)
    check("evaluating_frame_2_keeps_the_jobs_count", scene.cycles.samples == 8)
    stats = []
    collect = host_handler(lambda *args: stats.extend(str(arg) for arg in args))
    bpy.app.handlers.render_stats.append(collect)
    try:
        bpy.ops.render.render(write_still=False)
    finally:
        bpy.app.handlers.render_stats.remove(collect)
    totals = sorted({int(match.group(1)) for text in stats for match in re.finditer(r"Sample \d+/(\d+)", text)})
    REPORT["frame_2_render_sample_totals"] = totals
    check("frame_2_renders_the_jobs_8_samples", totals == [8])
    check("restore_takes_everything_back", overrides.restore() == [] and job_settings.TAG not in scene
          and not any(curve.mute for _where, curve in curves))
    scene.frame_set(2)
    check("animation_runs_again_after_the_job", scene.cycles.samples == 128)
    scene.animation_data_clear()
    scene.frame_set(1)


def reached():
    from angelo_agent import render_reach
    main, shot = bpy.context.scene, bpy.data.scenes.new("Shot")
    shot.camera = main.camera
    main.render.use_sequencer = True
    main.sequence_editor_create()
    main.sequence_editor.strips.new_scene("Shot strip", shot, 1, 1)
    check("strip_scene_found", [(other.name, camera) for other, camera in render_reach.strip_scenes(main)]
          == [("Shot", True)])
    check("look_refuses_a_strip_scene", "renders other scenes (Shot)" in (refused(render_reach.admit_look, main) or ""))
    check("job_names_the_strip_scene", render_reach.admit_job(main) == ["Shot"])
    tree = bpy.data.node_groups.new("QA Shot Compositor", "CompositorNodeTree")
    tree.nodes.new("CompositorNodeOutputFile")
    shot.compositing_node_group, shot.render.use_compositing = tree, True
    check("strip_scene_file_output_refuses_the_job",
          "Shot, whose compositor has a File Output node" in (refused(render_reach.admit_job, main) or ""))
    main.render.use_sequencer = False
    check("sequencer_off_reaches_nothing", render_reach.admit_job(main) == [])


def depsgraph_handlers(policy):
    """Review P0: a view's own writes dirty the depsgraph, so add-on depsgraph handlers run. Mode-aware rule."""
    from angelo_agent import view_scene_check
    import qa_render_addon
    scene = bpy.context.scene
    world = scene.world
    original = tuple(world.color)
    bpy.context.view_layer.update()  # Nothing left pending before the handler is registered.
    bpy.app.handlers.depsgraph_update_post.append(qa_render_addon.on_update)
    try:
        for mode in ("READ_ONLY", "ASK"):
            message = refused(policy.refuse_look, scene, mode) or ""
            check("look_refused_in_%s" % mode.lower(), "qa_render_addon.on_update (depsgraph_update_post)" in message
                  and ("Read-only" in message) == (mode == "READ_ONLY"))
        report = policy.refuse_look(scene, "AUTO_BOUNDED")
        check("look_allowed_and_named_in_auto", report.depsgraph_handlers == ["qa_render_addon.on_update"])
        check("refusals_ran_no_add_on_python", qa_render_addon.UPDATES == [] and tuple(world.color) == original)
        runner = SimpleNamespace(scene=scene, request={"kind": "RENDER_STILL"})
        policy.bind(runner)
        check("job_approval_lists_it", policy.facts(runner).get("extensions", {}).get("handlers")
              == ["qa_render_addon.on_update"])
        window = SimpleNamespace(scene=scene, view_layer=bpy.context.view_layer)
        scene_check = view_scene_check.SceneCheck(window, deep=True)
        scene.render.resolution_x += 1  # A view's own temporary write: it dirties the depsgraph.
        own = ["render.resolution_x"]
        scene.render.resolution_x -= 1
        found = scene_check.finish(own)
        REPORT["handler_mutation"] = {"updates": list(qa_render_addon.UPDATES), "check": found,
                                      "world_color": list(world.color)}
        check("handler_ran_on_the_views_update", qa_render_addon.UPDATES != [])
        check("handler_mutation_detected", "data-block World" in found["changes"])
        bpy.app.handlers.depsgraph_update_post[:] = [handler for handler in bpy.app.handlers.depsgraph_update_post
                                                     if handler is not qa_render_addon.on_update]
        bpy.app.handlers.depsgraph_update_post.append(qa_render_addon.on_change)
        check("job_swap_while_queued_is_stale", refused(policy.recheck, runner) == policy.CHANGED)
    finally:
        for handler in (qa_render_addon.on_update, qa_render_addon.on_change):
            if handler in bpy.app.handlers.depsgraph_update_post:
                bpy.app.handlers.depsgraph_update_post.remove(handler)
        world.color = original


def message_bus(policy):
    """Review P1, mode-aware: ``bpy.msgbus`` subscribers to a look's own settings count like depsgraph handlers."""
    import qa_render_addon
    from _bpy import _angelo_jobs
    from angelo_agent import view_render_restore
    scene = bpy.context.scene
    listing = getattr(_angelo_jobs, "msgbus_subscribers", None)
    check("msgbus_listing_available", listing is not None)
    if listing is None:
        return
    own = listing([("PreferencesFilePaths", None)])
    REPORT["msgbus_own"] = [[struct, prop, policy._label(function)] for struct, prop, function in own]
    check("msgbus_lists_angelos_own_subscriber", any(prop == "use_scripts_auto_execute" for _s, prop, _f in own))
    check("msgbus_own_subscribers_never_count", policy.subscribers([("PreferencesFilePaths", None)]) == [])
    written = view_render_restore.written_keys(scene, {"render.resolution_x": 64, "render.filepath": "//look.png",
                                                       "render.image_settings.file_format": "PNG"})
    check("look_written_keys", {("RenderSettings", "resolution_x"), ("RenderSettings", "filepath"),
                                ("ImageFormatSettings", "file_format")} <= written)
    owner = object()
    try:
        for key in ((bpy.types.RenderSettings, "resolution_x"), bpy.types.ImageFormatSettings,
                    scene.render.path_resolve("filepath", False), (bpy.types.RenderSettings, "engine")):
            bpy.msgbus.subscribe_rna(key=key, owner=owner, args=(), notify=qa_render_addon.on_message)
        found = policy.subscribers(written)
        REPORT["msgbus_found"] = found
        check("msgbus_property_struct_and_instance_subscribers_found", sorted(found) == sorted(
            "qa_render_addon.on_message (message bus: %s)" % key for key in (
                "RenderSettings.resolution_x", "ImageFormatSettings.*", "RenderSettings.filepath")))
        for mode in ("READ_ONLY", "ASK"):
            message = refused(policy.refuse_look, scene, mode, written) or ""
            check("msgbus_look_refused_in_%s" % mode.lower(), "qa_render_addon.on_message (message bus:" in message
                  and ("Read-only" in message) == (mode == "READ_ONLY"))
        check("msgbus_look_allowed_and_named_in_auto",
              sorted(policy.refuse_look(scene, "AUTO_BOUNDED", written).view_python) == sorted(found))
        check("msgbus_job_report_lists_none", policy.inspect(scene, policy.RENDER).subscribers == [])
    finally:
        bpy.msgbus.clear_by_owner(owner)
    check("msgbus_cleared", policy.subscribers(written) == [])


def main():
    assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background process"
    try:
        from angelo_agent import render_extensions, ui
        ui._authentication.start = lambda *args, **kwargs: None
        ui.refresh_connected_models = lambda *args, **kwargs: None
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
        no_false_alarms(render_extensions)
        found(render_extensions)
        depsgraph_handlers(render_extensions)
        message_bus(render_extensions)
        held()
        reached()
        REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
    except Exception:
        REPORT.update(success=False, error=traceback.format_exc())
    (OUT / "render-extensions.json").write_text(json.dumps(REPORT, indent=2, default=str))
    failed = sorted(name for name, passed in REPORT["checks"].items() if not passed)
    print("ANGELO_RENDER_EXTENSIONS", json.dumps({"success": REPORT["success"], "checks": len(REPORT["checks"]),
                                                  "failed": failed, "error": REPORT.get("error", "")[-600:]}), flush=True)
    assert REPORT["success"], failed or REPORT.get("error")


if __name__ == "__main__":
    main()
