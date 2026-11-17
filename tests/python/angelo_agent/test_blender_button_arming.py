# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's own buttons arm for a grant ticket only while the person's move put the pointer on them where they are
drawn now (doc/angelo/grant_operators.rst; the reviewer's read of 6392a6f).

A grant operator's or guarded property's button mints its C ticket only for a press after a real move onto it,
``ARM_SECONDS`` after it was drawn there. Its arming must not outlive what it rests on: leaving the button disarms
it, and a button moved on screen (a scrolled sidebar or Preferences, a re-flow) starts again where it now is, so a
press after it slid under a resting pointer grants nothing.

Any of Blender's buttons keeps this bookkeeping (``_angelo_jobs.button_arming``, a test seam), so the suite uses one in
the Preferences window: armed by a real move, disarmed by leaving, armed again, then disarmed when the region scrolls
under the still pointer. Synthetic events in a factory window; no provider, account or network. Run through
``run_native_suites.py --gui --include test_blender_button_arming``; ``button-arming.json`` has ``success`` and
``phase == "complete"``.
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

assert not bpy.app.background, "The button arming suite needs a window"
import qa_waits  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-button-arming-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "details": {}}
STARTED = time.monotonic()
LIMIT = 120.0 * qa_waits.timeout_scale()
ARM = 0.6   # Past C's ARM_SECONDS (0.3) with room.
WAITING = {}
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False


def check(name, condition, detail=None):
    REPORT["checks"][name] = bool(condition)
    if not condition and detail is not None:
        REPORT["details"][name] = str(detail)[:1500]


def after(label, seconds):
    return time.monotonic() - WAITING.setdefault(label, time.monotonic()) >= seconds


def prefs_parts():
    for window in bpy.context.window_manager.windows:
        area = next((a for a in window.screen.areas if a.type == 'PREFERENCES'), None)
        if area is not None:
            return window, area, next(r for r in area.regions if r.type == 'WINDOW')
    return None, None, None


def buttons(window, region):
    """(text, armed, x, y, w, h) of the buttons drawn in ``region`` of ``window``, as C's arming sees them."""
    from _bpy import _angelo_jobs
    return [(text, armed, x, y, w, h) for pointer, text, armed, x, y, w, h in _angelo_jobs.button_arming()
            if pointer == window.as_pointer() and text and w > 20 and h > 8
            and region.x <= x and x + w <= region.x + region.width
            and region.y <= y and y + h <= region.y + region.height]


def target():
    window, _area, region = prefs_parts()
    return next((b for b in buttons(window, region) if b[0] == REPORT["text"]), None)


def move(window, x, y):
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))


def tick():
    try:
        assert time.monotonic() - STARTED < LIMIT, "Timed out in " + REPORT["phase"]
        phase = REPORT["phase"]
        if phase == "boot":
            if not after("boot", 3.0):   # The factory window settles (this suite needs no Angelo start-up layout).
                return 0.2
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.screen.userpref_show(section='INTERFACE')
            REPORT["phase"] = "pick"
        elif phase == "pick":
            window, _area, region = prefs_parts()
            if window is None or not after("pick", 1.0):
                return 0.2
            drawn = buttons(window, region)
            middle = region.y + region.height / 2
            pick = min((b for b in drawn if b[0].strip()), key=lambda b: abs(b[3] + b[5] / 2 - middle), default=None)
            assert pick is not None, "no button in the Preferences region: %r" % drawn[:10]
            REPORT["text"], REPORT["at"] = pick[0], (pick[2] + pick[4] / 2, pick[3] + pick[5] / 2)
            move(window, REPORT["at"][0] - 1, REPORT["at"][1])   # Reaching it,
            REPORT["phase"] = "onto"
        elif phase in ("onto", "back_onto"):
            if not after(phase, ARM):
                return 0.1
            window, _area, _region = prefs_parts()
            move(window, *REPORT["at"])   # then the person's own move onto it, past ARM.
            REPORT["phase"] = phase + "_check"
        elif phase in ("onto_check", "back_onto_check"):
            if not after(phase, 0.4):
                return 0.1
            found = target()
            check("a_real_move_arms_it" if phase == "onto_check" else "a_move_back_arms_it_again",
                  found and found[1], found)
            window, _area, region = prefs_parts()
            if phase == "onto_check":
                move(window, region.x + 3, REPORT["at"][1])   # Off it, to the region's margin.
                REPORT["phase"] = "left"
            else:
                REPORT["before_scroll"] = found
                _window, area, region = prefs_parts()
                with bpy.context.temp_override(window=window, area=area, region=region):   # Code scrolls it.
                    for _ in range(3):
                        bpy.ops.view2d.scroll_down()
                REPORT["phase"] = "scrolled"
        elif phase == "left":
            if not after(phase, 0.4):
                return 0.1
            found = target()
            check("leaving_it_disarms_it", found and not found[1], found)
            window, _area, _region = prefs_parts()
            move(window, REPORT["at"][0] - 1, REPORT["at"][1])
            REPORT["phase"] = "back_onto"
        elif phase == "scrolled":
            if not after(phase, 0.5):
                return 0.1
            found, before = target(), REPORT["before_scroll"]
            moved = found is not None and (round(found[3]), round(found[2])) != (round(before[3]), round(before[2]))
            check("the_region_scrolled_it", moved, (before, found))
            check("moved_on_screen_it_is_disarmed", found is None or not found[1], found)
            return finish()
        return 0.1
    except Exception:   # noqa: BLE001 - the report says what failed.
        return finish(traceback.format_exc())


def finish(error=None):
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2))
    if error:
        REPORT["error"] = error
    (OUT / "button-arming.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_BUTTON_ARMING", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"],
                                              "checks": REPORT["checks"]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1.0)
