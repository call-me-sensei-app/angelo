# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``bench_floor_launch.py``: when a launch is ready, read this process's counters, then quit.

Started with ``--python`` by the driver, in Angelo and in stock Blender alike,
so both sides define "ready" the same way:

* ``headless`` (``--background``): when this script runs, which Blender does
  after its startup (preferences, add-ons, startup file).
* ``window``: the first frame. A draw callback on every editor of the startup
  screen fires during the first draw; it schedules a timer, which runs on the
  next main-loop turn, after that frame was swapped. ``--idle-s`` later the
  counters are read again, to show work the app still did after its first
  frame (deferred startup), then the app quits.

Counters are process-wide since the process started (``proc_pid_rusage``: every
thread, kernel included). Wall time is ``time.monotonic_ns`` here minus the
driver's reading just before it started the process (one clock for both).

``--save-userpref`` saves the preferences first (the driver's setup launch for
a returning person). ``floor_addon_trace`` (a user startup script the driver
installs for trace launches) reports each add-on enable and disable.
"""

import json
import os
from pathlib import Path
import sys
import time

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_counters  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _arg(name, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


OUT, MODE, IDLE_S = Path(_arg("--out")), _arg("--mode"), float(_arg("--idle-s", "5"))
report = {"mode": MODE, "binary": bpy.app.binary_path, "version": bpy.app.version_string,
          "build_hash": bpy.app.build_hash.decode("utf-8", "replace"), "angelo": "angelo_agent" in sys.modules,
          "addons": sorted(addon.module for addon in bpy.context.preferences.addons)}


def reading():
    counts = perf_counters.read_rusage() or {}
    return {"monotonic_ns": time.monotonic_ns(), "instructions": counts.get("instructions"),
            "cycles": counts.get("cycles"), "pageins": counts.get("pageins"), "load_1m": os.getloadavg()[0]}


def finish():
    trace = sys.modules.get("floor_addon_trace")
    report["addon_trace"] = trace.calls if trace is not None else None
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("FLOOR_LAUNCH_PROBE_DONE", flush=True)


if "--save-userpref" in ARGS:
    bpy.ops.wm.save_userpref()

if MODE == "headless":
    report["ready"] = reading()
    finish()
else:
    handles = []

    def after_idle():
        report["idle"] = reading()
        finish()
        bpy.context.preferences.view.use_save_prompt = False   # Quit without a dialog on either side.
        bpy.ops.wm.quit_blender()

    def after_first_frame():
        report["ready"] = reading()
        bpy.app.timers.register(after_idle, first_interval=IDLE_S)

    def first_draw():
        if handles:
            for space, handle in handles:
                space.draw_handler_remove(handle, "WINDOW")
            handles.clear()
            bpy.app.timers.register(after_first_frame, first_interval=0)

    spaces = {type(area.spaces.active) for window in bpy.context.window_manager.windows
              for area in window.screen.areas if area.spaces.active is not None}
    for space in sorted(spaces, key=lambda value: value.__name__):
        if hasattr(space, "draw_handler_add"):
            handles.append((space, space.draw_handler_add(first_draw, (), "WINDOW", "POST_PIXEL")))
    report["watched_editors"] = sorted(space.__name__ for space, _ in handles)
    if not handles:
        report["error"] = "no editor to watch in the startup screen"
        finish()
        bpy.ops.wm.quit_blender()
