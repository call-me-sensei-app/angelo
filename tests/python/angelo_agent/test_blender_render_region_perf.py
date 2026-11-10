# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Region render performance in the running app (doc/angelo/render_region.rst, "Performance").

Run on a quiet machine through the billed-lock queue, once per binary and alternating them (A B A B), by the suite
runner (each run gets a fresh profile):

    ANGELO_RR_BENCH_VARIANT=NAME ANGELO_RR_BENCH_REPS=N run_native_suites.py --gui --binary APP \
        --include test_blender_render_region_perf

Real default startup (Director), real render jobs (``render.render`` INVOKE, the render window as F12 opens it), Cycles
on the GPU when the machine has one, on both arms. The scene is the pixel suite's product shot (render_region_scenes)
at 1280 x 720. Every measure has one untimed warm-up, then ``--reps`` timed runs; the report keeps every run.

* ``full``: a full-frame F12 render, Cycles and EEVEE: wall clock from the operator to the job's end.
* ``viewport``: 30 window redraws of Director in Solid and in Material Preview, and 30 redraws of the render window.
* ``region``: only where the binary has the feature: the same render with a region of 20% of the frame
  (0.3-0.7 x 0.25-0.75) rendered over the last render.
* ``full-after-region``: only with the feature: an ordinary F12 right after a region render (the engine a region
  render keeps is freed first, as an ordinary render starts in Blender).
* ``tiny-region``: only with the feature: a 4 x 4 pixel region, so its pipeline time is the fixed cost (scene sync,
  BVH, device, denoiser setup) that the region's own pixels add to.
* ``stages``: each render's split: start (operator to pipeline), pipeline (sync, pixels, composite), after (display
  and the job's end). The feature's own costs (keeping the previous pixels, live tiles, the composite) are in the
  app's debug log (``--log render.region --log-level debug``).
