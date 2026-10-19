# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""GUI: actual production renders honor per-layer bounds, work, approval and restoration.

Run with run_native_suites.py --gui --include test_blender_render_samples.
Factory state, small CPU renders, no provider, account or network.
"""

import re
import sys
from pathlib import Path

import addon_utils
import bpy

from angelo_agent import ui

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
sys.path.insert(0, str(Path(__file__).resolve().parent))
from production_jobs_harness import Harness, ended
from angelo_agent import job_runners, job_settings, render_extensions

harness = Harness("ANGELO_RENDER_SAMPLES", "render-samples.json", "/tmp/angelo-render-samples-qa", timeout=180)
CASES = (("USE", 16, [128, 0], 8, [8, 8]),
         ("BOUNDED", 16, [128, 4], 8, [8, 4]),
         ("IGNORE", 16, [128, 4], 8, [8, 8]),
         ("USE", 4, [0, 8], 32, [32, 8]),
         ("USE", 32, [8, 16], None, [8, 16]))


def settings(scene):
    return (scene.cycles.samples, scene.cycles.use_layer_samples, scene.cycles.use_denoising,
            [(layer.name, layer.samples, layer.cycles.use_denoising) for layer in scene.view_layers])


@render_extensions.host_handler  # The fixture's measurement, not add-on code the approvals would name.
def stats(*args):
    harness.ctx.setdefault("stats", []).extend(str(arg) for arg in args)


def step(h):
    scene, ctx, check = h.window().scene, h.ctx, h.check
    phase = h.state["phase"]
    if phase == "boot":
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.use_adaptive_sampling = False
        scene.cycles.use_denoising = False
        scene.render.resolution_x, scene.render.resolution_y = 256, 144
        scene.render.resolution_percentage = 100
        scene.render.use_compositing = scene.render.use_sequencer = False
        scene.render.use_single_layer = False
        for obj in scene.objects:
            if obj.type != "CAMERA":
                obj.hide_render = True
        scene.view_layers.new('Other "\\ Layer')
        layers = list(scene.view_layers)
        scene.cycles.samples, scene.cycles.use_layer_samples = 1, "USE"
        layers[0].samples = 20000
        check("inherited_layer_over_limit_refused", "20,000" in (h.refused(
            {"kind": "RENDER_STILL", "output": "too-many-samples"}) or ""))
        scene.cycles.samples, scene.cycles.use_layer_samples = 16384, "IGNORE"
        for layer in layers:
            layer.samples = 0
        check("combined_layer_work_refused", "Across the rendered layers" in (h.refused(
            {"kind": "RENDER_ANIMATION", "output": "too-much-work", "resolution": [4096, 4096],
             "frame_start": 1, "frame_end": 60}) or ""))
        ctx["index"] = 0
        bpy.app.handlers.render_stats.append(stats)
        h.go("next")
    elif phase == "next":
        index = ctx["index"]
        if index == len(CASES):
            h.go("single")
            return 0.1
        mode, count, per_layer, requested, expected = CASES[index]
        scene.cycles.samples, scene.cycles.use_layer_samples = count, mode
        for layer, samples in zip(scene.view_layers, per_layer):
            layer.samples, layer.cycles.use_denoising = samples, False
        ctx["before"], ctx["stats"], ctx["expected"] = settings(scene), [], expected
        arguments = {"kind": "RENDER_STILL", "output": "case-%d" % index}
        if requested is not None:
            arguments["samples"] = requested
        ctx["job"], _result = h.start_job(arguments)
        check("case_%d_denoising_unchanged_during" % index,
              not scene.cycles.use_denoising and not any(layer.cycles.use_denoising for layer in scene.view_layers))
        h.go("rendering")
    elif phase == "rendering" and ended(ctx["job"]):
        index, record = ctx["index"], ctx["job"]
        observed = {int(match.group(1)) for text in ctx["stats"] for match in re.finditer(r"Sample \d+/(\d+)", text)}
        h.state.setdefault("renders", []).append({"case": index, "facts": record["detail"],
                                                  "status": record["status"], "native_samples": sorted(observed)})
        check("case_%d_done" % index, record["status"] == "done")
        check("case_%d_native_samples" % index, observed == set(ctx["expected"]))
        check("case_%d_truthful_facts" % index, record["detail"]["samples"] == max(ctx["expected"])
              and [row["samples"] for row in record["detail"]["layer_samples"]] == ctx["expected"])
        check("case_%d_restored" % index, settings(scene) == ctx["before"] and job_settings.TAG not in scene)
        ctx["index"] += 1
        h.go("next")
    elif phase == "single":
        scene.render.use_single_layer = True
        active = h.window().view_layer
        for layer in scene.view_layers:
            layer.samples, layer.use = (4, False) if layer == active else (20000, True)
        scene.cycles.samples, scene.cycles.use_layer_samples = 8, "USE"
        ctx["before"], ctx["stats"] = settings(scene), []
        ctx["job"], _result = h.start_job({"kind": "RENDER_STILL", "output": "single-layer"})
        check("single_layer_facts", ctx["job"]["detail"]["layer_samples"] == [{"name": active.name, "samples": 4}])
        h.go("single-rendering")
    elif phase == "single-rendering" and ended(ctx["job"]):
        observed = {int(match.group(1)) for text in ctx["stats"] for match in re.finditer(r"Sample \d+/(\d+)", text)}
        check("single_layer_actual", ctx["job"]["status"] == "done" and observed == {4})
        check("single_layer_restore", settings(scene) == ctx["before"] and job_settings.TAG not in scene)
        scene.render.use_single_layer = False
        for layer in scene.view_layers:
            layer.use, layer.samples = True, 128
        ctx["before"] = settings(scene)
        ctx["job"], _result = h.start_job({"kind": "RENDER_STILL", "output": "cancel", "samples": 8})
        h.cancel(ctx["job"])
        h.go("cancel")
    elif phase == "cancel" and ended(ctx["job"]):
        check("cancel_restores_sampling", ctx["job"]["status"] == "cancelled"
              and settings(scene) == ctx["before"] and job_settings.TAG not in scene)
        request = {"kind": "RENDER_STILL", "output": "fails.png", "format": "PNG", "samples": 8}
        runner = job_runners.RenderRunner(h.window(), request, h.controller.files)
        runner.identity = "failed-sampling-fixture"
        def refuse(*args, **kwargs):
            raise RuntimeError("fixture render start failure")
        runner._invoke = refuse
        try:
            runner.start()
            check("start_failure_refused", False)
        except RuntimeError as error:
            check("start_failure_refused", str(error) == "fixture render start failure")
        check("start_failure_restores_sampling", settings(scene) == ctx["before"] and job_settings.TAG not in scene)
        bpy.app.handlers.render_stats.remove(stats)
        h.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
