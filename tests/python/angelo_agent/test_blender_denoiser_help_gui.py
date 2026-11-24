# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles' Denoise panels say who OpenImageDenoise is for and why Use GPU is greyed out, in the real window
(doc/angelo/render_engines.rst, "Denoisers on every GPU").

From Angelo's default start (Director), the person's path: Manual mode (Show controls), Render Properties, Cycles,
then the Properties editor's own search for "Denoise", which opens the matching panels. On this machine:

* with Cycles' compute device set to None, a GPU here that supports OpenImageDenoise is not chosen: both Denoise
  panels say where to choose it ('AVAILABLE');
* with this machine's GPU chosen again (Angelo's default), there is no line ('USED');
* OpenImageDenoise's description, as the denoiser menu's tooltip reads it, names AMD, Apple, Intel and NVIDIA.

Screenshots are checked by eye. Run through ``run_native_suites.py --gui --include test_blender_denoiser_help_gui``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import console, director, product_env  # noqa: E402
import qa_boot  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # Angelo's default start: Director.
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-denoiser-help"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": []}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def properties():
    return next(a for a in window().screen.areas if a.type == 'PROPERTIES')


def cycles_prefs():
    return bpy.context.preferences.addons["cycles"].preferences


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
        state["failed_checks"] = [k for k, v in state["checks"].items() if not v]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    state.pop("scenario", None)
    (OUT / "denoiser-help.json").write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_DENOISER_HELP", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def scenario():
    checks, observed = state["checks"], state["observed"]
    began = time.monotonic()
    while not (director.is_director(window()) and qa_boot.started()):
        assert time.monotonic() - began < 90, "Director never settled: %s" % qa_boot.waiting_on()
        yield
    checks["starts_in_director_mode"] = True
    capture("denoise-0-director.png")
    chat = next(a for a in window().screen.areas if console.is_chat(a))
    with bpy.context.temp_override(window=window(), area=chat):
        bpy.ops.angelo.workspace_mode(mode='MANUAL')   # Show controls: the editors a person tunes Cycles in.
    began = time.monotonic()
    while not qa_boot.started() or not any(a.type == 'PROPERTIES' for a in window().screen.areas):
        assert time.monotonic() - began < 60, "Manual never showed the Properties editor"
        yield
    scene = window().scene
    scene.render.engine = 'CYCLES'
    scene.cycles.use_preview_denoising = True
    space = properties().spaces.active
    space.context = 'RENDER'
    space.search_filter = "Denoise"   # The editor's own search: matching panels open and light up.
    import _cycles
    from cycles import properties as cycles_properties
    description = cycles_properties.enum_openimagedenoise_denoiser(None, bpy.context)[0][2]
    observed["openimagedenoise_description"] = description
    checks["openimagedenoise_is_for_every_vendor"] = all(w in description for w in ("AMD", "Apple", "Intel", "NVIDIA"))
    observed["with_dlss"] = bool(getattr(_cycles, "with_dlss", False))
    prefs = cycles_prefs()
    original = prefs.compute_device_type
    observed["compute_device_type"] = original
    observed["gpu_state_as_set"] = prefs.oidn_gpu_state()
    prefs.compute_device_type = 'NONE'
    for _ in range(4):
        yield
    observed["gpu_state_without_a_device"] = prefs.oidn_gpu_state()
    checks["without_a_chosen_gpu_the_panel_says_where_to_choose_one"] = observed["gpu_state_without_a_device"] in (
        'AVAILABLE', 'NONE')
    capture("denoise-1-no-device.png")
    if original != 'NONE':
        prefs.compute_device_type = original
        for _ in range(4):
            yield
        observed["gpu_state_with_the_device"] = prefs.oidn_gpu_state()
        checks["with_the_gpu_chosen_no_line"] = observed["gpu_state_with_the_device"] == 'USED' or (
            observed["gpu_state_with_the_device"] == observed["gpu_state_as_set"])
        capture("denoise-2-gpu-chosen.png")
    space.search_filter = ""


def tick():
    try:
        assert time.monotonic() - started < 300, "The fixture timed out in " + state["phase"]
        if state["phase"] == "boot":
            state["phase"], state["scenario"] = "run", scenario()
            return 1.0
        try:
            next(state["scenario"])
        except StopIteration:
            finish()
            return None
        return 0.3
    except Exception as error:
        finish(error)
        return None


if __name__ == "__main__":
    bpy.app.timers.register(tick, first_interval=2.0)
