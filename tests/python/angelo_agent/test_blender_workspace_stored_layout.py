# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A workspace switch applied before a duplicated workspace's own never takes the copy's stored layout (1.0.5).

``ED_workspace_duplicate`` stores the copy's layout in the window's ``temp_layout_store`` for the copy's switch. A
switch queued earlier in the same event-loop pass (here: to the workspace already shown, as Angelo's tab build did)
used to take that layout for the wrong workspace: ``Couldn't find layout in this workspace: 'Layout' screen:
'Layout.001'`` and a null layout dereferenced in ``ED_workspace_change`` (part E's GUI suite crashed 2.5 s into
startup). ``workspace_edit.cc`` now gives the stored layout only to the workspace that owns it, and keeps it for that
one.

Real default startup, no input, CLI, account, network or inference. In one timer step the window is set to the
workspace it shows and ``workspace.duplicate`` runs; Blender then applies both queued switches in one pass. On a
build without the guard this crashes (the runner reports no report); with it, the window ends on the copy, on one of
the copy's own screens, and the original keeps its screens.

Run through ``run_native_suites.py --gui --include test_blender_workspace_stored_layout``; check
``workspace-stored-layout.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-workspace-stored-layout-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "workspace-stored-layout.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}}
checks = state["checks"]


def window():
    return bpy.context.window_manager.windows[0]


def settled():
    """Angelo's own start-up layout changes are over (Director's, the default layout's, Manual's tab build)."""
    return qa_boot.started()


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_WORKSPACE_STORED_LAYOUT", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        phase = state["phase"]
        since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
        if phase == "boot":
            if not settled() and time.monotonic() - since < 30:
                return .3
            state["phase"] = "act"
            return .5
        if phase == "act":
            w = window()
            shown = w.workspace
            state["shown"] = shown.name
            state["shown_screens"] = sorted(s.name for s in shown.screens)
            before = {ws.as_pointer() for ws in bpy.data.workspaces}
            w.workspace = shown   # A switch to the workspace already shown, queued (rna_Window_workspace_update).
            with bpy.context.temp_override(window=w):
                bpy.ops.workspace.duplicate()   # Stores the copy's layout; queues the copy's own switch.
            copy = next((ws for ws in bpy.data.workspaces if ws.as_pointer() not in before), None)
            if copy is None:
                raise AssertionError("workspace.duplicate added no workspace")
            state["copy"] = copy.name
            state["phase"] = "check"
            return .2
        if phase == "check":   # Both switches applied in the pass after "act" (a build without the guard crashed).
            w = window()
            copy = bpy.data.workspaces.get(state["copy"])
            if (copy is None or w.workspace != copy) and time.monotonic() - since < 5:
                return .1
            shown = bpy.data.workspaces.get(state["shown"])
            checks["the_copy_is_shown"] = copy is not None and w.workspace == copy
            checks["the_window_shows_one_of_the_copys_screens"] = copy is not None and any(
                s == w.screen for s in copy.screens)
            checks["the_original_keeps_its_screens"] = shown is not None and sorted(
                s.name for s in shown.screens) == state["shown_screens"]
            state["window_screen"] = w.screen.name
            if shown is not None:
                w.workspace = shown
            state["phase"] = "cleanup"
            return .3
        if phase == "cleanup":
            copy = bpy.data.workspaces.get(state["copy"])
            if copy is not None and window().workspace != copy:
                bpy.data.batch_remove(ids=(copy,))   # bpy.data.workspaces has no remove().
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:   # noqa: BLE001 - reported, then quit.
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
