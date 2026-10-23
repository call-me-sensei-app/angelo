# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An asset library added while a shelf loads its previews never crashes the app (1.0.5, PreviewLoadJob).

Evidence: preview lines on Part D's shelf (2026-10-23) crashed 3/3 on entering Sculpt mode. Part E's host added the
"Angelo Media Studio" library while Object mode's shelf (``media_shelf``, shown by default, every library) loaded
previews; the asset-list rebuild freed a PreviewImage the preview job was loading (a use-after-free in stock 5.2,
``IMB_thumb_path_unlock`` then SIGSEGV). The class fix is native (the job touches its preview only under its mutex);
this suite is its regression guard, on any binary.

Real default startup, Manual mode, Object mode (Object mode's shelf listing every library), a cold preview cache
(the runner's fresh home). For each brush shelf in ``ATTEMPTS`` (each with previews not loaded yet in this session): an
empty asset library is added to the Preferences, as Part E's host added its own, which re-lists every library; after
its delay (``ATTEMPTS``) that mode is entered, so its shelf's preview job starts while the re-listing may still
be finishing; then Object mode again. The original crash needed exactly this: a re-listing that finished under a
freshly started preview job. As in the session that crashed, an empty library is also added on a timer
``STARTUP_ADD`` seconds after startup (Part E's first host), and the interface language is set just before each
shelf shows. A slot repeats the suite in fresh sessions (see the race line). Checked: every attempt
completes (a crash ends the app, and the runner reports it); recorded: the main thread's longest stall per attempt
(the gap between 1 ms timer ticks; the reviewer's mutex-hold cost) and its 95th percentile.

Run through ``run_native_suites.py --gui --include test_blender_shelf_preview_race``; check
``shelf-preview-race.json`` for ``success`` and ``phase == "complete"``; a passing run's ``suite.log`` has no
``IMB_thumb_path_unlock`` line.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-shelf-preview-race-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "shelf-preview-race.json"
# (brush shelf, seconds from the library's addition to entering it): each shelf's previews are cold once per
# session, and the delays spread across the re-listing (the 1.0.4 crash hit at ~2.4 s); a slot repeats sessions.
ATTEMPTS = (('SCULPT', 0.3), ('TEXTURE_PAINT', 0.9), ('VERTEX_PAINT', 1.5), ('WEIGHT_PAINT', 2.2))
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "cycles": [], "cycle": 0}
checks = state["checks"]
started = time.monotonic()
_ticks = []          # perf_counter of each 1 ms timer tick while an attempt's previews load.


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    areas = [a for a in window().screen.areas if a.type == 'VIEW_3D']
    return max(areas, key=lambda a: a.width * a.height, default=None)


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def set_mode(mode):
    area = view3d()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=area, region=region):
        if mode != 'OBJECT' and (bpy.context.object is None or bpy.context.object.type != 'MESH'):
            bpy.ops.mesh.primitive_cube_add()
        bpy.ops.object.mode_set(mode=mode)


def tick_clock():
    """A 1 ms timer: the gaps between its ticks are how long the main thread was busy."""
    if state.get("measuring"):
        _ticks.append(time.perf_counter())
        return 0.001
    return None


def stalls():
    gaps = sorted(b - a for a, b in zip(_ticks, _ticks[1:]))
    if not gaps:
        return {"max_ms": None, "p95_ms": None, "ticks": 0}
    return {"max_ms": round(gaps[-1] * 1000, 2), "p95_ms": round(gaps[int(0.95 * (len(gaps) - 1))] * 1000, 2),
            "ticks": len(_ticks)}


def finish(error=None):
    state["measuring"] = False
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_SHELF_PREVIEW_RACE", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    from angelo_agent import director
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 120 + 15 * len(ATTEMPTS), "Suite timed out"
        if phase == "boot":
            if not settled():
                return .3
            if director.is_director(window()):
                director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            if not (settled() and not director.is_director(window())):
                return .2
            space = view3d().spaces.active
            if not space.is_property_readonly("show_region_asset_shelf"):
                space.show_region_asset_shelf = True
            state["phase"] = "add"
            return .5
        if phase == "add":   # An empty library, as Part E's host added its own: every library is listed again.
            index = state["cycle"]
            folder = OUT / ("library-%02d" % index)
            folder.mkdir(exist_ok=True)
            _ticks.clear()
            state["measuring"] = True
            bpy.app.timers.register(tick_clock, first_interval=0.001)
            prefs.filepaths.asset_libraries.new(name="Race %02d" % index, directory=str(folder))
            view3d().tag_redraw()
            state["phase"], state["since"] = "enter", time.monotonic()
            return ATTEMPTS[state["cycle"]][1]
        if phase == "enter":   # A brush shelf not shown yet this session: its preview job starts now.
            # As the 1.0.4 crash's suite did right before Sculpt: the interface language is set (translated
            # interface on), which reloads the interface's text, catalogue names included.
            prefs.view.language, prefs.view.use_translate_interface = "en_US", True
            set_mode(ATTEMPTS[state["cycle"]][0])
            view3d().tag_redraw()
            state["phase"] = "back"
            return 2.5
        if phase == "back":
            set_mode('OBJECT')
            view3d().tag_redraw()
            state["phase"] = "settle"
            return 0.5
        if phase == "settle":
            state["measuring"] = False
            mode, delay = ATTEMPTS[state["cycle"]]
            record = {"attempt": state["cycle"], "mode": mode, "delay": delay,
                      "seconds": round(time.monotonic() - state["since"], 2), **stalls()}
            state["cycles"].append(record)
            state["cycle"] += 1
            if state["cycle"] < len(ATTEMPTS):
                state["phase"] = "add"
                return .2
            state["phase"] = "complete"
            checks["every_attempt_completed_without_a_crash"] = len(state["cycles"]) == len(ATTEMPTS)
            worst = [c["max_ms"] for c in state["cycles"] if c["max_ms"] is not None]
            state["stall_ms"] = {"max": max(worst) if worst else None,
                                 "p95_of_attempt_max": sorted(worst)[int(0.95 * (len(worst) - 1))] if worst else None}
            finish()
            return None
    except Exception as error:   # noqa: BLE001 (the suite reports any failure in its receipt)
        finish(error)
        return None
    return .2


STARTUP_ADD = 2.0   # Seconds after the suite starts: Part E's old host added its library on a timer this soon.


def startup_add():
    """An empty library added at startup on a timer, as Part E's 1.0.5 host first did: every library is re-listed
    while Object mode's shelf (shown by default) loads its first previews; then the first brush shelf shows."""
    folder = OUT / "library-startup"
    folder.mkdir(exist_ok=True)
    prefs.filepaths.asset_libraries.new(name="Race startup", directory=str(folder))
    state["startup_added_at"] = round(time.monotonic() - started, 2)
    return None


bpy.app.timers.register(startup_add, first_interval=STARTUP_ADD)
bpy.app.timers.register(tick, first_interval=.5)
