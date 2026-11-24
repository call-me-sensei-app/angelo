# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout by hand in a real window: its packs run as Pack Islands' jobs, the interface keeps going, Esc stops it.

``angelo.uv_layout_run`` (what UV Layout's dialog starts, doc/angelo/uv_layout.rst) on generated plate kits with the
Quality packer (Blender's Concave shape, the slow one):

* a small kit lays out to the end: every island inside UDIM 1001, Pack Islands' job seen running, and this suite's
  own 50 ms timer kept firing all along (the longest gap between its ticks is recorded and must stay under 1 s);
* a larger kit with Search is stopped by Esc while a pack runs: the runner ends within 5 s and every UV is as it was.

Run through ``run_native_suites.py --gui --include test_blender_uv_layout_gui``; check ``uv-layout-gui.json`` for
``success`` and ``phase == "complete"``. No provider, account or network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import random
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy

assert not bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-uv-layout-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import uv_layout_ui  # noqa: E402

state = {"success": False, "checks": {}, "observed": {}, "phase": "setup"}
checks, observed = state["checks"], state["observed"]
TICK = 0.05
run = {}


def write():
    (OUT / "uv-layout-gui.json").write_text(json.dumps(state, indent=2))


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    area = max((a for a in window().screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == "WINDOW")


def plates(count, seed):
    """Flat irregular polygons (L shapes, triangles, notched panels): concave islands, slow to pack by shape."""
    rng = random.Random(seed)
    bm = bmesh.new()
    for k in range(count):
        s = 0.1 + rng.random() ** 2 * 1.2
        pts = ([(0, 0), (1, 0), (1, 0.35), (0.35, 0.35), (0.35, 1), (0, 1)] if k % 3 == 0 else
               [(0, 0), (1, 0), (rng.uniform(0.2, 0.8), rng.uniform(0.4, 1.0))] if k % 3 == 1 else
               [(0, 0), (1, 0), (1, 0.6), (0.6, 0.6), (0.5, 0.3), (0.4, 0.6), (0, 0.6)])
        ox, oy = rng.uniform(-8, 8), rng.uniform(-8, 8)
        bm.faces.new([bm.verts.new((ox + x * s * rng.uniform(0.7, 1.3), oy + y * s, k * 0.01)) for x, y in pts])
    mesh = bpy.data.meshes.new("Plates")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Plates", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def fresh(count, seed):
    with bpy.context.temp_override(window=window()):
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.batch_remove(tuple(bpy.data.objects))
    obj = plates(count, seed)
    for other in bpy.context.scene.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj
    return obj


def uvs(obj):
    """Every face corner's UV as the layout leaves it: read through BMesh in Edit Mode, where the layout writes (the
    mesh's own UV data lags until Edit Mode ends)."""
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        layer = bm.loops.layers.uv.active
        return [tuple(round(c, 6) for c in loop[layer].uv) for face in bm.faces for loop in face.loops]
    return [tuple(round(c, 6) for c in loop.uv) for loop in obj.data.uv_layers.active.data]


def running():
    return any(op.bl_idname == "ANGELO_OT_uv_layout_run" for op in window().modal_operators)


def start(obj, spec):
    area, region = view3d()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        result = bpy.ops.angelo.uv_layout_run("INVOKE_DEFAULT", spec=json.dumps(spec), objects=json.dumps([obj.name]))
    run.update(started=time.monotonic(), last=time.monotonic(), gap=0.0, job_seen=False, result=sorted(result))


def watch():
    """Each tick while the runner works: the longest gap between ticks, and whether Pack Islands' job ran."""
    from _bpy import _angelo_jobs
    now = time.monotonic()
    run["gap"] = max(run["gap"], now - run["last"])
    run["last"] = now
    if _angelo_jobs.progress(bpy.context.scene, "UV_PACK") is not None:
        run["job_seen"] = True


def tick():
    try:
        phase = state["phase"]
        if phase == "setup":
            bpy.context.preferences.view.show_splash = False
            if not hasattr(bpy.types, "ANGELO_OT_uv_layout_run"):
                bpy.utils.register_class(uv_layout_ui.ANGELO_OT_uv_layout_run)
            obj = fresh(24, 3)
            with bpy.context.temp_override(window=window()):
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.0)
            run["before"] = uvs(obj)
            start(obj, {"texture_size": 1024, "unwrap": "KEEP", "search": 0, "packer": "QUALITY"})
            state["phase"] = "complete_run"
            return TICK
        obj = bpy.data.objects["Plates"]
        if phase == "complete_run":
            if running():
                watch()
                return TICK
            observed["complete_run"] = {"seconds": round(time.monotonic() - run["started"], 2),
                                        "longest_gap_s": round(run["gap"], 3), "job_seen": run["job_seen"],
                                        "invoke": run["result"]}
            inside = all(-1e-4 <= c <= 1 + 1e-4 for uv in uvs(obj) for c in uv)
            packed = uvs(obj) != run["before"]   # A pack that never ran leaves the unwrap as it was.
            checks["a_layout_runs_to_the_end_inside_its_tile"] = inside and obj.mode == "EDIT" and packed
            checks["each_pack_runs_as_pack_islands_job"] = run["job_seen"]
            checks["the_interface_keeps_going_while_it_packs"] = run["gap"] < 1.0
            obj = fresh(90, 5)
            with bpy.context.temp_override(window=window()):
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.0)
            run["before"] = uvs(obj)
            start(obj, {"texture_size": 1024, "unwrap": "KEEP", "search": 32, "packer": "QUALITY"})
            state["phase"] = "stop_run"
            return TICK
        if phase == "stop_run":
            watch()
            if not run["job_seen"] and running() and time.monotonic() - run["started"] < 30:
                return TICK   # Esc once a pack is under way.
            window().event_simulate(type="ESC", value="PRESS")
            window().event_simulate(type="ESC", value="RELEASE")
            run["stopped_at"] = time.monotonic()
            state["phase"] = "stopping"
            return TICK
        if phase == "stopping":
            if running() and time.monotonic() - run["stopped_at"] < 30:
                return TICK
            took = time.monotonic() - run["stopped_at"]
            observed["stop_run"] = {"stop_seconds": round(took, 2), "job_seen": run["job_seen"]}
            checks["esc_stops_within_five_seconds"] = not running() and took < 5.0
            checks["a_stopped_layout_leaves_the_uvs_as_they_were"] = uvs(obj) == run["before"]
            state["success"] = all(checks.values())
            state["phase"] = "complete"
            write()
            print("ANGELO_UV_LAYOUT_GUI_OK" if state["success"] else "ANGELO_UV_LAYOUT_GUI_FAILED",
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
