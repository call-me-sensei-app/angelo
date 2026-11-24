# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The 2.1.0 bake additions end to end in the running app (doc/angelo/long_jobs.rst: "For an engine", "Measured
distances", "Bake check").

GUI, a stand-in conversation and no provider, account or network. A high-to-low ``BAKE_MAPS`` job for Unreal starts
through the agent's own ``angelo_job_start`` (precondition, then execute, as approval runs it) from a low sphere onto
which a larger high sphere with a procedural material bakes, with the scene's bake settings still Blender's defaults.
The job ends done; its files are Unreal's packing (an ORM map and a DirectX normal); its receipt's facts carry the
measured distances (every point covered, an extrusion that reaches the high sphere) and the bake check (both pictures
cover the same pixels, a colour difference within what a person tells apart, the worst tile), and the baked material;
the person's bake settings, materials, Render Result and data are as they were, and the check left nothing behind;
the main thread never stalled half a second while the job ran. Writes bake-delta-job.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-bake-delta-job-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS = OUT / "workspace"
WS.mkdir(exist_ok=True)
from angelo_agent import ui  # noqa: E402  (bundled add-on path)
from angelo_agent import job_start_wait  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import bake_maps_spec, job_ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

REPORT = OUT / "bake-delta-job.json"
SAME_LOOK_DELTA_E = 6.0   # Mean CIE76 a 256 px bake of a smooth procedural sphere stays within (2: just noticeable).
state = {"phase": "boot", "success": False, "checks": {}, "measured": {},
         "fixture": "A real Cycles CPU high-to-low bake at 256 px; stand-in conversation, no provider or account"}
ctx = {}
started = time.monotonic()
ticks = {"last": None, "longest": 0.0}
controller = SimpleNamespace(files=FileWorkspace(WS), state={"id": "suite-chat"},
                             executing_call=SimpleNamespace(id="call-1"))


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    value = BlenderExecutor(window())
    value.studio_context = controller
    return value


def check(name, value):
    state["checks"][name] = bool(value)


def save():
    REPORT.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")


def finish(error=None):
    if error:
        state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["failed_checks"] = [name for name, passed in state["checks"].items() if not passed]
    save()
    print("ANGELO_BAKE_DELTA_JOB", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def build_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=24, ring_count=12)
    low = bpy.context.active_object
    low.name = "Low"
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.04, segments=96, ring_count=48)
    high = bpy.context.active_object
    high.name = "High"
    bpy.ops.object.shade_smooth()
    material = bpy.data.materials.new("Painted")
    tree = material.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 2.0
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.55, 0.2, 0.08, 1.0)
    ramp.color_ramp.elements[1].color = (0.15, 0.35, 0.6, 1.0)
    tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    bsdf = tree.nodes["Principled BSDF"]
    tree.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.6
    high.data.materials.append(material)
    low.data.materials.append(bpy.data.materials.new("LowPlaceholder"))
    bake = scene.render.bake
    bake.cage_extrusion, bake.max_ray_distance = 0.0, 0.0   # Blender's defaults: the distances are measured.
    return low, high


def ids():
    return {kind: sorted(block.name for block in getattr(bpy.data, kind))
            for kind in ("scenes", "objects", "cameras", "lights", "worlds", "meshes")}


def step():
    phase = state["phase"]
    now = time.monotonic()
    if ticks["last"] is not None and phase == "running":
        ticks["longest"] = max(ticks["longest"], now - ticks["last"])
    ticks["last"] = now
    if now - started > 540:
        raise AssertionError("Suite timed out in phase %s" % phase)
    if phase == "boot":
        low, high = build_scene()
        ctx["low"], ctx["high"] = low.name, high.name
        ctx["ids"] = ids()
        ctx["materials"] = {obj.name: [slot.material.name for slot in obj.material_slots] for obj in (low, high)}
        ctx["render_results"] = sorted(i.name for i in bpy.data.images if i.type == "RENDER_RESULT")
        arguments = {"kind": "BAKE_MAPS", "target": object_handle(low), "sources": [object_handle(high)],
                     "for_engine": "UNREAL", "resolution": [256, 256], "material": True, "output": "bakes/delta"}
        # Each call gets its own arguments, as the conversation decodes them from the model's JSON each time.
        executor().precondition("angelo_job_start", json.loads(json.dumps(arguments)))
        result = job_start_wait.started(executor().execute("angelo_job_start", json.loads(json.dumps(arguments))))
        ctx["job"] = result.get("job")
        check("the_job_starts", result.get("status") in {"queued", "starting", "running", "done"})
        state["phase"] = "running"
        ctx["started_at"] = time.monotonic()
        return 0.05
    if phase == "running":
        record = job_ui.manager().record(ctx["job"])
        if record["status"] not in {"done", "failed", "cancelled", "interrupted"}:
            return 0.05
        state["measured"]["job_seconds"] = round(time.monotonic() - ctx["started_at"], 2)
        state["measured"]["longest_main_thread_gap_s"] = round(ticks["longest"], 3)
        state["measured"]["record"] = {key: record[key] for key in ("status", "reason", "receipt")}
        state["measured"]["poll_error"] = record["detail"].get("poll_error")
        verify(record)
        finish()
        return None
    return 0.1


def verify(record):
    receipt = record["receipt"]
    check("the_job_is_done", record["status"] == "done")
    maps = sorted(entry["map"] for entry in receipt.get("outputs", []))
    state["measured"]["maps"] = maps
    check("unreal_packing_files", {bake_maps_spec.BASE_NAMES["UNREAL"], bake_maps_spec.PACKED_NAMES["UNREAL"]}
          <= set(maps) and any("Normal" in name for name in maps))
    facts = receipt.get("facts", {}).get(ctx["low"], {})
    distances = facts.get("measured_distances", {})
    check("distances_measured_and_used", distances.get("covered_share") == 1.0
          and distances.get("extrusion", 0.0) >= 0.04 and facts.get("extrusion") == distances.get("extrusion"))
    bake_check = facts.get("bake_check", {})
    state["measured"]["bake_check"] = bake_check
    check("bake_check_in_the_receipt", {"delta_e_mean", "delta_e_p95", "only_one_share", "worst"} <= set(bake_check))
    check("bake_check_same_look", bake_check.get("delta_e_mean", 99.0) <= SAME_LOOK_DELTA_E
          and bake_check.get("only_one_share", 1.0) <= 0.1)
    check("baked_material_made", any(isinstance(name, str) for name in receipt.get("materials", [])))
    low, high = bpy.data.objects[ctx["low"]], bpy.data.objects[ctx["high"]]
    materials = {obj.name: [slot.material.name for slot in obj.material_slots] for obj in (low, high)}
    check("persons_materials_unchanged", materials == ctx["materials"])
    bake = bpy.context.scene.render.bake
    check("persons_bake_settings_unchanged", bake.cage_extrusion == 0.0 and bake.max_ray_distance == 0.0)
    check("check_left_nothing", ids() == ctx["ids"] and sorted(
        i.name for i in bpy.data.images if i.type == "RENDER_RESULT") == ctx["render_results"])
    check("main_thread_never_stalled_half_a_second", ticks["longest"] < 0.5)


def tick():
    try:
        return step()
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(qa_boot.gated(tick), first_interval=1.0, persistent=True)
