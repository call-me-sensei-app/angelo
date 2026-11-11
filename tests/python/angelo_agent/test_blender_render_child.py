# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Heavy Cycles render jobs run in a child Angelo, on a copy of the scene (job_render_route, job_render_child).

Found on 2026-10-22 (island 1.0.3 Sol trial 3) and measured for C6 (2026-11-04, long_jobs.rst "Renders in a separate
process"): a GPU render inside the app paces the interface on the GPU it keeps busy, and a stop never frees GPU work
already queued. This suite proves the route end to end, through the real executor and job manager:

* with the route forced (``ANGELO_RENDER_PROCESS=child``) a still renders in a child; the person's settings are back
  while it renders; an edit to the scene meanwhile never reaches the picture (the copy is the scene as the job started); the
  picture is published, and the copy and the staging folder are gone afterwards;
* Cancel ends the child process (its GPU work with it) within the stop bound, publishing nothing;
* the time budget ends the child while the main thread is held up (only the watchdog's thread can);
* the person's own F12 of the same scene meanwhile is Blender's render here, and the agent job's Cancel never stops it;
* an animation stopped part way keeps its whole finished frames, as in the application;
* a heavy GPU still takes, by default, the route its backend was measured for (a child on Metal, the app on OptiX and
  CUDA), and a light still (its work under ``MIN_WORK``) renders in the application.

Renders use the GPU when one is listed, else the CPU (the route is the same); the receipt says which. A window is
required: ``run_native_suites.py --gui --include test_blender_render_child.py``; ``render-child.json`` holds
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import sys
import time
from pathlib import Path

# The route's mechanism is proven on every platform: forced here, and the default route checked by itself below.
os.environ["ANGELO_RENDER_PROCESS"] = "child"

import addon_utils
import bpy

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_jobs_fixtures as fx  # noqa: E402
from production_jobs_harness import MAX_STOP_SECONDS, Harness, ended  # noqa: E402
from angelo_agent import job_render_child, job_render_route, job_spec  # noqa: E402

harness = Harness("ANGELO_RENDER_CHILD", "render-child.json", "/tmp/angelo-render-child-qa", timeout=900)
ctx = harness.ctx
MIN_MINUTES = next(tool for tool in job_spec.JOB_TOOLS if tool["name"] == "angelo_job_start")[
    "inputSchema"]["properties"]["minutes"]["minimum"]
BUDGET_S = 60.0 * MIN_MINUTES
GPU_WAIT_S = 90
HEAVY = {"resolution": [1280, 720], "samples": 8192}   # 7,550 megapixel-samples: past MIN_WORK, far past a budget.
LIGHT = {"resolution": [64, 64], "samples": 16}        # 0.07: in the application.


PICTURE = {"resolution": [640, 360], "samples": 256}   # Seconds on a GPU: finishes while the suite watches.
FRAMES = 12
WALK = {"resolution": [320, 180], "samples": 512}


def emitter(scene, color):
    """A plane filling the camera's view whose emission colour the picture shows, and nothing else: no other object
    or light, a black world and the Standard view, so the picture's mean is that colour (the default view transform
    turned a bright red emitter orange, and the default cube and lamp added white: the suite's first T4 run)."""
    for obj in list(scene.objects):
        if obj.type != "CAMERA":
            bpy.data.objects.remove(obj)
    scene.view_settings.view_transform = "Standard"
    world = scene.world
    background = world.node_tree.nodes.get("Background") if world is not None and world.node_tree else None
    if background is not None:
        background.inputs["Strength"].default_value = 0.0
    bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, -2))
    plane = bpy.context.active_object
    material = bpy.data.materials.new("ChildEmit")
    nodes = material.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Emission Color"].default_value = color
    bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 1.0
    plane.data.materials.append(material)
    return bsdf


def heavy(scene):
    """Make the scene costly to shade (glass and rough metal, 32 bounces), so a HEAVY still lasts far past a cancel
    and a budget on any GPU: one emitting plane at 8192 samples finished before its first poll on an M4 Pro (the
    suite's first Metal run, 2026-11-04), as ``test_blender_render_deadline`` found for its own still."""
    for index, (kind, location) in enumerate((("glass", (-1.5, 0, 1)), ("metal", (1.5, 0, 1)))):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, location=location)
        sphere = bpy.context.active_object
        material = bpy.data.materials.new("ChildHeavy%d" % index)
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if kind == "glass":
            bsdf.inputs["Transmission Weight"].default_value = 1.0
            bsdf.inputs["Roughness"].default_value = 0.05
        else:
            bsdf.inputs["Metallic"].default_value = 1.0
            bsdf.inputs["Roughness"].default_value = 0.4
        sphere.data.materials.append(material)
    scene.cycles.max_bounces = scene.cycles.transmission_bounces = scene.cycles.glossy_bounces = 32


