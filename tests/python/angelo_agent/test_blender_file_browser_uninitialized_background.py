# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In background mode no area is ever initialized or refreshed: an Asset Browser made there has neither its file list
(``SpaceFile.files``) nor its runtime data (``SpaceFile.runtime``). The Python entry points that read them unguarded
crashed Blender 5.2 with SIGSEGV (``activate_asset_by_id`` at once on the 1.0.6 rehearsal build 7ea7499b79b); each must
return, deferring an activation until the list reads, and the Delete poll must answer False. In a File Browser made the
same way, file operators that read the list refuse in their poll and a directory change keeps the directory.
``test_blender_file_browser_before_refresh`` covers the same class in a window; this runs where the release chain's
background suites run.

Run through ``run_native_suites.py --include test_blender_file_browser_uninitialized_background``; check
``file-browser-uninitialized-background.json`` for ``success``.
"""

import json
import os
import sys
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-file-browser-uninitialized-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "file-browser-uninitialized-background.json"
bpy.context.preferences.use_preferences_save = False
state = {"success": False, "checks": {}, "steps": []}
checks = state["checks"]


def step(name):
    state["steps"].append(name)
    print("file_browser_uninitialized step", name, flush=True)   # The last one printed names a crash.


try:
    assert bpy.app.background, "Background only: a window would initialize and refresh the browser"
    screen = next(s for s in bpy.data.screens if s.areas)
    area = max(screen.areas, key=lambda a: a.width * a.height)
    with bpy.context.temp_override(screen=screen, area=area):
        area.ui_type = 'ASSETS'
    space = area.spaces.active
    checks["the_area_is_an_asset_browser"] = space.type == 'FILE_BROWSER' and space.browse_mode == 'ASSETS'
    material = bpy.data.materials.new("Uninitialized Browser Probe")
    material.asset_mark()
    step("activate")
    space.activate_asset_by_id(id_to_activate=material, deferred=False)
    step("activate_deferred")
    space.activate_asset_by_id(id_to_activate=material, deferred=True)
    step("activate_relative_path")
    space.activate_file_by_relative_path(relative_path="Material/" + material.name)
    step("delete_poll")
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    with bpy.context.temp_override(screen=screen, area=area, region=region):
        checks["delete_polls_false_without_a_list"] = not bpy.ops.file.delete.poll()
    # A File Browser made the same way: operators that read the list refuse in their poll; one that only changes
    # the directory (through ED_file_change_dir) runs, and the first refresh reads the new directory.
    files_area = min(screen.areas, key=lambda a: a.width * a.height)
    with bpy.context.temp_override(screen=screen, area=files_area):
        files_area.ui_type = 'FILES'
    region = next((r for r in files_area.regions if r.type == 'WINDOW'), None)
    with bpy.context.temp_override(screen=screen, area=files_area, region=region):
        step("select_bookmark")
        bpy.ops.file.select_bookmark(dir=bpy.app.tempdir)
        params = files_area.spaces.active.params   # None while the browser has no parameters yet.
        state["files_params"] = None if params is None else params.directory.decode()
        checks["changing_directory_without_a_list_keeps_the_directory"] = params is None or (
            params.directory.decode().rstrip("/\\") == bpy.app.tempdir.rstrip("/\\"))
        for name in ("parent", "select_all", "execute", "rename", "select_walk"):
            step(name)
            operator = getattr(bpy.ops.file, name)
            checks["%s_polls_false_without_a_list" % name] = not operator.poll()
            try:
                operator()
            except RuntimeError:   # "poll() failed": the refusal, never a crash.
                pass
    checks["every_entry_point_returned"] = True
    state["success"] = all(checks.values())
except Exception:
    state["error"] = traceback.format_exc()
state["failed_checks"] = [name for name, ok in checks.items() if not ok]
REPORT.write_text(json.dumps(state, indent=2, default=str))
print("ANGELO_FILE_BROWSER_UNINITIALIZED", json.dumps({"success": state["success"]}), flush=True)
sys.exit(0 if state["success"] else 1)
