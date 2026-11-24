# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's UV_LAYOUT background job in a real window (doc/angelo/uv_layout.rst, doc/angelo/long_jobs.rst).

Agent requests go through the real executor (``BlenderExecutor`` and ``job_host``) with a stand-in conversation, as
``test_blender_long_jobs`` runs its kinds, on generated plate kits with the Quality packer:

* admission refuses what the layout could not do: an object that is not a mesh, one in Edit Mode, a hidden one and
  one with no UV map to keep;
* a layout starts at once with a job ID, runs Pack Islands' job, reports progress that stays in 0..1 and only grows,
  refuses scene edits and reads while it runs, and keeps this suite's own timer firing (the longest gap is recorded
  and must stay under 1 s);
* it ends done with a receipt (islands, tile 1001, efficiency, Quality), every face inside the tile, the object back
  in Object Mode, the person's selection and UV sync setting as they were; one Undo takes the layout back;
* a larger layout with Search, cancelled while a pack runs, ends cancelled within 5 s with every UV as it was.

Run through ``run_native_suites.py --gui --include test_blender_uv_layout_job``; check ``uv-layout-job.json`` for
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
from types import SimpleNamespace

import addon_utils
import bmesh
import bpy

assert not bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-uv-layout-job-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS = OUT / "workspace"
WS.mkdir(exist_ok=True)
from angelo_agent import ui  # noqa: E402  (bundled add-on path)
from angelo_agent import job_start_wait  # noqa: E402  (a quick job's start result may be held)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

from angelo_agent import job_host, job_ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

REPORT = OUT / "uv-layout-job.json"
MAX_STOP_SECONDS = 5.0   # Pack Islands stops at its next island.
state = {"phase": "boot", "success": False, "checks": {}, "observed": {},
         "fixture": "Generated plate kits, Quality packer; stand-in conversation, no provider, account or network"}
checks, observed = state["checks"], state["observed"]
ctx = {"samples": [], "last": None, "gap": 0.0}
started = time.monotonic()
controller = SimpleNamespace(files=FileWorkspace(WS), state={"id": "suite-chat"},
                             executing_call=SimpleNamespace(id="call-1"))


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    value = BlenderExecutor(window())
    value.studio_context = controller
    return value


def check(name, value):
    checks[name] = bool(value)
    if not value:
        print("ANGELO_UV_LAYOUT_JOB check failed:", name, flush=True)


def start_job(arguments, *, call="call-1"):
    """Approve-and-run as the controller does: precondition (approval facts), then execute."""
    controller.executing_call = SimpleNamespace(id=call)
    facts = executor().precondition("angelo_job_start", arguments)
    result = job_start_wait.started(executor().execute("angelo_job_start", arguments))
    return job_ui.manager().record(result["job"]), result, json.loads(facts[2])


def refused(arguments):
    """The refusal's text, or None when the request was admitted."""
    try:
        executor().precondition("angelo_job_start", arguments)
    except Exception as error:
        return str(error)
    return None


def ended(record):
    return record["status"] in {"done", "failed", "cancelled", "interrupted"}


def plates(name, count, seed, unwrap=True):
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
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if unwrap:   # Each plate its own island, spread far outside the tile: a layout that never ran is plain to see.
        layer = mesh.uv_layers.new(name="UVMap")
        for poly in mesh.polygons:
            for index in poly.loop_indices:
                co = mesh.vertices[mesh.loops[index].vertex_index].co
                layer.data[index].uv = (co.x, co.y)
    return obj


def uvs(obj):
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        layer = bm.loops.layers.uv.active
        return [tuple(round(c, 6) for c in loop[layer].uv) for face in bm.faces for loop in face.loops]
    return [tuple(round(c, 6) for c in loop.uv) for loop in obj.data.uv_layers.active.data]