def runner(record):
    return harness.jobs().runners.get(record["id"])


def mean_rgb(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        values = list(image.pixels)
        count = len(values) // 4
        return [sum(values[channel::4]) / count for channel in range(3)]
    finally:
        bpy.data.images.remove(image)


def person_values(scene):
    return (scene.render.resolution_x, scene.cycles.samples, scene.render.filepath)


def hold_until_child_gone(child, cap):
    """Hold the main thread until the child has ended (or ``cap`` seconds): only the watchdog can end it."""
    end = time.monotonic() + cap
    while child.running() and time.monotonic() < end:
        time.sleep(0.05)
    return not child.running()


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        scene.cycles.device = "GPU"
        ctx["bsdf"] = emitter(scene, (1.0, 0.0, 0.0, 1.0))
        ctx["person"] = person_values(scene)
        ctx["gpu_until"] = time.monotonic() + GPU_WAIT_S
        harness.go("gpu")
    elif phase == "gpu":
        from angelo_agent import render_device
        device = (render_device.facts(scene) or {}).get("device", "")
        if not device.startswith("GPU") and time.monotonic() < ctx["gpu_until"]:
            return 0.5
        harness.state["device"] = device
        harness.state["default_route_expected"] = "child" if job_render_route._backend(device) in \
            job_render_route.CHILD_BACKENDS else "app"
        ctx["still"], _ = harness.start_job(dict(PICTURE, kind="RENDER_STILL", output="renders/child.png", minutes=10))
        harness.go("still")
    elif phase == "still":
        record = ctx["still"]
        if record["status"] != "running":
            if ended(record):
                raise AssertionError("the still ended before it ran: %s %s" % (record["status"], record["reason"]))
            return 0.05
        job = runner(record)
        # The route and why (job_render_child.begin's record), so a still kept in the app says what kept it there.
        harness.state["still_route"] = {"process": record["detail"].get("process"),
                                        "why": record["detail"].get("process_why")}
        check("a_heavy_still_renders_in_a_child", record["detail"].get("process") == "child" and job.child is not None)
        check("the_persons_settings_are_back_while_it_renders", person_values(scene) == ctx["person"])
        check("the_interface_is_not_locked_by_it", not harness.jobs().locks_interface())
        if job.child is None:
            raise AssertionError("the heavy still rendered in this application: %s" % harness.state["still_route"])
        ctx["folder"] = job.child.folder
        ctx["bsdf"].inputs["Emission Color"].default_value = (0.0, 1.0, 0.0, 1.0)   # An edit while it renders.
        harness.go("still_end")
    elif phase == "still_end" and ended(ctx["still"]):
        record, published = ctx["still"], harness.workspace / "renders/child.png"
        harness.state["still"] = {"status": record["status"], "reason": record["reason"],
                                  "snapshot_s": record["detail"].get("snapshot_s"),
                                  "peak_memory_mb": record["detail"].get("peak_memory_mb"),
                                  "device": record["detail"].get("device")}
        check("the_still_is_published", record["status"] == "done" and fx.is_png(published, *PICTURE["resolution"]))
        red, green, _blue = mean_rgb(published) if published.exists() else (0, 0, 0)
        harness.state["still"]["mean_rgb"] = [round(red, 3), round(green, 3)]
        check("an_edit_while_it_rendered_never_reached_the_picture", red > 4 * max(green, 1e-3))
        check("its_peak_memory_is_recorded", isinstance(record["detail"].get("peak_memory_mb"), int))
        check("its_copy_is_removed", ctx["folder"] is not None and not Path(ctx["folder"]).exists())
        check("no_hidden_staging_left", harness.hidden_left() == [])
        ctx["bsdf"].inputs["Emission Color"].default_value = (1.0, 0.0, 0.0, 1.0)
        heavy(scene)   # From here every HEAVY still must outlast a cancel and a budget.
        ctx["cancel"], _ = harness.start_job(dict(HEAVY, kind="RENDER_STILL", output="renders/cancel.png", minutes=10))
        harness.go("cancel")
    elif phase == "cancel":
        record = ctx["cancel"]
        if not harness.running_with_progress(record):
            return 0.05
        ctx["child"] = runner(record).child
        harness.cancel(record)
        harness.go("cancel_end")
    elif phase == "cancel_end":
        if ctx["child"].running():
            return 0.02
        harness.stopped("child_process")   # Its process, and so its GPU work and memory, are gone.
        harness.go("cancel_record")
    elif phase == "cancel_record" and ended(ctx["cancel"]):
        record = ctx["cancel"]
        check("cancel_recorded", record["status"] == "cancelled" and record["reason"] == "user")
        check("a_cancelled_still_publishes_nothing", not (harness.workspace / "renders/cancel.png").exists())
        ctx["budget"], _ = harness.start_job(dict(HEAVY, kind="RENDER_STILL", output="renders/budget.png",
                                                  minutes=MIN_MINUTES))
        harness.go("budget")
    elif phase == "budget":
        record = ctx["budget"]
        if record["status"] != "running":
            if ended(record):
                raise AssertionError("the budget still ended first: %s %s" % (record["status"], record["reason"]))
            return 0.05
        child = runner(record).child
        gone = hold_until_child_gone(child, BUDGET_S + MAX_STOP_SECONDS)   # Nothing on the main thread runs.
        check("the_watchdog_ended_it_at_its_budget_while_the_main_thread_was_held",
              gone and child.reason() == "time_budget")
        harness.go("budget_end")
    elif phase == "budget_end" and ended(ctx["budget"]):
        record = ctx["budget"]
        check("budget_stop_recorded", record["status"] == "cancelled" and record["reason"] == "time_budget")
        check("its_scene_deadline_was_never_left_set", not record["detail"].get("deadline_armed"))
        ctx["beside"], _ = harness.start_job(dict(HEAVY, kind="RENDER_STILL", output="renders/beside.png", minutes=10))
        harness.go("beside")
    elif phase == "beside":
        record = ctx["beside"]
        if not harness.running_with_progress(record):
            return 0.05
        with bpy.context.temp_override(window=harness.window()):   # The person's own F12 of the same scene.
            bpy.ops.render.render("INVOKE_DEFAULT")
        ctx["person_at"] = time.monotonic()
        harness.cancel(record)
        check("the_agent_jobs_cancel_never_stops_the_persons_render", bpy.app.is_job_running("RENDER"))
        harness.go("beside_end")
    elif phase == "beside_end":
        if bpy.app.is_job_running("RENDER") or not ended(ctx["beside"]):
            return 0.05
        harness.state["person_render_beside_s"] = round(time.monotonic() - ctx["person_at"], 3)
        check("the_persons_render_ran_to_its_end", bpy.data.images["Render Result"].has_data)
        ctx["animation"], _ = harness.start_job(dict(WALK, kind="RENDER_ANIMATION", output="renders/walk/",
                                                     frame_start=1, frame_end=FRAMES, minutes=10))
        harness.go("animation")
    elif phase == "animation":
        record = ctx["animation"]
        if ended(record):
            raise AssertionError("the animation ended before two frames: %s" % record["status"])
        if record["status"] != "running" or record["detail"].get("frames_done", 0) < 2:
            return 0.05
        check("an_animation_renders_in_a_child", record["detail"].get("process") == "child")
        harness.cancel(record)
        harness.go("animation_end")
    elif phase == "animation_end" and ended(ctx["animation"]):
        record = ctx["animation"]
        kept = sorted((harness.workspace / "renders/walk").glob("*.png"))
        harness.state["animation"] = {"status": record["status"], "kept": len(kept), "receipt": record["receipt"]}
        check("a_stopped_animation_keeps_its_whole_frames", record["status"] == "cancelled" and len(kept) >= 2
              and all(fx.is_png(path, *WALK["resolution"]) for path in kept))
        os.environ.pop("ANGELO_RENDER_PROCESS")   # From here, the route as shipped.
        ctx["default"], _ = harness.start_job(dict(HEAVY, kind="RENDER_STILL", output="renders/default.png",
                                                   minutes=10))
        harness.go("default")
    elif phase == "default":
        record = ctx["default"]
        if record["status"] not in {"running"} and not ended(record):
            return 0.05
        harness.state["default_route"] = {"process": record["detail"].get("process"),
                                          "why": record["detail"].get("process_why")}
        check("a_heavy_gpu_still_takes_the_route_its_backend_was_measured_for",
              record["detail"].get("process") == harness.state["default_route_expected"])
        if not ended(record):
            harness.cancel(record)
        harness.go("default_end")
    elif phase == "default_end" and ended(ctx["default"]):
        ctx["light"], _ = harness.start_job(dict(LIGHT, kind="RENDER_STILL", output="renders/light.png", minutes=10))
        harness.go("light")
    elif phase == "light" and ended(ctx["light"]):
        record = ctx["light"]
        harness.state["light_route"] = {"status": record["status"], "reason": record.get("reason"),
                                        "process": record["detail"].get("process"),
                                        "why": record["detail"].get("process_why"),
                                        "estimate_s": (record["detail"].get("facts") or {}).get("estimate_s")}
        check("a_light_still_renders_in_the_application", record["status"] == "done"
              and record["detail"].get("process") == "app")
        check("no_child_left", not job_render_child._running)
        harness.state["thresholds"] = {"min_estimate_s": job_render_route.MIN_ESTIMATE_S,
                                       "min_work": job_render_route.MIN_WORK}
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0)
