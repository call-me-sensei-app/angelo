# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render a region over the last render in the running app (doc/angelo/render_region.rst).

Real default startup (Director Mode with the chat) and real input (``Window.event_simulate``). No account or network.
The person's path: a full render opens the render window; its *Render region* pill starts the region tool, whose hint
shows; a drag over the render renders that region with the last render still on screen; the card says the region was
rendered over the last render, and every pixel of every pass outside the region is bit-identical to the full render;
Revert gives the full render back bit for bit and the pill reads *Show region render*; a region render stopped (as its
Cancel does) keeps the last render exactly and the card says so. The chat bar's + menu lists Render region, and the Render
menu has the item.

Run through ``run_native_suites.py --gui --include test_blender_render_region``; check ``render-region.json`` for
``success`` and ``phase == "complete"``. The screenshots are the GUI proof (Director, the default view).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (synthetic clicks and drags)
import render_region_scenes  # noqa: E402  (an ordinary look-dev scene: the pixel suite's product shot)
product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-render-region-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "render-region.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()
DRAG = ((0.30, 0.30), (0.62, 0.70))   # The dragged region, as fractions of the render window's image area.
PIXELS = {}   # Every channel of the renders compared (kept out of the report).


def main_window():
    return bpy.context.window_manager.windows[0]


def render_window():
    from angelo_agent import live_windows, render_region
    return next((w for w, a in live_windows.window_areas(bpy.context.window_manager) if render_region.shows_render(a)),
                None)


def render_area(window):
    from angelo_agent import render_region
    return next(a for a in window.screen.areas if render_region.shows_render(a))


