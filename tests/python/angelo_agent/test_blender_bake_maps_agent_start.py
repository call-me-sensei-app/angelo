# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's own BAKE_MAPS start, end to end in the running app (doc/angelo/long_jobs.rst, "Texture sets").

GUI, a stand-in conversation and no provider, account or network. A ``BAKE_MAPS`` job starts through the agent's
``angelo_job_start`` exactly as approval runs it (precondition, then execute, each with the arguments as the model
wrote them): the executor and the job host each validate what the one before returned, so the request's canonical
form must be one the agent's schema takes (from 1.0.10 to the 2.0.0 candidate it was not, and every agent start was
refused as an unexpected tool argument; test_native_job_request_canonical guards every kind). The job ends done with
the ORM packing's files and a baked material; the person's materials are unchanged and the main thread never stalled
half a second. Writes bake-maps-agent-start.json under ANGELO_QA_DIR.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-bake-maps-agent-start-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS = OUT / "workspace"
WS.mkdir(exist_ok=True)
from angelo_agent import ui  # noqa: E402  (bundled add-on path)
from angelo_agent import job_start_wait  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import job_ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

REPORT = OUT / "bake-maps-agent-start.json"
state = {"phase": "boot", "success": False, "checks": {}, "measured": {},
         "fixture": "A real Cycles CPU bake at 128 px; stand-in conversation, no provider or account"}
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


def finish(error=None):
    if error:
        state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["failed_checks"] = [name for name, passed in state["checks"].items() if not passed]
    REPORT.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    print("ANGELO_BAKE_MAPS_AGENT_START", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


def build_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj)
    bpy.ops.mesh.primitive_cube_add(size=1.5)
    crate = bpy.context.active_object
    crate.name = "Crate"
    material = bpy.data.materials.new("Painted")
    tree = material.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    bsdf = tree.nodes["Principled BSDF"]
    tree.links.new(noise.outputs["Color"], bsdf.inputs["Base Color"])
    tree.links.new(noise.outputs["Fac"], bsdf.inputs["Roughness"])
    crate.data.materials.append(material)
    return crate


def step():
    phase = state["phase"]
    now = time.monotonic()
    if ticks["last"] is not None and phase == "running":
        ticks["longest"] = max(ticks["longest"], now - ticks["last"])
    ticks["last"] = now
    if now - started > 400:
        raise AssertionError("Suite timed out in phase %s" % phase)
    if phase == "boot":
        crate = build_scene()
        ctx["crate"] = crate.name
        ctx["materials"] = [slot.material.name for slot in crate.material_slots]
        arguments = {"kind": "BAKE_MAPS", "target": object_handle(crate), "output": "bakes/crate",
                     "packing": "ORM", "resolution": [128, 128], "material": True}
        # Each call gets its own arguments, as the conversation decodes them from the model's JSON each time.
        executor().precondition("angelo_job_start", json.loads(json.dumps(arguments)))
        result = job_start_wait.started(executor().execute("angelo_job_start", json.loads(json.dumps(arguments))))
        state["measured"]["start"] = {key: result.get(key) for key in ("job", "status", "error", "error_code")}
        ctx["job"] = result.get("job")
        check("the_agents_start_is_taken", result.get("status") in {"queued", "starting", "running", "done"})
        state["phase"] = "running"
        return 0.05
    if phase == "running":
        record = job_ui.manager().record(ctx["job"])
        if record["status"] not in {"done", "failed", "cancelled", "interrupted"}:
            return 0.05
        receipt = record["receipt"]
        state["measured"].update(longest_main_thread_gap_s=round(ticks["longest"], 3),
                                 record={key: record[key] for key in ("status", "reason", "receipt")})
        maps = sorted(entry["map"] for entry in receipt.get("outputs", []))
        check("the_job_is_done", record["status"] == "done")
        check("orm_packing_files", {"BaseColor", "ORM"} <= set(maps))
        check("files_written", all((WS / "bakes/crate" / entry["file"]).is_file()
                                   for entry in receipt.get("outputs", [])) and bool(maps))
        check("baked_material_made", any(isinstance(name, str) for name in receipt.get("materials", [])))
        crate = bpy.data.objects[ctx["crate"]]
        check("persons_materials_unchanged", [slot.material.name for slot in crate.material_slots] == ctx["materials"])
        check("main_thread_never_stalled_half_a_second", ticks["longest"] < 0.5)
        finish()
        return None
    return 0.1


def tick():
    try:
        return step()
    except Exception as error:
        finish(error)
        return None


# Held until Angelo's start-up (the first tab build its enable schedules) is over: qa_boot.gated.
bpy.app.timers.register(qa_boot.gated(tick), first_interval=1.0, persistent=True)
