# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mantaflow in the running app: a smoke bake as Blender's own job, its disk cache, its frames and the viewport.

``test_blender_release_features`` bakes smoke and liquid headless, where Blender's
bake operator runs synchronously. This suite bakes the way the Physics panel's
Bake button does (the operator invoked, so Blender runs it as a window-manager
job) in a saved project, and checks what an artist relies on:

* the bake runs as a job: the operator answers at once (running modal), the main
  thread keeps serving timers while it bakes (the longest gap between this
  suite's 0.05 s ticks is recorded and bounded), and its progress (the domain's
  pause frame) only grows and reaches the last frame;
* the cache: one data file per frame inside the project's cache folder, the
  domain's baked flags set;
* the frames: the smoke's density read back from that cache grows from an early
  frame to the last;
* the viewport: the 3D view at the last frame, as Blender's viewport render draws
  it, differs from the same view after the cache is freed, by the smoke and
  nothing else;
* a stop: Blender's Pause Bake stops a longer bake within the suite bound and
  leaves the frames it finished on disk.

No Angelo job tool is involved (Mantaflow is not a job kind yet); the agent
add-on is enabled as in the app. Fixture: a 2 m domain and a 0.3 m smoke inflow,
CPU only, no provider, account or network.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_mantaflow``; check ``mantaflow.json`` for ``success`` and
``phase == "complete"``. On a build without Mantaflow (``bpy.app.build_options.fluid``
false) the receipt records the skip and succeeds.
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
from production_jobs_harness import MAX_STOP_SECONDS, Harness  # noqa: E402

harness = Harness("ANGELO_MANTAFLOW", "mantaflow.json", "/tmp/angelo-mantaflow-qa", timeout=600)
ctx = harness.ctx
PROJECT = harness.out / "project"
TICK = 0.05
MAX_TICK_GAP = 1.0      # The main thread is not frozen: a timer never waits a second while the bake runs.
LAST = 24               # Frames of the checked bake (1..LAST).
STOP_LAST = 400         # Frames of the bake that is stopped mid-way; far more than it reaches before the stop.


def domain():
    return bpy.data.objects["Smoke Domain"]


def settings():
    return domain().modifiers["Fluid"].domain_settings


def view3d():
    """The window's largest 3D View (a layout may also hold collapsed ones) and its main region."""
    window = harness.window()
    area = max((area for area in window.screen.areas if area.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return window, area, next(region for region in area.regions if region.type == 'WINDOW')


def operator_context():
    window, area, region = view3d()
    return dict(window=window, area=area, region=region, active_object=domain(), object=domain(),
                selected_objects=[domain()])


def smoke_scene(resolution, last):
    scene = bpy.context.scene
    for obj in [obj for obj in scene.objects if obj.type == 'MESH']:
        bpy.data.objects.remove(obj)
    scene.frame_start, scene.frame_end, scene.frame_current = 1, last, 1
    mesh = bpy.data.meshes.new("Smoke Domain")
    mesh.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (0, 2)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    box = bpy.data.objects.new("Smoke Domain", mesh)
    scene.collection.objects.link(box)
    box.modifiers.new("Fluid", 'FLUID').fluid_type = 'DOMAIN'
    domain_settings = box.modifiers["Fluid"].domain_settings
    domain_settings.domain_type = 'GAS'
    domain_settings.resolution_max = resolution
    domain_settings.cache_type = 'ALL'
    domain_settings.cache_directory = "//cache smoke"
    domain_settings.cache_frame_start, domain_settings.cache_frame_end = 1, last
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0, 0, 0.5))
    flow = bpy.context.object
    flow.name = "Smoke Flow"
    flow.modifiers.new("Fluid", 'FLUID').fluid_type = 'FLOW'
    flow.modifiers["Fluid"].flow_settings.flow_type = 'SMOKE'
    flow.modifiers["Fluid"].flow_settings.flow_behavior = 'INFLOW'


def start_bake():
    with bpy.context.temp_override(**operator_context()):
        result = bpy.ops.fluid.bake_all('INVOKE_DEFAULT')
    ctx.update(bake_started=time.monotonic(), last_tick=time.monotonic(), gaps=[], pause=[])
    return result


def watch_bake():
    """One tick while Blender bakes: the gap since the last tick and the pause frame (its progress)."""
    now = time.monotonic()
    ctx["gaps"].append(now - ctx["last_tick"])
    ctx["last_tick"] = now
    ctx["pause"].append(settings().cache_frame_pause_data)
    return settings().is_cache_baking_any


def data_frames():
    folder = PROJECT / "cache smoke" / "data"
    return sorted({int(path.name.split("_")[-1].split(".")[0]) for path in folder.glob("fluid_data_*.*")}) \
        if folder.is_dir() else []


def density(frame):
    bpy.context.scene.frame_set(frame)
    evaluated = domain().evaluated_get(bpy.context.evaluated_depsgraph_get())
    return sum(evaluated.modifiers["Fluid"].domain_settings.density_grid)