def watch():
    """Each tick while a job runs: progress samples, the longest gap between ticks, whether Pack Islands' job ran."""
    from _bpy import _angelo_jobs
    now = time.monotonic()
    if ctx["last"] is not None:
        ctx["gap"] = max(ctx["gap"], now - ctx["last"])
    ctx["last"] = now
    record = job_ui.manager().active()
    if record is not None:
        ctx["samples"].append(round(record["progress"], 4))
    if _angelo_jobs.progress(bpy.context.scene, "UV_PACK") is not None:
        ctx["pack_seen"] = True


def go(phase):
    state["phase"] = phase
    ctx["entered"] = time.monotonic()


def finish(error=None):
    if error:
        state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_UV_LAYOUT_JOB", json.dumps({"success": state["success"], "phase": state["phase"],
                                               "failed": sorted(k for k, v in checks.items() if not v)}), flush=True)
    if os.environ.get("ANGELO_QA_KEEP_OPEN") != "1":
        bpy.ops.wm.quit_blender()


def step():
    assert time.monotonic() - started < 270, "UV layout job suite timed out in " + state["phase"]
    phase, scene = state["phase"], bpy.context.scene
    if phase == "boot":
        empty = bpy.data.objects.new("Marker", None)
        scene.collection.objects.link(empty)
        check("refuses_an_object_that_is_not_a_mesh",
              "works on a mesh" in (refused({"kind": "UV_LAYOUT", "target": object_handle(empty)}) or ""))
        bare = plates("Bare", 3, 1, unwrap=False)
        check("refuses_keeping_a_uv_map_the_mesh_lacks",
              "no UV map to keep" in (refused({"kind": "UV_LAYOUT", "target": object_handle(bare)}) or ""))
        hidden = plates("Hidden", 3, 2)
        hidden.hide_set(True)
        check("refuses_a_hidden_mesh", "hidden" in (refused({"kind": "UV_LAYOUT", "target": object_handle(hidden)})
                                                     or ""))
        editing = plates("Editing", 3, 4)
        for obj in scene.objects:
            obj.select_set(obj == editing)
        bpy.context.view_layer.objects.active = editing
        with bpy.context.temp_override(window=window()):
            bpy.ops.object.mode_set(mode="EDIT")
        check("refuses_a_mesh_in_edit_mode", "Object Mode" in (refused({"kind": "UV_LAYOUT",
                                                                         "target": object_handle(editing)}) or ""))
        with bpy.context.temp_override(window=window()):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.batch_remove((empty, bare, hidden, editing))
        kit = plates("Plates", 24, 3)
        bystander = plates("Bystander", 4, 6)
        for obj in scene.objects:
            obj.select_set(obj == bystander)   # The person's selection: not the job's target.
        bpy.context.view_layer.objects.active = bystander
        scene.tool_settings.use_uv_select_sync = False
        with bpy.context.temp_override(window=window()):   # As the person's and the agent's edits always are.
            bpy.ops.ed.undo_push(message="Fixture")
        ctx.update(kit=kit, before=uvs(kit), bystander_before=uvs(bystander), opened=time.monotonic())
        ctx["layout"], result, facts = start_job({"kind": "UV_LAYOUT", "target": object_handle(kit),
                                                  "layout": {"texture_size": "1024", "search": 0}})
        check("starts_at_once_with_a_job_id", result["status"] == "running" and result["job"] == ctx["layout"]["id"])
        check("approval_facts_name_the_layout", facts.get("objects") == ["Plates"] and facts.get("packer") == "QUALITY"
              and facts.get("faces") == 24)
        observed["start_seconds"] = round(time.monotonic() - ctx["opened"], 3)
        go("layout")
    elif phase == "layout":
        watch()
        record = ctx["layout"]
        if not ended(record):
            if record["status"] == "running" and "busy_checked" not in ctx:
                ctx["busy_checked"] = True
                try:
                    executor().precondition("angelo_scene_search", {"query": "Plates"})
                    check("scene_reads_wait_while_it_lays_out", False)
                except job_host.JobBusyError:
                    check("scene_reads_wait_while_it_lays_out", True)
            return 0.05
        kit = ctx["kit"]
        receipt = record.get("receipt") or {}
        observed["layout"] = {"seconds": round(time.monotonic() - ctx["opened"], 2), "longest_gap_s": round(ctx["gap"], 3),
                              "receipt": receipt, "samples": len(ctx["samples"]), "status": record["status"],
                              "reason": record.get("reason")}
        values = ctx["samples"]
        check("ends_done_with_a_receipt", record["status"] == "done" and receipt.get("islands") == 24
              and receipt.get("tiles") == [1001] and 0 < receipt.get("efficiency", 0) <= 1
              and receipt.get("packer") == "QUALITY")
        check("each_pack_runs_as_pack_islands_job", ctx.get("pack_seen", False))
        check("progress_in_range_and_growing", values and all(0.0 <= v <= 1.0 for v in values)
              and all(b >= a for a, b in zip(values, values[1:])))
        check("the_interface_keeps_going_while_it_lays_out", ctx["gap"] < 1.0)
        after = uvs(kit)
        check("every_face_inside_its_tile", all(-1e-4 <= c <= 1 + 1e-4 for uv in after for c in uv)
              and after != ctx["before"])
        check("object_mode_and_the_persons_selection_come_back",
              kit.mode == "OBJECT" and bpy.context.view_layer.objects.active == bpy.data.objects["Bystander"]
              and [o.name for o in scene.objects if o.select_get()] == ["Bystander"])
        check("the_persons_uv_sync_setting_comes_back", scene.tool_settings.use_uv_select_sync is False)
        check("other_meshes_untouched", uvs(bpy.data.objects["Bystander"]) == ctx["bystander_before"])
        with bpy.context.temp_override(window=window()):
            bpy.ops.ed.undo()
        kit = bpy.data.objects["Plates"]
        observed["after_one_undo"] = {"mode": kit.mode, "uvs_as_before": uvs(kit) == ctx["before"]}
        check("one_undo_takes_the_layout_back", uvs(kit) == ctx["before"])
        with bpy.context.temp_override(window=window()):
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        big = plates("Big", 90, 5)
        ctx.update(big=big, big_before=uvs(big), samples=[], pack_seen=False, last=None, gap=0.0)
        ctx["stopped"], _, _ = start_job({"kind": "UV_LAYOUT", "target": object_handle(big),
                                          "layout": {"texture_size": "1024", "search": 32}}, call="call-2")
        go("cancel")
    elif phase == "cancel":
        watch()
        record = ctx["stopped"]
        if ended(record):
            raise AssertionError("the layout to cancel ended first: %s" % record["status"])
        if not ctx.get("pack_seen") and time.monotonic() - ctx["entered"] < 30:
            return 0.05
        ctx["cancel_at"] = time.monotonic()
        job_ui.manager().cancel(record["id"], by="user", session=None)
        go("cancelling")
    elif phase == "cancelling":
        record = ctx["stopped"]
        if not ended(record) and time.monotonic() - ctx["cancel_at"] < 30:
            return 0.05
        took = time.monotonic() - ctx["cancel_at"]
        big = bpy.data.objects["Big"]
        observed["cancel"] = {"stop_seconds": round(took, 2), "status": record["status"],
                              "note": (record.get("receipt") or {}).get("note")}
        check("cancel_ends_within_bound", record["status"] == "cancelled" and took < MAX_STOP_SECONDS)
        check("a_cancelled_layout_leaves_the_uvs_as_they_were", big.mode == "OBJECT" and uvs(big) == ctx["big_before"])
        finish()
        return None
    return 0.05


def tick():
    try:
        return step()
    except Exception as error:
        finish(error)
        return None


# Held until Angelo's start-up (the first tab build its enable schedules) is over: qa_boot.gated.
bpy.app.timers.register(qa_boot.gated(tick), first_interval=1.0, persistent=True)
