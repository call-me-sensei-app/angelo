# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""GPU subdivision status (Blender #58191) in a real window: the panel and the viewport.

A cube with a level-2 Subdivision Surface modifier, shown in the Properties editor's Modifiers tab:

* plain: the status is GPU and the panel shows no reason;
* Shade Auto Smooth (Smooth by Angle pinned after it): NOT_LAST, and the panel names that modifier;
* Smooth by Angle moved before it: SPLIT_NORMALS, and the panel says sharp edges were detected.

Screenshots of the three panels are checked by eye. For each state the suite also records the time
to move one vertex and redraw the window (the cost the status explains), as observed numbers only:
compare them across runs only on a quiet machine.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import statistics
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-gpu-subdiv-qa"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"success": False, "checks": {}, "observed": {}, "phase": "setup", "started": time.monotonic()}
checks, observed = state["checks"], state["observed"]
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.system.use_gpu_subdivision = True


def write():
    (OUT / "gpu-subdivision-status-gui.json").write_text(json.dumps(state, indent=2))


def window():
    return bpy.context.window_manager.windows[0]


def largest(kind):
    return max((a for a in window().screen.areas if a.type == kind), key=lambda a: a.width * a.height)


def capture(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state.setdefault("screenshots", []).append(name)


def setup():
    bpy.data.batch_remove(tuple(bpy.data.objects))
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=20, use_grid_fill=True)
    mesh = bpy.data.meshes.new("Status")
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new("Status", mesh)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    ob.modifiers.new("Subdivision", 'SUBSURF').levels = 2
    properties = [a for a in window().screen.areas if a.type == 'PROPERTIES']
    if not properties:
        # A layout without a Properties editor: show it in the largest area that is not the 3D view.
        spare = max((a for a in window().screen.areas if a.type != 'VIEW_3D'), key=lambda a: a.width * a.height)
        spare.ui_type = 'PROPERTIES'
        properties = [spare]
    observed["properties_area"] = {"width": properties[0].width, "height": properties[0].height}
    properties[0].spaces.active.context = 'MODIFIER'
    view = largest('VIEW_3D')
    with bpy.context.temp_override(window=window(), area=view,
                                   region=next(r for r in view.regions if r.type == 'WINDOW')):
        bpy.ops.view3d.view_all()
    return ob


def edit_and_redraw_ms(ob, frames=20):
    """Median time to move one vertex and redraw the window (re-evaluation plus draw)."""
    samples = []
    vertex = ob.data.vertices[0]
    for index in range(frames):
        vertex.co.x += 0.001 if index % 2 == 0 else -0.001
        ob.data.update()
        start = time.perf_counter()
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples)


def record(ob, name):
    status = ob.modifiers["Subdivision"].gpu_subdivision_status
    observed[name] = {"status": status, "stack": [m.name for m in ob.modifiers],
                      "edit_and_redraw_ms": edit_and_redraw_ms(ob)}
    capture(f"gpu-subdivision-{name}.png")
    return status


def tick():
    try:
        phase = state["phase"]
        if phase == "setup":
            setup()
            state["phase"] = "plain"
            return 1.0
        ob = bpy.data.objects["Status"]
        if phase == "plain":
            checks["plain_reads_gpu"] = record(ob, "plain") == 'GPU'
            with bpy.context.temp_override(window=window()):
                bpy.ops.object.shade_auto_smooth()
            state["phase"] = "smooth_by_angle_after"
            return 1.0
        if phase == "smooth_by_angle_after":
            checks["smooth_by_angle_after_reads_not_last"] = record(ob, "smooth_by_angle_after") == 'NOT_LAST'
            state["phase"] = "translated"
            return 1.0
        if phase == "translated":
            # The status wraps in a narrow panel in every language: one with long words, one without spaces.
            view = bpy.context.preferences.view
            if view.language != "en_US":
                capture(f"gpu-subdivision-not_last-{view.language}.png")
            pending = state.setdefault("languages", ["de_DE", "ja_JP"])
            if pending:
                view.use_translate_reports = True
                view.language = pending.pop(0)
                return 1.0  # The panel animates to its new height.
            view.language = "en_US"
            smooth = ob.modifiers[-1]
            smooth.use_pin_to_last = False
            with bpy.context.temp_override(window=window(), object=ob):
                bpy.ops.object.modifier_move_to_index(modifier=smooth.name, index=0)
            state["phase"] = "smooth_by_angle_before"
            return 1.0
        if phase == "smooth_by_angle_before":
            checks["smooth_by_angle_before_reads_split_normals"] = (
                record(ob, "smooth_by_angle_before") == 'SPLIT_NORMALS')
            state["success"] = all(checks.values())
            state["phase"] = "complete"
            write()
            print("ANGELO_GPU_SUBDIV_GUI_OK" if state["success"] else "ANGELO_GPU_SUBDIV_GUI_FAILED",
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


bpy.app.timers.register(tick, first_interval=1)