* ``job``: only with the feature: the agent's ``angelo_job_start`` RENDER_REGION through the host's executor (approval
  facts, admission, start, the job's end), with a stand-in conversation (no provider or network).

Writes ``render-region-bench.json`` under ANGELO_QA_DIR (the variant inside), then quits.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
from angelo_agent import job_start_wait  # noqa: E402  (a quick job's start result may be held)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402  (Angelo's start-up is over)
import render_region_scenes  # noqa: E402
product_env.clear('DISABLE_DEFAULT_CHAT')   # Director as people start it.

VARIANT = os.environ.get("ANGELO_RR_BENCH_VARIANT", "unnamed")
REPS = int(os.environ.get("ANGELO_RR_BENCH_REPS", "5"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-render-region-bench"))
OUT.mkdir(parents=True, exist_ok=True)
REGION = (0.3, 0.25, 0.7, 0.75)   # 20% of the frame (Blender's left, bottom, right, top).
TINY = (0.5, 0.5, 0.5 + 4 / 1280, 0.5 + 4 / 720)   # 4 x 4 pixels: a region render's fixed cost.
bpy.context.preferences.use_preferences_save = False
STAGES = {}       # perf_counter of this run's render handlers and first sample (render thread).
report = {"variant": VARIANT, "reps": REPS, "binary": bpy.app.binary_path, "build_hash": bpy.app.build_hash.decode(),
          "settings": {}, "runs": {}, "summary": {}}
plan = []          # (label, start function, done check); run in order by ``tick``.
current = {"step": 0, "t0": None, "since": time.monotonic(), "warm": True}


def _stage(name):
    def handler(*_args):
        STAGES.setdefault(name, time.perf_counter())
    return handler


for _name in ("render_init", "render_pre", "render_post"):
    getattr(bpy.app.handlers, _name).append(_stage(_name))


def stages(t0, t1):
    """One run's split: start (operator to pipeline: arm, job start, window), pipeline (scene sync, BVH, device,
    pixels and the composite: the engine's sync and setup are what a 4 x 4 region's pipeline costs), after (display
    and the job's end)."""
    pre, post = STAGES.get("render_pre", t0), STAGES.get("render_post", t1)
    return {"start": round(pre - t0, 4), "pipeline": round(post - pre, 4), "after": round(t1 - post, 4)}


def has_feature():
    return "region_over_last" in bpy.ops.render.render.get_rna_type().properties


def main_window():
    return bpy.context.window_manager.windows[0]


def rendering():
    return bpy.app.is_job_running('RENDER')


def use_engine(engine):
    """Switch only when it differs: Blender frees every kept render engine on any set of ``render.engine``
    (ED_render_engine_changed), which a person never does between renders."""
    if bpy.context.scene.render.engine != engine:
        bpy.context.scene.render.engine = engine


def render(region=None):
    extra = {"region_over_last": True, "region": region} if region else {}
    with bpy.context.temp_override(window=main_window()):
        bpy.ops.render.render('INVOKE_DEFAULT', **extra)


def setup_scene():
    scene, _ = render_region_scenes.product()
    render_region_scenes.configure(scene, 'CYCLES', denoise=True)
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.cycles.samples = 64
    scene.eevee.taa_render_samples = 32
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type == kind]
            if gpus:
                for device in prefs.devices:
                    device.use = device.type == kind
                scene.cycles.device = 'GPU'
                report["settings"]["cycles_device"] = "%s (%s)" % (kind, ", ".join(d.name for d in gpus))
                break
        except TypeError:
            continue
    else:
        report["settings"]["cycles_device"] = "CPU"
    report["settings"].update(resolution=[1280, 720], cycles_samples=64, cycles_denoise=True, eevee_samples=32,
                              region=list(REGION), scene="render_region_scenes.product",
                              render_display=bpy.context.preferences.view.render_display_type)
    return scene


def redraw(label, window=None, area=None, iterations=30):
    window = window or main_window()
    override = {"window": window} if area is None else {
        "window": window, "area": area, "region": next(r for r in area.regions if r.type == 'WINDOW')}
    start = time.perf_counter()
    with bpy.context.temp_override(**override):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN' if area is None else 'DRAW', iterations=iterations)
    return (time.perf_counter() - start) / iterations


def render_window():
    """The window and Image Editor showing the Render Result (both arms: no add-on module of the feature)."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            image = getattr(area.spaces.active, "image", None) if area.type == 'IMAGE_EDITOR' else None
            if image is not None and image.type == 'RENDER_RESULT':
                return window, area
    return None, None


def add_render_steps(engine, region, prefix=None):
    label = (prefix or ("region" if region else "full")) + "/" + engine
    for n in range(REPS + 1):
        name = label if n else label + "/warm-up"
        if prefix == "full-after-region":   # An ordinary F12 right after a region render (untimed).
            plan.append(("@region-before", lambda e=engine: (use_engine(e), render(REGION)), lambda: not rendering()))
        plan.append((name, lambda e=engine, r=region: (use_engine(e), render(r)),
                     lambda: not rendering()))


def add_viewport_steps():
    def solid():
        view3d = next(a for a in main_window().screen.areas if a.type == 'VIEW_3D')
        view3d.spaces.active.shading.type = 'SOLID'
        redraw("warm")
        report["runs"].setdefault("viewport/solid", []).extend(redraw("solid") for _ in range(REPS))

    def material():
        view3d = next(a for a in main_window().screen.areas if a.type == 'VIEW_3D')
        view3d.spaces.active.shading.type = 'MATERIAL'
        for _ in range(3):   # Shaders compile on the first frames: untimed.
            redraw("warm")
        report["runs"].setdefault("viewport/material", []).extend(redraw("material") for _ in range(REPS))
        view3d.spaces.active.shading.type = 'SOLID'

    def render_view():
        window, area = render_window()
        if area is not None:
            redraw("warm", window, area)
            report["runs"].setdefault("viewport/render_window", []).extend(
                redraw("render window", window, area) for _ in range(REPS))
    for step in (solid, material, render_view):
        plan.append(("@" + step.__name__, step, lambda: True))


def add_job_steps():
    from angelo_agent import job_ui
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.files import FileWorkspace
    workspace = Path(tempfile.mkdtemp(prefix="rr-bench-ws-"))
    controller = SimpleNamespace(files=FileWorkspace(workspace), state={"id": "bench-chat"},
                                 executing_call=SimpleNamespace(id="call-1"))
    arguments = {"kind": "RENDER_REGION", "region": [0.3, 0.25, 0.7, 0.75]}   # The same region, from the top-left.
    held = {}

    def start():
        use_engine('CYCLES')
        executor = BlenderExecutor(main_window())
        executor.studio_context = controller
        executor.precondition("angelo_job_start", arguments)
        result = job_start_wait.started(executor.execute("angelo_job_start", arguments))
        held["record"] = job_ui.manager().record(result["job"])

    def done():
        record = held.get("record")
        return record is not None and record["status"] in {"done", "failed", "cancelled", "interrupted"}
    for n in range(REPS + 1):
        plan.append(("job/CYCLES" if n else "job/CYCLES/warm-up", start, done))
    plan.append(("@job_status", lambda: report.setdefault("job_status", held["record"]["status"]), lambda: True))


def summarize():
    for label, values in report["runs"].items():
        ordered = sorted(values)
        report["summary"][label] = {"n": len(values), "median": round(statistics.median(values), 4),
                                    "min": round(ordered[0], 4), "max": round(ordered[-1], 4),
                                    "spread": round(ordered[-1] - ordered[0], 4)}


def finish(error=None):
    if error:
        report["error"] = "".join(traceback.format_exception(error))
    summarize()
    report["success"] = error is None and bool(report["runs"])
    report["phase"] = "complete" if error is None else "error"
    (OUT / "render-region-bench.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_RENDER_REGION_BENCH", VARIANT, "error" if error else "done", flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if not plan:
            if not qa_boot.started():
                return 0.3
            setup_scene()
            report["feature"] = has_feature()
            for engine in ("CYCLES", "BLENDER_EEVEE"):
                add_render_steps(engine, None)
                if report["feature"]:
                    add_render_steps(engine, REGION)
                    add_render_steps(engine, TINY, "tiny-region")
                    add_render_steps(engine, None, "full-after-region")
            add_viewport_steps()
            if report["feature"]:
                add_job_steps()
            plan.append(("@end", lambda: None, lambda: True))
        label, start, done = plan[current["step"]]
        if current["t0"] is None:
            STAGES.clear()
            current["t0"], current["p0"] = time.monotonic(), time.perf_counter()
            start()
            return 0.01
        if not done():
            if time.monotonic() - current["t0"] > 600:
                raise TimeoutError("Timed out in " + label)
            return 0.01
        elapsed = time.monotonic() - current["t0"]
        if not label.startswith("@") and not label.endswith("warm-up"):
            report["runs"].setdefault(label, []).append(round(elapsed, 4))
            if not label.startswith("viewport"):
                report.setdefault("stages", {}).setdefault(label, []).append(
                    stages(current["p0"], current["p0"] + elapsed))
        current["t0"] = None
        current["step"] += 1
        if current["step"] >= len(plan):
            finish()
            return None
        return 0.5   # Let the render window settle between runs.
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=1.0)
