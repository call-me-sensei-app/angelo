# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Voxel remesh guard (Blender #77868): the dialogs of an interactive remesh, in a real window.

Sculpt Mode's Ctrl+R (the operator's invoke) on a 2 m cube:

* far too small a size: a refusal dialog naming a size that fits, whose default button is Cancel:
  Return keeps the mesh and its size;
* a size the estimate calls large on this computer (about 12 million vertices, or three quarters of
  the free memory where that is less): a "this can take a while" dialog whose default is Remesh:
  Escape keeps the mesh, Return remeshes near the estimate;
* a size that fits: remeshes at once, no dialog.

Screenshots of both dialogs are checked by eye.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy

# ANGELO_QA_REHEARSAL=1: the background rehearsal a new GUI suite gets before its first windowed slot: the same setup,
# the estimate calls that choose this computer's sizes and the dialog-free invoke run in background Blender; the
# dialogs, key presses and screenshots need a window and are listed instead.
REHEARSAL = os.environ.get("ANGELO_QA_REHEARSAL") == "1"
assert "--factory-startup" in sys.argv and (REHEARSAL or not bpy.app.background)
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-voxel-guard-qa"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"success": False, "checks": {}, "observed": {}, "phase": "setup", "started": time.monotonic(),
         "rehearsal": REHEARSAL, "skipped_in_rehearsal": []}
checks, observed = state["checks"], state["observed"]
bpy.context.preferences.view.show_splash = False


def write():
    (OUT / "voxel-remesh-guard-gui.json").write_text(json.dumps(state, indent=2))


def window_and_view():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def capture(name):
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state.setdefault("screenshots", []).append(name)


def press(key):
    window, area, region = window_and_view()
    x, y = region.x + region.width // 2, region.y + region.height // 2
    window.event_simulate(type=key, value='PRESS', x=x, y=y)
    window.event_simulate(type=key, value='RELEASE', x=x, y=y)


def sculpt_cube():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.batch_remove(tuple(bpy.data.objects))
    mesh = bpy.data.meshes.new("Guarded")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Guarded", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    window, area, region = window_and_view()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.object.mode_set(mode='SCULPT')
        if not REHEARSAL:  # Framing is for the screenshots.
            bpy.ops.view3d.view_all()
    return obj


def large_size(obj):
    """A size the estimate calls LARGE here: 12 million vertices, or 3/4 of the free memory if smaller."""
    def estimate(size):
        return obj.data.angelo_voxel_remesh_estimate(voxel_size=size)
    area = estimate(1.0)[0]
    one, two = estimate(math.sqrt(area / 1e6)), estimate(math.sqrt(area / 2e6))
    per_vertex, free = (two[1] - one[1]) / 1e6, one[2]
    fixed = one[1] - per_vertex * 1e6
    vertices = min(12e6, (.75 * free - fixed) / per_vertex)
    size = math.sqrt(area / vertices)
    observed["large_case"] = {"vertices": vertices, "free_bytes": free, "size": size, "estimate": estimate(size)[3]}
    return size, estimate(size)


def invoke():
    window, area, region = window_and_view()
    with bpy.context.temp_override(window=window, area=area, region=region):
        return sorted(bpy.ops.object.voxel_remesh('INVOKE_DEFAULT'))


def rehearse():
    """Everything the windowed run calls that a background Blender can: setup, the sizes, a dialog-free invoke."""
    obj = sculpt_cube()
    obj.data.remesh_voxel_size = .00001
    checks["too_large_estimate_is_too_large"] = obj.data.angelo_voxel_remesh_estimate(voxel_size=.00001)[3] == 'TOO_LARGE'
    size, predicted = large_size(obj)
    checks["the_large_case_is_large_here"] = predicted[3] == 'LARGE'
    obj.data.remesh_voxel_size = .02
    observed["fits_invoke"] = invoke()
    checks["a_fitting_size_remeshes_without_a_dialog"] = (observed["fits_invoke"] == ["FINISHED"]
                                                          and len(obj.data.vertices) > 8)
    state["skipped_in_rehearsal"] += ["refusal dialog and its Return", "large dialog, Escape and Return",
                                      "screenshots"]
    state["success"] = all(checks.values())
    state["phase"] = "complete"
    write()


def tick():
    try:
        phase = state["phase"]
        if phase == "setup":
            obj = sculpt_cube()
            obj.data.remesh_voxel_size = .00001
            observed["too_large_invoke"] = invoke()
            checks["too_large_opens_a_dialog"] = observed["too_large_invoke"] == ["RUNNING_MODAL"]
            state["phase"] = "too_large_shown"
            return 1.0
        if phase == "too_large_shown":
            capture("voxel-remesh-too-large-dialog.png")
            press('RET')  # Default button: Cancel.
            state["phase"] = "too_large_dismissed"
            return 1.0
        if phase == "too_large_dismissed":
            obj = bpy.data.objects["Guarded"]
            checks["return_on_the_refusal_keeps_the_mesh"] = (len(obj.data.vertices) == 8
                                                              and abs(obj.data.remesh_voxel_size - .00001) < 1e-9)
            size, predicted = large_size(obj)
            state["large_vertices"] = predicted[0]
            checks["the_large_case_is_large_here"] = predicted[3] == 'LARGE'
            obj.data.remesh_voxel_size = size
            observed["large_invoke"] = invoke()
            checks["large_opens_a_dialog"] = observed["large_invoke"] == ["RUNNING_MODAL"]
            state["phase"] = "large_shown"
            write()
            return 1.0
        if phase == "large_shown":
            capture("voxel-remesh-large-dialog.png")
            press('ESC')
            state["phase"] = "large_escaped"
            return 1.0
        if phase == "large_escaped":
            obj = bpy.data.objects["Guarded"]
            checks["escape_on_the_large_dialog_keeps_the_mesh"] = len(obj.data.vertices) == 8
            invoke()
            state["phase"] = "large_reshown"
            return 1.0
        if phase == "large_reshown":
            state["remesh_started"] = time.monotonic()
            press('RET')  # Default button: Remesh.
            state["phase"] = "large_running"
            return 2.0
        if phase == "large_running":
            obj = bpy.data.objects["Guarded"]
            if len(obj.data.vertices) == 8 and time.monotonic() - state["remesh_started"] < 240:
                return 2.0
            observed["large_result_vertices"] = len(obj.data.vertices)
            checks["return_on_the_large_dialog_remeshes_near_the_estimate"] = (
                abs(len(obj.data.vertices) - state["large_vertices"]) < .05 * state["large_vertices"])
            checks["still_in_sculpt_mode"] = obj.mode == 'SCULPT'
            obj = sculpt_cube()
            obj.data.remesh_voxel_size = .02
            observed["fits_invoke"] = invoke()
            checks["a_fitting_size_remeshes_without_a_dialog"] = (observed["fits_invoke"] == ["FINISHED"]
                                                                  and len(obj.data.vertices) > 8)
            state["success"] = all(checks.values())
            state["phase"] = "complete"
            write()
            print("ANGELO_VOXEL_GUARD_GUI_OK" if state["success"] else "ANGELO_VOXEL_GUARD_GUI_FAILED",
                  json.dumps(sorted(k for k, v in checks.items() if not v)), flush=True)
            if os.environ.get("ANGELO_QA_KEEP_OPEN") != "1":
                bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        write()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


if REHEARSAL:
    rehearse()
    assert state["success"], sorted(k for k, v in checks.items() if not v)
else:
    bpy.app.timers.register(tick, first_interval=1)
