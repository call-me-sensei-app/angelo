# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stale-frame watch sees a picture gone stale, and only that (``qa_stale_frames``).

The watch is the audit's instrument for "a state changed and nothing asked for a redraw": it must report such a
change wherever it happens, and stay silent for a region that is at rest or that was asked to redraw. Proved on a
state this suite owns, so no product fault is needed: a square the suite draws in the chat's main region, whose
colour is a variable here.

Real default startup (Director, the chat beside the 3D view); no input, CLI, account, network or inference.
1. The window rests: the watch asks each rested chat region to draw again, several times; no stale frame.
2. The colour changes and nothing asks for a redraw: the watch reports a stale frame in the chat's main region, its
   box inside the square, with the two pictures saved.
3. The colour changes and the area is asked to redraw: no further stale frame.

Run through ``run_native_suites.py --gui --include test_blender_stale_frames``; check ``stale-frames-proof.json``
for ``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
from angelo_agent import console  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_stale_frames as watch  # noqa: E402
import qa_waits  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-stale-frames-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "stale-frames-proof.json"
SQUARE = (40, 60, 120, 140)    # x0, y0, x1, y1 in the chat's main region
COLOURS = ((0.9, 0.1, 0.1, 1.0), (0.1, 0.8, 0.2, 1.0), (0.1, 0.2, 0.9, 1.0))
PROBES = 3                     # Times the rested chat is asked to draw again before a verdict on "at rest".
state = {"phase": "boot", "success": False, "checks": {}, "colour": 0}
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat_area():
    return next((area for area in window().screen.areas if console.is_chat(area)), None)


def square():
    """A draw handler on the chat's main region: this suite's own state, drawn."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    if bpy.context.area is None or not console.is_chat(bpy.context.area):
        return
    x0, y0, x1, y1 = SQUARE
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]},
                             indices=[(0, 1, 2), (0, 2, 3)])
    gpu.state.blend_set('NONE')
    shader.bind()
    shader.uniform_float("color", COLOURS[state["colour"]])
    batch.draw(shader)


def stale():
    """The watch's stale frames in the chat's main region."""
    return [event for event in watch._events if event["editor"] == chat_area().ui_type and event["region"] == "WINDOW"]


def inside(box):
    x0, y0, x1, y1 = SQUARE
    return x0 <= box[0] and y0 <= box[1] and box[2] <= x1 and box[3] <= y1


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    state["stale_frames"] = [{key: value for key, value in event.items() if key != "_pictures"} for event in watch._events]
    state["watch"] = {"draws_seen": watch._state["draws"], "regions_asked_to_draw_again": watch._state["probes"],
                      "errors": watch._state["errors"]}
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STALE_FRAMES", json.dumps({"success": state["success"], "phase": state["phase"],
                                             "failed": state["failed_checks"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit * qa_waits.timeout_scale():
        raise AssertionError("Timed out in %s" % phase)
    return False


def tick():
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 200 * qa_waits.timeout_scale(), "Suite timed out"
        if phase == "boot":
            if not wait(phase, lambda: qa_boot.started() and chat_area() is not None, 60):
                return .3
            bpy.types.SpaceConsole.draw_handler_add(square, (), 'WINDOW', 'POST_PIXEL')
            checks["the_watch_started"] = watch.watch("CONSOLE", str(OUT)) or watch._state["started"] is not None
            chat_area().tag_redraw()
            state["phase"], state["probes_at"] = "rest", None
            return 1.0
        if phase == "rest":
            # The start-up's own late draws are over once the watch has asked a few times without a draw between.
            if state["probes_at"] is None:
                state["probes_at"], state["stale_at_rest_from"] = watch._state["probes"], len(watch._events)
            if not wait(phase, lambda: watch._state["probes"] >= state["probes_at"] + PROBES * 2, 60):
                return .3
            # Angelo's start-up may still have changed the chat before it rested: only the last PROBES count.
            if state.get("rest_round") is None:
                state["rest_round"], state["probes_at"] = 1, watch._state["probes"]
                state["stale_at_rest_from"] = len(watch._events)
                return .3
            state["stale_at_rest"] = [e for e in watch._events[state["stale_at_rest_from"]:]]
            checks["a_rested_chat_is_asked_to_draw_again"] = watch._state["probes"] > state["probes_at"]
            checks["a_rested_chat_shows_no_stale_frame"] = not state["stale_at_rest"]
            state["colour"], state["before_silent"] = 1, len(stale())   # Changed; nothing asks for a redraw.
            state["phase"] = "silent"
            return .3
        if phase == "silent":
            if not wait(phase, lambda: len(stale()) > state["before_silent"], 15):
                return .3
            found = stale()[state["before_silent"]:]
            checks["a_silent_change_is_reported_once"] = len(found) == 1
            checks["the_report_points_at_what_changed"] = inside(found[0]["box"])
            state["phase"], state["silent_at"] = "pictures", time.monotonic()
            return .5
        if phase == "pictures":
            found = stale()[state["before_silent"]]
            if not wait(phase, lambda: bool(found.get("pictures")), 10):
                return .3
            checks["both_pictures_are_saved"] = all((OUT / name).stat().st_size > 60 for name in found["pictures"])
            state["colour"], state["before_asked"] = 2, len(stale())   # Changed, and the area is asked to redraw.
            chat_area().tag_redraw()
            state["phase"], state["asked_at"], state["probes_at"] = "asked", time.monotonic(), watch._state["probes"]
            return .3
        if phase == "asked":
            if not wait(phase, lambda: watch._state["probes"] >= state["probes_at"] + PROBES, 30):
                return .3
            checks["a_change_that_asked_for_its_redraw_is_not_reported"] = len(stale()) == state["before_asked"]
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .3


bpy.app.timers.register(qa_boot.gated(tick), first_interval=.5, persistent=True)
