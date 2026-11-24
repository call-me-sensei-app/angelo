# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every first-hour case grades what it claims to (``eval_firsthour_tasks``), in the real window, with no model.

For each case, from Blender's factory scene: the prepared state (the person's problem) fails its checks; the case's
reference fix passes them; a report the case says Blender showed is in the Info log. So a before/after measurement
can only move because the agent changed the cause, never because a check was wrong.

Run through ``run_native_suites.py --gui --include test_blender_firsthour_cases``; check ``firsthour-cases.json``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import time
import traceback
from pathlib import Path

import bpy

import eval_firsthour_tasks as cases  # noqa: E402
import eval_reports  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-firsthour-cases"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "firsthour-cases.json"
REPORTED = {"firsthour_laptop_gpu_error": "System is out of GPU memory",
            "firsthour_no_camera": "No camera found in scene"}
state = {"phase": "early", "success": False, "checks": {}, "cases": {}}
started = time.monotonic()


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
        state["failed_checks"] = [name for name, value in state["checks"].items() if not value]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    state.pop("scenario", None)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_FIRSTHOUR_CASES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def scenario():
    checks = state["checks"]
    checks["dev_and_heldout_are_disjoint_and_both_present"] = (
        {c["id"] for c in cases.cases("firsthour-dev")}.isdisjoint(c["id"] for c in cases.cases("firsthour-heldout"))
        and len(cases.cases("firsthour-dev")) >= 2 and len(cases.cases("firsthour-heldout")) >= 8)
    for entry in cases.CASES:   # A case that raises is recorded and the next one runs, so one run shows them all.
        case_id = entry["id"]
        window = bpy.context.window_manager.windows[0]
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.read_homefile()   # Blender's factory scene (the suite runs with --factory-startup).
        yield
        yield
        record = state["cases"][case_id] = {}
        try:
            record["why"] = cases.prepare(bpy, case_id)
            yield
            before, record["before"] = cases.grade(bpy, case_id)
            checks[case_id + ":prepared_state_fails"] = not before
            if case_id in REPORTED:
                log = eval_reports.info_log(bpy)
                checks[case_id + ":report_in_info_log"] = REPORTED[case_id] in log
            entry["reference_fix"](bpy)
            bpy.context.view_layer.update()
            yield
            after, record["after"] = cases.grade(bpy, case_id)
            checks[case_id + ":reference_fix_passes"] = after
        except Exception as error:   # noqa: BLE001 - the case's own failure, reported with its traceback.
            record["error"], record["traceback"] = "%s: %s" % (type(error).__name__, error), traceback.format_exc()
            checks[case_id + ":runs_without_error"] = False


def tick():
    try:
        assert time.monotonic() - started < 240, "The fixture timed out"   # Under the runner's 300 s: a report.
        if state["phase"] == "early":
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
    bpy.app.timers.register(tick, first_interval=2.0, persistent=True)   # Each case reloads the factory scene.