def viewport_pixels(name):
    """The largest 3D View as Blender's viewport render draws it (offscreen, ``render.opengl`` from the view), as a
    flat array of RGBA floats. A screenshot reads the window's framebuffer, which a machine with its display asleep
    or locked returns as one pixel; the viewport render draws the same view without the screen."""
    import numpy
    window, area, region = view3d()
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 240, 100
    path = harness.out / name
    harness.state["viewport_area"] = [area.width, area.height]
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.render.opengl(view_context=True)
    bpy.data.images["Render Result"].save_render(str(path))
    image = bpy.data.images.load(str(path))
    pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
    image.pixels.foreach_get(pixels)
    harness.state.setdefault("viewport_sizes", []).append(list(image.size))
    bpy.data.images.remove(image)
    return pixels


def bake_record(label):
    gaps = ctx["gaps"][1:]  # The first gap includes the operator call itself.
    pause = ctx["pause"]
    harness.state.setdefault("bakes", {})[label] = {
        "seconds": round(time.monotonic() - ctx["bake_started"], 3), "ticks": len(gaps),
        "max_tick_gap_s": round(max(gaps, default=0.0), 3), "pause_frames": [min(pause, default=None), max(pause, default=None)]}
    return gaps, pause


def step(harness):
    phase, check = harness.state["phase"], harness.check
    if phase == "boot":
        if not bpy.app.build_options.fluid:
            harness.state["skipped"] = "Mantaflow is not compiled into this build"
            check("fluid_compiled_out_recorded", True)
            harness.finish()
            return None
        PROJECT.mkdir(exist_ok=True)
        smoke_scene(32, LAST)
        bpy.ops.wm.save_as_mainfile(filepath=str(PROJECT / "smoke.blend"))
        result = start_bake()
        check("bake_starts_as_a_blender_job", result == {'RUNNING_MODAL'})
        harness.go("baking")
        return TICK
    if phase == "baking":
        if watch_bake():
            return TICK
        gaps, pause = bake_record("smoke")
        check("main_thread_serves_timers_while_baking", len(gaps) >= 3 and max(gaps) < MAX_TICK_GAP)
        check("progress_only_grows_to_the_last_frame", pause == sorted(pause) and max(pause, default=0) == LAST)
        check("cache_baked_flags_set", settings().has_cache_baked_data and not settings().is_cache_baking_any)
        frames = data_frames()
        harness.state["data_frames"] = frames
        check("a_data_file_per_frame_in_the_projects_cache", frames == list(range(1, LAST + 1)))
        early, last = density(2), density(LAST)
        harness.state["density"] = {"2": round(early, 3), str(LAST): round(last, 3)}
        check("frames_read_back_from_the_cache", last > early > 0.0)
        window, area, region = view3d()
        with bpy.context.temp_override(window=window, area=area, region=region, selected_objects=[domain()],
                                       active_object=domain(), object=domain()):
            bpy.ops.view3d.view_selected()   # The domain fills the view, the same way in both pictures.
        ctx["with_smoke"] = viewport_pixels("viewport-baked.png")
        # Free synchronously (exec): an invoked Free is a job of its own, and touching the domain while it runs
        # aborts Mantaflow ("can't clean grid cache, some grids are still in use"), as any edit during a bake does.
        with bpy.context.temp_override(**operator_context()):
            bpy.ops.fluid.free_all()
        bpy.context.scene.frame_set(LAST)
        without = viewport_pixels("viewport-freed.png")
        with_smoke = ctx.pop("with_smoke")
        same_size = len(without) == len(with_smoke)
        changed = float(abs(with_smoke - without).max(axis=None)) if same_size else 0.0
        share = float((abs(with_smoke - without).reshape(-1, 4)[:, :3].max(axis=1) > 0.05).mean()) if same_size else 0.0
        harness.state["viewport"] = {"pixels_changed_share": round(share, 4), "max_channel_change": round(changed, 3)}
        # The smoke covers part of the view: some pixels change, never the whole view (the frame is otherwise the same).
        check("viewport_shows_the_baked_smoke", same_size and 0.002 < share < 0.9)
        check("free_clears_the_cache", not settings().has_cache_baked_any and density(LAST) == 0.0)
        # A longer bake, stopped mid-way by Blender's own Pause Bake.
        settings().resolution_max = 64
        settings().cache_frame_end = bpy.context.scene.frame_end = STOP_LAST
        start_bake()
        harness.go("stopping")
        return TICK
    if phase == "stopping":
        baking = watch_bake()
        if "cancel_at" not in ctx:
            if not baking:
                raise AssertionError("The long bake ended before it could be stopped")
            if settings().cache_frame_pause_data >= 3:
                ctx["stopped_at_frame"] = settings().cache_frame_pause_data   # The frame being baked.
                ctx["cancel_at"] = time.monotonic()
                with bpy.context.temp_override(**operator_context()):
                    bpy.ops.fluid.pause_bake()
            return TICK
        if baking:
            return TICK
        harness.stopped("fluid_bake")
        gaps, _pause = bake_record("stopped")
        check("main_thread_serves_timers_while_the_long_bake_runs", max(gaps, default=0.0) < MAX_TICK_GAP)
        frames = data_frames()
        harness.state["stopped_frames"] = [min(frames, default=None), max(frames, default=None), len(frames)]
        # Every frame before the one it was baking is whole on disk, in order; it stopped long before the end.
        check("a_stopped_bake_keeps_the_frames_it_finished", ctx["stopped_at_frame"] - 1 <= len(frames) < STOP_LAST
              and frames == list(range(1, len(frames) + 1)))
        harness.finish()
        return None
    return TICK


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