def capture(name, window=None):
    path = OUT / name
    with bpy.context.temp_override(window=window or main_window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def channels(name):
    """Every layer and pass of the Render Result as float32 channels (a multilayer OpenEXR through OpenImageIO)."""
    import OpenImageIO as oiio
    path = OUT / (name + ".exr")
    bpy.data.images["Render Result"].save_render(filepath=str(path))
    source = oiio.ImageInput.open(str(path))
    found, part = {}, 0
    while source.seek_subimage(part, 0):   # A multilayer OpenEXR is one part per layer and pass.
        spec = source.spec()
        pixels = source.read_image(part, 0, 0, spec.nchannels, "float")
        found.update({channel: pixels[:, :, i].copy() for i, channel in enumerate(spec.channelnames)})
        part += 1
    source.close()
    return found


def differing(a, b, mask=None):
    import numpy
    out = {}
    for name in set(a) | set(b):
        if name not in a or name not in b:
            out[name] = "missing"
            continue
        unequal = a[name].view(numpy.uint32) != b[name].view(numpy.uint32)
        if mask is not None:
            unequal &= ~mask
        if unequal.any():
            out[name] = int(unequal.sum())
    return out


def region_mask(scene):
    import math
    import numpy
    left, bottom, right, top = scene["angelo_render_region"]
    width, height = scene.render.resolution_x, scene.render.resolution_y
    xmin, xmax = math.floor(left * width), math.ceil(right * width)
    ymin, ymax = math.floor(bottom * height), math.ceil(top * height)
    mask = numpy.zeros((height, width), bool)
    mask[height - ymax:height - ymin, xmin:xmax] = True
    return mask


def image_point(window, fx, fy):
    """Window pixel of the image's fraction (fx, fy) in the render window."""
    area = render_area(window)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    x, y = region.view2d.view_to_region(fx, fy, clip=False)
    return region.x + x, region.y + y


def pill_point(action):
    from angelo_agent import render_region_pills
    for _area, (_window, rects) in render_region_pills.places.items():
        for name, (x, y, w, h) in rects:
            if name == action:
                return x + w / 2, y + h / 2
    return None


def card_point(action):
    from angelo_agent import card_host
    active = card_host._active
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.as_pointer() == active.area:
                region = next(r for r in area.regions if r.type == 'WINDOW')
                for (x, y, w, h), name, _value in active.plan.hits:
                    if name == action:
                        return window, (region.x + x + w / 2, region.y + y + h / 2)
    return None, None


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_RENDER_REGION", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def rendering():
    return bpy.app.is_job_running('RENDER')


def tick():
    from angelo_agent import card_host, chat_destinations, director, render_region, render_region_draw
    phase = state["phase"]
    scene = bpy.context.scene
    try:
        assert time.monotonic() - started < 300, "Suite timed out"
        if phase == "boot":
            if not (qa_boot.started() and director.is_director(main_window())):
                return .3
            scene, _changed = render_region_scenes.product()
            render_region_scenes.configure(scene, 'CYCLES', denoise=True)
            # Long enough to see the last render kept while the region renders.
            scene.cycles.samples = 256
            scene.render.resolution_x, scene.render.resolution_y = 640, 360
            checks["plus_menu_lists_render_region"] = any(
                row.get("value") == "render_region"   # On the Render group's page (chat_destinations.PARENTS).
                for row in chat_destinations.group_rows(chat_destinations.PARENTS["render_region"]))
            checks["render_menu_has_the_item"] = render_region.render_menu in bpy.types.TOPBAR_MT_render._dyn_ui_initialize()
            capture("render-region-0-director.png")
            with bpy.context.temp_override(window=main_window()):
                bpy.ops.render.render('INVOKE_DEFAULT')
            state["phase"] = "full"
            return .5
        if phase == "full":
            if not wait(phase, lambda: not rendering() and render_window() is not None and pill_point("render")):
                return .3
            PIXELS["full"] = channels("full")
            capture("render-region-1-full-render.png", render_window())
            qa_input.press(render_window(), *pill_point("render"))
            state["phase"] = "tool"
            return .5
        if phase == "tool":
            if not wait(phase, lambda: render_region_draw._tool["op"] is not None):
                return .2
            capture("render-region-2-drag-hint.png", render_window())
            window = render_window()
            qa_input.send(window, qa_input.drag_path(image_point(window, *DRAG[0]), image_point(window, *DRAG[1])))
            state["phase"] = "region"
            return .05
        if phase == "region":
            if rendering() and "during" not in state:
                state["during"] = True   # The last render stays on screen while the region renders.
                capture("render-region-3-rendering.png", render_window())
            if not wait(phase, lambda: not rendering() and card_host.is_active("render_region"), limit=90):
                return .1
            checks["region_render_applied"] = render_region.state(scene) == "APPLIED"
            after = channels("applied")
            state["measured"]["outside_differs"] = differing(PIXELS["full"], after, region_mask(scene))
            checks["outside_the_region_bit_identical"] = not state["measured"]["outside_differs"]
            checks["scene_render_region_untouched"] = not scene.render.use_border
            capture("render-region-4-applied-card.png", render_window())
            window, point = card_point("revert")
            qa_input.press(window, *point)
            state["phase"] = "revert"
            return .5
        if phase == "revert":
            if not wait(phase, lambda: render_region.state(scene) == "REVERTED"):
                return .2
            state["measured"]["revert_differs"] = differing(PIXELS["full"], channels("reverted"))
            checks["revert_gives_the_full_render_back_bit_for_bit"] = not state["measured"]["revert_differs"]
            if card_host.is_active("render_region"):
                card_host.close("done")
            capture("render-region-5-reverted.png", render_window())
            scene.render.engine = 'CYCLES'   # Slow enough for the stop to land while the engine renders.
            scene.cycles.samples, scene.cycles.use_adaptive_sampling = 4096, False
            with bpy.context.temp_override(window=render_window()):
                bpy.ops.angelo.render_region_again('INVOKE_DEFAULT')
            state["phase"] = "cancel"
            return .05
        if phase == "cancel":
            if not wait(phase, rendering, limit=10):
                return .02
            # The job's own stop, as its Cancel does: a simulated Escape never sets Blender's break flag, which only
            # the operating system's key event does (wm_event_add_ghostevent).
            from _bpy import _angelo_jobs
            _angelo_jobs.stop(scene, 'RENDER')
            state["phase"] = "cancelled"
            return .2
        if phase == "cancelled":
            if not wait(phase, lambda: not rendering() and card_host.is_active("render_region"), limit=60):
                return .2
            state["measured"]["cancel_state"] = render_region.state(scene)
            state["measured"]["cancel_differs"] = differing(PIXELS["full"], channels("cancelled"))
            checks["stopping_ends_the_region_render_without_composing_it"] = state["measured"]["cancel_state"] == "NONE"
            checks["a_cancelled_region_render_keeps_the_last_render"] = not state["measured"]["cancel_differs"]
            capture("render-region-6-after-stop.png", render_window())
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
