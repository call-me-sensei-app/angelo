# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tension map playback benchmark: frames per second while the timeline plays, in a window, stock Blender or Angelo.

    <blender> --factory-startup --python tension_map_playback.py -- --arm ARM --out result.json [--size 1000]

The scene is ``tension_map_bench``'s (a ``size`` x ``size`` grid whose shape key deforms every vertex every frame),
the only object, framed in the largest 3D view in Solid shading. Playback runs with frame dropping off (every frame
evaluated and drawn) and as fast as the app can go; after ``--warm`` seconds of playing, frames are counted for
``--seconds``. Arms: ``none``, ``gn`` (the Geometry Nodes workaround), ``tension`` (the modifier, map hidden),
``tension_map`` (the modifier with the 3D view's tension map shown; Angelo only).

Reported: fps over the window, and the median, p25 and p75 of the frame-to-frame intervals. The app quits at the end.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tension_map_bench as bench  # noqa: E402


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("none", "gn", "tension", "tension_map"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--warm", type=float, default=3.0)
    parser.add_argument("--seconds", type=float, default=8.0)
    return parser.parse_args(argv)


args = arguments()
state = {"phase": "build", "stamps": []}


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def on_frame(scene, depsgraph=None):
    state["stamps"].append(time.perf_counter())


def finish(error=None):
    result = {"arm": args.arm, "size": args.size, "blender": bpy.app.version_string, "binary": bpy.app.binary_path}
    if error:
        result["error"] = error
    else:
        stamps = [s for s in state["stamps"] if s >= state["start"]]
        intervals = sorted(1000.0 * (b - a) for a, b in zip(stamps, stamps[1:]))
        quartiles = statistics.quantiles(intervals, n=4) if len(intervals) > 3 else [0, 0, 0]
        window, area, region = view3d()
        result.update(fps=round((len(stamps) - 1) / (stamps[-1] - stamps[0]), 2) if len(stamps) > 1 else 0.0,
                      frames=len(stamps), median_ms=round(statistics.median(intervals), 2) if intervals else None,
                      p25_ms=round(quartiles[0], 2), p75_ms=round(quartiles[2], 2),
                      window=[window.width, window.height], view=[region.width, region.height],
                      vertices=len(bpy.data.objects["Dense"].data.vertices))
    Path(args.out).write_text(json.dumps(result, indent=1))
    print("TENSION_PLAYBACK", json.dumps({k: v for k, v in result.items() if k != "binary"}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if state["phase"] == "build":
            for obj in list(bpy.data.objects):
                bpy.data.objects.remove(obj)
            obj = bench.scene_with_grid(args.size, 48, empty=False)
            if args.arm == "gn":
                bench.gn_workaround(obj)
            elif args.arm in {"tension", "tension_map"}:
                obj.modifiers.new("Tension", 'TENSION')
            scene = bpy.context.scene
            scene.sync_mode = 'NONE'           # Every frame evaluated and drawn, as fast as the app goes.
            scene.render.fps = 240
            window, area, region = view3d()
            space = area.spaces.active
            space.shading.type = 'SOLID'
            if hasattr(space.overlay, "show_tension"):
                space.overlay.show_tension = args.arm == "tension_map"
            bpy.context.view_layer.update()   # The removed factory objects leave the view layer.
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.view_selected()
                bpy.app.handlers.frame_change_post.append(on_frame)
                bpy.ops.screen.animation_play()
            state.update(phase="warm", started=time.perf_counter())
            return 0.2
        if state["phase"] == "warm":
            if time.perf_counter() - state["started"] < args.warm:
                return 0.2
            state.update(phase="measure", start=time.perf_counter())
            return args.seconds
        if state["phase"] == "measure":
            window, area, region = view3d()
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.screen.animation_cancel(restore_frame=False)
            finish()
            return None
    except Exception as error:   # Report and quit: a benchmark never hangs a slot.
        import traceback
        finish(traceback.format_exc())
        return None
    return 0.2


bpy.app.timers.register(tick, first_interval=2.0)
