# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A first Cycles render that compiles its kernels, in the built app: the job's short budget is not spent on the
compile, the card says what it waits for, the next estimate leaves it out, and a later child render reuses the kernels
earlier renders prepared: it compiles nothing and archives nothing new, judged the same on every backend
(render_kernel_reuse: Metal, CUDA, HIP, OptiX, CPU).

Blender 5.2.4 changed Cycles' kernels, so every Mac's first render after the 1.0.11 update compiles them (134 s on an
M4 Pro). Run on a warm machine every check still holds (nothing compiles); run cold (an app copy with its own bundle
id and a fresh HOME: macOS keeps compiled pipelines per bundle id, Cycles its archives in ~/.cache/cycles) it proves
the compile path: ``render-kernels.json`` records ``app_compile_s`` and ``child_compile_s``.

A window is required: ``run_native_suites.py --gui --include test_blender_render_kernels.py``. With
``ANGELO_RENDER_KERNELS_FORCE_RECOMPILE=1`` the third render finds no kernel archive and no OptiX cache, so it builds
its kernels again and ``a_child_render_reuses_the_compiled_kernels`` must FAIL: the check's fail-first proof on a
machine (its record says ``forced_recompile``).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import atexit
import time
from pathlib import Path

os.environ["ANGELO_RENDER_PROCESS"] = "app"   # The first still renders here; the second is sent to a child.

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
from production_jobs_harness import Harness, ended  # noqa: E402
from angelo_agent import job_chat, job_render_route  # noqa: E402
from angelo_render_device import kernels  # noqa: E402
import render_kernel_reuse as reuse  # noqa: E402

harness = Harness("ANGELO_RENDER_KERNELS", "render-kernels.json", "/tmp/angelo-render-kernels-qa", timeout=900)
ctx = harness.ctx
STILL = {"resolution": [320, 180], "samples": 64}   # Seconds warm: a 0.5 min budget is far past it.
BUDGET_MINUTES = 0.5   # Shorter than a cold compile (134 s): before the fix the compile alone ended the job.
GPU_WAIT_S = 90
FORCED = os.environ.get("ANGELO_RENDER_KERNELS_FORCE_RECOMPILE") == "1"   # render_kernel_reuse.FORCE_RECOMPILE


def kernel_root():
    """Cycles' kernel cache of this run (the child inherits the same variables and user folder)."""
    return reuse.kernel_root(user_config=bpy.utils.user_resource('CONFIG', path=''))


def watch(record):
    """While the still runs: whether its compile was seen, and what the card said then."""
    if record["detail"].get("kernels_loading"):
        ctx["seen_loading"] = True
        ctx["card"] = job_chat.progress_text(record)


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        scene.cycles.device = "GPU"
        ctx["gpu_until"] = time.monotonic() + GPU_WAIT_S
        harness.go("gpu")
    elif phase == "gpu":
        from angelo_agent import render_device
        device = (render_device.facts(scene) or {}).get("device", "")
        if not device.startswith("GPU") and time.monotonic() < ctx["gpu_until"]:
            return 0.5
        harness.state["device"] = device
        ctx["first"], _ = harness.start_job(dict(STILL, kind="RENDER_STILL", output="renders/first.png",
                                                 minutes=BUDGET_MINUTES))
        harness.go("first")
    elif phase == "first":
        record = ctx["first"]
        watch(record)
        if not ended(record):
            return 0.1
        compiled = record["detail"].get("kernel_compile_s", 0.0)
        harness.state["app_compile_s"] = compiled
        harness.state["first"] = {"status": record["status"], "reason": record["reason"], "card": ctx.get("card"),
                                  "seconds": round((record["ended"] or 0) - (record["started"] or 0), 1)}
        check("a_short_budget_is_not_spent_on_the_compile", record["status"] == "done")
        check("the_card_said_it_prepares_render_kernels_while_they_compiled",
              not ctx.get("seen_loading") or "render kernels" in (ctx.get("card") or ""))
        os.environ["ANGELO_RENDER_PROCESS"] = "child"
        ctx["second"], _ = harness.start_job(dict(STILL, kind="RENDER_STILL", output="renders/second.png",
                                                  minutes=BUDGET_MINUTES))
        harness.go("second")
    elif phase == "second":
        record = ctx["second"]
        if not ended(record):
            return 0.1
        estimate = record["detail"].get("estimate_s")
        harness.state["second"] = {"status": record["status"], "process": record["detail"].get("process"),
                                   "estimate_s": estimate, "estimate_from": record["detail"].get("estimate_from")}
        harness.state["child_compile_s"] = record["detail"].get("kernel_compile_s", 0.0)
        harness.state["second"]["seconds"] = round((record["ended"] or 0) - (record["started"] or 0), 3)
        check("the_next_estimate_leaves_the_compile_out",
              isinstance(estimate, (int, float)) and estimate < job_render_route.MIN_ESTIMATE_S)
        ctx["metal"] = reuse.is_metal(harness.state.get("device"))
        ctx["archives"] = reuse.archives(kernel_root(), ctx["metal"])
        harness.state["forced_recompile"] = FORCED
        if FORCED:   # The fail-first proof: the child finds nothing prepared (render_kernel_reuse.recompile).
            ctx["forced"] = reuse.recompile(kernel_root())
            ctx["forced"].__enter__()
            atexit.register(ctx["forced"].__exit__, None, None, None)   # Put back even if the run ends early.
        ctx["third"], _ = harness.start_job(dict(STILL, kind="RENDER_STILL", output="renders/third.png",
                                                 minutes=BUDGET_MINUTES))
        harness.go("third")
    elif phase == "third":
        record = ctx["third"]
        if not ended(record):
            return 0.1
        after = reuse.archives(kernel_root(), ctx["metal"])
        if "forced" in ctx:
            ctx.pop("forced").__exit__(None, None, None)
        # Reuse, as a person meets it: a child render loads what the earlier renders prepared, never builds it again.
        # The third render is judged, not the second: the first child after a warm app can have nothing archived to
        # read in a fresh HOME (16.8 s on 116c309). Three facts, on every backend (render_kernel_reuse): Cycles said it
        # loaded render kernels for under kernels.MIN_COMPILE_S, and the render archived nothing new (on Metal the
        # earlier renders must have archived something; OptiX archives nothing in Cycles' cache), and its
        # job took no longer than the identical second child's by render_kernel_reuse.SETUP_SLACK_S (a build too
        # quick to count as a compile: OptiX on the T4).
        ok, why = reuse.reused(record, ctx["second"], ctx["metal"], ctx["archives"], after, kernels.MIN_COMPILE_S)
        harness.state["third"] = {"status": record["status"], "process": record["detail"].get("process"),
                                  "kernel_compile_s": record["detail"].get("kernel_compile_s", 0.0),
                                  "seconds": round((record["ended"] or 0) - (record["started"] or 0), 3),
                                  "archives": [len(ctx["archives"]), len(after)],
                                  "archive_kind": "Metal generic" if ctx["metal"] else "any file",
                                  "reuse": why}
        check("a_child_render_reuses_the_compiled_kernels", ok)
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0)
