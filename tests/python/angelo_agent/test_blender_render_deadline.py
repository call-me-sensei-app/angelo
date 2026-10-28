# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A job's time budget is enforced by the job's own break check, on a GPU render and a bake, and by nothing else.

Found on 2026-10-22 (island 1.0.3 Sol trial 3): a GPU render held its budget for hours because only the main thread
checked it, and the main thread was held up. The budget is now also a deadline that belongs to one job (its kind and
scene) and that the job's own break check reads (``BKE_angelo_job_deadline.hh``, ``job_deadline``):

* a GPU still whose budget runs out while the main thread is held up (this suite blocks it) is stopped by its own break
  check: ``deadline_fired`` reads true before the main thread runs again, the job is cancelled with reason
  ``time_budget``, nothing is published and the person's settings come back;
* the deadline goes with its job: the person's own render of the same scene afterwards runs to its end;
* another scene's deadline, already past, never stops this scene's render, and is not recorded as fired;
* a texture bake whose budget runs out while the main thread is held up is stopped by its own break check, and its
  image is put back.

Renders run on the GPU the app chose (Metal on macOS, OptiX or CUDA on Windows and Linux); the suite fails when no GPU
is listed. A window is required: run through ``run_native_suites.py --gui --include test_blender_render_deadline.py``;
check ``render-deadline.json`` for ``success`` and ``phase == "complete"``.
"""

import sys
import time
from pathlib import Path

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
from production_jobs_harness import MAX_STOP_SECONDS, Harness, ended, job_seconds  # noqa: E402
from _bpy import _angelo_jobs  # noqa: E402
from angelo_agent import job_spec  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

harness = Harness("ANGELO_RENDER_DEADLINE", "render-deadline.json", "/tmp/angelo-render-deadline-qa", timeout=600)
ctx = harness.ctx
# The job's budget and deadline: the shortest one angelo_job_start accepts, read from its schema (a shorter one is refused
# before the job starts, as 0.05 minutes was on the suite's first GUI run).
MIN_MINUTES = next(tool for tool in job_spec.JOB_TOOLS if tool["name"] == "angelo_job_start")[
    "inputSchema"]["properties"]["minutes"]["minimum"]
BUDGET_S = 60.0 * MIN_MINUTES
HOLD_S = BUDGET_S + 4   # The main thread is held this long from the job's start: past the deadline, so only the job's
                        # own break check can stop it while the manager's timer cannot run.
GPU_WAIT_S = 90         # The app lists GPUs as it starts; a fresh profile's first listing.
# The person's own render: seconds long on a GPU once warm, so a render stopped at its first break check (a fraction of a
# second) is far shorter than a whole one. Measured warm: the first render of a session also loads kernels and builds
# the scene (6.7 s, then 1.1 s, then 0.45 s for 256 samples on the suite's runs of 2026-10-24), so it is not the reference.
PERSON = {"resolution": (320, 180), "samples": 2048}


def gpu_device(scene):
    from angelo_agent import render_device
    found = render_device.facts(scene) or {}
    return found.get("device", ""), found.get("device_why", "")


def heavy(scene):
    """A GPU still that lasts far past its budget: glass and a rough metal, no adaptive stop, no denoising."""
    for index, (kind, location) in enumerate((("glass", (-1.5, 0, 1)), ("metal", (1.5, 0, 1)))):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, location=location)
        sphere = bpy.context.active_object
        material = bpy.data.materials.new("Deadline%d" % index)
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if kind == "glass":
            bsdf.inputs["Transmission Weight"].default_value = 1.0
            bsdf.inputs["Roughness"].default_value = 0.05
        else:
            bsdf.inputs["Metallic"].default_value = 1.0
            bsdf.inputs["Roughness"].default_value = 0.4
        sphere.data.materials.append(material)
    scene.cycles.max_bounces = 32
    scene.cycles.transmission_bounces = 32


def person_render(scene):
    """The person's own F12 of this scene (Blender's render job, not an Angelo job); its start time."""
    scene.render.resolution_x, scene.render.resolution_y = PERSON["resolution"]
    scene.cycles.samples = PERSON["samples"]
    with bpy.context.temp_override(window=harness.window()):
        bpy.ops.render.render("INVOKE_DEFAULT")
    return time.monotonic()


def restore_person(scene):
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.cycles.samples = 16


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        scene.cycles.device = 'GPU'
        heavy(scene)
        ctx["gpu_wait_until"] = time.monotonic() + GPU_WAIT_S
        harness.go("gpu")
    elif phase == "gpu":
        device, why = gpu_device(scene)
        if not device.startswith("GPU") and time.monotonic() < ctx["gpu_wait_until"]:
            return 0.5
        harness.state["device"], harness.state["device_why"] = device, why
        check("a_gpu_is_listed", device.startswith("GPU"))
        if not device.startswith("GPU"):
            harness.finish()
            return None
        person_render(scene)   # Untimed: the session's first render also loads kernels and builds the scene.
        harness.go("warmup")
    elif phase == "warmup":
        if bpy.app.is_job_running("RENDER"):
            return 0.05
        ctx["reference_at"] = person_render(scene)   # The person's render, warm, with no deadline anywhere: its length.
        harness.go("reference")
    elif phase == "reference":
        if bpy.app.is_job_running("RENDER"):
            return 0.05
        ctx["reference_s"] = time.monotonic() - ctx["reference_at"]
        harness.state["person_render_s"] = {"reference": round(ctx["reference_s"], 3)}
        restore_person(scene)
        ctx["still"], _ = harness.start_job({"kind": "RENDER_STILL", "output": "renders/deadline.png",
                                             "resolution": [960, 540], "samples": 8192, "minutes": MIN_MINUTES})
        harness.go("hold")
    elif phase == "hold":
        record = ctx["still"]
        if record["status"] != "running":
            if ended(record):
                raise AssertionError("the still ended before its budget: %s %s" % (record["status"], record["reason"]))
            return 0.05
        time.sleep(max(0.0, record["started"] + HOLD_S - time.time()))   # Hold the main thread past the deadline.
        harness.state["fired_while_main_thread_held"] = fired = bool(_angelo_jobs.deadline_fired())
        check("the_jobs_own_break_check_stopped_it_while_the_main_thread_was_held", fired)
        harness.go("still_end")
    elif phase == "still_end" and ended(ctx["still"]):
        record = ctx["still"]
        harness.state["still"] = {"status": record["status"], "reason": record["reason"], "seconds": job_seconds(record),
                                  "device": record["detail"].get("device")}
        check("budget_stop_recorded", record["status"] == "cancelled" and record["reason"] == "time_budget")
        check("it_rendered_on_the_gpu", str(record["detail"].get("device", "")).startswith("GPU"))
        check("its_own_deadline_was_set", record["detail"].get("deadline_armed") is True)
        check("stopped_within_bound_of_its_budget", job_seconds(record) < HOLD_S + MAX_STOP_SECONDS)
        check("a_stopped_still_publishes_nothing", not (harness.workspace / "renders/deadline.png").exists())
        check("settings_restored", scene.cycles.samples == 16 and scene.render.resolution_x == 64)
        check("no_hidden_staging_left", harness.hidden_left() == [])
        ctx["after_at"] = person_render(scene)   # The job ended: its deadline must have gone with it.
        harness.go("after")
    elif phase == "after":
        if bpy.app.is_job_running("RENDER"):
            return 0.05
        seconds = time.monotonic() - ctx["after_at"]
        harness.state["person_render_s"]["after_the_job"] = round(seconds, 3)
        check("the_persons_render_after_the_job_runs_to_its_end", seconds >= 0.6 * ctx["reference_s"])
        other = bpy.data.scenes.new("Other")
        _angelo_jobs.deadline(other, "RENDER", 0.001)   # Another scene's render deadline, already past.
        time.sleep(0.05)
        ctx["foreign_at"] = person_render(scene)
        harness.go("foreign")
    elif phase == "foreign":
        if bpy.app.is_job_running("RENDER"):
            return 0.05
        seconds = time.monotonic() - ctx["foreign_at"]
        harness.state["person_render_s"]["another_scenes_deadline_past"] = round(seconds, 3)
        check("another_scenes_deadline_never_stops_this_render", seconds >= 0.6 * ctx["reference_s"])
        check("another_scenes_deadline_is_not_fired_by_this_render", not _angelo_jobs.deadline_fired())
        _angelo_jobs.deadline(None)
        restore_person(scene)
        plane, _small = fx.bake_plane(scene)
        image = bpy.data.images.new("DeadlineBake", 2048, 2048)   # Long enough to outlast its budget on a GPU.
        plane.active_material.node_tree.nodes.active.image = image
        ctx["image"], ctx["pixels"] = image, fx.pixels(image)
        ctx["bake"], _ = harness.start_job({"kind": "BAKE_TEXTURE", "target": object_handle(plane), "samples": 4096,
                                            "minutes": MIN_MINUTES})
        harness.go("bake_hold")
    elif phase == "bake_hold":
        record = ctx["bake"]
        if record["status"] != "running":
            if ended(record):
                raise AssertionError("the bake ended before its budget: %s %s" % (record["status"], record["reason"]))
            return 0.05
        # Held as for the still: with the main thread free the manager's timer reaches the same budget at the same
        # moment and may stop the bake first (the suite's 2026-10-24 run), which says nothing about its break check.
        time.sleep(max(0.0, record["started"] + HOLD_S - time.time()))
        harness.state["bake_fired_while_main_thread_held"] = fired = bool(_angelo_jobs.deadline_fired())
        check("bake_stopped_by_its_own_break_check", fired)
        harness.go("bake")
    elif phase == "bake" and ended(ctx["bake"]):
        record = ctx["bake"]
        harness.state["bake"] = {"status": record["status"], "reason": record["reason"], "seconds": job_seconds(record)}
        check("bake_budget_stop_recorded", record["status"] == "cancelled" and record["reason"] == "time_budget")
        check("bake_stopped_within_bound_of_its_budget", job_seconds(record) < HOLD_S + MAX_STOP_SECONDS)
        check("bake_image_put_back", fx.pixels(ctx["image"]) == ctx["pixels"])
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0)
